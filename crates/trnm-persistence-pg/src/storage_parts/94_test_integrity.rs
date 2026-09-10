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
        validate_version(Some(&object), VersionCheck::Exact(object.version)).unwrap();
        assert_eq!(
            validate_version(
                Some(&object),
                VersionCheck::Exact(ContentVersion::from_value(b"other")),
            )
            .unwrap_err()
            .reason(),
            "storage_version_mismatch"
        );
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
