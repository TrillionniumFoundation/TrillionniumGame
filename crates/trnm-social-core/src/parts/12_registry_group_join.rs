impl SocialRegistry {
    pub fn create_group(
        &mut self,
        command: SocialCommandId,
        fingerprint: CommandFingerprint,
        request: CreateGroupRequest,
    ) -> Result<CommandReceipt, SocialError> {
        if let Some(receipt) = self.existing_receipt(
            command,
            fingerprint,
            request.creator,
            &[
                ReceiptOutcome::GroupCreated,
            ],
        )? {
            return Ok(receipt);
        }
        self.ensure_known_user(request.creator)?;
        if self.groups.len() >= self.config.limits.max_groups {
            return Err(SocialError::new(
                SocialErrorCode::ResourceExhausted,
                "group_capacity_exhausted",
            ));
        }
        if self.groups.contains_key(&request.group) {
            return Err(SocialError::new(
                SocialErrorCode::AlreadyExists,
                "group_exists",
            ));
        }
        if request.max_members == 0 || request.max_members > self.config.limits.max_group_members {
            return Err(SocialError::new(
                SocialErrorCode::InvalidArgument,
                "group_max_members_invalid",
            ));
        }
        let revision = self.next_command_revision()?;
        let mut members = BTreeMap::new();
        members.insert(request.creator, GroupRole::Superadmin);
        let group = GroupRecord {
            id: request.group,
            name: request.name,
            join_mode: request.join_mode,
            max_members: request.max_members,
            revision: 1,
            members,
            join_requests: BTreeSet::new(),
            banned: BTreeSet::new(),
            next_message_sequence: 1,
        };
        let mut candidate = self.clone();
        candidate.groups.insert(request.group, group);
        candidate.messages.insert(request.group, Vec::new());
        let receipt = self.finish_command(
            &mut candidate,
            CommandCompletion {
                command,
                fingerprint,
                actor: request.creator,
                revision,
                outcome: ReceiptOutcome::GroupCreated,
                intents: &[],
            },
        )?;
        *self = candidate;
        Ok(receipt)
    }

    pub fn join_group(
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
                ReceiptOutcome::GroupJoined,
                ReceiptOutcome::GroupJoinRequested,
            ],
        )? {
            return Ok(receipt);
        }
        self.ensure_known_user(actor)?;
        let group = self.groups.get(&group_id).ok_or_else(|| {
            SocialError::new(SocialErrorCode::NotFound, "group_not_found")
        })?;
        if group.members.contains_key(&actor) {
            return Err(SocialError::new(
                SocialErrorCode::AlreadyExists,
                "group_member_exists",
            ));
        }
        if group.join_requests.contains(&actor) {
            return Err(SocialError::new(
                SocialErrorCode::AlreadyExists,
                "group_join_request_exists",
            ));
        }
        if group.banned.contains(&actor) {
            return Err(SocialError::new(
                SocialErrorCode::PermissionDenied,
                "group_member_banned",
            ));
        }
        if group.join_mode == GroupJoinMode::Closed {
            return Err(SocialError::new(
                SocialErrorCode::PermissionDenied,
                "group_closed",
            ));
        }
        if group.join_mode == GroupJoinMode::Open && group.members.len() >= group.max_members {
            return Err(SocialError::new(
                SocialErrorCode::ResourceExhausted,
                "group_member_capacity_exhausted",
            ));
        }
        let group_revision = next_revision(group.revision, "group_revision_overflow")?;
        let revision = self.next_command_revision()?;
        let mut candidate = self.clone();
        let candidate_group = candidate.groups.get_mut(&group_id).ok_or_else(invariant_error)?;
        let (outcome, intents): (ReceiptOutcome, Vec<(Option<AccountId>, OutboxIntentKind)>) =
            match candidate_group.join_mode {
                GroupJoinMode::Open => {
                    candidate_group.members.insert(actor, GroupRole::Member);
                    (
                        ReceiptOutcome::GroupJoined,
                        vec![(Some(actor), OutboxIntentKind::GroupJoinAccepted)],
                    )
                }
                GroupJoinMode::AdminApproval => {
                    candidate_group.join_requests.insert(actor);
                    (
                        ReceiptOutcome::GroupJoinRequested,
                        vec![(None, OutboxIntentKind::GroupJoinRequested)],
                    )
                }
                GroupJoinMode::Closed => return Err(invariant_error()),
            };
        candidate_group.revision = group_revision;
        let receipt = self.finish_command(
            &mut candidate,
            CommandCompletion {
                command,
                fingerprint,
                actor,
                revision,
                outcome,
                intents: &intents,
            },
        )?;
        *self = candidate;
        Ok(receipt)
    }

    pub fn approve_group_join(
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
                ReceiptOutcome::GroupJoinApproved,
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
        if !group.join_requests.contains(&target) {
            return Err(SocialError::new(
                SocialErrorCode::NotFound,
                "group_join_request_not_found",
            ));
        }
        if group.members.len() >= group.max_members {
            return Err(SocialError::new(
                SocialErrorCode::ResourceExhausted,
                "group_member_capacity_exhausted",
            ));
        }
        let group_revision = next_revision(group.revision, "group_revision_overflow")?;
        let revision = self.next_command_revision()?;
        let mut candidate = self.clone();
        let candidate_group = candidate.groups.get_mut(&group_id).ok_or_else(invariant_error)?;
        candidate_group.join_requests.remove(&target);
        candidate_group.members.insert(target, GroupRole::Member);
        candidate_group.revision = group_revision;
        let intents = [(Some(target), OutboxIntentKind::GroupJoinAccepted)];
        let receipt = self.finish_command(
            &mut candidate,
            CommandCompletion {
                command,
                fingerprint,
                actor,
                revision,
                outcome: ReceiptOutcome::GroupJoinApproved,
                intents: &intents,
            },
        )?;
        *self = candidate;
        Ok(receipt)
    }
}
