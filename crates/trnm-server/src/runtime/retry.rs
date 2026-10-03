use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

use trnm_contracts::{Digest32, DomainError, RetryClass, SessionFamilyId, StableCode, UserId};
use trnm_persistence_pg::{
    CommitOutcome, CommitRequest, EntityHead, EntityId, RefreshRotationOutcome, RotateRefreshToken,
    SessionFamilyRecord, StorageActor, StorageBatchOperation, StorageListPosition,
    StorageNakamaBatchKind, StorageObjectKey, StoredStorageClientListPage,
    StoredStorageMutationReceipt, StoredStorageObject,
};
use trnm_session_core::RevocationReason;

use super::app::{Repository, RepositoryOperationalMetrics};

static JITTER_SEQUENCE: AtomicU64 = AtomicU64::new(0x9e37_79b9_7f4a_7c15);

pub const DATABASE_OPERATION_BUDGET: Duration = Duration::from_secs(2);

pub trait BudgetedRepository: Repository {
    fn commit_command_with_budget(
        &mut self,
        request: &CommitRequest,
        operation_budget: Duration,
    ) -> Result<CommitOutcome, DomainError>;
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct RetryPolicy {
    pub max_attempts: u8,
    pub total_budget: Duration,
    pub initial_backoff: Duration,
    pub maximum_backoff: Duration,
}

impl RetryPolicy {
    #[must_use]
    pub const fn candidate_default() -> Self {
        Self {
            max_attempts: 3,
            total_budget: DATABASE_OPERATION_BUDGET,
            initial_backoff: Duration::from_millis(5),
            maximum_backoff: Duration::from_millis(100),
        }
    }

    fn validate(self) -> Result<Self, DomainError> {
        if self.max_attempts == 0
            || self.total_budget.is_zero()
            || self.initial_backoff > self.maximum_backoff
            || self.maximum_backoff > self.total_budget
        {
            return Err(DomainError::new(
                StableCode::InvalidArgument,
                "database_retry_policy_invalid",
                RetryClass::Never,
            ));
        }
        Ok(self)
    }
}

#[derive(Debug, Default)]
struct RetryMetrics {
    attempts: AtomicU64,
    retries: AtomicU64,
    exhausted: AtomicU64,
    sleep_nanos: AtomicU64,
}

#[derive(Debug)]
pub struct RetryingRepository<R> {
    inner: R,
    policy: RetryPolicy,
    metrics: Arc<RetryMetrics>,
}

impl<R> RetryingRepository<R> {
    pub fn new(inner: R, policy: RetryPolicy) -> Result<Self, DomainError> {
        Ok(Self {
            inner,
            policy: policy.validate()?,
            metrics: Arc::new(RetryMetrics::default()),
        })
    }
}

// Legacy account methods are not authority commands and have no exact replay
// receipt. Forward exactly one lease call, including late/unknown completion.
impl<R: super::legacy_auth::LegacyUserRepository> super::legacy_auth::LegacyUserRepository
    for RetryingRepository<R>
{
    fn read_legacy_user(
        &mut self,
        user: UserId,
    ) -> Result<
        Option<super::legacy_auth::LegacyStoredUser>,
        super::legacy_auth::LegacyRepositoryError,
    > {
        self.inner.read_legacy_user(user)
    }
}
impl<R: super::legacy_auth::LegacyDeviceRepository> super::legacy_auth::LegacyDeviceRepository
    for RetryingRepository<R>
{
    fn authenticate_legacy_device(
        &mut self,
        input: super::legacy_auth::LegacyDeviceRepositoryInput<'_>,
    ) -> Result<super::legacy_auth::LegacyDeviceAccount, super::legacy_auth::LegacyRepositoryError>
    {
        self.inner.authenticate_legacy_device(input)
    }
}
impl<R: super::legacy_auth::LegacyCustomRepository> super::legacy_auth::LegacyCustomRepository
    for RetryingRepository<R>
{
    fn authenticate_legacy_custom(
        &mut self,
        input: super::legacy_auth::LegacyCustomRepositoryInput<'_>,
    ) -> Result<super::legacy_auth::LegacyCustomAccount, super::legacy_auth::LegacyRepositoryError>
    {
        self.inner.authenticate_legacy_custom(input)
    }
}
impl<R: super::legacy_repository::LegacyNativeFailureObservation>
    super::legacy_repository::LegacyNativeFailureObservation for RetryingRepository<R>
{
    fn last_legacy_native_failure(&self) -> Option<trnm_persistence_pg::NakamaAccountError> {
        self.inner.last_legacy_native_failure()
    }
}

impl<R: BudgetedRepository> Repository for RetryingRepository<R> {
    fn read_legacy_user(
        &mut self,
        user: UserId,
    ) -> Result<
        Option<super::legacy_auth::LegacyStoredUser>,
        super::legacy_auth::LegacyRepositoryError,
    > {
        Repository::read_legacy_user(&mut self.inner, user)
    }
    fn authenticate_legacy_device(
        &mut self,
        input: super::legacy_auth::LegacyDeviceRepositoryInput<'_>,
    ) -> Result<super::legacy_auth::LegacyDeviceAccount, super::legacy_auth::LegacyRepositoryError>
    {
        Repository::authenticate_legacy_device(&mut self.inner, input)
    }

