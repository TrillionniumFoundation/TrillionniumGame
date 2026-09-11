// This profile-neutral live regression is part of the exact server source-checker
// authority set; omission or include-order substitution is rejected by control-plane tests.
#[test]
fn storage_listing_acl_owner_scope_and_cursor_are_stable() {
    let Some((database_url, profile)) = live_database_environment("storage listing contract")
    else {
        return;
    };
    let owner_a = UserId::new([0x91; 16]);
    let owner_b = UserId::new([0x92; 16]);
    let collection = "listing-contract-v1";
    let key_a = StorageObjectKey::new(collection, "a", owner_a).unwrap();
    let key_b_a = StorageObjectKey::new(collection, "b", owner_a).unwrap();
    let key_b_b = StorageObjectKey::new(collection, "b", owner_b).unwrap();
    let key_c = StorageObjectKey::new(collection, "c", owner_b).unwrap();
    let key_d = StorageObjectKey::new(collection, "d", owner_b).unwrap();
    let mut repository = PgRepository::connect(&database_url, profile).unwrap();

    let writes = [
        (&key_a, ReadPermission::Owner, b"a-private".as_slice()),
        (&key_b_a, ReadPermission::Public, b"b-a-public".as_slice()),
        (&key_b_b, ReadPermission::Public, b"b-b-public".as_slice()),
        (&key_c, ReadPermission::Owner, b"c-private".as_slice()),
        (&key_d, ReadPermission::Public, b"d-public".as_slice()),
    ]
    .into_iter()
    .map(|(key, read_permission, value)| {
        StorageBatchOperation::Write(StorageWriteOperation {
            key: key.clone(),
            value: value.to_vec(),
            expected: VersionCheck::MustNotExist,
            read_permission,
            write_permission: WritePermission::Owner,
        })
    })
    .collect::<Vec<_>>();
    repository
        .apply_storage_batch(StorageActor::Server, &writes, 50)
        .unwrap();

    let (server_page_1, server_cursor_1) = repository
        .list_storage_objects(StorageActor::Server, collection, None, None, 2)
        .unwrap();
    assert_eq!(
        server_page_1
            .iter()
            .map(|object| object.key.clone())
            .collect::<Vec<_>>(),
        vec![key_a.clone(), key_b_a.clone()]
    );
    assert_eq!(
        server_cursor_1.as_ref().map(|cursor| &cursor.2),
        Some(&key_b_a)
    );

    let (server_page_2, server_cursor_2) = repository
        .list_storage_objects(
            StorageActor::Server,
            collection,
            None,
            server_cursor_1.as_ref(),
            2,
        )
        .unwrap();
    assert_eq!(
        server_page_2
            .iter()
            .map(|object| object.key.clone())
            .collect::<Vec<_>>(),
        vec![key_b_b.clone(), key_c.clone()]
    );
    assert_eq!(
        server_cursor_2.as_ref().map(|cursor| &cursor.2),
        Some(&key_c)
    );

    let (server_page_3, server_cursor_3) = repository
        .list_storage_objects(
            StorageActor::Server,
            collection,
            None,
            server_cursor_2.as_ref(),
            2,
        )
        .unwrap();
    assert_eq!(
        server_page_3
            .iter()
            .map(|object| object.key.clone())
            .collect::<Vec<_>>(),
        vec![key_d.clone()]
    );
    assert_eq!(server_cursor_3, None);

    let (owner_a_page_1, owner_a_cursor_1) = repository
        .list_storage_objects(StorageActor::User(owner_a), collection, None, None, 2)
        .unwrap();
    assert_eq!(
        owner_a_page_1
            .iter()
            .map(|object| object.key.clone())
            .collect::<Vec<_>>(),
        vec![key_a.clone(), key_b_a.clone()]
    );
    assert_eq!(
        owner_a_cursor_1.as_ref().map(|cursor| &cursor.2),
        Some(&key_b_a)
    );

    let (owner_a_page_2, owner_a_cursor_2) = repository
        .list_storage_objects(
            StorageActor::User(owner_a),
            collection,
            None,
            owner_a_cursor_1.as_ref(),
            2,
        )
        .unwrap();
    assert_eq!(
        owner_a_page_2
            .iter()
            .map(|object| object.key.clone())
            .collect::<Vec<_>>(),
        vec![key_b_b.clone(), key_d.clone()]
    );
    assert_eq!(owner_a_cursor_2, None);

    let (foreign_owner_page, foreign_owner_cursor) = repository
        .list_storage_objects(
            StorageActor::User(owner_a),
            collection,
            Some(owner_b),
            None,
            2,
        )
        .unwrap();
    assert_eq!(
        foreign_owner_page
            .iter()
            .map(|object| object.key.clone())
            .collect::<Vec<_>>(),
        vec![key_b_b.clone(), key_d.clone()]
    );
    assert_eq!(foreign_owner_cursor, None);

    let server_unscoped_cursor = (StorageActor::Server, None, key_b_a.clone());
    assert_eq!(
        repository
            .list_storage_objects(
                StorageActor::Server,
                collection,
                Some(owner_b),
                Some(&server_unscoped_cursor),
                1,
            )
            .unwrap_err()
            .reason(),
        "storage_cursor_scope_mismatch"
    );
    assert_eq!(
        repository
            .list_storage_objects(
                StorageActor::User(owner_a),
                collection,
                None,
                Some(&server_unscoped_cursor),
                1,
            )
            .unwrap_err()
            .reason(),
        "storage_cursor_scope_mismatch"
    );

    let foreign_owner_scope = (
        StorageActor::User(owner_a),
        Some(owner_b),
        key_b_b.clone(),
    );
    assert_eq!(
        repository
            .list_storage_objects(
                StorageActor::User(owner_a),
                collection,
                None,
                Some(&foreign_owner_scope),
                1,
            )
            .unwrap_err()
            .reason(),
        "storage_cursor_scope_mismatch"
    );
    assert_eq!(
        repository
            .list_storage_objects(
                StorageActor::User(owner_b),
                collection,
                Some(owner_b),
                Some(&foreign_owner_scope),
                1,
            )
            .unwrap_err()
            .reason(),
        "storage_cursor_scope_mismatch"
    );
}

