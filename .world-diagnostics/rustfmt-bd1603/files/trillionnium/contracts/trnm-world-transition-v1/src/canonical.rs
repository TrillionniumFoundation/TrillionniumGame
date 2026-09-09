use core::fmt;
use std::fmt::Write as _;

pub const MAX_CANONICAL_DEPTH: usize = 128;

const FORBIDDEN_AUTHORITY_KEYS: &[&str] = &[
    "nakama_session_token",
    "nakama_private_key",
    "match_authority_private_key",
    "canonical_archive_root",
    "chain_finality",
    "chain_app_hash",
    "match_completed_v1",
    "participant_admission_receipt",
    "global_event_cursor",
];

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum CanonicalValue {
    Null,
    Bool(bool),
    Integer(i64),
    String(String),
    Array(Vec<Self>),
    Object(Vec<(String, Self)>),
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum CanonicalError {
    Empty,
    TooLarge { actual: usize, maximum: usize },
    InvalidUtf8,
    InvalidRoot,
    UnexpectedEnd,
    UnexpectedByte { index: usize },
    TrailingData { index: usize },
    DepthExceeded,
    DuplicateOrUnsortedKey { key: String },
    InvalidNumber,
    IntegerOutOfRange,
    InvalidString,
    NonMinimalEscape,
    ForbiddenAuthorityKey { key: String },
    NonCanonicalEncoding,
}

impl fmt::Display for CanonicalError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Empty => formatter.write_str("canonical JSON is empty"),
            Self::TooLarge { actual, maximum } => {
                write!(
                    formatter,
                    "canonical JSON is too large ({actual} > {maximum})"
                )
            }
            Self::InvalidUtf8 => formatter.write_str("canonical JSON is not valid UTF-8"),
            Self::InvalidRoot => formatter.write_str("canonical JSON root must be object or array"),
            Self::UnexpectedEnd => formatter.write_str("canonical JSON ended unexpectedly"),
            Self::UnexpectedByte { index } => {
                write!(formatter, "unexpected canonical JSON byte at {index}")
            }
            Self::TrailingData { index } => {
                write!(formatter, "canonical JSON has trailing data at {index}")
            }
            Self::DepthExceeded => formatter.write_str("canonical JSON depth exceeds 128"),
            Self::DuplicateOrUnsortedKey { key } => {
                write!(formatter, "object key is duplicated or unsorted: {key}")
            }
            Self::InvalidNumber => formatter.write_str("JSON number is not canonical signed-i64"),
            Self::IntegerOutOfRange => formatter.write_str("JSON integer exceeds signed-i64 range"),
            Self::InvalidString => formatter.write_str("JSON string is invalid"),
            Self::NonMinimalEscape => formatter.write_str("JSON string escape is nonminimal"),
            Self::ForbiddenAuthorityKey { key } => {
                write!(formatter, "forbidden authority key: {key}")
            }
            Self::NonCanonicalEncoding => formatter.write_str("JSON bytes are not canonical"),
        }
    }
}

impl std::error::Error for CanonicalError {}

pub fn parse_canonical_bytes(
    raw: &[u8],
    maximum_bytes: usize,
) -> Result<CanonicalValue, CanonicalError> {
    let raw = std::str::from_utf8(raw).map_err(|_| CanonicalError::InvalidUtf8)?;
    parse_canonical(raw, maximum_bytes)
}

pub fn parse_canonical(raw: &str, maximum_bytes: usize) -> Result<CanonicalValue, CanonicalError> {
    if raw.is_empty() {
        return Err(CanonicalError::Empty);
    }
    if raw.len() > maximum_bytes {
        return Err(CanonicalError::TooLarge {
            actual: raw.len(),
            maximum: maximum_bytes,
        });
    }
    let mut parser = Parser {
        text: raw,
        bytes: raw.as_bytes(),
        index: 0,
    };
    let value = parser.parse_value(0)?;
    if parser.index != parser.bytes.len() {
        return Err(CanonicalError::TrailingData {
            index: parser.index,
        });
    }
    if !matches!(value, CanonicalValue::Object(_) | CanonicalValue::Array(_)) {
        return Err(CanonicalError::InvalidRoot);
    }
    reject_forbidden_authority_keys(&value)?;
    if encode_canonical(&value) != raw {
        return Err(CanonicalError::NonCanonicalEncoding);
    }
    Ok(value)
}

