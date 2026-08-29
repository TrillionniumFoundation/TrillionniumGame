#![forbid(unsafe_code)]

use std::process::ExitCode;

use trnm_server::{healthcheck, serve, ServerConfig};

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("trnm-server: {error}");
            ExitCode::FAILURE
        }
    }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let command = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "serve".to_owned());
    match command.as_str() {
        "serve" => {
            let config = ServerConfig::from_env()?;
            serve(&config)?;
        }
        "check-config" => {
            let config = ServerConfig::from_env()?;
            config.validate()?;
            println!("{config:?}");
        }
        "healthcheck" => {
            let config = ServerConfig::from_env()?;
            if !healthcheck(config.listen)? {
                return Err("healthcheck returned a non-200 response".into());
            }
        }
        "version" => {
            println!("trnm-server {}", env!("CARGO_PKG_VERSION"));
        }
        other => {
            return Err(format!(
                "unknown command {other:?}; expected serve, check-config, healthcheck, or version"
            )
            .into());
        }
    }
    Ok(())
}
