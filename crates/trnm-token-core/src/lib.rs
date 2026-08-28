#![forbid(unsafe_code)]

use std::collections::BTreeMap;

use trnm_contracts::{
    Digest32, DomainError, RetryClass, SessionFamilyId, SessionGeneration, StableCode, UserId,
};

const MAX_KEYS: usize = 32;

#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct TokenId([u8; 16]);

impl TokenId {
    #[must_use]
    pub const fn new(value: [u8; 16]) -> Self {
        Self(value)
    }

    #[must_use]
    pub fn is_zero(self) -> bool {
        self.0.iter().all(|byte| *byte == 0)
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum TokenKind {
    Access,
    Refresh,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum KeyRole {
    Access,
    Refresh,
}

impl From<TokenKind> for KeyRole {
    fn from(value: TokenKind) -> Self {
        match value {
            TokenKind::Access => Self::Access,
            TokenKind::Refresh => Self::Refresh,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TokenProfile {
    NakamaV340Legacy,
    TrillionniumFamilyV1,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Algorithm {
    Hs256,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum KeyStatus {
    Active,
    VerifyOnly,
    Retired,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct KeyDescriptor {
    pub role: KeyRole,
    pub epoch: u64,
    pub status: KeyStatus,
    pub not_before: i64,
    pub not_after: i64,
    pub material_digest: Digest32,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct KeyRing {
    keys: BTreeMap<(KeyRole, u64), KeyDescriptor>,
}

impl KeyRing {
    pub fn insert(&mut self, key: KeyDescriptor) -> Result<(), DomainError> {
        if self.keys.len() >= MAX_KEYS {
            return Err(error(
                StableCode::ResourceExhausted,
                "token_key_limit_exceeded",
                RetryClass::Never,
            ));
        }
        if key.epoch == 0 || key.not_after <= key.not_before || key.material_digest.is_zero() {
            return Err(invalid("invalid_token_key"));
        }
        if self.keys.insert((key.role, key.epoch), key).is_some() {
            return Err(error(
                StableCode::AlreadyExists,
                "token_key_epoch_exists",
                RetryClass::Never,
            ));
        }
        Ok(())
    }

    #[must_use]
    pub fn get(&self, role: KeyRole, epoch: u64) -> Option<KeyDescriptor> {
        self.keys.get(&(role, epoch)).copied()
    }

    fn issue_key(&self, role: KeyRole, now: i64) -> Result<KeyDescriptor, DomainError> {
        unique_key(
            self.keys.values().copied().filter(|key| {
                key.role == role && key.status == KeyStatus::Active && key_valid(*key, now)
            }),
            "token_issue_key_unavailable",
            "ambiguous_active_token_key",
        )
    }

    fn verify_key(
        &self,
        role: KeyRole,
        profile: TokenProfile,
        declared_epoch: Option<u64>,
        now: i64,
    ) -> Result<KeyDescriptor, DomainError> {
        match (profile, declared_epoch) {
            (TokenProfile::NakamaV340Legacy, None) => unique_key(
                self.keys.values().copied().filter(|key| {
                    key.role == role
                        && matches!(key.status, KeyStatus::Active | KeyStatus::VerifyOnly)
                        && key_valid(*key, now)
                }),
                "token_verify_key_unavailable",
                "ambiguous_legacy_key_epoch",
            ),
            (TokenProfile::NakamaV340Legacy, Some(_)) => {
                Err(invalid("legacy_token_must_not_declare_key_epoch"))
            }
            (TokenProfile::TrillionniumFamilyV1, None) => Err(invalid("token_key_epoch_required")),
            (TokenProfile::TrillionniumFamilyV1, Some(epoch)) => {
                let key = self
                    .get(role, epoch)
                    .ok_or_else(|| unauthenticated("token_key_not_found"))?;
                if !matches!(key.status, KeyStatus::Active | KeyStatus::VerifyOnly)
                    || !key_valid(key, now)
                {
                    return Err(unauthenticated("token_key_unavailable"));
                }
                Ok(key)
            }
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct TokenPolicy {
    pub access_lifetime_max_sec: i64,
    pub refresh_lifetime_max_sec: i64,
    pub clock_skew_sec: i64,
    pub max_username_bytes: usize,
    pub max_vars: usize,
    pub max_var_key_bytes: usize,
    pub max_var_value_bytes: usize,
}

impl Default for TokenPolicy {
    fn default() -> Self {
        Self {
            access_lifetime_max_sec: 24 * 60 * 60,
            refresh_lifetime_max_sec: 90 * 24 * 60 * 60,
            clock_skew_sec: 30,
            max_username_bytes: 128,
            max_vars: 64,
            max_var_key_bytes: 128,
            max_var_value_bytes: 1_024,
        }
    }
}

impl TokenPolicy {
    fn validate(self) -> Result<(), DomainError> {
        if self.access_lifetime_max_sec <= 0
            || self.refresh_lifetime_max_sec < self.access_lifetime_max_sec
            || self.clock_skew_sec < 0
            || self.max_username_bytes == 0
            || self.max_vars == 0
            || self.max_var_key_bytes == 0
            || self.max_var_value_bytes == 0
        {
            return Err(invalid("invalid_token_policy"));
        }
        Ok(())
    }

    fn lifetime(self, kind: TokenKind) -> i64 {
        match kind {
            TokenKind::Access => self.access_lifetime_max_sec,
            TokenKind::Refresh => self.refresh_lifetime_max_sec,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TokenClaims {
    pub token_id: TokenId,
    pub user_id: UserId,
    pub username: String,
    pub vars: BTreeMap<String, String>,
    pub issued_at: i64,
    pub expires_at: i64,
    pub family_id: Option<SessionFamilyId>,
    pub family_generation: Option<SessionGeneration>,
    pub key_epoch: Option<u64>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SigningPlan {
    pub algorithm: Algorithm,
    pub role: KeyRole,
    pub selected_key_epoch: u64,
    pub emitted_key_epoch: Option<u64>,
    pub key_material_digest: Digest32,
    pub claims: TokenClaims,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct VerificationPlan {
    pub algorithm: Algorithm,
    pub kind: TokenKind,
    pub profile: TokenProfile,
    pub selected_key_epoch: u64,
    pub key_material_digest: Digest32,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct VerifiedToken {
    pub kind: TokenKind,
    pub profile: TokenProfile,
    pub selected_key_epoch: u64,
    pub claims: TokenClaims,
}

pub fn prepare_issue(
    kind: TokenKind,
    profile: TokenProfile,
    claims: TokenClaims,
    now: i64,
    policy: TokenPolicy,
    keys: &KeyRing,
) -> Result<SigningPlan, DomainError> {
    policy.validate()?;
    validate_claims(&claims, kind, profile, now, policy, ValidationMode::Issue)?;
    let role = KeyRole::from(kind);
    let key = keys.issue_key(role, now)?;
    match profile {
        TokenProfile::NakamaV340Legacy => {
            if claims.key_epoch.is_some() {
                return Err(invalid("legacy_token_must_not_declare_key_epoch"));
            }
        }
        TokenProfile::TrillionniumFamilyV1 => {
            if claims.key_epoch != Some(key.epoch) {
                return Err(invalid("token_claim_key_epoch_mismatch"));
            }
        }
    }
    Ok(SigningPlan {
        algorithm: Algorithm::Hs256,
        role,
        selected_key_epoch: key.epoch,
        emitted_key_epoch: match profile {
            TokenProfile::NakamaV340Legacy => None,
            TokenProfile::TrillionniumFamilyV1 => Some(key.epoch),
        },
        key_material_digest: key.material_digest,
        claims,
    })
}

pub fn prepare_verification(
    kind: TokenKind,
    profile: TokenProfile,
    declared_key_epoch: Option<u64>,
    now: i64,
    keys: &KeyRing,
) -> Result<VerificationPlan, DomainError> {
    let role = KeyRole::from(kind);
    let key = keys.verify_key(role, profile, declared_key_epoch, now)?;
    Ok(VerificationPlan {
        algorithm: Algorithm::Hs256,
        kind,
        profile,
        selected_key_epoch: key.epoch,
        key_material_digest: key.material_digest,
    })
}

pub fn accept_verified_claims(
    plan: VerificationPlan,
    claims: TokenClaims,
    now: i64,
    policy: TokenPolicy,
) -> Result<VerifiedToken, DomainError> {
    policy.validate()?;
    validate_claims(
        &claims,
        plan.kind,
        plan.profile,
        now,
        policy,
        ValidationMode::Verify,
    )?;
    match plan.profile {
        TokenProfile::NakamaV340Legacy => {
            if claims.key_epoch.is_some() {
                return Err(unauthenticated("legacy_token_key_epoch_present"));
            }
        }
        TokenProfile::TrillionniumFamilyV1 => {
            if claims.key_epoch != Some(plan.selected_key_epoch) {
                return Err(unauthenticated("token_claim_key_epoch_mismatch"));
            }
        }
    }
    Ok(VerifiedToken {
        kind: plan.kind,
        profile: plan.profile,
        selected_key_epoch: plan.selected_key_epoch,
        claims,
    })
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ValidationMode {
    Issue,
    Verify,
}

fn validate_claims(
    claims: &TokenClaims,
    kind: TokenKind,
    profile: TokenProfile,
    now: i64,
    policy: TokenPolicy,
    mode: ValidationMode,
) -> Result<(), DomainError> {
    if claims.token_id.is_zero() || claims.user_id.is_zero() {
        return Err(invalid_or_unauthenticated(mode, "invalid_token_identity"));
    }
    if claims.username.is_empty() || claims.username.len() > policy.max_username_bytes {
        return Err(invalid_or_unauthenticated(mode, "invalid_token_username"));
    }
    if claims.vars.len() > policy.max_vars {
        return Err(invalid_or_unauthenticated(
            mode,
            "token_vars_limit_exceeded",
        ));
    }
    for (key, value) in &claims.vars {
        if key.is_empty()
            || key.len() > policy.max_var_key_bytes
            || value.len() > policy.max_var_value_bytes
        {
            return Err(invalid_or_unauthenticated(mode, "invalid_token_vars"));
        }
    }
    if claims.expires_at <= claims.issued_at {
        return Err(invalid_or_unauthenticated(mode, "invalid_token_lifetime"));
    }
    let lifetime = claims
        .expires_at
        .checked_sub(claims.issued_at)
        .ok_or_else(|| invalid_or_unauthenticated(mode, "invalid_token_lifetime"))?;
    if lifetime > policy.lifetime(kind) {
        return Err(invalid_or_unauthenticated(mode, "token_lifetime_exceeded"));
    }
    let latest_issued_at = now
        .checked_add(policy.clock_skew_sec)
        .ok_or_else(|| invalid_or_unauthenticated(mode, "token_time_overflow"))?;
    if claims.issued_at > latest_issued_at {
        return Err(invalid_or_unauthenticated(mode, "token_issued_in_future"));
    }
    if mode == ValidationMode::Issue {
        let earliest_issued_at = now
            .checked_sub(policy.clock_skew_sec)
            .ok_or_else(|| invalid("token_time_overflow"))?;
        if claims.issued_at < earliest_issued_at {
            return Err(invalid("token_issue_time_outside_window"));
        }
    } else {
        let latest_valid = claims
            .expires_at
            .checked_add(policy.clock_skew_sec)
            .ok_or_else(|| unauthenticated("token_time_overflow"))?;
        if now >= latest_valid {
            return Err(unauthenticated("token_expired"));
        }
    }

    match profile {
        TokenProfile::NakamaV340Legacy => {
            if claims.family_id.is_some()
                || claims.family_generation.is_some()
                || claims.key_epoch.is_some()
            {
                return Err(invalid_or_unauthenticated(
                    mode,
                    "legacy_token_extension_claim_present",
                ));
            }
        }
        TokenProfile::TrillionniumFamilyV1 => {
            let family = claims
                .family_id
                .ok_or_else(|| invalid_or_unauthenticated(mode, "token_family_claim_required"))?;
            if family.is_zero()
                || claims.family_generation.is_none()
                || claims.key_epoch.unwrap_or(0) == 0
            {
                return Err(invalid_or_unauthenticated(
                    mode,
                    "invalid_token_family_claims",
                ));
            }
        }
    }
    Ok(())
}

fn unique_key(
    mut keys: impl Iterator<Item = KeyDescriptor>,
    none_reason: &'static str,
    multiple_reason: &'static str,
) -> Result<KeyDescriptor, DomainError> {
    let first = keys.next().ok_or_else(|| unauthenticated(none_reason))?;
    if keys.next().is_some() {
        return Err(error(
            StableCode::FailedPrecondition,
            multiple_reason,
            RetryClass::Never,
        ));
    }
    Ok(first)
}

fn key_valid(key: KeyDescriptor, now: i64) -> bool {
    key.not_before <= now && now < key.not_after
}

fn invalid_or_unauthenticated(mode: ValidationMode, reason: &'static str) -> DomainError {
    match mode {
        ValidationMode::Issue => invalid(reason),
        ValidationMode::Verify => unauthenticated(reason),
    }
}

const fn invalid(reason: &'static str) -> DomainError {
    error(StableCode::InvalidArgument, reason, RetryClass::Never)
}

const fn unauthenticated(reason: &'static str) -> DomainError {
    error(StableCode::Unauthenticated, reason, RetryClass::Never)
}

const fn error(code: StableCode, reason: &'static str, retry: RetryClass) -> DomainError {
    DomainError::new(code, reason, retry)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn digest(value: u8) -> Digest32 {
        Digest32::new([value; 32])
    }

    fn user(value: u8) -> UserId {
        UserId::new([value; 16])
    }

    fn family(value: u8) -> SessionFamilyId {
        SessionFamilyId::new([value; 16])
    }

    fn key(role: KeyRole, epoch: u64, status: KeyStatus) -> KeyDescriptor {
        KeyDescriptor {
            role,
            epoch,
            status,
            not_before: 900,
            not_after: 10_000,
            material_digest: digest(epoch as u8),
        }
    }

    fn claims(profile: TokenProfile, epoch: u64) -> TokenClaims {
        TokenClaims {
            token_id: TokenId::new([1; 16]),
            user_id: user(2),
            username: "player".to_owned(),
            vars: BTreeMap::from([("region".to_owned(), "ca".to_owned())]),
            issued_at: 1_000,
            expires_at: 1_300,
            family_id: match profile {
                TokenProfile::NakamaV340Legacy => None,
                TokenProfile::TrillionniumFamilyV1 => Some(family(3)),
            },
            family_generation: match profile {
                TokenProfile::NakamaV340Legacy => None,
                TokenProfile::TrillionniumFamilyV1 => Some(SessionGeneration::new(4)),
            },
            key_epoch: match profile {
                TokenProfile::NakamaV340Legacy => None,
                TokenProfile::TrillionniumFamilyV1 => Some(epoch),
            },
        }
    }

    #[test]
    fn legacy_issue_uses_one_active_key_without_emitting_epoch() {
        let mut keys = KeyRing::default();
        keys.insert(key(KeyRole::Access, 1, KeyStatus::Active))
            .unwrap();
        let plan = prepare_issue(
            TokenKind::Access,
            TokenProfile::NakamaV340Legacy,
            claims(TokenProfile::NakamaV340Legacy, 1),
            1_000,
            TokenPolicy::default(),
            &keys,
        )
        .unwrap();
        assert_eq!(plan.selected_key_epoch, 1);
        assert_eq!(plan.emitted_key_epoch, None);
    }

    #[test]
    fn legacy_overlap_is_ambiguous_because_no_key_epoch_is_carried() {
        let mut keys = KeyRing::default();
        keys.insert(key(KeyRole::Access, 1, KeyStatus::VerifyOnly))
            .unwrap();
        keys.insert(key(KeyRole::Access, 2, KeyStatus::Active))
            .unwrap();
        assert_eq!(
            prepare_verification(
                TokenKind::Access,
                TokenProfile::NakamaV340Legacy,
                None,
                1_000,
                &keys,
            )
            .unwrap_err()
            .reason(),
            "ambiguous_legacy_key_epoch"
        );
    }

    #[test]
    fn family_profile_binds_explicit_epoch_and_family_generation() {
        let mut keys = KeyRing::default();
        keys.insert(key(KeyRole::Refresh, 7, KeyStatus::Active))
            .unwrap();
        let mut value = claims(TokenProfile::TrillionniumFamilyV1, 7);
        value.expires_at = 2_000;
        let plan = prepare_issue(
            TokenKind::Refresh,
            TokenProfile::TrillionniumFamilyV1,
            value,
            1_000,
            TokenPolicy::default(),
            &keys,
        )
        .unwrap();
        assert_eq!(plan.emitted_key_epoch, Some(7));
    }

    #[test]
    fn verify_only_key_can_verify_but_cannot_issue() {
        let mut keys = KeyRing::default();
        keys.insert(key(KeyRole::Access, 3, KeyStatus::VerifyOnly))
            .unwrap();
        let verification = prepare_verification(
            TokenKind::Access,
            TokenProfile::TrillionniumFamilyV1,
            Some(3),
            1_000,
            &keys,
        )
        .unwrap();
        assert_eq!(verification.selected_key_epoch, 3);
        assert_eq!(
            prepare_issue(
                TokenKind::Access,
                TokenProfile::TrillionniumFamilyV1,
                claims(TokenProfile::TrillionniumFamilyV1, 3),
                1_000,
                TokenPolicy::default(),
                &keys,
            )
            .unwrap_err()
            .reason(),
            "token_issue_key_unavailable"
        );
    }

    #[test]
    fn retired_and_out_of_window_keys_are_rejected() {
        let mut retired = KeyRing::default();
        retired
            .insert(key(KeyRole::Access, 1, KeyStatus::Retired))
            .unwrap();
        assert_eq!(
            prepare_verification(
                TokenKind::Access,
                TokenProfile::TrillionniumFamilyV1,
                Some(1),
                1_000,
                &retired,
            )
            .unwrap_err()
            .reason(),
            "token_key_unavailable"
        );
        let mut future = KeyRing::default();
        let mut value = key(KeyRole::Access, 2, KeyStatus::Active);
        value.not_before = 2_000;
        future.insert(value).unwrap();
        assert!(prepare_verification(
            TokenKind::Access,
            TokenProfile::TrillionniumFamilyV1,
            Some(2),
            1_000,
            &future,
        )
        .is_err());
    }

    #[test]
    fn verified_claims_reject_expired_or_future_tokens() {
        let mut keys = KeyRing::default();
        keys.insert(key(KeyRole::Access, 2, KeyStatus::Active))
            .unwrap();
        let plan = prepare_verification(
            TokenKind::Access,
            TokenProfile::TrillionniumFamilyV1,
            Some(2),
            1_000,
            &keys,
        )
        .unwrap();
        let mut expired = claims(TokenProfile::TrillionniumFamilyV1, 2);
        expired.issued_at = 500;
        expired.expires_at = 600;
        assert_eq!(
            accept_verified_claims(plan, expired, 1_000, TokenPolicy::default())
                .unwrap_err()
                .reason(),
            "token_expired"
        );

        let mut future = claims(TokenProfile::TrillionniumFamilyV1, 2);
        future.issued_at = 1_100;
        future.expires_at = 1_200;
        assert_eq!(
            accept_verified_claims(plan, future, 1_000, TokenPolicy::default())
                .unwrap_err()
                .reason(),
            "token_issued_in_future"
        );
    }

    #[test]
    fn lifetime_and_variable_limits_fail_closed() {
        let mut keys = KeyRing::default();
        keys.insert(key(KeyRole::Access, 2, KeyStatus::Active))
            .unwrap();
        let policy = TokenPolicy {
            access_lifetime_max_sec: 10,
            max_vars: 1,
            ..TokenPolicy::default()
        };
        let mut value = claims(TokenProfile::TrillionniumFamilyV1, 2);
        value.expires_at = 1_020;
        assert_eq!(
            prepare_issue(
                TokenKind::Access,
                TokenProfile::TrillionniumFamilyV1,
                value,
                1_000,
                policy,
                &keys,
            )
            .unwrap_err()
            .reason(),
            "token_lifetime_exceeded"
        );
    }

    #[test]
    fn family_claims_and_epoch_must_match() {
        let mut keys = KeyRing::default();
        keys.insert(key(KeyRole::Access, 2, KeyStatus::Active))
            .unwrap();
        let mut missing = claims(TokenProfile::TrillionniumFamilyV1, 2);
        missing.family_id = None;
        assert_eq!(
            prepare_issue(
                TokenKind::Access,
                TokenProfile::TrillionniumFamilyV1,
                missing,
                1_000,
                TokenPolicy::default(),
                &keys,
            )
            .unwrap_err()
            .reason(),
            "token_family_claim_required"
        );
        assert_eq!(
            prepare_issue(
                TokenKind::Access,
                TokenProfile::TrillionniumFamilyV1,
                claims(TokenProfile::TrillionniumFamilyV1, 9),
                1_000,
                TokenPolicy::default(),
                &keys,
            )
            .unwrap_err()
            .reason(),
            "token_claim_key_epoch_mismatch"
        );
    }

    #[test]
    fn key_ring_rejects_duplicate_epoch_and_zero_digest() {
        let mut keys = KeyRing::default();
        keys.insert(key(KeyRole::Access, 1, KeyStatus::Active))
            .unwrap();
        assert_eq!(
            keys.insert(key(KeyRole::Access, 1, KeyStatus::VerifyOnly))
                .unwrap_err()
                .reason(),
            "token_key_epoch_exists"
        );
        let mut zero = key(KeyRole::Refresh, 2, KeyStatus::Active);
        zero.material_digest = Digest32::new([0; 32]);
        assert_eq!(keys.insert(zero).unwrap_err().reason(), "invalid_token_key");
    }

    #[test]
    fn legacy_profile_rejects_extension_claims() {
        let mut keys = KeyRing::default();
        keys.insert(key(KeyRole::Access, 1, KeyStatus::Active))
            .unwrap();
        let mut value = claims(TokenProfile::NakamaV340Legacy, 1);
        value.family_id = Some(family(4));
        assert_eq!(
            prepare_issue(
                TokenKind::Access,
                TokenProfile::NakamaV340Legacy,
                value,
                1_000,
                TokenPolicy::default(),
                &keys,
            )
            .unwrap_err()
            .reason(),
            "legacy_token_extension_claim_present"
        );
    }
}
