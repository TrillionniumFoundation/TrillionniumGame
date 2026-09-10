impl SocialRegistry {
    #[must_use]
    pub fn receipt(&self, command: SocialCommandId) -> Option<CommandReceipt> {
        self.receipts.get(&command).copied()
    }

    pub fn outbox_intents(&self) -> impl ExactSizeIterator<Item = OutboxIntent> + '_ {
        self.outbox.values().copied()
    }

    fn next_command_revision(&self) -> Result<u64, SocialError> {
        next_revision(self.revision, "social_revision_overflow")
    }

    fn existing_receipt(
        &self,
        command: SocialCommandId,
        fingerprint: CommandFingerprint,
    ) -> Result<Option<CommandReceipt>, SocialError> {
        let Some(receipt) = self.receipts.get(&command).copied() else {
            return Ok(None);
        };
        if receipt.fingerprint != fingerprint {
            return Err(SocialError::new(
                SocialErrorCode::Conflict,
                "social_command_fingerprint_mismatch",
            ));
        }
        Ok(Some(receipt))
    }

    fn finish_command(
        &self,
        candidate: &mut Self,
        completion: CommandCompletion<'_>,
    ) -> Result<CommandReceipt, SocialError> {
        let CommandCompletion {
            command,
            fingerprint,
            actor,
            revision,
            outcome,
            intents,
        } = completion;
        if self.receipts.len() >= self.config.limits.max_receipts {
            return Err(SocialError::new(
                SocialErrorCode::ResourceExhausted,
                "receipt_capacity_exhausted",
            ));
        }
        let projected_outbox = self
            .outbox
            .len()
            .checked_add(intents.len())
            .ok_or_else(|| {
                SocialError::new(SocialErrorCode::OutOfRange, "outbox_capacity_overflow")
            })?;
        if projected_outbox > self.config.limits.max_outbox_intents {
            return Err(SocialError::new(
                SocialErrorCode::ResourceExhausted,
                "outbox_capacity_exhausted",
            ));
        }
        let outbox_count = u16::try_from(intents.len()).map_err(|_| {
            SocialError::new(SocialErrorCode::OutOfRange, "outbox_ordinal_overflow")
        })?;
        let receipt = CommandReceipt {
            command,
            fingerprint,
            actor,
            revision,
            outcome,
            outbox_count,
        };
        candidate.revision = revision;
        candidate.receipts.insert(command, receipt);
        for (index, (recipient, kind)) in intents.iter().copied().enumerate() {
            let ordinal = u16::try_from(index + 1).map_err(|_| {
                SocialError::new(SocialErrorCode::OutOfRange, "outbox_ordinal_overflow")
            })?;
            let id = OutboxIntentId { command, ordinal };
            candidate.outbox.insert(
                id,
                OutboxIntent {
                    id,
                    recipient,
                    kind,
                },
            );
        }
        candidate.validate_invariants()?;
        Ok(receipt)
    }
}
