use trnm_contracts::{Digest32, DomainError};
use trnm_persistence_pg::{CommitOutcome, CommitRequest, EntityHead, EntityId, PgPool};

use super::app::{Repository, RepositoryOperationalMetrics};

#[derive(Clone, Debug)]
pub struct PooledRepository {
    pool: PgPool,
}

impl PooledRepository {
    #[must_use]
    pub const fn new(pool: PgPool) -> Self {
        Self { pool }
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
        self.pool
            .acquire()?
            .bootstrap_entity(entity, authority_generation, state, updated_at_ms)
    }

    fn commit_command(&mut self, request: &CommitRequest) -> Result<CommitOutcome, DomainError> {
        self.pool.acquire()?.commit_command(request)
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
