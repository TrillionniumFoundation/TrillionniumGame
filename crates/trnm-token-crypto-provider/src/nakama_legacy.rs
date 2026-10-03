//! Explicit fixed-key MAC profile for Nakama's legacy session tokens.
//!
//! Nakama's session configuration requires nonempty, distinct access and
//! refresh keys. Its public defaults have 20 and 27 bytes. This separately
//! selected profile permits those keys; `SecretKeyMaterial` and the existing
//! software provider retain their independent minimum-length policy.
//!
//! The caller supplies a key-byte resource budget. The 4096-byte ceiling is a
//! local candidate bound, not an upstream configuration acceptance rule. This
//! provider authenticates exact bytes only. It does not parse or validate JWT
//! headers, claims, expiration, users, or session-cache state.

use core::fmt;
use hmac::{Hmac, KeyInit, Mac};
use sha2::Sha256;
use subtle::ConstantTimeEq;
use zeroize::Zeroizing;

use crate::{
    validate_signing_input, Hs256Provider, KeyDomain, KeyHandle, KeyReference, ProviderError,
    Signature32, VerificationDecision, SIGNATURE_BYTES,
};

type HmacSha256 = Hmac<Sha256>;
const MAX_LOCAL_KEY_BYTES: usize = 4096;
const ACCESS_HANDLE: &str = "nakama-legacy/access";
const REFRESH_HANDLE: &str = "nakama-legacy/refresh";

/// The endpoint selects the key kind; a JWT header cannot select it.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum NakamaLegacyKeyKind {
    Access,
    Refresh,
}

impl NakamaLegacyKeyKind {
    #[must_use]
    pub fn key_reference(self) -> KeyReference {
        let (domain, handle) = match self {
            Self::Access => (KeyDomain::AccessToken, ACCESS_HANDLE),
            Self::Refresh => (KeyDomain::RefreshToken, REFRESH_HANDLE),
        };
        KeyReference {
            domain,
            handle: KeyHandle(handle.to_owned()),
            epoch: None,
        }
    }
}

/// Caller-selected resource policy, separate from upstream key validation.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct NakamaLegacyKeyLimits {
    max_key_bytes: usize,
}

impl NakamaLegacyKeyLimits {
    pub fn new(max_key_bytes: usize) -> Result<Self, ProviderError> {
        if !(1..=MAX_LOCAL_KEY_BYTES).contains(&max_key_bytes) {
            return Err(ProviderError::InvalidKeyMaterial);
        }
        Ok(Self { max_key_bytes })
    }

    #[must_use]
    pub const fn max_key_bytes(self) -> usize {
        self.max_key_bytes
    }
}

/// Two fixed legacy keys using the existing RustCrypto primitives.
///
/// Construction takes ownership. Both buffers are zeroized on rejection and
/// destruction. There is no key registry, epoch fallback, or shared-domain
/// lookup in this profile.
pub struct NakamaLegacyHs256Provider {
    access: Zeroizing<Vec<u8>>,
    refresh: Zeroizing<Vec<u8>>,
}

impl NakamaLegacyHs256Provider {
    pub fn new(
        access: impl Into<Vec<u8>>,
        refresh: impl Into<Vec<u8>>,
        limits: NakamaLegacyKeyLimits,
    ) -> Result<Self, ProviderError> {
        let access = Zeroizing::new(access.into());
        let refresh = Zeroizing::new(refresh.into());
        if access.is_empty()
            || refresh.is_empty()
            || access.len() > limits.max_key_bytes
            || refresh.len() > limits.max_key_bytes
            || bool::from(access.as_slice().ct_eq(refresh.as_slice()))
        {
            return Err(ProviderError::InvalidKeyMaterial);
        }
        Ok(Self { access, refresh })
    }

