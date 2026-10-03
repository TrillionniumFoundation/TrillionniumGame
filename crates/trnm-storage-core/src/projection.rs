use std::fmt;

use trnm_contracts::{DomainError, RetryClass, StableCode};

use crate::{error, validate_value, ContentVersion, IntegrityDigest};

pub const MAX_REQUEST_VALUE_BYTES: usize = 1024 * 1024;
pub const MAX_PROJECTION_VALUE_BYTES: usize = 16 * 1024 * 1024;

/// A persisted public token, independent of the rendered storage value. Its
/// character bound models the native column, not SQL input validity: empty,
/// nonhex, uppercase, star and control strings remain legal at this pure type.
#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct PublicVersion(String);

impl PublicVersion {
    pub fn new(value: impl Into<String>) -> Result<Self, DomainError> {
        let value = value.into();
        if value.chars().take(33).count() > 32 {
            return Err(error(
                StableCode::InvalidArgument,
                "invalid_storage_public_version",
                RetryClass::Never,
            ));
        }
        Ok(Self(value))
    }

    pub fn parse(value: &str) -> Result<Self, DomainError> {
        Self::new(value)
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }

    #[must_use]
    pub fn as_bytes(&self) -> &[u8] {
        self.0.as_bytes()
    }
}

impl From<ContentVersion> for PublicVersion {
    fn from(value: ContentVersion) -> Self {
        Self(value.as_str().to_owned())
    }
}

impl TryFrom<String> for PublicVersion {
    type Error = DomainError;

    fn try_from(value: String) -> Result<Self, Self::Error> {
        Self::new(value)
    }
}

impl TryFrom<&str> for PublicVersion {
    type Error = DomainError;

    fn try_from(value: &str) -> Result<Self, Self::Error> {
        Self::new(value)
    }
}

impl fmt::Display for PublicVersion {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

/// A checked fingerprint of known request bytes and their supplied projection.
/// This binding does not reconstruct request bytes or implement a native JSONB
/// renderer. The adapter owns the provenance and correctness of its projection.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct CollisionWitness {
    request_digest: IntegrityDigest,
    request_len: usize,
    request_version: ContentVersion,
    projection_digest: IntegrityDigest,
}

impl CollisionWitness {
    pub fn from_request(request: &[u8], projected_value: &[u8]) -> Result<Self, DomainError> {
        validate_value(request)?;
        validate_projection_budget(projected_value)?;
        Ok(Self {
            request_digest: IntegrityDigest::from_value(request),
            request_len: request.len(),
            request_version: ContentVersion::from_value(request),
            projection_digest: IntegrityDigest::from_value(projected_value),
        })
    }

    #[must_use]
    pub const fn request_version(&self) -> ContentVersion {
        self.request_version
    }

    #[must_use]
    pub const fn request_digest(&self) -> IntegrityDigest {
        self.request_digest
    }

    #[must_use]
    pub const fn request_len(&self) -> usize {
        self.request_len
    }

    #[must_use]
    pub const fn projection_digest(&self) -> IntegrityDigest {
        self.projection_digest
    }

    #[must_use]
    pub fn matches_request(&self, request: &[u8]) -> bool {
        request.len() <= MAX_REQUEST_VALUE_BYTES
            && request.len() == self.request_len
            && self.request_digest.matches_value(request)
            && self.request_version == ContentVersion::from_value(request)
    }

    pub fn validate_projection(
        &self,
        version: &PublicVersion,
        projected_value: &[u8],
    ) -> Result<(), DomainError> {
        validate_projection_budget(projected_value)?;
        if version.as_str() != self.request_version.as_str()
            || !self.projection_digest.matches_value(projected_value)
        {
            return Err(error(
                StableCode::DataLoss,
                "storage_collision_witness_binding_mismatch",
                RetryClass::Never,
            ));
        }
        Ok(())
    }
}

pub(crate) fn validate_projection_budget(value: &[u8]) -> Result<(), DomainError> {
    if value.len() > MAX_PROJECTION_VALUE_BYTES {
        return Err(error(
            StableCode::ResourceExhausted,
            "storage_projection_value_budget_exceeded",
            RetryClass::Never,
        ));
    }
    Ok(())
}
