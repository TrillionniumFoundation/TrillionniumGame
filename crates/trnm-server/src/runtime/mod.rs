pub(crate) mod app;
pub(crate) mod auth;
mod auth_runtime;
pub(crate) mod codec;
pub(crate) mod config;
mod cors;
pub(crate) mod error;
pub(crate) mod grpc;
pub(crate) mod http;
pub(crate) mod json;
mod legacy_auth;
mod legacy_config;
mod legacy_device_predicates;
mod legacy_http_api;
mod legacy_repository;
mod legacy_uuid;
pub(crate) mod pool;
pub(crate) mod retry;
#[cfg(test)]
pub(crate) mod retry_live_tests;
pub(crate) mod schema;
pub(crate) mod server;
pub(crate) mod session_api;
pub(crate) mod storage_api;
#[cfg(test)]
mod storage_api_tests;
mod storage_cursor;
mod storage_list_api;
#[cfg(test)]
mod storage_list_api_tests;
mod storage_list_query;
pub(crate) mod websocket;

use std::env;

use config::{Command, ServerConfig};
pub use error::ServerError;

const CONFIGURATION_VALID_MESSAGE: &str = "trnm-server configuration valid";
const MIGRATION_COMPLETE_MESSAGE: &str = "trnm-server migration completed";

pub fn run_from_environment() -> Result<(), ServerError> {
    let arguments = env::args().collect::<Vec<_>>();
    run(&arguments)
}

pub fn run(arguments: &[String]) -> Result<(), ServerError> {
    let (command, config) = ServerConfig::from_environment(arguments)?;
    run_configured(command, config)
}

fn run_configured(command: Command, config: ServerConfig) -> Result<(), ServerError> {
    match command {
        Command::CheckConfig => {
            println!("{CONFIGURATION_VALID_MESSAGE}");
            Ok(())
        }
        Command::Migrate => {
            schema::migrate(&config)?;
            println!("{MIGRATION_COMPLETE_MESSAGE}");
            Ok(())
        }
        Command::Serve => {
            auth::require_installed_http_authority(&config.auth_authority)?;
            let repository = schema::open_verified_repository(&config)?;
            server::serve(&config, repository)
        }
    }
}

#[cfg(test)]
mod operator_message_tests {
    use super::{
        config::{Command, ServerConfig},
        run_configured,
    };
    use super::{CONFIGURATION_VALID_MESSAGE, MIGRATION_COMPLETE_MESSAGE};

    #[test]
    fn legacy_check_config_is_pure_and_serve_rejects_before_repository_setup() {
        let values = std::collections::BTreeMap::from([
            ("TRNM_SERVER_DATABASE_URL", "unopened-invalid-database-url"),
            ("TRNM_SERVER_DATABASE_PROFILE", "postgresql"),
            ("TRNM_SERVER_DATABASE_TLS_MODE", "verify-full"),
            (
                "TRNM_SERVER_DATABASE_TLS_ROOT_CERT_PEM",
                "/unopened/auth-mode/root.pem",
            ),
            (
                "TRNM_SERVER_SCHEMA_SOURCE_COMMIT",
                "0123456789abcdef0123456789abcdef01234567",
            ),
            ("TRNM_SERVER_SCHEMA_TARGET", "nakama-accounts-v5"),
            (
                "TRNM_SERVER_ADMIN_TOKEN",
                "a_secure_local_admin_token_123456789",
            ),
            ("TRNM_SERVER_AUTH_MODE", "nakama-legacy"),
            ("TRNM_SERVER_LEGACY_SERVER_KEY", "deliberate-server-key"),
            ("TRNM_SERVER_LEGACY_ACCESS_KEY", "defaultencryptionkey"),
            (
                "TRNM_SERVER_LEGACY_REFRESH_KEY",
                "defaultrefreshencryptionkey",
            ),
            ("TRNM_SERVER_LEGACY_ACCESS_TTL_SECONDS", "60"),
            ("TRNM_SERVER_LEGACY_REFRESH_TTL_SECONDS", "3600"),
        ]);
        let (command, config) = ServerConfig::from_lookup(
            &["trnm-server".to_owned(), "check-config".to_owned()],
            |name| values.get(name).map(|v| (*v).to_owned()),
        )
        .unwrap();
        assert_eq!(command, Command::CheckConfig);
        assert!(run_configured(command, config.clone()).is_ok());
        assert!(matches!(
            run_configured(Command::Serve, config),
            Err(super::ServerError::Domain(error)) if error.code() == trnm_contracts::StableCode::FailedPrecondition && error.reason() == "schema5_native_catalog_capture_pending"
        ));
    }

    #[test]
    fn operator_messages_are_static_and_secret_free() {
        for message in [CONFIGURATION_VALID_MESSAGE, MIGRATION_COMPLETE_MESSAGE] {
            assert!(!message.contains("key"));
            assert!(!message.contains("token"));
            assert!(!message.contains("database"));
            assert!(!message.contains("profile"));
        }
    }
}

// Server-owned service API; native repository and HTTP adapters remain separate.
pub(crate) mod legacy_service_exports {
    pub use super::legacy_auth::*;
    pub use super::legacy_config::LegacyServerAuthConfig;
    pub use super::legacy_http_api::*;
    pub use super::legacy_repository::PgLegacyAuthRepository;
}
