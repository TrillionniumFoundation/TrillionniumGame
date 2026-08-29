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
        let key = self
            .legacy
            .as_ref()
            .ok_or(JwtError::LegacyKeyUnavailable)?;
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
            TokenRoute::Legacy => self
                .legacy
                .as_ref()
                .ok_or(JwtError::LegacyKeyUnavailable),
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

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum JwtError {
    InvalidProfile,
    InvalidKeyLength {
        minimum: usize,
        maximum: usize,
        actual: usize,
    },
    InvalidKeyEpoch,
    ActiveEpochUnavailable,
    TokenTooLarge {
        limit: usize,
        actual: usize,
    },
    SegmentCount,
    EmptySegment,
    HeaderBase64(Base64UrlError),
    PayloadBase64(Base64UrlError),
    SignatureBase64(Base64UrlError),
    HeaderJson(JsonError),
    PayloadJson(JsonError),
    HeaderEncode(JsonEncodeError),
    PayloadEncode(JsonEncodeError),
    HeaderNotObject,
    PayloadNotObject,
    UnknownHeaderField(String),
    CriticalHeaderForbidden,
    AlgorithmMissing,
    UnsupportedAlgorithm(String),
    InvalidTypeHeader,
    InvalidKeyId,
    UnknownKeyEpoch(u32),
    LegacyRouteForbidden,
    LegacyKeyUnavailable,
    SignatureLength {
        actual: usize,
    },
    SignatureMismatch,
    EpochClaimMissing,
    EpochClaimMismatch {
        header: u32,
        payload: u64,
    },
    EpochClaimOnLegacyRoute,
    MissingClaim(String),
    InvalidClaimType(String),
    EmptyClaim(String),
    ClaimLengthExceeded {
        claim: String,
        limit: usize,
    },
    InvalidNumericDate(String),
    Expired {
        expires_at: i64,
        now: i64,
    },
    NotYetValid {
        not_before: i64,
        now: i64,
    },
    IssuedInFuture {
        issued_at: i64,
        now: i64,
    },
    InvalidLifetime,
    LifetimeExceeded {
        limit: u64,
        actual: u64,
    },
    IssuerMismatch,
    AudienceMismatch,
    InvalidAudience,
    VariableCountExceeded {
        limit: usize,
    },
    InvalidVariableValue(String),
}

