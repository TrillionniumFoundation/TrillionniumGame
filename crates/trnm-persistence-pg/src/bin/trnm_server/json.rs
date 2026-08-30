use std::collections::{BTreeMap, BTreeSet};
use std::str;

use super::error::InputError;

const MAX_FIELDS: usize = 32;
const MAX_KEY_BYTES: usize = 64;
const MAX_STRING_BYTES: usize = 4096;

#[derive(Clone, Debug, Eq, PartialEq)]
enum Value {
    String(String),
    Unsigned(u64),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Object {
    values: BTreeMap<String, Value>,
}

impl Object {
    pub fn parse(input: &[u8]) -> Result<Self, InputError> {
        Parser::new(input).parse()
    }

    pub fn require_exact_keys(&self, expected: &[&str]) -> Result<(), InputError> {
        let expected = expected.iter().copied().collect::<BTreeSet<_>>();
        if self.values.len() != expected.len()
            || self
                .values
                .keys()
                .any(|key| !expected.contains(key.as_str()))
        {
            return Err(InputError::new("json_field_set_mismatch"));
        }
        Ok(())
    }

    pub fn string(&self, key: &str) -> Result<&str, InputError> {
        match self.values.get(key) {
            Some(Value::String(value)) => Ok(value),
            Some(Value::Unsigned(_)) => Err(InputError::new("json_field_type_mismatch")),
            None => Err(InputError::new("json_field_missing")),
        }
    }

    pub fn unsigned(&self, key: &str) -> Result<u64, InputError> {
        match self.values.get(key) {
            Some(Value::Unsigned(value)) => Ok(*value),
            Some(Value::String(_)) => Err(InputError::new("json_field_type_mismatch")),
            None => Err(InputError::new("json_field_missing")),
        }
    }
}

#[derive(Debug)]
struct Parser<'a> {
    input: &'a [u8],
    position: usize,
}

impl<'a> Parser<'a> {
    const fn new(input: &'a [u8]) -> Self {
        Self { input, position: 0 }
    }

    fn parse(mut self) -> Result<Object, InputError> {
        self.skip_whitespace();
        self.expect(b'{')?;
        self.skip_whitespace();
        let mut values = BTreeMap::new();
        if self.peek() == Some(b'}') {
            self.position += 1;
        } else {
            loop {
                if values.len() >= MAX_FIELDS {
                    return Err(InputError::new("json_field_limit_exceeded"));
                }
                let key = self.parse_string(MAX_KEY_BYTES, true)?;
                self.skip_whitespace();
                self.expect(b':')?;
                self.skip_whitespace();
                let value = match self.peek() {
                    Some(b'\"') => Value::String(self.parse_string(MAX_STRING_BYTES, false)?),
                    Some(b'0'..=b'9') => Value::Unsigned(self.parse_unsigned()?),
                    _ => return Err(InputError::new("json_value_type_not_supported")),
                };
                if values.insert(key, value).is_some() {
                    return Err(InputError::new("json_duplicate_field"));
                }
                self.skip_whitespace();
                match self.peek() {
                    Some(b',') => {
                        self.position += 1;
                        self.skip_whitespace();
                    }
                    Some(b'}') => {
                        self.position += 1;
                        break;
                    }
                    _ => return Err(InputError::new("json_object_delimiter_invalid")),
                }
            }
        }
        self.skip_whitespace();
        if self.position != self.input.len() {
            return Err(InputError::new("json_trailing_data"));
        }
        Ok(Object { values })
    }

    fn parse_string(&mut self, maximum: usize, key: bool) -> Result<String, InputError> {
        self.expect(b'\"')?;
        let start = self.position;
        loop {
            let byte = self
                .peek()
                .ok_or_else(|| InputError::new("json_string_unterminated"))?;
            match byte {
                b'\"' => break,
                b'\\' => return Err(InputError::new("json_escape_not_supported")),
                0x00..=0x1f => return Err(InputError::new("json_control_character")),
                _ => self.position += 1,
            }
            if self.position - start > maximum {
                return Err(InputError::new("json_string_limit_exceeded"));
            }
        }
        let bytes = &self.input[start..self.position];
        self.position += 1;
        if bytes.is_empty() {
            return Err(InputError::new("json_empty_string"));
        }
        let value = str::from_utf8(bytes)
            .map_err(|_| InputError::new("json_string_not_utf8"))?
            .to_owned();
        if key
            && !value
                .bytes()
                .all(|byte| matches!(byte, b'a'..=b'z' | b'0'..=b'9' | b'_'))
        {
            return Err(InputError::new("json_key_not_canonical"));
        }
        Ok(value)
    }

    fn parse_unsigned(&mut self) -> Result<u64, InputError> {
        let start = self.position;
        if self.peek() == Some(b'0') {
            self.position += 1;
            if matches!(self.peek(), Some(b'0'..=b'9')) {
                return Err(InputError::new("json_number_not_canonical"));
            }
        } else {
            while matches!(self.peek(), Some(b'0'..=b'9')) {
                self.position += 1;
            }
        }
        let value = str::from_utf8(&self.input[start..self.position])
            .map_err(|_| InputError::new("json_number_invalid"))?
            .parse::<u64>()
            .map_err(|_| InputError::new("json_number_out_of_range"))?;
        Ok(value)
    }

    fn skip_whitespace(&mut self) {
        while matches!(self.peek(), Some(b' ' | b'\t' | b'\r' | b'\n')) {
            self.position += 1;
        }
    }

    fn expect(&mut self, byte: u8) -> Result<(), InputError> {
        if self.peek() != Some(byte) {
            return Err(InputError::new("json_syntax_invalid"));
        }
        self.position += 1;
        Ok(())
    }

    fn peek(&self) -> Option<u8> {
        self.input.get(self.position).copied()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strict_object_parses_strings_and_unsigned_values() {
        let object = Object::parse(br#"{"entity_id":"01","revision":7}"#).unwrap();
        object
            .require_exact_keys(&["entity_id", "revision"])
            .unwrap();
        assert_eq!(object.string("entity_id").unwrap(), "01");
        assert_eq!(object.unsigned("revision").unwrap(), 7);
    }

    #[test]
    fn duplicate_nested_escaped_and_noncanonical_numbers_fail_closed() {
        for value in [
            br#"{"a":1,"a":2}"#.as_slice(),
            br#"{"a":{"b":1}}"#.as_slice(),
            br#"{"a":"x\ny"}"#.as_slice(),
            br#"{"a":01}"#.as_slice(),
        ] {
            assert!(Object::parse(value).is_err(), "{value:?}");
        }
    }

    #[test]
    fn unknown_or_missing_fields_are_rejected_by_endpoint_contract() {
        let object = Object::parse(br#"{"a":1,"b":2}"#).unwrap();
        assert_eq!(
            object.require_exact_keys(&["a"]).unwrap_err().reason(),
            "json_field_set_mismatch"
        );
        assert_eq!(
            object.string("missing").unwrap_err().reason(),
            "json_field_missing"
        );
    }
}
