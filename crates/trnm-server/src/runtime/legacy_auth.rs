//! Server-owned Nakama legacy authentication, separate from durable refresh families.
//! Source: heroiclabs/nakama d4d92f93 api.go, core_session.go, api_session.go,
//! api_authenticate.go and session_cache.go. Transport/hooks/native user adapters
//! are explicit remaining integration boundaries; no database ACK is invented.

use core::fmt;
use std::collections::BTreeMap;
use std::io::Read;
use std::sync::{Arc, Condvar, Mutex, MutexGuard, Weak};
use std::thread::{self, JoinHandle};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use trnm_contracts::UserId;
use trnm_session_core::{
    NakamaLegacyBlacklist, NakamaLegacyBlacklistError, NakamaLegacyBlacklistLimits,
    NakamaLegacyBlacklistStats, NakamaLegacyBlacklistSweep, NakamaLegacyCacheTokenId,
    NakamaLegacyCacheUserId, NakamaLegacyTokenRemoval,
};
use trnm_token_crypto_provider::{NakamaLegacyHs256Provider, NakamaLegacyKeyLimits};
use trnm_token_jwt_adapter::{
    NakamaLegacyClaims, NakamaLegacyIssueError, NakamaLegacyIssueLimits, NakamaLegacyIssuer,
    NakamaLegacyKeyKind, NakamaLegacyTokenPair, NakamaLegacyVerifiedClaims, NakamaLegacyVerifier,
    NakamaLegacyVerifyLimits,
};

use super::legacy_device_predicates::{validate_custom_input, validate_device_input};
pub use super::legacy_device_predicates::{
    CustomInputError, DeviceInputError, GeneratedUsername, InvalidGeneratedUsername,
    RawStoredDeviceRecord, ResolvedUsername, ValidatedCustomInput, ValidatedDeviceInput,
};
use super::legacy_uuid::{parse_uuid, uuid_string};

#[derive(Clone, Copy, Debug)]
pub struct LegacyAuthPolicyConfig {
    pub session_ttl_seconds: i64,
    pub refresh_ttl_seconds: i64,
    pub single_session: bool,
    pub issuer_limits: NakamaLegacyIssueLimits,
    pub verifier_limits: NakamaLegacyVerifyLimits,
    pub blacklist_limits: NakamaLegacyBlacklistLimits,
}

#[derive(Clone, Copy, Debug)]
pub struct LegacyAuthPolicy(LegacyAuthPolicyConfig);
impl LegacyAuthPolicy {
    pub fn new(config: LegacyAuthPolicyConfig) -> Result<Self, LegacyAuthError> {
        // Explicit local rejection of exotic Go Duration wrapping. No fake time
        // equivalence outside ordinary positive configured TTLs is claimed.
        if config.session_ttl_seconds <= 0
            || config.refresh_ttl_seconds <= 0
            || config
                .session_ttl_seconds
                .checked_mul(2)
                .and_then(|n| n.checked_mul(1_000_000_000))
                .is_none()
            || config
                .refresh_ttl_seconds
                .checked_mul(1_000_000_000)
                .is_none()
        {
            return Err(LegacyAuthError::InvalidConfiguration);
        }
        Ok(Self(config))
    }
}

/// Construction owns validated actual key bytes; it accepts no provider/clock trait.
/// Public defaults are valid only when deliberately supplied by the operator.
pub struct LegacyAuthConfig {
    provider: NakamaLegacyHs256Provider,
    policy: LegacyAuthPolicy,
}
impl LegacyAuthConfig {
    pub fn new(
        access_key: impl Into<Vec<u8>>,
        refresh_key: impl Into<Vec<u8>>,
        key_limits: NakamaLegacyKeyLimits,
        policy: LegacyAuthPolicy,
    ) -> Result<Self, LegacyAuthError> {
        let provider = NakamaLegacyHs256Provider::new(access_key, refresh_key, key_limits)
            .map_err(|_| LegacyAuthError::InvalidConfiguration)?;
        Ok(Self { provider, policy })
    }
}
impl fmt::Debug for LegacyAuthConfig {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("LegacyAuthConfig")
            .field("provider", &"<owned-fixed-keys>")
            .field("policy", &self.policy)
            .finish()
    }
}

