//! Fixed-purpose verification of Nakama's legacy HS256 token profile.
//!
//! The configured provider authenticates the original encoded header.payload.
//! Only then are the six claims decoded and expiration checked. The pinned Go
//! parser decodes claims earlier; internal failure precedence is deliberately
//! outside this API's compatibility claim. No UUID, user, blacklist, session
//! family, or business principal is established here.
//!
//! Expiration uses the pinned Go default one-second precision and zero leeway.
//! Go time.Unix stores signed seconds after adding unixToInternal with integer
//! wrap. This matters outside the ordinary Unix time range; raw i64 comparison
//! is not equivalent. Caller-selected caps are narrower than upstream inputs.

use core::fmt;
use trnm_token_crypto_provider::{
    verify_exact, Hs256Provider, NakamaLegacyKeyKind, ProviderError, VerificationDecision,
    MAX_SIGNING_INPUT_BYTES, SIGNATURE_BYTES,
};

use crate::{
    decode_and_validate_nakama_legacy_hs256_header, decode_nakama_legacy_claims,
    decode_nakama_legacy_raw_url_segment, NakamaLegacyDecodeError, NakamaLegacyDecodeLimits,
    NakamaLegacyDecodedClaims, NakamaLegacyHeaderError, NakamaLegacyRawClaims,
    NakamaLegacyRawUrlLimits,
};

const MAX_SIGNATURE_ENCODED_BYTES: usize = 1024;
const MAX_LOCAL_TOKEN_BYTES: usize = MAX_SIGNING_INPUT_BYTES + 1 + MAX_SIGNATURE_ENCODED_BYTES;
const GO_UNIX_TO_INTERNAL: i64 = 62_135_596_800;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct NakamaLegacyVerifyLimits {
    max_token_bytes: usize,
    header_json: NakamaLegacyDecodeLimits,
    claims_json: NakamaLegacyDecodeLimits,
}

impl NakamaLegacyVerifyLimits {
    pub fn new(
        max_token_bytes: usize,
        header_json: NakamaLegacyDecodeLimits,
        claims_json: NakamaLegacyDecodeLimits,
    ) -> Result<Self, NakamaLegacyVerifyError> {
        if !(3..=MAX_LOCAL_TOKEN_BYTES).contains(&max_token_bytes) {
            return Err(NakamaLegacyVerifyError::InvalidLimits);
        }
        Ok(Self {
            max_token_bytes,
            header_json,
            claims_json,
        })
    }

    #[must_use]
    pub const fn max_token_bytes(self) -> usize {
        self.max_token_bytes
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum NakamaLegacyVerifyError {
    InvalidLimits,
    TokenLimit { limit: usize },
    TokenSegments,
    SigningInputLimit,
    Header(NakamaLegacyHeaderError),
    PayloadTransport(NakamaLegacyHeaderError),
    SignatureTransport(NakamaLegacyHeaderError),
    SignatureLength,
    Provider(ProviderError),
    SignatureRejected,
    Claims(NakamaLegacyDecodeError),
    Expired,
}

impl fmt::Display for NakamaLegacyVerifyError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidLimits => f.write_str("invalid legacy verifier resource limits"),
            Self::TokenLimit { limit } => write!(f, "legacy token exceeds {limit} bytes"),
            Self::TokenSegments => f.write_str("legacy token requires three segments"),
            Self::SigningInputLimit => f.write_str("legacy signing input exceeds provider budget"),
            Self::Header(e) => write!(f, "legacy header: {e}"),
            Self::PayloadTransport(e) => write!(f, "legacy payload transport: {e}"),
            Self::SignatureTransport(e) => write!(f, "legacy signature transport: {e}"),
            Self::SignatureLength => f.write_str("legacy signature is not 32 bytes"),
            Self::Provider(e) => write!(f, "legacy MAC provider: {e}"),
            Self::SignatureRejected => f.write_str("legacy signature rejected"),
            Self::Claims(e) => write!(f, "legacy claims: {e}"),
            Self::Expired => f.write_str("legacy token expired"),
        }
    }
}
impl std::error::Error for NakamaLegacyVerifyError {}

