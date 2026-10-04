use std::io::{Read, Write};

use super::*;
use trnm_contracts::{Digest32, DomainError};
use trnm_persistence_pg::{CommitOutcome, CommitRequest, EntityHead, EntityId};

#[derive(Debug)]
struct NoBusinessRepository;

impl Repository for NoBusinessRepository {
    fn bootstrap_entity(
        &mut self,
        _: EntityId,
        _: u64,
        _: Digest32,
        _: u64,
    ) -> Result<EntityHead, DomainError> {
        panic!("CORS must not call authority bootstrap")
    }

    fn commit_command(&mut self, _: &CommitRequest) -> Result<CommitOutcome, DomainError> {
        panic!("CORS must not commit a command")
    }
}

fn wire(method: &str, target: &str, headers: &[(&str, &str)], draining: bool) -> String {
    let values = BTreeMap::from([
        ("TRNM_SERVER_DATABASE_URL", "postgresql://unopened/fixture"),
        ("TRNM_SERVER_DATABASE_PROFILE", "postgresql"),
        ("TRNM_SERVER_ALLOW_PLAINTEXT_DATABASE", "true"),
        (
            "TRNM_SERVER_SCHEMA_SOURCE_COMMIT",
            "0000000000000000000000000000000000000000",
        ),
        (
            "TRNM_SERVER_ADMIN_TOKEN",
            "operator-fixture-secret-unchanged-32",
        ),
    ]);
    let (_, config) =
        ServerConfig::from_lookup(&["trnm-server".to_owned(), "serve".to_owned()], |name| {
            values.get(name).map(|value| (*value).to_owned())
        })
        .unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let mut client = TcpStream::connect(listener.local_addr().unwrap()).unwrap();
    client
        .set_read_timeout(Some(Duration::from_secs(2)))
        .unwrap();
    client
        .set_write_timeout(Some(Duration::from_secs(2)))
        .unwrap();
    let (mut stream, _) = listener.accept().unwrap();
    let worker = thread::spawn(move || {
        let drain = SharedDrain::default();
        if draining {
            drain.begin();
        }
        let mut app = App::with_shared_state(
            NoBusinessRepository,
            config.admin_token.clone(),
            SharedAppMetrics::default(),
            drain.clone(),
        );
        handle_connection(&mut stream, &mut app, &config, &drain).unwrap();
    });
    let mut request =
        format!("{method} {target} HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n");
    for (name, value) in headers {
        request.push_str(&format!("{name}: {value}\r\n"));
    }
    request.push_str("\r\n");
    client.write_all(request.as_bytes()).unwrap();
    let mut response = String::new();
    client.read_to_string(&mut response).unwrap();
    worker.join().unwrap();
    response
}

#[test]
fn nakama_client_preflight_is_public_and_has_no_response_body() {
    let response = wire(
        "OPTIONS",
        "/healthcheck",
        &[
            ("Origin", "https://client.example"),
            ("Access-Control-Request-Method", "GET"),
        ],
        false,
    );
    assert!(response.starts_with("HTTP/1.1 200 OK\r\n"), "{response}");
    let (head, body) = response.split_once("\r\n\r\n").unwrap();
    assert!(head
        .split("\r\n")
        .any(|line| line == "Access-Control-Allow-Origin: *"));
    assert_eq!(body, "");
}

#[test]
fn preflight_covers_only_installed_nakama_client_routes_and_queries() {
    for target in [
        "/healthcheck?probe=browser",
        "/v2/storage",
        "/v2/storage/delete",
        "/v2/storage/inventory",
        "/v2/storage/inventory/00000000-0000-0000-0000-000000000001",
        "/v2/account/authenticate/device?create=false",
        "/v2/account/authenticate/custom",
        "/v2/account/session/refresh",
        "/v2/session/logout",
    ] {
        let response = wire(
            "OPTIONS",
            target,
            &[
                ("Origin", "https://client.example"),
                ("Access-Control-Request-Method", "POST"),
                (
                    "Access-Control-Request-Headers",
                    "authorization, content-type",
                ),
            ],
            false,
        );
        assert!(
            response.starts_with("HTTP/1.1 200 OK\r\n"),
            "{target}: {response}"
        );
        assert!(response.contains("Access-Control-Allow-Headers: Authorization,Content-Type\r\n"));
        assert!(!response.contains("Access-Control-Allow-Credentials"));
        assert_eq!(response.split_once("\r\n\r\n").unwrap().1, "");
    }
}