/// Bounded native failure, never a raw SQL/provider error or stored identity.
#[derive(Clone, Copy, Eq, PartialEq)]
pub struct LegacyNativeAccountFailure {
    pub code: trnm_contracts::StableCode,
    pub phase: Option<&'static str>,
    pub last_failure: Option<LegacyCommittedCleanupFailure>,
    pub cleanup_failure: Option<LegacyCommittedCleanupFailure>,
    pub attempts: Option<u8>,
    pub exhausted: Option<bool>,
    pub unknown_commit: Option<bool>,
}
impl fmt::Debug for LegacyNativeAccountFailure {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("LegacyNativeAccountFailure")
            .field("code", &self.code)
            .field("unknown_commit", &self.unknown_commit)
            .field("cleanup_failure_present", &self.cleanup_failure.is_some())
            .finish()
    }
}
#[derive(Clone, Copy, Eq, PartialEq)]
pub enum LegacyLeaseCompletion {
    NotObserved,
    UserRead,
    DeviceExisting,
    CustomExisting,
    UnconfirmedCleanup {
        committed_cleanup_failure: LegacyCommittedCleanupFailure,
    },
    ConfirmedCreation {
        committed_cleanup_failure: Option<LegacyCommittedCleanupFailure>,
    },
    NativeFailure(LegacyNativeAccountFailure),
}
impl fmt::Debug for LegacyLeaseCompletion {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::NotObserved => "NotObserved",
            Self::UserRead => "UserRead",
            Self::DeviceExisting => "DeviceExisting",
            Self::CustomExisting => "CustomExisting",
            Self::UnconfirmedCleanup { .. } => "UnconfirmedCleanup",
            Self::ConfirmedCreation { .. } => "ConfirmedCreation",
            Self::NativeFailure(_) => "NativeFailure",
        })
    }
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LegacyLeaseCancellation {
    None,
    Deadline,
    Shutdown,
}
/// The outer pool boundary cannot erase a real native commit or unknown result.
#[derive(Clone, Copy, Eq, PartialEq)]
pub struct LegacyLeaseFailure {
    pub boundary_error: trnm_contracts::DomainError,
    pub setup_error: Option<trnm_contracts::DomainError>,
    pub completion: LegacyLeaseCompletion,
    pub cancellation: LegacyLeaseCancellation,
    pub lease_retired: bool,
}
impl fmt::Debug for LegacyLeaseFailure {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("LegacyLeaseFailure")
            .field("code", &self.boundary_error.code())
            .field("completion", &self.completion)
            .field("cancellation", &self.cancellation)
            .field("lease_retired", &self.lease_retired)
            .finish()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum LegacyRepositoryError {
    Unimplemented,
    Unavailable,
    Internal,
    DataLoss,
    ResourceExhausted,
    UserNotFound,
    UserBanned,
    UsernameAlreadyInUse,
    NativeFailure(LegacyNativeAccountFailure),
    CustomLookupFailed(LegacyNativeAccountFailure),
    Lease(Box<LegacyLeaseFailure>),
}

impl LegacyRepositoryError {
    #[must_use]
    pub fn code(&self) -> trnm_contracts::StableCode {
        use trnm_contracts::StableCode;
        match self {
            Self::Unimplemented => StableCode::Unimplemented,
            Self::Unavailable => StableCode::Unavailable,
            Self::Internal => StableCode::Internal,
            Self::DataLoss => StableCode::DataLoss,
            Self::ResourceExhausted => StableCode::ResourceExhausted,
            Self::UserNotFound => StableCode::NotFound,
            Self::UserBanned => StableCode::PermissionDenied,
            Self::UsernameAlreadyInUse => StableCode::AlreadyExists,
            Self::NativeFailure(error) | Self::CustomLookupFailed(error) => error.code,
            Self::Lease(error) => error.boundary_error.code(),
        }
    }
}

/// A committed Cockroach RELEASE can have failed protocol COMMIT cleanup.
/// Preserve the bounded diagnostic, do not retry or turn it into a failed ACK.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct LegacyCommittedCleanupFailure {
    pub sqlstate: Option<[u8; 5]>,
    pub username_collision: bool,
    pub transaction_closed: bool,
    pub reason: &'static str,
}

/// Stored values are not revalidated with current input-byte/username predicates.
/// Native adapters must read the durable user row and its actual disable timestamp.
pub struct LegacyStoredUser {
    pub user_id: UserId,
    pub username: String,
    pub disable_time_unix_seconds: Option<i64>,
}
impl fmt::Debug for LegacyStoredUser {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("LegacyStoredUser")
            .field("username_bytes", &self.username.len())
            .field(
                "disable_time_present",
                &self.disable_time_unix_seconds.is_some(),
            )
            .finish()
    }
}
pub trait LegacyUserRepository {
    fn read_legacy_user(
        &mut self,
        user: UserId,
    ) -> Result<Option<LegacyStoredUser>, LegacyRepositoryError>;
}

/// This effect boundary MUST return only after its actual account transaction commits.
/// PgLegacyAuthRepository is the concrete local adapter. Its AccountsV5 guard
/// remains closed; no service token response bypasses native readiness.
pub trait LegacyDeviceRepository {
    fn authenticate_legacy_device(
        &mut self,
        input: LegacyDeviceRepositoryInput<'_>,
    ) -> Result<LegacyDeviceAccount, LegacyRepositoryError>;
}
#[derive(Clone, Copy)]
pub struct LegacyDeviceRepositoryInput<'a> {
    pub device_id: &'a str,
    pub requested_username: &'a str,
    pub create: bool,
}
impl fmt::Debug for LegacyDeviceRepositoryInput<'_> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("LegacyDeviceRepositoryInput")
            .field("device_id_bytes", &self.device_id.len())
            .field("requested_username_bytes", &self.requested_username.len())
            .field("create", &self.create)
            .finish()
    }
}
pub struct LegacyDeviceAccount {
    pub user_id: UserId,
    pub stored_username: String,
    pub created: bool,
    pub committed_cleanup_failure: Option<LegacyCommittedCleanupFailure>,
}
impl fmt::Debug for LegacyDeviceAccount {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("LegacyDeviceAccount")
            .field("user_id", &"<redacted>")
            .field("username_bytes", &self.stored_username.len())
            .field("created", &self.created)
            .finish()
    }
}
#[derive(Clone, Copy)]
pub struct LegacyDeviceAuthInput<'a> {
    pub account_id: Option<&'a str>,
    pub username: &'a str,
    pub create: Option<bool>,
    pub variables: Option<&'a BTreeMap<String, String>>,
}
impl fmt::Debug for LegacyDeviceAuthInput<'_> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("LegacyDeviceAuthInput")
            .field("account_id_bytes", &self.account_id.map(str::len))
            .field("username_bytes", &self.username.len())
            .field("create", &self.create)
            .field("variables", &self.variables.map(BTreeMap::len))
            .finish()
    }
}

