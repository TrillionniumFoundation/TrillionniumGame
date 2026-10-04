use std::collections::BTreeMap;
use std::io::ErrorKind;
use std::net::{Shutdown, TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc::{sync_channel, Receiver, SyncSender, TrySendError};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use super::app::{App, Repository, SharedAppMetrics, SharedDrain};
use super::auth::require_installed_http_authority;
use super::auth_runtime::AuthAuthorityRuntime;
use super::config::ServerConfig;
use super::error::ServerError;
use super::grpc;
use super::http::{read_request, Request, Response};
use super::legacy_http_api::legacy_auth_http_route;
use super::pool::InflightCancellation;
use super::retry::{BudgetedRepository, RetryPolicy, RetryingRepository};
use super::websocket;

const MAX_CONNECTION_WORKERS: usize = 32;
const QUEUED_CONNECTIONS_PER_WORKER: usize = 16;
const ACCEPT_POLL_INTERVAL: Duration = Duration::from_millis(10);
const STARTUP_MESSAGE: &str = "trnm-server source candidate started";

#[derive(Debug)]
struct QueuedConnection {
    id: u64,
    stream: TcpStream,
}

#[derive(Clone, Debug, Default)]
struct ConnectionRegistry {
    next_id: Arc<AtomicU64>,
    streams: Arc<Mutex<BTreeMap<u64, TcpStream>>>,
}

impl ConnectionRegistry {
    fn register(&self, stream: &TcpStream) -> Result<u64, ServerError> {
        let previous = self
            .next_id
            .fetch_update(Ordering::AcqRel, Ordering::Acquire, |value| {
                value.checked_add(1)
            })
            .map_err(|_| ServerError::Configuration("connection_id_exhausted"))?;
        let id = previous + 1;
        let retained = stream.try_clone()?;
        self.streams
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .insert(id, retained);
        Ok(id)
    }

    fn remove(&self, id: u64) {
        self.streams
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .remove(&id);
    }

    fn shutdown_all(&self) -> usize {
        let streams = self
            .streams
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        for stream in streams.values() {
            let _ = stream.shutdown(Shutdown::Both);
        }
        streams.len()
    }
}

pub fn serve<R>(config: &ServerConfig, mut repository: R) -> Result<(), ServerError>
where
    R: Repository + BudgetedRepository + InflightCancellation + Clone + Send + 'static,
{
    require_installed_http_authority(&config.auth_authority)?;
    if matches!(
        config.auth_authority,
        super::config::AuthAuthorityConfig::NakamaLegacy(_)
    ) && config.schema_target != trnm_persistence_pg::AuthoritativeSchemaTarget::NakamaAccountsV5
    {
        return Err(ServerError::Configuration(
            "legacy_auth_requires_accounts_v5_target",
        ));
    }
    config.schema_target.require_capture_ready()?;
    repository.verify_storage_import_serving()?;
    let authority = AuthAuthorityRuntime::install(&config.auth_authority)?;
    let listener = TcpListener::bind(config.bind)?;
    listener.set_nonblocking(true)?;
    let (worker_count, queue_capacity) = connection_policy(config.database_pool.max_size);
    let (sender, receiver) = sync_channel::<QueuedConnection>(queue_capacity);
    let receiver = Arc::new(Mutex::new(receiver));
    let connections = ConnectionRegistry::default();
    let draining = SharedDrain::default();
    let worker_failed = Arc::new(AtomicBool::new(false));
    let metrics = SharedAppMetrics::default();
    let mut grpc_worker = None;
    let mut workers = Vec::with_capacity(worker_count);
    let accept_result = (|| {
        grpc_worker = grpc::spawn(
            config.grpc_bind,
            draining.clone(),
            Arc::clone(&worker_failed),
        )?;

        for worker_index in 0..worker_count {
            let worker_repository =
                RetryingRepository::new(repository.clone(), RetryPolicy::candidate_default())?;
            let worker_config = config.clone();
            let worker_authority = authority.clone();
            let worker_receiver = Arc::clone(&receiver);
            let worker_draining = draining.clone();
            let worker_failed = Arc::clone(&worker_failed);
            let worker_metrics = metrics.clone();
            let worker_connections = connections.clone();
            workers.push(
                thread::Builder::new()
                    .name(format!("trnm-connection-{worker_index}"))
                    .spawn(move || {
                        let worker_drain_for_loop = worker_draining.clone();
                        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                            worker_loop(
                                worker_config,
                                worker_repository,
                                worker_receiver,
                                worker_drain_for_loop,
                                worker_metrics,
                                worker_connections,
                                worker_authority,
                            );
                        }));
                        if result.is_err() {
                            worker_failed.store(true, Ordering::Release);
                            worker_draining.begin();
                        }
                    })?,
            );
        }

        eprintln!("{STARTUP_MESSAGE}");

        accept_loop(
            &listener,
            &sender,
            config,
            &draining,
            &worker_failed,
            &connections,
        )
    })();
    draining.begin();
    drop(listener);
    let closed_connections = connections.shutdown_all();
    if closed_connections > 0 {
        eprintln!(
            "trnm-server closed {closed_connections} queued or active connections during drain"
        );
    }
    let cancelled_operations = repository.cancel_inflight();
    if cancelled_operations > 0 {
        eprintln!(
            "trnm-server requested cancellation for {cancelled_operations} in-flight database operations"
        );
    }
    drop(sender);
    let join_result = join_workers(workers);
    let grpc_join_result = grpc::join(grpc_worker);
    drop(authority);
    accept_result?;
    join_result?;
    grpc_join_result?;
    eprintln!("trnm-server source candidate drained");
    Ok(())
}

