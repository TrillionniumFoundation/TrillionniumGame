struct ValidatedClaims {
    principal: VerifiedPrincipal,
    issued_at: Option<i64>,
    not_before: Option<i64>,
    expires_at: Option<i64>,
    issuer: Option<String>,
    audiences: Vec<String>,
}

fn validate_claims(
    claims: &BTreeMap<String, JsonValue>,
    route: TokenRoute,
    profile: &VerificationProfile,
    now: i64,
) -> Result<ValidatedClaims, JwtError> {
    validate_epoch_claim(claims, route, profile)?;
    let subject =
        bounded_required_string(claims, &profile.claims.subject, profile.max_subject_bytes)?
            .to_owned();
    let username = match &profile.claims.username {
        Some(name) if profile.require_username => {
            Some(bounded_required_string(claims, name, profile.max_username_bytes)?.to_owned())
        }
        Some(name) => {
            bounded_optional_string(claims, name, profile.max_username_bytes)?.map(str::to_owned)
        }
        None if profile.require_username => return Err(JwtError::InvalidProfile),
        None => None,
    };
    let token_id = match &profile.claims.token_id {
        Some(name) => {
            bounded_optional_string(claims, name, profile.max_token_id_bytes)?.map(str::to_owned)
        }
        None => None,
    };
    let variables = validate_variables(claims, profile)?;

    let expires_at = numeric_date(claims, "exp", profile.require_expiration)?;
    let issued_at = numeric_date(claims, "iat", profile.require_issued_at)?;
    let not_before = numeric_date(claims, "nbf", false)?;
    let skew = i128::from(profile.clock_skew_seconds);
    let now_wide = i128::from(now);
    if let Some(expires_at) = expires_at {
        if now_wide >= i128::from(expires_at) + skew {
            return Err(JwtError::Expired { expires_at, now });
        }
    }
    if let Some(not_before) = not_before {
        if now_wide + skew < i128::from(not_before) {
            return Err(JwtError::NotYetValid { not_before, now });
        }
    }
    if let Some(issued_at) = issued_at {
        if now_wide + skew < i128::from(issued_at) {
            return Err(JwtError::IssuedInFuture { issued_at, now });
        }
    }
    if let (Some(issued_at), Some(expires_at), Some(limit)) =
        (issued_at, expires_at, profile.max_lifetime_seconds)
    {
        if expires_at <= issued_at {
            return Err(JwtError::InvalidLifetime);
        }
        let lifetime =
            u64::try_from(expires_at - issued_at).map_err(|_| JwtError::InvalidLifetime)?;
        if lifetime > limit {
            return Err(JwtError::LifetimeExceeded {
                limit,
                actual: lifetime,
            });
        }
    }

    let issuer = optional_string(claims, "iss")?.map(str::to_owned);
    if let Some(required) = &profile.required_issuer {
        if issuer.as_deref() != Some(required.as_str()) {
            return Err(JwtError::IssuerMismatch);
        }
    }
    let audiences = validate_audience(claims.get("aud"))?;
    if let Some(required) = &profile.required_audience {
        if !audiences.iter().any(|audience| audience == required) {
            return Err(JwtError::AudienceMismatch);
        }
    }

    Ok(ValidatedClaims {
        principal: VerifiedPrincipal {
            subject,
            username,
            variables,
            token_id,
        },
        issued_at,
        not_before,
        expires_at,
        issuer,
        audiences,
    })
}

fn validate_epoch_claim(
    claims: &BTreeMap<String, JsonValue>,
    route: TokenRoute,
    profile: &VerificationProfile,
) -> Result<(), JwtError> {
    let value = claims.get(&profile.claims.key_epoch);
    match route {
        TokenRoute::Legacy => {
            if value.is_some() {
                return Err(JwtError::EpochClaimOnLegacyRoute);
            }
        }
        TokenRoute::Epoch(header) => match value {
            None if profile.require_epoch_claim => return Err(JwtError::EpochClaimMissing),
            None => {}
            Some(value) => {
                let payload = value
                    .as_u64()
                    .ok_or_else(|| JwtError::InvalidClaimType(profile.claims.key_epoch.clone()))?;
                if payload != u64::from(header) {
                    return Err(JwtError::EpochClaimMismatch { header, payload });
                }
            }
        },
    }
    Ok(())
}