impl fmt::Display for JwtError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidProfile => formatter.write_str("invalid JWT verification profile"),
            Self::InvalidKeyLength {
                minimum,
                maximum,
                actual,
            } => write!(
                formatter,
                "invalid HMAC key length {actual}; expected {minimum}..={maximum} bytes"
            ),
            Self::InvalidKeyEpoch => formatter.write_str("key epoch must be greater than zero"),
            Self::ActiveEpochUnavailable => formatter.write_str("active key epoch is unavailable"),
            Self::TokenTooLarge { limit, actual } => {
                write!(formatter, "JWT length {actual} exceeds {limit} bytes")
            }
            Self::SegmentCount => formatter.write_str("JWT must contain exactly three segments"),
            Self::EmptySegment => formatter.write_str("JWT segments must not be empty"),
            Self::HeaderBase64(error) => write!(formatter, "JWT header base64url error: {error}"),
            Self::PayloadBase64(error) => write!(formatter, "JWT payload base64url error: {error}"),
            Self::SignatureBase64(error) => {
                write!(formatter, "JWT signature base64url error: {error}")
            }
            Self::HeaderJson(error) => write!(formatter, "JWT header JSON error: {error}"),
            Self::PayloadJson(error) => write!(formatter, "JWT payload JSON error: {error}"),
            Self::HeaderEncode(error) => write!(formatter, "JWT header encoding error: {error}"),
            Self::PayloadEncode(error) => write!(formatter, "JWT payload encoding error: {error}"),
            Self::HeaderNotObject => formatter.write_str("JWT header must be a JSON object"),
            Self::PayloadNotObject => formatter.write_str("JWT payload must be a JSON object"),
            Self::UnknownHeaderField(field) => {
                write!(formatter, "unrecognized JWT header field {field:?}")
            }
            Self::CriticalHeaderForbidden => {
                formatter.write_str("JWT critical and detached-payload headers are forbidden")
            }
            Self::AlgorithmMissing => formatter.write_str("JWT alg header is missing"),
            Self::UnsupportedAlgorithm(value) => {
                write!(formatter, "unsupported JWT algorithm {value:?}")
            }
            Self::InvalidTypeHeader => formatter.write_str("JWT typ header must be JWT when present"),
            Self::InvalidKeyId => formatter.write_str("JWT kid header is malformed"),
            Self::UnknownKeyEpoch(epoch) => write!(formatter, "unknown JWT key epoch {epoch}"),
            Self::LegacyRouteForbidden => formatter.write_str("legacy JWT route is disabled"),
            Self::LegacyKeyUnavailable => formatter.write_str("legacy JWT key is unavailable"),
            Self::SignatureLength { actual } => {
                write!(formatter, "HS256 signature must be 32 bytes, received {actual}")
            }
            Self::SignatureMismatch => formatter.write_str("JWT signature mismatch"),
            Self::EpochClaimMissing => formatter.write_str("epoch JWT payload claim is missing"),
            Self::EpochClaimMismatch { header, payload } => write!(
                formatter,
                "JWT key epoch mismatch: header {header}, payload {payload}"
            ),
            Self::EpochClaimOnLegacyRoute => {
                formatter.write_str("legacy JWT must not carry an epoch payload claim")
            }
            Self::MissingClaim(claim) => write!(formatter, "required JWT claim {claim:?} is missing"),
            Self::InvalidClaimType(claim) => {
                write!(formatter, "JWT claim {claim:?} has an invalid type")
            }
            Self::EmptyClaim(claim) => write!(formatter, "JWT claim {claim:?} must not be empty"),
            Self::ClaimLengthExceeded { claim, limit } => {
                write!(formatter, "JWT claim {claim:?} exceeds {limit} bytes")
            }
            Self::InvalidNumericDate(claim) => {
                write!(formatter, "JWT NumericDate claim {claim:?} is invalid")
            }
            Self::Expired { expires_at, now } => {
                write!(formatter, "JWT expired at {expires_at}; current time is {now}")
            }
            Self::NotYetValid { not_before, now } => write!(
                formatter,
                "JWT is not valid before {not_before}; current time is {now}"
            ),
            Self::IssuedInFuture { issued_at, now } => write!(
                formatter,
                "JWT issued-at {issued_at} is later than current time {now}"
            ),
            Self::InvalidLifetime => formatter.write_str("JWT lifetime is non-positive"),
            Self::LifetimeExceeded { limit, actual } => {
                write!(formatter, "JWT lifetime {actual}s exceeds {limit}s")
            }
            Self::IssuerMismatch => formatter.write_str("JWT issuer mismatch"),
            Self::AudienceMismatch => formatter.write_str("JWT audience mismatch"),
            Self::InvalidAudience => formatter.write_str("JWT audience claim is invalid"),
            Self::VariableCountExceeded { limit } => {
                write!(formatter, "JWT variables exceed {limit} entries")
            }
            Self::InvalidVariableValue(key) => {
                write!(formatter, "JWT variable {key:?} must be a bounded string")
            }
        }
    }
}

impl std::error::Error for JwtError {}

