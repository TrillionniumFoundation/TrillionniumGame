#![forbid(unsafe_code)]

use std::env;
use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use trnm_contracts::{Digest32, DomainError};
use trnm_persistence_pg::{
    DatabaseProfile, IntentKind, NodeId, OutboxLease, OutboxRetryOutcome, PgPool, PgPoolConfig,
    PgTlsConfig,
};
use trnm_token_jwt_adapter::sha256_digest;

const DEFAULT_BATCH_SIZE: u64 = 16;
const DEFAULT_LEASE_DURATION_MS: u64 = 30_000;
const DEFAULT_MAX_ATTEMPTS: u64 = 8;
const DEFAULT_POLL_INTERVAL_MS: u64 = 250;
const DEFAULT_MAX_BACKOFF_MS: u64 = 60_000;
const DEFAULT_POOL_MAX_SIZE: u64 = 4;
const DEFAULT_POOL_MIN_IDLE: u64 = 1;
const DEFAULT_POOL_ACQUIRE_TIMEOUT_MS: u64 = 2_000;
const DEFAULT_STATEMENT_TIMEOUT_MS: u64 = 5_000;
const MAX_CONSECUTIVE_DATABASE_FAILURES: u32 = 20;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Command {
    CheckConfig,
    RunOnce,
    Serve,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum DatabaseTlsMode {
    PlaintextCandidate,
    VerifyFull,
}

#[derive(Clone)]
struct WorkerConfig {
    database_url: String,
    database_profile: DatabaseProfile,
    database_tls_mode: DatabaseTlsMode,
    database_tls_root_cert: Option<PathBuf>,
    database_tls_identity_cert: Option<PathBuf>,
    database_tls_identity_key: Option<PathBuf>,
    database_pool: PgPoolConfig,
    node: NodeId,
    spool_directory: PathBuf,
    stop_file: Option<PathBuf>,
    batch_size: usize,
    lease_duration_ms: u64,
    max_attempts: u32,
    poll_interval: Duration,
    max_backoff_ms: u64,
}

impl fmt::Debug for WorkerConfig {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("WorkerConfig")
            .field("database_url", &"<redacted>")
            .field("database_profile", &self.database_profile)
            .field("database_tls_mode", &self.database_tls_mode)
            .field(
                "database_tls_root_cert_configured",
                &self.database_tls_root_cert.is_some(),
            )
            .field(
                "database_tls_identity_configured",
                &self.database_tls_identity_cert.is_some(),
            )
            .field("database_tls_identity_key", &"<redacted>")
            .field("database_pool", &self.database_pool)
            .field("node", &encode_hex(self.node.as_bytes()))
            .field("spool_directory", &self.spool_directory)
            .field("stop_file", &self.stop_file)
            .field("batch_size", &self.batch_size)
            .field("lease_duration_ms", &self.lease_duration_ms)
            .field("max_attempts", &self.max_attempts)
            .field("poll_interval", &self.poll_interval)
            .field("max_backoff_ms", &self.max_backoff_ms)
            .finish()
    }
}

impl WorkerConfig {
    fn from_environment(arguments: &[String]) -> Result<(Command, Self), WorkerError> {
        Self::from_lookup(arguments, |name| env::var(name).ok())
    }

