use std::collections::BTreeMap;

use trnm_contracts::{Digest32, DomainError, RetryClass, StableCode, UserId};
use trnm_persistence_pg::{
    CollisionWitness, CommitOutcome, CommitRequest, ContentVersion, EntityHead, EntityId,
    IntegrityDigest, PublicVersion, ReadPermission, StorageActor, StorageListPosition,
    StorageObject, StorageObjectKey, StorageTimes, StorageTimestamp, StoredStorageClientListPage,
    StoredStorageObject, WritePermission,
};

use super::app::Repository;
use super::http::{Request, Response};
use super::storage_cursor::{decode_cursor, encode_cursor};
use super::storage_list_api::{handle, is_list_target};

const USER: UserId = UserId::new([1; 16]);
const OTHER: UserId = UserId::new([2; 16]);

type ObservedQuery = (
    StorageActor,
    String,
    Option<UserId>,
    Option<StorageListPosition>,
    usize,
);

#[derive(Debug)]
struct FakeRepository {
    calls: usize,
    observed: Option<ObservedQuery>,
    page: Result<StoredStorageClientListPage, DomainError>,
}

impl Repository for FakeRepository {
    fn bootstrap_entity(
        &mut self,
        _: EntityId,
        _: u64,
        _: Digest32,
        _: u64,
    ) -> Result<EntityHead, DomainError> {
        Err(failure())
    }
    fn commit_command(&mut self, _: &CommitRequest) -> Result<CommitOutcome, DomainError> {
        Err(failure())
    }
    fn list_storage_objects_nakama(
        &mut self,
        actor: StorageActor,
        collection: &str,
        owner: Option<UserId>,
        after: Option<&StorageListPosition>,
        limit: usize,
    ) -> Result<StoredStorageClientListPage, DomainError> {
        self.calls += 1;
        self.observed = Some((actor, collection.to_owned(), owner, after.cloned(), limit));
        self.page.clone()
    }
}

fn failure() -> DomainError {
    DomainError::new(
        StableCode::Unavailable,
        "private_database_error",
        RetryClass::Never,
    )
}

fn repository(objects: Vec<StorageObject>, next: Option<StorageListPosition>) -> FakeRepository {
    FakeRepository {
        calls: 0,
        observed: None,
        page: Ok(StoredStorageClientListPage {
            objects: objects
                .into_iter()
                .map(|object| StoredStorageObject {
                    object,
                    times: StorageTimes {
                        create: None,
                        update: None,
                    },
                })
                .collect(),
            next,
        }),
    }
}
fn request(target: &str) -> Request {
    // GET's body and media type are irrelevant to ParseForm's query projection.
    Request::new("GET", target, BTreeMap::new(), "malformed-body")
}
fn object(key: &str, owner: UserId, read: ReadPermission) -> StorageObject {
    let value = br#"{"value":1}"#.to_vec();
    StorageObject {
        key: StorageObjectKey::new_nakama("inventory", key, owner).unwrap(),
        version: ContentVersion::from_value(&value).into(),
        collision_witness: Some(CollisionWitness::from_request(&value, &value).unwrap()),
        integrity_digest: IntegrityDigest::from_value(&value),
        value,
        read_permission: read,
        write_permission: WritePermission::OWNER,
    }
}

fn historical_object(key: &str, native: &str, version: &str) -> StorageObject {
    StorageObject {
        key: StorageObjectKey::new_nakama("inventory", key, USER).unwrap(),
        value: native.as_bytes().to_vec(),
        version: PublicVersion::new(version).unwrap(),
        integrity_digest: IntegrityDigest::from_value(native.as_bytes()),
        collision_witness: None,
        read_permission: ReadPermission::PUBLIC,
        write_permission: WritePermission::OWNER,
    }
}

#[test]
fn storage_list_preserves_native_history_shapes_and_opaque_public_versions() {
    let cases = [
        ("null", "".to_owned()),
        ("[1, 1.0, false, null]", "*".to_owned()),
        ("\"native string\"", "UPPERCASE-NONHEX".to_owned()),
        ("1.000", "雪".repeat(32)),
        ("true", "trailing ".to_owned()),
    ];
    let objects = cases
        .iter()
        .enumerate()
        .map(|(index, (native, version))| {
            historical_object(&format!("history-{index}"), native, version)
        })
        .collect();
    let mut repo = repository(objects, None);
    let response = handle(&mut repo, &request("/v2/storage/inventory?limit=100"), USER);
    assert_eq!(response.status, 200);
    let encoded = json(&response);
    for (object, (native, version)) in encoded["objects"].as_array().unwrap().iter().zip(&cases) {
        assert_eq!(
            object["value"].as_str().unwrap().as_bytes(),
            native.as_bytes()
        );
        if version.is_empty() {
            assert!(object.get("version").is_none());
        } else {
            assert_eq!(object["version"].as_str().unwrap(), version);
        }
        assert!(object.get("create_time").is_none());
        assert!(object.get("update_time").is_none());
    }
}

