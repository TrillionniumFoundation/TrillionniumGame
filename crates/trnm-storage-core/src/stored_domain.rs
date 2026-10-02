use trnm_contracts::{DomainError, RetryClass, StableCode};

use crate::error;

/// The full stored Nakama SMALLINT read domain. HTTP request permission
/// admission remains a separate adapter rule; it may only select 0, 1 or 2.
#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct ReadPermission(i16);

impl ReadPermission {
    pub const NONE: Self = Self(0);
    pub const OWNER: Self = Self(1);
    pub const PUBLIC: Self = Self(2);

    pub fn from_stored(value: i16) -> Result<Self, DomainError> {
        if value < 0 {
            return Err(error(
                StableCode::InvalidArgument,
                "invalid_storage_read_permission",
                RetryClass::Never,
            ));
        }
        Ok(Self(value))
    }

    #[must_use]
    pub const fn get(self) -> i16 {
        self.0
    }

    #[must_use]
    pub fn as_i32(self) -> i32 {
        i32::from(self.0)
    }

    /// Pinned batch-read predicate: public exactly 2, or owned exactly 1.
    #[must_use]
    pub const fn allows_batch_read(self, is_owner: bool) -> bool {
        self.0 == 2 || (is_owner && self.0 == 1)
    }

    /// A client collection-wide listing includes every stored read >= 2.
    #[must_use]
    pub const fn allows_public_listing(self) -> bool {
        self.0 >= 2
    }

    /// A client listing of its own owner scope includes every stored read >= 1.
    #[must_use]
    pub const fn allows_owner_listing(self) -> bool {
        self.0 >= 1
    }

    /// A client listing of a foreign or global owner includes read exactly 2.
    #[must_use]
    pub const fn allows_foreign_listing(self) -> bool {
        self.0 == 2
    }
}

/// The full stored Nakama SMALLINT write domain. HTTP request admission may
/// only select 0 or 1; stored values above 1 must not be clamped or normalized.
#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct WritePermission(i16);

impl WritePermission {
    pub const NONE: Self = Self(0);
    pub const OWNER: Self = Self(1);

    pub fn from_stored(value: i16) -> Result<Self, DomainError> {
        if value < 0 {
            return Err(error(
                StableCode::InvalidArgument,
                "invalid_storage_write_permission",
                RetryClass::Never,
            ));
        }
        Ok(Self(value))
    }

    #[must_use]
    pub const fn get(self) -> i16 {
        self.0
    }

    #[must_use]
    pub fn as_i32(self) -> i32 {
        i32::from(self.0)
    }

    /// Updating an existing client object requires stored write exactly 1.
    #[must_use]
    pub const fn allows_client_write(self) -> bool {
        self.0 == 1
    }

    /// Deleting a client object accepts every positive stored write value.
    #[must_use]
    pub const fn allows_client_delete(self) -> bool {
        self.0 > 0
    }
}
