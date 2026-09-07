//! Monotonic key-epoch lifecycle with anti-rollback and terminal revocation.

use std::collections::{BTreeMap, BTreeSet};
use std::fmt;

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

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
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
    AlreadyInitialized,
    NotInitialized,
    EpochNotFound(KeyEpoch),
    NonContiguousRotation {
        current: KeyEpoch,
        received: KeyEpoch,
    },
    KeyIdReused(KeyId),
    InvalidOverlap,
    NoActiveSigner,
    EpochNotUsableForVerification(KeyEpoch),
    EpochRevoked(KeyEpoch),
    EpochTerminal(KeyEpoch),
}

impl fmt::Display for KeyEpochError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::ZeroEpoch => formatter.write_str("key epoch must be positive"),
            Self::ZeroKeyId => formatter.write_str("key id must not be zero"),
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
            Self::KeyIdReused(key) => {
                write!(formatter, "key id {:?} was already used", key.as_bytes())
            }
            Self::InvalidOverlap => {
                formatter.write_str("verify-only overlap must end after activation")
            }
            Self::NoActiveSigner => formatter.write_str("no active signing epoch exists"),
            Self::EpochNotUsableForVerification(epoch) => write!(
                formatter,
                "key epoch {} is outside its verification window",
                epoch.get()
            ),
            Self::EpochRevoked(epoch) => write!(formatter, "key epoch {} is revoked", epoch.get()),
            Self::EpochTerminal(epoch) => {
                write!(formatter, "key epoch {} is terminal", epoch.get())
            }
        }
    }
}

impl std::error::Error for KeyEpochError {}

#[derive(Clone, Debug, Default)]
pub struct KeyEpochRegistry {
    active: Option<KeyEpoch>,
    records: BTreeMap<KeyEpoch, KeyEpochRecord>,
    used_key_ids: BTreeSet<KeyId>,
}

impl KeyEpochRegistry {
    pub fn new() -> Self {
        Self::default()
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

    pub fn active_signer(&self) -> Result<KeyEpochRecord, KeyEpochError> {
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

    pub fn rotate(
        &mut self,
        next_epoch: KeyEpoch,
        next_key_id: KeyId,
        activated_at_unix_seconds: i64,
        previous_retire_after_unix_seconds: i64,
    ) -> Result<KeyEpochRecord, KeyEpochError> {
        let current = self.active_signer()?;
        let expected =
            current
                .epoch
                .get()
                .checked_add(1)
                .ok_or(KeyEpochError::NonContiguousRotation {
                    current: current.epoch,
                    received: next_epoch,
                })?;
        if next_epoch.get() != expected {
            return Err(KeyEpochError::NonContiguousRotation {
                current: current.epoch,
                received: next_epoch,
            });
        }
        if self.used_key_ids.contains(&next_key_id) {
            return Err(KeyEpochError::KeyIdReused(next_key_id));
        }
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
        match current.state {
            KeyEpochState::Retired { .. } | KeyEpochState::Revoked { .. } => {
                return Err(KeyEpochError::EpochTerminal(epoch));
            }
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
    fn rotation_is_contiguous_and_preserves_verify_only_overlap() {
        let mut registry = KeyEpochRegistry::new();
        registry.initialize(epoch(7), key(7), 100).unwrap();
        registry.rotate(epoch(8), key(8), 200, 300).unwrap();
        assert_eq!(registry.active_signer().unwrap().epoch, epoch(8));
        assert_eq!(
            registry.verification_key(epoch(7), 300).unwrap().key_id,
            key(7)
        );
        assert_eq!(
            registry.verification_key(epoch(7), 301),
            Err(KeyEpochError::EpochNotUsableForVerification(epoch(7)))
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
        assert_eq!(registry.active_signer().unwrap().epoch, epoch(3));
        assert_eq!(
            registry.rotate(epoch(4), key(3), 20, 30),
            Err(KeyEpochError::KeyIdReused(key(3)))
        );
        assert_eq!(registry.active_signer().unwrap().epoch, epoch(3));
    }

    #[test]
    fn revocation_is_terminal_and_removes_signing_authority() {
        let mut registry = KeyEpochRegistry::new();
        registry.initialize(epoch(1), key(1), 10).unwrap();
        registry.revoke(epoch(1), 20).unwrap();
        assert_eq!(registry.active_signer(), Err(KeyEpochError::NoActiveSigner));
        assert_eq!(
            registry.verification_key(epoch(1), 20),
            Err(KeyEpochError::EpochRevoked(epoch(1)))
        );
        assert_eq!(
            registry.revoke(epoch(1), 21),
            Err(KeyEpochError::EpochTerminal(epoch(1)))
        );
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
}
