//! Bounded form-POST fallback for the installed public healthcheck only.
//! Behavior studied from the pinned grpc-gateway mux and Go 1.26.5 net/url.
//! The two-byte printability ranges derive from Go strconv.IsPrint; the Go
//! Authors' BSD license is retained in third_party/go-sort/LICENSE.

use super::http::{Request, Response};

pub(super) fn is_form_post(request: &Request) -> bool {
    request.method == "POST"
        && request.header("content-type") == Some("application/x-www-form-urlencoded")
}

pub(super) fn respond(request: &Request) -> Response {
    // Existing ingress framing, header/request byte caps and total read deadline
    // have already run. Validation borrows input; it never decodes or stores the
    // ignored form fields, and an error reflects at most three original bytes.
    let query = request
        .target
        .split_once('?')
        .map_or("", |(_, query)| query);
    let error = form_error(&request.body).or_else(|| form_error(query.as_bytes()));
    if let Some(error) = error {
        let message = match error {
            FormError::Semicolon => "invalid semicolon separator in query".to_owned(),
            FormError::Escape(fragment) => format!("invalid URL escape {}", quote_escape(fragment)),
        };
        return Response::nakama_healthcheck(
            400,
            format!(
                "{{\"code\":3,\"message\":{}}}",
                serde_json::Value::String(message)
            )
            .into_bytes(),
            false,
        );
    }
    // The upstream mux parses the form before applying an override, including
    // unsupported overrides. Without an override, POST can fall back to GET.
    let override_method = request.header("x-http-method-override").unwrap_or("");
    if override_method.is_empty()
        || simple_method_is(override_method, b"GET")
        || simple_method_is(override_method, b"POST")
    {
        Response::nakama_healthcheck(200, b"{}".to_vec(), false)
    } else {
        Response::nakama_healthcheck(
            501,
            br#"{"code":12,"message":"Method Not Allowed"}"#.to_vec(),
            simple_method_is(override_method, b"HEAD"),
        )
    }
}

// Of non-ASCII runes, Go's simple uppercase mapping can produce the letters in
// GET/POST/HEAD only for long s -> S. In particular it does not expand the st
// ligature into ST, as Rust's full Unicode to_uppercase would do.
fn simple_method_is(value: &str, expected: &[u8]) -> bool {
    let mut chars = value.chars();
    for byte in expected {
        let Some(actual) = chars.next() else {
            return false;
        };
        if !(actual.eq_ignore_ascii_case(&char::from(*byte)) || (actual == 'ſ' && *byte == b'S')) {
            return false;
        }
    }
    chars.next().is_none()
}

#[derive(Debug)]
enum FormError<'a> {
    Semicolon,
    Escape(&'a [u8]),
}

fn form_error(input: &[u8]) -> Option<FormError<'_>> {
    let mut error = None;
    for pair in input.split(|byte| *byte == b'&') {
        if pair.contains(&b';') {
            error = Some(FormError::Semicolon);
            continue;
        }
        let (key, value) = pair
            .iter()
            .position(|byte| *byte == b'=')
            .map_or((pair, &[][..]), |index| {
                (&pair[..index], &pair[index + 1..])
            });
        // An invalid key skips its value. The first escape error survives, but
        // any later literal semicolon takes precedence, as in url.ParseQuery.
        if let Some(fragment) = first_bad_escape(key).or_else(|| first_bad_escape(value)) {
            if error.is_none() {
                error = Some(FormError::Escape(fragment));
            }
        }
    }
    error
}

fn first_bad_escape(input: &[u8]) -> Option<&[u8]> {
    let mut index = 0;
    while index < input.len() {
        if input[index] == b'%' {
            let fragment = &input[index..input.len().min(index + 3)];
            if fragment.len() < 3 || !fragment[1..].iter().all(u8::is_ascii_hexdigit) {
                return Some(fragment);
            }
            index += 3;
        } else {
            index += 1;
        }
    }
    None
}

// EscapeError contains '%' followed by at most two original bytes. Thus a
// valid non-ASCII rune can only be a two-byte rune; invalid bytes use \xNN.
// No lossy UTF-8 conversion or Unicode normalization is permitted here.
fn quote_escape(fragment: &[u8]) -> String {
    let mut output = String::with_capacity(14);
    output.push('"');
    let mut index = 0;
    while index < fragment.len() {
        let byte = fragment[index];
        let escaped = match byte {
            b'"' => Some("\\\""),
            b'\\' => Some("\\\\"),
            7 => Some("\\a"),
            8 => Some("\\b"),
            12 => Some("\\f"),
            b'\n' => Some("\\n"),
            b'\r' => Some("\\r"),
            b'\t' => Some("\\t"),
            11 => Some("\\v"),
            _ => None,
        };
        if let Some(escaped) = escaped {
            output.push_str(escaped);
        } else if byte < 32 || byte == 127 {
            output.push_str(&format!("\\x{byte:02x}"));
        } else if byte < 128 {
            output.push(char::from(byte));
        } else if let Some(value) = fragment
            .get(index..index + 2)
            .and_then(|bytes| std::str::from_utf8(bytes).ok())
        {
            let scalar = value.chars().next().expect("two-byte UTF-8 rune") as u32;
            if matches!(scalar,
                0x0080..=0x00a0 | 0x00ad | 0x0378..=0x0379 | 0x0380..=0x0383 |
                0x038b | 0x038d | 0x03a2 | 0x0530 | 0x0557..=0x0558 |
                0x058b..=0x058c | 0x0590 | 0x05c8..=0x05cf | 0x05eb..=0x05ee |
                0x05f5..=0x0605 | 0x061c | 0x06dd | 0x070e..=0x070f |
                0x074b..=0x074c | 0x07b2..=0x07bf | 0x07fb..=0x07fc)
            {
                output.push_str(&format!("\\u{scalar:04x}"));
            } else {
                output.push_str(value);
            }
            index += 1;
        } else {
            output.push_str(&format!("\\x{byte:02x}"));
        }
        index += 1;
    }
    output.push('"');
    output
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fmt::Write;

    #[test]
    fn all_percent_error_byte_pairs_match_pinned_go_quote_digest() {
        let mut corpus = String::with_capacity(1_200_000);
        for first in 0..=255 {
            for second in 0..=255 {
                for byte in quote_escape(&[b'%', first, second]).bytes() {
                    write!(corpus, "{byte:02x}").unwrap();
                }
                corpus.push('\n');
            }
        }
        let actual = super::super::codec::encode_hex(&trnm_token_jwt_adapter::sha256_digest(
            corpus.as_bytes(),
        ));
        let contract: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../contracts/http/nakama-healthcheck-form-v1.json"
        ))
        .unwrap();
        assert_eq!(
            actual,
            contract["go_quote_corpus"]["sha256"].as_str().unwrap()
        );
        assert_eq!(
            corpus.len(),
            contract["go_quote_corpus"]["bytes"].as_u64().unwrap() as usize
        );
        assert_eq!(quote_escape(b"%"), "\"%\"");
        assert_eq!(quote_escape(b"%a"), "\"%a\"");
    }
}
