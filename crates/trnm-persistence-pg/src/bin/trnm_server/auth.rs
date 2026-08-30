use std::collections::BTreeMap;
use std::fmt;

use trnm_contracts::{Digest32, DomainError, RetryClass, StableCode, UserId};
use trnm_session_core::{RefreshTokenId, SessionFamilyId};
use trnm_token_jwt_adapter::{
    sha256_digest, JsonNumber, JsonValue, KeyRing, SecretKey, TokenRoute,
    VerificationProfile,
};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SessionPrincipal {
    pub user: UserId,
    pub family: SessionFamilyId,
    pub generation: u64,
    pub access_token_id: [u8; 16],
    pub expires_at_unix_seconds: i64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ParsedRefreshCredential {
    pub id: RefreshTokenId,
    pub digest: Digest32,
}

pub struct AccessTokenVerifier {
    key_ring: KeyRing,
    profile: VerificationProfile,
}

impl AccessTokenVerifier {
    pub fn from_epoch_key(
        issuer: String,
        audience: String,
        epoch: u32,
        key: Vec<u8>,
    ) -> Result<Self, DomainError> {
        if issuer.is_empty()
            || audience.is_empty()
            || issuer.len() > 512
            || audience.len() > 512
            || epoch == 0
        {
            return Err(configuration_error("access_token_profile_invalid"));
        }
        let mut key_ring = KeyRing::new();
        key_ring
            .insert_epoch_key(epoch, SecretKey::new(key).map_err(map_configuration_error)?)
            .map_err(map_configuration_error)?;
        key_ring
            .set_active_epoch(epoch)
            .map_err(map_configuration_error)?;
        let profile = VerificationProfile {
            required_issuer: Some(issuer),
            required_audience: Some(audience),
            allow_legacy_without_key_id: false,
            require_epoch_claim: true,
            max_lifetime_seconds: Some(15 * 60),
            max_subject_bytes: 32,
            max_token_id_bytes: 32,
            ..VerificationProfile::default()
        };
        profile.validate().map_err(map_configuration_error)?;
        Ok(Self { key_ring, profile })
    }

    pub fn verify_bearer(
        &self,
        authorization: Option<&str>,
        now_unix_seconds: i64,
    ) -> Result<SessionPrincipal, DomainError> {
        let token = authorization
            .and_then(|value| value.strip_prefix("Bearer "))
            .filter(|value| {
                !value.is_empty()
                    && !value
                        .bytes()
                        .any(|byte| byte.is_ascii_whitespace() || byte.is_ascii_control())
            })
            .ok_or_else(unauthenticated)?;
        let verified = self
            .key_ring
            .verify(token, &self.profile, now_unix_seconds)
            .map_err(|_| unauthenticated())?;
        if !matches!(verified.route, TokenRoute::Epoch(_)) {
            return Err(unauthenticated());
        }
        let user = UserId::new(parse_lower_hex::<16>(&verified.principal.subject)?);
        let access_token_id = parse_lower_hex::<16>(
            verified
                .principal
                .token_id
                .as_deref()
                .ok_or_else(unauthenticated)?,
        )?;
        let claims = match &verified.claims {
            JsonValue::Object(value) => value,
            _ => return Err(unauthenticated()),
        };
        let family = SessionFamilyId::new(parse_lower_hex::<16>(claim_string(claims, "sid")?)?);
        let generation = claim_unsigned(claims, "sgn")?;
        let expires_at_unix_seconds = verified.expires_at.ok_or_else(unauthenticated)?;
        if user.is_zero() || family.is_zero() || access_token_id.iter().all(|byte| *byte == 0) {
            return Err(unauthenticated());
        }
        Ok(SessionPrincipal {
            user,
            family,
            generation,
            access_token_id,
            expires_at_unix_seconds,
        })
    }
}

impl fmt::Debug for AccessTokenVerifier {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AccessTokenVerifier")
            .field("active_epoch", &self.key_ring.active_epoch())
            .field("key_material", &"<redacted>")
            .field("profile", &self.profile)
            .finish()
    }
}

