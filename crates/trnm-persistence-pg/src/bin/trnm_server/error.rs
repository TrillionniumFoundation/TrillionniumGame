use std::fmt;
use std::io;

use trnm_contracts::DomainError;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct InputError {
    reason: &'static str,
}

impl InputError {
    #[must_use]
    pub const fn new(reason: &'static str) -> Self {
        Self { reason }
    }

    #[must_use]
    pub const fn reason(self) -> &'static str {
        self.reason
    }
}

impl fmt::Display for InputError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.reason)
    }
}

impl std::error::Error for InputError {}

#[derive(Debug)]
pub enum ServerError {
    Input(InputError),
    Domain(DomainError),
    Database(postgres::Error),
    Io(io::Error),
    Configuration(&'static str),
}

impl fmt::Display for ServerError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Input(error) => write!(formatter, "invalid input: {error}"),
            Self::Domain(error) => write!(formatter, "domain failure: {}", error.code().as_str()),
            Self::Database(error) => match error.code() {
                Some(code) => write!(
                    formatter,
                    "database operation failed (SQLSTATE {})",
                    code.code()
                ),
                None => formatter.write_str("database transport operation failed"),
            },
            Self::Io(error) => write!(formatter, "I/O failure: {error}"),
            Self::Configuration(reason) => write!(formatter, "configuration failure: {reason}"),
        }
    }
}

impl std::error::Error for ServerError {}

impl From<InputError> for ServerError {
    fn from(value: InputError) -> Self {
        Self::Input(value)
    }
}

impl From<DomainError> for ServerError {
    fn from(value: DomainError) -> Self {
        Self::Domain(value)
    }
}

impl From<postgres::Error> for ServerError {
    fn from(value: postgres::Error) -> Self {
        Self::Database(value)
    }
}

impl From<io::Error> for ServerError {
    fn from(value: io::Error) -> Self {
        Self::Io(value)
    }
}

#[cfg(test)]
mod tests {
    use trnm_contracts::{RetryClass, StableCode};

    use super::*;

    #[test]
    fn process_error_display_redacts_private_domain_reason() {
        let error = ServerError::Domain(DomainError::new(
            StableCode::Internal,
            "private_database_detail",
            RetryClass::Never,
        ));
        let display = error.to_string();
        assert_eq!(display, "domain failure: internal");
        assert!(!display.contains("private_database_detail"));
    }
}
