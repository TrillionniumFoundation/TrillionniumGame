use std::env;
use std::fmt;
use std::net::SocketAddr;
use std::time::Duration;

use trnm_persistence_pg::DatabaseProfile;

use super::error::ServerError;

const DEFAULT_BIND: &str = "127.0.0.1:7350";
const DEFAULT_MAX_REQUEST_BYTES: usize = 128 * 1024;
const DEFAULT_READ_TIMEOUT_MS: u64 = 5_000;
const DEFAULT_WRITE_TIMEOUT_MS: u64 = 10_000;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Command {
    CheckConfig,
    Migrate,
    Serve,
}

#[derive(Clone)]
pub struct ServerConfig {
    pub bind: SocketAddr,
    pub database_url: String,
    pub database_profile: DatabaseProfile,
    pub schema_source_commit: String,
    pub admin_token: String,
    pub max_request_bytes: usize,
    pub read_timeout: Duration,
    pub write_timeout: Duration,
}

impl fmt::Debug for ServerConfig {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ServerConfig")
            .field("bind", &self.bind)
            .field("database_url", &"<redacted>")
            .field("database_profile", &self.database_profile)
            .field("schema_source_commit", &self.schema_source_commit)
            .field("admin_token", &"<redacted>")
            .field("max_request_bytes", &self.max_request_bytes)
            .field("read_timeout", &self.read_timeout)
            .field("write_timeout", &self.write_timeout)
            .finish()
    }
}

impl ServerConfig {
    pub fn from_environment(arguments: &[String]) -> Result<(Command, Self), ServerError> {
        Self::from_lookup(arguments, |name| env::var(name).ok())
    }

    fn from_lookup(
        arguments: &[String],
        lookup: impl Fn(&str) -> Option<String>,
    ) -> Result<(Command, Self), ServerError> {
        let command = match arguments {
            [_, value] if value == "check-config" => Command::CheckConfig,
            [_, value] if value == "migrate" => Command::Migrate,
            [_, value] if value == "serve" => Command::Serve,
            _ => {
                return Err(ServerError::Configuration(
                    "command_must_be_check_config_migrate_or_serve",
                ));
            }
        };

        let bind = lookup("TRNM_SERVER_BIND")
            .unwrap_or_else(|| DEFAULT_BIND.to_owned())
            .parse::<SocketAddr>()
            .map_err(|_| ServerError::Configuration("bind_address_invalid"))?;
        let allow_non_loopback = parse_bool(
            lookup("TRNM_SERVER_ALLOW_NON_LOOPBACK").as_deref(),
            false,
            "allow_non_loopback_invalid",
        )?;
        if !bind.ip().is_loopback() && !allow_non_loopback {
            return Err(ServerError::Configuration(
                "non_loopback_bind_requires_explicit_opt_in",
            ));
        }

        let database_url = required(&lookup, "TRNM_SERVER_DATABASE_URL", "database_url_missing")?;
        if database_url.len() > 4096
            || database_url
                .bytes()
                .any(|byte| byte.is_ascii_whitespace() || byte.is_ascii_control())
        {
            return Err(ServerError::Configuration("database_url_invalid"));
        }
        // The current repository adapter is deliberately NoTls. Requiring an
        // explicit acknowledgement prevents a candidate binary from silently
        // being treated as a production database transport.
        if !parse_bool(
            lookup("TRNM_SERVER_ALLOW_PLAINTEXT_DATABASE").as_deref(),
            false,
            "allow_plaintext_database_invalid",
        )? {
            return Err(ServerError::Configuration(
                "plaintext_database_requires_explicit_candidate_opt_in",
            ));
        }

        let database_profile = match required(
            &lookup,
            "TRNM_SERVER_DATABASE_PROFILE",
            "database_profile_missing",
        )?
        .as_str()
        {
            "postgresql" => DatabaseProfile::PostgreSql,
            "cockroachdb" => DatabaseProfile::CockroachDb,
            _ => return Err(ServerError::Configuration("database_profile_invalid")),
        };

        let schema_source_commit = required(
            &lookup,
            "TRNM_SERVER_SCHEMA_SOURCE_COMMIT",
            "schema_source_commit_missing",
        )?;
        if schema_source_commit.len() != 40
            || !schema_source_commit.bytes().all(is_lower_hex)
        {
            return Err(ServerError::Configuration("schema_source_commit_invalid"));
        }

        let admin_token = required(&lookup, "TRNM_SERVER_ADMIN_TOKEN", "admin_token_missing")?;
        if !(32..=512).contains(&admin_token.len())
            || !admin_token.bytes().all(is_token_byte)
        {
            return Err(ServerError::Configuration("admin_token_invalid"));
        }

        let max_request_bytes = parse_usize(
            lookup("TRNM_SERVER_MAX_REQUEST_BYTES").as_deref(),
            DEFAULT_MAX_REQUEST_BYTES,
            4096,
            1024 * 1024,
            "max_request_bytes_invalid",
        )?;
        let read_timeout_ms = parse_u64(
            lookup("TRNM_SERVER_READ_TIMEOUT_MS").as_deref(),
            DEFAULT_READ_TIMEOUT_MS,
            100,
            120_000,
            "read_timeout_invalid",
        )?;
        let write_timeout_ms = parse_u64(
            lookup("TRNM_SERVER_WRITE_TIMEOUT_MS").as_deref(),
            DEFAULT_WRITE_TIMEOUT_MS,
            100,
            120_000,
            "write_timeout_invalid",
        )?;

        Ok((
            command,
            Self {
                bind,
                database_url,
                database_profile,
                schema_source_commit,
                admin_token,
                max_request_bytes,
                read_timeout: Duration::from_millis(read_timeout_ms),
                write_timeout: Duration::from_millis(write_timeout_ms),
            },
        ))
    }
}

