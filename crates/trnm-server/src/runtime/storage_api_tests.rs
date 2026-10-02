use std::collections::BTreeMap;
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{SystemTime, UNIX_EPOCH};

use trnm_contracts::{
    Digest32, DomainError, RefreshTokenId, RetryClass, SessionFamilyId, StableCode, UserId,
};
use trnm_persistence_pg::{
    CommitOutcome, CommitRequest, ContentVersion, CreateSessionFamily, DatabaseProfile, EntityHead,
    EntityId, IntegrityDigest, PgRepository, ReadPermission, RefreshTokenCredential,
    SessionFamilyRecord, StorageActor, StorageBatchOperation, StorageMutationReceipt,
    StorageObject, StorageObjectKey, StorageState, StorageWriteOperation, VersionCheck,
    WritePermission,
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
    storage_reads: usize,
    last_actor: Option<StorageActor>,
    last_read_keys: Vec<StorageObjectKey>,
}

#[derive(Clone, Debug)]
struct StorageRepository(Arc<Mutex<RepositoryState>>);

impl StorageRepository {
    fn state(&self) -> MutexGuard<'_, RepositoryState> {
        self.0.lock().unwrap()
    }

    fn seed(&self, name: &str, owner: UserId, read_permission: ReadPermission, value: &str) {
        self.state()
            .storage
            .apply_batch(
                StorageActor::Server,
                &[StorageBatchOperation::Write(StorageWriteOperation {
                    key: StorageObjectKey::new("inventory", name, owner).unwrap(),
                    value: value.as_bytes().to_vec(),
                    expected: VersionCheck::MustNotExist,
                    read_permission,
                    write_permission: WritePermission::Owner,
                })],
            )
            .unwrap();
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

    fn read_storage_objects(
        &mut self,
        actor: StorageActor,
        keys: &[StorageObjectKey],
    ) -> Result<Vec<StorageObject>, DomainError> {
        let mut state = self.state();
        state.storage_reads += 1;
        state.last_actor = Some(actor);
        state.last_read_keys = keys.to_vec();
        let mut objects = Vec::new();
        for key in keys {
            match state.storage.read(actor, key) {
                Ok(object) => objects.push(object),
                Err(error)
                    if matches!(
                        error.code(),
                        StableCode::NotFound | StableCode::PermissionDenied
                    ) => {}
                Err(error) => return Err(error),
            }
        }
        Ok(objects)
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
        storage_reads: 0,
        last_actor: None,
        last_read_keys: Vec::new(),
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
    bearer_for(USER, FAMILY, generation)
}

fn bearer_for(user: UserId, family: SessionFamilyId, generation: u64) -> String {
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
        (
            "sub".to_owned(),
            JsonValue::String(super::codec::encode_hex(user.as_bytes())),
        ),
        ("jti".to_owned(), JsonValue::String("22".repeat(16))),
        (
            "sid".to_owned(),
            JsonValue::String(super::codec::encode_hex(family.as_bytes())),
        ),
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

fn read_request(path: &str, authorization: Option<&str>, body: &str) -> Request {
    let mut request = request(path, authorization, body);
    request.method = "POST".to_owned();
    request
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

#[test]
fn unverified_read_credentials_never_reach_session_or_storage_repositories() {
    let signed = bearer(GENERATION);
    let mut tampered = signed.into_bytes();
    let index = tampered.iter().rposition(|byte| *byte == b'.').unwrap() + 1;
    tampered[index] = if tampered[index] == b'A' { b'B' } else { b'A' };
    let tampered = String::from_utf8(tampered).unwrap();
    let operator = format!("Bearer {ADMIN}");
    for credential in [None, Some(tampered.as_str()), Some(operator.as_str())] {
        let (mut app, repository) = app();
        let response = app.handle(&read_request(
            "/v2/storage",
            credential,
            r#"{"object_ids":[{"collection":"inventory","key":"sword"}]}"#,
        ));
        assert_eq!(response.status, 401);
        assert_eq!(json(&response)["code"], 16);
        let state = repository.state();
        assert_eq!(state.verified_sessions, 0);
        assert_eq!(state.storage_reads, 0);
        assert_eq!(state.storage_batches, 0);
    }
}

#[test]
fn read_authentication_precedes_malformed_body_and_owner_validation() {
    for body in [
        "not-json",
        r#"{"object_ids":[null]}"#,
        r#"{"object_ids":[{"collection":"inventory","key":"sword","user_id":"not-a-uuid"}]}"#,
        r#"{"object_ids":[{"collection":"inventory","key":"sword","user_id":"00000000-0000-0000-0000-000000000000"}]}"#,
    ] {
        let (mut app, repository) = app();
        let response = app.handle(&read_request("/v2/storage", None, body));
        assert_eq!(response.status, 401, "{body}");
        assert_eq!(json(&response)["code"], 16);
        let state = repository.state();
        assert_eq!(state.verified_sessions, 0);
        assert_eq!(state.storage_reads, 0);
        assert_eq!(state.storage_batches, 0);
    }
}

#[test]
fn read_rejects_revoked_family_and_stale_generation_before_object_access() {
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
        let response = app.handle(&read_request(
            "/v2/storage",
            Some(&bearer(generation)),
            r#"{"object_ids":[{"collection":"inventory","key":"sword"}]}"#,
        ));
        assert_eq!(response.status, 401);
        assert_eq!(json(&response)["code"], 16);
        let state = repository.state();
        assert_eq!(state.verified_sessions, 1);
        assert_eq!(state.storage_reads, 0);
        assert_eq!(state.storage_batches, 0);
    }
}

#[test]
fn read_returns_visible_owner_and_public_objects_and_omits_the_rest() {
    let (mut app, repository) = app();
    let other = UserId::new([0x55; 16]);
    let global = UserId::new([0; 16]);
    let own_value = " { \"origin\": \"caller\" }\n";
    let other_value = r#"{"origin":"other"}"#;
    let global_value = r#"{"origin":"global"}"#;
    for (name, owner, read_permission, value) in [
        ("shared", USER, ReadPermission::Owner, own_value),
        ("shared", other, ReadPermission::Public, other_value),
        ("shared", global, ReadPermission::Public, global_value),
        ("own-hidden", USER, ReadPermission::None, "{}"),
        ("other-private", other, ReadPermission::Owner, "{}"),
        ("other-hidden", other, ReadPermission::None, "{}"),
        ("global-private", global, ReadPermission::Owner, "{}"),
        ("global-hidden", global, ReadPermission::None, "{}"),
    ] {
        repository.seed(name, owner, read_permission, value);
    }
    let body = r#"{"object_ids":[
        {"collection":"inventory","key":"shared","user_id":"11111111-1111-1111-1111-111111111111"},
        {"collection":"inventory","key":"shared","userId":"55555555-5555-5555-5555-555555555555"},
        {"collection":"inventory","key":"shared"},
        {"collection":"inventory","key":"own-hidden","user_id":"11111111-1111-1111-1111-111111111111"},
        {"collection":"inventory","key":"other-private","user_id":"55555555-5555-5555-5555-555555555555"},
        {"collection":"inventory","key":"other-hidden","user_id":"55555555-5555-5555-5555-555555555555"},
        {"collection":"inventory","key":"global-private"},
        {"collection":"inventory","key":"global-hidden"},
        {"collection":"inventory","key":"missing","user_id":"11111111-1111-1111-1111-111111111111"}
    ]}"#;
    let response = app.handle(&read_request(
        "/v2/storage",
        Some(&bearer(GENERATION)),
        body,
    ));
    assert_eq!(response.status, 200);
    let response_body = json(&response);
    let objects = response_body["objects"].as_array().unwrap();
    assert_eq!(objects.len(), 3);
    let visible = objects
        .iter()
        .map(|object| {
            assert_eq!(object["collection"], "inventory");
            assert_eq!(object["key"], "shared");
            assert_eq!(object["permission_write"], 1);
            (
                object["user_id"].as_str().unwrap().to_owned(),
                (
                    object["value"].as_str().unwrap().to_owned(),
                    object["version"].as_str().unwrap().to_owned(),
                    object["permission_read"].as_u64().unwrap(),
                ),
            )
        })
        .collect::<BTreeMap<_, _>>();
    // Compare distinct objects by owner without asserting database row order.
    let expected = [
        ("11111111-1111-1111-1111-111111111111", own_value, 1),
        ("55555555-5555-5555-5555-555555555555", other_value, 2),
        ("00000000-0000-0000-0000-000000000000", global_value, 2),
    ]
    .into_iter()
    .map(|(owner, value, read_permission)| {
        (
            owner.to_owned(),
            (
                value.to_owned(),
                ContentVersion::from_value(value.as_bytes())
                    .as_str()
                    .to_owned(),
                read_permission,
            ),
        )
    })
    .collect::<BTreeMap<_, _>>();
    assert_eq!(visible, expected);
    let state = repository.state();
    assert_eq!(state.verified_sessions, 1);
    assert_eq!(state.storage_reads, 1);
    assert_eq!(state.storage_batches, 0);
    assert_eq!(state.last_actor, Some(StorageActor::User(USER)));
    assert_eq!(state.last_read_keys.len(), 9);
    assert_eq!(state.storage.object_count(), 8);
}

