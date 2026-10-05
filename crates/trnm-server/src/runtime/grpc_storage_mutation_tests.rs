//! Synthetic repository/tonic tests. No live native database or oracle credit.
use super::*;
use crate::runtime::app::SharedDrain;
use crate::runtime::auth_runtime::AuthAuthorityRuntime;
use crate::runtime::config::AuthAuthorityConfig;
use crate::runtime::grpc::generated::google::protobuf::Int32Value;
use crate::runtime::grpc::generated::nakama::api::{
    nakama_client::NakamaClient, nakama_server::Nakama, AccountCustom, AuthenticateCustomRequest,
    DeleteStorageObjectId, SessionLogoutRequest, WriteStorageObject,
};
use crate::runtime::legacy_auth::{
    LegacyCustomAccount, LegacyCustomRepositoryInput, LegacyRepositoryError,
};
use crate::runtime::legacy_config::LegacyServerAuthConfig;
use std::collections::BTreeMap;
use std::sync::{
    atomic::{AtomicBool, AtomicUsize, Ordering},
    Arc, Mutex,
};
use std::time::Duration;
use tonic::{Code, Request};
use trnm_contracts::{Digest32, RetryClass};
use trnm_persistence_pg::{
    CommitOutcome, CommitRequest, EntityHead, EntityId, PublicVersion, StorageMutationReceipt,
    StorageState, StorageTimes,
};

#[derive(Clone, Debug, Default)]
struct FixtureRepository {
    calls: Arc<AtomicUsize>,
    input: Arc<Mutex<Vec<Operation>>>,
    state: Arc<Mutex<StorageState>>,
    result: Option<Result<Vec<StoredStorageMutationReceipt>, DomainError>>,
    unknown_times: bool,
    pause: Option<Arc<Mutex<std::sync::mpsc::Receiver<()>>>>,
}
impl Repository for FixtureRepository {
    fn bootstrap_entity(
        &mut self,
        _: EntityId,
        _: u64,
        _: Digest32,
        _: u64,
    ) -> Result<EntityHead, DomainError> {
        panic!("storage reached authority mutation")
    }
    fn commit_command(&mut self, _: &CommitRequest) -> Result<CommitOutcome, DomainError> {
        panic!("storage reached authority mutation")
    }
    fn authenticate_legacy_custom(
        &mut self,
        _: LegacyCustomRepositoryInput<'_>,
    ) -> Result<LegacyCustomAccount, LegacyRepositoryError> {
        Ok(LegacyCustomAccount {
            user_id: user(),
            stored_username: "player".into(),
            created: false,
        })
    }
    fn apply_storage_batch(
        &mut self,
        _: StorageActor,
        _: &[Operation],
        _: u64,
    ) -> Result<Vec<StoredStorageMutationReceipt>, DomainError> {
        panic!("must use homogeneous Nakama batch, not mixed internal policy")
    }
    fn apply_storage_batch_nakama(
        &mut self,
        actor: StorageActor,
        operations: &[Operation],
        now: u64,
        kind: Kind,
    ) -> Result<Vec<StoredStorageMutationReceipt>, DomainError> {
        assert_eq!(actor, StorageActor::User(user()));
        assert!(now > 0);
        assert!(operations.iter().all(|op| op.key().user_id() == user()));
        self.calls.fetch_add(1, Ordering::AcqRel);
        *self.input.lock().unwrap() = operations.to_vec();
        if let Some(pause) = &self.pause {
            pause
                .lock()
                .unwrap()
                .recv_timeout(Duration::from_secs(3))
                .unwrap();
        }
        if let Some(result) = &self.result {
            return result.clone();
        }
        let receipts = self
            .state
            .lock()
            .unwrap()
            .apply_nakama_batch(actor, operations, kind)?;
        Ok(receipts
            .into_iter()
            .map(|receipt| StoredStorageMutationReceipt {
                receipt,
                times: if self.unknown_times {
                    StorageTimes::default()
                } else {
                    times()
                },
            })
            .collect())
    }
}
fn user() -> UserId {
    UserId::new([7; 16])
}
fn times() -> StorageTimes {
    StorageTimes {
        create: Some(StorageTimestamp::new(-1, 999_999_000).unwrap()),
        update: Some(StorageTimestamp::new(-1, 999_999_000).unwrap()),
    }
}
fn write(name: &str, value: &str, version: &str) -> WriteStorageObject {
    WriteStorageObject {
        collection: "profile".into(),
        key: name.into(),
        value: value.into(),
        version: version.into(),
        permission_read: None,
        permission_write: None,
    }
}
fn writes(objects: Vec<WriteStorageObject>) -> WriteStorageObjectsRequest {
    WriteStorageObjectsRequest { objects }
}
fn delete(name: &str, version: &str) -> DeleteStorageObjectId {
    DeleteStorageObjectId {
        collection: "profile".into(),
        key: name.into(),
        version: version.into(),
    }
}
fn deletes(object_ids: Vec<DeleteStorageObjectId>) -> DeleteStorageObjectsRequest {
    DeleteStorageObjectsRequest { object_ids }
}
fn fail(code: StableCode) -> DomainError {
    DomainError::new(code, "private_database_key_or_sql", RetryClass::SafeBackoff)
}
fn runtime() -> tokio::runtime::Runtime {
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap()
}
fn authority() -> AuthAuthorityRuntime {
    AuthAuthorityRuntime::install(&AuthAuthorityConfig::NakamaLegacy(
        LegacyServerAuthConfig::new(
            b"server".to_vec(),
            b"access".to_vec(),
            b"refresh".to_vec(),
            60,
            3600,
            false,
        )
        .unwrap(),
    ))
    .unwrap()
}
fn service(
    repo: FixtureRepository,
    authority: AuthAuthorityRuntime,
) -> super::super::NativeGrpcService<FixtureRepository> {
    super::super::NativeGrpcService::new(
        repo,
        authority,
        SharedDrain::default(),
        Arc::new(AtomicBool::new(false)),
    )
}
fn authed<T>(body: T, token: &str) -> Request<T> {
    let mut request = Request::new(body);
    request
        .metadata_mut()
        .insert("authorization", format!("Bearer {token}").parse().unwrap());
    request
}
async fn mint(service: &super::super::NativeGrpcService<FixtureRepository>) -> (String, String) {
    let mut request = Request::new(AuthenticateCustomRequest {
        account: Some(AccountCustom {
            id: "custom-id".into(),
            vars: BTreeMap::new(),
        }),
        create: None,
        username: "player".into(),
    });
    request
        .metadata_mut()
        .insert("authorization", "Basic c2VydmVyOg==".parse().unwrap());
    let session = service
        .authenticate_custom(request)
        .await
        .unwrap()
        .into_inner();
    (session.token, session.refresh_token)
}

