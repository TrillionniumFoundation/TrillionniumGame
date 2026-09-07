//! Bounded external-signer operation journal with verified epoch compaction.
//!
//! Requests are bound to an explicit journal epoch. Every admitted operation
//! reserves a terminal tombstone slot. Terminal state leaves the current epoch
//! only through a digest-chained checkpoint accepted by a trusted durable
//! archive verifier. Provider receipt uniqueness therefore survives compaction.

use std::collections::BTreeMap;
use std::fmt;

use super::KeyDomain;

pub const MAX_SIGNER_JOURNAL_CAPACITY: usize = 4_096;

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct SignerJournalEpoch(u64);

impl SignerJournalEpoch {
    pub fn new(value: u64) -> Result<Self, SignerJournalError> {
        if value == 0 {
            return Err(SignerJournalError::ZeroEpoch);
        }
        Ok(Self(value))
    }

    pub const fn get(self) -> u64 {
        self.0
    }
}

impl fmt::Display for SignerJournalEpoch {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.fmt(formatter)
    }
}

#[derive(Clone, Copy, Eq, Ord, PartialEq, PartialOrd)]
pub struct SigningOperationId([u8; 16]);

impl SigningOperationId {
    pub fn new(value: [u8; 16]) -> Result<Self, SignerJournalError> {
        if value.iter().all(|byte| *byte == 0) {
            return Err(SignerJournalError::ZeroOperationId);
        }
        Ok(Self(value))
    }
}

