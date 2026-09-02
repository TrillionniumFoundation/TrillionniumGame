use std::collections::BTreeMap;
use std::fmt;
use std::ops::{Deref, DerefMut};
use std::str::FromStr;
use std::sync::atomic::{AtomicU64, AtomicU8, Ordering};
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

type PlainManager = PostgresConnectionManager<NoTls>;
type TlsManager = PostgresConnectionManager<MakeTlsConnector>;
type CancelAction = Arc<dyn Fn() -> bool + Send + Sync + 'static>;

const MINIMUM_OPERATION_BUDGET: Duration = Duration::from_millis(1);
const CANCEL_NONE: u8 = 0;
const CANCEL_DEADLINE: u8 = 1;
const CANCEL_SHUTDOWN: u8 = 2;

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
