#![forbid(unsafe_code)]

mod trnm_server;

use std::env;
use std::process::ExitCode;

use trnm_server::config::{Command, ServerConfig};
use trnm_server::error::ServerError;

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
            println!("trnm-server configuration: {config:?}");
            Ok(())
        }
        Command::Migrate => {
            let report = trnm_server::schema::migrate(&config)?;
            println!(
                "migration profile={} applied={} table_count={}",
                report.profile.metadata_value(),
                report.migration_applied,
                report.table_count,
            );
            Ok(())
        }
        Command::Serve => {
            let repository = trnm_server::schema::open_verified_repository(&config)?;
            trnm_server::server::serve(&config, repository)
        }
    }
}
