impl SocialRegistry {
    fn ensure_known_user(&self, account: AccountId) -> Result<(), SocialError> {
        if !self.users.contains(&account) {
            return Err(SocialError::new(
                SocialErrorCode::NotFound,
                "social_user_not_registered",
            ));
        }
        Ok(())
    }

    fn relationship_peers(&self, account: AccountId) -> BTreeSet<AccountId> {
        let mut peers = BTreeSet::new();
        for pair in &self.friendships {
            if let Some(other) = pair.other(account) {
                peers.insert(other);
            }
        }
        for (from, to) in &self.friend_requests {
            if *from == account {
                peers.insert(*to);
            } else if *to == account {
                peers.insert(*from);
            }
        }
        for (from, to) in &self.blocks {
            if *from == account {
                peers.insert(*to);
            } else if *to == account {
                peers.insert(*from);
            }
        }
        peers
    }

    fn has_relationship_pair(&self, first: AccountId, second: AccountId) -> bool {
        let pair = if first < second {
            UserPair {
                low: first,
                high: second,
            }
        } else {
            UserPair {
                low: second,
                high: first,
            }
        };
        self.friendships.contains(&pair)
            || self.friend_requests.contains(&(first, second))
            || self.friend_requests.contains(&(second, first))
            || self.blocks.contains(&(first, second))
            || self.blocks.contains(&(second, first))
    }

    fn ensure_relationship_capacity(
        &self,
        first: AccountId,
        second: AccountId,
    ) -> Result<(), SocialError> {
        if self.has_relationship_pair(first, second) {
            return Ok(());
        }
        if self.relationship_peers(first).len() >= self.config.limits.max_relationships_per_user
            || self.relationship_peers(second).len()
                >= self.config.limits.max_relationships_per_user
        {
            return Err(SocialError::new(
                SocialErrorCode::ResourceExhausted,
                "relationship_capacity_exhausted",
            ));
        }
        Ok(())
    }

    fn validate_outbox_payload(
        &self,
        payload: &OutboxIntentPayload,
        receipt: CommandReceipt,
    ) -> Result<(), SocialError> {
        if !payload.matches_receipt(receipt) {
            return Err(invariant_error());
        }
        match payload {
            OutboxIntentPayload::FriendRequest { requester, target } => {
                self.ensure_known_user(*requester)
                    .map_err(|_| invariant_error())?;
                self.ensure_known_user(*target)
                    .map_err(|_| invariant_error())?;
                UserPair::new(*requester, *target).map_err(|_| invariant_error())?;
            }
            OutboxIntentPayload::FriendAccepted {
                accepter,
                requester,
            } => {
                self.ensure_known_user(*accepter)
                    .map_err(|_| invariant_error())?;
                self.ensure_known_user(*requester)
                    .map_err(|_| invariant_error())?;
                UserPair::new(*accepter, *requester).map_err(|_| invariant_error())?;
            }
            OutboxIntentPayload::GroupJoinRequested { group, requester } => {
                self.ensure_known_user(*requester)
                    .map_err(|_| invariant_error())?;
                if !self.groups.contains_key(group) {
                    return Err(invariant_error());
                }
            }
            OutboxIntentPayload::GroupJoinAccepted {
                group,
                actor,
                member,
            }
            | OutboxIntentPayload::GroupRoleChanged {
                group,
                actor,
                member,
                ..
            } => {
                self.ensure_known_user(*actor)
                    .map_err(|_| invariant_error())?;
                self.ensure_known_user(*member)
                    .map_err(|_| invariant_error())?;
                if !self.groups.contains_key(group) {
                    return Err(invariant_error());
                }
            }
            OutboxIntentPayload::ChatMessage {
                group,
                message,
                sender,
                sequence,
                body,
            } => {
                self.ensure_known_user(*sender)
                    .map_err(|_| invariant_error())?;
                if *sequence == 0
                    || body.is_empty()
                    || body.len() > self.config.limits.max_message_bytes
                {
                    return Err(invariant_error());
                }
                let stored = self
                    .messages
                    .get(group)
                    .and_then(|messages| messages.iter().find(|item| item.id == *message))
                    .ok_or_else(invariant_error)?;
                if stored.group != *group
                    || stored.sender != *sender
                    || stored.sequence != *sequence
                    || stored.body.as_bytes() != body.as_bytes()
                {
                    return Err(invariant_error());
                }
            }
            OutboxIntentPayload::NotificationDelivery {
                sender,
                recipient,
                subject,
                content,
                sequence,
                ..
            } => {
                self.ensure_known_user(*recipient)
                    .map_err(|_| invariant_error())?;
                if let Some(sender) = sender {
                    self.ensure_known_user(*sender)
                        .map_err(|_| invariant_error())?;
                }
                if *sequence != receipt.revision
                    || subject.is_empty()
                    || content.is_empty()
                    || subject.len() > self.config.limits.max_notification_text_bytes
                    || content.len() > self.config.limits.max_notification_text_bytes
                {
                    return Err(invariant_error());
                }
            }
        }
        Ok(())
    }

