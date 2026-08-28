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
        self.consumed_tokens.insert(self.active_token);
        self.active_token = replacement_token;
        self.generation = self.generation.checked_next()?;
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
}
