use std::collections::BTreeMap;
use std::io::{self, ErrorKind, Read, Write};
use std::net::TcpStream;
use std::str;

use trnm_realtime_wire::{
    decode_authority_command, decode_client_frame, encode_authority_response, encode_server_frame,
    ClientFrame, Opcode, RealtimeEncoding, MAX_PAYLOAD_BYTES,
};

use super::app::{App, Repository};
use super::error::{InputError, ServerError};
use super::http::{Request, Response};

const WEBSOCKET_GUID: &str = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
const JSON_SUBPROTOCOL: &str = "trnm.json.v1";
const PROTOBUF_SUBPROTOCOL: &str = "trnm.protobuf.v1";
const MAX_MESSAGES_PER_CONNECTION: usize = 64;

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

    let maximum_payload = maximum_payload.min(MAX_PAYLOAD_BYTES);
    for _ in 0..MAX_MESSAGES_PER_CONNECTION {
        let frame = match read_client_frame_exact(stream, maximum_payload) {
            Ok(value) => value,
            Err(FrameReadError::Protocol) => {
                let _ = write_close_code(stream, 1002);
                return Ok(());
            }
            Err(FrameReadError::Io(error))
                if matches!(
                    error.kind(),
                    ErrorKind::UnexpectedEof | ErrorKind::ConnectionReset | ErrorKind::BrokenPipe
                ) =>
            {
                return Ok(());
            }
            Err(FrameReadError::Io(error))
                if matches!(error.kind(), ErrorKind::TimedOut | ErrorKind::WouldBlock) =>
            {
                let _ = write_close_code(stream, 1001);
                return Ok(());
            }
            Err(FrameReadError::Io(error)) => return Err(error.into()),
        };

        match frame.opcode {
            Opcode::Close => {
                write_frame(stream, Opcode::Close, &frame.payload)?;
                return Ok(());
            }
            Opcode::Ping => {
                write_frame(stream, Opcode::Pong, &frame.payload)?;
            }
            Opcode::Pong => {}
            Opcode::Text | Opcode::Binary => {
                if handshake.encoding.validate_data_frame(&frame).is_err() {
                    let _ = write_close_code(stream, 1003);
                    return Ok(());
                }
                let request_body = match handshake.encoding {
                    RealtimeEncoding::Json => frame.payload,
                    RealtimeEncoding::Protobuf => match decode_authority_command(&frame.payload) {
                        Ok(value) => value.json_request,
                        Err(_) => {
                            let _ = write_close_code(stream, 1007);
                            return Ok(());
                        }
                    },
                };
                let response = app.handle(&authority_request(
                    request_body,
                    handshake.authorization.as_deref(),
                ));
                let (opcode, response_body) = match handshake.encoding {
                    RealtimeEncoding::Json => (Opcode::Text, response.body),
                    RealtimeEncoding::Protobuf => {
                        let body = match encode_authority_response(response.status, &response.body)
                        {
                            Ok(value) => value,
                            Err(_) => {
                                let _ = write_close_code(stream, 1011);
                                return Ok(());
                            }
                        };
                        (Opcode::Binary, body)
                    }
                };
                write_frame(stream, opcode, &response_body)?;
                if app.should_stop() {
                    write_close_code(stream, 1001)?;
                    return Ok(());
                }
            }
        }
    }

    write_close_code(stream, 1008)
}

