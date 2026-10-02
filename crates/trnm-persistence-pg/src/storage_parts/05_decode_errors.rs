fn decode_storage_object(key: StorageObjectKey, row: &Row) -> Result<StorageObject, DomainError> {
    decode_storage_object_at(key, row, 0)
}

fn decode_stored_storage_object(
    key: StorageObjectKey,
    row: &Row,
) -> Result<StoredStorageObject, DomainError> {
    Ok(StoredStorageObject {
        object: decode_storage_object(key, row)?,
        times: decode_storage_times(row, 12)?,
    })
}

fn decode_storage_times(row: &Row, offset: usize) -> Result<StorageTimes, DomainError> {
    let times = StorageTimes {
        create: row
            .try_get(offset)
            .map_err(|_| data_loss("invalid_storage_timestamp"))?,
        update: row
            .try_get(offset + 1)
            .map_err(|_| data_loss("invalid_storage_timestamp"))?,
    };
    times.validate()?;
    Ok(times)
}

fn decode_listed_storage_object(collection: &str, row: &Row) -> Result<StorageObject, DomainError> {
    let object_key: String = row
        .try_get(0)
        .map_err(|_| data_loss("invalid_storage_key_material"))?;
    let user_bytes: Vec<u8> = row
        .try_get(1)
        .map_err(|_| data_loss("invalid_storage_user_id"))?;
    let key = decode_storage_key(collection.to_owned(), object_key, user_bytes)?;
    decode_storage_object_at(key, row, 2)
}

fn decode_nakama_listed_storage_object(
    collection: &str,
    row: &Row,
) -> Result<StorageObject, DomainError> {
    let object_key: String = row
        .try_get(0)
        .map_err(|_| data_loss("invalid_storage_key_material"))?;
    let user_bytes = row
        .try_get(1)
        .map_err(|_| data_loss("invalid_storage_user_id"))?;
    let user = decode_id16(user_bytes, UserId::new, "invalid_storage_user_id")?;
    let key = StorageObjectKey::new_nakama(collection.to_owned(), object_key, user)
        .map_err(|_| data_loss("invalid_storage_key_material"))?;
    decode_storage_object_at(key, row, 2)
}

fn decode_nakama_listed_storage_object_with_metadata(
    collection: &str,
    row: &Row,
) -> Result<StoredStorageObject, DomainError> {
    Ok(StoredStorageObject {
        object: decode_nakama_listed_storage_object(collection, row)?,
        times: decode_storage_times(row, 14)?,
    })
}

fn decode_storage_object_at(
    key: StorageObjectKey,
    row: &Row,
    offset: usize,
) -> Result<StorageObject, DomainError> {
    let value = decode_native_value(row, offset)?;
    let version = PublicVersion::new(
        row.try_get::<_, String>(offset + 2)
            .map_err(|_| data_loss("invalid_storage_public_version"))?,
    )
    .map_err(|_| data_loss("invalid_storage_public_version"))?;
    let integrity_digest = IntegrityDigest::new(decode_digest(
        row.try_get(offset + 3)
            .map_err(|_| data_loss("invalid_storage_integrity_digest"))?,
        "invalid_storage_integrity_digest",
    )?)
    .map_err(|_| data_loss("invalid_storage_integrity_digest"))?;
    verify_storage_integrity(&value, integrity_digest)?;
    let read_permission = match row
        .try_get::<_, i16>(offset + 4)
        .map_err(|_| data_loss("invalid_storage_read_permission"))?
    {
        0 => ReadPermission::None,
        1 => ReadPermission::Owner,
        2 => ReadPermission::Public,
        _ => return Err(data_loss("invalid_storage_read_permission")),
    };
    let write_permission = match row
        .try_get::<_, i16>(offset + 5)
        .map_err(|_| data_loss("invalid_storage_write_permission"))?
    {
        0 => WritePermission::None,
        1 => WritePermission::Owner,
        _ => return Err(data_loss("invalid_storage_write_permission")),
    };
    let origin: String = row
        .try_get(offset + 6)
        .map_err(|_| data_loss("invalid_storage_value_origin"))?;
    let raw: Option<Vec<u8>> = row
        .try_get(offset + 7)
        .map_err(|_| data_loss("invalid_storage_request_witness"))?;
    let raw_length: Option<i64> = row
        .try_get(offset + 8)
        .map_err(|_| data_loss("invalid_storage_request_witness"))?;
    let raw_digest: Option<Vec<u8>> = row
        .try_get(offset + 9)
        .map_err(|_| data_loss("invalid_storage_request_witness"))?;
    let manifest: Option<Vec<u8>> = row
        .try_get(offset + 10)
        .map_err(|_| data_loss("invalid_storage_source_manifest"))?;
    let raw_projection: Option<String> = row
        .try_get(offset + 11)
        .map_err(|_| data_loss("invalid_storage_request_witness"))?;
    let collision_witness = match origin.as_str() {
        "legacy-rust-v2-bytes" | "write-request-bytes" => {
            let length = raw_length
                .and_then(|length| usize::try_from(length).ok())
                .ok_or_else(|| data_loss("invalid_storage_request_witness"))?;
            if length > MAX_VALUE_BYTES {
                return Err(storage_resource_exhausted(
                    "storage_request_witness_budget_exceeded",
                ));
            }
            let raw = raw.ok_or_else(|| data_loss("invalid_storage_request_witness"))?;
            if length != raw.len() || manifest.is_some() {
                return Err(data_loss("invalid_storage_request_witness"));
            }
            let digest = IntegrityDigest::new(decode_digest(
                raw_digest.ok_or_else(|| data_loss("invalid_storage_request_witness"))?,
                "invalid_storage_request_witness",
            )?)
            .map_err(|_| data_loss("invalid_storage_request_witness"))?;
            if !digest.matches_value(&raw)
                || raw_projection.as_ref().map(String::as_bytes) != Some(value.as_slice())
            {
                return Err(data_loss("storage_request_witness_mismatch"));
            }
            let witness = CollisionWitness::from_request(&raw, &value)?;
            witness.validate_projection(&version, &value)?;
            Some(witness)
        }
        "nakama-export-unknown-request" => {
            if raw.is_some()
                || raw_length.is_some()
                || raw_digest.is_some()
                || raw_projection.is_some()
            {
                return Err(data_loss("invalid_storage_unknown_request_provenance"));
            }
            IntegrityDigest::new(decode_digest(
                manifest.ok_or_else(|| data_loss("invalid_storage_source_manifest"))?,
                "invalid_storage_source_manifest",
            )?)
            .map_err(|_| data_loss("invalid_storage_source_manifest"))?;
            None
        }
        _ => return Err(data_loss("invalid_storage_value_origin")),
    };
    let object = StorageObject {
        key,
        version,
        value,
        integrity_digest,
        collision_witness,
        read_permission,
        write_permission,
    };
    object.verify_integrity()?;
    Ok(object)
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
