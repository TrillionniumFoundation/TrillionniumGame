use std::fmt;

use native_tls::TlsConnector;
use postgres::{CancelToken, NoTls};
use postgres_native_tls::MakeTlsConnector;
use trnm_contracts::{DomainError, RetryClass, StableCode};

use super::{map_postgres_error, PgRepository};

#[derive(Clone)]
pub(crate) enum CancelTransport {
    Plain,
    Tls(TlsConnector),
}

impl fmt::Debug for CancelTransport {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::Plain => "plaintext",
            Self::Tls(_) => "tls-verify-full",
        })
    }
}

pub struct PgCancelHandle {
    token: CancelToken,
    transport: CancelTransport,
}

impl PgCancelHandle {
    pub fn cancel(self) -> Result<(), DomainError> {
        match self.transport {
            CancelTransport::Plain => self.token.cancel_query(NoTls),
            CancelTransport::Tls(connector) => self
                .token
                .cancel_query(MakeTlsConnector::new(connector)),
        }
        .map_err(map_postgres_error)
    }
}

impl fmt::Debug for PgCancelHandle {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("PgCancelHandle")
            .field("transport", &self.transport)
            .finish_non_exhaustive()
    }
}

impl PgRepository {
    #[must_use]
    pub fn cancellation_handle(&self) -> PgCancelHandle {
        PgCancelHandle {
            token: self.client.cancel_token(),
            transport: self.cancel_transport.clone(),
        }
    }
}

pub(crate) fn cancellation_watchdog_panicked() -> DomainError {
    DomainError::new(
        StableCode::Internal,
        "database_cancellation_watchdog_panicked",
        RetryClass::Never,
    )
}
