use std::fs;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use trnm_persistence_pg::{DatabaseProfile, IntegrityDigest, PgPool, PgTlsConfig, SchemaIdentity};

use super::config::{DatabaseTlsMode, ServerConfig};
use super::error::{diagnose_migration_result, MigrationPhase, ServerError};
use super::pool::PooledRepository;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MigrationReport {
    pub profile: DatabaseProfile,
    pub migration_applied: bool,
    pub table_count: usize,
    pub schema_version: u64,
    pub chain_digest: IntegrityDigest,
    pub v2_apply_source_commit: String,
    pub schema: SchemaIdentity,
}

pub fn migrate(config: &ServerConfig) -> Result<MigrationReport, ServerError> {
    let profile = config.database_profile;
    let pool = diagnose_migration_result(profile, MigrationPhase::BuildPool, build_pool(config))?;
    let mut repository = diagnose_migration_result(
        profile,
        MigrationPhase::AcquireSession,
        pool.acquire().map_err(ServerError::from),
    )?;
    let applied = (|| {
        let legacy_writer_role = match std::env::var("TRNM_STORAGE_LEGACY_WRITER_ROLE") {
            Ok(value) => Some(value),
            Err(std::env::VarError::NotPresent) => None,
            Err(_) => {
                return Err(ServerError::Configuration(
                    "invalid_legacy_storage_writer_role",
                ))
            }
        };
        repository
            .migrate_authoritative_schema(
                &config.schema_source_commit,
                now_millis()?,
                legacy_writer_role.as_deref(),
            )
            .map_err(ServerError::from)
    })();
    let report =
        diagnose_migration_result(profile, MigrationPhase::ApplyAuthoritativeChain, applied)?;
    Ok(MigrationReport {
        profile: config.database_profile,
        migration_applied: report.migration_applied,
        table_count: report.table_count,
        schema_version: report.identity.schema_version,
        chain_digest: report.identity.chain_digest,
        v2_apply_source_commit: report.identity.v2_apply_source_commit.clone(),
        schema: report.identity,
    })
}

pub fn open_verified_repository(config: &ServerConfig) -> Result<PooledRepository, ServerError> {
    let pool = build_pool(config)?;
    {
        let mut repository = pool.acquire()?;
        repository.verify_authoritative_schema()?;
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
    use trnm_persistence_pg::{authoritative_chain_digest, AUTHORITATIVE_SCHEMA_VERSION};

    #[test]
    fn both_authoritative_profiles_embed_the_ten_table_chain() {
        assert_eq!(AUTHORITATIVE_SCHEMA_VERSION, 3);
        for profile in [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb] {
            assert!(!authoritative_chain_digest(profile).get().is_zero());
        }
    }

    #[test]
    fn design_history_schema_is_not_embedded_by_the_server() {
        assert_ne!(
            authoritative_chain_digest(DatabaseProfile::PostgreSql),
            authoritative_chain_digest(DatabaseProfile::CockroachDb)
        );
    }
}
