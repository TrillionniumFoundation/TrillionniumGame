use std::collections::{BTreeMap, BTreeSet};
use std::fmt;

pub use trnm_identity_core::AccountId;

pub const MAX_GROUP_NAME_BYTES: usize = 256;
pub const MAX_MESSAGE_BYTES: usize = 16_384;
pub const MAX_NOTIFICATION_TEXT_BYTES: usize = 16_384;
pub const MAX_PAGE_SIZE: usize = 1_000;
pub const HARD_MAX_USERS: usize = 1_000_000;
pub const HARD_MAX_RELATIONSHIPS_PER_USER: usize = 100_000;
pub const HARD_MAX_GROUPS: usize = 1_000_000;
pub const HARD_MAX_GROUP_MEMBERS: usize = 100_000;
pub const HARD_MAX_MESSAGES_PER_GROUP: usize = 1_000_000;
pub const HARD_MAX_NOTIFICATIONS_PER_USER: usize = 1_000_000;
pub const HARD_MAX_RECEIPTS: usize = 4_000_000;
pub const HARD_MAX_OUTBOX_INTENTS: usize = 8_000_000;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SocialErrorCode {
    InvalidArgument,
    NotFound,
    AlreadyExists,
    FailedPrecondition,
    PermissionDenied,
    ResourceExhausted,
    Conflict,
    OutOfRange,
    DataLoss,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SocialError {
    code: SocialErrorCode,
    reason: &'static str,
}

impl SocialError {
    const fn new(code: SocialErrorCode, reason: &'static str) -> Self {
        Self { code, reason }
    }

    #[must_use]
    pub const fn code(&self) -> SocialErrorCode {
        self.code
    }

    #[must_use]
    pub const fn reason(&self) -> &'static str {
        self.reason
    }
}

macro_rules! bounded_id {
    ($name:ident, $size:expr, $zero_reason:literal) => {
        #[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
        pub struct $name([u8; $size]);

        impl $name {
            pub fn new(bytes: [u8; $size]) -> Result<Self, SocialError> {
                if bytes == [0; $size] {
                    return Err(SocialError::new(
                        SocialErrorCode::InvalidArgument,
                        $zero_reason,
                    ));
                }
                Ok(Self(bytes))
            }

            #[must_use]
            pub const fn as_bytes(&self) -> &[u8; $size] {
                &self.0
            }
        }
    };
}

bounded_id!(SocialCommandId, 16, "zero_social_command_id");
bounded_id!(CommandFingerprint, 32, "zero_social_command_fingerprint");
bounded_id!(GroupId, 16, "zero_group_id");
bounded_id!(MessageId, 16, "zero_message_id");
bounded_id!(NotificationId, 16, "zero_notification_id");
