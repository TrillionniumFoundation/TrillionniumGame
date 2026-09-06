use std::time::Duration;

use trnm_contracts::{Digest32, DomainError, SessionFamilyId, UserId};
use trnm_persistence_pg::{
    CommitOutcome, CommitRequest, EntityHead, EntityId, PgPool, RefreshRotationOutcome,
    RotateRefreshToken, SessionFamilyRecord,
};
use trnm_session_core::RevocationReason;

use super::app::{Repository, RepositoryOperationalMetrics};
use super::retry::{BudgetedRepository, DATABASE_OPERATION_BUDGET};

pub trait InflightCancellation {
    fn cancel_inflight(&self) -> u64;
}

#[derive(Clone, Debug)]
pub struct PooledRepository {
    pool: PgPool,
    operation_budget: Duration,
}

impl PooledRepository {
    #[must_use]
    pub fn new(pool: PgPool) -> Self {
        let operation_budget = DATABASE_OPERATION_BUDGET.min(pool.policy().statement_timeout);
        Self {
            pool,
            operation_budget,
        }
    }

    fn run<T>(
        &self,
        operation: impl FnOnce(&mut trnm_persistence_pg::PgRepository) -> Result<T, DomainError>,
    ) -> Result<T, DomainError> {
        self.pool
            .run_with_deadline(self.operation_budget, operation)
    }

    fn run_with_budget<T>(
        &self,
        operation_budget: Duration,
        operation: impl FnOnce(&mut trnm_persistence_pg::PgRepository) -> Result<T, DomainError>,
    ) -> Result<T, DomainError> {
        self.pool
            .run_with_deadline(operation_budget.min(self.operation_budget), operation)
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
        self.run(|repository| {
            repository.bootstrap_entity(entity, authority_generation, state, updated_at_ms)
        })
    }

    fn commit_command(&mut self, request: &CommitRequest) -> Result<CommitOutcome, DomainError> {
        self.run(|repository| repository.commit_command(request))
    }

    fn verify_access_session(
        &mut self,
        family: SessionFamilyId,
        user: UserId,
        generation: u64,
    ) -> Result<SessionFamilyRecord, DomainError> {
        self.run(|repository| repository.verify_access_session(family, user, generation))
    }

    fn rotate_refresh_token(
        &mut self,
        request: &RotateRefreshToken,
    ) -> Result<RefreshRotationOutcome, DomainError> {
        self.run(|repository| repository.rotate_refresh_token(request))
    }

    fn revoke_session_family(
        &mut self,
        family: SessionFamilyId,
        user: UserId,
        reason: RevocationReason,
        revoked_at_ms: u64,
    ) -> Result<SessionFamilyRecord, DomainError> {
        self.run(|repository| repository.revoke_session_family(family, user, reason, revoked_at_ms))
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
            database_inflight_operations: snapshot.inflight_operations,
            database_deadline_cancellations: snapshot.deadline_cancellations,
            database_shutdown_cancellations: snapshot.shutdown_cancellations,
            database_cancellation_deliveries: snapshot.cancellation_deliveries,
            database_cancellation_failures: snapshot.cancellation_failures,
            ..RepositoryOperationalMetrics::default()
        }
    }
}

impl BudgetedRepository for PooledRepository {
    fn commit_command_with_budget(
        &mut self,
        request: &CommitRequest,
        operation_budget: Duration,
    ) -> Result<CommitOutcome, DomainError> {
        self.run_with_budget(operation_budget, |repository| {
            repository.commit_command(request)
        })
    }
}

impl InflightCancellation for PooledRepository {
    fn cancel_inflight(&self) -> u64 {
        self.pool.cancel_inflight()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wrapper_is_cloneable_and_supports_budget_and_shutdown_contracts() {
        fn assert_contract<T: Clone + BudgetedRepository + InflightCancellation>() {}
        assert_contract::<PooledRepository>();
    }
}
