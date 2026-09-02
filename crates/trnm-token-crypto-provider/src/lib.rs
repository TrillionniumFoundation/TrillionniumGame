#![forbid(unsafe_code)]
#![deny(missing_debug_implementations)]

//! Opaque cryptographic provider contract for JWT compatibility.
//!
//! This crate contains no SHA, HMAC, constant-time or key-storage
//! implementation. Production providers must be supplied by separately
//! reviewed software or remote-key adapters.

mod lifecycle;

use core::fmt;

pub use lifecycle::{
    DomainLifecycleStatus, EpochWindow, KeyEpochRegistry, LifecycleAction,
    LifecycleAuditEvent, LifecycleError, LifecycleHealth, LifecycleMutation, ALL_KEY_DOMAINS,
    MAX_EPOCHS_PER_DOMAIN, MAX_LIFECYCLE_AUDIT_EVENTS, MAX_VERIFICATION_EPOCHS_AT_ONCE,
};

pub const MAX_SIGNING_INPUT_BYTES: usize = 32 * 1024;
pub const SIGNATURE_BYTES: usize = 32;

#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub enum KeyDomain {
    AccessToken,
    RefreshToken,
    Console,
    RuntimeHttp,
    Socket,
    Authority,
}

#[derive(Clone, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct KeyHandle(String);

