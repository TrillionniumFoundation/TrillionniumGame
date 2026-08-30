use std::fs;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use trnm_persistence_pg::{DatabaseProfile, PgPool, PgRepository, PgTlsConfig};

use super::config::{DatabaseTlsMode, ServerConfig};
use super::error::ServerError;
use super::pool::PooledRepository;

const POSTGRESQL_MIGRATION: &str =
    include_str!("../../../../../migrations/postgresql/0001_foundation_up.sql");
const COCKROACHDB_MIGRATION: &str =
    include_str!("../../../../../migrations/cockroachdb/0001_foundation_up.sql");
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

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct MigrationReport {
    pub profile: DatabaseProfile,
    pub migration_applied: bool,
    pub table_count: usize,
}

pub fn migrate(config: &ServerConfig) -> Result<MigrationReport, ServerError> {
    let pool = build_pool(config)?;
    let mut repository = pool.acquire()?;
    let metadata_exists = repository.table_exists("trnm_schema_metadata")?;
    if !metadata_exists {
        repository.execute_migration_batch(migration_for(config.database_profile))?;
    }
    verify_required_tables(&mut repository)?;
    repository.bind_schema_metadata(&config.schema_source_commit, now_millis()?)?;
    Ok(MigrationReport {
        profile: config.database_profile,
        migration_applied: !metadata_exists,
        table_count: REQUIRED_TABLES.len(),
    })
}

pub fn open_verified_repository(config: &ServerConfig) -> Result<PooledRepository, ServerError> {
    let pool = build_pool(config)?;
    {
        let mut repository = pool.acquire()?;
        verify_required_tables(&mut repository)?;
        repository.bind_schema_metadata(&config.schema_source_commit, now_millis()?)?;
    }
    Ok(PooledRepository::new(pool))
}

fn build_pool(config: &ServerConfig) -> Result<PgPool, ServerError> {
    match config.database_tls_mode {
        DatabaseTlsMode::PlaintextCandidate => Ok(PgPool::connect_plain(
            &config.database_url,
            config.database_profile,
            config.database_pool,
        )?),
        DatabaseTlsMode::VerifyFull => {
            let root_certificate = read_optional(config.database_tls_root_cert.as_deref())?;
            let identity_certificate = read_optional(config.database_tls_identity_cert.as_deref())?;
            let identity_key = read_optional(config.database_tls_identity_key.as_deref())?;
            let tls = PgTlsConfig::new(root_certificate, identity_certificate, identity_key)?;
            Ok(PgPool::connect_tls(
                &config.database_url,
                config.database_profile,
                config.database_pool,
                &tls,
            )?)
        }
    }
}

fn read_optional(path: Option<&Path>) -> Result<Option<Vec<u8>>, ServerError> {
    path.map(fs::read).transpose().map_err(ServerError::from)
}

fn migration_for(profile: DatabaseProfile) -> &'static str {
    match profile {
        DatabaseProfile::PostgreSql => POSTGRESQL_MIGRATION,
        DatabaseProfile::CockroachDb => COCKROACHDB_MIGRATION,
    }
}

fn verify_required_tables(repository: &mut PgRepository) -> Result<(), ServerError> {
    for table in REQUIRED_TABLES {
        if !repository.table_exists(table)? {
            return Err(ServerError::Configuration(
                "authoritative_schema_table_missing",
            ));
        }
    }
    Ok(())
}

fn now_millis() -> Result<u64, ServerError> {
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| ServerError::Configuration("system_clock_before_unix_epoch"))?;
    u64::try_from(duration.as_millis())
        .map_err(|_| ServerError::Configuration("system_clock_millis_overflow"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn both_authoritative_profiles_embed_the_ten_table_chain() {
        for profile in [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb] {
            let migration = migration_for(profile);
            for table in REQUIRED_TABLES {
                assert!(
                    migration.contains(&format!("CREATE TABLE {table}")),
                    "{profile:?}:{table}"
                );
            }
            assert!(migration.contains("BEGIN;"));
            assert!(migration.contains("COMMIT;"));
        }
    }

    #[test]
    fn design_history_schema_is_not_embedded_by_the_server() {
        for profile in [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb] {
            let migration = migration_for(profile);
            assert!(!migration.contains("tenant_id"));
            assert!(migration.contains("trnm_schema_metadata"));
        }
    }
}
