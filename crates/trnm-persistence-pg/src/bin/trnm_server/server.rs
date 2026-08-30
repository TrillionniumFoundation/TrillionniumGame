use std::io::ErrorKind;
use std::net::{Shutdown, TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{sync_channel, Receiver, SyncSender, TrySendError};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use super::app::{App, Repository};
use super::config::ServerConfig;
use super::error::ServerError;
use super::http::{read_request, Request, Response};
use super::retry::{RetryPolicy, RetryingRepository};
use super::websocket;

const MAX_CONNECTION_WORKERS: usize = 32;
const QUEUED_CONNECTIONS_PER_WORKER: usize = 16;
const ACCEPT_POLL_INTERVAL: Duration = Duration::from_millis(10);

pub fn serve<R>(config: &ServerConfig, repository: R) -> Result<(), ServerError>
where
    R: Repository + Clone + Send + 'static,
{
    let listener = TcpListener::bind(config.bind)?;
    listener.set_nonblocking(true)?;
    let (worker_count, queue_capacity) = connection_policy(config.database_pool.max_size);
    let (sender, receiver) = sync_channel(queue_capacity);
    let receiver = Arc::new(Mutex::new(receiver));
    let draining = Arc::new(AtomicBool::new(false));
    let worker_failed = Arc::new(AtomicBool::new(false));
    let mut workers = Vec::with_capacity(worker_count);

    for worker_index in 0..worker_count {
        let worker_repository =
            RetryingRepository::new(repository.clone(), RetryPolicy::candidate_default())?;
        let worker_config = config.clone();
        let worker_receiver = Arc::clone(&receiver);
        let worker_draining = Arc::clone(&draining);
        let worker_failed = Arc::clone(&worker_failed);
        workers.push(
            thread::Builder::new()
                .name(format!("trnm-connection-{worker_index}"))
                .spawn(move || {
                    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                        worker_loop(
                            worker_config,
                            worker_repository,
                            worker_receiver,
                            worker_draining,
                        );
                    }));
                    if result.is_err() {
                        worker_failed.store(true, Ordering::Release);
                    }
                })?,
        );
    }

    eprintln!(
        "trnm-server source candidate listening on {} profile={} workers={} queue_capacity={}",
        config.bind,
        config.database_profile.metadata_value(),
        worker_count,
        queue_capacity,
    );

    let accept_result = accept_loop(&listener, &sender, config, &draining, &worker_failed);
    drop(sender);
    let join_result = join_workers(workers);
    accept_result?;
    join_result?;
    eprintln!("trnm-server source candidate drained");
    Ok(())
}

fn accept_loop(
    listener: &TcpListener,
    sender: &SyncSender<TcpStream>,
    config: &ServerConfig,
    draining: &AtomicBool,
    worker_failed: &AtomicBool,
) -> Result<(), ServerError> {
    loop {
        if worker_failed.load(Ordering::Acquire) {
            draining.store(true, Ordering::Release);
            return Err(ServerError::Configuration("connection_worker_panicked"));
        }
        if draining.load(Ordering::Acquire) {
            return Ok(());
        }
        match listener.accept() {
            Ok((stream, _peer)) => match sender.try_send(stream) {
                Ok(()) => {}
                Err(TrySendError::Full(mut stream)) => {
                    configure_connection(&stream, config)?;
                    write_response(&mut stream, &overloaded());
                    let _ = stream.shutdown(Shutdown::Both);
                }
                Err(TrySendError::Disconnected(mut stream)) => {
                    write_response(&mut stream, &unavailable());
                    let _ = stream.shutdown(Shutdown::Both);
                    draining.store(true, Ordering::Release);
                    return Err(ServerError::Configuration(
                        "connection_worker_queue_disconnected",
                    ));
                }
            },
            Err(error) if error.kind() == ErrorKind::WouldBlock => {
                thread::sleep(ACCEPT_POLL_INTERVAL);
            }
            Err(error) if error.kind() == ErrorKind::Interrupted => {}
            Err(error) => return Err(error.into()),
        }
    }
}

fn worker_loop<R>(
    config: ServerConfig,
    repository: RetryingRepository<R>,
    receiver: Arc<Mutex<Receiver<TcpStream>>>,
    draining: Arc<AtomicBool>,
) where
    R: Repository,
{
    let mut app = App::new(repository, config.admin_token.clone());
    if let Some(session_auth) = &config.session_auth {
        match session_auth.verifier() {
            Ok(verifier) => app = app.with_access_token_verifier(verifier),
            Err(error) => {
                eprintln!(
                    "trnm-server connection worker session verifier failed: {}",
                    error.code().as_str()
                );
                draining.store(true, Ordering::Release);
                return;
            }
        }
    }

    while let Some(mut stream) = receive_connection(&receiver) {
        if let Err(error) = handle_connection(&mut stream, &mut app, &config, &draining) {
            eprintln!("trnm-server connection failed: {error}");
        }
        let _ = stream.shutdown(Shutdown::Both);
    }
}

