fn decode_storage_object(key: StorageObjectKey, row: &Row) -> Result<StorageObject, DomainError> {
    decode_storage_object_at(key, row, 0)
}

fn decode_listed_storage_object(collection: &str, row: &Row) -> Result<StorageObject, DomainError> {
    let object_key: String = row.get(0);
    let user_bytes: Vec<u8> = row.get(1);
    let key = decode_storage_key(collection.to_owned(), object_key, user_bytes)?;
    decode_storage_object_at(key, row, 2)
}

fn decode_storage_object_at(
    key: StorageObjectKey,
    row: &Row,
    offset: usize,
) -> Result<StorageObject, DomainError> {
    let value: Vec<u8> = row.get(offset);
    if value.len() > MAX_VALUE_BYTES {
        return Err(data_loss("invalid_storage_value_bytes"));
    }
    let integrity_digest = IntegrityDigest::new(decode_digest(
        row.get(offset + 1),
        "invalid_storage_integrity_digest",
    )?)
    .map_err(|_| data_loss("invalid_storage_integrity_digest"))?;
    verify_storage_integrity(&value, integrity_digest)?;
    let read_permission = match row.get::<_, i16>(offset + 2) {
        0 => ReadPermission::None,
        1 => ReadPermission::Owner,
        2 => ReadPermission::Public,
        _ => return Err(data_loss("invalid_storage_read_permission")),
    };
    let write_permission = match row.get::<_, i16>(offset + 3) {
        0 => WritePermission::None,
        1 => WritePermission::Owner,
        _ => return Err(data_loss("invalid_storage_write_permission")),
    };
    Ok(StorageObject {
        key,
        version: ContentVersion::from_value(&value),
        value,
        integrity_digest,
        read_permission,
        write_permission,
    })
}

fn verify_storage_integrity(value: &[u8], stored: IntegrityDigest) -> Result<(), DomainError> {
    if stored.matches_value(value) {
        Ok(())
    } else {
        Err(data_loss("storage_integrity_digest_mismatch"))
    }
}

fn decode_storage_key(
    collection: String,
    object_key: String,
    user_bytes: Vec<u8>,
) -> Result<StorageObjectKey, DomainError> {
    let user = decode_id16(user_bytes, UserId::new, "invalid_storage_user_id")?;
    StorageObjectKey::new(collection, object_key, user)
        .map_err(|_| data_loss("invalid_storage_key_material"))
}

const fn storage_not_found() -> DomainError {
    error(
        StableCode::NotFound,
        "storage_object_not_found",
        RetryClass::Never,
    )
}

const fn version_error() -> DomainError {
    error(
        StableCode::FailedPrecondition,
        "storage_version_mismatch",
        RetryClass::ResyncRequired,
    )
}

const fn write_permission_error() -> DomainError {
    error(
        StableCode::PermissionDenied,
        "storage_write_permission_denied",
        RetryClass::Never,
    )
}
