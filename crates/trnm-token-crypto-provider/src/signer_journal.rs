//! Bounded external-signer operation journal.
//!
//! A payload may be dispatched at most once.  Timeout after dispatch
//! is represented as an indeterminate result and can only be resolved
//! by an exact operation-bound provider receipt.

use std::collections::BTreeMap;
use std::fmt;

use super::KeyDomain;

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct SigningOperationId([u8; 16]);

impl SigningOperationId {
    pub fn new(value: [u8; 16]) -> Result<Self, SignerJournalError> {
        if value.iter().all(|byte| *byte == 0) {
            return Err(SignerJournalError::ZeroOperationId);
        }
        Ok(Self(value))
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SigningRequest {
    pub operation_id: SigningOperationId,
    pub key_domain: KeyDomain,
    pub key_epoch: u64,
    pub payload_digest: [u8; 32],
}

impl SigningRequest {
    pub fn validate(self) -> Result<Self, SignerJournalError> {
        if self.key_epoch == 0 {
            return Err(SignerJournalError::ZeroKeyEpoch);
        }
        if self.payload_digest.iter().all(|byte| *byte == 0) {
            return Err(SignerJournalError::ZeroDigest("payload_digest"));
        }
        Ok(self)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SigningOperationState {
    Prepared,
    Dispatched,
    Indeterminate,
    Confirmed {
        receipt_digest: [u8; 32],
        signature_digest: [u8; 32],
    },
    Rejected {
        reason_digest: [u8; 32],
    },
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SigningOperationRecord {
    pub request: SigningRequest,
    pub state: SigningOperationState,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum SignerJournalError {
    ZeroCapacity,
    ZeroOperationId,
    ZeroKeyEpoch,
    ZeroDigest(&'static str),
    CapacityExceeded { capacity: usize },
    UnknownOperation(SigningOperationId),
    ConflictingOperation(SigningOperationId),
    AlreadyDispatched(SigningOperationId),
    NotDispatched(SigningOperationId),
    IndeterminateRequiresReconciliation(SigningOperationId),
    ReceiptMismatch(SigningOperationId),
    Terminal(SigningOperationId),
}

impl fmt::Display for SignerJournalError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::ZeroCapacity => formatter.write_str("signer journal capacity must be positive"),
            Self::ZeroOperationId => formatter.write_str("signing operation id must not be zero"),
            Self::ZeroKeyEpoch => formatter.write_str("signing key epoch must be positive"),
            Self::ZeroDigest(field) => write!(formatter, "{field} must not be the zero digest"),
            Self::CapacityExceeded { capacity } => {
                write!(formatter, "signer journal is full at capacity {capacity}")
            }
            Self::UnknownOperation(_) => formatter.write_str("signing operation is not registered"),
            Self::ConflictingOperation(_) => {
                formatter.write_str("signing operation immutable input conflicts")
            }
            Self::AlreadyDispatched(_) => {
                formatter.write_str("signing operation was already dispatched")
            }
            Self::NotDispatched(_) => {
                formatter.write_str("signing operation has not been dispatched")
            }
            Self::IndeterminateRequiresReconciliation(_) => formatter.write_str(
                "signing operation may have completed and requires provider receipt reconciliation",
            ),
            Self::ReceiptMismatch(_) => {
                formatter.write_str("signing operation receipt conflicts with confirmed result")
            }
            Self::Terminal(_) => formatter.write_str("signing operation is terminal"),
        }
    }
}

impl std::error::Error for SignerJournalError {}

#[derive(Clone, Debug)]
pub struct SignerJournal {
    capacity: usize,
    records: BTreeMap<SigningOperationId, SigningOperationRecord>,
}

impl SignerJournal {
    pub fn new(capacity: usize) -> Result<Self, SignerJournalError> {
        if capacity == 0 {
            return Err(SignerJournalError::ZeroCapacity);
        }
        Ok(Self {
            capacity,
            records: BTreeMap::new(),
        })
    }

    pub fn len(&self) -> usize {
        self.records.len()
    }

    pub fn is_empty(&self) -> bool {
        self.records.is_empty()
    }

    pub fn prepare(
        &mut self,
        request: SigningRequest,
    ) -> Result<SigningOperationRecord, SignerJournalError> {
        let request = request.validate()?;
        if let Some(existing) = self.records.get(&request.operation_id).copied() {
            if existing.request == request {
                return Ok(existing);
            }
            return Err(SignerJournalError::ConflictingOperation(
                request.operation_id,
            ));
        }
        if self.records.len() >= self.capacity {
            return Err(SignerJournalError::CapacityExceeded {
                capacity: self.capacity,
            });
        }
        let record = SigningOperationRecord {
            request,
            state: SigningOperationState::Prepared,
        };
        self.records.insert(request.operation_id, record);
        Ok(record)
    }

    pub fn dispatch(
        &mut self,
        operation_id: SigningOperationId,
    ) -> Result<SigningOperationRecord, SignerJournalError> {
        let current = self.require(operation_id)?;
        match current.state {
            SigningOperationState::Prepared => {}
            SigningOperationState::Dispatched => {
                return Err(SignerJournalError::AlreadyDispatched(operation_id));
            }
            SigningOperationState::Indeterminate => {
                return Err(SignerJournalError::IndeterminateRequiresReconciliation(
                    operation_id,
                ));
            }
            SigningOperationState::Confirmed { .. } | SigningOperationState::Rejected { .. } => {
                return Err(SignerJournalError::Terminal(operation_id));
            }
        }
        let next = SigningOperationRecord {
            state: SigningOperationState::Dispatched,
            ..current
        };
        self.records.insert(operation_id, next);
        Ok(next)
    }

    pub fn mark_transport_lost(
        &mut self,
        operation_id: SigningOperationId,
    ) -> Result<SigningOperationRecord, SignerJournalError> {
        let current = self.require(operation_id)?;
        match current.state {
            SigningOperationState::Dispatched => {}
            SigningOperationState::Indeterminate => return Ok(current),
            SigningOperationState::Prepared => {
                return Err(SignerJournalError::NotDispatched(operation_id));
            }
            SigningOperationState::Confirmed { .. } | SigningOperationState::Rejected { .. } => {
                return Err(SignerJournalError::Terminal(operation_id));
            }
        }
        let next = SigningOperationRecord {
            state: SigningOperationState::Indeterminate,
            ..current
        };
        self.records.insert(operation_id, next);
        Ok(next)
    }

    pub fn reconcile(
        &mut self,
        request: SigningRequest,
        receipt_digest: [u8; 32],
        signature_digest: [u8; 32],
    ) -> Result<SigningOperationRecord, SignerJournalError> {
        let request = request.validate()?;
        if receipt_digest.iter().all(|byte| *byte == 0) {
            return Err(SignerJournalError::ZeroDigest("receipt_digest"));
        }
        if signature_digest.iter().all(|byte| *byte == 0) {
            return Err(SignerJournalError::ZeroDigest("signature_digest"));
        }
        let current = self.require(request.operation_id)?;
        if current.request != request {
            return Err(SignerJournalError::ConflictingOperation(
                request.operation_id,
            ));
        }
        match current.state {
            SigningOperationState::Dispatched | SigningOperationState::Indeterminate => {}
            SigningOperationState::Confirmed {
                receipt_digest: existing_receipt,
                signature_digest: existing_signature,
            } => {
                if existing_receipt == receipt_digest && existing_signature == signature_digest {
                    return Ok(current);
                }
                return Err(SignerJournalError::ReceiptMismatch(request.operation_id));
            }
            SigningOperationState::Prepared => {
                return Err(SignerJournalError::NotDispatched(request.operation_id));
            }
            SigningOperationState::Rejected { .. } => {
                return Err(SignerJournalError::Terminal(request.operation_id));
            }
        }
        let next = SigningOperationRecord {
            state: SigningOperationState::Confirmed {
                receipt_digest,
                signature_digest,
            },
            ..current
        };
        self.records.insert(request.operation_id, next);
        Ok(next)
    }

    pub fn reject_before_dispatch(
        &mut self,
        operation_id: SigningOperationId,
        reason_digest: [u8; 32],
    ) -> Result<SigningOperationRecord, SignerJournalError> {
        if reason_digest.iter().all(|byte| *byte == 0) {
            return Err(SignerJournalError::ZeroDigest("reason_digest"));
        }
        let current = self.require(operation_id)?;
        match current.state {
            SigningOperationState::Prepared => {}
            SigningOperationState::Dispatched | SigningOperationState::Indeterminate => {
                return Err(SignerJournalError::IndeterminateRequiresReconciliation(
                    operation_id,
                ));
            }
            SigningOperationState::Confirmed { .. } | SigningOperationState::Rejected { .. } => {
                return Err(SignerJournalError::Terminal(operation_id));
            }
        }
        let next = SigningOperationRecord {
            state: SigningOperationState::Rejected { reason_digest },
            ..current
        };
        self.records.insert(operation_id, next);
        Ok(next)
    }

    pub fn record(&self, operation_id: SigningOperationId) -> Option<SigningOperationRecord> {
        self.records.get(&operation_id).copied()
    }

    fn require(
        &self,
        operation_id: SigningOperationId,
    ) -> Result<SigningOperationRecord, SignerJournalError> {
        self.records
            .get(&operation_id)
            .copied()
            .ok_or(SignerJournalError::UnknownOperation(operation_id))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn operation(value: u8) -> SigningOperationId {
        SigningOperationId::new([value; 16]).unwrap()
    }

    fn request(value: u8) -> SigningRequest {
        SigningRequest {
            operation_id: operation(value),
            key_domain: KeyDomain::Authority,
            key_epoch: u64::from(value),
            payload_digest: [value; 32],
        }
    }

    #[test]
    fn immutable_operation_identity_is_idempotent_and_conflicts_fail() {
        let mut journal = SignerJournal::new(2).unwrap();
        let original = journal.prepare(request(1)).unwrap();
        assert_eq!(journal.prepare(request(1)).unwrap(), original);
        let mut conflict = request(1);
        conflict.payload_digest = [2; 32];
        assert_eq!(
            journal.prepare(conflict),
            Err(SignerJournalError::ConflictingOperation(operation(1)))
        );
        assert_eq!(journal.record(operation(1)), Some(original));

        let mut cross_domain = request(1);
        cross_domain.key_domain = KeyDomain::Socket;
        assert_eq!(
            journal.prepare(cross_domain),
            Err(SignerJournalError::ConflictingOperation(operation(1)))
        );
        assert_eq!(journal.record(operation(1)), Some(original));
    }

    #[test]
    fn response_loss_forbids_a_second_signing_dispatch() {
        let mut journal = SignerJournal::new(2).unwrap();
        journal.prepare(request(1)).unwrap();
        journal.dispatch(operation(1)).unwrap();
        journal.mark_transport_lost(operation(1)).unwrap();
        assert_eq!(
            journal.dispatch(operation(1)),
            Err(SignerJournalError::IndeterminateRequiresReconciliation(
                operation(1)
            ))
        );
        let confirmed = journal.reconcile(request(1), [7; 32], [8; 32]).unwrap();
        assert_eq!(
            journal.reconcile(request(1), [7; 32], [8; 32]).unwrap(),
            confirmed
        );
    }

    #[test]
    fn wrong_operation_or_receipt_cannot_reconcile() {
        let mut journal = SignerJournal::new(2).unwrap();
        journal.prepare(request(1)).unwrap();
        journal.dispatch(operation(1)).unwrap();
        let mut conflict = request(1);
        conflict.payload_digest = [3; 32];
        assert_eq!(
            journal.reconcile(conflict, [7; 32], [8; 32]),
            Err(SignerJournalError::ConflictingOperation(operation(1)))
        );
        journal.reconcile(request(1), [7; 32], [8; 32]).unwrap();
        assert_eq!(
            journal.reconcile(request(1), [9; 32], [8; 32]),
            Err(SignerJournalError::ReceiptMismatch(operation(1)))
        );
    }

    #[test]
    fn rejection_is_allowed_only_before_dispatch() {
        let mut journal = SignerJournal::new(2).unwrap();
        journal.prepare(request(1)).unwrap();
        journal
            .reject_before_dispatch(operation(1), [4; 32])
            .unwrap();
        assert_eq!(
            journal.dispatch(operation(1)),
            Err(SignerJournalError::Terminal(operation(1)))
        );
    }
}