#[test]
fn complete_upstream_mutation_protobuf_vectors_match_native_codec() {
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../contracts/grpc/nakama-storage-mutation-protobuf-fixtures.json"
    ))
    .unwrap();
    assert!(fixture["cases"].as_array().unwrap().len() >= 20);
    for case in fixture["cases"].as_array().unwrap() {
        let encoded = case["hex"].as_str().unwrap();
        let bytes: Vec<u8> = (0..encoded.len())
            .step_by(2)
            .map(|n| u8::from_str_radix(&encoded[n..n + 2], 16).unwrap())
            .collect();
        let actual = match case["message"].as_str().unwrap() {
            "WriteStorageObjectsRequest" => WriteStorageObjectsRequest::decode(bytes.as_slice())
                .unwrap()
                .encode_to_vec(),
            "DeleteStorageObjectsRequest" => DeleteStorageObjectsRequest::decode(bytes.as_slice())
                .unwrap()
                .encode_to_vec(),
            "StorageObjectAcks" => StorageObjectAcks::decode(bytes.as_slice())
                .unwrap()
                .encode_to_vec(),
            _ => panic!("unregistered mutation reference message"),
        };
        assert_eq!(actual, bytes, "{}", case["id"]);
    }
}

#[test]
fn wrappers_defaults_zero_and_raw_value_reach_one_owner_bound_batch() {
    let raw = " {\"n\":9007199254740993,\"n\":1e999,\"s\":\"\\ud800\"} ";
    let mut repo = FixtureRepository::default();
    let mut zero = write("zero", "{}", "*");
    zero.permission_read = Some(Int32Value { value: 0 });
    zero.permission_write = Some(Int32Value { value: 0 });
    let response =
        write_objects(&mut repo, writes(vec![write("z", raw, ""), zero]), user()).unwrap();
    assert_eq!(repo.calls.load(Ordering::Acquire), 1);
    let operations = repo.input.lock().unwrap();
    let Operation::Write(first) = &operations[0] else {
        panic!()
    };
    assert_eq!(first.value, raw.as_bytes());
    assert_eq!(first.read_permission, ReadPermission::OWNER);
    assert_eq!(first.write_permission, WritePermission::OWNER);
    assert_eq!(first.expected, VersionCheck::Any);
    let Operation::Write(second) = &operations[1] else {
        panic!()
    };
    assert_eq!(second.read_permission, ReadPermission::NONE);
    assert_eq!(second.write_permission, WritePermission::NONE);
    assert_eq!(second.expected, VersionCheck::MustNotExist);
    assert_eq!(
        response
            .acks
            .iter()
            .map(|ack| ack.key.as_str())
            .collect::<Vec<_>>(),
        ["z", "zero"]
    );
    assert_eq!(
        response.acks[0].version,
        ContentVersion::from_value(raw.as_bytes()).as_str()
    );
    assert_eq!(response.acks[0].user_id, uuid_string(user()));
    assert_eq!(
        response.acks[0].create_time,
        Some(Timestamp {
            seconds: -1,
            nanos: 999_999_000
        })
    );
}

