#![forbid(unsafe_code)]

use std::env;
use std::error::Error;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::process::ExitCode;
use std::time::Duration;

use trnm_contracts::{CommandId, Digest32, StableCode};
use trnm_persistence_pg::{
    CommitOutcome, CommitRequest, DatabaseProfile, EntityId, EventId, EventInput, IntentId,
    IntentKind, OutboxInput, PgRepository,
};

const MAX_REQUEST_BYTES: usize = 16 * 1024;
const ENTITY: EntityId = EntityId::new([0xa1; 16]);
const INITIAL_STATE: Digest32 = Digest32::new([0xa2; 32]);

#[derive(Debug)]
struct Config {
    listen: String,
    database_url: String,
    profile: DatabaseProfile,
    source_commit: String,
    token: String,
    max_requests: usize,
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("trnm-server-pg-slice: {error}");
            ExitCode::from(64)
        }
    }
}

fn run() -> Result<(), Box<dyn Error>> {
    let config = load_config()?;
    let mut repository = PgRepository::connect(&config.database_url, config.profile)?;
    repository.bind_schema_metadata(&config.source_commit, 1)?;
    match repository.bootstrap_entity(ENTITY, 1, INITIAL_STATE, 1) {
        Ok(head) => {
            if head.revision != 0 || head.authority_generation != 1 {
                return Err("unexpected newly bootstrapped entity head".into());
            }
        }
        Err(error) if error.code() == StableCode::AlreadyExists => {
            let head = repository
                .load_head(ENTITY)?
                .ok_or("existing entity head disappeared")?;
            if head.authority_generation != 1 {
                return Err("existing entity authority generation mismatch".into());
            }
        }
        Err(error) => return Err(error.into()),
    }

    let listener = TcpListener::bind(&config.listen)?;
    println!(
        "trnm-server-pg-slice listening={} profile={} source_commit={} claim=live-source-candidate",
        listener.local_addr()?,
        config.profile.metadata_value(),
        config.source_commit
    );
    for accepted in listener.incoming().take(config.max_requests) {
        let mut stream = accepted?;
        stream.set_read_timeout(Some(Duration::from_secs(2)))?;
        stream.set_write_timeout(Some(Duration::from_secs(2)))?;
        let response = match read_request(&mut stream) {
            Ok(request) => handle(&request, &config.token, &mut repository),
            Err(_) => response(400, "{\"error\":\"invalid_request\"}"),
        };
        stream.write_all(response.as_bytes())?;
        stream.flush()?;
    }
    println!("trnm-server-pg-slice drained=true");
    Ok(())
}

fn load_config() -> Result<Config, Box<dyn Error>> {
    let profile = match env::var("TRNM_DATABASE_PROFILE")?.as_str() {
        "postgresql" => DatabaseProfile::PostgreSql,
        "cockroachdb" => DatabaseProfile::CockroachDb,
        value => return Err(format!("unsupported database profile {value:?}").into()),
    };
    let source_commit = env::var("TRNM_SCHEMA_SOURCE_COMMIT")?;
    if source_commit.len() != 40
        || !source_commit
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit())
    {
        return Err("TRNM_SCHEMA_SOURCE_COMMIT must be a 40-character hexadecimal commit".into());
    }
    let token = env::var("TRNM_SERVER_DEV_TOKEN")?;
    if token.len() < 32 || token.len() > 4096 || !token.bytes().all(|byte| (0x21..=0x7e).contains(&byte)) {
        return Err("TRNM_SERVER_DEV_TOKEN must contain 32..=4096 visible ASCII bytes".into());
    }
    let max_requests = env::var("TRNM_SERVER_MAX_REQUESTS")?.parse::<usize>()?;
    if max_requests == 0 {
        return Err("TRNM_SERVER_MAX_REQUESTS must be greater than zero".into());
    }
    Ok(Config {
        listen: env::var("TRNM_SERVER_LISTEN")?,
        database_url: env::var("TRNM_DATABASE_URL")?,
        profile,
        source_commit,
        token,
        max_requests,
    })
}

#[derive(Debug)]
struct Request {
    authorization: Option<String>,
    body: String,
}

fn read_request(stream: &mut TcpStream) -> Result<Request, Box<dyn Error>> {
    let mut bytes = Vec::with_capacity(1024);
    let mut buffer = [0u8; 1024];
    loop {
        let count = stream.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        if bytes.len().saturating_add(count) > MAX_REQUEST_BYTES {
            return Err("request exceeds bound".into());
        }
        bytes.extend_from_slice(&buffer[..count]);
        if complete_length(&bytes).is_some_and(|expected| bytes.len() >= expected) {
            break;
        }
    }
    let text = std::str::from_utf8(&bytes)?;
    let separator = text.find("\r\n\r\n").ok_or("header terminator missing")?;
    let head = &text[..separator];
    let body = &text[separator + 4..];
    let mut lines = head.split("\r\n");
    if lines.next() != Some("POST /v2/rpc/trnm_pg_vertical_slice HTTP/1.1") {
        return Err("unsupported request line".into());
    }
    let mut authorization = None;
    let mut content_length = None;
    for line in lines {
        let (name, value) = line.split_once(':').ok_or("invalid header")?;
        let value = value.trim();
        if name.trim().eq_ignore_ascii_case("authorization") {
            if authorization.replace(value.to_owned()).is_some() {
                return Err("duplicate authorization".into());
            }
        } else if name.trim().eq_ignore_ascii_case("content-length") {
            if content_length.replace(value.parse::<usize>()?).is_some() {
                return Err("duplicate content-length".into());
            }
        } else if name.trim().eq_ignore_ascii_case("transfer-encoding") {
            return Err("transfer encoding forbidden".into());
        }
    }
    if content_length.unwrap_or(0) != body.len() {
        return Err("content-length mismatch".into());
    }
    Ok(Request {
        authorization,
        body: body.to_owned(),
    })
}

