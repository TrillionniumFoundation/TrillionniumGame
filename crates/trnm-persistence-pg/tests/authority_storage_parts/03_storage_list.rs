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

    let foreign_owner_scope = (StorageActor::User(owner_a), Some(owner_b), key_b_b.clone());
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
            .list_storage_objects(StorageActor::Server, collection, None, cursor.as_ref(), 3)
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

#[test]
fn nakama_client_listing_modes_cursors_and_integrity_are_database_projected() {
    let Some((database_url, profile)) = live_database_environment("Nakama client list projection")
    else {
        eprintln!("nakama_client_list_projection_skipped: optional database is absent");
        return;
    };
    let owner = UserId::new([0xb1; 16]);
    let other = UserId::new([0xb2; 16]);
    let global = UserId::new([0; 16]);
    let collection = "nakama-client-list-contract";
    let text_collection = "nakama-client-list-text-contract";
    let wide_collection = format!("nakama-list-wide-{}", "界".repeat(111));
    let control_collection = ".nakama-list-control-\n";
    let mut control = postgres::Client::connect(&database_url, postgres::NoTls)
        .unwrap_or_else(|_| panic!("Nakama client list fixture: control connection failed"));
    let cleanup = |control: &mut postgres::Client| {
        control
            .execute(
                "DELETE FROM trnm_storage_objects \
                 WHERE collection IN ($1, $2, $3, $4) AND user_id IN ($5, $6, $7)",
                &[
                    &collection,
                    &text_collection,
                    &wide_collection,
                    &control_collection,
                    &owner.as_bytes().as_slice(),
                    &other.as_bytes().as_slice(),
                    &global.as_bytes().as_slice(),
                ],
            )
            .unwrap_or_else(|_| panic!("Nakama client list fixture: scoped cleanup failed"));
    };
    cleanup(&mut control);
    let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let engine = control
            .query_one("SELECT version()", &[])
            .unwrap_or_else(|_| panic!("Nakama client list fixture: engine query failed"))
            .get::<_, String>(0);
        match profile {
            DatabaseProfile::PostgreSql => assert!(engine.starts_with("PostgreSQL ")),
            DatabaseProfile::CockroachDb => assert!(engine.contains("CockroachDB")),
        }
        let mut repository = PgRepository::connect(&database_url, profile)
            .unwrap_or_else(|_| panic!("Nakama client list fixture: repository connection failed"));
        let key = |name, user| StorageObjectKey::new(collection, name, user).unwrap();
        let own_private = key("z-own-private", owner);
        let own_public = key("a-public", owner);
        let own_hidden = key("00-own-hidden", owner);
        let other_public = key("a-public", other);
        let other_last = key("y-other-public", other);
        let other_private = key("00-other-private", other);
        let global_public = key("g-global", global);
        let global_private = key("00-global-private", global);
        let entries = [
            (&own_private, ReadPermission::Owner),
            (&own_public, ReadPermission::Public),
            (&own_hidden, ReadPermission::None),
            (&other_public, ReadPermission::Public),
            (&other_last, ReadPermission::Public),
            (&other_private, ReadPermission::Owner),
            (&global_public, ReadPermission::Public),
            (&global_private, ReadPermission::Owner),
        ];
        let value = br#"{"list":true}"#;
        let writes = entries
            .iter()
            .map(|(key, read_permission)| {
                StorageBatchOperation::Write(StorageWriteOperation {
                    key: (*key).clone(),
                    value: value.to_vec(),
                    expected: VersionCheck::MustNotExist,
                    read_permission: *read_permission,
                    write_permission: WritePermission::Owner,
                })
            })
            .collect::<Vec<_>>();
        repository
            .apply_storage_batch(StorageActor::Server, &writes, 120)
            .unwrap();
        let page_keys = |page: &trnm_persistence_pg::StorageClientListPage| {
            page.objects
                .iter()
                .map(|object| object.key.clone())
                .collect::<Vec<_>>()
        };
        let position = |key: &StorageObjectKey, read| trnm_persistence_pg::StorageListPosition {
            key: key.key().to_owned(),
            user_id: key.user_id(),
            read,
        };
        let public_first = repository
            .list_storage_objects_nakama(StorageActor::User(owner), collection, None, None, 1)
            .unwrap();
        assert_eq!(page_keys(&public_first), vec![own_public.clone()]);
        assert_eq!(public_first.next, Some(position(&own_public, 2)));
        let mut public_offset = public_first.next.unwrap();
        public_offset.read = i32::MAX;
        let public_second = repository
            .list_storage_objects_nakama(
                StorageActor::User(owner),
                collection,
                None,
                Some(&public_offset),
                1,
            )
            .unwrap();
        assert_eq!(page_keys(&public_second), vec![other_public.clone()]);
        assert_eq!(public_second.next, Some(position(&other_public, 2)));
        let public_terminal = repository
            .list_storage_objects_nakama(
                StorageActor::User(owner),
                collection,
                None,
                public_second.next.as_ref(),
                2,
            )
            .unwrap();
        assert_eq!(
            page_keys(&public_terminal),
            vec![global_public.clone(), other_last.clone()]
        );
        assert_eq!(public_terminal.next, None);

        let own_first = repository
            .list_storage_objects_nakama(
                StorageActor::User(owner),
                collection,
                Some(owner),
                None,
                1,
            )
            .unwrap();
        assert_eq!(page_keys(&own_first), vec![own_private.clone()]);
        assert_eq!(own_first.next, Some(position(&own_private, 1)));
        let mut own_offset = own_first.next.unwrap();
        own_offset.user_id = other;
        let own_terminal = repository
            .list_storage_objects_nakama(
                StorageActor::User(owner),
                collection,
                Some(owner),
                Some(&own_offset),
                1,
            )
            .unwrap();
        assert_eq!(page_keys(&own_terminal), vec![own_public.clone()]);
        assert_eq!(own_terminal.next, None);
        let empty_offset = trnm_persistence_pg::StorageListPosition {
            key: String::new(),
            user_id: other,
            read: i32::MIN,
        };
        let own_all = repository
            .list_storage_objects_nakama(
                StorageActor::User(owner),
                collection,
                Some(owner),
                Some(&empty_offset),
                100,
            )
            .unwrap();
        assert_eq!(
            page_keys(&own_all),
            vec![own_private.clone(), own_public.clone()]
        );
        let past_read_offset = trnm_persistence_pg::StorageListPosition {
            read: i32::MAX,
            ..empty_offset.clone()
        };
        let empty_page = repository
            .list_storage_objects_nakama(
                StorageActor::User(owner),
                collection,
                Some(owner),
                Some(&past_read_offset),
                100,
            )
            .unwrap();
        assert!(empty_page.objects.is_empty());
        assert_eq!(empty_page.next, None);

        let foreign_first = repository
            .list_storage_objects_nakama(
                StorageActor::User(owner),
                collection,
                Some(other),
                None,
                1,
            )
            .unwrap();
        assert_eq!(page_keys(&foreign_first), vec![other_public.clone()]);
        assert_eq!(foreign_first.next, Some(position(&other_public, 2)));
        let foreign_offset = trnm_persistence_pg::StorageListPosition {
            key: other_public.key().to_owned(),
            user_id: global,
            read: i32::MIN,
        };
        let foreign_terminal = repository
            .list_storage_objects_nakama(
                StorageActor::User(owner),
                collection,
                Some(other),
                Some(&foreign_offset),
                1,
            )
            .unwrap();
        assert_eq!(page_keys(&foreign_terminal), vec![other_last.clone()]);
        assert_eq!(foreign_terminal.next, None);
        let global_page = repository
            .list_storage_objects_nakama(
                StorageActor::User(owner),
                collection,
                Some(global),
                Some(&foreign_offset),
                1,
            )
            .unwrap();
        assert_eq!(page_keys(&global_page), vec![global_public.clone()]);
        assert_eq!(global_page.next, None);

        // Corrupt inaccessible objects through the independent control client.
        // They sort before visible rows but must neither consume capacity nor fail decoding.
        let corrupt = |control: &mut postgres::Client, key: &StorageObjectKey| {
            assert_eq!(
                control
                    .execute(
                        "UPDATE trnm_storage_objects SET version_digest = $4 \
                     WHERE collection = $1 AND object_key = $2 AND user_id = $3",
                        &[
                            &key.collection(),
                            &key.key(),
                            &key.user_id().as_bytes().as_slice(),
                            &vec![0x44_u8; 32],
                        ],
                    )
                    .unwrap(),
                1,
            );
        };
        for key in [&own_hidden, &other_private, &global_private] {
            corrupt(&mut control, key);
        }
        let visible = repository
            .list_storage_objects_nakama(StorageActor::User(owner), collection, None, None, 100)
            .unwrap();
        assert_eq!(
            page_keys(&visible),
            vec![
                own_public.clone(),
                other_public.clone(),
                global_public.clone(),
                other_last.clone()
            ]
        );
        let own_visible = repository
            .list_storage_objects_nakama(
                StorageActor::User(owner),
                collection,
                Some(owner),
                None,
                100,
            )
            .unwrap();
        assert_eq!(
            page_keys(&own_visible),
            vec![own_private.clone(), own_public.clone()]
        );
        let global_visible = repository
            .list_storage_objects_nakama(
                StorageActor::User(owner),
                collection,
                Some(global),
                None,
                100,
            )
            .unwrap();
        assert_eq!(page_keys(&global_visible), vec![global_public.clone()]);

        // A damaged public row beyond the returned page may serve as a sentinel.
        // Its bytes are decoded only once that row becomes part of the response.
        corrupt(&mut control, &other_last);
        let before_bad_sentinel = repository
            .list_storage_objects_nakama(StorageActor::User(owner), collection, None, None, 3)
            .unwrap();
        assert_eq!(
            page_keys(&before_bad_sentinel),
            vec![
                own_public.clone(),
                other_public.clone(),
                global_public.clone()
            ]
        );
        assert_eq!(before_bad_sentinel.next, Some(position(&global_public, 2)));
        let visible_error = repository
            .list_storage_objects_nakama(
                StorageActor::User(owner),
                collection,
                None,
                before_bad_sentinel.next.as_ref(),
                1,
            )
            .unwrap_err();
        assert_eq!(visible_error.code(), StableCode::DataLoss);
        assert_eq!(visible_error.reason(), "storage_integrity_digest_mismatch");
        let beyond_sentinel = repository
            .list_storage_objects_nakama(StorageActor::User(owner), collection, None, None, 1)
            .unwrap();
        assert_eq!(page_keys(&beyond_sentinel), vec![own_public.clone()]);
        let foreign_bad_sentinel = repository
            .list_storage_objects_nakama(
                StorageActor::User(owner),
                collection,
                Some(other),
                None,
                1,
            )
            .unwrap();
        assert_eq!(page_keys(&foreign_bad_sentinel), vec![other_public.clone()]);
        assert_eq!(foreign_bad_sentinel.next, Some(position(&other_public, 2)));
        assert_eq!(
            repository
                .list_storage_objects_nakama(
                    StorageActor::User(owner),
                    collection,
                    Some(other),
                    foreign_bad_sentinel.next.as_ref(),
                    1,
                )
                .unwrap_err()
                .code(),
            StableCode::DataLoss,
        );
        corrupt(&mut control, &own_public);
        let own_bad_sentinel = repository
            .list_storage_objects_nakama(
                StorageActor::User(owner),
                collection,
                Some(owner),
                None,
                1,
            )
            .unwrap();
        assert_eq!(page_keys(&own_bad_sentinel), vec![own_private.clone()]);
        assert_eq!(own_bad_sentinel.next, Some(position(&own_private, 1)));
        assert_eq!(
            repository
                .list_storage_objects_nakama(
                    StorageActor::User(owner),
                    collection,
                    Some(owner),
                    own_bad_sentinel.next.as_ref(),
                    1,
                )
                .unwrap_err()
                .code(),
            StableCode::DataLoss,
        );

        // Derive the text ordering from the actual database, retaining its locale.
        let text_keys = ["é", "A", "中", "a", "e\u{301}", "~", "_", "😀"];
        let text_writes = text_keys
            .into_iter()
            .map(|name| {
                StorageBatchOperation::Write(StorageWriteOperation {
                    key: StorageObjectKey::new(text_collection, name, other).unwrap(),
                    value: value.to_vec(),
                    expected: VersionCheck::MustNotExist,
                    read_permission: ReadPermission::Public,
                    write_permission: WritePermission::Owner,
                })
            })
            .collect::<Vec<_>>();
        repository
            .apply_storage_batch(StorageActor::Server, &text_writes, 130)
            .unwrap();
        let expected_text = control
            .query(
                "SELECT object_key FROM trnm_storage_objects \
             WHERE collection = $1 AND user_id = $2 ORDER BY object_key ASC",
                &[&text_collection, &other.as_bytes().as_slice()],
            )
            .unwrap()
            .into_iter()
            .map(|row| row.get::<_, String>(0))
            .collect::<Vec<_>>();
        let text_page = repository
            .list_storage_objects_nakama(
                StorageActor::User(owner),
                text_collection,
                Some(other),
                None,
                100,
            )
            .unwrap();
        assert_eq!(
            text_page
                .objects
                .iter()
                .map(|object| object.key.key().to_owned())
                .collect::<Vec<_>>(),
            expected_text,
        );
        assert_eq!(text_page.next, None);
        assert!(text_page.objects.iter().all(|object| {
            object.version == trnm_persistence_pg::ContentVersion::from_value(value)
                && object.integrity_digest.matches_value(value)
                && object.value.as_slice() == value.as_slice()
                && object.key.user_id() == other
        }));

        // Authoritative schema length is in Unicode characters. Seed valid
        // rows that the existing typed key's byte/character policy cannot express.
        let wide_key = "界".repeat(128);
        assert_eq!(wide_collection.chars().count(), 128);
        assert_eq!(wide_key.chars().count(), 128);
        assert!(StorageObjectKey::new(&wide_collection, &wide_key, other).is_err());
        assert!(StorageObjectKey::new(control_collection, ".control-\n", other).is_err());
        let integrity = trnm_persistence_pg::IntegrityDigest::from_value(value);
        for (seed_collection, seed_key) in [
            (wide_collection.as_str(), wide_key.as_str()),
            (control_collection, ".control-\n"),
        ] {
            assert_eq!(
                control
                    .execute(
                        "INSERT INTO trnm_storage_objects \
                 (collection, object_key, user_id, value_bytes, version_digest, \
                  read_permission, write_permission, updated_at_ms) \
                 VALUES ($1, $2, $3, $4, $5, 2, 1, 140)",
                        &[
                            &seed_collection,
                            &seed_key,
                            &other.as_bytes().as_slice(),
                            &value.as_slice(),
                            &integrity.get().as_bytes().as_slice(),
                        ],
                    )
                    .unwrap(),
                1
            );
            let seeded_page = repository
                .list_storage_objects_nakama(
                    StorageActor::User(owner),
                    seed_collection,
                    Some(other),
                    None,
                    1,
                )
                .unwrap();
            assert_eq!(seeded_page.objects.len(), 1);
            assert_eq!(seeded_page.objects[0].key.collection(), seed_collection);
            assert_eq!(seeded_page.objects[0].key.key(), seed_key);
            assert_eq!(seeded_page.objects[0].key.user_id(), other);
            assert_eq!(seeded_page.objects[0].value.as_slice(), value.as_slice());
            assert_eq!(seeded_page.objects[0].integrity_digest, integrity);
            assert_eq!(seeded_page.next, None);
        }

        for invalid_actor in [StorageActor::Server, StorageActor::User(global)] {
            assert_eq!(
                repository
                    .list_storage_objects_nakama(invalid_actor, collection, None, None, 1,)
                    .unwrap_err()
                    .reason(),
                "invalid_storage_actor"
            );
        }
        for invalid_limit in [0, 101] {
            assert_eq!(
                repository
                    .list_storage_objects_nakama(
                        StorageActor::User(owner),
                        collection,
                        None,
                        None,
                        invalid_limit,
                    )
                    .unwrap_err()
                    .reason(),
                "invalid_storage_list_limit"
            );
        }
        let long_offset = trnm_persistence_pg::StorageListPosition {
            key: "x".repeat(4097),
            user_id: global,
            read: i32::MIN,
        };
        assert_eq!(
            repository
                .list_storage_objects_nakama(
                    StorageActor::User(owner),
                    collection,
                    None,
                    Some(&long_offset),
                    1,
                )
                .unwrap_err()
                .reason(),
            "invalid_storage_list_position"
        );
        assert_eq!(
            repository
                .list_storage_objects_nakama(
                    StorageActor::User(owner),
                    &"x".repeat(4097),
                    None,
                    None,
                    1,
                )
                .unwrap_err()
                .reason(),
            "invalid_storage_collection"
        );
        for empty_collection in [
            String::new(),
            "x".repeat(129),
            ".reserved".to_owned(),
            "bad\nname".to_owned(),
            "x".repeat(4096),
        ] {
            let empty_result = repository
                .list_storage_objects_nakama(
                    StorageActor::User(owner),
                    &empty_collection,
                    None,
                    None,
                    1,
                )
                .unwrap();
            assert!(empty_result.objects.is_empty());
            assert_eq!(empty_result.next, None);
        }
    }));
    cleanup(&mut control);
    let remaining = control
        .query_one(
            "SELECT count(*) FROM trnm_storage_objects \
         WHERE collection IN ($1, $2, $3, $4) AND user_id IN ($5, $6, $7)",
            &[
                &collection,
                &text_collection,
                &wide_collection,
                &control_collection,
                &owner.as_bytes().as_slice(),
                &other.as_bytes().as_slice(),
                &global.as_bytes().as_slice(),
            ],
        )
        .unwrap()
        .get::<_, i64>(0);
    assert_eq!(remaining, 0);
    if let Err(payload) = outcome {
        std::panic::resume_unwind(payload);
    }
    eprintln!(
        "\nnakama_client_list_projection_executed profile={}",
        profile.metadata_value()
    );
}
