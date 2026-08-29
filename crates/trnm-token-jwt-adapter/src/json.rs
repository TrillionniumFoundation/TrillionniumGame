use core::fmt;
use std::collections::BTreeMap;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum JsonValue {
    Null,
    Bool(bool),
    Integer(i64),
    Unsigned(u64),
    String(String),
    Array(Vec<JsonValue>),
    Object(BTreeMap<String, JsonValue>),
}

impl JsonValue {
    pub fn as_object(&self) -> Option<&BTreeMap<String, JsonValue>> {
        match self {
            Self::Object(value) => Some(value),
            _ => None,
        }
    }

    pub fn as_array(&self) -> Option<&[JsonValue]> {
        match self {
            Self::Array(value) => Some(value),
            _ => None,
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            Self::String(value) => Some(value),
            _ => None,
        }
    }

    pub fn as_u64(&self) -> Option<u64> {
        match self {
            Self::Unsigned(value) => Some(*value),
            Self::Integer(value) if *value >= 0 => Some(*value as u64),
            _ => None,
        }
    }

    pub fn as_i64(&self) -> Option<i64> {
        match self {
            Self::Integer(value) => Some(*value),
            Self::Unsigned(value) => i64::try_from(*value).ok(),
            _ => None,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct JsonLimits {
    pub max_depth: usize,
    pub max_nodes: usize,
    pub max_string_bytes: usize,
    pub max_collection_len: usize,
}

impl Default for JsonLimits {
    fn default() -> Self {
        Self {
            max_depth: 16,
            max_nodes: 2_048,
            max_string_bytes: 16 * 1_024,
            max_collection_len: 256,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct JsonError {
    pub offset: usize,
    pub kind: JsonErrorKind,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum JsonErrorKind {
    InvalidUtf8,
    UnexpectedEnd,
    UnexpectedByte(u8),
    InvalidLiteral,
    InvalidEscape,
    InvalidUnicodeEscape,
    LoneSurrogate,
    DuplicateKey(String),
    LeadingZero,
    FloatForbidden,
    NumberOutOfRange,
    DepthExceeded { limit: usize },
    NodeCountExceeded { limit: usize },
    StringLengthExceeded { limit: usize },
    CollectionLengthExceeded { limit: usize },
    TrailingData,
}

impl fmt::Display for JsonError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "JSON error at byte {}: ", self.offset)?;
        match &self.kind {
            JsonErrorKind::InvalidUtf8 => formatter.write_str("input is not UTF-8"),
            JsonErrorKind::UnexpectedEnd => formatter.write_str("unexpected end of input"),
            JsonErrorKind::UnexpectedByte(byte) => {
                write!(formatter, "unexpected byte 0x{byte:02x}")
            }
            JsonErrorKind::InvalidLiteral => formatter.write_str("invalid JSON literal"),
            JsonErrorKind::InvalidEscape => formatter.write_str("invalid JSON string escape"),
            JsonErrorKind::InvalidUnicodeEscape => {
                formatter.write_str("invalid JSON unicode escape")
            }
            JsonErrorKind::LoneSurrogate => {
                formatter.write_str("lone or mismatched UTF-16 surrogate")
            }
            JsonErrorKind::DuplicateKey(key) => write!(formatter, "duplicate object key {key:?}"),
            JsonErrorKind::LeadingZero => formatter.write_str("number has a leading zero"),
            JsonErrorKind::FloatForbidden => {
                formatter.write_str("floating-point JSON numbers are forbidden")
            }
            JsonErrorKind::NumberOutOfRange => {
                formatter.write_str("integer is outside signed/unsigned 64-bit range")
            }
            JsonErrorKind::DepthExceeded { limit } => {
                write!(formatter, "maximum JSON depth {limit} exceeded")
            }
            JsonErrorKind::NodeCountExceeded { limit } => {
                write!(formatter, "maximum JSON node count {limit} exceeded")
            }
            JsonErrorKind::StringLengthExceeded { limit } => {
                write!(formatter, "maximum JSON string length {limit} exceeded")
            }
            JsonErrorKind::CollectionLengthExceeded { limit } => {
                write!(formatter, "maximum JSON collection length {limit} exceeded")
            }
            JsonErrorKind::TrailingData => formatter.write_str("trailing data after JSON value"),
        }
    }
}

impl std::error::Error for JsonError {}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum JsonEncodeError {
    OutputLengthExceeded { limit: usize },
}

impl fmt::Display for JsonEncodeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::OutputLengthExceeded { limit } => {
                write!(formatter, "canonical JSON output exceeds {limit} bytes")
            }
        }
    }
}

impl std::error::Error for JsonEncodeError {}

pub fn parse(input: &[u8], limits: JsonLimits) -> Result<JsonValue, JsonError> {
    if std::str::from_utf8(input).is_err() {
        return Err(JsonError {
            offset: 0,
            kind: JsonErrorKind::InvalidUtf8,
        });
    }
    let mut parser = Parser {
        input,
        offset: 0,
        limits,
        nodes: 0,
    };
    parser.skip_whitespace();
    let value = parser.parse_value(0)?;
    parser.skip_whitespace();
    if parser.offset != input.len() {
        return Err(parser.error(JsonErrorKind::TrailingData));
    }
    Ok(value)
}

pub fn to_canonical_bytes(
    value: &JsonValue,
    max_output_bytes: usize,
) -> Result<Vec<u8>, JsonEncodeError> {
    let mut output = Vec::new();
    encode_value(value, &mut output, max_output_bytes)?;
    Ok(output)
}

struct Parser<'a> {
    input: &'a [u8],
    offset: usize,
    limits: JsonLimits,
    nodes: usize,
}