fn authority_request(body: Vec<u8>, authorization: Option<&str>) -> Request {
    let mut headers = BTreeMap::from([("content-type".to_owned(), "application/json".to_owned())]);
    if let Some(value) = authorization {
        headers.insert("authorization".to_owned(), value.to_owned());
    }
    Request::new("POST", "/v1/authority/commit", headers, body)
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct Handshake {
    response: String,
    authorization: Option<String>,
    encoding: RealtimeEncoding,
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
    let (protocol, encoding) = select_subprotocol(protocols)
        .ok_or_else(|| InputError::new("websocket_protocol_invalid"))?;

    let mut accept_source = String::with_capacity(key.len() + WEBSOCKET_GUID.len());
    accept_source.push_str(key);
    accept_source.push_str(WEBSOCKET_GUID);
    let accept = encode_base64(&sha1(accept_source.as_bytes()));
    Ok(Handshake {
        response: format!(
            "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\nSec-WebSocket-Protocol: {protocol}\r\nCache-Control: no-store\r\n\r\n"
        ),
        authorization: request.header("authorization").map(str::to_owned),
        encoding,
    })
}

fn select_subprotocol(value: &str) -> Option<(&'static str, RealtimeEncoding)> {
    value.split(',').find_map(|raw| {
        let protocol = raw.trim();
        if protocol.eq_ignore_ascii_case(JSON_SUBPROTOCOL) {
            Some((JSON_SUBPROTOCOL, RealtimeEncoding::Json))
        } else if protocol.eq_ignore_ascii_case(PROTOBUF_SUBPROTOCOL) {
            Some((PROTOBUF_SUBPROTOCOL, RealtimeEncoding::Protobuf))
        } else {
            None
        }
    })
}

fn header_has_token(value: &str, expected: &str) -> bool {
    value
        .split(',')
        .any(|token| token.trim().eq_ignore_ascii_case(expected))
}

#[derive(Debug)]
enum FrameReadError {
    Io(io::Error),
    Protocol,
}

fn read_client_frame_exact(
    input: &mut impl Read,
    maximum_payload: usize,
) -> Result<ClientFrame, FrameReadError> {
    let mut head = [0_u8; 2];
    input.read_exact(&mut head).map_err(FrameReadError::Io)?;
    let indicator = head[1] & 0x7f;
    let extended_length = match indicator {
        0..=125 => Vec::new(),
        126 => {
            let mut value = vec![0_u8; 2];
            input.read_exact(&mut value).map_err(FrameReadError::Io)?;
            value
        }
        127 => {
            let mut value = vec![0_u8; 8];
            input.read_exact(&mut value).map_err(FrameReadError::Io)?;
            value
        }
        _ => unreachable!("seven-bit frame length indicator"),
    };
    let payload_length = match indicator {
        value @ 0..=125 => u64::from(value),
        126 => u64::from(u16::from_be_bytes(
            extended_length
                .as_slice()
                .try_into()
                .expect("two-byte extended length"),
        )),
        127 => u64::from_be_bytes(
            extended_length
                .as_slice()
                .try_into()
                .expect("eight-byte extended length"),
        ),
        _ => unreachable!("seven-bit frame length indicator"),
    };
    let maximum_payload = u64::try_from(maximum_payload).map_err(|_| FrameReadError::Protocol)?;
    if payload_length > maximum_payload || payload_length > MAX_PAYLOAD_BYTES as u64 {
        return Err(FrameReadError::Protocol);
    }
    let mask_length = if head[1] & 0x80 == 0 { 0 } else { 4 };
    let payload_length = usize::try_from(payload_length).map_err(|_| FrameReadError::Protocol)?;
    let mut remainder = vec![0_u8; mask_length + payload_length];
    input
        .read_exact(&mut remainder)
        .map_err(FrameReadError::Io)?;

    let mut encoded = Vec::with_capacity(2 + extended_length.len() + remainder.len());
    encoded.extend_from_slice(&head);
    encoded.extend_from_slice(&extended_length);
    encoded.extend_from_slice(&remainder);
    let (frame, consumed) = decode_client_frame(&encoded).map_err(|_| FrameReadError::Protocol)?;
    if consumed != encoded.len() {
        return Err(FrameReadError::Protocol);
    }
    Ok(frame)
}

fn write_frame(output: &mut impl Write, opcode: Opcode, payload: &[u8]) -> Result<(), ServerError> {
    let encoded = encode_server_frame(opcode, payload)
        .map_err(|_| InputError::new("websocket_server_frame_invalid"))?;
    output.write_all(&encoded)?;
    output.flush()?;
    Ok(())
}

fn write_close_code(output: &mut impl Write, code: u16) -> Result<(), ServerError> {
    write_frame(output, Opcode::Close, &code.to_be_bytes())
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
            words[index] =
                (words[index - 3] ^ words[index - 8] ^ words[index - 14] ^ words[index - 16])
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
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
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
        match (chunk[2] == b'=', chunk[3] == b'=') {
            (true, true) => {
                if !last || second & 0x0f != 0 {
                    return Err(InputError::new("websocket_key_invalid"));
                }
                output.push((first << 2) | (second >> 4));
            }
            (false, true) => {
                let third = decode_base64_byte(chunk[2])?;
                if !last || third & 0x03 != 0 {
                    return Err(InputError::new("websocket_key_invalid"));
                }
                output.push((first << 2) | (second >> 4));
                output.push((second << 4) | (third >> 2));
            }
            (false, false) => {
                let third = decode_base64_byte(chunk[2])?;
                let fourth = decode_base64_byte(chunk[3])?;
                output.push((first << 2) | (second >> 4));
                output.push((second << 4) | (third >> 2));
                output.push((third << 6) | fourth);
            }
            (true, false) => return Err(InputError::new("websocket_key_invalid")),
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

    use trnm_realtime_wire::{
        decode_authority_response, encode_authority_command, FrameError, ProtobufError,
    };

    use super::*;

    fn request(key: &str, protocol: &str, version: &str) -> Request {
        Request::new(
            "GET",
            "/v1/realtime",
            BTreeMap::from([
                ("connection".to_owned(), "keep-alive, Upgrade".to_owned()),
                ("upgrade".to_owned(), "websocket".to_owned()),
                ("sec-websocket-version".to_owned(), version.to_owned()),
                ("sec-websocket-key".to_owned(), key.to_owned()),
                ("sec-websocket-protocol".to_owned(), protocol.to_owned()),
                ("authorization".to_owned(), "Bearer candidate".to_owned()),
            ]),
            Vec::new(),
        )
    }

    fn masked(opcode: Opcode, payload: &[u8]) -> Vec<u8> {
        let mask = [1_u8, 2, 3, 4];
        let mut frame = vec![0x80 | opcode as u8];
        match payload.len() {
            value @ 0..=125 => frame.push(0x80 | value as u8),
            value @ 126..=65_535 => {
                frame.push(0x80 | 126);
                frame.extend_from_slice(&(value as u16).to_be_bytes());
            }
            value => {
                frame.push(0x80 | 127);
                frame.extend_from_slice(&(value as u64).to_be_bytes());
            }
        }
        frame.extend_from_slice(&mask);
        frame.extend(
            payload
                .iter()
                .enumerate()
                .map(|(index, byte)| byte ^ mask[index % mask.len()]),
        );
        frame
    }

    fn masked_text(payload: &[u8]) -> Vec<u8> {
        masked(Opcode::Text, payload)
    }

    #[test]
    fn rfc6455_handshake_accept_matches_the_published_vector() {
        let handshake = validate_handshake(&request(
            "dGhlIHNhbXBsZSBub25jZQ==",
            "other, trnm.json.v1",
            "13",
        ))
        .unwrap();
        assert!(handshake
            .response
            .contains("Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\n"));
        assert!(handshake
            .response
            .contains("Sec-WebSocket-Protocol: trnm.json.v1\r\n"));
        assert_eq!(handshake.authorization.as_deref(), Some("Bearer candidate"));
        assert_eq!(handshake.encoding, RealtimeEncoding::Json);
    }

    #[test]
    fn protobuf_subprotocol_selects_binary_encoding() {
        let handshake = validate_handshake(&request(
            "dGhlIHNhbXBsZSBub25jZQ==",
            "trnm.protobuf.v1, trnm.json.v1",
            "13",
        ))
        .unwrap();
        assert!(handshake
            .response
            .contains("Sec-WebSocket-Protocol: trnm.protobuf.v1\r\n"));
        assert_eq!(handshake.encoding, RealtimeEncoding::Protobuf);
    }

    #[test]
    fn malformed_key_version_and_subprotocol_fail_closed() {
        for candidate in [
            request("invalid", JSON_SUBPROTOCOL, "13"),
            request("dGhlIHNhbXBsZSBub25jZQ==", "other", "13"),
            request("dGhlIHNhbXBsZSBub25jZQ==", JSON_SUBPROTOCOL, "12"),
        ] {
            assert!(validate_handshake(&candidate).is_err());
        }
    }

    #[test]
    fn masked_single_text_frame_is_unmasked_exactly() {
        let frame = masked_text(br#"{"command":"one"}"#);
        let payload = read_client_frame_exact(&mut Cursor::new(frame), 4096).unwrap();
        assert_eq!(payload.opcode, Opcode::Text);
        assert_eq!(payload.payload, br#"{"command":"one"}"#);
    }

    #[test]
    fn persistent_reader_keeps_frame_boundaries_and_control_frames() {
        let command = encode_authority_command(br#"{"command":"one"}"#).unwrap();
        let mut bytes = masked(Opcode::Ping, b"p");
        bytes.extend_from_slice(&masked(Opcode::Binary, &command));
        bytes.extend_from_slice(&masked(Opcode::Close, &1000_u16.to_be_bytes()));
        let mut input = Cursor::new(bytes);

        let ping = read_client_frame_exact(&mut input, 4096).unwrap();
        assert_eq!(
            ping,
            ClientFrame {
                opcode: Opcode::Ping,
                payload: b"p".to_vec()
            }
        );
        let binary = read_client_frame_exact(&mut input, 4096).unwrap();
        assert_eq!(binary.opcode, Opcode::Binary);
        let decoded = decode_authority_command(&binary.payload).unwrap();
        assert_eq!(decoded.json_request, br#"{"command":"one"}"#);
        let close = read_client_frame_exact(&mut input, 4096).unwrap();
        assert_eq!(close.opcode, Opcode::Close);
    }

    #[test]
    fn unmasked_fragmented_and_oversized_frames_are_rejected() {
        let unmasked = vec![0x81, 0x01, b'x'];
        assert!(matches!(
            read_client_frame_exact(&mut Cursor::new(unmasked), 4096),
            Err(FrameReadError::Protocol)
        ));

        let fragmented = vec![0x01, 0x81, 1, 2, 3, 4, b'x' ^ 1];
        assert!(matches!(
            read_client_frame_exact(&mut Cursor::new(fragmented), 4096),
            Err(FrameReadError::Protocol)
        ));

        let oversized = masked_text(b"0123456789");
        assert!(matches!(
            read_client_frame_exact(&mut Cursor::new(oversized), 4),
            Err(FrameReadError::Protocol)
        ));
    }

    #[test]
    fn server_text_and_close_frames_are_unmasked_and_canonical() {
        let mut output = Vec::new();
        write_frame(&mut output, Opcode::Text, b"ok").unwrap();
        write_frame(&mut output, Opcode::Binary, &[0x0a, 0x00]).unwrap();
        write_frame(&mut output, Opcode::Pong, b"p").unwrap();
        write_close_code(&mut output, 1000).unwrap();
        assert_eq!(
            output,
            [
                0x81, 0x02, b'o', b'k', 0x82, 0x02, 0x0a, 0x00, 0x8a, 0x01, b'p', 0x88, 0x02, 0x03,
                0xe8
            ]
        );
    }

    #[test]
    fn protobuf_response_envelope_preserves_status_and_json_body() {
        let encoded = encode_authority_response(503, br#"{"code":"unavailable"}"#).unwrap();
        let decoded = decode_authority_response(&encoded).unwrap();
        assert_eq!(decoded.status, 503);
        assert_eq!(decoded.json_body, br#"{"code":"unavailable"}"#);
        assert_eq!(
            decode_authority_command(&[0x12, 0x00]).unwrap_err(),
            ProtobufError::UnknownField(2)
        );
    }

    #[test]
    fn message_budget_is_nonzero_and_hard_bounded() {
        let budget = std::hint::black_box(MAX_MESSAGES_PER_CONNECTION);
        assert_eq!(budget, 64);
        assert!(budget <= 256);
    }

    #[test]
    fn shared_codec_rejects_encoding_mismatch() {
        let frame = ClientFrame {
            opcode: Opcode::Text,
            payload: b"{}".to_vec(),
        };
        assert_eq!(
            RealtimeEncoding::Protobuf
                .validate_data_frame(&frame)
                .unwrap_err(),
            FrameError::EncodingOpcodeMismatch
        );
    }

    #[test]
    fn sha1_and_base64_helpers_match_known_vectors() {
        assert_eq!(encode_base64(&sha1(b"abc")), "qZk+NkcGgWq6PiVxeFDCbJzQ2J0=");
        assert_eq!(
            decode_base64("dGhlIHNhbXBsZSBub25jZQ==").unwrap(),
            b"the sample nonce"
        );
    }
}
