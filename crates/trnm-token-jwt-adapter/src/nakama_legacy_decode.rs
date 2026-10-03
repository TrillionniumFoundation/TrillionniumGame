#![forbid(unsafe_code)]
#![deny(missing_debug_implementations)]

//! Bounded decoding into a fresh Nakama legacy six-claim value.
//!
//! This is an independent event decoder for the pinned Go `encoding/json`
//! `Unmarshal` rules used by `SessionTokenClaims`, not the canonical JWT
//! parser. It checks the entire JSON syntax before assigning any claim.
//! Object occurrences keep their original order and original byte spans.
//! Unknown members are syntax-checked and preserved, without floating-point
//! conversion. Strings follow Go's per-invalid-byte UTF-8 replacement and
//! unpaired UTF-16 surrogate replacement rules.
//!
//! The caller chooses resource limits, whose ceilings are narrower than Go's
//! accepted input domain. These are not upstream validation requirements.
//! Error text is local and redacted; it does not reproduce Go error strings.
//! The six-claim API authenticates nothing: it has no MAC, UUID, clock,
//! blacklist, or principal API. The separate header API validates syntax only.
//! Both outputs remain untrusted until cryptographic verification.

use core::fmt;
use std::collections::BTreeMap;
use std::ops::Range;

const MAX_LOCAL_PAYLOAD_BYTES: usize = 1024 * 1024;
const MAX_LOCAL_DEPTH: usize = 128;
const MAX_LOCAL_MEMBERS: usize = 16_384;
const MAX_LOCAL_DECODED_STRING_BYTES: usize = 3 * MAX_LOCAL_PAYLOAD_BYTES;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct NakamaLegacyDecodeLimits {
    max_payload_bytes: usize,
    max_depth: usize,
    max_members: usize,
    max_decoded_string_bytes: usize,
}

impl NakamaLegacyDecodeLimits {
    pub fn new(
        max_payload_bytes: usize,
        max_depth: usize,
        max_members: usize,
        max_decoded_string_bytes: usize,
    ) -> Result<Self, NakamaLegacyDecodeError> {
        if !(1..=MAX_LOCAL_PAYLOAD_BYTES).contains(&max_payload_bytes)
            || !(1..=MAX_LOCAL_DEPTH).contains(&max_depth)
            || max_members > MAX_LOCAL_MEMBERS
            || max_decoded_string_bytes > MAX_LOCAL_DECODED_STRING_BYTES
        {
            return Err(NakamaLegacyDecodeError::InvalidLimits);
        }
        Ok(Self {
            max_payload_bytes,
            max_depth,
            max_members,
            max_decoded_string_bytes,
        })
    }

    #[must_use]
    pub const fn max_payload_bytes(self) -> usize {
        self.max_payload_bytes
    }

    #[must_use]
    pub const fn max_depth(self) -> usize {
        self.max_depth
    }

    #[must_use]
    pub const fn max_members(self) -> usize {
        self.max_members
    }

    #[must_use]
    pub const fn max_decoded_string_bytes(self) -> usize {
        self.max_decoded_string_bytes
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum NakamaLegacyField {
    TokenId,
    UserId,
    Username,
    Variables,
    ExpiresAt,
    IssuedAt,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum NakamaLegacySyntaxReason {
    UnexpectedEnd,
    UnexpectedByte,
    InvalidEscape,
    InvalidNumber,
    TrailingData,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum NakamaLegacyDecodeError {
    InvalidLimits,
    PayloadLimit {
        limit: usize,
    },
    DepthLimit {
        limit: usize,
    },
    MemberLimit {
        limit: usize,
    },
    DecodedStringLimit {
        limit: usize,
    },
    AllocationFailed,
    Syntax {
        offset: usize,
        reason: NakamaLegacySyntaxReason,
    },
    TypeMismatch {
        field: Option<NakamaLegacyField>,
        offset: usize,
    },
    InvalidInt64 {
        field: NakamaLegacyField,
        offset: usize,
    },
}

impl fmt::Display for NakamaLegacyDecodeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidLimits => formatter.write_str("invalid legacy decoder resource limits"),
            Self::PayloadLimit { limit } => {
                write!(formatter, "legacy payload exceeds {limit} bytes")
            }
            Self::DepthLimit { limit } => write!(formatter, "legacy JSON exceeds depth {limit}"),
            Self::MemberLimit { limit } => write!(formatter, "legacy JSON exceeds {limit} members"),
            Self::DecodedStringLimit { limit } => {
                write!(formatter, "legacy decoded strings exceed {limit} bytes")
            }
            Self::AllocationFailed => formatter.write_str("legacy decoder allocation failed"),
            Self::Syntax { offset, reason } => {
                write!(
                    formatter,
                    "legacy JSON syntax error at byte {offset}: {reason:?}"
                )
            }
            Self::TypeMismatch { field, offset } => {
                write!(
                    formatter,
                    "legacy claim type mismatch at byte {offset}: {field:?}"
                )
            }
            Self::InvalidInt64 { field, offset } => {
                write!(
                    formatter,
                    "legacy claim is not an int64 at byte {offset}: {field:?}"
                )
            }
        }
    }
}

impl std::error::Error for NakamaLegacyDecodeError {}

/// Owned, untrusted claims. Zero values do not establish an identity or time.
#[derive(Default, Eq, PartialEq)]
pub struct NakamaLegacyRawClaims {
    pub token_id: String,
    pub user_id: String,
    pub username: String,
    pub variables: Option<BTreeMap<String, String>>,
    pub expires_at: i64,
    pub issued_at: i64,
}

impl fmt::Debug for NakamaLegacyRawClaims {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("NakamaLegacyRawClaims")
            .field("token_id_bytes", &self.token_id.len())
            .field("user_id_bytes", &self.user_id.len())
            .field("username_bytes", &self.username.len())
            .field(
                "variable_count",
                &self.variables.as_ref().map(BTreeMap::len),
            )
            .field("expires_at", &self.expires_at)
            .field("issued_at", &self.issued_at)
            .finish()
    }
}

