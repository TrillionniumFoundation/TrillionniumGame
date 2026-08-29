use core::fmt;

const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Base64UrlError {
    InvalidCharacter { index: usize, byte: u8 },
    InvalidLength,
    NonCanonicalTrailingBits,
    DecodedLengthExceeded { limit: usize },
}

impl fmt::Display for Base64UrlError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidCharacter { index, byte } => {
                write!(
                    formatter,
                    "invalid base64url byte 0x{byte:02x} at index {index}"
                )
            }
            Self::InvalidLength => formatter.write_str("invalid unpadded base64url length"),
            Self::NonCanonicalTrailingBits => {
                formatter.write_str("non-canonical base64url trailing bits")
            }
            Self::DecodedLengthExceeded { limit } => {
                write!(formatter, "decoded base64url length exceeds {limit} bytes")
            }
        }
    }
}

impl std::error::Error for Base64UrlError {}

pub fn encode(input: &[u8]) -> String {
    let full_groups = input.len() / 3;
    let remainder = input.len() % 3;
    let encoded_len = full_groups
        .checked_mul(4)
        .and_then(|value| {
            value.checked_add(match remainder {
                0 => 0,
                1 => 2,
                2 => 3,
                _ => unreachable!(),
            })
        })
        .expect("base64url output length overflow");
    let mut output = String::with_capacity(encoded_len);

    for chunk in input.chunks_exact(3) {
        let value = (u32::from(chunk[0]) << 16) | (u32::from(chunk[1]) << 8) | u32::from(chunk[2]);
        output.push(char::from(ALPHABET[((value >> 18) & 0x3f) as usize]));
        output.push(char::from(ALPHABET[((value >> 12) & 0x3f) as usize]));
        output.push(char::from(ALPHABET[((value >> 6) & 0x3f) as usize]));
        output.push(char::from(ALPHABET[(value & 0x3f) as usize]));
    }

    let start = full_groups * 3;
    match remainder {
        0 => {}
        1 => {
            let value = u32::from(input[start]) << 16;
            output.push(char::from(ALPHABET[((value >> 18) & 0x3f) as usize]));
            output.push(char::from(ALPHABET[((value >> 12) & 0x3f) as usize]));
        }
        2 => {
            let value = (u32::from(input[start]) << 16) | (u32::from(input[start + 1]) << 8);
            output.push(char::from(ALPHABET[((value >> 18) & 0x3f) as usize]));
            output.push(char::from(ALPHABET[((value >> 12) & 0x3f) as usize]));
            output.push(char::from(ALPHABET[((value >> 6) & 0x3f) as usize]));
        }
        _ => unreachable!(),
    }

    debug_assert_eq!(output.len(), encoded_len);
    output
}

pub fn decode(input: &str, max_decoded_len: usize) -> Result<Vec<u8>, Base64UrlError> {
    let bytes = input.as_bytes();
    if bytes.len() % 4 == 1 {
        return Err(Base64UrlError::InvalidLength);
    }
    let decoded_len = bytes
        .len()
        .checked_div(4)
        .and_then(|groups| groups.checked_mul(3))
        .and_then(|value| {
            value.checked_add(match bytes.len() % 4 {
                0 => 0,
                2 => 1,
                3 => 2,
                _ => unreachable!(),
            })
        })
        .ok_or(Base64UrlError::DecodedLengthExceeded {
            limit: max_decoded_len,
        })?;
    if decoded_len > max_decoded_len {
        return Err(Base64UrlError::DecodedLengthExceeded {
            limit: max_decoded_len,
        });
    }

    let mut values = Vec::with_capacity(bytes.len());
    for (index, byte) in bytes.iter().copied().enumerate() {
        values.push(decode_byte(index, byte)?);
    }

    let mut output = Vec::with_capacity(decoded_len);
    for chunk in values.chunks_exact(4) {
        let value = (u32::from(chunk[0]) << 18)
            | (u32::from(chunk[1]) << 12)
            | (u32::from(chunk[2]) << 6)
            | u32::from(chunk[3]);
        output.push(((value >> 16) & 0xff) as u8);
        output.push(((value >> 8) & 0xff) as u8);
        output.push((value & 0xff) as u8);
    }

    let remainder_start = values.len() - values.len() % 4;
    match values.len() % 4 {
        0 => {}
        2 => {
            let first = values[remainder_start];
            let second = values[remainder_start + 1];
            if second & 0x0f != 0 {
                return Err(Base64UrlError::NonCanonicalTrailingBits);
            }
            output.push((first << 2) | (second >> 4));
        }
        3 => {
            let first = values[remainder_start];
            let second = values[remainder_start + 1];
            let third = values[remainder_start + 2];
            if third & 0x03 != 0 {
                return Err(Base64UrlError::NonCanonicalTrailingBits);
            }
            output.push((first << 2) | (second >> 4));
            output.push((second << 4) | (third >> 2));
        }
        _ => return Err(Base64UrlError::InvalidLength),
    }

    debug_assert_eq!(output.len(), decoded_len);
    Ok(output)
}

fn decode_byte(index: usize, byte: u8) -> Result<u8, Base64UrlError> {
    let value = match byte {
        b'A'..=b'Z' => byte - b'A',
        b'a'..=b'z' => byte - b'a' + 26,
        b'0'..=b'9' => byte - b'0' + 52,
        b'-' => 62,
        b'_' => 63,
        _ => return Err(Base64UrlError::InvalidCharacter { index, byte }),
    };
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rfc4648_url_vectors_round_trip_without_padding() {
        let vectors = [
            (b"".as_slice(), ""),
            (b"f".as_slice(), "Zg"),
            (b"fo".as_slice(), "Zm8"),
            (b"foo".as_slice(), "Zm9v"),
            (b"foob".as_slice(), "Zm9vYg"),
            (b"fooba".as_slice(), "Zm9vYmE"),
            (b"foobar".as_slice(), "Zm9vYmFy"),
            (&[0xfb, 0xff, 0xff], "-___"),
        ];
        for (raw, encoded) in vectors {
            assert_eq!(encode(raw), encoded);
            assert_eq!(decode(encoded, raw.len()).unwrap(), raw);
        }
    }

    #[test]
    fn rejects_padding_standard_alphabet_and_whitespace() {
        for value in ["Zg==", "Zm8=", "+___", "/___", "Z g", "Zg\n"] {
            assert!(matches!(
                decode(value, 64),
                Err(Base64UrlError::InvalidCharacter { .. })
            ));
        }
    }

    #[test]
    fn rejects_impossible_length_and_noncanonical_trailing_bits() {
        assert_eq!(decode("A", 64), Err(Base64UrlError::InvalidLength));
        assert_eq!(
            decode("Zh", 64),
            Err(Base64UrlError::NonCanonicalTrailingBits)
        );
        assert_eq!(
            decode("Zm9", 64),
            Err(Base64UrlError::NonCanonicalTrailingBits)
        );
    }

    #[test]
    fn enforces_decoded_length_before_allocation() {
        assert_eq!(
            decode("Zm9v", 2),
            Err(Base64UrlError::DecodedLengthExceeded { limit: 2 })
        );
    }
}
