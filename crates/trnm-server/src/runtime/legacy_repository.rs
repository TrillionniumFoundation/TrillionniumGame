//! Concrete local bridge to native account transactions. No HTTP route is admitted
//! by constructing this adapter; the native AccountsV5 gate remains authoritative.
use super::legacy_auth::{
    generate_account_id, LegacyCommittedCleanupFailure, LegacyDeviceAccount,
    LegacyDeviceRepository, LegacyDeviceRepositoryInput, LegacyRepositoryError, LegacyStoredUser,
    LegacyUserRepository,
};
use core::fmt;
use trnm_contracts::UserId;
use trnm_persistence_pg::{
    AccountSqlFailure, AuthenticateDevice, AuthenticateDeviceOutcome, NakamaAccountError,
    NakamaAccountIdGenerationError, NakamaLegacyUser, PgRepository,
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
fn map_stored_user(user: NakamaLegacyUser) -> LegacyStoredUser {
    LegacyStoredUser {
        user_id: user.id,
        username: user.stored_username,
        disable_time_unix_seconds: user.disable_unix_seconds,
    }
}
fn map_native_error(error: NakamaAccountError) -> LegacyRepositoryError {
    match error {
        NakamaAccountError::SchemaNotReady(_) => LegacyRepositoryError::Unavailable,
        NakamaAccountError::NotFound => LegacyRepositoryError::UserNotFound,
        NakamaAccountError::Banned => LegacyRepositoryError::UserBanned,
        NakamaAccountError::UsernameAlreadyExists(_) => LegacyRepositoryError::UsernameAlreadyInUse,
        NakamaAccountError::Internal(_) => LegacyRepositoryError::Internal,
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
