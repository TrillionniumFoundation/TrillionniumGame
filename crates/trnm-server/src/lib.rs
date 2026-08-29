#![forbid(unsafe_code)]

use std::fmt;
use std::io::{self, ErrorKind, Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::str;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver, SyncSender, TryRecvError, TrySendError};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use trnm_contracts::{CommandId, Digest32, DomainError, StableCode};
use trnm_persistence_core::{
    CommandIntent, DurableState, EntityId, EventId, EventInput, IntentId, IntentKind, OutboxInput,
    PrepareOutcome, Receipt,
};

const MAX_HEADER_BYTES: usize = 16 * 1024;
const BOOTSTRAP_BODY_BYTES: usize = 56;
const COMMAND_BODY_BYTES: usize = 208;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ServerConfig {
    pub bind: SocketAddr,
    pub worker_count: usize,
    pub queue_capacity: usize,
    pub max_request_bytes: usize,
    pub read_timeout: Duration,
    pub write_timeout: Duration,
}

impl Default for ServerConfig {
    fn default() -> Self {
        Self {
            bind: SocketAddr::from(([127, 0, 0, 1], 7350)),
            worker_count: 4,
            queue_capacity: 128,
            max_request_bytes: 256 * 1024,
            read_timeout: Duration::from_secs(5),
            write_timeout: Duration::from_secs(5),
        }
    }
}

impl ServerConfig {
    pub fn from_env() -> Result<Self, ConfigError> {
        let mut config = Self::default();
        if let Some(value) = read_env("TRNM_SERVER_BIND")? {
            config.bind = value
                .parse()
                .map_err(|_| ConfigError::InvalidValue("TRNM_SERVER_BIND"))?;
        }
        if let Some(value) = read_env("TRNM_SERVER_WORKERS")? {
            config.worker_count = parse_usize(&value, "TRNM_SERVER_WORKERS")?;
        }
        if let Some(value) = read_env("TRNM_SERVER_QUEUE_CAPACITY")? {
            config.queue_capacity = parse_usize(&value, "TRNM_SERVER_QUEUE_CAPACITY")?;
        }
        if let Some(value) = read_env("TRNM_SERVER_MAX_REQUEST_BYTES")? {
            config.max_request_bytes = parse_usize(&value, "TRNM_SERVER_MAX_REQUEST_BYTES")?;
        }
        config.validate()?;
        Ok(config)
    }