#[derive(Eq, PartialEq)]
pub struct NakamaLegacyClaimOccurrence {
    name: String,
    field: Option<NakamaLegacyField>,
    key_span: Range<usize>,
    value_span: Range<usize>,
}

impl NakamaLegacyClaimOccurrence {
    #[must_use]
    pub fn name(&self) -> &str {
        &self.name
    }

    #[must_use]
    pub const fn field(&self) -> Option<NakamaLegacyField> {
        self.field
    }

    #[must_use]
    pub fn key_span(&self) -> Range<usize> {
        self.key_span.clone()
    }

    #[must_use]
    pub fn value_span(&self) -> Range<usize> {
        self.value_span.clone()
    }
}

impl fmt::Debug for NakamaLegacyClaimOccurrence {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("NakamaLegacyClaimOccurrence")
            .field("name_bytes", &self.name.len())
            .field("field", &self.field)
            .field("key_span", &self.key_span)
            .field("value_span", &self.value_span)
            .finish()
    }
}

#[derive(Eq, PartialEq)]
pub struct NakamaLegacyDecodedClaims {
    pub claims: NakamaLegacyRawClaims,
    payload: Vec<u8>,
    occurrences: Vec<NakamaLegacyClaimOccurrence>,
    root_was_null: bool,
}

impl NakamaLegacyDecodedClaims {
    #[must_use]
    pub fn raw_payload(&self) -> &[u8] {
        &self.payload
    }

    #[must_use]
    pub fn occurrences(&self) -> &[NakamaLegacyClaimOccurrence] {
        &self.occurrences
    }

    #[must_use]
    pub const fn root_was_null(&self) -> bool {
        self.root_was_null
    }

    #[must_use]
    pub fn raw_key(&self, index: usize) -> Option<&[u8]> {
        self.payload
            .get(self.occurrences.get(index)?.key_span.clone())
    }

    #[must_use]
    pub fn raw_value(&self, index: usize) -> Option<&[u8]> {
        self.payload
            .get(self.occurrences.get(index)?.value_span.clone())
    }
}

impl fmt::Debug for NakamaLegacyDecodedClaims {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("NakamaLegacyDecodedClaims")
            .field("claims", &self.claims)
            .field("payload_bytes", &self.payload.len())
            .field("occurrence_count", &self.occurrences.len())
            .field("root_was_null", &self.root_was_null)
            .finish()
    }
}

/// Decode a fresh six-field struct, not an update into caller-owned state.
///
/// Unknown fields and duplicate occurrences are preserved without assigning
/// them authority. A type error returns no partial claim object. Primitive
/// nulls have no effect; a `vrs` null clears the map; successive `vrs` objects
/// merge. Within the map each element starts as a fresh Go string: `key:null`
/// inserts an empty string, including when it replaces an earlier value.
pub fn decode_nakama_legacy_claims(
    payload: &[u8],
    limits: NakamaLegacyDecodeLimits,
) -> Result<NakamaLegacyDecodedClaims, NakamaLegacyDecodeError> {
    let document = scan_document(payload, limits)?;
    if !matches!(document.kind, ValueKind::Object | ValueKind::Null) {
        return Err(NakamaLegacyDecodeError::TypeMismatch {
            field: None,
            offset: document.span.start,
        });
    }
    let mut claims = NakamaLegacyRawClaims::default();
    let mut occurrences = Vec::new();
    occurrences
        .try_reserve_exact(document.members.len())
        .map_err(|_| NakamaLegacyDecodeError::AllocationFailed)?;
    let mut string_budget = StringBudget {
        decoded: 0,
        limit: limits.max_decoded_string_bytes,
    };
    for member in document.members {
        let name = decode_string(&payload[member.key.clone()], &mut string_budget)?;
        let field = match_field(&name);
        if let Some(field) = field {
            apply_field(
                field,
                &payload[member.value.clone()],
                member.value.start,
                limits,
                &mut string_budget,
                &mut claims,
            )?;
        }
        occurrences.push(NakamaLegacyClaimOccurrence {
            name,
            field,
            key_span: member.key,
            value_span: member.value,
        });
    }
    let mut copied = Vec::new();
    copied
        .try_reserve_exact(payload.len())
        .map_err(|_| NakamaLegacyDecodeError::AllocationFailed)?;
    copied.extend_from_slice(payload);
    Ok(NakamaLegacyDecodedClaims {
        claims,
        payload: copied,
        occurrences,
        root_was_null: document.kind == ValueKind::Null,
    })
}

