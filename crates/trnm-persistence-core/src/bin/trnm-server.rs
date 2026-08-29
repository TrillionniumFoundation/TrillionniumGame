#![forbid(unsafe_code)]

use std::collections::BTreeMap;
use std::env;
use std::fmt::Write as _;
use std::fs;
use std::io::{self, Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::str::FromStr;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use trnm_contracts::{CommandId, Digest32, DomainError, StableCode};
use trnm_persistence_core::{
    CommandIntent, DurableState, EntityId, EventId, EventInput, IntentId, IntentKind,
    OutboxInput, PrepareOutcome, Receipt,
};

const DEFAULT_LISTEN: &str = "127.0.0.1:7350";
const DEFAULT_READ_TIMEOUT_MS: u64 = 5_000;
const MAX_REQUEST_BYTES: usize = 64 * 1024;
const ENTITY_BYTES: [u8; 16] = [1; 16];
const INITIAL_STATE_BYTES: [u8; 32] = [0x41; 32];

#[derive(Clone, Debug, Eq, PartialEq)]
struct ServeConfig {
    listen: SocketAddr,
    max_requests: Option<u64>,
    read_timeout: Duration,
}

#[derive(Clone, Debug, Eq, PartialEq)]
enum CliCommand {
    Serve(ServeConfig),
    Healthcheck { address: SocketAddr },
    MigrationContract { profile: DatabaseProfile },
    Help,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum DatabaseProfile {
    PostgreSql,
    CockroachDb,
}

impl DatabaseProfile {
    fn parse(value: &str) -> Result<Self, String> {
        match value {
            "postgresql" => Ok(Self::PostgreSql),
            "cockroachdb" => Ok(Self::CockroachDb),
            _ => Err(format!(
                "unsupported database profile {value:?}; expected postgresql or cockroachdb"
            )),
        }
    }

    const fn name(self) -> &'static str {
        match self {
            Self::PostgreSql => "postgresql",
            Self::CockroachDb => "cockroachdb",
        }
    }

    const fn migration_path(self) -> &'static str {
        match self {
            Self::PostgreSql => "migrations/postgresql/0001_foundation_up.sql",
            Self::CockroachDb => "migrations/cockroachdb/0001_foundation_up.sql",
        }
    }
}

#[derive(Debug)]
struct App {
    state: Mutex<DurableState>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct CommandResponse {
    duplicate: bool,
    receipt: Receipt,
}

#[derive(Debug)]
struct HttpRequest {
    method: String,
    path: String,
    headers: BTreeMap<String, String>,
    body: Vec<u8>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct HttpStatus {
    code: u16,
    reason: &'static str,
}

impl App {
    fn new() -> Result<Self, DomainError> {
        let mut state = DurableState::default();
        state.bootstrap(
            EntityId::new(ENTITY_BYTES),
            1,
            Digest32::new(INITIAL_STATE_BYTES),
        )?;
        Ok(Self {
            state: Mutex::new(state),
        })
    }