fn validate_variables(
    claims: &BTreeMap<String, JsonValue>,
    profile: &VerificationProfile,
) -> Result<BTreeMap<String, String>, JwtError> {
    let Some(name) = &profile.claims.variables else {
        return Ok(BTreeMap::new());
    };
    let Some(value) = claims.get(name) else {
        return Ok(BTreeMap::new());
    };
    let object = value
        .as_object()
        .ok_or_else(|| JwtError::InvalidClaimType(name.clone()))?;
    if object.len() > profile.max_variables {
        return Err(JwtError::VariableCountExceeded {
            limit: profile.max_variables,
        });
    }
    let mut variables = BTreeMap::new();
    for (key, value) in object {
        if key.is_empty() || key.len() > profile.max_variable_key_bytes || value.as_str().is_none()
        {
            return Err(JwtError::InvalidVariableValue(key.clone()));
        }
        let value = value.as_str().expect("checked above");
        if value.len() > profile.max_variable_value_bytes {
            return Err(JwtError::InvalidVariableValue(key.clone()));
        }
        variables.insert(key.clone(), value.to_owned());
    }
    Ok(variables)
}

fn validate_audience(value: Option<&JsonValue>) -> Result<Vec<String>, JwtError> {
    let Some(value) = value else {
        return Ok(Vec::new());
    };
    let values: Vec<&str> = match value {
        JsonValue::String(value) => vec![value.as_str()],
        JsonValue::Array(values) => values
            .iter()
            .map(JsonValue::as_str)
            .collect::<Option<Vec<_>>>()
            .ok_or(JwtError::InvalidAudience)?,
        _ => return Err(JwtError::InvalidAudience),
    };
    if values.iter().any(|value| value.is_empty()) {
        return Err(JwtError::InvalidAudience);
    }
    let mut seen = BTreeSet::new();
    for value in &values {
        if !seen.insert(*value) {
            return Err(JwtError::InvalidAudience);
        }
    }
    Ok(values.into_iter().map(str::to_owned).collect())
}

fn numeric_date(
    claims: &BTreeMap<String, JsonValue>,
    name: &str,
    required: bool,
) -> Result<Option<i64>, JwtError> {
    match claims.get(name) {
        None if required => Err(JwtError::MissingClaim(name.to_owned())),
        None => Ok(None),
        Some(value) => value
            .as_i64()
            .filter(|value| *value >= 0)
            .map(Some)
            .ok_or_else(|| JwtError::InvalidNumericDate(name.to_owned())),
    }
}

fn bounded_required_string<'a>(
    object: &'a BTreeMap<String, JsonValue>,
    name: &str,
    limit: usize,
) -> Result<&'a str, JwtError> {
    let value = required_string(object, name, limit)?;
    if value.is_empty() {
        return Err(JwtError::EmptyClaim(name.to_owned()));
    }
    Ok(value)
}

fn bounded_optional_string<'a>(
    object: &'a BTreeMap<String, JsonValue>,
    name: &str,
    limit: usize,
) -> Result<Option<&'a str>, JwtError> {
    match object.get(name) {
        None => Ok(None),
        Some(value) => {
            let value = value
                .as_str()
                .ok_or_else(|| JwtError::InvalidClaimType(name.to_owned()))?;
            if value.is_empty() {
                return Err(JwtError::EmptyClaim(name.to_owned()));
            }
            if value.len() > limit {
                return Err(JwtError::ClaimLengthExceeded {
                    claim: name.to_owned(),
                    limit,
                });
            }
            Ok(Some(value))
        }
    }
}

fn required_string<'a>(
    object: &'a BTreeMap<String, JsonValue>,
    name: &str,
    limit: usize,
) -> Result<&'a str, JwtError> {
    let value = object
        .get(name)
        .ok_or_else(|| JwtError::MissingClaim(name.to_owned()))?
        .as_str()
        .ok_or_else(|| JwtError::InvalidClaimType(name.to_owned()))?;
    if value.len() > limit {
        return Err(JwtError::ClaimLengthExceeded {
            claim: name.to_owned(),
            limit,
        });
    }
    Ok(value)
}

fn optional_string<'a>(
    object: &'a BTreeMap<String, JsonValue>,
    name: &str,
) -> Result<Option<&'a str>, JwtError> {
    match object.get(name) {
        None => Ok(None),
        Some(value) => value
            .as_str()
            .map(Some)
            .ok_or_else(|| JwtError::InvalidClaimType(name.to_owned())),
    }
}
