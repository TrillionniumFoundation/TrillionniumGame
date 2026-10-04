//! Bounded diagnostic client only. Never used by production transport.
use base64::engine::general_purpose::{STANDARD, URL_SAFE_NO_PAD};
use base64::Engine;
use openssl::{hash::MessageDigest, pkey::PKey, sign::Signer};
use serde::de::{MapAccess, SeqAccess, Visitor};
use serde::Deserialize;
use serde_json::{json, Map, Value};
use std::fmt;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub(super) type Result<T> = std::result::Result<T, &'static str>;
pub(super) const LIMIT: usize = 1024 * 1024;
pub(super) const TIMEOUT: Duration = Duration::from_secs(10);

pub(super) fn require(ok: bool, reason: &'static str) -> Result<()> {
    if ok {
        Ok(())
    } else {
        Err(reason)
    }
}
pub(super) fn epoch() -> Result<u64> {
    Ok(SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "clock")?
        .as_secs())
}
pub(super) fn digest(bytes: &[u8]) -> String {
    format!(
        "sha256:{}",
        openssl::sha::sha256(bytes)
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect::<String>()
    )
}
pub(super) fn encode(bytes: &[u8]) -> String {
    STANDARD.encode(bytes)
}

fn remaining(deadline: Instant) -> Result<Duration> {
    deadline
        .checked_duration_since(Instant::now())
        .filter(|v| !v.is_zero())
        .ok_or("response deadline")
}
fn write_until(stream: &mut TcpStream, bytes: &[u8], deadline: Instant) -> Result<()> {
    write_with_budget(bytes, deadline, |part, budget| {
        stream.set_write_timeout(Some(budget))?;
        stream.write(part)
    })
}
fn write_with_budget(
    mut bytes: &[u8],
    deadline: Instant,
    mut write: impl FnMut(&[u8], Duration) -> std::io::Result<usize>,
) -> Result<()> {
    while !bytes.is_empty() {
        match write(bytes, remaining(deadline)?) {
            Ok(0) => return Err("request write zero"),
            Ok(n) if n <= bytes.len() => bytes = &bytes[n..],
            Ok(_) => return Err("request write count"),
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(_) => return Err("request write"),
        }
    }
    remaining(deadline)?;
    Ok(())
}
#[test]
fn native_auth_slow_partial_writes_consume_shared_budget() {
    let mut budgets = Vec::new();
    let deadline = Instant::now() + Duration::from_millis(25);
    let result = write_with_budget(b"abcdef", deadline, |_, budget| {
        budgets.push(budget);
        std::thread::sleep(Duration::from_millis(10));
        Ok(1)
    });
    assert!(result.is_err());
    assert!(!budgets.is_empty() && budgets.len() < 6);
    assert!(budgets.windows(2).all(|pair| pair[1] < pair[0]));
}

fn read_until(stream: &mut TcpStream, deadline: Instant) -> Result<Vec<u8>> {
    let mut wire = Vec::new();
    loop {
        let remaining = remaining(deadline)?;
        stream
            .set_read_timeout(Some(remaining))
            .map_err(|_| "read timeout")?;
        let mut chunk = [0_u8; 8192];
        let n = stream.read(&mut chunk).map_err(|_| "response read")?;
        if n == 0 {
            break;
        }
        require(wire.len() + n <= LIMIT, "response bound")?;
        wire.extend_from_slice(&chunk[..n]);
    }
    remaining(deadline)?;
    Ok(wire)
}

#[test]
fn native_auth_io_deadline_covers_delayed_and_trickled_responses() {
    for trickle in [false, true] {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let peer = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            for _ in 0..10 {
                std::thread::sleep(Duration::from_millis(10));
                if trickle && stream.write(b"x").is_err() {
                    break;
                }
            }
        });
        let deadline = Instant::now() + Duration::from_millis(30);
        let mut stream =
            TcpStream::connect_timeout(&address, remaining(deadline).unwrap()).unwrap();
        assert!(read_until(&mut stream, deadline).is_err());
        drop(stream);
        peer.join().unwrap();
    }
}
#[test]
fn native_auth_partial_write_keeps_original_deadline() {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let peer = std::thread::spawn(move || {
        let (_stream, _) = listener.accept().unwrap();
        std::thread::sleep(Duration::from_millis(100));
    });
    let deadline = Instant::now() + Duration::from_millis(20);
    let mut stream = TcpStream::connect_timeout(&address, remaining(deadline).unwrap()).unwrap();
    assert!(write_until(&mut stream, &vec![0; 16 * LIMIT], deadline).is_err());
    assert!(write_until(&mut stream, b"x", Instant::now()).is_err());
    peer.join().unwrap();
}

