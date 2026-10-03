#![forbid(unsafe_code)]
#![deny(missing_debug_implementations)]

//! HS256 JWT adapters with separate canonical and Nakama legacy profiles.
//!
//! Canonical verification parses only the bounded header needed for key selection,
//! authenticates the exact encoded header and payload, then parses claims.
//! Canonical legacy and key-epoch routes remain separate; malformed or unknown
//! epochs never fall back to a legacy key.
//!
//! The Nakama legacy payload encoder and raw six-field decoder are bounded codec
//! APIs. Decoded raw claims carry no authentication, principal or session trust.
//! The separate legacy issuer uses an explicitly supplied cryptographic provider.

pub mod base64url;
pub mod json;
#[path = "jwt/mod.rs"]
mod jwt;
mod nakama_legacy;
mod nakama_legacy_decode;
mod nakama_legacy_payload;
mod nakama_legacy_verify;
mod sha256;

pub use jwt::{
    issue_epoch, issue_legacy, verify, ClaimMapping, JwtError, KeyRing, SecretKey, TokenRoute,
    VerificationProfile, VerifiedPrincipal, VerifiedToken, EPOCH_KEY_ID_PREFIX,
};

pub use nakama_legacy::{
    NakamaLegacyIssueError, NakamaLegacyIssueLimits, NakamaLegacyIssuer, NakamaLegacyToken,
    NakamaLegacyTokenPair,
};
pub use nakama_legacy_decode::{
    decode_and_validate_nakama_legacy_hs256_header, decode_nakama_legacy_claims,
    decode_nakama_legacy_raw_url_segment, validate_nakama_legacy_hs256_header,
    NakamaLegacyClaimOccurrence, NakamaLegacyDecodeError, NakamaLegacyDecodeLimits,
    NakamaLegacyDecodedClaims, NakamaLegacyField, NakamaLegacyHeaderError, NakamaLegacyHs256Header,
    NakamaLegacyRawClaims, NakamaLegacyRawUrlLimits, NakamaLegacySyntaxReason,
};
pub use nakama_legacy_payload::{
    encode_nakama_legacy_payload, NakamaLegacyClaims, NakamaLegacyEncodeError,
};
pub use nakama_legacy_verify::{
    NakamaLegacyVerifiedClaims, NakamaLegacyVerifier, NakamaLegacyVerifyError,
    NakamaLegacyVerifyLimits,
};
pub use trnm_token_crypto_provider::NakamaLegacyKeyKind;

#[must_use]
pub fn sha256_digest(input: &[u8]) -> [u8; 32] {
    sha256::digest(input)
}
