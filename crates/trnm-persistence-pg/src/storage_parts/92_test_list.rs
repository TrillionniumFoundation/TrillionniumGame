    #[test]
    fn list_validation_rejects_invalid_limits_actor_and_cursor_scope() {
        assert_eq!(
            validate_list_request(Actor::Server, "profile", None, None, 0)
                .unwrap_err()
                .reason(),
            "invalid_storage_list_limit"
        );
        assert_eq!(
            validate_list_request(Actor::Server, "profile", None, None, MAX_LIST_LIMIT + 1,)
                .unwrap_err()
                .reason(),
            "invalid_storage_list_limit"
        );
        assert_eq!(
            validate_list_request(Actor::User(UserId::new([0; 16])), "profile", None, None, 1,)
                .unwrap_err()
                .reason(),
            "invalid_storage_actor"
        );
        assert_eq!(
            validate_list_request(
                Actor::Server,
                "profile",
                Some(UserId::new([0; 16])),
                None,
                1,
            )
            .unwrap_err()
            .reason(),
            "invalid_storage_owner"
        );

        let other_collection = (
            Actor::Server,
            None,
            StorageObjectKey::new("other", "object", UserId::new([1; 16])).unwrap(),
        );
        assert_eq!(
            validate_list_request(Actor::Server, "profile", None, Some(&other_collection), 1,)
                .unwrap_err()
                .reason(),
            "storage_cursor_scope_mismatch"
        );

        let unscoped = (Actor::Server, None, key(1));
        assert_eq!(
            validate_list_request(
                Actor::Server,
                "profile",
                Some(UserId::new([2; 16])),
                Some(&unscoped),
                1,
            )
            .unwrap_err()
            .reason(),
            "storage_cursor_scope_mismatch"
        );
        assert_eq!(
            validate_list_request(
                Actor::User(UserId::new([1; 16])),
                "profile",
                None,
                Some(&unscoped),
                1,
            )
            .unwrap_err()
            .reason(),
            "storage_cursor_scope_mismatch"
        );

        let owner_scoped = (
            Actor::User(UserId::new([1; 16])),
            Some(UserId::new([1; 16])),
            key(1),
        );
        assert_eq!(
            validate_list_request(
                Actor::User(UserId::new([1; 16])),
                "profile",
                None,
                Some(&owner_scoped),
                1,
            )
            .unwrap_err()
            .reason(),
            "storage_cursor_scope_mismatch"
        );
    }

    #[test]
    fn list_queries_remove_ambient_text_collation_from_keyset_order() {
        let postgresql = storage_list_query(DatabaseProfile::PostgreSql);
        assert!(postgresql.contains("convert_to(object_key, 'UTF8') > convert_to($3, 'UTF8')"));
        assert!(postgresql.contains("ORDER BY convert_to(object_key, 'UTF8') ASC, user_id ASC"));
        assert!(!postgresql.contains("ORDER BY object_key ASC"));

        let cockroach = storage_list_query(DatabaseProfile::CockroachDb);
        assert!(cockroach.contains("object_key::BYTES > $3::STRING::BYTES"));
        assert!(cockroach.contains("ORDER BY object_key::BYTES ASC, user_id ASC"));
        assert!(!cockroach.contains("ORDER BY object_key ASC"));
    }
