//! Monotonic migration, cutover and rollback fencing model.
//!
//! This source contract does not execute a production migration.  It
//! prevents a controller from silently skipping snapshot, catch-up,
//! semantic comparison, write fencing, canary or source-read-only
//! stages and preserves a strictly increasing fence epoch across
//! rollback and re-entry.

use std::fmt;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct MigrationDigest([u8; 32]);

impl MigrationDigest {
    pub fn new(value: [u8; 32]) -> Result<Self, MigrationFenceError> {
        if value.iter().all(|byte| *byte == 0) {
            return Err(MigrationFenceError::ZeroDigest);
        }
        Ok(Self(value))
    }

    pub const fn as_bytes(self) -> [u8; 32] {
        self.0
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct MigrationCursor(u64);

impl MigrationCursor {
    pub const fn new(value: u64) -> Self {
        Self(value)
    }

    pub const fn get(self) -> u64 {
        self.0
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd)]
pub struct FenceEpoch(u64);

impl FenceEpoch {
    pub fn new(value: u64) -> Result<Self, MigrationFenceError> {
        if value == 0 {
            return Err(MigrationFenceError::ZeroFenceEpoch);
        }
        Ok(Self(value))
    }

    pub const fn get(self) -> u64 {
        self.0
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct MigrationIdentity {
    pub source_schema: MigrationDigest,
    pub target_schema: MigrationDigest,
    pub mapping: MigrationDigest,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AuthorityMode {
    SourceOnly,
    Fenced,
    Canary { target_basis_points: u16 },
    TargetOnly,
    Retired,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MigrationPhase {
    SourcePrimary,
    SnapshotSealed,
    CatchingUp,
    ShadowVerified,
    WriteFenced,
    Canary { target_basis_points: u16 },
    TargetPrimary,
    SourceReadOnly,
    Retired,
    RolledBack,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct MigrationFenceSnapshot {
    pub identity: MigrationIdentity,
    pub phase: MigrationPhase,
    pub source_cursor: MigrationCursor,
    pub target_cursor: MigrationCursor,
    pub snapshot_digest: Option<MigrationDigest>,
    pub semantic_digest: Option<MigrationDigest>,
    pub fence_epoch: Option<FenceEpoch>,
    pub cutover_receipt: Option<MigrationDigest>,
    pub source_read_only_receipt: Option<MigrationDigest>,
    pub rollback_receipt: Option<MigrationDigest>,
    pub retirement_receipt: Option<MigrationDigest>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum MigrationFenceError {
    ZeroDigest,
    ZeroFenceEpoch,
    InvalidIdentity,
    InvalidPhase {
        expected: &'static str,
        observed: MigrationPhase,
    },
    CursorRegression {
        side: &'static str,
        current: u64,
        received: u64,
    },
    TargetAheadOfSource {
        source: u64,
        target: u64,
    },
    CatchUpIncomplete {
        source: u64,
        target: u64,
    },
    SemanticMismatch,
    NonIncreasingFenceEpoch {
        current: Option<FenceEpoch>,
        received: FenceEpoch,
    },
    InvalidCanaryBasisPoints(u16),
    CanaryRegression {
        current: u16,
        received: u16,
    },
    CanaryIncomplete(u16),
    ReceiptMissing(&'static str),
    RetirementIsTerminal,
}

impl fmt::Display for MigrationFenceError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::ZeroDigest => formatter.write_str("migration digest must not be zero"),
            Self::ZeroFenceEpoch => formatter.write_str("migration fence epoch must be positive"),
            Self::InvalidIdentity => {
                formatter.write_str("source and target schema identities must differ")
            }
            Self::InvalidPhase { expected, observed } => {
                write!(
                    formatter,
                    "migration phase {observed:?} does not satisfy {expected}"
                )
            }
            Self::CursorRegression {
                side,
                current,
                received,
            } => write!(
                formatter,
                "{side} cursor regressed from {current} to {received}"
            ),
            Self::TargetAheadOfSource { source, target } => write!(
                formatter,
                "target cursor {target} is ahead of source cursor {source}"
            ),
            Self::CatchUpIncomplete { source, target } => write!(
                formatter,
                "target cursor {target} has not caught source cursor {source}"
            ),
            Self::SemanticMismatch => {
                formatter.write_str("source and target semantic digests differ")
            }
            Self::NonIncreasingFenceEpoch { current, received } => write!(
                formatter,
                "fence epoch {} does not advance current epoch {:?}",
                received.get(),
                current.map(FenceEpoch::get)
            ),
            Self::InvalidCanaryBasisPoints(value) => write!(
                formatter,
                "canary target basis points {value} must be in 1..=10000"
            ),
            Self::CanaryRegression { current, received } => write!(
                formatter,
                "canary target allocation regressed from {current} to {received} basis points"
            ),
            Self::CanaryIncomplete(value) => write!(
                formatter,
                "canary target allocation is {value}, not 10000 basis points"
            ),
            Self::ReceiptMissing(name) => write!(formatter, "{name} receipt must be nonzero"),
            Self::RetirementIsTerminal => {
                formatter.write_str("retired source cannot be rolled back")
            }
        }
    }
}

impl std::error::Error for MigrationFenceError {}

#[derive(Clone, Debug)]
pub struct MigrationFence {
    state: MigrationFenceSnapshot,
}

impl MigrationFence {
    pub fn new(identity: MigrationIdentity) -> Result<Self, MigrationFenceError> {
        if identity.source_schema == identity.target_schema {
            return Err(MigrationFenceError::InvalidIdentity);
        }
        Ok(Self {
            state: MigrationFenceSnapshot {
                identity,
                phase: MigrationPhase::SourcePrimary,
                source_cursor: MigrationCursor::new(0),
                target_cursor: MigrationCursor::new(0),
                snapshot_digest: None,
                semantic_digest: None,
                fence_epoch: None,
                cutover_receipt: None,
                source_read_only_receipt: None,
                rollback_receipt: None,
                retirement_receipt: None,
            },
        })
    }

    pub const fn snapshot(&self) -> MigrationFenceSnapshot {
        self.state
    }

    pub const fn authority_mode(&self) -> AuthorityMode {
        match self.state.phase {
            MigrationPhase::SourcePrimary
            | MigrationPhase::SnapshotSealed
            | MigrationPhase::CatchingUp
            | MigrationPhase::ShadowVerified
            | MigrationPhase::RolledBack => AuthorityMode::SourceOnly,
            MigrationPhase::WriteFenced => AuthorityMode::Fenced,
            MigrationPhase::Canary {
                target_basis_points,
            } => AuthorityMode::Canary {
                target_basis_points,
            },
            MigrationPhase::TargetPrimary | MigrationPhase::SourceReadOnly => {
                AuthorityMode::TargetOnly
            }
            MigrationPhase::Retired => AuthorityMode::Retired,
        }
    }

    pub fn advance_source(&mut self, cursor: MigrationCursor) -> Result<(), MigrationFenceError> {
        if matches!(self.state.phase, MigrationPhase::Retired) {
            return Err(MigrationFenceError::RetirementIsTerminal);
        }
        if cursor < self.state.source_cursor {
            return Err(MigrationFenceError::CursorRegression {
                side: "source",
                current: self.state.source_cursor.get(),
                received: cursor.get(),
            });
        }
        self.state.source_cursor = cursor;
        Ok(())
    }

    pub fn seal_snapshot(
        &mut self,
        cursor: MigrationCursor,
        digest: MigrationDigest,
    ) -> Result<(), MigrationFenceError> {
        self.require_phase("SourcePrimary", |phase| {
            phase == MigrationPhase::SourcePrimary
        })?;
        self.advance_source(cursor)?;
        self.state.snapshot_digest = Some(digest);
        self.state.phase = MigrationPhase::SnapshotSealed;
        Ok(())
    }

    pub fn advance_target(&mut self, cursor: MigrationCursor) -> Result<(), MigrationFenceError> {
        self.require_phase("SnapshotSealed or CatchingUp", |phase| {
            matches!(
                phase,
                MigrationPhase::SnapshotSealed | MigrationPhase::CatchingUp
            )
        })?;
        if cursor < self.state.target_cursor {
            return Err(MigrationFenceError::CursorRegression {
                side: "target",
                current: self.state.target_cursor.get(),
                received: cursor.get(),
            });
        }
        if cursor > self.state.source_cursor {
            return Err(MigrationFenceError::TargetAheadOfSource {
                source: self.state.source_cursor.get(),
                target: cursor.get(),
            });
        }
        self.state.target_cursor = cursor;
        self.state.phase = MigrationPhase::CatchingUp;
        Ok(())
    }

    pub fn verify_shadow(
        &mut self,
        source_semantic_digest: MigrationDigest,
        target_semantic_digest: MigrationDigest,
    ) -> Result<(), MigrationFenceError> {
        self.require_phase("CatchingUp", |phase| phase == MigrationPhase::CatchingUp)?;
        if self.state.source_cursor != self.state.target_cursor {
            return Err(MigrationFenceError::CatchUpIncomplete {
                source: self.state.source_cursor.get(),
                target: self.state.target_cursor.get(),
            });
        }
        if source_semantic_digest != target_semantic_digest {
            return Err(MigrationFenceError::SemanticMismatch);
        }
        self.state.semantic_digest = Some(source_semantic_digest);
        self.state.phase = MigrationPhase::ShadowVerified;
        Ok(())
    }

    pub fn activate_write_fence(
        &mut self,
        epoch: FenceEpoch,
        fence_receipt: MigrationDigest,
    ) -> Result<(), MigrationFenceError> {
        self.require_phase("ShadowVerified", |phase| {
            phase == MigrationPhase::ShadowVerified
        })?;
        if self
            .state
            .fence_epoch
            .is_some_and(|current| epoch <= current)
        {
            return Err(MigrationFenceError::NonIncreasingFenceEpoch {
                current: self.state.fence_epoch,
                received: epoch,
            });
        }
        if fence_receipt.as_bytes().iter().all(|byte| *byte == 0) {
            return Err(MigrationFenceError::ReceiptMissing("write-fence"));
        }
        self.state.fence_epoch = Some(epoch);
        self.state.phase = MigrationPhase::WriteFenced;
        Ok(())
    }

    pub fn set_canary(&mut self, target_basis_points: u16) -> Result<(), MigrationFenceError> {
        if !(1..=10_000).contains(&target_basis_points) {
            return Err(MigrationFenceError::InvalidCanaryBasisPoints(
                target_basis_points,
            ));
        }
        let current = match self.state.phase {
            MigrationPhase::WriteFenced => 0,
            MigrationPhase::Canary {
                target_basis_points,
            } => target_basis_points,
            observed => {
                return Err(MigrationFenceError::InvalidPhase {
                    expected: "WriteFenced or Canary",
                    observed,
                });
            }
        };
        if target_basis_points < current {
            return Err(MigrationFenceError::CanaryRegression {
                current,
                received: target_basis_points,
            });
        }
        self.state.phase = MigrationPhase::Canary {
            target_basis_points,
        };
        Ok(())
    }

    pub fn promote_target(
        &mut self,
        cutover_receipt: MigrationDigest,
    ) -> Result<(), MigrationFenceError> {
        let value = match self.state.phase {
            MigrationPhase::Canary {
                target_basis_points,
            } => target_basis_points,
            observed => {
                return Err(MigrationFenceError::InvalidPhase {
                    expected: "Canary at 10000 basis points",
                    observed,
                });
            }
        };
        if value != 10_000 {
            return Err(MigrationFenceError::CanaryIncomplete(value));
        }
        self.state.cutover_receipt = Some(cutover_receipt);
        self.state.phase = MigrationPhase::TargetPrimary;
        Ok(())
    }

    pub fn mark_source_read_only(
        &mut self,
        receipt: MigrationDigest,
    ) -> Result<(), MigrationFenceError> {
        self.require_phase("TargetPrimary", |phase| {
            phase == MigrationPhase::TargetPrimary
        })?;
        self.state.source_read_only_receipt = Some(receipt);
        self.state.phase = MigrationPhase::SourceReadOnly;
        Ok(())
    }

    pub fn retire_source(
        &mut self,
        retirement_receipt: MigrationDigest,
    ) -> Result<(), MigrationFenceError> {
        self.require_phase("SourceReadOnly", |phase| {
            phase == MigrationPhase::SourceReadOnly
        })?;
        self.state.retirement_receipt = Some(retirement_receipt);
        self.state.phase = MigrationPhase::Retired;
        Ok(())
    }

    pub fn resume_after_rollback(
        &mut self,
        source_cursor: MigrationCursor,
    ) -> Result<(), MigrationFenceError> {
        self.require_phase("RolledBack", |phase| phase == MigrationPhase::RolledBack)?;
        if source_cursor < self.state.source_cursor {
            return Err(MigrationFenceError::CursorRegression {
                side: "source",
                current: self.state.source_cursor.get(),
                received: source_cursor.get(),
            });
        }
        self.state.source_cursor = source_cursor;
        self.state.target_cursor = MigrationCursor::new(0);
        self.state.semantic_digest = None;
        self.state.cutover_receipt = None;
        self.state.source_read_only_receipt = None;
        self.state.retirement_receipt = None;
        self.state.phase = MigrationPhase::CatchingUp;
        Ok(())
    }

    pub fn rollback(
        &mut self,
        rollback_epoch: FenceEpoch,
        rollback_receipt: MigrationDigest,
    ) -> Result<(), MigrationFenceError> {
        if self.state.phase == MigrationPhase::Retired {
            return Err(MigrationFenceError::RetirementIsTerminal);
        }
        if !matches!(
            self.state.phase,
            MigrationPhase::WriteFenced
                | MigrationPhase::Canary { .. }
                | MigrationPhase::TargetPrimary
                | MigrationPhase::SourceReadOnly
        ) {
            return Err(MigrationFenceError::InvalidPhase {
                expected: "fenced, canary, target-primary or source-read-only",
                observed: self.state.phase,
            });
        }
        if self
            .state
            .fence_epoch
            .is_some_and(|current| rollback_epoch <= current)
        {
            return Err(MigrationFenceError::NonIncreasingFenceEpoch {
                current: self.state.fence_epoch,
                received: rollback_epoch,
            });
        }
        self.state.fence_epoch = Some(rollback_epoch);
        self.state.rollback_receipt = Some(rollback_receipt);
        self.state.phase = MigrationPhase::RolledBack;
        Ok(())
    }

    fn require_phase(
        &self,
        expected: &'static str,
        predicate: impl FnOnce(MigrationPhase) -> bool,
    ) -> Result<(), MigrationFenceError> {
        if predicate(self.state.phase) {
            Ok(())
        } else {
            Err(MigrationFenceError::InvalidPhase {
                expected,
                observed: self.state.phase,
            })
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn digest(value: u8) -> MigrationDigest {
        MigrationDigest::new([value; 32]).unwrap()
    }

    fn identity() -> MigrationIdentity {
        MigrationIdentity {
            source_schema: digest(1),
            target_schema: digest(2),
            mapping: digest(3),
        }
    }

    fn caught_up() -> MigrationFence {
        let mut fence = MigrationFence::new(identity()).unwrap();
        fence.advance_source(MigrationCursor::new(10)).unwrap();
        fence
            .seal_snapshot(MigrationCursor::new(10), digest(4))
            .unwrap();
        fence.advance_target(MigrationCursor::new(10)).unwrap();
        fence.verify_shadow(digest(5), digest(5)).unwrap();
        fence
    }

    #[test]
    fn cursor_regression_and_target_ahead_fail_without_mutation() {
        let mut fence = MigrationFence::new(identity()).unwrap();
        fence.advance_source(MigrationCursor::new(10)).unwrap();
        fence
            .seal_snapshot(MigrationCursor::new(10), digest(4))
            .unwrap();
        let before = fence.snapshot();
        assert!(matches!(
            fence.advance_target(MigrationCursor::new(11)),
            Err(MigrationFenceError::TargetAheadOfSource { .. })
        ));
        assert_eq!(fence.snapshot(), before);
        fence.advance_target(MigrationCursor::new(8)).unwrap();
        let before = fence.snapshot();
        assert!(matches!(
            fence.advance_target(MigrationCursor::new(7)),
            Err(MigrationFenceError::CursorRegression { side: "target", .. })
        ));
        assert_eq!(fence.snapshot(), before);
    }

    #[test]
    fn cutover_requires_zero_lag_equal_semantics_and_full_canary() {
        let mut fence = MigrationFence::new(identity()).unwrap();
        fence.advance_source(MigrationCursor::new(10)).unwrap();
        fence
            .seal_snapshot(MigrationCursor::new(10), digest(4))
            .unwrap();
        fence.advance_target(MigrationCursor::new(9)).unwrap();
        assert!(matches!(
            fence.verify_shadow(digest(5), digest(5)),
            Err(MigrationFenceError::CatchUpIncomplete { .. })
        ));
        fence.advance_target(MigrationCursor::new(10)).unwrap();
        assert_eq!(
            fence.verify_shadow(digest(5), digest(6)),
            Err(MigrationFenceError::SemanticMismatch)
        );
        fence.verify_shadow(digest(5), digest(5)).unwrap();
        fence
            .activate_write_fence(FenceEpoch::new(1).unwrap(), digest(7))
            .unwrap();
        fence.set_canary(500).unwrap();
        assert_eq!(
            fence.promote_target(digest(8)),
            Err(MigrationFenceError::CanaryIncomplete(500))
        );
        fence.set_canary(10_000).unwrap();
        fence.promote_target(digest(8)).unwrap();
        assert_eq!(fence.authority_mode(), AuthorityMode::TargetOnly);
    }

    #[test]
    fn rollback_requires_a_new_epoch_and_restores_source_only_authority() {
        let mut fence = caught_up();
        fence
            .activate_write_fence(FenceEpoch::new(4).unwrap(), digest(7))
            .unwrap();
        fence.set_canary(1_000).unwrap();
        let before = fence.snapshot();
        assert!(matches!(
            fence.rollback(FenceEpoch::new(4).unwrap(), digest(9)),
            Err(MigrationFenceError::NonIncreasingFenceEpoch { .. })
        ));
        assert_eq!(fence.snapshot(), before);
        fence
            .rollback(FenceEpoch::new(5).unwrap(), digest(9))
            .unwrap();
        assert_eq!(fence.authority_mode(), AuthorityMode::SourceOnly);
        assert_eq!(fence.snapshot().phase, MigrationPhase::RolledBack);
        assert!(matches!(
            fence.activate_write_fence(FenceEpoch::new(6).unwrap(), digest(12)),
            Err(MigrationFenceError::InvalidPhase { .. })
        ));
        fence
            .resume_after_rollback(MigrationCursor::new(12))
            .unwrap();
        fence.advance_target(MigrationCursor::new(12)).unwrap();
        fence.verify_shadow(digest(13), digest(13)).unwrap();
        fence
            .activate_write_fence(FenceEpoch::new(6).unwrap(), digest(12))
            .unwrap();
    }

    #[test]
    fn retirement_is_terminal() {
        let mut fence = caught_up();
        fence
            .activate_write_fence(FenceEpoch::new(1).unwrap(), digest(7))
            .unwrap();
        fence.set_canary(10_000).unwrap();
        fence.promote_target(digest(8)).unwrap();
        fence.mark_source_read_only(digest(9)).unwrap();
        fence.retire_source(digest(10)).unwrap();
        assert_eq!(fence.authority_mode(), AuthorityMode::Retired);
        assert_eq!(
            fence.rollback(FenceEpoch::new(2).unwrap(), digest(11)),
            Err(MigrationFenceError::RetirementIsTerminal)
        );
    }
}
