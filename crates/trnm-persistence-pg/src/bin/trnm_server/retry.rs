use std::thread;
use std::time::{Duration, Instant};

use trnm_contracts::{DomainError, RetryClass, StableCode};
use trnm_persistence_pg::{
    CommitOutcome, CommitRequest, EntityHead, EntityId, PgRepository,
};
use trnm_contracts::Digest32;

use super::app::Repository;

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
            total_budget: Duration::from_secs(2),
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

#[derive(Debug)]
pub struct RetryingRepository<R> {
    inner: R,
    policy: RetryPolicy,
}

impl<R> RetryingRepository<R> {
    pub fn new(inner: R, policy: RetryPolicy) -> Result<Self, DomainError> {
        Ok(Self {
            inner,
            policy: policy.validate()?,
        })
    }
}

impl Repository for RetryingRepository<PgRepository> {
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
        execute(self.policy, || self.inner.commit_command(request))
    }
}

pub fn execute<T>(
    policy: RetryPolicy,
    mut operation: impl FnMut() -> Result<T, DomainError>,
) -> Result<T, DomainError> {
    let policy = policy.validate()?;
    let started = Instant::now();
    let mut attempt = 0_u8;
    let mut backoff = policy.initial_backoff;
    loop {
        attempt = attempt.saturating_add(1);
        match operation() {
            Ok(value) => return Ok(value),
            Err(error) => {
                if !matches!(error.retry(), RetryClass::SafeImmediate | RetryClass::SafeBackoff) {
                    return Err(error);
                }
                if attempt >= policy.max_attempts || started.elapsed() >= policy.total_budget {
                    return Err(DomainError::new(
                        StableCode::Unavailable,
                        "database_retry_budget_exhausted",
                        RetryClass::SafeBackoff,
                    ));
                }
                if error.retry() == RetryClass::SafeBackoff {
                    let remaining = policy.total_budget.saturating_sub(started.elapsed());
                    let delay = backoff.min(remaining);
                    if !delay.is_zero() {
                        thread::sleep(delay);
                    }
                    backoff = backoff.saturating_mul(2).min(policy.maximum_backoff);
                }
            }
        }
    }
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
}
