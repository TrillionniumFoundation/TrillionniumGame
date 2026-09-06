//! Receipt-reconciled disconnect-effect journal.
//!
//! The journal separates durable intent, worker lease, possible network write,
//! ambiguous completion and authoritative receipt reconciliation.  A worker may
//! retry only before a dispatch could have reached the transport.  Once a
//! dispatch is recorded, timeout or connection loss becomes `Indeterminate`
//! and a second transport write is forbidden until an exact receipt resolves
//! the operation.

use std::collections::BTreeMap;
use std::fmt;

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

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
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
            return Err(DisconnectJournalError::ZeroIdentifier("socket_generation"));
        }
        if self.operation_digest.iter().all(|byte| *byte == 0) {
            return Err(DisconnectJournalError::ZeroDigest("operation_digest"));
        }
        Ok(self)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct DisconnectReceipt {
    pub operation_digest: [u8; 32],
    pub receipt_digest: [u8; 32],
}

impl DisconnectReceipt {
    pub fn validate(self) -> Result<Self, DisconnectJournalError> {
        if self.operation_digest.iter().all(|byte| *byte == 0) {
            return Err(DisconnectJournalError::ZeroDigest(
                "receipt.operation_digest",
            ));
        }
        if self.receipt_digest.iter().all(|byte| *byte == 0) {
            return Err(DisconnectJournalError::ZeroDigest("receipt.receipt_digest"));
        }
        Ok(self)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DisconnectState {
    Pending,
    Leased { worker: WorkerId, token: LeaseToken },
    Dispatched { worker: WorkerId, token: LeaseToken },
    Indeterminate,
    Applied { receipt: [u8; 32] },
    DeadLetter { reason: [u8; 32] },
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
pub enum RetryDisposition {
    Pending,
    DeadLettered,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum DisconnectJournalError {
    InvalidCapacity,
    ZeroIdentifier(&'static str),
    ZeroDigest(&'static str),
    CapacityExceeded { capacity: usize },
    UnknownIntent(DisconnectIntentId),
    ConflictingIntent(DisconnectIntentId),
    NotPending(DisconnectIntentId),
    LeaseMismatch(DisconnectIntentId),
    NotDispatched(DisconnectIntentId),
    AmbiguousCompletionRequiresReconciliation(DisconnectIntentId),
    ReceiptMismatch(DisconnectIntentId),
    Terminal(DisconnectIntentId),
    LeaseGenerationExhausted,
    AttemptExhausted,
}

impl fmt::Display for DisconnectJournalError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidCapacity => {
                formatter.write_str("disconnect journal capacities must be positive")
            }
            Self::ZeroIdentifier(field) => write!(formatter, "{field} must be positive"),
            Self::ZeroDigest(field) => write!(formatter, "{field} must not be the zero digest"),
            Self::CapacityExceeded { capacity } => {
                write!(formatter, "disconnect journal is full at capacity {capacity}")
            }
            Self::UnknownIntent(id) => write!(formatter, "unknown disconnect intent {}", id.get()),
            Self::ConflictingIntent(id) => {
                write!(formatter, "disconnect intent {} has conflicting immutable input", id.get())
            }
            Self::NotPending(id) => write!(formatter, "disconnect intent {} is not pending", id.get()),
            Self::LeaseMismatch(id) => write!(formatter, "disconnect intent {} lease fence mismatch", id.get()),
            Self::NotDispatched(id) => {
                write!(formatter, "disconnect intent {} has not been dispatched", id.get())
            }
            Self::AmbiguousCompletionRequiresReconciliation(id) => write!(
                formatter,
                "disconnect intent {} may have reached the transport and requires receipt reconciliation",
                id.get()
            ),
            Self::ReceiptMismatch(id) => {
                write!(formatter, "disconnect intent {} receipt does not match", id.get())
            }
            Self::Terminal(id) => write!(formatter, "disconnect intent {} is terminal", id.get()),
            Self::LeaseGenerationExhausted => {
                formatter.write_str("disconnect lease generation exhausted")
            }
            Self::AttemptExhausted => formatter.write_str("disconnect attempt counter exhausted"),
        }
    }
}

impl std::error::Error for DisconnectJournalError {}

#[derive(Clone, Debug)]
pub struct DisconnectJournal {
    capacity: usize,
    max_attempts: u32,
    records: BTreeMap<DisconnectIntentId, DisconnectRecord>,
}

impl DisconnectJournal {
    pub fn new(capacity: usize, max_attempts: u32) -> Result<Self, DisconnectJournalError> {
        if capacity == 0 || max_attempts == 0 {
            return Err(DisconnectJournalError::InvalidCapacity);
        }
        Ok(Self {
            capacity,
            max_attempts,
            records: BTreeMap::new(),
        })
    }

    pub fn len(&self) -> usize {
        self.records.len()
    }

    pub fn is_empty(&self) -> bool {
        self.records.is_empty()
    }

    pub fn get(&self, id: DisconnectIntentId) -> Option<DisconnectRecord> {
        self.records.get(&id).copied()
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
        if self.records.len() >= self.capacity {
            return Err(DisconnectJournalError::CapacityExceeded {
                capacity: self.capacity,
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
            DisconnectState::Applied { .. } | DisconnectState::DeadLetter { .. } => {
                return Err(DisconnectJournalError::Terminal(id));
            }
            DisconnectState::Dispatched { .. } | DisconnectState::Indeterminate => {
                return Err(DisconnectJournalError::AmbiguousCompletionRequiresReconciliation(id));
            }
            DisconnectState::Leased { .. } => return Err(DisconnectJournalError::NotPending(id)),
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
    ) -> Result<DisconnectRecord, DisconnectJournalError> {
        let current = self.require_fence(id, worker, token)?;
        let next = DisconnectRecord {
            state: DisconnectState::Dispatched { worker, token },
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
        match current.state {
            DisconnectState::Dispatched {
                worker: owner,
                token: lease,
            } if owner == worker && lease == token => {}
            DisconnectState::Applied { .. } | DisconnectState::DeadLetter { .. } => {
                return Err(DisconnectJournalError::Terminal(id));
            }
            DisconnectState::Indeterminate => return Ok(current),
            _ => return Err(DisconnectJournalError::NotDispatched(id)),
        }
        let next = DisconnectRecord {
            state: DisconnectState::Indeterminate,
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
        if terminal_reason.iter().all(|byte| *byte == 0) {
            return Err(DisconnectJournalError::ZeroDigest("terminal_reason"));
        }
        let current = self.require(id)?;
        match current.state {
            DisconnectState::Dispatched { .. } | DisconnectState::Indeterminate => {
                return Err(DisconnectJournalError::AmbiguousCompletionRequiresReconciliation(id));
            }
            DisconnectState::Applied { .. } | DisconnectState::DeadLetter { .. } => {
                return Err(DisconnectJournalError::Terminal(id));
            }
            _ => {}
        }
        let current = self.require_fence(id, worker, token)?;
        let (state, disposition) = if current.attempt >= self.max_attempts {
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

    pub fn reconcile_receipt(
        &mut self,
        id: DisconnectIntentId,
        receipt: DisconnectReceipt,
    ) -> Result<DisconnectRecord, DisconnectJournalError> {
        let receipt = receipt.validate()?;
        let current = self.require(id)?;
        if receipt.operation_digest != current.operation.operation_digest {
            return Err(DisconnectJournalError::ConflictingIntent(id));
        }
        match current.state {
            DisconnectState::Applied { receipt: existing } => {
                if existing == receipt.receipt_digest {
                    return Ok(current);
                }
                return Err(DisconnectJournalError::ReceiptMismatch(id));
            }
            DisconnectState::Dispatched { .. } | DisconnectState::Indeterminate => {}
            DisconnectState::DeadLetter { .. } => {
                return Err(DisconnectJournalError::Terminal(id));
            }
            DisconnectState::Pending | DisconnectState::Leased { .. } => {
                return Err(DisconnectJournalError::NotDispatched(id));
            }
        }
        let next = DisconnectRecord {
            state: DisconnectState::Applied {
                receipt: receipt.receipt_digest,
            },
            ..current
        };
        self.records.insert(id, next);
        Ok(next)
    }
    fn require(&self, id: DisconnectIntentId) -> Result<DisconnectRecord, DisconnectJournalError> {
        self.records
            .get(&id)
            .copied()
            .ok_or(DisconnectJournalError::UnknownIntent(id))
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
            DisconnectState::Applied { .. } | DisconnectState::DeadLetter { .. } => {
                Err(DisconnectJournalError::Terminal(id))
            }
            DisconnectState::Dispatched { .. } | DisconnectState::Indeterminate => {
                Err(DisconnectJournalError::AmbiguousCompletionRequiresReconciliation(id))
            }
            _ => Err(DisconnectJournalError::LeaseMismatch(id)),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

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

    #[test]
    fn conflicting_duplicate_intent_is_rejected_without_mutation() {
        let mut journal = DisconnectJournal::new(2, 3).unwrap();
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
        let mut journal = DisconnectJournal::new(2, 3).unwrap();
        journal.insert(id(1), operation(7)).unwrap();
        let leased = journal.lease(id(1), worker(1)).unwrap();
        let token = match leased.state {
            DisconnectState::Leased { token, .. } => token,
            _ => panic!("lease state"),
        };
        assert_eq!(
            journal.mark_dispatched(id(1), worker(2), token),
            Err(DisconnectJournalError::LeaseMismatch(id(1)))
        );
        assert_eq!(
            journal.mark_dispatched(id(1), worker(1), LeaseToken(token.get() + 1)),
            Err(DisconnectJournalError::LeaseMismatch(id(1)))
        );
        assert_eq!(journal.get(id(1)), Some(leased));
    }

    #[test]
    fn possible_network_write_forbids_blind_retry_until_receipt() {
        let mut journal = DisconnectJournal::new(2, 3).unwrap();
        journal.insert(id(1), operation(9)).unwrap();
        let leased = journal.lease(id(1), worker(1)).unwrap();
        let token = match leased.state {
            DisconnectState::Leased { token, .. } => token,
            _ => panic!("lease state"),
        };
        journal.mark_dispatched(id(1), worker(1), token).unwrap();
        journal
            .mark_transport_lost(id(1), worker(1), token)
            .unwrap();
        assert_eq!(
            journal.retry_before_dispatch(id(1), worker(1), token, digest(90)),
            Err(DisconnectJournalError::AmbiguousCompletionRequiresReconciliation(id(1)))
        );
        assert_eq!(
            journal.reconcile_receipt(
                id(1),
                DisconnectReceipt {
                    operation_digest: digest(8),
                    receipt_digest: digest(44),
                },
            ),
            Err(DisconnectJournalError::ConflictingIntent(id(1)))
        );
        let applied = journal
            .reconcile_receipt(
                id(1),
                DisconnectReceipt {
                    operation_digest: digest(9),
                    receipt_digest: digest(44),
                },
            )
            .unwrap();
        assert_eq!(
            applied.state,
            DisconnectState::Applied {
                receipt: digest(44)
            }
        );
        assert_eq!(
            journal
                .reconcile_receipt(
                    id(1),
                    DisconnectReceipt {
                        operation_digest: digest(9),
                        receipt_digest: digest(44),
                    },
                )
                .unwrap(),
            applied
        );
        assert_eq!(
            journal.reconcile_receipt(
                id(1),
                DisconnectReceipt {
                    operation_digest: digest(9),
                    receipt_digest: digest(45),
                },
            ),
            Err(DisconnectJournalError::ReceiptMismatch(id(1)))
        );
    }

    #[test]
    fn attempt_limit_is_one_atomic_terminal_transition() {
        let mut journal = DisconnectJournal::new(2, 2).unwrap();
        journal.insert(id(1), operation(1)).unwrap();
        for expected in [RetryDisposition::Pending, RetryDisposition::DeadLettered] {
            let leased = journal.lease(id(1), worker(1)).unwrap();
            let token = match leased.state {
                DisconnectState::Leased { token, .. } => token,
                _ => panic!("lease state"),
            };
            let (_, disposition) = journal
                .retry_before_dispatch(id(1), worker(1), token, digest(90))
                .unwrap();
            assert_eq!(disposition, expected);
        }
        let terminal = journal.get(id(1)).unwrap();
        assert_eq!(
            terminal.state,
            DisconnectState::DeadLetter { reason: digest(90) }
        );
        assert_eq!(
            journal.lease(id(1), worker(1)),
            Err(DisconnectJournalError::Terminal(id(1)))
        );
        assert_eq!(journal.get(id(1)), Some(terminal));
    }
}
