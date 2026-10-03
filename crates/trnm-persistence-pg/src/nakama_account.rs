//! Narrow native account repository for the pinned Nakama Device contract.
//! AccountsV5 admission remains closed; pure transaction tests grant no native credit.
//! Upstream-derived ordering: Nakama d4d92f93 core_authenticate.go/db.go, Apache-2.0.

use crate::DatabaseProfile;
use std::fmt;
use trnm_contracts::{DomainError, StableCode, UserId};

#[path = "nakama_account/native.rs"]
mod native;
#[cfg(test)]
#[path = "nakama_account/tests.rs"]
mod tests;

pub const NAKAMA_DEVICE_MAX_ATTEMPTS: u8 = 5;
const MAX_INPUT_BYTES: usize = 128;
const MAX_STORED_USERNAME_BYTES: usize = 128 * 4;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NakamaLegacyUser {
    pub id: UserId,
    pub stored_username: String,
    /// Go Time.Unix semantics: floor epoch seconds, including before epoch.
    pub disable_unix_seconds: Option<i64>,
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub struct AuthenticateDevice<'a> {
    device_id: &'a str,
    username: &'a str,
    create: bool,
}

impl fmt::Debug for AuthenticateDevice<'_> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AuthenticateDevice")
            .field("device_id", &"<redacted>")
            .field("username", &"<redacted>")
            .field("create", &self.create)
            .finish()
    }
}

impl<'a> AuthenticateDevice<'a> {
    /// Structural allocation bounds only. The authenticated service owns the
    /// complete pinned HTTP predicate, including username generation. Rust str
    /// does not represent the invalid-UTF8 domain of a raw Go string microcase.
    pub fn new(
        device_id: &'a str,
        username: &'a str,
        create: bool,
    ) -> Result<Self, NakamaAccountError> {
        if device_id.len() > MAX_INPUT_BYTES || username.len() > MAX_INPUT_BYTES {
            return Err(internal(
                AccountPhase::Input,
                AccountSqlFailure::invalid_input(),
            ));
        }
        Ok(Self {
            device_id,
            username,
            create,
        })
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AuthenticateDeviceOutcome {
    pub user: NakamaLegacyUser,
    pub created: bool,
    /// RELEASE already committed on CockroachDB. Cleanup failure is diagnostic
    /// and must never cause a second business attempt or compensate that commit.
    pub committed_cleanup_failure: Option<AccountSqlFailure>,
}

/// Closed local error; no raw OS/provider message can become a business error.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum NakamaAccountIdGenerationError {
    Unavailable,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AccountPhase {
    Input,
    DeviceLookup,
    CustomLookup,
    CustomInsert,
    UserLookup,
    Begin,
    Savepoint,
    InsertUser,
    InsertDevice,
    Commit,
    Release,
    Restart,
}

/// Bounded diagnostics; never retain a database message containing request data.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct AccountSqlFailure {
    pub sqlstate: Option<[u8; 5]>,
    pub username_collision: bool,
    pub transaction_closed: bool,
    pub reason: &'static str,
}

impl AccountSqlFailure {
    const fn invalid_input() -> Self {
        Self::local("nakama_device_input_invalid")
    }
    const fn local(reason: &'static str) -> Self {
        Self {
            sqlstate: None,
            username_collision: false,
            transaction_closed: false,
            reason,
        }
    }
    const fn closed() -> Self {
        Self {
            transaction_closed: true,
            ..Self::local("account_transaction_closed")
        }
    }
    fn retryable(self, profile: DatabaseProfile) -> bool {
        self.sqlstate.is_some_and(|code| match profile {
            DatabaseProfile::PostgreSql => code[..2] == *b"40",
            DatabaseProfile::CockroachDb => code == *b"40001" || code == *b"CR000",
        })
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct AccountFailure {
    pub phase: AccountPhase,
    pub last_failure: AccountSqlFailure,
    pub cleanup_failure: Option<AccountSqlFailure>,
    pub attempts: u8,
    pub exhausted: bool,
    pub unknown_commit: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum NakamaAccountError {
    /// Startup/admission failure remains distinct from the upstream business errors.
    SchemaNotReady(DomainError),
    NotFound,
    Banned,
    /// Custom lookup errors retain the upstream finding-vs-creating distinction.
    CustomLookupFailed(AccountFailure),
    UsernameAlreadyExists(AccountFailure),
    Internal(AccountFailure),
}

impl NakamaAccountError {
    #[must_use]
    pub const fn code(self) -> StableCode {
        match self {
            Self::SchemaNotReady(_) => StableCode::Unavailable,
            Self::NotFound => StableCode::NotFound,
            Self::Banned => StableCode::PermissionDenied,
            Self::UsernameAlreadyExists(_) => StableCode::AlreadyExists,
            Self::Internal(_) | Self::CustomLookupFailed(_) => StableCode::Internal,
        }
    }
    #[must_use]
    pub const fn reason(self) -> &'static str {
        match self {
            Self::SchemaNotReady(error) => error.reason(),
            Self::NotFound => "nakama_user_not_found",
            Self::Banned => "nakama_user_banned",
            Self::UsernameAlreadyExists(_) => "nakama_username_already_exists",
            Self::Internal(error) | Self::CustomLookupFailed(error) => error.last_failure.reason,
        }
    }
    fn unknown_commit(self) -> bool {
        matches!(
            self,
            Self::Internal(AccountFailure {
                unknown_commit: true,
                ..
            })
        )
    }
}

impl fmt::Display for NakamaAccountError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}:{}", self.code().as_str(), self.reason())
    }
}
impl std::error::Error for NakamaAccountError {}

