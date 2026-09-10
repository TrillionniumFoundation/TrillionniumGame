    #[test]
    fn keyset_page_returns_scope_bound_cursor_only_with_sentinel() {
        let actor = Actor::User(UserId::new([1; 16]));
        let owner = Some(UserId::new([2; 16]));
        let (page, cursor) =
            finish_storage_page(vec![object(1), object(2), object(3)], actor, owner, 2);
        assert_eq!(page.len(), 2);
        let cursor = cursor.unwrap();
        assert_eq!(cursor.0, actor);
        assert_eq!(cursor.1, owner);
        assert_eq!(cursor.2, key(2));

        let (final_page, final_cursor) =
            finish_storage_page(vec![object(1), object(2)], actor, owner, 2);
        assert_eq!(final_page.len(), 2);
        assert_eq!(final_cursor, None);
    }