pub fn verify(
    token: &str,
    keys: &KeyRing,
    profile: &VerificationProfile,
    now_unix_seconds: i64,
) -> Result<VerifiedToken, JwtError> {
    profile.validate()?;
    if token.len() > profile.max_token_bytes {
        return Err(JwtError::TokenTooLarge {
            limit: profile.max_token_bytes,
            actual: token.len(),
        });
    }
    let mut segments = token.split('.');
    let header_segment = segments.next().ok_or(JwtError::SegmentCount)?;
    let payload_segment = segments.next().ok_or(JwtError::SegmentCount)?;
    let signature_segment = segments.next().ok_or(JwtError::SegmentCount)?;
    if segments.next().is_some() {
        return Err(JwtError::SegmentCount);
    }
    if header_segment.is_empty() || payload_segment.is_empty() || signature_segment.is_empty() {
        return Err(JwtError::EmptySegment);
    }

    let header_bytes = base64url::decode(header_segment, profile.max_header_bytes)
        .map_err(JwtError::HeaderBase64)?;
    let header = json::parse(&header_bytes, profile.json_limits).map_err(JwtError::HeaderJson)?;
    let header_object = header.as_object().ok_or(JwtError::HeaderNotObject)?;
    validate_header_fields(header_object, profile)?;
    let algorithm = required_string(header_object, "alg", profile.max_header_bytes)?;
    if algorithm != "HS256" {
        return Err(JwtError::UnsupportedAlgorithm(algorithm.to_owned()));
    }
    if let Some(value) = header_object.get("typ") {
        if value.as_str() != Some("JWT") {
            return Err(JwtError::InvalidTypeHeader);
        }
    }
    let route = parse_route(header_object, profile)?;
    let key = keys.key_for_route(route)?;

    let signature = base64url::decode(signature_segment, SIGNATURE_BYTES)
        .map_err(JwtError::SignatureBase64)?;
    if signature.len() != SIGNATURE_BYTES {
        return Err(JwtError::SignatureLength {
            actual: signature.len(),
        });
    }
    let expected = hmac_sha256(
        key.expose(),
        &[header_segment.as_bytes(), b".", payload_segment.as_bytes()],
    );
    if !constant_time_eq(&signature, &expected) {
        return Err(JwtError::SignatureMismatch);
    }

    let payload_bytes = base64url::decode(payload_segment, profile.max_payload_bytes)
        .map_err(JwtError::PayloadBase64)?;
    let claims = json::parse(&payload_bytes, profile.json_limits).map_err(JwtError::PayloadJson)?;
    let claims_object = claims.as_object().ok_or(JwtError::PayloadNotObject)?;
    let validated = validate_claims(claims_object, route, profile, now_unix_seconds)?;

    Ok(VerifiedToken {
        route,
        principal: validated.principal,
        issued_at: validated.issued_at,
        not_before: validated.not_before,
        expires_at: validated.expires_at,
        issuer: validated.issuer,
        audiences: validated.audiences,
        claims,
    })
}

pub fn issue_legacy(
    claims: &JsonValue,
    key: &SecretKey,
    profile: &VerificationProfile,
) -> Result<String, JwtError> {
    profile.validate()?;
    let object = claims.as_object().ok_or(JwtError::PayloadNotObject)?;
    if object.contains_key(&profile.claims.key_epoch) {
        return Err(JwtError::EpochClaimOnLegacyRoute);
    }
    issue_with_route(claims.clone(), TokenRoute::Legacy, key, profile)
}

pub fn issue_epoch(
    claims: &JsonValue,
    epoch: u32,
    key: &SecretKey,
    profile: &VerificationProfile,
) -> Result<String, JwtError> {
    profile.validate()?;
    if epoch == 0 {
        return Err(JwtError::InvalidKeyEpoch);
    }
    let mut object = claims
        .as_object()
        .ok_or(JwtError::PayloadNotObject)?
        .clone();
    if let Some(existing) = object.get(&profile.claims.key_epoch) {
        let payload = existing
            .as_u64()
            .ok_or_else(|| JwtError::InvalidClaimType(profile.claims.key_epoch.clone()))?;
        if payload != u64::from(epoch) {
            return Err(JwtError::EpochClaimMismatch {
                header: epoch,
                payload,
            });
        }
    } else {
        object.insert(
            profile.claims.key_epoch.clone(),
            JsonValue::Unsigned(u64::from(epoch)),
        );
    }
    issue_with_route(JsonValue::Object(object), TokenRoute::Epoch(epoch), key, profile)
}

fn issue_with_route(
    claims: JsonValue,
    route: TokenRoute,
    key: &SecretKey,
    profile: &VerificationProfile,
) -> Result<String, JwtError> {
    let mut header = BTreeMap::from([
        ("alg".to_owned(), JsonValue::String("HS256".to_owned())),
        ("typ".to_owned(), JsonValue::String("JWT".to_owned())),
    ]);
    if let TokenRoute::Epoch(epoch) = route {
        header.insert(
            "kid".to_owned(),
            JsonValue::String(format!("{EPOCH_KEY_ID_PREFIX}{epoch}")),
        );
    }
    let header_bytes = json::to_canonical_bytes(
        &JsonValue::Object(header),
        profile.max_header_bytes,
    )
    .map_err(JwtError::HeaderEncode)?;
    let payload_bytes = json::to_canonical_bytes(&claims, profile.max_payload_bytes)
        .map_err(JwtError::PayloadEncode)?;
    let header_segment = base64url::encode(&header_bytes);
    let payload_segment = base64url::encode(&payload_bytes);
    let signature = hmac_sha256(
        key.expose(),
        &[header_segment.as_bytes(), b".", payload_segment.as_bytes()],
    );
    let signature_segment = base64url::encode(&signature);
    let token = format!("{header_segment}.{payload_segment}.{signature_segment}");
    if token.len() > profile.max_token_bytes {
        return Err(JwtError::TokenTooLarge {
            limit: profile.max_token_bytes,
            actual: token.len(),
        });
    }
    Ok(token)
}