fn accept_loop(
    listener: &TcpListener,
    sender: &SyncSender<QueuedConnection>,
    config: &ServerConfig,
    draining: &SharedDrain,
    worker_failed: &AtomicBool,
    connections: &ConnectionRegistry,
) -> Result<(), ServerError> {
    loop {
        if worker_failed.load(Ordering::Acquire) {
            draining.begin();
            return Err(ServerError::Configuration("connection_worker_panicked"));
        }
        if draining.is_draining() {
            return Ok(());
        }
        match listener.accept() {
            Ok((stream, _peer)) => {
                let id = connections.register(&stream)?;
                match sender.try_send(QueuedConnection { id, stream }) {
                    Ok(()) => {}
                    Err(TrySendError::Full(mut connection)) => {
                        connections.remove(connection.id);
                        configure_connection(&connection.stream, config)?;
                        write_response(&mut connection.stream, &overloaded());
                        let _ = connection.stream.shutdown(Shutdown::Both);
                    }
                    Err(TrySendError::Disconnected(mut connection)) => {
                        connections.remove(connection.id);
                        write_response(&mut connection.stream, &unavailable());
                        let _ = connection.stream.shutdown(Shutdown::Both);
                        draining.begin();
                        return Err(ServerError::Configuration(
                            "connection_worker_queue_disconnected",
                        ));
                    }
                }
            }
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
    receiver: Arc<Mutex<Receiver<QueuedConnection>>>,
    draining: SharedDrain,
    metrics: SharedAppMetrics,
    connections: ConnectionRegistry,
    authority: AuthAuthorityRuntime,
) where
    R: BudgetedRepository,
{
    let mut app = App::with_shared_state(
        repository,
        config.admin_token.clone(),
        metrics,
        draining.clone(),
    )
    .with_auth_authority(authority);

    while let Some(mut connection) = receive_connection(&receiver) {
        if let Err(error) = handle_connection(&mut connection.stream, &mut app, &config, &draining)
        {
            eprintln!("trnm-server connection failed: {error}");
        }
        let _ = connection.stream.shutdown(Shutdown::Both);
        connections.remove(connection.id);
    }
}

fn receive_connection(receiver: &Mutex<Receiver<QueuedConnection>>) -> Option<QueuedConnection> {
    let guard = receiver
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    guard.recv().ok()
}

fn handle_connection<R: Repository>(
    stream: &mut TcpStream,
    app: &mut App<R>,
    config: &ServerConfig,
    draining: &SharedDrain,
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

    if draining.is_draining()
        && (is_readiness(&request) || request_rejected_while_draining(&request))
    {
        write_response(stream, &request_draining_response(&request));
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
        draining.begin();
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
        br#"{"code":"unavailable","message":"Connection capacity is exhausted.","retry":"backoff"}"#
            .to_vec(),
    )
}

fn unavailable() -> Response {
    Response::json(
        503,
        br#"{"code":"unavailable","message":"Service is unavailable.","retry":"backoff"}"#.to_vec(),
    )
}

fn request_draining_response(request: &Request) -> Response {
    if legacy_auth_http_route(&request.method, &request.target).is_some() {
        return super::storage_api::gateway_error(503, 14, "Service is draining.");
    }
    draining_response()
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
    use std::io::Read;
    use std::net::{TcpListener as TestTcpListener, TcpStream as TestTcpStream};

    use super::*;

    #[test]
    fn startup_message_is_static_and_configuration_free() {
        assert_eq!(STARTUP_MESSAGE, "trnm-server source candidate started");
        for forbidden in ["bind", "profile", "worker", "queue", "key", "token"] {
            assert!(!STARTUP_MESSAGE.contains(forbidden));
        }
    }

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
        for target in ["/healthcheck", "/healthcheck?probe=live"] {
            let health = Request::new("GET", target, BTreeMap::new(), Vec::new());
            assert!(!request_rejected_while_draining(&health));
            assert!(!is_readiness(&health));
        }
    }

    #[test]
    fn nakama_http_healthcheck_does_not_bypass_startup_admission() {
        use trnm_contracts::{Digest32, DomainError, StableCode};
        use trnm_persistence_pg::{CommitOutcome, CommitRequest, EntityHead, EntityId};

        #[derive(Clone, Debug)]
        struct UnavailableRepository;
        impl Repository for UnavailableRepository {
            fn bootstrap_entity(
                &mut self,
                _: EntityId,
                _: u64,
                _: Digest32,
                _: u64,
            ) -> Result<EntityHead, DomainError> {
                panic!("startup must not mutate authority")
            }
            fn commit_command(&mut self, _: &CommitRequest) -> Result<CommitOutcome, DomainError> {
                panic!("startup must not commit")
            }
        }
        impl BudgetedRepository for UnavailableRepository {
            fn commit_command_with_budget(
                &mut self,
                _: &CommitRequest,
                _: Duration,
            ) -> Result<CommitOutcome, DomainError> {
                panic!("startup must not retry")
            }
        }
        impl InflightCancellation for UnavailableRepository {
            fn cancel_inflight(&self) -> u64 {
                0
            }
        }

        // An occupied address makes the ordering observable: import admission
        // must fail before bind, even when only public health is desired.
        let occupied = TestTcpListener::bind("127.0.0.1:0").unwrap();
        let mut values = BTreeMap::from([
            (
                "TRNM_SERVER_DATABASE_URL",
                "postgresql://unopened/fixture".to_owned(),
            ),
            ("TRNM_SERVER_DATABASE_PROFILE", "postgresql".to_owned()),
            ("TRNM_SERVER_ALLOW_PLAINTEXT_DATABASE", "true".to_owned()),
            ("TRNM_SERVER_SCHEMA_SOURCE_COMMIT", "0".repeat(40)),
            ("TRNM_SERVER_ADMIN_TOKEN", "a".repeat(32)),
        ]);
        values.insert(
            "TRNM_SERVER_BIND",
            occupied.local_addr().unwrap().to_string(),
        );
        let (_, config) =
            ServerConfig::from_lookup(&["trnm-server".to_owned(), "serve".to_owned()], |name| {
                values.get(name).cloned()
            })
            .unwrap();
        assert!(matches!(
            serve(&config, UnavailableRepository),
            Err(ServerError::Domain(error)) if error.code() == StableCode::FailedPrecondition && error.reason() == "storage_import_admission_unavailable"
        ));
    }

    #[test]
    fn drain_registry_actively_closes_registered_idle_connection() {
        let listener = TestTcpListener::bind("127.0.0.1:0").unwrap();
        let mut client = TestTcpStream::connect(listener.local_addr().unwrap()).unwrap();
        let (server, _) = listener.accept().unwrap();
        client
            .set_read_timeout(Some(Duration::from_secs(1)))
            .unwrap();
        let registry = ConnectionRegistry::default();
        let id = registry.register(&server).unwrap();
        assert_eq!(registry.shutdown_all(), 1);
        let mut byte = [0_u8; 1];
        assert!(matches!(client.read(&mut byte), Ok(0) | Err(_)));
        registry.remove(id);
        assert_eq!(registry.shutdown_all(), 0);
    }

    #[test]
    fn legacy_transport_drain_preserves_gateway_code_for_every_post_query_variant() {
        for path in [
            "/v2/account/authenticate/device",
            "/v2/account/session/refresh",
            "/v2/session/logout",
        ] {
            for query in ["", "?create=false", "?create=false&unknown=x"] {
                let request = Request::new(
                    "POST",
                    format!("{path}{query}"),
                    BTreeMap::new(),
                    b"invalid body must remain unparsed".to_vec(),
                );
                assert!(request_rejected_while_draining(&request));
                let response = request_draining_response(&request);
                assert_eq!(response.status, 503);
                let body: serde_json::Value = serde_json::from_slice(&response.body).unwrap();
                assert_eq!(body["code"], 14);
                assert_eq!(body["message"], "Service is draining.");
                assert!(body.get("retry").is_none());
                assert_eq!(body.as_object().unwrap().len(), 2);
            }
            let request = Request::new("GET", path, BTreeMap::new(), Vec::new());
            assert!(!request_rejected_while_draining(&request));
            let response = request_draining_response(&request);
            let body: serde_json::Value = serde_json::from_slice(&response.body).unwrap();
            assert_eq!(body["code"], "unavailable");
            assert_eq!(body["retry"], "backoff");
        }
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
