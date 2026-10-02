//! Live, test-only commit-boundary fault injection through the production
//! RetryingRepository -> PooledRepository -> PgRepository transaction path.
//! CockroachDB's session fault is disabled before the next whole-command attempt.
use std::fmt::Write as _;
use std::net::IpAddr;
use std::str::FromStr;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use postgres::config::Host;
use postgres::{Client, Config, NoTls};
use trnm_contracts::{CommandId, Digest32, DomainError, RetryClass, StableCode};
use trnm_persistence_pg::{
    CommitOutcome, CommitRequest, DatabaseProfile, EntityHead, EntityId, EventId, EventInput,
    IntentId, IntentKind, OutboxInput, PgPool, PgPoolConfig, SchemaMigrationReport,
};

use super::super::app::{Repository, RepositoryOperationalMetrics};
use super::super::pool::PooledRepository;
use super::super::retry::{BudgetedRepository, RetryPolicy, RetryingRepository};

const BUDGET: Duration = Duration::from_secs(10);

fn emit_schema_report(marker: &str, report: &SchemaMigrationReport) {
    // Test evidence is emitted only after the migration/verification transaction
    // has ended. The report contains no connection URL or credentials.
    let mut digest = String::with_capacity(64);
    for byte in report.identity.chain_digest.get().as_bytes() {
        write!(digest, "{byte:02x}").expect("String write");
    }
    // Profile/algorithm are static names, provenance is checked hexadecimal
    // and the digest is hexadecimal: no unescaped external text enters JSON.
    println!("\n{marker}={{\"schema\":\"trillionnium.authoritative-schema-report.v1\",\"profile\":\"{}\",\"schema_version\":{},\"chain_digest\":\"{}\",\"digest_algorithm\":\"{}\",\"storage_writer_epoch\":{},\"source_commit\":\"{}\",\"upgrade_source_commit\":\"{}\",\"v2_apply_source_commit\":\"{}\",\"migration_applied\":{},\"table_count\":{},\"applied_steps\":{},\"compatibility_credit\":false}}",
        report.identity.profile.metadata_value(), report.identity.schema_version, digest, report.identity.digest_algorithm,
        report.identity.storage_writer_epoch, report.identity.source_commit, report.identity.upgrade_source_commit, report.identity.v2_apply_source_commit,
        report.migration_applied, report.table_count, report.applied_steps);
}
struct InjectedRepository {
    inner: PooledRepository,
    pool: PgPool,
    inspector: Client,
    original: CommitRequest,
    calls: Arc<AtomicU64>,
    fail_every_attempt: bool,
}

impl std::fmt::Debug for InjectedRepository {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("InjectedRepository")
            .field("calls", &self.calls.load(Ordering::Relaxed))
            .field("fail_every_attempt", &self.fail_every_attempt)
            .finish_non_exhaustive()
    }
}

impl Repository for InjectedRepository {
    fn bootstrap_entity(
        &mut self,
        entity: EntityId,
        generation: u64,
        state: Digest32,
        now: u64,
    ) -> Result<EntityHead, DomainError> {
        self.inner.bootstrap_entity(entity, generation, state, now)
    }
    fn commit_command(&mut self, request: &CommitRequest) -> Result<CommitOutcome, DomainError> {
        self.commit_command_with_budget(request, BUDGET)
    }
    fn operational_metrics(&self) -> RepositoryOperationalMetrics {
        self.inner.operational_metrics()
    }
}

impl BudgetedRepository for InjectedRepository {
    fn commit_command_with_budget(
        &mut self,
        request: &CommitRequest,
        budget: Duration,
    ) -> Result<CommitOutcome, DomainError> {
        assert_eq!(
            request, &self.original,
            "retry must retain the entire command identity"
        );
        let call = self.calls.fetch_add(1, Ordering::Relaxed);
        let injected = call == 0 || self.fail_every_attempt;
        if injected {
            self.pool.run_with_deadline(budget, |repository| {
                repository
                    .execute_migration_batch("SET inject_retry_errors_on_commit_enabled = true")
            })?;
        }
        // This is the actual production wrapper, not a test-created receipt.
        // max_size=1 and no other borrower bind the fault to this same session.
        let outcome = self.inner.commit_command_with_budget(request, budget);
        if injected {
            self.pool.run_with_deadline(budget, |repository| {
                repository
                    .execute_migration_batch("SET inject_retry_errors_on_commit_enabled = false")
            })?;
            let error = outcome
                .as_ref()
                .expect_err("commit fault must reach the client");
            assert_eq!(error.reason(), "database_serialization_failure");
            assert_eq!(error.retry(), RetryClass::SafeImmediate);
            assert_counts(&mut self.inspector, request.entity, 0);
            let head = self
                .pool
                .run_with_deadline(BUDGET, |repository| repository.load_head(request.entity))
                .unwrap()
                .unwrap();
            assert_eq!(head.revision, 0);
            assert_eq!(head.last_event_sequence, 0);
            assert_eq!(head.state, Digest32::new([0x50; 32]));
        }
        outcome
    }
}

