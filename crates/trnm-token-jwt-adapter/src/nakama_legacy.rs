//! Explicit issuer for Nakama's six legacy session claims.
//!
//! This path feeds the ordered payload bytes directly to base64url and the MAC
//! provider. It does not pass through the existing canonical JSON encoder.
//! The pinned JWT library's `NewWithClaims` header serializes as `alg`, `typ`;
//! the signing input is the exact two encoded segments, without a `kid`.
//!
//! Caller-supplied limits are candidate resource policies, not Nakama size
//! validation rules. This module has no JWT verification or principal API:
//! Go's claims decoder, expiration rules, UUID parsing and session blacklist
//! remain separate work. Issuance does not validate or generate identities,
//! interpret timestamps, rotate refresh credentials, or persist a session.

use core::fmt;
use trnm_token_crypto_provider::{
    sign_exact, Hs256Provider, NakamaLegacyKeyKind, ProviderError, MAX_SIGNING_INPUT_BYTES,
};

use crate::{base64url, encode_nakama_legacy_payload, NakamaLegacyClaims, NakamaLegacyEncodeError};

const HEADER_JSON: &[u8] = br#"{"alg":"HS256","typ":"JWT"}"#;
const ENCODED_SIGNATURE_BYTES: usize = 43;
const MAX_LOCAL_TOKEN_BYTES: usize = MAX_SIGNING_INPUT_BYTES + 1 + ENCODED_SIGNATURE_BYTES;

/// Explicit per-call allocation/output budget for this issuer profile.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct NakamaLegacyIssueLimits {
    max_payload_bytes: usize,
    max_token_bytes: usize,
}

impl NakamaLegacyIssueLimits {
    pub fn new(
        max_payload_bytes: usize,
        max_token_bytes: usize,
    ) -> Result<Self, NakamaLegacyIssueError> {
        if !(2..=MAX_SIGNING_INPUT_BYTES).contains(&max_payload_bytes)
            || !(1..=MAX_LOCAL_TOKEN_BYTES).contains(&max_token_bytes)
        {
            return Err(NakamaLegacyIssueError::InvalidLimits);
        }
        Ok(Self {
            max_payload_bytes,
            max_token_bytes,
        })
    }

    #[must_use]
    pub const fn max_payload_bytes(self) -> usize {
        self.max_payload_bytes
    }

    #[must_use]
    pub const fn max_token_bytes(self) -> usize {
        self.max_token_bytes
    }
}

/// Issued credential with a redacted Debug implementation.
///
/// This value is not a verified token or an authenticated principal. Its kind
/// records the caller-selected signing key, not a claim read from a token.
#[derive(Eq, PartialEq)]
pub struct NakamaLegacyToken {
    kind: NakamaLegacyKeyKind,
    value: String,
}

impl NakamaLegacyToken {
    #[must_use]
    pub const fn kind(&self) -> NakamaLegacyKeyKind {
        self.kind
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.value
    }

    #[must_use]
    pub fn into_string(self) -> String {
        self.value
    }
}

impl fmt::Debug for NakamaLegacyToken {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("NakamaLegacyToken")
            .field("kind", &self.kind)
            .field("bytes", &self.value.len())
            .field("credential", &"<redacted>")
            .finish()
    }
}

#[derive(Debug, Eq, PartialEq)]
pub struct NakamaLegacyTokenPair {
    pub access: NakamaLegacyToken,
    pub refresh: NakamaLegacyToken,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum NakamaLegacyIssueError {
    InvalidLimits,
    Payload(NakamaLegacyEncodeError),
    SigningInputTooLarge,
    TokenTooLarge { limit: usize },
    AllocationFailed,
    Provider(ProviderError),
}

impl fmt::Display for NakamaLegacyIssueError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidLimits => formatter.write_str("invalid legacy issuer resource limits"),
            Self::Payload(error) => error.fmt(formatter),
            Self::SigningInputTooLarge => {
                formatter.write_str("legacy signing input exceeds provider byte limit")
            }
            Self::TokenTooLarge { limit } => {
                write!(formatter, "legacy token exceeds {limit} bytes")
            }
            Self::AllocationFailed => formatter.write_str("legacy token allocation failed"),
            Self::Provider(error) => error.fmt(formatter),
        }
    }
}

