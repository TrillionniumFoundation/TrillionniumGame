impl SocialRegistry {
    pub fn create_notification(
        &mut self,
        command: SocialCommandId,
        fingerprint: CommandFingerprint,
        request: CreateNotificationRequest,
    ) -> Result<CommandReceipt, SocialError> {
        if let Some(receipt) = self.existing_receipt(
            command,
            fingerprint,
            request.sender.unwrap_or(request.recipient),
            &[
                ReceiptOutcome::NotificationCreated,
            ],
        )? {
            return Ok(receipt);
        }
        self.ensure_known_user(request.recipient)?;
        if let Some(sender) = request.sender {
            self.ensure_known_user(sender)?;
        }
        if request.subject.len() > self.config.limits.max_notification_text_bytes
            || request.content.len() > self.config.limits.max_notification_text_bytes
        {
            return Err(SocialError::new(
                SocialErrorCode::InvalidArgument,
                "notification_text_exceeds_profile_limit",
            ));
        }
        let records = self.notifications.get(&request.recipient);
        if records.is_some_and(|values| values.contains_key(&request.notification)) {
            return Err(SocialError::new(
                SocialErrorCode::AlreadyExists,
                "notification_id_exists",
            ));
        }
        if records.map_or(0, |values| values.len()) >= self.config.limits.max_notifications_per_user {
            return Err(SocialError::new(
                SocialErrorCode::ResourceExhausted,
                "notification_capacity_exhausted",
            ));
        }
        let revision = self.next_command_revision()?;
        let record = NotificationRecord {
            id: request.notification,
            sender: request.sender,
            recipient: request.recipient,
            kind: request.kind,
            subject: request.subject.clone(),
            content: request.content.clone(),
            sequence: revision,
            read: false,
        };
        let mut candidate = self.clone();
        candidate
            .notifications
            .entry(request.recipient)
            .or_default()
            .insert(request.notification, record);
        let actor = request.sender.unwrap_or(request.recipient);
        let intents = [OutboxIntentPayload::NotificationDelivery {
            sender: request.sender,
            recipient: request.recipient,
            notification: request.notification,
            kind: request.kind,
            subject: request.subject,
            content: request.content,
            sequence: revision,
        }];
        let receipt = self.finish_command(
            &mut candidate,
            CommandCompletion {
                command,
                fingerprint,
                actor,
                revision,
                outcome: ReceiptOutcome::NotificationCreated,
                intents: &intents,
            },
        )?;
        *self = candidate;
        Ok(receipt)
    }

    pub fn mark_notification_read(
        &mut self,
        command: SocialCommandId,
        fingerprint: CommandFingerprint,
        recipient: AccountId,
        notification: NotificationId,
    ) -> Result<CommandReceipt, SocialError> {
        if let Some(receipt) = self.existing_receipt(
            command,
            fingerprint,
            recipient,
            &[
                ReceiptOutcome::NotificationMarkedRead,
            ],
        )? {
            return Ok(receipt);
        }
        self.ensure_known_user(recipient)?;
        if self
            .notifications
            .get(&recipient)
            .and_then(|records| records.get(&notification))
            .is_none()
        {
            return Err(SocialError::new(
                SocialErrorCode::NotFound,
                "notification_not_found",
            ));
        }
        let revision = self.next_command_revision()?;
        let mut candidate = self.clone();
        candidate
            .notifications
            .get_mut(&recipient)
            .and_then(|records| records.get_mut(&notification))
            .ok_or_else(invariant_error)?
            .read = true;
        let receipt = self.finish_command(
            &mut candidate,
            CommandCompletion {
                command,
                fingerprint,
                actor: recipient,
                revision,
                outcome: ReceiptOutcome::NotificationMarkedRead,
                intents: &[],
            },
        )?;
        *self = candidate;
        Ok(receipt)
    }

    pub fn delete_notification(
        &mut self,
        command: SocialCommandId,
        fingerprint: CommandFingerprint,
        recipient: AccountId,
        notification: NotificationId,
    ) -> Result<CommandReceipt, SocialError> {
        if let Some(receipt) = self.existing_receipt(
            command,
            fingerprint,
            recipient,
            &[
                ReceiptOutcome::NotificationDeleted,
            ],
        )? {
            return Ok(receipt);
        }
        self.ensure_known_user(recipient)?;
        if self
            .notifications
            .get(&recipient)
            .and_then(|records| records.get(&notification))
            .is_none()
        {
            return Err(SocialError::new(
                SocialErrorCode::NotFound,
                "notification_not_found",
            ));
        }
        let revision = self.next_command_revision()?;
        let mut candidate = self.clone();
        let records = candidate
            .notifications
            .get_mut(&recipient)
            .ok_or_else(invariant_error)?;
        records.remove(&notification);
        if records.is_empty() {
            candidate.notifications.remove(&recipient);
        }
        let receipt = self.finish_command(
            &mut candidate,
            CommandCompletion {
                command,
                fingerprint,
                actor: recipient,
                revision,
                outcome: ReceiptOutcome::NotificationDeleted,
                intents: &[],
            },
        )?;
        *self = candidate;
        Ok(receipt)
    }

    pub fn list_notifications(
        &self,
        recipient: AccountId,
        cursor: Option<NotificationCursor>,
        limit: usize,
    ) -> Result<NotificationPage, SocialError> {
        self.ensure_known_user(recipient)?;
        validate_page_limit(limit)?;
        let after = match cursor {
            Some(value) if value.recipient != recipient => {
                return Err(SocialError::new(
                    SocialErrorCode::InvalidArgument,
                    "notification_cursor_recipient_mismatch",
                ));
            }
            Some(value) if value.sequence > self.revision => {
                return Err(SocialError::new(
                    SocialErrorCode::InvalidArgument,
                    "notification_cursor_ahead",
                ));
            }
            Some(value) => value.sequence,
            None => 0,
        };
        let mut values: Vec<NotificationRecord> = self
            .notifications
            .get(&recipient)
            .into_iter()
            .flat_map(|values| values.values())
            .filter(|record| record.sequence > after)
            .cloned()
            .collect();
        values.sort_unstable_by_key(|record| (record.sequence, record.id));
        let has_more = values.len() > limit;
        values.truncate(limit);
        let next = if has_more {
            values
                .last()
                .map(|record| NotificationCursor::new(recipient, record.sequence))
        } else {
            None
        };
        Ok(NotificationPage {
            items: values,
            next,
        })
    }
}
