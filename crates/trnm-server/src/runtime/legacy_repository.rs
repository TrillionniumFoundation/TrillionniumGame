//! Concrete local bridge to native account transactions. No HTTP route is admitted
//! by constructing this adapter; the native AccountsV5 gate remains authoritative.
use super::legacy_auth::{
    generate_account_id, LegacyCommittedCleanupFailure, LegacyCustomAccount,
    LegacyCustomRepository, LegacyCustomRepositoryInput, LegacyDeviceAccount,
    LegacyDeviceRepository, LegacyDeviceRepositoryInput, LegacyLeaseCancellation,
    LegacyLeaseCompletion, LegacyLeaseFailure, LegacyNativeAccountFailure, LegacyRepositoryError,
    LegacyStoredUser, LegacyUserRepository,
};
use core::fmt;
use trnm_contracts::UserId;
use trnm_persistence_pg::{
    AccountPhase, AccountSqlFailure, AuthenticateCustom, AuthenticateCustomOutcome,
    AuthenticateDevice, AuthenticateDeviceOutcome, NakamaAccountError,
    NakamaAccountIdGenerationError, NakamaLegacyUser, PgAccountLeaseCancellation,
    PgAccountLeaseOutcome, PgRepository,
};

/// Borrow one actual native repository/lease. The adapter has no transaction retry
/// loop, provider/clock injection, principal factory, or second lookup fallback.
pub struct PgLegacyAuthRepository<'a> {
    repository: &'a mut PgRepository,
    last_failure: Option<NakamaAccountError>,
}
impl fmt::Debug for PgLegacyAuthRepository<'_> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("PgLegacyAuthRepository")
            .field("repository", &"<native-lease>")
            .field("last_failure", &self.last_failure)
            .finish()
    }
}
impl<'a> PgLegacyAuthRepository<'a> {
    #[must_use]
    pub fn new(repository: &'a mut PgRepository) -> Self {
        Self {
            repository,
            last_failure: None,
        }
    }
    /// Bounded native phase/SQLSTATE/readiness diagnostics, not a wire error body.
    #[must_use]
    pub const fn last_failure(&self) -> Option<NakamaAccountError> {
        self.last_failure
    }
    fn capture<T>(
        &mut self,
        result: Result<T, NakamaAccountError>,
    ) -> Result<T, LegacyRepositoryError> {
        self.last_failure = None;
        result.map_err(|error| {
            self.last_failure = Some(error);
            map_native_error(error)
        })
    }
}
impl LegacyUserRepository for PgLegacyAuthRepository<'_> {
    fn read_legacy_user(
        &mut self,
        user: UserId,
    ) -> Result<Option<LegacyStoredUser>, LegacyRepositoryError> {
        let result = self.repository.read_nakama_user(user);
        self.capture(result).map(|user| user.map(map_stored_user))
    }
}
impl LegacyDeviceRepository for PgLegacyAuthRepository<'_> {
    fn authenticate_legacy_device(
        &mut self,
        input: LegacyDeviceRepositoryInput<'_>,
    ) -> Result<LegacyDeviceAccount, LegacyRepositoryError> {
        // Complete Device regex/byte checks already ran in the owned service.
        let request = match AuthenticateDevice::new(
            input.device_id,
            input.requested_username,
            input.create,
        ) {
            Ok(request) => request,
            Err(error) => return self.capture(Err(error)),
        };
        // Native core calls this trusted OS source only at missing/create, before
        // TX, exactly once; its existing/banned/!create/guard branches skip it.
        let result = self
            .repository
            .authenticate_nakama_device_with_id_source(request, || {
                generate_account_id().map_err(|_| NakamaAccountIdGenerationError::Unavailable)
            });
        self.capture(result).map(map_device_outcome)
    }
}
/// Bounded native diagnostic observation on the same request's actual adapter.
/// It is not a second lookup, retry or a claim that an unobserved call had no effect.
#[cfg_attr(
    not(test),
    allow(dead_code, reason = "Diagnostic observation is exercised by the test-only native seam")
)]
pub trait LegacyNativeFailureObservation {
    fn last_legacy_native_failure(&self) -> Option<NakamaAccountError>;
}
impl LegacyNativeFailureObservation for PgLegacyAuthRepository<'_> {
    fn last_legacy_native_failure(&self) -> Option<NakamaAccountError> {
        self.last_failure
    }
}
fn phase_name(phase: AccountPhase) -> &'static str {
    match phase {
        AccountPhase::Input => "input",
        AccountPhase::DeviceLookup => "device_lookup",
        AccountPhase::CustomLookup => "custom_lookup",
        AccountPhase::CustomInsert => "custom_insert",
        AccountPhase::UserLookup => "user_lookup",
        AccountPhase::Begin => "begin",
        AccountPhase::Savepoint => "savepoint",
        AccountPhase::InsertUser => "insert_user",
        AccountPhase::InsertDevice => "insert_device",
        AccountPhase::Commit => "commit",
        AccountPhase::Release => "release",
        AccountPhase::Restart => "restart",
    }
}
fn map_native_failure(error: NakamaAccountError) -> LegacyNativeAccountFailure {
    let failure = match error {
        NakamaAccountError::Internal(failure)
        | NakamaAccountError::CustomLookupFailed(failure)
        | NakamaAccountError::UsernameAlreadyExists(failure) => Some(failure),
        _ => None,
    };
    LegacyNativeAccountFailure {
        code: error.code(),
        phase: failure.map(|f| phase_name(f.phase)),
        last_failure: failure.map(|f| map_cleanup(f.last_failure)),
        cleanup_failure: failure.and_then(|f| f.cleanup_failure.map(map_cleanup)),
        attempts: failure.map(|f| f.attempts),
        exhausted: failure.map(|f| f.exhausted),
        unknown_commit: failure.map(|f| f.unknown_commit),
    }
}
fn map_cancellation(c: PgAccountLeaseCancellation) -> LegacyLeaseCancellation {
    match c {
        PgAccountLeaseCancellation::None => LegacyLeaseCancellation::None,
        PgAccountLeaseCancellation::Deadline => LegacyLeaseCancellation::Deadline,
        PgAccountLeaseCancellation::Shutdown => LegacyLeaseCancellation::Shutdown,
    }
}
fn resolve_lease<T, U>(
    lease: PgAccountLeaseOutcome<T>,
    success_observation: impl FnOnce(&T) -> LegacyLeaseCompletion,
    map: impl FnOnce(T) -> U,
) -> Result<U, LegacyRepositoryError> {
    if let Some(boundary_error) = lease.boundary_error {
        let completion = match lease.observed.as_ref() {
            None => LegacyLeaseCompletion::NotObserved,
            Some(Ok(value)) => success_observation(value),
            Some(Err(error)) => LegacyLeaseCompletion::NativeFailure(map_native_failure(*error)),
        };
        // Retain outer and inner facts independently; never turn a late commit
        // into a success or replay this native call after cancellation.
        return Err(LegacyRepositoryError::Lease(Box::new(LegacyLeaseFailure {
            boundary_error,
            setup_error: lease.setup_error,
            completion,
            cancellation: map_cancellation(lease.cancellation),
            lease_retired: lease.lease_retired,
        })));
    }
    match lease.observed {
        Some(Ok(value)) => Ok(map(value)),
        Some(Err(error)) => Err(map_native_error(error)),
        None => Err(LegacyRepositoryError::Lease(Box::new(LegacyLeaseFailure {
            boundary_error: trnm_contracts::DomainError::new(
                trnm_contracts::StableCode::DataLoss,
                "account_pool_result_missing",
                trnm_contracts::RetryClass::Never,
            ),
            setup_error: lease.setup_error,
            completion: LegacyLeaseCompletion::NotObserved,
            cancellation: map_cancellation(lease.cancellation),
            lease_retired: lease.lease_retired,
        }))),
    }
}
pub(super) fn resolve_user_lease(
    lease: PgAccountLeaseOutcome<Option<NakamaLegacyUser>>,
) -> Result<Option<LegacyStoredUser>, LegacyRepositoryError> {
    resolve_lease(
        lease,
        |_| LegacyLeaseCompletion::UserRead,
        |v| v.map(map_stored_user),
    )
}
pub(super) fn resolve_device_lease(
    lease: PgAccountLeaseOutcome<AuthenticateDeviceOutcome>,
) -> Result<LegacyDeviceAccount, LegacyRepositoryError> {
    resolve_lease(
        lease,
        |v| {
            if v.created {
                LegacyLeaseCompletion::ConfirmedCreation {
                    committed_cleanup_failure: v.committed_cleanup_failure.map(map_cleanup),
                }
            } else if let Some(failure) = v.committed_cleanup_failure {
                // Preserve a contradictory cleanup report without inventing a
                // confirmed creation; the service returns UnconfirmedCleanup.
                LegacyLeaseCompletion::UnconfirmedCleanup {
                    committed_cleanup_failure: map_cleanup(failure),
                }
            } else {
                LegacyLeaseCompletion::DeviceExisting
            }
        },
        map_device_outcome,
    )
}

