#![forbid(unsafe_code)]
#![deny(missing_debug_implementations)]

//! Provider-backed strict HS256 JWT authentication boundary.
//!
//! Only the bounded JOSE header required for algorithm and key routing is
//! parsed before authentication. The payload is decoded only after the opaque
//! provider accepts the exact encoded `header.payload` signing input. Resolver
//! output is treated as untrusted routing data: its key domain and epoch must
//! exactly match the requested token domain and JOSE route before provider use.

use core::fmt;

use trnm_token_crypto_provider::{
    verify_exact, Hs256Provider, KeyDomain, KeyReference, ProviderError, VerificationDecision,
    SIGNATURE_BYTES,
};
use trnm_token_jwt_adapter::base64url;
use trnm_token_jwt_adapter::json::{self, JsonLimits, JsonValue};

pub const EPOCH_KEY_ID_PREFIX: &str = "trnm-kep-v1:";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TokenRoute {
    Legacy,
    Epoch(u32),
}

pub trait KeyResolver: fmt::Debug + Send + Sync {
    fn resolve(
        &self,
        domain: KeyDomain,
        route: TokenRoute,
    ) -> Result<KeyReference, AuthenticationError>;
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AuthenticationProfile {
    pub domain: KeyDomain,
    pub max_token_bytes: usize,
    pub max_header_bytes: usize,
    pub max_payload_bytes: usize,
    pub allow_legacy_without_key_id: bool,
    pub reject_unknown_header_fields: bool,
    pub json_limits: JsonLimits,
}

impl Default for AuthenticationProfile {
    fn default() -> Self {
        Self {
            domain: KeyDomain::AccessToken,
            max_token_bytes: 32 * 1024,
            max_header_bytes: 1024,
            max_payload_bytes: 16 * 1024,
            allow_legacy_without_key_id: true,
            reject_unknown_header_fields: true,
            json_limits: JsonLimits::default(),
        }
    }
}

impl AuthenticationProfile {
    pub fn validate(&self) -> Result<(), AuthenticationError> {
        if self.max_token_bytes == 0
            || self.max_header_bytes == 0
            || self.max_payload_bytes == 0
            || self.max_header_bytes >= self.max_token_bytes
            || self.max_payload_bytes >= self.max_token_bytes
        {
            return Err(AuthenticationError::InvalidProfile);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AuthenticatedJwt {
    pub route: TokenRoute,
    pub key: KeyReference,
    pub payload_bytes: Vec<u8>,
}

impl AuthenticatedJwt {
    pub fn parse_claims(&self, limits: JsonLimits) -> Result<JsonValue, AuthenticationError> {
        json::parse(&self.payload_bytes, limits).map_err(|_| AuthenticationError::PayloadJson)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum AuthenticationError {
    InvalidProfile,
    TokenTooLarge { actual: usize },
    SegmentCount,
    EmptySegment,
    HeaderDecode,
    HeaderJson,
    HeaderNotObject,
    AlgorithmMissing,
    UnsupportedAlgorithm,
    InvalidType,
    UnknownHeaderField,
    LegacyRouteForbidden,
    InvalidKeyId,
    UnknownKey,
    ResolvedKeyDomainMismatch { expected: KeyDomain, actual: KeyDomain },
    ResolvedKeyEpochMismatch {
        expected: Option<u32>,
        actual: Option<u32>,
    },
    SignatureDecode,
    SignatureLength { actual: usize },
    Provider(ProviderError),
    SignatureRejected,
    PayloadDecode,
    PayloadJson,
}

impl fmt::Display for AuthenticationError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidProfile => formatter.write_str("invalid JWT authentication profile"),
            Self::TokenTooLarge { actual } => write!(formatter, "JWT is too large: {actual} bytes"),
            Self::SegmentCount => formatter.write_str("JWT must contain exactly three segments"),
            Self::EmptySegment => formatter.write_str("JWT segments must not be empty"),
            Self::HeaderDecode => formatter.write_str("JWT header base64url decode failed"),
            Self::HeaderJson => formatter.write_str("JWT header JSON parse failed"),
            Self::HeaderNotObject => formatter.write_str("JWT header must be an object"),
            Self::AlgorithmMissing => formatter.write_str("JWT alg header is missing"),
            Self::UnsupportedAlgorithm => formatter.write_str("JWT algorithm is not HS256"),
            Self::InvalidType => formatter.write_str("JWT typ header is invalid"),
            Self::UnknownHeaderField => {
                formatter.write_str("JWT header contains an unrecognized field")
            }
            Self::LegacyRouteForbidden => formatter.write_str("legacy JWT route is disabled"),
            Self::InvalidKeyId => formatter.write_str("JWT kid header is invalid"),
            Self::UnknownKey => formatter.write_str("JWT key route is unavailable"),
            Self::ResolvedKeyDomainMismatch { expected, actual } => write!(
                formatter,
                "resolved JWT key domain {actual:?} does not match requested domain {expected:?}"
            ),
            Self::ResolvedKeyEpochMismatch { expected, actual } => write!(
                formatter,
                "resolved JWT key epoch {actual:?} does not match token route epoch {expected:?}"
            ),
            Self::SignatureDecode => formatter.write_str("JWT signature base64url decode failed"),
            Self::SignatureLength { actual } => {
                write!(
                    formatter,
                    "JWT signature is {actual} bytes; expected {SIGNATURE_BYTES}"
                )
            }
            Self::Provider(error) => write!(formatter, "cryptographic provider failed: {error}"),
            Self::SignatureRejected => formatter.write_str("JWT signature was rejected"),
            Self::PayloadDecode => formatter.write_str("authenticated JWT payload decode failed"),
            Self::PayloadJson => formatter.write_str("authenticated JWT payload JSON parse failed"),
        }
    }
}

impl std::error::Error for AuthenticationError {}

pub fn authenticate(
    token: &str,
    profile: &AuthenticationProfile,
    resolver: &dyn KeyResolver,
    provider: &dyn Hs256Provider,
) -> Result<AuthenticatedJwt, AuthenticationError> {
    profile.validate()?;
    if token.len() > profile.max_token_bytes {
        return Err(AuthenticationError::TokenTooLarge {
            actual: token.len(),
        });
    }

    let mut segments = token.split('.');
    let header_segment = segments.next().ok_or(AuthenticationError::SegmentCount)?;
    let payload_segment = segments.next().ok_or(AuthenticationError::SegmentCount)?;
    let signature_segment = segments.next().ok_or(AuthenticationError::SegmentCount)?;
    if segments.next().is_some() {
        return Err(AuthenticationError::SegmentCount);
    }
    if header_segment.is_empty() || payload_segment.is_empty() || signature_segment.is_empty() {
        return Err(AuthenticationError::EmptySegment);
    }

    let header_bytes = base64url::decode(header_segment, profile.max_header_bytes)
        .map_err(|_| AuthenticationError::HeaderDecode)?;
    let header = json::parse(&header_bytes, profile.json_limits)
        .map_err(|_| AuthenticationError::HeaderJson)?;
    let header = header
        .as_object()
        .ok_or(AuthenticationError::HeaderNotObject)?;
    validate_header(header, profile)?;
    let route = route(header, profile)?;
    let key = resolver.resolve(profile.domain, route)?;
    validate_resolved_key(&key, profile.domain, route)?;

    let signature = base64url::decode(signature_segment, SIGNATURE_BYTES)
        .map_err(|_| AuthenticationError::SignatureDecode)?;
    if signature.len() != SIGNATURE_BYTES {
        return Err(AuthenticationError::SignatureLength {
            actual: signature.len(),
        });
    }
    let signing_input = format!("{header_segment}.{payload_segment}");
    let decision = verify_exact(provider, &key, signing_input.as_bytes(), &signature)
        .map_err(AuthenticationError::Provider)?;
    if decision != VerificationDecision::Accepted {
        return Err(AuthenticationError::SignatureRejected);
    }

    let payload_bytes = base64url::decode(payload_segment, profile.max_payload_bytes)
        .map_err(|_| AuthenticationError::PayloadDecode)?;
    Ok(AuthenticatedJwt {
        route,
        key,
        payload_bytes,
    })
}

fn validate_resolved_key(
    key: &KeyReference,
    expected_domain: KeyDomain,
    route: TokenRoute,
) -> Result<(), AuthenticationError> {
    if key.domain != expected_domain {
        return Err(AuthenticationError::ResolvedKeyDomainMismatch {
            expected: expected_domain,
            actual: key.domain,
        });
    }
    let expected_epoch = match route {
        TokenRoute::Legacy => None,
        TokenRoute::Epoch(epoch) => Some(epoch),
    };
    if key.epoch != expected_epoch {
        return Err(AuthenticationError::ResolvedKeyEpochMismatch {
            expected: expected_epoch,
            actual: key.epoch,
        });
    }
    Ok(())
}

fn validate_header(
    header: &std::collections::BTreeMap<String, JsonValue>,
    profile: &AuthenticationProfile,
) -> Result<(), AuthenticationError> {
    if profile.reject_unknown_header_fields {
        for key in header.keys() {
            if !matches!(key.as_str(), "alg" | "typ" | "kid") {
                return Err(AuthenticationError::UnknownHeaderField);
            }
        }
    }
    let algorithm = header
        .get("alg")
        .and_then(JsonValue::as_str)
        .ok_or(AuthenticationError::AlgorithmMissing)?;
    if algorithm != "HS256" {
        return Err(AuthenticationError::UnsupportedAlgorithm);
    }
    if let Some(value) = header.get("typ") {
        if value.as_str() != Some("JWT") {
            return Err(AuthenticationError::InvalidType);
        }
    }
    Ok(())
}

fn route(
    header: &std::collections::BTreeMap<String, JsonValue>,
    profile: &AuthenticationProfile,
) -> Result<TokenRoute, AuthenticationError> {
    match header.get("kid") {
        None if profile.allow_legacy_without_key_id => Ok(TokenRoute::Legacy),
        None => Err(AuthenticationError::LegacyRouteForbidden),
        Some(JsonValue::String(value)) => {
            let digits = value
                .strip_prefix(EPOCH_KEY_ID_PREFIX)
                .ok_or(AuthenticationError::InvalidKeyId)?;
            if digits.is_empty()
                || (digits.len() > 1 && digits.starts_with('0'))
                || !digits.bytes().all(|byte| byte.is_ascii_digit())
            {
                return Err(AuthenticationError::InvalidKeyId);
            }
            let epoch = digits
                .parse::<u32>()
                .map_err(|_| AuthenticationError::InvalidKeyId)?;
            if epoch == 0 {
                return Err(AuthenticationError::InvalidKeyId);
            }
            Ok(TokenRoute::Epoch(epoch))
        }
        Some(_) => Err(AuthenticationError::InvalidKeyId),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;
    use trnm_token_crypto_provider::{KeyHandle, Signature32};

    #[derive(Debug, Default)]
    struct Resolver;

    impl KeyResolver for Resolver {
        fn resolve(
            &self,
            domain: KeyDomain,
            route: TokenRoute,
        ) -> Result<KeyReference, AuthenticationError> {
            let epoch = match route {
                TokenRoute::Legacy => None,
                TokenRoute::Epoch(value) => Some(value),
            };
            key_reference(domain, epoch)
        }
    }

    #[derive(Debug)]
    struct FixedResolver {
        key: KeyReference,
    }

    impl KeyResolver for FixedResolver {
        fn resolve(
            &self,
            _domain: KeyDomain,
            _route: TokenRoute,
        ) -> Result<KeyReference, AuthenticationError> {
            Ok(self.key.clone())
        }
    }

    #[derive(Debug)]
    struct Provider {
        accept: bool,
        inputs: Mutex<Vec<Vec<u8>>>,
    }

    impl Provider {
        fn new(accept: bool) -> Self {
            Self {
                accept,
                inputs: Mutex::new(Vec::new()),
            }
        }
    }

    impl Hs256Provider for Provider {
        fn sign(
            &self,
            _key: &KeyReference,
            _exact_signing_input: &[u8],
        ) -> Result<Signature32, ProviderError> {
            Err(ProviderError::PermissionDenied)
        }

        fn verify(
            &self,
            _key: &KeyReference,
            exact_signing_input: &[u8],
            _signature: &Signature32,
        ) -> Result<VerificationDecision, ProviderError> {
            self.inputs
                .lock()
                .unwrap()
                .push(exact_signing_input.to_vec());
            Ok(if self.accept {
                VerificationDecision::Accepted
            } else {
                VerificationDecision::Rejected
            })
        }
    }

    fn key_reference(domain: KeyDomain, epoch: Option<u32>) -> Result<KeyReference, AuthenticationError> {
        KeyReference::new(domain, KeyHandle::new("kms://jwt/access").unwrap(), epoch)
            .map_err(|_| AuthenticationError::UnknownKey)
    }

    fn token(header: &[u8], payload_segment: &str, signature: &[u8]) -> String {
        format!(
            "{}.{}.{}",
            base64url::encode(header),
            payload_segment,
            base64url::encode(signature)
        )
    }

    fn valid_payload() -> String {
        base64url::encode(br#"{"sub":"user-1","exp":2000000000}"#)
    }

    #[test]
    fn provider_authenticates_exact_encoded_input_before_payload_decode() {
        let provider = Provider::new(true);
        let value = token(
            br#"{"alg":"HS256","typ":"JWT"}"#,
            &valid_payload(),
            &[0x5a; SIGNATURE_BYTES],
        );
        let authenticated = authenticate(
            &value,
            &AuthenticationProfile::default(),
            &Resolver,
            &provider,
        )
        .unwrap();
        assert_eq!(authenticated.route, TokenRoute::Legacy);
        assert_eq!(
            authenticated
                .parse_claims(JsonLimits::default())
                .unwrap()
                .as_object()
                .unwrap()
                .get("sub")
                .and_then(JsonValue::as_str),
            Some("user-1")
        );
        let expected = value.rsplit_once('.').unwrap().0.as_bytes();
        assert_eq!(provider.inputs.lock().unwrap()[0], expected);
    }

    #[test]
    fn rejected_signature_precedes_invalid_payload_decode() {
        let provider = Provider::new(false);
        let value = token(
            br#"{"alg":"HS256","typ":"JWT"}"#,
            "%%%not-base64%%%",
            &[0x00; SIGNATURE_BYTES],
        );
        assert_eq!(
            authenticate(
                &value,
                &AuthenticationProfile::default(),
                &Resolver,
                &provider,
            )
            .unwrap_err(),
            AuthenticationError::SignatureRejected
        );
        assert_eq!(provider.inputs.lock().unwrap().len(), 1);
    }

    #[test]
    fn authenticated_invalid_payload_fails_only_after_provider_acceptance() {
        let provider = Provider::new(true);
        let value = token(
            br#"{"alg":"HS256","typ":"JWT"}"#,
            "%%%not-base64%%%",
            &[0x5a; SIGNATURE_BYTES],
        );
        assert_eq!(
            authenticate(
                &value,
                &AuthenticationProfile::default(),
                &Resolver,
                &provider,
            )
            .unwrap_err(),
            AuthenticationError::PayloadDecode
        );
        assert_eq!(provider.inputs.lock().unwrap().len(), 1);
    }

    #[test]
    fn algorithm_confusion_and_unknown_headers_fail_before_provider() {
        let provider = Provider::new(true);
        for header in [
            br#"{"alg":"none","typ":"JWT"}"#.as_slice(),
            br#"{"alg":"RS256","typ":"JWT"}"#.as_slice(),
            br#"{"alg":"HS256","typ":"JWT","crit":["x"]}"#.as_slice(),
        ] {
            let value = token(header, &valid_payload(), &[0x5a; SIGNATURE_BYTES]);
            assert!(authenticate(
                &value,
                &AuthenticationProfile::default(),
                &Resolver,
                &provider,
            )
            .is_err());
        }
        assert!(provider.inputs.lock().unwrap().is_empty());
    }

    #[test]
    fn epoch_route_is_strict_and_never_falls_back() {
        let provider = Provider::new(true);
        let value = token(
            br#"{"alg":"HS256","typ":"JWT","kid":"trnm-kep-v1:7"}"#,
            &valid_payload(),
            &[0x5a; SIGNATURE_BYTES],
        );
        let authenticated = authenticate(
            &value,
            &AuthenticationProfile::default(),
            &Resolver,
            &provider,
        )
        .unwrap();
        assert_eq!(authenticated.route, TokenRoute::Epoch(7));
        assert_eq!(authenticated.key.epoch, Some(7));

        for kid in ["trnm-kep-v1:0", "trnm-kep-v1:07", "unknown:7"] {
            let header = format!(r#"{{"alg":"HS256","typ":"JWT","kid":"{kid}"}}"#);
            let value = token(
                header.as_bytes(),
                &valid_payload(),
                &[0x5a; SIGNATURE_BYTES],
            );
            assert_eq!(
                authenticate(
                    &value,
                    &AuthenticationProfile::default(),
                    &Resolver,
                    &provider,
                )
                .unwrap_err(),
                AuthenticationError::InvalidKeyId
            );
        }
    }

    #[test]
    fn resolver_domain_mismatch_fails_before_provider() {
        let provider = Provider::new(true);
        let mut profile = AuthenticationProfile::default();
        profile.domain = KeyDomain::RefreshToken;
        let resolver = FixedResolver {
            key: key_reference(KeyDomain::AccessToken, None).unwrap(),
        };
        let value = token(
            br#"{"alg":"HS256","typ":"JWT"}"#,
            &valid_payload(),
            &[0x5a; SIGNATURE_BYTES],
        );
        assert_eq!(
            authenticate(&value, &profile, &resolver, &provider).unwrap_err(),
            AuthenticationError::ResolvedKeyDomainMismatch {
                expected: KeyDomain::RefreshToken,
                actual: KeyDomain::AccessToken,
            }
        );
        assert!(provider.inputs.lock().unwrap().is_empty());
    }

    #[test]
    fn resolver_epoch_mismatch_fails_before_provider() {
        let provider = Provider::new(true);
        let profile = AuthenticationProfile::default();
        let epoch_value = token(
            br#"{"alg":"HS256","typ":"JWT","kid":"trnm-kep-v1:7"}"#,
            &valid_payload(),
            &[0x5a; SIGNATURE_BYTES],
        );
        let wrong_epoch = FixedResolver {
            key: key_reference(KeyDomain::AccessToken, Some(8)).unwrap(),
        };
        assert_eq!(
            authenticate(&epoch_value, &profile, &wrong_epoch, &provider).unwrap_err(),
            AuthenticationError::ResolvedKeyEpochMismatch {
                expected: Some(7),
                actual: Some(8),
            }
        );

        let legacy_value = token(
            br#"{"alg":"HS256","typ":"JWT"}"#,
            &valid_payload(),
            &[0x5a; SIGNATURE_BYTES],
        );
        let epoch_for_legacy = FixedResolver {
            key: key_reference(KeyDomain::AccessToken, Some(7)).unwrap(),
        };
        assert_eq!(
            authenticate(&legacy_value, &profile, &epoch_for_legacy, &provider).unwrap_err(),
            AuthenticationError::ResolvedKeyEpochMismatch {
                expected: None,
                actual: Some(7),
            }
        );
        assert!(provider.inputs.lock().unwrap().is_empty());
    }

    #[test]
    fn signature_length_is_checked_before_provider() {
        let provider = Provider::new(true);
        let value = token(
            br#"{"alg":"HS256","typ":"JWT"}"#,
            &valid_payload(),
            &[0x5a; SIGNATURE_BYTES - 1],
        );
        assert_eq!(
            authenticate(
                &value,
                &AuthenticationProfile::default(),
                &Resolver,
                &provider,
            )
            .unwrap_err(),
            AuthenticationError::SignatureLength {
                actual: SIGNATURE_BYTES - 1
            }
        );
        assert!(provider.inputs.lock().unwrap().is_empty());
    }
}