    fn authenticate_legacy_custom(
        &mut self,
        input: super::legacy_auth::LegacyCustomRepositoryInput<'_>,
    ) -> Result<super::legacy_auth::LegacyCustomAccount, super::legacy_auth::LegacyRepositoryError>
    {
        Repository::authenticate_legacy_custom(&mut self.inner, input)
    }

    fn verify_storage_import_serving(&mut self) -> Result<(), DomainError> {
        self.inner.verify_storage_import_serving()
    }

    fn bootstrap_entity(
        &mut self,
        entity: EntityId,
        authority_generation: u64,
        state: Digest32,
        updated_at_ms: u64,
    ) -> Result<EntityHead, DomainError> {
        self.inner
            .bootstrap_entity(entity, authority_generation, state, updated_at_ms)
    }

    fn commit_command(&mut self, request: &CommitRequest) -> Result<CommitOutcome, DomainError> {
        let policy = self.policy;
        let metrics = Arc::clone(&self.metrics);
        execute_with_metrics(policy, metrics.as_ref(), |remaining| {
            self.inner.commit_command_with_budget(request, remaining)
        })
    }

    fn list_storage_objects_nakama(
        &mut self,
        actor: StorageActor,
        collection: &str,
        owner: Option<UserId>,
        after: Option<&StorageListPosition>,
        limit: usize,
    ) -> Result<StoredStorageClientListPage, DomainError> {
        // Preserve the complete readonly page snapshot and its one pool
        // deadline. No implicit authority-command retry or cursor replay.
        self.inner
            .list_storage_objects_nakama(actor, collection, owner, after, limit)
    }

    fn apply_storage_batch(
        &mut self,
        actor: StorageActor,
        operations: &[StorageBatchOperation],
        updated_at_ms: u64,
    ) -> Result<Vec<StoredStorageMutationReceipt>, DomainError> {
        // Storage write/delete receipts have no durable command identity or
        // exact replay lookup. An ambiguous commit must not repeat implicitly,
        // even when the adapter classifies an error as safe to retry.
        self.inner
            .apply_storage_batch(actor, operations, updated_at_ms)
    }

    fn read_storage_objects(
        &mut self,
        actor: StorageActor,
        keys: &[StorageObjectKey],
    ) -> Result<Vec<StoredStorageObject>, DomainError> {
        // The complete read batch has one pool deadline and snapshot. Do not
        // enter the authority-command retry supervisor implicitly.
        self.inner.read_storage_objects(actor, keys)
    }

    fn apply_storage_batch_nakama(
        &mut self,
        actor: StorageActor,
        operations: &[StorageBatchOperation],
        updated_at_ms: u64,
        kind: StorageNakamaBatchKind,
    ) -> Result<Vec<StoredStorageMutationReceipt>, DomainError> {
        // No durable command identity: one call, including ambiguous commit errors.
        self.inner
            .apply_storage_batch_nakama(actor, operations, updated_at_ms, kind)
    }

    fn verify_access_session(
        &mut self,
        family: SessionFamilyId,
        user: UserId,
        generation: u64,
    ) -> Result<SessionFamilyRecord, DomainError> {
        self.inner.verify_access_session(family, user, generation)
    }

    fn rotate_refresh_token(
        &mut self,
        request: &RotateRefreshToken,
    ) -> Result<RefreshRotationOutcome, DomainError> {
        // Refresh rotation and replay revocation are deliberately not retried
        // by the generic supervisor. A response-loss retry with the same
        // presented credential is security-significant and must be reconciled
        // by the caller instead of being repeated implicitly.
        self.inner.rotate_refresh_token(request)
    }