    pub fn validate(&self) -> Result<(), ConfigError> {
        if self.worker_count == 0 || self.worker_count > 64 {
            return Err(ConfigError::OutOfRange("worker_count"));
        }
        if self.queue_capacity == 0 || self.queue_capacity > 4096 {
            return Err(ConfigError::OutOfRange("queue_capacity"));
        }
        if !(1024..=1024 * 1024).contains(&self.max_request_bytes) {
            return Err(ConfigError::OutOfRange("max_request_bytes"));
        }
        if self.read_timeout.is_zero() || self.write_timeout.is_zero() {
            return Err(ConfigError::OutOfRange("io_timeout"));
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ConfigError {
    NonUnicode(&'static str),
    InvalidValue(&'static str),
    OutOfRange(&'static str),
}

impl fmt::Display for ConfigError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::NonUnicode(name) => write!(formatter, "environment variable {name} is not UTF-8"),
            Self::InvalidValue(name) => write!(formatter, "configuration value {name} is invalid"),
            Self::OutOfRange(name) => {
                write!(formatter, "configuration value {name} is out of range")
            }
        }
    }
}

impl std::error::Error for ConfigError {}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HttpRequest {
    method: String,
    path: String,
    body: Vec<u8>,
}

impl HttpRequest {
    #[must_use]
    pub fn new(method: impl Into<String>, path: impl Into<String>, body: Vec<u8>) -> Self {
        Self {
            method: method.into(),
            path: path.into(),
            body,
        }
    }

    #[must_use]
    pub fn method(&self) -> &str {
        &self.method
    }

    #[must_use]
    pub fn path(&self) -> &str {
        &self.path
    }

    #[must_use]
    pub fn body(&self) -> &[u8] {
        &self.body
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HttpResponse {
    status: u16,
    content_type: &'static str,
    body: Vec<u8>,
}

impl HttpResponse {
    #[must_use]
    pub fn status(&self) -> u16 {
        self.status
    }

    #[must_use]
    pub fn body(&self) -> &[u8] {
        &self.body
    }

    fn json(status: u16, body: String) -> Self {
        Self {
            status,
            content_type: "application/json",
            body: body.into_bytes(),
        }
    }
}

#[derive(Debug)]
struct SharedState {
    durable: Mutex<DurableState>,
    ready: AtomicBool,
    draining: AtomicBool,
}

#[derive(Clone, Debug)]
pub struct Application {
    shared: Arc<SharedState>,
}

impl Default for Application {
    fn default() -> Self {
        Self::new()
    }
}

impl Application {
    #[must_use]
    pub fn new() -> Self {
        Self {
            shared: Arc::new(SharedState {
                durable: Mutex::new(DurableState::default()),
                ready: AtomicBool::new(false),
                draining: AtomicBool::new(false),
            }),
        }
    }

    #[must_use]
    pub fn is_ready(&self) -> bool {
        self.shared.ready.load(Ordering::Acquire) && !self.shared.draining.load(Ordering::Acquire)
    }

    pub fn handle(&self, request: &HttpRequest) -> HttpResponse {
        match (request.method(), request.path()) {
            ("GET", "/healthz") => HttpResponse::json(200, "{\"status\":\"ok\"}".to_owned()),
            ("GET", "/readyz") if self.is_ready() => {
                HttpResponse::json(200, "{\"status\":\"ready\"}".to_owned())
            }
            ("GET", "/readyz") => HttpResponse::json(503, "{\"status\":\"not_ready\"}".to_owned()),
            ("POST", "/v1/bootstrap") => self.handle_bootstrap(request.body()),
            ("POST", "/v1/command") => self.handle_command(request.body()),
            ("GET", _) | ("POST", _) => HttpResponse::json(
                404,
                "{\"code\":\"not_found\",\"reason\":\"route_not_found\"}".to_owned(),
            ),
            _ => HttpResponse::json(
                405,
                "{\"code\":\"invalid_argument\",\"reason\":\"method_not_allowed\"}".to_owned(),
            ),
        }
    }

    fn handle_bootstrap(&self, body: &[u8]) -> HttpResponse {
        if body.len() != BOOTSTRAP_BODY_BYTES {
            return invalid_body_response(BOOTSTRAP_BODY_BYTES, body.len());
        }
        let entity = EntityId::new(read_array::<16>(body, 0));
        let generation = u64::from_be_bytes(read_array::<8>(body, 16));
        let state = Digest32::new(read_array::<32>(body, 24));
        let mut durable = match self.shared.durable.lock() {
            Ok(value) => value,
            Err(_) => return internal_state_response(),
        };
        match durable.bootstrap(entity, generation, state) {
            Ok(head) => HttpResponse::json(
                201,
                format!(
                    "{{\"outcome\":\"created\",\"revision\":{},\"last_sequence\":{},\"authority_generation\":{}}}",
                    head.revision, head.last_sequence, head.authority_generation
                ),
            ),
            Err(error) => domain_error_response(error),
        }
    }

    fn handle_command(&self, body: &[u8]) -> HttpResponse {
        if body.len() != COMMAND_BODY_BYTES {
            return invalid_body_response(COMMAND_BODY_BYTES, body.len());
        }
        let intent = CommandIntent {
            entity: EntityId::new(read_array::<16>(body, 0)),
            command: CommandId::new(read_array::<16>(body, 16)),
            fingerprint: Digest32::new(read_array::<32>(body, 32)),
            expected_revision: u64::from_be_bytes(read_array::<8>(body, 64)),
            authority_generation: u64::from_be_bytes(read_array::<8>(body, 72)),
            next_state: Digest32::new(read_array::<32>(body, 80)),
            events: vec![EventInput {
                id: EventId::new(read_array::<16>(body, 112)),
                payload: Digest32::new(read_array::<32>(body, 128)),
            }],
            outbox: vec![OutboxInput {
                id: IntentId::new(read_array::<16>(body, 160)),
                kind: IntentKind::Broadcast,
                payload: Digest32::new(read_array::<32>(body, 176)),
            }],
        };
        let mut durable = match self.shared.durable.lock() {
            Ok(value) => value,
            Err(_) => return internal_state_response(),
        };
        match durable.prepare(intent) {
            Ok(PrepareOutcome::Duplicate(receipt)) => receipt_response("duplicate", &receipt),
            Ok(PrepareOutcome::Prepared(prepared)) => match durable.commit(prepared) {
                Ok(receipt) => receipt_response("applied", &receipt),
                Err(error) => domain_error_response(error),
            },
            Err(error) => domain_error_response(error),
        }
    }
}

#[derive(Clone, Debug)]
pub struct Server {
    config: ServerConfig,
    application: Application,
}

impl Server {
    pub fn new(config: ServerConfig, application: Application) -> Result<Self, ConfigError> {
        config.validate()?;
        Ok(Self {
            config,
            application,
        })
    }

    pub fn serve(self, listener: TcpListener, shutdown: Receiver<()>) -> Result<(), ServerError> {
        listener.set_nonblocking(true)?;
        let (sender, receiver) = mpsc::sync_channel(self.config.queue_capacity);
        let receiver = Arc::new(Mutex::new(receiver));
        let mut workers = Vec::with_capacity(self.config.worker_count);
        for index in 0..self.config.worker_count {
            workers.push(spawn_worker(
                index,
                Arc::clone(&receiver),
                self.application.clone(),
                self.config.clone(),
            )?);
        }

        self.application
            .shared
            .draining
            .store(false, Ordering::Release);
        self.application.shared.ready.store(true, Ordering::Release);
        let accept_result = self.accept_loop(&listener, &sender, &shutdown);
        self.application
            .shared
            .ready
            .store(false, Ordering::Release);
        self.application
            .shared
            .draining
            .store(true, Ordering::Release);
        drop(sender);
        for worker in workers {
            worker.join().map_err(|_| ServerError::WorkerPanicked)?;
        }
        accept_result
    }

    fn accept_loop(
        &self,
        listener: &TcpListener,
        sender: &SyncSender<TcpStream>,
        shutdown: &Receiver<()>,
    ) -> Result<(), ServerError> {
        loop {
            match shutdown.try_recv() {
                Ok(()) | Err(TryRecvError::Disconnected) => return Ok(()),
                Err(TryRecvError::Empty) => {}
            }
            match listener.accept() {
                Ok((stream, _)) => match sender.try_send(stream) {
                    Ok(()) => {}
                    Err(TrySendError::Full(mut stream)) => {
                        let response = HttpResponse::json(
                            503,
                            "{\"code\":\"unavailable\",\"reason\":\"request_queue_full\"}"
                                .to_owned(),
                        );
                        let _ = write_response(&mut stream, &response);
                    }
                    Err(TrySendError::Disconnected(_)) => {
                        return Err(ServerError::WorkerChannelClosed);
                    }
                },
                Err(error) if error.kind() == ErrorKind::WouldBlock => {
                    thread::sleep(Duration::from_millis(5));
                }
                Err(error) => return Err(ServerError::Io(error)),
            }
        }
    }
}

#[derive(Debug)]
pub enum ServerError {
    Io(io::Error),
    WorkerSpawn(io::Error),
    WorkerChannelClosed,
    WorkerPanicked,
}

impl fmt::Display for ServerError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Io(error) => write!(formatter, "server I/O error: {error}"),
            Self::WorkerSpawn(error) => write!(formatter, "server worker spawn failed: {error}"),
            Self::WorkerChannelClosed => formatter.write_str("server worker channel closed"),
            Self::WorkerPanicked => formatter.write_str("server worker panicked"),
        }
    }
}

impl std::error::Error for ServerError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Io(error) | Self::WorkerSpawn(error) => Some(error),
            Self::WorkerChannelClosed | Self::WorkerPanicked => None,
        }
    }
}

