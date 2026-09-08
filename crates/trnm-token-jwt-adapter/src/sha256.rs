use hmac::{Hmac, KeyInit, Mac};
use sha2::{Digest, Sha256};
use subtle::ConstantTimeEq;

type HmacSha256 = Hmac<Sha256>;

#[must_use]
pub fn digest(input: &[u8]) -> [u8; 32] {
    let output = Sha256::digest(input);
    let mut result = [0_u8; 32];
    result.copy_from_slice(&output);
    result
}

#[must_use]
pub fn hmac_sha256(key: &[u8], chunks: &[&[u8]]) -> [u8; 32] {
    let mut mac = HmacSha256::new_from_slice(key).expect("HMAC-SHA256 accepts every key length");
    for chunk in chunks {
        mac.update(chunk);
    }
    let output = mac.finalize().into_bytes();
    let mut result = [0_u8; 32];
    result.copy_from_slice(&output);
    result
}

#[must_use]
pub fn constant_time_eq(left: &[u8], right: &[u8]) -> bool {
    left.len() == right.len() && bool::from(left.ct_eq(right))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn decode_hex(value: &str) -> Vec<u8> {
        value
            .as_bytes()
            .chunks_exact(2)
            .map(|pair| {
                let high = (pair[0] as char).to_digit(16).expect("hex") as u8;
                let low = (pair[1] as char).to_digit(16).expect("hex") as u8;
                (high << 4) | low
            })
            .collect()
    }

    #[test]
    fn sha256_known_answer() {
        assert_eq!(
            digest(b"abc").as_slice(),
            decode_hex("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        );
    }

    #[test]
    fn hmac_sha256_rfc_4231_case_one() {
        assert_eq!(
            hmac_sha256(&[0x0b; 20], &[b"Hi There"]).as_slice(),
            decode_hex("b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7")
        );
    }

    #[test]
    fn comparison_rejects_full_width_length_differences() {
        for delta in [1_usize, 255, 256, 257, 512] {
            let left = vec![0_u8; 32];
            let right = vec![0_u8; 32 + delta];
            assert!(!constant_time_eq(&left, &right));
            assert!(!constant_time_eq(&right, &left));
        }
        assert!(constant_time_eq(&[7; 32], &[7; 32]));
        assert!(!constant_time_eq(&[7; 32], &[8; 32]));
    }
}