    fn execute_command(
        &self,
        command_byte: u8,
        expected_revision: u64,
        authority_generation: u64,
    ) -> Result<CommandResponse, DomainError> {
        if !(1..=200).contains(&command_byte) {
            return Err(DomainError::new(
                StableCode::InvalidArgument,
                "command_byte_out_of_range",
                trnm_contracts::RetryClass::Never,
            ));
        }

        let intent = CommandIntent {
            entity: EntityId::new(ENTITY_BYTES),
            command: CommandId::new([command_byte; 16]),
            fingerprint: Digest32::new([command_byte; 32]),
            expected_revision,
            authority_generation,
            next_state: Digest32::new([command_byte.wrapping_add(10); 32]),
            events: vec![EventInput {
                id: EventId::new([command_byte.wrapping_add(20); 16]),
                payload: Digest32::new([command_byte.wrapping_add(21); 32]),
            }],
            outbox: vec![OutboxInput {
                id: IntentId::new([command_byte.wrapping_add(30); 16]),
                kind: IntentKind::Broadcast,
                payload: Digest32::new([command_byte.wrapping_add(31); 32]),
            }],
        };

        let mut state = self.state.lock().map_err(|_| {
            DomainError::new(
                StableCode::Internal,
                "state_lock_poisoned",
                trnm_contracts::RetryClass::Never,
            )
        })?;
        match state.prepare(intent)? {
            PrepareOutcome::Prepared(prepared) => Ok(CommandResponse {
                duplicate: false,
                receipt: state.commit(prepared)?,
            }),
            PrepareOutcome::Duplicate(receipt) => Ok(CommandResponse {
                duplicate: true,
                receipt,
            }),
        }
    }
}

fn main() {
    if let Err(error) = run(env::args().skip(1)) {
        eprintln!("trnm-server: {error}");
        std::process::exit(1);
    }
}

fn run(arguments: impl Iterator<Item = String>) -> Result<(), String> {
    match parse_cli(arguments)? {
        CliCommand::Serve(config) => {
            let listener = TcpListener::bind(config.listen)
                .map_err(|error| format!("cannot bind {}: {error}", config.listen))?;
            let address = listener
                .local_addr()
                .map_err(|error| format!("cannot read listener address: {error}"))?;
            println!(
                "trnm-server status=source-candidate listen={address} compatibility_credit=false"
            );
            serve_listener(listener, config)
        }
        CliCommand::Healthcheck { address } => healthcheck(address),
        CliCommand::MigrationContract { profile } => validate_migration_contract(profile),
        CliCommand::Help => {
            print_help();
            Ok(())
        }
    }
}

fn parse_cli(arguments: impl Iterator<Item = String>) -> Result<CliCommand, String> {
    let mut arguments = arguments.peekable();
    let Some(command) = arguments.next() else {
        return Ok(CliCommand::Help);
    };
    match command.as_str() {
        "serve" => {
            let mut listen = SocketAddr::from_str(DEFAULT_LISTEN)
                .map_err(|error| format!("invalid default listener: {error}"))?;
            let mut max_requests = None;
            let mut read_timeout = Duration::from_millis(DEFAULT_READ_TIMEOUT_MS);
            while let Some(argument) = arguments.next() {
                match argument.as_str() {
                    "--listen" => {
                        let value = arguments
                            .next()
                            .ok_or_else(|| "--listen requires an address".to_owned())?;
                        listen = value
                            .parse()
                            .map_err(|error| format!("invalid --listen value {value:?}: {error}"))?;
                    }
                    "--max-requests" => {
                        let value = arguments
                            .next()
                            .ok_or_else(|| "--max-requests requires a number".to_owned())?;
                        let parsed = value.parse::<u64>().map_err(|error| {
                            format!("invalid --max-requests value {value:?}: {error}")
                        })?;
                        max_requests = (parsed != 0).then_some(parsed);
                    }
                    "--read-timeout-ms" => {
                        let value = arguments
                            .next()
                            .ok_or_else(|| "--read-timeout-ms requires a number".to_owned())?;
                        let parsed = value.parse::<u64>().map_err(|error| {
                            format!("invalid --read-timeout-ms value {value:?}: {error}")
                        })?;
                        if parsed == 0 {
                            return Err("--read-timeout-ms must be greater than zero".to_owned());
                        }
                        read_timeout = Duration::from_millis(parsed);
                    }
                    other => return Err(format!("unknown serve option {other:?}")),
                }
            }
            Ok(CliCommand::Serve(ServeConfig {
                listen,
                max_requests,
                read_timeout,
            }))
        }
        "healthcheck" => {
            let mut address = SocketAddr::from_str(DEFAULT_LISTEN)
                .map_err(|error| format!("invalid default address: {error}"))?;
            while let Some(argument) = arguments.next() {
                match argument.as_str() {
                    "--address" => {
                        let value = arguments
                            .next()
                            .ok_or_else(|| "--address requires a value".to_owned())?;
                        address = value
                            .parse()
                            .map_err(|error| format!("invalid --address value {value:?}: {error}"))?;
                    }
                    other => return Err(format!("unknown healthcheck option {other:?}")),
                }
            }
            Ok(CliCommand::Healthcheck { address })
        }
        "migrate-contract" => {
            let mut profile = None;
            while let Some(argument) = arguments.next() {
                match argument.as_str() {
                    "--profile" => {
                        let value = arguments
                            .next()
                            .ok_or_else(|| "--profile requires a value".to_owned())?;
                        profile = Some(DatabaseProfile::parse(&value)?);
                    }
                    other => return Err(format!("unknown migrate-contract option {other:?}")),
                }
            }
            Ok(CliCommand::MigrationContract {
                profile: profile.ok_or_else(|| "--profile is required".to_owned())?,
            })
        }
        "help" | "--help" | "-h" => Ok(CliCommand::Help),
        other => Err(format!("unknown command {other:?}")),
    }
}

fn serve_listener(listener: TcpListener, config: ServeConfig) -> Result<(), String> {
    let app = Arc::new(App::new().map_err(|error| error.to_string())?);
    let mut completed = 0_u64;
    for incoming in listener.incoming() {
        let mut stream = incoming.map_err(|error| format!("accept failed: {error}"))?;
        stream
            .set_read_timeout(Some(config.read_timeout))
            .map_err(|error| format!("cannot set read timeout: {error}"))?;
        stream
            .set_write_timeout(Some(config.read_timeout))
            .map_err(|error| format!("cannot set write timeout: {error}"))?;
        handle_connection(&app, &mut stream)
            .unwrap_or_else(|error| eprintln!("trnm-server request failed: {error}"));
        completed = completed
            .checked_add(1)
            .ok_or_else(|| "request counter overflow".to_owned())?;
        if config.max_requests.is_some_and(|limit| completed >= limit) {
            break;
        }
    }
    Ok(())
}

fn handle_connection(app: &App, stream: &mut TcpStream) -> Result<(), String> {
    let request = read_http_request(stream).map_err(|error| format!("request read: {error}"))?;
    let response = route(app, &request);
    write_http_response(stream, response.0, &response.1, &response.2)
        .map_err(|error| format!("response write: {error}"))
}

fn route(
    app: &App,
    request: &HttpRequest,
) -> (HttpStatus, Vec<(&'static str, &'static str)>, Vec<u8>) {
    match (request.method.as_str(), request.path.as_str()) {
        ("GET", "/healthz") => json_response(
            HttpStatus {
                code: 200,
                reason: "OK",
            },
            r#"{"status":"healthy","compatibility_credit":false}"#,
        ),
        ("GET", "/readyz") => json_response(
            HttpStatus {
                code: 200,
                reason: "OK",
            },
            r#"{"status":"ready-source-candidate","compatibility_credit":false}"#,
        ),
        ("GET", "/version") => json_response(
            HttpStatus {
                code: 200,
                reason: "OK",
            },
            r#"{"component":"trnm-server","status":"source-candidate","c1":false,"c2":false}"#,
        ),
        ("POST", "/v1/command") => route_command(app, request),
        ("GET", "/v1/realtime") => (
            HttpStatus {
                code: 426,
                reason: "Upgrade Required",
            },
            vec![
                ("Content-Type", "application/json"),
                ("Upgrade", "websocket"),
                ("Connection", "Upgrade"),
            ],
            br#"{"error":"websocket_adapter_not_implemented","compatibility_credit":false}"#
                .to_vec(),
        ),
        _ => json_response(
            HttpStatus {
                code: 404,
                reason: "Not Found",
            },
            r#"{"error":"route_not_found"}"#,
        ),
    }
}

fn route_command(
    app: &App,
    request: &HttpRequest,
) -> (HttpStatus, Vec<(&'static str, &'static str)>, Vec<u8>) {
    if !request.body.is_empty() {
        return json_response(
            HttpStatus {
                code: 400,
                reason: "Bad Request",
            },
            r#"{"error":"body_must_be_empty_in_source_candidate"}"#,
        );
    }
    let parsed = (|| {
        let command = parse_required_header::<u8>(request, "x-trnm-command-byte")?;
        let revision = parse_required_header::<u64>(request, "x-trnm-expected-revision")?;
        let generation = parse_required_header::<u64>(request, "x-trnm-authority-generation")?;
        app.execute_command(command, revision, generation)
            .map_err(CommandRouteError::Domain)
    })();

    match parsed {
        Ok(response) => {
            let receipt = response.receipt;
            let mut body = String::with_capacity(256);
            write!(
                body,
                "{{\"outcome\":\"{}\",\"revision\":{},\"first_event_sequence\":{},\"last_event_sequence\":{},\"event_count\":{},\"outbox_count\":{},\"compatibility_credit\":false}}",
                if response.duplicate { "duplicate" } else { "applied" },
                receipt.revision,
                receipt
                    .first_sequence
                    .map_or_else(|| "null".to_owned(), |value| value.to_string()),
                receipt.last_sequence,
                receipt.event_count,
                receipt.outbox.len(),
            )
            .expect("writing to String cannot fail");
            json_response(
                HttpStatus {
                    code: 200,
                    reason: "OK",
                },
                &body,
            )
        }
        Err(CommandRouteError::Header(reason)) => json_response(
            HttpStatus {
                code: 400,
                reason: "Bad Request",
            },
            &format!("{{\"error\":\"{reason}\"}}"),
        ),
        Err(CommandRouteError::Domain(error)) => {
            let status = status_for_domain_error(error);
            json_response(
                status,
                &format!(
                    "{{\"code\":\"{}\",\"reason\":\"{}\",\"compatibility_credit\":false}}",
                    error.code().as_str(),
                    error.reason()
                ),
            )
        }
    }
}

#[derive(Debug)]
enum CommandRouteError {
    Header(String),
    Domain(DomainError),
}

fn parse_required_header<T>(request: &HttpRequest, name: &str) -> Result<T, CommandRouteError>
where
    T: FromStr,
    T::Err: std::fmt::Display,
{
    let value = request
        .headers
        .get(name)
        .ok_or_else(|| CommandRouteError::Header(format!("missing_{name}")))?;
    value
        .parse()
        .map_err(|error| CommandRouteError::Header(format!("invalid_{name}:{error}")))
}

const fn status_for_domain_error(error: DomainError) -> HttpStatus {
    match error.code() {
        StableCode::InvalidArgument => HttpStatus {
            code: 400,
            reason: "Bad Request",
        },
        StableCode::NotFound => HttpStatus {
            code: 404,
            reason: "Not Found",
        },
        StableCode::AlreadyExists | StableCode::Aborted => HttpStatus {
            code: 409,
            reason: "Conflict",
        },
        StableCode::PermissionDenied => HttpStatus {
            code: 403,
            reason: "Forbidden",
        },
        StableCode::Unauthenticated => HttpStatus {
            code: 401,
            reason: "Unauthorized",
        },
        StableCode::ResourceExhausted => HttpStatus {
            code: 429,
            reason: "Too Many Requests",
        },
        StableCode::FailedPrecondition | StableCode::OutOfRange => HttpStatus {
            code: 412,
            reason: "Precondition Failed",
        },
        StableCode::Unimplemented => HttpStatus {
            code: 501,
            reason: "Not Implemented",
        },
        StableCode::Unavailable => HttpStatus {
            code: 503,
            reason: "Service Unavailable",
        },
        StableCode::Internal | StableCode::DataLoss => HttpStatus {
            code: 500,
            reason: "Internal Server Error",
        },
    }
}

fn json_response(
    status: HttpStatus,
    body: &str,
) -> (HttpStatus, Vec<(&'static str, &'static str)>, Vec<u8>) {
    (
        status,
        vec![("Content-Type", "application/json")],
        body.as_bytes().to_vec(),
    )
}

fn read_http_request(stream: &mut TcpStream) -> io::Result<HttpRequest> {
    let mut bytes = Vec::with_capacity(4_096);
    let mut chunk = [0_u8; 4_096];
    let mut header_end = None;
    let mut expected_length = None;

    loop {
        let read = stream.read(&mut chunk)?;
        if read == 0 {
            break;
        }
        bytes.extend_from_slice(&chunk[..read]);
        if bytes.len() > MAX_REQUEST_BYTES {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "request exceeds 64 KiB source-candidate limit",
            ));
        }
        if header_end.is_none() {
            header_end = find_subsequence(&bytes, b"\r\n\r\n");
            if let Some(end) = header_end {
                let headers = std::str::from_utf8(&bytes[..end]).map_err(|_| {
                    io::Error::new(io::ErrorKind::InvalidData, "headers are not UTF-8")
                })?;
                expected_length = Some(parse_content_length(headers)?);
            }
        }
        if let (Some(end), Some(content_length)) = (header_end, expected_length) {
            let body_start = end + 4;
            if bytes.len() >= body_start + content_length {
                break;
            }
        }
    }

