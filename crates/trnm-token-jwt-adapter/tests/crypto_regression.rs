#[path = "../src/sha256.rs"]
mod sha256;

fn decode_hex(value: &str) -> Vec<u8> {
    assert_eq!(value.len() % 2, 0);
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| {
            let high = (pair[0] as char).to_digit(16).unwrap() as u8;
            let low = (pair[1] as char).to_digit(16).unwrap() as u8;
            (high << 4) | low
        })
        .collect()
}

#[test]
fn million_a_sha256_known_answer() {
    let value = vec![b'a'; 1_000_000];
    assert_eq!(
        sha256::digest(&value).as_slice(),
        decode_hex("cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0")
    );
}

#[test]
fn rfc_4231_long_key_cases() {
    let long_key = vec![0xaa; 131];
    let cases = [
        (
            b"Test Using Larger Than Block-Size Key - Hash Key First".as_slice(),
            "60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54",
        ),
        (
            b"This is a test using a larger than block-size key and a larger than block-size data. The key needs to be hashed before being used by the HMAC algorithm.".as_slice(),
            "9b09ffa71b942fcb27635fbcd5b0e944bfdc63644f0713938a7f51535c3a35e2",
        ),
    ];
    for (message, expected) in cases {
        assert_eq!(
            sha256::hmac_sha256(&long_key, &[message]).as_slice(),
            decode_hex(expected)
        );
    }
}

#[test]
fn sha256_padding_boundaries_match_known_python_hashlib_values() {
    let cases = [
        (55_usize, "9f4390f8d30c2dd92ec9f095b65e2b9ae9b0a925a5258e241c9f1e910f734318"),
        (56_usize, "b35439a4ac6f0948b6d6f9e3c6af0f5f590ce20f1bde7090ef7970686ec6738a"),
        (63_usize, "7d3e74a05d7db15bce4ad9ec0658ea98e3f06eeecf16b4c6fff2da457ddc2f34"),
        (64_usize, "ffe054fe7ae0cb6dc65c3af9b61d5209f439851db43d0ba5997337df154668eb"),
        (65_usize, "635361c48bb9eab14198e76ea8ab7f1a41685d6a1f395399f0c7a1c58b097ec4"),
    ];
    for (length, expected) in cases {
        assert_eq!(
            sha256::digest(&vec![b'a'; length]).as_slice(),
            decode_hex(expected),
            "length={length}"
        );
    }
}

#[test]
fn constant_time_comparison_rejects_every_tested_length_delta() {
    for left_len in [0, 1, 31, 32, 33, 255, 256, 257, 511, 512] {
        for right_len in [0, 1, 31, 32, 33, 255, 256, 257, 511, 512] {
            let left = vec![0_u8; left_len];
            let right = vec![0_u8; right_len];
            assert_eq!(
                sha256::constant_time_eq(&left, &right),
                left_len == right_len,
                "left={left_len} right={right_len}"
            );
        }
    }
}
