//! Bounded monotonic operational key-epoch lifecycle.
//!
//! The registry stores opaque key identifiers only. It enforces a finite
//! admitted epoch universe, monotonic activation time, anti-rollback,
//! terminal revocation and exact epoch lookup.

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
                write!(
                    formatter,
                    "key epoch registry is full at capacity {capacity}"
                )
            }
            Self::AlreadyInitialized => {
                formatter.write_str("key epoch registry is already initialized")
            }
            Self::NotInitialized => formatter.write_str("key epoch registry is not initialized"),
            Self::EpochNotFound(epoch) => {
                write!(formatter, "key epoch {} is not registered", epoch.get())
            }
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
}

impl Default for KeyEpochRegistry {
    fn default() -> Self {
        Self {
            capacity: MAX_OPERATIONAL_KEY_EPOCHS,
            active: None,
            records: BTreeMap::new(),
            used_key_ids: BTreeSet::new(),
        }
    }
}

impl KeyEpochRegistry {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_capacity(capacity: usize) -> Result<Self, KeyEpochError> {
        if capacity == 0 {
            return Err(KeyEpochError::ZeroCapacity);
        }
        if capacity > MAX_OPERATIONAL_KEY_EPOCHS {
            return Err(KeyEpochError::CapacityTooLarge {
                received: capacity,
                maximum: MAX_OPERATIONAL_KEY_EPOCHS,
            });
        }
        Ok(Self {
            capacity,
            active: None,
            records: BTreeMap::new(),
            used_key_ids: BTreeSet::new(),
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

    pub fn initialize(
        &mut self,
        epoch: KeyEpoch,
        key_id: KeyId,
        activated_at_unix_seconds: i64,
    ) -> Result<KeyEpochRecord, KeyEpochError> {
        if !self.records.is_empty() || self.active.is_some() {
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
        let current = self.active_record()?;
        self.validate_next_epoch(
            current.epoch,
            current.activated_at_unix_seconds,
            next_epoch,
            next_key_id,
            activated_at_unix_seconds,
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
        Ok(next)
    }

    pub fn install_after_revocation(
        &mut self,
        next_epoch: KeyEpoch,
        next_key_id: KeyId,
        activated_at_unix_seconds: i64,
    ) -> Result<KeyEpochRecord, KeyEpochError> {
        if self.active.is_some() {
            return Err(KeyEpochError::AlreadyInitialized);
        }
        let highest = self
            .records
            .iter()
            .next_back()
            .map(|(_, record)| *record)
            .ok_or(KeyEpochError::NotInitialized)?;
        let revoked_at = match highest.state {
            KeyEpochState::Revoked {
                revoked_at_unix_seconds,
            } => revoked_at_unix_seconds,
            _ => {
                return Err(KeyEpochError::ReplacementRequiresRevokedEpoch(
                    highest.epoch,
                ))
            }
        };
        self.validate_next_epoch(
            highest.epoch,
            revoked_at.max(highest.activated_at_unix_seconds),
            next_epoch,
            next_key_id,
            activated_at_unix_seconds,
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
        Ok(next)
    }

    pub fn verification_key(
        &self,
        epoch: KeyEpoch,
        now_unix_seconds: i64,
    ) -> Result<KeyEpochRecord, KeyEpochError> {
        let record = self
            .records
            .get(&epoch)
            .copied()
            .ok_or(KeyEpochError::EpochNotFound(epoch))?;
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
        for record in self.records.values_mut() {
            if let KeyEpochState::VerifyOnly {
                retire_after_unix_seconds,
            } = record.state
            {
                if now_unix_seconds > retire_after_unix_seconds {
                    record.state = KeyEpochState::Retired {
                        retired_at_unix_seconds: retire_after_unix_seconds,
                    };
                    changed += 1;
                }
            }
        }
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
        if self.active == Some(epoch) {
            self.active = None;
        }
        Ok(next)
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
    ) -> Result<(), KeyEpochError> {
        let expected =
            current_epoch
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

#[cfg(test)]
mod tests {
    use super::*;

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
        assert_eq!(
            registry.verification_key(epoch(3), 21),
            Err(KeyEpochError::EpochRevoked(epoch(3)))
        );
    }

    #[test]
    fn rollback_skip_and_key_reuse_are_rejected_without_mutation() {
        let mut registry = KeyEpochRegistry::new();
        registry.initialize(epoch(3), key(3), 10).unwrap();
        assert!(matches!(
            registry.rotate(epoch(5), key(5), 20, 30),
            Err(KeyEpochError::NonContiguousRotation { .. })
        ));
        assert_eq!(registry.active_signer_at(20).unwrap().epoch, epoch(3));
        assert!(matches!(
            registry.rotate(epoch(4), key(3), 20, 30),
            Err(KeyEpochError::KeyIdReused(_))
        ));
        assert_eq!(registry.len(), 1);
    }

    #[test]
    fn expired_overlap_can_be_retired_deterministically() {
        let mut registry = KeyEpochRegistry::new();
        registry.initialize(epoch(1), key(1), 10).unwrap();
        registry.rotate(epoch(2), key(2), 20, 30).unwrap();
        assert_eq!(registry.retire_expired(30), 0);
        assert_eq!(registry.retire_expired(31), 1);
        assert!(matches!(
            registry.record(epoch(1)).unwrap().state,
            KeyEpochState::Retired { .. }
        ));
    }

    #[test]
    fn capacity_configuration_has_a_hard_upper_bound() {
        assert!(matches!(
            KeyEpochRegistry::with_capacity(0),
            Err(KeyEpochError::ZeroCapacity)
        ));
        assert!(matches!(
            KeyEpochRegistry::with_capacity(MAX_OPERATIONAL_KEY_EPOCHS + 1),
            Err(KeyEpochError::CapacityTooLarge { received, maximum: MAX_OPERATIONAL_KEY_EPOCHS })
                if received == MAX_OPERATIONAL_KEY_EPOCHS + 1
        ));
    }
}
