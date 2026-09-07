#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

KEY_EPOCH_SOURCE = r'''//! Bounded monotonic operational key-epoch lifecycle with verified archive rollover.
//!
//! The registry stores opaque key identifiers only. Terminal records may leave
//! the operational window only through an externally verified, digest-chained
//! checkpoint. New key identifiers are checked against that durable archive.

use std::collections::{BTreeMap, BTreeSet};
use std::fmt;

pub const MAX_OPERATIONAL_KEY_EPOCHS: usize = 8;

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct KeyEpoch(u64);

impl KeyEpoch {
    pub fn new(value: u64) -> Result<Self, KeyEpochError> {
        if value == 0 {
            return Err(KeyEpochError::ZeroEpoch);
        }
        Ok(Self(value))
    }

    pub const fn get(self) -> u64 {
        self.0
    }
}

#[derive(Clone, Copy, Eq, Ord, PartialEq, PartialOrd)]
pub struct KeyId([u8; 16]);

impl KeyId {
    pub fn new(value: [u8; 16]) -> Result<Self, KeyEpochError> {
        if value.iter().all(|byte| *byte == 0) {
            return Err(KeyEpochError::ZeroKeyId);
        }
        Ok(Self(value))
    }

    pub const fn as_bytes(&self) -> &[u8; 16] {
        &self.0
    }
}

impl fmt::Debug for KeyId {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("KeyId(<redacted-key-id>)")
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum KeyEpochState {
    Active,
    VerifyOnly { retire_after_unix_seconds: i64 },
    Retired { retired_at_unix_seconds: i64 },
    Revoked { revoked_at_unix_seconds: i64 },
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct KeyEpochRecord {
    pub epoch: KeyEpoch,
    pub key_id: KeyId,
    pub state: KeyEpochState,
    pub activated_at_unix_seconds: i64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct KeyEpochArchiveCheckpoint {
    pub sequence: u64,
    pub previous_archive_digest: Option<[u8; 32]>,
    pub archive_digest: [u8; 32],
    pub highest_epoch: KeyEpoch,
    pub last_lifecycle_time: i64,
    pub authority_lost_epoch: Option<KeyEpoch>,
    pub active: Option<KeyEpoch>,
    pub retained_records: Vec<KeyEpochRecord>,
    pub archived_records: Vec<KeyEpochRecord>,
}

pub trait KeyEpochArchiveVerifier: fmt::Debug + Send + Sync {
    fn verify_checkpoint(&self, checkpoint: &KeyEpochArchiveCheckpoint) -> bool;

    fn key_id_is_absent(
        &self,
        checkpoint: &KeyEpochArchiveCheckpoint,
        candidate: KeyId,
    ) -> bool;
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum KeyEpochError {
    ZeroEpoch,
    ZeroKeyId,
    ZeroCapacity,
    CapacityTooLarge {
        received: usize,
        maximum: usize,
    },
    CapacityExceeded {
        capacity: usize,
    },
    AlreadyInitialized,
    NotInitialized,
    EpochNotFound(KeyEpoch),
    EpochArchived {
        epoch: KeyEpoch,
        archive_digest: [u8; 32],
    },
    NonContiguousRotation {
        current: KeyEpoch,
        received: KeyEpoch,
    },
    KeyIdReused(KeyId),
    ActivationTimeRegression {
        previous: i64,
        received: i64,
    },
    InvalidOverlap,
    NoActiveSigner,
    SignerNotYetActive {
        epoch: KeyEpoch,
        activated_at_unix_seconds: i64,
        observed_at_unix_seconds: i64,
    },
    EpochNotUsableForVerification(KeyEpoch),
    EpochRevoked(KeyEpoch),
    RevocationBeforeActivation {
        epoch: KeyEpoch,
        activated_at_unix_seconds: i64,
        received: i64,
    },
    ReplacementRequiresRevokedEpoch(KeyEpoch),
    EpochTerminal(KeyEpoch),
    ZeroArchiveDigest,
    NoTerminalEpochs,
    ArchiveVerificationRequired,
    ArchiveVerificationFailed,
    CheckpointInvalid(&'static str),
    CheckpointVerificationFailed,
    ArchiveSequenceExhausted,
}

impl fmt::Display for KeyEpochError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::ZeroEpoch => formatter.write_str("key epoch must be positive"),
            Self::ZeroKeyId => formatter.write_str("key id must not be zero"),
            Self::ZeroCapacity => formatter.write_str("key epoch capacity must be positive"),
            Self::CapacityTooLarge { received, maximum } => write!(
                formatter,
                "key epoch capacity {received} exceeds hard maximum {maximum}"
            ),
            Self::CapacityExceeded { capacity } => {
                write!(formatter, "key epoch registry is full at capacity {capacity}")
            }
            Self::AlreadyInitialized => {
                formatter.write_str("key epoch registry is already initialized")
            }
            Self::NotInitialized => formatter.write_str("key epoch registry is not initialized"),
            Self::EpochNotFound(epoch) => {
                write!(formatter, "key epoch {} is not registered", epoch.get())
            }
            Self::EpochArchived { epoch, .. } => write!(
                formatter,
                "key epoch {} requires durable archive verification",
                epoch.get()
            ),
            Self::NonContiguousRotation { current, received } => write!(
                formatter,
                "key rotation must advance exactly one epoch from {} to {}, not {}",
                current.get(),
                current.get().saturating_add(1),
                received.get()
            ),
            Self::KeyIdReused(_) => formatter.write_str("key id was already used"),
            Self::ActivationTimeRegression { previous, received } => write!(
                formatter,
                "key activation time {received} does not advance previous time {previous}"
            ),
            Self::InvalidOverlap => {
                formatter.write_str("verify-only overlap must end after new activation")
            }
            Self::NoActiveSigner => formatter.write_str("no active signing epoch exists"),
            Self::SignerNotYetActive {
                epoch,
                activated_at_unix_seconds,
                observed_at_unix_seconds,
            } => write!(
                formatter,
                "key epoch {} activates at {}, after observed time {}",
                epoch.get(),
                activated_at_unix_seconds,
                observed_at_unix_seconds
            ),
            Self::EpochNotUsableForVerification(epoch) => write!(
                formatter,
                "key epoch {} is outside its verification window",
                epoch.get()
            ),
            Self::EpochRevoked(epoch) => write!(formatter, "key epoch {} is revoked", epoch.get()),
            Self::RevocationBeforeActivation {
                epoch,
                activated_at_unix_seconds,
                received,
            } => write!(
                formatter,
                "key epoch {} cannot be revoked at {} before activation {}",
                epoch.get(),
                received,
                activated_at_unix_seconds
            ),
            Self::ReplacementRequiresRevokedEpoch(epoch) => write!(
                formatter,
                "replacement after authority loss requires highest epoch {} to be revoked",
                epoch.get()
            ),
            Self::EpochTerminal(epoch) => {
                write!(formatter, "key epoch {} is terminal", epoch.get())
            }
            Self::ZeroArchiveDigest => {
                formatter.write_str("key epoch archive digest must not be zero")
            }
            Self::NoTerminalEpochs => {
                formatter.write_str("key epoch archive has no terminal records")
            }
            Self::ArchiveVerificationRequired => formatter.write_str(
                "new key identity requires verification against the durable archive",
            ),
            Self::ArchiveVerificationFailed => {
                formatter.write_str("key epoch archive verification failed")
            }
            Self::CheckpointInvalid(message) => {
                write!(formatter, "key epoch checkpoint invalid: {message}")
            }
            Self::CheckpointVerificationFailed => {
                formatter.write_str("key epoch checkpoint was not accepted")
            }
            Self::ArchiveSequenceExhausted => {
                formatter.write_str("key epoch archive sequence exhausted")
            }
        }
    }
}

impl std::error::Error for KeyEpochError {}

#[derive(Clone, Debug)]
pub struct KeyEpochRegistry {
    capacity: usize,
    active: Option<KeyEpoch>,
    records: BTreeMap<KeyEpoch, KeyEpochRecord>,
    used_key_ids: BTreeSet<KeyId>,
    highest_epoch: Option<KeyEpoch>,
    last_lifecycle_time: Option<i64>,
    authority_lost_epoch: Option<KeyEpoch>,
    checkpoint: Option<KeyEpochArchiveCheckpoint>,
    next_archive_sequence: u64,
}

impl Default for KeyEpochRegistry {
    fn default() -> Self {
        Self {
            capacity: MAX_OPERATIONAL_KEY_EPOCHS,
            active: None,
            records: BTreeMap::new(),
            used_key_ids: BTreeSet::new(),
            highest_epoch: None,
            last_lifecycle_time: None,
            authority_lost_epoch: None,
            checkpoint: None,
            next_archive_sequence: 1,
        }
    }
}

impl KeyEpochRegistry {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_capacity(capacity: usize) -> Result<Self, KeyEpochError> {
        validate_capacity(capacity)?;
        Ok(Self {
            capacity,
            ..Self::default()
        })
    }

    pub fn from_checkpoint(
        capacity: usize,
        checkpoint: KeyEpochArchiveCheckpoint,
        verifier: &dyn KeyEpochArchiveVerifier,
    ) -> Result<Self, KeyEpochError> {
        validate_capacity(capacity)?;
        validate_checkpoint_shape(&checkpoint, capacity)?;
        if !verifier.verify_checkpoint(&checkpoint) {
            return Err(KeyEpochError::CheckpointVerificationFailed);
        }
        let next_archive_sequence = checkpoint
            .sequence
            .checked_add(1)
            .ok_or(KeyEpochError::ArchiveSequenceExhausted)?;
        let mut records = BTreeMap::new();
        let mut used_key_ids = BTreeSet::new();
        for record in &checkpoint.retained_records {
            records.insert(record.epoch, *record);
            used_key_ids.insert(record.key_id);
        }
        Ok(Self {
            capacity,
            active: checkpoint.active,
            records,
            used_key_ids,
            highest_epoch: Some(checkpoint.highest_epoch),
            last_lifecycle_time: Some(checkpoint.last_lifecycle_time),
            authority_lost_epoch: checkpoint.authority_lost_epoch,
            checkpoint: Some(checkpoint),
            next_archive_sequence,
        })
    }

    pub const fn capacity(&self) -> usize {
        self.capacity
    }

    pub fn len(&self) -> usize {
        self.records.len()
    }

    pub fn is_empty(&self) -> bool {
        self.records.is_empty()
    }

    pub const fn highest_epoch(&self) -> Option<KeyEpoch> {
        self.highest_epoch
    }

    pub const fn last_lifecycle_time(&self) -> Option<i64> {
        self.last_lifecycle_time
    }

    pub fn checkpoint(&self) -> Option<&KeyEpochArchiveCheckpoint> {
        self.checkpoint.as_ref()
    }

    pub fn initialize(
        &mut self,
        epoch: KeyEpoch,
        key_id: KeyId,
        activated_at_unix_seconds: i64,
    ) -> Result<KeyEpochRecord, KeyEpochError> {
        if self.highest_epoch.is_some() || self.active.is_some() || !self.records.is_empty() {
            return Err(KeyEpochError::AlreadyInitialized);
        }
        self.require_capacity()?;
        let record = KeyEpochRecord {
            epoch,
            key_id,
            state: KeyEpochState::Active,
            activated_at_unix_seconds,
        };
        self.records.insert(epoch, record);
        self.used_key_ids.insert(key_id);
        self.active = Some(epoch);
        self.highest_epoch = Some(epoch);
        self.last_lifecycle_time = Some(activated_at_unix_seconds);
        self.authority_lost_epoch = None;
        Ok(record)
    }

    pub fn active_signer_at(&self, now_unix_seconds: i64) -> Result<KeyEpochRecord, KeyEpochError> {
        let record = self.active_record()?;
        if now_unix_seconds < record.activated_at_unix_seconds {
            return Err(KeyEpochError::SignerNotYetActive {
                epoch: record.epoch,
                activated_at_unix_seconds: record.activated_at_unix_seconds,
                observed_at_unix_seconds: now_unix_seconds,
            });
        }
        Ok(record)
    }

    pub fn rotate(
        &mut self,
        next_epoch: KeyEpoch,
        next_key_id: KeyId,
        activated_at_unix_seconds: i64,
        previous_retire_after_unix_seconds: i64,
    ) -> Result<KeyEpochRecord, KeyEpochError> {
        self.rotate_inner(
            next_epoch,
            next_key_id,
            activated_at_unix_seconds,
            previous_retire_after_unix_seconds,
            None,
        )
    }

    pub fn rotate_with_archive_verifier(
        &mut self,
        next_epoch: KeyEpoch,
        next_key_id: KeyId,
        activated_at_unix_seconds: i64,
        previous_retire_after_unix_seconds: i64,
        verifier: &dyn KeyEpochArchiveVerifier,
    ) -> Result<KeyEpochRecord, KeyEpochError> {
        self.rotate_inner(
            next_epoch,
            next_key_id,
            activated_at_unix_seconds,
            previous_retire_after_unix_seconds,
            Some(verifier),
        )
    }

    fn rotate_inner(
        &mut self,
        next_epoch: KeyEpoch,
        next_key_id: KeyId,
        activated_at_unix_seconds: i64,
        previous_retire_after_unix_seconds: i64,
        verifier: Option<&dyn KeyEpochArchiveVerifier>,
    ) -> Result<KeyEpochRecord, KeyEpochError> {
        let current = self.active_record()?;
        let highest = self.highest_epoch.ok_or(KeyEpochError::NotInitialized)?;
        if current.epoch != highest {
            return Err(KeyEpochError::CheckpointInvalid(
                "active epoch is not the monotonic high-water",
            ));
        }
        self.validate_next_epoch(
            highest,
            self.last_lifecycle_time
                .ok_or(KeyEpochError::NotInitialized)?,
            next_epoch,
            next_key_id,
            activated_at_unix_seconds,
            verifier,
        )?;
        if previous_retire_after_unix_seconds <= activated_at_unix_seconds {
            return Err(KeyEpochError::InvalidOverlap);
        }
        let previous = KeyEpochRecord {
            state: KeyEpochState::VerifyOnly {
                retire_after_unix_seconds: previous_retire_after_unix_seconds,
            },
            ..current
        };
        let next = KeyEpochRecord {
            epoch: next_epoch,
            key_id: next_key_id,
            state: KeyEpochState::Active,
            activated_at_unix_seconds,
        };
        self.records.insert(current.epoch, previous);
        self.records.insert(next_epoch, next);
        self.used_key_ids.insert(next_key_id);
        self.active = Some(next_epoch);
        self.highest_epoch = Some(next_epoch);
        self.last_lifecycle_time = Some(activated_at_unix_seconds);
        self.authority_lost_epoch = None;
        Ok(next)
    }

    pub fn install_after_revocation(
        &mut self,
        next_epoch: KeyEpoch,
        next_key_id: KeyId,
        activated_at_unix_seconds: i64,
    ) -> Result<KeyEpochRecord, KeyEpochError> {
        self.install_after_revocation_inner(
            next_epoch,
            next_key_id,
            activated_at_unix_seconds,
            None,
        )
    }

    pub fn install_after_revocation_with_archive_verifier(
        &mut self,
        next_epoch: KeyEpoch,
        next_key_id: KeyId,
        activated_at_unix_seconds: i64,
        verifier: &dyn KeyEpochArchiveVerifier,
    ) -> Result<KeyEpochRecord, KeyEpochError> {
        self.install_after_revocation_inner(
            next_epoch,
            next_key_id,
            activated_at_unix_seconds,
            Some(verifier),
        )
    }

    fn install_after_revocation_inner(
        &mut self,
        next_epoch: KeyEpoch,
        next_key_id: KeyId,
        activated_at_unix_seconds: i64,
        verifier: Option<&dyn KeyEpochArchiveVerifier>,
    ) -> Result<KeyEpochRecord, KeyEpochError> {
        if self.active.is_some() {
            return Err(KeyEpochError::AlreadyInitialized);
        }
        let highest = self.highest_epoch.ok_or(KeyEpochError::NotInitialized)?;
        if self.authority_lost_epoch != Some(highest) {
            return Err(KeyEpochError::ReplacementRequiresRevokedEpoch(highest));
        }
        self.validate_next_epoch(
            highest,
            self.last_lifecycle_time
                .ok_or(KeyEpochError::NotInitialized)?,
            next_epoch,
            next_key_id,
            activated_at_unix_seconds,
            verifier,
        )?;
        let next = KeyEpochRecord {
            epoch: next_epoch,
            key_id: next_key_id,
            state: KeyEpochState::Active,
            activated_at_unix_seconds,
        };
        self.records.insert(next_epoch, next);
        self.used_key_ids.insert(next_key_id);
        self.active = Some(next_epoch);
        self.highest_epoch = Some(next_epoch);
        self.last_lifecycle_time = Some(activated_at_unix_seconds);
        self.authority_lost_epoch = None;
        Ok(next)
    }

    pub fn verification_key(
        &self,
        epoch: KeyEpoch,
        now_unix_seconds: i64,
    ) -> Result<KeyEpochRecord, KeyEpochError> {
        let record = match self.records.get(&epoch).copied() {
            Some(record) => record,
            None => {
                if let (Some(checkpoint), Some(highest)) =
                    (self.checkpoint.as_ref(), self.highest_epoch)
                {
                    if epoch <= highest {
                        return Err(KeyEpochError::EpochArchived {
                            epoch,
                            archive_digest: checkpoint.archive_digest,
                        });
                    }
                }
                return Err(KeyEpochError::EpochNotFound(epoch));
            }
        };
        if now_unix_seconds < record.activated_at_unix_seconds {
            return Err(KeyEpochError::SignerNotYetActive {
                epoch,
                activated_at_unix_seconds: record.activated_at_unix_seconds,
                observed_at_unix_seconds: now_unix_seconds,
            });
        }
        match record.state {
            KeyEpochState::Active => Ok(record),
            KeyEpochState::VerifyOnly {
                retire_after_unix_seconds,
            } if now_unix_seconds <= retire_after_unix_seconds => Ok(record),
            KeyEpochState::VerifyOnly { .. } | KeyEpochState::Retired { .. } => {
                Err(KeyEpochError::EpochNotUsableForVerification(epoch))
            }
            KeyEpochState::Revoked { .. } => Err(KeyEpochError::EpochRevoked(epoch)),
        }
    }

    pub fn retire_expired(&mut self, now_unix_seconds: i64) -> usize {
        let mut changed = 0;
        let mut latest_terminal_time = self.last_lifecycle_time;
        for record in self.records.values_mut() {
            if let KeyEpochState::VerifyOnly {
                retire_after_unix_seconds,
            } = record.state
            {
                if now_unix_seconds > retire_after_unix_seconds {
                    record.state = KeyEpochState::Retired {
                        retired_at_unix_seconds: retire_after_unix_seconds,
                    };
                    latest_terminal_time = Some(
                        latest_terminal_time
                            .map_or(retire_after_unix_seconds, |value| {
                                value.max(retire_after_unix_seconds)
                            }),
                    );
                    changed += 1;
                }
            }
        }
        self.last_lifecycle_time = latest_terminal_time;
        changed
    }

    pub fn revoke(
        &mut self,
        epoch: KeyEpoch,
        revoked_at_unix_seconds: i64,
    ) -> Result<KeyEpochRecord, KeyEpochError> {
        let current = self
            .records
            .get(&epoch)
            .copied()
            .ok_or(KeyEpochError::EpochNotFound(epoch))?;
        if revoked_at_unix_seconds < current.activated_at_unix_seconds {
            return Err(KeyEpochError::RevocationBeforeActivation {
                epoch,
                activated_at_unix_seconds: current.activated_at_unix_seconds,
                received: revoked_at_unix_seconds,
            });
        }
        match current.state {
            KeyEpochState::Retired { .. } => return Err(KeyEpochError::EpochTerminal(epoch)),
            KeyEpochState::Revoked { .. } => return Ok(current),
            KeyEpochState::Active | KeyEpochState::VerifyOnly { .. } => {}
        }
        let next = KeyEpochRecord {
            state: KeyEpochState::Revoked {
                revoked_at_unix_seconds,
            },
            ..current
        };
        self.records.insert(epoch, next);
        self.last_lifecycle_time = Some(
            self.last_lifecycle_time
                .map_or(revoked_at_unix_seconds, |value| value.max(revoked_at_unix_seconds)),
        );
        if self.active == Some(epoch) {
            self.active = None;
            self.authority_lost_epoch = Some(epoch);
        }
        Ok(next)
    }

    pub fn archive_terminal(
        &mut self,
        archive_digest: [u8; 32],
        verifier: &dyn KeyEpochArchiveVerifier,
    ) -> Result<KeyEpochArchiveCheckpoint, KeyEpochError> {
        if archive_digest.iter().all(|byte| *byte == 0) {
            return Err(KeyEpochError::ZeroArchiveDigest);
        }
        let archived_records: Vec<_> = self
            .records
            .values()
            .copied()
            .filter(|record| {
                matches!(
                    record.state,
                    KeyEpochState::Retired { .. } | KeyEpochState::Revoked { .. }
                )
            })
            .collect();
        if archived_records.is_empty() {
            return Err(KeyEpochError::NoTerminalEpochs);
        }
        let retained_records: Vec<_> = self
            .records
            .values()
            .copied()
            .filter(|record| {
                matches!(record.state, KeyEpochState::Active | KeyEpochState::VerifyOnly { .. })
            })
            .collect();
        let checkpoint = KeyEpochArchiveCheckpoint {
            sequence: self.next_archive_sequence,
            previous_archive_digest: self
                .checkpoint
                .as_ref()
                .map(|value| value.archive_digest),
            archive_digest,
            highest_epoch: self.highest_epoch.ok_or(KeyEpochError::NotInitialized)?,
            last_lifecycle_time: self
                .last_lifecycle_time
                .ok_or(KeyEpochError::NotInitialized)?,
            authority_lost_epoch: self.authority_lost_epoch,
            active: self.active,
            retained_records,
            archived_records,
        };
        validate_checkpoint_shape(&checkpoint, self.capacity)?;
        if !verifier.verify_checkpoint(&checkpoint) {
            return Err(KeyEpochError::ArchiveVerificationFailed);
        }
        let next_archive_sequence = self
            .next_archive_sequence
            .checked_add(1)
            .ok_or(KeyEpochError::ArchiveSequenceExhausted)?;
        for record in &checkpoint.archived_records {
            self.records.remove(&record.epoch);
            self.used_key_ids.remove(&record.key_id);
        }
        self.checkpoint = Some(checkpoint.clone());
        self.next_archive_sequence = next_archive_sequence;
        Ok(checkpoint)
    }

    pub fn record(&self, epoch: KeyEpoch) -> Option<KeyEpochRecord> {
        self.records.get(&epoch).copied()
    }

    fn active_record(&self) -> Result<KeyEpochRecord, KeyEpochError> {
        let epoch = self.active.ok_or(KeyEpochError::NoActiveSigner)?;
        let record = self
            .records
            .get(&epoch)
            .copied()
            .ok_or(KeyEpochError::EpochNotFound(epoch))?;
        if record.state != KeyEpochState::Active {
            return Err(KeyEpochError::NoActiveSigner);
        }
        Ok(record)
    }

    fn validate_next_epoch(
        &self,
        current_epoch: KeyEpoch,
        previous_time: i64,
        next_epoch: KeyEpoch,
        next_key_id: KeyId,
        activated_at_unix_seconds: i64,
        verifier: Option<&dyn KeyEpochArchiveVerifier>,
    ) -> Result<(), KeyEpochError> {
        let expected = current_epoch
            .get()
            .checked_add(1)
            .ok_or(KeyEpochError::NonContiguousRotation {
                current: current_epoch,
                received: next_epoch,
            })?;
        if next_epoch.get() != expected {
            return Err(KeyEpochError::NonContiguousRotation {
                current: current_epoch,
                received: next_epoch,
            });
        }
        if self.used_key_ids.contains(&next_key_id) {
            return Err(KeyEpochError::KeyIdReused(next_key_id));
        }
        if let Some(checkpoint) = self.checkpoint.as_ref() {
            let verifier = verifier.ok_or(KeyEpochError::ArchiveVerificationRequired)?;
            if !verifier.verify_checkpoint(checkpoint) {
                return Err(KeyEpochError::ArchiveVerificationFailed);
            }
            if !verifier.key_id_is_absent(checkpoint, next_key_id) {
                return Err(KeyEpochError::KeyIdReused(next_key_id));
            }
        }
        if activated_at_unix_seconds <= previous_time {
            return Err(KeyEpochError::ActivationTimeRegression {
                previous: previous_time,
                received: activated_at_unix_seconds,
            });
        }
        self.require_capacity()
    }

    fn require_capacity(&self) -> Result<(), KeyEpochError> {
        if self.records.len() >= self.capacity {
            Err(KeyEpochError::CapacityExceeded {
                capacity: self.capacity,
            })
        } else {
            Ok(())
        }
    }
}

fn validate_capacity(capacity: usize) -> Result<(), KeyEpochError> {
    if capacity == 0 {
        return Err(KeyEpochError::ZeroCapacity);
    }
    if capacity > MAX_OPERATIONAL_KEY_EPOCHS {
        return Err(KeyEpochError::CapacityTooLarge {
            received: capacity,
            maximum: MAX_OPERATIONAL_KEY_EPOCHS,
        });
    }
    Ok(())
}

fn validate_checkpoint_shape(
    checkpoint: &KeyEpochArchiveCheckpoint,
    capacity: usize,
) -> Result<(), KeyEpochError> {
    if checkpoint.sequence == 0 {
        return Err(KeyEpochError::CheckpointInvalid("zero archive sequence"));
    }
    if checkpoint.archive_digest.iter().all(|byte| *byte == 0) {
        return Err(KeyEpochError::ZeroArchiveDigest);
    }
    if checkpoint.archived_records.is_empty() {
        return Err(KeyEpochError::NoTerminalEpochs);
    }
    if checkpoint.retained_records.len() > capacity {
        return Err(KeyEpochError::CheckpointInvalid(
            "retained records exceed operational capacity",
        ));
    }
    let mut epochs = BTreeSet::new();
    let mut key_ids = BTreeSet::new();
    for record in &checkpoint.archived_records {
        if !matches!(
            record.state,
            KeyEpochState::Retired { .. } | KeyEpochState::Revoked { .. }
        ) {
            return Err(KeyEpochError::CheckpointInvalid(
                "archive contains nonterminal record",
            ));
        }
        if !epochs.insert(record.epoch) || !key_ids.insert(record.key_id) {
            return Err(KeyEpochError::CheckpointInvalid(
                "duplicate archived epoch or key id",
            ));
        }
    }
    for record in &checkpoint.retained_records {
        if !matches!(record.state, KeyEpochState::Active | KeyEpochState::VerifyOnly { .. }) {
            return Err(KeyEpochError::CheckpointInvalid(
                "retained set contains terminal record",
            ));
        }
        if !epochs.insert(record.epoch) || !key_ids.insert(record.key_id) {
            return Err(KeyEpochError::CheckpointInvalid(
                "duplicate retained epoch or key id",
            ));
        }
    }
    let highest = epochs
        .iter()
        .next_back()
        .copied()
        .ok_or(KeyEpochError::CheckpointInvalid("empty checkpoint"))?;
    if highest != checkpoint.highest_epoch {
        return Err(KeyEpochError::CheckpointInvalid(
            "highest epoch does not match checkpoint records",
        ));
    }
    if checkpoint
        .retained_records
        .iter()
        .chain(checkpoint.archived_records.iter())
        .any(|record| record.activated_at_unix_seconds > checkpoint.last_lifecycle_time)
    {
        return Err(KeyEpochError::CheckpointInvalid(
            "lifecycle time precedes an activation",
        ));
    }
    match checkpoint.active {
        Some(active) => {
            if active != checkpoint.highest_epoch
                || checkpoint.authority_lost_epoch.is_some()
                || !checkpoint.retained_records.iter().any(|record| {
                    record.epoch == active && record.state == KeyEpochState::Active
                })
            {
                return Err(KeyEpochError::CheckpointInvalid(
                    "active epoch binding is inconsistent",
                ));
            }
        }
        None => {
            if checkpoint.authority_lost_epoch != Some(checkpoint.highest_epoch)
                || !checkpoint.archived_records.iter().any(|record| {
                    record.epoch == checkpoint.highest_epoch
                        && matches!(record.state, KeyEpochState::Revoked { .. })
                })
            {
                return Err(KeyEpochError::CheckpointInvalid(
                    "authority-loss checkpoint is inconsistent",
                ));
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Debug)]
    struct ExactArchiveVerifier {
        digest: u8,
        denied_key: Option<KeyId>,
    }

    impl KeyEpochArchiveVerifier for ExactArchiveVerifier {
        fn verify_checkpoint(&self, checkpoint: &KeyEpochArchiveCheckpoint) -> bool {
            checkpoint.archive_digest[0] == self.digest
        }

        fn key_id_is_absent(
            &self,
            _checkpoint: &KeyEpochArchiveCheckpoint,
            candidate: KeyId,
        ) -> bool {
            self.denied_key != Some(candidate)
        }
    }

    fn verifier(digest: u8) -> ExactArchiveVerifier {
        ExactArchiveVerifier {
            digest,
            denied_key: None,
        }
    }

    fn epoch(value: u64) -> KeyEpoch {
        KeyEpoch::new(value).unwrap()
    }

    fn key(value: u8) -> KeyId {
        KeyId::new([value; 16]).unwrap()
    }

    #[test]
    fn rotation_is_contiguous_time_monotonic_and_bounded() {
        let mut registry = KeyEpochRegistry::with_capacity(2).unwrap();
        registry.initialize(epoch(7), key(7), 100).unwrap();
        assert!(matches!(
            registry.active_signer_at(99),
            Err(KeyEpochError::SignerNotYetActive { .. })
        ));
        assert!(matches!(
            registry.rotate(epoch(8), key(8), 100, 300),
            Err(KeyEpochError::ActivationTimeRegression { .. })
        ));
        registry.rotate(epoch(8), key(8), 200, 300).unwrap();
        assert_eq!(registry.active_signer_at(200).unwrap().epoch, epoch(8));
        assert_eq!(
            registry.rotate(epoch(9), key(9), 400, 500),
            Err(KeyEpochError::CapacityExceeded { capacity: 2 })
        );
        assert_eq!(registry.len(), 2);
    }

    #[test]
    fn verification_respects_activation_and_overlap_boundaries() {
        let mut registry = KeyEpochRegistry::new();
        registry.initialize(epoch(1), key(1), 10).unwrap();
        registry.rotate(epoch(2), key(2), 20, 30).unwrap();
        assert!(matches!(
            registry.verification_key(epoch(2), 19),
            Err(KeyEpochError::SignerNotYetActive { .. })
        ));
        assert_eq!(
            registry.verification_key(epoch(1), 30).unwrap().epoch,
            epoch(1)
        );
        assert_eq!(
            registry.verification_key(epoch(1), 31),
            Err(KeyEpochError::EpochNotUsableForVerification(epoch(1)))
        );
    }

    #[test]
    fn revocation_is_idempotent_and_requires_nonregressing_time() {
        let mut registry = KeyEpochRegistry::new();
        registry.initialize(epoch(1), key(1), 10).unwrap();
        assert!(matches!(
            registry.revoke(epoch(1), 9),
            Err(KeyEpochError::RevocationBeforeActivation { .. })
        ));
        let revoked = registry.revoke(epoch(1), 20).unwrap();
        assert_eq!(registry.revoke(epoch(1), 21).unwrap(), revoked);
        assert_eq!(
            registry.active_signer_at(21),
            Err(KeyEpochError::NoActiveSigner)
        );
        assert_eq!(
            registry.verification_key(epoch(1), 21),
            Err(KeyEpochError::EpochRevoked(epoch(1)))
        );
    }

    #[test]
    fn emergency_replacement_advances_epoch_key_and_time() {
        let mut registry = KeyEpochRegistry::new();
        registry.initialize(epoch(3), key(3), 10).unwrap();
        registry.revoke(epoch(3), 20).unwrap();
        assert!(matches!(
            registry.install_after_revocation(epoch(4), key(4), 20),
            Err(KeyEpochError::ActivationTimeRegression { .. })
        ));
        registry
            .install_after_revocation(epoch(4), key(4), 21)
            .unwrap();
        assert_eq!(registry.active_signer_at(21).unwrap().epoch, epoch(4));
    }

    #[test]
    fn terminal_archive_reopens_rotation_without_epoch_or_key_rollback() {
        let mut registry = KeyEpochRegistry::with_capacity(2).unwrap();
        registry.initialize(epoch(1), key(1), 10).unwrap();
        registry.rotate(epoch(2), key(2), 20, 30).unwrap();
        assert_eq!(registry.retire_expired(31), 1);
        let checkpoint = registry
            .archive_terminal([7; 32], &verifier(7))
            .unwrap();
        assert_eq!(checkpoint.archived_records.len(), 1);
        assert_eq!(registry.len(), 1);
        assert!(matches!(
            registry.rotate(epoch(3), key(3), 40, 50),
            Err(KeyEpochError::ArchiveVerificationRequired)
        ));
        registry
            .rotate_with_archive_verifier(epoch(3), key(3), 40, 50, &verifier(7))
            .unwrap();
        assert_eq!(registry.active_signer_at(40).unwrap().epoch, epoch(3));
        assert!(matches!(
            registry.verification_key(epoch(1), 40),
            Err(KeyEpochError::EpochArchived { .. })
        ));
    }

    #[test]
    fn capacity_full_active_compromise_can_recover_after_verified_archive() {
        let mut registry = KeyEpochRegistry::with_capacity(2).unwrap();
        registry.initialize(epoch(1), key(1), 10).unwrap();
        registry.rotate(epoch(2), key(2), 20, 30).unwrap();
        registry.retire_expired(31);
        registry.revoke(epoch(2), 40).unwrap();
        assert_eq!(registry.len(), 2);
        registry
            .archive_terminal([8; 32], &verifier(8))
            .unwrap();
        assert_eq!(registry.len(), 0);
        registry
            .install_after_revocation_with_archive_verifier(
                epoch(3),
                key(3),
                41,
                &verifier(8),
            )
            .unwrap();
        assert_eq!(registry.active_signer_at(41).unwrap().epoch, epoch(3));
    }

    #[test]
    fn archived_key_id_reuse_is_rejected_without_mutation() {
        let mut registry = KeyEpochRegistry::with_capacity(2).unwrap();
        registry.initialize(epoch(1), key(1), 10).unwrap();
        registry.rotate(epoch(2), key(2), 20, 30).unwrap();
        registry.retire_expired(31);
        registry
            .archive_terminal([9; 32], &verifier(9))
            .unwrap();
        let denied = ExactArchiveVerifier {
            digest: 9,
            denied_key: Some(key(1)),
        };
        let before = registry.len();
        assert!(matches!(
            registry.rotate_with_archive_verifier(epoch(3), key(1), 40, 50, &denied),
            Err(KeyEpochError::KeyIdReused(_))
        ));
        assert_eq!(registry.len(), before);
        assert_eq!(registry.highest_epoch(), Some(epoch(2)));
    }

    #[test]
    fn checkpoint_restore_preserves_high_water_and_active_window() {
        let mut registry = KeyEpochRegistry::with_capacity(2).unwrap();
        registry.initialize(epoch(1), key(1), 10).unwrap();
        registry.rotate(epoch(2), key(2), 20, 30).unwrap();
        registry.retire_expired(31);
        let checkpoint = registry
            .archive_terminal([10; 32], &verifier(10))
            .unwrap();
        let mut restored = KeyEpochRegistry::from_checkpoint(
            2,
            checkpoint.clone(),
            &verifier(10),
        )
        .unwrap();
        assert_eq!(restored.active_signer_at(31).unwrap().epoch, epoch(2));
        assert_eq!(restored.checkpoint(), Some(&checkpoint));
        restored
            .rotate_with_archive_verifier(epoch(3), key(3), 40, 50, &verifier(10))
            .unwrap();
    }

    #[test]
    fn unverified_archive_and_restore_fail_without_mutation() {
        let mut registry = KeyEpochRegistry::with_capacity(2).unwrap();
        registry.initialize(epoch(1), key(1), 10).unwrap();
        registry.rotate(epoch(2), key(2), 20, 30).unwrap();
        registry.retire_expired(31);
        let before = registry.len();
        assert_eq!(
            registry.archive_terminal([11; 32], &verifier(12)),
            Err(KeyEpochError::ArchiveVerificationFailed)
        );
        assert_eq!(registry.len(), before);
        let checkpoint = registry
            .archive_terminal([11; 32], &verifier(11))
            .unwrap();
        assert_eq!(
            KeyEpochRegistry::from_checkpoint(2, checkpoint, &verifier(12)).unwrap_err(),
            KeyEpochError::CheckpointVerificationFailed
        );
    }

    #[test]
    fn rollback_skip_and_local_key_reuse_are_rejected_without_mutation() {
        let mut registry = KeyEpochRegistry::new();
        registry.initialize(epoch(3), key(3), 10).unwrap();
        assert!(matches!(
            registry.rotate(epoch(5), key(5), 20, 30),
            Err(KeyEpochError::NonContiguousRotation { .. })
        ));
        assert!(matches!(
            registry.rotate(epoch(4), key(3), 20, 30),
            Err(KeyEpochError::KeyIdReused(_))
        ));
        assert_eq!(registry.len(), 1);
    }

    #[test]
    fn capacity_configuration_has_a_hard_upper_bound() {
        assert!(matches!(
            KeyEpochRegistry::with_capacity(0),
            Err(KeyEpochError::ZeroCapacity)
        ));
        assert!(matches!(
            KeyEpochRegistry::with_capacity(MAX_OPERATIONAL_KEY_EPOCHS + 1),
            Err(KeyEpochError::CapacityTooLarge {
                received,
                maximum: MAX_OPERATIONAL_KEY_EPOCHS
            }) if received == MAX_OPERATIONAL_KEY_EPOCHS + 1
        ));
    }
}
'''


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} anchor count={count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> None:
    (ROOT / "crates/trnm-token-crypto-provider/src/key_epoch.rs").write_text(
        KEY_EPOCH_SOURCE,
        encoding="utf-8",
    )

    lib = ROOT / "crates/trnm-token-crypto-provider/src/lib.rs"
    replace_once(
        lib,
        "pub use key_epoch::{\n"
        "    KeyEpoch, KeyEpochError, KeyEpochRecord, KeyEpochRegistry as OperationalKeyEpochRegistry,\n"
        "    KeyEpochState, KeyId,\n"
        "};",
        "pub use key_epoch::{\n"
        "    KeyEpoch, KeyEpochArchiveCheckpoint, KeyEpochArchiveVerifier, KeyEpochError,\n"
        "    KeyEpochRecord, KeyEpochRegistry as OperationalKeyEpochRegistry, KeyEpochState, KeyId,\n"
        "};",
        "key archive exports",
    )

    readme = ROOT / "crates/trnm-token-crypto-provider/README.md"
    replace_once(
        readme,
        "- retire only revoked or verification-expired records while preserving the epoch high-watermark;\n",
        "- retire and externally archive revoked or verification-expired records while preserving the epoch/time high-watermarks;\n",
        "README responsibility",
    )
    replace_once(
        readme,
        "The in-memory registry is a deterministic source component, not a production lifecycle database. A durable adapter must atomically store revision, high-watermarks, windows, revocations, retirements and audit receipts before this boundary can be shared by multiple nodes.\n",
        "The operational registry remains deterministic and bounded. Terminal operational records leave memory only through a digest-chained `KeyEpochArchiveCheckpoint` accepted by a trusted `KeyEpochArchiveVerifier`. The checkpoint carries the global highest epoch, last lifecycle time, authority-loss state, retained verification window and exact archived records. New key IDs after archival require an external absence proof, and archived verification requests return an explicit durable-archive requirement instead of falling back. A durable adapter must atomically persist and verify this checkpoint before the window is restored on another node.\n",
        "README lifecycle archive",
    )
    replace_once(
        readme,
        "The unit corpus covers all six domains, non-overlapping sign windows, bounded verification overlap, exact-epoch no-fallback, emergency revoke, retirement, revision/clock/audit exhaustion atomicity and debug redaction.\n",
        "The unit corpus covers all six domains, non-overlapping sign windows, bounded verification overlap, exact-epoch no-fallback, emergency revoke, retirement, full-window terminal archival, capacity-full compromise recovery, archived key-ID rejection, checkpoint restore, revision/clock/audit exhaustion atomicity and debug redaction.\n",
        "README test corpus",
    )


if __name__ == "__main__":
    main()