fn complete_length(bytes: &[u8]) -> Option<usize> {
    let text = std::str::from_utf8(bytes).ok()?;
    let separator = text.find("\r\n\r\n")?;
    let mut content_length = 0usize;
    for line in text[..separator].split("\r\n").skip(1) {
        let (name, value) = line.split_once(':')?;
        if name.trim().eq_ignore_ascii_case("content-length") {
            content_length = value.trim().parse().ok()?;
        }
    }
    Some(separator + 4 + content_length)
}

fn handle(request: &Request, token: &str, repository: &mut PgRepository) -> String {
    let expected_authorization = format!("Bearer {token}");
    if request.authorization.as_deref() != Some(expected_authorization.as_str()) {
        return response(401, "{\"error\":\"unauthenticated\"}");
    }
    let Some(command) = json_u64(&request.body, "command") else {
        return response(400, "{\"error\":\"invalid_command\"}");
    };
    let Some(expected_revision) = json_u64(&request.body, "expected_revision") else {
        return response(400, "{\"error\":\"invalid_expected_revision\"}");
    };
    let command_seed = match u8::try_from(command) {
        Ok(value) if (1..=200).contains(&value) => value,
        _ => return response(400, "{\"error\":\"invalid_command\"}"),
    };
    let request = command_request(command_seed, expected_revision);
    match repository.commit_command(&request) {
        Ok(CommitOutcome::Applied(receipt)) => receipt_response(false, &receipt),
        Ok(CommitOutcome::Duplicate(receipt)) => receipt_response(true, &receipt),
        Err(error) => response(
            match error.code() {
                StableCode::InvalidArgument | StableCode::OutOfRange => 400,
                StableCode::Unauthenticated => 401,
                StableCode::NotFound => 404,
                StableCode::AlreadyExists
                | StableCode::FailedPrecondition
                | StableCode::Aborted => 409,
                StableCode::Unavailable => 503,
                _ => 500,
            },
            &format!(
                "{{\"error\":\"{}\",\"reason\":\"{}\"}}",
                error.code().as_str(),
                error.reason()
            ),
        ),
    }
}

fn command_request(seed: u8, expected_revision: u64) -> CommitRequest {
    CommitRequest {
        entity: ENTITY,
        command: CommandId::new([seed; 16]),
        fingerprint: Digest32::new([seed.wrapping_add(1); 32]),
        expected_revision,
        authority_generation: 1,
        next_state: Digest32::new([seed.wrapping_add(2); 32]),
        committed_at_ms: u64::from(seed) + 10_000,
        events: vec![EventInput {
            id: EventId::new([seed.wrapping_add(3); 16]),
            payload: Digest32::new([seed.wrapping_add(4); 32]),
        }],
        outbox: vec![OutboxInput {
            id: IntentId::new([seed.wrapping_add(5); 16]),
            kind: IntentKind::Broadcast,
            payload: Digest32::new([seed.wrapping_add(6); 32]),
            available_at_ms: u64::from(seed) + 10_000,
        }],
    }
}

fn receipt_response(duplicate: bool, receipt: &trnm_persistence_pg::CommitReceipt) -> String {
    response(
        200,
        &format!(
            "{{\"duplicate\":{},\"revision\":{},\"event_count\":{},\"outbox_count\":{}}}",
            duplicate,
            receipt.revision,
            receipt.event_count,
            receipt.outbox.len()
        ),
    )
}

fn json_u64(body: &str, key: &str) -> Option<u64> {
    let needle = format!("\"{key}\"");
    let after_key = body.get(body.find(&needle)? + needle.len()..)?;
    let after_colon = after_key.get(after_key.find(':')? + 1..)?.trim_start();
    let digits = after_colon
        .chars()
        .take_while(char::is_ascii_digit)
        .collect::<String>();
    if digits.is_empty() {
        None
    } else {
        digits.parse().ok()
    }
}

fn response(status: u16, body: &str) -> String {
    let reason = match status {
        200 => "OK",
        400 => "Bad Request",
        401 => "Unauthorized",
        404 => "Not Found",
        409 => "Conflict",
        503 => "Service Unavailable",
        _ => "Internal Server Error",
    };
    format!(
        "HTTP/1.1 {status} {reason}\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
        body.len()
    )
}
