    fn key(value: u8) -> StorageObjectKey {
        StorageObjectKey::new(
            "profile",
            format!("object-{value}"),
            UserId::new([value; 16]),
        )
        .unwrap()
    }

    fn write(value: u8) -> BatchOperation {
        BatchOperation::Write(WriteOperation {
            key: key(value),
            value: vec![value],
            expected: VersionCheck::Any,
            read_permission: ReadPermission::Owner,
            write_permission: WritePermission::Owner,
        })
    }

    fn object(value: u8) -> StorageObject {
        StorageObject {
            key: key(value),
            value: vec![value],
            version: ContentVersion::from_value(&[value]),
            integrity_digest: IntegrityDigest::from_value(&[value]),
            read_permission: ReadPermission::Owner,
            write_permission: WritePermission::Owner,
        }
    }
