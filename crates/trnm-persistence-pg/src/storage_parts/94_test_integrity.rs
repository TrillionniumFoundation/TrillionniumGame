#[test]
fn permission_and_version_contract_matches_storage_core() {
    let owner = UserId::new([1; 16]);
    let object = StorageObject {
        key: key(1),
        value: b"v1".to_vec(),
        version: ContentVersion::from_value(b"v1").into(),
        collision_witness: None,
        integrity_digest: IntegrityDigest::from_value(b"v1"),
        read_permission: ReadPermission::OWNER,
        write_permission: WritePermission::OWNER,
    };
    authorize_read(Actor::User(owner), &object).unwrap();
    assert_eq!(
        authorize_read(Actor::User(UserId::new([2; 16])), &object)
            .unwrap_err()
            .reason(),
        "storage_read_permission_denied"
    );
    validate_version(
        Some(&object),
        &VersionCheck::Exact(object.version.clone().into()),
    )
    .unwrap();
    assert_eq!(
        validate_version(
            Some(&object),
            &VersionCheck::Exact(ContentVersion::from_value(b"other").into()),
        )
        .unwrap_err()
        .reason(),
        "storage_version_mismatch"
    );
    let tokens = [
        "non-hex-version".to_owned(),
        object.version.as_str().to_ascii_uppercase(),
        "版本🍀".to_owned(),
        format!("{}suffix", object.version.as_str()),
        "x".repeat(4096),
    ];
    for token in tokens {
        assert_ne!(token, object.version.as_str());
        let check = VersionCheck::Exact(token.into());
        for existing in [Some(&object), None] {
            let error = validate_version(existing, &check).unwrap_err();
            assert_eq!(error.code(), StableCode::FailedPrecondition);
            assert_eq!(error.reason(), "storage_version_mismatch");
        }
    }
}

#[test]
fn readback_integrity_rejects_caller_chosen_or_corrupt_digest() {
    let canonical = IntegrityDigest::from_value(b"value");
    verify_storage_integrity(b"value", canonical).unwrap();
    let attacker_chosen = IntegrityDigest::new(Digest32::new([0x44; 32])).unwrap();
    assert_eq!(
        verify_storage_integrity(b"value", attacker_chosen)
            .unwrap_err()
            .reason(),
        "storage_integrity_digest_mismatch"
    );
}

#[test]
fn database_permission_decoders_are_total() {
    assert_eq!(
        decode_storage_key("a".to_owned(), "b".to_owned(), vec![1; 16])
            .unwrap()
            .user_id(),
        UserId::new([1; 16])
    );
}

#[test]
fn raw_write_and_delete_permissions_keep_distinct_authority_and_owner_binding() {
    let owner = UserId::new([1; 16]);
    let foreign = UserId::new([2; 16]);
    let key = key(1);
    for raw in [0, 1, 2, 32767] {
        let permission = WritePermission::from_stored(raw).unwrap();
        assert_eq!(
            authorize_write_permission(Actor::User(owner), &key, Some(permission)).is_ok(),
            raw == 1,
        );
        assert_eq!(
            authorize_delete_permission(Actor::User(owner), &key, Some(permission)).is_ok(),
            raw > 0,
        );
        for actor in [Actor::User(foreign), Actor::User(UserId::new([0; 16]))] {
            assert_eq!(
                authorize_write_permission(actor, &key, Some(permission))
                    .unwrap_err()
                    .code(),
                StableCode::PermissionDenied,
            );
            assert_eq!(
                authorize_delete_permission(actor, &key, Some(permission))
                    .unwrap_err()
                    .code(),
                StableCode::PermissionDenied,
            );
        }
        authorize_write_permission(Actor::Server, &key, Some(permission)).unwrap();
        authorize_delete_permission(Actor::Server, &key, Some(permission)).unwrap();
    }
}

#[test]
fn lawful_raw_list_permission_is_not_a_batch_read_permission() {
    let owner = UserId::new([1; 16]);
    for raw in [3, 32767] {
        let mut object = object(1);
        object.read_permission = ReadPermission::from_stored(raw).unwrap();
        assert_eq!(
            authorize_read(Actor::User(owner), &object)
                .unwrap_err()
                .code(),
            StableCode::PermissionDenied,
        );
        authorize_read(Actor::Server, &object).unwrap();
        let page = finish_stored_client_storage_page(vec![StoredStorageObject::from(object)], true);
        assert_eq!(page.next.unwrap().read, i32::from(raw));
    }
}

#[test]
fn database_stored_key_decoder_preserves_empty_and_unicode_domains() {
    for (collection, object_key) in [
        (String::new(), String::new()),
        (".\n".to_owned(), "界".repeat(128)),
    ] {
        let key = decode_storage_key(collection.clone(), object_key.clone(), vec![0; 16]).unwrap();
        assert_eq!(key.collection(), collection);
        assert_eq!(key.key(), object_key);
        assert!(key.user_id().is_zero());
    }
}