impl Parser<'_> {
    fn error(&self, kind: JsonErrorKind) -> JsonError {
        JsonError {
            offset: self.offset,
            kind,
        }
    }

    fn parse_value(&mut self, depth: usize) -> Result<JsonValue, JsonError> {
        if depth > self.limits.max_depth {
            return Err(self.error(JsonErrorKind::DepthExceeded {
                limit: self.limits.max_depth,
            }));
        }
        self.nodes = self.nodes.saturating_add(1);
        if self.nodes > self.limits.max_nodes {
            return Err(self.error(JsonErrorKind::NodeCountExceeded {
                limit: self.limits.max_nodes,
            }));
        }
        self.skip_whitespace();
        let byte = self
            .input
            .get(self.offset)
            .copied()
            .ok_or_else(|| self.error(JsonErrorKind::UnexpectedEnd))?;
        match byte {
            b'n' => {
                self.consume_literal(b"null")?;
                Ok(JsonValue::Null)
            }
            b't' => {
                self.consume_literal(b"true")?;
                Ok(JsonValue::Bool(true))
            }
            b'f' => {
                self.consume_literal(b"false")?;
                Ok(JsonValue::Bool(false))
            }
            b'"' => self.parse_string().map(JsonValue::String),
            b'[' => self.parse_array(depth),
            b'{' => self.parse_object(depth),
            b'-' | b'0'..=b'9' => self.parse_number(),
            _ => Err(self.error(JsonErrorKind::UnexpectedByte(byte))),
        }
    }

    fn consume_literal(&mut self, expected: &[u8]) -> Result<(), JsonError> {
        let end = self.offset.saturating_add(expected.len());
        if self.input.get(self.offset..end) != Some(expected) {
            return Err(self.error(JsonErrorKind::InvalidLiteral));
        }
        self.offset = end;
        Ok(())
    }

    fn parse_array(&mut self, depth: usize) -> Result<JsonValue, JsonError> {
        self.offset += 1;
        self.skip_whitespace();
        let mut values = Vec::new();
        if self.consume_if(b']') {
            return Ok(JsonValue::Array(values));
        }
        loop {
            if values.len() >= self.limits.max_collection_len {
                return Err(self.error(JsonErrorKind::CollectionLengthExceeded {
                    limit: self.limits.max_collection_len,
                }));
            }
            values.push(self.parse_value(depth + 1)?);
            self.skip_whitespace();
            if self.consume_if(b']') {
                break;
            }
            self.expect(b',')?;
            self.skip_whitespace();
        }
        Ok(JsonValue::Array(values))
    }

    fn parse_object(&mut self, depth: usize) -> Result<JsonValue, JsonError> {
        self.offset += 1;
        self.skip_whitespace();
        let mut values = BTreeMap::new();
        if self.consume_if(b'}') {
            return Ok(JsonValue::Object(values));
        }
        loop {
            if values.len() >= self.limits.max_collection_len {
                return Err(self.error(JsonErrorKind::CollectionLengthExceeded {
                    limit: self.limits.max_collection_len,
                }));
            }
            if self.input.get(self.offset) != Some(&b'"') {
                let byte = self.input.get(self.offset).copied().unwrap_or(0);
                return Err(self.error(if self.offset == self.input.len() {
                    JsonErrorKind::UnexpectedEnd
                } else {
                    JsonErrorKind::UnexpectedByte(byte)
                }));
            }
            let key_offset = self.offset;
            let key = self.parse_string()?;
            self.skip_whitespace();
            self.expect(b':')?;
            self.skip_whitespace();
            let value = self.parse_value(depth + 1)?;
            if values.insert(key.clone(), value).is_some() {
                return Err(JsonError {
                    offset: key_offset,
                    kind: JsonErrorKind::DuplicateKey(key),
                });
            }
            self.skip_whitespace();
            if self.consume_if(b'}') {
                break;
            }
            self.expect(b',')?;
            self.skip_whitespace();
        }
        Ok(JsonValue::Object(values))
    }

    fn parse_number(&mut self) -> Result<JsonValue, JsonError> {
        let start = self.offset;
        let negative = self.consume_if(b'-');
        let first = self
            .input
            .get(self.offset)
            .copied()
            .ok_or_else(|| self.error(JsonErrorKind::UnexpectedEnd))?;
        match first {
            b'0' => {
                self.offset += 1;
                if matches!(self.input.get(self.offset), Some(b'0'..=b'9')) {
                    return Err(self.error(JsonErrorKind::LeadingZero));
                }
            }
            b'1'..=b'9' => {
                self.offset += 1;
                while matches!(self.input.get(self.offset), Some(b'0'..=b'9')) {
                    self.offset += 1;
                }
            }
            _ => return Err(self.error(JsonErrorKind::UnexpectedByte(first))),
        }
        if matches!(self.input.get(self.offset), Some(b'.' | b'e' | b'E')) {
            return Err(self.error(JsonErrorKind::FloatForbidden));
        }
        let raw = std::str::from_utf8(&self.input[start..self.offset])
            .expect("JSON input was validated as UTF-8");
        if negative {
            raw.parse::<i64>()
                .map(JsonValue::Integer)
                .map_err(|_| self.error(JsonErrorKind::NumberOutOfRange))
        } else {
            raw.parse::<u64>()
                .map(JsonValue::Unsigned)
                .map_err(|_| self.error(JsonErrorKind::NumberOutOfRange))
        }
    }

    fn parse_string(&mut self) -> Result<String, JsonError> {
        self.expect(b'"')?;
        let mut output = String::new();
        loop {
            let byte = self
                .input
                .get(self.offset)
                .copied()
                .ok_or_else(|| self.error(JsonErrorKind::UnexpectedEnd))?;
            match byte {
                b'"' => {
                    self.offset += 1;
                    return Ok(output);
                }
                b'\\' => {
                    self.offset += 1;
                    let escaped = self
                        .input
                        .get(self.offset)
                        .copied()
                        .ok_or_else(|| self.error(JsonErrorKind::UnexpectedEnd))?;
                    self.offset += 1;
                    match escaped {
                        b'"' => output.push('"'),
                        b'\\' => output.push('\\'),
                        b'/' => output.push('/'),
                        b'b' => output.push('\u{0008}'),
                        b'f' => output.push('\u{000c}'),
                        b'n' => output.push('\n'),
                        b'r' => output.push('\r'),
                        b't' => output.push('\t'),
                        b'u' => output.push(self.parse_unicode_escape()?),
                        _ => return Err(self.error(JsonErrorKind::InvalidEscape)),
                    }
                }
                0x00..=0x1f => {
                    return Err(self.error(JsonErrorKind::UnexpectedByte(byte)));
                }
                0x20..=0x7f => {
                    output.push(char::from(byte));
                    self.offset += 1;
                }
                _ => {
                    let remainder = std::str::from_utf8(&self.input[self.offset..])
                        .expect("JSON input was validated as UTF-8");
                    let character = remainder
                        .chars()
                        .next()
                        .ok_or_else(|| self.error(JsonErrorKind::UnexpectedEnd))?;
                    output.push(character);
                    self.offset += character.len_utf8();
                }
            }
            if output.len() > self.limits.max_string_bytes {
                return Err(self.error(JsonErrorKind::StringLengthExceeded {
                    limit: self.limits.max_string_bytes,
                }));
            }
        }
    }

    fn parse_unicode_escape(&mut self) -> Result<char, JsonError> {
        let first = self.parse_hex_u16()?;
        let scalar = match first {
            0xd800..=0xdbff => {
                if self.input.get(self.offset..self.offset + 2) != Some(b"\\u") {
                    return Err(self.error(JsonErrorKind::LoneSurrogate));
                }
                self.offset += 2;
                let second = self.parse_hex_u16()?;
                if !(0xdc00..=0xdfff).contains(&second) {
                    return Err(self.error(JsonErrorKind::LoneSurrogate));
                }
                0x1_0000
                    + ((u32::from(first) - 0xd800) << 10)
                    + (u32::from(second) - 0xdc00)
            }
            0xdc00..=0xdfff => return Err(self.error(JsonErrorKind::LoneSurrogate)),
            _ => u32::from(first),
        };
        char::from_u32(scalar).ok_or_else(|| self.error(JsonErrorKind::InvalidUnicodeEscape))
    }

    fn parse_hex_u16(&mut self) -> Result<u16, JsonError> {
        let end = self.offset.saturating_add(4);
        let digits = self
            .input
            .get(self.offset..end)
            .ok_or_else(|| self.error(JsonErrorKind::UnexpectedEnd))?;
        let mut value = 0u16;
        for byte in digits {
            let digit = match byte {
                b'0'..=b'9' => u16::from(*byte - b'0'),
                b'a'..=b'f' => u16::from(*byte - b'a' + 10),
                b'A'..=b'F' => u16::from(*byte - b'A' + 10),
                _ => return Err(self.error(JsonErrorKind::InvalidUnicodeEscape)),
            };
            value = (value << 4) | digit;
        }
        self.offset = end;
        Ok(value)
    }

    fn expect(&mut self, expected: u8) -> Result<(), JsonError> {
        match self.input.get(self.offset).copied() {
            Some(value) if value == expected => {
                self.offset += 1;
                Ok(())
            }
            Some(value) => Err(self.error(JsonErrorKind::UnexpectedByte(value))),
            None => Err(self.error(JsonErrorKind::UnexpectedEnd)),
        }
    }

    fn consume_if(&mut self, expected: u8) -> bool {
        if self.input.get(self.offset) == Some(&expected) {
            self.offset += 1;
            true
        } else {
            false
        }
    }

    fn skip_whitespace(&mut self) {
        while matches!(self.input.get(self.offset), Some(b' ' | b'\n' | b'\r' | b'\t')) {
            self.offset += 1;
        }
    }
}