fn match_field(name: &str) -> Option<NakamaLegacyField> {
    // All six tags are ASCII and unique. Their only non-ASCII simple-fold
    // equivalent is long s, U+017F, in "usn"/"vrs". Go's pinned caseOrbit
    // binds S -> s -> long-s -> S; Turkish dotted/dotless I do not join I/i.
    let mut folded = [0; 3];
    let mut count = 0;
    for character in name.chars() {
        let byte = if character.is_ascii() {
            (character as u8).to_ascii_lowercase()
        } else if character == '\u{017f}' {
            b's'
        } else {
            return None;
        };
        let slot = folded.get_mut(count)?;
        *slot = byte;
        count += 1;
    }
    if count != 3 {
        return None;
    }
    match &folded {
        b"tid" => Some(NakamaLegacyField::TokenId),
        b"uid" => Some(NakamaLegacyField::UserId),
        b"usn" => Some(NakamaLegacyField::Username),
        b"vrs" => Some(NakamaLegacyField::Variables),
        b"exp" => Some(NakamaLegacyField::ExpiresAt),
        b"iat" => Some(NakamaLegacyField::IssuedAt),
        _ => None,
    }
}

fn apply_field(
    field: NakamaLegacyField,
    value: &[u8],
    offset: usize,
    limits: NakamaLegacyDecodeLimits,
    budget: &mut StringBudget,
    claims: &mut NakamaLegacyRawClaims,
) -> Result<(), NakamaLegacyDecodeError> {
    if field == NakamaLegacyField::Variables {
        if value == b"null" {
            claims.variables = None;
            return Ok(());
        }
        if value.first() != Some(&b'{') {
            return Err(NakamaLegacyDecodeError::TypeMismatch {
                field: Some(field),
                offset,
            });
        }
        let object = scan_document(value, limits)?;
        let map = claims.variables.get_or_insert_with(BTreeMap::new);
        for member in object.members {
            let key = decode_string(&value[member.key], budget)?;
            let item = &value[member.value.clone()];
            let decoded = if item == b"null" {
                String::new()
            } else if item.first() == Some(&b'"') {
                decode_string(item, budget)?
            } else {
                return Err(NakamaLegacyDecodeError::TypeMismatch {
                    field: Some(field),
                    offset: offset + member.value.start,
                });
            };
            // Allocation is bounded by the member/string policies. Standard
            // BTreeMap node allocation has no fallible stable-Rust API.
            map.insert(key, decoded);
        }
        return Ok(());
    }
    if value == b"null" {
        return Ok(());
    }
    match field {
        NakamaLegacyField::TokenId | NakamaLegacyField::UserId | NakamaLegacyField::Username => {
            if value.first() != Some(&b'"') {
                return Err(NakamaLegacyDecodeError::TypeMismatch {
                    field: Some(field),
                    offset,
                });
            }
            let decoded = decode_string(value, budget)?;
            match field {
                NakamaLegacyField::TokenId => claims.token_id = decoded,
                NakamaLegacyField::UserId => claims.user_id = decoded,
                _ => claims.username = decoded,
            }
        }
        NakamaLegacyField::ExpiresAt | NakamaLegacyField::IssuedAt => {
            if !matches!(value.first(), Some(b'-' | b'0'..=b'9')) {
                return Err(NakamaLegacyDecodeError::TypeMismatch {
                    field: Some(field),
                    offset,
                });
            }
            let number = std::str::from_utf8(value)
                .ok()
                .and_then(|text| text.parse::<i64>().ok())
                .ok_or(NakamaLegacyDecodeError::InvalidInt64 { field, offset })?;
            match field {
                NakamaLegacyField::ExpiresAt => claims.expires_at = number,
                _ => claims.issued_at = number,
            }
        }
        NakamaLegacyField::Variables => {
            return Err(NakamaLegacyDecodeError::TypeMismatch {
                field: Some(field),
                offset,
            })
        }
    }
    Ok(())
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ValueKind {
    Object,
    Array,
    String,
    Number,
    Bool,
    Null,
}

struct RawMember {
    key: Range<usize>,
    value: Range<usize>,
}

struct Document {
    kind: ValueKind,
    span: Range<usize>,
    members: Vec<RawMember>,
}

struct Scanner<'a> {
    data: &'a [u8],
    position: usize,
    members_seen: usize,
    root_members: Vec<RawMember>,
    limits: NakamaLegacyDecodeLimits,
}

