use std::env;
use std::fmt;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::time::Duration;

use trnm_persistence_pg::{DatabaseProfile, PgPoolConfig};

use super::auth::AccessTokenVerifier;
use super::error::ServerError;

const DEFAULT_BIND: &str = "127.0.0.1:7350";
const DEFAULT_MAX_REQUEST_BYTES: usize = 128 * 1024;
const DEFAULT_READ_TIMEOUT_MS: u64 = 5_000;
const DEFAULT_WRITE_TIMEOUT_MS: u64 = 10_000;
const DEFAULT_POOL_MAX_SIZE: u64 = 8;
const DEFAULT_POOL_MIN_IDLE: u64 = 1;
const DEFAULT_POOL_ACQUIRE_TIMEOUT_MS: u64 = 2_000;
const DEFAULT_POOL_IDLE_TIMEOUT_MS: u64 = 60_000;
const DEFAULT_POOL_MAX_LIFETIME_MS: u64 = 15 * 60_000;
const DEFAULT_STATEMENT_TIMEOUT_MS: u64 = 5_000;
const DEFAULT_LOCK_TIMEOUT_MS: u64 = 1_000;
const DEFAULT_IDLE_TRANSACTION_TIMEOUT_MS: u64 = 5_000;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Command {
    CheckConfig,
    Migrate,
    Serve,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DatabaseTlsMode {
    PlaintextCandidate,
    VerifyFull,
}

#[derive(Clone)]
pub struct SessionAuthConfig {
    issuer: String,
    audience: String,
    epoch: u32,
    key: Vec<u8>,
}

impl SessionAuthConfig {
    pub fn verifier(&self) -> Result<AccessTokenVerifier, trnm_contracts::DomainError> {
        AccessTokenVerifier::from_epoch_key(
            self.issuer.clone(),
            self.audience.clone(),
            self.epoch,
            self.key.clone(),
        )
    }
}

impl fmt::Debug for SessionAuthConfig {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("SessionAuthConfig")
            .field("issuer", &self.issuer)
            .field("audience", &self.audience)
            .field("epoch", &self.epoch)
            .field("key", &"<redacted>")
            .finish()
    }
}

#[derive(Clone)]
pub struct ServerConfig {
    pub bind: SocketAddr,
    pub grpc_bind: Option<SocketAddr>,
    pub database_url: String,
    pub database_profile: DatabaseProfile,
    pub database_tls_mode: DatabaseTlsMode,
    pub database_tls_root_cert: Option<PathBuf>,
    pub database_tls_identity_cert: Option<PathBuf>,
    pub database_tls_identity_key: Option<PathBuf>,
    pub database_pool: PgPoolConfig,
    pub schema_source_commit: String,
    pub admin_token: String,
    pub session_auth: Option<SessionAuthConfig>,
    pub max_request_bytes: usize,
    pub read_timeout: Duration,
    pub write_timeout: Duration,
}

impl fmt::Debug for ServerConfig {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ServerConfig")
            .field("bind", &self.bind)
            .field("grpc_bind", &self.grpc_bind)
            .field("database_url", &"<redacted>")
            .field("database_profile", &self.database_profile)
            .field("database_tls_mode", &self.database_tls_mode)
            .field(
                "database_tls_root_cert_configured",
                &self.database_tls_root_cert.is_some(),
            )
            .field(
                "database_tls_identity_configured",
                &self.database_tls_identity_cert.is_some(),
            )
            .field("database_tls_identity_key", &"<redacted>")
            .field("database_pool", &self.database_pool)
            .field("schema_source_commit", &self.schema_source_commit)
            .field("admin_token", &"<redacted>")
            .field("session_auth", &self.session_auth)
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
        let grpc_bind = lookup("TRNM_SERVER_GRPC_BIND")
            .map(|value| {
                value
                    .parse::<SocketAddr>()
                    .map_err(|_| ServerError::Configuration("grpc_bind_address_invalid"))
            })
            .transpose()?;
        if let Some(grpc_bind) = grpc_bind {
            if !grpc_bind.ip().is_loopback() && !allow_non_loopback {
                return Err(ServerError::Configuration(
                    "grpc_non_loopback_bind_requires_explicit_opt_in",
                ));
            }
            if grpc_bind == bind {
                return Err(ServerError::Configuration("http_and_grpc_bind_must_differ"));
            }
        }

