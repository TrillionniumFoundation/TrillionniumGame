use std::env;
use std::fmt;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::time::Duration;

use trnm_persistence_pg::{AuthoritativeSchemaTarget, DatabaseProfile, PgPoolConfig};

use super::auth::AccessTokenVerifier;
use super::error::ServerError;
use super::legacy_config::LegacyServerAuthConfig;

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

/// Exactly one selected credential authority; no token sniffing or fallback.
#[derive(Clone)]
pub enum AuthAuthorityConfig {
    Disabled,
    DurableFamily(SessionAuthConfig),
    NakamaLegacy(LegacyServerAuthConfig),
}

impl fmt::Debug for AuthAuthorityConfig {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Disabled => formatter.write_str("Disabled"),
            Self::DurableFamily(config) => formatter
                .debug_tuple("DurableFamily")
                .field(config)
                .finish(),
            Self::NakamaLegacy(config) => {
                formatter.debug_tuple("NakamaLegacy").field(config).finish()
            }
        }
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
    pub schema_target: AuthoritativeSchemaTarget,
    pub admin_token: String,
    pub auth_authority: AuthAuthorityConfig,
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
            .field("schema_target", &self.schema_target)
            .field("admin_token", &"<redacted>")
            .field("auth_authority", &self.auth_authority)
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

    pub(super) fn from_lookup(
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

        let schema_target = parse_schema_target(lookup("TRNM_SERVER_SCHEMA_TARGET").as_deref())?;
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
        let auth_authority = parse_auth_authority(&lookup, schema_target)?;

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
                schema_target,
                admin_token,
                auth_authority,
                max_request_bytes,
                read_timeout: Duration::from_millis(read_timeout_ms),
                write_timeout: Duration::from_millis(write_timeout_ms),
            },
        ))
    }
}

fn parse_schema_target(value: Option<&str>) -> Result<AuthoritativeSchemaTarget, ServerError> {
    match value {
        None | Some("storage-v4") => Ok(AuthoritativeSchemaTarget::StorageV4),
        Some("nakama-accounts-v5") => Ok(AuthoritativeSchemaTarget::NakamaAccountsV5),
        _ => Err(ServerError::Configuration("schema_target_invalid")),
    }
}

const DURABLE_AUTH_MATERIAL_NAMES: [&str; 4] = [
    "TRNM_SERVER_SESSION_AUTH_ISSUER",
    "TRNM_SERVER_SESSION_AUTH_AUDIENCE",
    "TRNM_SERVER_SESSION_AUTH_EPOCH",
    "TRNM_SERVER_SESSION_AUTH_KEY_HEX",
];
const LEGACY_AUTH_MATERIAL_NAMES: [&str; 6] = [
    "TRNM_SERVER_LEGACY_SERVER_KEY",
    "TRNM_SERVER_LEGACY_ACCESS_KEY",
    "TRNM_SERVER_LEGACY_REFRESH_KEY",
    "TRNM_SERVER_LEGACY_ACCESS_TTL_SECONDS",
    "TRNM_SERVER_LEGACY_REFRESH_TTL_SECONDS",
    "TRNM_SERVER_LEGACY_SINGLE_SESSION",
];