fn scan_document(
    data: &[u8],
    limits: NakamaLegacyDecodeLimits,
) -> Result<Document, NakamaLegacyDecodeError> {
    if data.len() > limits.max_payload_bytes {
        return Err(NakamaLegacyDecodeError::PayloadLimit {
            limit: limits.max_payload_bytes,
        });
    }
    let mut scanner = Scanner {
        data,
        position: 0,
        members_seen: 0,
        root_members: Vec::new(),
        limits,
    };
    scanner.space();
    let start = scanner.position;
    let kind = scanner.value(0)?;
    let end = scanner.position;
    scanner.space();
    if scanner.position != data.len() {
        return Err(scanner.error(NakamaLegacySyntaxReason::TrailingData));
    }
    Ok(Document {
        kind,
        span: start..end,
        members: scanner.root_members,
    })
}

impl Scanner<'_> {
    fn error(&self, reason: NakamaLegacySyntaxReason) -> NakamaLegacyDecodeError {
        NakamaLegacyDecodeError::Syntax {
            offset: self.position,
            reason,
        }
    }

    fn space(&mut self) {
        while matches!(
            self.data.get(self.position),
            Some(b' ' | b'\t' | b'\r' | b'\n')
        ) {
            self.position += 1;
        }
    }

    fn expect(&mut self, byte: u8) -> Result<(), NakamaLegacyDecodeError> {
        match self.data.get(self.position) {
            Some(actual) if *actual == byte => {
                self.position += 1;
                Ok(())
            }
            Some(_) => Err(self.error(NakamaLegacySyntaxReason::UnexpectedByte)),
            None => Err(self.error(NakamaLegacySyntaxReason::UnexpectedEnd)),
        }
    }

    fn member(&mut self) -> Result<(), NakamaLegacyDecodeError> {
        self.members_seen = self
            .members_seen
            .checked_add(1)
            .filter(|count| *count <= self.limits.max_members)
            .ok_or(NakamaLegacyDecodeError::MemberLimit {
                limit: self.limits.max_members,
            })?;
        Ok(())
    }

    fn value(&mut self, depth: usize) -> Result<ValueKind, NakamaLegacyDecodeError> {
        self.space();
        match self.data.get(self.position) {
            Some(b'{') => {
                self.object(depth)?;
                Ok(ValueKind::Object)
            }
            Some(b'[') => {
                self.array(depth)?;
                Ok(ValueKind::Array)
            }
            Some(b'"') => {
                self.string()?;
                Ok(ValueKind::String)
            }
            Some(b't') => {
                self.literal(b"true")?;
                Ok(ValueKind::Bool)
            }
            Some(b'f') => {
                self.literal(b"false")?;
                Ok(ValueKind::Bool)
            }
            Some(b'n') => {
                self.literal(b"null")?;
                Ok(ValueKind::Null)
            }
            Some(b'-' | b'0'..=b'9') => {
                self.number()?;
                Ok(ValueKind::Number)
            }
            Some(_) => Err(self.error(NakamaLegacySyntaxReason::UnexpectedByte)),
            None => Err(self.error(NakamaLegacySyntaxReason::UnexpectedEnd)),
        }
    }

    fn object(&mut self, depth: usize) -> Result<(), NakamaLegacyDecodeError> {
        if depth >= self.limits.max_depth {
            return Err(NakamaLegacyDecodeError::DepthLimit {
                limit: self.limits.max_depth,
            });
        }
        self.expect(b'{')?;
        self.space();
        if self.data.get(self.position) == Some(&b'}') {
            self.position += 1;
            return Ok(());
        }
        loop {
            self.member()?;
            let start = self.position;
            self.string()?;
            let key = start..self.position;
            self.space();
            self.expect(b':')?;
            self.space();
            let start = self.position;
            self.value(depth + 1)?;
            if depth == 0 {
                self.root_members
                    .try_reserve(1)
                    .map_err(|_| NakamaLegacyDecodeError::AllocationFailed)?;
                self.root_members.push(RawMember {
                    key,
                    value: start..self.position,
                });
            }
            self.space();
            match self.data.get(self.position) {
                Some(b'}') => {
                    self.position += 1;
                    return Ok(());
                }
                Some(b',') => {
                    self.position += 1;
                    self.space();
                }
                Some(_) => return Err(self.error(NakamaLegacySyntaxReason::UnexpectedByte)),
                None => return Err(self.error(NakamaLegacySyntaxReason::UnexpectedEnd)),
            }
        }
    }

    fn array(&mut self, depth: usize) -> Result<(), NakamaLegacyDecodeError> {
        if depth >= self.limits.max_depth {
            return Err(NakamaLegacyDecodeError::DepthLimit {
                limit: self.limits.max_depth,
            });
        }
        self.expect(b'[')?;
        self.space();
        if self.data.get(self.position) == Some(&b']') {
            self.position += 1;
            return Ok(());
        }
        loop {
            self.member()?;
            self.value(depth + 1)?;
            self.space();
            match self.data.get(self.position) {
                Some(b']') => {
                    self.position += 1;
                    return Ok(());
                }
                Some(b',') => {
                    self.position += 1;
                }
                Some(_) => return Err(self.error(NakamaLegacySyntaxReason::UnexpectedByte)),
                None => return Err(self.error(NakamaLegacySyntaxReason::UnexpectedEnd)),
            }
        }
    }

    fn literal(&mut self, literal: &[u8]) -> Result<(), NakamaLegacyDecodeError> {
        for byte in literal {
            self.expect(*byte)?;
        }
        Ok(())
    }

    fn string(&mut self) -> Result<(), NakamaLegacyDecodeError> {
        self.expect(b'"')?;
        loop {
            match self.data.get(self.position).copied() {
                Some(b'"') => {
                    self.position += 1;
                    return Ok(());
                }
                Some(b'\\') => {
                    self.position += 1;
                    match self.data.get(self.position) {
                        Some(b'"' | b'\\' | b'/' | b'b' | b'f' | b'n' | b'r' | b't') => {
                            self.position += 1
                        }
                        Some(b'u') => {
                            self.position += 1;
                            for _ in 0..4 {
                                if !self
                                    .data
                                    .get(self.position)
                                    .is_some_and(u8::is_ascii_hexdigit)
                                {
                                    return Err(self.error(NakamaLegacySyntaxReason::InvalidEscape));
                                }
                                self.position += 1;
                            }
                        }
                        _ => return Err(self.error(NakamaLegacySyntaxReason::InvalidEscape)),
                    }
                }
                Some(0..=0x1f) => return Err(self.error(NakamaLegacySyntaxReason::UnexpectedByte)),
                // Go's scanner does not reject malformed UTF-8 inside strings.
                Some(_) => self.position += 1,
                None => return Err(self.error(NakamaLegacySyntaxReason::UnexpectedEnd)),
            }
        }
    }

    fn number(&mut self) -> Result<(), NakamaLegacyDecodeError> {
        if self.data.get(self.position) == Some(&b'-') {
            self.position += 1;
        }
        match self.data.get(self.position) {
            Some(b'0') => self.position += 1,
            Some(b'1'..=b'9') => {
                self.position += 1;
                while matches!(self.data.get(self.position), Some(b'0'..=b'9')) {
                    self.position += 1;
                }
            }
            _ => return Err(self.error(NakamaLegacySyntaxReason::InvalidNumber)),
        }
        if self.data.get(self.position) == Some(&b'.') {
            self.position += 1;
            self.digits()?;
        }
        if matches!(self.data.get(self.position), Some(b'e' | b'E')) {
            self.position += 1;
            if matches!(self.data.get(self.position), Some(b'+' | b'-')) {
                self.position += 1;
            }
            self.digits()?;
        }
        Ok(())
    }

    fn digits(&mut self) -> Result<(), NakamaLegacyDecodeError> {
        let start = self.position;
        while matches!(self.data.get(self.position), Some(b'0'..=b'9')) {
            self.position += 1;
        }
        if self.position == start {
            return Err(self.error(NakamaLegacySyntaxReason::InvalidNumber));
        }
        Ok(())
    }
}