#[test]
fn validation_precedence_and_whole_batch_validation_precede_native_work() {
    let mut repo = FixtureRepository::default();
    let mut invalid = write("", "bad", "");
    invalid.permission_read = Some(Int32Value { value: -1 });
    invalid.permission_write = Some(Int32Value { value: 2 });
    for (field, message) in [
        (0, INVALID_KEYS),
        (1, INVALID_READ),
        (2, INVALID_WRITE),
        (3, INVALID_VALUE),
    ] {
        if field == 1 {
            invalid.key = "k".into();
        }
        if field == 2 {
            invalid.permission_read = None;
        }
        if field == 3 {
            invalid.permission_write = None;
        }
        let error = write_objects(
            &mut repo,
            writes(vec![
                write("valid", "{}", "opaque\0condition"),
                invalid.clone(),
            ]),
            user(),
        )
        .unwrap_err();
        assert_eq!(error.code(), Code::InvalidArgument);
        assert_eq!(error.message(), message);
    }
    for raw in [
        " ",
        "[]",
        "null",
        "1",
        "true",
        "\"x\"",
        "{}{}",
        "{\"x\":NaN}",
    ] {
        assert_eq!(
            write_objects(&mut repo, writes(vec![write("k", raw, "")]), user())
                .unwrap_err()
                .message(),
            INVALID_VALUE
        );
    }
    assert_eq!(
        delete_objects(
            &mut repo,
            deletes(vec![delete("k", ""), delete("", "")]),
            user()
        )
        .unwrap_err()
        .message(),
        INVALID_KEYS
    );
    assert_eq!(repo.calls.load(Ordering::Acquire), 0);
    assert_eq!(repo.state.lock().unwrap().object_count(), 0);
}

#[test]
fn empty_batches_zero_identity_and_local_bounds_skip_repository() {
    let mut repo = FixtureRepository::default();
    assert!(write_objects(&mut repo, writes(vec![]), user())
        .unwrap()
        .acks
        .is_empty());
    delete_objects(&mut repo, deletes(vec![]), user()).unwrap();
    assert_eq!(
        write_objects(&mut repo, writes(vec![]), UserId::new([0; 16]))
            .unwrap_err()
            .code(),
        Code::Unauthenticated
    );
    assert_eq!(
        delete_objects(&mut repo, deletes(vec![]), UserId::new([0; 16]))
            .unwrap_err()
            .code(),
        Code::Unauthenticated
    );
    for input in [
        writes(vec![write("k", "{}", ""); 101]),
        writes(vec![write(&"a".repeat(129), "{}", "")]),
        writes(vec![write(
            "k",
            &format!("{{\"s\":\"{}\"}}", "a".repeat(MAX_VALUE_BYTES)),
            "",
        )]),
    ] {
        assert_eq!(
            write_objects(&mut repo, input, user()).unwrap_err().code(),
            Code::ResourceExhausted
        );
    }
    for input in [
        deletes(vec![delete("k", ""); 101]),
        deletes(vec![delete(&"界".repeat(129), "")]),
    ] {
        assert_eq!(
            delete_objects(&mut repo, input, user()).unwrap_err().code(),
            Code::ResourceExhausted
        );
    }
    assert_eq!(repo.calls.load(Ordering::Acquire), 0);
}

#[test]
fn unicode_and_control_key_domain_is_not_the_internal_identifier_grammar() {
    let mut repo = FixtureRepository::default();
    for name in ["界".repeat(128), ".hidden\u{1}".into()] {
        write_objects(&mut repo, writes(vec![write(&name, "{}", "")]), user()).unwrap();
        delete_objects(&mut repo, deletes(vec![delete(&name, "")]), user()).unwrap();
    }
    assert_eq!(repo.calls.load(Ordering::Acquire), 4);
}

