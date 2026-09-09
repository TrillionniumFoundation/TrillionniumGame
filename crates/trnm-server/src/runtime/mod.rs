pub(crate) mod app;
pub(crate) mod auth;
pub(crate) mod codec;
pub(crate) mod config;
pub(crate) mod error;
pub(crate) mod grpc;
pub(crate) mod http;
pub(crate) mod json;
pub(crate) mod pool;
pub(crate) mod retry;
#[cfg(test)]
pub(crate) mod retry_live_tests;
pub(crate) mod schema;
pub(crate) mod server;
pub(crate) mod session_api;
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
            let repository = schema::open_verified_repository(&config)?;
            server::serve(&config, repository)
        }
    }
}

#[cfg(test)]
mod operator_message_tests {
    use super::{CONFIGURATION_VALID_MESSAGE, MIGRATION_COMPLETE_MESSAGE};

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