    fn tag(
        &self,
        key: &KeyReference,
        input: &[u8],
    ) -> Result<[u8; SIGNATURE_BYTES], ProviderError> {
        validate_signing_input(input)?;
        let material = match (key.domain, key.handle.as_str(), key.epoch) {
            (KeyDomain::AccessToken, ACCESS_HANDLE, None) => self.access.as_slice(),
            (KeyDomain::RefreshToken, REFRESH_HANDLE, None) => self.refresh.as_slice(),
            _ => return Err(ProviderError::PermissionDenied),
        };
        let mut mac =
            HmacSha256::new_from_slice(material).map_err(|_| ProviderError::InvalidKeyMaterial)?;
        mac.update(input);
        let output = mac.finalize().into_bytes();
        let mut result = [0; SIGNATURE_BYTES];
        result.copy_from_slice(&output);
        Ok(result)
    }
}

impl fmt::Debug for NakamaLegacyHs256Provider {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("NakamaLegacyHs256Provider")
            .field("access_bytes", &self.access.len())
            .field("refresh_bytes", &self.refresh.len())
            .field("key_material", &"<redacted>")
            .finish()
    }
}

impl Hs256Provider for NakamaLegacyHs256Provider {
    fn sign(&self, key: &KeyReference, input: &[u8]) -> Result<Signature32, ProviderError> {
        self.tag(key, input).map(Signature32::new)
    }