pub fn encode_canonical(value: &CanonicalValue) -> String {
    let mut output = String::new();
    append_value(&mut output, value);
    output
}

fn append_value(output: &mut String, value: &CanonicalValue) {
    match value {
        CanonicalValue::Null => output.push_str("null"),
        CanonicalValue::Bool(true) => output.push_str("true"),
        CanonicalValue::Bool(false) => output.push_str("false"),
        CanonicalValue::Integer(value) => {
            let _ = write!(output, "{value}");
        }
        CanonicalValue::String(value) => append_string(output, value),
        CanonicalValue::Array(values) => {
            output.push('[');
            for (index, value) in values.iter().enumerate() {
                if index != 0 {
                    output.push(',');
                }
                append_value(output, value);
            }
            output.push(']');
        }
        CanonicalValue::Object(entries) => {
            output.push('{');
            for (index, (key, value)) in entries.iter().enumerate() {
                if index != 0 {
                    output.push(',');
                }
                append_string(output, key);
                output.push(':');
                append_value(output, value);
            }
            output.push('}');
        }
    }
}

fn append_string(output: &mut String, value: &str) {
    output.push('"');
    for character in value.chars() {
        match character {
            '"' => output.push_str("\\\""),
            '\\' => output.push_str("\\\\"),
            '\u{08}' => output.push_str("\\b"),
            '\u{0c}' => output.push_str("\\f"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            character if character <= '\u{1f}' => {
                let _ = write!(output, "\\u{:04x}", character as u32);
            }
            character => output.push(character),
        }
    }
    output.push('"');
}

fn reject_forbidden_authority_keys(value: &CanonicalValue) -> Result<(), CanonicalError> {
    match value {
        CanonicalValue::Array(values) => {
            for value in values {
                reject_forbidden_authority_keys(value)?;
            }
        }
        CanonicalValue::Object(entries) => {
            for (key, value) in entries {
                let normalized_key = key.to_ascii_lowercase();
                if FORBIDDEN_AUTHORITY_KEYS.contains(&normalized_key.as_str()) {
                    return Err(CanonicalError::ForbiddenAuthorityKey { key: key.clone() });
                }
                reject_forbidden_authority_keys(value)?;
            }
        }
        CanonicalValue::Null
        | CanonicalValue::Bool(_)
        | CanonicalValue::Integer(_)
        | CanonicalValue::String(_) => {}
    }
    Ok(())
}

struct Parser<'a> {
    text: &'a str,
    bytes: &'a [u8],
    index: usize,
}

