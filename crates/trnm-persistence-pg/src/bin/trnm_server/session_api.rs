use std::time::{SystemTime, UNIX_EPOCH};

use trnm_contracts::{DomainError, RetryClass, StableCode};
use trnm_persistence_pg::{
    RefreshRotationOutcome, RefreshTokenCredential, RotateRefreshToken, SessionFamilyRecord,
};
use trnm_session_core::RevocationReason;

use super::app::Repository;
use super::auth::{parse_refresh_credential, AccessTokenVerifier, SessionPrincipal};
use super::codec::encode_hex;
use super::error::InputError;
use super::http::{Request, Response};
use super::json::Object;

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct SessionApiMetrics {
    pub access_verified: u64,
    pub access_rejected: u64,
    pub refresh_rotated: u64,
    pub refresh_replay_revoked: u64,
    pub logout_revoked: u64,
}

#[derive(Debug)]
pub enum SessionError {
    Input(InputError),
    Domain(DomainError),
}

impl From<InputError> for SessionError {
    fn from(value: InputError) -> Self {
        Self::Input(value)
    }
}

impl From<DomainError> for SessionError {
    fn from(value: DomainError) -> Self {
        Self::Domain(value)
    }
}

#[derive(Debug, Default)]
pub struct SessionApi {
    verifier: Option<AccessTokenVerifier>,
    metrics: SessionApiMetrics,
}

impl SessionApi {
    pub fn configure(&mut self, verifier: AccessTokenVerifier) {
        self.verifier = Some(verifier);
    }

    #[must_use]
    pub const fn metrics(&self) -> SessionApiMetrics {
        self.metrics
    }

    pub fn handle<R: Repository>(
        &mut self,
        repository: &mut R,
        request: &Request,
    ) -> Result<Response, SessionError> {
        match (request.method.as_str(), request.target.as_str()) {
            ("GET", "/v1/session/me") => self.me(repository, request),
            ("POST", "/v1/session/refresh") => self.refresh(repository, request),
            ("POST", "/v1/session/logout") => self.logout(repository, request),
            _ => Err(session_unimplemented().into()),
        }
    }

    fn me<R: Repository>(
        &mut self,
        repository: &mut R,
        request: &Request,
    ) -> Result<Response, SessionError> {
        require_empty_body(request)?;
        let principal = self.authenticate(repository, request)?;
        Ok(Response::json(
            200,
            format!(
                "{{\"user_id\":\"{}\",\"session_family_id\":\"{}\",\"generation\":{},\"expires_at_unix_seconds\":{}}}",
                encode_hex(principal.user.as_bytes()),
                encode_hex(principal.family.as_bytes()),
                principal.generation,
                principal.expires_at_unix_seconds,
            ),
        ))
    }

    fn refresh<R: Repository>(
        &mut self,
        repository: &mut R,
        request: &Request,
    ) -> Result<Response, SessionError> {
        self.require_configured()?;
        require_json(request)?;
        let object = Object::parse(&request.body)?;
        object.require_exact_keys(&[
            "presented_refresh_credential",
            "replacement_refresh_credential",
        ])?;
        let presented = parse_refresh_credential(object.string("presented_refresh_credential")?)?;
        let replacement =
            parse_refresh_credential(object.string("replacement_refresh_credential")?)?;
        let request = RotateRefreshToken {
            presented: RefreshTokenCredential {
                id: presented.id,
                digest: presented.digest,
            },
            replacement: RefreshTokenCredential {
                id: replacement.id,
                digest: replacement.digest,
            },
            rotated_at_ms: now_millis()?,
        };
        match repository.rotate_refresh_token(&request)? {
            RefreshRotationOutcome::Rotated(record) => {
                increment(&mut self.metrics.refresh_rotated);
                Ok(session_record_response(200, "rotated", record))
            }
            RefreshRotationOutcome::ReplayRevoked(_) => {
                increment(&mut self.metrics.refresh_replay_revoked);
                Err(session_unauthenticated().into())
            }
        }
    }

