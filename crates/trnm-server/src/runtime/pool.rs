use std::time::Duration;

use trnm_contracts::{Digest32, DomainError, SessionFamilyId, UserId};
use trnm_persistence_pg::{
    CommitOutcome, CommitRequest, EntityHead, EntityId, PgPool, RefreshRotationOutcome,
    RotateRefreshToken, SessionFamilyRecord, StorageActor, StorageBatchOperation,
    StorageListPosition, StorageNakamaBatchKind, StorageObjectKey, StoredStorageClientListPage,
    StoredStorageMutationReceipt, StoredStorageObject,
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
    last_legacy_native_failure: Option<trnm_persistence_pg::NakamaAccountError>,
}

impl PooledRepository {
    #[must_use]
    pub fn new(pool: PgPool) -> Self {
        let operation_budget = DATABASE_OPERATION_BUDGET.min(pool.policy().statement_timeout);
        Self {
            pool,
            operation_budget,
            last_legacy_native_failure: None,
        }
    }

    fn run<T>(
        &self,
        operation: impl FnOnce(&mut trnm_persistence_pg::PgRepository) -> Result<T, DomainError>,
    ) -> Result<T, DomainError> {
        self.pool
            .run_with_deadline(self.operation_budget, |repository| {
                repository.verify_storage_import_serving()?;
                operation(repository)
            })
    }

    fn run_with_budget<T>(
        &self,
        operation_budget: Duration,
        operation: impl FnOnce(&mut trnm_persistence_pg::PgRepository) -> Result<T, DomainError>,
    ) -> Result<T, DomainError> {
        self.pool
            .run_with_deadline(operation_budget.min(self.operation_budget), |repository| {
                repository.verify_storage_import_serving()?;
                operation(repository)
            })
    }
}

impl super::legacy_repository::LegacyNativeFailureObservation for PooledRepository {
    fn last_legacy_native_failure(&self) -> Option<trnm_persistence_pg::NakamaAccountError> {
        self.last_legacy_native_failure
    }
}
impl super::legacy_auth::LegacyUserRepository for PooledRepository {
    fn read_legacy_user(
        &mut self,
        user: UserId,
    ) -> Result<
        Option<super::legacy_auth::LegacyStoredUser>,
        super::legacy_auth::LegacyRepositoryError,
    > {
        let lease = self
            .pool
            .run_account_with_deadline(self.operation_budget, |repository| {
                // Native account admission precedes its lookup. Do not use the
                // default-storage preflight here or bypass the AccountsV5 gate.
                repository.read_nakama_user(user)
            });
        self.last_legacy_native_failure = lease
            .observed
            .as_ref()
            .and_then(|r| r.as_ref().err())
            .copied();
        super::legacy_repository::resolve_user_lease(lease)
    }
}
impl super::legacy_auth::LegacyDeviceRepository for PooledRepository {
    fn authenticate_legacy_device(
        &mut self,
        input: super::legacy_auth::LegacyDeviceRepositoryInput<'_>,
    ) -> Result<super::legacy_auth::LegacyDeviceAccount, super::legacy_auth::LegacyRepositoryError>
    {
        self.last_legacy_native_failure = None;
        let request = trnm_persistence_pg::AuthenticateDevice::new(
            input.device_id,
            input.requested_username,
            input.create,
        )
        .map_err(|error| {
            self.last_legacy_native_failure = Some(error);
            super::legacy_repository::map_native_error(error)
        })?;
        let lease = self
            .pool
            .run_account_with_deadline(self.operation_budget, |repository| {
                repository.authenticate_nakama_device_with_id_source(request, || {
                    super::legacy_auth::generate_account_id().map_err(|_| {
                        trnm_persistence_pg::NakamaAccountIdGenerationError::Unavailable
                    })
                })
            });
        self.last_legacy_native_failure = lease
            .observed
            .as_ref()
            .and_then(|r| r.as_ref().err())
            .copied();
        super::legacy_repository::resolve_device_lease(lease)
    }
}

