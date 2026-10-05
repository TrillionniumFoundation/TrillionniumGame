//! Native Rust/tonic fixtures use a synthetic repository. No live DB/oracle credit.
use super::*;
use crate::runtime::app::SharedDrain;
use crate::runtime::auth_runtime::AuthAuthorityRuntime;
use crate::runtime::config::AuthAuthorityConfig;
use crate::runtime::grpc::generated::nakama::api::{
    nakama_client::NakamaClient, nakama_server::Nakama, AccountCustom, AuthenticateCustomRequest,
    ReadStorageObjectId, SessionLogoutRequest,
};
use crate::runtime::legacy_auth::{
    LegacyCustomAccount, LegacyCustomRepositoryInput, LegacyRepositoryError,
};
use crate::runtime::legacy_config::LegacyServerAuthConfig;
use std::sync::{
    atomic::{AtomicBool, AtomicUsize, Ordering},
    Arc, Mutex,
};
use std::time::Duration;
use tonic::{Code, Request};
use trnm_contracts::{Digest32, DomainError, RetryClass, StableCode};
use trnm_persistence_pg::{
    CommitOutcome, CommitRequest, EntityHead, EntityId, IntegrityDigest, PublicVersion,
    ReadPermission, StorageObject as NativeObject, StorageTimes, WritePermission,
};

#[derive(Clone, Debug, Default)]
struct FixtureRepository {
    calls: Arc<AtomicUsize>,
    input: Arc<Mutex<Vec<StorageObjectKey>>>,
    result: Vec<StoredStorageObject>,
    fail: bool,
}
impl Repository for FixtureRepository {
    fn bootstrap_entity(
        &mut self,
        _: EntityId,
        _: u64,
        _: Digest32,
        _: u64,
    ) -> Result<EntityHead, DomainError> {
        panic!("read reached mutation")
    }
    fn commit_command(&mut self, _: &CommitRequest) -> Result<CommitOutcome, DomainError> {
        panic!("read reached mutation")
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
    fn read_storage_objects(
        &mut self,
        actor: StorageActor,
        keys: &[StorageObjectKey],
    ) -> Result<Vec<StoredStorageObject>, DomainError> {
        assert_eq!(actor, StorageActor::User(user()));
        self.calls.fetch_add(1, Ordering::AcqRel);
        *self.input.lock().unwrap() = keys.to_vec();
        if self.fail {
            Err(DomainError::new(
                StableCode::Unavailable,
                "private_database_failure",
                RetryClass::Never,
            ))
        } else {
            Ok(self.result.clone())
        }
    }
}
fn user() -> UserId {
    UserId::new([7; 16])
}
fn key(owner: UserId, name: &str) -> StorageObjectKey {
    StorageObjectKey::new_nakama("profile", name, owner).unwrap()
}
fn id(owner: &str, name: &str) -> ReadStorageObjectId {
    ReadStorageObjectId {
        collection: "profile".into(),
        key: name.into(),
        user_id: owner.into(),
    }
}
fn input(ids: Vec<ReadStorageObjectId>) -> ReadStorageObjectsRequest {
    ReadStorageObjectsRequest { object_ids: ids }
}
fn object(owner: UserId, name: &str, value: &[u8], read: i16) -> StoredStorageObject {
    StoredStorageObject {
        object: NativeObject {
            key: key(owner, name),
            value: value.to_vec(),
            version: PublicVersion::parse("OpaqueABC").unwrap(),
            integrity_digest: IntegrityDigest::from_value(value),
            collision_witness: None,
            read_permission: ReadPermission::from_stored(read).unwrap(),
            write_permission: WritePermission::from_stored(32767).unwrap(),
        },
        times: StorageTimes {
            create: Some(StorageTimestamp::new(-1, 999_999_000).unwrap()),
            update: Some(StorageTimestamp::new(1_700_000_000, 123_456_000).unwrap()),
        },
    }
}
fn fixture() -> FixtureRepository {
    FixtureRepository {
        result: vec![object(
            user(),
            "k",
            b" {\"amount\": 9007199254740993, \"tiny\": 0.0000000000000000001} ",
            1,
        )],
        ..Default::default()
    }
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
fn complete_upstream_proto_reference_vectors_match_native_codec() {
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../contracts/grpc/nakama-storage-read-protobuf-fixtures.json"
    ))
    .unwrap();
    assert_eq!(fixture["cases"].as_array().unwrap().len(), 6);
    for case in fixture["cases"].as_array().unwrap() {
        let encoded = case["hex"].as_str().unwrap();
        let bytes: Vec<u8> = (0..encoded.len())
            .step_by(2)
            .map(|n| u8::from_str_radix(&encoded[n..n + 2], 16).unwrap())
            .collect();
        let actual = match case["message"].as_str().unwrap() {
            "ReadStorageObjectsRequest" => ReadStorageObjectsRequest::decode(bytes.as_slice())
                .unwrap()
                .encode_to_vec(),
            "StorageObjects" => StorageObjects::decode(bytes.as_slice())
                .unwrap()
                .encode_to_vec(),
            _ => panic!("unregistered reference message"),
        };
        assert_eq!(actual, bytes, "{}", case["id"]);
        if case["id"] == "full-response" {
            let mut repo = self::fixture();
            assert_eq!(
                read_objects(
                    &mut repo,
                    input(vec![id(&uuid_string(user()), "k")]),
                    user()
                )
                .unwrap()
                .encode_to_vec(),
                bytes
            );
        }
    }
}

