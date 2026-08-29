#[cfg(test)]
mod tests {
    use super::*;

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

    fn claims() -> JsonValue {
        JsonValue::Object(BTreeMap::from([
            ("aud".into(), JsonValue::String("game".into())),
            ("exp".into(), JsonValue::Unsigned(2_000)),
            ("iat".into(), JsonValue::Unsigned(1_000)),
            ("iss".into(), JsonValue::String("issuer".into())),
            ("tid".into(), JsonValue::String("token-1".into())),
            ("uid".into(), JsonValue::String("user-1".into())),
            ("usn".into(), JsonValue::String("alice".into())),
            (
                "vrs".into(),
                JsonValue::Object(BTreeMap::from([(
                    "region".into(),
                    JsonValue::String("ca".into()),
                )])),
            ),
        ]))
    }

    #[test]
    fn legacy_and_epoch_routes_round_trip() {
        let profile = profile();
        let mut keys = KeyRing::new();
        keys.set_legacy_key(key(0x11));
        keys.insert_epoch_key(7, key(0x22)).unwrap();
        keys.set_active_epoch(7).unwrap();

        let legacy = keys.issue_legacy(&claims(), &profile).unwrap();
        let verified = keys.verify(&legacy, &profile, 1_500).unwrap();
        assert_eq!(verified.route, TokenRoute::Legacy);
        assert_eq!(verified.principal.subject, "user-1");
        assert_eq!(verified.principal.username.as_deref(), Some("alice"));
        assert_eq!(verified.principal.variables["region"], "ca");

        let epoch = keys.issue_active_epoch(&claims(), &profile).unwrap();
        let verified = keys.verify(&epoch, &profile, 1_500).unwrap();
        assert_eq!(verified.route, TokenRoute::Epoch(7));
        assert_eq!(
            verified
                .claims
                .as_object()
                .unwrap()
                .get("trnm_kep")
                .and_then(JsonValue::as_u64),
            Some(7)
        );
    }

    #[test]
    fn unknown_or_malformed_epoch_never_downgrades_to_legacy_key() {
        let profile = profile();
        let mut keys = KeyRing::new();
        keys.set_legacy_key(key(0x11));
        keys.insert_epoch_key(7, key(0x22)).unwrap();
        let token = issue_epoch(&claims(), 8, &key(0x33), &profile).unwrap();
        assert_eq!(
            keys.verify(&token, &profile, 1_500),
            Err(JwtError::UnknownKeyEpoch(8))
        );

        let valid = issue_epoch(&claims(), 7, keys.epoch_keys.get(&7).unwrap(), &profile).unwrap();
        let mut segments = valid.split('.');
        let header_segment = segments.next().unwrap();
        let payload_segment = segments.next().unwrap();
        let signature_segment = segments.next().unwrap();
        assert!(segments.next().is_none());

        let header_bytes = base64url::decode(header_segment, profile.max_header_bytes).unwrap();
        let header = json::parse(&header_bytes, profile.json_limits).unwrap();
        let mut header_object = header.as_object().unwrap().clone();
        header_object.insert(
            "kid".to_owned(),
            JsonValue::String("malformed-epoch".to_owned()),
        );
        let malformed_header = json::to_canonical_bytes(
            &JsonValue::Object(header_object),
            profile.max_header_bytes,
        )
        .unwrap();
        let malformed = format!(
            "{}.{}.{}",
            base64url::encode(&malformed_header),
            payload_segment,
            signature_segment
        );
        assert_eq!(
            keys.verify(&malformed, &profile, 1_500),
            Err(JwtError::InvalidKeyId)
        );
    }

    #[test]
    fn signature_tamper_and_wrong_key_are_rejected() {
        let profile = profile();
        let token = issue_legacy(&claims(), &key(0x11), &profile).unwrap();
        let mut wrong = KeyRing::new();
        wrong.set_legacy_key(key(0x12));
        assert_eq!(
            wrong.verify(&token, &profile, 1_500),
            Err(JwtError::SignatureMismatch)
        );
        let mut bytes = token.into_bytes();
        let index = bytes.len() - 1;
        bytes[index] = if bytes[index] == b'A' { b'B' } else { b'A' };
        let tampered = String::from_utf8(bytes).unwrap();
        assert!(wrong.verify(&tampered, &profile, 1_500).is_err());
    }

    #[test]
    fn time_issuer_audience_and_lifetime_are_enforced() {
        let mut profile = profile();
        profile.required_issuer = Some("issuer".into());
        profile.required_audience = Some("game".into());
        let token = issue_legacy(&claims(), &key(0x11), &profile).unwrap();
        let mut keys = KeyRing::new();
        keys.set_legacy_key(key(0x11));
        assert!(keys.verify(&token, &profile, 1_500).is_ok());
        assert!(matches!(
            keys.verify(&token, &profile, 2_000),
            Err(JwtError::Expired { .. })
        ));

        let mut wrong_issuer = profile.clone();
        wrong_issuer.required_issuer = Some("other".into());
        assert_eq!(
            keys.verify(&token, &wrong_issuer, 1_500),
            Err(JwtError::IssuerMismatch)
        );
        let mut wrong_audience = profile.clone();
        wrong_audience.required_audience = Some("other".into());
        assert_eq!(
            keys.verify(&token, &wrong_audience, 1_500),
            Err(JwtError::AudienceMismatch)
        );
    }

    #[test]
    fn duplicate_payload_keys_and_unrecognized_headers_are_rejected() {
        let profile = profile();
        let header = base64url::encode(br#"{"alg":"HS256","x":1}"#);
        let payload = base64url::encode(br#"{"uid":"a","uid":"b","iat":1000,"exp":2000}"#);
        let signature = hmac_sha256(
            key(0x11).expose(),
            &[header.as_bytes(), b".", payload.as_bytes()],
        );
        let token = format!("{header}.{payload}.{}", base64url::encode(&signature));
        let mut keys = KeyRing::new();
        keys.set_legacy_key(key(0x11));
        assert!(matches!(
            keys.verify(&token, &profile, 1_500),
            Err(JwtError::UnknownHeaderField(_))
        ));
    }

    #[test]
    fn algorithm_confusion_and_base64_padding_are_rejected() {
        let profile = profile();
        let mut keys = KeyRing::new();
        keys.set_legacy_key(key(0x11));
        for algorithm in ["none", "HS384", "RS256"] {
            let header =
                base64url::encode(format!(r#"{{"alg":"{algorithm}","typ":"JWT"}}"#).as_bytes());
            let payload = base64url::encode(
                json::to_canonical_bytes(&claims(), profile.max_payload_bytes)
                    .unwrap()
                    .as_slice(),
            );
            let signature = hmac_sha256(
                key(0x11).expose(),
                &[header.as_bytes(), b".", payload.as_bytes()],
            );
            let token = format!("{header}.{payload}.{}", base64url::encode(&signature));
            assert_eq!(
                keys.verify(&token, &profile, 1_500),
                Err(JwtError::UnsupportedAlgorithm(algorithm.into()))
            );
        }
        let token = keys.issue_legacy(&claims(), &profile).unwrap();
        let padded = format!("{}=", token);
        assert!(matches!(
            keys.verify(&padded, &profile, 1_500),
            Err(JwtError::SignatureBase64(_))
        ));
    }
}