impl std::error::Error for NakamaLegacyIssueError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Payload(error) => Some(error),
            Self::Provider(error) => Some(error),
            _ => None,
        }
    }
}

pub struct NakamaLegacyIssuer<'a> {
    provider: &'a dyn Hs256Provider,
    limits: NakamaLegacyIssueLimits,
}

impl fmt::Debug for NakamaLegacyIssuer<'_> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("NakamaLegacyIssuer")
            .field("limits", &self.limits)
            .field("provider", &"<opaque-provider>")
            .finish()
    }
}

impl<'a> NakamaLegacyIssuer<'a> {
    #[must_use]
    pub fn new(provider: &'a dyn Hs256Provider, limits: NakamaLegacyIssueLimits) -> Self {
        Self { provider, limits }
    }

    /// Encode the supplied six claims without any identity/time policy.
    ///
    /// All length checks run before signing. The provider receives the exact
    /// encoded bytes, and an error returns no credential. The base64 encoder's
    /// internal allocation is bounded but uses its existing infallible API.
    pub fn issue(
        &self,
        kind: NakamaLegacyKeyKind,
        claims: &NakamaLegacyClaims<'_>,
    ) -> Result<NakamaLegacyToken, NakamaLegacyIssueError> {
        let payload = encode_nakama_legacy_payload(claims, self.limits.max_payload_bytes)
            .map_err(NakamaLegacyIssueError::Payload)?;
        let header = base64url::encode(HEADER_JSON);
        let payload_encoded_len = encoded_len(payload.len())?;
        let input_len = header
            .len()
            .checked_add(1)
            .and_then(|length| length.checked_add(payload_encoded_len))
            .filter(|length| *length <= MAX_SIGNING_INPUT_BYTES)
            .ok_or(NakamaLegacyIssueError::SigningInputTooLarge)?;
        let token_len = input_len
            .checked_add(1 + ENCODED_SIGNATURE_BYTES)
            .filter(|length| *length <= self.limits.max_token_bytes)
            .ok_or(NakamaLegacyIssueError::TokenTooLarge {
                limit: self.limits.max_token_bytes,
            })?;
        let mut token = String::new();
        token
            .try_reserve_exact(token_len)
            .map_err(|_| NakamaLegacyIssueError::AllocationFailed)?;
        token.push_str(&header);
        token.push('.');
        token.push_str(&base64url::encode(&payload));
        debug_assert_eq!(token.len(), input_len);
        let signature = sign_exact(self.provider, &kind.key_reference(), token.as_bytes())
            .map_err(NakamaLegacyIssueError::Provider)?;
        token.push('.');
        token.push_str(&base64url::encode(signature.as_bytes()));
        debug_assert_eq!(token.len(), token_len);
        Ok(NakamaLegacyToken { kind, value: token })
    }

    /// Preserve the identity, variables and original `iat`; select both `exp`s
    /// explicitly. No clock or TTL arithmetic occurs inside this module.
    ///
    /// A refresh-signing failure returns no pair. The access MAC may already
    /// have been computed: this is not a database transaction or provider
    /// compensation protocol.
    pub fn issue_pair(
        &self,
        identity: &NakamaLegacyClaims<'_>,
        access_expires_at: i64,
        refresh_expires_at: i64,
    ) -> Result<NakamaLegacyTokenPair, NakamaLegacyIssueError> {
        let access_claims = NakamaLegacyClaims {
            expires_at: access_expires_at,
            ..*identity
        };
        let refresh_claims = NakamaLegacyClaims {
            expires_at: refresh_expires_at,
            ..*identity
        };
        let access = self.issue(NakamaLegacyKeyKind::Access, &access_claims)?;
        let refresh = self.issue(NakamaLegacyKeyKind::Refresh, &refresh_claims)?;
        Ok(NakamaLegacyTokenPair { access, refresh })
    }
}

