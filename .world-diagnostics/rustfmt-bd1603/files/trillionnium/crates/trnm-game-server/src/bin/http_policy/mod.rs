//! Authenticated CLI requests must stay on the operator-selected endpoint.
//!
//! reqwest's default redirect policy does not remove our custom credential
//! headers. Return 3xx responses to the caller; do not follow even same-origin
//! redirects, rewrite a mutation's method, or treat a redirect as a transport
//! failure eligible for the caller's retry loop.
use std::time::Duration;

pub(super) fn client_builder(
    connect_timeout: Duration,
    request_timeout: Duration,
) -> reqwest::ClientBuilder {
    reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .connect_timeout(connect_timeout)
        .timeout(request_timeout)
}

#[cfg(test)]
mod tests {
    use super::client_builder;
    use std::time::Duration;
    use tokio::{
        io::{AsyncReadExt, AsyncWriteExt},
        net::{TcpListener, TcpStream},
        time::timeout,
    };

    const REQUEST_BODY: &str = "same-command-identity";
    const IO_LIMIT: Duration = Duration::from_secs(3);
    const OBSERVATION_LIMIT: Duration = Duration::from_millis(500);

    async fn read_request(stream: &mut TcpStream) -> Vec<u8> {
        let mut bytes = Vec::new();
        loop {
            let mut chunk = [0_u8; 1024];
            let count = timeout(IO_LIMIT, stream.read(&mut chunk))
                .await
                .expect("fixture request timed out")
                .expect("fixture request read");
            assert!(count > 0, "client closed before the complete request");
            bytes.extend_from_slice(&chunk[..count]);
            assert!(bytes.len() <= 16 * 1024, "fixture request exceeds budget");
            if let Some(offset) = bytes.windows(4).position(|part| part == b"\r\n\r\n") {
                let end = offset + 4;
                let headers = std::str::from_utf8(&bytes[..end]).expect("fixture headers");
                let length = headers
                    .lines()
                    .filter_map(|line| line.split_once(':'))
                    .find(|(name, _)| name.eq_ignore_ascii_case("content-length"))
                    .map(|(_, value)| value.trim().parse::<usize>().expect("fixture length"))
                    .unwrap_or(0);
                assert!(length <= 8 * 1024, "fixture body exceeds budget");
                if bytes.len() >= end + length {
                    return bytes;
                }
            }
        }
    }

    async fn reply(stream: &mut TcpStream, status: u16, location: Option<&str>) {
        let location_header = location
            .map(|value| format!("Location: {value}\r\n"))
            .unwrap_or_default();
        let response = format!(
            "HTTP/1.1 {status} Fixture\r\n{location_header}Content-Length: 0\r\nConnection: close\r\n\r\n"
        );
        timeout(IO_LIMIT, stream.write_all(response.as_bytes()))
            .await
            .expect("fixture response timed out")
            .expect("fixture response write");
    }

    fn assert_original_request(request: &[u8]) {
        let value = std::str::from_utf8(request).expect("fixture request is UTF-8");
        let headers = value.split("\r\n\r\n").next().unwrap();
        assert_eq!(headers.lines().next(), Some("POST /authorized HTTP/1.1"));
        for (name, expected) in [
            ("x-trnm-player-session", "fixture-session-only"),
            ("x-trnm-moderator", "fixture-moderator-only"),
        ] {
            let actual = headers
                .lines()
                .filter_map(|line| line.split_once(':'))
                .find(|(key, _)| key.eq_ignore_ascii_case(name))
                .map(|(_, field)| field.trim());
            assert_eq!(actual, Some(expected), "original credential header missing");
        }
        assert!(value.ends_with(REQUEST_BODY));
    }