struct StringBudget {
    decoded: usize,
    limit: usize,
}

fn decode_string(
    bytes: &[u8],
    budget: &mut StringBudget,
) -> Result<String, NakamaLegacyDecodeError> {
    let mut length = 0_usize;
    walk_string(bytes, |character| {
        length = length
            .checked_add(character.len_utf8())
            .filter(|length| {
                budget
                    .decoded
                    .checked_add(*length)
                    .is_some_and(|sum| sum <= budget.limit)
            })
            .ok_or(NakamaLegacyDecodeError::DecodedStringLimit {
                limit: budget.limit,
            })?;
        Ok(())
    })?;
    let mut output = String::new();
    output
        .try_reserve_exact(length)
        .map_err(|_| NakamaLegacyDecodeError::AllocationFailed)?;
    walk_string(bytes, |character| {
        output.push(character);
        Ok(())
    })?;
    budget.decoded += length;
    Ok(output)
}

fn walk_string(
    bytes: &[u8],
    mut emit: impl FnMut(char) -> Result<(), NakamaLegacyDecodeError>,
) -> Result<(), NakamaLegacyDecodeError> {
    if bytes.len() < 2 || bytes.first() != Some(&b'"') || bytes.last() != Some(&b'"') {
        return Err(NakamaLegacyDecodeError::Syntax {
            offset: 0,
            reason: NakamaLegacySyntaxReason::UnexpectedByte,
        });
    }
    let body = &bytes[1..bytes.len() - 1];
    let mut index = 0;
    while let Some(byte) = body.get(index).copied() {
        if byte == b'\\' {
            index += 1;
            let character = match body.get(index).copied() {
                Some(b'"') => '"',
                Some(b'\\') => '\\',
                Some(b'/') => '/',
                Some(b'b') => '\u{0008}',
                Some(b'f') => '\u{000c}',
                Some(b'n') => '\n',
                Some(b'r') => '\r',
                Some(b't') => '\t',
                Some(b'u') => {
                    let value = hex4(body.get(index + 1..index + 5).unwrap_or(&[]))?;
                    index += 5;
                    if (0xd800..=0xdbff).contains(&value)
                        && body.get(index..index + 2) == Some(b"\\u")
                    {
                        let low = hex4(body.get(index + 2..index + 6).unwrap_or(&[]))?;
                        if (0xdc00..=0xdfff).contains(&low) {
                            index += 6;
                            emit(
                                char::from_u32(0x10000 + ((value - 0xd800) << 10) + low - 0xdc00)
                                    .unwrap_or('\u{fffd}'),
                            )?;
                            continue;
                        }
                    }
                    emit(char::from_u32(value).unwrap_or('\u{fffd}'))?;
                    continue;
                }
                _ => {
                    return Err(NakamaLegacyDecodeError::Syntax {
                        offset: index,
                        reason: NakamaLegacySyntaxReason::InvalidEscape,
                    })
                }
            };
            index += 1;
            emit(character)?;
        } else if byte < 0x80 {
            emit(char::from(byte))?;
            index += 1;
        } else {
            let width = match byte {
                0xc2..=0xdf => 2,
                0xe0..=0xef => 3,
                0xf0..=0xf4 => 4,
                _ => 1,
            };
            let decoded = (width > 1)
                .then(|| body.get(index..index + width))
                .flatten()
                .and_then(|part| std::str::from_utf8(part).ok())
                .and_then(|text| text.chars().next());
            if let Some(character) = decoded {
                emit(character)?;
                index += width;
            } else {
                emit('\u{fffd}')?;
                index += 1;
            }
        }
    }
    Ok(())
}