fn encoded_len(input: usize) -> Result<usize, NakamaLegacyIssueError> {
    (input / 3)
        .checked_mul(4)
        .and_then(|length| {
            length.checked_add(match input % 3 {
                0 => 0,
                1 => 2,
                _ => 3,
            })
        })
        .ok_or(NakamaLegacyIssueError::SigningInputTooLarge)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;
    use std::sync::Mutex;
    use trnm_token_crypto_provider::{
        KeyReference, NakamaLegacyHs256Provider, NakamaLegacyKeyLimits, Signature32,
        VerificationDecision,
    };

    #[derive(Default)]
    struct RecordingProvider {
        calls: Mutex<Vec<(KeyReference, Vec<u8>)>>,
        fail_refresh: bool,
    }

    impl fmt::Debug for RecordingProvider {
        fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            formatter.write_str("RecordingProvider(private-provider-detail)")
        }
    }

    impl Hs256Provider for RecordingProvider {
        fn sign(&self, key: &KeyReference, input: &[u8]) -> Result<Signature32, ProviderError> {
            self.calls
                .lock()
                .unwrap()
                .push((key.clone(), input.to_vec()));
            if self.fail_refresh && *key == NakamaLegacyKeyKind::Refresh.key_reference() {
                return Err(ProviderError::KeyUnavailable);
            }
            Ok(Signature32::new([0x5a; 32]))
        }

        fn verify(
            &self,
            _: &KeyReference,
            _: &[u8],
            _: &Signature32,
        ) -> Result<VerificationDecision, ProviderError> {
            panic!("issuer must not call verification")
        }
    }

    fn limits() -> NakamaLegacyIssueLimits {
        NakamaLegacyIssueLimits::new(16 * 1024, MAX_LOCAL_TOKEN_BYTES).unwrap()
    }

    fn decoded_payload(token: &NakamaLegacyToken) -> Vec<u8> {
        let parts: Vec<_> = token.as_str().split('.').collect();
        assert_eq!(parts.len(), 3);
        assert_eq!(base64url::decode(parts[0], 100).unwrap(), HEADER_JSON);
        assert_eq!(base64url::decode(parts[2], 32).unwrap().len(), 32);
        base64url::decode(parts[1], 32 * 1024).unwrap()
    }

    #[test]
    fn nakama_legacy_issuer_preserves_order_escaping_and_exact_signing_bytes() {
        let provider = RecordingProvider::default();
        let variables = BTreeMap::from([
            ("z".into(), "<>&".into()),
            ("a".into(), "line\u{2028}".into()),
        ]);
        let claims = NakamaLegacyClaims {
            token_id: "raw-tid",
            user_id: "raw-uid",
            username: "用户",
            variables: Some(&variables),
            expires_at: 20,
            issued_at: 10,
        };
        let token = NakamaLegacyIssuer::new(&provider, limits())
            .issue(NakamaLegacyKeyKind::Access, &claims)
            .unwrap();
        let expected = r#"{"tid":"raw-tid","uid":"raw-uid","usn":"用户","vrs":{"a":"line\u2028","z":"\u003c\u003e\u0026"},"exp":20,"iat":10}"#;
        assert_eq!(decoded_payload(&token), expected.as_bytes());
        let calls = provider.calls.lock().unwrap();
        assert_eq!(calls.len(), 1);
        assert_eq!(calls[0].0, NakamaLegacyKeyKind::Access.key_reference());
        assert_eq!(
            calls[0].1,
            token.as_str().rsplit_once('.').unwrap().0.as_bytes()
        );
        assert_eq!(token.kind(), NakamaLegacyKeyKind::Access);
        assert!(!token.as_str().contains('='));
    }

    #[test]
    fn nakama_legacy_issuer_pair_preserves_identity_and_original_iat() {
        let provider = RecordingProvider::default();
        let claims = NakamaLegacyClaims {
            token_id: "previous-tid",
            user_id: "uid",
            username: "current-name",
            issued_at: -7,
            expires_at: 999,
            ..NakamaLegacyClaims::default()
        };
        let pair = NakamaLegacyIssuer::new(&provider, limits())
            .issue_pair(&claims, 100, 200)
            .unwrap();
        assert_eq!(
            decoded_payload(&pair.access),
            br#"{"tid":"previous-tid","uid":"uid","usn":"current-name","exp":100,"iat":-7}"#
        );
        assert_eq!(
            decoded_payload(&pair.refresh),
            br#"{"tid":"previous-tid","uid":"uid","usn":"current-name","exp":200,"iat":-7}"#
        );
        let calls = provider.calls.lock().unwrap();
        assert_eq!(calls.len(), 2);
        assert_eq!(calls[0].0, NakamaLegacyKeyKind::Access.key_reference());
        assert_eq!(calls[1].0, NakamaLegacyKeyKind::Refresh.key_reference());
    }

    #[test]
    fn nakama_legacy_issuer_all_bounds_reject_before_provider_call() {
        let provider = RecordingProvider::default();
        let small = NakamaLegacyIssuer::new(
            &provider,
            NakamaLegacyIssueLimits::new(2, MAX_LOCAL_TOKEN_BYTES).unwrap(),
        );
        assert!(matches!(
            small.issue(
                NakamaLegacyKeyKind::Access,
                &NakamaLegacyClaims {
                    username: "a",
                    ..NakamaLegacyClaims::default()
                }
            ),
            Err(NakamaLegacyIssueError::Payload(_))
        ));
        let token_bound =
            NakamaLegacyIssuer::new(&provider, NakamaLegacyIssueLimits::new(2, 1).unwrap());
        assert_eq!(
            token_bound
                .issue(NakamaLegacyKeyKind::Access, &NakamaLegacyClaims::default())
                .unwrap_err(),
            NakamaLegacyIssueError::TokenTooLarge { limit: 1 }
        );
        let large_name = "a".repeat(25_000);
        let signing_bound = NakamaLegacyIssuer::new(
            &provider,
            NakamaLegacyIssueLimits::new(32768, MAX_LOCAL_TOKEN_BYTES).unwrap(),
        );
        assert_eq!(
            signing_bound
                .issue(
                    NakamaLegacyKeyKind::Access,
                    &NakamaLegacyClaims {
                        username: &large_name,
                        ..NakamaLegacyClaims::default()
                    }
                )
                .unwrap_err(),
            NakamaLegacyIssueError::SigningInputTooLarge
        );
        assert!(provider.calls.lock().unwrap().is_empty());
    }

    #[test]
    fn nakama_legacy_issuer_token_exact_limit_and_omission_are_retained() {
        let provider = RecordingProvider::default();
        let initial = NakamaLegacyIssuer::new(&provider, limits())
            .issue(NakamaLegacyKeyKind::Refresh, &NakamaLegacyClaims::default())
            .unwrap();
        assert_eq!(decoded_payload(&initial), b"{}");
        let length = initial.as_str().len();
        let exact =
            NakamaLegacyIssuer::new(&provider, NakamaLegacyIssueLimits::new(2, length).unwrap());
        assert_eq!(
            exact
                .issue(NakamaLegacyKeyKind::Refresh, &NakamaLegacyClaims::default())
                .unwrap(),
            initial
        );
        let short = NakamaLegacyIssuer::new(
            &provider,
            NakamaLegacyIssueLimits::new(2, length - 1).unwrap(),
        );
        assert!(matches!(
            short.issue(NakamaLegacyKeyKind::Refresh, &NakamaLegacyClaims::default()),
            Err(NakamaLegacyIssueError::TokenTooLarge { .. })
        ));
        assert_eq!(provider.calls.lock().unwrap().len(), 2);
    }

    #[test]
    fn nakama_legacy_issuer_provider_failure_returns_no_pair() {
        let provider = RecordingProvider {
            fail_refresh: true,
            ..RecordingProvider::default()
        };
        let issuer = NakamaLegacyIssuer::new(&provider, limits());
        assert_eq!(
            issuer
                .issue_pair(&NakamaLegacyClaims::default(), 1, 2)
                .unwrap_err(),
            NakamaLegacyIssueError::Provider(ProviderError::KeyUnavailable)
        );
        assert_eq!(provider.calls.lock().unwrap().len(), 2);
    }

    #[test]
    fn nakama_legacy_issuer_debug_has_no_credential_or_provider_details() {
        let provider = RecordingProvider::default();
        let issuer = NakamaLegacyIssuer::new(&provider, limits());
        let token = issuer
            .issue(
                NakamaLegacyKeyKind::Access,
                &NakamaLegacyClaims {
                    username: "private-user",
                    ..NakamaLegacyClaims::default()
                },
            )
            .unwrap();
        let debug = format!("{issuer:?} {token:?}");
        assert!(!debug.contains(token.as_str()));
        assert!(!debug.contains("private-user"));
        assert!(!debug.contains("private-provider-detail"));
        assert!(debug.contains("<redacted>"));
        assert_eq!(token.into_string().split('.').count(), 3);
    }

    #[test]
    fn nakama_legacy_issuer_invalid_limits_are_explicit() {
        for (payload, token) in [
            (0, 100),
            (1, 100),
            (32769, 100),
            (2, 0),
            (2, MAX_LOCAL_TOKEN_BYTES + 1),
        ] {
            assert_eq!(
                NakamaLegacyIssueLimits::new(payload, token).unwrap_err(),
                NakamaLegacyIssueError::InvalidLimits
            );
        }
        assert_eq!(limits().max_payload_bytes(), 16384);
        assert_eq!(limits().max_token_bytes(), 32812);
    }

    #[test]
    fn nakama_legacy_issuer_real_mac_uses_selected_short_key() {
        let provider = NakamaLegacyHs256Provider::new(
            b"defaultencryptionkey",
            b"defaultrefreshencryptionkey",
            NakamaLegacyKeyLimits::new(4096).unwrap(),
        )
        .unwrap();
        let token = NakamaLegacyIssuer::new(&provider, limits())
            .issue(
                NakamaLegacyKeyKind::Access,
                &NakamaLegacyClaims {
                    token_id: "t",
                    user_id: "u",
                    username: "n",
                    expires_at: 2,
                    issued_at: 1,
                    ..NakamaLegacyClaims::default()
                },
            )
            .unwrap();
        // Independently generated Python stdlib HMAC fixture, not a Go oracle.
        assert_eq!(token.as_str(), "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0aWQiOiJ0IiwidWlkIjoidSIsInVzbiI6Im4iLCJleHAiOjIsImlhdCI6MX0.K4Ft-mZ34s91CPA_VQWoXGxIfAWP3SJFOx-uYINofKE");
        let (input, signature) = token.as_str().rsplit_once('.').unwrap();
        let signature = base64url::decode(signature, 32).unwrap();
        assert_eq!(
            trnm_token_crypto_provider::verify_exact(
                &provider,
                &NakamaLegacyKeyKind::Access.key_reference(),
                input.as_bytes(),
                &signature
            )
            .unwrap(),
            VerificationDecision::Accepted
        );
        assert_eq!(
            trnm_token_crypto_provider::verify_exact(
                &provider,
                &NakamaLegacyKeyKind::Refresh.key_reference(),
                input.as_bytes(),
                &signature
            )
            .unwrap(),
            VerificationDecision::Rejected
        );
    }
}
