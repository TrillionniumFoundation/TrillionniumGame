use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::sync::Arc;

use openssl::hash::{hash, MessageDigest};
use trnm_contracts::{
    Digest32, DomainError, RefreshTokenId, RetryClass, SessionFamilyId, StableCode, UserId,
};
use trnm_token_crypto_provider::{
    Hs256Provider, KeyDomain, KeyHandle, KeyReference, SoftwareHs256Provider,
};
use trnm_token_jwt_adapter::json::{JsonLimits, JsonValue};
use trnm_token_jwt_provider_adapter::{
    authenticate, AuthenticationError, AuthenticationProfile, KeyResolver, TokenRoute,
};

const MAX_TOKEN_BYTES: usize = 32 * 1_024;
const MAX_HEADER_BYTES: usize = 1_024;
const MAX_PAYLOAD_BYTES: usize = 16 * 1_024;
const MINIMUM_KEY_BYTES: usize = 32;
const MAXIMUM_KEY_BYTES: usize = 4_096;
const CLOCK_SKEW_SECONDS: i64 = 30;
const MAX_ACCESS_TOKEN_LIFETIME_SECONDS: u64 = 15 * 60;

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

#[derive(Clone, Debug)]
struct FixedAccessKeyResolver {
    epoch: u32,
    key: KeyReference,
}

impl KeyResolver for FixedAccessKeyResolver {
    fn resolve(
        &self,
        domain: KeyDomain,
        route: TokenRoute,
    ) -> Result<KeyReference, AuthenticationError> {
        if domain == KeyDomain::AccessToken && route == TokenRoute::Epoch(self.epoch) {
            Ok(self.key.clone())
        } else {
            Err(AuthenticationError::UnknownKey)
        }
    }
}

pub struct AccessTokenVerifier {
    issuer: String,
    audience: String,
    epoch: u32,
    resolver: FixedAccessKeyResolver,
    provider: Arc<dyn Hs256Provider>,
}

impl AccessTokenVerifier {
    pub fn from_epoch_key(
        issuer: String,
        audience: String,
        epoch: u32,
        key: Vec<u8>,
    ) -> Result<Self, DomainError> {
        if !(MINIMUM_KEY_BYTES..=MAXIMUM_KEY_BYTES).contains(&key.len()) {
            return Err(configuration_error("access_token_profile_invalid"));
        }
        let reference = KeyReference::new(
            KeyDomain::AccessToken,
            KeyHandle::new(format!("software://access-token/{epoch}"))
                .map_err(|_| configuration_error("access_token_profile_invalid"))?,
            Some(epoch),
        )
        .map_err(|_| configuration_error("access_token_profile_invalid"))?;
        let provider = Arc::new(SoftwareHs256Provider::new());
        provider
            .insert_key(&reference, key)
            .map_err(|_| configuration_error("access_token_profile_invalid"))?;
        Self::from_provider(issuer, audience, epoch, reference, provider)
    }

    pub fn from_provider(
        issuer: String,
        audience: String,
        epoch: u32,
        key: KeyReference,
        provider: Arc<dyn Hs256Provider>,
    ) -> Result<Self, DomainError> {
        if issuer.is_empty()
            || audience.is_empty()
            || issuer.len() > 512
            || audience.len() > 512
            || epoch == 0
            || key.domain != KeyDomain::AccessToken
            || key.epoch != Some(epoch)
        {
            return Err(configuration_error("access_token_profile_invalid"));
        }
        Ok(Self {
            issuer,
            audience,
            epoch,
            resolver: FixedAccessKeyResolver { epoch, key },
            provider,
        })
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
        let profile = AuthenticationProfile {
            domain: KeyDomain::AccessToken,
            max_token_bytes: MAX_TOKEN_BYTES,
            max_header_bytes: MAX_HEADER_BYTES,
            max_payload_bytes: MAX_PAYLOAD_BYTES,
            allow_legacy_without_key_id: false,
            reject_unknown_header_fields: true,
            json_limits: JsonLimits::default(),
        };
        let authenticated = authenticate(token, &profile, &self.resolver, self.provider.as_ref())
            .map_err(|_| unauthenticated())?;
        if authenticated.route != TokenRoute::Epoch(self.epoch)
            || authenticated.key != self.resolver.key
        {
            return Err(unauthenticated());
        }
        let claims = authenticated
            .parse_claims(JsonLimits::default())
            .map_err(|_| unauthenticated())?;
        let claims = claims.as_object().ok_or_else(unauthenticated)?;
        self.validate_claims(claims, now_unix_seconds)
    }

