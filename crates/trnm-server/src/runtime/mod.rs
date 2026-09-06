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

pub fn run_from_environment() -> Result<(), ServerError> {
    let arguments = env::args().collect::<Vec<_>>();
    run(&arguments)
}

pub fn run(arguments: &[String]) -> Result<(), ServerError> {
    let (command, config) = ServerConfig::from_environment(arguments)?;
    match command {
        Command::CheckConfig => {
            println!("trnm-server configuration: {config:?}");
            Ok(())
        }
        Command::Migrate => {
            let report = schema::migrate(&config)?;
            println!(
                "migration profile={} applied={} table_count={}",
                report.profile.metadata_value(),
                report.migration_applied,
                report.table_count,
            );
            Ok(())
        }
        Command::Serve => {
            let repository = schema::open_verified_repository(&config)?;
            server::serve(&config, repository)
        }
    }
}