#[derive(Debug)]
pub enum LegacyAuthError {
    InvalidConfiguration,
    ClockUnavailable,
    ClockRange,
    RandomUnavailable,
    CachePoisoned,
    Cache(NakamaLegacyBlacklistError),
    Issue(NakamaLegacyIssueError),
    AuthTokenInvalid,
    RefreshTokenRequired,
    RefreshTokenInvalidOrExpired,
    SessionLogoutTokenInvalid,
    RefreshLogoutTokenInvalid,
    PrincipalServiceMismatch,
    UserAccountNotFound,
    UserAccountBanned,
    UsernameAlreadyInUse,
    Repository(LegacyRepositoryError),
    DeviceRepository(LegacyRepositoryError),
    DeviceInput(DeviceInputError),
    CustomInput(CustomInputError),
    CustomRepository(LegacyRepositoryError),
    CustomLookupRepository(LegacyRepositoryError),
}
impl fmt::Display for LegacyAuthError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::AuthTokenInvalid => "Auth token invalid",
            Self::RefreshTokenRequired => "Refresh token is required.",
            Self::RefreshTokenInvalidOrExpired => "Refresh token invalid or expired.",
            Self::SessionLogoutTokenInvalid => "Session token invalid.",
            Self::RefreshLogoutTokenInvalid => "Refresh token invalid.",
            Self::UserAccountNotFound => "User account not found.",
            Self::UserAccountBanned => "User account banned.",
            Self::UsernameAlreadyInUse => "Username is already in use.",
            Self::DeviceRepository(_) => "Error finding or creating user account.",
            Self::Repository(_) => "Error finding user account.",
            Self::DeviceInput(e) => e.message(),
            Self::CustomInput(e) => e.message(),
            Self::CustomRepository(_) => "Error finding or creating user account.",
            Self::CustomLookupRepository(_) => "Error finding user account.",
            Self::InvalidConfiguration => "Invalid legacy authentication configuration.",
            Self::ClockUnavailable | Self::ClockRange => "Legacy authentication clock unavailable.",
            Self::RandomUnavailable => "Legacy authentication randomness unavailable.",
            Self::CachePoisoned | Self::Cache(_) => "Legacy authentication cache unavailable.",
            Self::Issue(_) => "Error issuing session tokens.",
            Self::PrincipalServiceMismatch => "Legacy authentication context invalid.",
        })
    }
}
impl std::error::Error for LegacyAuthError {}

/// Only a successful, newly created account outcome can construct this failure.
/// The account is durable even though no credential pair could be returned.
/// This carries bounded typed diagnostics for its local caller, never wire data.
pub struct LegacyDevicePostCommitFailure {
    cause: LegacyAuthError,
    committed_cleanup_failure: Option<LegacyCommittedCleanupFailure>,
}
impl LegacyDevicePostCommitFailure {
    #[must_use]
    pub const fn cause(&self) -> &LegacyAuthError {
        &self.cause
    }
    #[must_use]
    pub const fn committed_cleanup_failure(&self) -> Option<LegacyCommittedCleanupFailure> {
        self.committed_cleanup_failure
    }
    #[must_use]
    pub fn code(&self) -> trnm_contracts::StableCode {
        device_failure_code(&self.cause)
    }
    #[must_use]
    pub const fn creation_commit_confirmed(&self) -> bool {
        true
    }
    #[must_use]
    pub const fn retry_permitted(&self) -> bool {
        false
    }
    #[must_use]
    pub const fn compensation_permitted(&self) -> bool {
        false
    }
}
impl fmt::Debug for LegacyDevicePostCommitFailure {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("LegacyDevicePostCommitFailure")
            .field("cause", &self.cause)
            .field("code", &self.code())
            .field("account_committed", &true)
            .field(
                "committed_cleanup_failure_present",
                &self.committed_cleanup_failure.is_some(),
            )
            .finish()
    }
}
impl fmt::Display for LegacyDevicePostCommitFailure {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("Error issuing session tokens after account creation committed.")
    }
}
impl std::error::Error for LegacyDevicePostCommitFailure {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        Some(&self.cause)
    }
}

/// Separate wrapper avoids a recursive LegacyAuthError/heap allocation and
/// distinguishes existing-user reads from this request's durable creation.
pub enum LegacyDeviceAuthError {
    Unconfirmed(LegacyAuthError),
    CommittedCreation(LegacyDevicePostCommitFailure),
    /// A local repository violated its created/cleanup outcome contract. Keep
    /// its bounded diagnostic, but do not manufacture a confirmed creation.
    UnconfirmedCleanup {
        cause: LegacyAuthError,
        committed_cleanup_failure: LegacyCommittedCleanupFailure,
    },
}
impl LegacyDeviceAuthError {
    #[must_use]
    pub fn code(&self) -> trnm_contracts::StableCode {
        match self {
            Self::Unconfirmed(cause) | Self::UnconfirmedCleanup { cause, .. } => {
                device_failure_code(cause)
            }
            Self::CommittedCreation(failure) => failure.code(),
        }
    }
    #[must_use]
    pub const fn cause(&self) -> &LegacyAuthError {
        match self {
            Self::Unconfirmed(cause) | Self::UnconfirmedCleanup { cause, .. } => cause,
            Self::CommittedCreation(failure) => failure.cause(),
        }
    }
    #[must_use]
    pub const fn committed_cleanup_failure(&self) -> Option<LegacyCommittedCleanupFailure> {
        match self {
            Self::Unconfirmed(_) => None,
            Self::UnconfirmedCleanup {
                committed_cleanup_failure,
                ..
            } => Some(*committed_cleanup_failure),
            Self::CommittedCreation(failure) => failure.committed_cleanup_failure(),
        }
    }
    /// False means creation commit was not confirmed; native Internal can be an
    /// unknown completion and must not be treated as evidence of no effect.
    #[must_use]
    pub const fn creation_commit_confirmed(&self) -> bool {
        matches!(self, Self::CommittedCreation(_))
    }
    /// Native repository alone owns retries, including unknown completion.
    #[must_use]
    pub const fn retry_permitted(&self) -> bool {
        false
    }
    #[must_use]
    pub const fn compensation_permitted(&self) -> bool {
        false
    }
}
impl fmt::Debug for LegacyDeviceAuthError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Unconfirmed(cause) => f.debug_tuple("Unconfirmed").field(cause).finish(),
            Self::CommittedCreation(failure) => {
                f.debug_tuple("CommittedCreation").field(failure).finish()
            }
            Self::UnconfirmedCleanup { cause, .. } => f
                .debug_struct("UnconfirmedCleanup")
                .field("cause", cause)
                .field("cleanup_diagnostic_present", &true)
                .finish(),
        }
    }
}
impl From<LegacyAuthError> for LegacyDeviceAuthError {
    fn from(cause: LegacyAuthError) -> Self {
        Self::Unconfirmed(cause)
    }
}
impl fmt::Display for LegacyDeviceAuthError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Unconfirmed(cause) | Self::UnconfirmedCleanup { cause, .. } => cause.fmt(f),
            Self::CommittedCreation(failure) => failure.fmt(f),
        }
    }
}
impl std::error::Error for LegacyDeviceAuthError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Unconfirmed(cause) | Self::UnconfirmedCleanup { cause, .. } => Some(cause),
            Self::CommittedCreation(failure) => Some(failure),
        }
    }
}
fn device_repository_failure(error: LegacyRepositoryError) -> LegacyDeviceAuthError {
    // Borrow before moving the owned bounded error. Boxing its lease preserves
    // all native/boundary facts without enlarging every service Result error.
    let completion = match &error {
        LegacyRepositoryError::Lease(failure) => Some(failure.completion),
        _ => None,
    };
    let cause = match error {
        LegacyRepositoryError::UserNotFound => LegacyAuthError::UserAccountNotFound,
        LegacyRepositoryError::UserBanned => LegacyAuthError::UserAccountBanned,
        LegacyRepositoryError::UsernameAlreadyInUse => LegacyAuthError::UsernameAlreadyInUse,
        other => LegacyAuthError::DeviceRepository(other),
    };
    match completion {
        Some(LegacyLeaseCompletion::ConfirmedCreation {
            committed_cleanup_failure,
        }) => {
            // No token/cache action follows this error. A real commit stays
            // confirmed even when the boundary prevents acknowledgement.
            LegacyDeviceAuthError::CommittedCreation(LegacyDevicePostCommitFailure {
                cause,
                committed_cleanup_failure,
            })
        }
        Some(LegacyLeaseCompletion::UnconfirmedCleanup {
            committed_cleanup_failure,
        }) => LegacyDeviceAuthError::UnconfirmedCleanup {
            cause,
            committed_cleanup_failure,
        },
        _ => LegacyDeviceAuthError::Unconfirmed(cause),
    }
}

