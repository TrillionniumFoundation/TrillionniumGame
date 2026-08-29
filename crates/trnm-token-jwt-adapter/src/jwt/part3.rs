pub fn verify(
    token: &str,
    keys: &KeyRing,
    profile: &VerificationProfile,
    now_unix_seconds: i64,
) -> Result<VerifiedToken, JwtError> {
    profile.validate()?;
    if token.len() > profile.max_token_bytes {
        return Err(JwtError::TokenTooLarge {
            limit: profile.max_token_bytes,
            actual: token.len(),
        });
    }
    let mut segments = token.split('.');
    let header_segment = segments.next().ok_or(JwtError::SegmentCount)?;
    let payload_segment = segments.next().ok_or(JwtError::SegmentCount)?;
    let signature_segment = segments.next().ok_or(JwtError::SegmentCount)?;
    if segments.next().is_some() {
        return Err(JwtError::SegmentCount);
    }
    if header_segment.is_empty() || payload_segment.is_empty() || signature_segment.is_empty() {
        return Err(JwtError::EmptySegment);
    }

    let header_bytes = base64url::decode(header_segment, profile.max_header_bytes)
        .map_err(JwtError::HeaderBase64)?;
    let header = json::parse(&header_bytes, profile.json_limits).map_err(JwtError::HeaderJson)?;
    let header_object = header.as_object().ok_or(JwtError::HeaderNotObject)?;
    validate_header_fields(header_object, profile)?;
    let algorithm = required_string(header_object, "alg", profile.max_header_bytes)?;
    if algorithm != "HS256" {
        return Err(JwtError::UnsupportedAlgorithm(algorithm.to_owned()));
    }
    if let Some(value) = header_object.get("typ") {
        if value.as_str() != Some("JWT") {
            return Err(JwtError::InvalidTypeHeader);
        }
    }
    let route = parse_route(header_object, profile)?;
    let key = keys.key_for_route(route)?;

    let signature =
        base64url::decode(signature_segment, SIGNATURE_BYTES).map_err(JwtError::SignatureBase64)?;
    if signature.len() != SIGNATURE_BYTES {
        return Err(JwtError::SignatureLength {
            actual: signature.len(),
        });
    }
    let expected = hmac_sha256(
        key.expose(),
        &[header_segment.as_bytes(), b".", payload_segment.as_bytes()],
    );
    if !constant_time_eq(&signature, &expected) {
        return Err(JwtError::SignatureMismatch);
    }

    let payload_bytes = base64url::decode(payload_segment, profile.max_payload_bytes)
        .map_err(JwtError::PayloadBase64)?;
    let claims = json::parse(&payload_bytes, profile.json_limits).map_err(JwtError::PayloadJson)?;
    let claims_object = claims.as_object().ok_or(JwtError::PayloadNotObject)?;
    let validated = validate_claims(claims_object, route, profile, now_unix_seconds)?;

    Ok(VerifiedToken {
        route,
        principal: validated.principal,
        issued_at: validated.issued_at,
        not_before: validated.not_before,
        expires_at: validated.expires_at,
        issuer: validated.issuer,
        audiences: validated.audiences,
        claims,
    })
}

pub fn issue_legacy(
    claims: &JsonValue,
    key: &SecretKey,
    profile: &VerificationProfile,
) -> Result<String, JwtError> {
    profile.validate()?;
    let object = claims.as_object().ok_or(JwtError::PayloadNotObject)?;
    if object.contains_key(&profile.claims.key_epoch) {
        return Err(JwtError::EpochClaimOnLegacyRoute);
    }
    issue_with_route(claims.clone(), TokenRoute::Legacy, key, profile)
}