    fn logout<R: Repository>(
        &mut self,
        repository: &mut R,
        request: &Request,
    ) -> Result<Response, SessionError> {
        require_empty_body(request)?;
        let principal = self.authenticate(repository, request)?;
        let record = repository.revoke_session_family(
            principal.family,
            principal.user,
            RevocationReason::Logout,
            now_millis()?,
        )?;
        increment(&mut self.metrics.logout_revoked);
        Ok(session_record_response(200, "revoked", record))
    }

    fn authenticate<R: Repository>(
        &mut self,
        repository: &mut R,
        request: &Request,
    ) -> Result<SessionPrincipal, SessionError> {
        let result = (|| {
            let verifier = self.verifier.as_ref().ok_or_else(session_unimplemented)?;
            let principal =
                verifier.verify_bearer(request.header("authorization"), now_unix_seconds()?)?;
            repository.verify_access_session(
                principal.family,
                principal.user,
                principal.generation,
            )?;
            Ok::<SessionPrincipal, DomainError>(principal)
        })();
        match result {
            Ok(principal) => {
                increment(&mut self.metrics.access_verified);
                Ok(principal)
            }
            Err(error) => {
                increment(&mut self.metrics.access_rejected);
                Err(error.into())
            }
        }
    }

    fn require_configured(&self) -> Result<(), DomainError> {
        self.verifier
            .as_ref()
            .map(|_| ())
            .ok_or_else(session_unimplemented)
    }
}

fn session_record_response(status: u16, outcome: &str, record: SessionFamilyRecord) -> Response {
    Response::json(
        status,
        format!(
            "{{\"outcome\":\"{}\",\"user_id\":\"{}\",\"session_family_id\":\"{}\",\"generation\":{},\"active\":{}}}",
            outcome,
            encode_hex(record.user.as_bytes()),
            encode_hex(record.family.as_bytes()),
            record.generation,
            record.active_token.is_some() && record.revoked_reason.is_none(),
        ),
    )
}

fn require_empty_body(request: &Request) -> Result<(), InputError> {
    if request.body.is_empty() {
        Ok(())
    } else {
        Err(InputError::new("session_body_must_be_empty"))
    }
}

fn require_json(request: &Request) -> Result<(), InputError> {
    if request.header("content-type").is_some_and(|value| {
        value
            .split(';')
            .next()
            .is_some_and(|media_type| media_type.trim().eq_ignore_ascii_case("application/json"))
    }) {
        Ok(())
    } else {
        Err(InputError::new("session_json_content_type_required"))
    }
}

fn now_unix_seconds() -> Result<i64, DomainError> {
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| system_clock_error())?;
    i64::try_from(duration.as_secs()).map_err(|_| system_clock_error())
}

fn now_millis() -> Result<u64, DomainError> {
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| system_clock_error())?;
    u64::try_from(duration.as_millis()).map_err(|_| system_clock_error())
}

fn increment(value: &mut u64) {
    *value = value.saturating_add(1);
}

const fn session_unimplemented() -> DomainError {
    DomainError::new(
        StableCode::Unimplemented,
        "session_authentication_not_configured",
        RetryClass::Never,
    )
}

const fn session_unauthenticated() -> DomainError {
    DomainError::new(
        StableCode::Unauthenticated,
        "session_authentication_failed",
        RetryClass::Never,
    )
}

