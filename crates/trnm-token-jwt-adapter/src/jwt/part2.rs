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
            Self::InvalidTypeHeader => {
                formatter.write_str("JWT typ header must be JWT when present")
            }
            Self::InvalidKeyId => formatter.write_str("JWT kid header is malformed"),
            Self::UnknownKeyEpoch(epoch) => write!(formatter, "unknown JWT key epoch {epoch}"),
            Self::LegacyRouteForbidden => formatter.write_str("legacy JWT route is disabled"),
            Self::LegacyKeyUnavailable => formatter.write_str("legacy JWT key is unavailable"),
            Self::SignatureLength { actual } => {
                write!(
                    formatter,
                    "HS256 signature must be 32 bytes, received {actual}"
                )
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
            Self::MissingClaim(claim) => {
                write!(formatter, "required JWT claim {claim:?} is missing")
            }
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
                write!(
                    formatter,
                    "JWT expired at {expires_at}; current time is {now}"
                )
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
