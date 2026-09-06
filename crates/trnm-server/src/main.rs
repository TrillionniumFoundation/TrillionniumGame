#![forbid(unsafe_code)]

use std::process::ExitCode;

fn main() -> ExitCode {
    match trnm_server::run_from_environment() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("trnm-server failed: {error}");
            ExitCode::FAILURE
        }
    }
}
