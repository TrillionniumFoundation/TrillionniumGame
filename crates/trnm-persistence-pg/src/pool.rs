use std::fmt;
use std::ops::{Deref, DerefMut};
use std::str::FromStr;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use native_tls::{Certificate, Identity, Protocol, TlsConnector};
use postgres::config::SslMode;
use postgres::{Client, Config, NoTls};
use postgres_native_tls::MakeTlsConnector;
use r2d2::{Pool, PooledConnection};
use r2d2_postgres::PostgresConnectionManager;
use trnm_contracts::{DomainError, RetryClass, StableCode};

use super::{DatabaseProfile, PgRepository};

type PlainManager = PostgresConnectionManager<NoTls>;
type TlsManager = PostgresConnectionManager<MakeTlsConnector>;

pub(crate) enum ClientHandle {
    Direct(Client),
    Plain(PooledConnection<PlainManager>),
    Tls(PooledConnection<TlsManager>),
}

impl ClientHandle {
    pub(crate) fn direct(client: Client) -> Self {
        Self::Direct(client)
    }
}

impl fmt::Debug for ClientHandle {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        let transport = match self {
            Self::Direct(_) => "direct-plaintext",
            Self::Plain(_) => "pooled-plaintext",
            Self::Tls(_) => "pooled-tls",
        };
        formatter
            .debug_struct("ClientHandle")
            .field("transport", &transport)
            .finish_non_exhaustive()
    }
}

impl Deref for ClientHandle {
    type Target = Client;

    fn deref(&self) -> &Self::Target {
        match self {
            Self::Direct(client) => client,
            Self::Plain(client) => client,
            Self::Tls(client) => client,
        }
    }
}