fn internal(phase: AccountPhase, last_failure: AccountSqlFailure) -> NakamaAccountError {
    NakamaAccountError::Internal(AccountFailure {
        phase,
        last_failure,
        cleanup_failure: None,
        attempts: 0,
        exhausted: false,
        unknown_commit: false,
    })
}

trait DeviceTransaction {
    fn insert_user(
        &mut self,
        request: AuthenticateDevice<'_>,
        id: UserId,
    ) -> Result<u64, AccountSqlFailure>;
    fn insert_device(&mut self, device_id: &str, id: UserId) -> Result<u64, AccountSqlFailure>;
    fn savepoint(&mut self) -> Result<(), AccountSqlFailure>;
    fn release(&mut self) -> Result<(), AccountSqlFailure>;
    fn restart(&mut self) -> Result<(), AccountSqlFailure>;
    fn commit(&mut self) -> Result<(), AccountSqlFailure>;
    fn rollback(&mut self) -> Result<(), AccountSqlFailure>;
}

trait DeviceDatabase {
    type Transaction<'a>: DeviceTransaction
    where
        Self: 'a;
    fn require_ready(&mut self) -> Result<(), DomainError>;
    fn find_device(&mut self, device_id: &str) -> Result<Option<UserId>, AccountSqlFailure>;
    fn read_user(&mut self, id: UserId) -> Result<Option<NakamaLegacyUser>, AccountSqlFailure>;
    fn begin(&mut self) -> Result<Self::Transaction<'_>, AccountSqlFailure>;
}

fn read_user(
    database: &mut impl DeviceDatabase,
    id: UserId,
) -> Result<Option<NakamaLegacyUser>, NakamaAccountError> {
    database
        .require_ready()
        .map_err(NakamaAccountError::SchemaNotReady)?;
    database
        .read_user(id)
        .map_err(|e| internal(AccountPhase::UserLookup, e))
}

fn authenticate_device(
    database: &mut impl DeviceDatabase,
    profile: DatabaseProfile,
    request: AuthenticateDevice<'_>,
    new_user_id: Option<UserId>,
) -> Result<AuthenticateDeviceOutcome, NakamaAccountError> {
    // Retain the original Option API and its invalid-UUID reason. This closure
    // is never evaluated for unavailable, existing, banned, or !create paths.
    authenticate_device_with_id_source(database, profile, request, || {
        Ok(new_user_id.unwrap_or(UserId::new([0; 16])))
    })
}