impl From<io::Error> for ServerError {
    fn from(error: io::Error) -> Self {
        Self::Io(error)
    }
}

#[derive(Debug)]
enum ProtocolError {
    Io(io::Error),
    HeaderTooLarge,
    RequestTooLarge,
    InvalidHeader,
    InvalidContentLength,
    UnsupportedTransferEncoding,
    UnexpectedEnd,
}

impl From<io::Error> for ProtocolError {
    fn from(error: io::Error) -> Self {
        Self::Io(error)
    }
}

fn spawn_worker(
    index: usize,
    receiver: Arc<Mutex<Receiver<TcpStream>>>,
    application: Application,
    config: ServerConfig,
) -> Result<JoinHandle<()>, ServerError> {
    thread::Builder::new()
        .name(format!("trnm-http-{index}"))
        .spawn(move || loop {
            let stream = {
                let guard = match receiver.lock() {
                    Ok(value) => value,
                    Err(_) => return,
                };
                guard.recv()
            };
            let mut stream = match stream {
                Ok(value) => value,
                Err(_) => return,
            };
            let _ = stream.set_read_timeout(Some(config.read_timeout));
            let _ = stream.set_write_timeout(Some(config.write_timeout));
            let response = match read_request(&mut stream, config.max_request_bytes) {
                Ok(request) => application.handle(&request),
                Err(error) => protocol_error_response(&error),
            };
            let _ = write_response(&mut stream, &response);
        })
        .map_err(ServerError::WorkerSpawn)
}

