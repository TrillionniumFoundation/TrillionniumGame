#![forbid(unsafe_code)]
#![deny(missing_debug_implementations)]

//! Strict bounded RFC6455 frame boundary for a first realtime vertical slice.
//!
//! The slice accepts only complete, non-fragmented client frames. Client data
//! must be masked, RSV bits must be zero, control payloads are limited to 125
//! bytes and all payloads are bounded. Compression and extension negotiation
//! are intentionally outside this source candidate.

use core::fmt;

pub const MAX_PAYLOAD_BYTES: usize = 128 * 1024;

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
        value @ 126..=65535 => {
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
            value @ 126..=65535 => {
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

        let protobuf = masked(Opcode::Binary, &[0x0a, 0x01, 0x31]);
        let (frame, _) = decode_client_frame(&protobuf).unwrap();
        RealtimeEncoding::Protobuf
            .validate_data_frame(&frame)
            .unwrap();
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
        let encoded = encode_server_frame(Opcode::Binary, &vec![7; 126]).unwrap();
        assert_eq!(&encoded[..4], &[0x82, 126, 0, 126]);
        assert_eq!(encoded.len(), 130);
    }
}
