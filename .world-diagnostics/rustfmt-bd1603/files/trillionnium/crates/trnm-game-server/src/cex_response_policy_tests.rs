//! Loopback tests of the actual private CEX/signer HTTP adapter, not a model.
//! These tests do not prove PostgreSQL, deployed custody or cross-host recovery.
use super::{
    append_remote_chunk, bounded_remote_json, serialized_intent_hash, tests::base_intent,
    CexClient, CexSettlementReceiptLookupResponse, ExternalSettlementError,
    CEX_SETTLEMENT_RECEIPT_LOOKUP_CONTRACT, MAX_REMOTE_SUCCESS_BODY_BYTES,
};
use axum::{
    body::Body,
    http::StatusCode,
    response::Response,
    routing::{get, post},
    Json, Router,
};
use futures_util::{stream, StreamExt};
use serde_json::Value;
use std::{
    convert::Infallible,
    sync::{
        atomic::{AtomicBool, AtomicUsize, Ordering},
        Arc,
    },
    time::Duration,
};
use tokio::task::JoinHandle;
use trnm_economy_protocol::{
    EconomicIntentKind, EconomicReceipt, ReceiptStatus, SettlementBackendKind,
};

struct TestServer {
    url: String,
    task: JoinHandle<()>,
}

impl Drop for TestServer {
    fn drop(&mut self) {
        self.task.abort();
    }
}

async fn serve(app: Router) -> TestServer {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let task = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    TestServer {
        url: format!("http://{address}"),
        task,
    }
}

fn client(server: &TestServer) -> CexClient {
    CexClient::new(
        server.url.clone(),
        "g".repeat(24),
        server.url.clone(),
        "s".repeat(32),
    )
    .unwrap()
}

async fn response_for(body: Vec<u8>, chunked: bool) -> (reqwest::Response, TestServer) {
    let app = Router::new().route(
        "/",
        get(move || {
            let body = body.clone();
            async move {
                let body = if chunked {
                    let chunks: Vec<Result<Vec<u8>, Infallible>> =
                        body.chunks(4096).map(|chunk| Ok(chunk.to_vec())).collect();
                    Body::from_stream(stream::iter(chunks))
                } else {
                    Body::from(body)
                };
                Response::builder()
                    .status(StatusCode::OK)
                    .body(body)
                    .unwrap()
            }
        }),
    );
    let server = serve(app).await;
    let response = reqwest::Client::builder()
        .timeout(Duration::from_secs(5))
        .build()
        .unwrap()
        .get(&server.url)
        .send()
        .await
        .unwrap();
    (response, server)
}

#[test]
fn accumulator_preserves_bytes_when_limit_is_exceeded() {
    let mut body = vec![0; MAX_REMOTE_SUCCESS_BODY_BYTES - 1];
    append_remote_chunk(&mut body, &[1]).unwrap();
    let before = body.clone();
    assert_eq!(
        append_remote_chunk(&mut body, &[2]),
        Err("remote_success_body_too_large")
    );
    assert_eq!(body, before);
    append_remote_chunk(&mut body, &[]).unwrap();
    assert_eq!(body, before);
}

#[tokio::test]
async fn exact_limit_is_accepted_with_known_and_chunked_length() {
    for chunked in [false, true] {
        let body = format!("\"{}\"", "x".repeat(MAX_REMOTE_SUCCESS_BODY_BYTES - 2)).into_bytes();
        let (response, _server) = response_for(body, chunked).await;
        if chunked {
            assert!(response.content_length().is_none());
        } else {
            assert_eq!(
                response.content_length(),
                Some(MAX_REMOTE_SUCCESS_BODY_BYTES as u64)
            );
        }
        assert_eq!(
            bounded_remote_json::<String>(response).await.unwrap().len(),
            MAX_REMOTE_SUCCESS_BODY_BYTES - 2
        );
    }
}

#[tokio::test]
async fn oversize_is_rejected_with_known_and_chunked_length() {
    for chunked in [false, true] {
        let body = format!("\"{}\"", "x".repeat(MAX_REMOTE_SUCCESS_BODY_BYTES - 1)).into_bytes();
        let (response, _server) = response_for(body, chunked).await;
        if chunked {
            assert!(response.content_length().is_none());
        }
        assert_eq!(
            bounded_remote_json::<String>(response).await,
            Err("remote_success_body_too_large")
        );
    }
}

