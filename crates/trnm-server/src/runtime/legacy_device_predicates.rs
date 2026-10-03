//! Pure device-input predicates from Nakama d4d92f93; authenticates nothing.
//! Inputs are raw Go string bytes, after an external transport has produced them.
//! The unusual username POSIX class interpretation remains a candidate until
//! the separately frozen Go microoracle is actually executed. No trim or Unicode
//! whitespace predicate is used; source len(string) means UTF-8 byte count.

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DeviceInputError {
    DeviceIdRequired,
    DeviceIdInvalidCharacters,
    DeviceIdInvalidLength,
    UsernameInvalidCharacters,
    UsernameInvalidLength,
}
impl DeviceInputError {
    pub const fn category(self) -> &'static str {
        match self {
            Self::DeviceIdRequired => "id-required",
            Self::DeviceIdInvalidCharacters => "id-characters",
            Self::DeviceIdInvalidLength => "id-length",
            Self::UsernameInvalidCharacters => "username-characters",
            Self::UsernameInvalidLength => "username-length",
        }
    }
    pub const fn message(self) -> &'static str {
        match self {
            Self::DeviceIdRequired => "Device ID is required.",
            Self::DeviceIdInvalidCharacters => {
                "Device ID invalid, no spaces or control characters allowed."
            }
            Self::DeviceIdInvalidLength => "Device ID invalid, must be 10-128 bytes.",
            Self::UsernameInvalidCharacters => {
                "Username invalid, no spaces or control characters allowed."
            }
            Self::UsernameInvalidLength => "Username invalid, must be 1-128 bytes.",
        }
    }
    pub const fn ordinal(self) -> u8 {
        match self {
            Self::DeviceIdRequired => 1,
            Self::DeviceIdInvalidCharacters => 2,
            Self::DeviceIdInvalidLength => 3,
            Self::UsernameInvalidCharacters => 4,
            Self::UsernameInvalidLength => 5,
        }
    }
}

/// Checks ASCII POSIX [:cntrl:] and [:space:] without decoding/normalizing bytes.
/// Invalid UTF-8 bytes cannot be an ASCII class member; Go regexp consumes each
/// invalid byte as RuneError, so they must not accidentally become whitespace.
pub fn device_id_has_invalid_characters(raw: &[u8]) -> bool {
    raw.iter().any(|&byte| byte <= 0x20 || byte == 0x7f)
}