#[test]
fn read_batch_accepts_empty_and_bounded_server_owned_keys() {
    let actor = Actor::User(UserId::new([1; 16]));
    validate_read_batch(actor, &[]).unwrap();
    let global = StorageObjectKey::new("system", "global", UserId::new([0; 16])).unwrap();
    validate_read_batch(actor, &vec![global.clone(); MAX_BATCH_OPERATIONS]).unwrap();
    validate_read_batch(Actor::Server, std::slice::from_ref(&global)).unwrap();
    let error = validate_read_batch(actor, &vec![global; MAX_BATCH_OPERATIONS + 1]).unwrap_err();
    assert_eq!(error.code(), StableCode::InvalidArgument);
    assert_eq!(error.reason(), "invalid_storage_batch_size");
}

#[test]
fn read_batch_rejects_zero_actor_before_empty_batch_shortcut() {
    let error = validate_read_batch(Actor::User(UserId::new([0; 16])), &[]).unwrap_err();
    assert_eq!(error.code(), StableCode::InvalidArgument);
    assert_eq!(error.reason(), "invalid_storage_actor");
}

#[test]
fn native_result_budget_accumulates_and_rejects_without_wrapping() {
    let mut total = 0;
    consume_storage_result_budget(&mut total, MAX_NATIVE_VALUE_BYTES).unwrap();
    consume_storage_result_budget(&mut total, MAX_NATIVE_VALUE_BYTES).unwrap();
    assert_eq!(total, MAX_RESULT_VALUE_BYTES);
    let error = consume_storage_result_budget(&mut total, 1).unwrap_err();
    assert_eq!(error.code(), StableCode::ResourceExhausted);
    assert_eq!(total, MAX_RESULT_VALUE_BYTES);
    let error = consume_storage_result_budget(&mut total, usize::MAX).unwrap_err();
    assert_eq!(error.code(), StableCode::ResourceExhausted);
    assert_eq!(total, MAX_RESULT_VALUE_BYTES);
}

#[test]
fn nakama_insert_only_route_excludes_typed_any_exact_and_delete_policies() {
    let mut operation = match write(1) {
        BatchOperation::Write(write) => write,
        BatchOperation::Delete(_) => unreachable!(),
    };
    operation.expected = VersionCheck::MustNotExist;
    let insert_only = BatchOperation::Write(operation.clone());
    let selected = nakama_insert_only_write(Some(NakamaBatchKind::Write), &insert_only).unwrap();
    assert!(std::ptr::eq(
        selected,
        match &insert_only {
            BatchOperation::Write(write) => write,
            BatchOperation::Delete(_) => unreachable!(),
        },
    ));
    assert!(nakama_insert_only_write(None, &insert_only).is_none());
    assert!(nakama_insert_only_write(Some(NakamaBatchKind::Delete), &insert_only).is_none());
    for expected in [
        VersionCheck::Any,
        VersionCheck::Exact("*".into()),
        VersionCheck::Exact("raw\0token".into()),
    ] {
        operation.expected = expected;
        let other = BatchOperation::Write(operation.clone());
        assert!(nakama_insert_only_write(Some(NakamaBatchKind::Write), &other).is_none());
    }
    let delete = BatchOperation::Delete(DeleteOperation {
        key: operation.key,
        expected_version: Some("*".into()),
    });
    for kind in [
        None,
        Some(NakamaBatchKind::Write),
        Some(NakamaBatchKind::Delete),
    ] {
        assert!(nakama_insert_only_write(kind, &delete).is_none());
    }
}

#[test]
fn native_insert_only_unique_rejection_is_scoped_to_plain_nakama_insert() {
    let star = VersionCheck::MustNotExist;
    let exact = VersionCheck::Exact("literal-token".into());
    let expected = error(
        StableCode::AlreadyExists,
        "storage_object_already_exists",
        RetryClass::Never,
    );
    assert_eq!(
        nakama_insert_only_unique_rejection(
            StorageWriteBinding::Nakama,
            &star,
            false,
            Some("23505")
        ),
        Some(expected),
    );
    for (binding, check, previous_exists) in [
        (StorageWriteBinding::Typed, &star, false),
        (StorageWriteBinding::Typed, &star, true),
        (StorageWriteBinding::Nakama, &VersionCheck::Any, false),
        (StorageWriteBinding::Nakama, &VersionCheck::Any, true),
        (StorageWriteBinding::Nakama, &exact, false),
        (StorageWriteBinding::Nakama, &exact, true),
        (StorageWriteBinding::Nakama, &star, true),
    ] {
        assert!(nakama_insert_only_unique_rejection(
            binding,
            check,
            previous_exists,
            Some("23505")
        )
        .is_none());
    }
    for code in [
        None,
        Some("40001"),
        Some("40P01"),
        Some("23503"),
        Some("23502"),
        Some("23514"),
        Some("22P02"),
        Some("22021"),
        Some("08006"),
    ] {
        assert!(nakama_insert_only_unique_rejection(
            StorageWriteBinding::Nakama,
            &star,
            false,
            code
        )
        .is_none());
    }
    // The generic classifier remains independent, including retries and native
    // input failures. No native database error is fabricated by this unit test.
    assert_eq!(
        crate::classify_sqlstate("23505").reason(),
        "database_unique_violation"
    );
    assert_eq!(
        crate::classify_sqlstate("40001").retry(),
        RetryClass::SafeImmediate
    );
    assert_eq!(
        crate::classify_sqlstate("40P01").retry(),
        RetryClass::SafeBackoff
    );
    assert_eq!(
        crate::classify_sqlstate("22P02").reason(),
        "database_constraint_violation"
    );
}
