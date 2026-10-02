use super::*;

fn owner(value: u8) -> UserId {
    UserId::new([value; 16])
}

fn historical_object(key: &str, read: i16, write: i16) -> StorageObject {
    let value = b"[true, null, 17]".to_vec();
    StorageObject {
        key: StorageObjectKey::new_nakama("history", key, owner(1)).unwrap(),
        integrity_digest: IntegrityDigest::from_value(&value),
        value,
        version: PublicVersion::new("opaque-history").unwrap(),
        collision_witness: None,
        read_permission: ReadPermission::from_stored(read).unwrap(),
        write_permission: WritePermission::from_stored(write).unwrap(),
    }
}

fn state_with(object: StorageObject) -> StorageState {
    let mut state = StorageState::default();
    state.objects.insert(object.key.clone(), object);
    state
}

fn write_operation(object: &StorageObject, expected: VersionCheck) -> BatchOperation {
    BatchOperation::Write(WriteOperation {
        key: object.key.clone(),
        value: b"{}".to_vec(),
        expected,
        read_permission: ReadPermission::PUBLIC,
        write_permission: WritePermission::OWNER,
    })
}

#[test]
fn stored_nakama_identifiers_preserve_empty_unicode_control_and_owner_cells() {
    for user in [owner(1), owner(0)] {
        for (collection, key) in [
            (String::new(), String::new()),
            (String::new(), "key".to_owned()),
            ("collection".to_owned(), String::new()),
            ("界".repeat(128), "🦀".repeat(128)),
            (".collection\n".to_owned(), "\0\t.key".to_owned()),
        ] {
            let stored = StorageObjectKey::new_nakama(&collection, &key, user).unwrap();
            assert_eq!(stored.collection(), collection);
            assert_eq!(stored.key(), key);
            assert_eq!(stored.user_id(), user);
            assert!(StorageObjectKey::new(&collection, &key, user).is_err());
        }
        for too_long in ["a".repeat(129), "🦀".repeat(129)] {
            assert_eq!(
                StorageObjectKey::new_nakama(&too_long, "", user)
                    .unwrap_err()
                    .reason(),
                "invalid_storage_collection"
            );
            assert_eq!(
                StorageObjectKey::new_nakama("", &too_long, user)
                    .unwrap_err()
                    .reason(),
                "invalid_storage_key"
            );
        }
    }
    assert!(StorageObjectKey::new("collection", "key", owner(1)).is_ok());
}

#[test]
fn stored_permission_wrappers_preserve_full_nonnegative_smallint_domain() {
    for value in 0..=i16::MAX {
        let read = ReadPermission::from_stored(value).unwrap();
        let write = WritePermission::from_stored(value).unwrap();
        assert_eq!(read.get(), value);
        assert_eq!(write.get(), value);
        assert_eq!(read.as_i32(), i32::from(value));
        assert_eq!(write.as_i32(), i32::from(value));
    }
    assert_eq!(ReadPermission::NONE.get(), 0);
    assert_eq!(ReadPermission::OWNER.get(), 1);
    assert_eq!(ReadPermission::PUBLIC.get(), 2);
    assert_eq!(WritePermission::NONE.get(), 0);
    assert_eq!(WritePermission::OWNER.get(), 1);
    for value in [-1, i16::MIN] {
        let read = ReadPermission::from_stored(value).unwrap_err();
        assert_eq!(read.code(), StableCode::InvalidArgument);
        assert_eq!(read.reason(), "invalid_storage_read_permission");
        let write = WritePermission::from_stored(value).unwrap_err();
        assert_eq!(write.code(), StableCode::InvalidArgument);
        assert_eq!(write.reason(), "invalid_storage_write_permission");
    }
}

#[test]
fn stored_acl_predicates_keep_read_list_write_and_delete_distinct() {
    // Columns: batch-own, batch-other, all-public-list, own-list,
    // foreign/global-list, client-update, client-delete. This includes legal
    // source values for which the APIs intentionally disagree.
    for (value, expected) in [
        (0, [false, false, false, false, false, false, false]),
        (1, [true, false, false, true, false, true, true]),
        (2, [true, true, true, true, true, false, true]),
        (3, [false, false, true, true, false, false, true]),
        (i16::MAX, [false, false, true, true, false, false, true]),
    ] {
        let read = ReadPermission::from_stored(value).unwrap();
        let write = WritePermission::from_stored(value).unwrap();
        assert_eq!(
            [
                read.allows_batch_read(true),
                read.allows_batch_read(false),
                read.allows_public_listing(),
                read.allows_owner_listing(),
                read.allows_foreign_listing(),
                write.allows_client_write(),
                write.allows_client_delete(),
            ],
            expected,
            "stored ACL={value}"
        );
    }
}

