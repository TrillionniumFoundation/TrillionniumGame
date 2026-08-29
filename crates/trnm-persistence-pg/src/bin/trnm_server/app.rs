use trnm_contracts::{CommandId, Digest32, DomainError, RetryClass, StableCode};
use trnm_persistence_pg::{
    CommitOutcome, CommitReceipt, CommitRequest, EntityHead, EntityId, EventId, EventInput,
    IntentId, IntentKind, OutboxInput, PgRepository,
};

use super::codec::{decode_hex, encode_hex};
use super::error::InputError;
use super::http::{Request, Response};
use super::json::Object;

pub trait Repository: std::fmt::Debug {
    fn bootstrap_entity(
        &mut self,
        entity: EntityId,
        authority_generation: u64,
        state: Digest32,
        updated_at_ms: u64,
    ) -> Result<EntityHead, DomainError>;

    fn commit_command(&mut self, request: &CommitRequest) -> Result<CommitOutcome, DomainError>;
}

impl Repository for PgRepository {
    fn bootstrap_entity(
        &mut self,
        entity: EntityId,
        authority_generation: u64,
        state: Digest32,
        updated_at_ms: u64,
    ) -> Result<EntityHead, DomainError> {
        PgRepository::bootstrap_entity(self, entity, authority_generation, state, updated_at_ms)
    }

