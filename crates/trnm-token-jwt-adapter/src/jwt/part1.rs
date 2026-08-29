use core::fmt;
use std::collections::{BTreeMap, BTreeSet};

use crate::base64url::{self, Base64UrlError};
use crate::json::{self, JsonEncodeError, JsonError, JsonLimits, JsonValue};
use crate::sha256::{constant_time_eq, hmac_sha256};

pub const EPOCH_KEY_ID_PREFIX: &str = "trnm-kep-v1:";
const SIGNATURE_BYTES: usize = 32;
const MINIMUM_KEY_BYTES: usize = 16;
const MAXIMUM_KEY_BYTES: usize = 4_096;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TokenRoute {
    Legacy,
    Epoch(u32),
}

pub struct SecretKey {
    bytes: Vec<u8>,
}

impl SecretKey {
    pub fn new(bytes: impl Into<Vec<u8>>) -> Result<Self, JwtError> {
        let bytes = bytes.into();
        if !(MINIMUM_KEY_BYTES..=MAXIMUM_KEY_BYTES).contains(&bytes.len()) {
            return Err(JwtError::InvalidKeyLength {
                minimum: MINIMUM_KEY_BYTES,
                maximum: MAXIMUM_KEY_BYTES,
                actual: bytes.len(),
            });
        }
        Ok(Self { bytes })
    }

    fn expose(&self) -> &[u8] {
        &self.bytes
    }
}

impl fmt::Debug for SecretKey {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("SecretKey")
            .field("bytes", &"<redacted>")
            .field("length", &self.bytes.len())
            .finish()
    }
}

impl Drop for SecretKey {
    fn drop(&mut self) {
        self.bytes.fill(0);
    }
}

#[derive(Debug, Default)]
pub struct KeyRing {
    legacy: Option<SecretKey>,
    epoch_keys: BTreeMap<u32, SecretKey>,
    active_epoch: Option<u32>,
}

impl KeyRing {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn set_legacy_key(&mut self, key: SecretKey) {
        self.legacy = Some(key);
    }

    pub fn clear_legacy_key(&mut self) {
        self.legacy = None;
    }

    pub fn insert_epoch_key(&mut self, epoch: u32, key: SecretKey) -> Result<(), JwtError> {
        if epoch == 0 {
            return Err(JwtError::InvalidKeyEpoch);
        }
        self.epoch_keys.insert(epoch, key);
        Ok(())
    }

    pub fn remove_epoch_key(&mut self, epoch: u32) {
        self.epoch_keys.remove(&epoch);
        if self.active_epoch == Some(epoch) {
            self.active_epoch = None;
        }
    }

    pub fn set_active_epoch(&mut self, epoch: u32) -> Result<(), JwtError> {
        if !self.epoch_keys.contains_key(&epoch) {
            return Err(JwtError::UnknownKeyEpoch(epoch));
        }
        self.active_epoch = Some(epoch);
        Ok(())
    }

    pub fn active_epoch(&self) -> Option<u32> {
        self.active_epoch
    }

    pub fn verify(
        &self,
        token: &str,
        profile: &VerificationProfile,
        now_unix_seconds: i64,
    ) -> Result<VerifiedToken, JwtError> {
        verify(token, self, profile, now_unix_seconds)
    }

    pub fn issue_legacy(
        &self,
        claims: &JsonValue,
        profile: &VerificationProfile,
    ) -> Result<String, JwtError> {
        let key = self.legacy.as_ref().ok_or(JwtError::LegacyKeyUnavailable)?;
        issue_legacy(claims, key, profile)
    }

    pub fn issue_active_epoch(
        &self,
        claims: &JsonValue,
        profile: &VerificationProfile,
    ) -> Result<String, JwtError> {
        let epoch = self.active_epoch.ok_or(JwtError::ActiveEpochUnavailable)?;
        let key = self
            .epoch_keys
            .get(&epoch)
            .ok_or(JwtError::UnknownKeyEpoch(epoch))?;
        issue_epoch(claims, epoch, key, profile)
    }