fn validate_header_fields(
    header: &BTreeMap<String, JsonValue>,
    profile: &VerificationProfile,
) -> Result<(), JwtError> {
    if header.contains_key("crit") || header.contains_key("b64") {
        return Err(JwtError::CriticalHeaderForbidden);
    }
    if profile.reject_unknown_header_fields {
        for key in header.keys() {
            if !matches!(key.as_str(), "alg" | "typ" | "kid") {
                return Err(JwtError::UnknownHeaderField(key.clone()));
            }
        }
    }
    if !header.contains_key("alg") {
        return Err(JwtError::AlgorithmMissing);
    }
    Ok(())
}

fn parse_route(
    header: &BTreeMap<String, JsonValue>,
    profile: &VerificationProfile,
) -> Result<TokenRoute, JwtError> {
    match header.get("kid") {
        None if profile.allow_legacy_without_key_id => Ok(TokenRoute::Legacy),
        None => Err(JwtError::LegacyRouteForbidden),
        Some(JsonValue::String(key_id)) => {
            let raw_epoch = key_id
                .strip_prefix(EPOCH_KEY_ID_PREFIX)
                .ok_or(JwtError::InvalidKeyId)?;
            if raw_epoch.is_empty()
                || (raw_epoch.len() > 1 && raw_epoch.starts_with('0'))
                || !raw_epoch.bytes().all(|byte| byte.is_ascii_digit())
            {
                return Err(JwtError::InvalidKeyId);
            }
            let epoch = raw_epoch.parse::<u32>().map_err(|_| JwtError::InvalidKeyId)?;
            if epoch == 0 {
                return Err(JwtError::InvalidKeyId);
            }
            Ok(TokenRoute::Epoch(epoch))
        }
        Some(_) => Err(JwtError::InvalidKeyId),
    }
}

struct ValidatedClaims {
    principal: VerifiedPrincipal,
    issued_at: Option<i64>,
    not_before: Option<i64>,
    expires_at: Option<i64>,
    issuer: Option<String>,
    audiences: Vec<String>,
}

fn validate_claims(
    claims: &BTreeMap<String, JsonValue>,
    route: TokenRoute,
    profile: &VerificationProfile,
    now: i64,
) -> Result<ValidatedClaims, JwtError> {
    validate_epoch_claim(claims, route, profile)?;
    let subject = bounded_required_string(
        claims,
        &profile.claims.subject,
        profile.max_subject_bytes,
    )?
    .to_owned();
    let username = match &profile.claims.username {
        Some(name) if profile.require_username => Some(
            bounded_required_string(claims, name, profile.max_username_bytes)?.to_owned(),
        ),
        Some(name) => bounded_optional_string(claims, name, profile.max_username_bytes)?
            .map(str::to_owned),
        None if profile.require_username => return Err(JwtError::InvalidProfile),
        None => None,
    };
    let token_id = match &profile.claims.token_id {
        Some(name) => bounded_optional_string(claims, name, profile.max_token_id_bytes)?
            .map(str::to_owned),
        None => None,
    };
    let variables = validate_variables(claims, profile)?;

    let expires_at = numeric_date(claims, "exp", profile.require_expiration)?;
    let issued_at = numeric_date(claims, "iat", profile.require_issued_at)?;
    let not_before = numeric_date(claims, "nbf", false)?;
    let skew = i128::from(profile.clock_skew_seconds);
    let now_wide = i128::from(now);
    if let Some(expires_at) = expires_at {
        if now_wide >= i128::from(expires_at) + skew {
            return Err(JwtError::Expired { expires_at, now });
        }
    }
    if let Some(not_before) = not_before {
        if now_wide + skew < i128::from(not_before) {
            return Err(JwtError::NotYetValid { not_before, now });
        }
    }
    if let Some(issued_at) = issued_at {
        if now_wide + skew < i128::from(issued_at) {
            return Err(JwtError::IssuedInFuture { issued_at, now });
        }
    }
    if let (Some(issued_at), Some(expires_at), Some(limit)) =
        (issued_at, expires_at, profile.max_lifetime_seconds)
    {
        if expires_at <= issued_at {
            return Err(JwtError::InvalidLifetime);
        }
        let lifetime = u64::try_from(expires_at - issued_at)
            .map_err(|_| JwtError::InvalidLifetime)?;
        if lifetime > limit {
            return Err(JwtError::LifetimeExceeded {
                limit,
                actual: lifetime,
            });
        }
    }

    let issuer = optional_string(claims, "iss")?.map(str::to_owned);
    if let Some(required) = &profile.required_issuer {
        if issuer.as_deref() != Some(required.as_str()) {
            return Err(JwtError::IssuerMismatch);
        }
    }
    let audiences = validate_audience(claims.get("aud"))?;
    if let Some(required) = &profile.required_audience {
        if !audiences.iter().any(|audience| audience == required) {
            return Err(JwtError::AudienceMismatch);
        }
    }

    Ok(ValidatedClaims {
        principal: VerifiedPrincipal {
            subject,
            username,
            variables,
            token_id,
        },
        issued_at,
        not_before,
        expires_at,
        issuer,
        audiences,
    })
}