    fn commit_command(&mut self, request: &CommitRequest) -> Result<CommitOutcome, DomainError> {
        PgRepository::commit_command(self, request)
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
struct Metrics {
    requests: u64,
    successes: u64,
    input_failures: u64,
    domain_failures: u64,
    bootstraps: u64,
    commands_applied: u64,
    command_replays: u64,
    drain_requests: u64,
}

impl Metrics {
    fn increment(value: &mut u64) {
        *value = value.saturating_add(1);
    }
}

#[derive(Debug)]
pub struct App<R> {
    repository: R,
    admin_token: String,
    draining: bool,
    metrics: Metrics,
}

impl<R: Repository> App<R> {
    #[must_use]
    pub fn new(repository: R, admin_token: String) -> Self {
        Self {
            repository,
            admin_token,
            draining: false,
            metrics: Metrics::default(),
        }
    }

    #[must_use]
    pub const fn should_stop(&self) -> bool {
        self.draining
    }

    pub fn handle(&mut self, request: &Request) -> Response {
        Metrics::increment(&mut self.metrics.requests);
        let response = match (request.method.as_str(), request.target.as_str()) {
            ("GET", "/healthz") => Response::json(200, br#"{"status":"ok"}"#.to_vec()),
            ("GET", "/readyz") => self.readiness(),
            ("GET", "/metrics") => self.metrics_response(),
            ("POST", "/-/drain") => self.drain(request),
            ("POST", "/v1/authority/bootstrap") => self.bootstrap(request),
            ("POST", "/v1/authority/commit") => self.commit(request),
            (_, "/healthz" | "/readyz" | "/metrics" | "/-/drain" | "/v1/authority/bootstrap" | "/v1/authority/commit") => {
                error_response(405, "unimplemented", "Method is not allowed.", "never")
            }
            _ => error_response(404, "not_found", "Requested resource was not found.", "never"),
        };
        if response.status < 400 {
            Metrics::increment(&mut self.metrics.successes);
        }
        response
    }

    fn readiness(&self) -> Response {
        if self.draining {
            error_response(503, "unavailable", "Service is draining.", "backoff")
        } else {
            Response::json(200, br#"{"status":"ready"}"#.to_vec())
        }
    }

    fn metrics_response(&self) -> Response {
        let ready = u8::from(!self.draining);
        Response::text(
            200,
            format!(
                "# TYPE trnm_server_requests_total counter\n\
                 trnm_server_requests_total {}\n\
                 # TYPE trnm_server_successes_total counter\n\
                 trnm_server_successes_total {}\n\
                 # TYPE trnm_server_input_failures_total counter\n\
                 trnm_server_input_failures_total {}\n\
                 # TYPE trnm_server_domain_failures_total counter\n\
                 trnm_server_domain_failures_total {}\n\
                 # TYPE trnm_server_bootstraps_total counter\n\
                 trnm_server_bootstraps_total {}\n\
                 # TYPE trnm_server_commands_applied_total counter\n\
                 trnm_server_commands_applied_total {}\n\
                 # TYPE trnm_server_command_replays_total counter\n\
                 trnm_server_command_replays_total {}\n\
                 # TYPE trnm_server_ready gauge\n\
                 trnm_server_ready {}\n",
                self.metrics.requests,
                self.metrics.successes,
                self.metrics.input_failures,
                self.metrics.domain_failures,
                self.metrics.bootstraps,
                self.metrics.commands_applied,
                self.metrics.command_replays,
                ready,
            ),
        )
    }

    fn drain(&mut self, request: &Request) -> Response {
        if !request.body.is_empty() {
            return self.input_failure(InputError::new("drain_body_must_be_empty"));
        }
        if !self.authorized(request) {
            return error_response(401, "unauthenticated", "Authentication required.", "never");
        }
        self.draining = true;
        Metrics::increment(&mut self.metrics.drain_requests);
        Response::json(200, br#"{"status":"draining"}"#.to_vec())
    }

    fn bootstrap(&mut self, request: &Request) -> Response {
        if self.draining {
            return error_response(503, "unavailable", "Service is draining.", "backoff");
        }
        if !is_json(request) {
            return error_response(
                415,
                "invalid_argument",
                "Content-Type must be application/json.",
                "never",
            );
        }
        let parsed = parse_bootstrap(&request.body);
        let (entity, authority_generation, state, updated_at_ms) = match parsed {
            Ok(value) => value,
            Err(error) => return self.input_failure(error),
        };
        match self.repository.bootstrap_entity(
            entity,
            authority_generation,
            state,
            updated_at_ms,
        ) {
            Ok(head) => {
                Metrics::increment(&mut self.metrics.bootstraps);
                Response::json(
                    201,
                    format!(
                        "{{\"entity_id\":\"{}\",\"revision\":{},\"last_event_sequence\":{},\"authority_generation\":{},\"state_digest\":\"{}\"}}",
                        encode_hex(head.entity.as_bytes()),
                        head.revision,
                        head.last_event_sequence,
                        head.authority_generation,
                        encode_hex(head.state.as_bytes()),
                    ),
                )
            }
            Err(error) => self.domain_failure(error),
        }
    }

    fn commit(&mut self, request: &Request) -> Response {
        if self.draining {
            return error_response(503, "unavailable", "Service is draining.", "backoff");
        }
        if !is_json(request) {
            return error_response(
                415,
                "invalid_argument",
                "Content-Type must be application/json.",
                "never",
            );
        }
        let commit = match parse_commit(&request.body) {
            Ok(value) => value,
            Err(error) => return self.input_failure(error),
        };

        // PgRepository::commit_command returns only after SERIALIZABLE commit
        // succeeds or an exact prior receipt is loaded. Constructing the HTTP
        // response after this call is the acknowledgement-after-commit fence.
        match self.repository.commit_command(&commit) {
            Ok(CommitOutcome::Applied(receipt)) => {
                Metrics::increment(&mut self.metrics.commands_applied);
                receipt_response(201, "applied", &receipt)
            }
            Ok(CommitOutcome::Duplicate(receipt)) => {
                Metrics::increment(&mut self.metrics.command_replays);
                receipt_response(200, "duplicate", &receipt)
            }
            Err(error) => self.domain_failure(error),
        }
    }

    fn authorized(&self, request: &Request) -> bool {
        let expected = format!("Bearer {}", self.admin_token);
        request
            .header("authorization")
            .is_some_and(|value| constant_time_eq(value.as_bytes(), expected.as_bytes()))
    }

    fn input_failure(&mut self, _error: InputError) -> Response {
        Metrics::increment(&mut self.metrics.input_failures);
        error_response(400, "invalid_argument", "Request is invalid.", "never")
    }

    fn domain_failure(&mut self, error: DomainError) -> Response {
        Metrics::increment(&mut self.metrics.domain_failures);
        let status = http_status(error.code());
        error_response(
            status,
            error.code().as_str(),
            public_message(error.code()),
            retry_name(error.retry()),
        )
    }
}

fn parse_bootstrap(input: &[u8]) -> Result<(EntityId, u64, Digest32, u64), InputError> {
    let object = Object::parse(input)?;
    object.require_exact_keys(&[
        "entity_id",
        "authority_generation",
        "state_digest",
        "updated_at_ms",
    ])?;
    Ok((
        EntityId::new(decode_hex::<16>(object.string("entity_id")?, "entity_id_invalid")?),
        object.unsigned("authority_generation")?,
        Digest32::new(decode_hex::<32>(
            object.string("state_digest")?,
            "state_digest_invalid",
        )?),
        object.unsigned("updated_at_ms")?,
    ))
}

fn parse_commit(input: &[u8]) -> Result<CommitRequest, InputError> {
    let object = Object::parse(input)?;
    object.require_exact_keys(&[
        "entity_id",
        "command_id",
        "fingerprint",
        "expected_revision",
        "authority_generation",
        "next_state_digest",
        "committed_at_ms",
        "event_id",
        "event_payload_digest",
        "intent_id",
        "intent_kind",
        "intent_payload_digest",
        "available_at_ms",
    ])?;
    let intent_kind = match object.string("intent_kind")? {
        "broadcast" => IntentKind::Broadcast,
        "search_index" => IntentKind::SearchIndex,
        "notification" => IntentKind::Notification,
        "external_effect" => IntentKind::ExternalEffect,
        "completion" => IntentKind::Completion,
        _ => return Err(InputError::new("intent_kind_invalid")),
    };
    Ok(CommitRequest {
        entity: EntityId::new(decode_hex::<16>(object.string("entity_id")?, "entity_id_invalid")?),
        command: CommandId::new(decode_hex::<16>(
            object.string("command_id")?,
            "command_id_invalid",
        )?),
        fingerprint: Digest32::new(decode_hex::<32>(
            object.string("fingerprint")?,
            "fingerprint_invalid",
        )?),
        expected_revision: object.unsigned("expected_revision")?,
        authority_generation: object.unsigned("authority_generation")?,
        next_state: Digest32::new(decode_hex::<32>(
            object.string("next_state_digest")?,
            "next_state_digest_invalid",
        )?),
        committed_at_ms: object.unsigned("committed_at_ms")?,
        events: vec![EventInput {
            id: EventId::new(decode_hex::<16>(
                object.string("event_id")?,
                "event_id_invalid",
            )?),
            payload: Digest32::new(decode_hex::<32>(
                object.string("event_payload_digest")?,
                "event_payload_digest_invalid",
            )?),
        }],
        outbox: vec![OutboxInput {
            id: IntentId::new(decode_hex::<16>(
                object.string("intent_id")?,
                "intent_id_invalid",
            )?),
            kind: intent_kind,
            payload: Digest32::new(decode_hex::<32>(
                object.string("intent_payload_digest")?,
                "intent_payload_digest_invalid",
            )?),
            available_at_ms: object.unsigned("available_at_ms")?,
        }],
    })
}

fn receipt_response(status: u16, outcome: &str, receipt: &CommitReceipt) -> Response {
    let first_event_sequence = receipt
        .first_event_sequence
        .map_or_else(|| "null".to_owned(), |value| value.to_string());
    let outbox = receipt
        .outbox
        .iter()
        .map(|intent| format!("\"{}\"", encode_hex(intent.as_bytes())))
        .collect::<Vec<_>>()
        .join(",");
    Response::json(
        status,
        format!(
            "{{\"outcome\":\"{outcome}\",\"entity_id\":\"{}\",\"command_id\":\"{}\",\"fingerprint\":\"{}\",\"revision\":{},\"state_digest\":\"{}\",\"first_event_sequence\":{},\"last_event_sequence\":{},\"event_count\":{},\"outbox\":[{}]}}",
            encode_hex(receipt.entity.as_bytes()),
            encode_hex(receipt.command.as_bytes()),
            encode_hex(receipt.fingerprint.as_bytes()),
            receipt.revision,
            encode_hex(receipt.state.as_bytes()),
            first_event_sequence,
            receipt.last_event_sequence,
            receipt.event_count,
            outbox,
        ),
    )
}

fn is_json(request: &Request) -> bool {
    request.header("content-type").is_some_and(|value| {
        value
            .split(';')
            .next()
            .is_some_and(|media_type| media_type.trim().eq_ignore_ascii_case("application/json"))
    })
}

fn error_response(status: u16, code: &str, message: &str, retry: &str) -> Response {
    Response::json(
        status,
        format!(
            "{{\"code\":\"{}\",\"message\":\"{}\",\"retry\":\"{}\"}}",
            escape_json(code),
            escape_json(message),
            escape_json(retry),
        ),
    )
}

fn escape_json(value: &str) -> String {
    let mut output = String::with_capacity(value.len());
    for character in value.chars() {
        match character {
            '\"' => output.push_str("\\\""),
            '\\' => output.push_str("\\\\"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            value if value.is_control() => output.push_str("?"),
            value => output.push(value),
        }
    }
    output
}

fn constant_time_eq(left: &[u8], right: &[u8]) -> bool {
    let maximum = left.len().max(right.len());
    let mut difference = left.len() ^ right.len();
    for index in 0..maximum {
        let left_byte = left.get(index).copied().unwrap_or(0);
        let right_byte = right.get(index).copied().unwrap_or(0);
        difference |= usize::from(left_byte ^ right_byte);
    }
    difference == 0
}

const fn http_status(code: StableCode) -> u16 {
    match code {
        StableCode::InvalidArgument | StableCode::OutOfRange => 400,
        StableCode::Unauthenticated => 401,
        StableCode::PermissionDenied => 403,
        StableCode::NotFound => 404,
        StableCode::AlreadyExists | StableCode::Aborted => 409,
        StableCode::FailedPrecondition => 412,
        StableCode::ResourceExhausted => 429,
        StableCode::Unimplemented => 501,
        StableCode::Internal | StableCode::DataLoss => 500,
        StableCode::Unavailable => 503,
    }
}

const fn public_message(code: StableCode) -> &'static str {
    match code {
        StableCode::InvalidArgument => "Request is invalid.",
        StableCode::NotFound => "Requested resource was not found.",
        StableCode::AlreadyExists => "Requested resource already exists.",
        StableCode::PermissionDenied => "Permission denied.",
        StableCode::ResourceExhausted => "Resource limit exceeded.",
        StableCode::FailedPrecondition => "Request precondition failed.",
        StableCode::Aborted => "Request was aborted.",
        StableCode::OutOfRange => "Request value is out of range.",
        StableCode::Unimplemented => "Operation is not implemented.",
        StableCode::Internal => "Internal server error.",
        StableCode::Unavailable => "Service is unavailable.",
        StableCode::DataLoss => "Internal data integrity error.",
        StableCode::Unauthenticated => "Authentication required.",
    }
}

const fn retry_name(retry: RetryClass) -> &'static str {
    match retry {
        RetryClass::Never => "never",
        RetryClass::SafeImmediate => "immediate",
        RetryClass::SafeBackoff => "backoff",
        RetryClass::ResyncRequired => "resync",
    }
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use super::*;

    #[derive(Debug, Default)]
    struct FakeRepository {
        bootstrapped: bool,
        commits: usize,
        failure: Option<DomainError>,
    }

    impl Repository for FakeRepository {
        fn bootstrap_entity(
            &mut self,
            entity: EntityId,
            authority_generation: u64,
            state: Digest32,
            updated_at_ms: u64,
        ) -> Result<EntityHead, DomainError> {
            if let Some(error) = self.failure {
                return Err(error);
            }
            self.bootstrapped = true;
            Ok(EntityHead {
                entity,
                revision: 0,
                last_event_sequence: 0,
                authority_generation,
                state,
                updated_at_ms,
            })
        }

        fn commit_command(&mut self, request: &CommitRequest) -> Result<CommitOutcome, DomainError> {
            if let Some(error) = self.failure {
                return Err(error);
            }
            self.commits += 1;
            Ok(CommitOutcome::Applied(CommitReceipt {
                entity: request.entity,
                command: request.command,
                fingerprint: request.fingerprint,
                revision: request.expected_revision + 1,
                state: request.next_state,
                first_event_sequence: Some(1),
                last_event_sequence: 1,
                event_count: request.events.len(),
                outbox: request.outbox.iter().map(|intent| intent.id).collect(),
            }))
        }
    }

    fn headers() -> BTreeMap<String, String> {
        BTreeMap::from([("content-type".to_owned(), "application/json".to_owned())])
    }

    fn bootstrap_body() -> String {
        format!(
            "{{\"entity_id\":\"{}\",\"authority_generation\":1,\"state_digest\":\"{}\",\"updated_at_ms\":10}}",
            "01".repeat(16),
            "02".repeat(32),
        )
    }

    fn commit_body() -> String {
        format!(
            "{{\"entity_id\":\"{}\",\"command_id\":\"{}\",\"fingerprint\":\"{}\",\"expected_revision\":0,\"authority_generation\":1,\"next_state_digest\":\"{}\",\"committed_at_ms\":11,\"event_id\":\"{}\",\"event_payload_digest\":\"{}\",\"intent_id\":\"{}\",\"intent_kind\":\"broadcast\",\"intent_payload_digest\":\"{}\",\"available_at_ms\":11}}",
            "01".repeat(16),
            "03".repeat(16),
            "04".repeat(32),
            "05".repeat(32),
            "06".repeat(16),
            "07".repeat(32),
            "08".repeat(16),
            "09".repeat(32),
        )
    }

    #[test]
    fn health_ready_bootstrap_and_commit_form_one_in_process_vertical_slice() {
        let mut app = App::new(FakeRepository::default(), "a".repeat(32));
        assert_eq!(
            app.handle(&Request::new("GET", "/healthz", BTreeMap::new(), Vec::new()))
                .status,
            200
        );
        assert_eq!(
            app.handle(&Request::new("GET", "/readyz", BTreeMap::new(), Vec::new()))
                .status,
            200
        );
        let bootstrap = app.handle(&Request::new(
            "POST",
            "/v1/authority/bootstrap",
            headers(),
            bootstrap_body(),
        ));
        assert_eq!(bootstrap.status, 201);
        let commit = app.handle(&Request::new(
            "POST",
            "/v1/authority/commit",
            headers(),
            commit_body(),
        ));
        assert_eq!(commit.status, 201);
        let body = String::from_utf8(commit.body).unwrap();
        assert!(body.contains("\"outcome\":\"applied\""));
        assert!(body.contains(&"08".repeat(16)));
    }

    #[test]
    fn internal_domain_reason_is_never_exposed() {
        let repository = FakeRepository {
            failure: Some(DomainError::new(
                StableCode::Internal,
                "private_database_reason",
                RetryClass::Never,
            )),
            ..FakeRepository::default()
        };
        let mut app = App::new(repository, "a".repeat(32));
        let response = app.handle(&Request::new(
            "POST",
            "/v1/authority/commit",
            headers(),
            commit_body(),
        ));
        assert_eq!(response.status, 500);
        let body = String::from_utf8(response.body).unwrap();
        assert!(!body.contains("private_database_reason"));
        assert!(body.contains("Internal server error"));
    }

    #[test]
    fn authenticated_drain_stops_new_mutations() {
        let token = "a".repeat(32);
        let mut app = App::new(FakeRepository::default(), token.clone());
        let unauthorized = app.handle(&Request::new(
            "POST",
            "/-/drain",
            BTreeMap::new(),
            Vec::new(),
        ));
        assert_eq!(unauthorized.status, 401);

        let authorized_headers = BTreeMap::from([(
            "authorization".to_owned(),
            format!("Bearer {token}"),
        )]);
        let response = app.handle(&Request::new(
            "POST",
            "/-/drain",
            authorized_headers,
            Vec::new(),
        ));
        assert_eq!(response.status, 200);
        assert!(app.should_stop());
        assert_eq!(
            app.handle(&Request::new(
                "POST",
                "/v1/authority/commit",
                headers(),
                commit_body(),
            ))
            .status,
            503
        );
    }

    #[test]
    fn malformed_fields_and_media_type_fail_before_repository_mutation() {
        let mut app = App::new(FakeRepository::default(), "a".repeat(32));
        let wrong_media = app.handle(&Request::new(
            "POST",
            "/v1/authority/commit",
            BTreeMap::new(),
            commit_body(),
        ));
        assert_eq!(wrong_media.status, 415);
        let unknown_field = app.handle(&Request::new(
            "POST",
            "/v1/authority/bootstrap",
            headers(),
            br#"{"extra":1}"#.to_vec(),
        ));
        assert_eq!(unknown_field.status, 400);
    }

    #[test]
    fn admin_token_comparison_rejects_a_256_byte_length_delta() {
        assert!(!constant_time_eq(&vec![0_u8; 32], &vec![0_u8; 288]));
    }
}
