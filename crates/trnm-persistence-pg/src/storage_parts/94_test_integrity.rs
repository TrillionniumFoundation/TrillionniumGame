    #[test]
    fn permission_and_version_contract_matches_storage_core() {
        let owner = UserId::new([1; 16]);
        let object = StorageObject {
            key: key(1),
            value: b"v1".to_vec(),
            version: ContentVersion::from_value(b"v1"),
            integrity_digest: IntegrityDigest::from_value(b"v1"),
            read_permission: ReadPermission::Owner,
            write_permission: WritePermission::Owner,
        };
        authorize_read(Actor::User(owner), &object).unwrap();
        assert_eq!(
            authorize_read(Actor::User(UserId::new([2; 16])), &object)
                .unwrap_err()
                .reason(),
            "storage_read_permission_denied"
        );
        validate_version(Some(&object), &VersionCheck::Exact(object.version.into())).unwrap();
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