// This is the local Device error policy, not new HTTP/grpc routing or a claim
// that caller resource limits are upstream limits. No diagnostic string is used.
fn device_failure_code(cause: &LegacyAuthError) -> trnm_contracts::StableCode {
    use trnm_contracts::StableCode;
    match cause {
        LegacyAuthError::DeviceInput(_)
        | LegacyAuthError::CustomInput(_)
        | LegacyAuthError::InvalidConfiguration
        | LegacyAuthError::RefreshTokenRequired
        | LegacyAuthError::SessionLogoutTokenInvalid
        | LegacyAuthError::RefreshLogoutTokenInvalid => StableCode::InvalidArgument,
        LegacyAuthError::AuthTokenInvalid | LegacyAuthError::RefreshTokenInvalidOrExpired => {
            StableCode::Unauthenticated
        }
        LegacyAuthError::UserAccountNotFound => StableCode::NotFound,
        LegacyAuthError::UserAccountBanned | LegacyAuthError::PrincipalServiceMismatch => {
            StableCode::PermissionDenied
        }
        LegacyAuthError::UsernameAlreadyInUse => StableCode::AlreadyExists,
        LegacyAuthError::Repository(error)
        | LegacyAuthError::DeviceRepository(error)
        | LegacyAuthError::CustomRepository(error)
        | LegacyAuthError::CustomLookupRepository(error) => error.code(),
        LegacyAuthError::Cache(NakamaLegacyBlacklistError::InvalidLimits) => StableCode::Internal,
        LegacyAuthError::Cache(_) => StableCode::ResourceExhausted,
        LegacyAuthError::ClockUnavailable
        | LegacyAuthError::ClockRange
        | LegacyAuthError::RandomUnavailable
        | LegacyAuthError::CachePoisoned
        | LegacyAuthError::Issue(_) => StableCode::Internal,
    }
}

struct AuthState {
    provider: NakamaLegacyHs256Provider,
    policy: LegacyAuthPolicy,
    cache: Mutex<NakamaLegacyBlacklist>,
}
impl fmt::Debug for AuthState {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("AuthState")
            .field("provider", &"<owned-fixed-keys>")
            .field("policy", &self.policy)
            .field("cache", &"<shared-blacklist>")
            .finish()
    }
}

struct SweepWorker {
    stop: Arc<(Mutex<bool>, Condvar)>,
    join: Option<JoinHandle<()>>,
}
impl fmt::Debug for SweepWorker {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("SweepWorker")
            .field("joinable", &self.join.is_some())
            .finish()
    }
}
impl SweepWorker {
    fn start(state: Weak<AuthState>, interval: Duration) -> Result<Self, LegacyAuthError> {
        let stop = Arc::new((Mutex::new(false), Condvar::new()));
        let worker_stop = Arc::clone(&stop);
        let join = thread::Builder::new()
            .name("nakama-legacy-blacklist".into())
            .spawn(move || {
                let (mutex, condvar) = &*worker_stop;
                let mut stopped = match mutex.lock() {
                    Ok(g) => g,
                    Err(_) => return,
                };
                loop {
                    let waited = condvar.wait_timeout_while(stopped, interval, |flag| !*flag);
                    let Ok((guard, timeout)) = waited else { return };
                    stopped = guard;
                    if *stopped {
                        return;
                    }
                    if !timeout.timed_out() {
                        continue;
                    }
                    let Some(state) = state.upgrade() else { return };
                    let Ok(now) = utc_seconds() else { return };
                    let Ok(mut cache) = state.cache.lock() else {
                        return;
                    };
                    let _ = cache.sweep(now);
                }
            })
            .map_err(|_| LegacyAuthError::CachePoisoned)?;
        Ok(Self {
            stop,
            join: Some(join),
        })
    }
}
impl Drop for SweepWorker {
    fn drop(&mut self) {
        let (mutex, condvar) = &*self.stop;
        {
            let mut stopped = mutex
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            *stopped = true;
            condvar.notify_all();
        }
        if let Some(join) = self.join.take() {
            let _ = join.join();
        }
    }
}
#[derive(Debug)]
struct AuthRuntime {
    state: Arc<AuthState>,
    _worker: SweepWorker,
}

/// Clone shares the same blacklist and key authority, not a copied revocation set.
#[derive(Clone)]
pub struct LegacyAuthService {
    runtime: Arc<AuthRuntime>,
}
impl fmt::Debug for LegacyAuthService {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("LegacyAuthService")
            .field("runtime", &self.runtime.state)
            .finish()
    }
}