fn required(
    lookup: &impl Fn(&str) -> Option<String>,
    name: &str,
    reason: &'static str,
) -> Result<String, ServerError> {
    match lookup(name) {
        Some(value) if !value.is_empty() => Ok(value),
        _ => Err(ServerError::Configuration(reason)),
    }
}

fn parse_bool(
    value: Option<&str>,
    default: bool,
    reason: &'static str,
) -> Result<bool, ServerError> {
    match value {
        None => Ok(default),
        Some("1" | "true" | "TRUE" | "yes" | "YES") => Ok(true),
        Some("0" | "false" | "FALSE" | "no" | "NO") => Ok(false),
        Some(_) => Err(ServerError::Configuration(reason)),
    }
}

fn parse_usize(
    value: Option<&str>,
    default: usize,
    minimum: usize,
    maximum: usize,
    reason: &'static str,
) -> Result<usize, ServerError> {
    let value = value
        .map(str::parse::<usize>)
        .transpose()
        .map_err(|_| ServerError::Configuration(reason))?
        .unwrap_or(default);
    if !(minimum..=maximum).contains(&value) {
        return Err(ServerError::Configuration(reason));
    }
    Ok(value)
}

fn parse_u64(
    value: Option<&str>,
    default: u64,
    minimum: u64,
    maximum: u64,
    reason: &'static str,
) -> Result<u64, ServerError> {
    let value = value
        .map(str::parse::<u64>)
        .transpose()
        .map_err(|_| ServerError::Configuration(reason))?
        .unwrap_or(default);
    if !(minimum..=maximum).contains(&value) {
        return Err(ServerError::Configuration(reason));
    }
    Ok(value)
}

fn is_lower_hex(byte: u8) -> bool {
    byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase()
}

fn is_token_byte(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'~' | b'-')
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use super::*;

    fn base() -> BTreeMap<String, String> {
        BTreeMap::from([
            (
                "TRNM_SERVER_DATABASE_URL".to_owned(),
                "postgresql://trnm:secret@127.0.0.1/trnm".to_owned(),
            ),
            (
                "TRNM_SERVER_DATABASE_PROFILE".to_owned(),
                "postgresql".to_owned(),
            ),
            (
                "TRNM_SERVER_SCHEMA_SOURCE_COMMIT".to_owned(),
                "0123456789abcdef0123456789abcdef01234567".to_owned(),
            ),
            (
                "TRNM_SERVER_ADMIN_TOKEN".to_owned(),
                "a_secure_local_admin_token_123456789".to_owned(),
            ),
            (
                "TRNM_SERVER_ALLOW_PLAINTEXT_DATABASE".to_owned(),
                "1".to_owned(),
            ),
        ])
    }

    fn load(values: &BTreeMap<String, String>) -> Result<(Command, ServerConfig), ServerError> {
        ServerConfig::from_lookup(&["trnm-server".to_owned(), "serve".to_owned()], |name| {
            values.get(name).cloned()
        })
    }

    #[test]
    fn default_candidate_config_is_loopback_bounded_and_redacted() {
        let (_, config) = load(&base()).unwrap();
        assert!(config.bind.ip().is_loopback());
        assert_eq!(config.max_request_bytes, 128 * 1024);
        let debug = format!("{config:?}");
        assert!(!debug.contains("secret"));
        assert!(!debug.contains("a_secure_local"));
        assert!(debug.contains("<redacted>"));
    }

    #[test]
    fn accidental_public_bind_and_implicit_plaintext_database_fail_closed() {
        let mut values = base();
        values.insert("TRNM_SERVER_BIND".to_owned(), "0.0.0.0:7350".to_owned());
        assert!(matches!(
            load(&values),
            Err(ServerError::Configuration(
                "non_loopback_bind_requires_explicit_opt_in"
            ))
        ));

        let mut values = base();
        values.remove("TRNM_SERVER_ALLOW_PLAINTEXT_DATABASE");
        assert!(matches!(
            load(&values),
            Err(ServerError::Configuration(
                "plaintext_database_requires_explicit_candidate_opt_in"
            ))
        ));
    }

    #[test]
    fn secrets_and_source_identity_are_strictly_validated() {
        let mut values = base();
        values.insert("TRNM_SERVER_ADMIN_TOKEN".to_owned(), "short".to_owned());
        assert!(matches!(
            load(&values),
            Err(ServerError::Configuration("admin_token_invalid"))
        ));

        let mut values = base();
        values.insert(
            "TRNM_SERVER_SCHEMA_SOURCE_COMMIT".to_owned(),
            "0123456789ABCDEF0123456789ABCDEF01234567".to_owned(),
        );
        assert!(matches!(
            load(&values),
            Err(ServerError::Configuration(
                "schema_source_commit_invalid"
            ))
        ));
    }
}
