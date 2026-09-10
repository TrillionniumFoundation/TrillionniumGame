use core::fmt;
use std::collections::BTreeMap;
use std::sync::RwLock;

use hmac::{Hmac, KeyInit, Mac};
use sha2::Sha256;
use subtle::ConstantTimeEq;
use zeroize::Zeroize;

use super::{
    validate_signing_input, Hs256Provider, KeyDomain, KeyReference, ProviderError, Signature32,
    VerificationDecision, SIGNATURE_BYTES,
};

type HmacSha256 = Hmac<Sha256>;
const MINIMUM_KEY_BYTES: usize = 32;
const MAXIMUM_KEY_BYTES: usize = 4_096;

#[derive(Eq, PartialEq)]
pub struct SecretKeyMaterial(Vec<u8>);

impl SecretKeyMaterial {
    pub fn new(value: impl Into<Vec<u8>>) -> Result<Self, ProviderError> {
        let value = value.into();
        if !(MINIMUM_KEY_BYTES..=MAXIMUM_KEY_BYTES).contains(&value.len()) {
            return Err(ProviderError::InvalidKeyMaterial);
        }
        Ok(Self(value))
    }

    fn expose(&self) -> &[u8] {
        &self.0
    }
}

impl fmt::Debug for SecretKeyMaterial {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("SecretKeyMaterial")
            .field("bytes", &"<redacted>")
            .field("length", &self.0.len())
            .finish()
    }
}

impl Drop for SecretKeyMaterial {
    fn drop(&mut self) {
        self.0.zeroize();
    }
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
struct SoftwareKeyId {
    domain: KeyDomain,
    handle: String,
    epoch: Option<u32>,
}

impl From<&KeyReference> for SoftwareKeyId {
    fn from(value: &KeyReference) -> Self {
        Self {
            domain: value.domain,
            handle: value.handle.as_str().to_owned(),
            epoch: value.epoch,
        }
    }
}

#[derive(Default)]
pub struct SoftwareHs256Provider {
    keys: RwLock<BTreeMap<SoftwareKeyId, SecretKeyMaterial>>,
}

impl SoftwareHs256Provider {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    pub fn insert_key(
        &self,
        key: &KeyReference,
        material: impl Into<Vec<u8>>,
    ) -> Result<(), ProviderError> {
        let material = SecretKeyMaterial::new(material)?;
        self.keys
            .write()
            .map_err(|_| ProviderError::Internal)?
            .insert(SoftwareKeyId::from(key), material);
        Ok(())
    }

    pub fn remove_key(&self, key: &KeyReference) -> Result<bool, ProviderError> {
        Ok(self
            .keys
            .write()
            .map_err(|_| ProviderError::Internal)?
            .remove(&SoftwareKeyId::from(key))
            .is_some())
    }

    #[must_use]
    pub fn key_count(&self) -> usize {
        self.keys.read().map_or(0, |keys| keys.len())
    }

    fn tag(
        &self,
        key: &KeyReference,
        exact_signing_input: &[u8],
    ) -> Result<[u8; SIGNATURE_BYTES], ProviderError> {
        validate_signing_input(exact_signing_input)?;
        let keys = self.keys.read().map_err(|_| ProviderError::Internal)?;
        let material = keys
            .get(&SoftwareKeyId::from(key))
            .ok_or(ProviderError::KeyUnavailable)?;
        let mut mac = HmacSha256::new_from_slice(material.expose())
            .map_err(|_| ProviderError::InvalidKeyMaterial)?;
        mac.update(exact_signing_input);
        let output = mac.finalize().into_bytes();
        let mut result = [0_u8; SIGNATURE_BYTES];
        result.copy_from_slice(&output);
        Ok(result)
    }
}

impl fmt::Debug for SoftwareHs256Provider {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("SoftwareHs256Provider")
            .field("key_count", &self.key_count())
            .field("key_material", &"<redacted>")
            .finish()
    }
}

impl Hs256Provider for SoftwareHs256Provider {
    fn sign(
        &self,
        key: &KeyReference,
        exact_signing_input: &[u8],
    ) -> Result<Signature32, ProviderError> {
        self.tag(key, exact_signing_input).map(Signature32::new)
    }

    fn verify(
        &self,
        key: &KeyReference,
        exact_signing_input: &[u8],
        signature: &Signature32,
    ) -> Result<VerificationDecision, ProviderError> {
        let expected = self.tag(key, exact_signing_input)?;
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
    use crate::{KeyHandle, KeyReference};

    fn key(domain: KeyDomain, epoch: u32) -> KeyReference {
        KeyReference::new(
            domain,
            KeyHandle::new(format!("software://test/{epoch}")).unwrap(),
            Some(epoch),
        )
        .unwrap()
    }

    #[test]
    fn secret_material_uses_the_reviewed_zeroize_primitive() {
        let mut material = SecretKeyMaterial::new(vec![0x5a; 32]).unwrap();
        material.0.zeroize();
        assert!(material.0.is_empty());
    }

    #[test]
    fn sign_verify_and_domain_separation_are_explicit() {
        let provider = SoftwareHs256Provider::new();
        let access = key(KeyDomain::AccessToken, 7);
        let socket = key(KeyDomain::Socket, 7);
        provider.insert_key(&access, vec![0x11; 32]).unwrap();
        provider.insert_key(&socket, vec![0x22; 32]).unwrap();
        let input = b"header.payload";
        let access_tag = provider.sign(&access, input).unwrap();
        assert_eq!(
            provider.verify(&access, input, &access_tag).unwrap(),
            VerificationDecision::Accepted
        );
        assert_eq!(
            provider.verify(&socket, input, &access_tag).unwrap(),
            VerificationDecision::Rejected
        );
    }

    #[test]
    fn missing_removed_and_invalid_keys_fail_closed() {
        let provider = SoftwareHs256Provider::new();
        let access = key(KeyDomain::AccessToken, 9);
        assert_eq!(
            provider.sign(&access, b"header.payload").unwrap_err(),
            ProviderError::KeyUnavailable
        );
        assert_eq!(
            provider.insert_key(&access, vec![0; 15]).unwrap_err(),
            ProviderError::InvalidKeyMaterial
        );
        provider.insert_key(&access, vec![0x33; 32]).unwrap();
        assert!(provider.remove_key(&access).unwrap());
        assert_eq!(provider.key_count(), 0);
    }

    #[test]
    fn debug_output_never_contains_key_material() {
        let provider = SoftwareHs256Provider::new();
        let access = key(KeyDomain::AccessToken, 11);
        provider
            .insert_key(&access, b"0123456789abcdef0123456789abcdef".to_vec())
            .unwrap();
        let rendered = format!("{provider:?}");
        assert!(rendered.contains("<redacted>"));
        assert!(!rendered.contains("0123456789abcdef"));
    }
}