/// MAC and expiration verified at the supplied time, without business authority.
/// Private ownership prevents callers from changing the authenticated claims.
pub struct NakamaLegacyVerifiedClaims {
    decoded: NakamaLegacyDecodedClaims,
    kind: NakamaLegacyKeyKind,
    verified_at_unix_seconds: i64,
}
impl NakamaLegacyVerifiedClaims {
    #[must_use]
    pub fn claims(&self) -> &NakamaLegacyRawClaims {
        &self.decoded.claims
    }
    #[must_use]
    pub fn raw_payload(&self) -> &[u8] {
        self.decoded.raw_payload()
    }
    #[must_use]
    pub const fn key_kind(&self) -> NakamaLegacyKeyKind {
        self.kind
    }
    #[must_use]
    pub const fn verified_at_unix_seconds(&self) -> i64 {
        self.verified_at_unix_seconds
    }
}
impl fmt::Debug for NakamaLegacyVerifiedClaims {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("NakamaLegacyVerifiedClaims")
            .field("payload_bytes", &self.decoded.raw_payload().len())
            .field("key_kind", &self.kind)
            .field("verified_at_unix_seconds", &self.verified_at_unix_seconds)
            .finish()
    }
}

pub struct NakamaLegacyVerifier<'a> {
    provider: &'a dyn Hs256Provider,
    limits: NakamaLegacyVerifyLimits,
}
impl fmt::Debug for NakamaLegacyVerifier<'_> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("NakamaLegacyVerifier")
            .field("provider", &"<configured>")
            .field("limits", &self.limits)
            .finish()
    }
}
impl<'a> NakamaLegacyVerifier<'a> {
    #[must_use]
    pub fn new(provider: &'a dyn Hs256Provider, limits: NakamaLegacyVerifyLimits) -> Self {
        Self { provider, limits }
    }

    /// Endpoint code selects purpose; ignored kid/typ never select a key.
    pub fn verify(
        &self,
        token: &[u8],
        kind: NakamaLegacyKeyKind,
        now_unix_seconds: i64,
    ) -> Result<NakamaLegacyVerifiedClaims, NakamaLegacyVerifyError> {
        use NakamaLegacyVerifyError as E;
        if token.len() > self.limits.max_token_bytes {
            return Err(E::TokenLimit {
                limit: self.limits.max_token_bytes,
            });
        }
        let first = token
            .iter()
            .position(|b| *b == b'.')
            .ok_or(E::TokenSegments)?;
        let rest = &token[first + 1..];
        let second = first
            + 1
            + rest
                .iter()
                .position(|b| *b == b'.')
                .ok_or(E::TokenSegments)?;
        if token[second + 1..].contains(&b'.') {
            return Err(E::TokenSegments);
        }
        if second > MAX_SIGNING_INPUT_BYTES {
            return Err(E::SigningInputLimit);
        }
        let header_transport = NakamaLegacyRawUrlLimits::new(
            self.limits.max_token_bytes,
            self.limits.header_json.max_payload_bytes(),
        )
        .map_err(E::Header)?;
        decode_and_validate_nakama_legacy_hs256_header(
            &token[..first],
            header_transport,
            self.limits.header_json,
        )
        .map_err(E::Header)?;
        let signature_transport =
            NakamaLegacyRawUrlLimits::new(MAX_SIGNATURE_ENCODED_BYTES, SIGNATURE_BYTES)
                .map_err(E::SignatureTransport)?;
        let signature =
            decode_nakama_legacy_raw_url_segment(&token[second + 1..], signature_transport)
                .map_err(E::SignatureTransport)?;
        if signature.len() != SIGNATURE_BYTES {
            return Err(E::SignatureLength);
        }
        let decision = verify_exact(
            self.provider,
            &kind.key_reference(),
            &token[..second],
            &signature,
        )
        .map_err(E::Provider)?;
        if decision != VerificationDecision::Accepted {
            return Err(E::SignatureRejected);
        }
        let payload_transport = NakamaLegacyRawUrlLimits::new(
            self.limits.max_token_bytes,
            self.limits.claims_json.max_payload_bytes(),
        )
        .map_err(E::PayloadTransport)?;
        let payload =
            decode_nakama_legacy_raw_url_segment(&token[first + 1..second], payload_transport)
                .map_err(E::PayloadTransport)?;
        let decoded =
            decode_nakama_legacy_claims(&payload, self.limits.claims_json).map_err(E::Claims)?;
        if !go_default_expiration_valid(now_unix_seconds, decoded.claims.expires_at) {
            return Err(E::Expired);
        }
        Ok(NakamaLegacyVerifiedClaims {
            decoded,
            kind,
            verified_at_unix_seconds: now_unix_seconds,
        })
    }
}