fn parse_auth_authority(
    lookup: &impl Fn(&str) -> Option<String>,
    schema_target: AuthoritativeSchemaTarget,
) -> Result<AuthAuthorityConfig, ServerError> {
    let mode = lookup("TRNM_SERVER_AUTH_MODE");
    let durable_present = DURABLE_AUTH_MATERIAL_NAMES
        .iter()
        .any(|name| lookup(name).is_some());
    let legacy_present = LEGACY_AUTH_MATERIAL_NAMES
        .iter()
        .any(|name| lookup(name).is_some());
    let old_enabled = lookup("TRNM_SERVER_SESSION_AUTH_ENABLED");
    match mode.as_deref() {
        None => {
            if legacy_present {
                return Err(ServerError::Configuration(
                    "legacy_auth_material_requires_explicit_mode",
                ));
            }
            // Preserve the original enablement, missing-field and key errors.
            Ok(match parse_session_auth(lookup)? {
                Some(config) => AuthAuthorityConfig::DurableFamily(config),
                None => AuthAuthorityConfig::Disabled,
            })
        }
        Some("disabled") => {
            let enabled = parse_bool(
                old_enabled.as_deref(),
                false,
                "session_auth_enabled_invalid",
            )?;
            if enabled || durable_present || legacy_present {
                return Err(ServerError::Configuration("auth_mode_material_conflict"));
            }
            Ok(AuthAuthorityConfig::Disabled)
        }
        Some("durable-family") => {
            if legacy_present {
                return Err(ServerError::Configuration("auth_mode_material_conflict"));
            }
            if old_enabled.is_some()
                && !parse_bool(
                    old_enabled.as_deref(),
                    false,
                    "session_auth_enabled_invalid",
                )?
            {
                return Err(ServerError::Configuration("auth_mode_enablement_conflict"));
            }
            let selected = |name: &str| {
                if name == "TRNM_SERVER_SESSION_AUTH_ENABLED" {
                    Some("true".to_owned())
                } else {
                    lookup(name)
                }
            };
            match parse_session_auth(&selected)? {
                Some(config) => Ok(AuthAuthorityConfig::DurableFamily(config)),
                None => Err(ServerError::Configuration("auth_mode_enablement_conflict")),
            }
        }
        Some("nakama-legacy") => {
            // A leftover durable enablement flag is itself mixed configuration,
            // including false. Do not silently discard another profile's inputs.
            if durable_present || old_enabled.is_some() {
                return Err(ServerError::Configuration("auth_mode_material_conflict"));
            }
            if schema_target != AuthoritativeSchemaTarget::NakamaAccountsV5
                || lookup("TRNM_SERVER_SCHEMA_TARGET").as_deref() != Some("nakama-accounts-v5")
            {
                return Err(ServerError::Configuration(
                    "legacy_auth_requires_explicit_accounts_v5_target",
                ));
            }
            let server_key = required(
                lookup,
                "TRNM_SERVER_LEGACY_SERVER_KEY",
                "legacy_server_key_missing",
            )?;
            let access_key = required(
                lookup,
                "TRNM_SERVER_LEGACY_ACCESS_KEY",
                "legacy_access_key_missing",
            )?;
            let refresh_key = required(
                lookup,
                "TRNM_SERVER_LEGACY_REFRESH_KEY",
                "legacy_refresh_key_missing",
            )?;
            let access_ttl = parse_legacy_ttl(
                required(
                    lookup,
                    "TRNM_SERVER_LEGACY_ACCESS_TTL_SECONDS",
                    "legacy_access_ttl_missing",
                )?,
                "legacy_access_ttl_invalid",
            )?;
            let refresh_ttl = parse_legacy_ttl(
                required(
                    lookup,
                    "TRNM_SERVER_LEGACY_REFRESH_TTL_SECONDS",
                    "legacy_refresh_ttl_missing",
                )?,
                "legacy_refresh_ttl_invalid",
            )?;
            let single_session = parse_bool(
                lookup("TRNM_SERVER_LEGACY_SINGLE_SESSION").as_deref(),
                false,
                "legacy_single_session_invalid",
            )?;
            Ok(AuthAuthorityConfig::NakamaLegacy(
                LegacyServerAuthConfig::new(
                    server_key.into_bytes(),
                    access_key.into_bytes(),
                    refresh_key.into_bytes(),
                    access_ttl,
                    refresh_ttl,
                    single_session,
                )?,
            ))
        }
        Some(_) => Err(ServerError::Configuration("auth_mode_invalid")),
    }
}

fn parse_legacy_ttl(value: String, reason: &'static str) -> Result<i64, ServerError> {
    let value = value
        .parse::<i64>()
        .map_err(|_| ServerError::Configuration(reason))?;
    if value <= 0 {
        return Err(ServerError::Configuration(reason));
    }
    // Duration-overflow checks remain owned by the actual LegacyAuthPolicy.
    Ok(value)
}

