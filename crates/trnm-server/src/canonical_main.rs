#![forbid(unsafe_code)]

mod runtime;

use std::env;
use std::process::ExitCode;

use runtime::config::{Command, ServerConfig};
use runtime::error::ServerError;

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
            let report = runtime::schema::migrate(&config)?;
            println!(
                "migration profile={} applied={} table_count={}",
                report.profile.metadata_value(),
                report.migration_applied,
                report.table_count,
            );
            Ok(())
        }
        Command::Serve => {
            let repository = runtime::schema::open_verified_repository(&config)?;
            runtime::server::serve(&config, repository)
        }
    }
}