fn map_stored_user(user: NakamaLegacyUser) -> LegacyStoredUser {
    LegacyStoredUser {
        user_id: user.id,
        username: user.stored_username,
        disable_time_unix_seconds: user.disable_unix_seconds,
    }
}
pub(super) fn map_native_error(error: NakamaAccountError) -> LegacyRepositoryError {
    match error {
        NakamaAccountError::SchemaNotReady(_) => LegacyRepositoryError::Unavailable,
        NakamaAccountError::NotFound => LegacyRepositoryError::UserNotFound,
        NakamaAccountError::CustomLookupFailed(failure) => {
            LegacyRepositoryError::CustomLookupFailed(map_native_failure(
                NakamaAccountError::CustomLookupFailed(failure),
            ))
        }
        NakamaAccountError::Banned => LegacyRepositoryError::UserBanned,
        NakamaAccountError::UsernameAlreadyExists(_) => LegacyRepositoryError::UsernameAlreadyInUse,
        NakamaAccountError::Internal(failure) => LegacyRepositoryError::NativeFailure(
            map_native_failure(NakamaAccountError::Internal(failure)),
        ),
    }
}
fn map_cleanup(failure: AccountSqlFailure) -> LegacyCommittedCleanupFailure {
    LegacyCommittedCleanupFailure {
        sqlstate: failure.sqlstate,
        username_collision: failure.username_collision,
        transaction_closed: failure.transaction_closed,
        reason: failure.reason,
    }
}
fn map_device_outcome(outcome: AuthenticateDeviceOutcome) -> LegacyDeviceAccount {
    LegacyDeviceAccount {
        user_id: outcome.user.id,
        stored_username: outcome.user.stored_username,
        created: outcome.created,
        committed_cleanup_failure: outcome.committed_cleanup_failure.map(map_cleanup),
    }
}