fn encode_value(
    value: &JsonValue,
    output: &mut Vec<u8>,
    limit: usize,
) -> Result<(), JsonEncodeError> {
    match value {
        JsonValue::Null => push_bytes(output, b"null", limit)?,
        JsonValue::Bool(true) => push_bytes(output, b"true", limit)?,
        JsonValue::Bool(false) => push_bytes(output, b"false", limit)?,
        JsonValue::Integer(value) => push_bytes(output, value.to_string().as_bytes(), limit)?,
        JsonValue::Unsigned(value) => push_bytes(output, value.to_string().as_bytes(), limit)?,
        JsonValue::String(value) => encode_string(value, output, limit)?,
        JsonValue::Array(values) => {
            push_byte(output, b'[', limit)?;
            for (index, value) in values.iter().enumerate() {
                if index != 0 {
                    push_byte(output, b',', limit)?;
                }
                encode_value(value, output, limit)?;
            }
            push_byte(output, b']', limit)?;
        }
        JsonValue::Object(values) => {
            push_byte(output, b'{', limit)?;
            for (index, (key, value)) in values.iter().enumerate() {
                if index != 0 {
                    push_byte(output, b',', limit)?;
                }
                encode_string(key, output, limit)?;
                push_byte(output, b':', limit)?;
                encode_value(value, output, limit)?;
            }
            push_byte(output, b'}', limit)?;
        }
    }
    Ok(())
}

