#![forbid(unsafe_code)]
#![deny(missing_debug_implementations)]

//! Strict bounded realtime wire primitives for the first TrillionniumGame
//! WebSocket vertical slice.
//!
//! RFC 6455 frames are accepted only when they are complete, non-fragmented,
//! canonically length encoded and bounded. The checked-in protobuf envelope is
//! intentionally narrow: it transports the already reviewed JSON authority
//! request/response through binary frames without claiming Nakama protobuf
//! compatibility.

use core::fmt;

pub const MAX_PAYLOAD_BYTES: usize = 128 * 1024;
pub const MAX_ENVELOPE_BODY_BYTES: usize = MAX_PAYLOAD_BYTES - 16;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(u8)]
pub enum Opcode {
    Text = 0x1,
    Binary = 0x2,
    Close = 0x8,
    Ping = 0x9,
    Pong = 0xa,
}

impl Opcode {
    fn parse(value: u8) -> Result<Self, FrameError> {
        match value {
            0x1 => Ok(Self::Text),
            0x2 => Ok(Self::Binary),
            0x8 => Ok(Self::Close),
            0x9 => Ok(Self::Ping),
            0xa => Ok(Self::Pong),
            0x0 => Err(FrameError::FragmentationUnsupported),
            _ => Err(FrameError::UnsupportedOpcode(value)),
        }
    }