    fn key_for_route(&self, route: TokenRoute) -> Result<&SecretKey, JwtError> {
        match route {
            TokenRoute::Legacy => self.legacy.as_ref().ok_or(JwtError::LegacyKeyUnavailable),
            TokenRoute::Epoch(epoch) => self
                .epoch_keys
                .get(&epoch)
                .ok_or(JwtError::UnknownKeyEpoch(epoch)),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ClaimMapping {
    pub subject: String,
    pub username: Option<String>,
    pub variables: Option<String>,
    pub token_id: Option<String>,
    pub key_epoch: String,
}

impl ClaimMapping {
    pub fn standard() -> Self {
        Self {
            subject: "sub".into(),
            username: Some("preferred_username".into()),
            variables: Some("vrs".into()),
            token_id: Some("jti".into()),
            key_epoch: "trnm_kep".into(),
        }
    }

    pub fn uid_legacy() -> Self {
        Self {
            subject: "uid".into(),
            username: Some("usn".into()),
            variables: Some("vrs".into()),
            token_id: Some("tid".into()),
            key_epoch: "trnm_kep".into(),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct VerificationProfile {
    pub max_token_bytes: usize,
    pub max_header_bytes: usize,
    pub max_payload_bytes: usize,
    pub json_limits: JsonLimits,
    pub clock_skew_seconds: i64,
    pub max_lifetime_seconds: Option<u64>,
    pub require_expiration: bool,
    pub require_issued_at: bool,
    pub require_username: bool,
    pub required_issuer: Option<String>,
    pub required_audience: Option<String>,
    pub allow_legacy_without_key_id: bool,
    pub reject_unknown_header_fields: bool,
    pub require_epoch_claim: bool,
    pub max_subject_bytes: usize,
    pub max_username_bytes: usize,
    pub max_token_id_bytes: usize,
    pub max_variables: usize,
    pub max_variable_key_bytes: usize,
    pub max_variable_value_bytes: usize,
    pub claims: ClaimMapping,
}

impl Default for VerificationProfile {
    fn default() -> Self {
        Self {
            max_token_bytes: 32 * 1_024,
            max_header_bytes: 1_024,
            max_payload_bytes: 16 * 1_024,
            json_limits: JsonLimits::default(),
            clock_skew_seconds: 30,
            max_lifetime_seconds: Some(30 * 24 * 60 * 60),
            require_expiration: true,
            require_issued_at: true,
            require_username: false,
            required_issuer: None,
            required_audience: None,
            allow_legacy_without_key_id: true,
            reject_unknown_header_fields: true,
            require_epoch_claim: true,
            max_subject_bytes: 256,
            max_username_bytes: 256,
            max_token_id_bytes: 256,
            max_variables: 64,
            max_variable_key_bytes: 128,
            max_variable_value_bytes: 4_096,
            claims: ClaimMapping::standard(),
        }
    }
}

impl VerificationProfile {
    pub fn validate(&self) -> Result<(), JwtError> {
        if self.max_token_bytes == 0
            || self.max_header_bytes == 0
            || self.max_payload_bytes == 0
            || self.max_subject_bytes == 0
            || self.max_username_bytes == 0
            || self.max_token_id_bytes == 0
            || self.max_variable_key_bytes == 0
            || self.max_variable_value_bytes == 0
            || self.clock_skew_seconds < 0
        {
            return Err(JwtError::InvalidProfile);
        }
        let mut names = BTreeSet::new();
        for name in [
            Some(self.claims.subject.as_str()),
            self.claims.username.as_deref(),
            self.claims.variables.as_deref(),
            self.claims.token_id.as_deref(),
            Some(self.claims.key_epoch.as_str()),
        ]
        .into_iter()
        .flatten()
        {
            if name.is_empty() || !names.insert(name) {
                return Err(JwtError::InvalidProfile);
            }
        }
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct VerifiedPrincipal {
    pub subject: String,
    pub username: Option<String>,
    pub variables: BTreeMap<String, String>,
    pub token_id: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct VerifiedToken {
    pub route: TokenRoute,
    pub principal: VerifiedPrincipal,
    pub issued_at: Option<i64>,
    pub not_before: Option<i64>,
    pub expires_at: Option<i64>,
    pub issuer: Option<String>,
    pub audiences: Vec<String>,
    pub claims: JsonValue,
}