fn assert_counts(client: &mut Client, entity: EntityId, expected: i64) {
    for table in [
        "trnm_command_receipts",
        "trnm_events",
        "trnm_outbox",
        "trnm_command_outbox",
    ] {
        let count: i64 = client
            .query_one(
                &format!("SELECT count(*) FROM {table} WHERE entity_id = $1"),
                &[&entity.as_bytes().as_slice()],
            )
            .unwrap()
            .get(0);
        assert_eq!(count, expected, "atomic durable row cardinality in {table}");
    }
}

fn request(entity: u8) -> CommitRequest {
    CommitRequest {
        entity: EntityId::new([entity; 16]),
        command: CommandId::new([entity + 1; 16]),
        fingerprint: Digest32::new([entity + 2; 32]),
        expected_revision: 0,
        authority_generation: 1,
        next_state: Digest32::new([entity + 3; 32]),
        committed_at_ms: 100,
        events: vec![EventInput {
            id: EventId::new([entity + 4; 16]),
            payload: Digest32::new([entity + 5; 32]),
        }],
        outbox: vec![OutboxInput {
            id: IntentId::new([entity + 6; 16]),
            kind: IntentKind::Broadcast,
            payload: Digest32::new([entity + 7; 32]),
            available_at_ms: 100,
        }],
    }
}

fn pool_config() -> PgPoolConfig {
    PgPoolConfig {
        max_size: 1,
        min_idle: 0,
        acquire_timeout: Duration::from_secs(2),
        statement_timeout: BUDGET,
        lock_timeout: Duration::from_secs(2),
        idle_transaction_timeout: BUDGET,
        idle_timeout: Duration::from_secs(30),
        max_lifetime: Duration::from_secs(60),
    }
}

fn retry_policy(max_attempts: u8) -> RetryPolicy {
    RetryPolicy {
        max_attempts,
        total_budget: Duration::from_secs(20),
        initial_backoff: Duration::ZERO,
        maximum_backoff: Duration::ZERO,
    }
}

