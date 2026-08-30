use std::collections::BTreeMap;

use trnm_token_jwt_adapter::base64url;
use trnm_token_jwt_adapter::json::JsonValue;
use trnm_token_jwt_adapter::{
    issue_epoch, issue_legacy, verify, JwtError, KeyRing, SecretKey, VerificationProfile,
};

fn claims() -> JsonValue {
    JsonValue::Object(BTreeMap::from([
        ("exp".to_owned(), JsonValue::Unsigned(2_000)),
        ("iat".to_owned(), JsonValue::Unsigned(1_000)),
        ("sub".to_owned(), JsonValue::String("user-1".to_owned())),
    ]))
}

fn key(value: u8) -> SecretKey {
    SecretKey::new(vec![value; 32]).unwrap()
}

fn legacy_fixture() -> (String, KeyRing, VerificationProfile) {
    let profile = VerificationProfile::default();
    let signing_key = key(0x11);
    let token = issue_legacy(&claims(), &signing_key, &profile).unwrap();
    let mut ring = KeyRing::new();
    ring.set_legacy_key(signing_key);
    (token, ring, profile)
}

fn replace_header(token: &str, header: &[u8]) -> String {
    let mut segments = token.split('.');
    let _old_header = segments.next().unwrap();
    let payload = segments.next().unwrap();
    let signature = segments.next().unwrap();
    assert!(segments.next().is_none());
    format!("{}.{}.{}", base64url::encode(header), payload, signature)
}

#[test]
fn algorithm_confusion_and_none_are_rejected_before_claim_use() {
    let (token, ring, profile) = legacy_fixture();
    for header in [
        br#"{"alg":"none","typ":"JWT"}"#.as_slice(),
        br#"{"alg":"RS256","typ":"JWT"}"#.as_slice(),
        br#"{"alg":"HS512","typ":"JWT"}"#.as_slice(),
    ] {
        let changed = replace_header(&token, header);
        assert!(matches!(
            verify(&changed, &ring, &profile, 1_100),
            Err(JwtError::UnsupportedAlgorithm(_))
        ));
    }
}

#[test]
fn unknown_or_malformed_epoch_never_falls_back_to_legacy_key() {
    let (token, ring, profile) = legacy_fixture();
    let unknown = replace_header(
        &token,
        br#"{"alg":"HS256","kid":"trnm-kep-v1:77","typ":"JWT"}"#,
    );
    assert!(matches!(
        verify(&unknown, &ring, &profile, 1_100),
        Err(JwtError::UnknownKeyEpoch(77))
    ));

    for malformed in [
        br#"{"alg":"HS256","kid":"trnm-kep-v1:0","typ":"JWT"}"#.as_slice(),
        br#"{"alg":"HS256","kid":"trnm-kep-v1:01","typ":"JWT"}"#.as_slice(),
        br#"{"alg":"HS256","kid":"legacy","typ":"JWT"}"#.as_slice(),
        br#"{"alg":"HS256","kid":1,"typ":"JWT"}"#.as_slice(),
    ] {
        let changed = replace_header(&token, malformed);
        assert!(matches!(
            verify(&changed, &ring, &profile, 1_100),
            Err(JwtError::InvalidKeyId)
        ));
    }
}

#[test]
fn duplicate_or_critical_header_fields_fail_closed() {
    let (token, ring, profile) = legacy_fixture();
    let duplicate = replace_header(
        &token,
        br#"{"alg":"HS256","alg":"HS256","typ":"JWT"}"#,
    );
    assert!(verify(&duplicate, &ring, &profile, 1_100).is_err());

    for header in [
        br#"{"alg":"HS256","crit":["x"],"typ":"JWT"}"#.as_slice(),
        br#"{"alg":"HS256","b64":false,"typ":"JWT"}"#.as_slice(),
        br#"{"alg":"HS256","typ":"JWT","x":1}"#.as_slice(),
    ] {
        assert!(verify(&replace_header(&token, header), &ring, &profile, 1_100).is_err());
    }
}

#[test]
fn segment_and_signature_lengths_are_exact() {
    let (token, ring, profile) = legacy_fixture();
    assert!(matches!(
        verify("a.b", &ring, &profile, 1_100),
        Err(JwtError::SegmentCount)
    ));
    assert!(matches!(
        verify(&format!("{token}.extra"), &ring, &profile, 1_100),
        Err(JwtError::SegmentCount)
    ));

    let mut segments = token.split('.');
    let header = segments.next().unwrap();
    let payload = segments.next().unwrap();
    let short_signature = base64url::encode(&[0_u8; 31]);
    let short = format!("{header}.{payload}.{short_signature}");
    assert!(matches!(
        verify(&short, &ring, &profile, 1_100),
        Err(JwtError::SignatureLength { actual: 31 })
    ));
}

#[test]
fn signature_tampering_and_cross_key_use_are_rejected() {
    let (token, ring, profile) = legacy_fixture();
    let mut segments = token.split('.');
    let header = segments.next().unwrap();
    let payload = segments.next().unwrap();
    let mut signature = base64url::decode(segments.next().unwrap(), 32).unwrap();
    signature[0] ^= 1;
    let tampered = format!("{header}.{payload}.{}", base64url::encode(&signature));
    assert!(matches!(
        verify(&tampered, &ring, &profile, 1_100),
        Err(JwtError::SignatureMismatch)
    ));

    let mut wrong_ring = KeyRing::new();
    wrong_ring.set_legacy_key(key(0x22));
    assert!(matches!(
        verify(&token, &wrong_ring, &profile, 1_100),
        Err(JwtError::SignatureMismatch)
    ));
}

#[test]
fn legacy_and_epoch_claim_routes_cannot_be_mixed() {
    let profile = VerificationProfile::default();
    let signing_key = key(0x33);
    let mut legacy_with_epoch = claims().as_object().unwrap().clone();
    legacy_with_epoch.insert("trnm_kep".to_owned(), JsonValue::Unsigned(1));
    assert!(matches!(
        issue_legacy(
            &JsonValue::Object(legacy_with_epoch),
            &signing_key,
            &profile
        ),
        Err(JwtError::EpochClaimOnLegacyRoute)
    ));

    let mut wrong_epoch = claims().as_object().unwrap().clone();
    wrong_epoch.insert("trnm_kep".to_owned(), JsonValue::Unsigned(2));
    assert!(matches!(
        issue_epoch(
            &JsonValue::Object(wrong_epoch),
            1,
            &signing_key,
            &profile
        ),
        Err(JwtError::EpochClaimMismatch {
            header: 1,
            payload: 2
        })
    ));
}

#[test]
fn expiration_and_future_issue_time_are_enforced() {
    let profile = VerificationProfile::default();
    let signing_key = key(0x44);
    let token = issue_legacy(&claims(), &signing_key, &profile).unwrap();
    let mut ring = KeyRing::new();
    ring.set_legacy_key(signing_key);

    assert!(matches!(
        verify(&token, &ring, &profile, 2_100),
        Err(JwtError::Expired { .. })
    ));
    assert!(matches!(
        verify(&token, &ring, &profile, 900),
        Err(JwtError::IssuedInFuture { .. })
    ));
}
