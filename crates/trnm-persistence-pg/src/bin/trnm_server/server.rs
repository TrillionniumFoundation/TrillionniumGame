use std::io::ErrorKind;
use std::net::{Shutdown, TcpListener, TcpStream};

use super::app::{App, Repository};
use super::config::ServerConfig;
use super::error::ServerError;
use super::http::{read_request, Response};
use super::retry::{RetryPolicy, RetryingRepository};
use super::websocket;

pub fn serve<R: Repository>(config: &ServerConfig, repository: R) -> Result<(), ServerError> {
    let listener = TcpListener::bind(config.bind)?;
    let repository = RetryingRepository::new(repository, RetryPolicy::candidate_default())?;
    let mut app = App::new(repository, config.admin_token.clone());
    if let Some(session_auth) = &config.session_auth {
        app = app.with_access_token_verifier(session_auth.verifier()?);
    }
    eprintln!(
        "trnm-server source candidate listening on {} profile={}",
        config.bind,
        config.database_profile.metadata_value(),
    );

    for connection in listener.incoming() {
        let mut stream = connection?;
        configure_connection(&stream, config)?;
        match read_request(&mut stream, config.max_request_bytes) {
            Ok(request) if websocket::is_route(&request) => {
                if let Err(error) =
                    websocket::serve_once(&mut stream, &request, &mut app, config.max_request_bytes)
                {
                    // WebSocket delivery can fail after the shared application
                    // path has durably committed. The stable command receipt is
                    // the retry fence; never repeat an external effect here.
                    eprintln!("trnm-server WebSocket delivery failed: {error}");
                }
            }
            Ok(request) => {
                let response = app.handle(&request);
                write_response(&mut stream, &response);
            }
            Err(ServerError::Input(_)) => write_response(&mut stream, &bad_request()),
            Err(ServerError::Io(error))
                if matches!(error.kind(), ErrorKind::TimedOut | ErrorKind::WouldBlock) =>
            {
                write_response(&mut stream, &bad_request());
            }
            Err(error) => return Err(error),
        }

        let _ = stream.shutdown(Shutdown::Both);
        if app.should_stop() {
            break;
        }
    }
    eprintln!("trnm-server source candidate drained");
    Ok(())
}

fn write_response(stream: &mut TcpStream, response: &Response) {
    if let Err(error) = response.write_to(stream) {
        // A peer disappearing after the durable transaction has committed is
        // an ambiguous-response case. The command ID/fingerprint receipt is
        // the retry contract; a broken response must not roll back or replay an
        // external effect here.
        eprintln!("trnm-server response delivery failed: {error}");
    }
}

fn configure_connection(stream: &TcpStream, config: &ServerConfig) -> Result<(), ServerError> {
    stream.set_nodelay(true)?;
    stream.set_read_timeout(Some(config.read_timeout))?;
    stream.set_write_timeout(Some(config.write_timeout))?;
    Ok(())
}

fn bad_request() -> Response {
    Response::json(
        400,
        br#"{"code":"invalid_argument","message":"Request is invalid.","retry":"never"}"#.to_vec(),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn connection_parse_failure_has_no_internal_reason() {
        let response = bad_request();
        assert_eq!(response.status, 400);
        let body = String::from_utf8(response.body).unwrap();
        assert!(!body.contains("http_"));
        assert!(!body.contains("database"));
    }
}
