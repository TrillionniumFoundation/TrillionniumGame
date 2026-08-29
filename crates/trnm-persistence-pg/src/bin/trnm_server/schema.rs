use std::time::{SystemTime, UNIX_EPOCH};

use postgres::{Client, NoTls};
use trnm_persistence_pg::{DatabaseProfile, PgRepository};

use super::config::ServerConfig;
use super::error::ServerError;

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
    let mut client = Client::connect(&config.database_url, NoTls)?;
    let metadata_exists = table_exists(&mut client, "trnm_schema_metadata")?;
    if !metadata_exists {
        client.batch_execute(migration_for(config.database_profile))?;
    }
    verify_required_tables(&mut client)?;
    drop(client);

    let mut repository = PgRepository::connect(&config.database_url, config.database_profile)?;
    repository.bind_schema_metadata(&config.schema_source_commit, now_millis()?)?;
    Ok(MigrationReport {
        profile: config.database_profile,
        migration_applied: !metadata_exists,
        table_count: REQUIRED_TABLES.len(),
    })
}

pub fn open_verified_repository(config: &ServerConfig) -> Result<PgRepository, ServerError> {
    let mut client = Client::connect(&config.database_url, NoTls)?;
    verify_required_tables(&mut client)?;
    drop(client);

    let mut repository = PgRepository::connect(&config.database_url, config.database_profile)?;
    repository.bind_schema_metadata(&config.schema_source_commit, now_millis()?)?;
    Ok(repository)
}

fn migration_for(profile: DatabaseProfile) -> &'static str {
    match profile {
        DatabaseProfile::PostgreSql => POSTGRESQL_MIGRATION,
        DatabaseProfile::CockroachDb => COCKROACHDB_MIGRATION,
    }
}

fn verify_required_tables(client: &mut Client) -> Result<(), ServerError> {
    for table in REQUIRED_TABLES {
        if !table_exists(client, table)? {
            return Err(ServerError::Configuration("authoritative_schema_table_missing"));
        }
    }
    Ok(())
}

fn table_exists(client: &mut Client, table: &str) -> Result<bool, ServerError> {
    let row = client.query_one(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables \
         WHERE table_schema = 'public' AND table_name = $1)",
        &[&table],
    )?;
    Ok(row.get(0))
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
                assert!(migration.contains(&format!("CREATE TABLE {table}")), "{profile:?}:{table}");
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
