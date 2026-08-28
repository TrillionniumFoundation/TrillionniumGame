#![forbid(unsafe_code)]

use trnm_contracts::{DomainError, ProtocolVersion, RetryClass, StableCode};

const MAGIC: &[u8; 8] = b"TRNMCAN1";
const MAX_DOMAIN_BYTES: usize = 64;
const TAG_NULL: u8 = 0;
const TAG_FALSE: u8 = 1;
const TAG_TRUE: u8 = 2;
const TAG_I64: u8 = 3;
const TAG_STRING: u8 = 4;
const TAG_BYTES: u8 = 5;
const TAG_ARRAY: u8 = 6;
const TAG_OBJECT: u8 = 7;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct CanonicalLimits {
    pub max_depth: usize,
    pub max_nodes: usize,
    pub max_output_bytes: usize,
    pub max_string_bytes: usize,
    pub max_collection_items: usize,
}

impl Default for CanonicalLimits {
    fn default() -> Self {
        Self {
            max_depth: 32,
            max_nodes: 4_096,
            max_output_bytes: 1024 * 1024,
            max_string_bytes: 64 * 1024,
            max_collection_items: 4_096,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CanonicalObject {
    entries: Vec<(String, CanonicalValue)>,
}

impl CanonicalObject {
    pub fn new(mut entries: Vec<(String, CanonicalValue)>) -> Result<Self, DomainError> {
        entries.sort_by(|left, right| left.0.as_bytes().cmp(right.0.as_bytes()));
        if entries
            .windows(2)
            .any(|pair| pair[0].0.as_bytes() == pair[1].0.as_bytes())
        {
            return Err(invalid("duplicate_canonical_object_key"));
        }
        Ok(Self { entries })
    }

    #[must_use]
    pub fn entries(&self) -> &[(String, CanonicalValue)] {
        &self.entries
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CanonicalValue {
    Null,
    Bool(bool),
    I64(i64),
    String(String),
    Bytes(Vec<u8>),
    Array(Vec<CanonicalValue>),
    Object(CanonicalObject),
}

impl CanonicalValue {
    pub fn object(entries: Vec<(String, Self)>) -> Result<Self, DomainError> {
        CanonicalObject::new(entries).map(Self::Object)
    }
}

#[derive(Clone, Copy, Debug)]
pub struct CanonicalFrame<'a> {
    domain: &'a str,
    version: ProtocolVersion,
    value: &'a CanonicalValue,
}

impl<'a> CanonicalFrame<'a> {
    pub fn new(
        domain: &'a str,
        version: ProtocolVersion,
        value: &'a CanonicalValue,
    ) -> Result<Self, DomainError> {
        validate_domain(domain)?;
        if version.major() == 0 {
            return Err(invalid("canonical_protocol_major_zero"));
        }
        Ok(Self {
            domain,
            version,
            value,
        })
    }

    #[must_use]
    pub const fn domain(self) -> &'a str {
        self.domain
    }

    #[must_use]
    pub const fn version(self) -> ProtocolVersion {
        self.version
    }

    #[must_use]
    pub const fn value(self) -> &'a CanonicalValue {
        self.value
    }
}

pub fn encode_canonical(
    frame: CanonicalFrame<'_>,
    limits: CanonicalLimits,
) -> Result<Vec<u8>, DomainError> {
    validate_limits(limits)?;
    let mut encoder = Encoder::new(limits);
    encoder.extend(MAGIC)?;
    encoder.write_u16(frame.domain.len())?;
    encoder.extend(frame.domain.as_bytes())?;
    encoder.write_raw_u16(frame.version.major())?;
    encoder.write_raw_u16(frame.version.minor())?;
    encoder.write_value(frame.value, 0)?;
    Ok(encoder.finish())
}

#[derive(Debug)]
struct Encoder {
    limits: CanonicalLimits,
    nodes: usize,
    output: Vec<u8>,
}

impl Encoder {
    fn new(limits: CanonicalLimits) -> Self {
        Self {
            limits,
            nodes: 0,
            output: Vec::new(),
        }
    }

    fn finish(self) -> Vec<u8> {
        self.output
    }

    fn write_value(&mut self, value: &CanonicalValue, depth: usize) -> Result<(), DomainError> {
        if depth > self.limits.max_depth {
            return Err(exhausted("canonical_depth_limit_exceeded"));
        }
        self.nodes = self.nodes.checked_add(1).ok_or_else(counter_overflow)?;
        if self.nodes > self.limits.max_nodes {
            return Err(exhausted("canonical_node_limit_exceeded"));
        }

        match value {
            CanonicalValue::Null => self.push(TAG_NULL),
            CanonicalValue::Bool(false) => self.push(TAG_FALSE),
            CanonicalValue::Bool(true) => self.push(TAG_TRUE),
            CanonicalValue::I64(number) => {
                self.push(TAG_I64)?;
                self.extend(&number.to_be_bytes())
            }
            CanonicalValue::String(text) => {
                validate_string(text, self.limits)?;
                self.push(TAG_STRING)?;
                self.write_u32(text.len())?;
                self.extend(text.as_bytes())
            }
            CanonicalValue::Bytes(bytes) => {
                if bytes.len() > self.limits.max_string_bytes {
                    return Err(exhausted("canonical_bytes_limit_exceeded"));
                }
                self.push(TAG_BYTES)?;
                self.write_u32(bytes.len())?;
                self.extend(bytes)
            }
            CanonicalValue::Array(values) => {
                validate_collection_len(values.len(), self.limits)?;
                self.push(TAG_ARRAY)?;
                self.write_u32(values.len())?;
                let child_depth = depth.checked_add(1).ok_or_else(counter_overflow)?;
                for child in values {
                    self.write_value(child, child_depth)?;
                }
                Ok(())
            }
            CanonicalValue::Object(object) => {
                validate_collection_len(object.entries.len(), self.limits)?;
                self.push(TAG_OBJECT)?;
                self.write_u32(object.entries.len())?;
                let child_depth = depth.checked_add(1).ok_or_else(counter_overflow)?;
                for (key, child) in &object.entries {
                    validate_key(key, self.limits)?;
                    self.write_u32(key.len())?;
                    self.extend(key.as_bytes())?;
                    self.write_value(child, child_depth)?;
                }
                Ok(())
            }
        }
    }

    fn push(&mut self, value: u8) -> Result<(), DomainError> {
        self.ensure_capacity(1)?;
        self.output.push(value);
        Ok(())
    }

    fn extend(&mut self, value: &[u8]) -> Result<(), DomainError> {
        self.ensure_capacity(value.len())?;
        self.output.extend_from_slice(value);
        Ok(())
    }

    fn write_u16(&mut self, value: usize) -> Result<(), DomainError> {
        let value = u16::try_from(value).map_err(|_| exhausted("canonical_u16_length_exceeded"))?;
        self.write_raw_u16(value)
    }

    fn write_raw_u16(&mut self, value: u16) -> Result<(), DomainError> {
        self.extend(&value.to_be_bytes())
    }

    fn write_u32(&mut self, value: usize) -> Result<(), DomainError> {
        let value = u32::try_from(value).map_err(|_| exhausted("canonical_u32_length_exceeded"))?;
        self.extend(&value.to_be_bytes())
    }

    fn ensure_capacity(&self, additional: usize) -> Result<(), DomainError> {
        let next = self
            .output
            .len()
            .checked_add(additional)
            .ok_or_else(counter_overflow)?;
        if next > self.limits.max_output_bytes {
            return Err(exhausted("canonical_output_limit_exceeded"));
        }
        Ok(())
    }
}

fn validate_domain(domain: &str) -> Result<(), DomainError> {
    if domain.is_empty() || domain.len() > MAX_DOMAIN_BYTES {
        return Err(invalid("invalid_canonical_domain_length"));
    }
    let bytes = domain.as_bytes();
    if !bytes[0].is_ascii_alphanumeric()
        || !bytes[bytes.len() - 1].is_ascii_alphanumeric()
        || bytes.windows(2).any(|pair| pair == b"..")
        || !bytes.iter().all(|byte| {
            byte.is_ascii_lowercase()
                || byte.is_ascii_digit()
                || matches!(byte, b'.' | b'_' | b'-')
        })
    {
        return Err(invalid("invalid_canonical_domain"));
    }
    Ok(())
}

fn validate_limits(limits: CanonicalLimits) -> Result<(), DomainError> {
    if limits.max_nodes == 0
        || limits.max_output_bytes < MAGIC.len() + 8
        || limits.max_string_bytes == 0
        || limits.max_collection_items == 0
    {
        return Err(invalid("invalid_canonical_limits"));
    }
    Ok(())
}

fn validate_string(text: &str, limits: CanonicalLimits) -> Result<(), DomainError> {
    if text.len() > limits.max_string_bytes {
        return Err(exhausted("canonical_string_limit_exceeded"));
    }
    Ok(())
}

fn validate_key(key: &str, limits: CanonicalLimits) -> Result<(), DomainError> {
    if key.is_empty() || key.len() > limits.max_string_bytes {
        return Err(invalid("invalid_canonical_object_key"));
    }
    Ok(())
}

fn validate_collection_len(
    len: usize,
    limits: CanonicalLimits,
) -> Result<(), DomainError> {
    if len > limits.max_collection_items {
        Err(exhausted("canonical_collection_limit_exceeded"))
    } else {
        Ok(())
    }
}

const fn invalid(reason: &'static str) -> DomainError {
    DomainError::new(StableCode::InvalidArgument, reason, RetryClass::Never)
}

const fn exhausted(reason: &'static str) -> DomainError {
    DomainError::new(StableCode::ResourceExhausted, reason, RetryClass::Never)
}

const fn counter_overflow() -> DomainError {
    DomainError::new(
        StableCode::OutOfRange,
        "canonical_counter_overflow",
        RetryClass::Never,
    )
}

#[cfg(test)]
mod tests {
    use std::fmt::Write;

    use super::*;

    fn version() -> ProtocolVersion {
        ProtocolVersion::new(1, 0)
    }

    fn hex(bytes: &[u8]) -> String {
        let mut output = String::with_capacity(bytes.len() * 2);
        for byte in bytes {
            write!(&mut output, "{byte:02x}").unwrap();
        }
        output
    }

    #[test]
    fn simple_object_matches_locked_vector() {
        let value = CanonicalValue::object(vec![
            ("b".to_owned(), CanonicalValue::Bool(true)),
            ("a".to_owned(), CanonicalValue::I64(1)),
        ])
        .unwrap();
        let frame = CanonicalFrame::new("match.command", version(), &value).unwrap();
        let encoded = encode_canonical(frame, CanonicalLimits::default()).unwrap();
        assert_eq!(
            hex(&encoded),
            "54524e4d43414e31000d6d617463682e636f6d6d616e640001000007000000020000000161030000000000000001000000016202"
        );
    }

    #[test]
    fn object_input_order_does_not_change_bytes() {
        let left = CanonicalValue::object(vec![
            ("z".to_owned(), CanonicalValue::Null),
            ("a".to_owned(), CanonicalValue::Bool(false)),
        ])
        .unwrap();
        let right = CanonicalValue::object(vec![
            ("a".to_owned(), CanonicalValue::Bool(false)),
            ("z".to_owned(), CanonicalValue::Null),
        ])
        .unwrap();
        let left = encode_canonical(
            CanonicalFrame::new("state.snapshot", version(), &left).unwrap(),
            CanonicalLimits::default(),
        )
        .unwrap();
        let right = encode_canonical(
            CanonicalFrame::new("state.snapshot", version(), &right).unwrap(),
            CanonicalLimits::default(),
        )
        .unwrap();
        assert_eq!(left, right);
    }

    #[test]
    fn duplicate_object_keys_fail_closed() {
        assert_eq!(
            CanonicalValue::object(vec![
                ("same".to_owned(), CanonicalValue::Null),
                ("same".to_owned(), CanonicalValue::Bool(true)),
            ])
            .unwrap_err()
            .reason(),
            "duplicate_canonical_object_key"
        );
    }

    #[test]
    fn domains_are_explicitly_separated() {
        let value = CanonicalValue::I64(7);
        let left = encode_canonical(
            CanonicalFrame::new("command.intent", version(), &value).unwrap(),
            CanonicalLimits::default(),
        )
        .unwrap();
        let right = encode_canonical(
            CanonicalFrame::new("event.payload", version(), &value).unwrap(),
            CanonicalLimits::default(),
        )
        .unwrap();
        assert_ne!(left, right);
    }

    #[test]
    fn signed_integer_boundaries_are_exact() {
        let value = CanonicalValue::Array(vec![
            CanonicalValue::I64(i64::MIN),
            CanonicalValue::I64(i64::MAX),
        ]);
        let encoded = encode_canonical(
            CanonicalFrame::new("integer.boundary", version(), &value).unwrap(),
            CanonicalLimits::default(),
        )
        .unwrap();
        assert!(encoded.windows(8).any(|window| window == i64::MIN.to_be_bytes()));
        assert!(encoded.windows(8).any(|window| window == i64::MAX.to_be_bytes()));
    }

    #[test]
    fn unicode_strings_remain_exact_utf8() {
        let value = CanonicalValue::String("世界🌍".to_owned());
        let encoded = encode_canonical(
            CanonicalFrame::new("unicode.value", version(), &value).unwrap(),
            CanonicalLimits::default(),
        )
        .unwrap();
        assert!(encoded.ends_with("世界🌍".as_bytes()));
    }

    #[test]
    fn depth_node_collection_and_output_limits_fail_closed() {
        let nested = CanonicalValue::Array(vec![CanonicalValue::Array(vec![
            CanonicalValue::Array(vec![CanonicalValue::Null]),
        ])]);
        let frame = CanonicalFrame::new("limit.depth", version(), &nested).unwrap();
        let mut limits = CanonicalLimits::default();
        limits.max_depth = 1;
        assert_eq!(
            encode_canonical(frame, limits).unwrap_err().reason(),
            "canonical_depth_limit_exceeded"
        );

        let value = CanonicalValue::Array(vec![CanonicalValue::Null; 3]);
        let frame = CanonicalFrame::new("limit.nodes", version(), &value).unwrap();
        let mut limits = CanonicalLimits::default();
        limits.max_nodes = 2;
        assert_eq!(
            encode_canonical(frame, limits).unwrap_err().reason(),
            "canonical_node_limit_exceeded"
        );

        let frame = CanonicalFrame::new("limit.collection", version(), &value).unwrap();
        let mut limits = CanonicalLimits::default();
        limits.max_collection_items = 2;
        assert_eq!(
            encode_canonical(frame, limits).unwrap_err().reason(),
            "canonical_collection_limit_exceeded"
        );

        let value = CanonicalValue::Bytes(vec![1; 128]);
        let frame = CanonicalFrame::new("limit.output", version(), &value).unwrap();
        let mut limits = CanonicalLimits::default();
        limits.max_output_bytes = 32;
        assert_eq!(
            encode_canonical(frame, limits).unwrap_err().reason(),
            "canonical_output_limit_exceeded"
        );
    }

    #[test]
    fn invalid_domain_and_zero_protocol_major_are_rejected() {
        let value = CanonicalValue::Null;
        for domain in ["", ".bad", "bad.", "bad..name", "Bad.Name", "bad/name"] {
            assert!(CanonicalFrame::new(domain, version(), &value).is_err());
        }
        assert_eq!(
            CanonicalFrame::new("valid.domain", ProtocolVersion::new(0, 1), &value)
                .unwrap_err()
                .reason(),
            "canonical_protocol_major_zero"
        );
    }

    #[test]
    fn float_values_are_unrepresentable_by_the_public_type() {
        let value = CanonicalValue::I64(1);
        assert!(matches!(value, CanonicalValue::I64(1)));
    }
}