pub fn parse_refresh_credential(value: &str) -> Result<ParsedRefreshCredential, DomainError> {
    if value.len() > 600
        || value
            .bytes()
            .any(|byte| byte.is_ascii_whitespace() || byte.is_ascii_control())
    {
        return Err(unauthenticated());
    }
    let mut parts = value.split('.');
    let id = parts.next().ok_or_else(unauthenticated)?;
    let secret = parts.next().ok_or_else(unauthenticated)?;
    if parts.next().is_some()
        || secret.len() < 32
        || secret.len() > 512
        || !secret
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-' | b'~'))
    {
        return Err(unauthenticated());
    }
    Ok(ParsedRefreshCredential {
        id: RefreshTokenId::new(parse_lower_hex::<16>(id)?),
        digest: Digest32::new(sha256_digest(value.as_bytes())),
    })
}

fn claim_string<'a>(
    claims: &'a BTreeMap<String, JsonValue>,
    name: &str,
) -> Result<&'a str, DomainError> {
    match claims.get(name) {
        Some(JsonValue::String(value)) => Ok(value),
        _ => Err(unauthenticated()),
    }
}

fn claim_unsigned(
    claims: &BTreeMap<String, JsonValue>,
    name: &str,
) -> Result<u64, DomainError> {
    match claims.get(name) {
        Some(JsonValue::Number(JsonNumber::Unsigned(value))) => Ok(*value),
        Some(JsonValue::Number(JsonNumber::Integer(value))) => {
            u64::try_from(*value).map_err(|_| unauthenticated())
        }
        _ => Err(unauthenticated()),
    }
}

fn parse_lower_hex<const N: usize>(value: &str) -> Result<[u8; N], DomainError> {
    if value.len() != N * 2
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
    {
        return Err(unauthenticated());
    }
    let mut output = [0_u8; N];
    for (index, pair) in value.as_bytes().chunks_exact(2).enumerate() {
        output[index] = (hex_nibble(pair[0])? << 4) | hex_nibble(pair[1])?;
    }
    Ok(output)
}

fn hex_nibble(value: u8) -> Result<u8, DomainError> {
    match value {
        b'0'..=b'9' => Ok(value - b'0'),
        b'a'..=b'f' => Ok(value - b'a' + 10),
        _ => Err(unauthenticated()),
    }
}

fn map_configuration_error(_error: impl fmt::Debug) -> DomainError {
    configuration_error("access_token_profile_invalid")
}

const fn configuration_error(reason: &'static str) -> DomainError {
    DomainError::new(StableCode::InvalidArgument, reason, RetryClass::Never)
}