    fn from_lookup(
        arguments: &[String],
        lookup: impl Fn(&str) -> Option<String>,
    ) -> Result<(Command, Self), WorkerError> {
        let command = match arguments {
            [_, value] if value == "check-config" => Command::CheckConfig,
            [_, value] if value == "run-once" => Command::RunOnce,
            [_, value] if value == "serve" => Command::Serve,
            _ => {
                return Err(WorkerError::Configuration(
                    "command_must_be_check_config_run_once_or_serve",
                ));
            }
        };

        let database_url = required(&lookup, "TRNM_OUTBOX_DATABASE_URL", "database_url_missing")?;
        if database_url.len() > 4096
            || database_url
                .bytes()
                .any(|byte| byte.is_ascii_whitespace() || byte.is_ascii_control())
        {
            return Err(WorkerError::Configuration("database_url_invalid"));
        }

        let database_profile = match required(
            &lookup,
            "TRNM_OUTBOX_DATABASE_PROFILE",
            "database_profile_missing",
        )?
        .as_str()
        {
            "postgresql" => DatabaseProfile::PostgreSql,
            "cockroachdb" => DatabaseProfile::CockroachDb,
            _ => return Err(WorkerError::Configuration("database_profile_invalid")),
        };

        let database_tls_mode = match lookup("TRNM_OUTBOX_DATABASE_TLS_MODE").as_deref() {
            None | Some("plaintext-candidate") => DatabaseTlsMode::PlaintextCandidate,
            Some("verify-full") => DatabaseTlsMode::VerifyFull,
            Some(_) => return Err(WorkerError::Configuration("database_tls_mode_invalid")),
        };
        let allow_plaintext = parse_bool(
            lookup("TRNM_OUTBOX_ALLOW_PLAINTEXT_DATABASE").as_deref(),
            false,
            "allow_plaintext_database_invalid",
        )?;
        match database_tls_mode {
            DatabaseTlsMode::PlaintextCandidate if !allow_plaintext => {
                return Err(WorkerError::Configuration(
                    "plaintext_database_requires_explicit_candidate_opt_in",
                ));
            }
            DatabaseTlsMode::VerifyFull if allow_plaintext => {
                return Err(WorkerError::Configuration(
                    "tls_mode_conflicts_with_plaintext_opt_in",
                ));
            }
            _ => {}
        }

        let database_tls_root_cert =
            optional_path(lookup("TRNM_OUTBOX_DATABASE_TLS_ROOT_CERT_PEM"))?;
        let database_tls_identity_cert =
            optional_path(lookup("TRNM_OUTBOX_DATABASE_TLS_IDENTITY_CERT_PEM"))?;
        let database_tls_identity_key =
            optional_path(lookup("TRNM_OUTBOX_DATABASE_TLS_IDENTITY_KEY_PKCS8_PEM"))?;
        if database_tls_identity_cert.is_some() != database_tls_identity_key.is_some() {
            return Err(WorkerError::Configuration(
                "database_tls_identity_cert_key_pair_required",
            ));
        }
        if database_tls_mode == DatabaseTlsMode::PlaintextCandidate
            && (database_tls_root_cert.is_some()
                || database_tls_identity_cert.is_some()
                || database_tls_identity_key.is_some())
        {
            return Err(WorkerError::Configuration(
                "database_tls_material_requires_verify_full",
            ));
        }

        let node = NodeId::new(parse_lower_hex::<16>(&required(
            &lookup,
            "TRNM_OUTBOX_NODE_ID_HEX",
            "node_id_missing",
        )?)?);
        if node.is_zero() {
            return Err(WorkerError::Configuration("node_id_invalid"));
        }
        let spool_directory = required_path(
            &lookup,
            "TRNM_OUTBOX_SPOOL_DIRECTORY",
            "spool_directory_missing",
        )?;
        let stop_file = optional_path(lookup("TRNM_OUTBOX_STOP_FILE"))?;

        let batch_size = usize::try_from(parse_u64(
            lookup("TRNM_OUTBOX_BATCH_SIZE").as_deref(),
            DEFAULT_BATCH_SIZE,
            1,
            64,
            "batch_size_invalid",
        )?)
        .map_err(|_| WorkerError::Configuration("batch_size_invalid"))?;
        let lease_duration_ms = parse_u64(
            lookup("TRNM_OUTBOX_LEASE_DURATION_MS").as_deref(),
            DEFAULT_LEASE_DURATION_MS,
            1_000,
            10 * 60_000,
            "lease_duration_invalid",
        )?;
        let max_attempts = u32::try_from(parse_u64(
            lookup("TRNM_OUTBOX_MAX_ATTEMPTS").as_deref(),
            DEFAULT_MAX_ATTEMPTS,
            1,
            100,
            "max_attempts_invalid",
        )?)
        .map_err(|_| WorkerError::Configuration("max_attempts_invalid"))?;
        let poll_interval_ms = parse_u64(
            lookup("TRNM_OUTBOX_POLL_INTERVAL_MS").as_deref(),
            DEFAULT_POLL_INTERVAL_MS,
            10,
            60_000,
            "poll_interval_invalid",
        )?;
        let max_backoff_ms = parse_u64(
            lookup("TRNM_OUTBOX_MAX_BACKOFF_MS").as_deref(),
            DEFAULT_MAX_BACKOFF_MS,
            100,
            24 * 60 * 60_000,
            "max_backoff_invalid",
        )?;

        let pool_max_size = u32::try_from(parse_u64(
            lookup("TRNM_OUTBOX_DATABASE_POOL_MAX_SIZE").as_deref(),
            DEFAULT_POOL_MAX_SIZE,
            1,
            64,
            "database_pool_max_size_invalid",
        )?)
        .map_err(|_| WorkerError::Configuration("database_pool_max_size_invalid"))?;
        let pool_min_idle = u32::try_from(parse_u64(
            lookup("TRNM_OUTBOX_DATABASE_POOL_MIN_IDLE").as_deref(),
            DEFAULT_POOL_MIN_IDLE,
            0,
            u64::from(pool_max_size),
            "database_pool_min_idle_invalid",
        )?)
        .map_err(|_| WorkerError::Configuration("database_pool_min_idle_invalid"))?;
        let acquire_timeout_ms = parse_u64(
            lookup("TRNM_OUTBOX_DATABASE_POOL_ACQUIRE_TIMEOUT_MS").as_deref(),
            DEFAULT_POOL_ACQUIRE_TIMEOUT_MS,
            10,
            120_000,
            "database_pool_acquire_timeout_invalid",
        )?;
        let statement_timeout_ms = parse_u64(
            lookup("TRNM_OUTBOX_DATABASE_STATEMENT_TIMEOUT_MS").as_deref(),
            DEFAULT_STATEMENT_TIMEOUT_MS,
            50,
            600_000,
            "database_statement_timeout_invalid",
        )?;
        let default_pool = PgPoolConfig::default();
        let database_pool = PgPoolConfig {
            max_size: pool_max_size,
            min_idle: pool_min_idle,
            acquire_timeout: Duration::from_millis(acquire_timeout_ms),
            statement_timeout: Duration::from_millis(statement_timeout_ms),
            ..default_pool
        }
        .validate()?;

        Ok((
            command,
            Self {
                database_url,
                database_profile,
                database_tls_mode,
                database_tls_root_cert,
                database_tls_identity_cert,
                database_tls_identity_key,
                database_pool,
                node,
                spool_directory,
                stop_file,
                batch_size,
                lease_duration_ms,
                max_attempts,
                poll_interval: Duration::from_millis(poll_interval_ms),
                max_backoff_ms,
            },
        ))
    }
}