#[test]
fn missing_empty_and_null_read_owner_select_global_objects() {
    for owner_field in ["", r#", "user_id":"""#, r#", "userId":null"#] {
        let (mut app, repository) = app();
        repository.seed(
            "shared",
            USER,
            ReadPermission::Owner,
            r#"{"owner":"caller"}"#,
        );
        repository.seed(
            "shared",
            UserId::new([0; 16]),
            ReadPermission::Public,
            r#"{"owner":"global"}"#,
        );
        let body = format!(
            r#"{{"objectIds":[{{"collection":"inventory","key":"shared"{owner_field}}}]}}"#
        );
        let response = app.handle(&read_request(
            "/v2/storage",
            Some(&bearer(GENERATION)),
            &body,
        ));
        assert_eq!(response.status, 200, "{body}");
        let response_body = json(&response);
        let objects = response_body["objects"].as_array().unwrap();
        assert_eq!(objects.len(), 1);
        assert_eq!(
            objects[0]["user_id"],
            "00000000-0000-0000-0000-000000000000"
        );
        assert_eq!(objects[0]["value"], r#"{"owner":"global"}"#);
        let state = repository.state();
        assert_eq!(state.storage_reads, 1);
        assert_eq!(state.last_read_keys[0].user_id(), UserId::new([0; 16]));
        assert_eq!(state.storage_batches, 0);
    }
}

#[test]
fn read_validates_every_id_before_repository_access_and_rejects_explicit_nil_owner() {
    for body in [
        r#"{"object_ids":[{"collection":"inventory","key":"sword","user_id":"not-a-uuid"}]}"#,
        r#"{"object_ids":[{"collection":"inventory","key":"sword","user_id":"00000000-0000-0000-0000-000000000000"}]}"#,
        r#"{"object_ids":[{"collection":"inventory","key":"sword","user_id":42}]}"#,
        r#"{"object_ids":[{"collection":"inventory","key":"sword"},{"collection":"inventory","key":"shield","user_id":"00000000-0000-0000-0000-000000000000"}]}"#,
        r#"{"object_ids":[{"collection":"inventory","key":"sword"},{"collection":"inventory","key":""}]}"#,
        r#"{"object_ids":[{"collection":"inventory","key":"sword","user_id":null,"userId":"11111111-1111-1111-1111-111111111111"}]}"#,
        r#"{"object_ids":[{"collection":"inventory","key":"sword"},null]}"#,
    ] {
        let (mut app, repository) = app();
        let response = app.handle(&read_request(
            "/v2/storage",
            Some(&bearer(GENERATION)),
            body,
        ));
        assert_eq!(response.status, 400, "{body}");
        assert_eq!(json(&response)["code"], 3);
        let state = repository.state();
        assert_eq!(state.verified_sessions, 1);
        assert_eq!(state.storage_reads, 0);
        assert_eq!(state.storage_batches, 0);
    }
}

#[test]
fn empty_and_null_read_batches_authenticate_without_accessing_storage() {
    for body in [
        "",
        " \t\r\n",
        "{}",
        r#"{"object_ids":[]}"#,
        r#"{"objectIds":null}"#,
    ] {
        let (mut app, repository) = app();
        let response = app.handle(&read_request(
            "/v2/storage",
            Some(&bearer(GENERATION)),
            body,
        ));
        assert_eq!(response.status, 200, "{body}");
        assert_eq!(response.body, b"{}");
        let state = repository.state();
        assert_eq!(state.verified_sessions, 1);
        assert_eq!(state.storage_reads, 0);
        assert_eq!(state.storage_batches, 0);
    }
}

#[test]
fn read_query_parameters_do_not_replace_body_owner_or_change_routing() {
    for target in [
        "/v2/storage?trace=source&user_id=11111111-1111-1111-1111-111111111111",
        "/v2/storage?",
    ] {
        let (mut app, repository) = app();
        repository.seed(
            "shared",
            USER,
            ReadPermission::Owner,
            r#"{"owner":"caller"}"#,
        );
        repository.seed(
            "shared",
            UserId::new([0; 16]),
            ReadPermission::Public,
            r#"{"owner":"global"}"#,
        );
        let response = app.handle(&read_request(
            target,
            Some(&bearer(GENERATION)),
            r#"{"object_ids":[{"collection":"inventory","key":"shared"}]}"#,
        ));
        assert_eq!(response.status, 200, "{target}");
        assert_eq!(
            json(&response)["objects"][0]["value"],
            r#"{"owner":"global"}"#
        );
        let state = repository.state();
        assert_eq!(state.verified_sessions, 1);
        assert_eq!(state.storage_reads, 1);
        assert_eq!(state.storage_batches, 0);
    }
}

#[test]
fn draining_allows_authenticated_storage_reads_and_still_enforces_authentication() {
    let (mut app, repository) = app();
    repository.seed(
        "shared",
        UserId::new([0; 16]),
        ReadPermission::Public,
        r#"{"owner":"global"}"#,
    );
    let drain = app.handle(&Request::new(
        "POST",
        "/-/drain",
        BTreeMap::from([("authorization".to_owned(), format!("Bearer {ADMIN}"))]),
        Vec::new(),
    ));
    assert_eq!(drain.status, 200);
    assert!(app.should_stop());
    let credential = bearer(GENERATION);
    let response = app.handle(&read_request(
        "/v2/storage?trace=source",
        Some(&credential),
        r#"{"object_ids":[{"collection":"inventory","key":"shared"}]}"#,
    ));
    assert_eq!(response.status, 200);
    assert_eq!(
        json(&response)["objects"][0]["value"],
        r#"{"owner":"global"}"#
    );
    let unauthenticated = app.handle(&read_request("/v2/storage", None, "not-json"));
    assert_eq!(unauthenticated.status, 401);
    assert_eq!(json(&unauthenticated)["code"], 16);
    let malformed = app.handle(&read_request("/v2/storage", Some(&credential), "not-json"));
    assert_eq!(malformed.status, 400);
    assert_eq!(json(&malformed)["code"], 3);
    let state = repository.state();
    assert_eq!(state.verified_sessions, 2);
    assert_eq!(state.storage_reads, 1);
    assert_eq!(state.storage_batches, 0);
    assert_eq!(state.storage.object_count(), 1);
}

const LIVE_COLLECTION: &str = "canonical-storage-api-live";
const LIVE_USER: UserId = UserId::new([0xd1; 16]);
const LIVE_OTHER: UserId = UserId::new([0xd2; 16]);
const LIVE_FAMILY: SessionFamilyId = SessionFamilyId::new([0xe1; 16]);
const LIVE_USER_UUID: &str = "d1d1d1d1-d1d1-d1d1-d1d1-d1d1d1d1d1d1";
const LIVE_OTHER_UUID: &str = "d2d2d2d2-d2d2-d2d2-d2d2-d2d2d2d2d2d2";

fn storage_live_environment() -> Option<(String, DatabaseProfile)> {
    let required = match std::env::var("TRNM_REQUIRE_LIVE_DATABASE") {
        Err(std::env::VarError::NotPresent) => false,
        Ok(value) if matches!(value.as_str(), "1" | "true" | "TRUE" | "yes" | "YES") => true,
        Ok(value) if matches!(value.as_str(), "0" | "false" | "FALSE" | "no" | "NO") => false,
        _ => panic!("canonical storage fixture: invalid TRNM_REQUIRE_LIVE_DATABASE"),
    };
    let database_url = match std::env::var("TRNM_DATABASE_URL") {
        Ok(value) if !value.is_empty() => value,
        Ok(_) | Err(std::env::VarError::NotPresent) if !required => {
            eprintln!(
                "canonical_storage_api_live_skipped: TRNM_DATABASE_URL absent or empty; developer-only skip, no live execution"
            );
            return None;
        }
        _ => {
            panic!("canonical storage fixture: TRNM_DATABASE_URL is required and must not be empty")
        }
    };
    let profile = match std::env::var("TRNM_DATABASE_PROFILE").as_deref() {
        Ok("postgresql") => DatabaseProfile::PostgreSql,
        Ok("cockroachdb") => DatabaseProfile::CockroachDb,
        _ => panic!(
            "canonical storage fixture: TRNM_DATABASE_PROFILE must be postgresql or cockroachdb"
        ),
    };
    Some((database_url, profile))
}

fn cleanup_storage_live_fixture(control: &mut postgres::Client) -> Result<(), postgres::Error> {
    let mut transaction = control.transaction()?;
    transaction.execute(
        "DELETE FROM trnm_storage_objects WHERE collection = $1",
        &[&LIVE_COLLECTION],
    )?;
    transaction.execute(
        "DELETE FROM trnm_refresh_tokens WHERE family_id = $1 \
         AND EXISTS (SELECT 1 FROM trnm_session_families \
                     WHERE family_id = $1 AND user_id = $2)",
        &[
            &LIVE_FAMILY.as_bytes().as_slice(),
            &LIVE_USER.as_bytes().as_slice(),
        ],
    )?;
    transaction.execute(
        "DELETE FROM trnm_session_families WHERE family_id = $1 AND user_id = $2",
        &[
            &LIVE_FAMILY.as_bytes().as_slice(),
            &LIVE_USER.as_bytes().as_slice(),
        ],
    )?;
    transaction.commit()
}

fn persisted_storage_value(
    control: &mut postgres::Client,
    name: &str,
    owner: UserId,
) -> Option<Vec<u8>> {
    control
        .query_opt(
            "SELECT value_bytes, version_digest FROM trnm_storage_objects \
             WHERE collection = $1 AND object_key = $2 AND user_id = $3",
            &[&LIVE_COLLECTION, &name, &owner.as_bytes().as_slice()],
        )
        .unwrap_or_else(|_| panic!("canonical storage fixture: persisted object query failed"))
        .map(|row| {
            let value: Vec<u8> = row.get(0);
            let digest: Vec<u8> = row.get(1);
            assert_eq!(
                digest,
                IntegrityDigest::from_value(&value)
                    .get()
                    .as_bytes()
                    .as_slice()
            );
            value
        })
}

fn storage_live_row_count(control: &mut postgres::Client) -> i64 {
    control
        .query_one(
            "SELECT count(*) FROM trnm_storage_objects WHERE collection = $1",
            &[&LIVE_COLLECTION],
        )
        .unwrap_or_else(|_| panic!("canonical storage fixture: collection count failed"))
        .get(0)
}

#[test]
fn canonical_storage_api_live_database() {
    let Some((database_url, profile)) = storage_live_environment() else {
        return;
    };
    let mut control = postgres::Client::connect(&database_url, postgres::NoTls)
        .unwrap_or_else(|_| panic!("canonical storage fixture: control connection failed"));
    let engine: String = control
        .query_one("SELECT version()", &[])
        .unwrap_or_else(|_| panic!("canonical storage fixture: database identity query failed"))
        .get(0);
    match profile {
        DatabaseProfile::PostgreSql => {
            assert!(engine.contains("PostgreSQL") && !engine.contains("CockroachDB"));
        }
        DatabaseProfile::CockroachDb => assert!(engine.contains("CockroachDB")),
    }
    cleanup_storage_live_fixture(&mut control)
        .unwrap_or_else(|_| panic!("canonical storage fixture: initial scoped cleanup failed"));

    // Clean this fixture's rows after assertions fail as well as on success.
    // Only the dedicated collection and this user's synthetic family are owned.
    let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let mut repository = PgRepository::connect(&database_url, profile)
            .unwrap_or_else(|_| panic!("canonical storage fixture: repository connection failed"));
        let issued_at_ms = u64::try_from(
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_millis(),
        )
        .unwrap();
        let family = repository
            .create_session_family(&CreateSessionFamily {
                family: LIVE_FAMILY,
                user: LIVE_USER,
                refresh: RefreshTokenCredential {
                    id: RefreshTokenId::new([0xe2; 16]),
                    digest: Digest32::new([0xe3; 32]),
                },
                issued_at_ms,
            })
            .unwrap_or_else(|_| {
                panic!("canonical storage fixture: session family creation failed")
            });
        assert_eq!(family.generation, 0);
        for (name, owner, permission, value) in [
            (
                "other-public",
                LIVE_OTHER,
                ReadPermission::Public,
                r#"{"owner":"other-public"}"#,
            ),
            (
                "other-private",
                LIVE_OTHER,
                ReadPermission::Owner,
                r#"{"owner":"other-private"}"#,
            ),
            ("other-hidden", LIVE_OTHER, ReadPermission::None, "{}"),
            ("own-hidden", LIVE_USER, ReadPermission::None, "{}"),
            (
                "global-public",
                UserId::new([0; 16]),
                ReadPermission::Public,
                r#"{"owner":"global"}"#,
            ),
            (
                "global-private",
                UserId::new([0; 16]),
                ReadPermission::Owner,
                "{}",
            ),
        ] {
            let value = value.as_bytes();
            let digest = IntegrityDigest::from_value(value).get();
            assert_eq!(
                control
                    .execute(
                        "INSERT INTO trnm_storage_objects \
                         (collection, object_key, user_id, value_bytes, version_digest, \
                          read_permission, write_permission, updated_at_ms) \
                         VALUES ($1, $2, $3, $4, $5, $6, 1, 0)",
                        &[
                            &LIVE_COLLECTION,
                            &name,
                            &owner.as_bytes().as_slice(),
                            &value,
                            &digest.as_bytes().as_slice(),
                            &(permission as i16)
                        ],
                    )
                    .unwrap_or_else(|_| panic!("canonical storage fixture: object seed failed")),
                1
            );
        }
        let verifier = AccessTokenVerifier::from_epoch_key(
            ISSUER.to_owned(),
            AUDIENCE.to_owned(),
            EPOCH,
            KEY.to_vec(),
        )
        .unwrap();
        let mut app = App::new(repository, ADMIN.to_owned()).with_access_token_verifier(verifier);
        let credential = bearer_for(LIVE_USER, LIVE_FAMILY, family.generation);
        let original = " { \"level\": 1 }\n";
        let write_body = serde_json::json!({"objects":[{
            "collection":LIVE_COLLECTION,"key":"sword","value":original,"version":"*",
            "user_id":LIVE_OTHER_UUID
        }]})
        .to_string();
        let write = app.handle(&request(
            "/v2/storage?trace=live",
            Some(&credential),
            &write_body,
        ));
        assert_eq!(write.status, 200);
        assert_eq!(json(&write)["acks"][0]["user_id"], LIVE_USER_UUID);
        let version = ContentVersion::from_value(original.as_bytes())
            .as_str()
            .to_owned();
        assert_eq!(json(&write)["acks"][0]["version"], version);
        assert_eq!(
            persisted_storage_value(&mut control, "sword", LIVE_USER),
            Some(original.as_bytes().to_vec())
        );
        assert_eq!(
            persisted_storage_value(&mut control, "sword", LIVE_OTHER),
            None
        );

        let read_body = serde_json::json!({"object_ids":[
            {"collection":LIVE_COLLECTION,"key":"sword","user_id":LIVE_USER_UUID},
            {"collection":LIVE_COLLECTION,"key":"other-public","userId":LIVE_OTHER_UUID},
            {"collection":LIVE_COLLECTION,"key":"other-private","user_id":LIVE_OTHER_UUID},
            {"collection":LIVE_COLLECTION,"key":"other-hidden","user_id":LIVE_OTHER_UUID},
            {"collection":LIVE_COLLECTION,"key":"own-hidden","user_id":LIVE_USER_UUID},
            {"collection":LIVE_COLLECTION,"key":"global-public"},
            {"collection":LIVE_COLLECTION,"key":"global-private"},
            {"collection":LIVE_COLLECTION,"key":"missing","user_id":LIVE_USER_UUID}
        ]})
        .to_string();
        let read = app.handle(&read_request(
            "/v2/storage?trace=live",
            Some(&credential),
            &read_body,
        ));
        assert_eq!(read.status, 200);
        let read_json = json(&read);
        let objects = read_json["objects"].as_array().unwrap();
        assert_eq!(objects.len(), 3);
        let visible = objects
            .iter()
            .map(|object| {
                let value = object["value"].as_str().unwrap();
                assert_eq!(
                    object["version"],
                    ContentVersion::from_value(value.as_bytes()).as_str()
                );
                assert_eq!(object["collection"], LIVE_COLLECTION);
                (
                    object["key"].as_str().unwrap().to_owned(),
                    (
                        object["user_id"].as_str().unwrap().to_owned(),
                        value.to_owned(),
                    ),
                )
            })
            .collect::<BTreeMap<_, _>>();
        assert_eq!(
            visible,
            BTreeMap::from([
                (
                    "sword".to_owned(),
                    (LIVE_USER_UUID.to_owned(), original.to_owned())
                ),
                (
                    "other-public".to_owned(),
                    (
                        LIVE_OTHER_UUID.to_owned(),
                        r#"{"owner":"other-public"}"#.to_owned()
                    )
                ),
                (
                    "global-public".to_owned(),
                    (
                        "00000000-0000-0000-0000-000000000000".to_owned(),
                        r#"{"owner":"global"}"#.to_owned()
                    )
                ),
            ])
        );

        let rollback_body = serde_json::json!({"objects":[
            {"collection":LIVE_COLLECTION,"key":"sword","value":"{\"level\":2}"},
            {"collection":LIVE_COLLECTION,"key":"shield","value":"{}","version":"00000000000000000000000000000000"}
        ]}).to_string();
        let rejected = app.handle(&request("/v2/storage", Some(&credential), &rollback_body));
        assert_eq!(rejected.status, 400);
        assert_eq!(json(&rejected)["code"], 3);
        assert!(json(&rejected).get("acks").is_none());
        assert_eq!(
            persisted_storage_value(&mut control, "sword", LIVE_USER),
            Some(original.as_bytes().to_vec())
        );
        assert_eq!(
            persisted_storage_value(&mut control, "shield", LIVE_USER),
            None
        );

        let delete_body = |expected: &str| {
            serde_json::json!({"object_ids":[{
                "collection":LIVE_COLLECTION,"key":"sword","version":expected
            }]})
            .to_string()
        };
        let stale = app.handle(&request(
            "/v2/storage/delete",
            Some(&credential),
            &delete_body("00000000000000000000000000000000"),
        ));
        assert_eq!(stale.status, 400);
        assert_eq!(json(&stale)["code"], 3);
        assert_eq!(
            persisted_storage_value(&mut control, "sword", LIVE_USER),
            Some(original.as_bytes().to_vec())
        );
        let deleted = app.handle(&request(
            "/v2/storage/delete?trace=live",
            Some(&credential),
            &delete_body(&version),
        ));
        assert_eq!(deleted.status, 200);
        assert_eq!(deleted.body, b"{}");
        assert_eq!(
            persisted_storage_value(&mut control, "sword", LIVE_USER),
            None
        );

        let logout = app.handle(&Request::new(
            "POST",
            "/v1/session/logout",
            BTreeMap::from([("authorization".to_owned(), credential.clone())]),
            Vec::new(),
        ));
        assert_eq!(logout.status, 200);
        let revoked = control.query_one(
            "SELECT active_token_id IS NULL, revoked_reason FROM trnm_session_families WHERE family_id = $1",
            &[&LIVE_FAMILY.as_bytes().as_slice()],
        ).unwrap_or_else(|_| panic!("canonical storage fixture: revocation query failed"));
        assert!(revoked.get::<_, bool>(0));
        assert_eq!(revoked.get::<_, i16>(1), 0);
        let rows = storage_live_row_count(&mut control);
        for request in [
            request("/v2/storage", Some(&credential), &write_body),
            request(
                "/v2/storage/delete",
                Some(&credential),
                &delete_body(&version),
            ),
            read_request("/v2/storage", Some(&credential), &read_body),
            request("/v2/storage", Some(&credential), "not-json"),
            request("/v2/storage/delete", Some(&credential), "not-json"),
            read_request("/v2/storage", Some(&credential), "not-json"),
        ] {
            let response = app.handle(&request);
            assert_eq!(response.status, 401);
            assert_eq!(json(&response)["code"], 16);
        }
        assert_eq!(storage_live_row_count(&mut control), rows);
        assert_eq!(
            persisted_storage_value(&mut control, "sword", LIVE_USER),
            None
        );
    }));
    cleanup_storage_live_fixture(&mut control)
        .unwrap_or_else(|_| panic!("canonical storage fixture: final scoped cleanup failed"));
    if let Err(panic) = outcome {
        std::panic::resume_unwind(panic);
    }
    assert_eq!(storage_live_row_count(&mut control), 0);
    for table in ["trnm_refresh_tokens", "trnm_session_families"] {
        let count: i64 = control
            .query_one(
                &format!("SELECT count(*) FROM {table} WHERE family_id = $1"),
                &[&LIVE_FAMILY.as_bytes().as_slice()],
            )
            .unwrap_or_else(|_| panic!("canonical storage fixture: cleanup assertion failed"))
            .get(0);
        assert_eq!(count, 0);
    }
    println!(
        "\ncanonical_storage_api_live_executed profile={}",
        profile.metadata_value()
    );
}
