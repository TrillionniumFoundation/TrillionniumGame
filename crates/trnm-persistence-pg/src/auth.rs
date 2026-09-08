use std::collections::{BTreeMap, BTreeSet};
use std::fmt;

use openssl::hash::{hash, MessageDigest};
use openssl::memcmp;
use openssl::pkey::PKey;
use openssl::sign::Signer;
use trnm_contracts::{
    Digest32, DomainError, RefreshTokenId, RetryClass, SessionFamilyId, StableCode, UserId,
};
use trnm_token_jwt_adapter::base64url;
use trnm_token_jwt_adapter::json::{self, JsonLimits, JsonValue};
use trnm_token_jwt_adapter::EPOCH_KEY_ID_PREFIX;

const MAX_TOKEN_BYTES: usize = 32 * 1_024;
const MAX_HEADER_BYTES: usize = 1_024;
const MAX_PAYLOAD_BYTES: usize = 16 * 1_024;
const MINIMUM_KEY_BYTES: usize = 16;
const MAXIMUM_KEY_BYTES: usize = 4_096;
const SIGNATURE_BYTES: usize = 32;
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

pub struct AccessTokenVerifier {
    issuer: String,
    audience: String,
    epoch: u32,
    key: Vec<u8>,
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
            || !(MINIMUM_KEY_BYTES..=MAXIMUM_KEY_BYTES).contains(&key.len())
        {
            return Err(configuration_error("access_token_profile_invalid"));
        }
        Ok(Self {
            issuer,
            audience,
            epoch,
            key,
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
        self.verify_compact_token(token, now_unix_seconds)
    }

    fn verify_compact_token(
        &self,
        token: &str,
        now_unix_seconds: i64,
    ) -> Result<SessionPrincipal, DomainError> {
        if token.len() > MAX_TOKEN_BYTES {
            return Err(unauthenticated());
        }
        let (header_segment, payload_segment, signature_segment) = compact_segments(token)?;
        self.validate_header(header_segment)?;
        self.verify_signature(header_segment, payload_segment, signature_segment)?;

        let payload_bytes =
            base64url::decode(payload_segment, MAX_PAYLOAD_BYTES).map_err(|_| unauthenticated())?;
        let claims =
            json::parse(&payload_bytes, JsonLimits::default()).map_err(|_| unauthenticated())?;
        let claims = claims.as_object().ok_or_else(unauthenticated)?;
        self.validate_claims(claims, now_unix_seconds)
    }

    fn validate_header(&self, encoded: &str) -> Result<(), DomainError> {
        let header_bytes =
            base64url::decode(encoded, MAX_HEADER_BYTES).map_err(|_| unauthenticated())?;
        let header =
            json::parse(&header_bytes, JsonLimits::default()).map_err(|_| unauthenticated())?;
        let header = header.as_object().ok_or_else(unauthenticated)?;
        if header
            .keys()
            .any(|name| !matches!(name.as_str(), "alg" | "typ" | "kid"))
        {
            return Err(unauthenticated());
        }
        if claim_string(header, "alg")? != "HS256" {
            return Err(unauthenticated());
        }
        if let Some(value) = header.get("typ") {
            if value.as_str() != Some("JWT") {
                return Err(unauthenticated());
            }
        }
        let expected_key_id = format!("{EPOCH_KEY_ID_PREFIX}{}", self.epoch);
        if header.get("kid").and_then(JsonValue::as_str) != Some(expected_key_id.as_str()) {
            return Err(unauthenticated());
        }
        Ok(())
    }

    fn verify_signature(
        &self,
        header_segment: &str,
        payload_segment: &str,
        signature_segment: &str,
    ) -> Result<(), DomainError> {
        let signature =
            base64url::decode(signature_segment, SIGNATURE_BYTES).map_err(|_| unauthenticated())?;
        if signature.len() != SIGNATURE_BYTES {
            return Err(unauthenticated());
        }

        let key = PKey::hmac(&self.key).map_err(|_| unauthenticated())?;
        let mut signer =
            Signer::new(MessageDigest::sha256(), &key).map_err(|_| unauthenticated())?;
        signer
            .update(header_segment.as_bytes())
            .map_err(|_| unauthenticated())?;
        signer.update(b".").map_err(|_| unauthenticated())?;
        signer
            .update(payload_segment.as_bytes())
            .map_err(|_| unauthenticated())?;
        let expected = signer.sign_to_vec().map_err(|_| unauthenticated())?;
        if expected.len() != SIGNATURE_BYTES || !memcmp::eq(&signature, &expected) {
            return Err(unauthenticated());
        }
        Ok(())
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
            .field("key_material", &"<redacted>")
            .finish()
    }
}

impl Drop for AccessTokenVerifier {
    fn drop(&mut self) {
        self.key.fill(0);
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
    let digest = hash(MessageDigest::sha256(), value.as_bytes()).map_err(|_| unauthenticated())?;
    let digest: [u8; 32] = digest.as_ref().try_into().map_err(|_| unauthenticated())?;
    Ok(ParsedRefreshCredential {
        id: RefreshTokenId::new(parse_lower_hex::<16>(id)?),
        digest: Digest32::new(digest),
    })
}

fn compact_segments(token: &str) -> Result<(&str, &str, &str), DomainError> {
    let mut parts = token.split('.');
    let header = parts.next().ok_or_else(unauthenticated)?;
    let payload = parts.next().ok_or_else(unauthenticated)?;
    let signature = parts.next().ok_or_else(unauthenticated)?;
    if parts.next().is_some() || header.is_empty() || payload.is_empty() || signature.is_empty() {
        return Err(unauthenticated());
    }
    Ok((header, payload, signature))
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
            max_lifetime_seconds: Some(MAX_ACCESS_TOKEN_LIFETIME_SECONDS),
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
    fn openssl_hmac_verifier_yields_session_principal() {
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
            base64url::encode(&[0_u8; SIGNATURE_BYTES])
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
        let mut wrong_epoch = claims();
        if let JsonValue::Object(object) = &mut wrong_epoch {
            object.insert("trnm_kep".to_owned(), JsonValue::Unsigned(EPOCH.into()));
        }
        let token = issue(&wrong_epoch);
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
        let expected = hash(MessageDigest::sha256(), value.as_bytes()).unwrap();
        assert_eq!(parsed.digest.as_bytes(), expected.as_ref());

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
        let verifier = verifier();
        let debug = format!("{verifier:?}");
        assert!(debug.contains("<redacted>"));
        assert!(!debug.contains("0123456789abcdef"));
    }
}