    let end = header_end.ok_or_else(|| {
        io::Error::new(io::ErrorKind::InvalidData, "incomplete HTTP request headers")
    })?;
    let head = std::str::from_utf8(&bytes[..end])
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "headers are not UTF-8"))?;
    let mut lines = head.split("\r\n");
    let request_line = lines
        .next()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "missing request line"))?;
    let mut parts = request_line.split_whitespace();
    let method = parts
        .next()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "missing method"))?;
    let path = parts
        .next()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "missing path"))?;
    let version = parts
        .next()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "missing HTTP version"))?;
    if parts.next().is_some() || version != "HTTP/1.1" {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "only a three-part HTTP/1.1 request line is supported",
        ));
    }

    let mut headers = BTreeMap::new();
    for line in lines {
        let (name, value) = line.split_once(':').ok_or_else(|| {
            io::Error::new(io::ErrorKind::InvalidData, "malformed HTTP header")
        })?;
        let normalized = name.trim().to_ascii_lowercase();
        if normalized.is_empty() || headers.insert(normalized, value.trim().to_owned()).is_some() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "empty or duplicate HTTP header",
            ));
        }
    }

    let content_length = parse_content_length(head)?;
    let body_start = end + 4;
    let body_end = body_start
        .checked_add(content_length)
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "body length overflow"))?;
    if bytes.len() < body_end {
        return Err(io::Error::new(
            io::ErrorKind::UnexpectedEof,
            "incomplete HTTP body",
        ));
    }

    Ok(HttpRequest {
        method: method.to_owned(),
        path: path.to_owned(),
        headers,
        body: bytes[body_start..body_end].to_vec(),
    })
}