fn authenticate_device_with_id_source(
    database: &mut impl DeviceDatabase,
    profile: DatabaseProfile,
    request: AuthenticateDevice<'_>,
    new_user_id: impl FnOnce() -> Result<UserId, NakamaAccountIdGenerationError>,
) -> Result<AuthenticateDeviceOutcome, NakamaAccountError> {
    database
        .require_ready()
        .map_err(NakamaAccountError::SchemaNotReady)?;
    let existing = database
        .find_device(request.device_id)
        .map_err(|e| internal(AccountPhase::DeviceLookup, e))?;
    if let Some(id) = existing {
        let user = database
            .read_user(id)
            .map_err(|e| internal(AccountPhase::UserLookup, e))?
            .ok_or_else(|| {
                internal(
                    AccountPhase::UserLookup,
                    AccountSqlFailure::local("device_user_missing"),
                )
            })?;
        if user
            .disable_unix_seconds
            .is_some_and(|seconds| seconds != 0)
        {
            return Err(NakamaAccountError::Banned);
        }
        return Ok(AuthenticateDeviceOutcome {
            user,
            created: false,
            committed_cleanup_failure: None,
        });
    }
    if !request.create {
        return Err(NakamaAccountError::NotFound);
    }
    // Source UUID creation is deferred until AFTER readiness, the initial
    // device/user lookup, and !create rejection, but before any transaction.
    // The trusted local source is called exactly once; all attempts reuse it.
    let generated = new_user_id().map_err(|_| {
        internal(
            AccountPhase::Input,
            AccountSqlFailure::local("new_device_user_uuid_generation_failed"),
        )
    })?;
    let id = Some(generated)
        .filter(|id| !id.is_zero() && id.as_bytes()[6] >> 4 == 4 && id.as_bytes()[8] >> 6 == 2)
        .ok_or_else(|| {
            internal(
                AccountPhase::Input,
                AccountSqlFailure::local("new_device_user_uuid_invalid"),
            )
        })?;
    let committed_cleanup_failure = match profile {
        DatabaseProfile::PostgreSql => create_postgres(database, request, id)?,
        DatabaseProfile::CockroachDb => create_cockroach(database, request, id)?,
    };
    Ok(AuthenticateDeviceOutcome {
        user: NakamaLegacyUser {
            id,
            stored_username: request.username.to_owned(),
            disable_unix_seconds: Some(0),
        },
        created: true,
        committed_cleanup_failure,
    })
}

fn insert_both(
    transaction: &mut impl DeviceTransaction,
    request: AuthenticateDevice<'_>,
    id: UserId,
) -> Result<(), (AccountPhase, AccountSqlFailure)> {
    let rows = transaction
        .insert_user(request, id)
        .map_err(|e| (AccountPhase::InsertUser, e))?;
    if rows != 1 {
        return Err((
            AccountPhase::InsertUser,
            AccountSqlFailure::local("account_insert_rows_affected"),
        ));
    }
    let rows = transaction
        .insert_device(request.device_id, id)
        .map_err(|e| (AccountPhase::InsertDevice, e))?;
    if rows != 1 {
        return Err((
            AccountPhase::InsertDevice,
            AccountSqlFailure::local("device_insert_rows_affected"),
        ));
    }
    Ok(())
}

fn rollback(transaction: &mut impl DeviceTransaction) -> Option<AccountSqlFailure> {
    transaction
        .rollback()
        .err()
        .filter(|error| !error.transaction_closed)
}

fn transaction_error(failure: AccountFailure) -> NakamaAccountError {
    if failure.phase == AccountPhase::InsertUser && failure.last_failure.username_collision {
        NakamaAccountError::UsernameAlreadyExists(failure)
    } else {
        NakamaAccountError::Internal(failure)
    }
}

fn create_postgres(
    database: &mut impl DeviceDatabase,
    request: AuthenticateDevice<'_>,
    id: UserId,
) -> Result<Option<AccountSqlFailure>, NakamaAccountError> {
    for attempt in 1..=NAKAMA_DEVICE_MAX_ATTEMPTS {
        // Deliberately use database default isolation, as BeginTx(ctx,nil) does.
        let mut transaction = database
            .begin()
            .map_err(|e| internal(AccountPhase::Begin, e))?;
        let operation = insert_both(&mut transaction, request, id);
        let result =
            operation.and_then(|()| transaction.commit().map_err(|e| (AccountPhase::Commit, e)));
        let Err((phase, last_failure)) = result else {
            return Ok(None);
        };
        let unknown_commit = phase == AccountPhase::Commit
            && (last_failure.sqlstate.is_none() || last_failure.sqlstate == Some(*b"40003"));
        let retryable = last_failure.retryable(DatabaseProfile::PostgreSql) && !unknown_commit;
        let cleanup_failure = rollback(&mut transaction);
        let failure = AccountFailure {
            phase,
            last_failure,
            cleanup_failure,
            attempts: attempt,
            exhausted: retryable && attempt == NAKAMA_DEVICE_MAX_ATTEMPTS,
            unknown_commit,
        };
        if !retryable || cleanup_failure.is_some() || failure.exhausted {
            // The primary Go helper overwrites err with successful Rollback,
            // potentially returning nil after five failures. Preserve the last
            // operation failure instead: AGENTS durable-before-ACK takes priority.
            return Err(transaction_error(failure));
        }
    }
    unreachable!("the bounded loop returns on success or its last failure")
}

