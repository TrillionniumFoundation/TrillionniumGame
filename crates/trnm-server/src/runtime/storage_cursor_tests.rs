// Frozen observations from Go 1.26.5 + gofrs/uuid 5.4.0.
// Scratch oracle source SHA256: 3b58c592e5fde6b91e0bdcb3134b52ab95bb2b16a33ceec746d545452a49b53d
// Scratch oracle binary SHA256: f924e0029adfc742f1edc2826e9c5bd156a2c0feb55e6ba34a32435976a3df2b
// These are protocol tests, not accepted Nakama server compatibility evidence.

use super::{
    decode_base64, decode_cursor, encode_base64, encode_cursor, write_bytes, write_common,
    write_message, write_signed, write_struct_descriptor, write_unsigned, Field, MAX_DEPTH,
    MAX_ENCODED_BYTES, MAX_FIELDS, MAX_KEY_BYTES, MAX_MESSAGES,
};
use trnm_contracts::UserId;
use trnm_persistence_pg::StorageListPosition;

fn expected_position(value: &serde_json::Value) -> StorageListPosition {
    let text = value["user_id"].as_str().unwrap().replace('-', "");
    let mut owner = [0_u8; 16];
    for (index, pair) in text.as_bytes().chunks_exact(2).enumerate() {
        owner[index] = u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap();
    }
    StorageListPosition {
        key: value["key"].as_str().unwrap().to_owned(),
        user_id: UserId::new(owner),
        read: i32::try_from(value["read"].as_i64().unwrap()).unwrap(),
    }
}

#[test]
fn go_vectors_decode_and_encoder_matches_fresh_process() {
    let vectors: serde_json::Value = serde_json::from_str(ORACLE).unwrap();
    for vector in vectors.as_array().unwrap() {
        let name = vector["name"].as_str().unwrap();
        let input = vector["cursor"].as_str().unwrap();
        let expected = &vector["expected"];
        if name == "invalid_utf8_domain_residual" {
            // Go string holds invalid UTF-8; this Rust position requires String.
            // This stronger rejection is an explicit candidate difference.
            assert!(expected["accepted"].as_bool().unwrap());
            assert!(decode_cursor(input).is_err());
        } else if expected["accepted"].as_bool().unwrap() {
            let decoded = decode_cursor(input).unwrap_or_else(|()| panic!("Go accepts {name}"));
            let expected = expected_position(expected);
            assert_eq!(decoded, expected, "{name}");
            assert_eq!(
                decode_cursor(&encode_cursor(&decoded).unwrap()).unwrap(),
                expected
            );
            if vector["canonical"].as_bool() == Some(true) {
                // Exact bytes from a fresh Go process are a useful encoder
                // fixture, although Go process history can change type IDs.
                assert_eq!(encode_cursor(&decoded).unwrap(), input, "{name}");
            }
        } else {
            assert!(decode_cursor(input).is_err(), "Go rejects {name}");
        }
    }
}

#[test]
fn all_framed_prefix_truncations_fail_without_panicking() {
    let position = StorageListPosition {
        key: "inventory/剑".to_owned(),
        user_id: UserId::new([7; 16]),
        read: i32::MIN,
    };
    let bytes = decode_base64(&encode_cursor(&position).unwrap()).unwrap();
    for end in 0..bytes.len() {
        assert!(
            decode_cursor(&encode_base64(&bytes[..end])).is_err(),
            "prefix {end}"
        );
    }
}

#[test]
fn unknown_interface_is_an_explicit_subset_limit_even_when_omitted() {
    let fields = [
        Field {
            name: "Key".into(),
            id: 6,
        },
        Field {
            name: "Extension".into(),
            id: 8,
        },
    ];
    let mut stream = Vec::new();
    write_struct_descriptor(&mut stream, 777, "extended", &fields);
    let mut value = Vec::new();
    write_signed(&mut value, 777);
    write_unsigned(&mut value, 0);
    write_message(&mut stream, &value);
    assert!(decode_cursor(&encode_base64(&stream)).is_err());
}

