use std::collections::BTreeMap;
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{SystemTime, UNIX_EPOCH};

use trnm_contracts::{
    Digest32, DomainError, RefreshTokenId, RetryClass, SessionFamilyId, StableCode, UserId,
};
use trnm_persistence_pg::{
    CommitOutcome, CommitRequest, EntityHead, EntityId, SessionFamilyRecord, StorageActor,
    StorageBatchOperation, StorageMutationReceipt, StorageObjectKey, StorageState,
};
use trnm_session_core::RevocationReason;
use trnm_token_jwt_adapter::json::JsonValue;
use trnm_token_jwt_adapter::{KeyRing, SecretKey, VerificationProfile};

use super::app::{App, Repository};
use super::auth::AccessTokenVerifier;
use super::http::{Request, Response};

const KEY: &[u8] = b"0123456789abcdef0123456789abcdef";
const ISSUER: &str = "https://identity.test";
const AUDIENCE: &str = "trillionnium-game";
const EPOCH: u32 = 7;
const ADMIN: &str = "synthetic-operator-token-storage-tests-0001";
const USER: UserId = UserId::new([0x11; 16]);
const FAMILY: SessionFamilyId = SessionFamilyId::new([0x33; 16]);
const GENERATION: u64 = 4;

#[derive(Debug)]
struct RepositoryState {
    record: SessionFamilyRecord,
    storage: StorageState,
    verified_sessions: usize,
    storage_batches: usize,
    last_actor: Option<StorageActor>,
}

#[derive(Clone, Debug)]
struct StorageRepository(Arc<Mutex<RepositoryState>>);

impl StorageRepository {
    fn state(&self) -> MutexGuard<'_, RepositoryState> {
        self.0.lock().unwrap()
    }
}

impl Repository for StorageRepository {
    fn bootstrap_entity(
        &mut self,
        _entity: EntityId,
        _authority_generation: u64,
        _state: Digest32,
        _updated_at_ms: u64,
    ) -> Result<EntityHead, DomainError> {
        Err(unimplemented_domain())
    }

    fn commit_command(&mut self, _request: &CommitRequest) -> Result<CommitOutcome, DomainError> {
        Err(unimplemented_domain())
    }

    fn verify_access_session(
        &mut self,
        family: SessionFamilyId,
        user: UserId,
        generation: u64,
    ) -> Result<SessionFamilyRecord, DomainError> {
        let mut state = self.state();
        state.verified_sessions += 1;
        let record = state.record;
        if record.family == family
            && record.user == user
            && record.generation == generation
            && record.active_token.is_some()
            && record.revoked_reason.is_none()
        {
            Ok(record)
        } else {
            Err(DomainError::new(
                StableCode::Unauthenticated,
                "private_session_family_rejection",
                RetryClass::Never,
            ))
        }
    }

    fn apply_storage_batch(
        &mut self,
        actor: StorageActor,
        operations: &[StorageBatchOperation],
        _updated_at_ms: u64,
    ) -> Result<Vec<StorageMutationReceipt>, DomainError> {
        let mut state = self.state();
        state.storage_batches += 1;
        state.last_actor = Some(actor);
        state.storage.apply_batch(actor, operations)
    }
}

fn unimplemented_domain() -> DomainError {
    DomainError::new(
        StableCode::Unimplemented,
        "unrelated_test_operation",
        RetryClass::Never,
    )
}

fn app() -> (App<StorageRepository>, StorageRepository) {
    let repository = StorageRepository(Arc::new(Mutex::new(RepositoryState {
        record: SessionFamilyRecord {
            family: FAMILY,
            user: USER,
            generation: GENERATION,
            active_token: Some(RefreshTokenId::new([0x44; 16])),
            revoked_reason: None,
            created_at_ms: 1,
            updated_at_ms: 1,
        },
        storage: StorageState::default(),
        verified_sessions: 0,
        storage_batches: 0,
        last_actor: None,
    })));
    let verifier = AccessTokenVerifier::from_epoch_key(
        ISSUER.to_owned(),
        AUDIENCE.to_owned(),
        EPOCH,
        KEY.to_vec(),
    )
    .unwrap();
    let app = App::new(repository.clone(), ADMIN.to_owned()).with_access_token_verifier(verifier);
    (app, repository)
}

