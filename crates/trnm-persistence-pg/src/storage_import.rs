//! Source-bound offline storage transfer. Native values remain the only
//! payload authority; retained export text is never an original request.
//! Packet verification, operational custody and compatibility acceptance are
//! separate obligations. Local source-DDL fixtures confer no oracle credit.

use std::collections::{BTreeMap, BTreeSet};
use std::fs::File;
use std::io::Read;
use std::path::Path;

use postgres::{GenericClient, Transaction};
use serde::{Deserialize, Serialize};
use trnm_contracts::{DomainError, UserId};

use crate::{
    data_loss, failed_precondition, invalid, map_postgres_error, DatabaseProfile, IntegrityDigest,
    PublicVersion, ReadPermission, StorageObjectKey, StorageTimestamp, WritePermission,
};

const MAX_PACKET_BYTES: usize = 256 * 1024 * 1024;
const MAX_MANIFEST_BYTES: usize = 2 * 1024 * 1024;
const MAX_METADATA_BYTES: usize = 128 * 1024;
const MAX_ROWS_BYTES: usize = 16 * 1024 * 1024;
const MAX_ROWS: usize = 10_000;
const MAX_PAGE_ROWS: usize = 100;
const MAX_PAGE_BYTES: usize = 32 * 1024 * 1024;
const MAX_ROW_BYTES: usize = 16 * 1024 * 1024;

include!("storage_import_parts/packet.rs");
include!("storage_import_parts/exporter.rs");
include!("storage_import_parts/repository.rs");

impl crate::PgRepository {
    /// Read-only, live completion admission for startup/readiness and pooled
    /// business operations. Existing checked storage transactions also check
    /// this gate inside their own serializable snapshot.
    pub fn verify_storage_import_serving(&mut self) -> Result<(), DomainError> {
        let mut transaction = self
            .client
            .build_transaction()
            .isolation_level(postgres::IsolationLevel::Serializable)
            .read_only(true)
            .start()
            .map_err(map_postgres_error)?;
        match self.schema_admission.target() {
            crate::AuthoritativeSchemaTarget::StorageV4 => {
                crate::storage::verify_storage_writer_epoch_target(
                    &mut transaction,
                    self.profile,
                    self.serving_schema_target,
                )?;
            }
            crate::AuthoritativeSchemaTarget::NakamaAccountsV5 => {
                crate::schema::verify_serving_schema_admitted(
                    &mut transaction,
                    self.profile,
                    self.schema_admission,
                )?;
                verify_storage_import_serving(&mut transaction)?;
            }
        }
        transaction.commit().map_err(map_postgres_error)
    }
}

/// Mutable service operations share-lock the metadata row before checking
/// admission. Import registration/page/completion takes its exclusive lock.
/// PostgreSQL also rewrites that tuple with identical values after auditing
/// authority, forcing an older SERIALIZABLE waiter to abort instead of missing
/// the new job. CockroachDB's native locking/snapshot behavior is tested apart.
pub(crate) fn verify_business_storage_import_serving(
    transaction: &mut Transaction<'_>,
    profile: DatabaseProfile,
) -> Result<(), DomainError> {
    transaction
        .batch_execute("SET LOCAL search_path TO pg_catalog, public")
        .map_err(map_postgres_error)?;
    let row = transaction
        .query_one(
            "SELECT singleton FROM public.trnm_schema_metadata WHERE singleton=1 FOR SHARE",
            &[],
        )
        .map_err(map_postgres_error)?;
    if row.try_get::<_, i16>(0).map_err(map_postgres_error)? != 1 {
        return Err(data_loss("storage_import_admission_invalid"));
    }
    crate::storage::verify_storage_writer_epoch(transaction, profile)
}

/// Explicit selected-target bridge. The same metadata share lock and complete
/// import admission remain in the business transaction; no second authority.
pub(crate) fn verify_business_storage_import_serving_target(
    transaction: &mut Transaction<'_>,
    profile: DatabaseProfile,
    target: crate::AuthoritativeSchemaTarget,
) -> Result<(), DomainError> {
    if target == crate::AuthoritativeSchemaTarget::StorageV4 {
        return verify_business_storage_import_serving(transaction, profile);
    }
    target.require_capture_ready()?;
    transaction
        .batch_execute("SET LOCAL search_path TO pg_catalog, public")
        .map_err(map_postgres_error)?;
    let row = transaction
        .query_one(
            "SELECT singleton FROM public.trnm_schema_metadata WHERE singleton=1 FOR SHARE",
            &[],
        )
        .map_err(map_postgres_error)?;
    if row.try_get::<_, i16>(0).map_err(map_postgres_error)? != 1 {
        return Err(data_loss("storage_import_admission_invalid"));
    }
    crate::storage::verify_storage_writer_epoch_target(transaction, profile, target)
}

/// Called inside every ordinary storage transaction, including read-only
/// transactions, so a running process cannot expose a committed import prefix.
pub(crate) fn verify_storage_import_serving(
    transaction: &mut Transaction<'_>,
) -> Result<(), DomainError> {
    let row = transaction
        .query_one(
            "SELECT EXISTS(SELECT 1 FROM public.trnm_storage_import_jobs WHERE status <> 1)",
            &[],
        )
        .map_err(map_postgres_error)?;
    if row
        .try_get::<_, bool>(0)
        .map_err(|_| data_loss("storage_import_admission_invalid"))?
    {
        return Err(failed_precondition("storage_import_incomplete"));
    }
    Ok(())
}

fn digest_hex(value: IntegrityDigest) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut result = String::with_capacity(64);
    for byte in value.get().as_bytes() {
        result.push(char::from(HEX[usize::from(byte >> 4)]));
        result.push(char::from(HEX[usize::from(byte & 15)]));
    }
    result
}

fn parse_digest(value: &str) -> Result<IntegrityDigest, DomainError> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
    {
        return Err(invalid("storage_import_digest_invalid"));
    }
    let mut raw = [0_u8; 32];
    for (index, byte) in raw.iter_mut().enumerate() {
        *byte = u8::from_str_radix(&value[index * 2..index * 2 + 2], 16)
            .map_err(|_| invalid("storage_import_digest_invalid"))?;
    }
    IntegrityDigest::new(trnm_contracts::Digest32::new(raw))
        .map_err(|_| invalid("storage_import_digest_invalid"))
}

fn checked_commit(value: &str) -> bool {
    value.len() == 40
        && value
            .bytes()
            .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
        && value.bytes().any(|c| c != b'0')
}
