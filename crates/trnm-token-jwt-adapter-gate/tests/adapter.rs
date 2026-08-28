use std::collections::BTreeMap;

use trnm_token_jwt_adapter_gate::base64url;
use trnm_token_jwt_adapter_gate::json::{
    self, JsonErrorKind, JsonLimits, JsonValue,
};
use trnm_token_jwt_adapter_gate::{
    issue_epoch, issue_legacy, ClaimMapping, JwtError, KeyRing, SecretKey,
    TokenRoute, VerificationProfile,
};

fn key(byte: u8) -> SecretKey {
    SecretKey::new(vec![byte; 32]).unwrap()
}

fn profile() -> VerificationProfile {
    VerificationProfile {
        claims: ClaimMapping::uid_legacy(),
        clock_skew_seconds: 0,
        max_lifetime_seconds: Some(3_600),
        ..VerificationProfile::default()
    }
}

fn claims(iat: u64, exp: u64) -> JsonValue {
    JsonValue::Object(BTreeMap::from([
        (
            "aud".into(),
            JsonValue::Array(vec![
                JsonValue::String("game".into()),
                JsonValue::String("realtime".into()),
            ]),
        ),
        ("exp".into(), JsonValue::Unsigned(exp)),
        ("iat".into(), JsonValue::Unsigned(iat)),
        ("iss".into(), JsonValue::String("issuer".into())),
        ("tid".into(), JsonValue::String("token-1".into())),
        ("uid".into(), JsonValue::String("user-1".into())),
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

fn split(token: &str) -> (&str, &str, &str) {
    let mut segments = token.split('.');
    let result = (
        segments.next().unwrap(),
        segments.next().unwrap(),
        segments.next().unwrap(),
    );
    assert!(segments.next().is_none());
    result
}

#[test]
fn legacy_route_round_trips_projected_principal() {
    let profile = profile();
    let mut ring = KeyRing::new();
    ring.set_legacy_key(key(0x11));
    let token = ring.issue_legacy(&claims(1_000, 2_000), &profile).unwrap();
    let verified = ring.verify(&token, &profile, 1_500).unwrap();
    assert_eq!(verified.route, TokenRoute::Legacy);
    assert_eq!(verified.principal.subject, "user-1");
    assert_eq!(verified.principal.username.as_deref(), Some("alice"));
    assert_eq!(verified.principal.token_id.as_deref(), Some("token-1"));
    assert_eq!(verified.principal.variables["region"], "ca");
    assert_eq!(verified.principal.variables["tier"], "internal");
    assert_eq!(verified.issuer.as_deref(), Some("issuer"));
    assert_eq!(verified.audiences, ["game", "realtime"]);
}

#[test]
fn epoch_route_binds_header_payload_and_historical_key() {
    let profile = profile();
    let mut ring = KeyRing::new();
    ring.set_legacy_key(key(0x10));
    ring.insert_epoch_key(7, key(0x17)).unwrap();
    ring.insert_epoch_key(8, key(0x18)).unwrap();
    ring.set_active_epoch(8).unwrap();

    let historical = issue_epoch(&claims(1_000, 2_000), 7, &key(0x17), &profile).unwrap();
    let current = ring
        .issue_active_epoch(&claims(1_000, 2_000), &profile)
        .unwrap();
    assert_eq!(
        ring.verify(&historical, &profile, 1_500).unwrap().route,
        TokenRoute::Epoch(7)
    );
    assert_eq!(
        ring.verify(&current, &profile, 1_500).unwrap().route,
        TokenRoute::Epoch(8)
    );

    ring.remove_epoch_key(7);
    assert_eq!(
        ring.verify(&historical, &profile, 1_500),
        Err(JwtError::UnknownKeyEpoch(7))
    );
    assert_eq!(
        ring.verify(&current, &profile, 1_500).unwrap().route,
        TokenRoute::Epoch(8)
    );
}

#[test]
fn unknown_epoch_does_not_fall_back_to_legacy_key() {
    let profile = profile();
    let mut ring = KeyRing::new();
    ring.set_legacy_key(key(0x22));
    let unknown = issue_epoch(&claims(1_000, 2_000), 99, &key(0x22), &profile).unwrap();
    assert_eq!(
        ring.verify(&unknown, &profile, 1_500),
        Err(JwtError::UnknownKeyEpoch(99))
    );
}

#[test]
fn issuance_is_deterministic_and_uses_unpadded_segments() {
    let profile = profile();
    let value = claims(1_000, 2_000);
    let first = issue_legacy(&value, &key(0x33), &profile).unwrap();
    let second = issue_legacy(&value, &key(0x33), &profile).unwrap();
    assert_eq!(first, second);
    let (header, payload, signature) = split(&first);
    for segment in [header, payload, signature] {
        assert!(!segment.contains('='));
        assert!(segment
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_')));
    }
    let header = json::parse(
        &base64url::decode(header, 1_024).unwrap(),
        JsonLimits::default(),
    )
    .unwrap();
    assert_eq!(header.as_object().unwrap()["alg"].as_str(), Some("HS256"));
    assert_eq!(header.as_object().unwrap()["typ"].as_str(), Some("JWT"));
}

#[test]
fn tamper_wrong_key_and_noncanonical_signature_are_rejected() {
    let profile = profile();
    let token = issue_legacy(&claims(1_000, 2_000), &key(0x44), &profile).unwrap();
    let mut wrong = KeyRing::new();
    wrong.set_legacy_key(key(0x45));
    assert_eq!(
        wrong.verify(&token, &profile, 1_500),
        Err(JwtError::SignatureMismatch)
    );

    let mut bytes = token.clone().into_bytes();
    let payload_start = bytes.iter().position(|byte| *byte == b'.').unwrap() + 1;
    bytes[payload_start] = if bytes[payload_start] == b'A' { b'B' } else { b'A' };
    let tampered = String::from_utf8(bytes).unwrap();
    let mut correct = KeyRing::new();
    correct.set_legacy_key(key(0x44));
    assert_eq!(
        correct.verify(&tampered, &profile, 1_500),
        Err(JwtError::SignatureMismatch)
    );

    let padded = format!("{token}=");
    assert!(matches!(
        correct.verify(&padded, &profile, 1_500),
        Err(JwtError::SignatureBase64(_))
    ));
}

#[test]
fn algorithm_confusion_unknown_header_and_detached_payload_are_rejected_before_use() {
    let profile = profile();
    let token = issue_legacy(&claims(1_000, 2_000), &key(0x55), &profile).unwrap();
    let (_, payload, signature) = split(&token);
    let mut ring = KeyRing::new();
    ring.set_legacy_key(key(0x55));

    for algorithm in ["none", "HS384", "RS256"] {
        let header = base64url::encode(
            format!(r#"{{"alg":"{algorithm}","typ":"JWT"}}"#).as_bytes(),
        );
        let crafted = format!("{header}.{payload}.{signature}");
        assert_eq!(
            ring.verify(&crafted, &profile, 1_500),
            Err(JwtError::UnsupportedAlgorithm(algorithm.into()))
        );
    }

    let unknown_header = base64url::encode(br#"{"alg":"HS256","typ":"JWT","x":1}"#);
    let crafted = format!("{unknown_header}.{payload}.{signature}");
    assert_eq!(
        ring.verify(&crafted, &profile, 1_500),
        Err(JwtError::UnknownHeaderField("x".into()))
    );

    let detached = base64url::encode(
        br#"{"alg":"HS256","b64":false,"crit":["b64"],"typ":"JWT"}"#,
    );
    let crafted = format!("{detached}.{payload}.{signature}");
    assert_eq!(
        ring.verify(&crafted, &profile, 1_500),
        Err(JwtError::CriticalHeaderForbidden)
    );
}

#[test]
fn exact_expiration_not_before_issued_at_and_lifetime_boundaries_are_enforced() {
    let mut profile = profile();
    profile.clock_skew_seconds = 10;
    let mut ring = KeyRing::new();
    ring.set_legacy_key(key(0x66));

    let token = ring.issue_legacy(&claims(1_000, 2_000), &profile).unwrap();
    assert!(ring.verify(&token, &profile, 2_009).is_ok());
    assert!(matches!(
        ring.verify(&token, &profile, 2_010),
        Err(JwtError::Expired { .. })
    ));

    let mut not_before = claims(1_000, 2_000).as_object().unwrap().clone();
    not_before.insert("nbf".into(), JsonValue::Unsigned(1_600));
    let token = ring
        .issue_legacy(&JsonValue::Object(not_before), &profile)
        .unwrap();
    assert!(matches!(
        ring.verify(&token, &profile, 1_589),
        Err(JwtError::NotYetValid { .. })
    ));
    assert!(ring.verify(&token, &profile, 1_590).is_ok());

    let future = ring
        .issue_legacy(&claims(1_600, 2_000), &profile)
        .unwrap();
    assert!(matches!(
        ring.verify(&future, &profile, 1_589),
        Err(JwtError::IssuedInFuture { .. })
    ));

    profile.max_lifetime_seconds = Some(999);
    assert!(matches!(
        ring.verify(&token, &profile, 1_600),
        Err(JwtError::LifetimeExceeded { .. })
    ));
}

#[test]
fn issuer_audience_subject_username_and_variable_contracts_fail_closed() {
    let mut profile = profile();
    profile.required_issuer = Some("issuer".into());
    profile.required_audience = Some("game".into());
    profile.require_username = true;
    profile.max_variable_value_bytes = 4;
    let mut ring = KeyRing::new();
    ring.set_legacy_key(key(0x77));

    let valid = ring.issue_legacy(&claims(1_000, 2_000), &profile).unwrap();
    assert!(ring.verify(&valid, &profile, 1_500).is_err());

    profile.max_variable_value_bytes = 32;
    assert!(ring.verify(&valid, &profile, 1_500).is_ok());

    let mut missing_user = claims(1_000, 2_000).as_object().unwrap().clone();
    missing_user.remove("usn");
    let token = ring
        .issue_legacy(&JsonValue::Object(missing_user), &profile)
        .unwrap();
    assert_eq!(
        ring.verify(&token, &profile, 1_500),
        Err(JwtError::MissingClaim("usn".into()))
    );

    let mut empty_subject = claims(1_000, 2_000).as_object().unwrap().clone();
    empty_subject.insert("uid".into(), JsonValue::String(String::new()));
    let token = ring
        .issue_legacy(&JsonValue::Object(empty_subject), &profile)
        .unwrap();
    assert_eq!(
        ring.verify(&token, &profile, 1_500),
        Err(JwtError::EmptyClaim("uid".into()))
    );

    let mut wrong_issuer = profile.clone();
    wrong_issuer.required_issuer = Some("other".into());
    assert_eq!(
        ring.verify(&valid, &wrong_issuer, 1_500),
        Err(JwtError::IssuerMismatch)
    );
    let mut wrong_audience = profile.clone();
    wrong_audience.required_audience = Some("other".into());
    assert_eq!(
        ring.verify(&valid, &wrong_audience, 1_500),
        Err(JwtError::AudienceMismatch)
    );
}

#[test]
fn legacy_route_can_be_disabled_without_affecting_epoch_verification() {
    let mut profile = profile();
    profile.allow_legacy_without_key_id = false;
    let legacy = issue_legacy(&claims(1_000, 2_000), &key(0x81), &profile).unwrap();
    let epoch = issue_epoch(&claims(1_000, 2_000), 2, &key(0x82), &profile).unwrap();
    let mut ring = KeyRing::new();
    ring.set_legacy_key(key(0x81));
    ring.insert_epoch_key(2, key(0x82)).unwrap();
    assert_eq!(
        ring.verify(&legacy, &profile, 1_500),
        Err(JwtError::LegacyRouteForbidden)
    );
    assert_eq!(
        ring.verify(&epoch, &profile, 1_500).unwrap().route,
        TokenRoute::Epoch(2)
    );
}

#[test]
fn epoch_issuance_rejects_conflicting_payload_epoch() {
    let profile = profile();
    let mut value = claims(1_000, 2_000).as_object().unwrap().clone();
    value.insert("trnm_kep".into(), JsonValue::Unsigned(8));
    assert_eq!(
        issue_epoch(&JsonValue::Object(value), 7, &key(0x91), &profile),
        Err(JwtError::EpochClaimMismatch {
            header: 7,
            payload: 8
        })
    );
}

#[test]
fn profile_and_key_material_are_validated() {
    assert!(matches!(
        SecretKey::new(vec![0; 15]),
        Err(JwtError::InvalidKeyLength { .. })
    ));
    assert!(matches!(
        SecretKey::new(vec![0; 4_097]),
        Err(JwtError::InvalidKeyLength { .. })
    ));

    let mut invalid = profile();
    invalid.claims.key_epoch = invalid.claims.subject.clone();
    assert_eq!(invalid.validate(), Err(JwtError::InvalidProfile));
    invalid = profile();
    invalid.clock_skew_seconds = -1;
    assert_eq!(invalid.validate(), Err(JwtError::InvalidProfile));
}

#[test]
fn strict_json_rejects_duplicate_keys_floats_and_lone_surrogates() {
    assert!(matches!(
        json::parse(br#"{"uid":"a","uid":"b"}"#, JsonLimits::default()),
        Err(json::JsonError {
            kind: JsonErrorKind::DuplicateKey(_),
            ..
        })
    ));
    assert!(matches!(
        json::parse(b"1.0", JsonLimits::default()),
        Err(json::JsonError {
            kind: JsonErrorKind::FloatForbidden,
            ..
        })
    ));
    assert!(matches!(
        json::parse(br#""\ud800""#, JsonLimits::default()),
        Err(json::JsonError {
            kind: JsonErrorKind::LoneSurrogate,
            ..
        })
    ));
}

#[test]
fn base64url_rejects_padding_noncanonical_bits_and_oversize() {
    assert!(base64url::decode("Zg==", 64).is_err());
    assert!(base64url::decode("Zh", 64).is_err());
    assert!(base64url::decode("Zm9v", 2).is_err());
}

#[test]
fn token_shape_and_size_are_bounded_before_signature_work() {
    let profile = profile();
    let mut ring = KeyRing::new();
    ring.set_legacy_key(key(0xa1));
    for malformed in ["", "a", "a.b", "a.b.c.d", ".b.c", "a..c", "a.b."] {
        assert!(ring.verify(malformed, &profile, 1_500).is_err(), "{malformed}");
    }
    let mut small = profile;
    small.max_token_bytes = 8;
    assert!(matches!(
        ring.issue_legacy(&claims(1_000, 2_000), &small),
        Err(JwtError::TokenTooLarge { .. })
    ));
}
