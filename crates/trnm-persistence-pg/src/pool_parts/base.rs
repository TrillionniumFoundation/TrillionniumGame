use std::collections::BTreeMap;
use std::fmt;
use std::ops::{Deref, DerefMut};
use std::str::FromStr;
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicU8, Ordering};
use std::sync::mpsc::{self, RecvTimeoutError, Sender};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use native_tls::{Certificate, Identity, Protocol, TlsConnector};
use postgres::config::SslMode;
use postgres::{CancelToken, Client, Config, NoTls};
use postgres_native_tls::MakeTlsConnector;
use r2d2::{Pool, PooledConnection};
use r2d2_postgres::PostgresConnectionManager;
use trnm_contracts::{DomainError, RetryClass, StableCode};

use super::{DatabaseProfile, PgRepository};

type PlainManager = RetirementManager<PostgresConnectionManager<NoTls>>;
type TlsManager = RetirementManager<PostgresConnectionManager<MakeTlsConnector>>;
type CancelAction = Arc<dyn Fn() -> bool + Send + Sync + 'static>;

const MINIMUM_OPERATION_BUDGET: Duration = Duration::from_millis(1);
const CANCEL_NONE: u8 = 0;
const CANCEL_DEADLINE: u8 = 1;
const CANCEL_SHUTDOWN: u8 = 2;

// CancelRequest identifies a backend connection, not an individual query.
// Transport success is not server acknowledgement. Retire any lease that
// dispatched cancellation, even when the original operation won the race.
pub(crate) struct RetirementManager<M> {
    inner: M,
}

pub(crate) struct RetirableConnection<C> {
    client: C,
    retired: Arc<AtomicBool>,
}

impl<M> fmt::Debug for RetirementManager<M> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.debug_struct("RetirementManager").finish_non_exhaustive()
    }
}

impl<C> fmt::Debug for RetirableConnection<C> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("RetirableConnection")
            .field("retired", &self.retired.load(Ordering::Acquire))
            .finish_non_exhaustive()
    }
}

impl<M> RetirementManager<M> {
    fn new(inner: M) -> Self {
        Self { inner }
    }
}

impl<M: r2d2::ManageConnection> r2d2::ManageConnection for RetirementManager<M> {
    type Connection = RetirableConnection<M::Connection>;
    type Error = M::Error;

    fn connect(&self) -> Result<Self::Connection, Self::Error> {
        self.inner.connect().map(|client| RetirableConnection {
            client,
            retired: Arc::new(AtomicBool::new(false)),
        })
    }

    fn is_valid(&self, connection: &mut Self::Connection) -> Result<(), Self::Error> {
        self.inner.is_valid(&mut connection.client)
    }

    fn has_broken(&self, connection: &mut Self::Connection) -> bool {
        connection.retired.load(Ordering::Acquire) || self.inner.has_broken(&mut connection.client)
    }
}

pub(crate) enum ClientHandle {
    Direct(Client),
    Plain(PooledConnection<PlainManager>),
    Tls(PooledConnection<TlsManager>),
}

impl ClientHandle {
    pub(crate) fn direct(client: Client) -> Self {
        Self::Direct(client)
    }

    fn retirement_flag(&self) -> Option<Arc<AtomicBool>> {
        match self {
            Self::Direct(_) => None,
            Self::Plain(connection) => Some(Arc::clone(&connection.retired)),
            Self::Tls(connection) => Some(Arc::clone(&connection.retired)),
        }
    }

    fn retire(&self) {
        if let Some(retired) = self.retirement_flag() {
            retired.store(true, Ordering::Release);
        }
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
            Self::Plain(connection) => &connection.client,
            Self::Tls(connection) => &connection.client,
        }
    }
}

impl DerefMut for ClientHandle {
    fn deref_mut(&mut self) -> &mut Self::Target {
        match self {
            Self::Direct(client) => client,
            Self::Plain(connection) => &mut connection.client,
            Self::Tls(connection) => &mut connection.client,
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
            .field("custom_root_certificate", &self.root_certificate_pem.is_some())
            .field("client_identity", &self.identity_certificate_chain_pem.is_some())
            .field("private_key", &"<redacted>")
            .finish()
    }
}

#[cfg(test)]
mod retirement_tests {
    use super::*;

    struct TestManager {
        next: Arc<AtomicU64>,
        drops: Arc<AtomicU64>,
    }

    struct TestConnection {
        id: u64,
        drops: Arc<AtomicU64>,
    }

    impl Drop for TestConnection {
        fn drop(&mut self) {
            self.drops.fetch_add(1, Ordering::Relaxed);
        }
    }

    impl r2d2::ManageConnection for TestManager {
        type Connection = TestConnection;
        type Error = std::io::Error;

        fn connect(&self) -> Result<Self::Connection, Self::Error> {
            Ok(TestConnection {
                id: self.next.fetch_add(1, Ordering::Relaxed),
                drops: Arc::clone(&self.drops),
            })
        }

        fn is_valid(&self, _: &mut Self::Connection) -> Result<(), Self::Error> {
            Ok(())
        }

        fn has_broken(&self, _: &mut Self::Connection) -> bool {
            false
        }
    }

    #[test]
    fn retired_lease_is_dropped_not_recycled_by_r2d2() {
        let drops = Arc::new(AtomicU64::new(0));
        let pool = Pool::builder()
            .max_size(1)
            .min_idle(Some(0))
            .build(RetirementManager::new(TestManager {
                next: Arc::new(AtomicU64::new(0)),
                drops: Arc::clone(&drops),
            }))
            .unwrap();
        let lease = pool.get_timeout(Duration::from_secs(5)).unwrap();
        let previous = lease.client.id;
        lease.retired.store(true, Ordering::Release);
        drop(lease);
        assert_eq!(drops.load(Ordering::Relaxed), 1);
        let replacement = pool.get_timeout(Duration::from_secs(5)).unwrap();
        assert_ne!(replacement.client.id, previous);
        let replacement_id = replacement.client.id;
        drop(replacement);
        let healthy = pool.get_timeout(Duration::from_secs(5)).unwrap();
        assert_eq!(healthy.client.id, replacement_id);
        assert_eq!(drops.load(Ordering::Relaxed), 1);
    }
}
