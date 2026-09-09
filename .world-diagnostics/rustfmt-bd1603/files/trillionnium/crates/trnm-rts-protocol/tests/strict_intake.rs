use serde::Deserialize;
use serde_json::json;
use trnm_rts_protocol::{strict, RtsFrameOrder};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Corpus {
    schema: String,
    cases: Vec<Case>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Case {
    id: String,
    raw: String,
    expected: String,
}

fn sample() -> serde_json::Value {
    json!({"intake_contract": strict::INTAKE_CONTRACT, "order": {
        "contract": "trnm_rts_order_protocol_v1", "frame": 1,
        "player_id": "player", "subject_actor_ids": ["hero"],
        "kind": "hold", "source": "local_input"
    }})
}

#[test]
fn shared_raw_corpus_matches_without_losing_duplicate_keys() {
    let corpus: Corpus = serde_json::from_str(include_str!(
        "../../../../docs/protocol/vectors/trnm-rts-order-intake-v1.json"
    ))
    .unwrap();
    assert_eq!(corpus.schema, "trnm_rts_order_intake_vectors_v1");
    assert_eq!(corpus.cases.len(), 114, "incomplete or unexpected corpus");
    for case in corpus.cases {
        let actual = match strict::decode(case.raw.as_bytes()) {
            Ok(_) => "accepted",
            Err(error) => error.code(),
        };
        assert_eq!(actual, case.expected, "{}", case.id);
    }
}

#[test]
fn byte_limit_is_checked_before_json_parse() {
    assert_eq!(
        strict::decode(&vec![b' '; strict::MAX_INPUT_BYTES + 1]),
        Err(strict::IntakeError::ResourceBudgetExceeded)
    );
    let mut input = serde_json::to_vec(&sample()).unwrap();
    input.resize(strict::MAX_INPUT_BYTES, b' ');
    assert!(strict::decode(&input).is_ok());
    input.push(b' ');
    assert_eq!(
        strict::decode(&input),
        Err(strict::IntakeError::ResourceBudgetExceeded)
    );
}

#[test]
fn utf8_and_scalar_roots_fail_closed() {
    for input in [&b"\xff"[..], &b"null"[..], &b"[]"[..], &b"true"[..]] {
        assert_eq!(
            strict::decode(input),
            Err(strict::IntakeError::InvalidEncoding)
        );
    }
}

#[test]
fn limits_apply_to_utf8_bytes_not_unicode_scalar_count() {
    let mut value = sample();
    value["order"]["player_id"] = json!("界".repeat(53));
    assert!(strict::decode(&serde_json::to_vec(&value).unwrap()).is_ok());
    value["order"]["player_id"] = json!("界".repeat(54));
    assert_eq!(
        strict::decode(&serde_json::to_vec(&value).unwrap()),
        Err(strict::IntakeError::ResourceBudgetExceeded)
    );
}

#[test]
fn subject_count_boundary_and_duplicate_identity() {
    let mut value = sample();
    value["order"]["subject_actor_ids"] = json!((0..strict::MAX_SUBJECTS)
        .map(|i| format!("unit-{i}"))
        .collect::<Vec<_>>());
    assert!(strict::decode(&serde_json::to_vec(&value).unwrap()).is_ok());
    value["order"]["subject_actor_ids"] = json!(["hero", "hero"]);
    assert_eq!(
        strict::decode(&serde_json::to_vec(&value).unwrap()),
        Err(strict::IntakeError::DuplicateSubject)
    );
}

#[test]
fn strict_success_preserves_legacy_serialized_order_bytes() {
    let value = sample();
    let legacy: RtsFrameOrder = serde_json::from_value(value["order"].clone()).unwrap();
    let strict = strict::decode(&serde_json::to_vec(&value).unwrap()).unwrap();
    assert_eq!(strict.as_order(), &legacy);
    assert_eq!(
        serde_json::to_vec(strict.as_order()).unwrap(),
        serde_json::to_vec(&legacy).unwrap()
    );
    assert_eq!(strict.into_order(), legacy);
}

#[test]
fn strict_policy_never_silently_changes_legacy_decoder() {
    let mut value = sample();
    value["order"]["future"] = json!(true);
    assert!(serde_json::from_value::<RtsFrameOrder>(value["order"].clone()).is_ok());
    assert_eq!(
        strict::decode(&serde_json::to_vec(&value).unwrap()),
        Err(strict::IntakeError::InvalidEncoding)
    );
}

#[test]
fn diagnostics_do_not_reflect_untrusted_data() {
    let raw = b"{\"intake_contract\":\"secret-not-for-logs\"}";
    let message = strict::decode(raw).unwrap_err().to_string();
    assert_eq!(message, "invalid_encoding");
    assert!(!message.contains("secret"));
}

#[test]
fn enum_fields_require_strings_not_externally_tagged_maps() {
    let corpus: Corpus = serde_json::from_str(include_str!(
        "../../../../docs/protocol/vectors/trnm-rts-order-intake-v1.json"
    ))
    .unwrap();
    let mut tested_kinds = std::collections::BTreeSet::new();
    for case in corpus
        .cases
        .iter()
        .filter(|case| case.id.starts_with("kind-"))
    {
        let mut value: serde_json::Value = serde_json::from_str(&case.raw).unwrap();
        let spelling = value["order"]["kind"].as_str().unwrap().to_owned();
        tested_kinds.insert(spelling.clone());
        let mut tagged = serde_json::Map::new();
        tagged.insert(spelling, serde_json::Value::Null);
        value["order"]["kind"] = serde_json::Value::Object(tagged);
        assert_eq!(
            strict::decode(&serde_json::to_vec(&value).unwrap()),
            Err(strict::IntakeError::InvalidEncoding),
            "{} accepts non-string enum",
            case.id
        );
    }
    assert_eq!(tested_kinds.len(), 28);
    for source in ["local_input", "replay"] {
        let mut value = sample();
        let mut tagged = serde_json::Map::new();
        tagged.insert(source.to_owned(), serde_json::Value::Null);
        value["order"]["source"] = serde_json::Value::Object(tagged);
        assert_eq!(
            strict::decode(&serde_json::to_vec(&value).unwrap()),
            Err(strict::IntakeError::InvalidEncoding)
        );
    }
}
