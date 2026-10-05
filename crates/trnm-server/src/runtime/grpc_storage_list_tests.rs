//! Native Rust/tonic fixtures use a synthetic repository. No live DB/oracle credit.
use super::*;
use crate::runtime::app::SharedDrain;
use crate::runtime::auth_runtime::AuthAuthorityRuntime;
use crate::runtime::config::AuthAuthorityConfig;
use crate::runtime::grpc::generated::nakama::api::{
    nakama_client::NakamaClient, nakama_server::Nakama, AccountCustom, AuthenticateCustomRequest,
    ListStorageObjectsRequest, SessionLogoutRequest,
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
use trnm_contracts::{Digest32, DomainError, RetryClass, StableCode};
use trnm_persistence_pg::{
    CommitOutcome, CommitRequest, EntityHead, EntityId, IntegrityDigest, PublicVersion,
    ReadPermission, StorageObject as NativeObject, StorageObjectKey, StorageTimes,
    StoredStorageObject, WritePermission,
};

type ListCall = (String, Option<UserId>, Option<StorageListPosition>, usize);

#[derive(Clone, Debug, Default)]
struct FixtureRepository {
    calls: Arc<AtomicUsize>,
    input: Arc<Mutex<Option<ListCall>>>,
    result: Vec<StoredStorageObject>,
    next: Option<StorageListPosition>,
    error: Option<StableCode>,
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
    fn list_storage_objects_nakama(
        &mut self,
        actor: StorageActor,
        collection: &str,
        owner: Option<UserId>,
        after: Option<&StorageListPosition>,
        limit: usize,
    ) -> Result<StoredStorageClientListPage, DomainError> {
        assert_eq!(actor, StorageActor::User(user()));
        self.calls.fetch_add(1, Ordering::AcqRel);
        *self.input.lock().unwrap() = Some((collection.to_owned(), owner, after.cloned(), limit));
        if let Some(code) = self.error {
            Err(DomainError::new(
                code,
                "private_database_failure",
                RetryClass::Never,
            ))
        } else {
            Ok(StoredStorageClientListPage {
                objects: self.result.clone(),
                next: self.next.clone(),
            })
        }
    }
}
fn user() -> UserId {
    UserId::new([7; 16])
}
fn key(owner: UserId, name: &str) -> StorageObjectKey {
    StorageObjectKey::new_nakama("profile", name, owner).unwrap()
}
fn input(owner: &str, limit: Option<i32>, cursor: &str) -> ListStorageObjectsRequest {
    ListStorageObjectsRequest {
        user_id: owner.into(),
        collection: "profile".into(),
        limit: limit
            .map(|value| crate::runtime::grpc::generated::google::protobuf::Int32Value { value }),
        cursor: cursor.into(),
    }
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
            2,
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
fn default_limit_and_empty_collection_reach_one_native_read() {
    let mut repo = FixtureRepository::default();
    let mut request = input("", None, "");
    request.collection.clear();
    let result = list_objects(&mut repo, request, user()).unwrap();
    assert!(result.objects.is_empty());
    assert!(result.cursor.is_empty());
    assert_eq!(
        *repo.input.lock().unwrap(),
        Some((String::new(), None, None, 1))
    );
    assert_eq!(repo.calls.load(Ordering::Acquire), 1);
}

#[test]
fn validation_precedence_and_local_bounds_stop_before_repository() {
    let mut repo = FixtureRepository::default();
    for limit in [i32::MIN, -1, 0, 101, i32::MAX] {
        let error = list_objects(&mut repo, input("bad", Some(limit), "bad"), user()).unwrap_err();
        assert_eq!(
            (error.code(), error.message()),
            (Code::InvalidArgument, INVALID_LIMIT)
        );
    }
    let error = list_objects(&mut repo, input("bad", Some(1), "bad"), user()).unwrap_err();
    assert_eq!(
        (error.code(), error.message()),
        (Code::InvalidArgument, INVALID_USER)
    );
    for cursor in ["bad".to_owned(), "x".repeat(16 * 1024 + 1)] {
        let error = list_objects(&mut repo, input("", Some(1), &cursor), user()).unwrap_err();
        assert_eq!(
            (error.code(), error.message()),
            (Code::InvalidArgument, MALFORMED_CURSOR)
        );
    }
    let mut request = input("", Some(1), "");
    request.collection = "x".repeat(MAX_COLLECTION_BYTES + 1);
    assert_eq!(
        list_objects(&mut repo, request, user()).unwrap_err().code(),
        Code::ResourceExhausted
    );
    assert_eq!(
        list_objects(&mut repo, input("", None, ""), UserId::new([0; 16]))
            .unwrap_err()
            .code(),
        Code::Unauthenticated
    );
    assert_eq!(repo.calls.load(Ordering::Acquire), 0);
    let mut request = input("", Some(100), "");
    request.collection = "x".repeat(MAX_COLLECTION_BYTES);
    list_objects(&mut repo, request, user()).unwrap();
    assert_eq!(repo.input.lock().unwrap().as_ref().unwrap().3, 100);
}

#[test]
fn owner_aliases_zero_owner_and_untrusted_cursor_remain_filters() {
    let position = StorageListPosition {
        key: "offset".into(),
        user_id: UserId::new([9; 16]),
        read: i32::MIN,
    };
    let cursor = encode_cursor(&position).unwrap();
    for (owner, expected) in [
        ("".to_owned(), None),
        (uuid_string(user()), Some(user())),
        ("07070707070707070707070707070707".into(), Some(user())),
        (
            "00000000-0000-0000-0000-000000000000".into(),
            Some(UserId::new([0; 16])),
        ),
    ] {
        let mut repo = FixtureRepository::default();
        list_objects(&mut repo, input(&owner, None, &cursor), user()).unwrap();
        assert_eq!(
            *repo.input.lock().unwrap(),
            Some(("profile".into(), expected, Some(position.clone()), 1))
        );
    }
}

#[test]
fn all_three_acl_modes_preserve_raw_values_versions_and_order() {
    let foreign = UserId::new([8; 16]);
    for (filter, owner, reads) in [
        ("".to_owned(), foreign, vec![2, 3, 32767]),
        (uuid_string(user()), user(), vec![1, 2, 3]),
        (uuid_string(foreign), foreign, vec![2]),
        (
            uuid_string(UserId::new([0; 16])),
            UserId::new([0; 16]),
            vec![2],
        ),
    ] {
        let objects: Vec<_> = reads
            .iter()
            .enumerate()
            .map(|(i, read)| {
                object(
                    owner,
                    &format!("{}", 9 - i),
                    b" {\"amount\": 9007199254740993} ",
                    *read,
                )
            })
            .collect();
        let mut repo = FixtureRepository {
            result: objects.clone(),
            ..Default::default()
        };
        let response = list_objects(&mut repo, input(&filter, Some(100), ""), user()).unwrap();
        for (actual, original) in response.objects.iter().zip(objects) {
            assert_eq!(actual.key, original.object.key.key());
            assert_eq!(actual.value.as_bytes(), original.object.value);
            assert_eq!(actual.version, "OpaqueABC");
            assert_eq!(
                actual.permission_read,
                original.object.read_permission.as_i32()
            );
            assert_eq!(actual.permission_write, 32767);
            assert_eq!(
                actual.create_time,
                Some(Timestamp {
                    seconds: -1,
                    nanos: 0
                })
            );
            assert_eq!(
                actual.update_time,
                Some(Timestamp {
                    seconds: 1_700_000_000,
                    nanos: 0
                })
            );
        }
    }
}

#[test]
fn scope_acl_duplicate_and_excess_rows_fail_closed() {
    for (filter, owner, read) in [
        ("".to_owned(), user(), 1),
        (uuid_string(user()), user(), 0),
        (uuid_string(user()), UserId::new([8; 16]), 2),
        (uuid_string(UserId::new([8; 16])), UserId::new([8; 16]), 3),
    ] {
        let mut repo = FixtureRepository {
            result: vec![object(owner, "k", b"{}", read)],
            ..Default::default()
        };
        assert_eq!(
            list_objects(&mut repo, input(&filter, None, ""), user())
                .unwrap_err()
                .code(),
            Code::Internal
        );
    }
    let mut repo = fixture();
    repo.result[0].object.key = StorageObjectKey::new_nakama("other", "k", user()).unwrap();
    assert_eq!(
        list_objects(&mut repo, input("", None, ""), user())
            .unwrap_err()
            .code(),
        Code::Internal
    );
    let mut repo = fixture();
    repo.result.push(repo.result[0].clone());
    for limit in [1, 2] {
        assert_eq!(
            list_objects(&mut repo, input("", Some(limit), ""), user())
                .unwrap_err()
                .code(),
            Code::Internal
        );
    }
}

#[test]
fn continuation_is_exact_last_row_and_literal_equal_guard_is_preserved() {
    let mut repo = fixture();
    let position = StorageListPosition {
        key: "k".into(),
        user_id: user(),
        read: 2,
    };
    repo.next = Some(position.clone());
    let result = list_objects(&mut repo, input("", None, ""), user()).unwrap();
    assert_eq!(decode_cursor(&result.cursor).unwrap(), position);
    let second = list_objects(&mut repo, input("", None, &result.cursor), user()).unwrap();
    assert!(second.cursor.is_empty());
    // Go base64 accepts CR/LF. A semantically equal input with different bytes
    // must not suppress the ordinary emitted cursor.
    let alternate = format!("{}\r\n", result.cursor);
    let third = list_objects(&mut repo, input("", None, &alternate), user()).unwrap();
    assert_eq!(third.cursor, result.cursor);
    assert_eq!(
        list_objects(&mut repo, input("", Some(2), ""), user())
            .unwrap_err()
            .code(),
        Code::Internal
    );
    for bad in [
        StorageListPosition {
            key: "other".into(),
            ..position.clone()
        },
        StorageListPosition {
            user_id: UserId::new([8; 16]),
            ..position.clone()
        },
        StorageListPosition {
            read: 3,
            ..position
        },
    ] {
        repo.next = Some(bad);
        assert_eq!(
            list_objects(&mut repo, input("", None, ""), user())
                .unwrap_err()
                .code(),
            Code::Internal
        );
    }
    repo.result.clear();
    assert_eq!(
        list_objects(&mut repo, input("", None, ""), user())
            .unwrap_err()
            .code(),
        Code::Internal
    );
}

#[test]
fn integrity_utf8_missing_and_invalid_timestamps_are_opaque_failures() {
    for case in 0..5 {
        let mut repo = fixture();
        match case {
            0 => repo.result[0].object.value.push(b' '),
            1 => {
                repo.result[0].object.value = vec![0xff];
                repo.result[0].object.integrity_digest = IntegrityDigest::from_value(&[0xff]);
            }
            2 => repo.result[0].times.create = None,
            3 => repo.result[0].times.update = None,
            _ => {
                repo.result[0].times.create = Some(StorageTimestamp {
                    seconds: i64::MAX,
                    nanos: 0,
                })
            }
        }
        let error = list_objects(&mut repo, input("", None, ""), user()).unwrap_err();
        assert_eq!(
            (error.code(), error.message()),
            (Code::Internal, LIST_FAILURE)
        );
    }
}

#[test]
fn exact_protobuf_budget_includes_cursor_and_rejects_all_of_overflow() {
    let mut repo = fixture();
    repo.next = Some(StorageListPosition {
        key: "k".into(),
        user_id: user(),
        read: 2,
    });
    let initial = list_objects(&mut repo, input("", None, ""), user())
        .unwrap()
        .encoded_len();
    let original_len = repo.result[0].object.value.len();
    // Crossing from a short string/message to a 2 MiB one increases both
    // protobuf varint lengths by three bytes. Derive the final exact cap from
    // actual encoding rather than hand assuming those lengths.
    let length = MAX_RESPONSE_BYTES - initial + original_len;
    repo.result[0] = object(user(), "k", &vec![b' '; length], 2);
    let hypothetical = StorageObjectList {
        objects: vec![StorageObject {
            collection: "profile".into(),
            key: "k".into(),
            user_id: uuid_string(user()),
            value: " ".repeat(length),
            version: "OpaqueABC".into(),
            permission_read: 2,
            permission_write: 32767,
            create_time: Some(Timestamp {
                seconds: -1,
                nanos: 0,
            }),
            update_time: Some(Timestamp {
                seconds: 1_700_000_000,
                nanos: 0,
            }),
        }],
        cursor: encode_cursor(repo.next.as_ref().unwrap()).unwrap(),
    };
    let overflow = hypothetical.encoded_len() - MAX_RESPONSE_BYTES;
    repo.result[0] = object(user(), "k", &vec![b' '; length - overflow], 2);
    let result = list_objects(&mut repo, input("", None, ""), user()).unwrap();
    assert_eq!(result.encoded_len(), MAX_RESPONSE_BYTES);
    repo.result[0] = object(user(), "k", &vec![b' '; length - overflow + 1], 2);
    assert_eq!(
        list_objects(&mut repo, input("", None, ""), user())
            .unwrap_err()
            .code(),
        Code::ResourceExhausted
    );
}

#[test]
fn repository_resource_and_internal_errors_are_redacted_without_retry() {
    for (code, expected) in [
        (StableCode::ResourceExhausted, Code::ResourceExhausted),
        (StableCode::Unavailable, Code::Internal),
        (StableCode::FailedPrecondition, Code::Internal),
    ] {
        let mut repo = FixtureRepository {
            error: Some(code),
            ..Default::default()
        };
        let error = list_objects(&mut repo, input("", None, ""), user()).unwrap_err();
        assert_eq!((error.code(), error.message()), (expected, LIST_FAILURE));
        assert_eq!(repo.calls.load(Ordering::Acquire), 1);
    }
}

#[test]
fn rpc_authentication_precedes_business_validation_and_shared_logout_revokes_lists() {
    let repo = fixture();
    let calls = Arc::clone(&repo.calls);
    let service = service(repo, authority());
    runtime().block_on(async {
        let invalid = input("bad", Some(0), "bad");
        assert_eq!(
            service
                .list_storage_objects(Request::new(invalid))
                .await
                .unwrap_err()
                .code(),
            Code::Unauthenticated
        );
        let (access, refresh) = mint(&service).await;
        assert_eq!(
            service
                .list_storage_objects(authed(input("", None, ""), &refresh))
                .await
                .unwrap_err()
                .code(),
            Code::Unauthenticated
        );
        assert_eq!(calls.load(Ordering::Acquire), 0);
        let response = service
            .list_storage_objects(authed(input(&uuid_string(user()), None, ""), &access))
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
                .list_storage_objects(authed(input("", None, ""), &access))
                .await
                .unwrap_err()
                .code(),
            Code::Unauthenticated
        );
        assert_eq!(calls.load(Ordering::Acquire), 1);
    });
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
                .list_storage_objects(input("bad", Some(0), "bad"))
                .await
                .unwrap_err()
                .code(),
            Code::Unauthenticated
        );
        let (token, _) = mint(&service(repo.clone(), authority.clone())).await;
        let response = client
            .list_storage_objects(authed(input(&uuid_string(user()), None, ""), &token))
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
        let mut request = http::Request::builder().uri("/nakama.api.Nakama/ListStorageObjects");
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
                .list_storage_objects(Request::new(input("", None, "")))
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
                .list_storage_objects(authed(input(&uuid_string(user()), None, ""), &token))
                .await
                .unwrap_err()
                .code(),
            Code::Unavailable
        );
    });
    assert_eq!(calls.load(Ordering::Acquire), 0);
}

