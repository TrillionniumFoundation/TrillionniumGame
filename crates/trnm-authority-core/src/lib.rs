#![forbid(unsafe_code)]

use std::collections::BTreeMap;

use trnm_contracts::{
    AuthorityGeneration, CommandId, Digest32, DomainError, MatchVersion, ParticipantId,
    ParticipantSequence, RetryClass, StableCode,
};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CommandEnvelope {
    pub command_id: CommandId,
    pub participant_id: ParticipantId,
    pub participant_sequence: ParticipantSequence,
    pub expected_version: MatchVersion,
    pub authority_generation: AuthorityGeneration,
    pub fingerprint: Digest32,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PendingCommand {
    envelope: CommandEnvelope,
    base_version: MatchVersion,
    base_generation: AuthorityGeneration,
}

impl PendingCommand {
    #[must_use]
    pub const fn envelope(&self) -> &CommandEnvelope {
        &self.envelope
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CommandReceipt {
    pub command_id: CommandId,
    pub participant_id: ParticipantId,
    pub participant_sequence: ParticipantSequence,
    pub fingerprint: Digest32,
    pub base_version: MatchVersion,
    pub committed_version: MatchVersion,
    pub authority_generation: AuthorityGeneration,
    pub result_digest: Digest32,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum PrepareOutcome {
    Ready(PendingCommand),
    Replay(CommandReceipt),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MatchState {
    version: MatchVersion,
    generation: AuthorityGeneration,
    participant_sequences: BTreeMap<ParticipantId, ParticipantSequence>,
    receipts: BTreeMap<CommandId, CommandReceipt>,
}

impl MatchState {
    #[must_use]
    pub fn new(generation: AuthorityGeneration) -> Self {
        Self {
            version: MatchVersion::default(),
            generation,
            participant_sequences: BTreeMap::new(),
            receipts: BTreeMap::new(),
        }
    }

    #[must_use]
    pub const fn version(&self) -> MatchVersion {
        self.version
    }

    #[must_use]
    pub const fn generation(&self) -> AuthorityGeneration {
        self.generation
    }

    #[must_use]
    pub fn receipt(&self, command_id: CommandId) -> Option<&CommandReceipt> {
        self.receipts.get(&command_id)
    }

    pub fn prepare(&self, envelope: CommandEnvelope) -> Result<PrepareOutcome, DomainError> {
        reject_zero_identity(&envelope)?;

        if let Some(receipt) = self.receipts.get(&envelope.command_id) {
            if receipt.fingerprint == envelope.fingerprint
                && receipt.participant_id == envelope.participant_id
                && receipt.participant_sequence == envelope.participant_sequence
                && receipt.base_version == envelope.expected_version
            {
                return Ok(PrepareOutcome::Replay(receipt.clone()));
            }
            return Err(error(
                StableCode::AlreadyExists,
                "command_id_conflict",
                RetryClass::Never,
            ));
        }

        if envelope.authority_generation != self.generation {
            return Err(error(
                StableCode::Aborted,
                "stale_authority_generation",
                RetryClass::ResyncRequired,
            ));
        }
        if envelope.expected_version != self.version {
            return Err(error(
                StableCode::Aborted,
                "stale_match_version",
                RetryClass::ResyncRequired,
            ));
        }

        let current_sequence = self
            .participant_sequences
            .get(&envelope.participant_id)
            .copied()
            .unwrap_or_default();
        let expected_sequence = current_sequence.checked_next()?;
        if envelope.participant_sequence != expected_sequence {
            return Err(error(
                StableCode::FailedPrecondition,
                "participant_sequence_mismatch",
                RetryClass::ResyncRequired,
            ));
        }

        Ok(PrepareOutcome::Ready(PendingCommand {
            base_version: self.version,
            base_generation: self.generation,
            envelope,
        }))
    }

    pub fn commit(
        &mut self,
        pending: PendingCommand,
        result_digest: Digest32,
    ) -> Result<CommandReceipt, DomainError> {
        if let Some(receipt) = self.receipts.get(&pending.envelope.command_id) {
            if receipt.fingerprint == pending.envelope.fingerprint
                && receipt.participant_id == pending.envelope.participant_id
                && receipt.participant_sequence == pending.envelope.participant_sequence
                && receipt.base_version == pending.base_version
                && receipt.result_digest == result_digest
            {
                return Ok(receipt.clone());
            }
            return Err(error(
                StableCode::AlreadyExists,
                "command_commit_conflict",
                RetryClass::Never,
            ));
        }

        if pending.base_generation != self.generation {
            return Err(error(
                StableCode::Aborted,
                "stale_pending_generation",
                RetryClass::ResyncRequired,
            ));
        }
        if pending.base_version != self.version {
            return Err(error(
                StableCode::Aborted,
                "stale_pending_version",
                RetryClass::ResyncRequired,
            ));
        }

        let current_sequence = self
            .participant_sequences
            .get(&pending.envelope.participant_id)
            .copied()
            .unwrap_or_default();
        if current_sequence.checked_next()? != pending.envelope.participant_sequence {
            return Err(error(
                StableCode::Aborted,
                "stale_pending_participant_sequence",
                RetryClass::ResyncRequired,
            ));
        }

        let committed_version = self.version.checked_next()?;
        let receipt = CommandReceipt {
            command_id: pending.envelope.command_id,
            participant_id: pending.envelope.participant_id,
            participant_sequence: pending.envelope.participant_sequence,
            fingerprint: pending.envelope.fingerprint,
            base_version: pending.base_version,
            committed_version,
            authority_generation: self.generation,
            result_digest,
        };
        self.version = committed_version;
        self.participant_sequences.insert(
            pending.envelope.participant_id,
            pending.envelope.participant_sequence,
        );
        self.receipts
            .insert(pending.envelope.command_id, receipt.clone());
        Ok(receipt)
    }

    pub fn takeover(
        &mut self,
        expected_generation: AuthorityGeneration,
    ) -> Result<AuthorityGeneration, DomainError> {
        if expected_generation != self.generation {
            return Err(error(
                StableCode::Aborted,
                "takeover_generation_conflict",
                RetryClass::ResyncRequired,
            ));
        }
        self.generation = self.generation.checked_next()?;
        Ok(self.generation)
    }
}

fn reject_zero_identity(envelope: &CommandEnvelope) -> Result<(), DomainError> {
    if envelope.command_id.is_zero()
        || envelope.participant_id.is_zero()
        || envelope.fingerprint.is_zero()
    {
        return Err(error(
            StableCode::InvalidArgument,
            "zero_identity_or_fingerprint",
            RetryClass::Never,
        ));
    }
    Ok(())
}

const fn error(code: StableCode, reason: &'static str, retry: RetryClass) -> DomainError {
    DomainError::new(code, reason, retry)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn id(value: u8) -> [u8; 16] {
        [value; 16]
    }

    fn digest(value: u8) -> Digest32 {
        Digest32::new([value; 32])
    }

    fn command(
        command: u8,
        participant: u8,
        sequence: u64,
        version: u64,
        generation: u64,
        fingerprint: u8,
    ) -> CommandEnvelope {
        CommandEnvelope {
            command_id: CommandId::new(id(command)),
            participant_id: ParticipantId::new(id(participant)),
            participant_sequence: ParticipantSequence::new(sequence),
            expected_version: MatchVersion::new(version),
            authority_generation: AuthorityGeneration::new(generation),
            fingerprint: digest(fingerprint),
        }
    }

    fn ready(outcome: PrepareOutcome) -> PendingCommand {
        match outcome {
            PrepareOutcome::Ready(pending) => pending,
            PrepareOutcome::Replay(_) => unreachable!("expected a new command"),
        }
    }

    #[test]
    fn first_command_commits_and_advances_global_and_participant_sequences() {
        let mut state = MatchState::new(AuthorityGeneration::new(1));
        let pending = ready(state.prepare(command(1, 1, 1, 0, 1, 7)).unwrap());
        let receipt = state.commit(pending, digest(9)).unwrap();
        assert_eq!(receipt.committed_version, MatchVersion::new(1));
        assert_eq!(state.version(), MatchVersion::new(1));
    }

    #[test]
    fn exact_duplicate_replays_without_advancing_state() {
        let mut state = MatchState::new(AuthorityGeneration::new(1));
        let envelope = command(1, 1, 1, 0, 1, 7);
        let pending = ready(state.prepare(envelope.clone()).unwrap());
        let receipt = state.commit(pending, digest(9)).unwrap();
        assert_eq!(
            state.prepare(envelope).unwrap(),
            PrepareOutcome::Replay(receipt)
        );
        assert_eq!(state.version(), MatchVersion::new(1));
    }

    #[test]
    fn same_command_id_with_different_fingerprint_is_terminal_conflict() {
        let mut state = MatchState::new(AuthorityGeneration::new(1));
        let pending = ready(state.prepare(command(1, 1, 1, 0, 1, 7)).unwrap());
        state.commit(pending, digest(9)).unwrap();
        let failure = state.prepare(command(1, 1, 1, 0, 1, 8)).unwrap_err();
        assert_eq!(failure.reason(), "command_id_conflict");
        assert_eq!(failure.retry(), RetryClass::Never);
    }

    #[test]
    fn stale_version_and_sequence_gap_fail_closed() {
        let state = MatchState::new(AuthorityGeneration::new(1));
        assert_eq!(
            state
                .prepare(command(1, 1, 1, 1, 1, 7))
                .unwrap_err()
                .reason(),
            "stale_match_version"
        );
        assert_eq!(
            state
                .prepare(command(1, 1, 2, 0, 1, 7))
                .unwrap_err()
                .reason(),
            "participant_sequence_mismatch"
        );
    }

    #[test]
    fn takeover_fences_old_commands_and_pending_commits() {
        let mut state = MatchState::new(AuthorityGeneration::new(1));
        let pending = ready(state.prepare(command(1, 1, 1, 0, 1, 7)).unwrap());
        assert_eq!(
            state.takeover(AuthorityGeneration::new(1)).unwrap(),
            AuthorityGeneration::new(2)
        );
        assert_eq!(
            state.commit(pending, digest(9)).unwrap_err().reason(),
            "stale_pending_generation"
        );
        assert_eq!(
            state
                .prepare(command(2, 1, 1, 0, 1, 8))
                .unwrap_err()
                .reason(),
            "stale_authority_generation"
        );
    }

    #[test]
    fn participant_sequences_are_independent_while_match_version_is_global() {
        let mut state = MatchState::new(AuthorityGeneration::new(1));
        let first = ready(state.prepare(command(1, 1, 1, 0, 1, 7)).unwrap());
        state.commit(first, digest(9)).unwrap();
        let second = ready(state.prepare(command(2, 2, 1, 1, 1, 8)).unwrap());
        let receipt = state.commit(second, digest(10)).unwrap();
        assert_eq!(receipt.participant_sequence, ParticipantSequence::new(1));
        assert_eq!(receipt.committed_version, MatchVersion::new(2));
    }

    #[test]
    fn duplicate_replays_after_takeover_without_reexecution() {
        let mut state = MatchState::new(AuthorityGeneration::new(1));
        let envelope = command(1, 1, 1, 0, 1, 7);
        let pending = ready(state.prepare(envelope.clone()).unwrap());
        let receipt = state.commit(pending, digest(9)).unwrap();
        state.takeover(AuthorityGeneration::new(1)).unwrap();
        assert_eq!(
            state.prepare(envelope).unwrap(),
            PrepareOutcome::Replay(receipt)
        );
    }
}