#[test]
fn opaque_versions_and_delete_star_are_never_parsed_or_normalized() {
    for value in [
        "*".to_owned(),
        "ABC".repeat(50),
        "opaque\0condition".into(),
        "MiXeD".into(),
    ] {
        let mut repo = FixtureRepository {
            result: Some(Err(fail(StableCode::Internal))),
            ..Default::default()
        };
        let error =
            delete_objects(&mut repo, deletes(vec![delete("k", &value)]), user()).unwrap_err();
        assert_eq!(error.message(), DELETE_FAILURE);
        let operations = repo.input.lock().unwrap();
        let Operation::Delete(operation) = &operations[0] else {
            panic!()
        };
        assert_eq!(operation.expected_version.as_ref().unwrap().as_str(), value);
        drop(operations);
        let _ =
            write_objects(&mut repo, writes(vec![write("k", "{}", &value)]), user()).unwrap_err();
        let operations = repo.input.lock().unwrap();
        let Operation::Write(operation) = &operations[0] else {
            panic!()
        };
        assert_eq!(
            operation.expected,
            if value == "*" {
                VersionCheck::MustNotExist
            } else {
                VersionCheck::Exact(value.into())
            }
        );
    }
}

#[test]
fn repeated_occurrences_use_existing_core_acl_occ_and_atomic_batch_model() {
    let mut repo = FixtureRepository::default();
    let first = write_objects(
        &mut repo,
        writes(vec![write("k", "{}", ""), write("k", "{\"v\":1}", "")]),
        user(),
    )
    .unwrap();
    assert_eq!(first.acks.len(), 2);
    assert_ne!(first.acks[0].version, first.acks[1].version);
    assert_eq!(repo.state.lock().unwrap().object_count(), 1);
    let before = repo.state.lock().unwrap().clone();
    let error = write_objects(
        &mut repo,
        writes(vec![write("a-new", "{}", ""), write("k", "{}", "bad")]),
        user(),
    )
    .unwrap_err();
    assert_eq!(error.code(), Code::InvalidArgument);
    assert_eq!(*repo.state.lock().unwrap(), before);
    let error = delete_objects(
        &mut repo,
        deletes(vec![delete("k", ""), delete("k", "")]),
        user(),
    )
    .unwrap_err();
    assert_eq!(error.code(), Code::InvalidArgument);
    assert_eq!(*repo.state.lock().unwrap(), before);
    let mut locked = write("k", "{}", &first.acks[1].version);
    locked.permission_write = Some(Int32Value { value: 0 });
    write_objects(&mut repo, writes(vec![locked]), user()).unwrap();
    assert_eq!(
        write_objects(&mut repo, writes(vec![write("k", "{}", "")]), user())
            .unwrap_err()
            .message(),
        "Storage write rejected - permission denied."
    );
    assert_eq!(
        delete_objects(&mut repo, deletes(vec![delete("k", "")]), user())
            .unwrap_err()
            .message(),
        "Storage delete rejected - not found, version check failed, or permission denied."
    );
}

#[test]
fn native_errors_are_redacted_and_never_retried() {
    for code in [
        StableCode::AlreadyExists,
        StableCode::FailedPrecondition,
        StableCode::PermissionDenied,
        StableCode::NotFound,
        StableCode::InvalidArgument,
        StableCode::Unavailable,
        StableCode::Aborted,
        StableCode::Internal,
        StableCode::DataLoss,
        StableCode::ResourceExhausted,
    ] {
        let mut repo = FixtureRepository {
            result: Some(Err(fail(code))),
            ..Default::default()
        };
        let error =
            write_objects(&mut repo, writes(vec![write("k", "{}", "")]), user()).unwrap_err();
        let expected = if code == StableCode::ResourceExhausted {
            Code::ResourceExhausted
        } else {
            Code::Internal
        };
        assert_eq!(error.code(), expected);
        assert!(!error.message().contains("private"));
        let error = delete_objects(&mut repo, deletes(vec![delete("k", "")]), user()).unwrap_err();
        assert_eq!(error.code(), expected);
        assert!(!error.message().contains("private"));
        assert_eq!(repo.calls.load(Ordering::Acquire), 2);
    }
}