fn parse_session_auth(
    lookup: &impl Fn(&str) -> Option<String>,
) -> Result<Option<SessionAuthConfig>, ServerError> {
    let enabled = parse_bool(
        lookup("TRNM_SERVER_SESSION_AUTH_ENABLED").as_deref(),
        false,
        "session_auth_enabled_invalid",
    )?;
    let material_present = DURABLE_AUTH_MATERIAL_NAMES
        .iter()
        .any(|name| lookup(name).is_some());
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
    fn schema_target_syntax_is_closed_and_defaults_to_storage_four() {
        let (_, default) = load(&base()).unwrap();
        assert_eq!(default.schema_target, AuthoritativeSchemaTarget::StorageV4);
        for (literal, expected) in [
            ("storage-v4", AuthoritativeSchemaTarget::StorageV4),
            (
                "nakama-accounts-v5",
                AuthoritativeSchemaTarget::NakamaAccountsV5,
            ),
        ] {
            let mut values = base();
            values.insert("TRNM_SERVER_SCHEMA_TARGET".to_owned(), literal.to_owned());
            // check-config parses syntax only, even though serving gate5 is closed.
            let (command, config) = ServerConfig::from_lookup(
                &["trnm-server".to_owned(), "check-config".to_owned()],
                |name| values.get(name).cloned(),
            )
            .unwrap();
            assert_eq!(command, Command::CheckConfig);
            assert_eq!(config.schema_target, expected);
        }
        for literal in [
            "",
            "4",
            "5",
            "6",
            ">=4",
            "StorageV4",
            "nakama-accounts-v6",
            "storage-v4 ",
        ] {
            let mut values = base();
            values.insert("TRNM_SERVER_SCHEMA_TARGET".to_owned(), literal.to_owned());
            assert!(matches!(
                load(&values),
                Err(ServerError::Configuration("schema_target_invalid"))
            ));
        }
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
        let AuthAuthorityConfig::DurableFamily(session) = &config.auth_authority else {
            panic!("complete enabled durable profile must select durable authority");
        };
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

    fn durable_values() -> BTreeMap<String, String> {
        let mut values = base();
        values.extend([
            (
                "TRNM_SERVER_SESSION_AUTH_ENABLED".to_owned(),
                "true".to_owned(),
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
            (
                "TRNM_SERVER_SESSION_AUTH_KEY_HEX".to_owned(),
                "30".repeat(32),
            ),
        ]);
        values
    }

    fn legacy_values() -> BTreeMap<String, String> {
        let mut values = base();
        values.extend([
            (
                "TRNM_SERVER_AUTH_MODE".to_owned(),
                "nakama-legacy".to_owned(),
            ),
            (
                "TRNM_SERVER_SCHEMA_TARGET".to_owned(),
                "nakama-accounts-v5".to_owned(),
            ),
            (
                "TRNM_SERVER_LEGACY_SERVER_KEY".to_owned(),
                "deliberate-server-key".to_owned(),
            ),
            (
                "TRNM_SERVER_LEGACY_ACCESS_KEY".to_owned(),
                "defaultencryptionkey".to_owned(),
            ),
            (
                "TRNM_SERVER_LEGACY_REFRESH_KEY".to_owned(),
                "defaultrefreshencryptionkey".to_owned(),
            ),
            (
                "TRNM_SERVER_LEGACY_ACCESS_TTL_SECONDS".to_owned(),
                "60".to_owned(),
            ),
            (
                "TRNM_SERVER_LEGACY_REFRESH_TTL_SECONDS".to_owned(),
                "3600".to_owned(),
            ),
        ]);
        values
    }

    #[test]
    fn authority_modes_are_closed_and_default_is_disabled() {
        assert!(matches!(
            load(&base()).unwrap().1.auth_authority,
            AuthAuthorityConfig::Disabled
        ));
        let mut values = base();
        values.insert("TRNM_SERVER_AUTH_MODE".to_owned(), "disabled".to_owned());
        assert!(matches!(
            load(&values).unwrap().1.auth_authority,
            AuthAuthorityConfig::Disabled
        ));
        for mode in [
            "",
            "legacy",
            "durable",
            "NakamaLegacy",
            "nakama-legacy ",
            "disabled\n",
            "auto",
        ] {
            values.insert("TRNM_SERVER_AUTH_MODE".to_owned(), mode.to_owned());
            assert!(matches!(
                load(&values),
                Err(ServerError::Configuration("auth_mode_invalid"))
            ));
        }
    }

    #[test]
    fn durable_mode_preserves_old_enablement_and_never_falls_back() {
        let old = durable_values();
        assert!(matches!(
            load(&old).unwrap().1.auth_authority,
            AuthAuthorityConfig::DurableFamily(_)
        ));
        let mut explicit = old.clone();
        explicit.insert(
            "TRNM_SERVER_AUTH_MODE".to_owned(),
            "durable-family".to_owned(),
        );
        for enabled in [Some("true"), None] {
            match enabled {
                Some(value) => {
                    explicit.insert(
                        "TRNM_SERVER_SESSION_AUTH_ENABLED".to_owned(),
                        value.to_owned(),
                    );
                }
                None => {
                    explicit.remove("TRNM_SERVER_SESSION_AUTH_ENABLED");
                }
            }
            let (_, config) = load(&explicit).unwrap();
            let AuthAuthorityConfig::DurableFamily(auth) = config.auth_authority else {
                panic!("wrong authority");
            };
            assert!(auth.verifier().is_ok());
        }
        explicit.insert(
            "TRNM_SERVER_SESSION_AUTH_ENABLED".to_owned(),
            "false".to_owned(),
        );
        assert!(matches!(
            load(&explicit),
            Err(ServerError::Configuration("auth_mode_enablement_conflict"))
        ));
        explicit.insert(
            "TRNM_SERVER_SESSION_AUTH_ENABLED".to_owned(),
            "unknown".to_owned(),
        );
        assert!(matches!(
            load(&explicit),
            Err(ServerError::Configuration("session_auth_enabled_invalid"))
        ));
        let mut old_without_enabled = old;
        old_without_enabled.remove("TRNM_SERVER_SESSION_AUTH_ENABLED");
        assert!(matches!(
            load(&old_without_enabled),
            Err(ServerError::Configuration(
                "session_auth_material_requires_enablement"
            ))
        ));
    }

    #[test]
    fn durable_mode_rejects_each_missing_profile_field() {
        for (name, error) in [
            (
                "TRNM_SERVER_SESSION_AUTH_ISSUER",
                "session_auth_issuer_missing",
            ),
            (
                "TRNM_SERVER_SESSION_AUTH_AUDIENCE",
                "session_auth_audience_missing",
            ),
            (
                "TRNM_SERVER_SESSION_AUTH_EPOCH",
                "session_auth_epoch_invalid",
            ),
            (
                "TRNM_SERVER_SESSION_AUTH_KEY_HEX",
                "session_auth_key_missing",
            ),
        ] {
            let mut values = durable_values();
            values.insert(
                "TRNM_SERVER_AUTH_MODE".to_owned(),
                "durable-family".to_owned(),
            );
            values.remove(name);
            assert!(
                matches!(load(&values), Err(ServerError::Configuration(reason)) if reason == error)
            );
        }
    }

    #[test]
    fn authority_profiles_reject_every_mixed_material_field() {
        for name in LEGACY_AUTH_MATERIAL_NAMES {
            let mut values = durable_values();
            values.insert(
                "TRNM_SERVER_AUTH_MODE".to_owned(),
                "durable-family".to_owned(),
            );
            values.insert(name.to_owned(), String::new());
            assert!(matches!(
                load(&values),
                Err(ServerError::Configuration("auth_mode_material_conflict"))
            ));
        }
        for name in DURABLE_AUTH_MATERIAL_NAMES
            .into_iter()
            .chain(["TRNM_SERVER_SESSION_AUTH_ENABLED"])
        {
            for value in ["", "false", "any-leftover-material"] {
                let mut values = legacy_values();
                values.insert(name.to_owned(), value.to_owned());
                assert!(matches!(
                    load(&values),
                    Err(ServerError::Configuration("auth_mode_material_conflict"))
                ));
            }
        }
        for name in DURABLE_AUTH_MATERIAL_NAMES
            .into_iter()
            .chain(LEGACY_AUTH_MATERIAL_NAMES)
        {
            let mut values = base();
            values.insert("TRNM_SERVER_AUTH_MODE".to_owned(), "disabled".to_owned());
            values.insert(name.to_owned(), String::new());
            assert!(matches!(
                load(&values),
                Err(ServerError::Configuration("auth_mode_material_conflict"))
            ));
        }
        let mut values = base();
        values.insert("TRNM_SERVER_AUTH_MODE".to_owned(), "disabled".to_owned());
        values.insert(
            "TRNM_SERVER_SESSION_AUTH_ENABLED".to_owned(),
            "true".to_owned(),
        );
        assert!(matches!(
            load(&values),
            Err(ServerError::Configuration("auth_mode_material_conflict"))
        ));
    }

    #[test]
    fn legacy_material_requires_explicit_authority_mode() {
        for name in LEGACY_AUTH_MATERIAL_NAMES {
            let mut values = base();
            values.insert(name.to_owned(), String::new());
            assert!(matches!(
                load(&values),
                Err(ServerError::Configuration(
                    "legacy_auth_material_requires_explicit_mode"
                ))
            ));
        }
        let mut values = legacy_values();
        values.remove("TRNM_SERVER_AUTH_MODE");
        assert!(matches!(
            load(&values),
            Err(ServerError::Configuration(
                "legacy_auth_material_requires_explicit_mode"
            ))
        ));
    }

    #[test]
    fn legacy_mode_requires_explicit_accounts_five_target() {
        for target in [None, Some("storage-v4")] {
            let mut values = legacy_values();
            match target {
                None => {
                    values.remove("TRNM_SERVER_SCHEMA_TARGET");
                }
                Some(value) => {
                    values.insert("TRNM_SERVER_SCHEMA_TARGET".to_owned(), value.to_owned());
                }
            }
            assert!(matches!(
                load(&values),
                Err(ServerError::Configuration(
                    "legacy_auth_requires_explicit_accounts_v5_target"
                ))
            ));
        }
        let mut values = legacy_values();
        values.insert(
            "TRNM_SERVER_SCHEMA_TARGET".to_owned(),
            "nakama-accounts-v6".to_owned(),
        );
        assert!(matches!(
            load(&values),
            Err(ServerError::Configuration("schema_target_invalid"))
        ));
    }

    #[test]
    fn legacy_mode_requires_all_five_operator_key_and_ttl_values() {
        for (name, error) in [
            ("TRNM_SERVER_LEGACY_SERVER_KEY", "legacy_server_key_missing"),
            ("TRNM_SERVER_LEGACY_ACCESS_KEY", "legacy_access_key_missing"),
            (
                "TRNM_SERVER_LEGACY_REFRESH_KEY",
                "legacy_refresh_key_missing",
            ),
            (
                "TRNM_SERVER_LEGACY_ACCESS_TTL_SECONDS",
                "legacy_access_ttl_missing",
            ),
            (
                "TRNM_SERVER_LEGACY_REFRESH_TTL_SECONDS",
                "legacy_refresh_ttl_missing",
            ),
        ] {
            for absent in [false, true] {
                let mut values = legacy_values();
                if absent {
                    values.remove(name);
                } else {
                    values.insert(name.to_owned(), String::new());
                }
                assert!(
                    matches!(load(&values), Err(ServerError::Configuration(reason)) if reason == error)
                );
            }
        }
    }

    #[test]
    fn legacy_explicit_twenty_and_twenty_seven_byte_keys_are_redacted() {
        let mut values = legacy_values();
        let server_key = "操作员🙂 explicit key";
        values.insert(
            "TRNM_SERVER_LEGACY_SERVER_KEY".to_owned(),
            server_key.to_owned(),
        );
        let (_, config) = load(&values).unwrap();
        let AuthAuthorityConfig::NakamaLegacy(legacy) = &config.auth_authority else {
            panic!("wrong authority");
        };
        assert_eq!(values["TRNM_SERVER_LEGACY_ACCESS_KEY"].len(), 20);
        assert_eq!(values["TRNM_SERVER_LEGACY_REFRESH_KEY"].len(), 27);
        assert!(legacy.service_config().is_ok());
        use base64::Engine;
        let credential = base64::engine::general_purpose::STANDARD
            .encode(format!("{server_key}:ignored").as_bytes());
        assert!(super::super::legacy_http_api::require_basic_server_key(
            &legacy.server_key().unwrap(),
            Some(&format!("Basic {credential}")),
            super::super::legacy_http_api::LegacyHttpLimits::default(),
        )
        .is_ok());
        let debug = format!("{config:?}");
        for private in [
            server_key,
            "defaultencryptionkey",
            "defaultrefreshencryptionkey",
            "secret",
            "a_secure_local",
        ] {
            assert!(!debug.contains(private));
        }
        assert!(debug.contains("NakamaLegacy"));
        assert!(debug.contains("<redacted>"));
    }

    #[test]
    fn legacy_key_limits_reuse_actual_provider_without_durable_minimum() {
        assert!(LegacyServerAuthConfig::new(
            vec![b's'; 4096],
            vec![b'a'; 4096],
            vec![b'r'; 4096],
            1,
            1,
            false
        )
        .is_ok());
        assert!(
            LegacyServerAuthConfig::new(vec![b's'], vec![b'a'], vec![b'r'], 1, 1, false).is_ok()
        );
        for oversized in [0, 1, 2] {
            let mut keys = [vec![b's'; 20], vec![b'a'; 20], vec![b'r'; 27]];
            keys[oversized] = vec![b'x'; 4097];
            let [server, access, refresh] = keys;
            assert!(
                matches!(LegacyServerAuthConfig::new(server,access,refresh,1,1,false), Err(ServerError::Configuration(reason)) if reason == if oversized == 0 { "legacy_server_key_invalid" } else { "legacy_access_refresh_keys_invalid" })
            );
        }
        assert!(matches!(
            LegacyServerAuthConfig::new(Vec::new(), vec![b'a'], vec![b'r'], 1, 1, false),
            Err(ServerError::Configuration("legacy_server_key_invalid"))
        ));
        for (access, refresh) in [
            (Vec::new(), vec![b'r']),
            (vec![b'a'], Vec::new()),
            (vec![b'x'; 20], vec![b'x'; 20]),
        ] {
            assert!(matches!(
                LegacyServerAuthConfig::new(vec![b's'], access, refresh, 1, 1, false),
                Err(ServerError::Configuration(
                    "legacy_access_refresh_keys_invalid"
                ))
            ));
        }
    }

    #[test]
    fn legacy_ttls_reject_zero_parse_overflow_and_actual_duration_overflow() {
        for (name, reason, boundary) in [
            (
                "TRNM_SERVER_LEGACY_ACCESS_TTL_SECONDS",
                "legacy_access_ttl_invalid",
                i64::MAX / 2 / 1_000_000_000,
            ),
            (
                "TRNM_SERVER_LEGACY_REFRESH_TTL_SECONDS",
                "legacy_refresh_ttl_invalid",
                i64::MAX / 1_000_000_000,
            ),
        ] {
            for invalid in ["0", "-1", "9223372036854775808", "1.5", "seconds", " 60"] {
                let mut values = legacy_values();
                values.insert(name.to_owned(), invalid.to_owned());
                assert!(
                    matches!(load(&values), Err(ServerError::Configuration(error)) if error == reason)
                );
            }
            let mut values = legacy_values();
            values.insert(name.to_owned(), boundary.to_string());
            assert!(load(&values).is_ok());
            values.insert(name.to_owned(), (boundary + 1).to_string());
            assert!(matches!(
                load(&values),
                Err(ServerError::Configuration("legacy_auth_ttl_policy_invalid"))
            ));
        }
    }

    #[test]
    fn legacy_single_session_is_bounded_boolean_with_explicit_false_default() {
        let (_, default) = load(&legacy_values()).unwrap();
        let default_debug = format!("{:?}", default.auth_authority);
        assert!(default_debug.contains("single_session: false"));
        for (literal, expected) in [("true", true), ("false", false), ("1", true), ("0", false)] {
            let mut values = legacy_values();
            values.insert(
                "TRNM_SERVER_LEGACY_SINGLE_SESSION".to_owned(),
                literal.to_owned(),
            );
            let (_, config) = load(&values).unwrap();
            assert!(format!("{:?}", config.auth_authority)
                .contains(&format!("single_session: {expected}")));
        }
        let mut values = legacy_values();
        values.insert(
            "TRNM_SERVER_LEGACY_SINGLE_SESSION".to_owned(),
            "sometimes".to_owned(),
        );
        assert!(matches!(
            load(&values),
            Err(ServerError::Configuration("legacy_single_session_invalid"))
        ));
    }

    #[test]
    fn selected_legacy_guard_is_static_and_never_uses_durable_verifier() {
        for values in [base(), durable_values()] {
            let (_, config) = load(&values).unwrap();
            assert!(
                super::super::auth::require_installed_http_authority(&config.auth_authority)
                    .is_ok()
            );
        }
        let (_, config) = load(&legacy_values()).unwrap();
        let error = super::super::auth::require_installed_http_authority(&config.auth_authority)
            .unwrap_err();
        assert!(matches!(error, ServerError::Domain(_)));
        let display = error.to_string();
        for private in [
            "secret",
            "defaultencryptionkey",
            "defaultrefreshencryptionkey",
            "postgresql://",
        ] {
            assert!(!display.contains(private));
        }
    }
}