impl KeyHandle {
    pub fn new(value: impl Into<String>) -> Result<Self, ProviderError> {
        let value = value.into();
        if value.is_empty() || value.len() > 256 {
            return Err(ProviderError::InvalidKeyHandle);
        }
        if !value.bytes().all(|byte| {
            byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b':' | b'-' | b'/')
        }) {
            return Err(ProviderError::InvalidKeyHandle);
        }
        Ok(Self(value))
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for KeyHandle {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("KeyHandle(<redacted-key-handle>)")
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct KeyReference {
    pub domain: KeyDomain,
    pub handle: KeyHandle,
    pub epoch: Option<u32>,
}

impl KeyReference {
    pub fn new(
        domain: KeyDomain,
        handle: KeyHandle,
        epoch: Option<u32>,
    ) -> Result<Self, ProviderError> {
        if epoch == Some(0) {
            return Err(ProviderError::InvalidKeyEpoch);
        }
        Ok(Self {
            domain,
            handle,
            epoch,
        })
    }
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub struct Signature32([u8; SIGNATURE_BYTES]);

impl Signature32 {
    #[must_use]
    pub const fn new(value: [u8; SIGNATURE_BYTES]) -> Self {
        Self(value)
    }

    #[must_use]
    pub const fn as_bytes(&self) -> &[u8; SIGNATURE_BYTES] {
        &self.0
    }
}

impl fmt::Debug for Signature32 {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("Signature32")
            .field("bytes", &"<redacted-authenticator>")
            .finish()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum VerificationDecision {
    Accepted,
    Rejected,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ProviderError {
    InvalidKeyHandle,
    InvalidKeyEpoch,
    SigningInputEmpty,
    SigningInputTooLarge { actual: usize },
    KeyUnavailable,
    PermissionDenied,
    RateLimited,
    DeadlineExceeded,
    Cancelled,
    Internal,
}

impl fmt::Display for ProviderError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidKeyHandle => formatter.write_str("invalid key handle"),
            Self::InvalidKeyEpoch => formatter.write_str("key epoch must be greater than zero"),
            Self::SigningInputEmpty => formatter.write_str("JWT signing input must not be empty"),
            Self::SigningInputTooLarge { actual } => write!(
                formatter,
                "JWT signing input {actual} bytes exceeds {MAX_SIGNING_INPUT_BYTES}"
            ),
            Self::KeyUnavailable => formatter.write_str("cryptographic key is unavailable"),
            Self::PermissionDenied => {
                formatter.write_str("cryptographic operation is not permitted")
            }
            Self::RateLimited => formatter.write_str("cryptographic provider is rate limited"),
            Self::DeadlineExceeded => {
                formatter.write_str("cryptographic provider deadline exceeded")
            }
            Self::Cancelled => formatter.write_str("cryptographic provider operation cancelled"),
            Self::Internal => formatter.write_str("cryptographic provider internal failure"),
        }
    }
}

impl std::error::Error for ProviderError {}

pub trait Hs256Provider: fmt::Debug + Send + Sync {
    fn sign(
        &self,
        key: &KeyReference,
        exact_signing_input: &[u8],
    ) -> Result<Signature32, ProviderError>;

    fn verify(
        &self,
        key: &KeyReference,
        exact_signing_input: &[u8],
        signature: &Signature32,
    ) -> Result<VerificationDecision, ProviderError>;
}

pub fn validate_signing_input(value: &[u8]) -> Result<(), ProviderError> {
    if value.is_empty() {
        return Err(ProviderError::SigningInputEmpty);
    }
    if value.len() > MAX_SIGNING_INPUT_BYTES {
        return Err(ProviderError::SigningInputTooLarge {
            actual: value.len(),
        });
    }
    Ok(())
}

pub fn sign_exact(
    provider: &dyn Hs256Provider,
    key: &KeyReference,
    exact_signing_input: &[u8],
) -> Result<Signature32, ProviderError> {
    validate_signing_input(exact_signing_input)?;
    provider.sign(key, exact_signing_input)
}

pub fn verify_exact(
    provider: &dyn Hs256Provider,
    key: &KeyReference,
    exact_signing_input: &[u8],
    signature: &[u8],
) -> Result<VerificationDecision, ProviderError> {
    validate_signing_input(exact_signing_input)?;
    let signature: [u8; SIGNATURE_BYTES] =
        signature.try_into().map_err(|_| ProviderError::Internal)?;
    provider.verify(key, exact_signing_input, &Signature32::new(signature))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    #[derive(Debug, Default)]
    struct RecordingProvider {
        calls: Mutex<Vec<(KeyDomain, String, Vec<u8>)>>,
    }

    impl Hs256Provider for RecordingProvider {
        fn sign(
            &self,
            key: &KeyReference,
            exact_signing_input: &[u8],
        ) -> Result<Signature32, ProviderError> {
            self.calls.lock().unwrap().push((
                key.domain,
                key.handle.as_str().to_owned(),
                exact_signing_input.to_vec(),
            ));
            Ok(Signature32::new([0x5a; SIGNATURE_BYTES]))
        }

        fn verify(
            &self,
            key: &KeyReference,
            exact_signing_input: &[u8],
            signature: &Signature32,
        ) -> Result<VerificationDecision, ProviderError> {
            self.calls.lock().unwrap().push((
                key.domain,
                key.handle.as_str().to_owned(),
                exact_signing_input.to_vec(),
            ));
            Ok(if signature.as_bytes() == &[0x5a; SIGNATURE_BYTES] {
                VerificationDecision::Accepted
            } else {
                VerificationDecision::Rejected
            })
        }
    }

    fn key(domain: KeyDomain) -> KeyReference {
        KeyReference::new(
            domain,
            KeyHandle::new("kms://token/key-1").unwrap(),
            Some(7),
        )
        .unwrap()
    }

    #[test]
    fn key_domains_and_handles_remain_explicit() {
        let access = key(KeyDomain::AccessToken);
        let refresh = key(KeyDomain::RefreshToken);
        assert_ne!(access.domain, refresh.domain);
        assert_eq!(access.epoch, Some(7));
        assert!(KeyHandle::new("contains whitespace").is_err());
        assert!(KeyReference::new(
            KeyDomain::AccessToken,
            KeyHandle::new("key").unwrap(),
            Some(0)
        )
        .is_err());
    }

    #[test]
    fn key_handle_debug_is_always_redacted() {
        let handle = KeyHandle::new("kms://tenant/production-signing-key").unwrap();
        let key = KeyReference::new(KeyDomain::Authority, handle, Some(9)).unwrap();
        let rendered = format!("{key:?}");
        assert!(rendered.contains("<redacted-key-handle>"));
        assert!(!rendered.contains("tenant"));
        assert!(!rendered.contains("production-signing-key"));
    }

    #[test]
    fn exact_signing_input_is_forwarded_without_claim_parsing() {
        let provider = RecordingProvider::default();
        let input = b"encoded-header.encoded-payload";
        let signature = sign_exact(&provider, &key(KeyDomain::AccessToken), input).unwrap();
        assert_eq!(signature.as_bytes(), &[0x5a; SIGNATURE_BYTES]);
        let calls = provider.calls.lock().unwrap();
        assert_eq!(calls.len(), 1);
        assert_eq!(calls[0].2, input);
    }

    #[test]
    fn signature_length_is_rejected_before_provider_verification() {
        let provider = RecordingProvider::default();
        let decision = verify_exact(
            &provider,
            &key(KeyDomain::AccessToken),
            b"header.payload",
            &[0; SIGNATURE_BYTES - 1],
        );
        assert_eq!(decision.unwrap_err(), ProviderError::Internal);
        assert!(provider.calls.lock().unwrap().is_empty());
    }

    #[test]
    fn input_limits_fail_closed() {
        let provider = RecordingProvider::default();
        assert_eq!(
            sign_exact(&provider, &key(KeyDomain::AccessToken), b"").unwrap_err(),
            ProviderError::SigningInputEmpty
        );
        assert!(matches!(
            sign_exact(
                &provider,
                &key(KeyDomain::AccessToken),
                &vec![0; MAX_SIGNING_INPUT_BYTES + 1]
            )
            .unwrap_err(),
            ProviderError::SigningInputTooLarge { .. }
        ));
    }
}
