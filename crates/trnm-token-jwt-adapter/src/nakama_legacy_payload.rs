#![forbid(unsafe_code)]
#![deny(missing_debug_implementations)]

//! Bounded payload encoding for Nakama's six legacy session claims.
//!
//! The field order and `omitempty` behavior follow the pinned
//! `SessionTokenClaims` declaration. Strings use the Go `encoding/json.Marshal`
//! escaping profile for valid UTF-8. This module only emits an unsigned JSON
//! payload: it does not parse a JWT, validate a principal, sign, select keys, or
//! provide an authentication service. Existing canonical/epoch encoders are
//! independent of this module.
//!
//! Rust strings cannot represent the invalid UTF-8 Go strings which the Go
//! encoder replaces with `\ufffd`. Such inputs and decoder behavior are outside
//! this encoder's input type; they must not be silently repaired by an adapter.

use core::fmt;
use std::collections::BTreeMap;

/// Borrowed source claims, without UUID, timestamp, or authorization policy.
///
/// Empty strings, a missing/empty variables map, and integer zero are omitted.
/// Nonzero negative integers, an empty map key/value, and raw string identifiers
/// are retained. `None` versus `Some(empty)` is only equivalent at this payload
/// boundary; it is not a refresh-request presence rule.
#[derive(Clone, Copy, Default, Eq, PartialEq)]
pub struct NakamaLegacyClaims<'a> {
    pub token_id: &'a str,
    pub user_id: &'a str,
    pub username: &'a str,
    pub variables: Option<&'a BTreeMap<String, String>>,
    pub expires_at: i64,
    pub issued_at: i64,
}

impl fmt::Debug for NakamaLegacyClaims<'_> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("NakamaLegacyClaims")
            .field("token_id_bytes", &self.token_id.len())
            .field("user_id_bytes", &self.user_id.len())
            .field("username_bytes", &self.username.len())
            .field("variable_count", &self.variables.map_or(0, BTreeMap::len))
            .field("expires_at", &self.expires_at)
            .field("issued_at", &self.issued_at)
            .finish()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum NakamaLegacyEncodeError {
    OutputLengthExceeded { limit: usize },
    AllocationFailed,
}

impl fmt::Display for NakamaLegacyEncodeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::OutputLengthExceeded { limit } => {
                write!(formatter, "legacy session payload exceeds {limit} bytes")
            }
            Self::AllocationFailed => {
                formatter.write_str("legacy session payload allocation failed")
            }
        }
    }
}

impl std::error::Error for NakamaLegacyEncodeError {}

/// Emit `tid`, `uid`, `usn`, `vrs`, `exp`, `iat`, in that declared order.
///
/// The caller must supply its encoded byte budget. The complete escaped length
/// is checked before reserving output, then the private output is allocated once
/// with a fallible reservation. A budget failure returns no partial payload.
/// This bound is a caller policy, not a claim about Nakama's accepted size range.
pub fn encode_nakama_legacy_payload(
    claims: &NakamaLegacyClaims<'_>,
    max_output_bytes: usize,
) -> Result<Vec<u8>, NakamaLegacyEncodeError> {
    let mut measure = LengthSink {
        length: 0,
        limit: max_output_bytes,
    };
    encode_claims(claims, &mut measure)?;

    let mut output = Vec::new();
    output
        .try_reserve_exact(measure.length)
        .map_err(|_| NakamaLegacyEncodeError::AllocationFailed)?;
    let mut sink = OutputSink {
        output,
        limit: max_output_bytes,
    };
    encode_claims(claims, &mut sink)?;
    Ok(sink.output)
}

trait Sink {
    fn append(&mut self, bytes: &[u8]) -> Result<(), NakamaLegacyEncodeError>;
}

struct LengthSink {
    length: usize,
    limit: usize,
}

impl Sink for LengthSink {
    fn append(&mut self, bytes: &[u8]) -> Result<(), NakamaLegacyEncodeError> {
        self.length = bounded_length(self.length, bytes.len(), self.limit)?;
        Ok(())
    }
}

struct OutputSink {
    output: Vec<u8>,
    limit: usize,
}

impl Sink for OutputSink {
    fn append(&mut self, bytes: &[u8]) -> Result<(), NakamaLegacyEncodeError> {
        bounded_length(self.output.len(), bytes.len(), self.limit)?;
        self.output.extend_from_slice(bytes);
        Ok(())
    }
}