#[test]
fn bounded_keys_and_encoded_cursor_text_fail_closed() {
    let mut position = StorageListPosition {
        key: "k".repeat(MAX_KEY_BYTES),
        user_id: UserId::new([0; 16]),
        read: 2,
    };
    let encoded = encode_cursor(&position).unwrap();
    assert_eq!(decode_cursor(&encoded).unwrap(), position);
    position.key.push('k');
    assert!(encode_cursor(&position).is_err());
    let mut stream = Vec::new();
    write_struct_descriptor(
        &mut stream,
        777,
        "extended",
        &[Field {
            name: "Key".into(),
            id: 6,
        }],
    );
    let mut value = Vec::new();
    write_signed(&mut value, 777);
    write_unsigned(&mut value, 1);
    write_bytes(&mut value, position.key.as_bytes());
    write_unsigned(&mut value, 0);
    write_message(&mut stream, &value);
    assert!(decode_cursor(&encode_base64(&stream)).is_err());
    let padded_with_linebreaks = format!("{encoded}{}", "\n".repeat(MAX_ENCODED_BYTES));
    assert!(decode_cursor(&padded_with_linebreaks).is_err());
}

#[test]
fn type_message_and_descriptor_field_budgets_fail_closed() {
    let mut stream = Vec::new();
    for id in 64..64 + i32::try_from(MAX_MESSAGES).unwrap() {
        write_struct_descriptor(&mut stream, id, "empty", &[]);
    }
    let mut value = Vec::new();
    write_signed(&mut value, 64);
    write_unsigned(&mut value, 0);
    write_message(&mut stream, &value);
    assert!(decode_cursor(&encode_base64(&stream)).is_err());

    let fields = vec![
        Field {
            name: "Key".into(),
            id: 6
        };
        MAX_FIELDS + 1
    ];
    let mut stream = Vec::new();
    write_struct_descriptor(&mut stream, 64, "manyfields", &fields);
    write_message(&mut stream, &value);
    assert!(decode_cursor(&encode_base64(&stream)).is_err());
}

#[test]
fn nested_unknown_type_and_container_work_budgets_fail_closed() {
    let mut stream = Vec::new();
    write_struct_descriptor(
        &mut stream,
        64,
        "outer",
        &[
            Field {
                name: "Key".into(),
                id: 6,
            },
            Field {
                name: "Extension".into(),
                id: 65,
            },
        ],
    );
    for depth in 0..MAX_DEPTH {
        let id = 65 + i32::try_from(depth).unwrap();
        let next = if depth + 1 == MAX_DEPTH { 2 } else { id + 1 };
        write_struct_descriptor(
            &mut stream,
            id,
            "nested",
            &[Field {
                name: "Inner".into(),
                id: next,
            }],
        );
    }
    let mut value = Vec::new();
    write_signed(&mut value, 64);
    write_unsigned(&mut value, 0);
    write_message(&mut stream, &value);
    assert!(decode_cursor(&encode_base64(&stream)).is_err());

    let mut stream = Vec::new();
    write_struct_descriptor(
        &mut stream,
        64,
        "outer",
        &[
            Field {
                name: "Key".into(),
                id: 6,
            },
            Field {
                name: "Extension".into(),
                id: 65,
            },
        ],
    );
    // Slice descriptor followed by a count that cannot fit its remaining work
    // budget. The count is checked before iteration or allocation.
    let mut descriptor = Vec::new();
    write_signed(&mut descriptor, -65);
    write_unsigned(&mut descriptor, 2);
    write_unsigned(&mut descriptor, 1);
    write_common(&mut descriptor, 65, "slice");
    write_unsigned(&mut descriptor, 1);
    write_signed(&mut descriptor, 2);
    write_unsigned(&mut descriptor, 0);
    write_unsigned(&mut descriptor, 0);
    write_message(&mut stream, &descriptor);
    let mut value = Vec::new();
    write_signed(&mut value, 64);
    write_unsigned(&mut value, 2);
    write_unsigned(&mut value, u64::MAX);
    write_message(&mut stream, &value);
    assert!(decode_cursor(&encode_base64(&stream)).is_err());
}

