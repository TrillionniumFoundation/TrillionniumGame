impl SocialRegistry {
    pub fn send_group_message(
        &mut self,
        command: SocialCommandId,
        fingerprint: CommandFingerprint,
        request: SendGroupMessageRequest,
    ) -> Result<CommandReceipt, SocialError> {
        if let Some(receipt) = self.existing_receipt(
            command,
            fingerprint,
            request.sender,
            &[
                ReceiptOutcome::ChatMessageSent,
            ],
        )? {
            return Ok(receipt);
        }
        self.ensure_known_user(request.sender)?;
        let group = self.groups.get(&request.group).ok_or_else(|| {
            SocialError::new(SocialErrorCode::NotFound, "group_not_found")
        })?;
        if !group.members.contains_key(&request.sender) {
            return Err(SocialError::new(
                SocialErrorCode::PermissionDenied,
                "group_message_sender_not_member",
            ));
        }
        if request.body.len() > self.config.limits.max_message_bytes {
            return Err(SocialError::new(
                SocialErrorCode::InvalidArgument,
                "message_body_exceeds_profile_limit",
            ));
        }
        let current_messages = self.messages.get(&request.group).ok_or_else(invariant_error)?;
        if current_messages.len() >= self.config.limits.max_messages_per_group {
            return Err(SocialError::new(
                SocialErrorCode::ResourceExhausted,
                "group_message_capacity_exhausted",
            ));
        }
        if current_messages.iter().any(|message| message.id == request.message) {
            return Err(SocialError::new(
                SocialErrorCode::AlreadyExists,
                "message_id_exists",
            ));
        }
        let sequence = group.next_message_sequence;
        let next_sequence = next_revision(sequence, "message_sequence_overflow")?;
        let revision = self.next_command_revision()?;
        let message = ChatMessage {
            id: request.message,
            group: request.group,
            sender: request.sender,
            sequence,
            body: request.body.clone(),
        };
        let mut candidate = self.clone();
        candidate
            .messages
            .get_mut(&request.group)
            .ok_or_else(invariant_error)?
            .push(message);
        candidate
            .groups
            .get_mut(&request.group)
            .ok_or_else(invariant_error)?
            .next_message_sequence = next_sequence;
        let intents = [OutboxIntentPayload::ChatMessage {
            group: request.group,
            message: request.message,
            sender: request.sender,
            sequence,
            body: request.body,
        }];
        let receipt = self.finish_command(
            &mut candidate,
            CommandCompletion {
                command,
                fingerprint,
                actor: request.sender,
                revision,
                outcome: ReceiptOutcome::ChatMessageSent,
                intents: &intents,
            },
        )?;
        *self = candidate;
        Ok(receipt)
    }

    pub fn list_group_messages(
        &self,
        group_id: GroupId,
        cursor: Option<MessageCursor>,
        limit: usize,
    ) -> Result<MessagePage, SocialError> {
        validate_page_limit(limit)?;
        let group = self.groups.get(&group_id).ok_or_else(|| {
            SocialError::new(SocialErrorCode::NotFound, "group_not_found")
        })?;
        let after = match cursor {
            Some(value) if value.group != group_id => {
                return Err(SocialError::new(
                    SocialErrorCode::InvalidArgument,
                    "message_cursor_group_mismatch",
                ));
            }
            Some(value) if value.sequence >= group.next_message_sequence => {
                return Err(SocialError::new(
                    SocialErrorCode::InvalidArgument,
                    "message_cursor_ahead",
                ));
            }
            Some(value) => value.sequence,
            None => 0,
        };
        let messages = self.messages.get(&group_id).ok_or_else(invariant_error)?;
        let mut values: Vec<ChatMessage> = messages
            .iter()
            .filter(|message| message.sequence > after)
            .cloned()
            .collect();
        let has_more = values.len() > limit;
        values.truncate(limit);
        let next = if has_more {
            values
                .last()
                .map(|message| MessageCursor::new(group_id, message.sequence))
        } else {
            None
        };
        Ok(MessagePage {
            items: values,
            next,
        })
    }
}