        let database_url = required(&lookup, "TRNM_SERVER_DATABASE_URL", "database_url_missing")?;
        if database_url.len() > 4096
            || database_url
                .bytes()
                .any(|byte| byte.is_ascii_whitespace() || byte.is_ascii_control())
        {
            return Err(ServerError::Configuration("database_url_invalid"));
        }

        let database_tls_mode = match lookup("TRNM_SERVER_DATABASE_TLS_MODE").as_deref() {
            None | Some("plaintext-candidate") => DatabaseTlsMode::PlaintextCandidate,
            Some("verify-full") => DatabaseTlsMode::VerifyFull,
            Some(_) => return Err(ServerError::Configuration("database_tls_mode_invalid")),
        };
        let allow_plaintext = parse_bool(
            lookup("TRNM_SERVER_ALLOW_PLAINTEXT_DATABASE").as_deref(),
            false,
            "allow_plaintext_database_invalid",
        )?;
        match database_tls_mode {
            DatabaseTlsMode::PlaintextCandidate if !allow_plaintext => {
                return Err(ServerError::Configuration(
                    "plaintext_database_requires_explicit_candidate_opt_in",
                ));
            }
            DatabaseTlsMode::VerifyFull if allow_plaintext => {
                return Err(ServerError::Configuration(
                    "tls_mode_conflicts_with_plaintext_opt_in",
                ));
            }
            _ => {}
        }