fn read_request(
    stream: &mut TcpStream,
    max_request_bytes: usize,
) -> Result<HttpRequest, ProtocolError> {
    let mut buffer = Vec::with_capacity(1024);
    let header_end = loop {
        if let Some(index) = buffer.windows(4).position(|window| window == b"\r\n\r\n") {
            break index + 4;
        }
        if buffer.len() >= MAX_HEADER_BYTES {
            return Err(ProtocolError::HeaderTooLarge);
        }
        let mut chunk = [0u8; 1024];
        let read = stream.read(&mut chunk)?;
        if read == 0 {
            return Err(ProtocolError::UnexpectedEnd);
        }
        buffer.extend_from_slice(&chunk[..read]);
        if buffer.len() > MAX_HEADER_BYTES + max_request_bytes {
            return Err(ProtocolError::RequestTooLarge);
        }
    };

    let header = str::from_utf8(&buffer[..header_end]).map_err(|_| ProtocolError::InvalidHeader)?;
    let mut lines = header[..header.len() - 4].split("\r\n");
    let request_line = lines.next().ok_or(ProtocolError::InvalidHeader)?;
    let mut request_parts = request_line.split_ascii_whitespace();
    let method = request_parts
        .next()
        .ok_or(ProtocolError::InvalidHeader)?
        .to_owned();
    let path = request_parts
        .next()
        .ok_or(ProtocolError::InvalidHeader)?
        .to_owned();
    let version = request_parts.next().ok_or(ProtocolError::InvalidHeader)?;
    if request_parts.next().is_some()
        || !matches!(version, "HTTP/1.0" | "HTTP/1.1")
        || !matches!(method.as_str(), "GET" | "POST")
        || !path.starts_with('/')
    {
        return Err(ProtocolError::InvalidHeader);
    }

    let mut content_length = None;
    for line in lines {
        let (name, value) = line.split_once(':').ok_or(ProtocolError::InvalidHeader)?;
        let name = name.trim();
        let value = value.trim();
        if name.eq_ignore_ascii_case("content-length") {
            if content_length.is_some() {
                return Err(ProtocolError::InvalidContentLength);
            }
            content_length = Some(
                value
                    .parse::<usize>()
                    .map_err(|_| ProtocolError::InvalidContentLength)?,
            );
        }
        if name.eq_ignore_ascii_case("transfer-encoding") {
            return Err(ProtocolError::UnsupportedTransferEncoding);
        }
    }
    let content_length = content_length.unwrap_or(0);
    if content_length > max_request_bytes {
        return Err(ProtocolError::RequestTooLarge);
    }
    let required = header_end
        .checked_add(content_length)
        .ok_or(ProtocolError::RequestTooLarge)?;
    while buffer.len() < required {
        let mut chunk = [0u8; 4096];
        let read = stream.read(&mut chunk)?;
        if read == 0 {
            return Err(ProtocolError::UnexpectedEnd);
        }
        buffer.extend_from_slice(&chunk[..read]);
        if buffer.len() > required || buffer.len() > MAX_HEADER_BYTES + max_request_bytes {
            return Err(ProtocolError::RequestTooLarge);
        }
    }
    Ok(HttpRequest::new(
        method,
        path,
        buffer[header_end..required].to_vec(),
    ))
}

fn write_response(stream: &mut TcpStream, response: &HttpResponse) -> io::Result<()> {
    let reason = match response.status {
        200 => "OK",
        201 => "Created",
        400 => "Bad Request",
        401 => "Unauthorized",
        403 => "Forbidden",
        404 => "Not Found",
        405 => "Method Not Allowed",
        409 => "Conflict",
        412 => "Precondition Failed",
        413 => "Content Too Large",
        429 => "Too Many Requests",
        500 => "Internal Server Error",
        503 => "Service Unavailable",
        _ => "Error",
    };
    let header = format!(
        "HTTP/1.1 {} {}\r\nContent-Type: {}\r\nContent-Length: {}\r\nConnection: close\r\nX-Content-Type-Options: nosniff\r\n\r\n",
        response.status,
        reason,
        response.content_type,
        response.body.len()
    );
    stream.write_all(header.as_bytes())?;
    stream.write_all(&response.body)?;
    stream.flush()
}