#[test]
fn all_keys_validate_before_one_repository_read_and_error_order_is_pinned() {
    let mut repo = FixtureRepository::default();
    let mut missing = id("bad-owner", "");
    let error = read_objects(
        &mut repo,
        input(vec![id("", "ok"), missing.clone()]),
        user(),
    )
    .unwrap_err();
    assert_eq!(
        (error.code(), error.message()),
        (Code::InvalidArgument, INVALID_KEYS)
    );
    missing.key = "k".into();
    assert_eq!(
        read_objects(&mut repo, input(vec![missing]), user())
            .unwrap_err()
            .message(),
        INVALID_USER
    );
    for owner in [
        "00000000-0000-0000-0000-000000000000",
        "00000000000000000000000000000000",
        "malformed",
    ] {
        assert_eq!(
            read_objects(&mut repo, input(vec![id(owner, "k")]), user())
                .unwrap_err()
                .code(),
            Code::InvalidArgument
        );
    }
    assert_eq!(repo.calls.load(Ordering::Acquire), 0);
    assert!(read_objects(&mut repo, input(vec![]), user())
        .unwrap()
        .objects
        .is_empty());
    assert_eq!(repo.calls.load(Ordering::Acquire), 0);
    read_objects(
        &mut repo,
        input(vec![
            id("", "global"),
            id("{07070707-0707-0707-0707-070707070707}", "k"),
        ]),
        user(),
    )
    .unwrap();
    assert_eq!(repo.calls.load(Ordering::Acquire), 1);
    let keys = repo.input.lock().unwrap();
    assert_eq!(keys[0].user_id(), UserId::new([0; 16]));
    assert_eq!(keys[1].user_id(), user());
}

#[test]
fn native_projection_preserves_raw_value_version_acl_order_and_repetition() {
    let mut repo = fixture();
    repo.result
        .push(object(UserId::new([0; 16]), "global", b"null", 2));
    repo.result.push(repo.result[0].clone());
    let response = read_objects(
        &mut repo,
        input(vec![
            id(&uuid_string(user()), "k"),
            id("", "global"),
            id(&uuid_string(user()), "k"),
        ]),
        user(),
    )
    .unwrap();
    assert_eq!(response.objects.len(), 3);
    assert_eq!(
        response.objects[0].value,
        String::from_utf8(repo.result[0].object.value.clone()).unwrap()
    );
    assert_eq!(response.objects[0].version, "OpaqueABC");
    assert_eq!(response.objects[0].permission_write, 32767);
    assert_eq!(
        response.objects[0].create_time.as_ref().unwrap(),
        &Timestamp {
            seconds: -1,
            nanos: 0
        }
    );
    assert_eq!(response.objects[0].update_time.as_ref().unwrap().nanos, 0);
    assert_eq!(
        response.objects[1].user_id,
        "00000000-0000-0000-0000-000000000000"
    );
    assert_eq!(response.objects[0], response.objects[2]);
    assert_eq!(repo.calls.load(Ordering::Acquire), 1);
}