fn bounded_length(
    current: usize,
    additional: usize,
    limit: usize,
) -> Result<usize, NakamaLegacyEncodeError> {
    current
        .checked_add(additional)
        .filter(|length| *length <= limit)
        .ok_or(NakamaLegacyEncodeError::OutputLengthExceeded { limit })
}

fn encode_claims(
    claims: &NakamaLegacyClaims<'_>,
    sink: &mut impl Sink,
) -> Result<(), NakamaLegacyEncodeError> {
    sink.append(b"{")?;
    let mut first = true;
    for (name, value) in [
        (b"\"tid\":".as_slice(), claims.token_id),
        (b"\"uid\":".as_slice(), claims.user_id),
        (b"\"usn\":".as_slice(), claims.username),
    ] {
        if !value.is_empty() {
            begin_field(sink, &mut first, name)?;
            encode_string(value, sink)?;
        }
    }
    if let Some(variables) = claims.variables.filter(|variables| !variables.is_empty()) {
        begin_field(sink, &mut first, b"\"vrs\":")?;
        sink.append(b"{")?;
        // String Ord compares raw UTF-8 bytes, as Go's strings.Compare does.
        // Keys are sorted before escaping, never by their escaped spelling.
        for (index, (key, value)) in variables.iter().enumerate() {
            if index != 0 {
                sink.append(b",")?;
            }
            encode_string(key, sink)?;
            sink.append(b":")?;
            encode_string(value, sink)?;
        }
        sink.append(b"}")?;
    }
    for (name, value) in [
        (b"\"exp\":".as_slice(), claims.expires_at),
        (b"\"iat\":".as_slice(), claims.issued_at),
    ] {
        if value != 0 {
            begin_field(sink, &mut first, name)?;
            // This temporary is at most 20 ASCII bytes, including i64::MIN.
            sink.append(value.to_string().as_bytes())?;
        }
    }
    sink.append(b"}")
}

fn begin_field(
    sink: &mut impl Sink,
    first: &mut bool,
    name: &[u8],
) -> Result<(), NakamaLegacyEncodeError> {
    if !*first {
        sink.append(b",")?;
    }
    *first = false;
    sink.append(name)
}

