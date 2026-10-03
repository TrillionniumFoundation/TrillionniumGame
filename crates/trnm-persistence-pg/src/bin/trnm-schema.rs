#![forbid(unsafe_code)]

use std::env;
use std::fmt::Write as _;
use std::time::Duration;
use std::time::{SystemTime, UNIX_EPOCH};

use trnm_persistence_pg::{DatabaseProfile, PgPool, PgPoolConfig, SchemaMigrationReport};

fn main() {
    if let Err(reason) = run() {
        // Stable nonsecret reasons only. Connection strings and driver details
        // are never interpolated into this migration process's output.
        eprintln!("trnm-schema: {reason}");
        std::process::exit(1);
    }
}

fn required(name: &str) -> Result<String, &'static str> {
    env::var(name)
        .ok()
        .filter(|value| !value.is_empty())
        .ok_or("required_schema_environment_missing")
}

fn run() -> Result<(), &'static str> {
    let mut arguments = env::args().skip(1);
    let mode = arguments.next().ok_or("schema_command_required")?;
    if mode == "--help" {
        println!("trnm-schema <migrate|verify> --candidate-plaintext\nEnvironment: TRNM_DATABASE_URL, TRNM_DATABASE_PROFILE; migrate also requires TRNM_SCHEMA_SOURCE_COMMIT, optional TRNM_SCHEMA_APPLIED_AT_MS and TRNM_STORAGE_LEGACY_WRITER_ROLE. Candidate plain PG-wire only; no compatibility credit.");
        return Ok(());
    }
    if !matches!(mode.as_str(), "migrate" | "verify") {
        return Err("unsupported_schema_command");
    }
    if arguments.next().as_deref() != Some("--candidate-plaintext") || arguments.next().is_some() {
        return Err("candidate_plaintext_flag_required");
    }
    let database_url = required("TRNM_DATABASE_URL")?;
    let profile = match required("TRNM_DATABASE_PROFILE")?.as_str() {
        "postgresql" => DatabaseProfile::PostgreSql,
        "cockroachdb" => DatabaseProfile::CockroachDb,
        _ => return Err("invalid_database_profile"),
    };
    let pool = PgPool::connect_plain(
        &database_url,
        profile,
        PgPoolConfig {
            max_size: 1,
            min_idle: 0,
            acquire_timeout: Duration::from_secs(2),
            statement_timeout: Duration::from_secs(10),
            lock_timeout: Duration::from_secs(2),
            ..PgPoolConfig::default()
        },
    )
    .map_err(|error| error.reason())?;
    let mut repository = pool.acquire().map_err(|error| error.reason())?;
    let report = if mode == "migrate" {
        let source_commit = required("TRNM_SCHEMA_SOURCE_COMMIT")?;
        let applied_at_ms = match env::var("TRNM_SCHEMA_APPLIED_AT_MS") {
            Ok(value) => value
                .parse::<u64>()
                .map_err(|_| "invalid_schema_applied_at_ms")?,
            Err(env::VarError::NotPresent) => u64::try_from(
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .map_err(|_| "schema_audit_clock_before_epoch")?
                    .as_millis(),
            )
            .map_err(|_| "schema_audit_clock_overflow")?,
            Err(_) => return Err("invalid_schema_applied_at_ms"),
        };
        let role = match env::var("TRNM_STORAGE_LEGACY_WRITER_ROLE") {
            Ok(value) => Some(value),
            Err(env::VarError::NotPresent) => None,
            Err(_) => return Err("invalid_legacy_storage_writer_role"),
        };
        repository
            .migrate_authoritative_schema(&source_commit, applied_at_ms, role.as_deref())
            .map_err(|error| error.reason())?
    } else {
        SchemaMigrationReport {
            identity: repository
                .verify_authoritative_schema()
                .map_err(|error| error.reason())?,
            migration_applied: false,
            table_count: 12,
            applied_steps: 0,
        }
    };
    let mut digest = String::with_capacity(64);
    for byte in report.identity.chain_digest.get().as_bytes() {
        write!(digest, "{byte:02x}").expect("String write");
    }
    println!(
        "{}",
        serde_json::json!({
            "schema":"trillionnium.authoritative-schema-report.v1",
            "profile":report.identity.profile.metadata_value(),
            "schema_version":report.identity.schema_version,
            "chain_digest":digest,
            "digest_algorithm":report.identity.digest_algorithm,
            "storage_writer_epoch":report.identity.storage_writer_epoch,
            "source_commit":report.identity.source_commit,
            "upgrade_source_commit":report.identity.upgrade_source_commit,
            "v2_apply_source_commit":report.identity.v2_apply_source_commit,
            "v3_apply_source_commit":report.identity.v3_apply_source_commit,
            "migration_applied":report.migration_applied,
            "table_count":report.table_count,
            "applied_steps":report.applied_steps,
            "compatibility_credit":false
        })
    );
    Ok(())
}
