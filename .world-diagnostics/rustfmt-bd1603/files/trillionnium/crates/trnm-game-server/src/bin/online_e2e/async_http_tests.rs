//! Loopback transport regressions for the actual E2E HTTP helpers.
//! These tests do not contact a deployment or provide settlement evidence.
use super::*;
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::{TcpListener, TcpStream as AsyncTcpStream},
    task::JoinHandle,
    time::timeout,
};

#[derive(Debug)]
struct CapturedRequest {
    request_line: String,
    body: Vec<u8>,
}

async fn capture_request(stream: &mut AsyncTcpStream) -> CapturedRequest {
    let mut bytes = Vec::new();
    let header_end = loop {
        let mut chunk = [0_u8; 1024];
        let count = timeout(Duration::from_secs(3), stream.read(&mut chunk))
            .await
            .expect("test request read timed out")
            .expect("read test request");
        assert!(count > 0, "test client closed before headers");
        bytes.extend_from_slice(&chunk[..count]);
        assert!(bytes.len() <= 16 * 1024, "test request exceeds budget");
        if let Some(offset) = bytes.windows(4).position(|part| part == b"\r\n\r\n") {
            break offset + 4;
        }
    };
    let headers = std::str::from_utf8(&bytes[..header_end]).expect("ASCII test headers");
    let request_line = headers.lines().next().expect("request line").to_string();
    let content_length = headers
        .lines()
        .filter_map(|line| line.split_once(':'))
        .find(|(name, _)| name.eq_ignore_ascii_case("content-length"))
        .map(|(_, value)| value.trim().parse::<usize>().expect("content length"))
        .unwrap_or(0);
    assert!(content_length <= 8 * 1024, "test body exceeds budget");
    while bytes.len() < header_end + content_length {
        let mut chunk = [0_u8; 1024];
        let count = timeout(Duration::from_secs(3), stream.read(&mut chunk))
            .await
            .expect("test body read timed out")
            .expect("read test body");
        assert!(count > 0, "test client closed before body");
        bytes.extend_from_slice(&chunk[..count]);
        assert!(bytes.len() <= 24 * 1024, "test request exceeds budget");
    }
    CapturedRequest {
        request_line,
        body: bytes[header_end..header_end + content_length].to_vec(),
    }
}

// None deliberately loses the response after recording the complete request.
async fn server(
    script: Vec<Option<(u16, &'static str)>>,
) -> (String, JoinHandle<Vec<CapturedRequest>>) {
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .expect("loopback bind");
    let base_url = format!("http://{}", listener.local_addr().expect("bound address"));
    let task = tokio::spawn(async move {
        let mut requests = Vec::new();
        for response in script {
            let (mut stream, _) = timeout(Duration::from_secs(3), listener.accept())
                .await
                .expect("expected test request did not arrive")
                .expect("test accept");
            requests.push(capture_request(&mut stream).await);
            if let Some((status, body)) = response {
                let message = format!(
                    "HTTP/1.1 {status} Test\r\nContent-Length: {}\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
                timeout(Duration::from_secs(3), stream.write_all(message.as_bytes()))
                    .await
                    .expect("test response write timed out")
                    .expect("test response write");
            }
            drop(stream);
        }
        assert!(
            timeout(Duration::from_millis(350), listener.accept())
                .await
                .is_err(),
            "unexpected extra request: retry policy changed"
        );
        requests
    });
    (base_url, task)
}

fn request(client: &Client, base_url: &str) -> reqwest::RequestBuilder {
    client
        .post(format!("{base_url}/same-intent"))
        .json(&json!({"command_id": "same-intent", "sequence": 7}))
}

fn http_client() -> Client {
    Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(2))
        .build()
        .expect("async test client")
}

#[tokio::test]
async fn successful_request_is_not_retried() {
    let (base_url, task) = server(vec![Some((200, "{}"))]).await;
    let response = send_with_retry(request(&http_client(), &base_url))
        .await
        .expect("response");
    assert_eq!(response.status().as_u16(), 200);
    assert_eq!(task.await.expect("server task").len(), 1);
}

#[tokio::test]
async fn http_conflict_is_returned_without_transport_retry() {
    let (base_url, task) = server(vec![Some((409, "{\"recoverable\":true}"))]).await;
    let response = send_with_retry(request(&http_client(), &base_url))
        .await
        .expect("HTTP response");
    assert_eq!(response.status().as_u16(), 409);
    assert_eq!(task.await.expect("server task").len(), 1);
}

#[tokio::test]
async fn lost_success_replays_exact_request_identity_and_body() {
    let (base_url, task) = server(vec![Some((200, "{}")), Some((200, "{}"))]).await;
    let response = send_with_lost_response_retry(request(&http_client(), &base_url))
        .await
        .expect("recovered response");
    assert_eq!(response.status().as_u16(), 200);
    let requests = task.await.expect("server task");
    assert_eq!(requests.len(), 2);
    assert_eq!(requests[0].request_line, requests[1].request_line);
    assert_eq!(requests[0].body, requests[1].body);
    assert_eq!(
        serde_json::from_slice::<Value>(&requests[0].body).unwrap()["command_id"],
        "same-intent"
    );
}

#[tokio::test]
async fn transport_failure_replays_exact_bytes_then_stops_on_success() {
    let (base_url, task) = server(vec![None, Some((200, "{}"))]).await;
    send_with_retry(request(&http_client(), &base_url))
        .await
        .expect("transport recovery");
    let requests = task.await.expect("server task");
    assert_eq!(requests.len(), 2);
    assert_eq!(requests[0].body, requests[1].body);
}

#[tokio::test]
async fn transport_retries_are_bounded_to_four_attempts() {
    let (base_url, task) = server(vec![None, None, None, None]).await;
    assert!(send_with_retry(request(&http_client(), &base_url))
        .await
        .is_err());
    let requests = task.await.expect("server task");
    assert_eq!(requests.len(), 4);
    assert!(requests.iter().all(|item| item.body == requests[0].body));
}

#[tokio::test]
async fn non_idempotent_creation_does_not_retry_response_loss() {
    let (base_url, task) = server(vec![None]).await;
    let mut client = OnlineClient::new(base_url).expect("online client");
    client.non_idempotent_http = http_client();
    let identity = Identity {
        player_id: "fixture-player".into(),
        account_id: "fixture-account".into(),
        session: "fixture-session".into(),
    };
    let result: Result<Value, String> = client
        .post_one_shot_non_idempotent(&identity, "/create", &json!({"map": "fixture"}))
        .await;
    assert!(result.is_err());
    assert_eq!(task.await.expect("server task").len(), 1);
}

#[tokio::test]
async fn malformed_success_is_not_reported_as_a_decoded_receipt() {
    let (base_url, task) = server(vec![Some((200, "not-json"))]).await;
    let mut client = OnlineClient::new(base_url).expect("online client");
    client.http = http_client();
    let identity = Identity {
        player_id: "fixture-player".into(),
        account_id: "fixture-account".into(),
        session: "fixture-session".into(),
    };
    let result: Result<Value, String> = client
        .post(
            &identity,
            "/same-intent",
            &json!({"command_id": "same-intent"}),
        )
        .await;
    assert!(result.is_err());
    assert_eq!(task.await.expect("server task").len(), 1);
}
