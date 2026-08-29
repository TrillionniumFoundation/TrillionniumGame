#![forbid(unsafe_code)]

use std::env;
use std::error::Error;
use std::net::TcpListener;
use std::process::ExitCode;
use std::sync::mpsc;

use trnm_server::{Application, ConfigError, Server, ServerConfig};

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("trnm-server: {error}");
            ExitCode::FAILURE
        }
    }
}

fn run() -> Result<(), Box<dyn Error>> {
    let mut arguments = env::args().skip(1);
    let command = arguments.next().unwrap_or_else(|| "serve".to_owned());
    match command.as_str() {
        "serve" => {
            let config = apply_overrides(ServerConfig::from_env()?, arguments)?;
            let listener = TcpListener::bind(config.bind)?;
            let address = listener.local_addr()?;
            eprintln!(
                "trnm-server listening on {address}; profile=source-vertical-slice; compatibility_credit=false"
            );
            let application = Application::new();
            let server = Server::new(config, application)?;
            let (shutdown_guard, shutdown) = mpsc::channel();
            let _keep_sender_alive = shutdown_guard;
            server.serve(listener, shutdown)?;
            Ok(())
        }
        "check-config" => {
            let config = apply_overrides(ServerConfig::from_env()?, arguments)?;
            println!(
                "{{\"bind\":\"{}\",\"worker_count\":{},\"queue_capacity\":{},\"max_request_bytes\":{},\"valid\":true}}",
                config.bind,
                config.worker_count,
                config.queue_capacity,
                config.max_request_bytes
            );
            Ok(())
        }
        "version" | "--version" | "-V" => {
            println!("trnm-server {}", env!("CARGO_PKG_VERSION"));
            Ok(())
        }
        "help" | "--help" | "-h" => {
            print_help();
            Ok(())
        }
        _ => Err(format!("unknown command {command:?}; use --help").into()),
    }
}

fn apply_overrides(
    mut config: ServerConfig,
    mut arguments: impl Iterator<Item = String>,
) -> Result<ServerConfig, Box<dyn Error>> {
    while let Some(argument) = arguments.next() {
        match argument.as_str() {
            "--bind" => {
                config.bind = next_value(&mut arguments, "--bind")?
                    .parse()
                    .map_err(|_| ConfigError::InvalidValue("--bind"))?;
            }
            "--workers" => {
                config.worker_count = parse_usize(
                    &next_value(&mut arguments, "--workers")?,
                    "--workers",
                )?;
            }
            "--queue-capacity" => {
                config.queue_capacity = parse_usize(
                    &next_value(&mut arguments, "--queue-capacity")?,
                    "--queue-capacity",
                )?;
            }
            "--max-request-bytes" => {
                config.max_request_bytes = parse_usize(
                    &next_value(&mut arguments, "--max-request-bytes")?,
                    "--max-request-bytes",
                )?;
            }
            _ => return Err(format!("unknown serve option {argument:?}").into()),
        }
    }
    config.validate()?;
    Ok(config)
}

fn next_value(
    arguments: &mut impl Iterator<Item = String>,
    option: &'static str,
) -> Result<String, ConfigError> {
    arguments
        .next()
        .ok_or(ConfigError::InvalidValue(option))
}

fn parse_usize(value: &str, name: &'static str) -> Result<usize, ConfigError> {
    value
        .parse::<usize>()
        .map_err(|_| ConfigError::InvalidValue(name))
}

fn print_help() {
    println!(
        "trnm-server [serve|check-config|version] [options]\n\n\
         Options:\n\
           --bind ADDRESS\n\
           --workers COUNT\n\
           --queue-capacity COUNT\n\
           --max-request-bytes BYTES\n\n\
         Environment:\n\
           TRNM_SERVER_BIND\n\
           TRNM_SERVER_WORKERS\n\
           TRNM_SERVER_QUEUE_CAPACITY\n\
           TRNM_SERVER_MAX_REQUEST_BYTES\n\n\
         This binary is a fail-closed source vertical slice. It does not claim\n\
         Nakama compatibility, durable database authority or production readiness."
    );
}