pub fn issue_epoch(
    claims: &JsonValue,
    epoch: u32,
    key: &SecretKey,
    profile: &VerificationProfile,
) -> Result<String, JwtError> {
    profile.validate()?;
    if epoch == 0 {
        return Err(JwtError::InvalidKeyEpoch);
    }
    let mut object = claims
        .as_object()
        .ok_or(JwtError::PayloadNotObject)?
        .clone();
    if let Some(existing) = object.get(&profile.claims.key_epoch) {
        let payload = existing
            .as_u64()
            .ok_or_else(|| JwtError::InvalidClaimType(profile.claims.key_epoch.clone()))?;
        if payload != u64::from(epoch) {
            return Err(JwtError::EpochClaimMismatch {
                header: epoch,
                payload,
            });
        }
    } else {
        object.insert(
            profile.claims.key_epoch.clone(),
            JsonValue::Unsigned(u64::from(epoch)),
        );
    }
    issue_with_route(
        JsonValue::Object(object),
        TokenRoute::Epoch(epoch),
        key,
        profile,
    )
}

fn issue_with_route(
    claims: JsonValue,
    route: TokenRoute,
    key: &SecretKey,
    profile: &VerificationProfile,
) -> Result<String, JwtError> {
    let mut header = BTreeMap::from([
        ("alg".to_owned(), JsonValue::String("HS256".to_owned())),
        ("typ".to_owned(), JsonValue::String("JWT".to_owned())),
    ]);
    if let TokenRoute::Epoch(epoch) = route {
        header.insert(
            "kid".to_owned(),
            JsonValue::String(format!("{EPOCH_KEY_ID_PREFIX}{epoch}")),
        );
    }
    let header_bytes =
        json::to_canonical_bytes(&JsonValue::Object(header), profile.max_header_bytes)
            .map_err(JwtError::HeaderEncode)?;
    let payload_bytes = json::to_canonical_bytes(&claims, profile.max_payload_bytes)
        .map_err(JwtError::PayloadEncode)?;
    let header_segment = base64url::encode(&header_bytes);
    let payload_segment = base64url::encode(&payload_bytes);
    let signature = hmac_sha256(
        key.expose(),
        &[header_segment.as_bytes(), b".", payload_segment.as_bytes()],
    );
    let signature_segment = base64url::encode(&signature);
    let token = format!("{header_segment}.{payload_segment}.{signature_segment}");
    if token.len() > profile.max_token_bytes {
        return Err(JwtError::TokenTooLarge {
            limit: profile.max_token_bytes,
            actual: token.len(),
        });
    }
    Ok(token)
}

fn validate_header_fields(
    header: &BTreeMap<String, JsonValue>,
    profile: &VerificationProfile,
) -> Result<(), JwtError> {
    if header.contains_key("crit") || header.contains_key("b64") {
        return Err(JwtError::CriticalHeaderForbidden);
    }
    if profile.reject_unknown_header_fields {
        for key in header.keys() {
            if !matches!(key.as_str(), "alg" | "typ" | "kid") {
                return Err(JwtError::UnknownHeaderField(key.clone()));
            }
        }
    }
    if !header.contains_key("alg") {
        return Err(JwtError::AlgorithmMissing);
    }
    Ok(())
}

fn parse_route(
    header: &BTreeMap<String, JsonValue>,
    profile: &VerificationProfile,
) -> Result<TokenRoute, JwtError> {
    match header.get("kid") {
        None if profile.allow_legacy_without_key_id => Ok(TokenRoute::Legacy),
        None => Err(JwtError::LegacyRouteForbidden),
        Some(JsonValue::String(key_id)) => {
            let raw_epoch = key_id
                .strip_prefix(EPOCH_KEY_ID_PREFIX)
                .ok_or(JwtError::InvalidKeyId)?;
            if raw_epoch.is_empty()
                || (raw_epoch.len() > 1 && raw_epoch.starts_with('0'))
                || !raw_epoch.bytes().all(|byte| byte.is_ascii_digit())
            {
                return Err(JwtError::InvalidKeyId);
            }
            let epoch = raw_epoch
                .parse::<u32>()
                .map_err(|_| JwtError::InvalidKeyId)?;
            if epoch == 0 {
                return Err(JwtError::InvalidKeyId);
            }
            Ok(TokenRoute::Epoch(epoch))
        }
        Some(_) => Err(JwtError::InvalidKeyId),
    }
}
