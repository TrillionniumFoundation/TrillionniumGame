#![forbid(unsafe_code)]
#![deny(missing_debug_implementations)]

//! Strict HS256 JWT compatibility adapter.
//!
//! The adapter intentionally has no unverified-decode API. Verification parses
//! only the bounded header required for key selection, authenticates the exact
//! encoded header and payload, and parses claims only after the HMAC succeeds.
//! Legacy tokens without `kid` and key-epoch tokens are separate routes; a
//! malformed or unknown epoch never falls back to the legacy key.

pub mod base64url;
pub mod json;
#[path = "jwt/mod.rs"]
mod jwt;
mod sha256;

pub use jwt::{
    issue_epoch, issue_legacy, verify, ClaimMapping, JwtError, KeyRing, SecretKey, TokenRoute,
    VerificationProfile, VerifiedPrincipal, VerifiedToken, EPOCH_KEY_ID_PREFIX,
};