#[test]
fn historical_raw_acl_read_visibility_uses_authenticated_owner_and_exact_read_values() {
    for (read, own_visible, other_visible) in [
        (0, false, false),
        (1, true, false),
        (2, true, true),
        (3, false, false),
        (i16::MAX, false, false),
    ] {
        let mut object = historical_object("row", read, i16::MAX);
        for user in [owner(1), owner(0)] {
            object.key = StorageObjectKey::new_nakama("", "", user).unwrap();
            let state = state_with(object.clone());
            for (actor, visible) in [
                (Actor::Server, true),
                (
                    Actor::User(owner(1)),
                    if user.is_zero() {
                        other_visible
                    } else {
                        own_visible
                    },
                ),
                (Actor::User(owner(2)), other_visible),
                (Actor::User(owner(0)), false),
            ] {
                let result = state.read(actor, &object.key);
                if visible {
                    assert_eq!(result.unwrap(), object);
                } else {
                    let error = result.unwrap_err();
                    assert_eq!(error.code(), StableCode::PermissionDenied);
                    assert_eq!(error.reason(), "storage_read_permission_denied");
                }
                assert_eq!(state.objects.get(&object.key), Some(&object));
            }
        }
    }
}

#[test]
fn historical_raw_acl_write_delete_and_occ_precedence_are_distinct() {
    for write in [0, 1, 2, 3, i16::MAX] {
        let object = historical_object("row", i16::MAX, write);
        let state = state_with(object.clone());
        for actor in [
            Actor::Server,
            Actor::User(owner(1)),
            Actor::User(owner(2)),
            Actor::User(owner(0)),
        ] {
            let own_authority = matches!(actor, Actor::Server) || actor == Actor::User(owner(1));
            let update_allowed = matches!(actor, Actor::Server) || (own_authority && write == 1);
            let delete_allowed = matches!(actor, Actor::Server) || (own_authority && write > 0);
            for expected in [
                VersionCheck::Any,
                VersionCheck::Exact("stale-token".into()),
                VersionCheck::MustNotExist,
            ] {
                let mut candidate = state.clone();
                let operation = write_operation(&object, expected.clone());
                let result = candidate.apply_batch(actor, &[operation]);
                let expected_error = match expected {
                    VersionCheck::Any if update_allowed => None,
                    VersionCheck::Any => Some(StableCode::PermissionDenied),
                    VersionCheck::Exact(_) if update_allowed => {
                        Some(StableCode::FailedPrecondition)
                    }
                    VersionCheck::Exact(_) => Some(StableCode::PermissionDenied),
                    VersionCheck::MustNotExist if own_authority => Some(StableCode::AlreadyExists),
                    VersionCheck::MustNotExist => Some(StableCode::PermissionDenied),
                };
                if let Some(expected_error) = expected_error {
                    assert_eq!(
                        result.unwrap_err().code(),
                        expected_error,
                        "write={write} actor={actor:?}"
                    );
                    assert_eq!(candidate, state);
                } else {
                    let receipt = result.unwrap();
                    assert_eq!(receipt[0].previous_version, Some(object.version.clone()));
                    assert_eq!(
                        receipt[0].current_version,
                        Some(ContentVersion::from_value(b"{}"))
                    );
                    assert_eq!(
                        candidate.read(Actor::Server, &object.key).unwrap().value,
                        b"{}"
                    );
                }
            }
            for token in ["*", "opaque-history"] {
                let mut candidate = state.clone();
                let result = candidate.apply_batch(
                    actor,
                    &[BatchOperation::Delete(DeleteOperation {
                        key: object.key.clone(),
                        expected_version: Some(token.into()),
                    })],
                );
                if !delete_allowed {
                    assert_eq!(result.unwrap_err().code(), StableCode::PermissionDenied);
                    assert_eq!(candidate, state);
                } else if token == "*" {
                    assert_eq!(result.unwrap_err().code(), StableCode::FailedPrecondition);
                    assert_eq!(candidate, state);
                } else {
                    let receipt = result.unwrap();
                    assert_eq!(receipt[0].previous_version, Some(object.version.clone()));
                    assert_eq!(receipt[0].current_version, None);
                    assert_eq!(candidate.object_count(), 0);
                }
            }
        }
    }
}