#[tokio::test]
async fn malformed_or_trailing_input_has_static_diagnostics() {
    for body in [
        b"{\"private-test-value\":".to_vec(),
        b"{} {}".to_vec(),
        vec![],
        vec![0xff],
    ] {
        let (response, _server) = response_for(body, true).await;
        assert_eq!(
            bounded_remote_json::<Value>(response).await,
            Err("remote_success_json_invalid")
        );
    }
}

#[tokio::test]
async fn valid_prefix_cannot_hide_an_oversized_tail() {
    let body = format!("{{}}{}", " ".repeat(MAX_REMOTE_SUCCESS_BODY_BYTES)).into_bytes();
    let (response, _server) = response_for(body, true).await;
    assert_eq!(
        bounded_remote_json::<Value>(response).await,
        Err("remote_success_body_too_large")
    );
}

#[tokio::test]
async fn body_timeout_remains_a_read_failure() {
    let app = Router::new().route(
        "/",
        get(|| async {
            let first = stream::once(async { Ok::<_, Infallible>("{") });
            let pending = stream::pending::<Result<&'static str, Infallible>>();
            Response::builder()
                .body(Body::from_stream(first.chain(pending)))
                .unwrap()
        }),
    );
    let server = serve(app).await;
    let response = reqwest::Client::builder()
        .timeout(Duration::from_secs(1))
        .build()
        .unwrap()
        .get(&server.url)
        .send()
        .await
        .unwrap();
    assert_eq!(
        bounded_remote_json::<Value>(response).await,
        Err("remote_success_body_read_failed")
    );
}

#[tokio::test]
async fn signer_lookup_redirects_never_connect_to_another_origin() {
    for status in [301_u16, 302, 303, 307, 308] {
        let target = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let location = format!("http://{}/must-not-receive", target.local_addr().unwrap());
        let app = Router::new().route(
            "/v1/signer/receipts/request-a",
            get(move || {
                let location = location.clone();
                async move {
                    Response::builder()
                        .status(status)
                        .header("location", location)
                        .body(Body::empty())
                        .unwrap()
                }
            }),
        );
        let server = serve(app).await;
        assert!(
            matches!(
                client(&server).lookup_signer_receipt("request-a").await,
                Err(ExternalSettlementError::Retryable(_))
            ),
            "status {status}"
        );
        assert!(
            tokio::time::timeout(Duration::from_millis(200), target.accept())
                .await
                .is_err(),
            "status {status} contacted a redirect target"
        );
    }
}

