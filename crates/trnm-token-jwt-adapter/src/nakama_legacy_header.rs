//! Work candidate: untrusted legacy RawURL transport and HS256 header syntax.
//! Nothing here authenticates a token. Original segments remain available for a
//! future verifier's signing-input custody; never re-encode them to form a MAC.
use super::{
    decode_string, scan_document, NakamaLegacyDecodeError, NakamaLegacyDecodeLimits, Scanner,
    StringBudget, ValueKind,
};
use core::fmt;
const MAX_ENCODED_BYTES: usize = 2 * 1024 * 1024;
const MAX_DECODED_BYTES: usize = 1024 * 1024;
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct NakamaLegacyRawUrlLimits {
    max_encoded_bytes: usize,
    max_decoded_bytes: usize,
}
impl NakamaLegacyRawUrlLimits {
    pub fn new(
        max_encoded_bytes: usize,
        max_decoded_bytes: usize,
    ) -> Result<Self, NakamaLegacyHeaderError> {
        if max_encoded_bytes > MAX_ENCODED_BYTES || max_decoded_bytes > MAX_DECODED_BYTES {
            return Err(NakamaLegacyHeaderError::InvalidLimits);
        }
        Ok(Self {
            max_encoded_bytes,
            max_decoded_bytes,
        })
    }
    #[must_use]
    pub const fn max_encoded_bytes(self) -> usize {
        self.max_encoded_bytes
    }
    #[must_use]
    pub const fn max_decoded_bytes(self) -> usize {
        self.max_decoded_bytes
    }
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum NakamaLegacyHeaderError {
    InvalidLimits,
    EncodedLimit { limit: usize },
    DecodedLimit { limit: usize },
    InvalidRawUrl { offset: usize },
    Json(NakamaLegacyDecodeError),
    HeaderRootType,
    NonFiniteHeaderNumber { offset: usize },
    InvalidAlgorithm,
    AllocationFailed,
}
impl fmt::Display for NakamaLegacyHeaderError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidLimits => f.write_str("invalid legacy RawURL limits"),
            Self::EncodedLimit { limit } => {
                write!(f, "legacy RawURL exceeds {limit} encoded bytes")
            }
            Self::DecodedLimit { limit } => {
                write!(f, "legacy RawURL exceeds {limit} decoded bytes")
            }
            Self::InvalidRawUrl { offset } => write!(f, "invalid legacy RawURL at byte {offset}"),
            Self::Json(error) => write!(f, "legacy header JSON: {error}"),
            Self::HeaderRootType => f.write_str("legacy header root is not an object or null"),
            Self::NonFiniteHeaderNumber { offset } => write!(
                f,
                "legacy header number is not a finite float64 at byte {offset}"
            ),
            Self::InvalidAlgorithm => f.write_str("legacy header algorithm is not HS256"),
            Self::AllocationFailed => f.write_str("legacy header allocation failed"),
        }
    }
}
impl std::error::Error for NakamaLegacyHeaderError {}
impl From<NakamaLegacyDecodeError> for NakamaLegacyHeaderError {
    fn from(error: NakamaLegacyDecodeError) -> Self {
        Self::Json(error)
    }
}
fn sextet(byte: u8) -> Option<u8> {
    match byte {
        b'A'..=b'Z' => Some(byte - b'A'),
        b'a'..=b'z' => Some(byte - b'a' + 26),
        b'0'..=b'9' => Some(byte - b'0' + 52),
        b'-' => Some(62),
        b'_' => Some(63),
        _ => None,
    }
}
/// Matches unpadded Go RawURLEncoding default (non-Strict), inside explicit caps.
/// CR/LF alone are ignored, including inside a quantum. Unused low bits in a
/// two/three-symbol final quantum are allowed. No partial result is returned.
pub fn decode_nakama_legacy_raw_url_segment(
    segment: &[u8],
    limits: NakamaLegacyRawUrlLimits,
) -> Result<Vec<u8>, NakamaLegacyHeaderError> {
    if segment.len() > limits.max_encoded_bytes {
        return Err(NakamaLegacyHeaderError::EncodedLimit {
            limit: limits.max_encoded_bytes,
        });
    }
    let mut count = 0usize;
    for (offset, byte) in segment.iter().copied().enumerate() {
        if matches!(byte, b'\r' | b'\n') {
            continue;
        }
        if sextet(byte).is_none() {
            return Err(NakamaLegacyHeaderError::InvalidRawUrl { offset });
        }
        count = count
            .checked_add(1)
            .ok_or(NakamaLegacyHeaderError::AllocationFailed)?;
    }
    let tail = count % 4;
    if tail == 1 {
        return Err(NakamaLegacyHeaderError::InvalidRawUrl {
            offset: segment.len(),
        });
    }
    let length = (count / 4)
        .checked_mul(3)
        .and_then(|n| {
            n.checked_add(match tail {
                2 => 1,
                3 => 2,
                _ => 0,
            })
        })
        .ok_or(NakamaLegacyHeaderError::AllocationFailed)?;
    if length > limits.max_decoded_bytes {
        return Err(NakamaLegacyHeaderError::DecodedLimit {
            limit: limits.max_decoded_bytes,
        });
    }
    let mut out = Vec::new();
    out.try_reserve_exact(length)
        .map_err(|_| NakamaLegacyHeaderError::AllocationFailed)?;
    let mut q = [0u8; 4];
    let mut used = 0;
    for byte in segment.iter().copied() {
        if matches!(byte, b'\r' | b'\n') {
            continue;
        }
        q[used] = sextet(byte).ok_or(NakamaLegacyHeaderError::InvalidRawUrl { offset: 0 })?;
        used += 1;
        if used == 4 {
            out.extend_from_slice(&[
                (q[0] << 2) | (q[1] >> 4),
                (q[1] << 4) | (q[2] >> 2),
                (q[2] << 6) | q[3],
            ]);
            used = 0;
        }
    }
    if used >= 2 {
        out.push((q[0] << 2) | (q[1] >> 4));
    }
    if used == 3 {
        out.push((q[1] << 4) | (q[2] >> 2));
    }
    debug_assert_eq!(out.len(), length);
    Ok(out)
}
/// A parsed header predicate only. Private fields prevent constructing this
/// result without validation; it proves neither MAC nor claims nor authority.
#[derive(Eq, PartialEq)]
pub struct NakamaLegacyHs256Header {
    raw_header: Vec<u8>,
    raw_encoded_segment: Option<Vec<u8>>,
}
impl NakamaLegacyHs256Header {
    #[must_use]
    pub fn raw_header(&self) -> &[u8] {
        &self.raw_header
    }
    #[must_use]
    pub fn raw_encoded_segment(&self) -> Option<&[u8]> {
        self.raw_encoded_segment.as_deref()
    }
    #[must_use]
    pub const fn algorithm(&self) -> &'static str {
        "HS256"
    }
}
impl fmt::Debug for NakamaLegacyHs256Header {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("NakamaLegacyHs256Header")
            .field("header_bytes", &self.raw_header.len())
            .field(
                "encoded_bytes",
                &self.raw_encoded_segment.as_ref().map(Vec::len),
            )
            .field("algorithm", &"HS256")
            .finish()
    }
}
/// Validate exactly the case-sensitive map key `alg`. Duplicates use last-map
/// assignment including null and non-string values, unlike six-claim nulls.
/// Every string receives the parent Go UTF8/UTF16 decoder and string budget;
/// every nested number is converted to finite float64, as JSONv1 map[any] does.
/// No generic JSON parser is copied: grammar and token scanning are shared.
pub fn validate_nakama_legacy_hs256_header(
    header: &[u8],
    limits: NakamaLegacyDecodeLimits,
) -> Result<NakamaLegacyHs256Header, NakamaLegacyHeaderError> {
    let document = scan_document(header, limits)?;
    if !matches!(document.kind, ValueKind::Object | ValueKind::Null) {
        return Err(NakamaLegacyHeaderError::HeaderRootType);
    }
    let mut cursor = Scanner {
        data: header,
        position: 0,
        members_seen: 0,
        root_members: Vec::new(),
        limits,
    };
    let mut budget = StringBudget {
        decoded: 0,
        limit: limits.max_decoded_string_bytes,
    };
    let mut next_member = 0;
    let mut alg_value_start = None;
    let mut algorithm = None;
    while cursor.position < header.len() {
        let start = cursor.position;
        match header[start] {
            b'"' => {
                cursor.string()?;
                let text = decode_string(&header[start..cursor.position], &mut budget)?;
                if document
                    .members
                    .get(next_member)
                    .is_some_and(|member| member.key.start == start)
                {
                    let member = &document.members[next_member];
                    if text == "alg" {
                        algorithm = None;
                        alg_value_start = Some(member.value.start);
                    }
                    next_member += 1;
                } else if alg_value_start == Some(start) {
                    algorithm = Some(text);
                }
            }
            b'-' | b'0'..=b'9' => {
                cursor.number()?;
                let number = std::str::from_utf8(&header[start..cursor.position])
                    .ok()
                    .and_then(|s| s.parse::<f64>().ok());
                if !number.is_some_and(f64::is_finite) {
                    return Err(NakamaLegacyHeaderError::NonFiniteHeaderNumber { offset: start });
                }
            }
            _ => cursor.position += 1,
        }
    }
    if algorithm.as_deref() != Some("HS256") {
        return Err(NakamaLegacyHeaderError::InvalidAlgorithm);
    }
    let mut copied = Vec::new();
    copied
        .try_reserve_exact(header.len())
        .map_err(|_| NakamaLegacyHeaderError::AllocationFailed)?;
    copied.extend_from_slice(header);
    Ok(NakamaLegacyHs256Header {
        raw_header: copied,
        raw_encoded_segment: None,
    })
}
/// Retains the original encoded segment. Future MAC verification must use that
/// original segment, never a normalized/re-encoded header after this predicate.
pub fn decode_and_validate_nakama_legacy_hs256_header(
    segment: &[u8],
    raw_url_limits: NakamaLegacyRawUrlLimits,
    json_limits: NakamaLegacyDecodeLimits,
) -> Result<NakamaLegacyHs256Header, NakamaLegacyHeaderError> {
    let decoded = decode_nakama_legacy_raw_url_segment(segment, raw_url_limits)?;
    let mut result = validate_nakama_legacy_hs256_header(&decoded, json_limits)?;
    let mut original = Vec::new();
    original
        .try_reserve_exact(segment.len())
        .map_err(|_| NakamaLegacyHeaderError::AllocationFailed)?;
    original.extend_from_slice(segment);
    result.raw_encoded_segment = Some(original);
    Ok(result)
}
#[cfg(test)]
mod tests {
    use super::*;
    fn limits() -> NakamaLegacyDecodeLimits {
        NakamaLegacyDecodeLimits::new(32768, 32, 1024, 98304).unwrap()
    }
    fn raw_limits() -> NakamaLegacyRawUrlLimits {
        NakamaLegacyRawUrlLimits::new(65536, 32768).unwrap()
    }
    fn header(raw: &[u8]) -> Result<NakamaLegacyHs256Header, NakamaLegacyHeaderError> {
        validate_nakama_legacy_hs256_header(raw, limits())
    }
    #[test]
    fn raw_url_empty_crlf_and_padding_whitespace_alphabet_boundaries() {
        for raw in [b"".as_slice(), b"\r\n\n\r"] {
            assert_eq!(
                decode_nakama_legacy_raw_url_segment(raw, raw_limits()).unwrap(),
                b""
            );
        }
        for raw in [
            b"Zg=".as_slice(),
            b"Zg==",
            b"Z g",
            b"Zg\t",
            b"Zg\x0b",
            b"Zg\x0c",
            b"Zg+",
            b"Zg/",
            b"Zg\xff",
            b"A",
            b"AAAAA",
        ] {
            assert!(decode_nakama_legacy_raw_url_segment(raw, raw_limits()).is_err());
        }
        assert_eq!(
            decode_nakama_legacy_raw_url_segment(b"Z\rg\n", raw_limits()).unwrap(),
            b"f"
        );
        assert_eq!(
            decode_nakama_legacy_raw_url_segment(b"SGVsbG8td29ybGQ_", raw_limits()).unwrap(),
            b"Hello-world?"
        );
    }
    #[test]
    fn raw_url_all_nonzero_unused_terminal_bits_are_accepted() {
        const A: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
        for &second in &A[32..48] {
            assert_eq!(
                decode_nakama_legacy_raw_url_segment(&[b'Z', second], raw_limits()).unwrap(),
                b"f"
            );
        }
        for &third in &A[60..64] {
            assert_eq!(
                decode_nakama_legacy_raw_url_segment(&[b'Z', b'm', third], raw_limits()).unwrap(),
                b"fo"
            );
        }
    }
    #[test]
    fn raw_url_exact_encoded_and_decoded_budgets_include_ignored_bytes() {
        assert!(decode_nakama_legacy_raw_url_segment(
            b"Zg",
            NakamaLegacyRawUrlLimits::new(2, 1).unwrap()
        )
        .is_ok());
        assert!(matches!(
            decode_nakama_legacy_raw_url_segment(
                b"Z\rg",
                NakamaLegacyRawUrlLimits::new(2, 1).unwrap()
            ),
            Err(NakamaLegacyHeaderError::EncodedLimit { limit: 2 })
        ));
        assert!(matches!(
            decode_nakama_legacy_raw_url_segment(
                b"Zm8",
                NakamaLegacyRawUrlLimits::new(3, 1).unwrap()
            ),
            Err(NakamaLegacyHeaderError::DecodedLimit { limit: 1 })
        ));
        assert!(NakamaLegacyRawUrlLimits::new(MAX_ENCODED_BYTES + 1, 1).is_err());
        assert!(NakamaLegacyRawUrlLimits::new(1, MAX_DECODED_BYTES + 1).is_err());
        assert!(decode_nakama_legacy_raw_url_segment(
            b"",
            NakamaLegacyRawUrlLimits::new(0, 0).unwrap()
        )
        .is_ok());
    }
    #[test]
    fn raw_url_full_groups_all_byte_values_roundtrip() {
        const A: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
        for value in 0..=255u8 {
            let s = [A[usize::from(value >> 2)], A[usize::from((value & 3) << 4)]];
            assert_eq!(
                decode_nakama_legacy_raw_url_segment(&s, raw_limits()).unwrap(),
                [value]
            );
        }
        for value in [0u32, 1, 0x123456, 0xffffff] {
            let s = [
                A[((value >> 18) & 63) as usize],
                A[((value >> 12) & 63) as usize],
                A[((value >> 6) & 63) as usize],
                A[(value & 63) as usize],
            ];
            assert_eq!(
                decode_nakama_legacy_raw_url_segment(&s, raw_limits()).unwrap(),
                [(value >> 16) as u8, (value >> 8) as u8, value as u8]
            );
        }
    }
    #[test]
    fn exact_algorithm_and_ignored_typ_kid_do_not_establish_identity() {
        let h = header(
            br#"{"alg":"HS256","typ":null,"kid":{"x":[1,true,null]},"uid":"nonuuid","exp":-1}"#,
        )
        .unwrap();
        assert_eq!(h.algorithm(), "HS256");
        assert!(h.raw_encoded_segment().is_none());
        for raw in [
            br#"{"ALG":"HS256"}"#.as_slice(),
            br#"{"Alg":"HS256"}"#,
            br#"{"alg":"hs256"}"#,
            br#"{"alg":"none"}"#,
            br#"{"alg":"HS512"}"#,
            br#"{"alg":"HS256 "}"#,
            br#"{"alg":"HS256\u0000"}"#,
        ] {
            assert_eq!(
                header(raw).unwrap_err(),
                NakamaLegacyHeaderError::InvalidAlgorithm
            );
        }
    }
    #[test]
    fn duplicate_algorithm_null_and_nonstring_overwrite_previous_string() {
        for last in ["null", "true", "123", "{}", "[]"] {
            let raw = format!("{{\"alg\":\"HS256\",\"alg\":{last}}}");
            assert_eq!(
                header(raw.as_bytes()).unwrap_err(),
                NakamaLegacyHeaderError::InvalidAlgorithm
            );
            let raw = format!("{{\"alg\":{last},\"alg\":\"HS256\"}}");
            assert!(header(raw.as_bytes()).is_ok());
        }
        assert!(header(br#"{"alg":"HS512","alg":"HS256","ALG":null}"#).is_ok());
    }
    #[test]
    fn escaped_exact_algorithm_key_and_value_follow_go_string_rules() {
        assert!(header(br#"{"\u0061lg":"H\u0053256"}"#).is_ok());
        assert_eq!(
            header(br#"{"a\u017fg":"HS256"}"#).unwrap_err(),
            NakamaLegacyHeaderError::InvalidAlgorithm
        );
        assert!(header(b"{\"alg\":\"HS256\",\"x\":\"\xe2\x82|\xed\xa0\x80\"}").is_ok());
        assert!(header(br#"{"alg":"HS256","x":"\ud800\udc00|\ud800A\udc00"}"#).is_ok());
    }
    #[test]
    fn all_nested_header_numbers_have_finite_float64_conversion() {
        for raw in [
            br#"{"alg":"HS256","x":1e999}"#.as_slice(),
            br#"{"x":[{"x":-1e999}],"alg":"HS256"}"#,
            br#"{"alg":{"x":1e999},"alg":"HS256"}"#,
            br#"{"alg":1e999,"alg":"HS256"}"#,
            br#"{"x":1e999,"x":null,"alg":"HS256"}"#,
        ] {
            assert!(matches!(
                header(raw),
                Err(NakamaLegacyHeaderError::NonFiniteHeaderNumber { .. })
            ));
        }
        assert!(header(
            br#"{"alg":"HS256","x":[1.5,-0,1e-999999,9223372036854775808,1.7976931348623157e308]}"#
        )
        .is_ok());
        assert!(matches!(
            header(br#"{"alg":"HS256","x":1.7976931348623159e308}"#),
            Err(NakamaLegacyHeaderError::NonFiniteHeaderNumber { .. })
        ));
        assert!(header(br#"{"alg":"HS256","x":"1e999"}"#).is_ok());
    }
    #[test]
    fn header_syntax_precedes_algorithm_and_type_root_is_narrow() {
        for raw in [
            b"{\"alg\":null,\"x\":[1,]}".as_slice(),
            b"{\"alg\":\"HS256\"} null",
            b"{\"alg\":\"HS256\",\"x\":00}",
            b"{\"alg\":\"HS256\",\"x\":\"\\q\"}",
        ] {
            assert!(matches!(
                header(raw),
                Err(NakamaLegacyHeaderError::Json(
                    NakamaLegacyDecodeError::Syntax { .. }
                ))
            ));
        }
        assert_eq!(
            header(b"null").unwrap_err(),
            NakamaLegacyHeaderError::InvalidAlgorithm
        );
        for raw in [
            b"[]".as_slice(),
            b"0",
            b"true",
            br#""HS256""#,
            br#"[1e999]"#,
        ] {
            assert_eq!(
                header(raw).unwrap_err(),
                NakamaLegacyHeaderError::HeaderRootType
            );
        }
    }
    #[test]
    fn all_unknown_header_strings_share_cumulative_decoding_budget() {
        let raw = br#"{"alg":"HS256","x":["a","b"]}"#;
        assert!(validate_nakama_legacy_hs256_header(
            raw,
            NakamaLegacyDecodeLimits::new(raw.len(), 2, 4, 11).unwrap()
        )
        .is_ok());
        assert!(matches!(
            validate_nakama_legacy_hs256_header(
                raw,
                NakamaLegacyDecodeLimits::new(raw.len(), 2, 4, 10).unwrap()
            ),
            Err(NakamaLegacyHeaderError::Json(
                NakamaLegacyDecodeError::DecodedStringLimit { limit: 10 }
            ))
        ));
        let raw = b"{\"alg\":\"HS256\",\"x\":\"\xff\"}";
        assert!(validate_nakama_legacy_hs256_header(
            raw,
            NakamaLegacyDecodeLimits::new(raw.len(), 1, 2, 12).unwrap()
        )
        .is_ok());
        assert!(matches!(
            validate_nakama_legacy_hs256_header(
                raw,
                NakamaLegacyDecodeLimits::new(raw.len(), 1, 2, 11).unwrap()
            ),
            Err(NakamaLegacyHeaderError::Json(
                NakamaLegacyDecodeError::DecodedStringLimit { limit: 11 }
            ))
        ));
    }
    #[test]
    fn convenience_preserves_noncanonical_original_segment_and_debug_redacts() {
        let segment = b"eyJhbGciOiJIUzI1NiJ9\r\n";
        let result =
            decode_and_validate_nakama_legacy_hs256_header(segment, raw_limits(), limits())
                .unwrap();
        assert_eq!(result.raw_header(), br#"{"alg":"HS256"}"#);
        assert_eq!(result.raw_encoded_segment(), Some(segment.as_slice()));
        let raw = br#"{"alg":"HS256","private":"secret"}"#;
        let result = header(raw).unwrap();
        let debug = format!("{result:?}");
        assert!(!debug.contains("private") && !debug.contains("secret"));
        assert!(!format!("{:?}", header(br#"{"alg":"secret"}"#).unwrap_err()).contains("secret"));
    }
    #[test]
    fn nested_algorithm_keys_do_not_override_root_and_late_root_nonstring_does() {
        assert!(header(br#"{"alg":"HS256","x":{"alg":"none","nested":[{"alg":null}]}}"#).is_ok());
        assert!(header(br#"{"x":{"alg":"HS256"},"alg":null}"#).is_err());
        assert!(header(br#"{"alg":"HS256","x":{"alg":"none"},"\u0061lg":null}"#).is_err());
        assert!(header(br#"{"alg":null,"x":{"alg":"none"},"\u0061lg":"HS256"}"#).is_ok());
    }
    #[test]
    fn raw_url_each_byte_alphabet_and_crlf_has_a_closed_classification() {
        for byte in 0..=255u8 {
            let raw = [b'Z', b'g', byte];
            let result = decode_nakama_legacy_raw_url_segment(&raw, raw_limits());
            if sextet(byte).is_some() {
                assert!(result.is_ok());
            } else if matches!(byte, b'\r' | b'\n') {
                assert_eq!(result.unwrap(), b"f");
            } else {
                assert!(matches!(
                    result,
                    Err(NakamaLegacyHeaderError::InvalidRawUrl { offset: 2 })
                ));
            }
        }
    }
    #[test]
    fn malformed_late_number_rejects_whole_header_even_after_valid_algorithm() {
        for number in ["+1", "01", "-01", "1.", "1e", "NaN", "Infinity", "0x10"] {
            let raw = format!("{{\"alg\":\"HS256\",\"ignored\":[{number}]}}");
            assert!(matches!(
                header(raw.as_bytes()),
                Err(NakamaLegacyHeaderError::Json(
                    NakamaLegacyDecodeError::Syntax { .. }
                ))
            ));
        }
    }

    #[test]
    fn accepted_unused_bits_are_retained_for_future_signing_input_custody() {
        let raw = b"eyJhbGciOiJIUzI1NiIgff";
        let value =
            decode_and_validate_nakama_legacy_hs256_header(raw, raw_limits(), limits()).unwrap();
        assert_eq!(value.raw_header(), br#"{"alg":"HS256" }"#);
        assert_eq!(value.raw_encoded_segment(), Some(raw.as_slice()));
        assert_ne!(raw, b"eyJhbGciOiJIUzI1NiIgfQ" as &[u8]);
    }
}