fn receipt() -> StoredStorageMutationReceipt {
    StoredStorageMutationReceipt {
        receipt: StorageMutationReceipt {
            key: key("profile".into(), "k".into(), user()).unwrap(),
            previous_version: None,
            current_version: Some(ContentVersion::from_value(b"{}")),
        },
        times: times(),
    }
}
#[test]
fn malformed_write_receipts_fail_closed_without_retry_or_partial_ack() {
    let mut bad = Vec::new();
    let mut row = receipt();
    row.receipt.key = key("other".into(), "k".into(), user()).unwrap();
    bad.push(vec![row]);
    let mut row = receipt();
    row.receipt.current_version = None;
    bad.push(vec![row]);
    let mut row = receipt();
    row.receipt.current_version = Some(ContentVersion::from_value(b"[]"));
    bad.push(vec![row]);
    let mut row = receipt();
    row.times.create = None;
    bad.push(vec![row]);
    let mut row = receipt();
    row.times.update = None;
    bad.push(vec![row]);
    let mut row = receipt();
    row.times.update.as_mut().unwrap().nanos = 1_000_000_000;
    bad.push(vec![row]);
    let mut row = receipt();
    row.times.create.as_mut().unwrap().seconds = i64::MIN;
    bad.push(vec![row]);
    let mut row = receipt();
    row.times.update.as_mut().unwrap().seconds += 1;
    bad.push(vec![row]);
    bad.push(vec![]);
    bad.push(vec![receipt(), receipt()]);
    for rows in bad {
        let mut repo = FixtureRepository {
            result: Some(Ok(rows)),
            ..Default::default()
        };
        assert_eq!(
            write_objects(&mut repo, writes(vec![write("k", "{}", "")]), user())
                .unwrap_err()
                .code(),
            Code::Internal
        );
        assert_eq!(repo.calls.load(Ordering::Acquire), 1);
    }
    for version in ["*", "expected"] {
        let mut row = receipt();
        row.receipt.previous_version = Some(PublicVersion::parse("wrong").unwrap());
        let mut repo = FixtureRepository {
            result: Some(Ok(vec![row])),
            ..Default::default()
        };
        assert_eq!(
            write_objects(&mut repo, writes(vec![write("k", "{}", version)]), user())
                .unwrap_err()
                .code(),
            Code::Internal
        );
    }
}

#[test]
fn malformed_delete_receipts_fail_closed_without_retry() {
    let mut valid = receipt();
    valid.receipt.previous_version = Some(PublicVersion::parse("v").unwrap());
    valid.receipt.current_version = None;
    let mut wrong_key = valid.clone();
    wrong_key.receipt.key = key("other".into(), "k".into(), user()).unwrap();
    let mut missing = valid.clone();
    missing.receipt.previous_version = None;
    let mut current = valid.clone();
    current.receipt.current_version = Some(ContentVersion::from_value(b"{}"));
    for rows in [
        vec![],
        vec![valid.clone(), valid.clone()],
        vec![wrong_key],
        vec![missing],
        vec![current],
    ] {
        let mut repo = FixtureRepository {
            result: Some(Ok(rows)),
            ..Default::default()
        };
        assert_eq!(
            delete_objects(&mut repo, deletes(vec![delete("k", "v")]), user())
                .unwrap_err()
                .code(),
            Code::Internal
        );
        assert_eq!(repo.calls.load(Ordering::Acquire), 1);
    }
    let mut repo = FixtureRepository {
        result: Some(Ok(vec![valid])),
        ..Default::default()
    };
    assert_eq!(
        delete_objects(&mut repo, deletes(vec![delete("k", "wrong")]), user())
            .unwrap_err()
            .code(),
        Code::Internal
    );
}

#[test]
fn projection_failure_after_commit_does_not_compensate_or_repeat_mutation() {
    let mut repo = FixtureRepository {
        unknown_times: true,
        ..Default::default()
    };
    assert_eq!(
        write_objects(&mut repo, writes(vec![write("k", "{}", "*")]), user())
            .unwrap_err()
            .code(),
        Code::Internal
    );
    assert_eq!(repo.state.lock().unwrap().object_count(), 1);
    assert_eq!(repo.calls.load(Ordering::Acquire), 1);
}