fn encode_string(
    value: &str,
    output: &mut Vec<u8>,
    limit: usize,
) -> Result<(), JsonEncodeError> {
    push_byte(output, b'"', limit)?;
    for character in value.chars() {
        match character {
            '"' => push_bytes(output, b"\\\"", limit)?,
            '\\' => push_bytes(output, b"\\\\", limit)?,
            '\u{0008}' => push_bytes(output, b"\\b", limit)?,
            '\u{000c}' => push_bytes(output, b"\\f", limit)?,
            '\n' => push_bytes(output, b"\\n", limit)?,
            '\r' => push_bytes(output, b"\\r", limit)?,
            '\t' => push_bytes(output, b"\\t", limit)?,
            '\u{0000}'..='\u{001f}' => {
                let value = character as u32;
                let encoded = [
                    b'\\',
                    b'u',
                    b'0',
                    b'0',
                    hex_digit(((value >> 4) & 0x0f) as u8),
                    hex_digit((value & 0x0f) as u8),
                ];
                push_bytes(output, &encoded, limit)?;
            }
            _ => {
                let mut encoded = [0u8; 4];
                push_bytes(output, character.encode_utf8(&mut encoded).as_bytes(), limit)?;
            }
        }
    }
    push_byte(output, b'"', limit)
}

