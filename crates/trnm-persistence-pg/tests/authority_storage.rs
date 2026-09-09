use std::env;

use trnm_contracts::{CommandId, Digest32, StableCode, UserId};
use trnm_persistence_pg::{
    CommitRequest, DatabaseProfile, EntityId, NodeId, PgRepository, ReadPermission, StorageActor,
    StorageBatchOperation, StorageDeleteOperation, StorageObjectKey, StorageWriteOperation,
    VersionCheck, WritePermission,
};

fn digest(value: u8) -> Digest32 {
    Digest32::new([value; 32])
}

fn profile(value: &str) -> DatabaseProfile {
    match value {
        "postgresql" => DatabaseProfile::PostgreSql,
        "cockroachdb" => DatabaseProfile::CockroachDb,
        other => panic!("unsupported TRNM_DATABASE_PROFILE={other}"),
    }
}

fn live_database_environment(label: &str) -> Option<(String, DatabaseProfile)> {
    let required = match env::var("TRNM_REQUIRE_LIVE_DATABASE") {
        Err(env::VarError::NotPresent) => false,
        Err(error) => panic!("cannot read TRNM_REQUIRE_LIVE_DATABASE: {error}"),
        Ok(value) if matches!(value.as_str(), "1" | "true" | "TRUE" | "yes" | "YES") => true,
        Ok(value) if matches!(value.as_str(), "0" | "false" | "FALSE" | "no" | "NO") => false,
        Ok(value) => panic!("invalid TRNM_REQUIRE_LIVE_DATABASE={value:?}"),
    };
    let database_url = match env::var("TRNM_DATABASE_URL") {
        Ok(value) if !value.is_empty() => value,
        Ok(_) if required => panic!("{label}: empty TRNM_DATABASE_URL"),
        Ok(_) => return None,
        Err(env::VarError::NotPresent) if required => {
            panic!("{label}: required TRNM_DATABASE_URL is absent")
        }
        Err(env::VarError::NotPresent) => return None,
        Err(error) => panic!("{label}: cannot read TRNM_DATABASE_URL: {error}"),
    };
    let profile_value = env::var("TRNM_DATABASE_PROFILE")
        .unwrap_or_else(|_| panic!("{label}: TRNM_DATABASE_PROFILE is required"));
    Some((database_url, profile(&profile_value)))
}

#[test]
fn authority_takeover_fences_stale_generation() {
    let Some((database_url, profile)) = live_database_environment("authority lease contract")
    else {
        return;
    };
    let entity = EntityId::new([0x71; 16]);
    let owner_a = NodeId::new([0x72; 16]);
    let owner_b = NodeId::new([0x73; 16]);
    let mut repository = PgRepository::connect(&database_url, profile).unwrap();
    repository
        .bootstrap_entity(entity, 1, digest(0x74), 1)
        .unwrap();
    let lease_a = repository
        .acquire_authority_lease(entity, owner_a, 1, 10, 20)
        .unwrap();
    assert_eq!(lease_a.lease_generation, 1);
    assert_eq!(lease_a.authority_generation, 1);
    let held = repository
        .acquire_authority_lease(entity, owner_b, 1, 15, 30)
        .unwrap_err();
    assert_eq!(held.code(), StableCode::Aborted);
    assert_eq!(held.reason(), "authority_lease_held");
    let renewed = repository.renew_authority_lease(lease_a, 15, 25).unwrap();
    assert_eq!(renewed.lease_generation, 1);
    let lease_b = repository
        .acquire_authority_lease(entity, owner_b, 1, 25, 40)
        .unwrap();
    assert_eq!(lease_b.lease_generation, 2);
    assert_eq!(lease_b.authority_generation, 2);
    let head = repository.load_head(entity).unwrap().unwrap();
    assert_eq!(head.authority_generation, 2);

    let stale = CommitRequest {
        entity,
        command: CommandId::new([0x75; 16]),
        fingerprint: digest(0x76),
        expected_revision: 0,
        authority_generation: 1,
        next_state: digest(0x77),
        committed_at_ms: 30,
        events: vec![],
        outbox: vec![],
    };
    assert_eq!(
        repository.commit_command(&stale).unwrap_err().reason(),
        "authority_generation_mismatch"
    );
}

#[test]
fn storage_occ_acl_and_batch_rollback_are_transactional() {
    let Some((database_url, profile)) = live_database_environment("storage object contract") else {
        return;
    };
    let user = UserId::new([0x81; 16]);
    let other = UserId::new([0x82; 16]);
    let key = StorageObjectKey::new("profile", "ac3-object", user).unwrap();
    let mut repository = PgRepository::connect(&database_url, profile).unwrap();
    let created = repository
        .apply_storage_batch(
            StorageActor::User(user),
            &[StorageBatchOperation::Write(StorageWriteOperation {
                key: key.clone(),
                value: b"v1".to_vec(),
                expected: VersionCheck::MustNotExist,
                read_permission: ReadPermission::Owner,
                write_permission: WritePermission::Owner,
            })],
            10,
        )
        .unwrap();
    let version = created[0].current_version.unwrap();
    let stored_v1 = repository
        .read_storage_object(StorageActor::User(user), &key)
        .unwrap();
    assert_eq!(stored_v1.value, b"v1");
    assert!(stored_v1.integrity_digest.matches_value(&stored_v1.value));
    assert_eq!(
        repository
            .read_storage_object(StorageActor::User(other), &key)
            .unwrap_err()
            .reason(),
        "storage_read_permission_denied"
    );

    repository
        .apply_storage_batch(
            StorageActor::User(user),
            &[StorageBatchOperation::Write(StorageWriteOperation {
                key: key.clone(),
                value: b"v2".to_vec(),
                expected: VersionCheck::Exact(version),
                read_permission: ReadPermission::Public,
                write_permission: WritePermission::Owner,
            })],
            20,
        )
        .unwrap();
    let stored_v2 = repository
        .read_storage_object(StorageActor::User(other), &key)
        .unwrap();
    assert_eq!(stored_v2.value, b"v2");
    assert!(stored_v2.integrity_digest.matches_value(&stored_v2.value));

    let stale = repository
        .apply_storage_batch(
            StorageActor::User(user),
            &[StorageBatchOperation::Write(StorageWriteOperation {
                key: key.clone(),
                value: b"v3".to_vec(),
                expected: VersionCheck::Exact(version),
                read_permission: ReadPermission::Owner,
                write_permission: WritePermission::Owner,
            })],
            30,
        )
        .unwrap_err();
    assert_eq!(stale.reason(), "storage_version_mismatch");
    assert_eq!(
        repository
            .read_storage_object(StorageActor::Server, &key)
            .unwrap()
            .value,
        b"v2"
    );

    let current = repository
        .read_storage_object(StorageActor::Server, &key)
        .unwrap()
        .version;
    repository
        .apply_storage_batch(
            StorageActor::User(user),
            &[StorageBatchOperation::Delete(StorageDeleteOperation {
                key: key.clone(),
                expected_version: Some(current),
            })],
            40,
        )
        .unwrap();
    assert_eq!(
        repository
            .read_storage_object(StorageActor::Server, &key)
            .unwrap_err()
            .code(),
        StableCode::NotFound
    );
}
