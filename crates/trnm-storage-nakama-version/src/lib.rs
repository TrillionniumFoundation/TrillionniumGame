#![forbid(unsafe_code)]
#![deny(missing_debug_implementations)]

//! Nakama-compatible public storage version source candidate.
//!
//! The public `version` is a lowercase MD5 hex string derived from the exact
//! stored value bytes. This compatibility identifier is intentionally distinct
//! from any internal 32-byte integrity digest. MD5 is used here only to match a
//! pinned public compatibility contract, never for authentication, signatures,
//! secret derivation or integrity-security decisions.

use core::fmt;

const ROTATIONS: [u32; 64] = [
    7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9,
    14, 20, 5, 9, 14, 20, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 6, 10, 15,
    21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
];

const ROUND_CONSTANTS: [u32; 64] = [
    0xd76a_a478,
    0xe8c7_b756,
    0x2420_70db,
    0xc1bd_ceee,
    0xf57c_0faf,
    0x4787_c62a,
    0xa830_4613,
    0xfd46_9501,
    0x6980_98d8,
    0x8b44_f7af,
    0xffff_5bb1,
    0x895c_d7be,
    0x6b90_1122,
    0xfd98_7193,
    0xa679_438e,
    0x49b4_0821,
    0xf61e_2562,
    0xc040_b340,
    0x265e_5a51,
    0xe9b6_c7aa,
    0xd62f_105d,
    0x0244_1453,
    0xd8a1_e681,
    0xe7d3_fbc8,
    0x21e1_cde6,
    0xc337_07d6,
    0xf4d5_0d87,
    0x455a_14ed,
    0xa9e3_e905,
    0xfcef_a3f8,
    0x676f_02d9,
    0x8d2a_4c8a,
    0xfffa_3942,
    0x8771_f681,
    0x6d9d_6122,
    0xfde5_380c,
    0xa4be_ea44,
    0x4bde_cfa9,
    0xf6bb_4b60,
    0xbebf_bc70,
    0x289b_7ec6,
    0xeaa1_27fa,
    0xd4ef_3085,
    0x0488_1d05,
    0xd9d4_d039,
    0xe6db_99e5,
    0x1fa2_7cf8,
    0xc4ac_5665,
    0xf429_2244,
    0x432a_ff97,
    0xab94_23a7,
    0xfc93_a039,
    0x655b_59c3,
    0x8f0c_cc92,
    0xffef_f47d,
    0x8584_5dd1,
    0x6fa8_7e4f,
    0xfe2c_e6e0,
    0xa301_4314,
    0x4e08_11a1,
    0xf753_7e82,
    0xbd3a_f235,
    0x2ad7_d2bb,
    0xeb86_d391,
];

#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct PublicStorageVersion([u8; 16]);

impl PublicStorageVersion {
    #[must_use]
    pub fn from_value(value: &[u8]) -> Self {
        Self(md5(value))
    }

    pub fn parse_hex(value: &str) -> Result<Self, VersionError> {
        if value.len() != 32 {
            return Err(VersionError::InvalidLength {
                actual: value.len(),
            });
        }
        let bytes = value.as_bytes();
        let mut output = [0u8; 16];
        for (index, pair) in bytes.chunks_exact(2).enumerate() {
            let high = decode_lower_hex(pair[0]).ok_or(VersionError::InvalidLowerHex)?;
            let low = decode_lower_hex(pair[1]).ok_or(VersionError::InvalidLowerHex)?;
            output[index] = (high << 4) | low;
        }
        Ok(Self(output))
    }

    #[must_use]
    pub fn to_hex(self) -> String {
        const HEX: &[u8; 16] = b"0123456789abcdef";
        let mut output = String::with_capacity(32);
        for byte in self.0 {
            output.push(char::from(HEX[usize::from(byte >> 4)]));
            output.push(char::from(HEX[usize::from(byte & 0x0f)]));
        }
        output
    }

    #[must_use]
    pub const fn as_bytes(&self) -> &[u8; 16] {
        &self.0
    }
}

impl fmt::Display for PublicStorageVersion {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.to_hex())
    }
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub struct ContentIntegrityDigest([u8; 32]);

impl ContentIntegrityDigest {
    #[must_use]
    pub const fn new(value: [u8; 32]) -> Self {
        Self(value)
    }