fn hex4(bytes: &[u8]) -> Result<u32, NakamaLegacyDecodeError> {
    if bytes.len() != 4 {
        return Err(NakamaLegacyDecodeError::Syntax {
            offset: 0,
            reason: NakamaLegacySyntaxReason::InvalidEscape,
        });
    }
    let mut value = 0;
    for byte in bytes {
        let digit = match byte {
            b'0'..=b'9' => u32::from(byte - b'0'),
            b'a'..=b'f' => u32::from(byte - b'a' + 10),
            b'A'..=b'F' => u32::from(byte - b'A' + 10),
            _ => {
                return Err(NakamaLegacyDecodeError::Syntax {
                    offset: 0,
                    reason: NakamaLegacySyntaxReason::InvalidEscape,
                })
            }
        };
        value = value * 16 + digit;
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn limits() -> NakamaLegacyDecodeLimits {
        NakamaLegacyDecodeLimits::new(32768, 32, 1024, 98304).unwrap()
    }

    fn decode(input: &[u8]) -> NakamaLegacyDecodedClaims {
        decode_nakama_legacy_claims(input, limits()).unwrap()
    }

    #[test]
    fn null_and_empty_object_decode_to_fresh_raw_zero_claims() {
        let null = decode(b" \nnull\t ");
        assert!(null.root_was_null());
        assert_eq!(null.claims, NakamaLegacyRawClaims::default());
        let object = decode(b"{}");
        assert!(!object.root_was_null());
        assert_eq!(object.claims, NakamaLegacyRawClaims::default());
        for input in [b"[]".as_slice(), b"0", b"true", br#""text""#] {
            assert!(matches!(
                decode_nakama_legacy_claims(input, limits()),
                Err(NakamaLegacyDecodeError::TypeMismatch { field: None, .. })
            ));
        }
    }

    #[test]
    fn exact_and_folded_tags_follow_occurrence_order_without_uuid_policy() {
        let result = decode(br#"{"tid":"raw","TID":"later","uid":"zero-or-nonuuid","EXP":7,"eXp":8,"iat":-9,"TokenId":"ignored","ISS":"unknown"}"#);
        assert_eq!(result.claims.token_id, "later");
        assert_eq!(result.claims.user_id, "zero-or-nonuuid");
        assert_eq!((result.claims.expires_at, result.claims.issued_at), (8, -9));
        assert_eq!(result.occurrences().len(), 8);
        assert_eq!(result.occurrences()[0].name(), "tid");
        assert_eq!(result.occurrences()[1].name(), "TID");
        assert_eq!(result.occurrences()[6].field(), None);
    }

    #[test]
    fn unicode_fold_only_matches_the_source_ascii_tag_orbit() {
        let result = decode(r#"{"uſn":"long-s","vrſ":{"a":"b"},"UİD":"ignored","uıd":"ignored","ｕｉｄ":"ignored"}"#.as_bytes());
        assert_eq!(result.claims.username, "long-s");
        assert_eq!(result.claims.variables.unwrap().get("a").unwrap(), "b");
        assert_eq!(result.claims.user_id, "");
    }

    #[test]
    fn primitive_null_does_not_overwrite_an_earlier_value() {
        let result = decode(br#"{"tid":"t","tid":null,"uid":"u","UID":null,"usn":"n","usn":null,"exp":12,"exp":null,"iat":13,"iat":null}"#);
        assert_eq!(result.claims.token_id, "t");
        assert_eq!(result.claims.user_id, "u");
        assert_eq!(result.claims.username, "n");
        assert_eq!(
            (result.claims.expires_at, result.claims.issued_at),
            (12, 13)
        );
    }

    #[test]
    fn variables_merge_then_null_clear_and_map_null_entry_is_empty() {
        let merged =
            decode(br#"{"vrs":{"a":"first","b":"keep"},"VRS":{},"vrs":{"a":null,"c":"new"}}"#);
        assert_eq!(
            merged.claims.variables.unwrap(),
            BTreeMap::from([
                ("a".into(), "".into()),
                ("b".into(), "keep".into()),
                ("c".into(), "new".into())
            ])
        );
        let cleared = decode(br#"{"vrs":{"a":"gone"},"vrs":null,"vrs":{"b":"only"}}"#);
        assert_eq!(
            cleared.claims.variables.unwrap(),
            BTreeMap::from([("b".into(), "only".into())])
        );
        assert_eq!(
            decode(br#"{"vrs":{}}"#).claims.variables,
            Some(BTreeMap::new())
        );
        assert_eq!(decode(br#"{"vrs":null}"#).claims.variables, None);
        let case_sensitive = decode(br#"{"vrs":{"":"empty","A":"upper","a":"lower"}}"#);
        assert_eq!(
            case_sensitive.claims.variables.unwrap(),
            BTreeMap::from([
                ("".into(), "empty".into()),
                ("A".into(), "upper".into()),
                ("a".into(), "lower".into()),
            ])
        );
    }

    #[test]
    fn variable_key_replacement_can_collide_and_later_occurrence_wins() {
        let result = decode(br#"{"vrs":{"\ud800":"old","\ufffd":"new","x":"before","x":null}}"#);
        let map = result.claims.variables.unwrap();
        assert_eq!(map.get("�").unwrap(), "new");
        assert_eq!(map.get("x").unwrap(), "");
        assert_eq!(map.len(), 2);
    }

    #[test]
    fn integers_keep_signed_extremes_and_reject_fraction_exponent_and_overflow() {
        let result = decode(br#"{"exp":9223372036854775807,"iat":-9223372036854775808}"#);
        assert_eq!(
            (result.claims.expires_at, result.claims.issued_at),
            (i64::MAX, i64::MIN)
        );
        assert_eq!(decode(br#"{"exp":-0}"#).claims.expires_at, 0);
        for number in ["1.0", "1e0", "9223372036854775808", "-9223372036854775809"] {
            let input = format!("{{\"exp\":{number}}}");
            assert!(matches!(
                decode_nakama_legacy_claims(input.as_bytes(), limits()),
                Err(NakamaLegacyDecodeError::InvalidInt64 {
                    field: NakamaLegacyField::ExpiresAt,
                    ..
                })
            ));
        }
        for input in [
            br#"{"exp":"1"}"#.as_slice(),
            br#"{"iat":true}"#,
            br#"{"vrs":{"a":1}}"#,
            br#"{"tid":[]}"#,
        ] {
            assert!(matches!(
                decode_nakama_legacy_claims(input, limits()),
                Err(NakamaLegacyDecodeError::TypeMismatch { .. })
            ));
        }
    }

    #[test]
    fn unknown_values_keep_original_bytes_and_do_not_convert_large_numbers() {
        let input = br#" { "extra" : [1e999999,{"dup":1,"dup":2}],"extra":null,"tid":"t" } "#;
        let result = decode(input);
        assert_eq!(result.raw_payload(), input);
        assert_eq!(result.raw_key(0).unwrap(), br#""extra""#);
        assert_eq!(
            result.raw_value(0).unwrap(),
            br#"[1e999999,{"dup":1,"dup":2}]"#
        );
        assert_eq!(result.raw_value(1).unwrap(), b"null");
        assert_eq!(result.occurrences()[0].field(), None);
        assert!(result.raw_value(99).is_none());
    }

    #[test]
    fn malformed_unknown_syntax_is_rejected_before_known_type_errors() {
        for input in [
            br#"{"exp":true,"unknown":[01]}"#.as_slice(),
            br#"{"tid":true,"unknown":"\q"}"#,
            br#"{"vrs":1,"unknown":{"a":}}"#,
            br#"{"tid":1} null"#,
        ] {
            assert!(matches!(
                decode_nakama_legacy_claims(input, limits()),
                Err(NakamaLegacyDecodeError::Syntax { .. })
            ));
        }
        for input in [
            br#"{"x":1,}"#.as_slice(),
            b"[1,]",
            b"{",
            b"",
            b"\xef\xbb\xbf{}",
            br#"{"unknown":"\'"}"#,
        ] {
            assert!(matches!(
                decode_nakama_legacy_claims(input, limits()),
                Err(NakamaLegacyDecodeError::Syntax { .. })
            ));
        }
    }

    #[test]
    fn invalid_utf8_is_replaced_once_per_invalid_byte_not_per_lossy_sequence() {
        let input = b"{\"usn\":\"\xe2\x82|\xed\xa0\x80|\xf4\x90\x80\x80|\xc0\xaf|\xc2\xa2\"}";
        let result = decode(input);
        assert_eq!(result.claims.username, "��|���|����|��|¢");
        assert_eq!(result.raw_payload(), input);
        assert_eq!(result.raw_value(0).unwrap(), &input[7..input.len() - 1]);
    }

    #[test]
    fn escaped_surrogate_pairs_and_lone_surrogates_follow_go_replacement() {
        let result = decode(
            br#"{"usn":"\ud83d\ude00|\ud800x|\ud800\ud800|\udc00\ud800|\u0000\b\f\n\r\t\/\"\\"}"#,
        );
        assert_eq!(
            result.claims.username,
            "😀|�x|��|��|\0\u{0008}\u{000c}\n\r\t/\"\\"
        );
        assert_eq!(
            decode(r#"{"usn":"用户<>&  "}"#.as_bytes()).claims.username,
            "用户<>&\u{2028}\u{2029}"
        );
    }

    #[test]
    fn resource_bounds_are_explicit_and_charge_nested_unknown_members() {
        let small = NakamaLegacyDecodeLimits::new(2, 1, 0, 0).unwrap();
        assert!(decode_nakama_legacy_claims(b"{}", small).is_ok());
        assert!(matches!(
            decode_nakama_legacy_claims(b"null", small),
            Err(NakamaLegacyDecodeError::PayloadLimit { .. })
        ));
        let members = NakamaLegacyDecodeLimits::new(100, 8, 2, 100).unwrap();
        assert!(matches!(
            decode_nakama_legacy_claims(br#"{"unknown":[0,1]}"#, members),
            Err(NakamaLegacyDecodeError::MemberLimit { .. })
        ));
        let depth = NakamaLegacyDecodeLimits::new(100, 2, 100, 100).unwrap();
        assert!(decode_nakama_legacy_claims(br#"{"unknown":[]}"#, depth).is_ok());
        assert!(matches!(
            decode_nakama_legacy_claims(br#"{"unknown":[[]]}"#, depth),
            Err(NakamaLegacyDecodeError::DepthLimit { .. })
        ));
        let strings = NakamaLegacyDecodeLimits::new(100, 8, 100, 6).unwrap();
        assert!(decode_nakama_legacy_claims(b"{\"usn\":\"\xff\"}", strings).is_ok());
        let strings = NakamaLegacyDecodeLimits::new(100, 8, 100, 5).unwrap();
        assert!(matches!(
            decode_nakama_legacy_claims(b"{\"usn\":\"\xff\"}", strings),
            Err(NakamaLegacyDecodeError::DecodedStringLimit { .. })
        ));
    }

    #[test]
    fn first_type_error_is_redacted_and_a_later_valid_duplicate_does_not_erase_it() {
        let error =
            decode_nakama_legacy_claims(br#"{"tid":12,"tid":"private-id","exp":true}"#, limits())
                .unwrap_err();
        assert!(matches!(
            error,
            NakamaLegacyDecodeError::TypeMismatch {
                field: Some(NakamaLegacyField::TokenId),
                ..
            }
        ));
        assert!(!format!("{error:?} {error}").contains("private-id"));
        let result = decode(br#"{"tid":"private-id","unknown":"private-value","vrs":{"private-key":"private-value"}}"#);
        let debug = format!("{result:?} {:?}", result.occurrences());
        assert!(!debug.contains("private-id"));
        assert!(!debug.contains("private-value"));
        assert!(!debug.contains("private-key"));
    }

    #[test]
    fn limits_cannot_hide_an_unbounded_payload_or_recursion_policy() {
        for arguments in [
            (0, 1, 1, 1),
            (MAX_LOCAL_PAYLOAD_BYTES + 1, 1, 1, 1),
            (1, 0, 1, 1),
            (1, MAX_LOCAL_DEPTH + 1, 1, 1),
            (1, 1, MAX_LOCAL_MEMBERS + 1, 1),
            (1, 1, 1, MAX_LOCAL_DECODED_STRING_BYTES + 1),
        ] {
            assert_eq!(
                NakamaLegacyDecodeLimits::new(arguments.0, arguments.1, arguments.2, arguments.3)
                    .unwrap_err(),
                NakamaLegacyDecodeError::InvalidLimits
            );
        }
        assert_eq!(
            (
                limits().max_payload_bytes(),
                limits().max_depth(),
                limits().max_members(),
                limits().max_decoded_string_bytes()
            ),
            (32768, 32, 1024, 98304)
        );
    }
}

// Separate untrusted transport/header kernel; legacy six-claim bodies remain unchanged.
#[path = "nakama_legacy_header.rs"]
mod nakama_legacy_header;
pub use nakama_legacy_header::{
    decode_and_validate_nakama_legacy_hs256_header, decode_nakama_legacy_raw_url_segment,
    validate_nakama_legacy_hs256_header, NakamaLegacyHeaderError, NakamaLegacyHs256Header,
    NakamaLegacyRawUrlLimits,
};
