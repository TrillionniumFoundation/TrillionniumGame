impl SocialRegistry {
    pub fn change_group_role(
        &mut self,
        command: SocialCommandId,
        fingerprint: CommandFingerprint,
        group_id: GroupId,
        actor: AccountId,
        target: AccountId,
        role: GroupRole,
    ) -> Result<CommandReceipt, SocialError> {
        if let Some(receipt) = self.existing_receipt(
            command,
            fingerprint,
            actor,
            &[
                ReceiptOutcome::GroupRoleChanged,
            ],
        )? {
            return Ok(receipt);
        }
        self.ensure_known_user(actor)?;
        self.ensure_known_user(target)?;
        let group = self.groups.get(&group_id).ok_or_else(|| {
            SocialError::new(SocialErrorCode::NotFound, "group_not_found")
        })?;
        require_group_role(group, actor, GroupRole::Superadmin)?;
        let current = group.members.get(&target).copied().ok_or_else(|| {
            SocialError::new(SocialErrorCode::NotFound, "group_member_not_found")
        })?;
        if current == role {
            return Err(SocialError::new(
                SocialErrorCode::AlreadyExists,
                "group_role_unchanged",
            ));
        }
        if current == GroupRole::Superadmin
            && role != GroupRole::Superadmin
            && superadmin_count(group) == 1
        {
            return Err(SocialError::new(
                SocialErrorCode::FailedPrecondition,
                "last_superadmin_cannot_be_demoted",
            ));
        }
        let group_revision = next_revision(group.revision, "group_revision_overflow")?;
        let revision = self.next_command_revision()?;
        let mut candidate = self.clone();
        let candidate_group = candidate.groups.get_mut(&group_id).ok_or_else(invariant_error)?;
        candidate_group.members.insert(target, role);
        candidate_group.revision = group_revision;
        let intents = [(Some(target), OutboxIntentKind::GroupRoleChanged)];
        let receipt = self.finish_command(
            &mut candidate,
            CommandCompletion {
                command,
                fingerprint,
                actor,
                revision,
                outcome: ReceiptOutcome::GroupRoleChanged,
                intents: &intents,
            },
        )?;
        *self = candidate;
        Ok(receipt)
    }

    pub fn leave_group(
        &mut self,
        command: SocialCommandId,
        fingerprint: CommandFingerprint,
        group_id: GroupId,
        actor: AccountId,
    ) -> Result<CommandReceipt, SocialError> {
        if let Some(receipt) = self.existing_receipt(
            command,
            fingerprint,
            actor,
            &[
                ReceiptOutcome::GroupLeft,
            ],
        )? {
            return Ok(receipt);
        }
        self.ensure_known_user(actor)?;
        let group = self.groups.get(&group_id).ok_or_else(|| {
            SocialError::new(SocialErrorCode::NotFound, "group_not_found")
        })?;
        let role = group.members.get(&actor).copied().ok_or_else(|| {
            SocialError::new(SocialErrorCode::NotFound, "group_member_not_found")
        })?;
        if role == GroupRole::Superadmin && superadmin_count(group) == 1 {
            return Err(SocialError::new(
                SocialErrorCode::FailedPrecondition,
                "last_superadmin_cannot_leave",
            ));
        }
        let group_revision = next_revision(group.revision, "group_revision_overflow")?;
        let revision = self.next_command_revision()?;
        let mut candidate = self.clone();
        let candidate_group = candidate.groups.get_mut(&group_id).ok_or_else(invariant_error)?;
        candidate_group.members.remove(&actor);
        candidate_group.revision = group_revision;
        let receipt = self.finish_command(
            &mut candidate,
            CommandCompletion {
                command,
                fingerprint,
                actor,
                revision,
                outcome: ReceiptOutcome::GroupLeft,
                intents: &[],
            },
        )?;
        *self = candidate;
        Ok(receipt)
    }

    pub fn ban_group_member(
        &mut self,
        command: SocialCommandId,
        fingerprint: CommandFingerprint,
        group_id: GroupId,
        actor: AccountId,
        target: AccountId,
    ) -> Result<CommandReceipt, SocialError> {
        if let Some(receipt) = self.existing_receipt(
            command,
            fingerprint,
            actor,
            &[
                ReceiptOutcome::GroupMemberBanned,
            ],
        )? {
            return Ok(receipt);
        }
        self.ensure_known_user(actor)?;
        self.ensure_known_user(target)?;
        if actor == target {
            return Err(SocialError::new(
                SocialErrorCode::InvalidArgument,
                "group_self_ban_forbidden",
            ));
        }
        let group = self.groups.get(&group_id).ok_or_else(|| {
            SocialError::new(SocialErrorCode::NotFound, "group_not_found")
        })?;
        let actor_role = require_group_role(group, actor, GroupRole::Admin)?;
        if group.banned.contains(&target) {
            return Err(SocialError::new(
                SocialErrorCode::AlreadyExists,
                "group_ban_exists",
            ));
        }
        if let Some(target_role) = group.members.get(&target).copied() {
            if target_role == GroupRole::Superadmin || target_role.rank() >= actor_role.rank() {
                return Err(SocialError::new(
                    SocialErrorCode::PermissionDenied,
                    "group_role_cannot_ban_target",
                ));
            }
        } else if !group.join_requests.contains(&target) {
            return Err(SocialError::new(
                SocialErrorCode::NotFound,
                "group_member_or_request_not_found",
            ));
        }
        let group_revision = next_revision(group.revision, "group_revision_overflow")?;
        let revision = self.next_command_revision()?;
        let mut candidate = self.clone();
        let candidate_group = candidate.groups.get_mut(&group_id).ok_or_else(invariant_error)?;
        candidate_group.members.remove(&target);
        candidate_group.join_requests.remove(&target);
        candidate_group.banned.insert(target);
        candidate_group.revision = group_revision;
        let receipt = self.finish_command(
            &mut candidate,
            CommandCompletion {
                command,
                fingerprint,
                actor,
                revision,
                outcome: ReceiptOutcome::GroupMemberBanned,
                intents: &[],
            },
        )?;
        *self = candidate;
        Ok(receipt)
    }

    pub fn unban_group_member(
        &mut self,
        command: SocialCommandId,
        fingerprint: CommandFingerprint,
        group_id: GroupId,
        actor: AccountId,
        target: AccountId,
    ) -> Result<CommandReceipt, SocialError> {
        if let Some(receipt) = self.existing_receipt(
            command,
            fingerprint,
            actor,
            &[
                ReceiptOutcome::GroupMemberUnbanned,
            ],
        )? {
            return Ok(receipt);
        }
        self.ensure_known_user(actor)?;
        self.ensure_known_user(target)?;
        let group = self.groups.get(&group_id).ok_or_else(|| {
            SocialError::new(SocialErrorCode::NotFound, "group_not_found")
        })?;
        require_group_role(group, actor, GroupRole::Admin)?;
        if !group.banned.contains(&target) {
            return Err(SocialError::new(
                SocialErrorCode::NotFound,
                "group_ban_not_found",
            ));
        }
        let group_revision = next_revision(group.revision, "group_revision_overflow")?;
        let revision = self.next_command_revision()?;
        let mut candidate = self.clone();
        let candidate_group = candidate.groups.get_mut(&group_id).ok_or_else(invariant_error)?;
        candidate_group.banned.remove(&target);
        candidate_group.revision = group_revision;
        let receipt = self.finish_command(
            &mut candidate,
            CommandCompletion {
                command,
                fingerprint,
                actor,
                revision,
                outcome: ReceiptOutcome::GroupMemberUnbanned,
                intents: &[],
            },
        )?;
        *self = candidate;
        Ok(receipt)
    }

    #[must_use]
    pub fn group(&self, group: GroupId) -> Option<&GroupRecord> {
        self.groups.get(&group)
    }
}
