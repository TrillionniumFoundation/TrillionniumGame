use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

use trnm_contracts::{Digest32, DomainError, RetryClass, SessionFamilyId, StableCode, UserId};
use trnm_persistence_pg::{
    CommitOutcome, CommitRequest, EntityHead, EntityId, RefreshRotationOutcome, RotateRefreshToken,
    SessionFamilyRecord,
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

impl<R: BudgetedRepository> Repository for RetryingRepository<R> {
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
