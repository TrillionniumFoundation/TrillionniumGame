//! Deterministic, bounded lifecycle scheduling for opaque provider keys.
//!
//! The registry never stores key bytes and never invokes a provider. It owns
//! only domain, opaque handle, epoch and time-window routing metadata. Durable
//! persistence, KMS/HSM adapters and cross-node distribution remain separate
//! production responsibilities.

use core::fmt;
use std::collections::BTreeMap;

use crate::{KeyDomain, KeyReference};

pub const ALL_KEY_DOMAINS: [KeyDomain; 6] = [
    KeyDomain::AccessToken,
    KeyDomain::RefreshToken,
    KeyDomain::Console,
    KeyDomain::RuntimeHttp,
    KeyDomain::Socket,
    KeyDomain::Authority,
];

pub const MAX_EPOCHS_PER_DOMAIN: usize = 8;
pub const MAX_VERIFICATION_EPOCHS_AT_ONCE: usize = 2;
pub const MAX_LIFECYCLE_AUDIT_EVENTS: usize = 256;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EpochWindow {
    key: KeyReference,
    not_before_unix: u64,
    sign_until_unix: u64,
    verify_until_unix: u64,
}

impl EpochWindow {
    pub fn new(
        key: KeyReference,
        not_before_unix: u64,
        sign_until_unix: u64,
        verify_until_unix: u64,
    ) -> Result<Self, LifecycleError> {
        if key.epoch.is_none() {
            return Err(LifecycleError::EpochRequired);
        }
        if !(not_before_unix < sign_until_unix && sign_until_unix < verify_until_unix) {
            return Err(LifecycleError::InvalidWindow);
        }
        Ok(Self {
            key,
            not_before_unix,
            sign_until_unix,
            verify_until_unix,
        })
    }

    #[must_use]
    pub const fn key(&self) -> &KeyReference {
        &self.key
    }

    #[must_use]
    pub const fn domain(&self) -> KeyDomain {
        self.key.domain
    }

    #[must_use]
    pub fn epoch(&self) -> u32 {
        self.key
            .epoch
            .expect("EpochWindow constructor guarantees a nonzero epoch")
    }

    #[must_use]
    pub const fn not_before_unix(&self) -> u64 {
        self.not_before_unix
    }

    #[must_use]
    pub const fn sign_until_unix(&self) -> u64 {
        self.sign_until_unix
    }

    #[must_use]
    pub const fn verify_until_unix(&self) -> u64 {
        self.verify_until_unix
    }

    fn can_sign_at(&self, at_unix: u64) -> bool {
        self.not_before_unix <= at_unix && at_unix < self.sign_until_unix
    }