fn parse_content_length(headers: &str) -> io::Result<usize> {
    let mut value = None;
    for line in headers.split("\r\n").skip(1) {
        let Some((name, raw)) = line.split_once(':') else {
            continue;
        };
        if name.trim().eq_ignore_ascii_case("content-length") {
            if value.is_some() {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "duplicate Content-Length",
                ));
            }
            value = Some(raw.trim().parse::<usize>().map_err(|_| {
                io::Error::new(io::ErrorKind::InvalidData, "invalid Content-Length")
            })?);
        }
    }
    Ok(value.unwrap_or(0))
}

fn find_subsequence(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack
        .windows(needle.len())
        .position(|window| window == needle)
}

fn write_http_response(
    stream: &mut TcpStream,
    status: HttpStatus,
    headers: &[(&str, &str)],
    body: &[u8],
) -> io::Result<()> {
    write!(stream, "HTTP/1.1 {} {}\r\n", status.code, status.reason)?;
    for (name, value) in headers {
        write!(stream, "{name}: {value}\r\n")?;
    }
    write!(
        stream,
        "Content-Length: {}\r\nConnection: close\r\nX-Trnm-Claim: source-candidate\r\n\r\n",
        body.len()
    )?;
    stream.write_all(body)?;
    stream.flush()
}