    #[must_use]
    pub const fn is_control(self) -> bool {
        matches!(self, Self::Close | Self::Ping | Self::Pong)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RealtimeEncoding {
    Json,
    Protobuf,
}

impl RealtimeEncoding {
    #[must_use]
    pub const fn expected_opcode(self) -> Opcode {
        match self {
            Self::Json => Opcode::Text,
            Self::Protobuf => Opcode::Binary,
        }
    }

    pub fn validate_data_frame(self, frame: &ClientFrame) -> Result<(), FrameError> {
        if frame.opcode != self.expected_opcode() {
            return Err(FrameError::EncodingOpcodeMismatch);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ClientFrame {
    pub opcode: Opcode,
    pub payload: Vec<u8>,
}

impl ClientFrame {
    pub fn close(&self) -> Result<Option<CloseFrame<'_>>, FrameError> {
        if self.opcode != Opcode::Close {
            return Ok(None);
        }
        if self.payload.is_empty() {
            return Ok(Some(CloseFrame {
                code: None,
                reason: "",
            }));
        }
        if self.payload.len() == 1 {
            return Err(FrameError::InvalidClosePayload);
        }
        let code = u16::from_be_bytes([self.payload[0], self.payload[1]]);
        if !valid_close_code(code) {
            return Err(FrameError::InvalidCloseCode(code));
        }
        let reason =
            std::str::from_utf8(&self.payload[2..]).map_err(|_| FrameError::InvalidCloseReason)?;
        Ok(Some(CloseFrame {
            code: Some(code),
            reason,
        }))
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct CloseFrame<'a> {
    pub code: Option<u16>,
    pub reason: &'a str,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum FrameError {
    Incomplete,
    ReservedBitsSet,
    FragmentationUnsupported,
    UnsupportedOpcode(u8),
    ClientFrameNotMasked,
    PayloadLengthNonCanonical,
    PayloadTooLarge { actual: u64 },
    ControlPayloadTooLarge,
    InvalidTextUtf8,
    InvalidClosePayload,
    InvalidCloseCode(u16),
    InvalidCloseReason,
    EncodingOpcodeMismatch,
}

impl fmt::Display for FrameError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Incomplete => formatter.write_str("WebSocket frame is incomplete"),
            Self::ReservedBitsSet => formatter.write_str("WebSocket RSV bits are not negotiated"),
            Self::FragmentationUnsupported => {
                formatter.write_str("fragmented WebSocket frames are not accepted by this profile")
            }
            Self::UnsupportedOpcode(value) => {
                write!(formatter, "unsupported WebSocket opcode {value:#x}")
            }
            Self::ClientFrameNotMasked => {
                formatter.write_str("client WebSocket frame must be masked")
            }
            Self::PayloadLengthNonCanonical => {
                formatter.write_str("WebSocket payload length uses a non-canonical encoding")
            }
            Self::PayloadTooLarge { actual } => write!(
                formatter,
                "WebSocket payload {actual} exceeds {MAX_PAYLOAD_BYTES} bytes"
            ),
            Self::ControlPayloadTooLarge => {
                formatter.write_str("WebSocket control payload exceeds 125 bytes")
            }
            Self::InvalidTextUtf8 => {
                formatter.write_str("WebSocket text payload is not valid UTF-8")
            }
            Self::InvalidClosePayload => formatter.write_str("WebSocket close payload is invalid"),
            Self::InvalidCloseCode(code) => {
                write!(formatter, "WebSocket close code {code} is invalid")
            }
            Self::InvalidCloseReason => {
                formatter.write_str("WebSocket close reason is not valid UTF-8")
            }
            Self::EncodingOpcodeMismatch => formatter
                .write_str("WebSocket opcode does not match negotiated JSON/protobuf encoding"),
        }
    }
}

impl std::error::Error for FrameError {}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AuthorityCommandEnvelope {
    pub json_request: Vec<u8>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AuthorityResponseEnvelope {
    pub status: u16,
    pub json_body: Vec<u8>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ProtobufError {
    Incomplete,
    InvalidFieldKey,
    InvalidWireType { field: u32, wire_type: u8 },
    UnknownField(u32),
    DuplicateField(u32),
    NonCanonicalVarint,
    VarintOverflow,
    LengthOverflow,
    PayloadTooLarge { actual: usize },
    MissingField(u32),
    InvalidUtf8(u32),
    InvalidStatus(u64),
}

impl fmt::Display for ProtobufError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Incomplete => formatter.write_str("protobuf envelope is incomplete"),
            Self::InvalidFieldKey => formatter.write_str("protobuf field key is invalid"),
            Self::InvalidWireType { field, wire_type } => {
                write!(formatter, "protobuf field {field} has wire type {wire_type}")
            }
            Self::UnknownField(field) => write!(formatter, "protobuf field {field} is unknown"),
            Self::DuplicateField(field) => {
                write!(formatter, "protobuf field {field} is duplicated")
            }
            Self::NonCanonicalVarint => {
                formatter.write_str("protobuf varint is not minimally encoded")
            }
            Self::VarintOverflow => formatter.write_str("protobuf varint overflows u64"),
            Self::LengthOverflow => formatter.write_str("protobuf length overflows usize"),
            Self::PayloadTooLarge { actual } => write!(
                formatter,
                "protobuf envelope payload {actual} exceeds {MAX_ENVELOPE_BODY_BYTES} bytes"
            ),
            Self::MissingField(field) => {
                write!(formatter, "protobuf required field {field} is missing")
            }
            Self::InvalidUtf8(field) => {
                write!(formatter, "protobuf bytes field {field} is not UTF-8 JSON")
            }
            Self::InvalidStatus(status) => {
                write!(formatter, "protobuf response status {status} is invalid")
            }
        }
    }
}

impl std::error::Error for ProtobufError {}

pub fn decode_client_frame(input: &[u8]) -> Result<(ClientFrame, usize), FrameError> {
    if input.len() < 2 {
        return Err(FrameError::Incomplete);
    }
    let first = input[0];
    let second = input[1];
    if first & 0x70 != 0 {
        return Err(FrameError::ReservedBitsSet);
    }
    if first & 0x80 == 0 {
        return Err(FrameError::FragmentationUnsupported);
    }
    let opcode = Opcode::parse(first & 0x0f)?;
    if second & 0x80 == 0 {
        return Err(FrameError::ClientFrameNotMasked);
    }

    let indicator = second & 0x7f;
    let (payload_length, header_length) = match indicator {
        value @ 0..=125 => (u64::from(value), 2usize),
        126 => {
            if input.len() < 4 {
                return Err(FrameError::Incomplete);
            }
            let value = u64::from(u16::from_be_bytes([input[2], input[3]]));
            if value < 126 {
                return Err(FrameError::PayloadLengthNonCanonical);
            }
            (value, 4)
        }
        127 => {
            if input.len() < 10 {
                return Err(FrameError::Incomplete);
            }
            if input[2] & 0x80 != 0 {
                return Err(FrameError::PayloadTooLarge { actual: u64::MAX });
            }
            let value = u64::from_be_bytes(input[2..10].try_into().expect("eight-byte length"));
            if value <= u64::from(u16::MAX) {
                return Err(FrameError::PayloadLengthNonCanonical);
            }
            (value, 10)
        }
        128..=u8::MAX => unreachable!("masked payload indicator is at most 127"),
    };
    if opcode.is_control() && payload_length > 125 {
        return Err(FrameError::ControlPayloadTooLarge);
    }
    if payload_length > MAX_PAYLOAD_BYTES as u64 {
        return Err(FrameError::PayloadTooLarge {
            actual: payload_length,
        });
    }
    let payload_length = usize::try_from(payload_length)
        .map_err(|_| FrameError::PayloadTooLarge { actual: u64::MAX })?;
    let mask_end = header_length.checked_add(4).ok_or(FrameError::Incomplete)?;
    let frame_end = mask_end
        .checked_add(payload_length)
        .ok_or(FrameError::PayloadTooLarge { actual: u64::MAX })?;
    if input.len() < frame_end {
        return Err(FrameError::Incomplete);
    }
    let mask: [u8; 4] = input[header_length..mask_end]
        .try_into()
        .expect("four-byte mask");
    let mut payload = Vec::with_capacity(payload_length);
    for (index, byte) in input[mask_end..frame_end].iter().copied().enumerate() {
        payload.push(byte ^ mask[index % 4]);
    }
    if opcode == Opcode::Text && std::str::from_utf8(&payload).is_err() {
        return Err(FrameError::InvalidTextUtf8);
    }
    let frame = ClientFrame { opcode, payload };
    if opcode == Opcode::Close {
        frame.close()?;
    }
    Ok((frame, frame_end))
}

pub fn encode_server_frame(opcode: Opcode, payload: &[u8]) -> Result<Vec<u8>, FrameError> {
    if payload.len() > MAX_PAYLOAD_BYTES {
        return Err(FrameError::PayloadTooLarge {
            actual: payload.len() as u64,
        });
    }
    if opcode.is_control() && payload.len() > 125 {
        return Err(FrameError::ControlPayloadTooLarge);
    }
    if opcode == Opcode::Text && std::str::from_utf8(payload).is_err() {
        return Err(FrameError::InvalidTextUtf8);
    }
    let mut output = Vec::with_capacity(payload.len().saturating_add(10));
    output.push(0x80 | opcode as u8);
    match payload.len() {
        value @ 0..=125 => output.push(value as u8),
        value @ 126..=65_535 => {
            output.push(126);
            output.extend_from_slice(&(value as u16).to_be_bytes());
        }
        value => {
            output.push(127);
            output.extend_from_slice(&(value as u64).to_be_bytes());
        }
    }
    output.extend_from_slice(payload);
    Ok(output)
}

pub fn encode_authority_command(json_request: &[u8]) -> Result<Vec<u8>, ProtobufError> {
    validate_json_bytes(1, json_request)?;
    let mut output = Vec::with_capacity(json_request.len().saturating_add(5));
    output.push(0x0a);
    write_varint(
        u64::try_from(json_request.len()).map_err(|_| ProtobufError::LengthOverflow)?,
        &mut output,
    );
    output.extend_from_slice(json_request);
    Ok(output)
}

pub fn decode_authority_command(
    input: &[u8],
) -> Result<AuthorityCommandEnvelope, ProtobufError> {
    let mut cursor = 0usize;
    let mut json_request = None;
    while cursor < input.len() {
        let (field, wire_type) = read_key(input, &mut cursor)?;
        match (field, wire_type) {
            (1, 2) => {
                if json_request.is_some() {
                    return Err(ProtobufError::DuplicateField(1));
                }
                let value = read_length_delimited(input, &mut cursor)?;
                validate_json_bytes(1, value)?;
                json_request = Some(value.to_vec());
            }
            (1, other) => {
                return Err(ProtobufError::InvalidWireType {
                    field: 1,
                    wire_type: other,
                });
            }
            (other, _) => return Err(ProtobufError::UnknownField(other)),
        }
    }
    Ok(AuthorityCommandEnvelope {
        json_request: json_request.ok_or(ProtobufError::MissingField(1))?,
    })
}

pub fn encode_authority_response(
    status: u16,
    json_body: &[u8],
) -> Result<Vec<u8>, ProtobufError> {
    if !(100..=599).contains(&status) {
        return Err(ProtobufError::InvalidStatus(u64::from(status)));
    }
    validate_json_bytes(2, json_body)?;
    let mut output = Vec::with_capacity(json_body.len().saturating_add(9));
    output.push(0x08);
    write_varint(u64::from(status), &mut output);
    output.push(0x12);
    write_varint(
        u64::try_from(json_body.len()).map_err(|_| ProtobufError::LengthOverflow)?,
        &mut output,
    );
    output.extend_from_slice(json_body);
    Ok(output)
}

pub fn decode_authority_response(
    input: &[u8],
) -> Result<AuthorityResponseEnvelope, ProtobufError> {
    let mut cursor = 0usize;
    let mut status = None;
    let mut json_body = None;
    while cursor < input.len() {
        let (field, wire_type) = read_key(input, &mut cursor)?;
        match (field, wire_type) {
            (1, 0) => {
                if status.is_some() {
                    return Err(ProtobufError::DuplicateField(1));
                }
                let raw = read_varint(input, &mut cursor)?;
                if !(100..=599).contains(&raw) {
                    return Err(ProtobufError::InvalidStatus(raw));
                }
                status = Some(u16::try_from(raw).expect("validated HTTP status fits u16"));
            }
            (1, other) => {
                return Err(ProtobufError::InvalidWireType {
                    field: 1,
                    wire_type: other,
                });
            }
            (2, 2) => {
                if json_body.is_some() {
                    return Err(ProtobufError::DuplicateField(2));
                }
                let value = read_length_delimited(input, &mut cursor)?;
                validate_json_bytes(2, value)?;
                json_body = Some(value.to_vec());
            }
            (2, other) => {
                return Err(ProtobufError::InvalidWireType {
                    field: 2,
                    wire_type: other,
                });
            }
            (other, _) => return Err(ProtobufError::UnknownField(other)),
        }
    }
    Ok(AuthorityResponseEnvelope {
        status: status.ok_or(ProtobufError::MissingField(1))?,
        json_body: json_body.ok_or(ProtobufError::MissingField(2))?,
    })
}

fn validate_json_bytes(field: u32, value: &[u8]) -> Result<(), ProtobufError> {
    if value.is_empty() || value.len() > MAX_ENVELOPE_BODY_BYTES {
        return Err(ProtobufError::PayloadTooLarge {
            actual: value.len(),
        });
    }
    std::str::from_utf8(value).map_err(|_| ProtobufError::InvalidUtf8(field))?;
    Ok(())
}

fn read_key(input: &[u8], cursor: &mut usize) -> Result<(u32, u8), ProtobufError> {
    let key = read_varint(input, cursor)?;
    let field = u32::try_from(key >> 3).map_err(|_| ProtobufError::InvalidFieldKey)?;
    let wire_type = u8::try_from(key & 0x07).expect("three-bit wire type");
    if field == 0 {
        return Err(ProtobufError::InvalidFieldKey);
    }
    Ok((field, wire_type))
}

fn read_length_delimited<'a>(
    input: &'a [u8],
    cursor: &mut usize,
) -> Result<&'a [u8], ProtobufError> {
    let length = usize::try_from(read_varint(input, cursor)?)
        .map_err(|_| ProtobufError::LengthOverflow)?;
    if length > MAX_ENVELOPE_BODY_BYTES {
        return Err(ProtobufError::PayloadTooLarge { actual: length });
    }
    let end = cursor
        .checked_add(length)
        .ok_or(ProtobufError::LengthOverflow)?;
    let value = input.get(*cursor..end).ok_or(ProtobufError::Incomplete)?;
    *cursor = end;
    Ok(value)
}