#[derive(Debug)]
enum WorkerError {
    Configuration(&'static str),
    Io(io::Error),
    Domain(DomainError),
}

impl fmt::Display for WorkerError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Configuration(reason) => formatter.write_str(reason),
            Self::Io(error) => write!(formatter, "io_failure({:?})", error.kind()),
            Self::Domain(error) => write!(
                formatter,
                "domain_failure(code={},retry={:?})",
                error.code().as_str(),
                error.retry(),
            ),
        }
    }
}

impl std::error::Error for WorkerError {}

impl From<io::Error> for WorkerError {
    fn from(value: io::Error) -> Self {
        Self::Io(value)
    }
}

impl From<DomainError> for WorkerError {
    fn from(value: DomainError) -> Self {
        Self::Domain(value)
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
struct BatchReport {
    claimed: usize,
    completed: usize,
    retried: usize,
    dead_lettered: usize,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct DeliveryFailure {
    code: &'static str,
}

#[derive(Clone, Debug)]
struct SpoolSink {
    directory: PathBuf,
}

impl SpoolSink {
    fn new(directory: PathBuf) -> Result<Self, WorkerError> {
        fs::create_dir_all(&directory)?;
        let metadata = fs::metadata(&directory)?;
        if !metadata.is_dir() {
            return Err(WorkerError::Configuration("spool_path_is_not_directory"));
        }
        Ok(Self { directory })
    }

    fn deliver(&self, lease: &OutboxLease) -> Result<Digest32, DeliveryFailure> {
        let record = spool_record(lease);
        let final_path = self
            .directory
            .join(format!("{}.json", encode_hex(lease.id.as_bytes())));
        if final_path.exists() {
            verify_existing_file(&final_path, &record)?;
            return Ok(Digest32::new(sha256_digest(&record)));
        }

        let temporary_path = self.directory.join(format!(
            ".{}.{}.{}.tmp",
            encode_hex(lease.id.as_bytes()),
            lease.lease_generation,
            encode_hex(lease.owner.as_bytes()),
        ));
        prepare_temporary_file(&temporary_path, &record)?;
        match fs::hard_link(&temporary_path, &final_path) {
            Ok(()) => {}
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {
                verify_existing_file(&final_path, &record)?;
            }
            Err(_) => {
                return Err(DeliveryFailure {
                    code: "spool_link_failed",
                })
            }
        }
        let _ = fs::remove_file(&temporary_path);
        sync_directory(&self.directory)?;
        verify_existing_file(&final_path, &record)?;
        Ok(Digest32::new(sha256_digest(&record)))
    }
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("trnm-outbox-worker failed: {error}");
            ExitCode::FAILURE
        }
    }
}

fn run() -> Result<(), WorkerError> {
    let arguments = env::args().collect::<Vec<_>>();
    let (command, config) = WorkerConfig::from_environment(&arguments)?;
    match command {
        Command::CheckConfig => {
            println!("trnm-outbox-worker configuration: {config:?}");
            Ok(())
        }
        Command::RunOnce => {
            let pool = build_pool(&config)?;
            let sink = SpoolSink::new(config.spool_directory.clone())?;
            let report = process_once(&pool, &sink, &config)?;
            print_report(report);
            Ok(())
        }
        Command::Serve => serve(&config),
    }
}

fn serve(config: &WorkerConfig) -> Result<(), WorkerError> {
    let pool = build_pool(config)?;
    let sink = SpoolSink::new(config.spool_directory.clone())?;
    let mut consecutive_database_failures = 0_u32;
    eprintln!(
        "trnm-outbox-worker source candidate started profile={} node={} batch_size={}",
        config.database_profile.metadata_value(),
        encode_hex(config.node.as_bytes()),
        config.batch_size,
    );
    loop {
        if stop_requested(config.stop_file.as_deref())? {
            eprintln!("trnm-outbox-worker source candidate stopped");
            return Ok(());
        }
        match process_once(&pool, &sink, config) {
            Ok(report) => {
                consecutive_database_failures = 0;
                if report.claimed == 0 {
                    thread::sleep(config.poll_interval);
                } else {
                    print_report(report);
                }
            }
            Err(WorkerError::Domain(error)) => {
                consecutive_database_failures = consecutive_database_failures.saturating_add(1);
                eprintln!(
                    "trnm-outbox-worker database operation failed code={} retry={:?} consecutive_failures={}",
                    error.code().as_str(),
                    error.retry(),
                    consecutive_database_failures,
                );
                if consecutive_database_failures >= MAX_CONSECUTIVE_DATABASE_FAILURES {
                    return Err(WorkerError::Domain(error));
                }
                thread::sleep(config.poll_interval);
            }
            Err(error) => return Err(error),
        }
    }
}

fn process_once(
    pool: &PgPool,
    sink: &SpoolSink,
    config: &WorkerConfig,
) -> Result<BatchReport, WorkerError> {
    let now_ms = now_millis()?;
    let mut repository = pool.acquire()?;
    let leases = repository.claim_outbox(
        config.node,
        now_ms,
        config.lease_duration_ms,
        config.max_attempts,
        config.batch_size,
    )?;
    let mut report = BatchReport {
        claimed: leases.len(),
        ..BatchReport::default()
    };
    for lease in leases {
        match sink.deliver(&lease) {
            Ok(receipt) => {
                repository.complete_outbox(&lease, receipt, now_millis()?)?;
                report.completed = report.completed.saturating_add(1);
            }
            Err(failure) => {
                let retry_now_ms = now_millis()?;
                let delay_ms = retry_delay_ms(&lease, config.max_backoff_ms);
                let next_available_at_ms = retry_now_ms
                    .checked_add(delay_ms)
                    .ok_or(WorkerError::Configuration("retry_timestamp_overflow"))?;
                let reason = Digest32::new(sha256_digest(failure.code.as_bytes()));
                match repository.retry_or_dead_letter_outbox(
                    &lease,
                    retry_now_ms,
                    next_available_at_ms,
                    config.max_attempts,
                    reason,
                )? {
                    OutboxRetryOutcome::Pending { .. } => {
                        report.retried = report.retried.saturating_add(1);
                    }
                    OutboxRetryOutcome::DeadLetter { .. } => {
                        report.dead_lettered = report.dead_lettered.saturating_add(1);
                    }
                }
            }
        }
    }
    Ok(report)
}

fn build_pool(config: &WorkerConfig) -> Result<PgPool, WorkerError> {
    match config.database_tls_mode {
        DatabaseTlsMode::PlaintextCandidate => Ok(PgPool::connect_plain(
            &config.database_url,
            config.database_profile,
            config.database_pool,
        )?),
        DatabaseTlsMode::VerifyFull => {
            let tls = PgTlsConfig::new(
                read_optional(config.database_tls_root_cert.as_deref())?,
                read_optional(config.database_tls_identity_cert.as_deref())?,
                read_optional(config.database_tls_identity_key.as_deref())?,
            )?;
            Ok(PgPool::connect_tls(
                &config.database_url,
                config.database_profile,
                config.database_pool,
                &tls,
            )?)
        }
    }
}

fn spool_record(lease: &OutboxLease) -> Vec<u8> {
    format!(
        "{{\"schema\":\"trillionnium.outbox-spool.v1\",\"intent_id\":\"{}\",\"entity_id\":\"{}\",\"command_id\":\"{}\",\"kind\":\"{}\",\"payload_digest\":\"{}\",\"attempt\":{},\"lease_generation\":{},\"owner_node\":\"{}\",\"lease_expires_at_ms\":{}}}\n",
        encode_hex(lease.id.as_bytes()),
        encode_hex(lease.entity.as_bytes()),
        encode_hex(lease.command.as_bytes()),
        intent_kind_name(lease.kind),
        encode_hex(lease.payload.as_bytes()),
        lease.attempt,
        lease.lease_generation,
        encode_hex(lease.owner.as_bytes()),
        lease.lease_expires_at_ms,
    )
    .into_bytes()
}

fn prepare_temporary_file(path: &Path, expected: &[u8]) -> Result<(), DeliveryFailure> {
    match OpenOptions::new().write(true).create_new(true).open(path) {
        Ok(mut file) => {
            file.write_all(expected).map_err(|_| DeliveryFailure {
                code: "spool_write_failed",
            })?;
            file.sync_all().map_err(|_| DeliveryFailure {
                code: "spool_sync_failed",
            })?;
            Ok(())
        }
        Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {
            verify_existing_file(path, expected)?;
            File::open(path)
                .and_then(|file| file.sync_all())
                .map_err(|_| DeliveryFailure {
                    code: "spool_sync_failed",
                })
        }
        Err(_) => Err(DeliveryFailure {
            code: "spool_create_failed",
        }),
    }
}

fn verify_existing_file(path: &Path, expected: &[u8]) -> Result<(), DeliveryFailure> {
    let metadata = fs::symlink_metadata(path).map_err(|_| DeliveryFailure {
        code: "spool_read_failed",
    })?;
    if !metadata.file_type().is_file() {
        return Err(DeliveryFailure {
            code: "spool_not_regular_file",
        });
    }
    let actual = fs::read(path).map_err(|_| DeliveryFailure {
        code: "spool_read_failed",
    })?;
    if constant_time_eq(&actual, expected) {
        Ok(())
    } else {
        Err(DeliveryFailure {
            code: "spool_receipt_conflict",
        })
    }
}

#[cfg(unix)]
fn sync_directory(path: &Path) -> Result<(), DeliveryFailure> {
    File::open(path)
        .and_then(|directory| directory.sync_all())
        .map_err(|_| DeliveryFailure {
            code: "spool_directory_sync_failed",
        })
}

#[cfg(not(unix))]
fn sync_directory(_path: &Path) -> Result<(), DeliveryFailure> {
    Ok(())
}

fn retry_delay_ms(lease: &OutboxLease, maximum_ms: u64) -> u64 {
    let exponent = lease.attempt.saturating_sub(1).min(20);
    let base = 100_u64
        .checked_shl(exponent)
        .unwrap_or(u64::MAX)
        .min(maximum_ms)
        .max(1);
    let floor = (base / 2).max(1);
    let width = base.saturating_sub(floor).saturating_add(1);
    let mut material = Vec::with_capacity(40);
    material.extend_from_slice(lease.id.as_bytes());
    material.extend_from_slice(lease.owner.as_bytes());
    material.extend_from_slice(&lease.lease_generation.to_be_bytes());
    let digest = sha256_digest(&material);
    let mut seed_bytes = [0_u8; 8];
    seed_bytes.copy_from_slice(&digest[..8]);
    floor.saturating_add(u64::from_be_bytes(seed_bytes) % width)
}

fn stop_requested(path: Option<&Path>) -> Result<bool, WorkerError> {
    match path {
        None => Ok(false),
        Some(path) => match fs::metadata(path) {
            Ok(metadata) => Ok(metadata.is_file()),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
            Err(error) => Err(error.into()),
        },
    }
}

fn print_report(report: BatchReport) {
    println!(
        "outbox claimed={} completed={} retried={} dead_lettered={}",
        report.claimed, report.completed, report.retried, report.dead_lettered,
    );
}

fn now_millis() -> Result<u64, WorkerError> {
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| WorkerError::Configuration("system_clock_before_unix_epoch"))?;
    u64::try_from(duration.as_millis())
        .map_err(|_| WorkerError::Configuration("system_clock_millis_overflow"))
}