    fn can_verify_at(&self, at_unix: u64) -> bool {
        self.not_before_unix <= at_unix && at_unix < self.verify_until_unix
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LifecycleAction {
    Installed,
    Revoked,
    Retired,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct LifecycleAuditEvent {
    pub revision: u64,
    pub action: LifecycleAction,
    pub domain: KeyDomain,
    pub epoch: u32,
    pub at_unix: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct LifecycleMutation {
    pub revision: u64,
    pub applied: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DomainLifecycleStatus {
    pub revision: u64,
    pub domain: KeyDomain,
    pub highest_epoch_ever: Option<u32>,
    pub signing_epoch: Option<u32>,
    pub verification_epochs: Vec<u32>,
    pub configured_epoch_count: usize,
    pub revoked_epoch_count: usize,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct LifecycleHealth {
    pub revision: u64,
    pub configured_domains: usize,
    pub signing_ready_domains: usize,
    pub verifiable_epoch_count: usize,
    pub revoked_epoch_count: usize,
    pub audit_event_count: usize,
    pub audit_capacity_remaining: usize,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum LifecycleError {
    EpochRequired,
    InvalidWindow,
    WindowAlreadySigningExpired,
    RevisionMismatch { expected: u64, actual: u64 },
    RevisionExhausted,
    ClockRegression { previous: u64, requested: u64 },
    EpochNotMonotonic { previous: u32, requested: u32 },
    EpochAlreadyExists { domain: KeyDomain, epoch: u32 },
    ScheduleCapacityExceeded { domain: KeyDomain, limit: usize },
    SigningWindowOverlap { domain: KeyDomain },
    VerificationWindowLimitExceeded { domain: KeyDomain, limit: usize },
    AuditCapacityExceeded { limit: usize },
    EpochUnknown { domain: KeyDomain, epoch: u32 },
    EpochNotYetActive { domain: KeyDomain, epoch: u32 },
    EpochSigningExpired { domain: KeyDomain, epoch: u32 },
    EpochVerificationExpired { domain: KeyDomain, epoch: u32 },
    EpochRevoked { domain: KeyDomain, epoch: u32 },
    EpochRetirementNotAllowed { domain: KeyDomain, epoch: u32 },
    NoSigningEpoch { domain: KeyDomain },
    InternalInvariant,
}

impl fmt::Display for LifecycleError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::EpochRequired => formatter.write_str("lifecycle key requires a nonzero epoch"),
            Self::InvalidWindow => formatter.write_str(
                "key window must satisfy not-before < sign-until < verify-until",
            ),
            Self::WindowAlreadySigningExpired => {
                formatter.write_str("cannot install an epoch after its signing window")
            }
            Self::RevisionMismatch { expected, actual } => write!(
                formatter,
                "lifecycle revision mismatch: expected {expected}, actual {actual}"
            ),
            Self::RevisionExhausted => formatter.write_str("lifecycle revision exhausted"),
            Self::ClockRegression {
                previous,
                requested,
            } => write!(
                formatter,
                "lifecycle mutation clock regressed from {previous} to {requested}"
            ),
            Self::EpochNotMonotonic {
                previous,
                requested,
            } => write!(
                formatter,
                "key epoch {requested} is not greater than prior epoch {previous}"
            ),
            Self::EpochAlreadyExists { domain, epoch } => {
                write!(formatter, "key epoch {domain:?}/{epoch} already exists")
            }
            Self::ScheduleCapacityExceeded { domain, limit } => write!(
                formatter,
                "key schedule {domain:?} exceeds bounded capacity {limit}"
            ),
            Self::SigningWindowOverlap { domain } => {
                write!(formatter, "key signing windows overlap for {domain:?}")
            }
            Self::VerificationWindowLimitExceeded { domain, limit } => write!(
                formatter,
                "key verification overlap for {domain:?} exceeds {limit}"
            ),
            Self::AuditCapacityExceeded { limit } => {
                write!(formatter, "lifecycle audit capacity {limit} exhausted")
            }
            Self::EpochUnknown { domain, epoch } => {
                write!(formatter, "key epoch {domain:?}/{epoch} is unknown")
            }
            Self::EpochNotYetActive { domain, epoch } => {
                write!(formatter, "key epoch {domain:?}/{epoch} is not active")
            }
            Self::EpochSigningExpired { domain, epoch } => {
                write!(formatter, "key epoch {domain:?}/{epoch} cannot sign")
            }
            Self::EpochVerificationExpired { domain, epoch } => {
                write!(formatter, "key epoch {domain:?}/{epoch} cannot verify")
            }
            Self::EpochRevoked { domain, epoch } => {
                write!(formatter, "key epoch {domain:?}/{epoch} is revoked")
            }
            Self::EpochRetirementNotAllowed { domain, epoch } => write!(
                formatter,
                "key epoch {domain:?}/{epoch} is neither revoked nor verification-expired"
            ),
            Self::NoSigningEpoch { domain } => {
                write!(formatter, "no signing epoch is active for {domain:?}")
            }
            Self::InternalInvariant => formatter.write_str("key lifecycle invariant violated"),
        }
    }
}

impl std::error::Error for LifecycleError {}

#[derive(Clone, Debug, Eq, PartialEq)]
struct EpochRecord {
    window: EpochWindow,
    revoked_at_unix: Option<u64>,
}

impl EpochRecord {
    fn is_revoked(&self) -> bool {
        self.revoked_at_unix.is_some()
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
struct DomainSchedule {
    highest_epoch_ever: Option<u32>,
    epochs: BTreeMap<u32, EpochRecord>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct KeyEpochRegistry {
    revision: u64,
    last_mutation_unix: u64,
    schedules: BTreeMap<KeyDomain, DomainSchedule>,
    audit: Vec<LifecycleAuditEvent>,
}

impl KeyEpochRegistry {
    #[must_use]
    pub const fn revision(&self) -> u64 {
        self.revision
    }

    #[must_use]
    pub const fn last_mutation_unix(&self) -> u64 {
        self.last_mutation_unix
    }

    #[must_use]
    pub fn audit_events(&self) -> &[LifecycleAuditEvent] {
        &self.audit
    }

    pub fn install(
        &mut self,
        expected_revision: u64,
        window: EpochWindow,
        installed_at_unix: u64,
    ) -> Result<LifecycleMutation, LifecycleError> {
        let next_revision = self.preflight_mutation(expected_revision, installed_at_unix)?;
        if installed_at_unix >= window.sign_until_unix {
            return Err(LifecycleError::WindowAlreadySigningExpired);
        }

        let domain = window.domain();
        let epoch = window.epoch();
        if let Some(schedule) = self.schedules.get(&domain) {
            if schedule.epochs.contains_key(&epoch) {
                return Err(LifecycleError::EpochAlreadyExists { domain, epoch });
            }
            if let Some(previous) = schedule.highest_epoch_ever {
                if epoch <= previous {
                    return Err(LifecycleError::EpochNotMonotonic {
                        previous,
                        requested: epoch,
                    });
                }
            }
            if schedule.epochs.len() >= MAX_EPOCHS_PER_DOMAIN {
                return Err(LifecycleError::ScheduleCapacityExceeded {
                    domain,
                    limit: MAX_EPOCHS_PER_DOMAIN,
                });
            }
            if schedule.epochs.values().any(|existing| {
                intervals_overlap(
                    window.not_before_unix,
                    window.sign_until_unix,
                    existing.window.not_before_unix,
                    existing.window.sign_until_unix,
                )
            }) {
                return Err(LifecycleError::SigningWindowOverlap { domain });
            }
            if verification_overlap_exceeds(schedule, &window) {
                return Err(LifecycleError::VerificationWindowLimitExceeded {
                    domain,
                    limit: MAX_VERIFICATION_EPOCHS_AT_ONCE,
                });
            }
        }

        let schedule = self.schedules.entry(domain).or_default();
        schedule.highest_epoch_ever = Some(epoch);
        schedule.epochs.insert(
            epoch,
            EpochRecord {
                window,
                revoked_at_unix: None,
            },
        );
        self.commit_mutation(
            next_revision,
            installed_at_unix,
            LifecycleAction::Installed,
            domain,
            epoch,
        );
        Ok(LifecycleMutation {
            revision: next_revision,
            applied: true,
        })
    }

    pub fn revoke(
        &mut self,
        expected_revision: u64,
        domain: KeyDomain,
        epoch: u32,
        revoked_at_unix: u64,
    ) -> Result<LifecycleMutation, LifecycleError> {
        self.validate_revision(expected_revision)?;
        self.validate_clock(revoked_at_unix)?;
        let record = self
            .schedules
            .get(&domain)
            .and_then(|schedule| schedule.epochs.get(&epoch))
            .ok_or(LifecycleError::EpochUnknown { domain, epoch })?;
        if record.is_revoked() {
            return Ok(LifecycleMutation {
                revision: self.revision,
                applied: false,
            });
        }
        self.ensure_audit_capacity()?;
        let next_revision = self
            .revision
            .checked_add(1)
            .ok_or(LifecycleError::RevisionExhausted)?;

        self.schedules
            .get_mut(&domain)
            .and_then(|schedule| schedule.epochs.get_mut(&epoch))
            .ok_or(LifecycleError::InternalInvariant)?
            .revoked_at_unix = Some(revoked_at_unix);
        self.commit_mutation(
            next_revision,
            revoked_at_unix,
            LifecycleAction::Revoked,
            domain,
            epoch,
        );
        Ok(LifecycleMutation {
            revision: next_revision,
            applied: true,
        })
    }

    pub fn retire(
        &mut self,
        expected_revision: u64,
        domain: KeyDomain,
        epoch: u32,
        retired_at_unix: u64,
    ) -> Result<LifecycleMutation, LifecycleError> {
        let next_revision = self.preflight_mutation(expected_revision, retired_at_unix)?;
        let record = self
            .schedules
            .get(&domain)
            .and_then(|schedule| schedule.epochs.get(&epoch))
            .ok_or(LifecycleError::EpochUnknown { domain, epoch })?;
        if !record.is_revoked() && retired_at_unix < record.window.verify_until_unix {
            return Err(LifecycleError::EpochRetirementNotAllowed { domain, epoch });
        }

        let removed = self
            .schedules
            .get_mut(&domain)
            .and_then(|schedule| schedule.epochs.remove(&epoch));
        if removed.is_none() {
            return Err(LifecycleError::InternalInvariant);
        }
        self.commit_mutation(
            next_revision,
            retired_at_unix,
            LifecycleAction::Retired,
            domain,
            epoch,
        );
        Ok(LifecycleMutation {
            revision: next_revision,
            applied: true,
        })
    }

    pub fn signing_key(
        &self,
        domain: KeyDomain,
        at_unix: u64,
    ) -> Result<KeyReference, LifecycleError> {
        let mut matches = self
            .schedules
            .get(&domain)
            .into_iter()
            .flat_map(|schedule| schedule.epochs.values())
            .filter(|record| !record.is_revoked() && record.window.can_sign_at(at_unix));
        let selected = matches
            .next()
            .ok_or(LifecycleError::NoSigningEpoch { domain })?;
        if matches.next().is_some() {
            return Err(LifecycleError::InternalInvariant);
        }
        Ok(selected.window.key.clone())
    }

    pub fn verification_key(
        &self,
        domain: KeyDomain,
        epoch: u32,
        at_unix: u64,
    ) -> Result<KeyReference, LifecycleError> {
        let record = self
            .schedules
            .get(&domain)
            .and_then(|schedule| schedule.epochs.get(&epoch))
            .ok_or(LifecycleError::EpochUnknown { domain, epoch })?;
        if record.is_revoked() {
            return Err(LifecycleError::EpochRevoked { domain, epoch });
        }
        if at_unix < record.window.not_before_unix {
            return Err(LifecycleError::EpochNotYetActive { domain, epoch });
        }
        if at_unix >= record.window.verify_until_unix {
            return Err(LifecycleError::EpochVerificationExpired { domain, epoch });
        }
        Ok(record.window.key.clone())
    }

    pub fn assert_signing_allowed(
        &self,
        domain: KeyDomain,
        epoch: u32,
        at_unix: u64,
    ) -> Result<(), LifecycleError> {
        let record = self
            .schedules
            .get(&domain)
            .and_then(|schedule| schedule.epochs.get(&epoch))
            .ok_or(LifecycleError::EpochUnknown { domain, epoch })?;
        if record.is_revoked() {
            return Err(LifecycleError::EpochRevoked { domain, epoch });
        }
        if at_unix < record.window.not_before_unix {
            return Err(LifecycleError::EpochNotYetActive { domain, epoch });
        }
        if at_unix >= record.window.sign_until_unix {
            return Err(LifecycleError::EpochSigningExpired { domain, epoch });
        }
        Ok(())
    }

    #[must_use]
    pub fn status(&self, domain: KeyDomain, at_unix: u64) -> DomainLifecycleStatus {
        let schedule = self.schedules.get(&domain);
        let signing_epoch = schedule.and_then(|schedule| {
            schedule
                .epochs
                .iter()
                .find(|(_, record)| {
                    !record.is_revoked() && record.window.can_sign_at(at_unix)
                })
                .map(|(epoch, _)| *epoch)
        });
        let mut verification_epochs = schedule
            .into_iter()
            .flat_map(|schedule| schedule.epochs.iter())
            .filter(|(_, record)| {
                !record.is_revoked() && record.window.can_verify_at(at_unix)
            })
            .map(|(epoch, _)| *epoch)
            .collect::<Vec<_>>();
        verification_epochs.sort_unstable_by(|left, right| right.cmp(left));
        DomainLifecycleStatus {
            revision: self.revision,
            domain,
            highest_epoch_ever: schedule.and_then(|schedule| schedule.highest_epoch_ever),
            signing_epoch,
            verification_epochs,
            configured_epoch_count: schedule.map_or(0, |schedule| schedule.epochs.len()),
            revoked_epoch_count: schedule.map_or(0, |schedule| {
                schedule
                    .epochs
                    .values()
                    .filter(|record| record.is_revoked())
                    .count()
            }),
        }
    }

    #[must_use]
    pub fn health(&self, at_unix: u64) -> LifecycleHealth {
        let mut signing_ready_domains = 0;
        let mut verifiable_epoch_count = 0;
        let mut revoked_epoch_count = 0;
        for domain in ALL_KEY_DOMAINS {
            let status = self.status(domain, at_unix);
            signing_ready_domains += usize::from(status.signing_epoch.is_some());
            verifiable_epoch_count += status.verification_epochs.len();
            revoked_epoch_count += status.revoked_epoch_count;
        }
        LifecycleHealth {
            revision: self.revision,
            configured_domains: self.schedules.len(),
            signing_ready_domains,
            verifiable_epoch_count,
            revoked_epoch_count,
            audit_event_count: self.audit.len(),
            audit_capacity_remaining: MAX_LIFECYCLE_AUDIT_EVENTS.saturating_sub(self.audit.len()),
        }
    }

    fn preflight_mutation(
        &self,
        expected_revision: u64,
        at_unix: u64,
    ) -> Result<u64, LifecycleError> {
        self.validate_revision(expected_revision)?;
        self.validate_clock(at_unix)?;
        self.ensure_audit_capacity()?;
        self.revision
            .checked_add(1)
            .ok_or(LifecycleError::RevisionExhausted)
    }

    fn validate_revision(&self, expected_revision: u64) -> Result<(), LifecycleError> {
        if expected_revision != self.revision {
            return Err(LifecycleError::RevisionMismatch {
                expected: expected_revision,
                actual: self.revision,
            });
        }
        Ok(())
    }

    fn validate_clock(&self, at_unix: u64) -> Result<(), LifecycleError> {
        if at_unix < self.last_mutation_unix {
            return Err(LifecycleError::ClockRegression {
                previous: self.last_mutation_unix,
                requested: at_unix,
            });
        }
        Ok(())
    }

    fn ensure_audit_capacity(&self) -> Result<(), LifecycleError> {
        if self.audit.len() >= MAX_LIFECYCLE_AUDIT_EVENTS {
            return Err(LifecycleError::AuditCapacityExceeded {
                limit: MAX_LIFECYCLE_AUDIT_EVENTS,
            });
        }
        Ok(())
    }

    fn commit_mutation(
        &mut self,
        revision: u64,
        at_unix: u64,
        action: LifecycleAction,
        domain: KeyDomain,
        epoch: u32,
    ) {
        self.revision = revision;
        self.last_mutation_unix = at_unix;
        self.audit.push(LifecycleAuditEvent {
            revision,
            action,
            domain,
            epoch,
            at_unix,
        });
    }
}

fn intervals_overlap(
    left_start: u64,
    left_end: u64,
    right_start: u64,
    right_end: u64,
) -> bool {
    left_start < right_end && right_start < left_end
}

fn verification_overlap_exceeds(schedule: &DomainSchedule, candidate: &EpochWindow) -> bool {
    let windows = schedule
        .epochs
        .values()
        .filter(|record| !record.is_revoked())
        .map(|record| {
            (
                record.window.not_before_unix,
                record.window.verify_until_unix,
            )
        })
        .chain(std::iter::once((
            candidate.not_before_unix,
            candidate.verify_until_unix,
        )))
        .collect::<Vec<_>>();
    windows.iter().any(|(sample, _)| {
        windows
            .iter()
            .filter(|(start, end)| start <= sample && sample < end)
            .count()
            > MAX_VERIFICATION_EPOCHS_AT_ONCE
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{KeyHandle, KeyReference};

    fn key(domain: KeyDomain, epoch: u32) -> KeyReference {
        KeyReference::new(
            domain,
            KeyHandle::new(format!("kms://token/{domain:?}/{epoch}")).unwrap(),
            Some(epoch),
        )
        .unwrap()
    }

    fn window(
        domain: KeyDomain,
        epoch: u32,
        not_before: u64,
        sign_until: u64,
        verify_until: u64,
    ) -> EpochWindow {
        EpochWindow::new(
            key(domain, epoch),
            not_before,
            sign_until,
            verify_until,
        )
        .unwrap()
    }

    #[test]
    fn all_six_domains_are_isolated() {
        let mut registry = KeyEpochRegistry::default();
        for (index, domain) in ALL_KEY_DOMAINS.into_iter().enumerate() {
            let revision = u64::try_from(index).unwrap();
            registry
                .install(revision, window(domain, 1, 10, 20, 30), 1)
                .unwrap();
        }
        assert_eq!(registry.health(15).signing_ready_domains, 6);
        for domain in ALL_KEY_DOMAINS {
            assert_eq!(registry.signing_key(domain, 15).unwrap().domain, domain);
        }
    }

    #[test]
    fn epoch_and_signing_windows_are_monotonic_and_non_overlapping() {
        let mut registry = KeyEpochRegistry::default();
        registry
            .install(
                0,
                window(KeyDomain::AccessToken, 7, 10, 20, 30),
                1,
            )
            .unwrap();
        let before = registry.clone();
        assert!(matches!(
            registry.install(
                1,
                window(KeyDomain::AccessToken, 6, 20, 25, 35),
                2,
            ),
            Err(LifecycleError::EpochNotMonotonic { .. })
        ));
        assert_eq!(registry, before);
        assert!(matches!(
            registry.install(
                1,
                window(KeyDomain::AccessToken, 8, 19, 25, 35),
                2,
            ),
            Err(LifecycleError::SigningWindowOverlap { .. })
        ));
        assert_eq!(registry, before);
        registry
            .install(
                1,
                window(KeyDomain::AccessToken, 8, 20, 30, 40),
                2,
            )
            .unwrap();
        assert_eq!(
            registry.signing_key(KeyDomain::AccessToken, 19).unwrap().epoch,
            Some(7)
        );
        assert_eq!(
            registry.signing_key(KeyDomain::AccessToken, 20).unwrap().epoch,
            Some(8)
        );
    }

    #[test]
    fn verification_overlap_is_bounded_to_two_epochs() {
        let mut registry = KeyEpochRegistry::default();
        registry
            .install(
                0,
                window(KeyDomain::AccessToken, 1, 0, 10, 30),
                0,
            )
            .unwrap();
        registry
            .install(
                1,
                window(KeyDomain::AccessToken, 2, 10, 20, 40),
                1,
            )
            .unwrap();
        let before = registry.clone();
        assert!(matches!(
            registry.install(
                2,
                window(KeyDomain::AccessToken, 3, 20, 25, 50),
                2,
            ),
            Err(LifecycleError::VerificationWindowLimitExceeded { .. })
        ));
        assert_eq!(registry, before);
        assert_eq!(
            registry.status(KeyDomain::AccessToken, 15).verification_epochs,
            vec![2, 1]
        );
    }

    #[test]
    fn verification_requires_the_exact_epoch_without_fallback() {
        let mut registry = KeyEpochRegistry::default();
        registry
            .install(
                0,
                window(KeyDomain::AccessToken, 4, 10, 20, 30),
                1,
            )
            .unwrap();
        assert!(matches!(
            registry.verification_key(KeyDomain::AccessToken, 3, 15),
            Err(LifecycleError::EpochUnknown { epoch: 3, .. })
        ));
        assert!(matches!(
            registry.verification_key(KeyDomain::RefreshToken, 4, 15),
            Err(LifecycleError::EpochUnknown { .. })
        ));
        assert_eq!(
            registry
                .verification_key(KeyDomain::AccessToken, 4, 25)
                .unwrap()
                .epoch,
            Some(4)
        );
    }

    #[test]
    fn emergency_revoke_is_monotonic_idempotent_and_immediate() {
        let mut registry = KeyEpochRegistry::default();
        registry
            .install(0, window(KeyDomain::Socket, 1, 10, 20, 30), 1)
            .unwrap();
        let applied = registry.revoke(1, KeyDomain::Socket, 1, 15).unwrap();
        assert!(applied.applied);
        assert_eq!(applied.revision, 2);
        assert!(matches!(
            registry.signing_key(KeyDomain::Socket, 15),
            Err(LifecycleError::NoSigningEpoch { .. })
        ));
        assert!(matches!(
            registry.verification_key(KeyDomain::Socket, 1, 15),
            Err(LifecycleError::EpochRevoked { .. })
        ));
        let replay = registry.revoke(2, KeyDomain::Socket, 1, 16).unwrap();
        assert!(!replay.applied);
        assert_eq!(replay.revision, 2);
        assert_eq!(registry.audit_events().len(), 2);
    }

    #[test]
    fn retirement_requires_revocation_or_verification_expiry_and_preserves_high_watermark() {
        let mut registry = KeyEpochRegistry::default();
        registry
            .install(0, window(KeyDomain::Console, 5, 10, 20, 30), 1)
            .unwrap();
        let before = registry.clone();
        assert!(matches!(
            registry.retire(1, KeyDomain::Console, 5, 29),
            Err(LifecycleError::EpochRetirementNotAllowed { .. })
        ));
        assert_eq!(registry, before);
        registry
            .retire(1, KeyDomain::Console, 5, 30)
            .unwrap();
        assert_eq!(
            registry
                .status(KeyDomain::Console, 30)
                .highest_epoch_ever,
            Some(5)
        );
        assert!(matches!(
            registry.install(2, window(KeyDomain::Console, 5, 40, 50, 60), 31),
            Err(LifecycleError::EpochNotMonotonic { .. })
        ));
        registry
            .install(2, window(KeyDomain::Console, 6, 40, 50, 60), 31)
            .unwrap();
    }

    #[test]
    fn stale_revision_clock_regression_and_revision_exhaustion_are_atomic() {
        let mut registry = KeyEpochRegistry::default();
        registry
            .install(0, window(KeyDomain::Authority, 1, 10, 20, 30), 5)
            .unwrap();
        let before = registry.clone();
        assert!(matches!(
            registry.revoke(0, KeyDomain::Authority, 1, 6),
            Err(LifecycleError::RevisionMismatch { .. })
        ));
        assert_eq!(registry, before);
        assert!(matches!(
            registry.revoke(1, KeyDomain::Authority, 1, 4),
            Err(LifecycleError::ClockRegression { .. })
        ));
        assert_eq!(registry, before);

        registry.revision = u64::MAX;
        let exhausted = registry.clone();
        assert_eq!(
            registry.revoke(u64::MAX, KeyDomain::Authority, 1, 6),
            Err(LifecycleError::RevisionExhausted)
        );
        assert_eq!(registry, exhausted);
    }

    #[test]
    fn audit_capacity_fails_before_schedule_mutation() {
        let mut registry = KeyEpochRegistry::default();
        registry.audit = vec![
            LifecycleAuditEvent {
                revision: 1,
                action: LifecycleAction::Installed,
                domain: KeyDomain::AccessToken,
                epoch: 1,
                at_unix: 1,
            };
            MAX_LIFECYCLE_AUDIT_EVENTS
        ];
        let before = registry.clone();
        assert!(matches!(
            registry.install(
                0,
                window(KeyDomain::AccessToken, 1, 10, 20, 30),
                1,
            ),
            Err(LifecycleError::AuditCapacityExceeded { .. })
        ));
        assert_eq!(registry, before);
    }

    #[test]
    fn status_and_health_expose_no_key_handle() {
        let mut registry = KeyEpochRegistry::default();
        registry
            .install(
                0,
                window(KeyDomain::RuntimeHttp, 1, 10, 20, 30),
                1,
            )
            .unwrap();
        let status = format!("{:?}", registry.status(KeyDomain::RuntimeHttp, 15));
        let health = format!("{:?}", registry.health(15));
        assert!(!status.contains("kms://"));
        assert!(!health.contains("kms://"));
    }

    #[test]
    fn invalid_windows_and_legacy_references_fail_closed() {
        let legacy = KeyReference::new(
            KeyDomain::AccessToken,
            KeyHandle::new("kms://legacy").unwrap(),
            None,
        )
        .unwrap();
        assert_eq!(
            EpochWindow::new(legacy, 10, 20, 30),
            Err(LifecycleError::EpochRequired)
        );
        assert_eq!(
            EpochWindow::new(key(KeyDomain::AccessToken, 1), 10, 10, 30),
            Err(LifecycleError::InvalidWindow)
        );
    }
}
