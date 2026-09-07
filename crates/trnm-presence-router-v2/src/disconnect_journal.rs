//! Receipt-reconciled disconnect-effect journal.
//!
//! A transport write is identified by the journal instance and epoch, intent,
//! immutable operation, socket generation, attempt, worker lease and endpoint.
//! Once dispatch may have reached the transport, a second write is forbidden
//! until a trusted verifier returns an exact typed outcome. `Unknown` remains
//! indeterminate; `DefinitelyNotApplied` is the only safe retry path.

use std::collections::BTreeMap;
use std::fmt;

pub const MAX_DISCONNECT_ACTIVE_RECORDS: usize = 65_536;
pub const MAX_DISCONNECT_TOMBSTONES: usize = 262_144;
pub const MAX_DISCONNECT_ATTEMPTS: u32 = 1_024;

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct DisconnectJournalId([u8; 16]);

impl DisconnectJournalId {
    pub fn new(value: [u8; 16]) -> Result<Self, DisconnectJournalError> {
        require_nonzero("journal_id", &value)?;
        Ok(Self(value))
    }

    pub const fn as_bytes(self) -> [u8; 16] {
        self.0
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct DisconnectJournalEpoch(u64);

impl DisconnectJournalEpoch {
    pub fn new(value: u64) -> Result<Self, DisconnectJournalError> {
        if value == 0 {
            return Err(DisconnectJournalError::ZeroIdentifier("journal_epoch"));
        }
        Ok(Self(value))
    }

    pub const fn get(self) -> u64 {
        self.0
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct DisconnectIntentId(u64);

impl DisconnectIntentId {
    pub fn new(value: u64) -> Result<Self, DisconnectJournalError> {
        if value == 0 {
            return Err(DisconnectJournalError::ZeroIdentifier("intent_id"));
        }
        Ok(Self(value))
    }

    pub const fn get(self) -> u64 {
        self.0
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct WorkerId(u64);

impl WorkerId {
    pub fn new(value: u64) -> Result<Self, DisconnectJournalError> {
        if value == 0 {
            return Err(DisconnectJournalError::ZeroIdentifier("worker_id"));
        }
        Ok(Self(value))
    }

    pub const fn get(self) -> u64 {
        self.0
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct LeaseToken(u64);

impl LeaseToken {
    pub const fn get(self) -> u64 {
        self.0
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct DisconnectOperation {
    pub socket_generation: u64,
    pub operation_digest: [u8; 32],
}

impl DisconnectOperation {
    pub fn validate(self) -> Result<Self, DisconnectJournalError> {
        if self.socket_generation == 0 {
            return Err(DisconnectJournalError::ZeroIdentifier(
                "socket_generation",
            ));
        }
        require_nonzero("operation_digest", &self.operation_digest)?;
        Ok(self)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct DisconnectDispatchBinding {
    pub journal_id: DisconnectJournalId,
    pub journal_epoch: DisconnectJournalEpoch,
    pub intent_id: DisconnectIntentId,
    pub operation: DisconnectOperation,
    pub attempt: u32,
    pub worker: WorkerId,
    pub lease_token: LeaseToken,
    pub endpoint_digest: [u8; 32],
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DisconnectOutcomeKind {
    Applied,
    DefinitelyNotApplied,
    Rejected,
    Unknown,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct DisconnectOutcomeEvidence {
    pub binding: DisconnectDispatchBinding,
    pub kind: DisconnectOutcomeKind,
    pub outcome_digest: [u8; 32],
    pub verifier_receipt_digest: [u8; 32],
}

impl DisconnectOutcomeEvidence {
    pub fn validate(self) -> Result<Self, DisconnectJournalError> {
        require_nonzero("outcome_digest", &self.outcome_digest)?;
        require_nonzero(
            "verifier_receipt_digest",
            &self.verifier_receipt_digest,
        )?;
        Ok(self)
    }
}

/// Trusted transport-verification boundary. Production composition must back
/// this with an authenticated receipt/proof verifier. The journal still checks
/// every immutable binding field before invoking it; a boolean supplied by the
/// caller is never accepted as evidence.
pub trait DisconnectOutcomeVerifier {
    fn verify(&self, evidence: &DisconnectOutcomeEvidence) -> bool;
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DisconnectState {
    Pending,
    Leased {
        worker: WorkerId,
        token: LeaseToken,
    },
    Dispatched {
        binding: DisconnectDispatchBinding,
    },
    Indeterminate {
        binding: DisconnectDispatchBinding,
    },
    Applied {
        binding: DisconnectDispatchBinding,
        receipt: [u8; 32],
        verifier_receipt: [u8; 32],
    },
    Rejected {
        binding: DisconnectDispatchBinding,
        reason: [u8; 32],
        verifier_receipt: [u8; 32],
    },
    DeadLetter {
        reason: [u8; 32],
    },
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct DisconnectRecord {
    pub id: DisconnectIntentId,
    pub operation: DisconnectOperation,
    pub attempt: u32,
    pub lease_generation: u64,
    pub state: DisconnectState,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct DisconnectArchiveTombstone {
    pub journal_id: DisconnectJournalId,
    pub journal_epoch: DisconnectJournalEpoch,
    pub id: DisconnectIntentId,
    pub operation: DisconnectOperation,
    pub terminal_digest: [u8; 32],
    pub archive_digest: [u8; 32],
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct DisconnectJournalConfig {
    pub journal_id: DisconnectJournalId,
    pub epoch: DisconnectJournalEpoch,
    pub active_capacity: usize,
    pub tombstone_capacity: usize,
    pub max_attempts: u32,
}

impl DisconnectJournalConfig {
    pub fn validate(self) -> Result<Self, DisconnectJournalError> {
        if self.active_capacity == 0
            || self.tombstone_capacity == 0
            || self.max_attempts == 0
        {
            return Err(DisconnectJournalError::InvalidCapacity);
        }
        if self.active_capacity > MAX_DISCONNECT_ACTIVE_RECORDS {
            return Err(DisconnectJournalError::CapacityTooLarge {
                field: "active_capacity",
                received: self.active_capacity,
                maximum: MAX_DISCONNECT_ACTIVE_RECORDS,
            });
        }
        if self.tombstone_capacity > MAX_DISCONNECT_TOMBSTONES {
            return Err(DisconnectJournalError::CapacityTooLarge {
                field: "tombstone_capacity",
                received: self.tombstone_capacity,
                maximum: MAX_DISCONNECT_TOMBSTONES,
            });
        }
        if self.max_attempts > MAX_DISCONNECT_ATTEMPTS {
            return Err(DisconnectJournalError::AttemptLimitTooLarge {
                received: self.max_attempts,
                maximum: MAX_DISCONNECT_ATTEMPTS,
            });
        }
        Ok(self)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RetryDisposition {
    Pending,
    DeadLettered,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ReconciliationDisposition {
    Applied,
    Pending,
    Rejected,
    Indeterminate,
    DeadLettered,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum DisconnectJournalError {
    InvalidCapacity,
    CapacityTooLarge {
        field: &'static str,
        received: usize,
        maximum: usize,
    },
    AttemptLimitTooLarge {
        received: u32,
        maximum: u32,
    },
    ZeroIdentifier(&'static str),
    ZeroDigest(&'static str),
    ActiveCapacityExceeded {
        capacity: usize,
    },
    TombstoneCapacityExceeded {
        capacity: usize,
    },
    UnknownIntent(DisconnectIntentId),
    ArchivedIntent(DisconnectIntentId),
    ConflictingIntent(DisconnectIntentId),
    NotPending(DisconnectIntentId),
    LeaseMismatch(DisconnectIntentId),
    NotDispatched(DisconnectIntentId),
    AmbiguousCompletionRequiresReconciliation(DisconnectIntentId),
    OutcomeBindingMismatch(DisconnectIntentId),
    OutcomeVerificationFailed(DisconnectIntentId),
    OutcomeMismatch(DisconnectIntentId),
    OutcomeReceiptReused {
        receipt_owner: DisconnectIntentId,
        received_for: DisconnectIntentId,
    },
    VerifierReceiptReused {
        receipt_owner: DisconnectIntentId,
        received_for: DisconnectIntentId,
    },
    Terminal(DisconnectIntentId),
    NotTerminal(DisconnectIntentId),
    LeaseGenerationExhausted,
    AttemptExhausted,
    NonIncreasingJournalEpoch {
        current: DisconnectJournalEpoch,
        received: DisconnectJournalEpoch,
    },
    ActiveRecordsPreventEpochAdvance {
        active: usize,
    },
}

impl fmt::Display for DisconnectJournalError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidCapacity => {
                formatter.write_str("disconnect journal capacities must be positive")
            }
            Self::CapacityTooLarge {
                field,
                received,
                maximum,
            } => write!(formatter, "{field} {received} exceeds hard maximum {maximum}"),
            Self::AttemptLimitTooLarge { received, maximum } => write!(
                formatter,
                "disconnect max attempts {received} exceeds hard maximum {maximum}"
            ),
            Self::ZeroIdentifier(field) => write!(formatter, "{field} must be positive"),
            Self::ZeroDigest(field) => write!(formatter, "{field} must not be the zero digest"),
            Self::ActiveCapacityExceeded { capacity } => {
                write!(formatter, "disconnect journal is full at capacity {capacity}")
            }
            Self::TombstoneCapacityExceeded { capacity } => write!(
                formatter,
                "disconnect tombstone archive is full at capacity {capacity}"
            ),
            Self::UnknownIntent(id) => write!(formatter, "unknown disconnect intent {}", id.get()),
            Self::ArchivedIntent(id) => write!(
                formatter,
                "disconnect intent {} is archived in the current journal epoch",
                id.get()
            ),
            Self::ConflictingIntent(id) => write!(
                formatter,
                "disconnect intent {} has conflicting immutable input",
                id.get()
            ),
            Self::NotPending(id) => {
                write!(formatter, "disconnect intent {} is not pending", id.get())
            }
            Self::LeaseMismatch(id) => write!(
                formatter,
                "disconnect intent {} lease fence mismatch",
                id.get()
            ),
            Self::NotDispatched(id) => write!(
                formatter,
                "disconnect intent {} has not been dispatched",
                id.get()
            ),
            Self::AmbiguousCompletionRequiresReconciliation(id) => write!(
                formatter,
                "disconnect intent {} may have reached the transport and requires reconciliation",
                id.get()
            ),
            Self::OutcomeBindingMismatch(id) => write!(
                formatter,
                "disconnect intent {} outcome binding does not match the exact dispatch",
                id.get()
            ),
            Self::OutcomeVerificationFailed(id) => write!(
                formatter,
                "disconnect intent {} outcome verification failed",
                id.get()
            ),
            Self::OutcomeMismatch(id) => write!(
                formatter,
                "disconnect intent {} outcome conflicts with its terminal result",
                id.get()
            ),
            Self::OutcomeReceiptReused {
                receipt_owner,
                received_for,
            } => write!(
                formatter,
                "applied receipt owned by intent {} was supplied for intent {}",
                receipt_owner.get(),
                received_for.get()
            ),
            Self::VerifierReceiptReused {
                receipt_owner,
                received_for,
            } => write!(
                formatter,
                "verifier receipt owned by intent {} was supplied for intent {}",
                receipt_owner.get(),
                received_for.get()
            ),
            Self::Terminal(id) => write!(formatter, "disconnect intent {} is terminal", id.get()),
            Self::NotTerminal(id) => write!(
                formatter,
                "disconnect intent {} cannot be archived before terminal state",
                id.get()
            ),
            Self::LeaseGenerationExhausted => {
                formatter.write_str("disconnect lease generation exhausted")
            }
            Self::AttemptExhausted => formatter.write_str("disconnect attempt counter exhausted"),
            Self::NonIncreasingJournalEpoch { current, received } => write!(
                formatter,
                "disconnect journal epoch {} does not advance current epoch {}",
                received.get(),
                current.get()
            ),
            Self::ActiveRecordsPreventEpochAdvance { active } => write!(
                formatter,
                "{active} active disconnect records prevent journal epoch advance"
            ),
        }
    }
}

impl std::error::Error for DisconnectJournalError {}

#[derive(Clone, Debug)]
pub struct DisconnectJournal {
    config: DisconnectJournalConfig,
    records: BTreeMap<DisconnectIntentId, DisconnectRecord>,
    tombstones: BTreeMap<DisconnectIntentId, DisconnectArchiveTombstone>,
    outcome_receipt_owners: BTreeMap<[u8; 32], DisconnectIntentId>,
    verifier_receipt_owners: BTreeMap<[u8; 32], DisconnectIntentId>,
    last_epoch_checkpoint: Option<[u8; 32]>,
}

impl DisconnectJournal {
    pub fn new(config: DisconnectJournalConfig) -> Result<Self, DisconnectJournalError> {
        Ok(Self {
            config: config.validate()?,
            records: BTreeMap::new(),
            tombstones: BTreeMap::new(),
            outcome_receipt_owners: BTreeMap::new(),
            verifier_receipt_owners: BTreeMap::new(),
            last_epoch_checkpoint: None,
        })
    }

    pub const fn journal_id(&self) -> DisconnectJournalId {
        self.config.journal_id
    }

    pub const fn epoch(&self) -> DisconnectJournalEpoch {
        self.config.epoch
    }

    pub fn len(&self) -> usize {
        self.records.len()
    }

    pub fn is_empty(&self) -> bool {
        self.records.is_empty()
    }

    pub fn tombstone_len(&self) -> usize {
        self.tombstones.len()
    }

    pub fn get(&self, id: DisconnectIntentId) -> Option<DisconnectRecord> {
        self.records.get(&id).copied()
    }

    pub fn tombstone(&self, id: DisconnectIntentId) -> Option<DisconnectArchiveTombstone> {
        self.tombstones.get(&id).copied()
    }

    pub fn insert(
        &mut self,
        id: DisconnectIntentId,
        operation: DisconnectOperation,
    ) -> Result<DisconnectRecord, DisconnectJournalError> {
        let operation = operation.validate()?;
        if let Some(existing) = self.records.get(&id).copied() {
            if existing.operation == operation {
                return Ok(existing);
            }
            return Err(DisconnectJournalError::ConflictingIntent(id));
        }
        if self.tombstones.contains_key(&id) {
            return Err(DisconnectJournalError::ArchivedIntent(id));
        }
        if self.records.len() >= self.config.active_capacity {
            return Err(DisconnectJournalError::ActiveCapacityExceeded {
                capacity: self.config.active_capacity,
            });
        }
        let record = DisconnectRecord {
            id,
            operation,
            attempt: 0,
            lease_generation: 0,
            state: DisconnectState::Pending,
        };
        self.records.insert(id, record);
        Ok(record)
    }

    pub fn lease(
        &mut self,
        id: DisconnectIntentId,
        worker: WorkerId,
    ) -> Result<DisconnectRecord, DisconnectJournalError> {
        let current = self.require(id)?;
        match current.state {
            DisconnectState::Pending => {}
            DisconnectState::Applied { .. }
            | DisconnectState::Rejected { .. }
            | DisconnectState::DeadLetter { .. } => {
                return Err(DisconnectJournalError::Terminal(id));
            }
            DisconnectState::Dispatched { .. } | DisconnectState::Indeterminate { .. } => {
                return Err(
                    DisconnectJournalError::AmbiguousCompletionRequiresReconciliation(id),
                );
            }
            DisconnectState::Leased { .. } => {
                return Err(DisconnectJournalError::NotPending(id));
            }
        }
        let lease_generation = current
            .lease_generation
            .checked_add(1)
            .ok_or(DisconnectJournalError::LeaseGenerationExhausted)?;
        let attempt = current
            .attempt
            .checked_add(1)
            .ok_or(DisconnectJournalError::AttemptExhausted)?;
        let token = LeaseToken(lease_generation);
        let next = DisconnectRecord {
            lease_generation,
            attempt,
            state: DisconnectState::Leased { worker, token },
            ..current
        };
        self.records.insert(id, next);
        Ok(next)
    }

    pub fn mark_dispatched(
        &mut self,
        id: DisconnectIntentId,
        worker: WorkerId,
        token: LeaseToken,
        endpoint_digest: [u8; 32],
    ) -> Result<DisconnectRecord, DisconnectJournalError> {
        require_nonzero("endpoint_digest", &endpoint_digest)?;
        let current = self.require_fence(id, worker, token)?;
        let binding = DisconnectDispatchBinding {
            journal_id: self.config.journal_id,
            journal_epoch: self.config.epoch,
            intent_id: id,
            operation: current.operation,
            attempt: current.attempt,
            worker,
            lease_token: token,
            endpoint_digest,
        };
        let next = DisconnectRecord {
            state: DisconnectState::Dispatched { binding },
            ..current
        };
        self.records.insert(id, next);
        Ok(next)
    }

    pub fn mark_transport_lost(
        &mut self,
        id: DisconnectIntentId,
        worker: WorkerId,
        token: LeaseToken,
    ) -> Result<DisconnectRecord, DisconnectJournalError> {
        let current = self.require(id)?;
        let binding = match current.state {
            DisconnectState::Dispatched { binding }
                if binding.worker == worker && binding.lease_token == token =>
            {
                binding
            }
            DisconnectState::Indeterminate { binding }
                if binding.worker == worker && binding.lease_token == token =>
            {
                return Ok(current);
            }
            DisconnectState::Applied { .. }
            | DisconnectState::Rejected { .. }
            | DisconnectState::DeadLetter { .. } => {
                return Err(DisconnectJournalError::Terminal(id));
            }
            _ => return Err(DisconnectJournalError::NotDispatched(id)),
        };
        let next = DisconnectRecord {
            state: DisconnectState::Indeterminate { binding },
            ..current
        };
        self.records.insert(id, next);
        Ok(next)
    }

    pub fn retry_before_dispatch(
        &mut self,
        id: DisconnectIntentId,
        worker: WorkerId,
        token: LeaseToken,
        terminal_reason: [u8; 32],
    ) -> Result<(DisconnectRecord, RetryDisposition), DisconnectJournalError> {
        require_nonzero("terminal_reason", &terminal_reason)?;
        let current = self.require(id)?;
        match current.state {
            DisconnectState::Dispatched { .. } | DisconnectState::Indeterminate { .. } => {
                return Err(
                    DisconnectJournalError::AmbiguousCompletionRequiresReconciliation(id),
                );
            }
            DisconnectState::Applied { .. }
            | DisconnectState::Rejected { .. }
            | DisconnectState::DeadLetter { .. } => {
                return Err(DisconnectJournalError::Terminal(id));
            }
            DisconnectState::Pending | DisconnectState::Leased { .. } => {}
        }
        let current = self.require_fence(id, worker, token)?;
        let (state, disposition) = if current.attempt >= self.config.max_attempts {
            (
                DisconnectState::DeadLetter {
                    reason: terminal_reason,
                },
                RetryDisposition::DeadLettered,
            )
        } else {
            (DisconnectState::Pending, RetryDisposition::Pending)
        };
        let next = DisconnectRecord { state, ..current };
        self.records.insert(id, next);
        Ok((next, disposition))
    }

    pub fn reconcile(
        &mut self,
        id: DisconnectIntentId,
        evidence: DisconnectOutcomeEvidence,
        verifier: &impl DisconnectOutcomeVerifier,
    ) -> Result<(DisconnectRecord, ReconciliationDisposition), DisconnectJournalError> {
        let evidence = evidence.validate()?;
        let current = self.require(id)?;

        if let Some(result) = terminal_replay(current, evidence)? {
            return Ok(result);
        }

        let expected_binding = match current.state {
            DisconnectState::Dispatched { binding }
            | DisconnectState::Indeterminate { binding } => binding,
            DisconnectState::Pending | DisconnectState::Leased { .. } => {
                return Err(DisconnectJournalError::NotDispatched(id));
            }
            DisconnectState::Applied { .. }
            | DisconnectState::Rejected { .. }
            | DisconnectState::DeadLetter { .. } => unreachable!("terminal replay handled above"),
        };
        if evidence.binding != expected_binding || evidence.binding.intent_id != id {
            return Err(DisconnectJournalError::OutcomeBindingMismatch(id));
        }
        if !verifier.verify(&evidence) {
            return Err(DisconnectJournalError::OutcomeVerificationFailed(id));
        }
        self.require_unique_verifier_receipt(id, evidence.verifier_receipt_digest)?;

        let (state, disposition) = match evidence.kind {
            DisconnectOutcomeKind::Applied => {
                self.require_unique_outcome_receipt(id, evidence.outcome_digest)?;
                (
                    DisconnectState::Applied {
                        binding: expected_binding,
                        receipt: evidence.outcome_digest,
                        verifier_receipt: evidence.verifier_receipt_digest,
                    },
                    ReconciliationDisposition::Applied,
                )
            }
            DisconnectOutcomeKind::DefinitelyNotApplied => {
                if current.attempt >= self.config.max_attempts {
                    (
                        DisconnectState::DeadLetter {
                            reason: evidence.outcome_digest,
                        },
                        ReconciliationDisposition::DeadLettered,
                    )
                } else {
                    (DisconnectState::Pending, ReconciliationDisposition::Pending)
                }
            }
            DisconnectOutcomeKind::Rejected => (
                DisconnectState::Rejected {
                    binding: expected_binding,
                    reason: evidence.outcome_digest,
                    verifier_receipt: evidence.verifier_receipt_digest,
                },
                ReconciliationDisposition::Rejected,
            ),
            DisconnectOutcomeKind::Unknown => (
                DisconnectState::Indeterminate {
                    binding: expected_binding,
                },
                ReconciliationDisposition::Indeterminate,
            ),
        };

        self.verifier_receipt_owners
            .insert(evidence.verifier_receipt_digest, id);
        if evidence.kind == DisconnectOutcomeKind::Applied {
            self.outcome_receipt_owners
                .insert(evidence.outcome_digest, id);
        }
        let next = DisconnectRecord { state, ..current };
        self.records.insert(id, next);
        Ok((next, disposition))
    }

    pub fn archive_terminal(
        &mut self,
        id: DisconnectIntentId,
        archive_digest: [u8; 32],
    ) -> Result<DisconnectArchiveTombstone, DisconnectJournalError> {
        require_nonzero("archive_digest", &archive_digest)?;
        if self.tombstones.len() >= self.config.tombstone_capacity {
            return Err(DisconnectJournalError::TombstoneCapacityExceeded {
                capacity: self.config.tombstone_capacity,
            });
        }
        let current = self.require(id)?;
        let terminal_digest = match current.state {
            DisconnectState::Applied { receipt, .. } => receipt,
            DisconnectState::Rejected { reason, .. }
            | DisconnectState::DeadLetter { reason } => reason,
            DisconnectState::Pending
            | DisconnectState::Leased { .. }
            | DisconnectState::Dispatched { .. }
            | DisconnectState::Indeterminate { .. } => {
                return Err(DisconnectJournalError::NotTerminal(id));
            }
        };
        let tombstone = DisconnectArchiveTombstone {
            journal_id: self.config.journal_id,
            journal_epoch: self.config.epoch,
            id,
            operation: current.operation,
            terminal_digest,
            archive_digest,
        };
        self.records.remove(&id);
        self.tombstones.insert(id, tombstone);
        Ok(tombstone)
    }

    pub fn advance_epoch(
        &mut self,
        next_epoch: DisconnectJournalEpoch,
        checkpoint_digest: [u8; 32],
    ) -> Result<(), DisconnectJournalError> {
        require_nonzero("checkpoint_digest", &checkpoint_digest)?;
        if !self.records.is_empty() {
            return Err(DisconnectJournalError::ActiveRecordsPreventEpochAdvance {
                active: self.records.len(),
            });
        }
        if next_epoch <= self.config.epoch {
            return Err(DisconnectJournalError::NonIncreasingJournalEpoch {
                current: self.config.epoch,
                received: next_epoch,
            });
        }
        self.config.epoch = next_epoch;
        self.tombstones.clear();
        self.outcome_receipt_owners.clear();
        self.verifier_receipt_owners.clear();
        self.last_epoch_checkpoint = Some(checkpoint_digest);
        Ok(())
    }

    fn require_unique_outcome_receipt(
        &self,
        id: DisconnectIntentId,
        receipt: [u8; 32],
    ) -> Result<(), DisconnectJournalError> {
        if let Some(owner) = self.outcome_receipt_owners.get(&receipt).copied() {
            if owner != id {
                return Err(DisconnectJournalError::OutcomeReceiptReused {
                    receipt_owner: owner,
                    received_for: id,
                });
            }
        }
        Ok(())
    }

    fn require_unique_verifier_receipt(
        &self,
        id: DisconnectIntentId,
        receipt: [u8; 32],
    ) -> Result<(), DisconnectJournalError> {
        if let Some(owner) = self.verifier_receipt_owners.get(&receipt).copied() {
            if owner != id {
                return Err(DisconnectJournalError::VerifierReceiptReused {
                    receipt_owner: owner,
                    received_for: id,
                });
            }
        }
        Ok(())
    }

    fn require(
        &self,
        id: DisconnectIntentId,
    ) -> Result<DisconnectRecord, DisconnectJournalError> {
        self.records
            .get(&id)
            .copied()
            .ok_or_else(|| {
                if self.tombstones.contains_key(&id) {
                    DisconnectJournalError::ArchivedIntent(id)
                } else {
                    DisconnectJournalError::UnknownIntent(id)
                }
            })
    }

    fn require_fence(
        &self,
        id: DisconnectIntentId,
        worker: WorkerId,
        token: LeaseToken,
    ) -> Result<DisconnectRecord, DisconnectJournalError> {
        let current = self.require(id)?;
        match current.state {
            DisconnectState::Leased {
                worker: owner,
                token: lease,
            } if owner == worker && lease == token => Ok(current),
            DisconnectState::Applied { .. }
            | DisconnectState::Rejected { .. }
            | DisconnectState::DeadLetter { .. } => {
                Err(DisconnectJournalError::Terminal(id))
            }
            DisconnectState::Dispatched { .. } | DisconnectState::Indeterminate { .. } => Err(
                DisconnectJournalError::AmbiguousCompletionRequiresReconciliation(id),
            ),
            DisconnectState::Pending | DisconnectState::Leased { .. } => {
                Err(DisconnectJournalError::LeaseMismatch(id))
            }
        }
    }
}

fn terminal_replay(
    current: DisconnectRecord,
    evidence: DisconnectOutcomeEvidence,
) -> Result<Option<(DisconnectRecord, ReconciliationDisposition)>, DisconnectJournalError> {
    let result = match current.state {
        DisconnectState::Applied {
            binding,
            receipt,
            verifier_receipt,
        } => {
            if evidence.kind == DisconnectOutcomeKind::Applied
                && evidence.binding == binding
                && evidence.outcome_digest == receipt
                && evidence.verifier_receipt_digest == verifier_receipt
            {
                Some((current, ReconciliationDisposition::Applied))
            } else {
                return Err(DisconnectJournalError::OutcomeMismatch(current.id));
            }
        }
        DisconnectState::Rejected {
            binding,
            reason,
            verifier_receipt,
        } => {
            if evidence.kind == DisconnectOutcomeKind::Rejected
                && evidence.binding == binding
                && evidence.outcome_digest == reason
                && evidence.verifier_receipt_digest == verifier_receipt
            {
                Some((current, ReconciliationDisposition::Rejected))
            } else {
                return Err(DisconnectJournalError::OutcomeMismatch(current.id));
            }
        }
        DisconnectState::DeadLetter { .. } => {
            return Err(DisconnectJournalError::Terminal(current.id));
        }
        DisconnectState::Pending
        | DisconnectState::Leased { .. }
        | DisconnectState::Dispatched { .. }
        | DisconnectState::Indeterminate { .. } => None,
    };
    Ok(result)
}

fn require_nonzero(field: &'static str, value: &[u8]) -> Result<(), DisconnectJournalError> {
    if value.iter().all(|byte| *byte == 0) {
        Err(DisconnectJournalError::ZeroDigest(field))
    } else {
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Clone, Copy, Debug)]
    struct ExactVerifier {
        accepted_verifier_prefix: u8,
    }

    impl DisconnectOutcomeVerifier for ExactVerifier {
        fn verify(&self, evidence: &DisconnectOutcomeEvidence) -> bool {
            evidence.verifier_receipt_digest[0] == self.accepted_verifier_prefix
        }
    }

    fn journal(active: usize, tombstones: usize, attempts: u32) -> DisconnectJournal {
        DisconnectJournal::new(DisconnectJournalConfig {
            journal_id: DisconnectJournalId::new([7; 16]).unwrap(),
            epoch: DisconnectJournalEpoch::new(1).unwrap(),
            active_capacity: active,
            tombstone_capacity: tombstones,
            max_attempts: attempts,
        })
        .unwrap()
    }

    fn id(value: u64) -> DisconnectIntentId {
        DisconnectIntentId::new(value).unwrap()
    }

    fn worker(value: u64) -> WorkerId {
        WorkerId::new(value).unwrap()
    }

    fn digest(value: u8) -> [u8; 32] {
        [value; 32]
    }

    fn operation(generation: u64) -> DisconnectOperation {
        DisconnectOperation {
            socket_generation: generation,
            operation_digest: digest(generation as u8),
        }
    }

    fn dispatch(
        journal: &mut DisconnectJournal,
        intent: DisconnectIntentId,
        operation: DisconnectOperation,
        worker_id: WorkerId,
        endpoint: u8,
    ) -> DisconnectDispatchBinding {
        journal.insert(intent, operation).unwrap();
        let leased = journal.lease(intent, worker_id).unwrap();
        let token = match leased.state {
            DisconnectState::Leased { token, .. } => token,
            _ => panic!("expected leased state"),
        };
        let dispatched = journal
            .mark_dispatched(intent, worker_id, token, digest(endpoint))
            .unwrap();
        match dispatched.state {
            DisconnectState::Dispatched { binding } => binding,
            _ => panic!("expected dispatched state"),
        }
    }

    fn evidence(
        binding: DisconnectDispatchBinding,
        kind: DisconnectOutcomeKind,
        outcome: u8,
        verifier_receipt: u8,
    ) -> DisconnectOutcomeEvidence {
        DisconnectOutcomeEvidence {
            binding,
            kind,
            outcome_digest: digest(outcome),
            verifier_receipt_digest: digest(verifier_receipt),
        }
    }

    #[test]
    fn conflicting_duplicate_intent_is_rejected_without_mutation() {
        let mut journal = journal(2, 2, 3);
        let original = journal.insert(id(1), operation(1)).unwrap();
        assert_eq!(journal.insert(id(1), operation(1)).unwrap(), original);
        assert_eq!(
            journal.insert(id(1), operation(2)),
            Err(DisconnectJournalError::ConflictingIntent(id(1)))
        );
        assert_eq!(journal.get(id(1)), Some(original));
    }

    #[test]
    fn stale_worker_or_generation_cannot_mark_dispatch() {
        let mut journal = journal(2, 2, 3);
        journal.insert(id(1), operation(7)).unwrap();
        let leased = journal.lease(id(1), worker(1)).unwrap();
        let token = match leased.state {
            DisconnectState::Leased { token, .. } => token,
            _ => panic!("expected leased state"),
        };
        assert_eq!(
            journal.mark_dispatched(id(1), worker(2), token, digest(8)),
            Err(DisconnectJournalError::LeaseMismatch(id(1)))
        );
        assert_eq!(
            journal.mark_dispatched(id(1), worker(1), LeaseToken(token.get() + 1), digest(8)),
            Err(DisconnectJournalError::LeaseMismatch(id(1)))
        );
        assert_eq!(journal.get(id(1)), Some(leased));
    }

    #[test]
    fn possible_network_write_forbids_blind_retry_until_verified_outcome() {
        let mut journal = journal(2, 2, 3);
        let binding = dispatch(&mut journal, id(1), operation(9), worker(1), 8);
        journal
            .mark_transport_lost(id(1), binding.worker, binding.lease_token)
            .unwrap();
        assert_eq!(
            journal.retry_before_dispatch(
                id(1),
                binding.worker,
                binding.lease_token,
                digest(90),
            ),
            Err(DisconnectJournalError::AmbiguousCompletionRequiresReconciliation(
                id(1)
            ))
        );
        let verifier = ExactVerifier {
            accepted_verifier_prefix: 70,
        };
        let (_, disposition) = journal
            .reconcile(
                id(1),
                evidence(
                    binding,
                    DisconnectOutcomeKind::DefinitelyNotApplied,
                    71,
                    70,
                ),
                &verifier,
            )
            .unwrap();
        assert_eq!(disposition, ReconciliationDisposition::Pending);
        let next = journal.lease(id(1), worker(2)).unwrap();
        assert_eq!(next.attempt, 2);
        assert!(next.lease_generation > binding.lease_token.get());
    }

    #[test]
    fn cross_intent_socket_attempt_endpoint_and_epoch_evidence_fail_closed() {
        let verifier = ExactVerifier {
            accepted_verifier_prefix: 70,
        };
        let mut journal = journal(2, 2, 3);
        let binding = dispatch(&mut journal, id(1), operation(9), worker(1), 8);
        let original = journal.get(id(1));

        let mut mutations = Vec::new();
        let mut cross_intent = binding;
        cross_intent.intent_id = id(2);
        mutations.push(cross_intent);
        let mut cross_socket = binding;
        cross_socket.operation.socket_generation += 1;
        mutations.push(cross_socket);
        let mut stale_attempt = binding;
        stale_attempt.attempt += 1;
        mutations.push(stale_attempt);
        let mut wrong_endpoint = binding;
        wrong_endpoint.endpoint_digest = digest(9);
        mutations.push(wrong_endpoint);
        let mut wrong_epoch = binding;
        wrong_epoch.journal_epoch = DisconnectJournalEpoch::new(2).unwrap();
        mutations.push(wrong_epoch);

        for mutated in mutations {
            assert_eq!(
                journal.reconcile(
                    id(1),
                    evidence(mutated, DisconnectOutcomeKind::Applied, 80, 70),
                    &verifier,
                ),
                Err(DisconnectJournalError::OutcomeBindingMismatch(id(1)))
            );
            assert_eq!(journal.get(id(1)), original);
        }
    }

    #[test]
    fn verifier_rejection_and_unknown_outcome_preserve_indeterminate_state() {
        let mut journal = journal(2, 2, 3);
        let binding = dispatch(&mut journal, id(1), operation(9), worker(1), 8);
        journal
            .mark_transport_lost(id(1), binding.worker, binding.lease_token)
            .unwrap();
        let before = journal.get(id(1));
        let rejecting = ExactVerifier {
            accepted_verifier_prefix: 99,
        };
        assert_eq!(
            journal.reconcile(
                id(1),
                evidence(binding, DisconnectOutcomeKind::Applied, 80, 70),
                &rejecting,
            ),
            Err(DisconnectJournalError::OutcomeVerificationFailed(id(1)))
        );
        assert_eq!(journal.get(id(1)), before);

        let accepting = ExactVerifier {
            accepted_verifier_prefix: 70,
        };
        let (record, disposition) = journal
            .reconcile(
                id(1),
                evidence(binding, DisconnectOutcomeKind::Unknown, 81, 70),
                &accepting,
            )
            .unwrap();
        assert_eq!(disposition, ReconciliationDisposition::Indeterminate);
        assert!(matches!(
            record.state,
            DisconnectState::Indeterminate { binding: observed } if observed == binding
        ));
    }

    #[test]
    fn applied_and_rejected_outcomes_are_exact_and_terminal() {
        let verifier = ExactVerifier {
            accepted_verifier_prefix: 70,
        };
        let mut journal = journal(3, 3, 3);
        let applied_binding = dispatch(&mut journal, id(1), operation(9), worker(1), 8);
        let applied_evidence = evidence(
            applied_binding,
            DisconnectOutcomeKind::Applied,
            80,
            70,
        );
        let applied = journal
            .reconcile(id(1), applied_evidence, &verifier)
            .unwrap();
        assert_eq!(applied.1, ReconciliationDisposition::Applied);
        assert_eq!(
            journal.reconcile(id(1), applied_evidence, &verifier)
                .unwrap(),
            applied
        );
        let conflicting = evidence(
            applied_binding,
            DisconnectOutcomeKind::Applied,
            81,
            70,
        );
        assert_eq!(
            journal.reconcile(id(1), conflicting, &verifier),
            Err(DisconnectJournalError::OutcomeMismatch(id(1)))
        );

        let rejected_binding = dispatch(&mut journal, id(2), operation(10), worker(2), 9);
        let rejected = journal
            .reconcile(
                id(2),
                evidence(
                    rejected_binding,
                    DisconnectOutcomeKind::Rejected,
                    90,
                    71,
                ),
                &ExactVerifier {
                    accepted_verifier_prefix: 71,
                },
            )
            .unwrap();
        assert_eq!(rejected.1, ReconciliationDisposition::Rejected);
        assert_eq!(
            journal.lease(id(2), worker(1)),
            Err(DisconnectJournalError::Terminal(id(2)))
        );
    }

    #[test]
    fn outcome_and_verifier_receipts_cannot_cross_intents() {
        let mut journal = journal(3, 3, 3);
        let verifier = ExactVerifier {
            accepted_verifier_prefix: 70,
        };
        let first = dispatch(&mut journal, id(1), operation(9), worker(1), 8);
        let second = dispatch(&mut journal, id(2), operation(10), worker(2), 8);
        journal
            .reconcile(
                id(1),
                evidence(first, DisconnectOutcomeKind::Applied, 80, 70),
                &verifier,
            )
            .unwrap();
        assert_eq!(
            journal.reconcile(
                id(2),
                evidence(second, DisconnectOutcomeKind::Applied, 80, 71),
                &ExactVerifier {
                    accepted_verifier_prefix: 71,
                },
            ),
            Err(DisconnectJournalError::OutcomeReceiptReused {
                receipt_owner: id(1),
                received_for: id(2),
            })
        );
        assert_eq!(
            journal.reconcile(
                id(2),
                evidence(second, DisconnectOutcomeKind::Applied, 81, 70),
                &verifier,
            ),
            Err(DisconnectJournalError::VerifierReceiptReused {
                receipt_owner: id(1),
                received_for: id(2),
            })
        );
    }

    #[test]
    fn archive_frees_active_capacity_but_tombstone_blocks_same_epoch_aba() {
        let mut journal = journal(1, 2, 2);
        let verifier = ExactVerifier {
            accepted_verifier_prefix: 70,
        };
        let binding = dispatch(&mut journal, id(1), operation(9), worker(1), 8);
        journal
            .reconcile(
                id(1),
                evidence(binding, DisconnectOutcomeKind::Applied, 80, 70),
                &verifier,
            )
            .unwrap();
        let tombstone = journal.archive_terminal(id(1), digest(100)).unwrap();
        assert_eq!(journal.len(), 0);
        assert_eq!(journal.tombstone(id(1)), Some(tombstone));
        assert_eq!(
            journal.insert(id(1), operation(11)),
            Err(DisconnectJournalError::ArchivedIntent(id(1)))
        );
        journal.insert(id(2), operation(12)).unwrap();
    }

    #[test]
    fn epoch_advance_requires_no_active_records_and_rejects_old_proof() {
        let mut journal = journal(1, 2, 2);
        let old_binding = dispatch(&mut journal, id(1), operation(9), worker(1), 8);
        assert_eq!(
            journal.advance_epoch(DisconnectJournalEpoch::new(2).unwrap(), digest(110)),
            Err(DisconnectJournalError::ActiveRecordsPreventEpochAdvance { active: 1 })
        );
        let verifier = ExactVerifier {
            accepted_verifier_prefix: 70,
        };
        journal
            .reconcile(
                id(1),
                evidence(old_binding, DisconnectOutcomeKind::Applied, 80, 70),
                &verifier,
            )
            .unwrap();
        journal.archive_terminal(id(1), digest(100)).unwrap();
        journal
            .advance_epoch(DisconnectJournalEpoch::new(2).unwrap(), digest(110))
            .unwrap();

        let new_binding = dispatch(&mut journal, id(1), operation(11), worker(1), 8);
        assert_eq!(new_binding.journal_epoch.get(), 2);
        assert_eq!(
            journal.reconcile(
                id(1),
                evidence(old_binding, DisconnectOutcomeKind::Applied, 80, 70),
                &verifier,
            ),
            Err(DisconnectJournalError::OutcomeBindingMismatch(id(1)))
        );
    }

    #[test]
    fn definitely_not_applied_at_attempt_limit_dead_letters_atomically() {
        let mut journal = journal(1, 1, 1);
        let binding = dispatch(&mut journal, id(1), operation(9), worker(1), 8);
        let (record, disposition) = journal
            .reconcile(
                id(1),
                evidence(
                    binding,
                    DisconnectOutcomeKind::DefinitelyNotApplied,
                    90,
                    70,
                ),
                &ExactVerifier {
                    accepted_verifier_prefix: 70,
                },
            )
            .unwrap();
        assert_eq!(disposition, ReconciliationDisposition::DeadLettered);
        assert_eq!(
            record.state,
            DisconnectState::DeadLetter { reason: digest(90) }
        );
        assert_eq!(
            journal.lease(id(1), worker(1)),
            Err(DisconnectJournalError::Terminal(id(1)))
        );
    }
}
