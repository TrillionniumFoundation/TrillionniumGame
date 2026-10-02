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

#[test]
fn storage_batch_reads_omit_missing_and_hidden_rows_and_fail_on_visible_corruption() {
    let Some((database_url, profile)) = live_database_environment("storage batch read contract")
    else {
        return;
    };
    let owner = UserId::new([0x91; 16]);
    let other = UserId::new([0x92; 16]);
    let owned = StorageObjectKey::new("read-batch-contract", "owned", owner).unwrap();
    let private = StorageObjectKey::new("read-batch-contract", "private", other).unwrap();
    let public = StorageObjectKey::new("read-batch-contract", "public", other).unwrap();
    let global =
        StorageObjectKey::new("read-batch-contract", "global", UserId::new([0; 16])).unwrap();
    let missing = StorageObjectKey::new("read-batch-contract", "missing", owner).unwrap();
    let mut repository = PgRepository::connect(&database_url, profile).unwrap();
    let writes = [
        (owned.clone(), ReadPermission::Owner),
        (private.clone(), ReadPermission::Owner),
        (public.clone(), ReadPermission::Public),
        (global.clone(), ReadPermission::Public),
    ]
    .into_iter()
    .map(|(key, read_permission)| {
        StorageBatchOperation::Write(StorageWriteOperation {
            key,
            value: br#"{"value":1}"#.to_vec(),
            expected: VersionCheck::Any,
            read_permission,
            write_permission: WritePermission::Owner,
        })
    })
    .collect::<Vec<_>>();
    repository
        .apply_storage_batch(StorageActor::Server, &writes, 10)
        .unwrap();
    repository
        .execute_migration_batch(
            "UPDATE trnm_storage_objects SET value_bytes = 'corrupted'::BYTEA \
             WHERE collection = 'read-batch-contract' AND object_key = 'private'",
        )
        .unwrap();
    let objects = repository
        .read_storage_objects(
            StorageActor::User(owner),
            &[
                missing,
                private.clone(),
                public.clone(),
                owned.clone(),
                global.clone(),
            ],
        )
        .unwrap();
    let returned = objects
        .iter()
        .map(|object| object.key.clone())
        .collect::<std::collections::BTreeSet<_>>();
    assert_eq!(
        returned,
        [owned.clone(), public.clone(), global]
            .into_iter()
            .collect::<std::collections::BTreeSet<_>>()
    );
    for object in objects {
        assert_eq!(object.value, br#"{"value":1}"#);
        assert!(object.integrity_digest.matches_value(&object.value));
    }
    let privileged_error = repository
        .read_storage_objects(StorageActor::Server, std::slice::from_ref(&private))
        .unwrap_err();
    assert_eq!(privileged_error.code(), StableCode::DataLoss);
    assert_eq!(
        privileged_error.reason(),
        "storage_integrity_digest_mismatch"
    );
    repository
        .execute_migration_batch(
            "UPDATE trnm_storage_objects SET value_bytes = 'corrupted'::BYTEA \
             WHERE collection = 'read-batch-contract' AND object_key = 'public'",
        )
        .unwrap();
    let visible_error = repository
        .read_storage_objects(StorageActor::User(owner), &[owned.clone(), public.clone()])
        .unwrap_err();
    assert_eq!(visible_error.code(), StableCode::DataLoss);
    assert_eq!(visible_error.reason(), "storage_integrity_digest_mismatch");
    repository
        .execute_migration_batch(
            "DELETE FROM trnm_storage_objects WHERE collection = 'read-batch-contract'",
        )
        .unwrap();
}
