use std::collections::BTreeMap;

use trnm_token_jwt_adapter_gate::json::JsonValue;
use trnm_token_jwt_adapter_gate::{
    issue_epoch, issue_legacy, ClaimMapping, SecretKey, VerificationProfile,
};

fn claims() -> JsonValue {
    JsonValue::Object(BTreeMap::from([
        ("aud".into(), JsonValue::String("game".into())),
        ("exp".into(), JsonValue::Unsigned(2_000_000_000)),
        ("iat".into(), JsonValue::Unsigned(1_999_999_000)),
        ("iss".into(), JsonValue::String("trillionnium-test".into())),
        ("tid".into(), JsonValue::String("token-vector-1".into())),
        ("uid".into(), JsonValue::String("user-vector-1".into())),
        ("usn".into(), JsonValue::String("alice".into())),
        (
            "vrs".into(),
            JsonValue::Object(BTreeMap::from([
                ("region".into(), JsonValue::String("ca".into())),
                ("tier".into(), JsonValue::String("internal".into())),
            ])),
        ),
    ]))
}

fn main() {
    let profile = VerificationProfile {
        claims: ClaimMapping::uid_legacy(),
        clock_skew_seconds: 0,
        max_lifetime_seconds: Some(3_600),
        ..VerificationProfile::default()
    };
    let key = SecretKey::new(b"0123456789abcdef0123456789abcdef".to_vec()).unwrap();
    let legacy = issue_legacy(&claims(), &key, &profile).unwrap();
    let epoch = issue_epoch(&claims(), 7, &key, &profile).unwrap();
    println!("legacy={legacy}");
    println!("epoch={epoch}");
}