fn validate_epoch_claim(
    claims: &BTreeMap<String, JsonValue>,
    route: TokenRoute,
    profile: &VerificationProfile,
) -> Result<(), JwtError> {
    let value = claims.get(&profile.claims.key_epoch);
    match route {
        TokenRoute::Legacy => {
            if value.is_some() {
                return Err(JwtError::EpochClaimOnLegacyRoute);
            }
        }
        TokenRoute::Epoch(header) => match value {
            None if profile.require_epoch_claim => return Err(JwtError::EpochClaimMissing),
            None => {}
            Some(value) => {
                let payload = value.as_u64().ok_or_else(|| {
                    JwtError::InvalidClaimType(profile.claims.key_epoch.clone())
                })?;
                if payload != u64::from(header) {
                    return Err(JwtError::EpochClaimMismatch { header, payload });
                }
            }
        },
    }
    Ok(())
}

fn validate_variables(
    claims: &BTreeMap<String, JsonValue>,
    profile: &VerificationProfile,
) -> Result<BTreeMap<String, String>, JwtError> {
    let Some(name) = &profile.claims.variables else {
        return Ok(BTreeMap::new());
    };
    let Some(value) = claims.get(name) else {
        return Ok(BTreeMap::new());
    };
    let object = value
        .as_object()
        .ok_or_else(|| JwtError::InvalidClaimType(name.clone()))?;
    if object.len() > profile.max_variables {
        return Err(JwtError::VariableCountExceeded {
            limit: profile.max_variables,
        });
    }
    let mut variables = BTreeMap::new();
    for (key, value) in object {
        if key.is_empty()
            || key.len() > profile.max_variable_key_bytes
            || value.as_str().is_none()
        {
            return Err(JwtError::InvalidVariableValue(key.clone()));
        }
        let value = value.as_str().expect("checked above");
        if value.len() > profile.max_variable_value_bytes {
            return Err(JwtError::InvalidVariableValue(key.clone()));
        }
        variables.insert(key.clone(), value.to_owned());
    }
    Ok(variables)
}

fn validate_audience(value: Option<&JsonValue>) -> Result<Vec<String>, JwtError> {
    let Some(value) = value else {
        return Ok(Vec::new());
    };
    let values: Vec<&str> = match value {
        JsonValue::String(value) => vec![value.as_str()],
        JsonValue::Array(values) => values
            .iter()
            .map(JsonValue::as_str)
            .collect::<Option<Vec<_>>>()
            .ok_or(JwtError::InvalidAudience)?,
        _ => return Err(JwtError::InvalidAudience),
    };
    if values.iter().any(|value| value.is_empty()) {
        return Err(JwtError::InvalidAudience);
    }
    let mut seen = BTreeSet::new();
    for value in &values {
        if !seen.insert(*value) {
            return Err(JwtError::InvalidAudience);
        }
    }
    Ok(values.into_iter().map(str::to_owned).collect())
}

fn numeric_date(
    claims: &BTreeMap<String, JsonValue>,
    name: &str,
    required: bool,
) -> Result<Option<i64>, JwtError> {
    match claims.get(name) {
        None if required => Err(JwtError::MissingClaim(name.to_owned())),
        None => Ok(None),
        Some(value) => value
            .as_i64()
            .filter(|value| *value >= 0)
            .map(Some)
            .ok_or_else(|| JwtError::InvalidNumericDate(name.to_owned())),
    }
}