#[test]
fn complete_upstream_schema_vectors_roundtrip_through_prost() {
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../contracts/grpc/nakama-storage-list-protobuf-fixtures.json"
    ))
    .unwrap();
    assert_eq!(fixture["cases"].as_array().unwrap().len(), 14);
    for case in fixture["cases"].as_array().unwrap() {
        let hex = case["hex"].as_str().unwrap();
        let bytes: Vec<u8> = (0..hex.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&hex[i..i + 2], 16).unwrap())
            .collect();
        let encoded = match case["message"].as_str().unwrap() {
            "ListStorageObjectsRequest" => ListStorageObjectsRequest::decode(bytes.as_slice())
                .unwrap()
                .encode_to_vec(),
            "StorageObjectList" => StorageObjectList::decode(bytes.as_slice())
                .unwrap()
                .encode_to_vec(),
            _ => panic!("fixture message"),
        };
        assert_eq!(encoded, bytes, "{}", case["id"]);
        if case["id"] == "list-full-response" {
            let mut repo = self::fixture();
            repo.result[0].object.read_permission = ReadPermission::from_stored(1).unwrap();
            assert_eq!(
                list_objects(&mut repo, input(&uuid_string(user()), None, ""), user())
                    .unwrap()
                    .encode_to_vec(),
                bytes
            );
        }
    }
    let absent = ListStorageObjectsRequest::decode(&[][..]).unwrap();
    let zero = ListStorageObjectsRequest::decode(&[0x1a, 0][..]).unwrap();
    assert!(absent.limit.is_none());
    assert_eq!(zero.limit.unwrap().value, 0);
    assert!(ListStorageObjectsRequest::decode(&[0x12, 1, 0xff][..]).is_err());
    let unknown = ListStorageObjectsRequest::decode(&[0x50, 1][..]).unwrap();
    assert_eq!(unknown, absent);
}