fn read_optional(path: Option<&Path>) -> Result<Option<Vec<u8>>, WorkerError> {
    path.map(fs::read).transpose().map_err(WorkerError::from)
}

fn required(
    lookup: &impl Fn(&str) -> Option<String>,
    name: &str,
    reason: &'static str,
) -> Result<String, WorkerError> {
    match lookup(name) {
        Some(value) if !value.is_empty() => Ok(value),
        _ => Err(WorkerError::Configuration(reason)),
    }
}

fn required_path(
    lookup: &impl Fn(&str) -> Option<String>,
    name: &str,
    reason: &'static str,
) -> Result<PathBuf, WorkerError> {
    optional_path(lookup(name))?.ok_or(WorkerError::Configuration(reason))
}

fn optional_path(value: Option<String>) -> Result<Option<PathBuf>, WorkerError> {
    match value {
        None => Ok(None),
        Some(value)
            if !value.is_empty()
                && value.len() <= 4096
                && !value.bytes().any(|byte| byte.is_ascii_control()) =>
        {
            Ok(Some(PathBuf::from(value)))
        }
        Some(_) => Err(WorkerError::Configuration("path_invalid")),
    }
}

fn parse_bool(
    value: Option<&str>,
    default: bool,
    reason: &'static str,
) -> Result<bool, WorkerError> {
    match value {
        None => Ok(default),
        Some("1" | "true" | "TRUE" | "yes" | "YES") => Ok(true),
        Some("0" | "false" | "FALSE" | "no" | "NO") => Ok(false),
        Some(_) => Err(WorkerError::Configuration(reason)),
    }
}