    async fn assert_redirect_stops(status: u16, relative: bool) {
        let origin = TcpListener::bind("127.0.0.1:0").await.expect("origin bind");
        let target = TcpListener::bind("127.0.0.1:0").await.expect("target bind");
        let origin_url = format!("http://{}/authorized", origin.local_addr().unwrap());
        let target_url = format!("http://{}/not-authorized", target.local_addr().unwrap());
        let location = if relative {
            "/not-authorized".to_string()
        } else {
            target_url
        };
        let origin_task = tokio::spawn(async move {
            let (mut stream, _) = timeout(IO_LIMIT, origin.accept())
                .await
                .expect("origin request timed out")
                .expect("origin accept");
            let request = read_request(&mut stream).await;
            reply(&mut stream, status, Some(&location)).await;
            drop(stream);
            let followed = match timeout(OBSERVATION_LIMIT, origin.accept()).await {
                Ok(Ok((mut extra, _))) => {
                    let _ = read_request(&mut extra).await;
                    reply(&mut extra, 200, None).await;
                    true
                }
                Ok(Err(error)) => panic!("redirect observer failed: {error}"),
                Err(_) => false,
            };
            (request, followed)
        });
        let target_task = tokio::spawn(async move {
            match timeout(OBSERVATION_LIMIT, target.accept()).await {
                Ok(Ok((mut stream, _))) => {
                    let _ = read_request(&mut stream).await;
                    reply(&mut stream, 200, None).await;
                    true
                }
                Ok(Err(error)) => panic!("target observer failed: {error}"),
                Err(_) => false,
            }
        });
        let response = client_builder(IO_LIMIT, IO_LIMIT)
            .no_proxy()
            .build()
            .expect("policy client")
            .post(origin_url)
            .header("x-trnm-player-session", "fixture-session-only")
            .header("x-trnm-moderator", "fixture-moderator-only")
            .body(REQUEST_BODY)
            .send()
            .await
            .expect("original HTTP response");
        let actual_status = response.status().as_u16();
        let (request, same_origin_followed) = origin_task.await.expect("origin task");
        let cross_origin_followed = target_task.await.expect("target task");
        assert_original_request(&request);
        assert_eq!(actual_status, status, "redirect response was replaced");
        assert!(!same_origin_followed, "same-origin redirect was followed");
        assert!(
            !cross_origin_followed,
            "credentialed request crossed origin"
        );
    }

    #[tokio::test]
    async fn redirect_301_preserves_response_without_forwarding_credentials() {
        assert_redirect_stops(301, false).await;
    }

    #[tokio::test]
    async fn redirect_302_preserves_response_without_forwarding_credentials() {
        assert_redirect_stops(302, false).await;
    }

    #[tokio::test]
    async fn redirect_303_does_not_rewrite_authenticated_post_to_get() {
        assert_redirect_stops(303, false).await;
    }

    #[tokio::test]
    async fn redirect_307_does_not_forward_authenticated_post_body() {
        assert_redirect_stops(307, false).await;
    }

    #[tokio::test]
    async fn redirect_308_does_not_forward_authenticated_post_body() {
        assert_redirect_stops(308, false).await;
    }

    #[tokio::test]
    async fn relative_redirect_does_not_replay_a_mutation_on_another_path() {
        assert_redirect_stops(307, true).await;
    }

    #[tokio::test]
    async fn direct_success_still_sends_the_original_credentials_and_body() {
        let listener = TcpListener::bind("127.0.0.1:0").await.expect("origin bind");
        let url = format!("http://{}/authorized", listener.local_addr().unwrap());
        let task = tokio::spawn(async move {
            let (mut stream, _) = timeout(IO_LIMIT, listener.accept())
                .await
                .expect("origin request timed out")
                .expect("origin accept");
            let request = read_request(&mut stream).await;
            reply(&mut stream, 200, None).await;
            request
        });
        let response = client_builder(IO_LIMIT, IO_LIMIT)
            .no_proxy()
            .build()
            .expect("policy client")
            .post(url)
            .header("x-trnm-player-session", "fixture-session-only")
            .header("x-trnm-moderator", "fixture-moderator-only")
            .body(REQUEST_BODY)
            .send()
            .await
            .expect("direct HTTP response");
        assert_eq!(response.status().as_u16(), 200);
        assert_original_request(&task.await.expect("origin task"));
    }
}
