//! Bounded Nakama client-route CORS policy. Operator routes are never included.
//! Source behavior: pinned Nakama api.go and its vendored Gorilla middleware.

use super::http::{Request, Response};
use super::legacy_http_api::legacy_auth_http_route;
use super::storage_list_api;

const MAX_REQUEST_HEADERS_BYTES: usize = 32 * 1024;

#[derive(Clone, Debug, Eq, PartialEq)]
pub(super) struct CorsHeaders {
    allowed_headers: String,
    allowed_method: Option<&'static str>,
}

impl CorsHeaders {
    pub(super) fn allowed_headers(&self) -> &str {
        &self.allowed_headers
    }

    pub(super) fn allowed_method(&self) -> Option<&'static str> {
        self.allowed_method
    }
}

#[derive(Debug)]
pub(super) enum Decision {
    Continue(Option<CorsHeaders>),
    Respond(Response),
}

fn client_target(target: &str) -> bool {
    let path = target.split_once('?').map_or(target, |(path, _)| path);
    matches!(
        path,
        "/" | "/healthcheck" | "/v2/storage" | "/v2/storage/delete"
    ) || legacy_auth_http_route("POST", target).is_some()
        || storage_list_api::is_list_target(target)
}

pub(super) fn evaluate(request: &Request) -> Decision {
    if !client_target(&request.target) {
        return Decision::Continue(None);
    }
    let origin_present = request
        .header("origin")
        .is_some_and(|value| !value.is_empty());
    if !origin_present {
        return if request.method == "OPTIONS" {
            Decision::Respond(Response::cors_preflight(200, None))
        } else {
            Decision::Continue(None)
        };
    }
    let mut headers = CorsHeaders {
        allowed_headers: String::new(),
        allowed_method: None,
    };
    if request.method != "OPTIONS" {
        // CORS adds no principal and does not change method/auth dispatch.
        return Decision::Continue(Some(headers));
    }
    let Some(method) = request.header("access-control-request-method") else {
        return Decision::Respond(Response::cors_preflight(400, None));
    };
    headers.allowed_method = match method {
        "GET" | "HEAD" | "POST" => None,
        "PUT" => Some("PUT"),
        "DELETE" => Some("DELETE"),
        _ => return Decision::Respond(Response::cors_preflight(405, None)),
    };
    let requested = request
        .header("access-control-request-headers")
        .unwrap_or("");
    // Real ingress is already capped at this total header-byte budget. Keep
    // direct typed callers bounded too; output contains only fixed names.
    if requested.len() > MAX_REQUEST_HEADERS_BYTES {
        return Decision::Respond(Response::cors_preflight(403, None));
    }
    for name in requested.split(',').map(str::trim) {
        if name.is_empty()
            || ["Accept", "Accept-Language", "Content-Language", "Origin"]
                .iter()
                .any(|allowed| name.eq_ignore_ascii_case(allowed))
        {
            continue;
        }
        let Some(canonical) = ["Authorization", "Content-Type", "User-Agent"]
            .into_iter()
            .find(|allowed| name.eq_ignore_ascii_case(allowed))
        else {
            return Decision::Respond(Response::cors_preflight(403, None));
        };
        if !headers.allowed_headers.is_empty() {
            headers.allowed_headers.push(',');
        }
        headers.allowed_headers.push_str(canonical);
    }
    Decision::Respond(Response::cors_preflight(200, Some(headers)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    #[test]
    fn pinned_cors_reference_fixtures_match_all_twenty_two_cases() {
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../contracts/http/nakama-cors-fixtures-v1.json"
        ))
        .unwrap();
        let cases = fixture["fixtures"].as_array().unwrap();
        assert_eq!(cases.len(), 22);
        for case in cases {
            let input = &case["input"];
            let headers = input["headers"]
                .as_object()
                .unwrap()
                .iter()
                .map(|(name, value)| (name.clone(), value.as_str().unwrap().to_owned()))
                .collect();
            let request = Request::new(
                input["method"].as_str().unwrap(),
                "/healthcheck",
                headers,
                Vec::new(),
            );
            let (response, calls) = match evaluate(&request) {
                Decision::Continue(headers) => {
                    (Response::json(202, Vec::new()).with_cors(headers), 1)
                }
                Decision::Respond(response) => (response, 0),
            };
            assert_eq!(
                response.status,
                case["status"].as_u64().unwrap() as u16,
                "{}",
                input["name"]
            );
            assert_eq!(
                calls,
                case["handler_calls"].as_u64().unwrap(),
                "{}",
                input["name"]
            );
            let mut wire = Vec::new();
            response.write_to(&mut wire).unwrap();
            let wire = String::from_utf8(wire).unwrap();
            let (head, body) = wire.split_once("\r\n\r\n").unwrap();
            assert_eq!(body, case["body"].as_str().unwrap());
            let observed: BTreeMap<_, Vec<_>> = head
                .split("\r\n")
                .filter_map(|line| {
                    let (name, value) = line.split_once(": ")?;
                    name.starts_with("Access-Control-")
                        .then_some((name.to_owned(), vec![value.to_owned()]))
                })
                .collect();
            assert_eq!(
                serde_json::to_value(observed).unwrap(),
                case["headers"],
                "{}",
                input["name"]
            );
            assert!(!head.contains("Access-Control-Allow-Credentials"));
            if calls == 0 {
                for absent in [
                    "Content-Type:",
                    "Cache-Control:",
                    "WWW-Authenticate:",
                    "Vary:",
                    "Grpc-Metadata-",
                ] {
                    assert!(
                        !head.split("\r\n").any(|line| line.starts_with(absent)),
                        "{absent}"
                    );
                }
            }
        }
    }

    #[test]
    fn client_route_allowlist_excludes_every_operator_and_unimplemented_route() {
        for target in [
            "/",
            "/healthcheck",
            "/v2/storage",
            "/v2/storage/delete",
            "/v2/storage/inventory",
            "/v2/storage/inventory/user",
            "/v2/account/authenticate/device",
            "/v2/account/authenticate/custom",
            "/v2/account/session/refresh",
            "/v2/session/logout",
        ] {
            assert!(client_target(target), "{target}");
            assert!(
                client_target(&format!("{target}?probe=browser")),
                "{target}"
            );
        }
        for target in [
            "/healthcheck/",
            "/healthz",
            "/readyz",
            "/metrics",
            "/-/drain",
            "/v1/authority/bootstrap",
            "/v1/authority/commit",
            "/v1/session/me",
            "/v1/session/refresh",
            "/v1/session/logout",
            "/v1/realtime",
            "/ws",
            "/v2/unknown",
            "/v2/rpc/example",
            "/v2/storage/a/b/c",
        ] {
            let request = Request::new(
                "OPTIONS",
                target,
                BTreeMap::from([("Origin".to_owned(), "https://client.example".to_owned())]),
                Vec::new(),
            );
            assert!(
                matches!(evaluate(&request), Decision::Continue(None)),
                "{target}"
            );
        }
    }
}