    fn revoke_session_family(
        &mut self,
        family: SessionFamilyId,
        user: UserId,
        reason: RevocationReason,
        revoked_at_ms: u64,
    ) -> Result<SessionFamilyRecord, DomainError> {
        self.inner
            .revoke_session_family(family, user, reason, revoked_at_ms)
    }

    fn operational_metrics(&self) -> RepositoryOperationalMetrics {
        let mut metrics = self.inner.operational_metrics();
        metrics.retry_attempts = self.metrics.attempts.load(Ordering::Relaxed);
        metrics.retries = self.metrics.retries.load(Ordering::Relaxed);
        metrics.retry_exhausted = self.metrics.exhausted.load(Ordering::Relaxed);
        metrics.retry_sleep_milliseconds = self
            .metrics
            .sleep_nanos
            .load(Ordering::Relaxed)
            .saturating_div(1_000_000);
        metrics
    }
}

#[cfg(test)]
pub fn execute<T>(
    policy: RetryPolicy,
    mut operation: impl FnMut() -> Result<T, DomainError>,
) -> Result<T, DomainError> {
    let metrics = RetryMetrics::default();
    execute_with_metrics(policy, &metrics, |_| operation())
}

fn execute_with_metrics<T>(
    policy: RetryPolicy,
    metrics: &RetryMetrics,
    mut operation: impl FnMut(Duration) -> Result<T, DomainError>,
) -> Result<T, DomainError> {
    let policy = policy.validate()?;
    let started = Instant::now();
    let mut attempt = 0_u8;
    let mut backoff = policy.initial_backoff;
    loop {
        let remaining = policy.total_budget.saturating_sub(started.elapsed());
        if remaining.is_zero() {
            metrics.exhausted.fetch_add(1, Ordering::Relaxed);
            return Err(retry_budget_exhausted());
        }

        attempt = attempt.saturating_add(1);
        metrics.attempts.fetch_add(1, Ordering::Relaxed);
        let result = operation(remaining);
        if started.elapsed() >= policy.total_budget {
            metrics.exhausted.fetch_add(1, Ordering::Relaxed);
            return Err(retry_budget_exhausted());
        }

        match result {
            Ok(value) => return Ok(value),
            Err(error) => {
                if !matches!(
                    error.retry(),
                    RetryClass::SafeImmediate | RetryClass::SafeBackoff
                ) {
                    return Err(error);
                }
                if attempt >= policy.max_attempts {
                    metrics.exhausted.fetch_add(1, Ordering::Relaxed);
                    return Err(retry_budget_exhausted());
                }

                metrics.retries.fetch_add(1, Ordering::Relaxed);
                if error.retry() == RetryClass::SafeBackoff {
                    let remaining = policy.total_budget.saturating_sub(started.elapsed());
                    let delay = jittered_backoff(backoff).min(remaining);
                    if !delay.is_zero() {
                        metrics.sleep_nanos.fetch_add(
                            u64::try_from(delay.as_nanos()).unwrap_or(u64::MAX),
                            Ordering::Relaxed,
                        );
                        thread::sleep(delay);
                    }
                    backoff = backoff.saturating_mul(2).min(policy.maximum_backoff);
                }
            }
        }
    }
}

fn jittered_backoff(base: Duration) -> Duration {
    if base.is_zero() {
        return Duration::ZERO;
    }
    let sequence = JITTER_SEQUENCE
        .fetch_add(0x9e37_79b9_7f4a_7c15, Ordering::Relaxed)
        .rotate_left(17)
        ^ 0xa076_1d64_78bd_642f;
    let base_nanos = base.as_nanos();
    let minimum = base_nanos / 2;
    let width = base_nanos.saturating_sub(minimum).saturating_add(1);
    let selected = minimum.saturating_add(u128::from(sequence) % width);
    Duration::from_nanos(u64::try_from(selected).unwrap_or(u64::MAX))
}

const fn retry_budget_exhausted() -> DomainError {
    DomainError::new(
        StableCode::Unavailable,
        "database_retry_budget_exhausted",
        RetryClass::SafeBackoff,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use trnm_persistence_pg::{
        ContentVersion, ReadPermission, StorageDeleteOperation, StorageObjectKey,
        StorageWriteOperation, VersionCheck, WritePermission,
    };

    #[derive(Debug)]
    struct StorageMutationRepository {
        calls: usize,
        actor: StorageActor,
        operation: StorageBatchOperation,
        updated_at_ms: u64,
        failure: DomainError,
    }

    impl Repository for StorageMutationRepository {
        fn bootstrap_entity(
            &mut self,
            _entity: EntityId,
            _authority_generation: u64,
            _state: Digest32,
            _updated_at_ms: u64,
        ) -> Result<EntityHead, DomainError> {
            panic!("storage mutation must not bootstrap an entity")
        }

        fn commit_command(
            &mut self,
            _request: &CommitRequest,
        ) -> Result<CommitOutcome, DomainError> {
            panic!("storage mutation must not commit an authority command")
        }

        fn apply_storage_batch(
            &mut self,
            actor: StorageActor,
            operations: &[StorageBatchOperation],
            updated_at_ms: u64,
        ) -> Result<Vec<StoredStorageMutationReceipt>, DomainError> {
            self.calls += 1;
            assert_eq!(actor, self.actor);
            assert_eq!(operations, std::slice::from_ref(&self.operation));
            assert_eq!(updated_at_ms, self.updated_at_ms);
            Err(self.failure)
        }

        fn apply_storage_batch_nakama(
            &mut self,
            actor: StorageActor,
            operations: &[StorageBatchOperation],
            updated_at_ms: u64,
            kind: StorageNakamaBatchKind,
        ) -> Result<Vec<StoredStorageMutationReceipt>, DomainError> {
            assert!(matches!(
                (kind, &self.operation),
                (
                    StorageNakamaBatchKind::Write,
                    StorageBatchOperation::Write(_)
                ) | (
                    StorageNakamaBatchKind::Delete,
                    StorageBatchOperation::Delete(_)
                )
            ));
            self.apply_storage_batch(actor, operations, updated_at_ms)
        }

        fn list_storage_objects_nakama(
            &mut self,
            actor: StorageActor,
            collection: &str,
            owner: Option<UserId>,
            after: Option<&StorageListPosition>,
            limit: usize,
        ) -> Result<StoredStorageClientListPage, DomainError> {
            self.calls += 1;
            assert_eq!(actor, self.actor);
            assert_eq!(collection, self.operation.key().collection());
            assert_eq!(owner, Some(self.operation.key().user_id()));
            assert_eq!(
                after.map(|position| position.key.as_str()),
                Some(self.operation.key().key())
            );
            assert_eq!(limit, 100);
            Err(self.failure)
        }

        fn read_storage_objects(
            &mut self,
            actor: StorageActor,
            keys: &[StorageObjectKey],
        ) -> Result<Vec<StoredStorageObject>, DomainError> {
            self.calls += 1;
            assert_eq!(actor, self.actor);
            assert_eq!(keys, std::slice::from_ref(self.operation.key()));
            Err(self.failure)
        }
    }

    impl BudgetedRepository for StorageMutationRepository {
        fn commit_command_with_budget(
            &mut self,
            _request: &CommitRequest,
            _operation_budget: Duration,
        ) -> Result<CommitOutcome, DomainError> {
            panic!("storage mutation must not enter command retry supervision")
        }
    }

    fn error(retry: RetryClass) -> DomainError {
        DomainError::new(StableCode::Aborted, "synthetic", retry)
    }

    fn immediate_policy(max_attempts: u8) -> RetryPolicy {
        RetryPolicy {
            max_attempts,
            total_budget: Duration::from_secs(1),
            initial_backoff: Duration::ZERO,
            maximum_backoff: Duration::ZERO,
        }
    }

    #[test]
    fn safe_immediate_failure_is_retried_within_attempt_budget() {
        let mut calls = 0_u8;
        let result = execute(immediate_policy(3), || {
            calls += 1;
            if calls == 1 {
                Err(error(RetryClass::SafeImmediate))
            } else {
                Ok("committed")
            }
        })
        .unwrap();
        assert_eq!(result, "committed");
        assert_eq!(calls, 2);
    }

    #[test]
    fn storage_write_and_delete_are_never_automatically_retried() {
        let owner = UserId::new([1; 16]);
        let key = StorageObjectKey::new("profile", "main", owner).unwrap();
        let operations = [
            StorageBatchOperation::Write(StorageWriteOperation {
                key: key.clone(),
                value: br#"{"score":1}"#.to_vec(),
                expected: VersionCheck::MustNotExist,
                read_permission: ReadPermission::OWNER,
                write_permission: WritePermission::OWNER,
            }),
            StorageBatchOperation::Delete(StorageDeleteOperation {
                key,
                expected_version: Some(ContentVersion::from_value(br#"{"score":0}"#).into()),
            }),
        ];
        for operation in operations {
            for retry in [
                RetryClass::Never,
                RetryClass::SafeImmediate,
                RetryClass::SafeBackoff,
                RetryClass::ResyncRequired,
            ] {
                let failure = error(retry);
                let mut repository = RetryingRepository::new(
                    StorageMutationRepository {
                        calls: 0,
                        actor: StorageActor::User(owner),
                        operation: operation.clone(),
                        updated_at_ms: 123,
                        failure,
                    },
                    immediate_policy(3),
                )
                .unwrap();
                let returned = repository
                    .apply_storage_batch(
                        StorageActor::User(owner),
                        std::slice::from_ref(&operation),
                        123,
                    )
                    .unwrap_err();
                assert_eq!(returned, failure);
                assert_eq!(repository.inner.calls, 1);
                let metrics = repository.operational_metrics();
                assert_eq!(metrics.retry_attempts, 0);
                assert_eq!(metrics.retries, 0);
                assert_eq!(metrics.retry_exhausted, 0);
                assert_eq!(metrics.retry_sleep_milliseconds, 0);
            }
        }
    }

    #[test]
    fn nakama_homogeneous_storage_batches_are_not_implicitly_retried() {
        let owner = UserId::new([0x94; 16]);
        let key = StorageObjectKey::new("profile", "nakama-retry", owner).unwrap();
        let operations = [
            (
                StorageNakamaBatchKind::Write,
                StorageBatchOperation::Write(StorageWriteOperation {
                    key: key.clone(),
                    value: b"{}".to_vec(),
                    expected: VersionCheck::Any,
                    read_permission: ReadPermission::OWNER,
                    write_permission: WritePermission::OWNER,
                }),
            ),
            (
                StorageNakamaBatchKind::Delete,
                StorageBatchOperation::Delete(StorageDeleteOperation {
                    key,
                    expected_version: None,
                }),
            ),
        ];
        for (kind, operation) in operations {
            for retry in [
                RetryClass::Never,
                RetryClass::SafeImmediate,
                RetryClass::SafeBackoff,
                RetryClass::ResyncRequired,
            ] {
                let failure = error(retry);
                let mut repository = RetryingRepository::new(
                    StorageMutationRepository {
                        calls: 0,
                        actor: StorageActor::User(owner),
                        operation: operation.clone(),
                        updated_at_ms: 123,
                        failure,
                    },
                    immediate_policy(3),
                )
                .unwrap();
                assert_eq!(
                    repository
                        .apply_storage_batch_nakama(
                            StorageActor::User(owner),
                            std::slice::from_ref(&operation),
                            123,
                            kind
                        )
                        .unwrap_err(),
                    failure
                );
                assert_eq!(repository.inner.calls, 1);
                let metrics = repository.operational_metrics();
                assert_eq!(metrics.retry_attempts, 0);
                assert_eq!(metrics.retries, 0);
                assert_eq!(metrics.retry_exhausted, 0);
                assert_eq!(metrics.retry_sleep_milliseconds, 0);
            }
        }
    }

    #[test]
    fn never_and_resync_errors_are_not_retried() {
        for retry in [RetryClass::Never, RetryClass::ResyncRequired] {
            let mut calls = 0;
            let returned = execute(immediate_policy(3), || {
                calls += 1;
                Err::<(), _>(error(retry))
            })
            .unwrap_err();
            assert_eq!(calls, 1);
            assert_eq!(returned.retry(), retry);
        }
    }

    #[test]
    fn storage_reads_do_not_enter_implicit_retry_supervision() {
        let actor = StorageActor::User(UserId::new([1; 16]));
        let key = StorageObjectKey::new("system", "global", UserId::new([0; 16])).unwrap();
        for retry in [
            RetryClass::Never,
            RetryClass::SafeImmediate,
            RetryClass::SafeBackoff,
            RetryClass::ResyncRequired,
        ] {
            let failure = error(retry);
            let mut repository = RetryingRepository::new(
                StorageMutationRepository {
                    calls: 0,
                    actor,
                    operation: StorageBatchOperation::Delete(StorageDeleteOperation {
                        key: key.clone(),
                        expected_version: None,
                    }),
                    updated_at_ms: 0,
                    failure,
                },
                immediate_policy(3),
            )
            .unwrap();
            let returned = repository
                .read_storage_objects(actor, std::slice::from_ref(&key))
                .unwrap_err();
            assert_eq!(returned, failure);
            assert_eq!(repository.inner.calls, 1);
            let after = StorageListPosition {
                key: key.key().to_owned(),
                user_id: UserId::new([2; 16]),
                read: -10,
            };
            assert_eq!(
                repository
                    .list_storage_objects_nakama(
                        actor,
                        key.collection(),
                        Some(key.user_id()),
                        Some(&after),
                        100
                    )
                    .unwrap_err(),
                failure
            );
            assert_eq!(repository.inner.calls, 2);
            assert_eq!(repository.operational_metrics().retry_attempts, 0);
        }
    }

    #[test]
    fn exhausted_retry_returns_stable_unavailable_error() {
        let mut calls = 0;
        let returned = execute(immediate_policy(2), || {
            calls += 1;
            Err::<(), _>(error(RetryClass::SafeBackoff))
        })
        .unwrap_err();
        assert_eq!(calls, 2);
        assert_eq!(returned.code(), StableCode::Unavailable);
        assert_eq!(returned.reason(), "database_retry_budget_exhausted");
        assert_eq!(returned.retry(), RetryClass::SafeBackoff);
    }

    #[test]
    fn elapsed_budget_prevents_an_additional_attempt() {
        let mut calls = 0;
        let returned = execute(
            RetryPolicy {
                max_attempts: 3,
                total_budget: Duration::from_millis(1),
                initial_backoff: Duration::ZERO,
                maximum_backoff: Duration::ZERO,
            },
            || {
                calls += 1;
                thread::sleep(Duration::from_millis(2));
                Err::<(), _>(error(RetryClass::SafeImmediate))
            },
        )
        .unwrap_err();
        assert_eq!(calls, 1);
        assert_eq!(returned.reason(), "database_retry_budget_exhausted");
    }

    #[test]
    fn successful_result_after_budget_is_rejected() {
        let returned = execute(
            RetryPolicy {
                max_attempts: 1,
                total_budget: Duration::from_millis(1),
                initial_backoff: Duration::ZERO,
                maximum_backoff: Duration::ZERO,
            },
            || {
                thread::sleep(Duration::from_millis(2));
                Ok::<_, DomainError>("too-late")
            },
        )
        .unwrap_err();
        assert_eq!(returned.reason(), "database_retry_budget_exhausted");
    }

    #[test]
    fn each_attempt_receives_only_the_remaining_total_budget() {
        let policy = RetryPolicy {
            max_attempts: 2,
            total_budget: Duration::from_millis(100),
            initial_backoff: Duration::ZERO,
            maximum_backoff: Duration::ZERO,
        };
        let metrics = RetryMetrics::default();
        let mut observed = Vec::new();
        let result = execute_with_metrics(policy, &metrics, |remaining| {
            observed.push(remaining);
            if observed.len() == 1 {
                thread::sleep(Duration::from_millis(5));
                Err(error(RetryClass::SafeImmediate))
            } else {
                Ok("committed")
            }
        })
        .unwrap();
        assert_eq!(result, "committed");
        assert_eq!(observed.len(), 2);
        assert!(observed[1] < observed[0]);
    }

    #[test]
    fn invalid_retry_policy_fails_before_operation() {
        let mut called = false;
        let returned = execute(
            RetryPolicy {
                max_attempts: 0,
                total_budget: Duration::ZERO,
                initial_backoff: Duration::ZERO,
                maximum_backoff: Duration::ZERO,
            },
            || {
                called = true;
                Ok(())
            },
        )
        .unwrap_err();
        assert!(!called);
        assert_eq!(returned.reason(), "database_retry_policy_invalid");
    }

    #[test]
    fn jitter_remains_inside_half_to_full_backoff() {
        let base = Duration::from_millis(100);
        for _ in 0..64 {
            let value = jittered_backoff(base);
            assert!(value >= Duration::from_millis(50));
            assert!(value <= base);
        }
    }
}

#[cfg(test)]
mod legacy_forwarding_tests {
    use super::*;
    use crate::runtime::legacy_auth::*;
    struct OneCall {
        calls: usize,
    }
    impl LegacyUserRepository for OneCall {
        fn read_legacy_user(
            &mut self,
            _: UserId,
        ) -> Result<Option<LegacyStoredUser>, LegacyRepositoryError> {
            self.calls += 1;
            Err(LegacyRepositoryError::Unavailable)
        }
    }
    impl LegacyDeviceRepository for OneCall {
        fn authenticate_legacy_device(
            &mut self,
            _: LegacyDeviceRepositoryInput<'_>,
        ) -> Result<LegacyDeviceAccount, LegacyRepositoryError> {
            self.calls += 1;
            Err(LegacyRepositoryError::Lease(Box::new(LegacyLeaseFailure {
                boundary_error: DomainError::new(
                    StableCode::Unavailable,
                    "database_operation_deadline_exceeded",
                    RetryClass::SafeBackoff,
                ),
                setup_error: None,
                completion: LegacyLeaseCompletion::ConfirmedCreation {
                    committed_cleanup_failure: None,
                },
                cancellation: LegacyLeaseCancellation::Deadline,
                lease_retired: true,
            })))
        }
    }
    #[test]
    fn legacy_read_and_late_commit_forward_once_without_authority_retry_metrics() {
        let mut repository =
            RetryingRepository::new(OneCall { calls: 0 }, RetryPolicy::candidate_default())
                .unwrap();
        assert!(repository.read_legacy_user(UserId::new([0; 16])).is_err());
        let error = repository
            .authenticate_legacy_device(LegacyDeviceRepositoryInput {
                device_id: "0123456789",
                requested_username: "name",
                create: true,
            })
            .unwrap_err();
        assert!(matches!(
            error,
            LegacyRepositoryError::Lease(ref record)
                if matches!(record.completion, LegacyLeaseCompletion::ConfirmedCreation { .. })
        ));
        assert_eq!(repository.inner.calls, 2);
        assert_eq!(repository.metrics.attempts.load(Ordering::Relaxed), 0);
        assert_eq!(repository.metrics.retries.load(Ordering::Relaxed), 0);
    }
}

#[cfg(test)]
mod custom_forwarding_tests {
    use super::*;
    use crate::runtime::legacy_auth::*;

    #[derive(Debug)]
    struct CustomCall {
        calls: usize,
        expected: LegacyCustomRepositoryInput<'static>,
        outcome: Option<Result<LegacyCustomAccount, LegacyRepositoryError>>,
    }
    impl LegacyCustomRepository for CustomCall {
        fn authenticate_legacy_custom(
            &mut self,
            input: LegacyCustomRepositoryInput<'_>,
        ) -> Result<LegacyCustomAccount, LegacyRepositoryError> {
            self.calls += 1;
            assert_eq!(input.custom_id, self.expected.custom_id);
            assert_eq!(input.requested_username, self.expected.requested_username);
            assert_eq!(input.create, self.expected.create);
            self.outcome
                .take()
                .expect("Custom effect called more than once")
        }
    }
    impl Repository for CustomCall {
        fn bootstrap_entity(
            &mut self,
            _: EntityId,
            _: u64,
            _: Digest32,
            _: u64,
        ) -> Result<EntityHead, DomainError> {
            panic!("Custom must not bootstrap authority commands")
        }
        fn commit_command(&mut self, _: &CommitRequest) -> Result<CommitOutcome, DomainError> {
            panic!("Custom must not enter authority command commit")
        }
        fn authenticate_legacy_custom(
            &mut self,
            input: LegacyCustomRepositoryInput<'_>,
        ) -> Result<LegacyCustomAccount, LegacyRepositoryError> {
            LegacyCustomRepository::authenticate_legacy_custom(self, input)
        }
    }
    impl BudgetedRepository for CustomCall {
        fn commit_command_with_budget(
            &mut self,
            _: &CommitRequest,
            _: Duration,
        ) -> Result<CommitOutcome, DomainError> {
            panic!("Custom must not enter command retry supervision")
        }
    }
    fn forward(
        repository: &mut RetryingRepository<CustomCall>,
        input: LegacyCustomRepositoryInput<'_>,
        via_app_repository: bool,
    ) -> Result<LegacyCustomAccount, LegacyRepositoryError> {
        if via_app_repository {
            Repository::authenticate_legacy_custom(repository, input)
        } else {
            LegacyCustomRepository::authenticate_legacy_custom(repository, input)
        }
    }
    fn assert_one_effect_no_retry(repository: &RetryingRepository<CustomCall>) {
        assert_eq!(repository.inner.calls, 1);
        assert!(repository.inner.outcome.is_none());
        assert_eq!(repository.metrics.attempts.load(Ordering::Relaxed), 0);
        assert_eq!(repository.metrics.retries.load(Ordering::Relaxed), 0);
        assert_eq!(repository.metrics.exhausted.load(Ordering::Relaxed), 0);
        assert_eq!(repository.metrics.sleep_nanos.load(Ordering::Relaxed), 0);
    }
    #[test]
    fn custom_both_dispatch_paths_preserve_committed_account_once_without_retry() {
        for via_app_repository in [false, true] {
            for created in [false, true] {
                let input = LegacyCustomRepositoryInput {
                    custom_id: "source_custom_id",
                    requested_username: "ignored_query_username",
                    create: created,
                };
                let mut repository = RetryingRepository::new(
                    CustomCall {
                        calls: 0,
                        expected: input,
                        outcome: Some(Ok(LegacyCustomAccount {
                            user_id: UserId::new([7; 16]),
                            stored_username: "native_stored_username".to_owned(),
                            created,
                        })),
                    },
                    RetryPolicy::candidate_default(),
                )
                .unwrap();
                let account = forward(&mut repository, input, via_app_repository).unwrap();
                assert_eq!(account.user_id, UserId::new([7; 16]));
                assert_eq!(account.stored_username, "native_stored_username");
                assert_eq!(account.created, created);
                assert_one_effect_no_retry(&repository);
            }
        }
    }
    #[test]
    fn custom_both_dispatch_paths_preserve_errors_late_and_unknown_completion_once() {
        let native = LegacyNativeAccountFailure {
            code: StableCode::Internal,
            phase: Some("custom_insert"),
            last_failure: Some(LegacyCommittedCleanupFailure {
                sqlstate: Some(*b"23505"),
                username_collision: false,
                transaction_closed: false,
                reason: "source_custom_unique_insert_failed",
            }),
            cleanup_failure: None,
            attempts: Some(1),
            exhausted: Some(false),
            unknown_commit: Some(true),
        };
        let late = |completion, cancellation| {
            LegacyRepositoryError::Lease(Box::new(LegacyLeaseFailure {
                boundary_error: DomainError::new(
                    StableCode::Unavailable,
                    "database_operation_deadline_exceeded",
                    RetryClass::SafeBackoff,
                ),
                setup_error: None,
                completion,
                cancellation,
                lease_retired: true,
            }))
        };
        for via_app_repository in [false, true] {
            for error in [
                LegacyRepositoryError::UserNotFound,
                LegacyRepositoryError::UserBanned,
                LegacyRepositoryError::UsernameAlreadyInUse,
                LegacyRepositoryError::CustomLookupFailed(native),
                LegacyRepositoryError::NativeFailure(native),
                late(
                    LegacyLeaseCompletion::ConfirmedCreation {
                        committed_cleanup_failure: None,
                    },
                    LegacyLeaseCancellation::Deadline,
                ),
                late(
                    LegacyLeaseCompletion::NativeFailure(native),
                    LegacyLeaseCancellation::Shutdown,
                ),
                late(
                    LegacyLeaseCompletion::NotObserved,
                    LegacyLeaseCancellation::Deadline,
                ),
            ] {
                let expected = error.clone();
                let lease_address = match &error {
                    LegacyRepositoryError::Lease(record) => {
                        Some(record.as_ref() as *const LegacyLeaseFailure)
                    }
                    _ => None,
                };
                let input = LegacyCustomRepositoryInput {
                    custom_id: "source_custom_id",
                    requested_username: "requested_name",
                    create: false,
                };
                let mut repository = RetryingRepository::new(
                    CustomCall {
                        calls: 0,
                        expected: input,
                        outcome: Some(Err(error)),
                    },
                    RetryPolicy::candidate_default(),
                )
                .unwrap();
                let returned = forward(&mut repository, input, via_app_repository).unwrap_err();
                assert_eq!(returned, expected);
                if let (Some(address), LegacyRepositoryError::Lease(record)) =
                    (lease_address, &returned)
                {
                    assert_eq!(record.as_ref() as *const LegacyLeaseFailure, address);
                }
                assert_one_effect_no_retry(&repository);
            }
        }
    }
}
