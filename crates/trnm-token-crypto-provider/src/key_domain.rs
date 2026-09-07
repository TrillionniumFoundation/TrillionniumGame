//! Explicit cryptographic key-domain separation.
//!
//! The wrapper reuses the crate's canonical [`KeyDomain`] enum, so provider
//! keys cannot be silently reclassified through a divergent vocabulary. It
//! stores opaque key IDs and delegates bounded lifecycle transitions to the
//! operational epoch registry.

use std::fmt;

use super::key_epoch::{KeyEpoch, KeyEpochError, KeyEpochRecord, KeyEpochRegistry, KeyId};
use super::KeyDomain;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct DomainBoundKeyId {
    pub domain: KeyDomain,
    pub key_id: KeyId,
}

impl DomainBoundKeyId {
    #[must_use]
    pub const fn new(domain: KeyDomain, key_id: KeyId) -> Self {
        Self { domain, key_id }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum KeyDomainError {
    DomainMismatch { expected: KeyDomain, received: KeyDomain },
    Lifecycle(KeyEpochError),
}

impl fmt::Display for KeyDomainError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::DomainMismatch { expected, received } => write!(
                formatter,
                "key domain mismatch: expected {expected:?}, received {received:?}"
            ),
            Self::Lifecycle(error) => error.fmt(formatter),
        }
    }
}

impl std::error::Error for KeyDomainError {}

impl From<KeyEpochError> for KeyDomainError {
    fn from(value: KeyEpochError) -> Self {
        Self::Lifecycle(value)
    }
}

#[derive(Clone, Debug)]
pub struct DomainKeyEpochRegistry {
    domain: KeyDomain,
    inner: KeyEpochRegistry,
}

impl DomainKeyEpochRegistry {
    #[must_use]
    pub fn new(domain: KeyDomain) -> Self {
        Self { domain, inner: KeyEpochRegistry::new() }
    }

    pub fn with_capacity(domain: KeyDomain, capacity: usize) -> Result<Self, KeyDomainError> {
        Ok(Self { domain, inner: KeyEpochRegistry::with_capacity(capacity)? })
    }

    #[must_use]
    pub const fn domain(&self) -> KeyDomain {
        self.domain
    }

    #[must_use]
    pub const fn capacity(&self) -> usize {
        self.inner.capacity()
    }

    #[must_use]
    pub fn len(&self) -> usize {
        self.inner.len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.inner.is_empty()
    }

    pub fn initialize(
        &mut self,
        epoch: KeyEpoch,
        key: DomainBoundKeyId,
        activated_at_unix_seconds: i64,
    ) -> Result<KeyEpochRecord, KeyDomainError> {
        self.require_domain(key)?;
        Ok(self.inner.initialize(epoch, key.key_id, activated_at_unix_seconds)?)
    }

    pub fn rotate(
        &mut self,
        next_epoch: KeyEpoch,
        next_key: DomainBoundKeyId,
        activated_at_unix_seconds: i64,
        previous_retire_after_unix_seconds: i64,
    ) -> Result<KeyEpochRecord, KeyDomainError> {
        self.require_domain(next_key)?;
        Ok(self.inner.rotate(
            next_epoch,
            next_key.key_id,
            activated_at_unix_seconds,
            previous_retire_after_unix_seconds,
        )?)
    }

    pub fn install_after_revocation(
        &mut self,
        next_epoch: KeyEpoch,
        next_key: DomainBoundKeyId,
        activated_at_unix_seconds: i64,
    ) -> Result<KeyEpochRecord, KeyDomainError> {
        self.require_domain(next_key)?;
        Ok(self.inner.install_after_revocation(
            next_epoch,
            next_key.key_id,
            activated_at_unix_seconds,
        )?)
    }

    pub fn active_signer_at(
        &self,
        now_unix_seconds: i64,
    ) -> Result<KeyEpochRecord, KeyDomainError> {
        Ok(self.inner.active_signer_at(now_unix_seconds)?)
    }

