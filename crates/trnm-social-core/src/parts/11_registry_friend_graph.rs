impl SocialRegistry {
    pub fn remove_friend(
        &mut self,
        command: SocialCommandId,
        fingerprint: CommandFingerprint,
        actor: AccountId,
        target: AccountId,
    ) -> Result<CommandReceipt, SocialError> {
        if let Some(receipt) = self.existing_receipt(
            command,
            fingerprint,
            actor,
            &[
                ReceiptOutcome::FriendRemoved,
            ],
        )? {
            return Ok(receipt);
        }
        self.ensure_known_user(actor)?;
        self.ensure_known_user(target)?;
        let pair = UserPair::new(actor, target)?;
        if !self.friendships.contains(&pair) {
            return Err(SocialError::new(
                SocialErrorCode::NotFound,
                "friendship_not_found",
            ));
        }
        let revision = self.next_command_revision()?;
        let mut candidate = self.clone();
        candidate.friendships.remove(&pair);
        let receipt = self.finish_command(
            &mut candidate,
            CommandCompletion {
                command,
                fingerprint,
                actor,
                revision,
                outcome: ReceiptOutcome::FriendRemoved,
                intents: &[],
            },
        )?;
        *self = candidate;
        Ok(receipt)
    }

    pub fn block_user(
        &mut self,
        command: SocialCommandId,
        fingerprint: CommandFingerprint,
        actor: AccountId,
        target: AccountId,
    ) -> Result<CommandReceipt, SocialError> {
        if let Some(receipt) = self.existing_receipt(
            command,
            fingerprint,
            actor,
            &[
                ReceiptOutcome::UserBlocked,
            ],
        )? {
            return Ok(receipt);
        }
        self.ensure_known_user(actor)?;
        self.ensure_known_user(target)?;
        let pair = UserPair::new(actor, target)?;
        if self.blocks.contains(&(actor, target)) {
            return Err(SocialError::new(
                SocialErrorCode::AlreadyExists,
                "block_exists",
            ));
        }
        self.ensure_relationship_capacity(actor, target)?;
        let revision = self.next_command_revision()?;
        let mut candidate = self.clone();
        candidate.friendships.remove(&pair);
        candidate.friend_requests.remove(&(actor, target));
        candidate.friend_requests.remove(&(target, actor));
        candidate.blocks.insert((actor, target));
        let receipt = self.finish_command(
            &mut candidate,
            CommandCompletion {
                command,
                fingerprint,
                actor,
                revision,
                outcome: ReceiptOutcome::UserBlocked,
                intents: &[],
            },
        )?;
        *self = candidate;
        Ok(receipt)
    }

    pub fn unblock_user(
        &mut self,
        command: SocialCommandId,
        fingerprint: CommandFingerprint,
        actor: AccountId,
        target: AccountId,
    ) -> Result<CommandReceipt, SocialError> {
        if let Some(receipt) = self.existing_receipt(
            command,
            fingerprint,
            actor,
            &[
                ReceiptOutcome::UserUnblocked,
            ],
        )? {
            return Ok(receipt);
        }
        self.ensure_known_user(actor)?;
        self.ensure_known_user(target)?;
        UserPair::new(actor, target)?;
        if !self.blocks.contains(&(actor, target)) {
            return Err(SocialError::new(
                SocialErrorCode::NotFound,
                "block_not_found",
            ));
        }
        let revision = self.next_command_revision()?;
        let mut candidate = self.clone();
        candidate.blocks.remove(&(actor, target));
        let receipt = self.finish_command(
            &mut candidate,
            CommandCompletion {
                command,
                fingerprint,
                actor,
                revision,
                outcome: ReceiptOutcome::UserUnblocked,
                intents: &[],
            },
        )?;
        *self = candidate;
        Ok(receipt)
    }

    pub fn list_friends(
        &self,
        owner: AccountId,
        cursor: Option<FriendCursor>,
        limit: usize,
    ) -> Result<FriendPage, SocialError> {
        self.ensure_known_user(owner)?;
        validate_page_limit(limit)?;
        let after = match cursor {
            Some(value) if value.owner != owner => {
                return Err(SocialError::new(
                    SocialErrorCode::InvalidArgument,
                    "friend_cursor_owner_mismatch",
                ));
            }
            Some(value) => Some(value.after),
            None => None,
        };
        let mut values: Vec<AccountId> = self
            .friendships
            .iter()
            .filter_map(|pair| pair.other(owner))
            .filter(|account| after.is_none_or(|minimum| *account > minimum))
            .collect();
        values.sort_unstable();
        let has_more = values.len() > limit;
        values.truncate(limit);
        let next = if has_more {
            values
                .last()
                .copied()
                .map(|last| FriendCursor { owner, after: last })
        } else {
            None
        };
        Ok(FriendPage {
            items: values,
            next,
        })
    }
}