#[test]
fn actual_service_rejects_missing_basic_refresh_revoked_and_draining_before_mutation() {
    let repo = FixtureRepository::default();
    let calls = Arc::clone(&repo.calls);
    runtime().block_on(async {
        let active = service(repo.clone(), authority());
        let (token, refresh) = mint(&active).await;
        for authorization in [
            None,
            Some("Basic c2VydmVyOg==".to_owned()),
            Some(format!("Bearer {refresh}")),
        ] {
            let mut wr = Request::new(writes(vec![write("", "bad", "")]));
            let mut dr = Request::new(deletes(vec![delete("", "")]));
            if let Some(value) = authorization {
                wr.metadata_mut()
                    .insert("authorization", value.parse().unwrap());
                dr.metadata_mut()
                    .insert("authorization", value.parse().unwrap());
            }
            assert_eq!(
                active.write_storage_objects(wr).await.unwrap_err().code(),
                Code::Unauthenticated
            );
            assert_eq!(
                active.delete_storage_objects(dr).await.unwrap_err().code(),
                Code::Unauthenticated
            );
        }
        active
            .session_logout(authed(
                SessionLogoutRequest {
                    token: token.clone(),
                    refresh_token: String::new(),
                },
                &token,
            ))
            .await
            .unwrap();
        assert_eq!(
            active
                .write_storage_objects(authed(writes(vec![]), &token))
                .await
                .unwrap_err()
                .code(),
            Code::Unauthenticated
        );
        assert_eq!(
            active
                .delete_storage_objects(authed(deletes(vec![]), &token))
                .await
                .unwrap_err()
                .code(),
            Code::Unauthenticated
        );
        let (token, _) = mint(&active).await;
        active.fence.draining.begin();
        assert_eq!(
            active
                .write_storage_objects(authed(writes(vec![]), &token))
                .await
                .unwrap_err()
                .code(),
            Code::Unavailable
        );
        assert_eq!(
            active
                .delete_storage_objects(authed(deletes(vec![]), &token))
                .await
                .unwrap_err()
                .code(),
            Code::Unavailable
        );
        let disabled = service(repo, AuthAuthorityRuntime::Disabled);
        assert_eq!(
            disabled
                .write_storage_objects(Request::new(writes(vec![])))
                .await
                .unwrap_err()
                .code(),
            Code::Unimplemented
        );
        assert_eq!(
            disabled
                .delete_storage_objects(Request::new(deletes(vec![])))
                .await
                .unwrap_err()
                .code(),
            Code::Unimplemented
        );
    });
    assert_eq!(calls.load(Ordering::Acquire), 0);
}

#[test]
fn metadata_boundary_rejects_both_mutations_before_protobuf_decoding() {
    use std::{
        convert::Infallible,
        future::Ready,
        task::{Context, Poll},
    };
    use tonic::codegen::{http, Service};
    #[derive(Clone)]
    struct NeverDecode;
    impl Service<http::Request<Vec<u8>>> for NeverDecode {
        type Response = http::Response<tonic::body::Body>;
        type Error = Infallible;
        type Future = Ready<Result<Self::Response, Self::Error>>;
        fn poll_ready(&mut self, _: &mut Context<'_>) -> Poll<Result<(), Self::Error>> {
            Poll::Ready(Ok(()))
        }
        fn call(&mut self, _: http::Request<Vec<u8>>) -> Self::Future {
            panic!("unauthenticated protobuf decode")
        }
    }
    for method in ["WriteStorageObjects", "DeleteStorageObjects"] {
        for authorization in [None, Some("Basic c2VydmVyOg=="), Some("Bearer bad")] {
            let mut boundary = super::super::AuthBoundary::new(NeverDecode, authority());
            let mut request = http::Request::builder().uri(format!("/nakama.api.Nakama/{method}"));
            if let Some(value) = authorization {
                request = request.header("authorization", value);
            }
            let response = runtime()
                .block_on(boundary.call(request.body(vec![0xff]).unwrap()))
                .unwrap();
            assert_eq!(response.headers().get("grpc-status").unwrap(), "16");
        }
    }
}

