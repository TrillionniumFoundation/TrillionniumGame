use std::str::FromStr;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use postgres::{Client, Config, IsolationLevel, NoTls};
use trnm_contracts::{CommandId, Digest32, DomainError, RetryClass, StableCode};
use trnm_persistence_pg::{
    classify_sqlstate, CommitOutcome, CommitReceipt, CommitRequest, EntityHead, EntityId,
};

use super::app::{Repository, RepositoryOperationalMetrics};
use super::retry::{BudgetedRepository, RetryPolicy, RetryingRepository};

const PROBE_TABLE: &str = "trnm_crdb_retry_probe";

#[derive(Clone, Debug)]
struct LiveCockroachRepository {
    database_url: Arc<str>,
    calls: Arc<AtomicU64>,
}

impl LiveCockroachRepository {
    fn connect(&self, budget: Duration) -> Result<Client, DomainError> {
        let mut config = Config::from_str(&self.database_url)
            .map_err(|_| invalid("cockroach_retry_database_url_invalid"))?;
        config.connect_timeout(
            budget
                .min(Duration::from_secs(2))
                .max(Duration::from_millis(1)),
        );
        config.connect(NoTls).map_err(map_postgres_error)
    }

    fn force_serialization_failure(&self, budget: Duration) -> Result<DomainError, DomainError> {
        let mut left = self.connect(budget)?;
        let mut right = self.connect(budget)?;
        let mut left_transaction = left
            .build_transaction()
            .isolation_level(IsolationLevel::Serializable)
            .start()
            .map_err(map_postgres_error)?;
        let left_observed: i64 = left_transaction
            .query_one("SELECT value FROM trnm_crdb_retry_probe WHERE id = 2", &[])
            .map_err(map_postgres_error)?
            .get(0);
        if left_observed != 0 {
            return Err(failed_precondition(
                "cockroach_retry_probe_left_precondition_failed",
            ));
        }

        let mut right_transaction = right
            .build_transaction()
            .isolation_level(IsolationLevel::Serializable)
            .start()
            .map_err(map_postgres_error)?;
        let right_observed: i64 = right_transaction
            .query_one("SELECT value FROM trnm_crdb_retry_probe WHERE id = 1", &[])
            .map_err(map_postgres_error)?
            .get(0);
        if right_observed != 0 {
            return Err(failed_precondition(
                "cockroach_retry_probe_right_precondition_failed",
            ));
        }
        right_transaction
            .execute(
                "UPDATE trnm_crdb_retry_probe SET value = 1 WHERE id = 2",
                &[],
            )
            .map_err(map_postgres_error)?;
        right_transaction.commit().map_err(map_postgres_error)?;

        let source = match left_transaction.execute(
            "UPDATE trnm_crdb_retry_probe SET value = 1 WHERE id = 1",
            &[],
        ) {
            Err(source) => source,
            Ok(_) => match left_transaction.commit() {
                Err(source) => source,
                Ok(()) => {
                    return Err(failed_precondition(
                        "cockroach_serialization_conflict_not_observed",
                    ));
                }
            },
        };
        if source.code().map(|code| code.code()) != Some("40001") {
            return Err(failed_precondition(
                "cockroach_retry_sqlstate_was_not_40001",
            ));
        }
        Ok(map_postgres_error(source))
    }

    fn apply_after_retry(
        &self,
        request: &CommitRequest,
        budget: Duration,
    ) -> Result<CommitOutcome, DomainError> {
        let mut client = self.connect(budget)?;
        let mut transaction = client
            .build_transaction()
            .isolation_level(IsolationLevel::Serializable)
            .start()
            .map_err(map_postgres_error)?;
        let updated = transaction
            .execute(
                "UPDATE trnm_crdb_retry_probe SET value = value + 1 WHERE id = 1",
                &[],
            )
            .map_err(map_postgres_error)?;
        if updated != 1 {
            return Err(failed_precondition(
                "cockroach_retry_probe_update_cardinality_invalid",
            ));
        }
        transaction.commit().map_err(map_postgres_error)?;
        Ok(CommitOutcome::Applied(CommitReceipt {
            entity: request.entity,
            command: request.command,
            fingerprint: request.fingerprint,
            revision: request.expected_revision.saturating_add(1),
            state: request.next_state,
            first_event_sequence: None,
            last_event_sequence: 0,
            event_count: 0,
            outbox: Vec::new(),
        }))
    }
}

impl Repository for LiveCockroachRepository {
    fn bootstrap_entity(
        &mut self,
        _entity: EntityId,
        _authority_generation: u64,
        _state: Digest32,
        _updated_at_ms: u64,
    ) -> Result<EntityHead, DomainError> {
        Err(failed_precondition(
            "cockroach_retry_probe_bootstrap_not_supported",
        ))
    }