// Preserve integer values and reject duplicate keys rather than silently selecting
// a winner. serde_json's default recursion limit and the byte ceiling both apply.
struct Strict(Value);
impl<'de> Deserialize<'de> for Strict {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> std::result::Result<Self, D::Error> {
        struct V;
        impl<'de> Visitor<'de> for V {
            type Value = Strict;
            fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str("bounded integer JSON")
            }
            fn visit_bool<E: serde::de::Error>(self, v: bool) -> std::result::Result<Strict, E> {
                Ok(Strict(json!(v)))
            }
            fn visit_i64<E: serde::de::Error>(self, v: i64) -> std::result::Result<Strict, E> {
                Ok(Strict(json!(v)))
            }
            fn visit_u64<E: serde::de::Error>(self, v: u64) -> std::result::Result<Strict, E> {
                Ok(Strict(json!(v)))
            }
            fn visit_str<E: serde::de::Error>(self, v: &str) -> std::result::Result<Strict, E> {
                Ok(Strict(json!(v)))
            }
            fn visit_unit<E: serde::de::Error>(self) -> std::result::Result<Strict, E> {
                Ok(Strict(Value::Null))
            }
            fn visit_seq<A: SeqAccess<'de>>(
                self,
                mut a: A,
            ) -> std::result::Result<Strict, A::Error> {
                let mut values = Vec::new();
                while let Some(v) = a.next_element::<Strict>()? {
                    values.push(v.0);
                }
                Ok(Strict(Value::Array(values)))
            }
            fn visit_map<A: MapAccess<'de>>(
                self,
                mut a: A,
            ) -> std::result::Result<Strict, A::Error> {
                let mut values = Map::new();
                while let Some((k, v)) = a.next_entry::<String, Strict>()? {
                    if values.insert(k, v.0).is_some() {
                        return Err(serde::de::Error::custom("duplicate JSON key"));
                    }
                }
                Ok(Strict(Value::Object(values)))
            }
        }
        d.deserialize_any(V)
    }
}
pub(super) fn parse(bytes: &[u8]) -> Result<Value> {
    require(bytes.len() <= LIMIT, "JSON byte limit")?;
    serde_json::from_slice::<Strict>(bytes)
        .map(|v| v.0)
        .map_err(|_| "invalid diagnostic JSON")
}
fn jwt_like(bytes: &[u8]) -> bool {
    bytes
        .split(|b| !(b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.')))
        .any(|word| {
            let parts: Vec<_> = word.split(|b| *b == b'.').collect();
            parts.len() == 3 && parts.iter().all(|p| p.len() >= 10)
        })
}
fn no_embedded_token(value: &Value) -> bool {
    match value {
        Value::String(s) => !jwt_like(s.as_bytes()),
        Value::Array(a) => a.iter().all(no_embedded_token),
        Value::Object(o) => o
            .iter()
            .all(|(k, v)| !jwt_like(k.as_bytes()) && no_embedded_token(v)),
        _ => true,
    }
}
pub(super) fn token_view(token: &str, key: &[u8]) -> Result<Value> {
    require(!token.is_empty() && token.len() <= 16384, "token bound")?;
    let parts: Vec<_> = token.split('.').collect();
    require(parts.len() == 3, "token format")?;
    let header = parse(
        &URL_SAFE_NO_PAD
            .decode(parts[0])
            .map_err(|_| "token header encoding")?,
    )?;
    let claims = parse(
        &URL_SAFE_NO_PAD
            .decode(parts[1])
            .map_err(|_| "token claims encoding")?,
    )?;
    require(
        header == json!({"alg":"HS256","typ":"JWT"}),
        "token algorithm",
    )?;
    require(
        claims.is_object() && no_embedded_token(&claims),
        "token claims shape",
    )?;
    let signature = URL_SAFE_NO_PAD
        .decode(parts[2])
        .map_err(|_| "token signature encoding")?;
    let key = PKey::hmac(key).map_err(|_| "fixture HMAC key")?;
    let mut verifier =
        Signer::new(MessageDigest::sha256(), &key).map_err(|_| "fixture HMAC verifier")?;
    verifier
        .update(format!("{}.{}", parts[0], parts[1]).as_bytes())
        .map_err(|_| "fixture HMAC input")?;
    let expected = verifier
        .sign_to_vec()
        .map_err(|_| "fixture HMAC computation")?;
    require(
        signature.len() == expected.len() && openssl::memcmp::eq(&signature, &expected),
        "fixture signature mismatch",
    )?;
    Ok(
        json!({"sha256":digest(token.as_bytes()),"length":token.len(),"header":header,"claims":claims,
        "signature_verified":true,"signature_verifier":"openssl-hmac-sha256-known-fixture-key"}),
    )
}

// Debug is deliberately redacted: the live body contains credentials/JWTs.
pub(super) struct Response {
    pub record: Value,
    pub live_body: Value,
}
impl fmt::Debug for Response {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("DiagnosticResponse(<redacted>)")
    }
}
pub(super) fn exchange(
    address: SocketAddr,
    method: &str,
    path: &str,
    body: &[u8],
    authorization: Option<&str>,
    keys: (&[u8], &[u8]),
    forbidden_keys: &[&[u8]],
) -> Result<Response> {
    require(
        address.ip().is_loopback() && body.len() <= LIMIT,
        "client scope/bound",
    )?;
    if let Some(auth) = authorization {
        require(
            !auth.is_empty() && auth.len() <= 32768 && !auth.contains(['\r', '\n']),
            "authorization bound",
        )?;
    }
    let auth = authorization.map_or(String::new(), |a| format!("Authorization: {a}\r\n"));
    let mut request = format!("{method} {path} HTTP/1.1\r\nHost: {address}\r\nConnection: close\r\nContent-Type: application/json\r\n{auth}Content-Length: {}\r\n\r\n",body.len()).into_bytes();
    request.extend_from_slice(body);
    let started_at = epoch()?;
    let deadline = Instant::now() + TIMEOUT;
    let mut stream =
        TcpStream::connect_timeout(&address, remaining(deadline)?).map_err(|_| "connect")?;
    write_until(&mut stream, &request, deadline)?;
    let wire = read_until(&mut stream, deadline)?;
    if let Some(auth) = authorization {
        require(
            !wire.windows(auth.len()).any(|v| v == auth.as_bytes()),
            "authorization echo",
        )?;
    }
    for key in forbidden_keys {
        require(
            !key.is_empty() && !wire.windows(key.len()).any(|v| v == *key),
            "credential echo",
        )?;
    }
    let split = wire
        .windows(4)
        .position(|p| p == b"\r\n\r\n")
        .ok_or("HTTP framing")?
        + 4;
    require(
        split <= 32768 && !jwt_like(&wire[..split]),
        "header bound/secret",
    )?;
    let headers_text = std::str::from_utf8(&wire[..split]).map_err(|_| "header UTF8")?;
    let mut lines = headers_text.split("\r\n");
    let status_line = lines.next().ok_or("status line")?;
    let status = status_line
        .split_whitespace()
        .nth(1)
        .ok_or("status code")?
        .parse::<u16>()
        .map_err(|_| "status code")?;
    require(
        status_line.starts_with("HTTP/1.1 ") && (100..=599).contains(&status),
        "HTTP status",
    )?;
    let mut headers = Vec::new();
    let mut lengths = Vec::new();
    for line in lines.filter(|line| !line.is_empty()) {
        let (name, value) = line.split_once(':').ok_or("header format")?;
        let lower = name.to_ascii_lowercase();
        require(
            !matches!(
                lower.as_str(),
                "authorization" | "proxy-authenticate" | "set-cookie" | "transfer-encoding"
            ),
            "unexpected credential/framing header",
        )?;
        if lower == "content-length" {
            lengths.push(
                value
                    .trim()
                    .parse::<usize>()
                    .map_err(|_| "content length")?,
            );
        }
        headers.push(json!([name, value.trim()]));
    }
    let raw_body = &wire[split..];
    require(
        lengths == vec![raw_body.len()],
        "single exact content length",
    )?;
    let live_body = parse(raw_body)?;
    let mut view = live_body.clone();
    let object = view.as_object_mut().ok_or("response object")?;
    let mut sensitive = false;
    for (name, key) in [("token", keys.0), ("refresh_token", keys.1)] {
        if let Some(value) = object.get_mut(name) {
            let token = value.as_str().ok_or("token type")?;
            *value = token_view(token, key)?;
            sensitive = true;
        }
    }
    require(no_embedded_token(&view), "unrecognized token output")?;
    Ok(Response {
        live_body,
        record: json!({"request_sha256":digest(&request),"request_length":request.len(),
        "response_sha256":digest(&wire),"response_length":wire.len(),"status":status,
        "headers_base64":encode(&wire[..split]),"headers":headers,"started_at_epoch":started_at,
        "completed_at_epoch":epoch()?,"body_sha256":digest(raw_body),"body_length":raw_body.len(),
        "body_base64":if sensitive {Value::Null} else {json!(encode(raw_body))},"body":view,"raw_tokens_retained":false}),
    })
}

#[test]
fn native_auth_fixture_json_rejects_duplicate_and_fractional_values() {
    assert!(parse(br#"{"a":1,"a":2}"#).is_err());
    assert!(parse(br#"{"a":1.5}"#).is_err());
    assert!(parse(br#"{"a":[1,true,null]}"#).is_ok());
}
#[test]
fn native_auth_fixture_signature_check_is_not_claim_decoding() {
    let mut key = [0_u8; 32];
    openssl::rand::rand_bytes(&mut key).unwrap();
    let header = URL_SAFE_NO_PAD.encode(br#"{"alg":"HS256","typ":"JWT"}"#);
    let claims = URL_SAFE_NO_PAD.encode(br#"{"uid":"fixture"}"#);
    let input = format!("{header}.{claims}");
    let signing_key = PKey::hmac(&key).unwrap();
    let mut signer = Signer::new(MessageDigest::sha256(), &signing_key).unwrap();
    signer.update(input.as_bytes()).unwrap();
    let token = format!(
        "{input}.{}",
        URL_SAFE_NO_PAD.encode(signer.sign_to_vec().unwrap())
    );
    assert!(token_view(&token, &key).is_ok());
    key[0] ^= 1;
    assert!(token_view(&token, &key).is_err());
    key[0] ^= 1;
    let tampered = format!(
        "{header}.{}.{}",
        URL_SAFE_NO_PAD.encode(br#"{"uid":"changed"}"#),
        token.rsplit('.').next().unwrap()
    );
    assert!(token_view(&tampered, &key).is_err());
}