#[test]
fn generated_client_loopback_writes_and_deletes_through_one_existing_model() {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let bind = listener.local_addr().unwrap();
    drop(listener);
    let repo = FixtureRepository::default();
    let authority = authority();
    let drain = SharedDrain::default();
    let failed = Arc::new(AtomicBool::new(false));
    let worker = crate::runtime::grpc::spawn_authenticated(
        Some(bind),
        drain.clone(),
        Arc::clone(&failed),
        &repo,
        &authority,
    )
    .unwrap();
    let runtime = runtime();
    runtime.block_on(async {
        let mut client = None;
        for _ in 0..100 {
            match NakamaClient::connect(format!("http://{bind}")).await {
                Ok(value) => {
                    client = Some(value);
                    break;
                }
                Err(_) => tokio::time::sleep(Duration::from_millis(10)).await,
            }
        }
        let mut client = client.expect("bounded gRPC startup");
        let (token, _) = mint(&service(repo.clone(), authority)).await;
        let ack = client
            .write_storage_objects(authed(writes(vec![write("k", "{\"n\":1}", "*")]), &token))
            .await
            .unwrap()
            .into_inner()
            .acks
            .remove(0);
        assert_eq!(ack.update_time.unwrap().nanos, 999_999_000);
        assert_eq!(repo.state.lock().unwrap().object_count(), 1);
        client
            .delete_storage_objects(authed(deletes(vec![delete("k", &ack.version)]), &token))
            .await
            .unwrap();
        assert_eq!(repo.state.lock().unwrap().object_count(), 0);
        assert_eq!(
            client
                .delete_storage_objects(authed(deletes(vec![delete("k", "")]), &token))
                .await
                .unwrap_err()
                .code(),
            Code::InvalidArgument
        );
        assert_eq!(repo.calls.load(Ordering::Acquire), 3);
    });
    drop(runtime);
    drain.begin();
    crate::runtime::grpc::join(worker).unwrap();
    assert!(!failed.load(Ordering::Acquire));
}

#[test]
fn abandoned_write_retains_job_and_can_commit_without_replay_or_compensation() {
    let (release, wait) = std::sync::mpsc::channel();
    let repo = FixtureRepository {
        pause: Some(Arc::new(Mutex::new(wait))),
        ..Default::default()
    };
    let calls = Arc::clone(&repo.calls);
    let state = Arc::clone(&repo.state);
    let active = Arc::new(service(repo, authority()));
    let runtime = runtime();
    runtime.block_on(async {
        let (token, _) = mint(&active).await;
        let worker = Arc::clone(&active);
        let request = tokio::spawn(async move {
            worker
                .write_storage_objects(authed(writes(vec![write("k", "{}", "*")]), &token))
                .await
        });
        let deadline = std::time::Instant::now() + Duration::from_secs(3);
        while calls.load(Ordering::Acquire) == 0 {
            assert!(std::time::Instant::now() < deadline);
            tokio::time::sleep(Duration::from_millis(1)).await;
        }
        request.abort();
        assert!(request.await.unwrap_err().is_cancelled());
        assert_eq!(active.jobs.load(Ordering::Acquire), 1);
        assert_eq!(state.lock().unwrap().object_count(), 0);
        release.send(()).unwrap();
        while active.jobs.load(Ordering::Acquire) != 0 {
            assert!(std::time::Instant::now() < deadline);
            tokio::time::sleep(Duration::from_millis(1)).await;
        }
        assert_eq!(calls.load(Ordering::Acquire), 1);
        assert_eq!(state.lock().unwrap().object_count(), 1);
        assert!(!active.fence.draining.is_draining());
        assert!(!active.fence.worker_failed.load(Ordering::Acquire));
    });
}

#[test]
fn unknown_owner_wire_field_cannot_change_mutation_owner_and_invalid_utf8_rejects() {
    let original = write("k", "{}", "");
    let mut bytes = original.encode_to_vec();
    // WriteStorageObject has no owner field; unknown tag 7 is skipped.
    bytes.extend_from_slice(&[0x3a, 5, b'a', b'd', b'm', b'i', b'n']);
    let decoded = WriteStorageObject::decode(bytes.as_slice()).unwrap();
    assert_eq!(decoded, original);
    let mut repo = FixtureRepository::default();
    let ack = write_objects(&mut repo, writes(vec![decoded]), user()).unwrap();
    assert_eq!(ack.acks[0].user_id, uuid_string(user()));
    assert!(WriteStorageObject::decode(&[0x12, 1, 0xff][..]).is_err());
    assert!(DeleteStorageObjectId::decode(&[0x12, 1, 0xff][..]).is_err());
}