fn protocol_error_response(error: &ProtocolError) -> HttpResponse {
    match error {
        ProtocolError::RequestTooLarge | ProtocolError::HeaderTooLarge => HttpResponse::json(
            413,
            "{\"code\":\"resource_exhausted\",\"reason\":\"request_too_large\"}".to_owned(),
        ),
        ProtocolError::Io(source) if source.kind() == ErrorKind::TimedOut => HttpResponse::json(
            400,
            "{\"code\":\"invalid_argument\",\"reason\":\"request_timeout\"}".to_owned(),
        ),
        ProtocolError::Io(_)
        | ProtocolError::InvalidHeader
        | ProtocolError::InvalidContentLength
        | ProtocolError::UnsupportedTransferEncoding
        | ProtocolError::UnexpectedEnd => HttpResponse::json(
            400,
            "{\"code\":\"invalid_argument\",\"reason\":\"malformed_http_request\"}".to_owned(),
        ),
    }
}

fn receipt_response(outcome: &str, receipt: &Receipt) -> HttpResponse {
    HttpResponse::json(
        200,
        format!(
            "{{\"outcome\":\"{}\",\"revision\":{},\"first_sequence\":{},\"last_sequence\":{},\"event_count\":{},\"outbox_count\":{}}}",
            outcome,
            receipt.revision,
            receipt
                .first_sequence
                .map_or_else(|| "null".to_owned(), |value| value.to_string()),
            receipt.last_sequence,
            receipt.event_count,
            receipt.outbox.len()
        ),
    )
}

fn domain_error_response(error: DomainError) -> HttpResponse {
    let status = match error.code() {
        StableCode::InvalidArgument | StableCode::OutOfRange => 400,
        StableCode::Unauthenticated => 401,
        StableCode::PermissionDenied => 403,
        StableCode::NotFound => 404,
        StableCode::AlreadyExists | StableCode::Aborted => 409,
        StableCode::FailedPrecondition => 412,
        StableCode::ResourceExhausted => 429,
        StableCode::Unavailable => 503,
        StableCode::Unimplemented | StableCode::Internal | StableCode::DataLoss => 500,
    };
    HttpResponse::json(
        status,
        format!(
            "{{\"code\":\"{}\",\"reason\":\"{}\"}}",
            error.code().as_str(),
            error.reason()
        ),
    )
}

fn invalid_body_response(expected: usize, actual: usize) -> HttpResponse {
    HttpResponse::json(
        400,
        format!(
            "{{\"code\":\"invalid_argument\",\"reason\":\"invalid_body_length\",\"expected\":{expected},\"actual\":{actual}}}"
        ),
    )
}

fn internal_state_response() -> HttpResponse {
    HttpResponse::json(
        500,
        "{\"code\":\"internal\",\"reason\":\"state_lock_poisoned\"}".to_owned(),
    )
}

fn read_array<const N: usize>(body: &[u8], offset: usize) -> [u8; N] {
    body[offset..offset + N]
        .try_into()
        .expect("body length is validated before fixed-width decoding")
}

fn read_env(name: &'static str) -> Result<Option<String>, ConfigError> {
    match std::env::var(name) {
        Ok(value) => Ok(Some(value)),
        Err(std::env::VarError::NotPresent) => Ok(None),
        Err(std::env::VarError::NotUnicode(_)) => Err(ConfigError::NonUnicode(name)),
    }
}