fn healthcheck(address: SocketAddr) -> Result<(), String> {
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_secs(3))
        .map_err(|error| format!("connect to {address}: {error}"))?;
    stream
        .set_read_timeout(Some(Duration::from_secs(3)))
        .map_err(|error| format!("set healthcheck timeout: {error}"))?;
    stream
        .write_all(b"GET /healthz HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        .map_err(|error| format!("write healthcheck request: {error}"))?;
    let mut response = String::new();
    stream
        .read_to_string(&mut response)
        .map_err(|error| format!("read healthcheck response: {error}"))?;
    if !response.starts_with("HTTP/1.1 200 OK\r\n") || !response.contains("\"healthy\"") {
        return Err("healthcheck response was not healthy".to_owned());
    }
    println!("trnm-server healthcheck=success address={address}");
    Ok(())
}

fn repository_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn validate_migration_contract(profile: DatabaseProfile) -> Result<(), String> {
    let relative = profile.migration_path();
    let path = repository_root().join(relative);
    let source = fs::read_to_string(&path)
        .map_err(|error| format!("cannot read {}: {error}", path.display()))?;
    if !source.starts_with("BEGIN;") || !source.trim_end().ends_with("COMMIT;") {
        return Err(format!("{relative}: migration must be wrapped by BEGIN/COMMIT"));
    }
    let required_tables = [
        "trnm_schema_metadata",
        "trnm_entity_heads",
        "trnm_command_receipts",
        "trnm_events",
        "trnm_outbox",
        "trnm_command_outbox",
        "trnm_authority_leases",
        "trnm_session_families",
        "trnm_refresh_tokens",
        "trnm_storage_objects",
    ];
    for table in required_tables {
        if !source.contains(&format!("CREATE TABLE {table}")) {
            return Err(format!("{relative}: missing table {table}"));
        }
    }
    println!(
        "profile={} migration={} bytes={} table_count=10 execution=false compatibility_credit=false",
        profile.name(),
        relative,
        source.len()
    );
    Ok(())
}