/// Private immutable server-owned Access context. No public from-claims/from-user
/// constructor and no durable-family/SID conversion exists. Nil UUID is preserved.
pub struct LegacyAccessPrincipal {
    state: Arc<AuthState>,
    verified: NakamaLegacyVerifiedClaims,
    user: UserId,
}
impl LegacyAccessPrincipal {
    #[must_use]
    pub const fn user(&self) -> UserId {
        self.user
    }
    #[must_use]
    pub fn username(&self) -> &str {
        &self.verified.claims().username
    }
    #[must_use]
    pub fn token_id(&self) -> &str {
        &self.verified.claims().token_id
    }
    #[must_use]
    pub fn variables(&self) -> Option<&BTreeMap<String, String>> {
        self.verified.claims().variables.as_ref()
    }
    #[must_use]
    pub fn expires_at(&self) -> i64 {
        self.verified.claims().expires_at
    }
    #[must_use]
    pub fn issued_at(&self) -> i64 {
        self.verified.claims().issued_at
    }
}
impl fmt::Debug for LegacyAccessPrincipal {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("LegacyAccessPrincipal")
            .field("user", &"<redacted>")
            .field("claims", &self.verified)
            .finish()
    }
}
#[derive(Debug)]
pub struct LegacySession {
    pub created: bool,
    pub access_expires_at: i64,
    pub refresh_expires_at: i64,
    pub committed_cleanup_failure: Option<LegacyCommittedCleanupFailure>,
    tokens: NakamaLegacyTokenPair,
}
impl LegacySession {
    #[must_use]
    pub const fn tokens(&self) -> &NakamaLegacyTokenPair {
        &self.tokens
    }
    #[must_use]
    pub fn into_tokens(self) -> NakamaLegacyTokenPair {
        self.tokens
    }
}