#[cfg(test)]
#[path = "legacy_repository_tests.rs"]
mod tests;

impl LegacyCustomRepository for PgLegacyAuthRepository<'_> {
    fn authenticate_legacy_custom(
        &mut self,
        input: LegacyCustomRepositoryInput<'_>,
    ) -> Result<LegacyCustomAccount, LegacyRepositoryError> {
        let request = match AuthenticateCustom::new(
            input.custom_id,
            input.requested_username,
            input.create,
        ) {
            Ok(r) => r,
            Err(e) => return self.capture(Err(e)),
        };
        let result = self
            .repository
            .authenticate_nakama_custom_with_id_source(request, || {
                generate_account_id().map_err(|_| NakamaAccountIdGenerationError::Unavailable)
            });
        self.capture(result).map(map_custom_outcome)
    }
}
pub(super) fn resolve_custom_lease(
    lease: PgAccountLeaseOutcome<AuthenticateCustomOutcome>,
) -> Result<LegacyCustomAccount, LegacyRepositoryError> {
    resolve_lease(
        lease,
        |v| {
            if v.created {
                LegacyLeaseCompletion::ConfirmedCreation {
                    committed_cleanup_failure: None,
                }
            } else {
                LegacyLeaseCompletion::CustomExisting
            }
        },
        map_custom_outcome,
    )
}
fn map_custom_outcome(outcome: AuthenticateCustomOutcome) -> LegacyCustomAccount {
    LegacyCustomAccount {
        user_id: outcome.user.id,
        stored_username: outcome.user.stored_username,
        created: outcome.created,
    }
}