#[test]
fn cors_does_not_change_operator_control_or_unimplemented_route_responses() {
    for (method, target) in [
        ("POST", "/-/drain"),
        ("POST", "/v1/authority/bootstrap"),
        ("POST", "/v1/authority/commit"),
        ("GET", "/v1/session/me"),
        ("GET", "/healthz"),
        ("GET", "/readyz"),
        ("GET", "/metrics"),
        ("OPTIONS", "/v1/realtime"),
        ("OPTIONS", "/v2/unknown"),
        ("OPTIONS", "/v2/rpc/example"),
        ("OPTIONS", "/ws"),
    ] {
        let before = wire(method, target, &[], false);
        let with_origin = wire(
            method,
            target,
            &[
                ("Origin", "https://client.example"),
                ("Access-Control-Request-Method", "POST"),
            ],
            false,
        );
        assert_eq!(with_origin, before, "{method} {target}");
        assert!(!with_origin.contains("Access-Control-"));
    }
}

#[test]
fn cors_preserves_actual_authentication_and_method_rejection() {
    for (method, target) in [
        ("POST", "/v2/account/authenticate/device"),
        ("POST", "/v2/account/authenticate/custom"),
        ("POST", "/v2/account/session/refresh"),
        ("POST", "/v2/session/logout"),
        ("PUT", "/v2/storage"),
        ("GET", "/v2/storage/inventory"),
        ("GET", "/v2/account/authenticate/device"),
        ("PATCH", "/healthcheck"),
    ] {
        let before = wire(method, target, &[], false);
        let with_origin = wire(
            method,
            target,
            &[("Origin", "https://client.example")],
            false,
        );
        assert_eq!(
            with_origin.replace("Access-Control-Allow-Origin: *\r\n", ""),
            before,
            "{method} {target}"
        );
        assert!(with_origin.contains("Access-Control-Allow-Origin: *\r\n"));
        assert!(!with_origin.contains("Access-Control-Allow-Credentials"));
        assert!(!with_origin.starts_with("HTTP/1.1 200"));
    }
}

#[test]
fn cors_preflight_does_not_admit_mutations_during_drain() {
    let headers = [
        ("Origin", "https://client.example"),
        ("Access-Control-Request-Method", "PUT"),
    ];
    let preflight = wire("OPTIONS", "/v2/storage", &headers, true);
    assert!(preflight.starts_with("HTTP/1.1 200 OK\r\n"));
    let actual = wire(
        "PUT",
        "/v2/storage",
        &[("Origin", "https://client.example")],
        true,
    );
    assert!(actual.starts_with("HTTP/1.1 503 Service Unavailable\r\n"));
    assert!(actual.contains("Access-Control-Allow-Origin: *\r\n"));
    assert_eq!(
        actual.replace("Access-Control-Allow-Origin: *\r\n", ""),
        wire("PUT", "/v2/storage", &[], true)
    );
}

#[test]
fn installed_nakama_method_errors_match_reference_over_tcp() {
    let contract: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../contracts/http/nakama-routing-v1.json"
    ))
    .unwrap();
    let fixtures = contract["fixtures"].as_array().unwrap();
    assert_eq!(fixtures.len(), 27);
    for case in fixtures {
        let method = case["method"].as_str().unwrap();
        let target = case["target"].as_str().unwrap();
        let response = wire(
            method,
            target,
            &[
                ("Origin", "https://client.example"),
                ("Authorization", "Bearer invalid"),
            ],
            false,
        );
        assert!(
            response.starts_with("HTTP/1.1 501 Not Implemented\r\n"),
            "{method} {target}: {response}"
        );
        let (head, body) = response.split_once("\r\n\r\n").unwrap();
        assert!(head.contains("Content-Length: 42\r\n"));
        assert!(head
            .split("\r\n")
            .any(|line| line == "Access-Control-Allow-Origin: *"));
        assert!(!head.contains("WWW-Authenticate"));
        if method == "HEAD" {
            assert_eq!(body, "");
        } else {
            let decoded: serde_json::Value = serde_json::from_str(body).unwrap();
            assert_eq!(decoded["code"], case["code"]);
            assert_eq!(decoded["message"], case["message"]);
        }
    }
}