fn read_varint(input: &[u8], cursor: &mut usize) -> Result<u64, ProtobufError> {
    let start = *cursor;
    let mut value = 0u64;
    for index in 0..10usize {
        let byte = *input.get(*cursor).ok_or(ProtobufError::Incomplete)?;
        *cursor = (*cursor)
            .checked_add(1)
            .ok_or(ProtobufError::LengthOverflow)?;
        if index == 9 && byte > 1 {
            return Err(ProtobufError::VarintOverflow);
        }
        value |= u64::from(byte & 0x7f) << (index * 7);
        if byte & 0x80 == 0 {
            if *cursor - start != varint_length(value) {
                return Err(ProtobufError::NonCanonicalVarint);
            }
            return Ok(value);
        }
    }
    Err(ProtobufError::VarintOverflow)
}

fn write_varint(mut value: u64, output: &mut Vec<u8>) {
    while value >= 0x80 {
        output.push((value as u8 & 0x7f) | 0x80);
        value >>= 7;
    }
    output.push(value as u8);
}

fn varint_length(mut value: u64) -> usize {
    let mut length = 1usize;
    while value >= 0x80 {
        value >>= 7;
        length += 1;
    }
    length
}

fn valid_close_code(code: u16) -> bool {
    matches!(code, 1000..=1003 | 1007..=1014 | 3000..=4999)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn masked(opcode: Opcode, payload: &[u8]) -> Vec<u8> {
        let mask = [0x12, 0x34, 0x56, 0x78];
        let mut output = vec![0x80 | opcode as u8];
        match payload.len() {
            value @ 0..=125 => output.push(0x80 | value as u8),
            value @ 126..=65_535 => {
                output.push(0x80 | 126);
                output.extend_from_slice(&(value as u16).to_be_bytes());
            }
            value => {
                output.push(0x80 | 127);
                output.extend_from_slice(&(value as u64).to_be_bytes());
            }
        }
        output.extend_from_slice(&mask);
        output.extend(
            payload
                .iter()
                .copied()
                .enumerate()
                .map(|(index, byte)| byte ^ mask[index % 4]),
        );
        output
    }

    #[test]
    fn json_and_protobuf_frames_decode_exactly() {
        let json = masked(Opcode::Text, br#"{"cid":"1"}"#);
        let (frame, consumed) = decode_client_frame(&json).unwrap();
        assert_eq!(consumed, json.len());
        assert_eq!(frame.payload, br#"{"cid":"1"}"#);
        RealtimeEncoding::Json.validate_data_frame(&frame).unwrap();
        assert_eq!(
            RealtimeEncoding::Protobuf
                .validate_data_frame(&frame)
                .unwrap_err(),
            FrameError::EncodingOpcodeMismatch
        );

        let protobuf = masked(Opcode::Binary, &[0x0a, 0x01, b'x']);
        let (frame, _) = decode_client_frame(&protobuf).unwrap();
        RealtimeEncoding::Protobuf
            .validate_data_frame(&frame)
            .unwrap();
    }

    #[test]
    fn authority_command_envelope_round_trips_strictly() {
        let encoded = encode_authority_command(br#"{"command":"one"}"#).unwrap();
        let decoded = decode_authority_command(&encoded).unwrap();
        assert_eq!(decoded.json_request, br#"{"command":"one"}"#);

        let mut duplicate = encoded.clone();
        duplicate.extend_from_slice(&encoded);
        assert_eq!(
            decode_authority_command(&duplicate).unwrap_err(),
            ProtobufError::DuplicateField(1)
        );
        assert_eq!(
            decode_authority_command(&[0x12, 0x01, b'x']).unwrap_err(),
            ProtobufError::UnknownField(2)
        );
        assert_eq!(
            decode_authority_command(&[0x0a, 0x81, 0x00, b'x']).unwrap_err(),
            ProtobufError::NonCanonicalVarint
        );
    }

    #[test]
    fn authority_response_envelope_round_trips_status_and_body() {
        let encoded = encode_authority_response(409, br#"{"code":"aborted"}"#).unwrap();
        let decoded = decode_authority_response(&encoded).unwrap();
        assert_eq!(decoded.status, 409);
        assert_eq!(decoded.json_body, br#"{"code":"aborted"}"#);
        assert_eq!(
            encode_authority_response(99, b"{}").unwrap_err(),
            ProtobufError::InvalidStatus(99)
        );
        assert_eq!(
            decode_authority_response(&[0x08, 0x63, 0x12, 0x02, b'{', b'}']).unwrap_err(),
            ProtobufError::InvalidStatus(99)
        );
    }

    #[test]
    fn unmasked_fragmented_reserved_and_unknown_frames_fail_closed() {
        assert_eq!(
            decode_client_frame(&[0x81, 0x00]).unwrap_err(),
            FrameError::ClientFrameNotMasked
        );
        assert_eq!(
            decode_client_frame(&[0x01, 0x80, 0, 0, 0, 0]).unwrap_err(),
            FrameError::FragmentationUnsupported
        );
        assert_eq!(
            decode_client_frame(&[0xc1, 0x80, 0, 0, 0, 0]).unwrap_err(),
            FrameError::ReservedBitsSet
        );
        assert_eq!(
            decode_client_frame(&[0x83, 0x80, 0, 0, 0, 0]).unwrap_err(),
            FrameError::UnsupportedOpcode(3)
        );
    }

    #[test]
    fn payload_lengths_are_canonical_and_bounded() {
        let noncanonical = [0x82, 0x80 | 126, 0, 1, 0, 0, 0, 0, 0];
        assert_eq!(
            decode_client_frame(&noncanonical).unwrap_err(),
            FrameError::PayloadLengthNonCanonical
        );
        let too_large = [0x82, 0x80 | 127, 0, 0, 0, 0, 0, 3, 0, 0, 0, 0, 0, 0];
        assert!(matches!(
            decode_client_frame(&too_large).unwrap_err(),
            FrameError::PayloadTooLarge { .. }
        ));
        assert_eq!(
            encode_server_frame(Opcode::Binary, &vec![0; MAX_PAYLOAD_BYTES + 1]).unwrap_err(),
            FrameError::PayloadTooLarge {
                actual: (MAX_PAYLOAD_BYTES + 1) as u64
            }
        );
    }

    #[test]
    fn text_and_close_payload_validation_is_strict() {
        assert_eq!(
            decode_client_frame(&masked(Opcode::Text, &[0xff])).unwrap_err(),
            FrameError::InvalidTextUtf8
        );
        assert_eq!(
            decode_client_frame(&masked(Opcode::Close, &[0x03])).unwrap_err(),
            FrameError::InvalidClosePayload
        );
        assert_eq!(
            decode_client_frame(&masked(Opcode::Close, &[0x03, 0xed])).unwrap_err(),
            FrameError::InvalidCloseCode(1005)
        );
        let close = masked(Opcode::Close, &[0x03, 0xe8, b'b', b'y', b'e']);
        let (frame, _) = decode_client_frame(&close).unwrap();
        assert_eq!(
            frame.close().unwrap(),
            Some(CloseFrame {
                code: Some(1000),
                reason: "bye"
            })
        );
    }

    #[test]
    fn server_frames_are_unmasked_and_minimally_encoded() {
        let encoded = encode_server_frame(Opcode::Text, b"ok").unwrap();
        assert_eq!(encoded, [0x81, 0x02, b'o', b'k']);
        let encoded = encode_server_frame(Opcode::Binary, &[7; 126]).unwrap();
        assert_eq!(&encoded[..4], &[0x82, 126, 0, 126]);
        assert_eq!(encoded.len(), 130);
    }
}