// The same test runs against PostgreSQL and CockroachDB. The expected sequence is
// derived from Rust's exact UTF-8 bytes plus the 16-byte user identity, never from
// an ambient database locale or text collation.
#[test]
fn storage_listing_unicode_order_matches_canonical_utf8_bytes() {
    let Some((database_url, profile)) =
        live_database_environment("storage canonical byte order contract")
    else {
        return;
    };
    let collection = "listing-byte-order-v1";
    let hostile = [
        ("é", 8_u8),
        ("A", 7),
        ("中", 6),
        ("a", 5),
        ("e\u{301}", 4),
        ("~", 3),
        ("_", 2),
        ("😀", 1),
        ("a", 1),
    ];
    let mut keys = hostile
        .into_iter()
        .map(|(object_key, user)| {
            StorageObjectKey::new(collection, object_key, UserId::new([user; 16])).unwrap()
        })
        .collect::<Vec<_>>();
    let writes = keys
        .iter()
        .enumerate()
        .map(|(index, key)| {
            StorageBatchOperation::Write(StorageWriteOperation {
                key: key.clone(),
                value: format!("canonical-byte-order-{index}").into_bytes(),
                expected: VersionCheck::MustNotExist,
                read_permission: ReadPermission::Public,
                write_permission: WritePermission::Owner,
            })
        })
        .collect::<Vec<_>>();

    let mut repository = PgRepository::connect(&database_url, profile).unwrap();
    repository
        .apply_storage_batch(StorageActor::Server, &writes, 70)
        .unwrap();

    keys.sort_by(|left, right| {
        left.key()
            .as_bytes()
            .cmp(right.key().as_bytes())
            .then_with(|| left.user_id().as_bytes().cmp(right.user_id().as_bytes()))
    });

    let mut observed = Vec::new();
    let mut cursor = None;
    let mut page_count = 0_usize;
    loop {
        page_count += 1;
        assert!(page_count <= 4, "cursor failed to make bounded progress");
        let (page, next) = repository
            .list_storage_objects(
                StorageActor::Server,
                collection,
                None,
                cursor.as_ref(),
                3,
            )
            .unwrap();
        observed.extend(page.into_iter().map(|object| object.key));
        cursor = next;
        if cursor.is_none() {
            break;
        }
    }

    assert_eq!(page_count, 3);
    assert_eq!(observed, keys);
    assert_eq!(
        observed
            .iter()
            .map(|key| (key.key(), key.user_id().as_bytes()[0]))
            .collect::<Vec<_>>(),
        vec![
            ("A", 7),
            ("_", 2),
            ("a", 1),
            ("a", 5),
            ("e\u{301}", 4),
            ("~", 3),
            ("é", 8),
            ("中", 6),
            ("😀", 1),
        ]
    );
}