    fn verify(
        &self,
        key: &KeyReference,
        input: &[u8],
        signature: &Signature32,
    ) -> Result<VerificationDecision, ProviderError> {
        let expected = self.tag(key, input)?;
        Ok(if bool::from(expected.ct_eq(signature.as_bytes())) {
            VerificationDecision::Accepted
        } else {
            VerificationDecision::Rejected
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{SecretKeyMaterial, SoftwareHs256Provider, MAX_SIGNING_INPUT_BYTES};

    fn limits() -> NakamaLegacyKeyLimits {
        NakamaLegacyKeyLimits::new(4096).unwrap()
    }

    fn hex(value: &str) -> [u8; 32] {
        let mut result = [0; 32];
        for (offset, byte) in result.iter_mut().enumerate() {
            *byte = u8::from_str_radix(&value[offset * 2..offset * 2 + 2], 16).unwrap();
        }
        result
    }

    #[test]
    fn legacy_hs256_short_key_matches_rfc4231_case_two() {
        let provider = NakamaLegacyHs256Provider::new(b"Jefe", b"refresh", limits()).unwrap();
        let key = NakamaLegacyKeyKind::Access.key_reference();
        let input = b"what do ya want for nothing?";
        let signature = provider.sign(&key, input).unwrap();
        assert_eq!(
            signature.as_bytes(),
            &hex("5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843")
        );
        assert_eq!(
            provider.verify(&key, input, &signature).unwrap(),
            VerificationDecision::Accepted
        );
        assert_eq!(
            provider.verify(&key, b"changed", &signature).unwrap(),
            VerificationDecision::Rejected
        );
    }

    #[test]
    fn legacy_hs256_long_binary_key_matches_rfc4231_case_six() {
        let provider =
            NakamaLegacyHs256Provider::new(vec![0xaa; 131], vec![0xbb; 132], limits()).unwrap();
        let signature = provider
            .sign(
                &NakamaLegacyKeyKind::Access.key_reference(),
                b"Test Using Larger Than Block-Size Key - Hash Key First",
            )
            .unwrap();
        assert_eq!(
            signature.as_bytes(),
            &hex("60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54")
        );
        // Nonempty configuration bytes are not trimmed or text-normalized.
        assert!(NakamaLegacyHs256Provider::new(b"\0", b" ", limits()).is_ok());
    }

    #[test]
    fn legacy_hs256_defaults_are_distinct_and_do_not_weaken_software_keys() {
        let access = b"defaultencryptionkey";
        let refresh = b"defaultrefreshencryptionkey";
        assert_eq!((access.len(), refresh.len()), (20, 27));
        let provider = NakamaLegacyHs256Provider::new(access, refresh, limits()).unwrap();
        let access_key = NakamaLegacyKeyKind::Access.key_reference();
        let refresh_key = NakamaLegacyKeyKind::Refresh.key_reference();
        let signature = provider.sign(&access_key, b"header.payload").unwrap();
        assert_eq!(
            provider
                .verify(&refresh_key, b"header.payload", &signature)
                .unwrap(),
            VerificationDecision::Rejected
        );
        assert_eq!(
            SecretKeyMaterial::new(access).unwrap_err(),
            ProviderError::InvalidKeyMaterial
        );
        assert_eq!(
            SecretKeyMaterial::new(refresh).unwrap_err(),
            ProviderError::InvalidKeyMaterial
        );
        let strict = SoftwareHs256Provider::new();
        assert_eq!(
            strict.insert_key(&access_key, access).unwrap_err(),
            ProviderError::InvalidKeyMaterial
        );
        assert_eq!(strict.key_count(), 0);
        // Synthetic 19/26-byte keys are legal too; these are not the defaults.
        assert!(NakamaLegacyHs256Provider::new(vec![b'a'; 19], vec![b'r'; 26], limits()).is_ok());
    }

    #[test]
    fn legacy_hs256_rejects_empty_equal_and_over_budget_key_pairs() {
        for (access, refresh) in [
            (b"".as_slice(), b"r".as_slice()),
            (b"a", b""),
            (b"same", b"same"),
        ] {
            assert_eq!(
                NakamaLegacyHs256Provider::new(access, refresh, limits()).unwrap_err(),
                ProviderError::InvalidKeyMaterial
            );
        }
        let limit = NakamaLegacyKeyLimits::new(1).unwrap();
        assert!(NakamaLegacyHs256Provider::new(b"a", b"b", limit).is_ok());
        assert_eq!(
            NakamaLegacyHs256Provider::new(b"ab", b"b", limit).unwrap_err(),
            ProviderError::InvalidKeyMaterial
        );
        assert!(NakamaLegacyKeyLimits::new(0).is_err());
        assert!(NakamaLegacyKeyLimits::new(4097).is_err());
        assert_eq!(limits().max_key_bytes(), 4096);
    }

    #[test]
    fn legacy_hs256_wrong_domain_handle_and_epoch_have_no_fallback() {
        let provider = NakamaLegacyHs256Provider::new(b"access", b"refresh", limits()).unwrap();
        let original = NakamaLegacyKeyKind::Access.key_reference();
        for wrong in [
            KeyReference {
                domain: KeyDomain::RefreshToken,
                ..original.clone()
            },
            KeyReference {
                domain: KeyDomain::Console,
                ..original.clone()
            },
            KeyReference {
                handle: KeyHandle::new("elsewhere").unwrap(),
                ..original.clone()
            },
            KeyReference {
                epoch: Some(1),
                ..original.clone()
            },
            KeyReference {
                epoch: Some(0),
                ..original.clone()
            },
        ] {
            assert_eq!(
                provider.sign(&wrong, b"input").unwrap_err(),
                ProviderError::PermissionDenied
            );
            assert_eq!(
                provider
                    .verify(&wrong, b"input", &Signature32::new([0; 32]))
                    .unwrap_err(),
                ProviderError::PermissionDenied
            );
        }
    }

    #[test]
    fn legacy_hs256_signing_input_bounds_precede_key_selection() {
        let provider = NakamaLegacyHs256Provider::new(b"access", b"refresh", limits()).unwrap();
        let mut wrong = NakamaLegacyKeyKind::Access.key_reference();
        wrong.domain = KeyDomain::Authority;
        assert_eq!(
            provider.sign(&wrong, b"").unwrap_err(),
            ProviderError::SigningInputEmpty
        );
        assert!(matches!(
            provider.sign(&wrong, &vec![0; MAX_SIGNING_INPUT_BYTES + 1]),
            Err(ProviderError::SigningInputTooLarge { .. })
        ));
        assert!(provider
            .sign(
                &NakamaLegacyKeyKind::Access.key_reference(),
                &vec![0; MAX_SIGNING_INPUT_BYTES]
            )
            .is_ok());
    }

    #[test]
    fn legacy_hs256_debug_redacts_owned_material() {
        let provider =
            NakamaLegacyHs256Provider::new(b"private-access", b"private-refresh", limits())
                .unwrap();
        let debug = format!("{provider:?}");
        assert!(!debug.contains("private-access"));
        assert!(!debug.contains("private-refresh"));
        assert!(debug.contains("<redacted>"));
    }
}