impl LegacyAuthService {
    pub fn from_config(config: LegacyAuthConfig) -> Result<Self, LegacyAuthError> {
        let policy = config.policy;
        let cache = NakamaLegacyBlacklist::new(
            policy.0.session_ttl_seconds,
            policy.0.refresh_ttl_seconds,
            policy.0.blacklist_limits,
        );
        let state = Arc::new(AuthState {
            provider: config.provider,
            policy,
            cache: Mutex::new(cache),
        });
        let interval = Duration::from_secs(
            u64::try_from(policy.0.session_ttl_seconds * 2)
                .map_err(|_| LegacyAuthError::InvalidConfiguration)?,
        );
        let worker = SweepWorker::start(Arc::downgrade(&state), interval)?;
        Ok(Self {
            runtime: Arc::new(AuthRuntime {
                state,
                _worker: worker,
            }),
        })
    }
    fn state(&self) -> &Arc<AuthState> {
        &self.runtime.state
    }
    fn cache(&self) -> Result<MutexGuard<'_, NakamaLegacyBlacklist>, LegacyAuthError> {
        self.state()
            .cache
            .lock()
            .map_err(|_| LegacyAuthError::CachePoisoned)
    }
    pub fn verify_access(&self, token: &[u8]) -> Result<LegacyAccessPrincipal, LegacyAuthError> {
        self.verify_access_at(token, utc_seconds()?)
    }
    pub fn verify_access_bearer(
        &self,
        authorization: &str,
    ) -> Result<LegacyAccessPrincipal, LegacyAuthError> {
        let token = authorization
            .strip_prefix("Bearer ")
            .ok_or(LegacyAuthError::AuthTokenInvalid)?;
        self.verify_access(token.as_bytes())
    }
    fn parse_at(
        &self,
        token: &[u8],
        kind: NakamaLegacyKeyKind,
        now: i64,
    ) -> Result<(NakamaLegacyVerifiedClaims, UserId), LegacyAuthError> {
        let error = || match kind {
            NakamaLegacyKeyKind::Access => LegacyAuthError::AuthTokenInvalid,
            NakamaLegacyKeyKind::Refresh => LegacyAuthError::RefreshTokenInvalidOrExpired,
        };
        let verified = NakamaLegacyVerifier::new(
            &self.state().provider,
            self.state().policy.0.verifier_limits,
        )
        .verify(token, kind, now)
        .map_err(|_| error())?;
        let user = parse_uuid(&verified.claims().user_id).ok_or_else(error)?;
        Ok((verified, user))
    }
    fn verify_access_at(
        &self,
        token: &[u8],
        now: i64,
    ) -> Result<LegacyAccessPrincipal, LegacyAuthError> {
        let (verified, user) = self.parse_at(token, NakamaLegacyKeyKind::Access, now)?;
        let claims = verified.claims();
        let valid = self
            .cache()?
            .is_valid_session(
                cache_user(user),
                claims.expires_at,
                cache_token(&claims.token_id),
            )
            .map_err(LegacyAuthError::Cache)?;
        if !valid {
            return Err(LegacyAuthError::AuthTokenInvalid);
        }
        Ok(LegacyAccessPrincipal {
            state: Arc::clone(self.state()),
            verified,
            user,
        })
    }
    pub fn refresh<R: LegacyUserRepository>(
        &self,
        repository: &mut R,
        token: &[u8],
        request_variables: Option<&BTreeMap<String, String>>,
    ) -> Result<LegacySession, LegacyAuthError> {
        if token.is_empty() {
            return Err(LegacyAuthError::RefreshTokenRequired);
        }
        self.refresh_at(
            repository,
            token,
            request_variables,
            utc_seconds()?,
            utc_seconds,
        )
    }
    fn refresh_at<R: LegacyUserRepository>(
        &self,
        repository: &mut R,
        token: &[u8],
        request_variables: Option<&BTreeMap<String, String>>,
        check_now: i64,
        mut issue_now: impl FnMut() -> Result<i64, LegacyAuthError>,
    ) -> Result<LegacySession, LegacyAuthError> {
        if token.is_empty() {
            return Err(LegacyAuthError::RefreshTokenRequired);
        }
        let (verified, user) = self.parse_at(token, NakamaLegacyKeyKind::Refresh, check_now)?;
        let claims = verified.claims();
        if !self
            .cache()?
            .is_valid_refresh(
                cache_user(user),
                claims.expires_at,
                cache_token(&claims.token_id),
            )
            .map_err(LegacyAuthError::Cache)?
        {
            return Err(LegacyAuthError::RefreshTokenInvalidOrExpired);
        }
        // Do not hold the cache mutex over native user I/O. Source does not
        // recheck invalidation after this read; no extra atomic refresh claim.
        let stored = repository
            .read_legacy_user(user)
            .map_err(LegacyAuthError::Repository)?
            .ok_or(LegacyAuthError::UserAccountNotFound)?;
        if stored.user_id != user {
            return Err(LegacyAuthError::Repository(LegacyRepositoryError::DataLoss));
        }
        if stored.disable_time_unix_seconds.is_some_and(|n| n != 0) {
            return Err(LegacyAuthError::UserAccountBanned);
        }
        let canonical_user = uuid_string(user);
        let identity = NakamaLegacyClaims {
            token_id: &claims.token_id,
            user_id: &canonical_user,
            username: &stored.username,
            variables: request_variables.or(claims.variables.as_ref()),
            expires_at: 0,
            issued_at: claims.issued_at,
        };
        self.issue_session(user, &identity, false, &mut issue_now)
    }
    fn issue_session(
        &self,
        user: UserId,
        identity: &NakamaLegacyClaims<'_>,
        created: bool,
        issue_now: &mut impl FnMut() -> Result<i64, LegacyAuthError>,
    ) -> Result<LegacySession, LegacyAuthError> {
        let policy = self.state().policy.0;
        let access_expires_at = issue_now()?
            .checked_add(policy.session_ttl_seconds)
            .ok_or(LegacyAuthError::ClockRange)?;
        let refresh_expires_at = issue_now()?
            .checked_add(policy.refresh_ttl_seconds)
            .ok_or(LegacyAuthError::ClockRange)?;
        let tokens = NakamaLegacyIssuer::new(&self.state().provider, policy.issuer_limits)
            .issue_pair(identity, access_expires_at, refresh_expires_at)
            .map_err(LegacyAuthError::Issue)?;
        self.cache()?.add(
            cache_user(user),
            access_expires_at,
            cache_token(identity.token_id),
            refresh_expires_at,
            cache_token(identity.token_id),
        );
        Ok(LegacySession {
            created,
            access_expires_at,
            refresh_expires_at,
            committed_cleanup_failure: None,
            tokens,
        })
    }
    /// Requires this service's Access context. The transport authenticates each
    /// request freshly; this internal context is not a reusable HTTP credential.
    pub fn logout_authenticated(
        &self,
        principal: &LegacyAccessPrincipal,
        access_body: &[u8],
        refresh_body: &[u8],
    ) -> Result<(), LegacyAuthError> {
        self.logout_at(principal, access_body, refresh_body, utc_seconds)
    }
    pub fn logout_bearer(
        &self,
        authorization: &str,
        access_body: &[u8],
        refresh_body: &[u8],
    ) -> Result<(), LegacyAuthError> {
        let principal = self.verify_access_bearer(authorization)?;
        self.logout_authenticated(&principal, access_body, refresh_body)
    }
    fn logout_at(
        &self,
        principal: &LegacyAccessPrincipal,
        access_body: &[u8],
        refresh_body: &[u8],
        mut now: impl FnMut() -> Result<i64, LegacyAuthError>,
    ) -> Result<(), LegacyAuthError> {
        if !Arc::ptr_eq(self.state(), &principal.state) {
            return Err(LegacyAuthError::PrincipalServiceMismatch);
        }
        let mut access = None;
        if !access_body.is_empty() {
            let checked = self
                .parse_at(access_body, NakamaLegacyKeyKind::Access, now()?)
                .map_err(|_| LegacyAuthError::SessionLogoutTokenInvalid)?;
            if checked.1 != principal.user {
                return Err(LegacyAuthError::SessionLogoutTokenInvalid);
            }
            access = Some(checked.0);
        }
        let mut refresh = None;
        if !refresh_body.is_empty() {
            let checked = self
                .parse_at(refresh_body, NakamaLegacyKeyKind::Refresh, now()?)
                .map_err(|_| LegacyAuthError::RefreshLogoutTokenInvalid)?;
            if checked.1 != principal.user {
                return Err(LegacyAuthError::RefreshLogoutTokenInvalid);
            }
            refresh = Some(checked.0);
        }
        // Body token validation deliberately omits cache lookups. Cache mutation
        // is one whole preflight, after BOTH purpose/owner validations succeed.
        let access_id = access.as_ref().map_or("", |t| t.claims().token_id.as_str());
        let refresh_id = refresh
            .as_ref()
            .map_or("", |t| t.claims().token_id.as_str());
        if access_id.is_empty() && refresh_id.is_empty() {
            let cutoff = now()?;
            self.cache()?
                .remove_all(cache_user(principal.user), cutoff)
                .map_err(LegacyAuthError::Cache)
        } else {
            self.cache()?
                .remove(NakamaLegacyTokenRemoval {
                    user: cache_user(principal.user),
                    session_exp: access.as_ref().map_or(0, |t| t.claims().expires_at),
                    session_token_id: cache_token(access_id),
                    refresh_exp: refresh.as_ref().map_or(0, |t| t.claims().expires_at),
                    refresh_token_id: cache_token(refresh_id),
                })
                .map_err(LegacyAuthError::Cache)
        }
    }
    pub fn authenticate_device<R: LegacyDeviceRepository>(
        &self,
        repository: &mut R,
        input: LegacyDeviceAuthInput<'_>,
    ) -> Result<LegacySession, LegacyDeviceAuthError> {
        let id = input.account_id.map(str::as_bytes);
        let generated = if input.username.is_empty() {
            // Source ID errors must precede even a possible OS RNG failure.
            validate_device_input(id, b"source-precheck", input.create, || unreachable!())
                .map_err(LegacyAuthError::DeviceInput)?;
            Some(generate_username()?)
        } else {
            None
        };
        let validated = validate_device_input(id, input.username.as_bytes(), input.create, || {
            generated.expect("empty username generated after ID validation")
        })
        .map_err(LegacyAuthError::DeviceInput)?;
        let name = std::str::from_utf8(validated.username.as_bytes())
            .map_err(|_| LegacyAuthError::Repository(LegacyRepositoryError::DataLoss))?;
        let account = repository
            .authenticate_legacy_device(LegacyDeviceRepositoryInput {
                device_id: input.account_id.expect("validated present ID"),
                requested_username: name,
                create: validated.create,
            })
            .map_err(device_repository_failure)?;
        self.finish_device_account(account, input.variables, utc_seconds, random_uuid_v4)
    }
    // Private deterministic sources exist only for finite unit tests. The public
    // entry point always supplies this runtime's UTC clock and OS UUID source.
    fn finish_device_account(
        &self,
        account: LegacyDeviceAccount,
        variables: Option<&BTreeMap<String, String>>,
        mut now: impl FnMut() -> Result<i64, LegacyAuthError>,
        token_id: impl FnOnce() -> Result<String, LegacyAuthError>,
    ) -> Result<LegacySession, LegacyDeviceAuthError> {
        if !account.created {
            if let Some(committed_cleanup_failure) = account.committed_cleanup_failure {
                return Err(LegacyDeviceAuthError::UnconfirmedCleanup {
                    cause: LegacyAuthError::DeviceRepository(LegacyRepositoryError::DataLoss),
                    committed_cleanup_failure,
                });
            }
        }
        let result = (|| {
            if self.state().policy.0.single_session {
                let cutoff = now()?;
                self.cache()?
                    .remove_all(cache_user(account.user_id), cutoff)
                    .map_err(LegacyAuthError::Cache)?;
            }
            let tid = token_id()?;
            let issued_at = now()?;
            let uid = uuid_string(account.user_id);
            let identity = NakamaLegacyClaims {
                token_id: &tid,
                user_id: &uid,
                username: &account.stored_username,
                variables,
                expires_at: 0,
                issued_at,
            };
            let mut session =
                self.issue_session(account.user_id, &identity, account.created, &mut now)?;
            session.committed_cleanup_failure = account.committed_cleanup_failure;
            Ok(session)
        })();
        result.map_err(|cause| {
            if account.created {
                LegacyDeviceAuthError::CommittedCreation(LegacyDevicePostCommitFailure {
                    cause,
                    committed_cleanup_failure: account.committed_cleanup_failure,
                })
            } else {
                // The existing-account branch is a read, not a commit by this
                // request. Preserve its original local failure without that claim.
                LegacyDeviceAuthError::Unconfirmed(cause)
            }
        })
    }
    pub fn remove_all(&self, user: UserId) -> Result<(), LegacyAuthError> {
        let cutoff = utc_seconds()?;
        self.cache()?
            .remove_all(cache_user(user), cutoff)
            .map_err(LegacyAuthError::Cache)
    }
    pub fn ban(&self, users: &[UserId]) -> Result<(), LegacyAuthError> {
        self.ban_at(users, utc_seconds)
    }
    fn ban_at(
        &self,
        users: &[UserId],
        now: impl FnOnce() -> Result<i64, LegacyAuthError>,
    ) -> Result<(), LegacyAuthError> {
        // Source Ban samples before its lock and before any key preparation.
        // Quota/allocation preflight still completes before cache mutation.
        let cutoff = now()?;
        if users.len()
            > self
                .state()
                .policy
                .0
                .blacklist_limits
                .policy()
                .max_mutation_items
        {
            return Err(LegacyAuthError::Cache(
                NakamaLegacyBlacklistError::MutationItemsLimit,
            ));
        }
        let mut keys = Vec::new();
        keys.try_reserve_exact(users.len())
            .map_err(|_| LegacyAuthError::Cache(NakamaLegacyBlacklistError::AllocationFailed))?;
        for &user in users {
            keys.push(cache_user(user));
        }
        self.cache()?
            .ban(&keys, cutoff)
            .map_err(LegacyAuthError::Cache)
    }
    pub fn unban(&self, _users: &[UserId]) { /* Exact upstream no-op. */
    }
    pub fn sweep_expired(&self) -> Result<NakamaLegacyBlacklistSweep, LegacyAuthError> {
        let cutoff = utc_seconds()?;
        Ok(self.cache()?.sweep(cutoff))
    }
    pub fn blacklist_stats(&self) -> Result<NakamaLegacyBlacklistStats, LegacyAuthError> {
        Ok(self.cache()?.stats())
    }
}
fn cache_user(user: UserId) -> NakamaLegacyCacheUserId {
    NakamaLegacyCacheUserId::from_bytes(*user.as_bytes())
}
fn cache_token(id: &str) -> NakamaLegacyCacheTokenId<'_> {
    NakamaLegacyCacheTokenId::from_bytes(id.as_bytes())
}
fn utc_seconds() -> Result<i64, LegacyAuthError> {
    system_time_seconds(SystemTime::now())
}
fn system_time_seconds(time: SystemTime) -> Result<i64, LegacyAuthError> {
    match time.duration_since(UNIX_EPOCH) {
        Ok(d) => i64::try_from(d.as_secs()).map_err(|_| LegacyAuthError::ClockRange),
        Err(e) => {
            let d = e.duration();
            let n = i64::try_from(d.as_secs()).map_err(|_| LegacyAuthError::ClockRange)?;
            n.checked_neg()
                .and_then(|n| n.checked_sub(i64::from(d.subsec_nanos() != 0)))
                .ok_or(LegacyAuthError::ClockRange)
        }
    }
}
fn random_bytes(buffer: &mut [u8]) -> Result<(), LegacyAuthError> {
    // Current owned Linux server profile. OS CSPRNG errors return no credential;
    // this is not a cross-platform RNG or Go math/rand stream-equivalence claim.
    std::fs::File::open("/dev/urandom")
        .and_then(|mut file| file.read_exact(buffer))
        .map_err(|_| LegacyAuthError::RandomUnavailable)
}
pub(super) fn generate_account_id() -> Result<UserId, LegacyAuthError> {
    let mut raw = [0; 16];
    random_bytes(&mut raw)?;
    raw[6] = (raw[6] & 0x0f) | 0x40;
    raw[8] = (raw[8] & 0x3f) | 0x80;
    Ok(UserId::new(raw))
}
fn random_uuid_v4() -> Result<String, LegacyAuthError> {
    Ok(uuid_string(generate_account_id()?))
}
fn generate_username() -> Result<GeneratedUsername, LegacyAuthError> {
    const ALPHABET: &[u8; 52] = b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";
    let mut raw = [0; 10];
    let mut i = 0;
    // One bounded OS read: exhaustion of the 256-byte rejection reservoir is a
    // fail-closed local RNG resource policy, not a source RNG equivalence claim.
    let mut reservoir = [0; 256];
    random_bytes(&mut reservoir)?;
    for byte in reservoir {
        if byte < 208 {
            raw[i] = ALPHABET[(byte % 52) as usize];
            i += 1;
            if i == 10 {
                break;
            }
        }
    }
    if i != 10 {
        return Err(LegacyAuthError::RandomUnavailable);
    }
    GeneratedUsername::new(raw).map_err(|_| LegacyAuthError::RandomUnavailable)
}