fn encode_string(value: &str, sink: &mut impl Sink) -> Result<(), NakamaLegacyEncodeError> {
    const HEX: &[u8; 16] = b"0123456789abcdef";

    sink.append(b"\"")?;
    for character in value.chars() {
        match character {
            '"' => sink.append(b"\\\"")?,
            '\\' => sink.append(b"\\\\")?,
            '\u{0008}' => sink.append(b"\\b")?,
            '\u{000c}' => sink.append(b"\\f")?,
            '\n' => sink.append(b"\\n")?,
            '\r' => sink.append(b"\\r")?,
            '\t' => sink.append(b"\\t")?,
            '\u{0000}'..='\u{001f}' | '<' | '>' | '&' => {
                let byte = character as u8;
                sink.append(&[
                    b'\\',
                    b'u',
                    b'0',
                    b'0',
                    HEX[usize::from(byte >> 4)],
                    HEX[usize::from(byte & 0x0f)],
                ])?;
            }
            '\u{2028}' => sink.append(b"\\u2028")?,
            '\u{2029}' => sink.append(b"\\u2029")?,
            _ => {
                let mut utf8 = [0; 4];
                sink.append(character.encode_utf8(&mut utf8).as_bytes())?;
            }
        }
    }
    sink.append(b"\"")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn text(claims: &NakamaLegacyClaims<'_>) -> String {
        String::from_utf8(encode_nakama_legacy_payload(claims, 16 * 1024).unwrap()).unwrap()
    }

    #[test]
    fn declared_claim_order_keeps_vars_before_numeric_dates() {
        let variables = BTreeMap::from([("z".into(), "last".into()), ("a".into(), "first".into())]);
        let claims = NakamaLegacyClaims {
            token_id: "session-id",
            user_id: "user-id",
            username: "Player",
            variables: Some(&variables),
            expires_at: 123,
            issued_at: 100,
        };
        assert_eq!(
            text(&claims),
            r#"{"tid":"session-id","uid":"user-id","usn":"Player","vrs":{"a":"first","z":"last"},"exp":123,"iat":100}"#
        );
    }

    #[test]
    fn zero_claims_nil_and_empty_variables_have_identical_omission() {
        assert_eq!(text(&NakamaLegacyClaims::default()), "{}");
        let empty = BTreeMap::new();
        assert_eq!(
            text(&NakamaLegacyClaims {
                variables: Some(&empty),
                ..NakamaLegacyClaims::default()
            }),
            "{}"
        );
    }

    #[test]
    fn all_64_presence_combinations_have_ordered_fields_and_valid_commas() {
        let variables = BTreeMap::from([("k".into(), "v".into())]);
        let expected_fields = [
            r#""tid":"t""#,
            r#""uid":"u""#,
            r#""usn":"n""#,
            r#""vrs":{"k":"v"}"#,
            r#""exp":1"#,
            r#""iat":-1"#,
        ];
        for mask in 0_u8..64 {
            let claims = NakamaLegacyClaims {
                token_id: if mask & 1 != 0 { "t" } else { "" },
                user_id: if mask & 2 != 0 { "u" } else { "" },
                username: if mask & 4 != 0 { "n" } else { "" },
                variables: if mask & 8 != 0 {
                    Some(&variables)
                } else {
                    None
                },
                expires_at: if mask & 16 != 0 { 1 } else { 0 },
                issued_at: if mask & 32 != 0 { -1 } else { 0 },
            };
            let fields = expected_fields
                .iter()
                .enumerate()
                .filter(|(index, _)| mask & (1 << index) != 0)
                .map(|(_, field)| *field)
                .collect::<Vec<_>>();
            assert_eq!(
                text(&claims),
                format!("{{{}}}", fields.join(",")),
                "mask {mask}"
            );
        }
    }

    #[test]
    fn go_html_and_line_separator_escaping_applies_to_every_string_position() {
        let value = "<>&\u{2028}\u{2029}";
        let variables = BTreeMap::from([(value.into(), value.into())]);
        let claims = NakamaLegacyClaims {
            token_id: value,
            user_id: value,
            username: value,
            variables: Some(&variables),
            ..NakamaLegacyClaims::default()
        };
        assert_eq!(
            text(&claims),
            r#"{"tid":"\u003c\u003e\u0026\u2028\u2029","uid":"\u003c\u003e\u0026\u2028\u2029","usn":"\u003c\u003e\u0026\u2028\u2029","vrs":{"\u003c\u003e\u0026\u2028\u2029":"\u003c\u003e\u0026\u2028\u2029"}}"#
        );
    }

    #[test]
    fn ascii_controls_use_short_escapes_and_lowercase_hex() {
        let controls = (0_u8..32).map(char::from).collect::<String>();
        assert_eq!(
            text(&NakamaLegacyClaims {
                username: &controls,
                ..NakamaLegacyClaims::default()
            }),
            r#"{"usn":"\u0000\u0001\u0002\u0003\u0004\u0005\u0006\u0007\b\t\n\u000b\f\r\u000e\u000f\u0010\u0011\u0012\u0013\u0014\u0015\u0016\u0017\u0018\u0019\u001a\u001b\u001c\u001d\u001e\u001f"}"#
        );
    }

    #[test]
    fn backslash_quote_slash_and_literal_unicode_escape_are_distinct() {
        assert_eq!(
            text(&NakamaLegacyClaims {
                username: "\"\\/\\u2028\u{007f}",
                ..NakamaLegacyClaims::default()
            }),
            "{\"usn\":\"\\\"\\\\/\\\\u2028\u{007f}\"}"
        );
    }

    #[test]
    fn valid_unicode_stays_utf8_except_the_two_line_separators() {
        let value = "é中😀\u{fffd}\u{fdd0}\u{ffff}";
        assert_eq!(
            text(&NakamaLegacyClaims {
                username: value,
                ..NakamaLegacyClaims::default()
            }),
            format!("{{\"usn\":\"{value}\"}}")
        );
    }

    #[test]
    fn map_keys_sort_by_original_utf8_bytes_instead_of_escaped_json() {
        let variables = BTreeMap::from([
            ("😀".into(), "emoji".into()),
            ("é".into(), "accent".into()),
            ("\u{e000}".into(), "bmp".into()),
            ("a".into(), "lower".into()),
            ("A".into(), "upper".into()),
            ("<".into(), "html".into()),
            ("\\".into(), "slash".into()),
            ("\r".into(), "control".into()),
        ]);
        assert_eq!(
            text(&NakamaLegacyClaims {
                variables: Some(&variables),
                ..NakamaLegacyClaims::default()
            }),
            "{\"vrs\":{\"\\r\":\"control\",\"\\u003c\":\"html\",\"A\":\"upper\",\"\\\\\":\"slash\",\"a\":\"lower\",\"é\":\"accent\",\"\u{e000}\":\"bmp\",\"😀\":\"emoji\"}}"
        );
    }

    #[test]
    fn nonempty_map_retains_empty_key_and_value() {
        let variables = BTreeMap::from([("".into(), "".into())]);
        assert_eq!(
            text(&NakamaLegacyClaims {
                variables: Some(&variables),
                ..NakamaLegacyClaims::default()
            }),
            r#"{"vrs":{"":""}}"#
        );
    }

    #[test]
    fn signed_int64_extremes_are_decimal_without_float_or_quote_conversion() {
        assert_eq!(
            text(&NakamaLegacyClaims {
                expires_at: i64::MIN,
                issued_at: i64::MAX,
                ..NakamaLegacyClaims::default()
            }),
            r#"{"exp":-9223372036854775808,"iat":9223372036854775807}"#
        );
    }

    #[test]
    fn identifiers_are_not_trimmed_canonicalized_or_repaired() {
        assert_eq!(
            text(&NakamaLegacyClaims {
                token_id: " arbitrary tid ",
                user_id: "{ABCDEF01-2345-6789-ABCD-EF0123456789}",
                username: " spaced ",
                ..NakamaLegacyClaims::default()
            }),
            r#"{"tid":" arbitrary tid ","uid":"{ABCDEF01-2345-6789-ABCD-EF0123456789}","usn":" spaced "}"#
        );
    }

    #[test]
    fn escaped_output_budget_is_exact_and_never_returns_a_partial_payload() {
        let claims = NakamaLegacyClaims {
            username: "<\u{2028}é",
            ..NakamaLegacyClaims::default()
        };
        let expected = b"{\"usn\":\"\\u003c\\u2028\xc3\xa9\"}";
        let length = expected.len();
        assert_eq!(
            encode_nakama_legacy_payload(&claims, length).unwrap(),
            expected
        );
        for limit in 0..length {
            assert_eq!(
                encode_nakama_legacy_payload(&claims, limit),
                Err(NakamaLegacyEncodeError::OutputLengthExceeded { limit })
            );
        }
        assert_eq!(
            encode_nakama_legacy_payload(&NakamaLegacyClaims::default(), 1),
            Err(NakamaLegacyEncodeError::OutputLengthExceeded { limit: 1 })
        );
        assert_eq!(
            encode_nakama_legacy_payload(&NakamaLegacyClaims::default(), 2).unwrap(),
            b"{}"
        );
    }

    #[test]
    fn length_overflow_is_a_budget_error_without_wrapping() {
        assert_eq!(
            bounded_length(usize::MAX, 1, usize::MAX),
            Err(NakamaLegacyEncodeError::OutputLengthExceeded { limit: usize::MAX })
        );
    }

    #[test]
    fn repeat_encoding_does_not_mutate_claims_or_variables() {
        let variables = BTreeMap::from([("<".into(), "unchanged".into())]);
        let original = variables.clone();
        let claims = NakamaLegacyClaims {
            variables: Some(&variables),
            ..NakamaLegacyClaims::default()
        };
        let first = text(&claims);
        assert_eq!(first, text(&claims));
        assert_eq!(variables, original);
    }

    #[test]
    fn debug_and_budget_error_do_not_publish_claim_strings() {
        let variables = BTreeMap::from([("SECRET_MAP_KEY".into(), "SECRET_MAP_VALUE".into())]);
        let claims = NakamaLegacyClaims {
            token_id: "SECRET_TOKEN_ID",
            user_id: "SECRET_USER_ID",
            username: "SECRET_USERNAME",
            variables: Some(&variables),
            ..NakamaLegacyClaims::default()
        };
        let debug = format!("{claims:?}");
        let error = encode_nakama_legacy_payload(&claims, 2).unwrap_err();
        for secret in [
            "SECRET_TOKEN_ID",
            "SECRET_USER_ID",
            "SECRET_USERNAME",
            "SECRET_MAP_KEY",
            "SECRET_MAP_VALUE",
        ] {
            assert!(!debug.contains(secret));
            assert!(!format!("{error:?} {error}").contains(secret));
        }
        assert!(debug.contains("variable_count: 1"));
    }
}
