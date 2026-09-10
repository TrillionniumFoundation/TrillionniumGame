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