fn print_help() {
    println!(
        "trnm-server source candidate\n\n\
         Commands:\n\
           serve [--listen ADDR] [--max-requests N] [--read-timeout-ms N]\n\
           healthcheck [--address ADDR]\n\
           migrate-contract --profile postgresql|cockroachdb\n\n\
         This binary proves only a bounded Rust composition slice. It does not claim\n\
         Nakama wire compatibility, live database migration, gRPC, WebSocket, C1-C5,\n\
         production readiness, public online approval or Nakama retirement."
    );
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::thread;

    #[test]
    fn cli_is_typed_and_rejects_unknown_arguments() {
        assert_eq!(
            parse_cli(["migrate-contract", "--profile", "postgresql"].map(str::to_owned))
                .unwrap(),
            CliCommand::MigrationContract {
                profile: DatabaseProfile::PostgreSql
            }
        );
        assert!(parse_cli(["serve", "--unknown"].map(str::to_owned)).is_err());
        assert!(parse_cli(["migrate-contract"].map(str::to_owned)).is_err());
    }

    #[test]
    fn command_path_applies_and_replays_exact_receipt() {
        let app = App::new().unwrap();
        let applied = app.execute_command(2, 0, 1).unwrap();
        assert!(!applied.duplicate);
        assert_eq!(applied.receipt.revision, 1);
        assert_eq!(applied.receipt.event_count, 1);
        assert_eq!(applied.receipt.outbox.len(), 1);

        let duplicate = app.execute_command(2, 0, 1).unwrap();
        assert!(duplicate.duplicate);
        assert_eq!(duplicate.receipt, applied.receipt);

        let stale = app.execute_command(3, 0, 1).unwrap_err();
        assert_eq!(stale.reason(), "entity_revision_mismatch");
        let wrong_generation = app.execute_command(3, 1, 2).unwrap_err();
        assert_eq!(wrong_generation.reason(), "authority_generation_mismatch");
    }

    #[test]
    fn migration_contract_uses_authoritative_profiles() {
        validate_migration_contract(DatabaseProfile::PostgreSql).unwrap();
        validate_migration_contract(DatabaseProfile::CockroachDb).unwrap();
    }

    #[test]
    fn bounded_http_server_reports_health_and_commits_command() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let worker = thread::spawn(move || {
            serve_listener(
                listener,
                ServeConfig {
                    listen: address,
                    max_requests: Some(2),
                    read_timeout: Duration::from_secs(3),
                },
            )
            .unwrap();
        });

        let mut health = TcpStream::connect(address).unwrap();
        health
            .write_all(b"GET /healthz HTTP/1.1\r\nHost: localhost\r\n\r\n")
            .unwrap();
        let mut health_response = String::new();
        health.read_to_string(&mut health_response).unwrap();
        assert!(health_response.starts_with("HTTP/1.1 200 OK"));
        assert!(health_response.contains("healthy"));

        let mut command = TcpStream::connect(address).unwrap();
        command
            .write_all(
                b"POST /v1/command HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\nX-Trnm-Command-Byte: 7\r\nX-Trnm-Expected-Revision: 0\r\nX-Trnm-Authority-Generation: 1\r\n\r\n",
            )
            .unwrap();
        let mut command_response = String::new();
        command.read_to_string(&mut command_response).unwrap();
        assert!(command_response.starts_with("HTTP/1.1 200 OK"));
        assert!(command_response.contains("\"outcome\":\"applied\""));
        assert!(command_response.contains("\"outbox_count\":1"));

        worker.join().unwrap();
    }

    #[test]
    fn malformed_and_duplicate_headers_fail_closed() {
        let request = b"POST /v1/command HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\nContent-Length: 0\r\n\r\n";
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let client = thread::spawn(move || {
            let mut stream = TcpStream::connect(address).unwrap();
            stream.write_all(request).unwrap();
        });
        let (mut stream, _) = listener.accept().unwrap();
        assert!(read_http_request(&mut stream).is_err());
        client.join().unwrap();
    }
}
