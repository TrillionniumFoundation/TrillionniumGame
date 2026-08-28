#![forbid(unsafe_code)]
#![deny(missing_debug_implementations)]

#[path = "../../trnm-token-jwt-adapter/src/base64url.rs"]
pub mod base64url;
#[path = "../../trnm-token-jwt-adapter/src/json.rs"]
pub mod json;
#[path = "../../trnm-token-jwt-adapter/src/jwt.rs"]
mod jwt;
#[path = "../../trnm-token-jwt-adapter/src/sha256.rs"]
mod sha256;

pub use jwt::{
    issue_epoch, issue_legacy, verify, ClaimMapping, JwtError, KeyRing, SecretKey,
    TokenRoute, VerificationProfile, VerifiedPrincipal, VerifiedToken,
    EPOCH_KEY_ID_PREFIX,
};