fn bearer(generation: u64) -> String {
    let now = i64::try_from(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs(),
    )
    .unwrap();
    let claims = JsonValue::Object(BTreeMap::from([
        ("iss".to_owned(), JsonValue::String(ISSUER.to_owned())),
        ("aud".to_owned(), JsonValue::String(AUDIENCE.to_owned())),
        ("sub".to_owned(), JsonValue::String("11".repeat(16))),
        ("jti".to_owned(), JsonValue::String("22".repeat(16))),
        ("sid".to_owned(), JsonValue::String("33".repeat(16))),
        ("sgn".to_owned(), JsonValue::Unsigned(generation)),
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

fn request(path: &str, authorization: Option<&str>, body: &str) -> Request {
    let mut headers = BTreeMap::from([("content-type".to_owned(), "application/json".to_owned())]);
    if let Some(value) = authorization {
        headers.insert("authorization".to_owned(), value.to_owned());
    }
    Request::new("PUT", path, headers, body)
}

fn key(name: &str) -> StorageObjectKey {
    StorageObjectKey::new("inventory", name, USER).unwrap()
}

fn json(response: &Response) -> serde_json::Value {
    serde_json::from_slice(&response.body).unwrap()
}

fn write_body() -> &'static str {
    r#"{"objects":[{"collection":"inventory","key":"sword","value":"{\"level\":1}","version":"*"}]}"#
}

#[test]
fn missing_tampered_and_operator_credentials_never_reach_storage_or_session_repository() {
    let signed = bearer(GENERATION);
    let mut tampered = signed.clone().into_bytes();
    let index = tampered.iter().rposition(|byte| *byte == b'.').unwrap() + 1;
    tampered[index] = if tampered[index] == b'A' { b'B' } else { b'A' };
    let tampered = String::from_utf8(tampered).unwrap();
    let operator = format!("Bearer {ADMIN}");
    for credential in [None, Some(tampered.as_str()), Some(operator.as_str())] {
        for path in ["/v2/storage", "/v2/storage/delete"] {
            let (mut app, repository) = app();
            let response = app.handle(&request(path, credential, write_body()));
            assert_eq!(response.status, 401);
            assert_eq!(json(&response)["code"], 16);
            let state = repository.state();
            assert_eq!(state.verified_sessions, 0);
            assert_eq!(state.storage_batches, 0);
            assert_eq!(state.storage.object_count(), 0);
        }
    }
}

#[test]
fn authentication_precedes_malformed_business_body_and_media_type() {
    for path in ["/v2/storage", "/v2/storage/delete"] {
        for body in ["not-json", r#"{"objects":[null]}"#, r#"{"object_ids":42}"#] {
            let (mut app, repository) = app();
            let request = Request::new("PUT", path, BTreeMap::new(), body);
            let response = app.handle(&request);
            assert_eq!(response.status, 401);
            assert_eq!(json(&response)["code"], 16);
            let state = repository.state();
            assert_eq!(state.verified_sessions, 0);
            assert_eq!(state.storage_batches, 0);
        }
    }
}

#[test]
fn persisted_revocation_and_stale_generation_reject_validly_signed_storage_requests() {
    for revoke in [true, false] {
        let (mut app, repository) = app();
        let generation = if revoke {
            let mut state = repository.state();
            state.record.revoked_reason = Some(RevocationReason::Logout);
            state.record.active_token = None;
            GENERATION
        } else {
            GENERATION - 1
        };
        let response = app.handle(&request(
            "/v2/storage",
            Some(&bearer(generation)),
            write_body(),
        ));
        assert_eq!(response.status, 401);
        assert_eq!(json(&response)["code"], 16);
        assert!(
            !String::from_utf8_lossy(&response.body).contains("private_session_family_rejection")
        );
        let state = repository.state();
        assert_eq!(state.verified_sessions, 1);
        assert_eq!(state.storage_batches, 0);
        assert_eq!(state.storage.object_count(), 0);
    }
}

#[test]
fn signed_principal_owns_writes_even_when_payload_supplies_another_user() {
    let (mut app, repository) = app();
    let body = r#"{"objects":[{"collection":"inventory","key":"sword","value":"{\"level\":1}","version":"*","user_id":"99999999-9999-9999-9999-999999999999"}]}"#;
    let response = app.handle(&request("/v2/storage", Some(&bearer(GENERATION)), body));
    assert_eq!(response.status, 200);
    let ack = json(&response);
    assert_eq!(
        ack["acks"][0]["user_id"],
        "11111111-1111-1111-1111-111111111111"
    );
    let state = repository.state();
    assert_eq!(state.last_actor, Some(StorageActor::User(USER)));
    let object = state
        .storage
        .read(StorageActor::User(USER), &key("sword"))
        .unwrap();
    assert_eq!(object.value, br#"{"level":1}"#);
    assert_eq!(ack["acks"][0]["version"], object.version.as_str());
    assert_eq!(state.storage.object_count(), 1);
}

#[test]
fn write_and_conditional_delete_apply_real_storage_transitions() {
    let (mut app, repository) = app();
    let credential = bearer(GENERATION);
    let write = app.handle(&request("/v2/storage", Some(&credential), write_body()));
    assert_eq!(write.status, 200);
    let version = json(&write)["acks"][0]["version"]
        .as_str()
        .unwrap()
        .to_owned();
    let delete = format!(
        r#"{{"object_ids":[{{"collection":"inventory","key":"sword","version":"{version}"}}]}}"#
    );
    let response = app.handle(&request("/v2/storage/delete", Some(&credential), &delete));
    assert_eq!(response.status, 200);
    assert_eq!(json(&response), serde_json::json!({}));
    let state = repository.state();
    assert_eq!(state.storage_batches, 2);
    assert_eq!(state.verified_sessions, 2);
    assert_eq!(state.storage.object_count(), 0);
    assert_eq!(
        state
            .storage
            .read(StorageActor::User(USER), &key("sword"))
            .unwrap_err()
            .code(),
        StableCode::NotFound
    );
}

#[test]
fn invalid_later_object_never_mutates_an_earlier_valid_object() {
    let (mut app, repository) = app();
    let body = r#"{"objects":[{"collection":"inventory","key":"sword","value":"{\"level\":1}"},{"collection":"inventory","key":"shield","value":"[]"}]}"#;
    let response = app.handle(&request("/v2/storage", Some(&bearer(GENERATION)), body));
    assert_eq!(response.status, 400);
    assert_eq!(json(&response)["code"], 3);
    let state = repository.state();
    assert_eq!(state.verified_sessions, 1);
    assert_eq!(state.storage_batches, 0);
    assert_eq!(state.storage.object_count(), 0);
}

#[test]
fn later_occ_failure_rolls_back_every_storage_batch_effect() {
    let (mut app, repository) = app();
    let credential = bearer(GENERATION);
    assert_eq!(
        app.handle(&request("/v2/storage", Some(&credential), write_body()))
            .status,
        200
    );
    let body = r#"{"objects":[{"collection":"inventory","key":"sword","value":"{\"level\":2}"},{"collection":"inventory","key":"shield","value":"{}","version":"00000000000000000000000000000000"}]}"#;
    let response = app.handle(&request("/v2/storage", Some(&credential), body));
    assert_eq!(response.status, 400);
    assert_eq!(json(&response)["code"], 3);
    let state = repository.state();
    assert_eq!(state.storage_batches, 2);
    assert_eq!(state.storage.object_count(), 1);
    assert_eq!(
        state
            .storage
            .read(StorageActor::User(USER), &key("sword"))
            .unwrap()
            .value,
        br#"{"level":1}"#
    );
    assert_eq!(
        state
            .storage
            .read(StorageActor::User(USER), &key("shield"))
            .unwrap_err()
            .code(),
        StableCode::NotFound
    );
}

#[test]
fn empty_storage_requests_authenticate_and_return_without_repository_mutation() {
    for (path, body) in [
        ("/v2/storage", r#"{"objects":[]}"#),
        ("/v2/storage/delete", r#"{"object_ids":[]}"#),
    ] {
        let (mut app, repository) = app();
        let response = app.handle(&request(path, Some(&bearer(GENERATION)), body));
        assert_eq!(response.status, 200);
        assert_eq!(json(&response), serde_json::json!({}));
        let state = repository.state();
        assert_eq!(state.verified_sessions, 1);
        assert_eq!(state.storage_batches, 0);
    }
}

#[test]
fn drain_fences_new_storage_writes_and_deletes_before_repository_access() {
    let (mut app, repository) = app();
    let response = app.handle(&Request::new(
        "POST",
        "/-/drain",
        BTreeMap::from([("authorization".to_owned(), format!("Bearer {ADMIN}"))]),
        Vec::new(),
    ));
    assert_eq!(response.status, 200);
    assert!(app.should_stop());
    let credential = bearer(GENERATION);
    for path in ["/v2/storage", "/v2/storage/delete"] {
        let response = app.handle(&request(path, Some(&credential), write_body()));
        assert_eq!(response.status, 503);
        assert_eq!(json(&response)["code"], 14);
    }
    let state = repository.state();
    assert_eq!(state.verified_sessions, 0);
    assert_eq!(state.storage_batches, 0);
    assert_eq!(state.storage.object_count(), 0);
}

#[test]
fn query_parameters_do_not_change_storage_write_or_delete_routing() {
    let (mut app, repository) = app();
    let credential = bearer(GENERATION);
    let write = app.handle(&request(
        "/v2/storage?trace=source&user_id=other",
        Some(&credential),
        write_body(),
    ));
    assert_eq!(write.status, 200);
    let version = json(&write)["acks"][0]["version"]
        .as_str()
        .unwrap()
        .to_owned();
    {
        let state = repository.state();
        let object = state
            .storage
            .read(StorageActor::User(USER), &key("sword"))
            .unwrap();
        assert_eq!(object.value, br#"{"level":1}"#);
        assert_eq!(object.version.as_str(), version);
        assert_eq!(state.last_actor, Some(StorageActor::User(USER)));
    }
    let delete = format!(
        r#"{{"object_ids":[{{"collection":"inventory","key":"sword","version":"{version}"}}]}}"#
    );
    let response = app.handle(&request(
        "/v2/storage/delete?trace=source&version=ignored",
        Some(&credential),
        &delete,
    ));
    assert_eq!(response.status, 200);
    assert_eq!(json(&response), serde_json::json!({}));
    let state = repository.state();
    assert_eq!(state.verified_sessions, 2);
    assert_eq!(state.storage_batches, 2);
    assert_eq!(state.storage.object_count(), 0);
}

#[test]
fn drained_storage_query_targets_never_reach_session_or_storage_repositories() {
    let (mut app, repository) = app();
    let response = app.handle(&Request::new(
        "POST",
        "/-/drain",
        BTreeMap::from([("authorization".to_owned(), format!("Bearer {ADMIN}"))]),
        Vec::new(),
    ));
    assert_eq!(response.status, 200);
    assert!(app.should_stop());
    let credential = bearer(GENERATION);
    for target in [
        "/v2/storage?trace=source",
        "/v2/storage/delete?trace=source",
        "/v2/storage?",
        "/v2/storage/delete?",
    ] {
        let response = app.handle(&request(target, Some(&credential), write_body()));
        assert_eq!(response.status, 503, "{target}");
        assert_eq!(json(&response)["code"], 14);
    }
    let state = repository.state();
    assert_eq!(state.verified_sessions, 0);
    assert_eq!(state.storage_batches, 0);
    assert_eq!(state.storage.object_count(), 0);
}