impl DerefMut for ClientHandle {
    fn deref_mut(&mut self) -> &mut Self::Target {
        match self {
            Self::Direct(client) => client,
            Self::Plain(client) => client,
            Self::Tls(client) => client,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct PgPoolConfig {
    pub max_size: u32,
    pub min_idle: u32,
    pub acquire_timeout: Duration,
    pub idle_timeout: Duration,
    pub max_lifetime: Duration,
    pub statement_timeout: Duration,
    pub lock_timeout: Duration,
    pub idle_transaction_timeout: Duration,
}

impl PgPoolConfig {
    pub fn validate(self) -> Result<Self, DomainError> {
        if self.max_size == 0
            || self.max_size > 256
            || self.min_idle > self.max_size
            || self.acquire_timeout.is_zero()
            || self.idle_timeout.is_zero()
            || self.max_lifetime.is_zero()
            || self.statement_timeout.is_zero()
            || self.lock_timeout.is_zero()
            || self.idle_transaction_timeout.is_zero()
            || self.lock_timeout > self.statement_timeout
            || self.idle_timeout > self.max_lifetime
        {
            return Err(configuration_error("database_pool_policy_invalid"));
        }
        Ok(self)
    }
}

impl Default for PgPoolConfig {
    fn default() -> Self {
        Self {
            max_size: 8,
            min_idle: 1,
            acquire_timeout: Duration::from_secs(2),
            idle_timeout: Duration::from_secs(60),
            max_lifetime: Duration::from_secs(15 * 60),
            statement_timeout: Duration::from_secs(5),
            lock_timeout: Duration::from_secs(1),
            idle_transaction_timeout: Duration::from_secs(5),
        }
    }
}

#[derive(Default)]
pub struct PgTlsConfig {
    root_certificate_pem: Option<Vec<u8>>,
    identity_certificate_chain_pem: Option<Vec<u8>>,
    identity_private_key_pkcs8_pem: Option<Vec<u8>>,
}

impl PgTlsConfig {
    pub fn new(
        root_certificate_pem: Option<Vec<u8>>,
        identity_certificate_chain_pem: Option<Vec<u8>>,
        identity_private_key_pkcs8_pem: Option<Vec<u8>>,
    ) -> Result<Self, DomainError> {
        if identity_certificate_chain_pem.is_some() != identity_private_key_pkcs8_pem.is_some() {
            return Err(configuration_error(
                "database_tls_identity_cert_key_pair_required",
            ));
        }
        Ok(Self {
            root_certificate_pem,
            identity_certificate_chain_pem,
            identity_private_key_pkcs8_pem,
        })
    }

    fn connector(&self) -> Result<MakeTlsConnector, DomainError> {
        let mut builder = TlsConnector::builder();
        builder.min_protocol_version(Some(Protocol::Tlsv12));
        if let Some(root) = &self.root_certificate_pem {
            let certificate = Certificate::from_pem(root)
                .map_err(|_| configuration_error("database_tls_root_certificate_invalid"))?;
            builder.add_root_certificate(certificate);
        }
        match (
            &self.identity_certificate_chain_pem,
            &self.identity_private_key_pkcs8_pem,
        ) {
            (Some(certificate), Some(key)) => {
                let identity = Identity::from_pkcs8(certificate, key)
                    .map_err(|_| configuration_error("database_tls_identity_invalid"))?;
                builder.identity(identity);
            }
            (None, None) => {}
            _ => {
                return Err(configuration_error(
                    "database_tls_identity_cert_key_pair_required",
                ));
            }
        }
        let connector = builder
            .build()
            .map_err(|_| configuration_error("database_tls_connector_invalid"))?;
        Ok(MakeTlsConnector::new(connector))
    }
}

impl fmt::Debug for PgTlsConfig {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("PgTlsConfig")
            .field(
                "custom_root_certificate",
                &self.root_certificate_pem.is_some(),
            )
            .field(
                "client_identity",
                &self.identity_certificate_chain_pem.is_some(),
            )
            .field("private_key", &"<redacted>")
            .finish()
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct PgPoolSnapshot {
    pub max_size: u32,
    pub connections: u32,
    pub idle_connections: u32,
    pub acquire_attempts: u64,
    pub acquire_failures: u64,
    pub session_policy_failures: u64,
}

#[derive(Debug, Default)]
struct PgPoolMetrics {
    acquire_attempts: AtomicU64,
    acquire_failures: AtomicU64,
    session_policy_failures: AtomicU64,
}

#[derive(Clone)]
enum PoolInner {
    Plain(Pool<PlainManager>),
    Tls(Pool<TlsManager>),
}

#[derive(Clone)]
pub struct PgPool {
    profile: DatabaseProfile,
    policy: PgPoolConfig,
    inner: PoolInner,
    metrics: Arc<PgPoolMetrics>,
}

impl PgPool {
    pub fn connect_plain(
        database_url: &str,
        profile: DatabaseProfile,
        policy: PgPoolConfig,
    ) -> Result<Self, DomainError> {
        let policy = policy.validate()?;
        let mut database = Config::from_str(database_url).map_err(super::map_postgres_error)?;
        database.ssl_mode(SslMode::Disable);
        let manager = PostgresConnectionManager::new(database, NoTls);
        let pool = pool_builder(&policy)
            .build(manager)
            .map_err(|_| operational_error("database_pool_initialization_failed"))?;
        Ok(Self {
            profile,
            policy,
            inner: PoolInner::Plain(pool),
            metrics: Arc::new(PgPoolMetrics::default()),
        })
    }

    pub fn connect_tls(
        database_url: &str,
        profile: DatabaseProfile,
        policy: PgPoolConfig,
        tls: &PgTlsConfig,
    ) -> Result<Self, DomainError> {
        let policy = policy.validate()?;
        let mut database = Config::from_str(database_url).map_err(super::map_postgres_error)?;
        database.ssl_mode(SslMode::Require);
        let manager = PostgresConnectionManager::new(database, tls.connector()?);
        let pool = pool_builder(&policy)
            .build(manager)
            .map_err(|_| operational_error("database_tls_pool_initialization_failed"))?;
        Ok(Self {
            profile,
            policy,
            inner: PoolInner::Tls(pool),
            metrics: Arc::new(PgPoolMetrics::default()),
        })
    }

    pub fn acquire(&self) -> Result<PgRepository, DomainError> {
        self.metrics
            .acquire_attempts
            .fetch_add(1, Ordering::Relaxed);
        let handle = match &self.inner {
            PoolInner::Plain(pool) => pool
                .get_timeout(self.policy.acquire_timeout)
                .map(ClientHandle::Plain),
            PoolInner::Tls(pool) => pool
                .get_timeout(self.policy.acquire_timeout)
                .map(ClientHandle::Tls),
        }
        .map_err(|_| {
            self.metrics
                .acquire_failures
                .fetch_add(1, Ordering::Relaxed);
            operational_error("database_pool_acquire_timeout")
        })?;

        let mut repository = PgRepository {
            profile: self.profile,
            client: handle,
        };
        if let Err(error) = configure_session(&mut repository.client, self.profile, self.policy) {
            self.metrics
                .session_policy_failures
                .fetch_add(1, Ordering::Relaxed);
            return Err(error);
        }
        Ok(repository)
    }

    #[must_use]
    pub fn snapshot(&self) -> PgPoolSnapshot {
        let (max_size, state) = match &self.inner {
            PoolInner::Plain(pool) => (pool.max_size(), pool.state()),
            PoolInner::Tls(pool) => (pool.max_size(), pool.state()),
        };
        PgPoolSnapshot {
            max_size,
            connections: state.connections,
            idle_connections: state.idle_connections,
            acquire_attempts: self.metrics.acquire_attempts.load(Ordering::Relaxed),
            acquire_failures: self.metrics.acquire_failures.load(Ordering::Relaxed),
            session_policy_failures: self.metrics.session_policy_failures.load(Ordering::Relaxed),
        }
    }

    #[must_use]
    pub const fn profile(&self) -> DatabaseProfile {
        self.profile
    }

    #[must_use]
    pub const fn policy(&self) -> PgPoolConfig {
        self.policy
    }
}

impl fmt::Debug for PgPool {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        let transport = match &self.inner {
            PoolInner::Plain(_) => "plaintext-candidate",
            PoolInner::Tls(_) => "tls-verify-full",
        };
        formatter
            .debug_struct("PgPool")
            .field("profile", &self.profile)
            .field("transport", &transport)
            .field("policy", &self.policy)
            .field("snapshot", &self.snapshot())
            .finish()
    }
}

fn pool_builder<M>(policy: &PgPoolConfig) -> r2d2::Builder<M>
where
    M: r2d2::ManageConnection,
{
    Pool::builder()
        .max_size(policy.max_size)
        .min_idle(Some(policy.min_idle))
        .connection_timeout(policy.acquire_timeout)
        .idle_timeout(Some(policy.idle_timeout))
        .max_lifetime(Some(policy.max_lifetime))
        .test_on_check_out(true)
}

fn configure_session(
    client: &mut Client,
    profile: DatabaseProfile,
    policy: PgPoolConfig,
) -> Result<(), DomainError> {
    let statement_timeout = duration_millis(policy.statement_timeout)?;
    client
        .batch_execute(&format!(
            "SET application_name = 'trillionnium-game'; SET statement_timeout = '{statement_timeout}ms';"
        ))
        .map_err(super::map_postgres_error)?;
    if profile == DatabaseProfile::PostgreSql {
        let lock_timeout = duration_millis(policy.lock_timeout)?;
        let idle_transaction_timeout = duration_millis(policy.idle_transaction_timeout)?;
        client
            .batch_execute(&format!(
                "SET lock_timeout = '{lock_timeout}ms'; \
                 SET idle_in_transaction_session_timeout = '{idle_transaction_timeout}ms';"
            ))
            .map_err(super::map_postgres_error)?;
    }
    Ok(())
}

fn duration_millis(value: Duration) -> Result<u64, DomainError> {
    u64::try_from(value.as_millis())
        .map_err(|_| configuration_error("database_timeout_millis_overflow"))
}

fn configuration_error(reason: &'static str) -> DomainError {
    DomainError::new(StableCode::InvalidArgument, reason, RetryClass::Never)
}

fn operational_error(reason: &'static str) -> DomainError {
    DomainError::new(StableCode::Unavailable, reason, RetryClass::SafeBackoff)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_pool_policy_is_bounded_and_valid() {
        let policy = PgPoolConfig::default().validate().unwrap();
        assert_eq!(policy.max_size, 8);
        assert_eq!(policy.min_idle, 1);
        assert!(policy.acquire_timeout < policy.statement_timeout);
        assert!(policy.lock_timeout <= policy.statement_timeout);
        assert!(policy.idle_timeout < policy.max_lifetime);
    }

    #[test]
    fn invalid_pool_policy_fails_closed() {
        let policy = PgPoolConfig {
            max_size: 0,
            ..PgPoolConfig::default()
        };
        assert_eq!(
            policy.validate().unwrap_err().reason(),
            "database_pool_policy_invalid"
        );

        let default = PgPoolConfig::default();
        let policy = PgPoolConfig {
            min_idle: default.max_size + 1,
            ..default
        };
        assert_eq!(
            policy.validate().unwrap_err().reason(),
            "database_pool_policy_invalid"
        );

        let policy = PgPoolConfig {
            lock_timeout: Duration::from_secs(6),
            statement_timeout: Duration::from_secs(5),
            ..PgPoolConfig::default()
        };
        assert_eq!(
            policy.validate().unwrap_err().reason(),
            "database_pool_policy_invalid"
        );
    }

    #[test]
    fn tls_identity_requires_cert_and_key_pair() {
        assert_eq!(
            PgTlsConfig::new(None, Some(b"cert".to_vec()), None)
                .unwrap_err()
                .reason(),
            "database_tls_identity_cert_key_pair_required"
        );
        assert!(PgTlsConfig::new(None, None, None).is_ok());
    }

    #[test]
    fn tls_debug_never_exposes_private_key_material() {
        let config = PgTlsConfig::new(
            None,
            Some(b"certificate".to_vec()),
            Some(b"super-secret-private-key".to_vec()),
        )
        .unwrap();
        let debug = format!("{config:?}");
        assert!(debug.contains("<redacted>"));
        assert!(!debug.contains("super-secret"));
    }
}