fn bounded_required_string<'a>(
    object: &'a BTreeMap<String, JsonValue>,
    name: &str,
    limit: usize,
) -> Result<&'a str, JwtError> {
    let value = required_string(object, name, limit)?;
    if value.is_empty() {
        return Err(JwtError::EmptyClaim(name.to_owned()));
    }
    Ok(value)
}

fn bounded_optional_string<'a>(
    object: &'a BTreeMap<String, JsonValue>,
    name: &str,
    limit: usize,
) -> Result<Option<&'a str>, JwtError> {
    match object.get(name) {
        None => Ok(None),
        Some(value) => {
            let value = value
                .as_str()
                .ok_or_else(|| JwtError::InvalidClaimType(name.to_owned()))?;
            if value.is_empty() {
                return Err(JwtError::EmptyClaim(name.to_owned()));
            }
            if value.len() > limit {
                return Err(JwtError::ClaimLengthExceeded {
                    claim: name.to_owned(),
                    limit,
                });
            }
            Ok(Some(value))
        }
    }
}

fn required_string<'a>(
    object: &'a BTreeMap<String, JsonValue>,
    name: &str,
    limit: usize,
) -> Result<&'a str, JwtError> {
    let value = object
        .get(name)
        .ok_or_else(|| JwtError::MissingClaim(name.to_owned()))?
        .as_str()
        .ok_or_else(|| JwtError::InvalidClaimType(name.to_owned()))?;
    if value.len() > limit {
        return Err(JwtError::ClaimLengthExceeded {
            claim: name.to_owned(),
            limit,
        });
    }
    Ok(value)
}