const fn unauthenticated() -> DomainError {
    DomainError::new(
        StableCode::Unauthenticated,
        "session_authentication_failed",
        RetryClass::Never,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    const KEY: &[u8] = b"0123456789abcdef0123456789abcdef";
    const ISSUER: &str = "https://identity.test";
    const AUDIENCE: &str = "trillionnium-game";
    const EPOCH: u32 = 7;

    fn claims() -> JsonValue {
        JsonValue::Object(BTreeMap::from([
            ("iss".to_owned(), JsonValue::String(ISSUER.to_owned())),
            ("aud".to_owned(), JsonValue::String(AUDIENCE.to_owned())),
            ("sub".to_owned(), JsonValue::String("11".repeat(16))),
            ("jti".to_owned(), JsonValue::String("22".repeat(16))),
            ("sid".to_owned(), JsonValue::String("33".repeat(16))),
            (
                "sgn".to_owned(),
                JsonValue::Number(JsonNumber::Unsigned(4)),
            ),
            (
                "trnm_kep".to_owned(),
                JsonValue::Number(JsonNumber::Unsigned(u64::from(EPOCH))),
            ),
            (
                "iat".to_owned(),
                JsonValue::Number(JsonNumber::Integer(1_000)),
            ),
            (
                "exp".to_owned(),
                JsonValue::Number(JsonNumber::Integer(1_600)),
            ),
        ]))
    }

    fn issue(claims: &JsonValue) -> String {
        let mut key_ring = KeyRing::new();
        key_ring
            .insert_epoch_key(EPOCH, SecretKey::new(KEY.to_vec()).unwrap())
            .unwrap();
        key_ring.set_active_epoch(EPOCH).unwrap();
        let profile = VerificationProfile {
            required_issuer: Some(ISSUER.to_owned()),
            required_audience: Some(AUDIENCE.to_owned()),
            allow_legacy_without_key_id: false,
            max_lifetime_seconds: Some(15 * 60),
            ..VerificationProfile::default()
        };
        key_ring.issue_active_epoch(claims, &profile).unwrap()
    }

    #[test]
    fn strict_epoch_access_token_yields_session_principal() {
        let verifier = AccessTokenVerifier::from_epoch_key(
            ISSUER.to_owned(),
            AUDIENCE.to_owned(),
            EPOCH,
            KEY.to_vec(),
        )
        .unwrap();
        let token = issue(&claims());
        let principal = verifier
            .verify_bearer(Some(&format!("Bearer {token}")), 1_100)
            .unwrap();
        assert_eq!(principal.user, UserId::new([0x11; 16]));
        assert_eq!(principal.family, SessionFamilyId::new([0x33; 16]));
        assert_eq!(principal.generation, 4);
        assert_eq!(principal.access_token_id, [0x22; 16]);
        assert_eq!(principal.expires_at_unix_seconds, 1_600);
    }

    #[test]
    fn malformed_tampered_and_incomplete_access_tokens_fail_closed() {
        let verifier = AccessTokenVerifier::from_epoch_key(
            ISSUER.to_owned(),
            AUDIENCE.to_owned(),
            EPOCH,
            KEY.to_vec(),
        )
        .unwrap();
        for authorization in [None, Some("bearer token"), Some("Bearer malformed")]
        {
            assert_eq!(
                verifier
                    .verify_bearer(authorization, 1_100)
                    .unwrap_err()
                    .code(),
                StableCode::Unauthenticated
            );
        }

        let mut incomplete = claims();
        if let JsonValue::Object(object) = &mut incomplete {
            object.remove("sid");
        }
        let token = issue(&incomplete);
        assert_eq!(
            verifier
                .verify_bearer(Some(&format!("Bearer {token}")), 1_100)
                .unwrap_err()
                .reason(),
            "session_authentication_failed"
        );
    }

    #[test]
    fn refresh_credential_is_bounded_id_prefixed_and_hashed() {
        let value = format!("{}.{}", "44".repeat(16), "s".repeat(48));
        let parsed = parse_refresh_credential(&value).unwrap();
        assert_eq!(parsed.id, RefreshTokenId::new([0x44; 16]));
        assert_eq!(parsed.digest, Digest32::new(sha256_digest(value.as_bytes())));

        for invalid in [
            "",
            "44.secret",
            "44444444444444444444444444444444.short",
            "44444444444444444444444444444444.secret.with.dot",
        ] {
            assert_eq!(
                parse_refresh_credential(invalid).unwrap_err().code(),
                StableCode::Unauthenticated
            );
        }
    }

    #[test]
    fn verifier_debug_redacts_key_material() {
        let verifier = AccessTokenVerifier::from_epoch_key(
            ISSUER.to_owned(),
            AUDIENCE.to_owned(),
            EPOCH,
            KEY.to_vec(),
        )
        .unwrap();
        let debug = format!("{verifier:?}");
        assert!(debug.contains("<redacted>"));
        assert!(!debug.contains("0123456789abcdef"));
    }
}
