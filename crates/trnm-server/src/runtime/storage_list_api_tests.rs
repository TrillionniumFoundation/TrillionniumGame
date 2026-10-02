use std::collections::BTreeMap;

use trnm_contracts::{Digest32, DomainError, RetryClass, StableCode, UserId};
use trnm_persistence_pg::{
    CommitOutcome, CommitRequest, ContentVersion, EntityHead, EntityId, IntegrityDigest,
    ReadPermission, StorageActor, StorageClientListPage, StorageListPosition, StorageObject,
    StorageObjectKey, WritePermission,
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
    page: Result<StorageClientListPage, DomainError>,
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
    ) -> Result<StorageClientListPage, DomainError> {
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
        page: Ok(StorageClientListPage { objects, next }),
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
        version: ContentVersion::from_value(&value),
        integrity_digest: IntegrityDigest::from_value(&value),
        value,
        read_permission: read,
        write_permission: WritePermission::Owner,
    }
}
fn position(object: &StorageObject) -> StorageListPosition {
    StorageListPosition {
        key: object.key.key().to_owned(),
        user_id: object.key.user_id(),
        read: i32::from(object.read_permission as u8),
    }
}
fn json(response: &Response) -> serde_json::Value {
    serde_json::from_slice(&response.body).unwrap()
}

#[test]
fn storage_list_default_page_and_original_gob_continuation_are_bounded() {
    let first = object("sword", OTHER, ReadPermission::Public);
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
    let public = object("sword", USER, ReadPermission::Public);
    let mut broken = public.clone();
    broken.value.push(b' ');
    let wrong_collection = StorageObject {
        key: StorageObjectKey::new_nakama("other", "sword", USER).unwrap(),
        ..public.clone()
    };
    let cases = [
        (
            "/v2/storage/inventory",
            vec![object("sword", USER, ReadPermission::Owner)],
        ),
        (
            "/v2/storage/inventory/02020202-0202-0202-0202-020202020202",
            vec![object("sword", OTHER, ReadPermission::Owner)],
        ),
        (
            "/v2/storage/inventory/01010101-0101-0101-0101-010101010101",
            vec![object("sword", USER, ReadPermission::None)],
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
                object("shield", OTHER, ReadPermission::Public),
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
fn storage_list_visibility_and_equal_literal_cursor_guard_match_projection() {
    let own = object("sword", USER, ReadPermission::Owner);
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
            ReadPermission::Public,
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