fn hex_digit(value: u8) -> u8 {
    match value {
        0..=9 => b'0' + value,
        10..=15 => b'a' + value - 10,
        _ => unreachable!(),
    }
}

fn push_byte(output: &mut Vec<u8>, byte: u8, limit: usize) -> Result<(), JsonEncodeError> {
    if output.len() >= limit {
        return Err(JsonEncodeError::OutputLengthExceeded { limit });
    }
    output.push(byte);
    Ok(())
}

fn push_bytes(
    output: &mut Vec<u8>,
    bytes: &[u8],
    limit: usize,
) -> Result<(), JsonEncodeError> {
    if output.len().saturating_add(bytes.len()) > limit {
        return Err(JsonEncodeError::OutputLengthExceeded { limit });
    }
    output.extend_from_slice(bytes);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn object(entries: impl IntoIterator<Item = (&'static str, JsonValue)>) -> JsonValue {
        JsonValue::Object(
            entries
                .into_iter()
                .map(|(key, value)| (key.to_owned(), value))
                .collect(),
        )
    }

    #[test]
    fn canonical_encoder_sorts_object_keys_and_preserves_integer_types() {
        let value = object([
            ("z", JsonValue::Integer(-1)),
            ("a", JsonValue::Unsigned(u64::MAX)),
            ("m", JsonValue::Bool(true)),
        ]);
        assert_eq!(
            to_canonical_bytes(&value, 1_024).unwrap(),
            br#"{"a":18446744073709551615,"m":true,"z":-1}"#
        );
    }

    #[test]
    fn parses_escapes_and_surrogate_pairs() {
        let value = parse(
            br#"{"emoji":"\ud83d\ude80","line":"a\nb","slash":"\/"}"#,
            JsonLimits::default(),
        )
        .unwrap();
        let object = value.as_object().unwrap();
        assert_eq!(object["emoji"].as_str(), Some("🚀"));
        assert_eq!(object["line"].as_str(), Some("a\nb"));
        assert_eq!(object["slash"].as_str(), Some("/"));
    }

    #[test]
    fn rejects_duplicate_keys_at_every_depth() {
        for value in [
            br#"{"a":1,"a":2}"#.as_slice(),
            br#"{"outer":{"a":1,"a":2}}"#.as_slice(),
        ] {
            assert!(matches!(
                parse(value, JsonLimits::default()),
                Err(JsonError {
                    kind: JsonErrorKind::DuplicateKey(_),
                    ..
                })
            ));
        }
    }

    #[test]
    fn rejects_floats_exponents_leading_zero_and_overflow() {
        for value in ["1.0", "1e3", "01", "18446744073709551616", "-9223372036854775809"] {
            assert!(parse(value.as_bytes(), JsonLimits::default()).is_err(), "{value}");
        }
    }

    #[test]
    fn rejects_lone_or_mismatched_surrogates() {
        for value in [r#""\ud800""#, r#""\udc00""#, r#""\ud800\u0041""#] {
            assert!(matches!(
                parse(value.as_bytes(), JsonLimits::default()),
                Err(JsonError {
                    kind: JsonErrorKind::LoneSurrogate,
                    ..
                })
            ));
        }
    }

    #[test]
    fn rejects_invalid_utf8_and_trailing_data() {
        assert!(matches!(
            parse(&[b'"', 0xff, b'"'], JsonLimits::default()),
            Err(JsonError {
                kind: JsonErrorKind::InvalidUtf8,
                ..
            })
        ));
        assert!(matches!(
            parse(b"null false", JsonLimits::default()),
            Err(JsonError {
                kind: JsonErrorKind::TrailingData,
                ..
            })
        ));
    }

    #[test]
    fn enforces_depth_node_string_collection_and_output_limits() {
        let limits = JsonLimits {
            max_depth: 1,
            max_nodes: 3,
            max_string_bytes: 2,
            max_collection_len: 2,
        };
        assert!(matches!(
            parse(br#"[[0]]"#, limits),
            Err(JsonError {
                kind: JsonErrorKind::DepthExceeded { .. },
                ..
            })
        ));
        assert!(matches!(
            parse(br#"[0,1,2]"#, limits),
            Err(JsonError {
                kind: JsonErrorKind::CollectionLengthExceeded { .. },
                ..
            })
        ));
        assert!(matches!(
            parse(br#""abc""#, limits),
            Err(JsonError {
                kind: JsonErrorKind::StringLengthExceeded { .. },
                ..
            })
        ));
        assert_eq!(
            to_canonical_bytes(&JsonValue::String("abc".into()), 4),
            Err(JsonEncodeError::OutputLengthExceeded { limit: 4 })
        );
    }

    #[test]
    fn canonical_round_trip_is_stable() {
        let source = br#" { "b" : [true,null,-0], "a":"\u00e9" } "#;
        let value = parse(source, JsonLimits::default()).unwrap();
        let first = to_canonical_bytes(&value, 1_024).unwrap();
        assert_eq!(first, b"{\"a\":\"\xc3\xa9\",\"b\":[true,null,0]}");
        let reparsed = parse(&first, JsonLimits::default()).unwrap();
        let second = to_canonical_bytes(&reparsed, 1_024).unwrap();
        assert_eq!(first, second);
    }
}