    fn validate_claims(
        &self,
        claims: &BTreeMap<String, JsonValue>,
        now_unix_seconds: i64,
    ) -> Result<SessionPrincipal, DomainError> {
        if claim_string(claims, "iss")? != self.issuer
            || !audience_contains(claims.get("aud"), &self.audience)?
            || claim_unsigned(claims, "trnm_kep")? != u64::from(self.epoch)
        {
            return Err(unauthenticated());
        }

        let issued_at = claim_numeric_date(claims, "iat")?;
        let expires_at_unix_seconds = claim_numeric_date(claims, "exp")?;
        let not_before = optional_numeric_date(claims, "nbf")?;
        let now = i128::from(now_unix_seconds);
        let skew = i128::from(CLOCK_SKEW_SECONDS);
        if now >= i128::from(expires_at_unix_seconds) + skew
            || now + skew < i128::from(issued_at)
            || not_before.is_some_and(|value| now + skew < i128::from(value))
            || expires_at_unix_seconds <= issued_at
        {
            return Err(unauthenticated());
        }
        let lifetime =
            u64::try_from(expires_at_unix_seconds - issued_at).map_err(|_| unauthenticated())?;
        if lifetime > MAX_ACCESS_TOKEN_LIFETIME_SECONDS {
            return Err(unauthenticated());
        }

        let user = UserId::new(parse_lower_hex::<16>(claim_string(claims, "sub")?)?);
        let access_token_id = parse_lower_hex::<16>(claim_string(claims, "jti")?)?;
        let family = SessionFamilyId::new(parse_lower_hex::<16>(claim_string(claims, "sid")?)?);
        let generation = claim_unsigned(claims, "sgn")?;
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
            .field("issuer", &self.issuer)
            .field("audience", &self.audience)
            .field("active_epoch", &self.epoch)
            .field("key_reference", &self.resolver.key)
            .field("provider", &"<opaque-provider>")
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
    let digest = sha256_digest(value.as_bytes())?;
    Ok(ParsedRefreshCredential {
        id: RefreshTokenId::new(parse_lower_hex::<16>(id)?),
        digest: Digest32::new(digest),
    })
}

fn sha256_digest(input: &[u8]) -> Result<[u8; 32], DomainError> {
    let digest = hash(MessageDigest::sha256(), input).map_err(|_| unauthenticated())?;
    digest.as_ref().try_into().map_err(|_| unauthenticated())
}

fn claim_string<'a>(
    claims: &'a BTreeMap<String, JsonValue>,
    name: &str,
) -> Result<&'a str, DomainError> {
    match claims.get(name) {
        Some(JsonValue::String(value)) if !value.is_empty() => Ok(value),
        _ => Err(unauthenticated()),
    }
}

fn claim_unsigned(claims: &BTreeMap<String, JsonValue>, name: &str) -> Result<u64, DomainError> {
    claims
        .get(name)
        .and_then(JsonValue::as_u64)
        .ok_or_else(unauthenticated)
}

fn claim_numeric_date(
    claims: &BTreeMap<String, JsonValue>,
    name: &str,
) -> Result<i64, DomainError> {
    claims
        .get(name)
        .and_then(JsonValue::as_i64)
        .filter(|value| *value >= 0)
        .ok_or_else(unauthenticated)
}

fn optional_numeric_date(
    claims: &BTreeMap<String, JsonValue>,
    name: &str,
) -> Result<Option<i64>, DomainError> {
    match claims.get(name) {
        None => Ok(None),
        Some(value) => value
            .as_i64()
            .filter(|value| *value >= 0)
            .map(Some)
            .ok_or_else(unauthenticated),
    }
}