impl Parser<'_> {
    fn parse_value(&mut self, depth: usize) -> Result<CanonicalValue, CanonicalError> {
        if depth > MAX_CANONICAL_DEPTH {
            return Err(CanonicalError::DepthExceeded);
        }
        let byte = *self
            .bytes
            .get(self.index)
            .ok_or(CanonicalError::UnexpectedEnd)?;
        match byte {
            b'{' => self.parse_object(depth),
            b'[' => self.parse_array(depth),
            b'"' => self.parse_string().map(CanonicalValue::String),
            b't' => {
                self.consume_literal(b"true")?;
                Ok(CanonicalValue::Bool(true))
            }
            b'f' => {
                self.consume_literal(b"false")?;
                Ok(CanonicalValue::Bool(false))
            }
            b'n' => {
                self.consume_literal(b"null")?;
                Ok(CanonicalValue::Null)
            }
            b'-' | b'0'..=b'9' => self.parse_integer().map(CanonicalValue::Integer),
            _ => Err(CanonicalError::UnexpectedByte { index: self.index }),
        }
    }

    fn parse_object(&mut self, depth: usize) -> Result<CanonicalValue, CanonicalError> {
        self.expect_byte(b'{')?;
        let mut entries = Vec::new();
        let mut previous: Option<String> = None;
        if self.peek_byte() == Some(b'}') {
            self.index += 1;
            return Ok(CanonicalValue::Object(entries));
        }
        loop {
            if self.peek_byte() != Some(b'"') {
                return Err(CanonicalError::UnexpectedByte { index: self.index });
            }
            let key = self.parse_string()?;
            if previous
                .as_ref()
                .is_some_and(|prior| key.as_str() <= prior.as_str())
            {
                return Err(CanonicalError::DuplicateOrUnsortedKey { key });
            }
            previous = Some(key.clone());
            self.expect_byte(b':')?;
            let value = self.parse_value(depth + 1)?;
            entries.push((key, value));
            match self.peek_byte() {
                Some(b',') => self.index += 1,
                Some(b'}') => {
                    self.index += 1;
                    break;
                }
                Some(_) => return Err(CanonicalError::UnexpectedByte { index: self.index }),
                None => return Err(CanonicalError::UnexpectedEnd),
            }
        }
        Ok(CanonicalValue::Object(entries))
    }

    fn parse_array(&mut self, depth: usize) -> Result<CanonicalValue, CanonicalError> {
        self.expect_byte(b'[')?;
        let mut values = Vec::new();
        if self.peek_byte() == Some(b']') {
            self.index += 1;
            return Ok(CanonicalValue::Array(values));
        }
        loop {
            values.push(self.parse_value(depth + 1)?);
            match self.peek_byte() {
                Some(b',') => self.index += 1,
                Some(b']') => {
                    self.index += 1;
                    break;
                }
                Some(_) => return Err(CanonicalError::UnexpectedByte { index: self.index }),
                None => return Err(CanonicalError::UnexpectedEnd),
            }
        }
        Ok(CanonicalValue::Array(values))
    }

    fn parse_integer(&mut self) -> Result<i64, CanonicalError> {
        let start = self.index;
        let negative = self.peek_byte() == Some(b'-');
        if negative {
            self.index += 1;
        }
        let first = self.peek_byte().ok_or(CanonicalError::UnexpectedEnd)?;
        match first {
            b'0' => {
                self.index += 1;
                if negative || self.peek_byte().is_some_and(|byte| byte.is_ascii_digit()) {
                    return Err(CanonicalError::InvalidNumber);
                }
            }
            b'1'..=b'9' => {
                self.index += 1;
                while self.peek_byte().is_some_and(|byte| byte.is_ascii_digit()) {
                    self.index += 1;
                }
            }
            _ => return Err(CanonicalError::InvalidNumber),
        }
        let number = &self.text[start..self.index];
        number
            .parse::<i64>()
            .map_err(|_| CanonicalError::IntegerOutOfRange)
    }

    fn parse_string(&mut self) -> Result<String, CanonicalError> {
        self.expect_byte(b'"')?;
        let mut output = String::new();
        loop {
            let byte = *self
                .bytes
                .get(self.index)
                .ok_or(CanonicalError::UnexpectedEnd)?;
            match byte {
                b'"' => {
                    self.index += 1;
                    return Ok(output);
                }
                b'\\' => {
                    self.index += 1;
                    self.parse_escape(&mut output)?;
                }
                0x00..=0x1f => return Err(CanonicalError::InvalidString),
                0x20..=0x7e => {
                    output.push(byte as char);
                    self.index += 1;
                }
                0x7f => return Err(CanonicalError::InvalidString),
                _ => {
                    let character = self.text[self.index..]
                        .chars()
                        .next()
                        .ok_or(CanonicalError::InvalidUtf8)?;
                    if character.is_control() {
                        return Err(CanonicalError::InvalidString);
                    }
                    output.push(character);
                    self.index += character.len_utf8();
                }
            }
        }
    }

    fn parse_escape(&mut self, output: &mut String) -> Result<(), CanonicalError> {
        let escape = *self
            .bytes
            .get(self.index)
            .ok_or(CanonicalError::UnexpectedEnd)?;
        self.index += 1;
        match escape {
            b'"' => output.push('"'),
            b'\\' => output.push('\\'),
            b'b' => output.push('\u{08}'),
            b'f' => output.push('\u{0c}'),
            b'n' => output.push('\n'),
            b'r' => output.push('\r'),
            b't' => output.push('\t'),
            b'u' => {
                let value = self.parse_lower_hex4()?;
                if value > 0x1f || matches!(value, 0x08 | 0x09 | 0x0a | 0x0c | 0x0d) {
                    return Err(CanonicalError::NonMinimalEscape);
                }
                output.push(char::from_u32(u32::from(value)).ok_or(CanonicalError::InvalidString)?);
            }
            _ => return Err(CanonicalError::NonMinimalEscape),
        }
        Ok(())
    }

    fn parse_lower_hex4(&mut self) -> Result<u16, CanonicalError> {
        let mut value = 0_u16;
        for _ in 0..4 {
            let byte = *self
                .bytes
                .get(self.index)
                .ok_or(CanonicalError::UnexpectedEnd)?;
            self.index += 1;
            let digit = match byte {
                b'0'..=b'9' => u16::from(byte - b'0'),
                b'a'..=b'f' => u16::from(byte - b'a' + 10),
                _ => return Err(CanonicalError::NonMinimalEscape),
            };
            value = (value << 4) | digit;
        }
        Ok(value)
    }

    fn consume_literal(&mut self, literal: &[u8]) -> Result<(), CanonicalError> {
        let end = self.index.saturating_add(literal.len());
        if self.bytes.get(self.index..end) != Some(literal) {
            return Err(CanonicalError::UnexpectedByte { index: self.index });
        }
        self.index = end;
        Ok(())
    }

    fn expect_byte(&mut self, expected: u8) -> Result<(), CanonicalError> {
        if self.peek_byte() != Some(expected) {
            return Err(CanonicalError::UnexpectedByte { index: self.index });
        }
        self.index += 1;
        Ok(())
    }

    fn peek_byte(&self) -> Option<u8> {
        self.bytes.get(self.index).copied()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn valid_nested_value_round_trips() {
        let raw = "{\"a\":[-1,0,true,false,null,\"世界\"],\"b\":{\"c\":\"x\\n\"}}";
        let value = parse_canonical(raw, 4096).unwrap();
        assert_eq!(encode_canonical(&value), raw);
    }

    #[test]
    fn malformed_and_noncanonical_values_fail() {
        for raw in [
            "{\"a\":}",
            "{\"b\":1,\"a\":2}",
            "{\"a\":1,\"a\":2}",
            "[01]",
            "[-0]",
            "[1.0]",
            "[1e3]",
            "[9223372036854775808]",
            "{\"a\":\"\\/\"}",
            "{\"a\":\"\\u0061\"}",
            "{ \"a\":1}",
            "{}x",
        ] {
            assert!(
                parse_canonical(raw, 4096).is_err(),
                "unexpected pass: {raw}"
            );
        }
    }

    #[test]
    fn forbidden_authority_key_fails_recursively() {
        let error = parse_canonical("{\"a\":{\"nakama_private_key\":\"x\"}}", 4096).unwrap_err();
        assert!(matches!(
            error,
            CanonicalError::ForbiddenAuthorityKey { .. }
        ));
    }

    #[test]
    fn escaped_authority_key_still_fails_closed() {
        assert!(parse_canonical("{\"nakama_\\u0070rivate_key\":\"x\"}", 4096,).is_err());
    }

    #[test]
    fn case_folded_authority_key_still_fails_closed() {
        for raw in [
            "{\"Nakama_Private_Key\":\"x\"}",
            "{\"MATCH_COMPLETED_V1\":{}}",
            "{\"a\":{\"Chain_App_Hash\":\"x\"}}",
        ] {
            assert!(matches!(
                parse_canonical(raw, 4096),
                Err(CanonicalError::ForbiddenAuthorityKey { .. })
            ));
        }
    }

    #[test]
    fn invalid_utf8_and_control_characters_fail() {
        assert!(matches!(
            parse_canonical_bytes(&[0xff], 16),
            Err(CanonicalError::InvalidUtf8)
        ));
        assert!(parse_canonical("{\"a\":\"\u{7f}\"}", 4096).is_err());
        assert!(parse_canonical("{\"a\":\"\u{85}\"}", 4096).is_err());
    }

    #[test]
    fn excessive_depth_fails() {
        let mut raw = String::new();
        for _ in 0..=MAX_CANONICAL_DEPTH {
            raw.push('[');
        }
        raw.push_str("{}");
        for _ in 0..=MAX_CANONICAL_DEPTH {
            raw.push(']');
        }
        assert!(matches!(
            parse_canonical(&raw, raw.len()),
            Err(CanonicalError::DepthExceeded)
        ));
    }
}