impl fmt::Debug for SigningOperationId {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("SigningOperationId(<redacted-operation-id>)")
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct SigningOperationHandle {
    pub journal_epoch: SignerJournalEpoch,
    pub operation_id: SigningOperationId,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SigningRequest {
    pub journal_epoch: SignerJournalEpoch,
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

    pub const fn handle(self) -> SigningOperationHandle {
        SigningOperationHandle {
            journal_epoch: self.journal_epoch,
            operation_id: self.operation_id,
        }
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

impl SigningOperationState {
    pub const fn is_terminal(self) -> bool {
        matches!(self, Self::Confirmed { .. } | Self::Rejected { .. })
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SigningOperationRecord {
    pub request: SigningRequest,
    pub state: SigningOperationState,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SigningOperationTombstone {
    pub request: SigningRequest,
    pub state: SigningOperationState,
    pub archive_digest: [u8; 32],
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SignerReceiptBinding {
    pub receipt_digest: [u8; 32],
    pub operation_id: SigningOperationId,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SignerJournalCheckpoint {
    pub sequence: u64,
    pub previous_checkpoint_digest: Option<[u8; 32]>,
    pub checkpoint_digest: [u8; 32],
    pub retired_epoch: SignerJournalEpoch,
    pub active_epoch: SignerJournalEpoch,
    pub tombstones: Vec<SigningOperationTombstone>,
    pub receipt_bindings: Vec<SignerReceiptBinding>,
}

pub trait SignerJournalArchiveVerifier: fmt::Debug + Send + Sync {
    /// A durable adapter must persist and verify the complete checkpoint before
    /// returning true. A transient signature check alone is insufficient.
    fn verify_checkpoint(&self, checkpoint: &SignerJournalCheckpoint) -> bool;

    /// Return true only when the provider receipt is absent from every durable
    /// checkpoint reachable through the supplied digest chain.
    fn receipt_is_absent(&self, checkpoint: &SignerJournalCheckpoint, candidate: [u8; 32]) -> bool;
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum SignerJournalError {
    ZeroEpoch,
    ZeroCapacity,
    CapacityTooLarge {
        field: &'static str,
        received: usize,
        maximum: usize,
    },
    InvalidCapacityRelationship,
    ZeroOperationId,
    ZeroKeyEpoch,
    ZeroDigest(&'static str),
    CapacityExceeded {
        capacity: usize,
    },
    EpochCapacityExceeded {
        capacity: usize,
    },
    EpochMismatch {
        current: SignerJournalEpoch,
        received: SignerJournalEpoch,
    },
    EpochNotAdvanced {
        current: SignerJournalEpoch,
        received: SignerJournalEpoch,
    },
    UnknownOperation(SigningOperationId),
    ConflictingOperation(SigningOperationId),
    AlreadyDispatched(SigningOperationId),
    NotDispatched(SigningOperationId),
    IndeterminateRequiresReconciliation(SigningOperationId),
    ReceiptMismatch(SigningOperationId),
    ReceiptReused {
        receipt_owner: SigningOperationId,
        received_for: SigningOperationId,
    },
    ArchivedReceiptReused {
        received_for: SigningOperationId,
    },
    ArchiveVerificationRequired,
    ArchiveVerificationFailed,
    Terminal(SigningOperationId),
    ArchiveRequiresTerminal(SigningOperationId),
    ArchiveDigestMismatch(SigningOperationId),
    ActiveRecordsPreventEpochAdvance,
    NoTerminalOperations,
    CheckpointVerificationFailed,
    CheckpointInvalid(&'static str),
    CheckpointSequenceExhausted,
    InvariantViolation(&'static str),
}

impl fmt::Display for SignerJournalError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::ZeroEpoch => formatter.write_str("signer journal epoch must be positive"),
            Self::ZeroCapacity => formatter.write_str("signer journal capacity must be positive"),
            Self::CapacityTooLarge {
                field,
                received,
                maximum,
            } => write!(
                formatter,
                "signer journal {field} {received} exceeds hard maximum {maximum}"
            ),
            Self::InvalidCapacityRelationship => formatter
                .write_str("signer journal active capacity cannot exceed epoch tombstone capacity"),
            Self::ZeroOperationId => formatter.write_str("signing operation id must not be zero"),
            Self::ZeroKeyEpoch => formatter.write_str("signing key epoch must be positive"),
            Self::ZeroDigest(field) => write!(formatter, "{field} must not be the zero digest"),
            Self::CapacityExceeded { capacity } => {
                write!(
                    formatter,
                    "signer journal active set is full at capacity {capacity}"
                )
            }
            Self::EpochCapacityExceeded { capacity } => write!(
                formatter,
                "signer journal epoch is full at terminal capacity {capacity}"
            ),
            Self::EpochMismatch { current, received } => write!(
                formatter,
                "signer journal epoch {received} does not match active epoch {current}"
            ),
            Self::EpochNotAdvanced { current, received } => write!(
                formatter,
                "signer journal epoch must advance exactly from {current} to {}, not {received}",
                current.get().saturating_add(1)
            ),
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
            Self::ReceiptReused { .. } => {
                formatter.write_str("provider receipt is already bound to another operation")
            }
            Self::ArchivedReceiptReused { .. } => {
                formatter.write_str("provider receipt is bound in the durable archive")
            }
            Self::ArchiveVerificationRequired => formatter.write_str(
                "provider receipt requires verification against the durable signer archive",
            ),
            Self::ArchiveVerificationFailed => {
                formatter.write_str("durable signer archive verification failed")
            }
            Self::Terminal(_) => formatter.write_str("signing operation is terminal"),
            Self::ArchiveRequiresTerminal(_) => {
                formatter.write_str("only a terminal signing operation can be archived")
            }
            Self::ArchiveDigestMismatch(_) => {
                formatter.write_str("signing operation archive digest conflicts")
            }
            Self::ActiveRecordsPreventEpochAdvance => formatter
                .write_str("signer journal epoch cannot advance while active records remain"),
            Self::NoTerminalOperations => {
                formatter.write_str("signer journal checkpoint has no terminal operations")
            }
            Self::CheckpointVerificationFailed => {
                formatter.write_str("signer journal checkpoint was not accepted")
            }
            Self::CheckpointInvalid(message) => {
                write!(formatter, "signer journal checkpoint invalid: {message}")
            }
            Self::CheckpointSequenceExhausted => {
                formatter.write_str("signer journal checkpoint sequence exhausted")
            }
            Self::InvariantViolation(message) => {
                write!(formatter, "signer journal invariant violation: {message}")
            }
        }
    }
}

impl std::error::Error for SignerJournalError {}

#[derive(Clone, Debug)]
pub struct SignerJournal {
    active_capacity: usize,
    epoch_capacity: usize,
    epoch: SignerJournalEpoch,
    records: BTreeMap<SigningOperationId, SigningOperationRecord>,
    tombstones: BTreeMap<SigningOperationId, SigningOperationTombstone>,
    receipt_owners: BTreeMap<[u8; 32], SigningOperationId>,
    checkpoint: Option<SignerJournalCheckpoint>,
    next_checkpoint_sequence: u64,
}

impl SignerJournal {
    pub fn new(capacity: usize) -> Result<Self, SignerJournalError> {
        Self::with_limits(capacity, capacity)
    }

    pub fn with_limits(
        active_capacity: usize,
        epoch_capacity: usize,
    ) -> Result<Self, SignerJournalError> {
        validate_capacities(active_capacity, epoch_capacity)?;
        Ok(Self {
            active_capacity,
            epoch_capacity,
            epoch: SignerJournalEpoch(1),
            records: BTreeMap::new(),
            tombstones: BTreeMap::new(),
            receipt_owners: BTreeMap::new(),
            checkpoint: None,
            next_checkpoint_sequence: 1,
        })
    }

    pub fn from_checkpoint(
        active_capacity: usize,
        epoch_capacity: usize,
        checkpoint: SignerJournalCheckpoint,
        verifier: &dyn SignerJournalArchiveVerifier,
    ) -> Result<Self, SignerJournalError> {
        validate_capacities(active_capacity, epoch_capacity)?;
        validate_checkpoint_shape(&checkpoint, epoch_capacity)?;
        if !verifier.verify_checkpoint(&checkpoint) {
            return Err(SignerJournalError::CheckpointVerificationFailed);
        }
        let next_checkpoint_sequence = checkpoint
            .sequence
            .checked_add(1)
            .ok_or(SignerJournalError::CheckpointSequenceExhausted)?;
        Ok(Self {
            active_capacity,
            epoch_capacity,
            epoch: checkpoint.active_epoch,
            records: BTreeMap::new(),
            tombstones: BTreeMap::new(),
            receipt_owners: BTreeMap::new(),
            checkpoint: Some(checkpoint),
            next_checkpoint_sequence,
        })
    }

    pub const fn active_capacity(&self) -> usize {
        self.active_capacity
    }

    pub const fn epoch_capacity(&self) -> usize {
        self.epoch_capacity
    }

    pub const fn capacity(&self) -> usize {
        self.active_capacity
    }

    pub const fn epoch(&self) -> SignerJournalEpoch {
        self.epoch
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

    pub fn receipt_binding_len(&self) -> usize {
        self.receipt_owners.len()
    }

    pub fn checkpoint(&self) -> Option<&SignerJournalCheckpoint> {
        self.checkpoint.as_ref()
    }

    pub fn prepare(
        &mut self,
        request: SigningRequest,
    ) -> Result<SigningOperationRecord, SignerJournalError> {
        self.verify_invariants()?;
        let request = request.validate()?;
        self.require_epoch(request.journal_epoch)?;
        if let Some(existing) = self.records.get(&request.operation_id).copied() {
            if existing.request == request {
                return Ok(existing);
            }
            return Err(SignerJournalError::ConflictingOperation(
                request.operation_id,
            ));
        }
        if let Some(existing) = self.tombstones.get(&request.operation_id) {
            return Err(if existing.request == request {
                SignerJournalError::Terminal(request.operation_id)
            } else {
                SignerJournalError::ConflictingOperation(request.operation_id)
            });
        }
        let epoch_occupancy = self
            .records
            .len()
            .checked_add(self.tombstones.len())
            .ok_or(SignerJournalError::InvariantViolation(
                "epoch occupancy overflow",
            ))?;
        if epoch_occupancy >= self.epoch_capacity {
            return Err(SignerJournalError::EpochCapacityExceeded {
                capacity: self.epoch_capacity,
            });
        }
        if self.records.len() >= self.active_capacity {
            return Err(SignerJournalError::CapacityExceeded {
                capacity: self.active_capacity,
            });
        }
        let record = SigningOperationRecord {
            request,
            state: SigningOperationState::Prepared,
        };
        self.records.insert(request.operation_id, record);
        self.verify_invariants()?;
        Ok(record)
    }

    pub fn dispatch(
        &mut self,
        handle: SigningOperationHandle,
    ) -> Result<SigningOperationRecord, SignerJournalError> {
        let current = self.require(handle)?;
        match current.state {
            SigningOperationState::Prepared => {}
            SigningOperationState::Dispatched => {
                return Err(SignerJournalError::AlreadyDispatched(handle.operation_id));
            }
            SigningOperationState::Indeterminate => {
                return Err(SignerJournalError::IndeterminateRequiresReconciliation(
                    handle.operation_id,
                ));
            }
            SigningOperationState::Confirmed { .. } | SigningOperationState::Rejected { .. } => {
                return Err(SignerJournalError::Terminal(handle.operation_id));
            }
        }
        let next = SigningOperationRecord {
            state: SigningOperationState::Dispatched,
            ..current
        };
        self.records.insert(handle.operation_id, next);
        Ok(next)
    }

    pub fn mark_transport_lost(
        &mut self,
        handle: SigningOperationHandle,
    ) -> Result<SigningOperationRecord, SignerJournalError> {
        let current = self.require(handle)?;
        match current.state {
            SigningOperationState::Dispatched => {}
            SigningOperationState::Indeterminate => return Ok(current),
            SigningOperationState::Prepared => {
                return Err(SignerJournalError::NotDispatched(handle.operation_id));
            }
            SigningOperationState::Confirmed { .. } | SigningOperationState::Rejected { .. } => {
                return Err(SignerJournalError::Terminal(handle.operation_id));
            }
        }
        let next = SigningOperationRecord {
            state: SigningOperationState::Indeterminate,
            ..current
        };
        self.records.insert(handle.operation_id, next);
        Ok(next)
    }

    pub fn reconcile(
        &mut self,
        request: SigningRequest,
        receipt_digest: [u8; 32],
        signature_digest: [u8; 32],
    ) -> Result<SigningOperationRecord, SignerJournalError> {
        self.reconcile_inner(request, receipt_digest, signature_digest, None)
    }

    pub fn reconcile_with_archive_verifier(
        &mut self,
        request: SigningRequest,
        receipt_digest: [u8; 32],
        signature_digest: [u8; 32],
        verifier: &dyn SignerJournalArchiveVerifier,
    ) -> Result<SigningOperationRecord, SignerJournalError> {
        self.reconcile_inner(request, receipt_digest, signature_digest, Some(verifier))
    }

    fn reconcile_inner(
        &mut self,
        request: SigningRequest,
        receipt_digest: [u8; 32],
        signature_digest: [u8; 32],
        verifier: Option<&dyn SignerJournalArchiveVerifier>,
    ) -> Result<SigningOperationRecord, SignerJournalError> {
        let request = request.validate()?;
        self.require_epoch(request.journal_epoch)?;
        require_nonzero_digest("receipt_digest", receipt_digest)?;
        require_nonzero_digest("signature_digest", signature_digest)?;
        let current = self.require(request.handle())?;
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
        if let Some(receipt_owner) = self.receipt_owners.get(&receipt_digest).copied() {
            if receipt_owner != request.operation_id {
                return Err(SignerJournalError::ReceiptReused {
                    receipt_owner,
                    received_for: request.operation_id,
                });
            }
        } else if let Some(checkpoint) = self.checkpoint.as_ref() {
            let verifier = verifier.ok_or(SignerJournalError::ArchiveVerificationRequired)?;
            if !verifier.verify_checkpoint(checkpoint) {
                return Err(SignerJournalError::ArchiveVerificationFailed);
            }
            if !verifier.receipt_is_absent(checkpoint, receipt_digest) {
                return Err(SignerJournalError::ArchivedReceiptReused {
                    received_for: request.operation_id,
                });
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
        self.receipt_owners
            .insert(receipt_digest, request.operation_id);
        self.verify_invariants()?;
        Ok(next)
    }

    pub fn reject_before_dispatch(
        &mut self,
        handle: SigningOperationHandle,
        reason_digest: [u8; 32],
    ) -> Result<SigningOperationRecord, SignerJournalError> {
        require_nonzero_digest("reason_digest", reason_digest)?;
        let current = self.require(handle)?;
        match current.state {
            SigningOperationState::Prepared => {}
            SigningOperationState::Dispatched | SigningOperationState::Indeterminate => {
                return Err(SignerJournalError::IndeterminateRequiresReconciliation(
                    handle.operation_id,
                ));
            }
            SigningOperationState::Confirmed { .. } | SigningOperationState::Rejected { .. } => {
                return Err(SignerJournalError::Terminal(handle.operation_id));
            }
        }
        let next = SigningOperationRecord {
            state: SigningOperationState::Rejected { reason_digest },
            ..current
        };
        self.records.insert(handle.operation_id, next);
        Ok(next)
    }

    pub fn archive_terminal(
        &mut self,
        handle: SigningOperationHandle,
        archive_digest: [u8; 32],
    ) -> Result<SigningOperationTombstone, SignerJournalError> {
        self.require_epoch(handle.journal_epoch)?;
        require_nonzero_digest("archive_digest", archive_digest)?;
        if let Some(existing) = self.tombstones.get(&handle.operation_id).copied() {
            if existing.archive_digest == archive_digest {
                return Ok(existing);
            }
            return Err(SignerJournalError::ArchiveDigestMismatch(
                handle.operation_id,
            ));
        }
        let current = self.require(handle)?;
        if !current.state.is_terminal() {
            return Err(SignerJournalError::ArchiveRequiresTerminal(
                handle.operation_id,
            ));
        }
        if self.tombstones.len() >= self.epoch_capacity {
            return Err(SignerJournalError::InvariantViolation(
                "reserved tombstone capacity was lost",
            ));
        }
        let tombstone = SigningOperationTombstone {
            request: current.request,
            state: current.state,
            archive_digest,
        };
        self.records.remove(&handle.operation_id);
        self.tombstones.insert(handle.operation_id, tombstone);
        self.verify_invariants()?;
        Ok(tombstone)
    }

    pub fn propose_checkpoint(
        &self,
        next_epoch: SignerJournalEpoch,
        checkpoint_digest: [u8; 32],
    ) -> Result<SignerJournalCheckpoint, SignerJournalError> {
        self.verify_invariants()?;
        if !self.records.is_empty() {
            return Err(SignerJournalError::ActiveRecordsPreventEpochAdvance);
        }
        if self.tombstones.is_empty() {
            return Err(SignerJournalError::NoTerminalOperations);
        }
        self.require_next_epoch(next_epoch)?;
        require_nonzero_digest("checkpoint_digest", checkpoint_digest)?;
        let checkpoint = SignerJournalCheckpoint {
            sequence: self.next_checkpoint_sequence,
            previous_checkpoint_digest: self
                .checkpoint
                .as_ref()
                .map(|value| value.checkpoint_digest),
            checkpoint_digest,
            retired_epoch: self.epoch,
            active_epoch: next_epoch,
            tombstones: self.tombstones.values().copied().collect(),
            receipt_bindings: self
                .receipt_owners
                .iter()
                .map(|(receipt_digest, operation_id)| SignerReceiptBinding {
                    receipt_digest: *receipt_digest,
                    operation_id: *operation_id,
                })
                .collect(),
        };
        validate_checkpoint_shape(&checkpoint, self.epoch_capacity)?;
        Ok(checkpoint)
    }

    pub fn advance_epoch(
        &mut self,
        next_epoch: SignerJournalEpoch,
        checkpoint_digest: [u8; 32],
        verifier: &dyn SignerJournalArchiveVerifier,
    ) -> Result<SignerJournalCheckpoint, SignerJournalError> {
        let checkpoint = self.propose_checkpoint(next_epoch, checkpoint_digest)?;
        if !verifier.verify_checkpoint(&checkpoint) {
            return Err(SignerJournalError::CheckpointVerificationFailed);
        }
        let next_sequence = self
            .next_checkpoint_sequence
            .checked_add(1)
            .ok_or(SignerJournalError::CheckpointSequenceExhausted)?;
        self.epoch = next_epoch;
        self.records.clear();
        self.tombstones.clear();
        self.receipt_owners.clear();
        self.checkpoint = Some(checkpoint.clone());
        self.next_checkpoint_sequence = next_sequence;
        self.verify_invariants()?;
        Ok(checkpoint)
    }

    pub fn record(
        &self,
        handle: SigningOperationHandle,
    ) -> Result<Option<SigningOperationRecord>, SignerJournalError> {
        self.require_epoch(handle.journal_epoch)?;
        Ok(self.records.get(&handle.operation_id).copied())
    }

    pub fn tombstone(
        &self,
        handle: SigningOperationHandle,
    ) -> Result<Option<SigningOperationTombstone>, SignerJournalError> {
        self.require_epoch(handle.journal_epoch)?;
        Ok(self.tombstones.get(&handle.operation_id).copied())
    }

    pub fn verify_invariants(&self) -> Result<(), SignerJournalError> {
        if self.records.len() > self.active_capacity {
            return Err(SignerJournalError::InvariantViolation(
                "active records exceed capacity",
            ));
        }
        let epoch_occupancy = self
            .records
            .len()
            .checked_add(self.tombstones.len())
            .ok_or(SignerJournalError::InvariantViolation(
                "epoch occupancy overflow",
            ))?;
        if epoch_occupancy > self.epoch_capacity {
            return Err(SignerJournalError::InvariantViolation(
                "epoch occupancy exceeds terminal capacity",
            ));
        }
        for (operation_id, record) in &self.records {
            if record.request.operation_id != *operation_id
                || record.request.journal_epoch != self.epoch
                || self.tombstones.contains_key(operation_id)
            {
                return Err(SignerJournalError::InvariantViolation(
                    "active operation identity does not bind current epoch",
                ));
            }
        }
        for (operation_id, tombstone) in &self.tombstones {
            if tombstone.request.operation_id != *operation_id
                || tombstone.request.journal_epoch != self.epoch
                || !tombstone.state.is_terminal()
                || tombstone.archive_digest.iter().all(|byte| *byte == 0)
            {
                return Err(SignerJournalError::InvariantViolation(
                    "terminal tombstone is malformed",
                ));
            }
        }
        let mut expected_receipts = BTreeMap::new();
        for record in self.records.values() {
            if let SigningOperationState::Confirmed { receipt_digest, .. } = record.state {
                expected_receipts.insert(receipt_digest, record.request.operation_id);
            }
        }
        for tombstone in self.tombstones.values() {
            if let SigningOperationState::Confirmed { receipt_digest, .. } = tombstone.state {
                expected_receipts.insert(receipt_digest, tombstone.request.operation_id);
            }
        }
        if expected_receipts != self.receipt_owners {
            return Err(SignerJournalError::InvariantViolation(
                "receipt ownership differs from terminal state",
            ));
        }
        if let Some(checkpoint) = self.checkpoint.as_ref() {
            if checkpoint.active_epoch != self.epoch {
                return Err(SignerJournalError::InvariantViolation(
                    "checkpoint does not bind active epoch",
                ));
            }
        }
        Ok(())
    }

    fn require(
        &self,
        handle: SigningOperationHandle,
    ) -> Result<SigningOperationRecord, SignerJournalError> {
        self.require_epoch(handle.journal_epoch)?;
        self.records
            .get(&handle.operation_id)
            .copied()
            .ok_or(SignerJournalError::UnknownOperation(handle.operation_id))
    }

    fn require_epoch(&self, received: SignerJournalEpoch) -> Result<(), SignerJournalError> {
        if received != self.epoch {
            Err(SignerJournalError::EpochMismatch {
                current: self.epoch,
                received,
            })
        } else {
            Ok(())
        }
    }

    fn require_next_epoch(&self, received: SignerJournalEpoch) -> Result<(), SignerJournalError> {
        let expected =
            self.epoch
                .get()
                .checked_add(1)
                .ok_or(SignerJournalError::EpochNotAdvanced {
                    current: self.epoch,
                    received,
                })?;
        if received.get() != expected {
            Err(SignerJournalError::EpochNotAdvanced {
                current: self.epoch,
                received,
            })
        } else {
            Ok(())
        }
    }
}

fn validate_capacities(
    active_capacity: usize,
    epoch_capacity: usize,
) -> Result<(), SignerJournalError> {
    for (field, received) in [
        ("active_capacity", active_capacity),
        ("epoch_capacity", epoch_capacity),
    ] {
        if received == 0 {
            return Err(SignerJournalError::ZeroCapacity);
        }
        if received > MAX_SIGNER_JOURNAL_CAPACITY {
            return Err(SignerJournalError::CapacityTooLarge {
                field,
                received,
                maximum: MAX_SIGNER_JOURNAL_CAPACITY,
            });
        }
    }
    if active_capacity > epoch_capacity {
        return Err(SignerJournalError::InvalidCapacityRelationship);
    }
    Ok(())
}

fn validate_checkpoint_shape(
    checkpoint: &SignerJournalCheckpoint,
    epoch_capacity: usize,
) -> Result<(), SignerJournalError> {
    if checkpoint.sequence == 0 {
        return Err(SignerJournalError::CheckpointInvalid(
            "zero checkpoint sequence",
        ));
    }
    require_nonzero_digest("checkpoint_digest", checkpoint.checkpoint_digest)?;
    if checkpoint
        .previous_checkpoint_digest
        .is_some_and(|digest| digest.iter().all(|byte| *byte == 0))
    {
        return Err(SignerJournalError::CheckpointInvalid(
            "zero previous checkpoint digest",
        ));
    }
    let expected_epoch = checkpoint.retired_epoch.get().checked_add(1).ok_or(
        SignerJournalError::CheckpointInvalid("retired epoch overflow"),
    )?;
    if checkpoint.active_epoch.get() != expected_epoch {
        return Err(SignerJournalError::CheckpointInvalid(
            "active epoch is not the next epoch",
        ));
    }
    if checkpoint.tombstones.is_empty() {
        return Err(SignerJournalError::NoTerminalOperations);
    }
    if checkpoint.tombstones.len() > epoch_capacity {
        return Err(SignerJournalError::CheckpointInvalid(
            "tombstones exceed epoch capacity",
        ));
    }
    let mut tombstones = BTreeMap::new();
    let mut expected_receipts = BTreeMap::new();
    for tombstone in &checkpoint.tombstones {
        if tombstone.request.journal_epoch != checkpoint.retired_epoch
            || !tombstone.state.is_terminal()
            || tombstone.archive_digest.iter().all(|byte| *byte == 0)
            || tombstones
                .insert(tombstone.request.operation_id, *tombstone)
                .is_some()
        {
            return Err(SignerJournalError::CheckpointInvalid(
                "malformed or duplicate tombstone",
            ));
        }
        if let SigningOperationState::Confirmed { receipt_digest, .. } = tombstone.state {
            if expected_receipts
                .insert(receipt_digest, tombstone.request.operation_id)
                .is_some()
            {
                return Err(SignerJournalError::CheckpointInvalid(
                    "duplicate provider receipt",
                ));
            }
        }
    }
    let mut actual_receipts = BTreeMap::new();
    for binding in &checkpoint.receipt_bindings {
        require_nonzero_digest("receipt_digest", binding.receipt_digest)?;
        if actual_receipts
            .insert(binding.receipt_digest, binding.operation_id)
            .is_some()
        {
            return Err(SignerJournalError::CheckpointInvalid(
                "duplicate receipt binding",
            ));
        }
    }
    if actual_receipts != expected_receipts {
        return Err(SignerJournalError::CheckpointInvalid(
            "receipt bindings do not match confirmed tombstones",
        ));
    }
    Ok(())
}

fn require_nonzero_digest(field: &'static str, digest: [u8; 32]) -> Result<(), SignerJournalError> {
    if digest.iter().all(|byte| *byte == 0) {
        Err(SignerJournalError::ZeroDigest(field))
    } else {
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Debug)]
    struct ExactArchiveVerifier {
        digest: u8,
        denied_receipt: Option<[u8; 32]>,
    }

    impl SignerJournalArchiveVerifier for ExactArchiveVerifier {
        fn verify_checkpoint(&self, checkpoint: &SignerJournalCheckpoint) -> bool {
            checkpoint.checkpoint_digest[0] == self.digest
        }

        fn receipt_is_absent(
            &self,
            _checkpoint: &SignerJournalCheckpoint,
            candidate: [u8; 32],
        ) -> bool {
            self.denied_receipt != Some(candidate)
        }
    }

    fn verifier(digest: u8) -> ExactArchiveVerifier {
        ExactArchiveVerifier {
            digest,
            denied_receipt: None,
        }
    }

    fn epoch(value: u64) -> SignerJournalEpoch {
        SignerJournalEpoch::new(value).unwrap()
    }

    fn operation(value: u8) -> SigningOperationId {
        SigningOperationId::new([value; 16]).unwrap()
    }

    fn request(journal_epoch: SignerJournalEpoch, value: u8) -> SigningRequest {
        SigningRequest {
            journal_epoch,
            operation_id: operation(value),
            key_domain: KeyDomain::Authority,
            key_epoch: u64::from(value),
            payload_digest: [value; 32],
        }
    }

    fn confirmed(
        journal: &mut SignerJournal,
        request: SigningRequest,
        receipt: u8,
    ) -> SigningOperationRecord {
        journal.prepare(request).unwrap();
        journal.dispatch(request.handle()).unwrap();
        journal
            .reconcile(request, [receipt; 32], [receipt.wrapping_add(1); 32])
            .unwrap()
    }

    #[test]
    fn immutable_operation_identity_is_idempotent_and_conflicts_fail() {
        let mut journal = SignerJournal::new(2).unwrap();
        let original_request = request(journal.epoch(), 1);
        let original = journal.prepare(original_request).unwrap();
        assert_eq!(journal.prepare(original_request).unwrap(), original);
        let mut conflict = original_request;
        conflict.payload_digest = [2; 32];
        assert_eq!(
            journal.prepare(conflict),
            Err(SignerJournalError::ConflictingOperation(operation(1)))
        );
        assert_eq!(
            journal.record(original_request.handle()).unwrap(),
            Some(original)
        );
    }

    #[test]
    fn response_loss_forbids_a_second_signing_dispatch() {
        let mut journal = SignerJournal::new(2).unwrap();
        let request = request(journal.epoch(), 1);
        journal.prepare(request).unwrap();
        journal.dispatch(request.handle()).unwrap();
        journal.mark_transport_lost(request.handle()).unwrap();
        assert_eq!(
            journal.dispatch(request.handle()),
            Err(SignerJournalError::IndeterminateRequiresReconciliation(
                operation(1)
            ))
        );
        let confirmed = journal.reconcile(request, [7; 32], [8; 32]).unwrap();
        assert_eq!(
            journal.reconcile(request, [7; 32], [8; 32]).unwrap(),
            confirmed
        );
    }

    #[test]
    fn provider_receipt_cannot_be_reused_inside_epoch() {
        let mut journal = SignerJournal::new(2).unwrap();
        let one = request(journal.epoch(), 1);
        let two = request(journal.epoch(), 2);
        for request in [one, two] {
            journal.prepare(request).unwrap();
            journal.dispatch(request.handle()).unwrap();
        }
        journal.reconcile(one, [7; 32], [8; 32]).unwrap();
        assert_eq!(
            journal.reconcile(two, [7; 32], [9; 32]),
            Err(SignerJournalError::ReceiptReused {
                receipt_owner: operation(1),
                received_for: operation(2),
            })
        );
        assert!(matches!(
            journal.record(two.handle()).unwrap().unwrap().state,
            SigningOperationState::Dispatched
        ));
    }

    #[test]
    fn every_admitted_operation_has_a_reserved_archive_slot() {
        let mut journal = SignerJournal::with_limits(2, 2).unwrap();
        let one = request(journal.epoch(), 1);
        let two = request(journal.epoch(), 2);
        confirmed(&mut journal, one, 7);
        journal.prepare(two).unwrap();
        journal
            .reject_before_dispatch(two.handle(), [4; 32])
            .unwrap();
        journal.archive_terminal(one.handle(), [10; 32]).unwrap();
        journal.archive_terminal(two.handle(), [11; 32]).unwrap();
        assert_eq!(journal.len(), 0);
        assert_eq!(journal.tombstone_len(), 2);
        journal.verify_invariants().unwrap();
    }

    #[test]
    fn full_epoch_rejects_new_admission_but_can_advance() {
        let mut journal = SignerJournal::with_limits(1, 1).unwrap();
        let one = request(journal.epoch(), 1);
        confirmed(&mut journal, one, 7);
        journal.archive_terminal(one.handle(), [10; 32]).unwrap();
        let two = request(journal.epoch(), 2);
        assert_eq!(
            journal.prepare(two),
            Err(SignerJournalError::EpochCapacityExceeded { capacity: 1 })
        );
        assert_eq!(journal.len(), 0);
        let checkpoint = journal
            .advance_epoch(epoch(2), [12; 32], &verifier(12))
            .unwrap();
        assert_eq!(checkpoint.tombstones.len(), 1);
        journal.prepare(request(journal.epoch(), 2)).unwrap();
    }

    #[test]
    fn operation_id_reuse_requires_new_epoch_and_old_handle_is_fenced() {
        let mut journal = SignerJournal::with_limits(1, 1).unwrap();
        let old = request(journal.epoch(), 1);
        confirmed(&mut journal, old, 7);
        journal.archive_terminal(old.handle(), [10; 32]).unwrap();
        journal
            .advance_epoch(epoch(2), [12; 32], &verifier(12))
            .unwrap();
        let current = request(journal.epoch(), 1);
        journal.prepare(current).unwrap();
        assert_eq!(
            journal.dispatch(old.handle()),
            Err(SignerJournalError::EpochMismatch {
                current: epoch(2),
                received: epoch(1),
            })
        );
        journal.dispatch(current.handle()).unwrap();
    }

    #[test]
    fn archived_receipt_replay_requires_and_obeys_durable_absence_proof() {
        let mut journal = SignerJournal::with_limits(1, 1).unwrap();
        let old = request(journal.epoch(), 1);
        confirmed(&mut journal, old, 7);
        journal.archive_terminal(old.handle(), [10; 32]).unwrap();
        journal
            .advance_epoch(epoch(2), [12; 32], &verifier(12))
            .unwrap();
        let current = request(journal.epoch(), 2);
        journal.prepare(current).unwrap();
        journal.dispatch(current.handle()).unwrap();
        assert_eq!(
            journal.reconcile(current, [7; 32], [9; 32]),
            Err(SignerJournalError::ArchiveVerificationRequired)
        );
        let deny = ExactArchiveVerifier {
            digest: 12,
            denied_receipt: Some([7; 32]),
        };
        assert_eq!(
            journal.reconcile_with_archive_verifier(current, [7; 32], [9; 32], &deny),
            Err(SignerJournalError::ArchivedReceiptReused {
                received_for: operation(2),
            })
        );
        journal
            .reconcile_with_archive_verifier(current, [8; 32], [9; 32], &verifier(12))
            .unwrap();
    }

    #[test]
    fn checkpoint_restore_preserves_epoch_and_archive_chain() {
        let mut journal = SignerJournal::with_limits(1, 1).unwrap();
        let request = request(journal.epoch(), 1);
        confirmed(&mut journal, request, 7);
        journal
            .archive_terminal(request.handle(), [10; 32])
            .unwrap();
        let checkpoint = journal
            .advance_epoch(epoch(2), [12; 32], &verifier(12))
            .unwrap();
        let restored =
            SignerJournal::from_checkpoint(1, 1, checkpoint.clone(), &verifier(12)).unwrap();
        assert_eq!(restored.epoch(), epoch(2));
        assert_eq!(restored.checkpoint(), Some(&checkpoint));
        restored.verify_invariants().unwrap();
    }

    #[test]
    fn unverified_checkpoint_fails_without_mutation() {
        let mut journal = SignerJournal::with_limits(1, 1).unwrap();
        let request = request(journal.epoch(), 1);
        confirmed(&mut journal, request, 7);
        journal
            .archive_terminal(request.handle(), [10; 32])
            .unwrap();
        let before_epoch = journal.epoch();
        let before_tombstones = journal.tombstone_len();
        assert_eq!(
            journal.advance_epoch(epoch(2), [12; 32], &verifier(13)),
            Err(SignerJournalError::CheckpointVerificationFailed)
        );
        assert_eq!(journal.epoch(), before_epoch);
        assert_eq!(journal.tombstone_len(), before_tombstones);
    }

    #[test]
    fn epoch_advance_requires_all_active_records_to_be_terminal_and_archived() {
        let mut journal = SignerJournal::with_limits(1, 1).unwrap();
        let request = request(journal.epoch(), 1);
        journal.prepare(request).unwrap();
        assert_eq!(
            journal.advance_epoch(epoch(2), [12; 32], &verifier(12)),
            Err(SignerJournalError::ActiveRecordsPreventEpochAdvance)
        );
        assert_eq!(journal.epoch(), epoch(1));
    }

    #[test]
    fn archive_is_idempotent_only_for_exact_digest() {
        let mut journal = SignerJournal::with_limits(1, 1).unwrap();
        let request = request(journal.epoch(), 1);
        journal.prepare(request).unwrap();
        journal
            .reject_before_dispatch(request.handle(), [4; 32])
            .unwrap();
        let archived = journal
            .archive_terminal(request.handle(), [10; 32])
            .unwrap();
        assert_eq!(
            journal
                .archive_terminal(request.handle(), [10; 32])
                .unwrap(),
            archived
        );
        assert_eq!(
            journal.archive_terminal(request.handle(), [11; 32]),
            Err(SignerJournalError::ArchiveDigestMismatch(operation(1)))
        );
    }

    #[test]
    fn capacities_and_relationship_are_hard_bounded() {
        assert!(matches!(
            SignerJournal::new(MAX_SIGNER_JOURNAL_CAPACITY + 1),
            Err(SignerJournalError::CapacityTooLarge { .. })
        ));
        assert_eq!(
            SignerJournal::with_limits(2, 1).unwrap_err(),
            SignerJournalError::InvalidCapacityRelationship
        );
        let mut journal = SignerJournal::with_limits(1, 2).unwrap();
        let one = request(journal.epoch(), 1);
        let two = request(journal.epoch(), 2);
        journal.prepare(one).unwrap();
        assert_eq!(
            journal.prepare(two),
            Err(SignerJournalError::CapacityExceeded { capacity: 1 })
        );
        assert_eq!(journal.len(), 1);
    }
}