#[cfg(test)]
#[path = "legacy_auth_tests.rs"]
mod tests;

/// Custom has its own typed repository boundary. Its completed autocommit
/// account alone can enter the existing private session issuance path.
pub trait LegacyCustomRepository {
    fn authenticate_legacy_custom(
        &mut self,
        input: LegacyCustomRepositoryInput<'_>,
    ) -> Result<LegacyCustomAccount, LegacyRepositoryError>;
}
#[derive(Clone, Copy)]
pub struct LegacyCustomRepositoryInput<'a> {
    pub custom_id: &'a str,
    pub requested_username: &'a str,
    pub create: bool,
}
impl fmt::Debug for LegacyCustomRepositoryInput<'_> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("LegacyCustomRepositoryInput")
            .field("custom_id", &"<redacted>")
            .field("username", &"<redacted>")
            .field("create", &self.create)
            .finish()
    }
}
pub struct LegacyCustomAccount {
    pub user_id: UserId,
    pub stored_username: String,
    pub created: bool,
}
impl fmt::Debug for LegacyCustomAccount {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("LegacyCustomAccount")
            .field("user_id", &"<redacted>")
            .field("username", &"<redacted>")
            .field("created", &self.created)
            .finish()
    }
}
#[derive(Clone, Copy)]
pub struct LegacyCustomAuthInput<'a> {
    pub account_id: Option<&'a str>,
    pub username: &'a str,
    pub create: Option<bool>,
    pub variables: Option<&'a BTreeMap<String, String>>,
}
impl fmt::Debug for LegacyCustomAuthInput<'_> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("LegacyCustomAuthInput")
            .field("account_id", &"<redacted>")
            .field("username", &"<redacted>")
            .field("create", &self.create)
            .field("variables", &self.variables.map(BTreeMap::len))
            .finish()
    }
}
/// Same typed postcommit/session wrapper; this alias does not route a Custom
/// mutation through Device's repository or retry loop.
pub type LegacyCustomAuthError = LegacyDeviceAuthError;
impl LegacyAuthService {
    pub fn authenticate_custom<R: LegacyCustomRepository>(
        &self,
        repository: &mut R,
        input: LegacyCustomAuthInput<'_>,
    ) -> Result<LegacySession, LegacyCustomAuthError> {
        let id = input.account_id.map(str::as_bytes);
        let generated = if input.username.is_empty() {
            validate_custom_input(id, b"source-precheck", input.create, || unreachable!())
                .map_err(LegacyAuthError::CustomInput)?;
            Some(generate_username()?)
        } else {
            None
        };
        let validated = validate_custom_input(id, input.username.as_bytes(), input.create, || {
            generated.expect("empty username generated after Custom ID validation")
        })
        .map_err(LegacyAuthError::CustomInput)?;
        let name = std::str::from_utf8(validated.username.as_bytes())
            .map_err(|_| LegacyAuthError::CustomRepository(LegacyRepositoryError::DataLoss))?;
        let account = repository
            .authenticate_legacy_custom(LegacyCustomRepositoryInput {
                custom_id: input.account_id.expect("validated present Custom ID"),
                requested_username: name,
                create: validated.create,
            })
            .map_err(custom_repository_failure)?;
        // This conversion shares only the private post-account issuer/cache.
        // Autocommit Custom has no CR Device RELEASE cleanup diagnostic.
        self.finish_device_account(
            LegacyDeviceAccount {
                user_id: account.user_id,
                stored_username: account.stored_username,
                created: account.created,
                committed_cleanup_failure: None,
            },
            input.variables,
            utc_seconds,
            random_uuid_v4,
        )
    }
}
fn custom_repository_failure(error: LegacyRepositoryError) -> LegacyCustomAuthError {
    let completion = match &error {
        LegacyRepositoryError::Lease(f) => Some(f.completion),
        _ => None,
    };
    let cause = match error {
        LegacyRepositoryError::UserNotFound => LegacyAuthError::UserAccountNotFound,
        LegacyRepositoryError::UserBanned => LegacyAuthError::UserAccountBanned,
        LegacyRepositoryError::UsernameAlreadyInUse => LegacyAuthError::UsernameAlreadyInUse,
        error @ LegacyRepositoryError::CustomLookupFailed(_) => {
            LegacyAuthError::CustomLookupRepository(error)
        }
        other => LegacyAuthError::CustomRepository(other),
    };
    match completion {
        Some(LegacyLeaseCompletion::ConfirmedCreation {
            committed_cleanup_failure,
        }) => LegacyDeviceAuthError::CommittedCreation(LegacyDevicePostCommitFailure {
            cause,
            committed_cleanup_failure,
        }),
        _ => LegacyDeviceAuthError::Unconfirmed(cause),
    }
}
