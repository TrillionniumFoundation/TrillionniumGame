use std::collections::BTreeMap;
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{SystemTime, UNIX_EPOCH};

use trnm_contracts::{
    Digest32, DomainError, RefreshTokenId, RetryClass, SessionFamilyId, StableCode, UserId,
};
use trnm_persistence_pg::{
    CommitOutcome, CommitRequest, ContentVersion, CreateSessionFamily, DatabaseProfile, EntityHead,
    EntityId, IntegrityDigest, PgRepository, ReadPermission, RefreshTokenCredential,
    SessionFamilyRecord, StorageActor, StorageBatchOperation, StorageListPosition,
    StorageObjectKey, StorageState, StorageTimes, StorageTimestamp, StorageWriteOperation,
    StoredStorageClientListPage, StoredStorageMutationReceipt, StoredStorageObject, VersionCheck,
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
    storage_lists: usize,
    last_actor: Option<StorageActor>,
    last_read_keys: Vec<StorageObjectKey>,
    read_times: StorageTimes,
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
                    write_permission: WritePermission::OWNER,
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
    ) -> Result<Vec<StoredStorageMutationReceipt>, DomainError> {
        let mut state = self.state();
        state.storage_batches += 1;
        state.last_actor = Some(actor);
        state
            .storage
            .apply_batch(actor, operations)
            .map(|receipts| {
                receipts
                    .into_iter()
                    .map(|receipt| {
                        let timestamp = StorageTimestamp {
                            seconds: 1_700_000_000,
                            nanos: 123_456_000,
                        };
                        StoredStorageMutationReceipt {
                            receipt,
                            times: StorageTimes {
                                create: Some(timestamp),
                                update: Some(timestamp),
                            },
                        }
                    })
                    .collect()
            })
    }

    fn read_storage_objects(
        &mut self,
        actor: StorageActor,
        keys: &[StorageObjectKey],
    ) -> Result<Vec<StoredStorageObject>, DomainError> {
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
        Ok(objects
            .into_iter()
            .map(|object| StoredStorageObject {
                object,
                times: state.read_times,
            })
            .collect())
    }

    fn list_storage_objects_nakama(
        &mut self,
        actor: StorageActor,
        collection: &str,
        owner: Option<UserId>,
        after: Option<&StorageListPosition>,
        limit: usize,
    ) -> Result<StoredStorageClientListPage, DomainError> {
        // This mock only witnesses App routing, principal and drain policy.
        // SQL ACL, ordering and pagination belong to the real DB fixtures.
        assert_eq!(actor, StorageActor::User(USER));
        assert_eq!(collection, "inventory");
        assert_eq!(owner, None);
        assert!(after.is_none());
        assert_eq!(limit, 1);
        let mut state = self.state();
        state.storage_lists += 1;
        state.last_actor = Some(actor);
        Ok(StoredStorageClientListPage {
            objects: Vec::new(),
            next: None,
        })
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
        storage_lists: 0,
        last_actor: None,
        last_read_keys: Vec::new(),
        read_times: StorageTimes::default(),
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

fn list_request(path: &str, authorization: Option<&str>) -> Request {
    let mut request = request(path, authorization, "");
    request.method = "GET".to_owned();
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
fn signed_storage_http_reads_and_deletes_history_without_relaxing_new_write_admission() {
    for (collection, name, strict_key) in [
        ("雪".repeat(128), "🔑".repeat(128), false),
        (
            ".legacy.collection".to_owned(),
            ".legacy.key".to_owned(),
            false,
        ),
        (
            "legacy\ncollection".to_owned(),
            "legacy\tkey".to_owned(),
            false,
        ),
        ("inventory".to_owned(), "legacy".to_owned(), true),
    ] {
        let (mut app, repository) = app();
        let credential = bearer(GENERATION);
        let stored_key = StorageObjectKey::new_nakama(&collection, &name, USER).unwrap();
        let value = br#"{"history":true}"#;
        let receipts = repository
            .state()
            .storage
            .apply_batch(
                StorageActor::Server,
                &[StorageBatchOperation::Write(StorageWriteOperation {
                    key: stored_key.clone(),
                    value: value.to_vec(),
                    expected: VersionCheck::MustNotExist,
                    read_permission: ReadPermission::PUBLIC,
                    write_permission: WritePermission::from_stored(32767).unwrap(),
                })],
            )
            .unwrap();
        let version = receipts[0].current_version.unwrap();
        let read = serde_json::json!({"object_ids":[{
            "collection": collection, "key": name,
            "user_id": "11111111-1111-1111-1111-111111111111",
        }]})
        .to_string();
        let response = app.handle(&read_request("/v2/storage", Some(&credential), &read));
        assert_eq!(response.status, 200);
        let objects = json(&response);
        assert_eq!(objects["objects"][0]["collection"], collection);
        assert_eq!(objects["objects"][0]["key"], name);
        assert_eq!(
            objects["objects"][0]["value"],
            std::str::from_utf8(value).unwrap()
        );
        assert_eq!(objects["objects"][0]["version"], version.as_str());
        assert_eq!(objects["objects"][0]["permission_write"], 32767);
        let write = serde_json::json!({"objects":[{
            "collection": collection, "key": name, "value":"{}",
        }]})
        .to_string();
        let response = app.handle(&request("/v2/storage", Some(&credential), &write));
        assert_eq!(response.status, 400);
        assert_eq!(json(&response)["code"], 3);
        assert_eq!(
            json(&response)["message"],
            if strict_key {
                "Storage write rejected - permission denied."
            } else {
                "Invalid collection or key value supplied. They must be set."
            }
        );
        {
            let state = repository.state();
            let unchanged = state
                .storage
                .read(StorageActor::Server, &stored_key)
                .unwrap();
            assert_eq!(unchanged.value, value);
            assert_eq!(unchanged.version.as_str(), version.as_str());
            assert_eq!(unchanged.write_permission.get(), 32767);
        }
        let delete = serde_json::json!({"object_ids":[{
            "collection": collection, "key": name, "version": version.as_str(),
            "user_id":"99999999-9999-9999-9999-999999999999",
        }]})
        .to_string();
        let response = app.handle(&request("/v2/storage/delete", Some(&credential), &delete));
        assert_eq!(response.status, 200);
        assert_eq!(json(&response), serde_json::json!({}));
        let state = repository.state();
        assert_eq!(state.verified_sessions, 3);
        assert_eq!(state.storage_reads, 1);
        assert_eq!(state.storage_batches, if strict_key { 2 } else { 1 });
        assert_eq!(state.last_read_keys, [stored_key]);
        assert_eq!(state.last_actor, Some(StorageActor::User(USER)));
        assert_eq!(state.storage.object_count(), 0);
    }
    for (collection, name) in [("", "key"), ("collection", "")] {
        let (mut app, repository) = app();
        let credential = bearer(GENERATION);
        let body = serde_json::json!({"object_ids":[{
            "collection":collection, "key":name,
        }]})
        .to_string();
        for request in [
            read_request("/v2/storage", Some(&credential), &body),
            request("/v2/storage/delete", Some(&credential), &body),
        ] {
            let response = app.handle(&request);
            assert_eq!(response.status, 400);
            assert_eq!(json(&response)["code"], 3);
        }
        let state = repository.state();
        assert_eq!(state.verified_sessions, 2);
        assert_eq!(state.storage_reads, 0);
        assert_eq!(state.storage_batches, 0);
    }
}

fn opaque_condition_tokens(value: &[u8]) -> [String; 5] {
    let stored = ContentVersion::from_value(value);
    let upper = stored.as_str().to_ascii_uppercase();
    assert_ne!(upper, stored.as_str());
    [
        upper,
        "g".repeat(32),
        "版本🔒".to_owned(),
        format!("{}suffix", stored.as_str()),
        "opaque-condition-".repeat(256),
    ]
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
fn opaque_write_conditions_reach_storage_occ_and_acl_after_authentication() {
    for token in opaque_condition_tokens(b"{}") {
        for permission in [
            None,
            Some(WritePermission::OWNER),
            Some(WritePermission::NONE),
        ] {
            let (mut app, repository) = app();
            if let Some(permission) = permission {
                repository
                    .state()
                    .storage
                    .apply_batch(
                        StorageActor::Server,
                        &[StorageBatchOperation::Write(StorageWriteOperation {
                            key: key("opaque"),
                            value: b"{}".to_vec(),
                            expected: VersionCheck::MustNotExist,
                            read_permission: ReadPermission::PUBLIC,
                            write_permission: permission,
                        })],
                    )
                    .unwrap();
            }
            let before = repository.state().storage.clone();
            let input = serde_json::json!({"objects":[{
                "collection":"inventory", "key":"opaque", "value":"{\"new\":true}",
                "version":token
            }]})
            .to_string();
            let response = app.handle(&request("/v2/storage", Some(&bearer(GENERATION)), &input));
            assert_eq!(response.status, 400);
            assert_eq!(json(&response)["code"], 3);
            assert_eq!(
                json(&response)["message"],
                if permission == Some(WritePermission::NONE) {
                    "Storage write rejected - permission denied."
                } else {
                    "Storage write rejected - version check failed."
                }
            );
            assert!(json(&response).get("acks").is_none());
            let state = repository.state();
            assert_eq!(state.verified_sessions, 1);
            assert_eq!(state.storage_batches, 1, "opaque tokens must reach storage");
            assert_eq!(state.storage, before);
        }
    }
}

#[test]
fn opaque_delete_conditions_including_star_are_literal_and_reach_storage() {
    for token in opaque_condition_tokens(b"{}")
        .into_iter()
        .chain(std::iter::once("*".to_owned()))
    {
        for permission in [
            None,
            Some(WritePermission::OWNER),
            Some(WritePermission::NONE),
        ] {
            let (mut app, repository) = app();
            if let Some(permission) = permission {
                repository
                    .state()
                    .storage
                    .apply_batch(
                        StorageActor::Server,
                        &[StorageBatchOperation::Write(StorageWriteOperation {
                            key: key("opaque"),
                            value: b"{}".to_vec(),
                            expected: VersionCheck::MustNotExist,
                            read_permission: ReadPermission::PUBLIC,
                            write_permission: permission,
                        })],
                    )
                    .unwrap();
            }
            let before = repository.state().storage.clone();
            let input = serde_json::json!({"object_ids":[{
                "collection":"inventory", "key":"opaque", "version":token
            }]})
            .to_string();
            let response = app.handle(&request(
                "/v2/storage/delete",
                Some(&bearer(GENERATION)),
                &input,
            ));
            assert_eq!(response.status, 400);
            assert_eq!(
                json(&response),
                serde_json::json!({"code":3,"message":
                    "Storage delete rejected - not found, version check failed, or permission denied."})
            );
            let state = repository.state();
            assert_eq!(state.verified_sessions, 1);
            assert_eq!(
                state.storage_batches, 1,
                "literal tokens must reach storage"
            );
            assert_eq!(state.storage, before);
        }
    }
}

#[test]
fn absent_null_and_empty_conditions_keep_unconditional_write_and_delete_semantics() {
    for version in [
        None,
        Some(serde_json::Value::Null),
        Some(serde_json::json!("")),
    ] {
        let (mut app, repository) = app();
        repository.seed("opaque", USER, ReadPermission::PUBLIC, "{}");
        let mut write =
            serde_json::json!({"collection":"inventory","key":"opaque","value":"{\"new\":true}"});
        let mut delete = serde_json::json!({"collection":"inventory","key":"opaque"});
        if let Some(version) = version {
            write["version"] = version.clone();
            delete["version"] = version;
        }
        let credential = bearer(GENERATION);
        let response = app.handle(&request(
            "/v2/storage",
            Some(&credential),
            &serde_json::json!({"objects":[write]}).to_string(),
        ));
        assert_eq!(response.status, 200);
        assert_eq!(
            json(&response)["acks"][0]["version"],
            ContentVersion::from_value(br#"{"new":true}"#).as_str()
        );
        let response = app.handle(&request(
            "/v2/storage/delete",
            Some(&credential),
            &serde_json::json!({"object_ids":[delete]}).to_string(),
        ));
        assert_eq!(response.status, 200);
        let state = repository.state();
        assert_eq!(state.verified_sessions, 2);
        assert_eq!(state.storage_batches, 2);
        assert_eq!(state.storage.object_count(), 0);
    }
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
        ("shared", USER, ReadPermission::OWNER, own_value),
        ("shared", other, ReadPermission::PUBLIC, other_value),
        ("shared", global, ReadPermission::PUBLIC, global_value),
        ("own-hidden", USER, ReadPermission::NONE, "{}"),
        ("other-private", other, ReadPermission::OWNER, "{}"),
        ("other-hidden", other, ReadPermission::NONE, "{}"),
        ("global-private", global, ReadPermission::OWNER, "{}"),
        ("global-hidden", global, ReadPermission::NONE, "{}"),
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
fn read_projects_valid_fraction_to_seconds_omits_unknown_and_rejects_invalid_times() {
    let (mut app, repository) = app();
    repository.seed("sword", USER, ReadPermission::OWNER, "{}");
    let body = r#"{"object_ids":[{"collection":"inventory","key":"sword","user_id":"11111111-1111-1111-1111-111111111111"}]}"#;
    let request = read_request("/v2/storage", Some(&bearer(GENERATION)), body);
    let known = StorageTimes {
        create: Some(StorageTimestamp {
            seconds: -1,
            nanos: 999_999_000,
        }),
        update: Some(StorageTimestamp {
            seconds: 1_700_000_000,
            nanos: 123_456_000,
        }),
    };
    for times in [
        known,
        StorageTimes::default(),
        StorageTimes {
            create: None,
            update: known.update,
        },
    ] {
        repository.state().read_times = times;
        let response = app.handle(&request);
        assert_eq!(response.status, 200);
        let encoded = json(&response);
        let object = &encoded["objects"][0];
        for (field, timestamp, expected) in [
            ("create_time", times.create, "1969-12-31T23:59:59Z"),
            ("update_time", times.update, "2023-11-14T22:13:20Z"),
        ] {
            if let Some(timestamp) = timestamp {
                assert_eq!(object[field], expected);
                let projected: prost_types::Timestamp =
                    object[field].as_str().unwrap().parse().unwrap();
                assert_eq!(projected.seconds, timestamp.seconds);
                assert_eq!(projected.nanos, 0);
            } else {
                assert!(
                    object.get(field).is_none(),
                    "unknown {field} must stay absent"
                );
            }
        }
    }
    for bad in [
        StorageTimestamp {
            seconds: 0,
            nanos: 1_000_000_000,
        },
        StorageTimestamp {
            seconds: -62_135_596_801,
            nanos: 0,
        },
        StorageTimestamp {
            seconds: 253_402_300_800,
            nanos: 0,
        },
    ] {
        for times in [
            StorageTimes {
                create: Some(bad),
                update: known.update,
            },
            StorageTimes {
                create: known.create,
                update: Some(bad),
            },
        ] {
            repository.state().read_times = times;
            let response = app.handle(&request);
            assert_eq!(response.status, 500);
            assert_eq!(
                json(&response),
                serde_json::json!({"code":13,"message":"Error reading storage objects."})
            );
            assert!(json(&response).get("objects").is_none());
        }
    }
    let state = repository.state();
    assert_eq!(state.storage_reads, 9);
    assert_eq!(state.verified_sessions, 9);
    assert_eq!(state.storage_batches, 0);
    assert_eq!(state.storage.object_count(), 1);
}

#[test]
fn missing_empty_and_null_read_owner_select_global_objects() {
    for owner_field in ["", r#", "user_id":"""#, r#", "userId":null"#] {
        let (mut app, repository) = app();
        repository.seed(
            "shared",
            USER,
            ReadPermission::OWNER,
            r#"{"owner":"caller"}"#,
        );
        repository.seed(
            "shared",
            UserId::new([0; 16]),
            ReadPermission::PUBLIC,
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
            ReadPermission::OWNER,
            r#"{"owner":"caller"}"#,
        );
        repository.seed(
            "shared",
            UserId::new([0; 16]),
            ReadPermission::PUBLIC,
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
        ReadPermission::PUBLIC,
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
    let listed = app.handle(&list_request("/v2/storage/inventory", Some(&credential)));
    assert_eq!(listed.status, 200);
    assert_eq!(listed.body, b"{}");
    let unauthenticated_list = app.handle(&list_request(
        "/v2/storage/inventory?limit=bad&cursor=!",
        None,
    ));
    assert_eq!(unauthenticated_list.status, 401);
    assert_eq!(json(&unauthenticated_list)["code"], 16);
    let state = repository.state();
    assert_eq!(state.verified_sessions, 3);
    assert_eq!(state.storage_reads, 1);
    assert_eq!(state.storage_lists, 1);
    assert_eq!(state.last_actor, Some(StorageActor::User(USER)));
    assert_eq!(state.storage_batches, 0);
    assert_eq!(state.storage.object_count(), 1);
}

const LIVE_COLLECTION: &str = "canonical-storage-api-live";
const LIVE_USER: UserId = UserId::new([0xd1; 16]);
const LIVE_OTHER: UserId = UserId::new([0xd2; 16]);
const LIVE_FAMILY: SessionFamilyId = SessionFamilyId::new([0xe1; 16]);
const LIVE_USER_UUID: &str = "d1d1d1d1-d1d1-d1d1-d1d1-d1d1d1d1d1d1";
const LIVE_OTHER_UUID: &str = "d2d2d2d2-d2d2-d2d2-d2d2-d2d2d2d2d2d2";
const LIVE_GLOBAL_UUID: &str = "00000000-0000-0000-0000-000000000000";

fn storage_live_list_page(response: &Response) -> (Vec<(String, String, i32)>, Option<String>) {
    assert_eq!(response.status, 200);
    let body = json(response);
    let objects = body["objects"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|object| {
            assert_eq!(object["collection"], LIVE_COLLECTION);
            assert!(object["value"].as_str().is_some());
            assert!(object["version"].as_str().is_some());
            for field in ["create_time", "update_time"] {
                let timestamp = object[field]
                    .as_str()
                    .expect("v3 writer supplies database time");
                let parsed: prost_types::Timestamp = timestamp.parse().unwrap();
                assert_eq!(parsed.nanos, 0, "upstream read/list seconds projection");
            }
            (
                object["key"].as_str().unwrap().to_owned(),
                object["user_id"].as_str().unwrap().to_owned(),
                i32::try_from(object["permission_read"].as_i64().unwrap()).unwrap(),
            )
        })
        .collect();
    let cursor = body.get("cursor").map(|cursor| {
        let cursor = cursor.as_str().unwrap();
        assert!(!cursor.is_empty());
        cursor.to_owned()
    });
    (objects, cursor)
}

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
            "SELECT value_jsonb::TEXT, value_projection_digest FROM public.trnm_storage_objects \
             WHERE collection = $1 AND object_key = $2 AND user_id = $3",
            &[&LIVE_COLLECTION, &name, &owner.as_bytes().as_slice()],
        )
        .unwrap_or_else(|_| panic!("canonical storage fixture: persisted object query failed"))
        .map(|row| {
            let value: String = row.get(0);
            let digest: Vec<u8> = row.get(1);
            assert_eq!(
                digest,
                IntegrityDigest::from_value(value.as_bytes())
                    .get()
                    .as_bytes()
                    .as_slice()
            );
            value.into_bytes()
        })
}

#[derive(Debug, Eq, PartialEq)]
struct LiveStorageSnapshot {
    key: String,
    owner: Vec<u8>,
    value: String,
    public_version: String,
    projection_integrity: Vec<u8>,
    value_origin: String,
    source_manifest: Option<Vec<u8>>,
    raw_value: Option<Vec<u8>>,
    raw_integrity: Option<Vec<u8>>,
    read_permission: i16,
    write_permission: i16,
    updated_at_ms: i64,
    create_micros: Option<i64>,
    update_micros: Option<i64>,
}

fn storage_live_snapshot(control: &mut postgres::Client) -> Vec<LiveStorageSnapshot> {
    control
        .query(
            "SELECT object_key, user_id, value_jsonb::TEXT, public_version, \
             value_projection_digest, value_origin, source_manifest_digest, value_bytes, \
             version_digest, read_permission, \
             write_permission, updated_at_ms, \
             (extract(epoch FROM create_time)*1000000)::BIGINT, \
             (extract(epoch FROM update_time)*1000000)::BIGINT \
             FROM public.trnm_storage_objects WHERE collection = $1 \
             ORDER BY object_key, user_id",
            &[&LIVE_COLLECTION],
        )
        .unwrap_or_else(|_| panic!("canonical storage fixture: complete snapshot failed"))
        .into_iter()
        .map(|row| LiveStorageSnapshot {
            key: row.get(0),
            owner: row.get(1),
            value: row.get(2),
            public_version: row.get(3),
            projection_integrity: row.get(4),
            value_origin: row.get(5),
            source_manifest: row.get(6),
            raw_value: row.get(7),
            raw_integrity: row.get(8),
            read_permission: row.get(9),
            write_permission: row.get(10),
            updated_at_ms: row.get(11),
            create_micros: row.get(12),
            update_micros: row.get(13),
        })
        .collect()
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

include!("storage_api_v3_live.rs");

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
                ReadPermission::PUBLIC,
                r#"{"owner":"other-public"}"#,
            ),
            (
                "other-private",
                LIVE_OTHER,
                ReadPermission::OWNER,
                r#"{"owner":"other-private"}"#,
            ),
            ("other-hidden", LIVE_OTHER, ReadPermission::NONE, "{}"),
            ("own-hidden", LIVE_USER, ReadPermission::NONE, "{}"),
            ("a-own-public", LIVE_USER, ReadPermission::PUBLIC, "{}"),
            ("z-own-private", LIVE_USER, ReadPermission::OWNER, "{}"),
            ("other-public-2", LIVE_OTHER, ReadPermission::PUBLIC, "{}"),
            (
                "global-public",
                UserId::new([0; 16]),
                ReadPermission::PUBLIC,
                r#"{"owner":"global"}"#,
            ),
            (
                "global-private",
                UserId::new([0; 16]),
                ReadPermission::OWNER,
                "{}",
            ),
            (
                "global-public-2",
                UserId::new([0; 16]),
                ReadPermission::PUBLIC,
                "{}",
            ),
        ] {
            seed_storage_known_native(&mut control, name, owner, permission, value);
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
        let rendered_original = native_storage_render(&mut control, original);
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
        assert_ne!(
            ContentVersion::from_value(original.as_bytes()),
            ContentVersion::from_value(rendered_original.as_bytes())
        );
        let persisted = control.query_one(
            "SELECT (extract(epoch FROM create_time)*1000000)::BIGINT, (extract(epoch FROM update_time)*1000000)::BIGINT FROM trnm_storage_objects WHERE collection=$1 AND object_key='sword' AND user_id=$2",
            &[&LIVE_COLLECTION, &LIVE_USER.as_bytes().as_slice()],
        ).unwrap();
        for (index, field) in ["create_time", "update_time"].into_iter().enumerate() {
            let timestamp: prost_types::Timestamp = json(&write)["acks"][0][field]
                .as_str()
                .unwrap()
                .parse()
                .unwrap();
            assert_eq!(timestamp.nanos % 1000, 0);
            assert_eq!(
                timestamp.seconds * 1_000_000 + i64::from(timestamp.nanos) / 1000,
                persisted.get::<_, i64>(index)
            );
        }

        assert_eq!(
            persisted_storage_value(&mut control, "sword", LIVE_USER),
            Some(rendered_original.as_bytes().to_vec())
        );
        assert_eq!(
            persisted_storage_value(&mut control, "sword", LIVE_OTHER),
            None
        );
        // An independent SQL sentinel makes the no-op assertion deterministic
        // without relying on wall-clock resolution or sleeping between calls.
        assert_eq!(
            control
                .execute(
                    "UPDATE trnm_storage_objects SET updated_at_ms = 7 \
                     WHERE collection = $1 AND object_key = 'sword' AND user_id = $2",
                    &[&LIVE_COLLECTION, &LIVE_USER.as_bytes().as_slice()],
                )
                .unwrap(),
            1
        );
        let mut repeated: serde_json::Value = serde_json::from_str(&write_body).unwrap();
        repeated["objects"][0]["version"] = serde_json::json!("");
        let blind = app.handle(&request(
            "/v2/storage",
            Some(&credential),
            &repeated.to_string(),
        ));
        assert_eq!(blind.status, 200);
        assert_eq!(
            json(&blind)["acks"],
            json(&write)["acks"],
            "blind no-op returns original precise database times"
        );
        let unchanged: i64 = control
            .query_one(
                "SELECT updated_at_ms FROM trnm_storage_objects \
                 WHERE collection = $1 AND object_key = 'sword' AND user_id = $2",
                &[&LIVE_COLLECTION, &LIVE_USER.as_bytes().as_slice()],
            )
            .unwrap()
            .get(0);
        assert_eq!(unchanged, 7);
        repeated["objects"][0]["version"] = serde_json::json!(version);
        let exact = app.handle(&request(
            "/v2/storage",
            Some(&credential),
            &repeated.to_string(),
        ));
        assert_eq!(exact.status, 200);
        assert_eq!(json(&exact)["acks"][0]["version"], version);
        let refreshed: i64 = control
            .query_one(
                "SELECT updated_at_ms FROM trnm_storage_objects \
                 WHERE collection = $1 AND object_key = 'sword' AND user_id = $2",
                &[&LIVE_COLLECTION, &LIVE_USER.as_bytes().as_slice()],
            )
            .unwrap()
            .get(0);
        assert!(refreshed > unchanged);

        // These are request conditions, not stored ContentVersion values.
        // Existing locked rows expose ACL precedence; missing/writable rows
        // expose literal OCC mismatch. No token is normalized or length-capped.
        assert_eq!(
            control
                .execute(
                    "UPDATE public.trnm_storage_objects SET write_permission = 0 \
                     WHERE collection = $1 AND object_key = 'own-hidden' AND user_id = $2",
                    &[&LIVE_COLLECTION, &LIVE_USER.as_bytes().as_slice()],
                )
                .unwrap(),
            1
        );
        let before_opaque = storage_live_snapshot(&mut control);
        let conditions = opaque_condition_tokens(original.as_bytes());
        for condition in &conditions {
            for (name, message) in [
                (
                    "opaque-missing",
                    "Storage write rejected - version check failed.",
                ),
                ("sword", "Storage write rejected - version check failed."),
                ("own-hidden", "Storage write rejected - permission denied."),
            ] {
                let input = serde_json::json!({"objects":[{
                    "collection":LIVE_COLLECTION,"key":name,"value":"{\"opaque\":true}",
                    "version":condition
                }]})
                .to_string();
                let response = app.handle(&request("/v2/storage", Some(&credential), &input));
                assert_eq!(response.status, 400);
                assert_eq!(
                    json(&response),
                    serde_json::json!({"code":3,"message":message})
                );
                assert_eq!(storage_live_snapshot(&mut control), before_opaque);
            }
        }
        for condition in conditions
            .iter()
            .map(String::as_str)
            .chain(std::iter::once("*"))
        {
            for name in ["opaque-missing", "sword", "own-hidden"] {
                let input = serde_json::json!({"object_ids":[{
                    "collection":LIVE_COLLECTION,"key":name,"version":condition
                }]})
                .to_string();
                let response =
                    app.handle(&request("/v2/storage/delete", Some(&credential), &input));
                assert_eq!(response.status, 400);
                assert_eq!(
                    json(&response),
                    serde_json::json!({"code":3,"message":
                    "Storage delete rejected - not found, version check failed, or permission denied."})
                );
                assert_eq!(storage_live_snapshot(&mut control), before_opaque);
            }
        }
        // A later opaque OCC mismatch rolls back a staged real update/delete,
        // including both database timestamps and the extension clock field.
        let opaque_write_batch = serde_json::json!({"objects":[
            {"collection":LIVE_COLLECTION,"key":"sword","value":"{\"opaque\":true}"},
            {"collection":LIVE_COLLECTION,"key":"opaque-missing","value":"{}","version":conditions[4]}
        ]})
        .to_string();
        let opaque_delete_batch = serde_json::json!({"object_ids":[
            {"collection":LIVE_COLLECTION,"key":"sword","version":version},
            {"collection":LIVE_COLLECTION,"key":"opaque-missing","version":conditions[4]}
        ]})
        .to_string();
        for (path, input, message) in [
            (
                "/v2/storage",
                opaque_write_batch,
                "Storage write rejected - version check failed.",
            ),
            (
                "/v2/storage/delete",
                opaque_delete_batch,
                "Storage delete rejected - not found, version check failed, or permission denied.",
            ),
        ] {
            let response = app.handle(&request(path, Some(&credential), &input));
            assert_eq!(response.status, 400);
            assert_eq!(
                json(&response),
                serde_json::json!({"code":3,"message":message})
            );
            assert_eq!(storage_live_snapshot(&mut control), before_opaque);
        }
        assert_eq!(
            control
                .execute(
                    "UPDATE public.trnm_storage_objects SET write_permission = 1 \
                     WHERE collection = $1 AND object_key = 'own-hidden' AND user_id = $2",
                    &[&LIVE_COLLECTION, &LIVE_USER.as_bytes().as_slice()],
                )
                .unwrap(),
            1
        );
        println!(
            "\nstorage_opaque_conditions_live_executed profile={} write_cases=15 delete_cases=18 batch_cases=2",
            profile.metadata_value()
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
                assert_eq!(object["collection"], LIVE_COLLECTION);
                let name = object["key"].as_str().unwrap();
                let owner =
                    super::storage_api::parse_uuid(object["user_id"].as_str().unwrap()).unwrap();
                let persisted = control
                    .query_one(
                        "SELECT floor(extract(epoch FROM create_time))::BIGINT, \
                     floor(extract(epoch FROM update_time))::BIGINT, public_version, value_jsonb::TEXT \
                     FROM public.trnm_storage_objects \
                     WHERE collection = $1 AND object_key = $2 AND user_id = $3",
                        &[&LIVE_COLLECTION, &name, &owner.as_bytes().as_slice()],
                    )
                    .unwrap_or_else(|_| {
                        panic!("canonical storage fixture: read timestamp SQL comparison failed")
                    });
                assert_eq!(object["version"].as_str().unwrap(), persisted.get::<_, String>(2));
                assert_eq!(value.as_bytes(), persisted.get::<_, String>(3).as_bytes());
                for (index, field) in ["create_time", "update_time"].into_iter().enumerate() {
                    let projected: prost_types::Timestamp =
                        object[field].as_str().unwrap().parse().unwrap();
                    assert_eq!(
                        projected.nanos, 0,
                        "batch-read uses upstream seconds projection"
                    );
                    assert_eq!(
                        projected.seconds,
                        persisted.try_get::<_, i64>(index).unwrap()
                    );
                }
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
                    (LIVE_USER_UUID.to_owned(), rendered_original.clone())
                ),
                (
                    "other-public".to_owned(),
                    (
                        LIVE_OTHER_UUID.to_owned(),
                        native_storage_render(&mut control, r#"{"owner":"other-public"}"#)
                    )
                ),
                (
                    "global-public".to_owned(),
                    (
                        "00000000-0000-0000-0000-000000000000".to_owned(),
                        native_storage_render(&mut control, r#"{"owner":"global"}"#)
                    )
                ),
            ])
        );

        // List observes its own source-defined projection: missing owner only
        // exposes public rows, and own lists order read permission before key.
        // All requests enter canonical App authentication and the real DB.
        let own_path = format!("/v2/storage/{LIVE_COLLECTION}/{LIVE_USER_UUID}?limit=2");
        let own_first = app.handle(&list_request(&own_path, Some(&credential)));
        let (own_objects, own_cursor) = storage_live_list_page(&own_first);
        assert_eq!(
            own_objects,
            vec![
                ("sword".to_owned(), LIVE_USER_UUID.to_owned(), 1),
                ("z-own-private".to_owned(), LIVE_USER_UUID.to_owned(), 1),
            ]
        );
        let own_cursor = own_cursor.unwrap();
        let own_position = super::storage_cursor::decode_cursor(&own_cursor).unwrap();
        assert_eq!(own_position.key, "z-own-private");
        assert_eq!(own_position.user_id, LIVE_USER);
        assert_eq!(own_position.read, 1);
        let own_reencoded = super::storage_cursor::encode_cursor(&own_position).unwrap();
        let own_next = app.handle(&list_request(
            &format!("{own_path}&cursor={own_reencoded}"),
            Some(&credential),
        ));
        let (own_objects, own_cursor) = storage_live_list_page(&own_next);
        assert_eq!(
            own_objects,
            vec![("a-own-public".to_owned(), LIVE_USER_UUID.to_owned(), 2)]
        );
        assert!(own_cursor.is_none());

        let all_path = format!("/v2/storage/{LIVE_COLLECTION}?limit=2");
        let all_first = app.handle(&list_request(&all_path, Some(&credential)));
        let (all_objects, all_cursor) = storage_live_list_page(&all_first);
        assert_eq!(
            all_objects,
            vec![
                ("a-own-public".to_owned(), LIVE_USER_UUID.to_owned(), 2),
                ("global-public".to_owned(), LIVE_GLOBAL_UUID.to_owned(), 2),
            ]
        );
        let all_position = super::storage_cursor::decode_cursor(&all_cursor.unwrap()).unwrap();
        assert_eq!(all_position.key, "global-public");
        assert_eq!(all_position.user_id, UserId::new([0; 16]));
        assert_eq!(all_position.read, 2);
        let all_second = app.handle(&list_request(
            &format!(
                "{all_path}&cursor={}",
                super::storage_cursor::encode_cursor(&all_position).unwrap()
            ),
            Some(&credential),
        ));
        let (all_objects, all_cursor) = storage_live_list_page(&all_second);
        assert_eq!(
            all_objects,
            vec![
                ("global-public-2".to_owned(), LIVE_GLOBAL_UUID.to_owned(), 2),
                ("other-public".to_owned(), LIVE_OTHER_UUID.to_owned(), 2),
            ]
        );
        let all_third = app.handle(&list_request(
            &format!("{all_path}&cursor={}", all_cursor.unwrap()),
            Some(&credential),
        ));
        let (all_objects, all_cursor) = storage_live_list_page(&all_third);
        assert_eq!(
            all_objects,
            vec![("other-public-2".to_owned(), LIVE_OTHER_UUID.to_owned(), 2)]
        );
        assert!(all_cursor.is_none());

        // Explicit foreign and global owners both use public-only, key-order
        // pagination. Their private/hidden rows never consume the sentinel.
        for (owner, owner_id, first_key, second_key) in [
            (
                LIVE_OTHER_UUID,
                LIVE_OTHER,
                "other-public",
                "other-public-2",
            ),
            (
                LIVE_GLOBAL_UUID,
                UserId::new([0; 16]),
                "global-public",
                "global-public-2",
            ),
        ] {
            let path = format!("/v2/storage/{LIVE_COLLECTION}/{owner}?limit=1");
            let first = app.handle(&list_request(&path, Some(&credential)));
            let (objects, cursor) = storage_live_list_page(&first);
            assert_eq!(objects, vec![(first_key.to_owned(), owner.to_owned(), 2)]);
            let position = super::storage_cursor::decode_cursor(&cursor.unwrap()).unwrap();
            assert_eq!(position.key, first_key);
            assert_eq!(position.user_id, owner_id);
            assert_eq!(position.read, 2);
            let second = app.handle(&list_request(
                &format!(
                    "{path}&cursor={}",
                    super::storage_cursor::encode_cursor(&position).unwrap()
                ),
                Some(&credential),
            ));
            let (objects, cursor) = storage_live_list_page(&second);
            assert_eq!(objects, vec![(second_key.to_owned(), owner.to_owned(), 2)]);
            assert!(cursor.is_none());
        }

        let empty_owner = app.handle(&list_request(
            &format!("/v2/storage/{LIVE_COLLECTION}?user_id=&limit=100"),
            Some(&credential),
        ));
        let (objects, cursor) = storage_live_list_page(&empty_owner);
        assert_eq!(objects.len(), 5);
        assert!(objects.iter().all(|object| object.2 == 2));
        assert!(cursor.is_none());
        let omitted_limit = app.handle(&list_request(
            &format!("/v2/storage/{LIVE_COLLECTION}"),
            Some(&credential),
        ));
        let (objects, cursor) = storage_live_list_page(&omitted_limit);
        assert_eq!(
            objects,
            vec![("a-own-public".to_owned(), LIVE_USER_UUID.to_owned(), 2)]
        );
        assert!(cursor.is_some());
        let missing_collection = app.handle(&list_request(
            &format!("/v2/storage/{LIVE_COLLECTION}-empty"),
            Some(&credential),
        ));
        assert_eq!(missing_collection.status, 200);
        assert_eq!(missing_collection.body, b"{}");

        let persisted_before_bad_list = storage_live_row_count(&mut control);
        for (path, message) in [
            (
                format!("/v2/storage/{LIVE_COLLECTION}/not-a-uuid"),
                "Invalid user ID - make sure user ID is a valid UUID.",
            ),
            (
                format!("/v2/storage/{LIVE_COLLECTION}?limit=0"),
                "Invalid limit - limit must be between 1 and 100.",
            ),
            (
                format!("/v2/storage/{LIVE_COLLECTION}?cursor=!"),
                "Malformed cursor was used.",
            ),
        ] {
            let malformed = app.handle(&list_request(&path, Some(&credential)));
            assert_eq!(malformed.status, 400);
            assert_eq!(json(&malformed)["code"], 3);
            assert_eq!(json(&malformed)["message"], message);
            for auth in [None, Some("Bearer invalid")] {
                let unauthenticated = app.handle(&list_request(&path, auth));
                assert_eq!(unauthenticated.status, 401);
                assert_eq!(json(&unauthenticated)["code"], 16);
            }
        }
        assert_eq!(
            storage_live_row_count(&mut control),
            persisted_before_bad_list
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
            Some(rendered_original.as_bytes().to_vec())
        );
        assert_eq!(
            persisted_storage_value(&mut control, "shield", LIVE_USER),
            None
        );

        prove_storage_jsonb_v3_app(&mut control, &mut app, &credential, profile);

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
            Some(rendered_original.as_bytes().to_vec())
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
            list_request(&all_path, Some(&credential)),
            list_request(&own_path, Some(&credential)),
            list_request(
                &format!("/v2/storage/{LIVE_COLLECTION}/{LIVE_GLOBAL_UUID}"),
                Some(&credential),
            ),
            list_request(
                &format!("/v2/storage/{LIVE_COLLECTION}?limit=bad&cursor=!"),
                Some(&credential),
            ),
            list_request(
                &format!("/v2/storage/{LIVE_COLLECTION}/not-a-uuid"),
                Some(&credential),
            ),
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