        let database_tls_root_cert =
            optional_path(lookup("TRNM_SERVER_DATABASE_TLS_ROOT_CERT_PEM"))?;
        let database_tls_identity_cert =
            optional_path(lookup("TRNM_SERVER_DATABASE_TLS_IDENTITY_CERT_PEM"))?;
        let database_tls_identity_key =
            optional_path(lookup("TRNM_SERVER_DATABASE_TLS_IDENTITY_KEY_PKCS8_PEM"))?;
        if database_tls_identity_cert.is_some() != database_tls_identity_key.is_some() {
            return Err(ServerError::Configuration(
                "database_tls_identity_cert_key_pair_required",
            ));
        }
        if database_tls_mode == DatabaseTlsMode::PlaintextCandidate
            && (database_tls_root_cert.is_some()
                || database_tls_identity_cert.is_some()
                || database_tls_identity_key.is_some())
        {
            return Err(ServerError::Configuration(
                "database_tls_material_requires_verify_full",
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
        if schema_source_commit.len() != 40 || !schema_source_commit.bytes().all(is_lower_hex) {
            return Err(ServerError::Configuration("schema_source_commit_invalid"));
        }

        let admin_token = required(&lookup, "TRNM_SERVER_ADMIN_TOKEN", "admin_token_missing")?;
        if !(32..=512).contains(&admin_token.len()) || !admin_token.bytes().all(is_token_byte) {
            return Err(ServerError::Configuration("admin_token_invalid"));
        }
        let session_auth = parse_session_auth(&lookup)?;

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

        let pool_max_size = parse_u64(
            lookup("TRNM_SERVER_DATABASE_POOL_MAX_SIZE").as_deref(),
            DEFAULT_POOL_MAX_SIZE,
            1,
            256,
            "database_pool_max_size_invalid",
        )?;
        let pool_min_idle = parse_u64(
            lookup("TRNM_SERVER_DATABASE_POOL_MIN_IDLE").as_deref(),
            DEFAULT_POOL_MIN_IDLE,
            0,
            pool_max_size,
            "database_pool_min_idle_invalid",
        )?;
        let pool_acquire_timeout_ms = parse_u64(
            lookup("TRNM_SERVER_DATABASE_POOL_ACQUIRE_TIMEOUT_MS").as_deref(),
            DEFAULT_POOL_ACQUIRE_TIMEOUT_MS,
            10,
            120_000,
            "database_pool_acquire_timeout_invalid",
        )?;
        let pool_idle_timeout_ms = parse_u64(
            lookup("TRNM_SERVER_DATABASE_POOL_IDLE_TIMEOUT_MS").as_deref(),
            DEFAULT_POOL_IDLE_TIMEOUT_MS,
            1_000,
            3_600_000,
            "database_pool_idle_timeout_invalid",
        )?;
        let pool_max_lifetime_ms = parse_u64(
            lookup("TRNM_SERVER_DATABASE_POOL_MAX_LIFETIME_MS").as_deref(),
            DEFAULT_POOL_MAX_LIFETIME_MS,
            pool_idle_timeout_ms,
            24 * 3_600_000,
            "database_pool_max_lifetime_invalid",
        )?;
        let statement_timeout_ms = parse_u64(
            lookup("TRNM_SERVER_DATABASE_STATEMENT_TIMEOUT_MS").as_deref(),
            DEFAULT_STATEMENT_TIMEOUT_MS,
            50,
            600_000,
            "database_statement_timeout_invalid",
        )?;
        let lock_timeout_ms = parse_u64(
            lookup("TRNM_SERVER_DATABASE_LOCK_TIMEOUT_MS").as_deref(),
            DEFAULT_LOCK_TIMEOUT_MS,
            10,
            statement_timeout_ms,
            "database_lock_timeout_invalid",
        )?;
        let idle_transaction_timeout_ms = parse_u64(
            lookup("TRNM_SERVER_DATABASE_IDLE_TRANSACTION_TIMEOUT_MS").as_deref(),
            DEFAULT_IDLE_TRANSACTION_TIMEOUT_MS,
            50,
            600_000,
            "database_idle_transaction_timeout_invalid",
        )?;
        let database_pool = PgPoolConfig {
            max_size: u32::try_from(pool_max_size)
                .map_err(|_| ServerError::Configuration("database_pool_max_size_invalid"))?,
            min_idle: u32::try_from(pool_min_idle)
                .map_err(|_| ServerError::Configuration("database_pool_min_idle_invalid"))?,
            acquire_timeout: Duration::from_millis(pool_acquire_timeout_ms),
            idle_timeout: Duration::from_millis(pool_idle_timeout_ms),
            max_lifetime: Duration::from_millis(pool_max_lifetime_ms),
            statement_timeout: Duration::from_millis(statement_timeout_ms),
            lock_timeout: Duration::from_millis(lock_timeout_ms),
            idle_transaction_timeout: Duration::from_millis(idle_transaction_timeout_ms),
        }
        .validate()?;

        Ok((
            command,
            Self {
                bind,
                grpc_bind,
                database_url,
                database_profile,
                database_tls_mode,
                database_tls_root_cert,
                database_tls_identity_cert,
                database_tls_identity_key,
                database_pool,
                schema_source_commit,
                admin_token,
                session_auth,
                max_request_bytes,
                read_timeout: Duration::from_millis(read_timeout_ms),
                write_timeout: Duration::from_millis(write_timeout_ms),
            },
        ))
    }
}

fn parse_session_auth(
    lookup: &impl Fn(&str) -> Option<String>,
) -> Result<Option<SessionAuthConfig>, ServerError> {
    let enabled = parse_bool(
        lookup("TRNM_SERVER_SESSION_AUTH_ENABLED").as_deref(),
        false,
        "session_auth_enabled_invalid",
    )?;
    let names = [
        "TRNM_SERVER_SESSION_AUTH_ISSUER",
        "TRNM_SERVER_SESSION_AUTH_AUDIENCE",
        "TRNM_SERVER_SESSION_AUTH_EPOCH",
        "TRNM_SERVER_SESSION_AUTH_KEY_HEX",
    ];
    let material_present = names.iter().any(|name| lookup(name).is_some());
    if !enabled {
        if material_present {
            return Err(ServerError::Configuration(
                "session_auth_material_requires_enablement",
            ));
        }
        return Ok(None);
    }

    let issuer = required(
        lookup,
        "TRNM_SERVER_SESSION_AUTH_ISSUER",
        "session_auth_issuer_missing",
    )?;
    let audience = required(
        lookup,
        "TRNM_SERVER_SESSION_AUTH_AUDIENCE",
        "session_auth_audience_missing",
    )?;
    if !valid_session_profile_text(&issuer) || !valid_session_profile_text(&audience) {
        return Err(ServerError::Configuration("session_auth_profile_invalid"));
    }
    let epoch = u32::try_from(parse_u64(
        lookup("TRNM_SERVER_SESSION_AUTH_EPOCH").as_deref(),
        0,
        1,
        u64::from(u32::MAX),
        "session_auth_epoch_invalid",
    )?)
    .map_err(|_| ServerError::Configuration("session_auth_epoch_invalid"))?;
    let key_hex = required(
        lookup,
        "TRNM_SERVER_SESSION_AUTH_KEY_HEX",
        "session_auth_key_missing",
    )?;
    let config = SessionAuthConfig {
        issuer,
        audience,
        epoch,
        key: decode_session_key(&key_hex)?,
    };
    config.verifier()?;
    Ok(Some(config))
}

fn valid_session_profile_text(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 512
        && !value
            .bytes()
            .any(|byte| byte.is_ascii_whitespace() || byte.is_ascii_control())
}

fn decode_session_key(value: &str) -> Result<Vec<u8>, ServerError> {
    if value.len() != 64 || !value.bytes().all(is_lower_hex) {
        return Err(ServerError::Configuration("session_auth_key_invalid"));
    }
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| Ok((hex_nibble(pair[0])? << 4) | hex_nibble(pair[1])?))
        .collect()
}

fn hex_nibble(value: u8) -> Result<u8, ServerError> {
    match value {
        b'0'..=b'9' => Ok(value - b'0'),
        b'a'..=b'f' => Ok(value - b'a' + 10),
        _ => Err(ServerError::Configuration("session_auth_key_invalid")),
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

fn optional_path(value: Option<String>) -> Result<Option<PathBuf>, ServerError> {
    match value {
        None => Ok(None),
        Some(value)
            if !value.is_empty()
                && value.len() <= 4096
                && !value.bytes().any(|byte| byte.is_ascii_control()) =>
        {
            Ok(Some(PathBuf::from(value)))
        }
        Some(_) => Err(ServerError::Configuration("database_tls_path_invalid")),
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
        assert!(config.grpc_bind.is_none());
        assert_eq!(config.max_request_bytes, 128 * 1024);
        assert_eq!(config.database_pool.max_size, 8);
        assert_eq!(config.database_pool.min_idle, 1);
        assert_eq!(
            config.database_tls_mode,
            DatabaseTlsMode::PlaintextCandidate
        );
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
    fn grpc_bind_is_optional_distinct_and_public_bind_requires_opt_in() {
        let mut values = base();
        values.insert(
            "TRNM_SERVER_GRPC_BIND".to_owned(),
            "127.0.0.1:7351".to_owned(),
        );
        let (_, config) = load(&values).unwrap();
        assert_eq!(
            config.grpc_bind,
            Some("127.0.0.1:7351".parse::<SocketAddr>().unwrap())
        );

        values.insert(
            "TRNM_SERVER_GRPC_BIND".to_owned(),
            "127.0.0.1:7350".to_owned(),
        );
        assert!(matches!(
            load(&values),
            Err(ServerError::Configuration("http_and_grpc_bind_must_differ"))
        ));

        values.insert(
            "TRNM_SERVER_GRPC_BIND".to_owned(),
            "0.0.0.0:7351".to_owned(),
        );
        assert!(matches!(
            load(&values),
            Err(ServerError::Configuration(
                "grpc_non_loopback_bind_requires_explicit_opt_in"
            ))
        ));
        values.insert("TRNM_SERVER_ALLOW_NON_LOOPBACK".to_owned(), "1".to_owned());
        assert!(load(&values).is_ok());
    }

    #[test]
    fn verify_full_tls_is_secure_by_default_and_material_is_paired() {
        let mut values = base();
        values.remove("TRNM_SERVER_ALLOW_PLAINTEXT_DATABASE");
        values.insert(
            "TRNM_SERVER_DATABASE_TLS_MODE".to_owned(),
            "verify-full".to_owned(),
        );
        let (_, config) = load(&values).unwrap();
        assert_eq!(config.database_tls_mode, DatabaseTlsMode::VerifyFull);

        values.insert(
            "TRNM_SERVER_DATABASE_TLS_IDENTITY_CERT_PEM".to_owned(),
            "/run/secrets/client-cert.pem".to_owned(),
        );
        assert!(matches!(
            load(&values),
            Err(ServerError::Configuration(
                "database_tls_identity_cert_key_pair_required"
            ))
        ));
    }

    #[test]
    fn pool_and_timeout_bounds_fail_closed() {
        let mut values = base();
        values.insert(
            "TRNM_SERVER_DATABASE_POOL_MAX_SIZE".to_owned(),
            "0".to_owned(),
        );
        assert!(matches!(
            load(&values),
            Err(ServerError::Configuration("database_pool_max_size_invalid"))
        ));

        let mut values = base();
        values.insert(
            "TRNM_SERVER_DATABASE_LOCK_TIMEOUT_MS".to_owned(),
            "6000".to_owned(),
        );
        assert!(matches!(
            load(&values),
            Err(ServerError::Configuration("database_lock_timeout_invalid"))
        ));
    }

    #[test]
    fn session_auth_is_explicit_bounded_and_redacted() {
        let mut values = base();
        values.insert(
            "TRNM_SERVER_SESSION_AUTH_ISSUER".to_owned(),
            "https://identity.test".to_owned(),
        );
        assert!(matches!(
            load(&values),
            Err(ServerError::Configuration(
                "session_auth_material_requires_enablement"
            ))
        ));

        values.insert(
            "TRNM_SERVER_SESSION_AUTH_ENABLED".to_owned(),
            "1".to_owned(),
        );
        values.insert(
            "TRNM_SERVER_SESSION_AUTH_AUDIENCE".to_owned(),
            "trillionnium-game".to_owned(),
        );
        values.insert("TRNM_SERVER_SESSION_AUTH_EPOCH".to_owned(), "7".to_owned());
        let key = "30".repeat(32);
        values.insert("TRNM_SERVER_SESSION_AUTH_KEY_HEX".to_owned(), key.clone());
        let (_, config) = load(&values).unwrap();
        let session = config.session_auth.as_ref().unwrap();
        assert!(session.verifier().is_ok());
        let debug = format!("{config:?}");
        assert!(debug.contains("SessionAuthConfig"));
        assert!(debug.contains("<redacted>"));
        assert!(!debug.contains(&key));
    }

    #[test]
    fn session_auth_rejects_partial_or_noncanonical_key_material() {
        let mut values = base();
        values.extend([
            (
                "TRNM_SERVER_SESSION_AUTH_ENABLED".to_owned(),
                "1".to_owned(),
            ),
            (
                "TRNM_SERVER_SESSION_AUTH_ISSUER".to_owned(),
                "https://identity.test".to_owned(),
            ),
            (
                "TRNM_SERVER_SESSION_AUTH_AUDIENCE".to_owned(),
                "trillionnium-game".to_owned(),
            ),
            ("TRNM_SERVER_SESSION_AUTH_EPOCH".to_owned(), "7".to_owned()),
        ]);
        assert!(matches!(
            load(&values),
            Err(ServerError::Configuration("session_auth_key_missing"))
        ));
        values.insert(
            "TRNM_SERVER_SESSION_AUTH_KEY_HEX".to_owned(),
            "AA".repeat(32),
        );
        assert!(matches!(
            load(&values),
            Err(ServerError::Configuration("session_auth_key_invalid"))
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
            Err(ServerError::Configuration("schema_source_commit_invalid"))
        ));
    }
}