#[tokio::test]
async fn relative_redirect_is_not_followed_either() {
    let hits = Arc::new(AtomicUsize::new(0));
    let seen = hits.clone();
    let app = Router::new()
        .route(
            "/v1/signer/receipts/request-a",
            get(|| async {
                Response::builder()
                    .status(302)
                    .header("location", "/target")
                    .body(Body::empty())
                    .unwrap()
            }),
        )
        .route(
            "/target",
            get(move || {
                seen.fetch_add(1, Ordering::SeqCst);
                async { "{}" }
            }),
        );
    let server = serve(app).await;
    assert!(matches!(
        client(&server).lookup_signer_receipt("request-a").await,
        Err(ExternalSettlementError::Retryable(_))
    ));
    assert_eq!(hits.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn oversized_signer_lookup_is_not_missing() {
    let app = Router::new().route(
        "/v1/signer/receipts/request-a",
        get(|| async { "x".repeat(MAX_REMOTE_SUCCESS_BODY_BYTES + 1) }),
    );
    let server = serve(app).await;
    assert!(matches!(
        client(&server).lookup_signer_receipt("request-a").await,
        Err(ExternalSettlementError::Retryable(_))
    ));
}

#[tokio::test]
async fn malformed_cex_lookup_never_falls_through_to_submit() {
    for body in ["", "not-json", "{}"] {
        let posts = Arc::new(AtomicUsize::new(0));
        let seen = posts.clone();
        let app = Router::new()
            .route(
                "/v1/trnm/economy/receipts/by-intent",
                get(move || async move { body }),
            )
            .route(
                "/v1/trnm/economy/intents",
                post(move || {
                    seen.fetch_add(1, Ordering::SeqCst);
                    async { StatusCode::INTERNAL_SERVER_ERROR }
                }),
            );
        let server = serve(app).await;
        let intent = base_intent(EconomicIntentKind::CompleteContract, 0);
        assert!(matches!(
            client(&server)
                .submit_authorized_settlement_intent(&intent)
                .await,
            Err(ExternalSettlementError::Retryable(_))
        ));
        assert_eq!(
            posts.load(Ordering::SeqCst),
            0,
            "malformed lookup caused a submit"
        );
    }
}

#[tokio::test]
async fn oversized_cex_lookup_never_falls_through_to_submit() {
    let posts = Arc::new(AtomicUsize::new(0));
    let seen = posts.clone();
    let app = Router::new()
        .route(
            "/v1/trnm/economy/receipts/by-intent",
            get(|| async { "x".repeat(MAX_REMOTE_SUCCESS_BODY_BYTES + 1) }),
        )
        .route(
            "/v1/trnm/economy/intents",
            post(move || {
                seen.fetch_add(1, Ordering::SeqCst);
                async { StatusCode::INTERNAL_SERVER_ERROR }
            }),
        );
    let server = serve(app).await;
    let intent = base_intent(EconomicIntentKind::CompleteContract, 0);
    assert!(matches!(
        client(&server)
            .submit_authorized_settlement_intent(&intent)
            .await,
        Err(ExternalSettlementError::Retryable(_))
    ));
    assert_eq!(posts.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn oversized_committed_success_recovers_through_lookup_without_resubmit() {
    let intent = base_intent(EconomicIntentKind::CompleteContract, 0);
    let receipt = EconomicReceipt::from_intent(
        "receipt-a",
        &intent,
        "mock-cex",
        SettlementBackendKind::Cex,
        ReceiptStatus::Settled,
        chrono::Utc::now().timestamp(),
    );
    let lookup = CexSettlementReceiptLookupResponse {
        contract_version: CEX_SETTLEMENT_RECEIPT_LOOKUP_CONTRACT.to_string(),
        intent_id: intent.intent_id.clone(),
        intent_hash: serialized_intent_hash(&intent).unwrap(),
        receipt,
    };
    let committed = Arc::new(AtomicBool::new(false));
    let posts = Arc::new(AtomicUsize::new(0));
    let get_committed = committed.clone();
    let post_committed = committed.clone();
    let seen = posts.clone();
    let expected_intent = serde_json::to_value(&intent).unwrap();
    let app = Router::new()
        .route(
            "/v1/trnm/economy/receipts/by-intent",
            get(move || {
                let committed = get_committed.load(Ordering::SeqCst);
                let lookup = lookup.clone();
                async move {
                    if committed {
                        Response::builder()
                            .status(200)
                            .header("content-type", "application/json")
                            .body(Body::from(serde_json::to_vec(&lookup).unwrap()))
                            .unwrap()
                    } else {
                        Response::builder().status(404).body(Body::empty()).unwrap()
                    }
                }
            }),
        )
        .route(
            "/v1/trnm/economy/intents",
            post(move |Json(body): Json<Value>| {
                assert_eq!(body["intent"], expected_intent);
                seen.fetch_add(1, Ordering::SeqCst);
                post_committed.store(true, Ordering::SeqCst);
                async { "x".repeat(MAX_REMOTE_SUCCESS_BODY_BYTES + 1) }
            }),
        );
    let server = serve(app).await;
    let client = client(&server);
    assert!(matches!(
        client.submit_authorized_settlement_intent(&intent).await,
        Err(ExternalSettlementError::Retryable(_))
    ));
    let recovered = client
        .submit_authorized_settlement_intent(&intent)
        .await
        .unwrap();
    recovered.validate_for(&intent).unwrap();
    assert_eq!(posts.load(Ordering::SeqCst), 1);
}