    #[must_use]
    pub const fn as_bytes(&self) -> &[u8; 32] {
        &self.0
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum WriteCondition {
    /// Empty client version: perform no optimistic-concurrency check.
    Blind,
    /// `*`: create only when no current object exists.
    CreateOnly,
    /// A 32-character lowercase public version: update only on an exact match.
    Exact(PublicStorageVersion),
}

impl WriteCondition {
    pub fn parse_client_version(value: &str) -> Result<Self, VersionError> {
        match value {
            "" => Ok(Self::Blind),
            "*" => Ok(Self::CreateOnly),
            _ => PublicStorageVersion::parse_hex(value).map(Self::Exact),
        }
    }

    #[must_use]
    pub fn allows(self, current: Option<PublicStorageVersion>) -> bool {
        match self {
            Self::Blind => true,
            Self::CreateOnly => current.is_none(),
            Self::Exact(expected) => current == Some(expected),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum VersionError {
    InvalidLength { actual: usize },
    InvalidLowerHex,
}

impl fmt::Display for VersionError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidLength { actual } => {
                write!(formatter, "storage version length {actual}; expected 32")
            }
            Self::InvalidLowerHex => {
                formatter.write_str("storage version must be lowercase hexadecimal")
            }
        }
    }
}

impl std::error::Error for VersionError {}

fn decode_lower_hex(value: u8) -> Option<u8> {
    match value {
        b'0'..=b'9' => Some(value - b'0'),
        b'a'..=b'f' => Some(value - b'a' + 10),
        _ => None,
    }
}

fn md5(input: &[u8]) -> [u8; 16] {
    let bit_length = (input.len() as u64).wrapping_mul(8);
    let mut padded = Vec::with_capacity(input.len().saturating_add(72));
    padded.extend_from_slice(input);
    padded.push(0x80);
    while padded.len() % 64 != 56 {
        padded.push(0);
    }
    padded.extend_from_slice(&bit_length.to_le_bytes());

    let mut a0 = 0x6745_2301u32;
    let mut b0 = 0xefcd_ab89u32;
    let mut c0 = 0x98ba_dcfeu32;
    let mut d0 = 0x1032_5476u32;

    for block in padded.chunks_exact(64) {
        let mut words = [0u32; 16];
        for (index, word) in block.chunks_exact(4).enumerate() {
            words[index] = u32::from_le_bytes(word.try_into().expect("four-byte MD5 word"));
        }

        let mut a = a0;
        let mut b = b0;
        let mut c = c0;
        let mut d = d0;

        for index in 0..64 {
            let (function, word_index) = match index {
                0..=15 => ((b & c) | ((!b) & d), index),
                16..=31 => ((d & b) | ((!d) & c), (5 * index + 1) % 16),
                32..=47 => (b ^ c ^ d, (3 * index + 5) % 16),
                _ => (c ^ (b | (!d)), (7 * index) % 16),
            };
            let next_d = c;
            c = b;
            b = b.wrapping_add(
                a.wrapping_add(function)
                    .wrapping_add(ROUND_CONSTANTS[index])
                    .wrapping_add(words[word_index])
                    .rotate_left(ROTATIONS[index]),
            );
            a = d;
            d = next_d;
        }

        a0 = a0.wrapping_add(a);
        b0 = b0.wrapping_add(b);
        c0 = c0.wrapping_add(c);
        d0 = d0.wrapping_add(d);
        words.fill(0);
    }
    padded.fill(0);

    let mut output = [0u8; 16];
    output[0..4].copy_from_slice(&a0.to_le_bytes());
    output[4..8].copy_from_slice(&b0.to_le_bytes());
    output[8..12].copy_from_slice(&c0.to_le_bytes());
    output[12..16].copy_from_slice(&d0.to_le_bytes());
    output
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rfc1321_vectors_are_exact_lowercase_hex() {
        let vectors = [
            (b"".as_slice(), "d41d8cd98f00b204e9800998ecf8427e"),
            (b"a".as_slice(), "0cc175b9c0f1b6a831c399e269772661"),
            (b"abc".as_slice(), "900150983cd24fb0d6963f7d28e17f72"),
            (
                b"message digest".as_slice(),
                "f96b697d7cb7938d525a2f31aaf161d0",
            ),
            (
                b"abcdefghijklmnopqrstuvwxyz".as_slice(),
                "c3fcd3d76192e4007dfb496cca67e13b",
            ),
        ];
        for (input, expected) in vectors {
            let version = PublicStorageVersion::from_value(input);
            assert_eq!(version.to_hex(), expected);
            assert_eq!(PublicStorageVersion::parse_hex(expected).unwrap(), version);
        }
    }

    #[test]
    fn client_occ_conditions_distinguish_blind_create_and_exact() {
        let current = PublicStorageVersion::from_value(br#"{"value":1}"#);
        assert!(WriteCondition::parse_client_version("")
            .unwrap()
            .allows(None));
        assert!(WriteCondition::parse_client_version("")
            .unwrap()
            .allows(Some(current)));
        assert!(WriteCondition::parse_client_version("*")
            .unwrap()
            .allows(None));
        assert!(!WriteCondition::parse_client_version("*")
            .unwrap()
            .allows(Some(current)));
        assert!(WriteCondition::parse_client_version(&current.to_hex())
            .unwrap()
            .allows(Some(current)));
        assert!(!WriteCondition::parse_client_version(&current.to_hex())
            .unwrap()
            .allows(None));
    }

    #[test]
    fn malformed_or_uppercase_versions_fail_closed() {
        assert_eq!(
            PublicStorageVersion::parse_hex("abc").unwrap_err(),
            VersionError::InvalidLength { actual: 3 }
        );
        assert_eq!(
            PublicStorageVersion::parse_hex("900150983CD24FB0D6963F7D28E17F72").unwrap_err(),
            VersionError::InvalidLowerHex
        );
        assert_eq!(
            WriteCondition::parse_client_version("not-a-version").unwrap_err(),
            VersionError::InvalidLength { actual: 13 }
        );
    }

    #[test]
    fn public_version_and_internal_digest_are_distinct_types() {
        let public = PublicStorageVersion::from_value(b"value");
        let integrity = ContentIntegrityDigest::new([7; 32]);
        assert_eq!(public.as_bytes().len(), 16);
        assert_eq!(integrity.as_bytes().len(), 32);
    }
}