#[test]
fn only_exact_native_storage_reasons_become_semantic_rejections() {
    for (code, reason, write_code, delete_code) in [
        (
            StableCode::AlreadyExists,
            "storage_object_already_exists",
            Code::InvalidArgument,
            Code::Internal,
        ),
        (
            StableCode::FailedPrecondition,
            "storage_version_mismatch",
            Code::InvalidArgument,
            Code::InvalidArgument,
        ),
        (
            StableCode::PermissionDenied,
            "storage_write_permission_denied",
            Code::InvalidArgument,
            Code::InvalidArgument,
        ),
        (
            StableCode::NotFound,
            "storage_object_not_found",
            Code::Internal,
            Code::InvalidArgument,
        ),
        (
            StableCode::FailedPrecondition,
            "storage_import_incomplete",
            Code::Internal,
            Code::Internal,
        ),
        (
            StableCode::FailedPrecondition,
            "database_schema_missing",
            Code::Internal,
            Code::Internal,
        ),
        (
            StableCode::FailedPrecondition,
            "database_foreign_key_violation",
            Code::Internal,
            Code::Internal,
        ),
        (
            StableCode::AlreadyExists,
            "database_unique_violation",
            Code::Internal,
            Code::Internal,
        ),
        (
            StableCode::PermissionDenied,
            "storage_import_authority_denied",
            Code::Internal,
            Code::Internal,
        ),
        (
            StableCode::Internal,
            "storage_version_mismatch",
            Code::Internal,
            Code::Internal,
        ),
    ] {
        let mut repo = FixtureRepository {
            result: Some(Err(DomainError::new(code, reason, RetryClass::Never))),
            ..Default::default()
        };
        let write_error =
            write_objects(&mut repo, writes(vec![write("k", "{}", "")]), user()).unwrap_err();
        let delete_error =
            delete_objects(&mut repo, deletes(vec![delete("k", "")]), user()).unwrap_err();
        assert_eq!(write_error.code(), write_code, "{reason}");
        assert_eq!(delete_error.code(), delete_code, "{reason}");
        if write_code == Code::Internal {
            assert_eq!(write_error.message(), WRITE_FAILURE);
        }
        if delete_code == Code::Internal {
            assert_eq!(delete_error.message(), DELETE_FAILURE);
        }
        assert_eq!(repo.calls.load(Ordering::Acquire), 2);
    }
}

#[test]
fn json_nesting_matches_the_pinned_go_limit_before_repository_entry() {
    let at_limit = format!(
        "{{\"x\":{}0{}}}",
        "[".repeat(MAX_JSON_DEPTH - 1),
        "]".repeat(MAX_JSON_DEPTH - 1)
    );
    let too_deep = format!(
        "{{\"x\":{}0{}}}",
        "[".repeat(MAX_JSON_DEPTH),
        "]".repeat(MAX_JSON_DEPTH)
    );
    // RawValue alone admits both, so the explicit bound cannot be removed.
    assert!(serde_json::from_str::<Box<RawValue>>(&too_deep).is_ok());
    let mut repo = FixtureRepository::default();
    write_objects(&mut repo, writes(vec![write("k", &at_limit, "")]), user()).unwrap();
    assert_eq!(repo.calls.load(Ordering::Acquire), 1);
    let before = repo.state.lock().unwrap().clone();
    let error = write_objects(
        &mut repo,
        writes(vec![write("first", "{}", ""), write("last", &too_deep, "")]),
        user(),
    )
    .unwrap_err();
    assert_eq!(
        (error.code(), error.message()),
        (Code::InvalidArgument, INVALID_VALUE)
    );
    assert_eq!(repo.calls.load(Ordering::Acquire), 1);
    assert_eq!(*repo.state.lock().unwrap(), before);
    let nested_objects = format!(
        "{}0{}",
        "{\"x\":".repeat(MAX_JSON_DEPTH),
        "}".repeat(MAX_JSON_DEPTH)
    );
    write_objects(
        &mut repo,
        writes(vec![write("objects", &nested_objects, "")]),
        user(),
    )
    .unwrap();
}

#[test]
fn json_depth_preflight_ignores_string_brackets_escaped_quotes_and_backslashes() {
    let value = serde_json::json!({"s": format!("\\\"{}\\\\{}", "[".repeat(MAX_JSON_DEPTH + 1), "}".repeat(MAX_JSON_DEPTH + 1))}).to_string();
    let mut repo = FixtureRepository::default();
    write_objects(&mut repo, writes(vec![write("quoted", &value, "")]), user()).unwrap();
    let mixed = format!(
        "{{\"quoted\":\"[\",\"real\":{}0{}}}",
        "[".repeat(MAX_JSON_DEPTH),
        "]".repeat(MAX_JSON_DEPTH)
    );
    assert_eq!(
        write_objects(&mut repo, writes(vec![write("deep", &mixed, "")]), user())
            .unwrap_err()
            .message(),
        INVALID_VALUE
    );
    for malformed in [r#"{"s":"\"[}"#, r#"{"s":"\","x":[}"#, "{]}"] {
        assert_eq!(
            write_objects(
                &mut repo,
                writes(vec![write("invalid", malformed, "")]),
                user()
            )
            .unwrap_err()
            .message(),
            INVALID_VALUE
        );
    }
    assert_eq!(repo.calls.load(Ordering::Acquire), 1);
}