#[test]
fn blind_raw_acl_no_op_and_acl_change_preserve_or_replace_unknown_witness() {
    for (read, write, actor, next_read, next_write) in [
        (
            3,
            1,
            Actor::User(owner(1)),
            ReadPermission::PUBLIC,
            WritePermission::OWNER,
        ),
        (
            i16::MAX,
            i16::MAX,
            Actor::Server,
            ReadPermission::OWNER,
            WritePermission::OWNER,
        ),
    ] {
        let mut object = historical_object("row", read, write);
        object.version = ContentVersion::from_value(b"{}").into();
        let mut state = state_with(object.clone());
        let mut operation = WriteOperation {
            key: object.key.clone(),
            value: b"{}".to_vec(),
            expected: VersionCheck::Any,
            read_permission: object.read_permission,
            write_permission: object.write_permission,
        };
        let receipts = state
            .apply_batch_projected(actor, &[BatchOperation::Write(operation.clone())], |_| {
                panic!("unknown matching-token matching-ACL no-op must keep its native value")
            })
            .unwrap();
        assert_eq!(state.read(Actor::Server, &object.key).unwrap(), object);
        assert_eq!(receipts[0].previous_version, Some(object.version.clone()));

        operation.read_permission = next_read;
        operation.write_permission = next_write;
        let mut projected_calls = 0;
        state
            .apply_batch_projected(
                actor,
                &[BatchOperation::Write(operation.clone())],
                |request| {
                    projected_calls += 1;
                    assert_eq!(request, b"{}");
                    Ok(b"{ }".to_vec())
                },
            )
            .unwrap();
        assert_eq!(projected_calls, 1);
        let updated = state.read(Actor::Server, &object.key).unwrap();
        assert_eq!(updated.value, b"{ }");
        assert_eq!(updated.version, object.version);
        assert_eq!(updated.read_permission, next_read);
        assert_eq!(updated.write_permission, next_write);
        assert!(updated.collision_witness.unwrap().matches_request(b"{}"));

        operation.expected = VersionCheck::Exact(updated.version.clone().into());
        let mut exact_calls = 0;
        state
            .apply_batch_projected(actor, &[BatchOperation::Write(operation)], |_| {
                exact_calls += 1;
                Ok(b"{}".to_vec())
            })
            .unwrap();
        assert_eq!(exact_calls, 1);
        assert_eq!(state.read(Actor::Server, &object.key).unwrap().value, b"{}");
    }
}

#[test]
fn historical_acl_batch_failure_preserves_values_versions_witnesses_and_all_rows() {
    let first = historical_object("first", 3, 3);
    let second = historical_object("second", i16::MAX, i16::MAX);
    let mut state = state_with(first.clone());
    state.objects.insert(second.key.clone(), second.clone());
    let snapshot = state.clone();
    let error = state
        .apply_batch(
            Actor::User(owner(1)),
            &[
                BatchOperation::Delete(DeleteOperation {
                    key: first.key.clone(),
                    expected_version: Some(first.version.clone().into()),
                }),
                write_operation(&second, VersionCheck::Any),
            ],
        )
        .unwrap_err();
    assert_eq!(error.code(), StableCode::PermissionDenied);
    assert_eq!(state, snapshot);

    let mut writable = second.clone();
    writable.key = StorageObjectKey::new_nakama("history", "third", owner(1)).unwrap();
    writable.write_permission = WritePermission::OWNER;
    state.objects.insert(writable.key.clone(), writable.clone());
    let snapshot = state.clone();
    let error = state
        .apply_batch(
            Actor::User(owner(1)),
            &[
                write_operation(&writable, VersionCheck::Any),
                BatchOperation::Delete(DeleteOperation {
                    key: first.key.clone(),
                    expected_version: Some("*".into()),
                }),
            ],
        )
        .unwrap_err();
    assert_eq!(error.code(), StableCode::FailedPrecondition);
    assert_eq!(state, snapshot);
}