/// Candidate reading of ([[:cntrl:]]|[[\t\n\r\f\v]])+: the second branch
/// contains a literal '[' in its class followed by a literal ']'. The listed
/// controls are already rejected by [:cntrl:], leaving the extra substring [].
/// This static reading is deliberately tested against the actual Go POSIX parser.
pub fn username_has_invalid_characters(raw: &[u8]) -> bool {
    raw.iter().any(|&byte| byte < 0x20 || byte == 0x7f) || raw.windows(2).any(|pair| pair == b"[]")
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct GeneratedUsername([u8; 10]);
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct InvalidGeneratedUsername;
impl GeneratedUsername {
    /// Source generateUsername always chooses exactly ten ASCII alphabetic bytes.
    /// Randomness/generator policy belongs to the caller, not this predicate.
    pub fn new(raw: [u8; 10]) -> Result<Self, InvalidGeneratedUsername> {
        if raw.iter().all(u8::is_ascii_alphabetic) {
            Ok(Self(raw))
        } else {
            Err(InvalidGeneratedUsername)
        }
    }
    pub const fn as_bytes(&self) -> &[u8; 10] {
        &self.0
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ResolvedUsername<'a> {
    Requested(&'a [u8]),
    Generated(GeneratedUsername),
}
impl ResolvedUsername<'_> {
    pub fn as_bytes(&self) -> &[u8] {
        match self {
            Self::Requested(raw) => raw,
            Self::Generated(name) => name.as_bytes(),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ValidatedDeviceInput<'a> {
    pub device_id: &'a [u8],
    pub username: ResolvedUsername<'a>,
    pub create: bool,
}

/// Only validates API input predicates. No database/user/session/JWT result.
/// None models absent Account; Some(empty) models present but empty Account.Id.
/// The generator is called only after ID validation and only for empty username.
/// There is no additional input cap or hidden normalization; transport budgets
/// and its JSON string-decoding policy remain external, explicit caller duties.
pub fn validate_device_input<'a>(
    account_id: Option<&'a [u8]>,
    username: &'a [u8],
    create: Option<bool>,
    generate: impl FnOnce() -> GeneratedUsername,
) -> Result<ValidatedDeviceInput<'a>, DeviceInputError> {
    let id = account_id
        .filter(|raw| !raw.is_empty())
        .ok_or(DeviceInputError::DeviceIdRequired)?;
    if device_id_has_invalid_characters(id) {
        return Err(DeviceInputError::DeviceIdInvalidCharacters);
    }
    if !(10..=128).contains(&id.len()) {
        return Err(DeviceInputError::DeviceIdInvalidLength);
    }
    let username = if username.is_empty() {
        ResolvedUsername::Generated(generate())
    } else {
        if username_has_invalid_characters(username) {
            return Err(DeviceInputError::UsernameInvalidCharacters);
        }
        if username.len() > 128 {
            return Err(DeviceInputError::UsernameInvalidLength);
        }
        ResolvedUsername::Requested(username)
    };
    Ok(ValidatedDeviceInput {
        device_id: id,
        username,
        create: create.unwrap_or(true),
    })
}

/// Raw stored-source record: keeps historical username/device bytes independently
/// from current input checks. This is not a verified database row or principal.
/// In particular VARCHAR(128 characters) usernames may exceed 128 UTF8 bytes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RawStoredDeviceRecord<'a> {
    pub raw_username: &'a [u8],
    pub raw_device_id: Option<&'a [u8]>,
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::Cell;
    fn generated() -> GeneratedUsername {
        GeneratedUsername::new(*b"aBcDeFgHiJ").unwrap()
    }
    #[test]
    fn device_failure_precedence_is_source_order_and_does_not_generate() {
        let calls = Cell::new(0);
        type RejectionVector<'a> = (Option<&'a [u8]>, &'a [u8], DeviceInputError);
        let rows: &[RejectionVector<'_>] = &[
            (None, b"\0[]", DeviceInputError::DeviceIdRequired),
            (Some(b""), b"\0[]", DeviceInputError::DeviceIdRequired),
            (
                Some(b" "),
                b"[]",
                DeviceInputError::DeviceIdInvalidCharacters,
            ),
            (
                Some(b"short"),
                b"\0",
                DeviceInputError::DeviceIdInvalidLength,
            ),
            (
                Some(b"valid-id-0"),
                b"\0[]",
                DeviceInputError::UsernameInvalidCharacters,
            ),
        ];
        for &(id, name, error) in rows {
            assert_eq!(
                validate_device_input(id, name, None, || {
                    calls.set(calls.get() + 1);
                    generated()
                }),
                Err(error)
            );
        }
        assert_eq!(calls.get(), 0);
    }
    #[test]
    fn generated_name_is_ten_ascii_letters_only_when_username_empty() {
        let calls = Cell::new(0);
        let v = validate_device_input(Some(b"valid-id-0"), b"", None, || {
            calls.set(calls.get() + 1);
            generated()
        })
        .unwrap();
        assert_eq!(v.username.as_bytes(), b"aBcDeFgHiJ");
        assert!(v.create);
        assert_eq!(calls.get(), 1);
        let v = validate_device_input(Some(b"valid-id-0"), b" ", Some(false), || {
            panic!("provided name must bypass generator")
        })
        .unwrap();
        assert_eq!(v.username, ResolvedUsername::Requested(b" "));
        assert!(!v.create);
        assert_eq!(
            GeneratedUsername::new(*b"abcdefgh1J"),
            Err(InvalidGeneratedUsername)
        );
    }
    #[test]
    fn byte_lengths_not_unicode_character_counts_or_unicode_space() {
        let id = "é".repeat(5);
        assert!(validate_device_input(
            Some(id.as_bytes()),
            "\u{a0}\u{2003}".as_bytes(),
            None,
            generated
        )
        .is_ok());
        let name = "😀".repeat(32);
        assert_eq!(name.len(), 128);
        assert!(
            validate_device_input(Some(b"valid-id-0"), name.as_bytes(), None, generated).is_ok()
        );
        let long = "😀".repeat(33);
        assert_eq!(
            validate_device_input(Some(b"valid-id-0"), long.as_bytes(), None, generated),
            Err(DeviceInputError::UsernameInvalidLength)
        );
        let long_bad = [vec![b'x'; 129], vec![0]].concat();
        assert_eq!(
            validate_device_input(Some(b"valid-id-0"), &long_bad, None, generated),
            Err(DeviceInputError::UsernameInvalidCharacters)
        );
    }
    #[test]
    fn unusual_username_bracket_candidate_is_distinct_from_device_space() {
        for raw in [b"[".as_slice(), b"]", b"[a]", b" ", b"\\t", b"[[a]]"] {
            assert!(!username_has_invalid_characters(raw));
        }
        for raw in [b"[]".as_slice(), b"x[]x", b"[][]", b"\t", b"\x7f"] {
            assert!(username_has_invalid_characters(raw));
        }
        assert!(device_id_has_invalid_characters(b" "));
        assert!(!device_id_has_invalid_characters(b"[]"));
    }
    #[test]
    fn invalid_utf8_is_kept_as_raw_go_string_domain_not_json_parity() {
        let raw = [0xff, 0x80, 0xc0, b'x', b'x', b'x', b'x', b'x', b'x', b'x'];
        let v = validate_device_input(Some(&raw), &[0xff, 0x80], Some(true), generated).unwrap();
        assert_eq!(v.device_id, &raw);
        assert_eq!(v.username.as_bytes(), &[0xff, 0x80]);
        assert!(std::str::from_utf8(v.device_id).is_err());
    }
    #[test]
    fn raw_historical_record_never_reuses_current_input_validation() {
        let historical = "😀".repeat(128);
        let row = RawStoredDeviceRecord {
            raw_username: historical.as_bytes(),
            raw_device_id: Some(b""),
        };
        assert_eq!(row.raw_username.len(), 512);
        assert_eq!(row.raw_device_id, Some(b"".as_slice()));
        assert_eq!(
            validate_device_input(Some(b"valid-id-0"), row.raw_username, None, generated),
            Err(DeviceInputError::UsernameInvalidLength)
        );
    }
}

/// Custom shares the pinned POSIX classes/username policy, with its own ID
/// bounds and messages. Source: Nakama d4d92f93 api_authenticate.go, Apache-2.0.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CustomInputError {
    IdRequired,
    IdInvalidCharacters,
    IdInvalidLength,
    UsernameInvalidCharacters,
    UsernameInvalidLength,
}
impl CustomInputError {
    pub const fn message(self) -> &'static str {
        match self {
            Self::IdRequired => "Custom ID is required.",
            Self::IdInvalidCharacters => {
                "Custom ID invalid, no spaces or control characters allowed."
            }
            Self::IdInvalidLength => "Custom ID invalid, must be 6-128 bytes.",
            Self::UsernameInvalidCharacters => {
                "Username invalid, no spaces or control characters allowed."
            }
            Self::UsernameInvalidLength => "Username invalid, must be 1-128 bytes.",
        }
    }
}
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ValidatedCustomInput<'a> {
    pub custom_id: &'a [u8],
    pub username: ResolvedUsername<'a>,
    pub create: bool,
}
/// Pure validation only. No caller may obtain a principal or durable outcome.
pub fn validate_custom_input<'a>(
    account_id: Option<&'a [u8]>,
    username: &'a [u8],
    create: Option<bool>,
    generate: impl FnOnce() -> GeneratedUsername,
) -> Result<ValidatedCustomInput<'a>, CustomInputError> {
    let id = account_id
        .filter(|id| !id.is_empty())
        .ok_or(CustomInputError::IdRequired)?;
    if device_id_has_invalid_characters(id) {
        return Err(CustomInputError::IdInvalidCharacters);
    }
    if !(6..=128).contains(&id.len()) {
        return Err(CustomInputError::IdInvalidLength);
    }
    let username = if username.is_empty() {
        ResolvedUsername::Generated(generate())
    } else {
        if username_has_invalid_characters(username) {
            return Err(CustomInputError::UsernameInvalidCharacters);
        }
        if username.len() > 128 {
            return Err(CustomInputError::UsernameInvalidLength);
        }
        ResolvedUsername::Requested(username)
    };
    Ok(ValidatedCustomInput {
        custom_id: id,
        username,
        create: create.unwrap_or(true),
    })
}
