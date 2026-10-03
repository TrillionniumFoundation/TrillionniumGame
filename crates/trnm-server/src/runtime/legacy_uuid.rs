//! Shared source UUID text parsing: nil is accepted; endpoint owner policy stays external.
use super::codec::encode_hex;
use trnm_contracts::UserId;

/// Independent implementation of the six text formats accepted by the pinned
/// gofrs/uuid v5.4.0 parser. Hex digits are case insensitive; urn:uuid: is exact.
pub(super) fn parse_uuid(text: &str) -> Option<UserId> {
    let bytes = text.as_bytes();
    let body = match bytes.len() {
        32 | 36 => bytes,
        34 | 38 if bytes.first() == Some(&b'{') && bytes.last() == Some(&b'}') => {
            &bytes[1..bytes.len() - 1]
        }
        41 | 45 if bytes.starts_with(b"urn:uuid:") => &bytes[9..],
        _ => return None,
    };
    if body.len() == 36 && [8, 13, 18, 23].iter().any(|index| body[*index] != b'-') {
        return None;
    }
    let mut output = [0_u8; 16];
    let mut index = 0;
    let mut nibble = 0;
    for (position, byte) in body.iter().copied().enumerate() {
        if body.len() == 36 && matches!(position, 8 | 13 | 18 | 23) {
            continue;
        }
        let digit = match byte {
            b'0'..=b'9' => byte - b'0',
            b'a'..=b'f' => byte - b'a' + 10,
            b'A'..=b'F' => byte - b'A' + 10,
            _ => return None,
        };
        if nibble % 2 == 0 {
            output[index] = digit << 4;
        } else {
            output[index] |= digit;
            index += 1;
        }
        nibble += 1;
    }
    Some(UserId::new(output))
}

pub(super) fn uuid_string(user: UserId) -> String {
    let hex = encode_hex(user.as_bytes());
    format!(
        "{}-{}-{}-{}-{}",
        &hex[..8],
        &hex[8..12],
        &hex[12..16],
        &hex[16..20],
        &hex[20..],
    )
}