fn create_cockroach(
    database: &mut impl DeviceDatabase,
    request: AuthenticateDevice<'_>,
    id: UserId,
) -> Result<Option<AccountSqlFailure>, NakamaAccountError> {
    let mut transaction = database
        .begin()
        .map_err(|e| internal(AccountPhase::Begin, e))?;
    if let Err(last_failure) = transaction.savepoint() {
        return Err(transaction_error(AccountFailure {
            phase: AccountPhase::Savepoint,
            last_failure,
            cleanup_failure: rollback(&mut transaction),
            attempts: 0,
            exhausted: false,
            unknown_commit: false,
        }));
    }
    for attempt in 1..=NAKAMA_DEVICE_MAX_ATTEMPTS {
        let operation = insert_both(&mut transaction, request, id);
        let result = operation.and_then(|()| {
            transaction
                .release()
                .map_err(|e| (AccountPhase::Release, e))
        });
        let Err((phase, last_failure)) = result else {
            // RELEASE successfully commits the retry-savepoint transaction.
            // The final protocol COMMIT is cleanup, never a second business try.
            return Ok(transaction.commit().err());
        };
        let retryable = last_failure.retryable(DatabaseProfile::CockroachDb);
        if !retryable {
            return Err(transaction_error(AccountFailure {
                phase,
                last_failure,
                cleanup_failure: rollback(&mut transaction),
                attempts: attempt,
                exhausted: false,
                unknown_commit: phase == AccountPhase::Release,
            }));
        }
        if let Err(restart_failure) = transaction.restart() {
            let _ = rollback(&mut transaction);
            return Err(transaction_error(AccountFailure {
                phase,
                last_failure,
                cleanup_failure: Some(restart_failure),
                attempts: attempt,
                exhausted: false,
                unknown_commit: false,
            }));
        }
        if attempt == NAKAMA_DEVICE_MAX_ATTEMPTS {
            return Err(transaction_error(AccountFailure {
                phase,
                last_failure,
                cleanup_failure: rollback(&mut transaction),
                attempts: attempt,
                exhausted: true,
                unknown_commit: false,
            }));
        }
    }
    unreachable!("the bounded loop retains the last failure")
}

fn checked_stored_user(
    id: UserId,
    stored_username: String,
    disable_unix_seconds: Option<i64>,
) -> Result<NakamaLegacyUser, AccountSqlFailure> {
    // Native VARCHAR(128) is 128 characters, not the API's byte predicate.
    if stored_username.len() > MAX_STORED_USERNAME_BYTES || stored_username.chars().count() > 128 {
        return Err(AccountSqlFailure::local("stored_username_domain_invalid"));
    }
    Ok(NakamaLegacyUser {
        id,
        stored_username,
        disable_unix_seconds,
    })
}

/// Only decode the canonical lowercase representation emitted by UUID::TEXT.
/// HTTP/token UUID parsing remains in the existing service parser.
fn decode_database_uuid(value: &str) -> Result<UserId, AccountSqlFailure> {
    if value.len() != 36 {
        return Err(AccountSqlFailure::local("native_user_uuid_invalid"));
    }
    let mut bytes = [0; 16];
    let mut cursor = 0;
    for (index, byte) in value.bytes().enumerate() {
        if matches!(index, 8 | 13 | 18 | 23) {
            if byte != b'-' {
                return Err(AccountSqlFailure::local("native_user_uuid_invalid"));
            }
            continue;
        }
        let nibble = match byte {
            b'0'..=b'9' => byte - b'0',
            b'a'..=b'f' => byte - b'a' + 10,
            _ => return Err(AccountSqlFailure::local("native_user_uuid_invalid")),
        };
        bytes[cursor / 2] |= nibble << (if cursor % 2 == 0 { 4 } else { 0 });
        cursor += 1;
    }
    Ok(UserId::new(bytes))
}

fn database_uuid(id: UserId) -> String {
    let mut value = String::with_capacity(36);
    const HEX: &[u8; 16] = b"0123456789abcdef";
    for (index, byte) in id.as_bytes().iter().enumerate() {
        if matches!(index, 4 | 6 | 8 | 10) {
            value.push('-');
        }
        value.push(char::from(HEX[usize::from(byte >> 4)]));
        value.push(char::from(HEX[usize::from(byte & 15)]));
    }
    value
}