fn audience_contains(value: Option<&JsonValue>, required: &str) -> Result<bool, DomainError> {
    match value {
        Some(JsonValue::String(value)) => Ok(!value.is_empty() && value == required),
        Some(JsonValue::Array(values)) => {
            let mut seen = BTreeSet::new();
            let mut matched = false;
            for value in values {
                let value = value
                    .as_str()
                    .filter(|value| !value.is_empty())
                    .ok_or_else(unauthenticated)?;
                if !seen.insert(value) {
                    return Err(unauthenticated());
                }
                matched |= value == required;
            }
            Ok(matched)
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

fn configuration_error(reason: &'static str) -> DomainError {
    DomainError::new(StableCode::InvalidArgument, reason, RetryClass::Never)
}

fn unauthenticated() -> DomainError {
    DomainError::new(
        StableCode::Unauthenticated,
        "session_authentication_failed",
        RetryClass::Never,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use trnm_token_jwt_adapter::{KeyRing, SecretKey, VerificationProfile};

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
            ("sgn".to_owned(), JsonValue::Unsigned(4)),
            ("trnm_kep".to_owned(), JsonValue::Unsigned(u64::from(EPOCH))),
            ("iat".to_owned(), JsonValue::Integer(1_000)),
            ("exp".to_owned(), JsonValue::Integer(1_600)),
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

    fn verifier() -> AccessTokenVerifier {
        AccessTokenVerifier::from_epoch_key(
            ISSUER.to_owned(),
            AUDIENCE.to_owned(),
            EPOCH,
            KEY.to_vec(),
        )
        .unwrap()
    }

    #[test]
    fn hs256_key_length_requires_32_actual_bytes() {
        for length in [0_usize, 15, 16, 31] {
            let error = AccessTokenVerifier::from_epoch_key(
                ISSUER.to_owned(),
                AUDIENCE.to_owned(),
                EPOCH,
                vec![0x5a; length],
            )
            .unwrap_err();
            assert_eq!(
                error.reason(),
                "access_token_profile_invalid",
                "length={length}"
            );
        }
        for length in [32_usize, 48, 64] {
            AccessTokenVerifier::from_epoch_key(
                ISSUER.to_owned(),
                AUDIENCE.to_owned(),
                EPOCH,
                vec![0x5a; length],
            )
            .unwrap();
        }
    }

    #[test]
    fn strict_epoch_access_token_yields_session_principal() {
        let token = issue(&claims());
        let principal = verifier()
            .verify_bearer(Some(&format!("Bearer {token}")), 1_100)
            .unwrap();
        assert_eq!(principal.user, UserId::new([0x11; 16]));
        assert_eq!(principal.family, SessionFamilyId::new([0x33; 16]));
        assert_eq!(principal.generation, 4);
        assert_eq!(principal.access_token_id, [0x22; 16]);
        assert_eq!(principal.expires_at_unix_seconds, 1_600);
    }

    #[test]
    fn verifier_debug_redacts_key_material() {
        let verifier = verifier();
        let debug = format!("{verifier:?}");
        assert!(debug.contains("<opaque-provider>"));
        assert!(debug.contains("<redacted-key-handle>"));
        assert!(!debug.contains("0123456789abcdef"));
    }

    #[test]
    fn malformed_tampered_and_incomplete_access_tokens_fail_closed() {
        let verifier = verifier();
        for authorization in [None, Some("bearer token"), Some("Bearer malformed")] {
            assert_eq!(
                verifier
                    .verify_bearer(authorization, 1_100)
                    .unwrap_err()
                    .code(),
                StableCode::Unauthenticated
            );
        }

        let valid = issue(&claims());
        let (signing_input, _) = valid.rsplit_once('.').unwrap();
        let tampered = format!(
            "{signing_input}.{}",
            trnm_token_jwt_adapter::base64url::encode(&[0_u8; 32])
        );
        assert_eq!(
            verifier
                .verify_bearer(Some(&format!("Bearer {tampered}")), 1_100)
                .unwrap_err()
                .code(),
            StableCode::Unauthenticated
        );

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
    fn wrong_epoch_and_invalid_lifetime_never_fall_back() {
        let token = issue(&claims());
        let wrong_verifier = AccessTokenVerifier::from_epoch_key(
            ISSUER.to_owned(),
            AUDIENCE.to_owned(),
            EPOCH + 1,
            KEY.to_vec(),
        )
        .unwrap();
        assert_eq!(
            wrong_verifier
                .verify_bearer(Some(&format!("Bearer {token}")), 1_100)
                .unwrap_err()
                .code(),
            StableCode::Unauthenticated
        );

        let mut excessive = claims();
        if let JsonValue::Object(object) = &mut excessive {
            object.insert("exp".to_owned(), JsonValue::Integer(2_000));
        }
        let token = issue(&excessive);
        assert_eq!(
            verifier()
                .verify_bearer(Some(&format!("Bearer {token}")), 1_100)
                .unwrap_err()
                .code(),
            StableCode::Unauthenticated
        );
    }

    #[test]
    fn refresh_credential_is_bounded_id_prefixed_and_hashed() {
        let value = format!("{}.{}", "44".repeat(16), "s".repeat(48));
        let parsed = parse_refresh_credential(&value).unwrap();
        assert_eq!(parsed.id, RefreshTokenId::new([0x44; 16]));
        assert_eq!(
            parsed.digest.as_bytes(),
            &sha256_digest(value.as_bytes()).unwrap()
        );

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
}