#[test]
fn inconsistent_repository_rows_never_leak_private_or_unrequested_data() {
    for owner in [user(), UserId::new([8; 16]), UserId::new([0; 16])] {
        for permission in [0, 1, 2, 3, 32767] {
            let row = object(owner, "k", b"private", permission);
            let result = encode_objects(&[key(owner, "k")], vec![row], user());
            assert_eq!(
                result.is_ok(),
                permission == 2 || (permission == 1 && owner == user())
            );
        }
    }
    let row = fixture().result.remove(0);
    for rows in [
        vec![row.clone(), row.clone()],
        vec![object(user(), "other", b"secret", 1)],
    ] {
        assert_eq!(
            encode_objects(&[key(user(), "k")], rows, user())
                .unwrap_err()
                .code(),
            Code::Internal
        );
    }
}

#[test]
fn corrupt_utf8_integrity_unknown_or_invalid_times_fail_closed() {
    for mode in 0..5 {
        let mut row = fixture().result.remove(0);
        match mode {
            0 => row.object.value.push(b'!'),
            1 => {
                row.object.value = vec![0xff];
                row.object.integrity_digest = IntegrityDigest::from_value(&row.object.value);
            }
            2 => row.times.create = None,
            3 => row.times.update = None,
            4 => {
                row.times.update = Some(StorageTimestamp {
                    seconds: 0,
                    nanos: 1_000_000_000,
                })
            }
            _ => unreachable!(),
        }
        let error = encode_objects(&[key(user(), "k")], vec![row], user()).unwrap_err();
        assert_eq!(
            (error.code(), error.message()),
            (Code::Internal, READ_FAILURE)
        );
    }
}

#[test]
fn local_input_and_actual_encoded_response_bounds_are_enforced_without_partial_output() {
    let mut repo = FixtureRepository::default();
    for request in [
        input(vec![id("", "k"); MAX_BATCH + 1]),
        input(vec![id("", &"x".repeat(129))]),
    ] {
        assert_eq!(
            read_objects(&mut repo, request, user()).unwrap_err().code(),
            Code::ResourceExhausted
        );
    }
    assert_eq!(repo.calls.load(Ordering::Acquire), 0);
    let size = MAX_RESPONSE_BYTES - 160;
    let mut row = object(user(), "k", &vec![b'x'; size], 1);
    let encoded = encode_objects(&[key(user(), "k")], vec![row.clone()], user()).unwrap();
    let difference = MAX_RESPONSE_BYTES - encoded.encoded_len();
    row.object.value.resize(size + difference, b'x');
    row.object.integrity_digest = IntegrityDigest::from_value(&row.object.value);
    assert_eq!(
        encode_objects(&[key(user(), "k")], vec![row.clone()], user())
            .unwrap()
            .encoded_len(),
        MAX_RESPONSE_BYTES
    );
    row.object.value.push(b'x');
    row.object.integrity_digest = IntegrityDigest::from_value(&row.object.value);
    assert_eq!(
        encode_objects(&[key(user(), "k")], vec![row], user())
            .unwrap_err()
            .code(),
        Code::ResourceExhausted
    );
}

#[test]
fn repository_errors_use_exact_fixed_upstream_error_without_details() {
    let mut repo = FixtureRepository {
        fail: true,
        ..Default::default()
    };
    let error = read_objects(&mut repo, input(vec![id("", "k")]), user()).unwrap_err();
    assert_eq!(
        (error.code(), error.message()),
        (Code::Internal, READ_FAILURE)
    );
    assert_eq!(repo.calls.load(Ordering::Acquire), 1);
}

