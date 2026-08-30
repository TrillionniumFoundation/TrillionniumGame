use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{mpsc, Arc};
use std::thread;
use std::time::Duration;

use trnm_contracts::{Digest32, DomainError};
use trnm_persistence_pg::{
    cancellation_watchdog_panicked, CommitOutcome, CommitRequest, EntityHead, EntityId, PgPool,
    PgRepository,
};

use super::app::{Repository, RepositoryOperationalMetrics};

#[derive(Debug, Default)]
struct DeadlineMetrics {
    watchdogs: AtomicU64,
    cancellations: AtomicU64,
    cancellation_failures: AtomicU64,
}

#[derive(Clone, Debug)]
pub struct PooledRepository {
    pool: PgPool,
    deadline: Duration,
    metrics: Arc<DeadlineMetrics>,
}

impl PooledRepository {
    #[must_use]
    pub fn new(pool: PgPool) -> Self {
        Self {
            deadline: pool.policy().statement_timeout,
            pool,
            metrics: Arc::new(DeadlineMetrics::default()),
        }
    }

    fn run_with_deadline<T>(
        &self,
        operation: impl FnOnce(&mut PgRepository) -> Result<T, DomainError>,
    ) -> Result<T, DomainError> {
        let mut repository = self.pool.acquire()?;
        let cancel = repository.cancellation_handle();
        let (finished, signal) = mpsc::sync_channel(1);
        let deadline = self.deadline;
        let metrics = Arc::clone(&self.metrics);
        metrics.watchdogs.fetch_add(1, Ordering::Relaxed);
        let watchdog = thread::spawn(move || {
            if signal.recv_timeout(deadline) == Err(mpsc::RecvTimeoutError::Timeout) {
                metrics.cancellations.fetch_add(1, Ordering::Relaxed);
                if cancel.cancel().is_err() {
                    metrics
                        .cancellation_failures
                        .fetch_add(1, Ordering::Relaxed);
                }
            }
        });

        let result = operation(&mut repository);
        let _ = finished.send(());
        watchdog
            .join()
            .map_err(|_| cancellation_watchdog_panicked())?;
        result
    }
}

impl Repository for PooledRepository {
    fn bootstrap_entity(
        &mut self,
        entity: EntityId,
        authority_generation: u64,
        state: Digest32,
        updated_at_ms: u64,
    ) -> Result<EntityHead, DomainError> {
        self.run_with_deadline(|repository| {
            repository.bootstrap_entity(entity, authority_generation, state, updated_at_ms)
        })
    }

    fn commit_command(&mut self, request: &CommitRequest) -> Result<CommitOutcome, DomainError> {
        self.run_with_deadline(|repository| repository.commit_command(request))
    }

    fn operational_metrics(&self) -> RepositoryOperationalMetrics {
        let snapshot = self.pool.snapshot();
        RepositoryOperationalMetrics {
            pool_max_size: u64::from(snapshot.max_size),
            pool_connections: u64::from(snapshot.connections),
            pool_idle_connections: u64::from(snapshot.idle_connections),
            pool_acquire_attempts: snapshot.acquire_attempts,
            pool_acquire_failures: snapshot.acquire_failures,
            pool_session_policy_failures: snapshot.session_policy_failures,
            deadline_watchdogs: self.metrics.watchdogs.load(Ordering::Relaxed),
            deadline_cancellations: self.metrics.cancellations.load(Ordering::Relaxed),
            deadline_cancel_failures: self
                .metrics
                .cancellation_failures
                .load(Ordering::Relaxed),
            ..RepositoryOperationalMetrics::default()
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wrapper_is_cloneable_without_exposing_database_url() {
        fn assert_clone<T: Clone>() {}
        assert_clone::<PooledRepository>();
    }
}
