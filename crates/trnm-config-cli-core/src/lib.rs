#![forbid(unsafe_code)]

use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::net::SocketAddr;
use std::str::FromStr;

const MAX_NODE_NAME_BYTES: usize = 64;
const MAX_SECRET_BYTES: usize = 4096;
const MIN_SECRET_BYTES: usize = 32;
const MAX_DATABASE_URL_BYTES: usize = 4096;
const MAX_CONNECTIONS: u32 = 1_000_000;
const MIN_SHUTDOWN_GRACE_MS: u64 = 100;
const MAX_SHUTDOWN_GRACE_MS: u64 = 300_000;

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum LayerKind {
    Defaults,
    File,
    Environment,
    CommandLine,
}

impl LayerKind {
    const fn rank(self) -> u8 {
        match self {
            Self::Defaults => 0,
            Self::File => 1,
            Self::Environment => 2,
            Self::CommandLine => 3,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ConfigLayer {
    pub kind: LayerKind,
    pub values: BTreeMap<String, String>,
}

impl ConfigLayer {
    #[must_use]
    pub fn new(kind: LayerKind, values: BTreeMap<String, String>) -> Self {
        Self { kind, values }
    }

    #[must_use]
    pub fn defaults() -> Self {
        Self::new(
            LayerKind::Defaults,
            BTreeMap::from([
                ("node_name".to_owned(), "trillionnium-game".to_owned()),
                ("http_bind".to_owned(), "127.0.0.1:7350".to_owned()),
                ("grpc_bind".to_owned(), "127.0.0.1:7349".to_owned()),
                ("socket_bind".to_owned(), "127.0.0.1:7351".to_owned()),
                ("max_connections".to_owned(), "10000".to_owned()),
                ("shutdown_grace_ms".to_owned(), "30000".to_owned()),
                ("log_level".to_owned(), "info".to_owned()),
            ]),
        )
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct SecretString(String);

impl SecretString {
    fn parse(key: &'static str, value: String) -> Result<Self, ConfigError> {
        if !(MIN_SECRET_BYTES..=MAX_SECRET_BYTES).contains(&value.len()) {
            return Err(ConfigError::InvalidValue {
                key,
                reason: "secret_length_out_of_range",
            });
        }
        if value.bytes().any(|byte| byte.is_ascii_control()) {
            return Err(ConfigError::InvalidValue {
                key,
                reason: "secret_contains_control_byte",
            });
        }
        Ok(Self(value))
    }

    #[must_use]
    pub fn expose_for_adapter(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for SecretString {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("SecretString([REDACTED])")
    }
}

impl fmt::Display for SecretString {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("[REDACTED]")
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LogLevel {
    Error,
    Warn,
    Info,
    Debug,
    Trace,
}

impl LogLevel {
    fn parse(value: &str) -> Result<Self, ConfigError> {
        match value {
            "error" => Ok(Self::Error),
            "warn" => Ok(Self::Warn),
            "info" => Ok(Self::Info),
            "debug" => Ok(Self::Debug),
            "trace" => Ok(Self::Trace),
            _ => Err(ConfigError::InvalidValue {
                key: "log_level",
                reason: "unsupported_log_level",
            }),
        }
    }

    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Error => "error",
            Self::Warn => "warn",
            Self::Info => "info",
            Self::Debug => "debug",
            Self::Trace => "trace",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Config {
    pub node_name: String,
    pub database_url: SecretString,
    pub session_encryption_key: SecretString,
    pub http_bind: SocketAddr,
    pub grpc_bind: SocketAddr,
    pub socket_bind: SocketAddr,
    pub max_connections: u32,
    pub shutdown_grace_ms: u64,
    pub log_level: LogLevel,
    provenance: BTreeMap<&'static str, LayerKind>,
}

impl Config {
    pub fn load(layers: &[ConfigLayer]) -> Result<Self, ConfigError> {
        if layers.first().map(|layer| layer.kind) != Some(LayerKind::Defaults) {
            return Err(ConfigError::MissingDefaultsLayer);
        }

        let mut seen = BTreeSet::new();
        let mut previous_rank = None;
        let mut merged = BTreeMap::<String, String>::new();
        let mut provenance = BTreeMap::<&'static str, LayerKind>::new();

        for layer in layers {
            if !seen.insert(layer.kind) {
                return Err(ConfigError::DuplicateLayer(layer.kind));
            }
            if previous_rank.is_some_and(|rank| layer.kind.rank() <= rank) {
                return Err(ConfigError::LayerOrderViolation);
            }
            previous_rank = Some(layer.kind.rank());

            for (key, value) in &layer.values {
                let canonical = canonical_key(key).ok_or_else(|| ConfigError::UnknownKey(key.clone()))?;
                merged.insert(canonical.to_owned(), value.clone());
                provenance.insert(canonical, layer.kind);
            }
        }

        let node_name = take_required(&merged, "node_name")?;
        validate_node_name(&node_name)?;
        let database_url = parse_database_url(take_required(&merged, "database_url")?)?;
        let session_encryption_key = SecretString::parse(
            "session_encryption_key",
            take_required(&merged, "session_encryption_key")?,
        )?;
        let http_bind = parse_socket(&merged, "http_bind")?;
        let grpc_bind = parse_socket(&merged, "grpc_bind")?;
        let socket_bind = parse_socket(&merged, "socket_bind")?;
        let max_connections = parse_u32(&merged, "max_connections", 1, MAX_CONNECTIONS)?;
        let shutdown_grace_ms = parse_u64(
            &merged,
            "shutdown_grace_ms",
            MIN_SHUTDOWN_GRACE_MS,
            MAX_SHUTDOWN_GRACE_MS,
        )?;
        let log_level = LogLevel::parse(&take_required(&merged, "log_level")?)?;

        let distinct = BTreeSet::from([http_bind, grpc_bind, socket_bind]);
        if distinct.len() != 3 {
            return Err(ConfigError::ConflictingBindAddress);
        }

        Ok(Self {
            node_name,
            database_url,
            session_encryption_key,
            http_bind,
            grpc_bind,
            socket_bind,
            max_connections,
            shutdown_grace_ms,
            log_level,
            provenance,
        })
    }

    #[must_use]
    pub fn provenance(&self, key: &str) -> Option<LayerKind> {
        canonical_key(key).and_then(|canonical| self.provenance.get(canonical).copied())
    }

    #[must_use]
    pub fn redacted_snapshot(&self) -> BTreeMap<&'static str, String> {
        BTreeMap::from([
            ("node_name", self.node_name.clone()),
            ("database_url", "[REDACTED]".to_owned()),
            ("session_encryption_key", "[REDACTED]".to_owned()),
            ("http_bind", self.http_bind.to_string()),
            ("grpc_bind", self.grpc_bind.to_string()),
            ("socket_bind", self.socket_bind.to_string()),
            ("max_connections", self.max_connections.to_string()),
            ("shutdown_grace_ms", self.shutdown_grace_ms.to_string()),
            ("log_level", self.log_level.as_str().to_owned()),
        ])
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ConfigError {
    MissingDefaultsLayer,
    DuplicateLayer(LayerKind),
    LayerOrderViolation,
    UnknownKey(String),
    MissingRequired(&'static str),
    InvalidValue {
        key: &'static str,
        reason: &'static str,
    },
    ConflictingBindAddress,
}

impl fmt::Display for ConfigError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::MissingDefaultsLayer => formatter.write_str("missing_defaults_layer"),
            Self::DuplicateLayer(layer) => write!(formatter, "duplicate_layer:{layer:?}"),
            Self::LayerOrderViolation => formatter.write_str("layer_order_violation"),
            Self::UnknownKey(key) => write!(formatter, "unknown_key:{key}"),
            Self::MissingRequired(key) => write!(formatter, "missing_required:{key}"),
            Self::InvalidValue { key, reason } => write!(formatter, "invalid_value:{key}:{reason}"),
            Self::ConflictingBindAddress => formatter.write_str("conflicting_bind_address"),
        }
    }
}

impl std::error::Error for ConfigError {}

fn canonical_key(key: &str) -> Option<&'static str> {
    match key {
        "node_name" | "node-name" => Some("node_name"),
        "database_url" | "database-url" => Some("database_url"),
        "session_encryption_key" | "session-encryption-key" => Some("session_encryption_key"),
        "http_bind" | "http-bind" => Some("http_bind"),
        "grpc_bind" | "grpc-bind" => Some("grpc_bind"),
        "socket_bind" | "socket-bind" => Some("socket_bind"),
        "max_connections" | "max-connections" => Some("max_connections"),
        "shutdown_grace_ms" | "shutdown-grace-ms" => Some("shutdown_grace_ms"),
        "log_level" | "log-level" => Some("log_level"),
        _ => None,
    }
}

fn take_required(values: &BTreeMap<String, String>, key: &'static str) -> Result<String, ConfigError> {
    values
        .get(key)
        .cloned()
        .ok_or(ConfigError::MissingRequired(key))
}

fn validate_node_name(value: &str) -> Result<(), ConfigError> {
    if value.is_empty()
        || value.len() > MAX_NODE_NAME_BYTES
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
    {
        return Err(ConfigError::InvalidValue {
            key: "node_name",
            reason: "invalid_node_name",
        });
    }
    Ok(())
}

fn parse_database_url(value: String) -> Result<SecretString, ConfigError> {
    if value.len() > MAX_DATABASE_URL_BYTES
        || !(value.starts_with("postgres://") || value.starts_with("postgresql://"))
        || value.bytes().any(|byte| byte.is_ascii_control())
    {
        return Err(ConfigError::InvalidValue {
            key: "database_url",
            reason: "invalid_postgres_url",
        });
    }
    Ok(SecretString(value))
}

fn parse_socket(values: &BTreeMap<String, String>, key: &'static str) -> Result<SocketAddr, ConfigError> {
    let value = take_required(values, key)?;
    SocketAddr::from_str(&value).map_err(|_| ConfigError::InvalidValue {
        key,
        reason: "invalid_socket_address",
    })
}

fn parse_u32(
    values: &BTreeMap<String, String>,
    key: &'static str,
    minimum: u32,
    maximum: u32,
) -> Result<u32, ConfigError> {
    let value = take_required(values, key)?;
    let parsed = value.parse::<u32>().map_err(|_| ConfigError::InvalidValue {
        key,
        reason: "invalid_unsigned_integer",
    })?;
    if !(minimum..=maximum).contains(&parsed) {
        return Err(ConfigError::InvalidValue {
            key,
            reason: "integer_out_of_range",
        });
    }
    Ok(parsed)
}

fn parse_u64(
    values: &BTreeMap<String, String>,
    key: &'static str,
    minimum: u64,
    maximum: u64,
) -> Result<u64, ConfigError> {
    let value = take_required(values, key)?;
    let parsed = value.parse::<u64>().map_err(|_| ConfigError::InvalidValue {
        key,
        reason: "invalid_unsigned_integer",
    })?;
    if !(minimum..=maximum).contains(&parsed) {
        return Err(ConfigError::InvalidValue {
            key,
            reason: "integer_out_of_range",
        });
    }
    Ok(parsed)
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MigrationCommand {
    Up,
    Down,
    Status,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Command {
    Server,
    Migrate(MigrationCommand),
    Healthcheck,
    Version,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Invocation {
    pub command: Command,
    pub overrides: BTreeMap<String, String>,
}

impl Invocation {
    pub fn parse<I, S>(arguments: I) -> Result<Self, CliError>
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        let mut arguments = arguments.into_iter().map(Into::into).peekable();
        let command = match arguments.next().as_deref() {
            Some("server") => Command::Server,
            Some("healthcheck") => Command::Healthcheck,
            Some("version") => Command::Version,
            Some("migrate") => match arguments.next().as_deref() {
                Some("up") => Command::Migrate(MigrationCommand::Up),
                Some("down") => Command::Migrate(MigrationCommand::Down),
                Some("status") => Command::Migrate(MigrationCommand::Status),
                Some(other) => return Err(CliError::UnknownMigrationCommand(other.to_owned())),
                None => return Err(CliError::MissingMigrationCommand),
            },
            Some("--help" | "-h") => return Err(CliError::HelpRequested),
            Some(other) => return Err(CliError::UnknownCommand(other.to_owned())),
            None => return Err(CliError::MissingCommand),
        };

        let mut overrides = BTreeMap::new();
        while let Some(argument) = arguments.next() {
            if !argument.starts_with("--") {
                return Err(CliError::UnexpectedPositional(argument));
            }
            let flag = argument.trim_start_matches("--");
            let (raw_key, value) = if let Some((key, value)) = flag.split_once('=') {
                if value.is_empty() {
                    return Err(CliError::MissingFlagValue(key.to_owned()));
                }
                (key.to_owned(), value.to_owned())
            } else {
                let value = arguments
                    .next()
                    .ok_or_else(|| CliError::MissingFlagValue(flag.to_owned()))?;
                if value.starts_with("--") {
                    return Err(CliError::MissingFlagValue(flag.to_owned()));
                }
                (flag.to_owned(), value)
            };
            let key = canonical_key(&raw_key).ok_or_else(|| CliError::UnknownFlag(raw_key.clone()))?;
            if overrides.insert(key.to_owned(), value).is_some() {
                return Err(CliError::DuplicateFlag(key));
            }
        }

        if !matches!(command, Command::Server | Command::Migrate(_)) && !overrides.is_empty() {
            return Err(CliError::FlagsNotAllowed(command));
        }

        Ok(Self { command, overrides })
    }

    #[must_use]
    pub fn config_layer(&self) -> ConfigLayer {
        ConfigLayer::new(LayerKind::CommandLine, self.overrides.clone())
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CliError {
    MissingCommand,
    HelpRequested,
    UnknownCommand(String),
    MissingMigrationCommand,
    UnknownMigrationCommand(String),
    UnexpectedPositional(String),
    UnknownFlag(String),
    MissingFlagValue(String),
    DuplicateFlag(&'static str),
    FlagsNotAllowed(Command),
}

impl fmt::Display for CliError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::MissingCommand => formatter.write_str("missing_command"),
            Self::HelpRequested => formatter.write_str("help_requested"),
            Self::UnknownCommand(command) => write!(formatter, "unknown_command:{command}"),
            Self::MissingMigrationCommand => formatter.write_str("missing_migration_command"),
            Self::UnknownMigrationCommand(command) => write!(formatter, "unknown_migration_command:{command}"),
            Self::UnexpectedPositional(value) => write!(formatter, "unexpected_positional:{value}"),
            Self::UnknownFlag(flag) => write!(formatter, "unknown_flag:{flag}"),
            Self::MissingFlagValue(flag) => write!(formatter, "missing_flag_value:{flag}"),
            Self::DuplicateFlag(flag) => write!(formatter, "duplicate_flag:{flag}"),
            Self::FlagsNotAllowed(command) => write!(formatter, "flags_not_allowed:{command:?}"),
        }
    }
}

impl std::error::Error for CliError {}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Dependencies {
    pub database: bool,
    pub migrations: bool,
    pub runtime: bool,
}

impl Dependencies {
    #[must_use]
    pub const fn all_ready(self) -> bool {
        self.database && self.migrations && self.runtime
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Lifecycle {
    Starting,
    Ready,
    NotReady,
    Draining { deadline_ms: u64 },
    Stopped,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ServiceLifecycle {
    state: Lifecycle,
    dependencies: Dependencies,
    has_been_ready: bool,
    last_observed_ms: u64,
}

impl ServiceLifecycle {
    #[must_use]
    pub const fn new(initial_time_ms: u64) -> Self {
        Self {
            state: Lifecycle::Starting,
            dependencies: Dependencies {
                database: false,
                migrations: false,
                runtime: false,
            },
            has_been_ready: false,
            last_observed_ms: initial_time_ms,
        }
    }

    pub fn update_dependencies(
        &mut self,
        dependencies: Dependencies,
        observed_ms: u64,
    ) -> Result<Lifecycle, LifecycleError> {
        self.observe_time(observed_ms)?;
        if matches!(self.state, Lifecycle::Draining { .. } | Lifecycle::Stopped) {
            return Err(LifecycleError::TerminalTransition);
        }
        self.dependencies = dependencies;
        self.state = if dependencies.all_ready() {
            self.has_been_ready = true;
            Lifecycle::Ready
        } else if self.has_been_ready {
            Lifecycle::NotReady
        } else {
            Lifecycle::Starting
        };
        Ok(self.state)
    }

    pub fn begin_drain(&mut self, observed_ms: u64, grace_ms: u64) -> Result<Lifecycle, LifecycleError> {
        self.observe_time(observed_ms)?;
        if matches!(self.state, Lifecycle::Draining { .. } | Lifecycle::Stopped) {
            return Err(LifecycleError::TerminalTransition);
        }
        if !(MIN_SHUTDOWN_GRACE_MS..=MAX_SHUTDOWN_GRACE_MS).contains(&grace_ms) {
            return Err(LifecycleError::InvalidGracePeriod);
        }
        let deadline_ms = observed_ms
            .checked_add(grace_ms)
            .ok_or(LifecycleError::ClockOverflow)?;
        self.state = Lifecycle::Draining { deadline_ms };
        Ok(self.state)
    }

    pub fn tick(&mut self, observed_ms: u64) -> Result<Lifecycle, LifecycleError> {
        self.observe_time(observed_ms)?;
        if let Lifecycle::Draining { deadline_ms } = self.state {
            if observed_ms >= deadline_ms {
                self.state = Lifecycle::Stopped;
            }
        }
        Ok(self.state)
    }

    pub fn force_stop(&mut self, observed_ms: u64) -> Result<Lifecycle, LifecycleError> {
        self.observe_time(observed_ms)?;
        self.state = Lifecycle::Stopped;
        Ok(self.state)
    }

    #[must_use]
    pub const fn state(&self) -> Lifecycle {
        self.state
    }

    #[must_use]
    pub const fn dependencies(&self) -> Dependencies {
        self.dependencies
    }

    #[must_use]
    pub const fn health_live(&self) -> bool {
        !matches!(self.state, Lifecycle::Stopped)
    }

    #[must_use]
    pub const fn health_ready(&self) -> bool {
        matches!(self.state, Lifecycle::Ready)
    }

    #[must_use]
    pub const fn accepts_new_work(&self) -> bool {
        matches!(self.state, Lifecycle::Ready)
    }

    fn observe_time(&mut self, observed_ms: u64) -> Result<(), LifecycleError> {
        if observed_ms < self.last_observed_ms {
            return Err(LifecycleError::ClockRegression);
        }
        self.last_observed_ms = observed_ms;
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LifecycleError {
    ClockRegression,
    ClockOverflow,
    InvalidGracePeriod,
    TerminalTransition,
}

impl fmt::Display for LifecycleError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::ClockRegression => "clock_regression",
            Self::ClockOverflow => "clock_overflow",
            Self::InvalidGracePeriod => "invalid_grace_period",
            Self::TerminalTransition => "terminal_transition",
        })
    }
}

impl std::error::Error for LifecycleError {}

#[cfg(test)]
mod tests {
    use super::*;

    fn required_layer(kind: LayerKind, database_url: &str, secret: &str) -> ConfigLayer {
        ConfigLayer::new(
            kind,
            BTreeMap::from([
                ("database_url".to_owned(), database_url.to_owned()),
                ("session_encryption_key".to_owned(), secret.to_owned()),
            ]),
        )
    }

    fn valid_config() -> Result<Config, ConfigError> {
        Config::load(&[
            ConfigLayer::defaults(),
            required_layer(
                LayerKind::Environment,
                "postgresql://user:password@127.0.0.1/game",
                "0123456789abcdef0123456789abcdef",
            ),
        ])
    }

    #[test]
    fn precedence_and_provenance_are_deterministic() {
        let file = ConfigLayer::new(
            LayerKind::File,
            BTreeMap::from([
                ("node_name".to_owned(), "file-node".to_owned()),
                ("database_url".to_owned(), "postgresql://file/game".to_owned()),
                (
                    "session_encryption_key".to_owned(),
                    "file-secret-0123456789abcdef0123456789".to_owned(),
                ),
            ]),
        );
        let environment = ConfigLayer::new(
            LayerKind::Environment,
            BTreeMap::from([("node-name".to_owned(), "env-node".to_owned())]),
        );
        let cli = ConfigLayer::new(
            LayerKind::CommandLine,
            BTreeMap::from([("node_name".to_owned(), "cli-node".to_owned())]),
        );
        let result = Config::load(&[ConfigLayer::defaults(), file, environment, cli]);
        assert!(matches!(
            result,
            Ok(Config {
                ref node_name,
                ..
            }) if node_name == "cli-node"
        ));
        let config = result.ok();
        assert_eq!(
            config.as_ref().and_then(|value| value.provenance("node-name")),
            Some(LayerKind::CommandLine)
        );
        assert_eq!(
            config.as_ref().and_then(|value| value.provenance("database_url")),
            Some(LayerKind::File)
        );
    }

    #[test]
    fn layers_must_be_unique_and_monotonic() {
        let secret = "0123456789abcdef0123456789abcdef";
        assert_eq!(
            Config::load(&[
                ConfigLayer::defaults(),
                required_layer(LayerKind::Environment, "postgresql://db/game", secret),
                ConfigLayer::new(LayerKind::File, BTreeMap::new()),
            ]),
            Err(ConfigError::LayerOrderViolation)
        );
        assert!(matches!(
            Config::load(&[ConfigLayer::defaults(), ConfigLayer::defaults()]),
            Err(ConfigError::DuplicateLayer(LayerKind::Defaults))
        ));
    }

    #[test]
    fn unknown_keys_and_missing_secrets_fail_closed() {
        let unknown = ConfigLayer::new(
            LayerKind::Environment,
            BTreeMap::from([("typo_key".to_owned(), "value".to_owned())]),
        );
        assert_eq!(
            Config::load(&[ConfigLayer::defaults(), unknown]),
            Err(ConfigError::UnknownKey("typo_key".to_owned()))
        );
        assert_eq!(
            Config::load(&[ConfigLayer::defaults()]),
            Err(ConfigError::MissingRequired("database_url"))
        );
    }

    #[test]
    fn secret_values_are_never_rendered() {
        let config = valid_config();
        assert!(config.is_ok());
        let rendered = config.as_ref().map(|value| format!("{value:?}"));
        assert!(rendered.as_ref().is_some_and(|value| !value.contains("password")));
        assert!(rendered
            .as_ref()
            .is_some_and(|value| !value.contains("0123456789abcdef")));
        let snapshot = config.as_ref().map(Config::redacted_snapshot);
        assert_eq!(
            snapshot.as_ref().and_then(|value| value.get("database_url")),
            Some(&"[REDACTED]".to_owned())
        );
        assert_eq!(
            snapshot
                .as_ref()
                .and_then(|value| value.get("session_encryption_key")),
            Some(&"[REDACTED]".to_owned())
        );
    }

    #[test]
    fn invalid_secret_error_does_not_echo_input() {
        let value = "short-secret-value";
        let result = Config::load(&[
            ConfigLayer::defaults(),
            required_layer(LayerKind::Environment, "postgresql://db/game", value),
        ]);
        assert!(result.is_err());
        assert!(!result.err().map(|error| error.to_string()).unwrap_or_default().contains(value));
    }

    #[test]
    fn bind_addresses_must_be_valid_and_distinct() {
        let duplicate = ConfigLayer::new(
            LayerKind::CommandLine,
            BTreeMap::from([("grpc_bind".to_owned(), "127.0.0.1:7350".to_owned())]),
        );
        assert_eq!(
            Config::load(&[
                ConfigLayer::defaults(),
                required_layer(
                    LayerKind::Environment,
                    "postgresql://db/game",
                    "0123456789abcdef0123456789abcdef",
                ),
                duplicate,
            ]),
            Err(ConfigError::ConflictingBindAddress)
        );
    }

    #[test]
    fn server_cli_supports_split_and_equals_flags() {
        let result = Invocation::parse([
            "server",
            "--node-name=alpha",
            "--max-connections",
            "2000",
            "--database-url=postgresql://db/game",
        ]);
        assert!(matches!(result, Ok(Invocation { command: Command::Server, .. })));
        let overrides = result.ok().map(|value| value.overrides).unwrap_or_default();
        assert_eq!(overrides.get("node_name"), Some(&"alpha".to_owned()));
        assert_eq!(overrides.get("max_connections"), Some(&"2000".to_owned()));
    }

    #[test]
    fn duplicate_unknown_and_missing_cli_flags_fail_closed() {
        assert!(matches!(
            Invocation::parse(["server", "--node-name=a", "--node_name=b"]),
            Err(CliError::DuplicateFlag("node_name"))
        ));
        assert_eq!(
            Invocation::parse(["server", "--mystery=x"]),
            Err(CliError::UnknownFlag("mystery".to_owned()))
        );
        assert_eq!(
            Invocation::parse(["server", "--node-name"]),
            Err(CliError::MissingFlagValue("node-name".to_owned()))
        );
    }

    #[test]
    fn migrate_and_read_only_commands_are_typed() {
        assert_eq!(
            Invocation::parse(["migrate", "up"]),
            Ok(Invocation {
                command: Command::Migrate(MigrationCommand::Up),
                overrides: BTreeMap::new(),
            })
        );
        assert_eq!(
            Invocation::parse(["healthcheck", "--node-name=x"]),
            Err(CliError::FlagsNotAllowed(Command::Healthcheck))
        );
        assert_eq!(Invocation::parse(["--help"]), Err(CliError::HelpRequested));
    }

    #[test]
    fn service_becomes_ready_only_when_every_dependency_is_ready() {
        let mut lifecycle = ServiceLifecycle::new(100);
        assert_eq!(
            lifecycle.update_dependencies(
                Dependencies {
                    database: true,
                    migrations: true,
                    runtime: false,
                },
                101,
            ),
            Ok(Lifecycle::Starting)
        );
        assert_eq!(
            lifecycle.update_dependencies(
                Dependencies {
                    database: true,
                    migrations: true,
                    runtime: true,
                },
                102,
            ),
            Ok(Lifecycle::Ready)
        );
        assert!(lifecycle.health_ready());
        assert!(lifecycle.accepts_new_work());
    }

    #[test]
    fn dependency_loss_after_ready_is_not_ready_not_starting() {
        let mut lifecycle = ServiceLifecycle::new(0);
        assert!(lifecycle
            .update_dependencies(
                Dependencies {
                    database: true,
                    migrations: true,
                    runtime: true,
                },
                1,
            )
            .is_ok());
        assert_eq!(
            lifecycle.update_dependencies(
                Dependencies {
                    database: false,
                    migrations: true,
                    runtime: true,
                },
                2,
            ),
            Ok(Lifecycle::NotReady)
        );
        assert!(!lifecycle.accepts_new_work());
        assert!(lifecycle.health_live());
    }

    #[test]
    fn drain_rejects_new_work_and_stops_at_deadline() {
        let mut lifecycle = ServiceLifecycle::new(10);
        assert_eq!(lifecycle.begin_drain(20, 100), Ok(Lifecycle::Draining { deadline_ms: 120 }));
        assert!(!lifecycle.accepts_new_work());
        assert!(lifecycle.health_live());
        assert_eq!(lifecycle.tick(119), Ok(Lifecycle::Draining { deadline_ms: 120 }));
        assert_eq!(lifecycle.tick(120), Ok(Lifecycle::Stopped));
        assert!(!lifecycle.health_live());
    }

    #[test]
    fn lifecycle_rejects_time_regression_overflow_and_bad_grace() {
        let mut lifecycle = ServiceLifecycle::new(100);
        assert_eq!(lifecycle.tick(99), Err(LifecycleError::ClockRegression));
        assert_eq!(
            lifecycle.begin_drain(u64::MAX - 10, 100),
            Err(LifecycleError::ClockOverflow)
        );
        let mut other = ServiceLifecycle::new(0);
        assert_eq!(other.begin_drain(1, 99), Err(LifecycleError::InvalidGracePeriod));
    }

    #[test]
    fn stopped_and_draining_states_are_terminal_for_dependency_updates() {
        let mut draining = ServiceLifecycle::new(0);
        assert!(draining.begin_drain(1, 100).is_ok());
        assert_eq!(
            draining.update_dependencies(
                Dependencies {
                    database: true,
                    migrations: true,
                    runtime: true,
                },
                2,
            ),
            Err(LifecycleError::TerminalTransition)
        );
        let mut stopped = ServiceLifecycle::new(0);
        assert_eq!(stopped.force_stop(1), Ok(Lifecycle::Stopped));
        assert_eq!(stopped.begin_drain(2, 100), Err(LifecycleError::TerminalTransition));
    }
}
