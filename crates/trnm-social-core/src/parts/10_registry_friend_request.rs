impl SocialRegistry {
    pub fn new(config: SocialConfig) -> Self {
        Self {
            config,
            revision: 0,
            users: BTreeSet::new(),
            friendships: BTreeSet::new(),
            friend_requests: BTreeSet::new(),
            blocks: BTreeSet::new(),
            groups: BTreeMap::new(),
            messages: BTreeMap::new(),
            notifications: BTreeMap::new(),
            receipts: BTreeMap::new(),
            outbox: BTreeMap::new(),
        }
    }

    #[must_use]
    pub const fn revision(&self) -> u64 {
        self.revision
    }

    pub fn register_user(&mut self, account: AccountId) -> Result<bool, SocialError> {
        if self.users.contains(&account) {
            return Ok(false);
        }
        if self.users.len() >= self.config.limits.max_users {
            return Err(SocialError::new(
                SocialErrorCode::ResourceExhausted,
                "user_capacity_exhausted",
            ));
        }
        let mut candidate = self.clone();
        candidate.users.insert(account);
        candidate.validate_invariants()?;
        *self = candidate;
        Ok(true)
    }

    pub fn relationship(
        &self,
        actor: AccountId,
        other: AccountId,
    ) -> Result<RelationshipView, SocialError> {
        self.ensure_known_user(actor)?;
        self.ensure_known_user(other)?;
        let pair = UserPair::new(actor, other)?;
        let actor_blocks = self.blocks.contains(&(actor, other));
        let other_blocks = self.blocks.contains(&(other, actor));
        if actor_blocks && other_blocks {
            return Ok(RelationshipView::MutuallyBlocked);
        }
        if actor_blocks {
            return Ok(RelationshipView::BlockedBySelf);
        }
        if other_blocks {
            return Ok(RelationshipView::BlockedByOther);
        }
        if self.friendships.contains(&pair) {
            return Ok(RelationshipView::Friends);
        }
        if self.friend_requests.contains(&(actor, other)) {
            return Ok(RelationshipView::OutgoingRequest);
        }
        if self.friend_requests.contains(&(other, actor)) {
            return Ok(RelationshipView::IncomingRequest);
        }
        Ok(RelationshipView::None)
    }

    pub fn send_friend_request(
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
                ReceiptOutcome::FriendRequestSent,
            ],
        )? {
            return Ok(receipt);
        }
        self.ensure_known_user(actor)?;
        self.ensure_known_user(target)?;
        let pair = UserPair::new(actor, target)?;
        match self.relationship(actor, target)? {
            RelationshipView::None => {}
            RelationshipView::IncomingRequest => {
                return Err(SocialError::new(
                    SocialErrorCode::FailedPrecondition,
                    "inverse_friend_request_pending",
                ));
            }
            RelationshipView::OutgoingRequest => {
                return Err(SocialError::new(
                    SocialErrorCode::AlreadyExists,
                    "friend_request_exists",
                ));
            }
            RelationshipView::Friends => {
                return Err(SocialError::new(
                    SocialErrorCode::AlreadyExists,
                    "friendship_exists",
                ));
            }
            RelationshipView::BlockedBySelf
            | RelationshipView::BlockedByOther
            | RelationshipView::MutuallyBlocked => {
                return Err(SocialError::new(
                    SocialErrorCode::PermissionDenied,
                    "relationship_blocked",
                ));
            }
        }
        self.ensure_relationship_capacity(actor, target)?;
        let revision = self.next_command_revision()?;
        let mut candidate = self.clone();
        candidate.friend_requests.insert((actor, target));
        debug_assert!(!candidate.friendships.contains(&pair));
        let intents = [OutboxIntentPayload::FriendRequest {
            requester: actor,
            target,
        }];
        let receipt = self.finish_command(
            &mut candidate,
            CommandCompletion {
                command,
                fingerprint,
                actor,
                revision,
                outcome: ReceiptOutcome::FriendRequestSent,
                intents: &intents,
            },
        )?;
        *self = candidate;
        Ok(receipt)
    }

    pub fn accept_friend_request(
        &mut self,
        command: SocialCommandId,
        fingerprint: CommandFingerprint,
        actor: AccountId,
        requester: AccountId,
    ) -> Result<CommandReceipt, SocialError> {
        if let Some(receipt) = self.existing_receipt(
            command,
            fingerprint,
            actor,
            &[
                ReceiptOutcome::FriendRequestAccepted,
            ],
        )? {
            return Ok(receipt);
        }
        self.ensure_known_user(actor)?;
        self.ensure_known_user(requester)?;
        let pair = UserPair::new(actor, requester)?;
        if self.blocks.contains(&(actor, requester)) || self.blocks.contains(&(requester, actor)) {
            return Err(SocialError::new(
                SocialErrorCode::PermissionDenied,
                "relationship_blocked",
            ));
        }
        if !self.friend_requests.contains(&(requester, actor)) {
            return Err(SocialError::new(
                SocialErrorCode::NotFound,
                "friend_request_not_found",
            ));
        }
        let revision = self.next_command_revision()?;
        let mut candidate = self.clone();
        candidate.friend_requests.remove(&(requester, actor));
        candidate.friendships.insert(pair);
        let intents = [OutboxIntentPayload::FriendAccepted {
            accepter: actor,
            requester,
        }];
        let receipt = self.finish_command(
            &mut candidate,
            CommandCompletion {
                command,
                fingerprint,
                actor,
                revision,
                outcome: ReceiptOutcome::FriendRequestAccepted,
                intents: &intents,
            },
        )?;
        *self = candidate;
        Ok(receipt)
    }
}