const fn system_clock_error() -> DomainError {
    DomainError::new(
        StableCode::Internal,
        "system_clock_invalid",
        RetryClass::Never,
    )
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use trnm_contracts::{Digest32, SessionFamilyId, UserId};
    use trnm_persistence_pg::{CommitOutcome, CommitRequest, EntityHead, EntityId};
    use trnm_token_jwt_adapter::json::JsonValue;
    use trnm_token_jwt_adapter::{KeyRing, SecretKey, VerificationProfile};

    use super::*;

    const KEY: &[u8] = b"0123456789abcdef0123456789abcdef";
    const ISSUER: &str = "https://identity.test";
    const AUDIENCE: &str = "trillionnium-game";
    const EPOCH: u32 = 7;

    #[derive(Debug)]
    struct SessionRepository {
        record: SessionFamilyRecord,
        replay: bool,
    }

    impl Repository for SessionRepository {
        fn bootstrap_entity(
            &mut self,
            _entity: EntityId,
            _authority_generation: u64,
            _state: Digest32,
            _updated_at_ms: u64,
        ) -> Result<EntityHead, DomainError> {
            Err(session_unimplemented())
        }

        fn commit_command(
            &mut self,
            _request: &CommitRequest,
        ) -> Result<CommitOutcome, DomainError> {
            Err(session_unimplemented())
        }

        fn verify_access_session(
            &mut self,
            family: SessionFamilyId,
            user: UserId,
            generation: u64,
        ) -> Result<SessionFamilyRecord, DomainError> {
            if self.record.family == family
                && self.record.user == user
                && self.record.generation == generation
                && self.record.active_token.is_some()
                && self.record.revoked_reason.is_none()
            {
                Ok(self.record)
            } else {
                Err(session_unauthenticated())
            }
        }

        fn rotate_refresh_token(
            &mut self,
            request: &RotateRefreshToken,
        ) -> Result<RefreshRotationOutcome, DomainError> {
            if self.replay {
                let revoked = SessionFamilyRecord {
                    active_token: None,
                    revoked_reason: Some(RevocationReason::RefreshReplay),
                    updated_at_ms: request.rotated_at_ms,
                    ..self.record
                };
                self.record = revoked;
                Ok(RefreshRotationOutcome::ReplayRevoked(revoked))
            } else {
                let rotated = SessionFamilyRecord {
                    generation: self.record.generation + 1,
                    active_token: Some(request.replacement.id),
                    updated_at_ms: request.rotated_at_ms,
                    ..self.record
                };
                self.record = rotated;
                Ok(RefreshRotationOutcome::Rotated(rotated))
            }
        }

        fn revoke_session_family(
            &mut self,
            family: SessionFamilyId,
            user: UserId,
            reason: RevocationReason,
            revoked_at_ms: u64,
        ) -> Result<SessionFamilyRecord, DomainError> {
            if self.record.family != family || self.record.user != user {
                return Err(session_unauthenticated());
            }
            let revoked = SessionFamilyRecord {
                active_token: None,
                revoked_reason: Some(reason),
                updated_at_ms: revoked_at_ms,
                ..self.record
            };
            self.record = revoked;
            Ok(revoked)
        }
    }

    fn repository() -> SessionRepository {
        SessionRepository {
            record: SessionFamilyRecord {
                family: SessionFamilyId::new([0x33; 16]),
                user: UserId::new([0x11; 16]),
                generation: 4,
                active_token: Some(trnm_contracts::RefreshTokenId::new([0x44; 16])),
                revoked_reason: None,
                created_at_ms: 1,
                updated_at_ms: 1,
            },
            replay: false,
        }
    }

    fn verifier() -> AccessTokenVerifier {
        AccessTokenVerifier::from_epoch_key(
            ISSUER.to_owned(),
            AUDIENCE.to_owned(),
            EPOCH,
            KEY.to_vec(),
        )
        .unwrap()
    }

    fn bearer() -> String {
        let now = now_unix_seconds().unwrap();
        let claims = JsonValue::Object(BTreeMap::from([
            ("iss".to_owned(), JsonValue::String(ISSUER.to_owned())),
            ("aud".to_owned(), JsonValue::String(AUDIENCE.to_owned())),
            ("sub".to_owned(), JsonValue::String("11".repeat(16))),
            ("jti".to_owned(), JsonValue::String("22".repeat(16))),
            ("sid".to_owned(), JsonValue::String("33".repeat(16))),
            ("sgn".to_owned(), JsonValue::Unsigned(4)),
            ("trnm_kep".to_owned(), JsonValue::Unsigned(u64::from(EPOCH))),
            ("iat".to_owned(), JsonValue::Integer(now - 1)),
            ("exp".to_owned(), JsonValue::Integer(now + 600)),
        ]));
        let mut key_ring = KeyRing::new();
        key_ring
            .insert_epoch_key(EPOCH, SecretKey::new(KEY.to_vec()).unwrap())
            .unwrap();
        key_ring.set_active_epoch(EPOCH).unwrap();
        let profile = VerificationProfile {
            required_issuer: Some(ISSUER.to_owned()),
            required_audience: Some(AUDIENCE.to_owned()),
            allow_legacy_without_key_id: false,
            max_lifetime_seconds: Some(15 * 60),
            ..VerificationProfile::default()
        };
        format!(
            "Bearer {}",
            key_ring.issue_active_epoch(&claims, &profile).unwrap()
        )
    }

    fn authorization_headers() -> BTreeMap<String, String> {
        BTreeMap::from([("authorization".to_owned(), bearer())])
    }

    fn json_headers() -> BTreeMap<String, String> {
        BTreeMap::from([("content-type".to_owned(), "application/json".to_owned())])
    }

    #[test]
    fn configured_access_token_is_bound_to_persisted_family() {
        let mut api = SessionApi::default();
        api.configure(verifier());
        let mut repository = repository();
        let response = api
            .handle(
                &mut repository,
                &Request::new("GET", "/v1/session/me", authorization_headers(), Vec::new()),
            )
            .unwrap();
        assert_eq!(response.status, 200);
        let body = String::from_utf8(response.body).unwrap();
        assert!(body.contains(&"11".repeat(16)));
        assert!(body.contains("\"generation\":4"));
        assert_eq!(api.metrics().access_verified, 1);
    }

    #[test]
    fn refresh_rotation_hashes_credentials_and_advances_generation() {
        let mut api = SessionApi::default();
        api.configure(verifier());
        let mut repository = repository();
        let body = format!(
            "{{\"presented_refresh_credential\":\"{}.{}\",\"replacement_refresh_credential\":\"{}.{}\"}}",
            "44".repeat(16),
            "a".repeat(32),
            "55".repeat(16),
            "b".repeat(32),
        );
        let response = api
            .handle(
                &mut repository,
                &Request::new("POST", "/v1/session/refresh", json_headers(), body),
            )
            .unwrap();
        assert_eq!(response.status, 200);
        assert_eq!(repository.record.generation, 5);
        assert_eq!(api.metrics().refresh_rotated, 1);
    }

    #[test]
    fn refresh_replay_revokes_family_without_disclosing_state() {
        let mut api = SessionApi::default();
        api.configure(verifier());
        let mut repository = repository();
        repository.replay = true;
        let body = format!(
            "{{\"presented_refresh_credential\":\"{}.{}\",\"replacement_refresh_credential\":\"{}.{}\"}}",
            "44".repeat(16),
            "a".repeat(32),
            "55".repeat(16),
            "b".repeat(32),
        );
        let error = api
            .handle(
                &mut repository,
                &Request::new("POST", "/v1/session/refresh", json_headers(), body),
            )
            .unwrap_err();
        assert!(matches!(
            error,
            SessionError::Domain(value) if value.code() == StableCode::Unauthenticated
        ));
        assert_eq!(
            repository.record.revoked_reason,
            Some(RevocationReason::RefreshReplay)
        );
        assert_eq!(api.metrics().refresh_replay_revoked, 1);
    }

    #[test]
    fn logout_revokes_persisted_family() {
        let mut api = SessionApi::default();
        api.configure(verifier());
        let mut repository = repository();
        let response = api
            .handle(
                &mut repository,
                &Request::new(
                    "POST",
                    "/v1/session/logout",
                    authorization_headers(),
                    Vec::new(),
                ),
            )
            .unwrap();
        assert_eq!(response.status, 200);
        assert_eq!(
            repository.record.revoked_reason,
            Some(RevocationReason::Logout)
        );
        assert_eq!(api.metrics().logout_revoked, 1);
    }

    #[test]
    fn disabled_session_api_fails_closed_without_parsing_credentials() {
        let mut api = SessionApi::default();
        let mut repository = repository();
        let error = api
            .handle(
                &mut repository,
                &Request::new("GET", "/v1/session/me", authorization_headers(), Vec::new()),
            )
            .unwrap_err();
        assert!(matches!(
            error,
            SessionError::Domain(value) if value.code() == StableCode::Unimplemented
        ));
        assert_eq!(api.metrics().access_rejected, 1);
    }
}