impl super::legacy_auth::LegacyCustomRepository for PooledRepository {
    fn authenticate_legacy_custom(
        &mut self,
        input: super::legacy_auth::LegacyCustomRepositoryInput<'_>,
    ) -> Result<super::legacy_auth::LegacyCustomAccount, super::legacy_auth::LegacyRepositoryError>
    {
        self.last_legacy_native_failure = None;
        let request = trnm_persistence_pg::AuthenticateCustom::new(
            input.custom_id,
            input.requested_username,
            input.create,
        )
        .map_err(|error| {
            self.last_legacy_native_failure = Some(error);
            super::legacy_repository::map_native_error(error)
        })?;
        let lease = self
            .pool
            .run_account_with_deadline(self.operation_budget, |repository| {
                repository.authenticate_nakama_custom_with_id_source(request, || {
                    super::legacy_auth::generate_account_id().map_err(|_| {
                        trnm_persistence_pg::NakamaAccountIdGenerationError::Unavailable
                    })
                })
            });
        self.last_legacy_native_failure = lease
            .observed
            .as_ref()
            .and_then(|r| r.as_ref().err())
            .copied();
        super::legacy_repository::resolve_custom_lease(lease)
    }
}

impl Repository for PooledRepository {
    fn authenticate_legacy_custom(
        &mut self,
        input: super::legacy_auth::LegacyCustomRepositoryInput<'_>,
    ) -> Result<super::legacy_auth::LegacyCustomAccount, super::legacy_auth::LegacyRepositoryError>
    {
        super::legacy_auth::LegacyCustomRepository::authenticate_legacy_custom(self, input)
    }
    fn read_legacy_user(
        &mut self,
        user: UserId,
    ) -> Result<
        Option<super::legacy_auth::LegacyStoredUser>,
        super::legacy_auth::LegacyRepositoryError,
    > {
        super::legacy_auth::LegacyUserRepository::read_legacy_user(self, user)
    }
    fn authenticate_legacy_device(
        &mut self,
        input: super::legacy_auth::LegacyDeviceRepositoryInput<'_>,
    ) -> Result<super::legacy_auth::LegacyDeviceAccount, super::legacy_auth::LegacyRepositoryError>
    {
        super::legacy_auth::LegacyDeviceRepository::authenticate_legacy_device(self, input)
    }

    fn verify_storage_import_serving(&mut self) -> Result<(), DomainError> {
        self.pool
            .run_with_deadline(self.operation_budget, |repository| {
                repository.verify_storage_import_serving()
            })
    }

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

    fn read_storage_objects(
        &mut self,
        actor: StorageActor,
        keys: &[StorageObjectKey],
    ) -> Result<Vec<StoredStorageObject>, DomainError> {
        self.run(|repository| repository.read_storage_objects_with_metadata(actor, keys))
    }

    fn list_storage_objects_nakama(
        &mut self,
        actor: StorageActor,
        collection: &str,
        owner: Option<UserId>,
        after: Option<&StorageListPosition>,
        limit: usize,
    ) -> Result<StoredStorageClientListPage, DomainError> {
        self.run(|repository| {
            repository
                .list_storage_objects_nakama_with_metadata(actor, collection, owner, after, limit)
        })
    }

    fn apply_storage_batch(
        &mut self,
        actor: StorageActor,
        operations: &[StorageBatchOperation],
        updated_at_ms: u64,
    ) -> Result<Vec<StoredStorageMutationReceipt>, DomainError> {
        self.run(|repository| {
            repository.apply_storage_batch_with_metadata(actor, operations, updated_at_ms)
        })
    }

    fn apply_storage_batch_nakama(
        &mut self,
        actor: StorageActor,
        operations: &[StorageBatchOperation],
        updated_at_ms: u64,
        kind: StorageNakamaBatchKind,
    ) -> Result<Vec<StoredStorageMutationReceipt>, DomainError> {
        self.run(|repository| {
            repository.apply_storage_batch_nakama_with_metadata(
                actor,
                operations,
                updated_at_ms,
                kind,
            )
        })
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
        fn assert_contract<
            T: Clone
                + BudgetedRepository
                + InflightCancellation
                + super::super::legacy_auth::LegacyUserRepository
                + super::super::legacy_auth::LegacyDeviceRepository
                + super::super::legacy_repository::LegacyNativeFailureObservation,
        >() {
        }
        assert_contract::<PooledRepository>();
    }
}
