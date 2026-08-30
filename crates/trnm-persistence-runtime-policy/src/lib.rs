#![forbid(unsafe_code)]
#![deny(missing_debug_implementations)]

//! Pure, bounded runtime policy for database clients.
//!
//! The crate performs no I/O and owns no connection. It defines the policy a
//! PostgreSQL/CockroachDB adapter must apply around pooled, TLS-protected,
//! deadline-bound operations. Automatic retry is allowed only for idempotent
//! operation classes and retry-aware domain errors.

use core::fmt;

use trnm_contracts::{DomainError, RetryClass};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DatabaseProfile {
    PostgreSql,
    CockroachDb,
}

impl DatabaseProfile {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::PostgreSql => "postgresql",
            Self::CockroachDb => "cockroachdb",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DeploymentClass {
    Developer,
    Compatibility,
    Production,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum TlsMode {
    DisabledDeveloperOnly,
    RequireFull {
        root_ca_handle: String,
        client_identity_handle: Option<String>,
        server_name: String,
    },
}

impl TlsMode {
    pub fn validate(&self, deployment: DeploymentClass) -> Result<(), PolicyError> {
        match self {
            Self::DisabledDeveloperOnly if deployment == DeploymentClass::Developer => Ok(()),
            Self::DisabledDeveloperOnly => Err(PolicyError::TlsRequired),
            Self::RequireFull {
                root_ca_handle,
                client_identity_handle,
                server_name,
            } => {
                validate_handle(root_ca_handle)?;
                if let Some(handle) = client_identity_handle {
                    validate_handle(handle)?;
                }
                if server_name.is_empty()
                    || server_name.len() > 253
                    || !server_name
                        .bytes()
                        .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-'))
                {
                    return Err(PolicyError::InvalidServerName);
                }
                Ok(())
            }
        }
    }
}

fn validate_handle(value: &str) -> Result<(), PolicyError> {
    if value.is_empty()
        || value.len() > 512
        || !value.bytes().all(|byte| {
            byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b':' | b'-' | b'/' | b'@')
        })
    {
        return Err(PolicyError::InvalidSecretHandle);
    }
    Ok(())
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct PoolPolicy {
    pub minimum_idle: u16,
    pub maximum_size: u16,
    pub acquire_timeout_ms: u64,
    pub idle_timeout_ms: u64,
    pub maximum_lifetime_ms: u64,
}

impl PoolPolicy {
    pub fn validate(self) -> Result<(), PolicyError> {
        if self.maximum_size == 0 || self.minimum_idle > self.maximum_size {
            return Err(PolicyError::InvalidPoolSize);
        }
        if self.acquire_timeout_ms == 0
            || self.idle_timeout_ms == 0
            || self.maximum_lifetime_ms == 0
            || self.acquire_timeout_ms > self.idle_timeout_ms
            || self.idle_timeout_ms > self.maximum_lifetime_ms
        {
            return Err(PolicyError::InvalidPoolTimeout);
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct StatementPolicy {
    pub statement_timeout_ms: u64,
    pub lock_timeout_ms: u64,
    pub transaction_timeout_ms: u64,
}

impl StatementPolicy {
    pub fn validate(self) -> Result<(), PolicyError> {
        if self.statement_timeout_ms == 0
            || self.lock_timeout_ms == 0
            || self.transaction_timeout_ms == 0
            || self.lock_timeout_ms > self.statement_timeout_ms
            || self.statement_timeout_ms > self.transaction_timeout_ms
        {
            return Err(PolicyError::InvalidStatementTimeout);
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct RetryBudget {
    /// Total attempts including the initial attempt.
    pub maximum_attempts: u8,
    pub base_backoff_ms: u64,
    pub maximum_backoff_ms: u64,
    pub total_deadline_ms: u64,
}

impl RetryBudget {
    pub fn validate(self) -> Result<(), PolicyError> {
        if self.maximum_attempts == 0
            || self.maximum_attempts > 16
            || self.base_backoff_ms == 0
            || self.maximum_backoff_ms < self.base_backoff_ms
            || self.total_deadline_ms == 0
            || self.maximum_backoff_ms >= self.total_deadline_ms
        {
            return Err(PolicyError::InvalidRetryBudget);
        }
        Ok(())
    }

    #[must_use]
    pub fn backoff_ms(self, completed_attempts: u8) -> u64 {
        let exponent = u32::from(completed_attempts.saturating_sub(1)).min(20);
        self.base_backoff_ms
            .saturating_mul(1u64 << exponent)
            .min(self.maximum_backoff_ms)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum OperationClass {
    ReadOnly,
    IdempotentCommand,
    OutboxReceiptApply,
    NonIdempotentExternalEffect,
}

impl OperationClass {
    #[must_use]
    pub const fn automatic_retry_allowed(self) -> bool {
        matches!(
            self,
            Self::ReadOnly | Self::IdempotentCommand | Self::OutboxReceiptApply
        )
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DatabaseRuntimePolicy {
    pub profile: DatabaseProfile,
    pub deployment: DeploymentClass,
    pub tls: TlsMode,
    pub pool: PoolPolicy,
    pub statements: StatementPolicy,
    pub retry: RetryBudget,
}

impl DatabaseRuntimePolicy {
    pub fn validate(&self) -> Result<(), PolicyError> {
        self.tls.validate(self.deployment)?;
        self.pool.validate()?;
        self.statements.validate()?;
        self.retry.validate()?;
        if self.retry.total_deadline_ms > self.statements.transaction_timeout_ms {
            return Err(PolicyError::RetryDeadlineExceedsTransactionTimeout);
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct RetryContext {
    /// Number of attempts already completed, including the failed attempt.
    pub completed_attempts: u8,
    pub elapsed_ms: u64,
    pub cancelled: bool,
    pub operation: OperationClass,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RetryStopReason {
    Cancelled,
    OperationNotIdempotent,
    ErrorNotRetryable,
    ResyncRequired,
    AttemptLimit,
    Deadline,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RetryDecision {
    Retry { next_attempt: u8, delay_ms: u64 },
    Stop { reason: RetryStopReason },
}

pub fn decide_retry(
    budget: RetryBudget,
    error: DomainError,
    context: RetryContext,
) -> Result<RetryDecision, PolicyError> {
    budget.validate()?;
    if context.completed_attempts == 0 {
        return Err(PolicyError::InvalidCompletedAttempts);
    }
    if context.cancelled {
        return Ok(RetryDecision::Stop {
            reason: RetryStopReason::Cancelled,
        });
    }
    if !context.operation.automatic_retry_allowed() {
        return Ok(RetryDecision::Stop {
            reason: RetryStopReason::OperationNotIdempotent,
        });
    }
    let delay_ms = match error.retry() {
        RetryClass::Never => {
            return Ok(RetryDecision::Stop {
                reason: RetryStopReason::ErrorNotRetryable,
            });
        }
        RetryClass::ResyncRequired => {
            return Ok(RetryDecision::Stop {
                reason: RetryStopReason::ResyncRequired,
            });
        }
        RetryClass::SafeImmediate => 0,
        RetryClass::SafeBackoff => budget.backoff_ms(context.completed_attempts),
    };
    if context.completed_attempts >= budget.maximum_attempts {
        return Ok(RetryDecision::Stop {
            reason: RetryStopReason::AttemptLimit,
        });
    }
    if context.elapsed_ms.saturating_add(delay_ms) >= budget.total_deadline_ms {
        return Ok(RetryDecision::Stop {
            reason: RetryStopReason::Deadline,
        });
    }
    Ok(RetryDecision::Retry {
        next_attempt: context.completed_attempts + 1,
        delay_ms,
    })
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum PolicyError {
    TlsRequired,
    InvalidSecretHandle,
    InvalidServerName,
    InvalidPoolSize,
    InvalidPoolTimeout,
    InvalidStatementTimeout,
    InvalidRetryBudget,
    RetryDeadlineExceedsTransactionTimeout,
    InvalidCompletedAttempts,
}

impl fmt::Display for PolicyError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::TlsRequired => "TLS verify-full is required outside developer mode",
            Self::InvalidSecretHandle => "TLS secret handle is invalid",
            Self::InvalidServerName => "TLS server name is invalid",
            Self::InvalidPoolSize => "database pool size policy is invalid",
            Self::InvalidPoolTimeout => "database pool timeout policy is invalid",
            Self::InvalidStatementTimeout => {
                "database statement/lock/transaction timeout policy is invalid"
            }
            Self::InvalidRetryBudget => "database retry budget is invalid",
            Self::RetryDeadlineExceedsTransactionTimeout => {
                "retry deadline exceeds transaction timeout"
            }
            Self::InvalidCompletedAttempts => {
                "retry context must include the failed initial attempt"
            }
        })
    }
}

impl std::error::Error for PolicyError {}

#[cfg(test)]
mod tests {
    use super::*;
    use trnm_contracts::StableCode;

    fn error(retry: RetryClass) -> DomainError {
        DomainError::new(StableCode::Aborted, "test", retry)
    }

    fn budget() -> RetryBudget {
        RetryBudget {
            maximum_attempts: 4,
            base_backoff_ms: 10,
            maximum_backoff_ms: 40,
            total_deadline_ms: 100,
        }
    }

    fn context(attempts: u8) -> RetryContext {
        RetryContext {
            completed_attempts: attempts,
            elapsed_ms: 0,
            cancelled: false,
            operation: OperationClass::IdempotentCommand,
        }
    }

    #[test]
    fn production_and_compatibility_require_verify_full_tls() {
        let disabled = TlsMode::DisabledDeveloperOnly;
        assert_eq!(disabled.validate(DeploymentClass::Developer), Ok(()));
        assert_eq!(
            disabled.validate(DeploymentClass::Compatibility),
            Err(PolicyError::TlsRequired)
        );
        assert_eq!(
            disabled.validate(DeploymentClass::Production),
            Err(PolicyError::TlsRequired)
        );
        TlsMode::RequireFull {
            root_ca_handle: "kms://database/ca".to_owned(),
            client_identity_handle: Some("kms://database/client".to_owned()),
            server_name: "database.internal".to_owned(),
        }
        .validate(DeploymentClass::Production)
        .unwrap();
    }

    #[test]
    fn pool_and_deadline_ordering_is_bounded() {
        assert_eq!(
            PoolPolicy {
                minimum_idle: 5,
                maximum_size: 4,
                acquire_timeout_ms: 100,
                idle_timeout_ms: 1000,
                maximum_lifetime_ms: 10_000,
            }
            .validate(),
            Err(PolicyError::InvalidPoolSize)
        );
        assert_eq!(
            StatementPolicy {
                statement_timeout_ms: 1000,
                lock_timeout_ms: 2000,
                transaction_timeout_ms: 3000,
            }
            .validate(),
            Err(PolicyError::InvalidStatementTimeout)
        );
    }

    #[test]
    fn retry_is_bounded_by_error_operation_attempt_and_deadline() {
        assert_eq!(
            decide_retry(budget(), error(RetryClass::SafeImmediate), context(1)).unwrap(),
            RetryDecision::Retry {
                next_attempt: 2,
                delay_ms: 0
            }
        );
        assert_eq!(
            decide_retry(budget(), error(RetryClass::SafeBackoff), context(2)).unwrap(),
            RetryDecision::Retry {
                next_attempt: 3,
                delay_ms: 20
            }
        );
        assert_eq!(
            decide_retry(budget(), error(RetryClass::Never), context(1)).unwrap(),
            RetryDecision::Stop {
                reason: RetryStopReason::ErrorNotRetryable
            }
        );
        assert_eq!(
            decide_retry(budget(), error(RetryClass::ResyncRequired), context(1)).unwrap(),
            RetryDecision::Stop {
                reason: RetryStopReason::ResyncRequired
            }
        );
        assert_eq!(
            decide_retry(budget(), error(RetryClass::SafeBackoff), context(4)).unwrap(),
            RetryDecision::Stop {
                reason: RetryStopReason::AttemptLimit
            }
        );
        let mut deadline = context(3);
        deadline.elapsed_ms = 90;
        assert_eq!(
            decide_retry(budget(), error(RetryClass::SafeBackoff), deadline).unwrap(),
            RetryDecision::Stop {
                reason: RetryStopReason::Deadline
            }
        );
    }

    #[test]
    fn cancellation_and_external_effects_never_retry() {
        let mut cancelled = context(1);
        cancelled.cancelled = true;
        assert_eq!(
            decide_retry(budget(), error(RetryClass::SafeImmediate), cancelled).unwrap(),
            RetryDecision::Stop {
                reason: RetryStopReason::Cancelled
            }
        );
        let mut external = context(1);
        external.operation = OperationClass::NonIdempotentExternalEffect;
        assert_eq!(
            decide_retry(budget(), error(RetryClass::SafeImmediate), external).unwrap(),
            RetryDecision::Stop {
                reason: RetryStopReason::OperationNotIdempotent
            }
        );
    }

    #[test]
    fn profile_identity_is_separate() {
        assert_eq!(DatabaseProfile::PostgreSql.as_str(), "postgresql");
        assert_eq!(DatabaseProfile::CockroachDb.as_str(), "cockroachdb");
        assert_ne!(DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb);
    }
}
