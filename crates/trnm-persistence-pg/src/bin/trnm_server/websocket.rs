use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::str;

use super::app::{App, Repository};
use super::error::{InputError, ServerError};
use super::http::{Request, Response};

const WEBSOCKET_GUID: &str = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
const JSON_SUBPROTOCOL: &str = "trnm.json.v1";
const OPCODE_TEXT: u8 = 0x1;
const OPCODE_CLOSE: u8 = 0x8;

#[must_use]
pub fn is_route(request: &Request) -> bool {
    request.method == "GET" && request.target == "/v1/realtime"
}

pub fn serve_once<R: Repository>(
    stream: &mut TcpStream,
    request: &Request,
    app: &mut App<R>,
    maximum_payload: usize,
) -> Result<(), ServerError> {
    let handshake = match validate_handshake(request) {
        Ok(value) => value,
        Err(_) => {
            Response::json(
                400,
                br#"{"code":"invalid_argument","message":"WebSocket handshake is invalid.","retry":"never"}"#
                    .to_vec(),
            )
            .write_to(stream)?;
            return Ok(());
        }
    };
    stream.write_all(handshake.response.as_bytes())?;
    stream.flush()?;

    let payload = match read_client_text_frame(stream, maximum_payload) {
        Ok(value) => value,
        Err(_) => {
            write_close_frame(stream, 1002)?;
            return Ok(());
        }
    };
    let mut headers = BTreeMap::from([(
        "content-type".to_owned(),
        "application/json".to_owned(),
    )]);
    if let Some(authorization) = handshake.authorization {
        headers.insert("authorization".to_owned(), authorization);
    }
    let response = app.handle(&Request::new(
        "POST",
        "/v1/authority/commit",
        headers,
        payload,
    ));
    write_server_frame(stream, OPCODE_TEXT, &response.body)?;
    write_close_frame(stream, 1000)?;
    Ok(())
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct Handshake {
    response: String,
    authorization: Option<String>,
}

fn validate_handshake(request: &Request) -> Result<Handshake, InputError> {
    if !is_route(request) {
        return Err(InputError::new("websocket_route_invalid"));
    }
    let connection = request
        .header("connection")
        .ok_or_else(|| InputError::new("websocket_connection_missing"))?;
    if !header_has_token(connection, "upgrade") {
        return Err(InputError::new("websocket_connection_invalid"));
    }
    if !request
        .header("upgrade")
        .is_some_and(|value| value.eq_ignore_ascii_case("websocket"))
    {
        return Err(InputError::new("websocket_upgrade_invalid"));
    }
    if request.header("sec-websocket-version") != Some("13") {
        return Err(InputError::new("websocket_version_invalid"));
    }
    let key = request
        .header("sec-websocket-key")
        .ok_or_else(|| InputError::new("websocket_key_missing"))?;
    if decode_base64(key)?.len() != 16 {
        return Err(InputError::new("websocket_key_invalid"));
    }
    let protocols = request
        .header("sec-websocket-protocol")
        .ok_or_else(|| InputError::new("websocket_protocol_missing"))?;
    if !header_has_token(protocols, JSON_SUBPROTOCOL) {
        return Err(InputError::new("websocket_protocol_invalid"));
    }

    let mut accept_source = String::with_capacity(key.len() + WEBSOCKET_GUID.len());
    accept_source.push_str(key);
    accept_source.push_str(WEBSOCKET_GUID);
    let accept = encode_base64(&sha1(accept_source.as_bytes()));
    Ok(Handshake {
        response: format!(
            "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\nSec-WebSocket-Protocol: {JSON_SUBPROTOCOL}\r\nCache-Control: no-store\r\n\r\n"
        ),
        authorization: request.header("authorization").map(str::to_owned),
    })
}

fn header_has_token(value: &str, expected: &str) -> bool {
    value
        .split(',')
        .any(|token| token.trim().eq_ignore_ascii_case(expected))
}

fn read_client_text_frame(
    input: &mut impl Read,
    maximum_payload: usize,
) -> Result<Vec<u8>, InputError> {
    let mut head = [0_u8; 2];
    input
        .read_exact(&mut head)
        .map_err(|_| InputError::new("websocket_frame_incomplete"))?;
    if head[0] & 0x80 == 0 || head[0] & 0x70 != 0 || head[0] & 0x0f != OPCODE_TEXT {
        return Err(InputError::new("websocket_frame_type_invalid"));
    }
    if head[1] & 0x80 == 0 {
        return Err(InputError::new("websocket_client_frame_unmasked"));
    }
    let marker = head[1] & 0x7f;
    let length = match marker {
        0..=125 => u64::from(marker),
        126 => {
            let mut bytes = [0_u8; 2];
            input
                .read_exact(&mut bytes)
                .map_err(|_| InputError::new("websocket_frame_incomplete"))?;
            let value = u64::from(u16::from_be_bytes(bytes));
            if value < 126 {
                return Err(InputError::new("websocket_length_not_canonical"));
            }
            value
        }
        127 => {
            let mut bytes = [0_u8; 8];
            input
                .read_exact(&mut bytes)
                .map_err(|_| InputError::new("websocket_frame_incomplete"))?;
            let value = u64::from_be_bytes(bytes);
            if value <= u64::from(u16::MAX) || value & (1_u64 << 63) != 0 {
                return Err(InputError::new("websocket_length_not_canonical"));
            }
            value
        }
        _ => unreachable!("seven-bit marker is exhausted"),
    };
    let length = usize::try_from(length)
        .map_err(|_| InputError::new("websocket_payload_too_large"))?;
    if length == 0 || length > maximum_payload {
        return Err(InputError::new("websocket_payload_too_large"));
    }
    let mut mask = [0_u8; 4];
    input
        .read_exact(&mut mask)
        .map_err(|_| InputError::new("websocket_frame_incomplete"))?;
    let mut payload = vec![0_u8; length];
    input
        .read_exact(&mut payload)
        .map_err(|_| InputError::new("websocket_frame_incomplete"))?;
    for (index, byte) in payload.iter_mut().enumerate() {
        *byte ^= mask[index % mask.len()];
    }
    str::from_utf8(&payload).map_err(|_| InputError::new("websocket_text_not_utf8"))?;
    Ok(payload)
}

fn write_server_frame(
    output: &mut impl Write,
    opcode: u8,
    payload: &[u8],
) -> Result<(), ServerError> {
    output.write_all(&[0x80 | opcode])?;
    write_length(output, payload.len())?;
    output.write_all(payload)?;
    output.flush()?;
    Ok(())
}

fn write_close_frame(output: &mut impl Write, code: u16) -> Result<(), ServerError> {
    write_server_frame(output, OPCODE_CLOSE, &code.to_be_bytes())
}

fn write_length(output: &mut impl Write, length: usize) -> Result<(), ServerError> {
    match length {
        0..=125 => output.write_all(&[u8::try_from(length).expect("bounded frame length")])?,
        126..=65_535 => {
            output.write_all(&[126])?;
            output.write_all(
                &u16::try_from(length)
                    .expect("bounded sixteen-bit frame length")
                    .to_be_bytes(),
            )?;
        }
        _ => {
            output.write_all(&[127])?;
            output.write_all(
                &u64::try_from(length)
                    .map_err(|_| InputError::new("websocket_payload_too_large"))?
                    .to_be_bytes(),
            )?;
        }
    }
    Ok(())
}

fn sha1(input: &[u8]) -> [u8; 20] {
    let bit_length = (input.len() as u64).wrapping_mul(8);
    let mut message = input.to_vec();
    message.push(0x80);
    while message.len() % 64 != 56 {
        message.push(0);
    }
    message.extend_from_slice(&bit_length.to_be_bytes());

    let mut state = [
        0x6745_2301_u32,
        0xefcd_ab89,
        0x98ba_dcfe,
        0x1032_5476,
        0xc3d2_e1f0,
    ];
    for block in message.chunks_exact(64) {
        let mut words = [0_u32; 80];
        for (index, word) in words.iter_mut().take(16).enumerate() {
            let offset = index * 4;
            *word = u32::from_be_bytes(
                block[offset..offset + 4]
                    .try_into()
                    .expect("SHA-1 block word is exactly four bytes"),
            );
        }
        for index in 16..80 {
            words[index] = (words[index - 3]
                ^ words[index - 8]
                ^ words[index - 14]
                ^ words[index - 16])
                .rotate_left(1);
        }

        let [mut a, mut b, mut c, mut d, mut e] = state;
        for (index, word) in words.iter().copied().enumerate() {
            let (function, constant) = match index {
                0..=19 => ((b & c) | ((!b) & d), 0x5a82_7999),
                20..=39 => (b ^ c ^ d, 0x6ed9_eba1),
                40..=59 => ((b & c) | (b & d) | (c & d), 0x8f1b_bcdc),
                _ => (b ^ c ^ d, 0xca62_c1d6),
            };
            let next = a
                .rotate_left(5)
                .wrapping_add(function)
                .wrapping_add(e)
                .wrapping_add(constant)
                .wrapping_add(word);
            e = d;
            d = c;
            c = b.rotate_left(30);
            b = a;
            a = next;
        }
        state[0] = state[0].wrapping_add(a);
        state[1] = state[1].wrapping_add(b);
        state[2] = state[2].wrapping_add(c);
        state[3] = state[3].wrapping_add(d);
        state[4] = state[4].wrapping_add(e);
    }

    let mut output = [0_u8; 20];
    for (index, word) in state.iter().enumerate() {
        output[index * 4..index * 4 + 4].copy_from_slice(&word.to_be_bytes());
    }
    output
}

fn encode_base64(input: &[u8]) -> String {
    const ALPHABET: &[u8; 64] =
        b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut output = String::with_capacity(input.len().div_ceil(3) * 4);
    for chunk in input.chunks(3) {
        let first = chunk[0];
        let second = chunk.get(1).copied().unwrap_or(0);
        let third = chunk.get(2).copied().unwrap_or(0);
        output.push(char::from(ALPHABET[usize::from(first >> 2)]));
        output.push(char::from(
            ALPHABET[usize::from(((first & 0x03) << 4) | (second >> 4))],
        ));
        if chunk.len() > 1 {
            output.push(char::from(
                ALPHABET[usize::from(((second & 0x0f) << 2) | (third >> 6))],
            ));
        } else {
            output.push('=');
        }
        if chunk.len() > 2 {
            output.push(char::from(ALPHABET[usize::from(third & 0x3f)]));
        } else {
            output.push('=');
        }
    }
    output
}

fn decode_base64(input: &str) -> Result<Vec<u8>, InputError> {
    if input.is_empty() || input.len() % 4 != 0 || !input.is_ascii() {
        return Err(InputError::new("websocket_key_invalid"));
    }
    let bytes = input.as_bytes();
    let mut output = Vec::with_capacity(input.len() / 4 * 3);
    for (index, chunk) in bytes.chunks_exact(4).enumerate() {
        let last = index + 1 == bytes.len() / 4;
        let first = decode_base64_byte(chunk[0])?;
        let second = decode_base64_byte(chunk[1])?;
        let third_padding = chunk[2] == b'=';
        let fourth_padding = chunk[3] == b'=';
        if third_padding && (!last || !fourth_padding || second & 0x0f != 0) {
            return Err(InputError::new("websocket_key_invalid"));
        }
        let third = if third_padding {
            0
        } else {
            decode_base64_byte(chunk[2])?
        };
        if fourth_padding && (!last || third_padding || third & 0x03 != 0) {
            return Err(InputError::new("websocket_key_invalid"));
        }
        let fourth = if fourth_padding {
            0
        } else {
            decode_base64_byte(chunk[3])?
        };
        output.push((first << 2) | (second >> 4));
        if !third_padding {
            output.push((second << 4) | (third >> 2));
        }
        if !fourth_padding {
            output.push((third << 6) | fourth);
        }
    }
    Ok(output)
}

fn decode_base64_byte(byte: u8) -> Result<u8, InputError> {
    match byte {
        b'A'..=b'Z' => Ok(byte - b'A'),
        b'a'..=b'z' => Ok(byte - b'a' + 26),
        b'0'..=b'9' => Ok(byte - b'0' + 52),
        b'+' => Ok(62),
        b'/' => Ok(63),
        _ => Err(InputError::new("websocket_key_invalid")),
    }
}

#[cfg(test)]
mod tests {
    use std::io::Cursor;

    use super::*;

    fn request(key: &str, protocol: &str) -> Request {
        Request::new(
            "GET",
            "/v1/realtime",
            BTreeMap::from([
                ("connection".to_owned(), "keep-alive, Upgrade".to_owned()),
                ("upgrade".to_owned(), "websocket".to_owned()),
                ("sec-websocket-version".to_owned(), "13".to_owned()),
                ("sec-websocket-key".to_owned(), key.to_owned()),
                ("sec-websocket-protocol".to_owned(), protocol.to_owned()),
                ("authorization".to_owned(), "Bearer candidate".to_owned()),
            ]),
            Vec::new(),
        )
    }

    fn masked_text(payload: &[u8]) -> Vec<u8> {
        let mask = [1_u8, 2, 3, 4];
        let mut frame = vec![0x81, 0x80 | u8::try_from(payload.len()).unwrap()];
        frame.extend_from_slice(&mask);
        frame.extend(
            payload
                .iter()
                .enumerate()
                .map(|(index, byte)| byte ^ mask[index % mask.len()]),
        );
        frame
    }

    #[test]
    fn rfc6455_handshake_accept_matches_the_published_vector() {
        let handshake = validate_handshake(&request(
            "dGhlIHNhbXBsZSBub25jZQ==",
            "other, trnm.json.v1",
        ))
        .unwrap();
        assert!(handshake
            .response
            .contains("Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\n"));
        assert!(handshake
            .response
            .contains("Sec-WebSocket-Protocol: trnm.json.v1\r\n"));
        assert_eq!(handshake.authorization.as_deref(), Some("Bearer candidate"));
    }

    #[test]
    fn malformed_key_version_and_subprotocol_fail_closed() {
        for candidate in [
            request("invalid", JSON_SUBPROTOCOL),
            request("dGhlIHNhbXBsZSBub25jZQ==", "other"),
        ] {
            assert!(validate_handshake(&candidate).is_err());
        }
        let mut wrong_version = request("dGhlIHNhbXBsZSBub25jZQ==", JSON_SUBPROTOCOL);
        wrong_version = Request::new(
            wrong_version.method,
            wrong_version.target,
            BTreeMap::from([
                ("connection".to_owned(), "Upgrade".to_owned()),
                ("upgrade".to_owned(), "websocket".to_owned()),
                ("sec-websocket-version".to_owned(), "12".to_owned()),
                (
                    "sec-websocket-key".to_owned(),
                    "dGhlIHNhbXBsZSBub25jZQ==".to_owned(),
                ),
                (
                    "sec-websocket-protocol".to_owned(),
                    JSON_SUBPROTOCOL.to_owned(),
                ),
            ]),
            Vec::new(),
        );
        assert!(validate_handshake(&wrong_version).is_err());
    }

    #[test]
    fn masked_single_text_frame_is_unmasked_exactly() {
        let frame = masked_text(br#"{"command":"one"}"#);
        let payload = read_client_text_frame(&mut Cursor::new(frame), 4096).unwrap();
        assert_eq!(payload, br#"{"command":"one"}"#);
    }

    #[test]
    fn unmasked_fragmented_and_oversized_frames_are_rejected() {
        let unmasked = vec![0x81, 0x01, b'x'];
        assert!(read_client_text_frame(&mut Cursor::new(unmasked), 4096).is_err());

        let fragmented = vec![0x01, 0x81, 1, 2, 3, 4, b'x' ^ 1];
        assert!(read_client_text_frame(&mut Cursor::new(fragmented), 4096).is_err());

        let oversized = masked_text(b"0123456789");
        assert!(read_client_text_frame(&mut Cursor::new(oversized), 4).is_err());
    }

    #[test]
    fn server_text_and_close_frames_are_unmasked_and_canonical() {
        let mut output = Vec::new();
        write_server_frame(&mut output, OPCODE_TEXT, b"ok").unwrap();
        write_close_frame(&mut output, 1000).unwrap();
        assert_eq!(output, [0x81, 0x02, b'o', b'k', 0x88, 0x02, 0x03, 0xe8]);
    }

    #[test]
    fn sha1_and_base64_helpers_match_known_vectors() {
        assert_eq!(
            encode_base64(&sha1(b"abc")),
            "qZk+NkcGgWq6PiVxeFDCbJzQ2J0="
        );
        assert_eq!(
            decode_base64("dGhlIHNhbXBsZSBub25jZQ==").unwrap(),
            b"the sample nonce"
        );
    }
}
