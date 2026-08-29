use super::error::InputError;

const HEX: &[u8; 16] = b"0123456789abcdef";

pub fn decode_hex<const N: usize>(value: &str, reason: &'static str) -> Result<[u8; N], InputError> {
    if value.len() != N * 2 || !value.bytes().all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f')) {
        return Err(InputError::new(reason));
    }
    let mut output = [0_u8; N];
    for (index, pair) in value.as_bytes().chunks_exact(2).enumerate() {
        output[index] = (nibble(pair[0]) << 4) | nibble(pair[1]);
    }
    Ok(output)
}

#[must_use]
pub fn encode_hex(value: &[u8]) -> String {
    let mut output = String::with_capacity(value.len() * 2);
    for byte in value {
        output.push(char::from(HEX[usize::from(byte >> 4)]));
        output.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    output
}

const fn nibble(value: u8) -> u8 {
    match value {
        b'0'..=b'9' => value - b'0',
        b'a'..=b'f' => value - b'a' + 10,
        _ => 0,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fixed_hex_round_trip_is_lowercase_and_exact_width() {
        let value = [0x00, 0x01, 0xab, 0xff];
        assert_eq!(encode_hex(&value), "0001abff");
        assert_eq!(decode_hex::<4>("0001abff", "invalid").unwrap(), value);
    }

    #[test]
    fn noncanonical_or_wrong_width_hex_is_rejected() {
        for value in ["", "0001ABFF", "0001abf", "0001abfg"] {
            assert_eq!(decode_hex::<4>(value, "invalid_hex").unwrap_err().reason(), "invalid_hex");
        }
    }
}
