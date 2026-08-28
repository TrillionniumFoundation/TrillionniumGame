#![forbid(unsafe_code)]

use core::fmt;

macro_rules! byte_id {
    ($name:ident, $size:expr) => {
        #[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
        pub struct $name([u8; $size]);

        impl $name {
            #[must_use]
            pub const fn new(bytes: [u8; $size]) -> Self {
                Self(bytes)
            }

            #[must_use]
            pub const fn as_bytes(&self) -> &[u8; $size] {
                &self.0
            }

            #[must_use]
            pub fn is_zero(&self) -> bool {
                self.0.iter().all(|byte| *byte == 0)
            }
        }
    };
}

macro_rules! counter {
    ($name:ident) => {
        #[derive(Clone, Copy, Debug, Default, Eq, Hash, Ord, PartialEq, PartialOrd)]
        pub struct $name(u64);

        impl $name {
            #[must_use]
            pub const fn new(value: u64) -> Self {
                Self(value)
            }

            #[must_use]
            pub const fn get(self) -> u64 {
                self.0
            }

            pub fn checked_next(self) -> Result<Self, DomainError> {
                self.0.checked_add(1).map(Self).ok_or_else(|| {
                    DomainError::new(
                        StableCode::OutOfRange,
                        "counter_overflow",
                        RetryClass::Never,
                    )
                })
            }
        }
    };
}

byte_id!(CommandId, 16);
byte_id!(ParticipantId, 16);
byte_id!(SessionFamilyId, 16);
byte_id!(RefreshTokenId, 16);
byte_id!(Digest32, 32);
byte_id!(IdempotencyKey, 32);

counter!(MatchVersion);
counter!(ParticipantSequence);
counter!(AuthorityGeneration);
counter!(SessionGeneration);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ProtocolVersion {
    major: u16,
    minor: u16,
}

impl ProtocolVersion {
    #[must_use]
    pub const fn new(major: u16, minor: u16) -> Self {
        Self { major, minor }
    }

    #[must_use]
    pub const fn major(self) -> u16 {
        self.major
    }

    #[must_use]
    pub const fn minor(self) -> u16 {
        self.minor
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(u16)]
pub enum StableCode {
    InvalidArgument = 3,
    NotFound = 5,
    AlreadyExists = 6,
    PermissionDenied = 7,
    ResourceExhausted = 8,
    FailedPrecondition = 9,
    Aborted = 10,
    OutOfRange = 11,
    Unimplemented = 12,
    Internal = 13,
    Unavailable = 14,
    DataLoss = 15,
    Unauthenticated = 16,
}

impl StableCode {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::InvalidArgument => "invalid_argument",
            Self::NotFound => "not_found",
            Self::AlreadyExists => "already_exists",
            Self::PermissionDenied => "permission_denied",
            Self::ResourceExhausted => "resource_exhausted",
            Self::FailedPrecondition => "failed_precondition",
            Self::Aborted => "aborted",
            Self::OutOfRange => "out_of_range",
            Self::Unimplemented => "unimplemented",
            Self::Internal => "internal",
            Self::Unavailable => "unavailable",
            Self::DataLoss => "data_loss",
            Self::Unauthenticated => "unauthenticated",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RetryClass {
    Never,
    SafeImmediate,
    SafeBackoff,
    ResyncRequired,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct DomainError {
    code: StableCode,
    reason: &'static str,
    retry: RetryClass,
}

impl DomainError {
    #[must_use]
    pub const fn new(code: StableCode, reason: &'static str, retry: RetryClass) -> Self {
        Self {
            code,
            reason,
            retry,
        }
    }

    #[must_use]
    pub const fn code(self) -> StableCode {
        self.code
    }

    #[must_use]
    pub const fn reason(self) -> &'static str {
        self.reason
    }

    #[must_use]
    pub const fn retry(self) -> RetryClass {
        self.retry
    }
}

impl fmt::Display for DomainError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{}:{}", self.code.as_str(), self.reason)
    }
}

impl std::error::Error for DomainError {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn counters_fail_closed_on_overflow() {
        let result = MatchVersion::new(u64::MAX).checked_next();
        assert_eq!(
            result,
            Err(DomainError::new(
                StableCode::OutOfRange,
                "counter_overflow",
                RetryClass::Never,
            ))
        );
    }

    #[test]
    fn stable_codes_have_transport_safe_names() {
        assert_eq!(StableCode::Aborted.as_str(), "aborted");
        assert_eq!(StableCode::Unauthenticated as u16, 16);
    }

    #[test]
    fn identifiers_do_not_treat_zero_as_valid_business_identity() {
        assert!(CommandId::new([0; 16]).is_zero());
        assert!(!CommandId::new([1; 16]).is_zero());
    }
}
