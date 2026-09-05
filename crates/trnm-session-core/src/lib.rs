#![forbid(unsafe_code)]

use std::collections::BTreeSet;

use trnm_contracts::{
    DomainError, RefreshTokenId, RetryClass, SessionFamilyId, SessionGeneration, StableCode,
};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RevocationReason {
    Logout,
    Administrator,
    CredentialReset,
    RefreshReplay,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FamilyStatus {
    Active,
    Revoked(RevocationReason),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct RotationReceipt {
    pub previous_generation: SessionGeneration,
    pub current_generation: SessionGeneration,
    pub active_token: RefreshTokenId,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RefreshFamily {
    family_id: SessionFamilyId,
    generation: SessionGeneration,
    active_token: RefreshTokenId,
    consumed_tokens: BTreeSet<RefreshTokenId>,
    status: FamilyStatus,
}

impl RefreshFamily {
    pub fn new(
        family_id: SessionFamilyId,
        active_token: RefreshTokenId,
    ) -> Result<Self, DomainError> {
        if family_id.is_zero() || active_token.is_zero() {
            return Err(error(
                StableCode::InvalidArgument,
                "zero_session_family_or_token",
                RetryClass::Never,
            ));
        }
        Ok(Self {
            family_id,
            generation: SessionGeneration::default(),
            active_token,
            consumed_tokens: BTreeSet::new(),
            status: FamilyStatus::Active,
        })
    }

    #[must_use]
    pub const fn family_id(&self) -> SessionFamilyId {
        self.family_id
    }

    #[must_use]
    pub const fn generation(&self) -> SessionGeneration {
        self.generation
    }

    #[must_use]
    pub const fn status(&self) -> FamilyStatus {
        self.status
    }

    #[must_use]
    pub const fn active_token(&self) -> RefreshTokenId {
        self.active_token
    }

    pub fn verify_active(&self, token: RefreshTokenId) -> Result<(), DomainError> {
        self.require_active()?;
        if token != self.active_token {
            return Err(error(
                StableCode::Unauthenticated,
                "refresh_token_not_active",
                RetryClass::Never,
            ));
        }
        Ok(())
    }

    pub fn rotate(
        &mut self,
        presented_token: RefreshTokenId,
        replacement_token: RefreshTokenId,
    ) -> Result<RotationReceipt, DomainError> {
        self.require_active()?;
        if presented_token.is_zero() || replacement_token.is_zero() {
            return Err(error(
                StableCode::InvalidArgument,
                "zero_refresh_token",
                RetryClass::Never,
            ));
        }

        if self.consumed_tokens.contains(&presented_token) {
            self.status = FamilyStatus::Revoked(RevocationReason::RefreshReplay);
            return Err(error(
                StableCode::Unauthenticated,
                "refresh_replay_detected",
                RetryClass::Never,
            ));
        }
        if presented_token != self.active_token {
            return Err(error(
                StableCode::Unauthenticated,
                "refresh_token_unknown",
                RetryClass::Never,
            ));
        }
        if replacement_token == self.active_token
            || self.consumed_tokens.contains(&replacement_token)
        {
            return Err(error(
                StableCode::AlreadyExists,
                "replacement_refresh_token_reused",
                RetryClass::Never,
            ));
        }

        let previous_generation = self.generation;
        // Validate every fallible rotation condition before changing token state.
        let current_generation = previous_generation.checked_next()?;
        self.consumed_tokens.insert(self.active_token);
        self.active_token = replacement_token;
        self.generation = current_generation;
        Ok(RotationReceipt {
            previous_generation,
            current_generation: self.generation,
            active_token: self.active_token,
        })
    }

    pub fn revoke(&mut self, reason: RevocationReason) {
        self.status = FamilyStatus::Revoked(reason);
    }

    fn require_active(&self) -> Result<(), DomainError> {
        match self.status {
            FamilyStatus::Active => Ok(()),
            FamilyStatus::Revoked(_) => Err(error(
                StableCode::Unauthenticated,
                "session_family_revoked",
                RetryClass::Never,
            )),
        }
    }
}

const fn error(code: StableCode, reason: &'static str, retry: RetryClass) -> DomainError {
    DomainError::new(code, reason, retry)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn family() -> RefreshFamily {
        RefreshFamily::new(SessionFamilyId::new([1; 16]), RefreshTokenId::new([2; 16])).unwrap()
    }

    #[test]
    fn refresh_rotation_advances_generation_and_replaces_active_token() {
        let mut value = family();
        let receipt = value
            .rotate(RefreshTokenId::new([2; 16]), RefreshTokenId::new([3; 16]))
            .unwrap();
        assert_eq!(receipt.previous_generation, SessionGeneration::new(0));
        assert_eq!(receipt.current_generation, SessionGeneration::new(1));
        assert_eq!(value.active_token(), RefreshTokenId::new([3; 16]));
    }

    #[test]
    fn replay_of_consumed_refresh_token_revokes_entire_family() {
        let mut value = family();
        value
            .rotate(RefreshTokenId::new([2; 16]), RefreshTokenId::new([3; 16]))
            .unwrap();
        let error = value
            .rotate(RefreshTokenId::new([2; 16]), RefreshTokenId::new([4; 16]))
            .unwrap_err();
        assert_eq!(error.reason(), "refresh_replay_detected");
        assert_eq!(
            value.status(),
            FamilyStatus::Revoked(RevocationReason::RefreshReplay)
        );
        assert_eq!(
            value
                .verify_active(RefreshTokenId::new([3; 16]))
                .unwrap_err()
                .reason(),
            "session_family_revoked"
        );
    }

    #[test]
    fn unknown_refresh_token_does_not_rotate_or_revoke_family() {
        let mut value = family();
        let error = value
            .rotate(RefreshTokenId::new([9; 16]), RefreshTokenId::new([3; 16]))
            .unwrap_err();
        assert_eq!(error.reason(), "refresh_token_unknown");
        assert_eq!(value.status(), FamilyStatus::Active);
        assert_eq!(value.generation(), SessionGeneration::new(0));
    }

    #[test]
    fn logout_revocation_is_terminal() {
        let mut value = family();
        value.revoke(RevocationReason::Logout);
        assert_eq!(
            value
                .verify_active(RefreshTokenId::new([2; 16]))
                .unwrap_err()
                .reason(),
            "session_family_revoked"
        );
    }

    #[test]
    fn replacement_token_cannot_reuse_current_or_consumed_identity() {
        let mut value = family();
        assert_eq!(
            value
                .rotate(RefreshTokenId::new([2; 16]), RefreshTokenId::new([2; 16]))
                .unwrap_err()
                .reason(),
            "replacement_refresh_token_reused"
        );
        value
            .rotate(RefreshTokenId::new([2; 16]), RefreshTokenId::new([3; 16]))
            .unwrap();
        assert_eq!(
            value
                .rotate(RefreshTokenId::new([3; 16]), RefreshTokenId::new([2; 16]))
                .unwrap_err()
                .reason(),
            "replacement_refresh_token_reused"
        );
    }

    #[test]
    fn generation_overflow_preserves_entire_family() {
        let mut value = family();
        value.generation = SessionGeneration::new(u64::MAX);
        value.consumed_tokens.insert(RefreshTokenId::new([1; 16]));
        let before = value.clone();

        for replacement in [3, 4] {
            let failure = value
                .rotate(
                    RefreshTokenId::new([2; 16]),
                    RefreshTokenId::new([replacement; 16]),
                )
                .unwrap_err();
            assert_eq!(failure.code(), StableCode::OutOfRange);
            assert_eq!(failure.reason(), "counter_overflow");
            assert_eq!(failure.retry(), RetryClass::Never);
            assert_eq!(value, before);
            assert!(value.verify_active(before.active_token()).is_ok());
        }
    }

    #[test]
    fn final_generation_transition_succeeds_without_wrapping() {
        let mut value = family();
        value.generation = SessionGeneration::new(u64::MAX - 1);
        let receipt = value
            .rotate(RefreshTokenId::new([2; 16]), RefreshTokenId::new([3; 16]))
            .unwrap();
        assert_eq!(
            receipt.previous_generation,
            SessionGeneration::new(u64::MAX - 1)
        );
        assert_eq!(receipt.current_generation, SessionGeneration::new(u64::MAX));
        assert_eq!(receipt.active_token, RefreshTokenId::new([3; 16]));
        assert!(value
            .consumed_tokens
            .contains(&RefreshTokenId::new([2; 16])));
        let before = value.clone();

        assert_eq!(
            value
                .rotate(RefreshTokenId::new([3; 16]), RefreshTokenId::new([4; 16]))
                .unwrap_err()
                .reason(),
            "counter_overflow"
        );
        assert_eq!(value, before);
    }

    #[test]
    fn ordinary_rotation_rejections_preserve_all_fields() {
        let mut initial = family();
        initial
            .rotate(RefreshTokenId::new([2; 16]), RefreshTokenId::new([3; 16]))
            .unwrap();
        for (presented, replacement, reason) in [
            (0, 4, "zero_refresh_token"),
            (3, 0, "zero_refresh_token"),
            (9, 4, "refresh_token_unknown"),
            (3, 3, "replacement_refresh_token_reused"),
            (3, 2, "replacement_refresh_token_reused"),
        ] {
            let mut value = initial.clone();
            let failure = value
                .rotate(
                    RefreshTokenId::new([presented; 16]),
                    RefreshTokenId::new([replacement; 16]),
                )
                .unwrap_err();
            assert_eq!(failure.reason(), reason);
            assert_eq!(failure.retry(), RetryClass::Never);
            assert_eq!(value, initial);
        }
    }

    #[test]
    fn replay_at_generation_ceiling_still_revokes_without_rotating() {
        let mut value = family();
        value
            .rotate(RefreshTokenId::new([2; 16]), RefreshTokenId::new([3; 16]))
            .unwrap();
        value.generation = SessionGeneration::new(u64::MAX);
        let mut expected = value.clone();
        expected.status = FamilyStatus::Revoked(RevocationReason::RefreshReplay);
        assert_eq!(
            value
                .rotate(RefreshTokenId::new([2; 16]), RefreshTokenId::new([4; 16]))
                .unwrap_err()
                .reason(),
            "refresh_replay_detected"
        );
        assert_eq!(value, expected);
        assert_eq!(
            value
                .verify_active(RefreshTokenId::new([3; 16]))
                .unwrap_err()
                .reason(),
            "session_family_revoked"
        );
    }

    #[test]
    fn revoked_rotation_keeps_existing_reason_and_token_state() {
        for reason in [
            RevocationReason::Logout,
            RevocationReason::Administrator,
            RevocationReason::CredentialReset,
            RevocationReason::RefreshReplay,
        ] {
            let mut value = family();
            value.revoke(reason);
            let before = value.clone();
            assert_eq!(
                value
                    .rotate(RefreshTokenId::new([2; 16]), RefreshTokenId::new([3; 16]))
                    .unwrap_err()
                    .reason(),
                "session_family_revoked"
            );
            assert_eq!(value, before);
        }
    }
}