fn parse_u64(
    value: Option<&str>,
    default: u64,
    minimum: u64,
    maximum: u64,
    reason: &'static str,
) -> Result<u64, WorkerError> {
    let parsed = value
        .map(str::parse::<u64>)
        .transpose()
        .map_err(|_| WorkerError::Configuration(reason))?
        .unwrap_or(default);
    if (minimum..=maximum).contains(&parsed) {
        Ok(parsed)
    } else {
        Err(WorkerError::Configuration(reason))
    }
}

fn parse_lower_hex<const N: usize>(value: &str) -> Result<[u8; N], WorkerError> {
    if value.len() != N * 2
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
    {
        return Err(WorkerError::Configuration("node_id_invalid"));
    }
    let mut output = [0_u8; N];
    for (index, pair) in value.as_bytes().chunks_exact(2).enumerate() {
        output[index] = (hex_nibble(pair[0])? << 4) | hex_nibble(pair[1])?;
    }
    Ok(output)
}

fn hex_nibble(value: u8) -> Result<u8, WorkerError> {
    match value {
        b'0'..=b'9' => Ok(value - b'0'),
        b'a'..=b'f' => Ok(value - b'a' + 10),
        _ => Err(WorkerError::Configuration("node_id_invalid")),
    }
}

fn encode_hex(input: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(input.len() * 2);
    for byte in input {
        output.push(char::from(HEX[usize::from(byte >> 4)]));
        output.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    output
}

fn intent_kind_name(kind: IntentKind) -> &'static str {
    match kind {
        IntentKind::Broadcast => "broadcast",
        IntentKind::SearchIndex => "search_index",
        IntentKind::Notification => "notification",
        IntentKind::ExternalEffect => "external_effect",
        IntentKind::Completion => "completion",
    }
}