#[test]
fn storage_list_encoded_response_budget_rejects_partial_objects_with_code_eight() {
    let native = format!("\"{}\"", "\\\"".repeat(4 * 1024 * 1024));
    let mut repo = repository(
        vec![
            historical_object("a", &native, "history-a"),
            historical_object("b", &native, "history-b"),
        ],
        None,
    );
    let response = handle(&mut repo, &request("/v2/storage/inventory?limit=2"), USER);
    assert_eq!(response.status, 429);
    assert_eq!(
        json(&response),
        serde_json::json!({"code":8,"message":"Error listing storage objects."})
    );
    assert!(json(&response).get("objects").is_none());
    assert!(json(&response).get("cursor").is_none());
}

#[test]
fn storage_list_repository_resource_exhaustion_is_redacted_without_integrity_code() {
    let mut repo = repository(vec![], None);
    repo.page = Err(DomainError::new(
        StableCode::ResourceExhausted,
        "private native row budget",
        RetryClass::Never,
    ));
    let response = handle(&mut repo, &request("/v2/storage/inventory"), USER);
    assert_eq!(response.status, 429);
    assert_eq!(
        json(&response),
        serde_json::json!({"code":8,"message":"Error listing storage objects."})
    );
    assert!(!String::from_utf8(response.body)
        .unwrap()
        .contains("private"));
}
fn position(object: &StorageObject) -> StorageListPosition {
    StorageListPosition {
        key: object.key.key().to_owned(),
        user_id: object.key.user_id(),
        read: object.read_permission.as_i32(),
    }
}
fn json(response: &Response) -> serde_json::Value {
    serde_json::from_slice(&response.body).unwrap()
}

#[test]
fn storage_list_default_page_and_original_gob_continuation_are_bounded() {
    let first = object("sword", OTHER, ReadPermission::PUBLIC);
    let next = position(&first);
    let mut repo = repository(vec![first], Some(next.clone()));
    let response = handle(&mut repo, &request("/v2/storage/inventory"), USER);
    assert_eq!(response.status, 200);
    assert_eq!(
        repo.observed,
        Some((StorageActor::User(USER), "inventory".into(), None, None, 1))
    );
    let value = json(&response);
    assert_eq!(value["objects"][0]["key"], "sword");
    assert_eq!(decode_cursor(value["cursor"].as_str().unwrap()), Ok(next));
    assert!(value["objects"][0].get("create_time").is_none());
    assert!(value["objects"][0].get("update_time").is_none());
    assert!(value["objects"][0]["version"].as_str().unwrap().len() == 32);
}

#[test]
fn storage_list_offset_never_supplies_principal_owner_or_collection_authority() {
    let input = StorageListPosition {
        key: ".untrusted\u{1}".into(),
        user_id: OTHER,
        read: -10,
    };
    let cursor = encode_cursor(&input).unwrap();
    let mut repo = repository(vec![], None);
    let response = handle(
        &mut repo,
        &request(&format!(
            "/v2/storage/inventory/01010101-0101-0101-0101-010101010101?limit=100&cursor={cursor}"
        )),
        USER,
    );
    assert_eq!(response.status, 200);
    assert_eq!(response.body, b"{}");
    assert_eq!(
        repo.observed,
        Some((
            StorageActor::User(USER),
            "inventory".into(),
            Some(USER),
            Some(input),
            100
        ))
    );
}

#[test]
fn storage_list_validation_rejects_before_repository_and_redacts_failures() {
    for (target, expected) in [
        (
            "/v2/storage/inventory?cursor=not-gob",
            "Malformed cursor was used.",
        ),
        (
            "/v2/storage/inventory?limit=0",
            "Invalid limit - limit must be between 1 and 100.",
        ),
        (
            "/v2/storage/inventory?user_id=bad",
            "Invalid user ID - make sure user ID is a valid UUID.",
        ),
    ] {
        let mut repo = repository(vec![], None);
        let response = handle(&mut repo, &request(target), USER);
        assert_eq!(response.status, 400);
        assert_eq!(json(&response)["message"], expected);
        assert_eq!(repo.calls, 0);
    }
    let mut repo = repository(vec![], None);
    assert_eq!(
        handle(
            &mut repo,
            &request("/v2/storage/inventory?limit=bad"),
            UserId::new([0; 16])
        )
        .status,
        401
    );
    assert_eq!(repo.calls, 0);
    repo.page = Err(failure());
    let response = handle(&mut repo, &request("/v2/storage/inventory"), USER);
    assert_eq!(response.status, 500);
    assert_eq!(json(&response)["message"], "Error listing storage objects.");
    assert!(!String::from_utf8(response.body)
        .unwrap()
        .contains("private_database_error"));
}