    pub fn verification_key(
        &self,
        epoch: KeyEpoch,
        now_unix_seconds: i64,
    ) -> Result<KeyEpochRecord, KeyDomainError> {
        Ok(self.inner.verification_key(epoch, now_unix_seconds)?)
    }

    pub fn retire_expired(&mut self, now_unix_seconds: i64) -> usize {
        self.inner.retire_expired(now_unix_seconds)
    }

    pub fn revoke(
        &mut self,
        epoch: KeyEpoch,
        revoked_at_unix_seconds: i64,
    ) -> Result<KeyEpochRecord, KeyDomainError> {
        Ok(self.inner.revoke(epoch, revoked_at_unix_seconds)?)
    }

    fn require_domain(&self, key: DomainBoundKeyId) -> Result<(), KeyDomainError> {
        if key.domain == self.domain {
            Ok(())
        } else {
            Err(KeyDomainError::DomainMismatch {
                expected: self.domain,
                received: key.domain,
            })
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn epoch(value: u64) -> KeyEpoch { KeyEpoch::new(value).unwrap() }
    fn key(domain: KeyDomain, value: u8) -> DomainBoundKeyId {
        DomainBoundKeyId::new(domain, KeyId::new([value; 16]).unwrap())
    }

    #[test]
    fn cross_domain_initialization_is_rejected_without_state() {
        let mut registry = DomainKeyEpochRegistry::new(KeyDomain::AccessToken);
        assert_eq!(
            registry.initialize(epoch(1), key(KeyDomain::RefreshToken, 1), 10),
            Err(KeyDomainError::DomainMismatch {
                expected: KeyDomain::AccessToken,
                received: KeyDomain::RefreshToken,
            })
        );
        assert!(matches!(
            registry.active_signer_at(10),
            Err(KeyDomainError::Lifecycle(KeyEpochError::NoActiveSigner))
        ));
        assert!(registry.is_empty());
    }

    #[test]
    fn cross_domain_rotation_does_not_retire_current_signer() {
        let mut registry = DomainKeyEpochRegistry::new(KeyDomain::Socket);
        registry.initialize(epoch(1), key(KeyDomain::Socket, 1), 10).unwrap();
        assert_eq!(
            registry.rotate(epoch(2), key(KeyDomain::Authority, 2), 20, 30),
            Err(KeyDomainError::DomainMismatch {
                expected: KeyDomain::Socket,
                received: KeyDomain::Authority,
            })
        );
        assert_eq!(registry.active_signer_at(20).unwrap().epoch, epoch(1));
    }

    #[test]
    fn same_domain_rotation_preserves_activation_and_overlap_contract() {
        let mut registry = DomainKeyEpochRegistry::new(KeyDomain::Authority);
        registry.initialize(epoch(7), key(KeyDomain::Authority, 7), 100).unwrap();
        registry.rotate(epoch(8), key(KeyDomain::Authority, 8), 200, 300).unwrap();
        assert_eq!(registry.active_signer_at(200).unwrap().epoch, epoch(8));
        assert_eq!(registry.verification_key(epoch(7), 300).unwrap().epoch, epoch(7));
    }

    #[test]
    fn revoked_domain_can_only_install_a_same_domain_successor() {
        let mut registry = DomainKeyEpochRegistry::new(KeyDomain::Console);
        registry.initialize(epoch(1), key(KeyDomain::Console, 1), 10).unwrap();
        registry.revoke(epoch(1), 20).unwrap();
        assert_eq!(
            registry.install_after_revocation(epoch(2), key(KeyDomain::RuntimeHttp, 2), 21),
            Err(KeyDomainError::DomainMismatch {
                expected: KeyDomain::Console,
                received: KeyDomain::RuntimeHttp,
            })
        );
        registry.install_after_revocation(epoch(2), key(KeyDomain::Console, 2), 21).unwrap();
        assert_eq!(registry.active_signer_at(21).unwrap().epoch, epoch(2));
    }
}
