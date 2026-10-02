//! The locked production-authoritative schema chain and read-only serve fence.
//! Source identity is not migration or compatibility acceptance.

use std::collections::BTreeMap;
use std::ops::Range;

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

#[derive(Clone, Debug)]
struct RevisionDescriptor {
    version: u64,
    /// Same tagged digest over only the files through this revision.
    chain_digest: [u8; 32],
    /// v1 has no persisted writer epoch; later ABIs are explicitly declared.
    storage_writer_epoch: Option<u64>,
    /// Half-open range in the profile's append-only action inventory.
    action_range: Range<usize>,
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
    let declared = match profile {
        DatabaseProfile::PostgreSql => POSTGRESQL_ACTIONS,
        DatabaseProfile::CockroachDb => COCKROACHDB_ACTIONS,
    };
    debug_assert!(declared.iter().all(|action| !action.id.is_empty()));
    let mut next_action = 0;
    for descriptor in revisions(profile) {
        debug_assert_eq!(descriptor.action_range.start, next_action);
        debug_assert!(descriptor.action_range.end <= declared.len());
        next_action = descriptor.action_range.end;
    }
    debug_assert_eq!(next_action, declared.len());
    declared
}

fn revisions(profile: DatabaseProfile) -> &'static [RevisionDescriptor] {
    match profile {
        DatabaseProfile::PostgreSql => POSTGRESQL_REVISIONS,
        DatabaseProfile::CockroachDb => COCKROACHDB_REVISIONS,
    }
}

/// Historical descriptors do not grant serve readiness for an older ABI.
fn revision(profile: DatabaseProfile, version: u64) -> Option<&'static RevisionDescriptor> {
    let index = usize::try_from(version.checked_sub(1)?).ok()?;
    revisions(profile)
        .get(index)
        .filter(|descriptor| descriptor.version == version)
}

fn digest_for_steps(chain: &[MigrationStep]) -> IntegrityDigest {
    let mut input = Vec::new();
    for (index, step) in chain.iter().enumerate() {
        input.extend_from_slice(&(index as u64).to_be_bytes());
        input.extend_from_slice(step.path.as_bytes());
        input.push(0);
        input.extend_from_slice(&step.git_blob);
    }
    IntegrityDigest::from_value(&input)
}

fn chain_digest_at(profile: DatabaseProfile, version: u64) -> Option<IntegrityDigest> {
    let descriptor = revision(profile, version)?;
    let end = usize::try_from(version).ok()?;
    let chain = steps(profile).get(..end)?;
    debug_assert!(chain
        .iter()
        .enumerate()
        .all(|(index, step)| step.version == index as u64 + 1));
    let digest = digest_for_steps(chain);
    debug_assert_eq!(digest.get(), Digest32::new(descriptor.chain_digest));
    Some(digest)
}

#[must_use]
pub fn authoritative_chain_digest(profile: DatabaseProfile) -> IntegrityDigest {
    let current = revision(profile, AUTHORITATIVE_SCHEMA_VERSION)
        .expect("the locked current schema revision is embedded");
    debug_assert_eq!(
        current.storage_writer_epoch,
        Some(AUTHORITATIVE_STORAGE_WRITER_EPOCH)
    );
    let digest = chain_digest_at(profile, AUTHORITATIVE_SCHEMA_VERSION)
        .expect("the locked current migration prefix is embedded");
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
    fn historical_prefix_identities_do_not_absorb_later_files() {
        // These pre-registry v1/v2 identities must survive append-only changes;
        // comparing only two hashes produced by this code misses history drift.
        for (profile, foundation, timestamps) in [
            (
                DatabaseProfile::PostgreSql,
                "f4939a8aa5eeba0fdb2eba6f1f3d09f678d3a40a3b743611591dd1e006858223",
                "b063c33fce9a7c3c506f82c0204b545d8a5ef915b0ff234680fca15057c4a9df",
            ),
            (
                DatabaseProfile::CockroachDb,
                "558d7ef5b177451cd0548c3abd4aaa3c248c15bab54470d389ac420f302bcb4e",
                "85892562d78571090202d5b2d6bb2941364614399a82542f4cf809370c0e4b0c",
            ),
        ] {
            assert_eq!(digest_hex(chain_digest_at(profile, 1).unwrap()), foundation);
            assert_eq!(digest_hex(chain_digest_at(profile, 2).unwrap()), timestamps);
            assert_eq!(
                chain_digest_at(profile, 2).unwrap(),
                authoritative_chain_digest(profile)
            );
            let current = steps(profile);
            let extended = [
                current[0],
                current[1],
                MigrationStep {
                    version: 3,
                    path: "synthetic-later-file",
                    git_blob: [7; 20],
                    sql: "",
                },
            ];
            assert_eq!(digest_hex(digest_for_steps(&extended[..1])), foundation);
            assert_eq!(digest_hex(digest_for_steps(&extended[..2])), timestamps);
            assert_ne!(digest_hex(digest_for_steps(&extended)), timestamps);
        }
    }

    #[test]
    fn ordered_prefix_identity_detects_path_blob_and_order_changes() {
        for profile in [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb] {
            let chain = steps(profile);
            let expected = chain_digest_at(profile, 2).unwrap();
            let mut changed_path = [chain[0], chain[1]];
            changed_path[0].path = "a-different-foundation-path";
            let mut changed_blob = [chain[0], chain[1]];
            changed_blob[0].git_blob[0] ^= 1;
            for mutant in [changed_path, changed_blob, [chain[1], chain[0]]] {
                assert_ne!(digest_for_steps(&mutant), expected);
            }
        }
    }

    #[test]
    fn revision_ranges_preserve_v2_action_order_and_reject_unknown_revisions() {
        let expected_actions = [
            "metadata_chain_digest",
            "metadata_digest_algorithm",
            "metadata_storage_writer_epoch",
            "metadata_upgrade_source_commit",
            "storage_create_time",
            "storage_update_time",
        ];
        for profile in [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb] {
            let foundation = revision(profile, 1).unwrap();
            let timestamps = revision(profile, 2).unwrap();
            assert_eq!(foundation.storage_writer_epoch, None);
            assert_eq!(foundation.action_range, 0..0);
            assert!(actions(profile)[foundation.action_range.clone()].is_empty());
            assert_eq!(timestamps.storage_writer_epoch, Some(2));
            assert_eq!(timestamps.action_range, 0..expected_actions.len());
            let actual: Vec<_> = actions(profile)[timestamps.action_range.clone()]
                .iter()
                .map(|action| action.id)
                .collect();
            assert_eq!(actual, expected_actions);
            for unknown in [0, 3, u64::MAX] {
                assert!(revision(profile, unknown).is_none());
                assert!(chain_digest_at(profile, unknown).is_none());
            }
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
