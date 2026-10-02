use std::error::Error;

use postgres::types::{FromSql, Type};
use trnm_contracts::{DomainError, RetryClass, StableCode};
use trnm_storage_core::{MutationReceipt, StorageObject};

use crate::storage::{StorageClientListPage, StorageListPosition};

const MIN_PROTOBUF_SECONDS: i64 = -62_135_596_800;
const MAX_PROTOBUF_SECONDS: i64 = 253_402_300_799;
const POSTGRES_EPOCH_SECONDS: i64 = 946_684_800;
const MICROSECONDS_PER_SECOND: i64 = 1_000_000;

/// A checked protobuf timestamp, preserving the database's microsecond precision.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct StorageTimestamp {
    pub seconds: i64,
    pub nanos: u32,
}

impl StorageTimestamp {
    pub fn new(seconds: i64, nanos: u32) -> Result<Self, DomainError> {
        let value = Self { seconds, nanos };
        value.validate()?;
        Ok(value)
    }

    pub fn validate(self) -> Result<(), DomainError> {
        if !(MIN_PROTOBUF_SECONDS..=MAX_PROTOBUF_SECONDS).contains(&self.seconds)
            || self.nanos >= 1_000_000_000
        {
            return Err(invalid_timestamp());
        }
        Ok(())
    }

    fn from_database_microseconds(micros: i64) -> Result<Self, DomainError> {
        // PostgreSQL pgwire reserves these values for negative/positive infinity.
        // CockroachDB exposes finite TIMESTAMPTZ through the same binary layout.
        if matches!(micros, i64::MIN | i64::MAX) {
            return Err(invalid_timestamp());
        }
        let seconds = micros
            .div_euclid(MICROSECONDS_PER_SECOND)
            .checked_add(POSTGRES_EPOCH_SECONDS)
            .ok_or_else(invalid_timestamp)?;
        let nanos = u32::try_from(micros.rem_euclid(MICROSECONDS_PER_SECOND))
            .map_err(|_| invalid_timestamp())?
            .checked_mul(1000)
            .ok_or_else(invalid_timestamp)?;
        Self::new(seconds, nanos)
    }
}

impl<'a> FromSql<'a> for StorageTimestamp {
    fn from_sql(kind: &Type, raw: &'a [u8]) -> Result<Self, Box<dyn Error + Sync + Send>> {
        if !Self::accepts(kind) {
            return Err(Box::new(invalid_timestamp()));
        }
        let bytes: [u8; 8] = raw.try_into().map_err(|_| invalid_timestamp())?;
        Ok(Self::from_database_microseconds(i64::from_be_bytes(bytes))?)
    }

    fn accepts(kind: &Type) -> bool {
        *kind == Type::TIMESTAMPTZ
    }
}

/// Historical NULL timestamps stay unknown until source-bound import or real update.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct StorageTimes {
    pub create: Option<StorageTimestamp>,
    pub update: Option<StorageTimestamp>,
}

impl StorageTimes {
    pub fn validate(self) -> Result<(), DomainError> {
        if let Some(create) = self.create {
            create.validate()?;
        }
        if let Some(update) = self.update {
            update.validate()?;
        }
        // No ordering assumption: database transaction clocks may move backward.
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StoredStorageObject {
    pub object: StorageObject,
    pub times: StorageTimes,
}

impl From<StorageObject> for StoredStorageObject {
    fn from(object: StorageObject) -> Self {
        Self {
            object,
            times: StorageTimes::default(),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StoredStorageMutationReceipt {
    pub receipt: MutationReceipt,
    pub times: StorageTimes,
}

impl From<MutationReceipt> for StoredStorageMutationReceipt {
    fn from(receipt: MutationReceipt) -> Self {
        Self {
            receipt,
            times: StorageTimes::default(),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StoredStorageClientListPage {
    pub objects: Vec<StoredStorageObject>,
    pub next: Option<StorageListPosition>,
}

impl From<StorageClientListPage> for StoredStorageClientListPage {
    fn from(page: StorageClientListPage) -> Self {
        Self {
            objects: page.objects.into_iter().map(Into::into).collect(),
            next: page.next,
        }
    }
}

const fn invalid_timestamp() -> DomainError {
    DomainError::new(
        StableCode::DataLoss,
        "invalid_storage_timestamp",
        RetryClass::Never,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pg_micros(seconds: i64, nanos: u32) -> i64 {
        (seconds - POSTGRES_EPOCH_SECONDS) * MICROSECONDS_PER_SECOND + i64::from(nanos / 1000)
    }

    #[test]
    fn timestamp_binary_decoding_preserves_microseconds_and_pre_epoch_normalization() {
        for value in [
            StorageTimestamp {
                seconds: 0,
                nanos: 0,
            },
            StorageTimestamp {
                seconds: -1,
                nanos: 999_999_000,
            },
            StorageTimestamp {
                seconds: -1,
                nanos: 123_456_000,
            },
            StorageTimestamp {
                seconds: POSTGRES_EPOCH_SECONDS,
                nanos: 1_000,
            },
            StorageTimestamp {
                seconds: MIN_PROTOBUF_SECONDS,
                nanos: 0,
            },
            StorageTimestamp {
                seconds: MAX_PROTOBUF_SECONDS,
                nanos: 999_999_000,
            },
        ] {
            let bytes = pg_micros(value.seconds, value.nanos).to_be_bytes();
            assert_eq!(
                StorageTimestamp::from_sql(&Type::TIMESTAMPTZ, &bytes).unwrap(),
                value
            );
        }
        assert!(StorageTimestamp::accepts(&Type::TIMESTAMPTZ));
        assert!(!StorageTimestamp::accepts(&Type::TIMESTAMP));
        assert!(!StorageTimestamp::accepts(&Type::INT8));
        assert!(StorageTimestamp::from_sql(&Type::INT8, &[0; 8]).is_err());
    }

    #[test]
    fn timestamp_binary_decoding_rejects_infinity_width_range_and_invalid_nanos() {
        for micros in [
            i64::MIN,
            i64::MAX,
            pg_micros(MIN_PROTOBUF_SECONDS - 1, 999_999_000),
            pg_micros(MAX_PROTOBUF_SECONDS + 1, 0),
        ] {
            let bytes = micros.to_be_bytes();
            assert!(StorageTimestamp::from_sql(&Type::TIMESTAMPTZ, &bytes).is_err());
        }
        for width in [0, 1, 7, 9, 16] {
            assert!(StorageTimestamp::from_sql(&Type::TIMESTAMPTZ, &vec![0; width]).is_err());
        }
        assert_eq!(
            StorageTimestamp::new(0, 1_000_000_000).unwrap_err().code(),
            StableCode::DataLoss
        );
        assert!(StorageTimestamp::new(MIN_PROTOBUF_SECONDS, 0).is_ok());
        assert!(StorageTimestamp::new(MAX_PROTOBUF_SECONDS, 999_999_999).is_ok());
        let unknown = StorageTimes::default();
        assert_eq!(unknown.create, None);
        assert_eq!(unknown.update, None);
        unknown.validate().unwrap();
        StorageTimes {
            create: Some(StorageTimestamp::new(100, 0).unwrap()),
            update: Some(StorageTimestamp::new(99, 0).unwrap()),
        }
        .validate()
        .unwrap();
    }
}