    fn commit_command(&mut self, request: &CommitRequest) -> Result<CommitOutcome, DomainError> {
        self.commit_command_with_budget(request, Duration::from_secs(5))
    }

    fn operational_metrics(&self) -> RepositoryOperationalMetrics {
        RepositoryOperationalMetrics::default()
    }
}

impl BudgetedRepository for LiveCockroachRepository {
    fn commit_command_with_budget(
        &mut self,
        request: &CommitRequest,
        operation_budget: Duration,
    ) -> Result<CommitOutcome, DomainError> {
        let call = self.calls.fetch_add(1, Ordering::Relaxed);
        if call == 0 {
            let retry = self.force_serialization_failure(operation_budget)?;
            if retry.reason() != "database_serialization_failure"
                || retry.retry() != RetryClass::SafeImmediate
            {
                return Err(failed_precondition(
                    "cockroach_retry_classification_invalid",
                ));
            }
            return Err(retry);
        }
        self.apply_after_retry(request, operation_budget)
    }
}

fn map_postgres_error(source: postgres::Error) -> DomainError {
    source
        .code()
        .map(|code| classify_sqlstate(code.code()))
        .unwrap_or_else(|| {
            DomainError::new(
                StableCode::Unavailable,
                "database_transport_failure",
                RetryClass::SafeBackoff,
            )
        })
}

const fn invalid(reason: &'static str) -> DomainError {
    DomainError::new(StableCode::InvalidArgument, reason, RetryClass::Never)
}

const fn failed_precondition(reason: &'static str) -> DomainError {
    DomainError::new(StableCode::FailedPrecondition, reason, RetryClass::Never)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn live_database_url() -> Option<String> {
        match std::env::var("TRNM_TEST_COCKROACH_URL") {
            Ok(value) if !value.is_empty() => Some(value),
            Ok(_) | Err(_)
                if std::env::var("TRNM_REQUIRE_LIVE_CRDB_RETRY").as_deref() == Ok("1") =>
            {
                panic!("TRNM_TEST_COCKROACH_URL is required for the live Cockroach retry lane");
            }
            Ok(_) | Err(_) => None,
        }
    }

    fn request() -> CommitRequest {
        CommitRequest {
            entity: EntityId::new([1; 16]),
            command: CommandId::new([2; 16]),
            fingerprint: Digest32::new([3; 32]),
            expected_revision: 0,
            authority_generation: 1,
            next_state: Digest32::new([4; 32]),
            committed_at_ms: 1,
            events: Vec::new(),
            outbox: Vec::new(),
        }
    }

    #[test]
    fn live_cockroach_serialization_failure_retries_entire_command() {
        let Some(database_url) = live_database_url() else {
            return;
        };
        let mut setup = Config::from_str(&database_url)
            .unwrap()
            .connect(NoTls)
            .unwrap();
        setup
            .batch_execute(&format!(
                "DROP TABLE IF EXISTS {PROBE_TABLE}; \
                 CREATE TABLE {PROBE_TABLE} (id INT PRIMARY KEY, value INT NOT NULL); \
                 INSERT INTO {PROBE_TABLE} (id, value) VALUES (1, 0), (2, 0);"
            ))
            .unwrap();

        let calls = Arc::new(AtomicU64::new(0));
        let inner = LiveCockroachRepository {
            database_url: Arc::from(database_url),
            calls: Arc::clone(&calls),
        };
        let policy = RetryPolicy {
            max_attempts: 3,
            total_budget: Duration::from_secs(5),
            initial_backoff: Duration::ZERO,
            maximum_backoff: Duration::ZERO,
        };
        let mut repository = RetryingRepository::new(inner, policy).unwrap();
        let outcome = repository.commit_command(&request()).unwrap();
        assert!(matches!(outcome, CommitOutcome::Applied(_)));
        assert_eq!(calls.load(Ordering::Relaxed), 2);

        let metrics = repository.operational_metrics();
        assert_eq!(metrics.retry_attempts, 2);
        assert_eq!(metrics.retries, 1);
        assert_eq!(metrics.retry_exhausted, 0);

        let rows = setup
            .query(
                &format!("SELECT id, value FROM {PROBE_TABLE} ORDER BY id"),
                &[],
            )
            .unwrap();
        let values = rows
            .iter()
            .map(|row| (row.get::<_, i64>(0), row.get::<_, i64>(1)))
            .collect::<Vec<_>>();
        assert_eq!(values, vec![(1, 1), (2, 1)]);
        setup
            .batch_execute(&format!("DROP TABLE {PROBE_TABLE}"))
            .unwrap();
    }
}