    fn validate_invariants(&self) -> Result<(), SocialError> {
        if self.users.len() > self.config.limits.max_users
            || self.groups.len() > self.config.limits.max_groups
            || self.receipts.len() > self.config.limits.max_receipts
            || self.outbox.len() > self.config.limits.max_outbox_intents
        {
            return Err(invariant_error());
        }
        for pair in &self.friendships {
            if !self.users.contains(&pair.low)
                || !self.users.contains(&pair.high)
                || self.friend_requests.contains(&(pair.low, pair.high))
                || self.friend_requests.contains(&(pair.high, pair.low))
                || self.blocks.contains(&(pair.low, pair.high))
                || self.blocks.contains(&(pair.high, pair.low))
            {
                return Err(invariant_error());
            }
        }
        for (from, to) in &self.friend_requests {
            let pair = UserPair::new(*from, *to).map_err(|_| invariant_error())?;
            if !self.users.contains(from)
                || !self.users.contains(to)
                || self.friendships.contains(&pair)
                || self.blocks.contains(&(*from, *to))
                || self.blocks.contains(&(*to, *from))
                || self.friend_requests.contains(&(*to, *from))
            {
                return Err(invariant_error());
            }
        }
        for (from, to) in &self.blocks {
            let pair = UserPair::new(*from, *to).map_err(|_| invariant_error())?;
            if !self.users.contains(from)
                || !self.users.contains(to)
                || self.friendships.contains(&pair)
                || self.friend_requests.contains(&(*from, *to))
                || self.friend_requests.contains(&(*to, *from))
            {
                return Err(invariant_error());
            }
        }
        for account in &self.users {
            if self.relationship_peers(*account).len()
                > self.config.limits.max_relationships_per_user
            {
                return Err(invariant_error());
            }
        }
        for (group_id, group) in &self.groups {
            if group.id != *group_id
                || group.max_members == 0
                || group.max_members > self.config.limits.max_group_members
                || group.members.is_empty()
                || group.members.len() > group.max_members
                || superadmin_count(group) == 0
                || group.next_message_sequence == 0
            {
                return Err(invariant_error());
            }
            for account in group.members.keys() {
                if !self.users.contains(account)
                    || group.join_requests.contains(account)
                    || group.banned.contains(account)
                {
                    return Err(invariant_error());
                }
            }
            for account in &group.join_requests {
                if !self.users.contains(account) || group.banned.contains(account) {
                    return Err(invariant_error());
                }
            }
            for account in &group.banned {
                if !self.users.contains(account) {
                    return Err(invariant_error());
                }
            }
            let messages = self.messages.get(group_id).ok_or_else(invariant_error)?;
            if messages.len() > self.config.limits.max_messages_per_group {
                return Err(invariant_error());
            }
            let mut expected_sequence = 1_u64;
            let mut message_ids = BTreeSet::new();
            for message in messages {
                if message.group != *group_id
                    || !self.users.contains(&message.sender)
                    || message.sequence != expected_sequence
                    || message.body.is_empty()
                    || message.body.len() > self.config.limits.max_message_bytes
                    || !message_ids.insert(message.id)
                {
                    return Err(invariant_error());
                }
                expected_sequence = next_revision(expected_sequence, "message_sequence_overflow")?;
            }
            if group.next_message_sequence != expected_sequence {
                return Err(invariant_error());
            }
        }
        if self.messages.keys().any(|group| !self.groups.contains_key(group)) {
            return Err(invariant_error());
        }
        for (recipient, records) in &self.notifications {
            if !self.users.contains(recipient)
                || records.len() > self.config.limits.max_notifications_per_user
            {
                return Err(invariant_error());
            }
            for (notification_id, record) in records {
                if record.id != *notification_id
                    || record.recipient != *recipient
                    || record.sequence == 0
                    || record.sequence > self.revision
                    || record.subject.is_empty()
                    || record.content.is_empty()
                    || record.subject.len() > self.config.limits.max_notification_text_bytes
                    || record.content.len() > self.config.limits.max_notification_text_bytes
                    || record.sender.is_some_and(|sender| !self.users.contains(&sender))
                {
                    return Err(invariant_error());
                }
            }
        }
        for (command, receipt) in &self.receipts {
            if receipt.command != *command
                || !self.users.contains(&receipt.actor)
                || receipt.revision == 0
                || receipt.revision > self.revision
            {
                return Err(invariant_error());
            }
            let intent_count = self
                .outbox
                .values()
                .filter(|intent| intent.id.command == *command)
                .count();
            if intent_count != usize::from(receipt.outbox_count) {
                return Err(invariant_error());
            }
        }
        for (id, intent) in &self.outbox {
            let receipt = self
                .receipts
                .get(&id.command)
                .copied()
                .ok_or_else(invariant_error)?;
            if intent.id != *id || id.ordinal == 0 {
                return Err(invariant_error());
            }
            self.validate_outbox_payload(&intent.payload, receipt)?;
        }
        Ok(())
    }
}