#[test]
fn storage_list_response_defends_against_acl_scope_and_integrity_violations() {
    let public = object("sword", USER, ReadPermission::PUBLIC);
    let mut broken = public.clone();
    broken.value.push(b' ');
    let wrong_collection = StorageObject {
        key: StorageObjectKey::new_nakama("other", "sword", USER).unwrap(),
        ..public.clone()
    };
    let cases = [
        (
            "/v2/storage/inventory",
            vec![object("sword", USER, ReadPermission::OWNER)],
        ),
        (
            "/v2/storage/inventory/02020202-0202-0202-0202-020202020202",
            vec![object("sword", OTHER, ReadPermission::OWNER)],
        ),
        (
            "/v2/storage/inventory/01010101-0101-0101-0101-010101010101",
            vec![object("sword", USER, ReadPermission::NONE)],
        ),
        ("/v2/storage/inventory", vec![broken]),
        ("/v2/storage/inventory", vec![wrong_collection]),
        (
            "/v2/storage/inventory?limit=2",
            vec![public.clone(), public.clone()],
        ),
        (
            "/v2/storage/inventory",
            vec![
                public.clone(),
                object("shield", OTHER, ReadPermission::PUBLIC),
            ],
        ),
    ];
    for (target, objects) in cases {
        let mut repo = repository(objects, None);
        assert_eq!(
            handle(&mut repo, &request(target), USER).status,
            500,
            "{target}"
        );
    }
    let mut repo = repository(
        vec![public.clone()],
        Some(StorageListPosition {
            key: "wrong".into(),
            ..position(&public)
        }),
    );
    assert_eq!(
        handle(&mut repo, &request("/v2/storage/inventory"), USER).status,
        500
    );
    let mut repo = repository(vec![], Some(position(&public)));
    assert_eq!(
        handle(&mut repo, &request("/v2/storage/inventory"), USER).status,
        500
    );
}

#[test]
fn storage_list_projects_fraction_to_seconds_omits_unknown_and_rejects_invalid_times() {
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
        let mut repo = repository(vec![object("sword", USER, ReadPermission::PUBLIC)], None);
        repo.page.as_mut().unwrap().objects[0].times = times;
        let response = handle(&mut repo, &request("/v2/storage/inventory"), USER);
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
        assert_eq!(repo.calls, 1);
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
            let mut repo = repository(vec![object("sword", USER, ReadPermission::PUBLIC)], None);
            repo.page.as_mut().unwrap().objects[0].times = times;
            let response = handle(&mut repo, &request("/v2/storage/inventory"), USER);
            assert_eq!(response.status, 500);
            assert_eq!(
                json(&response),
                serde_json::json!({"code":13,"message":"Error listing storage objects."})
            );
            assert!(json(&response).get("objects").is_none());
            assert_eq!(repo.calls, 1);
        }
    }
}

#[test]
fn storage_list_visibility_and_equal_literal_cursor_guard_match_projection() {
    let own = object("sword", USER, ReadPermission::OWNER);
    let next = position(&own);
    let cursor = encode_cursor(&next).unwrap();
    let mut repo = repository(vec![own], Some(next));
    let response = handle(
        &mut repo,
        &request(&format!(
            "/v2/storage/inventory?user_id=01010101-0101-0101-0101-010101010101&cursor={cursor}"
        )),
        USER,
    );
    assert_eq!(response.status, 200);
    assert!(json(&response).get("cursor").is_none());
    let mut repo = repository(
        vec![object(
            "system",
            UserId::new([0; 16]),
            ReadPermission::PUBLIC,
        )],
        None,
    );
    assert_eq!(
        handle(
            &mut repo,
            &request("/v2/storage/inventory/00000000-0000-0000-0000-000000000000"),
            USER
        )
        .status,
        200
    );
}

#[test]
fn storage_list_dynamic_route_templates_are_used_by_live_matcher() {
    for target in [
        "/v2/storage/inventory",
        "/v2/storage/inventory/user?limit=2",
        "/v2/storage/",
        "/v2/storage/a%2Fb",
        "/%76%32/storage/a",
        "/v2/%73torage/a",
    ] {
        assert!(is_list_target(target), "{target}");
    }
    for target in [
        "/v2/storage",
        "/v2/storagex/a",
        "/v2/storage/a/b/c",
        "/v2/storage/a/b/",
        "/v2/storage/a%2Fb%2Fc",
        "/v2/storage/%GG",
    ] {
        assert!(!is_list_target(target), "{target}");
    }
}