/// Source Custom is a single autocommit users INSERT, never Device's TX loop.
#[derive(Clone, Copy, Eq, PartialEq)]
pub struct AuthenticateCustom<'a> {
    custom_id: &'a str,
    username: &'a str,
    create: bool,
}
impl fmt::Debug for AuthenticateCustom<'_> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("AuthenticateCustom")
            .field("custom_id", &"<redacted>")
            .field("username", &"<redacted>")
            .field("create", &self.create)
            .finish()
    }
}
impl<'a> AuthenticateCustom<'a> {
    /// Structural bounds only; source API predicates are owned by the service.
    pub fn new(
        custom_id: &'a str,
        username: &'a str,
        create: bool,
    ) -> Result<Self, NakamaAccountError> {
        if custom_id.len() > MAX_INPUT_BYTES || username.len() > MAX_INPUT_BYTES {
            return Err(internal(
                AccountPhase::Input,
                AccountSqlFailure::local("nakama_custom_input_invalid"),
            ));
        }
        Ok(Self {
            custom_id,
            username,
            create,
        })
    }
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AuthenticateCustomOutcome {
    pub user: NakamaLegacyUser,
    pub created: bool,
}
trait CustomDatabase {
    fn require_custom_ready(&mut self) -> Result<(), DomainError>;
    fn find_custom(
        &mut self,
        custom_id: &str,
    ) -> Result<Option<NakamaLegacyUser>, AccountSqlFailure>;
    /// An observed retired/cancelled lease cannot start a new write.
    fn check_custom_write_allowed(&mut self) -> Result<(), AccountSqlFailure>;
    /// Success is only an observed completed autocommit, never a pending write.
    fn insert_custom(
        &mut self,
        request: AuthenticateCustom<'_>,
        id: UserId,
    ) -> Result<u64, AccountSqlFailure>;
}
fn custom_failure(
    phase: AccountPhase,
    last_failure: AccountSqlFailure,
    attempted: bool,
    unknown_commit: bool,
) -> AccountFailure {
    AccountFailure {
        phase,
        last_failure,
        cleanup_failure: None,
        attempts: u8::from(attempted),
        exhausted: false,
        unknown_commit,
    }
}
fn authenticate_custom_with_id_source(
    database: &mut impl CustomDatabase,
    request: AuthenticateCustom<'_>,
    new_user_id: impl FnOnce() -> Result<UserId, NakamaAccountIdGenerationError>,
) -> Result<AuthenticateCustomOutcome, NakamaAccountError> {
    database
        .require_custom_ready()
        .map_err(NakamaAccountError::SchemaNotReady)?;
    let existing = database.find_custom(request.custom_id).map_err(|error| {
        NakamaAccountError::CustomLookupFailed(custom_failure(
            AccountPhase::CustomLookup,
            error,
            false,
            false,
        ))
    })?;
    if let Some(user) = existing {
        if user
            .disable_unix_seconds
            .is_some_and(|seconds| seconds != 0)
        {
            return Err(NakamaAccountError::Banned);
        }
        return Ok(AuthenticateCustomOutcome {
            user,
            created: false,
        });
    }
    if !request.create {
        return Err(NakamaAccountError::NotFound);
    }
    let id = new_user_id().map_err(|_| {
        internal(
            AccountPhase::Input,
            AccountSqlFailure::local("new_custom_user_uuid_generation_failed"),
        )
    })?;
    if id.is_zero() || id.as_bytes()[6] >> 4 != 4 || id.as_bytes()[8] >> 6 != 2 {
        return Err(internal(
            AccountPhase::Input,
            AccountSqlFailure::local("new_custom_user_uuid_invalid"),
        ));
    }
    database
        .check_custom_write_allowed()
        .map_err(|error| internal(AccountPhase::Input, error))?;
    let count = database.insert_custom(request, id).map_err(|error| {
        // An unobserved autocommit completion is never replayed. SQLSTATE and
        // the exact source username-message classification remain separate.
        let unknown = error.sqlstate.is_none()
            || error.sqlstate == Some(*b"40003")
            || error.sqlstate.is_some_and(|code| code[..2] == *b"08");
        let failure = custom_failure(AccountPhase::CustomInsert, error, true, unknown);
        if error.sqlstate == Some(*b"23505") && error.username_collision {
            NakamaAccountError::UsernameAlreadyExists(failure)
        } else {
            NakamaAccountError::Internal(failure)
        }
    })?;
    if count != 1 {
        return Err(NakamaAccountError::Internal(custom_failure(
            AccountPhase::CustomInsert,
            AccountSqlFailure::local("custom_insert_rows_affected_unexpected"),
            true,
            count > 0,
        )));
    }
    Ok(AuthenticateCustomOutcome {
        user: NakamaLegacyUser {
            id,
            stored_username: request.username.to_owned(),
            disable_unix_seconds: Some(0),
        },
        created: true,
    })
}