pub fn prove(database_url: &str) {
    let mut config = Config::from_str(database_url).unwrap();
    let [Host::Tcp(host)] = config.get_hosts() else {
        panic!("atomicity lab requires one numeric loopback host");
    };
    let host = host.clone();
    assert!(IpAddr::from_str(&host).unwrap().is_loopback());
    assert!(config.get_hostaddrs().is_empty());
    assert_eq!(host, "127.0.0.1");
    assert_eq!(config.get_user(), Some("root"));
    assert!(config.get_password().is_none());
    assert!(config.get_ports().len() <= 1);
    config.connect_timeout(Duration::from_secs(2));
    config.options("-c statement_timeout=10000");
    let mut admin = config.connect(NoTls).unwrap();
    let database = format!("trnm_retry_atomicity_{}", std::process::id());
    // CREATE without IF NOT EXISTS proves ownership of this disposable database;
    // a collision fails, rather than overwriting or dropping a preexisting one.
    admin
        .batch_execute(&format!("CREATE DATABASE {database}"))
        .unwrap();
    config.dbname(&database);
    let mut inspector = config.connect(NoTls).unwrap();
    let port = config.get_ports().first().copied().unwrap_or(26257);
    // Preserve credentials/options from the caller for real connections. The
    // lab only accepts its existing insecure root loopback profile.
    assert_eq!(config.get_user(), Some("root"));
    assert!(config.get_password().is_none());
    let url = format!("postgresql://root@127.0.0.1:{port}/{database}?sslmode=disable");
    assert_eq!(host, "127.0.0.1");
    let pool = PgPool::connect_plain(&url, DatabaseProfile::CockroachDb, pool_config()).unwrap();
    {
        let mut migrator = pool.acquire().unwrap();
        let source_commit = std::env::var("TRNM_SCHEMA_SOURCE_COMMIT")
            .expect("atomicity fixture requires actual schema apply source commit");
        let report = migrator
            .migrate_authoritative_schema(&source_commit, 1, None)
            .unwrap();
        emit_schema_report("retry_schema_apply_report", &report);
    }
    let original = request(0x61);
    let mut actual = PooledRepository::new(pool.clone());
    actual
        .bootstrap_entity(original.entity, 1, Digest32::new([0x50; 32]), 1)
        .unwrap();
    let calls = Arc::new(AtomicU64::new(0));
    let injected = InjectedRepository {
        inner: actual,
        pool: pool.clone(),
        inspector: config.connect(NoTls).unwrap(),
        original: original.clone(),
        calls: calls.clone(),
        fail_every_attempt: false,
    };
    let mut retrying = RetryingRepository::new(injected, retry_policy(3)).unwrap();
    let applied = match retrying.commit_command(&original).unwrap() {
        CommitOutcome::Applied(receipt) => receipt,
        CommitOutcome::Duplicate(_) => panic!("fresh command unexpectedly replayed"),
    };
    assert_eq!(calls.load(Ordering::Relaxed), 2);
    assert_eq!(applied.entity, original.entity);
    assert_eq!(applied.command, original.command);
    assert_eq!(applied.fingerprint, original.fingerprint);
    assert_eq!(applied.revision, 1);
    assert_eq!(applied.event_count, 1);
    assert_eq!(applied.first_event_sequence, Some(1));
    assert_eq!(applied.last_event_sequence, 1);
    assert_eq!(applied.outbox, vec![original.outbox[0].id]);
    let metrics = retrying.operational_metrics();
    assert_eq!(
        (
            metrics.retry_attempts,
            metrics.retries,
            metrics.retry_exhausted
        ),
        (2, 1, 0)
    );
    assert_counts(&mut inspector, original.entity, 1);
    drop(retrying);
    drop(pool);

    // Discard the original connection and result-delivery path. A fresh pool
    // must resolve the original identity from the real durable receipt.
    let reconnected =
        PgPool::connect_plain(&url, DatabaseProfile::CockroachDb, pool_config()).unwrap();
    let mut actual = PooledRepository::new(reconnected.clone());
    assert_eq!(
        actual.commit_command(&original).unwrap(),
        CommitOutcome::Duplicate(applied)
    );
    assert_counts(&mut inspector, original.entity, 1);
    let mut conflict = original.clone();
    conflict.fingerprint = Digest32::new([0x7f; 32]);
    let conflict = actual.commit_command(&conflict).unwrap_err();
    assert_eq!(conflict.code(), StableCode::AlreadyExists);
    assert_eq!(conflict.reason(), "command_id_conflict");
    assert_counts(&mut inspector, original.entity, 1);

    let exhausted = request(0x71);
    actual
        .bootstrap_entity(exhausted.entity, 1, Digest32::new([0x50; 32]), 1)
        .unwrap();
    let attempts = Arc::new(AtomicU64::new(0));
    let injected = InjectedRepository {
        inner: actual,
        pool: reconnected.clone(),
        inspector: config.connect(NoTls).unwrap(),
        original: exhausted.clone(),
        calls: attempts.clone(),
        fail_every_attempt: true,
    };
    let mut retrying = RetryingRepository::new(injected, retry_policy(2)).unwrap();
    assert!(retrying.commit_command(&exhausted).is_err());
    assert_eq!(attempts.load(Ordering::Relaxed), 2);
    let metrics = retrying.operational_metrics();
    assert_eq!(metrics.retry_attempts, 2);
    assert_eq!(metrics.retry_exhausted, 1);
    assert_counts(&mut inspector, exhausted.entity, 0);
    let identity = reconnected
        .acquire()
        .unwrap()
        .verify_authoritative_schema()
        .unwrap();
    let table_count: i64 = inspector
        .query_one(
            "SELECT count(*)::BIGINT FROM information_schema.tables WHERE table_schema='public' AND table_name LIKE 'trnm_%' AND table_type='BASE TABLE'",
            &[],
        )
        .unwrap()
        .get(0);
    let table_count = usize::try_from(table_count).unwrap();
    assert_eq!(table_count, 10);
    emit_schema_report(
        "retry_schema_verify_report",
        &SchemaMigrationReport {
            identity,
            migration_applied: false,
            table_count,
            applied_steps: 0,
        },
    );
    drop(retrying);
    drop(reconnected);
    drop(inspector);
    admin
        .batch_execute(&format!("DROP DATABASE {database} CASCADE"))
        .unwrap();
    println!("assertion=production_commit_boundary_40001_was_not_acknowledged");
    println!("assertion=failed_commit_left_no_receipt_event_or_outbox");
    println!("assertion=production_retry_preserved_complete_command_identity");
    println!("assertion=production_retry_committed_one_receipt_event_and_outbox");
    println!("assertion=fresh_pool_replayed_exact_durable_receipt");
    println!("assertion=changed_fingerprint_rejected_without_extra_effects");
    println!("assertion=retry_exhaustion_left_no_partial_durable_effect");
    println!("fault_mode=cockroach_session_commit_error_injection");
    println!("network_response_loss_injected=false");
}
