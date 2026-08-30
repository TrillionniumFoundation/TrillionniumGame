#![forbid(unsafe_code)]

use std::env;
use std::io::{self, Read};

const MAX_VALUE_BYTES: usize = 1_048_576;
const SHIFT: [u32; 64] = [
    7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 5, 9, 14, 20, 5, 9,
    14, 20, 5, 9, 14, 20, 5, 9, 14, 20, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4,
    11, 16, 23, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
];
const TABLE: [u32; 64] = [
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
struct PublicContentVersion([u8; 16]);

impl PublicContentVersion {
    fn from_value(value: &[u8]) -> Self {
        Self(md5(value))
    }

    fn lowercase_hex(self) -> String {
        const HEX: &[u8; 16] = b"0123456789abcdef";
        let mut output = String::with_capacity(32);
        for byte in self.0 {
            output.push(char::from(HEX[usize::from(byte >> 4)]));
            output.push(char::from(HEX[usize::from(byte & 0x0f)]));
        }
        output
    }
}

fn main() {
    if let Err(error) = run(env::args().skip(1)) {
        eprintln!("trnm-storage-version: {error}");
        std::process::exit(1);
    }
}

fn run(mut arguments: impl Iterator<Item = String>) -> Result<(), String> {
    let command = arguments.next().unwrap_or_else(|| "version".to_owned());
    if arguments.next().is_some() {
        return Err("only one optional command is accepted".to_owned());
    }
    match command.as_str() {
        "version" => {
            let mut value = Vec::new();
            io::stdin()
                .take((MAX_VALUE_BYTES + 1) as u64)
                .read_to_end(&mut value)
                .map_err(|error| format!("read stdin: {error}"))?;
            if value.len() > MAX_VALUE_BYTES {
                return Err(format!(
                    "value exceeds {MAX_VALUE_BYTES} byte source-candidate limit"
                ));
            }
            println!("{}", PublicContentVersion::from_value(&value).lowercase_hex());
            Ok(())
        }
        "help" | "--help" | "-h" => {
            println!(
                "Read raw storage value bytes from stdin and print the lowercase 32-character public content version.\n\
                 This is an exact compatibility candidate for public version generation, not a security digest,\n\
                 storage engine, OCC implementation or compatibility claim."
            );
            Ok(())
        }
        other => Err(format!("unknown command {other:?}")),
    }
}

fn md5(input: &[u8]) -> [u8; 16] {
    let original_bit_length = u64::try_from(input.len())
        .unwrap_or(u64::MAX)
        .wrapping_mul(8);
    let mut padded = Vec::with_capacity(input.len().saturating_add(72));
    padded.extend_from_slice(input);
    padded.push(0x80);
    while padded.len() % 64 != 56 {
        padded.push(0);
    }
    padded.extend_from_slice(&original_bit_length.to_le_bytes());

    let mut a0 = 0x6745_2301_u32;
    let mut b0 = 0xefcd_ab89_u32;
    let mut c0 = 0x98ba_dcfe_u32;
    let mut d0 = 0x1032_5476_u32;

    for chunk in padded.chunks_exact(64) {
        let mut words = [0_u32; 16];
        for (index, word) in words.iter_mut().enumerate() {
            let offset = index * 4;
            *word = u32::from_le_bytes(
                chunk[offset..offset + 4]
                    .try_into()
                    .expect("four-byte word"),
            );
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
            let next = a
                .wrapping_add(function)
                .wrapping_add(TABLE[index])
                .wrapping_add(words[word_index]);
            a = d;
            d = c;
            c = b;
            b = b.wrapping_add(next.rotate_left(SHIFT[index]));
        }

        a0 = a0.wrapping_add(a);
        b0 = b0.wrapping_add(b);
        c0 = c0.wrapping_add(c);
        d0 = d0.wrapping_add(d);
    }

    let mut output = [0_u8; 16];
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
    fn rfc_1321_vectors_are_exact_lowercase_hex() {
        let vectors = [
            ("", "d41d8cd98f00b204e9800998ecf8427e"),
            ("a", "0cc175b9c0f1b6a831c399e269772661"),
            ("abc", "900150983cd24fb0d6963f7d28e17f72"),
            ("message digest", "f96b697d7cb7938d525a2f31aaf161d0"),
            (
                "abcdefghijklmnopqrstuvwxyz",
                "c3fcd3d76192e4007dfb496cca67e13b",
            ),
            (
                "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
                "d174ab98d277d9f5a5611c2c9f419d9f",
            ),
            (
                "12345678901234567890123456789012345678901234567890123456789012345678901234567890",
                "57edf4a22be3c955ac49da2e2107b67a",
            ),
        ];
        for (value, expected) in vectors {
            assert_eq!(
                PublicContentVersion::from_value(value.as_bytes()).lowercase_hex(),
                expected
            );
        }
    }

    #[test]
    fn binary_and_json_whitespace_change_the_public_version() {
        let compact = PublicContentVersion::from_value(br#"{"a":1}"#);
        let spaced = PublicContentVersion::from_value(br#"{ "a": 1 }"#);
        assert_ne!(compact, spaced);
        assert_ne!(
            PublicContentVersion::from_value(&[0, 1, 2]),
            PublicContentVersion::from_value(&[0, 1, 3])
        );
    }

    #[test]
    fn result_is_always_exactly_32_lowercase_hex_characters() {
        for length in [0, 1, 55, 56, 57, 63, 64, 65, 1_000] {
            let value = vec![0xa5; length];
            let version = PublicContentVersion::from_value(&value).lowercase_hex();
            assert_eq!(version.len(), 32);
            assert!(version
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)));
        }
    }
}