fn receive_connection(receiver: &Mutex<Receiver<TcpStream>>) -> Option<TcpStream> {
    let guard = receiver
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    guard.recv().ok()
}

fn handle_connection<R: Repository>(
    stream: &mut TcpStream,
    app: &mut App<R>,
    config: &ServerConfig,
    draining: &AtomicBool,
) -> Result<(), ServerError> {
    configure_connection(stream, config)?;
    let request = match read_request(stream, config.max_request_bytes) {
        Ok(request) => request,
        Err(ServerError::Input(_)) => {
            write_response(stream, &bad_request());
            return Ok(());
        }
        Err(ServerError::Io(error))
            if matches!(error.kind(), ErrorKind::TimedOut | ErrorKind::WouldBlock) =>
        {
            write_response(stream, &bad_request());
            return Ok(());
        }
        Err(error) => return Err(error),
    };

    if draining.load(Ordering::Acquire)
        && (is_readiness(&request) || request_rejected_while_draining(&request))
    {
        write_response(stream, &draining_response());
        return Ok(());
    }

    if websocket::is_route(&request) {
        if let Err(error) = websocket::serve_once(stream, &request, app, config.max_request_bytes) {
            // WebSocket delivery can fail after the shared application path has
            // durably committed. The stable command receipt is the retry fence;
            // never repeat an external effect here.
            eprintln!("trnm-server WebSocket delivery failed: {error}");
        }
        return Ok(());
    }

    let response = app.handle(&request);
    if app.should_stop() {
        draining.store(true, Ordering::Release);
    }
    write_response(stream, &response);
    Ok(())
}

fn request_rejected_while_draining(request: &Request) -> bool {
    websocket::is_route(request) || (request.method == "POST" && request.target != "/-/drain")
}

fn is_readiness(request: &Request) -> bool {
    request.method == "GET" && request.target == "/readyz"
}

fn connection_policy(database_pool_max_size: u32) -> (usize, usize) {
    let worker_count = (database_pool_max_size as usize).clamp(1, MAX_CONNECTION_WORKERS);
    let queue_capacity = worker_count.saturating_mul(QUEUED_CONNECTIONS_PER_WORKER);
    (worker_count, queue_capacity)
}

fn join_workers(workers: Vec<JoinHandle<()>>) -> Result<(), ServerError> {
    let mut panicked = false;
    for worker in workers {
        panicked |= worker.join().is_err();
    }
    if panicked {
        Err(ServerError::Configuration("connection_worker_panicked"))
    } else {
        Ok(())
    }
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

fn overloaded() -> Response {
    Response::json(
        503,
        br#"{"code":"unavailable","message":"Connection capacity is exhausted.","retry":"backoff"}"#.to_vec(),
    )
}

fn unavailable() -> Response {
    Response::json(
        503,
        br#"{"code":"unavailable","message":"Service is unavailable.","retry":"backoff"}"#.to_vec(),
    )
}

fn draining_response() -> Response {
    Response::json(
        503,
        br#"{"code":"unavailable","message":"Service is draining.","retry":"backoff"}"#.to_vec(),
    )
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use super::*;

    #[test]
    fn connection_parse_failure_has_no_internal_reason() {
        let response = bad_request();
        assert_eq!(response.status, 400);
        let body = String::from_utf8(response.body).unwrap();
        assert!(!body.contains("http_"));
        assert!(!body.contains("database"));
    }

    #[test]
    fn connection_policy_is_nonzero_and_hard_bounded() {
        assert_eq!(connection_policy(0), (1, 16));
        assert_eq!(connection_policy(8), (8, 128));
        assert_eq!(connection_policy(256), (32, 512));
    }

    #[test]
    fn global_drain_rejects_new_mutations_but_keeps_control_reads() {
        let mutation = Request::new("POST", "/v1/authority/commit", BTreeMap::new(), Vec::new());
        let metrics = Request::new("GET", "/metrics", BTreeMap::new(), Vec::new());
        let readiness = Request::new("GET", "/readyz", BTreeMap::new(), Vec::new());
        assert!(request_rejected_while_draining(&mutation));
        assert!(!request_rejected_while_draining(&metrics));
        assert!(is_readiness(&readiness));
    }

    #[test]
    fn overload_and_drain_responses_are_stable_and_retryable() {
        for response in [overloaded(), unavailable(), draining_response()] {
            assert_eq!(response.status, 503);
            let body = String::from_utf8(response.body).unwrap();
            assert!(body.contains("\"code\":\"unavailable\""));
            assert!(body.contains("\"retry\":\"backoff\""));
            assert!(!body.contains("database"));
        }
    }
}