fn constant_time_eq(left: &[u8], right: &[u8]) -> bool {
    let maximum = left.len().max(right.len());
    let mut difference = left.len() ^ right.len();
    for index in 0..maximum {
        let left_byte = left.get(index).copied().unwrap_or(0);
        let right_byte = right.get(index).copied().unwrap_or(0);
        difference |= usize::from(left_byte ^ right_byte);
    }
    difference == 0
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;
    use std::process;

    use trnm_contracts::CommandId;
    use trnm_persistence_pg::{EntityId, IntentId};

    use super::*;

    fn lease() -> OutboxLease {
        OutboxLease {
            id: IntentId::new([1; 16]),
            entity: EntityId::new([2; 16]),
            command: CommandId::new([3; 16]),
            kind: IntentKind::Broadcast,
            payload: Digest32::new([4; 32]),
            attempt: 2,
            lease_generation: 7,
            owner: NodeId::new([5; 16]),
            lease_expires_at_ms: 123_456,
        }
    }

    fn temporary_directory(name: &str) -> PathBuf {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        env::temp_dir().join(format!(
            "trnm-outbox-worker-{name}-{}-{unique}",
            process::id()
        ))
    }

    fn base_config(directory: &Path) -> BTreeMap<String, String> {
        BTreeMap::from([
            (
                "TRNM_OUTBOX_DATABASE_URL".to_owned(),
                "postgresql://trnm:secret@127.0.0.1/trnm".to_owned(),
            ),
            (
                "TRNM_OUTBOX_DATABASE_PROFILE".to_owned(),
                "postgresql".to_owned(),
            ),
            (
                "TRNM_OUTBOX_ALLOW_PLAINTEXT_DATABASE".to_owned(),
                "1".to_owned(),
            ),
            ("TRNM_OUTBOX_NODE_ID_HEX".to_owned(), "11".repeat(16)),
            (
                "TRNM_OUTBOX_SPOOL_DIRECTORY".to_owned(),
                directory.display().to_string(),
            ),
        ])
    }

    fn load(values: &BTreeMap<String, String>) -> Result<(Command, WorkerConfig), WorkerError> {
        WorkerConfig::from_lookup(
            &["trnm-outbox-worker".to_owned(), "run-once".to_owned()],
            |name| values.get(name).cloned(),
        )
    }

    #[test]
    fn spool_delivery_is_atomic_idempotent_and_content_addressed() {
        let directory = temporary_directory("idempotent");
        let sink = SpoolSink::new(directory.clone()).unwrap();
        let value = lease();
        let first = sink.deliver(&value).unwrap();
        let second = sink.deliver(&value).unwrap();
        assert_eq!(first, second);
        let final_path = directory.join(format!("{}.json", encode_hex(value.id.as_bytes())));
        let content = fs::read(&final_path).unwrap();
        assert_eq!(first, Digest32::new(sha256_digest(&content)));
        assert!(String::from_utf8(content)
            .unwrap()
            .contains("\"schema\":\"trillionnium.outbox-spool.v1\""));
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn conflicting_existing_receipt_fails_closed() {
        let directory = temporary_directory("conflict");
        let sink = SpoolSink::new(directory.clone()).unwrap();
        let value = lease();
        sink.deliver(&value).unwrap();
        let final_path = directory.join(format!("{}.json", encode_hex(value.id.as_bytes())));
        fs::write(&final_path, b"different").unwrap();
        assert_eq!(
            sink.deliver(&value).unwrap_err().code,
            "spool_receipt_conflict"
        );
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn retry_delay_is_stable_and_bounded() {
        let value = lease();
        let first = retry_delay_ms(&value, 60_000);
        let second = retry_delay_ms(&value, 60_000);
        assert_eq!(first, second);
        assert!((100..=200).contains(&first));

        let mut exhausted = value;
        exhausted.attempt = 100;
        let bounded = retry_delay_ms(&exhausted, 5_000);
        assert!((2_500..=5_000).contains(&bounded));
    }

    #[test]
    fn implicit_plaintext_and_invalid_node_fail_closed() {
        let directory = temporary_directory("config");
        let mut values = base_config(&directory);
        values.remove("TRNM_OUTBOX_ALLOW_PLAINTEXT_DATABASE");
        assert!(matches!(
            load(&values),
            Err(WorkerError::Configuration(
                "plaintext_database_requires_explicit_candidate_opt_in"
            ))
        ));

        let mut values = base_config(&directory);
        values.insert("TRNM_OUTBOX_NODE_ID_HEX".to_owned(), "00".repeat(16));
        assert!(matches!(
            load(&values),
            Err(WorkerError::Configuration("node_id_invalid"))
        ));
    }

    #[test]
    fn tls_identity_requires_a_pair_and_debug_redacts_url() {
        let directory = temporary_directory("tls");
        let mut values = base_config(&directory);
        values.remove("TRNM_OUTBOX_ALLOW_PLAINTEXT_DATABASE");
        values.insert(
            "TRNM_OUTBOX_DATABASE_TLS_MODE".to_owned(),
            "verify-full".to_owned(),
        );
        values.insert(
            "TRNM_OUTBOX_DATABASE_TLS_IDENTITY_CERT_PEM".to_owned(),
            "/run/secrets/client-cert.pem".to_owned(),
        );
        assert!(matches!(
            load(&values),
            Err(WorkerError::Configuration(
                "database_tls_identity_cert_key_pair_required"
            ))
        ));

        let config = load(&base_config(&directory)).unwrap().1;
        let debug = format!("{config:?}");
        assert!(debug.contains("<redacted>"));
        assert!(!debug.contains("trnm:secret"));
    }

    #[test]
    fn spool_record_covers_every_declared_intent_kind() {
        for (kind, name) in [
            (IntentKind::Broadcast, "broadcast"),
            (IntentKind::SearchIndex, "search_index"),
            (IntentKind::Notification, "notification"),
            (IntentKind::ExternalEffect, "external_effect"),
            (IntentKind::Completion, "completion"),
        ] {
            let mut value = lease();
            value.kind = kind;
            let record = String::from_utf8(spool_record(&value)).unwrap();
            assert!(record.contains(&format!("\"kind\":\"{name}\"")));
        }
    }
}
