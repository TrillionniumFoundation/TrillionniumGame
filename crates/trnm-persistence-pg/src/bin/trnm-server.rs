#![forbid(unsafe_code)]

mod trnm_server;

use std::env;
use std::process::ExitCode;

use trnm_server::config::{Command, ServerConfig};
use trnm_server::error::ServerError;

const CONFIGURATION_VALID_MESSAGE: &str = "trnm-server configuration valid";
const MIGRATION_COMPLETE_MESSAGE: &str = "trnm-server migration completed";

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("trnm-server failed: {error}");
            ExitCode::FAILURE
        }
    }
}

fn run() -> Result<(), ServerError> {
    let arguments = env::args().collect::<Vec<_>>();
    let (command, config) = ServerConfig::from_environment(&arguments)?;
    match command {
        Command::CheckConfig => {
            println!("{CONFIGURATION_VALID_MESSAGE}");
            Ok(())
        }
        Command::Migrate => {
            trnm_server::schema::migrate(&config)?;
            println!("{MIGRATION_COMPLETE_MESSAGE}");
            Ok(())
        }
        Command::Serve => {
            let repository = trnm_server::schema::open_verified_repository(&config)?;
            trnm_server::server::serve(&config, repository)
        }
    }
}

#[cfg(test)]
mod operator_message_tests {
    use super::{CONFIGURATION_VALID_MESSAGE, MIGRATION_COMPLETE_MESSAGE};

    #[test]
    fn legacy_operator_messages_are_static_and_secret_free() {
        for message in [CONFIGURATION_VALID_MESSAGE, MIGRATION_COMPLETE_MESSAGE] {
            for forbidden in ["key", "token", "database", "profile", "bind"] {
                assert!(!message.contains(forbidden));
            }
        }
    }
}