#[test]
fn invalid_gob_integer_widths_and_partial_bytes_fail_closed() {
    for first in 128..=247_u8 {
        let input = encode_base64(&[first]);
        assert!(decode_cursor(&input).is_err());
    }
    for input in ["A", "=", "+A", "/A", "AA A", "AA\tA", "\r\n"] {
        assert!(decode_cursor(input).is_err());
    }
}

#[test]
fn mutation_corpus_is_bounded_and_never_panics() {
    let position = StorageListPosition {
        key: "k".into(),
        user_id: UserId::new([1; 16]),
        read: 2,
    };
    let original = decode_base64(&encode_cursor(&position).unwrap()).unwrap();
    for index in 0..original.len() {
        for mask in [1, 3, 128, 255] {
            let mut bytes = original.clone();
            bytes[index] ^= mask;
            let _ = decode_cursor(&encode_base64(&bytes));
        }
    }
}

const ORACLE: &str = r###"[
  {
    "name": "pinned_0",
    "cursor": "N38DAQENc3RvcmFnZUN1cnNvcgH_gAABAwEDS2V5AQwAAQZVc2VySUQB_4IAAQRSZWFkAQQAAAAQ_4EGAQEEVVVJRAH_ggAAAAP_gAA",
    "expected": {
      "accepted": true,
      "key": "",
      "read": 0,
      "user_id": "00000000-0000-0000-0000-000000000000"
    },
    "canonical": true
  },
  {
    "name": "pinned_1",
    "cursor": "OP-BAwEBDXN0b3JhZ2VDdXJzb3IB_4IAAQMBA0tleQEMAAEGVXNlcklEAf-EAAEEUmVhZAEEAAAAEP-DBgEBBFVVSUQB_4QAAAAD_4IA",
    "expected": {
      "accepted": true,
      "key": "",
      "read": 0,
      "user_id": "00000000-0000-0000-0000-000000000000"
    },
    "canonical": false
  },
  {
    "name": "pinned_2",
    "cursor": "N38DAQENc3RvcmFnZUN1cnNvcgH_gAABAwEDS2V5AQwAAQZVc2VySUQB_4IAAQRSZWFkAQQAAAAQ_4EGAQEEVVVJRAH_ggAAABr_gAEBYQEQEREREREREREREREREREREQECAA",
    "expected": {
      "accepted": true,
      "key": "a",
      "read": 1,
      "user_id": "11111111-1111-1111-1111-111111111111"
    },
    "canonical": true
  },
  {
    "name": "pinned_3",
    "cursor": "OP-BAwEBDXN0b3JhZ2VDdXJzb3IB_4IAAQMBA0tleQEMAAEGVXNlcklEAf-EAAEEUmVhZAEEAAAAEP-DBgEBBFVVSUQB_4QAAAAa_4IBAWEBEBEREREREREREREREREREREBAgA",
    "expected": {
      "accepted": true,
      "key": "a",
      "read": 1,
      "user_id": "11111111-1111-1111-1111-111111111111"
    },
    "canonical": false
  },
  {
    "name": "pinned_4",
    "cursor": "N38DAQENc3RvcmFnZUN1cnNvcgH_gAABAwEDS2V5AQwAAQZVc2VySUQB_4IAAQRSZWFkAQQAAAAQ_4EGAQEEVVVJRAH_ggAAABr_gAEBYQEQIiIiIiIiIiIiIiIiIiIiIgEEAA",
    "expected": {
      "accepted": true,
      "key": "a",
      "read": 2,
      "user_id": "22222222-2222-2222-2222-222222222222"
    },
    "canonical": true
  },
  {
    "name": "pinned_5",
    "cursor": "OP-BAwEBDXN0b3JhZ2VDdXJzb3IB_4IAAQMBA0tleQEMAAEGVXNlcklEAf-EAAEEUmVhZAEEAAAAEP-DBgEBBFVVSUQB_4QAAAAa_4IBAWEBECIiIiIiIiIiIiIiIiIiIiIBBAA",
    "expected": {
      "accepted": true,
      "key": "a",
      "read": 2,
      "user_id": "22222222-2222-2222-2222-222222222222"
    },
    "canonical": false
  },
  {
    "name": "pinned_6",
    "cursor": "N38DAQENc3RvcmFnZUN1cnNvcgH_gAABAwEDS2V5AQwAAQZVc2VySUQB_4IAAQRSZWFkAQQAAAAQ_4EGAQEEVVVJRAH_ggAAACb_gAENaW52ZW50b3J5L-WJkQEQEjRWeJq83vASNFZ4mrze8AEBAA",
    "expected": {
      "accepted": true,
      "key": "inventory/剑",
      "read": -1,
      "user_id": "12345678-9abc-def0-1234-56789abcdef0"
    },
    "canonical": true
  },
  {
    "name": "pinned_7",
    "cursor": "OP-BAwEBDXN0b3JhZ2VDdXJzb3IB_4IAAQMBA0tleQEMAAEGVXNlcklEAf-EAAEEUmVhZAEEAAAAEP-DBgEBBFVVSUQB_4QAAAAm_4IBDWludmVudG9yeS_liZEBEBI0VniavN7wEjRWeJq83vABAQA",
    "expected": {
      "accepted": true,
      "key": "inventory/剑",
      "read": -1,
      "user_id": "12345678-9abc-def0-1234-56789abcdef0"
    },
    "canonical": false
  },
  {
    "name": "pinned_8",
    "cursor": "N38DAQENc3RvcmFnZUN1cnNvcgH_gAABAwEDS2V5AQwAAQZVc2VySUQB_4IAAQRSZWFkAQQAAAAQ_4EGAQEEVVVJRAH_ggAAABP_gAEIbGluZQprZXkC_P____8A",
    "expected": {
      "accepted": true,
      "key": "line\nkey",
      "read": -2147483648,
      "user_id": "00000000-0000-0000-0000-000000000000"
    },
    "canonical": true
  },
  {
    "name": "pinned_9",
    "cursor": "OP-BAwEBDXN0b3JhZ2VDdXJzb3IB_4IAAQMBA0tleQEMAAEGVXNlcklEAf-EAAEEUmVhZAEEAAAAEP-DBgEBBFVVSUQB_4QAAAAT_4IBCGxpbmUKa2V5Avz_____AA",
    "expected": {
      "accepted": true,
      "key": "line\nkey",
      "read": -2147483648,
      "user_id": "00000000-0000-0000-0000-000000000000"
    },
    "canonical": false
  },
  {
    "name": "pinned_10",
    "cursor": "N38DAQENc3RvcmFnZUN1cnNvcgH_gAABAwEDS2V5AQwAAQZVc2VySUQB_4IAAQRSZWFkAQQAAAAQ_4EGAQEEVVVJRAH_ggAAAP4BH_-AAf4BAMOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6kBEP____________________8B_P____4A",
    "expected": {
      "accepted": true,
      "key": "éééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééé",
      "read": 2147483647,
      "user_id": "ffffffff-ffff-ffff-ffff-ffffffffffff"
    },
    "canonical": true
  },
  {
    "name": "pinned_11",
    "cursor": "OP-BAwEBDXN0b3JhZ2VDdXJzb3IB_4IAAQMBA0tleQEMAAEGVXNlcklEAf-EAAEEUmVhZAEEAAAAEP-DBgEBBFVVSUQB_4QAAAD-AR__ggH-AQDDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpw6nDqcOpARD_____________________Afz____-AA",
    "expected": {
      "accepted": true,
      "key": "éééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééééé",
      "read": 2147483647,
      "user_id": "ffffffff-ffff-ffff-ffff-ffffffffffff"
    },
    "canonical": false
  },
  {
    "name": "padding",
    "cursor": "N38DAQENc3RvcmFnZUN1cnNvcgH_gAABAwEDS2V5AQwAAQZVc2VySUQB_4IAAQRSZWFkAQQAAAAQ_4EGAQEEVVVJRAH_ggAAABr_gAEBYQEQEREREREREREREREREREREQECAA=",
    "expected": {
      "accepted": false,
      "error": "illegal base64 data at input byte 134",
      "stage": "base64"
    }
  },
  {
    "name": "line_breaks",
    "cursor": "N38DAQEN\r\nc3RvcmFnZUN1cnNvcgH_gAABAwEDS2V5AQwAAQZVc2VySUQB_4IAAQRSZWFkAQQAAAAQ_4EGAQEEVVVJRAH_ggAAABr_gAEBYQEQEREREREREREREREREREREQECAA",
    "expected": {
      "accepted": true,
      "key": "a",
      "read": 1,
      "user_id": "11111111-1111-1111-1111-111111111111"
    }
  },
  {
    "name": "trailing_junk",
    "cursor": "N38DAQENc3RvcmFnZUN1cnNvcgH_gAABAwEDS2V5AQwAAQZVc2VySUQB_4IAAQRSZWFkAQQAAAAQ_4EGAQEEVVVJRAH_ggAAABr_gAEBYQEQEREREREREREREREREREREQECAGp1bms",
    "expected": {
      "accepted": true,
      "key": "a",
      "read": 1,
      "user_id": "11111111-1111-1111-1111-111111111111"
    }
  },
  {
    "name": "trailing_message",
    "cursor": "N38DAQENc3RvcmFnZUN1cnNvcgH_gAABAwEDS2V5AQwAAQZVc2VySUQB_4IAAQRSZWFkAQQAAAAQ_4EGAQEEVVVJRAH_ggAAABr_gAEBYQEQEREREREREREREREREREREQECADd_AwEBDXN0b3JhZ2VDdXJzb3IB_4AAAQMBA0tleQEMAAEGVXNlcklEAf-CAAEEUmVhZAEEAAAAEP-BBgEBBFVVSUQB_4IAAAAa_4ABAWEBEBEREREREREREREREREREREBAgA",
    "expected": {
      "accepted": true,
      "key": "a",
      "read": 1,
      "user_id": "11111111-1111-1111-1111-111111111111"
    }
  },
  {
    "name": "truncated_gob",
    "cursor": "N38DAQENc3RvcmFnZUN1cnNvcgH_gAABAwEDS2V5AQwAAQZVc2VySUQB_4IAAQRSZWFkAQQAAAAQ_4EGAQEEVVVJRAH_ggAAABr_gAEBYQEQEREREREREREREREREREREQEC",
    "expected": {
      "accepted": false,
      "error": "unexpected EOF",
      "stage": "gob"
    }
  },
  {
    "name": "empty",
    "cursor": "",
    "expected": {
      "accepted": false,
      "error": "EOF",
      "stage": "gob"
    }
  },
  {
    "name": "bad_alphabet",
    "cursor": "!",
    "expected": {
      "accepted": false,
      "error": "illegal base64 data at input byte 0",
      "stage": "base64"
    }
  },
  {
    "name": "empty_struct_payload",
    "cursor": "D_-ZAwEBAXgB_5oAAQAAAAL_mg",
    "expected": {
      "accepted": true,
      "key": "",
      "read": 0,
      "user_id": "00000000-0000-0000-0000-000000000000"
    }
  },
  {
    "name": "only_key_no_common_type",
    "cursor": "EP4FdwMCAQEDS2V5AQwAAAAJ_gV4AQNrZXkA",
    "expected": {
      "accepted": true,
      "key": "key",
      "read": 0,
      "user_id": "00000000-0000-0000-0000-000000000000"
    }
  },
  {
    "name": "duplicate_key_names",
    "cursor": "H_-vAwEBAXgB_7AAAQIBA0tleQEMAAEDS2V5AQwAAAAQ_7ABBWZpcnN0AQRsYXN0AA",
    "expected": {
      "accepted": true,
      "key": "last",
      "read": 0,
      "user_id": "00000000-0000-0000-0000-000000000000"
    }
  },
  {
    "name": "reordered_with_unknown_scalar",
    "cursor": "Ov4HCQMBAQF4Af4HCgABBAEEUmVhZAEEAAEGVW51c2VkAQYAAQZVc2VySUQB_gcMAAEDS2V5AQwAAAAS_gcLBgEBBFVVSUQB_gcMAAAAJP4HCgEJAf4nDwEQEjRWeJq83vASNFZ4mrze8AEG6YeN5o6SAA",
    "expected": {
      "accepted": true,
      "key": "重排",
      "read": -5,
      "user_id": "12345678-9abc-def0-1234-56789abcdef0"
    }
  },
  {
    "name": "unknown_external_4",
    "cursor": "J_4GUwMBAQF4Af4GVAABAgEHSWdub3JlZAH-BlYAAQNLZXkBDAAAAAb-BlUFAAAX_gZUAQ1vcGFxdWUgYmluYXJ5AQJvawA",
    "expected": {
      "accepted": true,
      "key": "ok",
      "read": 0,
      "user_id": "00000000-0000-0000-0000-000000000000"
    }
  },
  {
    "name": "unknown_external_5",
    "cursor": "J_4GUwMBAQF4Af4GVAABAgEHSWdub3JlZAH-BlYAAQNLZXkBDAAAAAb-BlUGAAAX_gZUAQ1vcGFxdWUgYmluYXJ5AQJvawA",
    "expected": {
      "accepted": true,
      "key": "ok",
      "read": 0,
      "user_id": "00000000-0000-0000-0000-000000000000"
    }
  },
  {
    "name": "unknown_external_6",
    "cursor": "J_4GUwMBAQF4Af4GVAABAgEHSWdub3JlZAH-BlYAAQNLZXkBDAAAAAb-BlUHAAAX_gZUAQ1vcGFxdWUgYmluYXJ5AQJvawA",
    "expected": {
      "accepted": true,
      "key": "ok",
      "read": 0,
      "user_id": "00000000-0000-0000-0000-000000000000"
    }
  },
  {
    "name": "unknown_nested_struct",
    "cursor": "KP4GewMBAQF4Af4GfAABAgEHSWdub3JlZAH-Bn4AAQRSZWFkAQQAAAAd_gZ9AwEBAXgB_gZ-AAECAQFYAQwAAQFZAQYAAAAR_gZ8AQEFaW5uZXIBDAABVAA",
    "expected": {
      "accepted": true,
      "key": "",
      "read": 42,
      "user_id": "00000000-0000-0000-0000-000000000000"
    }
  },
  {
    "name": "unknown_array",
    "cursor": "KP4GjwMBAQF4Af4GkAABAgEHSWdub3JlZAH-BpIAAQRSZWFkAQQAAAAT_gaRAQEBAXgB_gaSAAEEAQYAAAz-BpABAw0A_8gBBAA",
    "expected": {
      "accepted": true,
      "key": "",
      "read": 2,
      "user_id": "00000000-0000-0000-0000-000000000000"
    }
  },
  {
    "name": "unknown_slice",
    "cursor": "KP4GowMBAQF4Af4GpAABAgEHSWdub3JlZAH-BqYAAQRSZWFkAQQAAAAR_galAgEBAXgB_gamAAEMAAAT_gakAQIEbGVmdAVyaWdodAECAA",
    "expected": {
      "accepted": true,
      "key": "",
      "read": 1,
      "user_id": "00000000-0000-0000-0000-000000000000"
    }
  },
  {
    "name": "unknown_map",
    "cursor": "KP4GtwMBAQF4Af4GuAABAgEHSWdub3JlZAH-BroAAQRSZWFkAQQAAAAT_ga5BAEBAXgB_ga6AAEMAQQAAA7-BrgBAgFhEgFiEQEAAA",
    "expected": {
      "accepted": true,
      "key": "",
      "read": 0,
      "user_id": "00000000-0000-0000-0000-000000000000"
    }
  },
  {
    "name": "missing_struct_terminator",
    "cursor": "F_-NAwEBAXgB_44AAQEBA0tleQEMAAAABv-OAQJvaw",
    "expected": {
      "accepted": true,
      "key": "ok",
      "read": 0,
      "user_id": "00000000-0000-0000-0000-000000000000"
    }
  },
  {
    "name": "same_message_trailing_junk",
    "cursor": "F_-NAwEBAXgB_44AAQEBA0tleQEMAAAAC_-OAQJvawBqdW5r",
    "expected": {
      "accepted": true,
      "key": "ok",
      "read": 0,
      "user_id": "00000000-0000-0000-0000-000000000000"
    }
  },
  {
    "name": "uuid_binary_no_common_type",
    "cursor": "Hv4FnwMBAQF4Af4FoAABAQEGVXNlcklEAf4FogAAAAb-BaEGAAAW_gWgARASNFZ4mrze8BI0VniavN7wAA",
    "expected": {
      "accepted": true,
      "key": "",
      "read": 0,
      "user_id": "12345678-9abc-def0-1234-56789abcdef0"
    }
  },
  {
    "name": "int32_maximum",
    "cursor": "Gv4FswMBAQF4Af4FtAABAQEEUmVhZAEEAAAACv4FtAH8_____gA",
    "expected": {
      "accepted": true,
      "key": "",
      "read": 2147483647,
      "user_id": "00000000-0000-0000-0000-000000000000"
    }
  },
  {
    "name": "int32_minimum",
    "cursor": "Gv4FswMBAQF4Af4FtAABAQEEUmVhZAEEAAAACv4FtAH8_____wA",
    "expected": {
      "accepted": true,
      "key": "",
      "read": -2147483648,
      "user_id": "00000000-0000-0000-0000-000000000000"
    }
  },
  {
    "name": "known_read_wrong_type_omitted",
    "cursor": "Gv4FxwMBAQF4Af4FyAABAQEEUmVhZAEGAAAABP4FyAA",
    "expected": {
      "accepted": false,
      "error": "gob: wrong type (int32) for received field x.Read",
      "stage": "gob"
    }
  },
  {
    "name": "unknown_only_struct",
    "cursor": "HP4FxwMBAQF4Af4FyAABAQEGVW51c2VkAQwAAAAE_gXIAA",
    "expected": {
      "accepted": false,
      "error": "gob: type mismatch: no fields matched compiling decoder for storageCursor",
      "stage": "gob"
    }
  },
  {
    "name": "unknown_undefined_type_omitted",
    "cursor": "Jv4FxwMBAQF4Af4FyAABAgEDS2V5AQwAAQZVbnVzZWQB_gfOAAAABP4FyAA",
    "expected": {
      "accepted": false,
      "error": "gob: bad data: undefined type <nil>",
      "stage": "gob"
    }
  },
  {
    "name": "known_uuid_gob_type",
    "cursor": "Hv4FxwMBAQF4Af4FyAABAQEGVXNlcklEAf4FygAAABL-BckFAQEEVVVJRAH-BcoAAAAW_gXIARASNFZ4mrze8BI0VniavN7wAA",
    "expected": {
      "accepted": false,
      "error": "gob: wrong type (uuid.UUID) for received field x.UserID",
      "stage": "gob"
    }
  },
  {
    "name": "known_uuid_text_type",
    "cursor": "Hv4FxwMBAQF4Af4FyAABAQEGVXNlcklEAf4FygAAABL-BckHAQEEVVVJRAH-BcoAAAAW_gXIARASNFZ4mrze8BI0VniavN7wAA",
    "expected": {
      "accepted": false,
      "error": "gob: wrong type (uuid.UUID) for received field x.UserID",
      "stage": "gob"
    }
  },
  {
    "name": "known_uuid_short",
    "cursor": "Hv4FxwMBAQF4Af4FyAABAQEGVXNlcklEAf4FygAAABL-BckGAQEEVVVJRAH-BcoAAAAL_gXIAQVzaG9ydAA",
    "expected": {
      "accepted": false,
      "error": "uuid: UUID must be exactly 16 bytes long, got 5 bytes",
      "stage": "gob"
    }
  },
  {
    "name": "read_positive_overflow",
    "cursor": "Gv4FxwMBAQF4Af4FyAABAQEEUmVhZAEEAAAAC_4FyAH7AQAAAAAA",
    "expected": {
      "accepted": false,
      "error": "value for \"Read\" out of range",
      "stage": "gob"
    }
  },
  {
    "name": "read_negative_overflow",
    "cursor": "Gv4FxwMBAQF4Af4FyAABAQEEUmVhZAEEAAAAC_4FyAH7AQAAAAEA",
    "expected": {
      "accepted": false,
      "error": "value for \"Read\" out of range",
      "stage": "gob"
    }
  },
  {
    "name": "empty_remote_field_name",
    "cursor": "Hv4FxwMBAQF4Af4FyAABAgEDS2V5AQwAAQABBAAAAAT-BcgA",
    "expected": {
      "accepted": false,
      "error": "gob: empty name for remote field of type x",
      "stage": "gob"
    }
  },
  {
    "name": "builtin_redefined",
    "cursor": "FQsDAQEBeAEMAAEBAQNLZXkBDAAAAAIMAA",
    "expected": {
      "accepted": false,
      "error": "gob: duplicate type received",
      "stage": "gob"
    }
  },
  {
    "name": "duplicate_type_id",
    "cursor": "Gf4FxwMBAQF4Af4FyAABAQEDS2V5AQwAAAAZ_gXHAwEBAXgB_gXIAAEBAQNLZXkBDAAAAAT-BcgA",
    "expected": {
      "accepted": false,
      "error": "gob: duplicate type received",
      "stage": "gob"
    }
  },
  {
    "name": "struct_field_out_of_range",
    "cursor": "Gf4FxwMBAQF4Af4FyAABAQEDS2V5AQwAAAAK_gXIAgRvb3BzAA",
    "expected": {
      "accepted": false,
      "error": "gob: bad data: field numbers out of bounds",
      "stage": "gob"
    }
  },
  {
    "name": "descriptor_trailing_junk",
    "cursor": "Hf4FxwMBAQF4Af4FyAABAQEDS2V5AQwAAABqdW5rBP4FyAA",
    "expected": {
      "accepted": false,
      "error": "extra data in buffer",
      "stage": "gob"
    }
  },
  {
    "name": "invalid_utf8_domain_residual",
    "cursor": "Gf4FxwMBAQF4Af4FyAABAQEDS2V5AQwAAAAH_gXIAQH_AA",
    "expected": {
      "accepted": true,
      "key": "�",
      "read": 0,
      "user_id": "00000000-0000-0000-0000-000000000000"
    }
  },
  {
    "name": "nonzero_base64_tail_bits",
    "cursor": "N38DAQENc3RvcmFnZUN1cnNvcgH_gAABAwEDS2V5AQwAAQZVc2VySUQB_4IAAQRSZWFkAQQAAAAQ_4EGAQEEVVVJRAH_ggAAABr_gAEBYQEQEREREREREREREREREREREQECAB",
    "expected": {
      "accepted": true,
      "key": "a",
      "read": 1,
      "user_id": "11111111-1111-1111-1111-111111111111"
    }
  }
]"###;