#[test]
fn rpc_authentication_precedes_business_validation_and_shared_logout_revokes_reads() {
    let repo = fixture();
    let calls = Arc::clone(&repo.calls);
    let service = service(repo, authority());
    runtime().block_on(async {
        let invalid = input(vec![id("bad", "")]);
        assert_eq!(
            service
                .read_storage_objects(Request::new(invalid))
                .await
                .unwrap_err()
                .code(),
            Code::Unauthenticated
        );
        let (access, refresh) = mint(&service).await;
        assert_eq!(
            service
                .read_storage_objects(authed(input(vec![id("", "k")]), &refresh))
                .await
                .unwrap_err()
                .code(),
            Code::Unauthenticated
        );
        assert_eq!(calls.load(Ordering::Acquire), 0);
        let response = service
            .read_storage_objects(authed(input(vec![id(&uuid_string(user()), "k")]), &access))
            .await
            .unwrap();
        assert_eq!(response.into_inner().objects.len(), 1);
        service
            .session_logout(authed(
                SessionLogoutRequest {
                    token: access.clone(),
                    refresh_token: "".into(),
                },
                &access,
            ))
            .await
            .unwrap();
        assert_eq!(
            service
                .read_storage_objects(authed(input(vec![]), &access))
                .await
                .unwrap_err()
                .code(),
            Code::Unauthenticated
        );
        assert_eq!(calls.load(Ordering::Acquire), 1);
    });
}

#[test]
fn protobuf_unknown_fields_invalid_utf8_and_empty_message_presence_are_real() {
    let raw = [0x0a, 0, 0x50, 1];
    let decoded = ReadStorageObjectsRequest::decode(&raw[..]).unwrap();
    assert_eq!(decoded.object_ids.len(), 1);
    assert_eq!(
        read_objects(&mut FixtureRepository::default(), decoded, user())
            .unwrap_err()
            .message(),
        INVALID_KEYS
    );
    assert!(ReadStorageObjectsRequest::decode(&[0x0a, 3, 0x0a, 1, 0xff][..]).is_err());
}

#[test]
fn generated_client_reaches_native_storage_route_with_shared_auth_and_repository() {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let bind = listener.local_addr().unwrap();
    drop(listener);
    let repo = fixture();
    let calls = Arc::clone(&repo.calls);
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
        assert_eq!(
            client
                .read_storage_objects(input(vec![id("bad", "")]))
                .await
                .unwrap_err()
                .code(),
            Code::Unauthenticated
        );
        let (token, _) = mint(&service(repo.clone(), authority.clone())).await;
        let response = client
            .read_storage_objects(authed(input(vec![id(&uuid_string(user()), "k")]), &token))
            .await
            .unwrap()
            .into_inner();
        assert_eq!(response.objects[0].version, "OpaqueABC");
        assert_eq!(
            response.objects[0].create_time.as_ref().unwrap().seconds,
            -1
        );
        assert_eq!(calls.load(Ordering::Acquire), 1);
    });
    drop(runtime);
    drain.begin();
    crate::runtime::grpc::join(worker).unwrap();
    assert!(!failed.load(Ordering::Acquire));
}

#[test]
fn metadata_boundary_rejects_storage_before_entering_the_protobuf_router() {
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
            // This represents handing the body to tonic/prost. It must never
            // happen without authenticated metadata, even for malformed frames.
            panic!("unauthenticated protobuf decoding");
        }
    }
    let authority = authority();
    for authorization in [None, Some("Basic c2VydmVyOg=="), Some("Bearer bad")] {
        let mut boundary = super::super::AuthBoundary::new(NeverDecode, authority.clone());
        let mut request = http::Request::builder().uri("/nakama.api.Nakama/ReadStorageObjects");
        if let Some(value) = authorization {
            request = request.header("authorization", value);
        }
        let response = runtime()
            .block_on(boundary.call(request.body(vec![0xff]).unwrap()))
            .unwrap();
        assert_eq!(response.headers().get("grpc-status").unwrap(), "16");
    }
}

#[test]
fn wrong_selected_authority_and_drain_do_not_reach_storage() {
    let repo = fixture();
    let calls = Arc::clone(&repo.calls);
    runtime().block_on(async {
        let disabled = service(repo.clone(), AuthAuthorityRuntime::Disabled);
        assert_eq!(
            disabled
                .read_storage_objects(Request::new(input(vec![])))
                .await
                .unwrap_err()
                .code(),
            Code::Unimplemented
        );
        let active = service(repo, authority());
        let (token, _) = mint(&active).await;
        active.fence.draining.begin();
        assert_eq!(
            active
                .read_storage_objects(authed(input(vec![id(&uuid_string(user()), "k")]), &token))
                .await
                .unwrap_err()
                .code(),
            Code::Unavailable
        );
    });
    assert_eq!(calls.load(Ordering::Acquire), 0);
}