// Both values have no monotonic time component. Expiry has nanoseconds zero;
// equal seconds therefore never satisfy Before, regardless of now's fraction.
fn go_default_expiration_valid(now: i64, expires: i64) -> bool {
    now.wrapping_add(GO_UNIX_TO_INTERNAL) < expires.wrapping_add(GO_UNIX_TO_INTERNAL)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::base64url;
    use trnm_token_crypto_provider::{NakamaLegacyHs256Provider, NakamaLegacyKeyLimits};

    fn provider() -> NakamaLegacyHs256Provider {
        NakamaLegacyHs256Provider::new(
            b"defaultencryptionkey",
            b"defaultrefreshencryptionkey",
            NakamaLegacyKeyLimits::new(4096).unwrap(),
        )
        .unwrap()
    }
    fn limits() -> NakamaLegacyVerifyLimits {
        let json = NakamaLegacyDecodeLimits::new(32768, 32, 1024, 98304).unwrap();
        NakamaLegacyVerifyLimits::new(MAX_LOCAL_TOKEN_BYTES, json, json).unwrap()
    }
    fn sign_segments(p: &dyn Hs256Provider, h: &str, b: &str, k: NakamaLegacyKeyKind) -> String {
        let input = format!("{h}.{b}");
        let tag = p.sign(&k.key_reference(), input.as_bytes()).unwrap();
        format!("{input}.{}", base64url::encode(tag.as_bytes()))
    }
    fn token(p: &dyn Hs256Provider, h: &[u8], b: &[u8], k: NakamaLegacyKeyKind) -> String {
        sign_segments(p, &base64url::encode(h), &base64url::encode(b), k)
    }
    const HEADER: &[u8] = br#"{"alg":"HS256"}"#;
    const CLAIMS: &[u8] = br#"{"tid":"private-tid","uid":"raw-nonuuid","usn":"private-user","exp":101,"iat":999999999,"vrs":{"private-var":"private-value"}}"#;

    #[test]
    fn verified_raw_claims_do_not_require_uuid_or_family() {
        let p = provider();
        let v = NakamaLegacyVerifier::new(&p, limits());
        let t = token(&p, HEADER, CLAIMS, NakamaLegacyKeyKind::Access);
        let c = v
            .verify(t.as_bytes(), NakamaLegacyKeyKind::Access, 100)
            .unwrap();
        assert_eq!(c.claims().user_id, "raw-nonuuid");
        assert_eq!(c.claims().issued_at, 999999999);
        assert_eq!(c.raw_payload(), CLAIMS);
        assert_eq!(c.key_kind(), NakamaLegacyKeyKind::Access);
        assert_eq!(c.verified_at_unix_seconds(), 100);
    }
    #[test]
    fn endpoint_purpose_never_falls_back() {
        let p = provider();
        let v = NakamaLegacyVerifier::new(&p, limits());
        for kind in [NakamaLegacyKeyKind::Access, NakamaLegacyKeyKind::Refresh] {
            let t = token(
                &p,
                br#"{"alg":"HS256","kid":"ignored","typ":null}"#,
                CLAIMS,
                kind,
            );
            assert!(v.verify(t.as_bytes(), kind, 100).is_ok());
            let other = if kind == NakamaLegacyKeyKind::Access {
                NakamaLegacyKeyKind::Refresh
            } else {
                NakamaLegacyKeyKind::Access
            };
            assert_eq!(
                v.verify(t.as_bytes(), other, 100).unwrap_err(),
                NakamaLegacyVerifyError::SignatureRejected
            );
        }
    }
    #[test]
    fn original_crlf_segments_are_signed_without_reencoding() {
        let p = provider();
        let v = NakamaLegacyVerifier::new(&p, limits());
        let h = format!("\r{}\n", base64url::encode(HEADER));
        let b = format!("{}\r\n", base64url::encode(CLAIMS));
        let t = sign_segments(&p, &h, &b, NakamaLegacyKeyKind::Access);
        assert!(v
            .verify(t.as_bytes(), NakamaLegacyKeyKind::Access, 100)
            .is_ok());
        let stripped = t.replace(['\r', '\n'], "");
        assert_eq!(
            v.verify(stripped.as_bytes(), NakamaLegacyKeyKind::Access, 100)
                .unwrap_err(),
            NakamaLegacyVerifyError::SignatureRejected
        );
    }
    #[test]
    fn noncanonical_signature_low_bits_are_accepted() {
        let p = provider();
        let v = NakamaLegacyVerifier::new(&p, limits());
        let mut t = token(&p, HEADER, CLAIMS, NakamaLegacyKeyKind::Access).into_bytes();
        const A: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
        let i = A.iter().position(|x| *x == *t.last().unwrap()).unwrap();
        assert_eq!(i & 3, 0);
        *t.last_mut().unwrap() = A[i | 1];
        t.extend_from_slice(b"\r\n");
        assert!(v.verify(&t, NakamaLegacyKeyKind::Access, 100).is_ok());
    }
    #[test]
    fn authenticated_payload_still_requires_valid_claim_types() {
        let p = provider();
        let v = NakamaLegacyVerifier::new(&p, limits());
        let t = token(&p, HEADER, br#"{"exp":"101"}"#, NakamaLegacyKeyKind::Access);
        assert!(matches!(
            v.verify(t.as_bytes(), NakamaLegacyKeyKind::Access, 100),
            Err(NakamaLegacyVerifyError::Claims(_))
        ));
    }
    #[test]
    fn wrong_mac_precedes_untrusted_claim_semantics() {
        let p = provider();
        let v = NakamaLegacyVerifier::new(&p, limits());
        let t = token(
            &p,
            HEADER,
            br#"{"exp":"101"}"#,
            NakamaLegacyKeyKind::Refresh,
        );
        assert_eq!(
            v.verify(t.as_bytes(), NakamaLegacyKeyKind::Access, 100)
                .unwrap_err(),
            NakamaLegacyVerifyError::SignatureRejected
        );
    }
    #[test]
    fn duplicate_claims_and_zero_expiration_follow_source_decoder() {
        let p = provider();
        let v = NakamaLegacyVerifier::new(&p, limits());
        let t = token(
            &p,
            HEADER,
            br#"{"exp":101,"EXP":null,"tid":"a","TID":"b"}"#,
            NakamaLegacyKeyKind::Access,
        );
        assert_eq!(
            v.verify(t.as_bytes(), NakamaLegacyKeyKind::Access, 100)
                .unwrap()
                .claims()
                .token_id,
            "b"
        );
        for body in [b"null".as_slice(), b"{}", br#"{"exp":0}"#] {
            let t = token(&p, HEADER, body, NakamaLegacyKeyKind::Access);
            assert_eq!(
                v.verify(t.as_bytes(), NakamaLegacyKeyKind::Access, 0)
                    .unwrap_err(),
                NakamaLegacyVerifyError::Expired
            );
            assert!(v
                .verify(t.as_bytes(), NakamaLegacyKeyKind::Access, -1)
                .is_ok());
        }
    }
    #[test]
    fn expiration_equal_past_and_future_are_distinct() {
        assert!(!go_default_expiration_valid(101, 101));
        assert!(!go_default_expiration_valid(102, 101));
        assert!(go_default_expiration_valid(100, 101));
    }
    #[test]
    fn expiration_go_internal_ext_wrap_is_preserved() {
        assert!(!go_default_expiration_valid(100, i64::MAX));
        let boundary = i64::MAX - GO_UNIX_TO_INTERNAL;
        assert!(go_default_expiration_valid(boundary - 1, boundary));
        assert!(!go_default_expiration_valid(boundary, boundary + 1));
        assert!(go_default_expiration_valid(boundary + 1, boundary));
        assert!(!go_default_expiration_valid(i64::MIN, i64::MIN));
    }
    #[test]
    fn invalid_header_algorithm_and_map_overflow_reject() {
        let p = provider();
        let v = NakamaLegacyVerifier::new(&p, limits());
        for h in [
            br#"{"alg":"HS256","alg":null}"#.as_slice(),
            br#"{"alg":"hs256"}"#,
            br#"{"alg":"HS256","ignored":1e999}"#,
        ] {
            let t = token(&p, h, CLAIMS, NakamaLegacyKeyKind::Access);
            assert!(matches!(
                v.verify(t.as_bytes(), NakamaLegacyKeyKind::Access, 100),
                Err(NakamaLegacyVerifyError::Header(_))
            ));
        }
    }
    #[test]
    fn segment_and_signature_shape_reject_before_authentication() {
        let p = provider();
        let v = NakamaLegacyVerifier::new(&p, limits());
        for t in [b"".as_slice(), b"a", b"a.b", b"a.b.c.d"] {
            assert_eq!(
                v.verify(t, NakamaLegacyKeyKind::Access, 100).unwrap_err(),
                NakamaLegacyVerifyError::TokenSegments
            );
        }
        let h = base64url::encode(HEADER);
        let b = base64url::encode(CLAIMS);
        assert_eq!(
            v.verify(
                format!("{h}.{b}.").as_bytes(),
                NakamaLegacyKeyKind::Access,
                100
            )
            .unwrap_err(),
            NakamaLegacyVerifyError::SignatureLength
        );
        assert!(matches!(
            v.verify(
                format!("{h}.{b}.AA=").as_bytes(),
                NakamaLegacyKeyKind::Access,
                100
            ),
            Err(NakamaLegacyVerifyError::SignatureTransport(_))
        ));
    }
    #[test]
    fn explicit_resource_caps_and_provider_budget_reject() {
        let p = provider();
        let l = limits();
        assert!(NakamaLegacyVerifyLimits::new(2, l.header_json, l.claims_json).is_err());
        assert!(NakamaLegacyVerifyLimits::new(
            MAX_LOCAL_TOKEN_BYTES + 1,
            l.header_json,
            l.claims_json
        )
        .is_err());
        let small = NakamaLegacyVerifier::new(
            &p,
            NakamaLegacyVerifyLimits::new(3, l.header_json, l.claims_json).unwrap(),
        );
        assert!(matches!(
            small.verify(b"a.b.c", NakamaLegacyKeyKind::Access, 100),
            Err(NakamaLegacyVerifyError::TokenLimit { limit: 3 })
        ));
        let v = NakamaLegacyVerifier::new(&p, l);
        let t = format!("{}.b.c", "A".repeat(MAX_SIGNING_INPUT_BYTES));
        assert_eq!(
            v.verify(t.as_bytes(), NakamaLegacyKeyKind::Access, 100)
                .unwrap_err(),
            NakamaLegacyVerifyError::SigningInputLimit
        );
    }
    #[test]
    fn debug_and_errors_do_not_expose_authenticated_secrets() {
        let p = provider();
        let v = NakamaLegacyVerifier::new(&p, limits());
        let t = token(&p, HEADER, CLAIMS, NakamaLegacyKeyKind::Access);
        let c = v
            .verify(t.as_bytes(), NakamaLegacyKeyKind::Access, 100)
            .unwrap();
        let text = format!("{v:?} {c:?}");
        for s in [
            "private-tid",
            "private-user",
            "private-value",
            "defaultencryptionkey",
            t.as_str(),
        ] {
            assert!(!text.contains(s));
        }
    }
}