fn optional_string<'a>(
    object: &'a BTreeMap<String, JsonValue>,
    name: &str,
) -> Result<Option<&'a str>, JwtError> {
    match object.get(name) {
        None => Ok(None),
        Some(value) => value
            .as_str()
            .map(Some)
            .ok_or_else(|| JwtError::InvalidClaimType(name.to_owned())),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn key(byte: u8) -> SecretKey {
        SecretKey::new(vec![byte; 32]).unwrap()
    }

    fn profile() -> VerificationProfile {
        VerificationProfile {
            claims: ClaimMapping::uid_legacy(),
            clock_skew_seconds: 0,
            max_lifetime_seconds: Some(3_600),
            ..VerificationProfile::default()
        }
    }

    fn claims() -> JsonValue {
        JsonValue::Object(BTreeMap::from([
            ("aud".into(), JsonValue::String("game".into())),
            ("exp".into(), JsonValue::Unsigned(2_000)),
            ("iat".into(), JsonValue::Unsigned(1_000)),
            ("iss".into(), JsonValue::String("issuer".into())),
            ("tid".into(), JsonValue::String("token-1".into())),
            ("uid".into(), JsonValue::String("user-1".into())),
            ("usn".into(), JsonValue::String("alice".into())),
            (
                "vrs".into(),
                JsonValue::Object(BTreeMap::from([(
                    "region".into(),
                    JsonValue::String("ca".into()),
                )])),
            ),
        ]))
    }

    #[test]
    fn legacy_and_epoch_routes_round_trip() {
        let profile = profile();
        let mut keys = KeyRing::new();
        keys.set_legacy_key(key(0x11));
        keys.insert_epoch_key(7, key(0x22)).unwrap();
        keys.set_active_epoch(7).unwrap();

        let legacy = keys.issue_legacy(&claims(), &profile).unwrap();
        let verified = keys.verify(&legacy, &profile, 1_500).unwrap();
        assert_eq!(verified.route, TokenRoute::Legacy);
        assert_eq!(verified.principal.subject, "user-1");
        assert_eq!(verified.principal.username.as_deref(), Some("alice"));
        assert_eq!(verified.principal.variables["region"], "ca");

        let epoch = keys.issue_active_epoch(&claims(), &profile).unwrap();
        let verified = keys.verify(&epoch, &profile, 1_500).unwrap();
        assert_eq!(verified.route, TokenRoute::Epoch(7));
        assert_eq!(
            verified
                .claims
                .as_object()
                .unwrap()
                .get("trnm_kep")
                .and_then(JsonValue::as_u64),
            Some(7)
        );
    }

    #[test]
    fn unknown_or_malformed_epoch_never_downgrades_to_legacy_key() {
        let profile = profile();
        let mut keys = KeyRing::new();
        keys.set_legacy_key(key(0x11));
        keys.insert_epoch_key(7, key(0x22)).unwrap();
        let token = issue_epoch(&claims(), 8, &key(0x33), &profile).unwrap();
        assert_eq!(
            keys.verify(&token, &profile, 1_500),
            Err(JwtError::UnknownKeyEpoch(8))
        );

        let valid = issue_epoch(&claims(), 7, keys.epoch_keys.get(&7).unwrap(), &profile).unwrap();
        let malformed = valid.replacen("dHJu", "YmFk", 1);
        assert!(keys.verify(&malformed, &profile, 1_500).is_err());
    }

    #[test]
    fn signature_tamper_and_wrong_key_are_rejected() {
        let profile = profile();
        let token = issue_legacy(&claims(), &key(0x11), &profile).unwrap();
        let mut wrong = KeyRing::new();
        wrong.set_legacy_key(key(0x12));
        assert_eq!(
            wrong.verify(&token, &profile, 1_500),
            Err(JwtError::SignatureMismatch)
        );
        let mut bytes = token.into_bytes();
        let index = bytes.len() - 1;
        bytes[index] = if bytes[index] == b'A' { b'B' } else { b'A' };
        let tampered = String::from_utf8(bytes).unwrap();
        assert!(wrong.verify(&tampered, &profile, 1_500).is_err());
    }

    #[test]
    fn time_issuer_audience_and_lifetime_are_enforced() {
        let mut profile = profile();
        profile.required_issuer = Some("issuer".into());
        profile.required_audience = Some("game".into());
        let token = issue_legacy(&claims(), &key(0x11), &profile).unwrap();
        let mut keys = KeyRing::new();
        keys.set_legacy_key(key(0x11));
        assert!(keys.verify(&token, &profile, 1_500).is_ok());
        assert!(matches!(
            keys.verify(&token, &profile, 2_000),
            Err(JwtError::Expired { .. })
        ));

        let mut wrong_issuer = profile.clone();
        wrong_issuer.required_issuer = Some("other".into());
        assert_eq!(
            keys.verify(&token, &wrong_issuer, 1_500),
            Err(JwtError::IssuerMismatch)
        );
        let mut wrong_audience = profile.clone();
        wrong_audience.required_audience = Some("other".into());
        assert_eq!(
            keys.verify(&token, &wrong_audience, 1_500),
            Err(JwtError::AudienceMismatch)
        );
    }

    #[test]
    fn duplicate_payload_keys_and_unrecognized_headers_are_rejected() {
        let profile = profile();
        let header = base64url::encode(br#"{"alg":"HS256","x":1}"#);
        let payload = base64url::encode(
            br#"{"uid":"a","uid":"b","iat":1000,"exp":2000}"#,
        );
        let signature = hmac_sha256(
            key(0x11).expose(),
            &[header.as_bytes(), b".", payload.as_bytes()],
        );
        let token = format!("{header}.{payload}.{}", base64url::encode(&signature));
        let mut keys = KeyRing::new();
        keys.set_legacy_key(key(0x11));
        assert!(matches!(
            keys.verify(&token, &profile, 1_500),
            Err(JwtError::UnknownHeaderField(_))
        ));
    }

    #[test]
    fn algorithm_confusion_and_base64_padding_are_rejected() {
        let profile = profile();
        let mut keys = KeyRing::new();
        keys.set_legacy_key(key(0x11));
        for algorithm in ["none", "HS384", "RS256"] {
            let header = base64url::encode(
                format!(r#"{{"alg":"{algorithm}","typ":"JWT"}}"#).as_bytes(),
            );
            let payload = base64url::encode(
                json::to_canonical_bytes(&claims(), profile.max_payload_bytes)
                    .unwrap()
                    .as_slice(),
            );
            let signature = hmac_sha256(
                key(0x11).expose(),
                &[header.as_bytes(), b".", payload.as_bytes()],
            );
            let token = format!("{header}.{payload}.{}", base64url::encode(&signature));
            assert_eq!(
                keys.verify(&token, &profile, 1_500),
                Err(JwtError::UnsupportedAlgorithm(algorithm.into()))
            );
        }
        let token = keys.issue_legacy(&claims(), &profile).unwrap();
        let padded = format!("{}=", token);
        assert!(matches!(
            keys.verify(&padded, &profile, 1_500),
            Err(JwtError::SignatureBase64(_))
        ));
    }
}
