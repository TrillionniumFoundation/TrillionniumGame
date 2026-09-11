fn sorted_keys(operations: &[BatchOperation]) -> BTreeSet<StorageObjectKey> {
    operations
        .iter()
        .map(|operation| operation.key().clone())
        .collect()
}

fn load_for_update(
    transaction: &mut Transaction<'_>,
    key: &StorageObjectKey,
) -> Result<Option<StorageObject>, DomainError> {
    transaction
        .query_opt(
            "SELECT value_bytes, version_digest, read_permission, write_permission \
             FROM trnm_storage_objects \
             WHERE collection = $1 AND object_key = $2 AND user_id = $3 \
             FOR UPDATE",
            &[
                &key.collection(),
                &key.key(),
                &key.user_id().as_bytes().as_slice(),
            ],
        )
        .map_err(map_postgres_error)?
        .map(|row| decode_storage_object(key.clone(), &row))
        .transpose()
}

fn apply_write(
    transaction: &mut Transaction<'_>,
    staged: &mut BTreeMap<StorageObjectKey, Option<StorageObject>>,
    actor: Actor,
    operation: &WriteOperation,
    updated_at_ms: i64,
) -> Result<MutationReceipt, DomainError> {
    if operation.value.len() > MAX_VALUE_BYTES {
        return Err(invalid("invalid_storage_value"));
    }
    let previous = staged
        .get(&operation.key)
        .cloned()
        .ok_or_else(|| data_loss("storage_batch_lock_missing"))?;
    authorize_write(actor, &operation.key, previous.as_ref())?;
    validate_version(previous.as_ref(), operation.expected)?;

    let version = ContentVersion::from_value(&operation.value);
    let integrity_digest = IntegrityDigest::from_value(&operation.value);
    if let Some(existing) = previous.as_ref() {
        if existing.version == version && existing.value != operation.value {
            return Err(data_loss(
                "storage_public_version_collision_or_integrity_mismatch",
            ));
        }
    }
    let integrity = integrity_digest.get();
    let read_permission = operation.read_permission as i16;
    let write_permission = operation.write_permission as i16;
    let affected = if previous.is_some() {
        transaction
            .execute(
                "UPDATE trnm_storage_objects \
                 SET value_bytes = $4, version_digest = $5, read_permission = $6, \
                     write_permission = $7, updated_at_ms = $8 \
                 WHERE collection = $1 AND object_key = $2 AND user_id = $3",
                &[
                    &operation.key.collection(),
                    &operation.key.key(),
                    &operation.key.user_id().as_bytes().as_slice(),
                    &operation.value.as_slice(),
                    &integrity.as_bytes().as_slice(),
                    &read_permission,
                    &write_permission,
                    &updated_at_ms,
                ],
            )
            .map_err(map_postgres_error)?
    } else {
        transaction
            .execute(
                "INSERT INTO trnm_storage_objects \
                 (collection, object_key, user_id, value_bytes, version_digest, \
                  read_permission, write_permission, updated_at_ms) \
                 VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                &[
                    &operation.key.collection(),
                    &operation.key.key(),
                    &operation.key.user_id().as_bytes().as_slice(),
                    &operation.value.as_slice(),
                    &integrity.as_bytes().as_slice(),
                    &read_permission,
                    &write_permission,
                    &updated_at_ms,
                ],
            )
            .map_err(map_postgres_error)?
    };
    if affected != 1 {
        return Err(data_loss("storage_write_row_count_mismatch"));
    }
    let next = StorageObject {
        key: operation.key.clone(),
        value: operation.value.clone(),
        version,
        integrity_digest,
        read_permission: operation.read_permission,
        write_permission: operation.write_permission,
    };
    staged.insert(operation.key.clone(), Some(next));
    Ok(MutationReceipt {
        key: operation.key.clone(),
        previous_version: previous.map(|object| object.version),
        current_version: Some(version),
    })
}