fn parse_usize(value: &str, name: &'static str) -> Result<usize, ConfigError> {
    value
        .parse::<usize>()
        .map_err(|_| ConfigError::InvalidValue(name))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc;

    fn bootstrap_body() -> Vec<u8> {
        let mut body = Vec::with_capacity(BOOTSTRAP_BODY_BYTES);
        body.extend_from_slice(&[1; 16]);
        body.extend_from_slice(&1u64.to_be_bytes());
        body.extend_from_slice(&[2; 32]);
        body
    }

    fn command_body(command: u8, expected_revision: u64) -> Vec<u8> {
        let mut body = Vec::with_capacity(COMMAND_BODY_BYTES);
        body.extend_from_slice(&[1; 16]);
        body.extend_from_slice(&[command; 16]);
        body.extend_from_slice(&[command.wrapping_add(1); 32]);
        body.extend_from_slice(&expected_revision.to_be_bytes());
        body.extend_from_slice(&1u64.to_be_bytes());
        body.extend_from_slice(&[command.wrapping_add(2); 32]);
        body.extend_from_slice(&[command.wrapping_add(3); 16]);
        body.extend_from_slice(&[command.wrapping_add(4); 32]);
        body.extend_from_slice(&[command.wrapping_add(5); 16]);
        body.extend_from_slice(&[command.wrapping_add(6); 32]);
        assert_eq!(body.len(), COMMAND_BODY_BYTES);
        body
    }

    #[test]
    fn configuration_is_bounded() {
        let mut config = ServerConfig::default();
        assert!(config.validate().is_ok());
        config.worker_count = 0;
        assert_eq!(
            config.validate().unwrap_err(),
            ConfigError::OutOfRange("worker_count")
        );
        config.worker_count = 1;
        config.max_request_bytes = 1;
        assert_eq!(
            config.validate().unwrap_err(),
            ConfigError::OutOfRange("max_request_bytes")
        );
    }

    #[test]
    fn command_is_atomic_idempotent_and_revision_fenced() {
        let application = Application::new();
        let bootstrap =
            application.handle(&HttpRequest::new("POST", "/v1/bootstrap", bootstrap_body()));
        assert_eq!(bootstrap.status(), 201);

        let body = command_body(10, 0);
        let applied = application.handle(&HttpRequest::new("POST", "/v1/command", body.clone()));
        assert_eq!(applied.status(), 200);
        assert!(str::from_utf8(applied.body())
            .unwrap()
            .contains("\"outcome\":\"applied\""));

        let duplicate = application.handle(&HttpRequest::new("POST", "/v1/command", body));
        assert_eq!(duplicate.status(), 200);
        assert!(str::from_utf8(duplicate.body())
            .unwrap()
            .contains("\"outcome\":\"duplicate\""));

        let stale = application.handle(&HttpRequest::new(
            "POST",
            "/v1/command",
            command_body(20, 0),
        ));
        assert_eq!(stale.status(), 409);
        assert!(str::from_utf8(stale.body())
            .unwrap()
            .contains("entity_revision_mismatch"));
    }

    #[test]
    fn readiness_tracks_lifecycle_and_health_is_liveness_only() {
        let application = Application::new();
        assert_eq!(
            application
                .handle(&HttpRequest::new("GET", "/readyz", Vec::new()))
                .status(),
            503
        );
        assert_eq!(
            application
                .handle(&HttpRequest::new("GET", "/healthz", Vec::new()))
                .status(),
            200
        );
    }

    #[test]
    fn bounded_server_accepts_health_and_drains() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let application = Application::new();
        let server = Server::new(
            ServerConfig {
                bind: address,
                worker_count: 2,
                queue_capacity: 4,
                ..ServerConfig::default()
            },
            application,
        )
        .unwrap();
        let (shutdown_tx, shutdown_rx) = mpsc::channel();
        let handle = thread::spawn(move || server.serve(listener, shutdown_rx).unwrap());

        let mut stream = (0..100)
            .find_map(|_| match TcpStream::connect(address) {
                Ok(value) => Some(value),
                Err(_) => {
                    thread::sleep(Duration::from_millis(5));
                    None
                }
            })
            .expect("server did not accept connections");
        stream
            .write_all(b"GET /healthz HTTP/1.1\r\nHost: localhost\r\n\r\n")
            .unwrap();
        let mut response = Vec::new();
        stream.read_to_end(&mut response).unwrap();
        assert!(response.starts_with(b"HTTP/1.1 200 OK\r\n"));
        assert!(response.ends_with(b"{\"status\":\"ok\"}"));

        shutdown_tx.send(()).unwrap();
        handle.join().unwrap();
    }

    #[test]
    fn malformed_and_oversized_bodies_fail_closed() {
        let application = Application::new();
        let response = application.handle(&HttpRequest::new(
            "POST",
            "/v1/command",
            vec![0; COMMAND_BODY_BYTES - 1],
        ));
        assert_eq!(response.status(), 400);
        assert!(str::from_utf8(response.body())
            .unwrap()
            .contains("invalid_body_length"));
    }
}
