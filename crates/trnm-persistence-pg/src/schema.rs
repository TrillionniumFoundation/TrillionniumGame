//! The locked production-authoritative schema chain and read-only serve fence.
//! Source identity is not migration or compatibility acceptance.

use std::collections::BTreeMap;

use postgres::{GenericClient, IsolationLevel};
use trnm_contracts::{Digest32, DomainError};

use crate::{data_loss, failed_precondition, invalid, map_postgres_error, to_i64};
use crate::{DatabaseProfile, IntegrityDigest, PgRepository};

pub const AUTHORITATIVE_SCHEMA_VERSION: u64 = 2;
pub const AUTHORITATIVE_STORAGE_WRITER_EPOCH: u64 = 2;
pub const AUTHORITATIVE_CHAIN_DIGEST_ALGORITHM: &str = "ordered-path-git-blob-sha256.v1";
const REQUIRED_TABLES: [&str; 10] = [
    "trnm_schema_metadata",
    "trnm_entity_heads",
    "trnm_command_receipts",
    "trnm_events",
    "trnm_outbox",
    "trnm_command_outbox",
    "trnm_authority_leases",
    "trnm_session_families",
    "trnm_refresh_tokens",
    "trnm_storage_objects",
];

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SchemaIdentity {
    pub profile: DatabaseProfile,
    pub schema_version: u64,
    pub chain_digest: IntegrityDigest,
    pub digest_algorithm: &'static str,
    pub storage_writer_epoch: u64,
    /// Original foundation provenance, retained when schema v2 is published.
    pub source_commit: String,
    pub upgrade_source_commit: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SchemaMigrationReport {
    pub identity: SchemaIdentity,
    pub migration_applied: bool,
    pub table_count: usize,
    /// Number of migration files newly completed, rather than SQL statements.
    pub applied_steps: usize,
}

#[derive(Clone, Copy, Debug)]
struct MigrationStep {
    version: u64,
    path: &'static str,
    git_blob: [u8; 20],
    sql: &'static str,
}

#[derive(Clone, Copy, Debug)]
struct ColumnDescriptor {
    table: &'static str,
    name: &'static str,
    kind: &'static str,
    nullable: bool,
    default_zero: bool,
}

#[derive(Clone, Copy, Debug)]
struct MigrationAction {
    id: &'static str,
    column: ColumnDescriptor,
    sql: &'static str,
}

include!(concat!(env!("OUT_DIR"), "/authoritative_schema.rs"));
include!("schema_parts/catalog.rs");
include!("schema_parts/metadata.rs");
include!("schema_parts/migrate.rs");

fn steps(profile: DatabaseProfile) -> &'static [MigrationStep] {
    match profile {
        DatabaseProfile::PostgreSql => POSTGRESQL_STEPS,
        DatabaseProfile::CockroachDb => COCKROACHDB_STEPS,
    }
}

fn baseline(profile: DatabaseProfile) -> &'static [ColumnDescriptor] {
    match profile {
        DatabaseProfile::PostgreSql => POSTGRESQL_BASELINE,
        DatabaseProfile::CockroachDb => COCKROACHDB_BASELINE,
    }
}

fn actions(profile: DatabaseProfile) -> &'static [MigrationAction] {
    match profile {
        DatabaseProfile::PostgreSql => POSTGRESQL_ACTIONS,
        DatabaseProfile::CockroachDb => COCKROACHDB_ACTIONS,
    }
}

#[must_use]
pub fn authoritative_chain_digest(profile: DatabaseProfile) -> IntegrityDigest {
    let mut input = Vec::new();
    for (index, step) in steps(profile).iter().enumerate() {
        debug_assert_eq!(step.version, index as u64 + 1);
        input.extend_from_slice(&(index as u64).to_be_bytes());
        input.extend_from_slice(step.path.as_bytes());
        input.push(0);
        input.extend_from_slice(&step.git_blob);
    }
    let digest = IntegrityDigest::from_value(&input);
    let expected = match profile {
        DatabaseProfile::PostgreSql => POSTGRESQL_CHAIN_BYTES,
        DatabaseProfile::CockroachDb => COCKROACHDB_CHAIN_BYTES,
    };
    debug_assert_eq!(digest.get(), Digest32::new(expected));
    digest
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn embedded_chain_uses_the_tagged_complete_lock_identity() {
        for (profile, expected) in [
            (DatabaseProfile::PostgreSql, POSTGRESQL_CHAIN_BYTES),
            (DatabaseProfile::CockroachDb, COCKROACHDB_CHAIN_BYTES),
        ] {
            assert_eq!(
                authoritative_chain_digest(profile).get(),
                Digest32::new(expected)
            );
            let chain = steps(profile);
            assert_eq!(chain.len(), 2);
            assert_eq!(chain[0].version, 1);
            assert_eq!(chain[1].version, AUTHORITATIVE_SCHEMA_VERSION);
            assert!(chain[0].sql.contains("BEGIN;"));
            assert!(chain[0].sql.contains("COMMIT;"));
            assert!(!chain
                .iter()
                .any(|step| step.path.contains("database/schema")));
            assert_eq!(actions(profile).len(), 6);
            // Nullable bare type declarations still belong to the baseline;
            // dropping their comma-suffixed type token rejected real v1 DBs.
            assert_eq!(baseline(profile).len(), 71);
            assert!(baseline(profile)
                .iter()
                .any(|column| column.table == "trnm_command_receipts"
                    && column.name == "first_event_sequence"
                    && column.nullable));
            assert!(baseline(profile)
                .iter()
                .any(|column| column.table == "trnm_refresh_tokens"
                    && column.name == "consumed_at_ms"
                    && column.nullable));
            assert!(actions(profile).iter().all(|action| action.column.nullable
                && !action.column.default_zero
                && !action.sql.contains("DEFAULT")));
        }
    }

    #[test]
    fn role_and_commit_inputs_are_bounded_before_sql() {
        assert!(validate_source_commit(&"a".repeat(40)).is_ok());
        assert!(validate_source_commit(&"a".repeat(39)).is_err());
        assert!(validate_source_commit(&"g".repeat(40)).is_err());
        for bad in [
            "",
            "PUBLIC",
            "public",
            "root",
            "admin",
            "x; DROP ROLE x",
            "x.y",
            "x\0",
            " space",
        ] {
            assert!(validate_legacy_role(bad).is_err(), "{bad:?}");
        }
        assert!(validate_legacy_role("legacy_storage_writer_1").is_ok());
        assert!(validate_legacy_role(&"x".repeat(64)).is_err());
    }
}
