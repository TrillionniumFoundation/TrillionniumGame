use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::str;

use super::error::{InputError, ServerError};

const HEADER_TERMINATOR: &[u8; 4] = b"\r\n\r\n";
const MAX_HEADER_BYTES: usize = 32 * 1024;
const MAX_HEADERS: usize = 64;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Request {
    pub method: String,
    pub target: String,
    headers: BTreeMap<String, String>,
    pub body: Vec<u8>,
}

impl Request {
    #[must_use]
    pub fn new(
        method: impl Into<String>,
        target: impl Into<String>,
        headers: BTreeMap<String, String>,
        body: impl Into<Vec<u8>>,
    ) -> Self {
        Self {
            method: method.into(),
            target: target.into(),
            headers: headers
                .into_iter()
                .map(|(name, value)| (name.to_ascii_lowercase(), value))
                .collect(),
            body: body.into(),
        }
    }

    #[must_use]
    pub fn header(&self, name: &str) -> Option<&str> {
        self.headers.get(name).map(String::as_str)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Response {
    pub status: u16,
    pub content_type: &'static str,
    pub body: Vec<u8>,
}

impl Response {
    #[must_use]
    pub fn json(status: u16, body: impl Into<Vec<u8>>) -> Self {
        Self {
            status,
            content_type: "application/json; charset=utf-8",
            body: body.into(),
        }
    }

    #[must_use]
    pub fn text(status: u16, body: impl Into<Vec<u8>>) -> Self {
        Self {
            status,
            content_type: "text/plain; charset=utf-8",
            body: body.into(),
        }
    }

    pub fn write_to(&self, output: &mut impl Write) -> Result<(), ServerError> {
        write!(
            output,
            "HTTP/1.1 {} {}\r\nContent-Type: {}\r\nContent-Length: {}\r\nConnection: close\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n\r\n",
            self.status,
            reason_phrase(self.status),
            self.content_type,
            self.body.len()
        )?;
        output.write_all(&self.body)?;
        output.flush()?;
        Ok(())
    }
}

pub fn read_request(stream: &mut TcpStream, maximum: usize) -> Result<Request, ServerError> {
    let mut input = Vec::with_capacity(4096);
    let mut buffer = [0_u8; 4096];
    loop {
        let read = stream.read(&mut buffer)?;
        if read == 0 {
            return Err(InputError::new("http_request_incomplete").into());
        }
        if input.len().saturating_add(read) > maximum {
            return Err(InputError::new("http_request_too_large").into());
        }
        input.extend_from_slice(&buffer[..read]);
        if let Some(header_end) = find_header_end(&input) {
            if header_end + HEADER_TERMINATOR.len() > MAX_HEADER_BYTES {
                return Err(InputError::new("http_headers_too_large").into());
            }
            let content_length = content_length_from_head(&input[..header_end])?;
            let total = header_end
                .checked_add(HEADER_TERMINATOR.len())
                .and_then(|value| value.checked_add(content_length))
                .ok_or_else(|| InputError::new("http_content_length_overflow"))?;
            if total > maximum {
                return Err(InputError::new("http_request_too_large").into());
            }
            if input.len() >= total {
                if input.len() != total {
                    return Err(InputError::new("http_pipelining_not_supported").into());
                }
                return parse_request_bytes(&input, maximum).map_err(ServerError::from);
            }
        } else if input.len() > MAX_HEADER_BYTES {
            return Err(InputError::new("http_headers_too_large").into());
        }
    }
}

pub fn parse_request_bytes(input: &[u8], maximum: usize) -> Result<Request, InputError> {
    if input.len() > maximum {
        return Err(InputError::new("http_request_too_large"));
    }
    let header_end = find_header_end(input)
        .ok_or_else(|| InputError::new("http_request_incomplete"))?;
    if header_end + HEADER_TERMINATOR.len() > MAX_HEADER_BYTES {
        return Err(InputError::new("http_headers_too_large"));
    }
    let (method, target, headers, content_length) = parse_head(&input[..header_end])?;
    let body_start = header_end + HEADER_TERMINATOR.len();
    let total = body_start
        .checked_add(content_length)
        .ok_or_else(|| InputError::new("http_content_length_overflow"))?;
    if input.len() != total {
        return Err(InputError::new(if input.len() < total {
            "http_request_incomplete"
        } else {
            "http_pipelining_not_supported"
        }));
    }
    Ok(Request::new(
        method,
        target,
        headers,
        input[body_start..].to_vec(),
    ))
}

fn content_length_from_head(input: &[u8]) -> Result<usize, InputError> {
    let (_, _, _, length) = parse_head(input)?;
    Ok(length)
}

fn parse_head(
    input: &[u8],
) -> Result<(String, String, BTreeMap<String, String>, usize), InputError> {
    let head = str::from_utf8(input).map_err(|_| InputError::new("http_headers_not_utf8"))?;
    let mut lines = head.split("\r\n");
    let request_line = lines
        .next()
        .ok_or_else(|| InputError::new("http_request_line_missing"))?;
    let mut parts = request_line.split(' ');
    let method = parts
        .next()
        .ok_or_else(|| InputError::new("http_request_line_invalid"))?;
    let target = parts
        .next()
        .ok_or_else(|| InputError::new("http_request_line_invalid"))?;
    let version = parts
        .next()
        .ok_or_else(|| InputError::new("http_request_line_invalid"))?;
    if parts.next().is_some()
        || method.is_empty()
        || target.is_empty()
        || version != "HTTP/1.1"
    {
        return Err(InputError::new("http_request_line_invalid"));
    }
    if !method.bytes().all(|byte| byte.is_ascii_uppercase())
        || !target.starts_with('/')
        || target
            .bytes()
            .any(|byte| byte.is_ascii_control() || byte == b' ')
    {
        return Err(InputError::new("http_method_or_target_invalid"));
    }

    let mut headers = BTreeMap::new();
    for line in lines {
        if headers.len() >= MAX_HEADERS {
            return Err(InputError::new("http_header_count_exceeded"));
        }
        let (name, raw_value) = line
            .split_once(':')
            .ok_or_else(|| InputError::new("http_header_invalid"))?;
        if name.is_empty()
            || !name
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
        {
            return Err(InputError::new("http_header_name_invalid"));
        }
        let value = raw_value.trim_matches([' ', '\t']);
        if value
            .bytes()
            .any(|byte| byte.is_ascii_control() && byte != b'\t')
        {
            return Err(InputError::new("http_header_value_invalid"));
        }
        if headers
            .insert(name.to_ascii_lowercase(), value.to_owned())
            .is_some()
        {
            return Err(InputError::new("http_duplicate_header"));
        }
    }

    if headers.contains_key("transfer-encoding") {
        return Err(InputError::new("http_transfer_encoding_not_supported"));
    }
    let content_length = match headers.get("content-length") {
        Some(value) => parse_content_length(value)?,
        None if method == "POST" => {
            return Err(InputError::new("http_content_length_required"));
        }
        None => 0,
    };
    Ok((
        method.to_owned(),
        target.to_owned(),
        headers,
        content_length,
    ))
}

fn parse_content_length(value: &str) -> Result<usize, InputError> {
    if value.is_empty()
        || (value.len() > 1 && value.starts_with('0'))
        || !value.bytes().all(|byte| byte.is_ascii_digit())
    {
        return Err(InputError::new("http_content_length_invalid"));
    }
    value
        .parse::<usize>()
        .map_err(|_| InputError::new("http_content_length_overflow"))
}

fn find_header_end(input: &[u8]) -> Option<usize> {
    input
        .windows(HEADER_TERMINATOR.len())
        .position(|window| window == HEADER_TERMINATOR)
}

const fn reason_phrase(status: u16) -> &'static str {
    match status {
        200 => "OK",
        201 => "Created",
        400 => "Bad Request",
        401 => "Unauthorized",
        403 => "Forbidden",
        404 => "Not Found",
        405 => "Method Not Allowed",
        409 => "Conflict",
        412 => "Precondition Failed",
        413 => "Content Too Large",
        415 => "Unsupported Media Type",
        429 => "Too Many Requests",
        500 => "Internal Server Error",
        501 => "Not Implemented",
        503 => "Service Unavailable",
        _ => "Error",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn complete_json_request_is_parsed_exactly() {
        let bytes = b"POST /v1/authority/commit HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}";
        let request = parse_request_bytes(bytes, 4096).unwrap();
        assert_eq!(request.method, "POST");
        assert_eq!(request.target, "/v1/authority/commit");
        assert_eq!(request.header("content-type"), Some("application/json"));
        assert_eq!(request.body, b"{}");
    }

    #[test]
    fn duplicate_chunked_pipelined_and_noncanonical_lengths_fail_closed() {
        let cases = [
            b"POST / HTTP/1.1\r\nContent-Length: 1\r\nContent-Length: 1\r\n\r\nx".as_slice(),
            b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\nContent-Length: 0\r\n\r\n".as_slice(),
            b"POST / HTTP/1.1\r\nContent-Length: 01\r\n\r\nx".as_slice(),
            b"GET / HTTP/1.1\r\n\r\nGET /two HTTP/1.1\r\n\r\n".as_slice(),
        ];
        for value in cases {
            assert!(parse_request_bytes(value, 4096).is_err(), "{value:?}");
        }
    }

    #[test]
    fn response_framing_is_close_delimited_and_length_exact() {
        let response = Response::json(200, br#"{"ok":true}"#.to_vec());
        let mut output = Vec::new();
        response.write_to(&mut output).unwrap();
        let text = String::from_utf8(output).unwrap();
        assert!(text.starts_with("HTTP/1.1 200 OK\r\n"));
        assert!(text.contains("Content-Length: 11\r\n"));
        assert!(text.contains("Connection: close\r\n"));
        assert!(text.ends_with("{\"ok\":true}"));
    }
}
