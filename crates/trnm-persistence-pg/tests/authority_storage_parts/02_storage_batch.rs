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
                expected: VersionCheck::Exact(version.into()),
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
                expected: VersionCheck::Exact(version.into()),
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
                expected_version: Some(current.into()),
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
#[test]
fn blind_storage_no_op_preserves_timestamp_after_acl_occ_and_integrity_checks() {
    let Some((database_url, profile)) = live_database_environment("blind storage write timestamp")
    else {
        eprintln!("storage_blind_write_timestamps_skipped: optional database is absent");
        return;
    };
    let owner = UserId::new([0xf1; 16]);
    let other = UserId::new([0xf2; 16]);
    let collection = "blind-write-time-contract";
    let key = StorageObjectKey::new(collection, "existing", owner).unwrap();
    let staged_key = StorageObjectKey::new(collection, "staged", owner).unwrap();
    let missing = StorageObjectKey::new(collection, "missing", owner).unwrap();
    let value = br#"{"v":1}"#;
    let mut control = postgres::Client::connect(&database_url, postgres::NoTls)
        .unwrap_or_else(|_| panic!("storage timestamp fixture: control connection failed"));
    let cleanup = |control: &mut postgres::Client| {
        control
            .execute(
                "DELETE FROM trnm_storage_objects WHERE collection = $1 AND user_id = $2",
                &[&collection, &owner.as_bytes().as_slice()],
            )
            .unwrap_or_else(|_| panic!("storage timestamp fixture: scoped cleanup failed"));
    };
    cleanup(&mut control);
    let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let mut repository = PgRepository::connect(&database_url, profile)
            .unwrap_or_else(|_| panic!("storage timestamp fixture: repository connection failed"));
        let write = |key: &StorageObjectKey, expected, read_permission, write_permission| {
            StorageBatchOperation::Write(StorageWriteOperation {
                key: key.clone(),
                value: value.to_vec(),
                expected,
                read_permission,
                write_permission,
            })
        };
        let timestamp = |control: &mut postgres::Client| {
            control
                .query_one(
                    "SELECT updated_at_ms FROM trnm_storage_objects \
                     WHERE collection = $1 AND object_key = $2 AND user_id = $3",
                    &[&collection, &key.key(), &owner.as_bytes().as_slice()],
                )
                .unwrap_or_else(|_| panic!("storage timestamp fixture: timestamp query failed"))
                .get::<_, i64>(0)
        };
        let snapshot = |control: &mut postgres::Client| {
            let row = control
                .query_one(
                    "SELECT value_bytes, version_digest, read_permission, write_permission, \
                     updated_at_ms, create_time::TEXT, update_time::TEXT \
                     FROM public.trnm_storage_objects \
                     WHERE collection = $1 AND object_key = $2 AND user_id = $3",
                    &[&collection, &key.key(), &owner.as_bytes().as_slice()],
                )
                .unwrap_or_else(|_| panic!("storage timestamp fixture: row snapshot failed"));
            (
                row.get::<_, Vec<u8>>(0),
                row.get::<_, Vec<u8>>(1),
                row.get::<_, i16>(2),
                row.get::<_, i16>(3),
                row.get::<_, i64>(4),
                row.get::<_, Option<String>>(5),
                row.get::<_, Option<String>>(6),
            )
        };
        let created = repository
            .apply_storage_batch(
                StorageActor::User(owner),
                &[write(
                    &key,
                    VersionCheck::MustNotExist,
                    ReadPermission::Owner,
                    WritePermission::Owner,
                )],
                10,
            )
            .unwrap();
        let version = created[0].current_version.unwrap();
        assert_eq!(timestamp(&mut control), 10);
        let unchanged = repository
            .apply_storage_batch(
                StorageActor::User(owner),
                &[write(
                    &key,
                    VersionCheck::Any,
                    ReadPermission::Owner,
                    WritePermission::Owner,
                )],
                20,
            )
            .unwrap();
        assert_eq!(unchanged.len(), 1);
        assert_eq!(unchanged[0].key, key);
        assert_eq!(unchanged[0].previous_version, Some(version));
        assert_eq!(unchanged[0].current_version, Some(version));
        assert_eq!(timestamp(&mut control), 10);

        repository
            .apply_storage_batch(
                StorageActor::User(owner),
                &[write(
                    &key,
                    VersionCheck::Exact(version.into()),
                    ReadPermission::Owner,
                    WritePermission::Owner,
                )],
                30,
            )
            .unwrap();
        assert_eq!(timestamp(&mut control), 30);
        let stale = trnm_persistence_pg::ContentVersion::from_value(br#"{"v":0}"#);
        for (actor, expected, code) in [
            (
                StorageActor::User(other),
                VersionCheck::Any,
                StableCode::PermissionDenied,
            ),
            (
                StorageActor::User(owner),
                VersionCheck::Exact(stale.into()),
                StableCode::FailedPrecondition,
            ),
            (
                StorageActor::User(owner),
                VersionCheck::MustNotExist,
                StableCode::AlreadyExists,
            ),
        ] {
            let error = repository
                .apply_storage_batch(
                    actor,
                    &[write(
                        &key,
                        expected,
                        ReadPermission::Owner,
                        WritePermission::Owner,
                    )],
                    35,
                )
                .unwrap_err();
            assert_eq!(error.code(), code);
            assert_eq!(timestamp(&mut control), 30);
        }

        // The incoming exact condition is an opaque, case-sensitive token.
        // Its shape must not produce a parse error or a varchar(32) SQL cast.
        let opaque_tokens = [
            "non-hex-version".to_owned(),
            version.as_str().to_ascii_uppercase(),
            "版本🍀".to_owned(),
            format!("{}suffix", version.as_str()),
            "x".repeat(4096),
        ];
        let before_opaque_rejection = snapshot(&mut control);
        for token in &opaque_tokens {
            assert_ne!(token, version.as_str());
            for actor in [StorageActor::User(owner), StorageActor::Server] {
                let exact_write = write(
                    &key,
                    VersionCheck::Exact(token.clone().into()),
                    ReadPermission::Public,
                    WritePermission::None,
                );
                let conditional_delete = StorageBatchOperation::Delete(StorageDeleteOperation {
                    key: key.clone(),
                    expected_version: Some(token.clone().into()),
                });
                for rejected_operation in [exact_write, conditional_delete] {
                    for prefixed in [false, true] {
                        let mut operations = Vec::new();
                        if prefixed {
                            operations.push(write(
                                &staged_key,
                                VersionCheck::MustNotExist,
                                ReadPermission::Owner,
                                WritePermission::Owner,
                            ));
                        }
                        operations.push(rejected_operation.clone());
                        let error = repository
                            .apply_storage_batch(actor, &operations, 36)
                            .unwrap_err();
                        assert_eq!(error.code(), StableCode::FailedPrecondition);
                        assert_eq!(error.reason(), "storage_version_mismatch");
                        assert_eq!(snapshot(&mut control), before_opaque_rejection);
                        assert_eq!(
                            repository
                                .read_storage_object(StorageActor::Server, &staged_key)
                                .unwrap_err()
                                .code(),
                            StableCode::NotFound
                        );
                    }
                }
            }
            let missing_write = repository
                .apply_storage_batch(
                    StorageActor::User(owner),
                    &[write(
                        &missing,
                        VersionCheck::Exact(token.clone().into()),
                        ReadPermission::Owner,
                        WritePermission::Owner,
                    )],
                    37,
                )
                .unwrap_err();
            assert_eq!(missing_write.code(), StableCode::FailedPrecondition);
            assert_eq!(missing_write.reason(), "storage_version_mismatch");
            assert_eq!(snapshot(&mut control), before_opaque_rejection);
            assert_eq!(
                repository
                    .read_storage_object(StorageActor::Server, &missing)
                    .unwrap_err()
                    .code(),
                StableCode::NotFound
            );
        }
        // '*' has insert-only meaning for writes, but is a literal delete
        // condition. It cannot turn a conditional delete into an unconditional one.
        let delete_star = repository
            .apply_storage_batch(
                StorageActor::User(owner),
                &[StorageBatchOperation::Delete(StorageDeleteOperation {
                    key: key.clone(),
                    expected_version: Some("*".into()),
                })],
                38,
            )
            .unwrap_err();
        assert_eq!(delete_star.code(), StableCode::FailedPrecondition);
        assert_eq!(delete_star.reason(), "storage_version_mismatch");
        assert_eq!(snapshot(&mut control), before_opaque_rejection);

        for (updated_at_ms, read_permission, write_permission) in [
            (40, ReadPermission::Public, WritePermission::Owner),
            (60, ReadPermission::Public, WritePermission::None),
        ] {
            repository
                .apply_storage_batch(
                    StorageActor::User(owner),
                    &[write(
                        &key,
                        VersionCheck::Any,
                        read_permission,
                        write_permission,
                    )],
                    updated_at_ms,
                )
                .unwrap();
            assert_eq!(
                timestamp(&mut control),
                i64::try_from(updated_at_ms).unwrap()
            );
            let object = repository
                .read_storage_object(StorageActor::Server, &key)
                .unwrap();
            assert_eq!(object.value, value);
            assert_eq!(object.read_permission, read_permission);
            assert_eq!(object.write_permission, write_permission);
        }
        let before_rejection = snapshot(&mut control);
        for (actor, expected, code, reason) in [
            (
                StorageActor::User(owner),
                VersionCheck::Any,
                StableCode::PermissionDenied,
                "storage_write_permission_denied",
            ),
            (
                StorageActor::User(owner),
                VersionCheck::Exact(version.into()),
                StableCode::PermissionDenied,
                "storage_write_permission_denied",
            ),
            (
                StorageActor::User(owner),
                VersionCheck::Exact(stale.into()),
                StableCode::PermissionDenied,
                "storage_write_permission_denied",
            ),
            (
                StorageActor::User(owner),
                VersionCheck::MustNotExist,
                StableCode::AlreadyExists,
                "storage_object_already_exists",
            ),
            (
                StorageActor::User(other),
                VersionCheck::MustNotExist,
                StableCode::PermissionDenied,
                "storage_write_permission_denied",
            ),
            (
                StorageActor::Server,
                VersionCheck::MustNotExist,
                StableCode::AlreadyExists,
                "storage_object_already_exists",
            ),
        ] {
            let error = repository
                .apply_storage_batch(
                    actor,
                    &[write(
                        &key,
                        expected.clone(),
                        ReadPermission::Owner,
                        WritePermission::Owner,
                    )],
                    70,
                )
                .unwrap_err();
            assert_eq!((error.code(), error.reason()), (code, reason));
            assert_eq!(snapshot(&mut control), before_rejection);
            if actor == StorageActor::User(owner) || actor == StorageActor::Server {
                let error = repository
                    .apply_storage_batch(
                        actor,
                        &[
                            write(
                                &staged_key,
                                VersionCheck::MustNotExist,
                                ReadPermission::Owner,
                                WritePermission::Owner,
                            ),
                            write(
                                &key,
                                expected,
                                ReadPermission::Owner,
                                WritePermission::Owner,
                            ),
                        ],
                        75,
                    )
                    .unwrap_err();
                assert_eq!((error.code(), error.reason()), (code, reason));
                assert_eq!(snapshot(&mut control), before_rejection);
                assert_eq!(
                    repository
                        .read_storage_object(StorageActor::Server, &staged_key)
                        .unwrap_err()
                        .code(),
                    StableCode::NotFound
                );
            }
        }
        for expected_version in [None, Some(version.into()), Some(stale.into())] {
            let error = repository
                .apply_storage_batch(
                    StorageActor::User(owner),
                    &[
                        write(
                            &staged_key,
                            VersionCheck::MustNotExist,
                            ReadPermission::Owner,
                            WritePermission::Owner,
                        ),
                        StorageBatchOperation::Delete(StorageDeleteOperation {
                            key: key.clone(),
                            expected_version,
                        }),
                    ],
                    76,
                )
                .unwrap_err();
            assert_eq!(error.code(), StableCode::PermissionDenied);
            assert_eq!(error.reason(), "storage_write_permission_denied");
            assert_eq!(snapshot(&mut control), before_rejection);
            assert_eq!(
                repository
                    .read_storage_object(StorageActor::Server, &staged_key)
                    .unwrap_err()
                    .code(),
                StableCode::NotFound
            );
        }
        for token in opaque_tokens.iter().map(String::as_str).chain(["*"]) {
            for actor in [StorageActor::User(owner), StorageActor::Server] {
                let expected = if actor == StorageActor::User(owner) {
                    (
                        StableCode::PermissionDenied,
                        "storage_write_permission_denied",
                    )
                } else {
                    (StableCode::FailedPrecondition, "storage_version_mismatch")
                };
                let mut rejected_operations = Vec::new();
                if token != "*" {
                    rejected_operations.push(write(
                        &key,
                        VersionCheck::Exact(token.into()),
                        ReadPermission::Owner,
                        WritePermission::Owner,
                    ));
                }
                rejected_operations.push(StorageBatchOperation::Delete(StorageDeleteOperation {
                    key: key.clone(),
                    expected_version: Some(token.into()),
                }));
                for rejected_operation in rejected_operations {
                    let error = repository
                        .apply_storage_batch(
                            actor,
                            &[
                                write(
                                    &staged_key,
                                    VersionCheck::MustNotExist,
                                    ReadPermission::Owner,
                                    WritePermission::Owner,
                                ),
                                rejected_operation,
                            ],
                            76,
                        )
                        .unwrap_err();
                    assert_eq!((error.code(), error.reason()), expected);
                    assert_eq!(snapshot(&mut control), before_rejection);
                    assert_eq!(
                        repository
                            .read_storage_object(StorageActor::Server, &staged_key)
                            .unwrap_err()
                            .code(),
                        StableCode::NotFound
                    );
                }
            }
        }
        for actor in [
            StorageActor::User(other),
            StorageActor::User(UserId::new([0; 16])),
        ] {
            for target in [&key, &missing] {
                let error = repository
                    .apply_storage_batch(
                        actor,
                        &[write(
                            target,
                            VersionCheck::MustNotExist,
                            ReadPermission::Owner,
                            WritePermission::Owner,
                        )],
                        76,
                    )
                    .unwrap_err();
                assert_eq!(error.code(), StableCode::PermissionDenied);
                assert_eq!(snapshot(&mut control), before_rejection);
                assert_eq!(
                    repository
                        .read_storage_object(StorageActor::Server, &missing)
                        .unwrap_err()
                        .code(),
                    StableCode::NotFound
                );
            }
        }
        repository
            .apply_storage_batch(
                StorageActor::Server,
                &[write(
                    &key,
                    VersionCheck::Any,
                    ReadPermission::Public,
                    WritePermission::None,
                )],
                80,
            )
            .unwrap();
        assert_eq!(timestamp(&mut control), 60);
        repository
            .apply_storage_batch(
                StorageActor::Server,
                &[write(
                    &key,
                    VersionCheck::Exact(version.into()),
                    ReadPermission::Public,
                    WritePermission::None,
                )],
                90,
            )
            .unwrap();
        assert_eq!(timestamp(&mut control), 90);

        let failed_batch = repository
            .apply_storage_batch(
                StorageActor::Server,
                &[
                    write(
                        &key,
                        VersionCheck::Any,
                        ReadPermission::Public,
                        WritePermission::None,
                    ),
                    write(
                        &staged_key,
                        VersionCheck::Any,
                        ReadPermission::Owner,
                        WritePermission::Owner,
                    ),
                    write(
                        &missing,
                        VersionCheck::Exact(version.into()),
                        ReadPermission::Owner,
                        WritePermission::Owner,
                    ),
                ],
                100,
            )
            .unwrap_err();
        assert_eq!(failed_batch.code(), StableCode::FailedPrecondition);
        assert_eq!(timestamp(&mut control), 90);
        assert_eq!(
            repository
                .read_storage_object(StorageActor::Server, &staged_key)
                .unwrap_err()
                .code(),
            StableCode::NotFound
        );
        control
            .execute(
                "UPDATE trnm_storage_objects SET version_digest = $1 \
                 WHERE collection = $2 AND object_key = $3 AND user_id = $4",
                &[
                    &[1_u8; 32].as_slice(),
                    &collection,
                    &key.key(),
                    &owner.as_bytes().as_slice(),
                ],
            )
            .unwrap_or_else(|_| panic!("storage timestamp fixture: integrity corruption failed"));
        for expected in [VersionCheck::Any, VersionCheck::MustNotExist] {
            let corrupt = repository
                .apply_storage_batch(
                    StorageActor::Server,
                    &[write(
                        &key,
                        expected,
                        ReadPermission::Public,
                        WritePermission::None,
                    )],
                    110,
                )
                .unwrap_err();
            assert_eq!(corrupt.code(), StableCode::DataLoss);
            assert_eq!(corrupt.reason(), "storage_integrity_digest_mismatch");
            assert_eq!(timestamp(&mut control), 90);
        }
    }));
    cleanup(&mut control);
    if let Err(panic) = outcome {
        std::panic::resume_unwind(panic);
    }
    let remaining: i64 = control
        .query_one(
            "SELECT count(*) FROM trnm_storage_objects WHERE collection = $1 AND user_id = $2",
            &[&collection, &owner.as_bytes().as_slice()],
        )
        .unwrap_or_else(|_| panic!("storage timestamp fixture: cleanup assertion failed"))
        .get(0);
    assert_eq!(remaining, 0);
    println!(
        "\nstorage_blind_write_timestamps_executed profile={}",
        profile.metadata_value()
    );
}
