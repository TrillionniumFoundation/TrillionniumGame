fn sorted_keys(operations: &[BatchOperation]) -> BTreeSet<StorageObjectKey> {
    operations
        .iter()
        .map(|operation| operation.key().clone())
        .collect()
}

fn load_for_update(
    transaction: &mut Transaction<'_>,
    key: &StorageObjectKey,
) -> Result<Option<StoredStorageObject>, DomainError> {
    transaction
        .query_opt(
            "SELECT value_bytes, version_digest, read_permission, write_permission, create_time, update_time \
             FROM public.trnm_storage_objects \
             WHERE collection = $1 AND object_key = $2 AND user_id = $3 \
             FOR UPDATE",
            &[
                &key.collection(),
                &key.key(),
                &key.user_id().as_bytes().as_slice(),
            ],
        )
        .map_err(map_postgres_error)?
        .map(|row| decode_stored_storage_object(key.clone(), &row))
        .transpose()
}

fn apply_write(
    transaction: &mut Transaction<'_>,
    staged: &mut BTreeMap<StorageObjectKey, Option<StoredStorageObject>>,
    actor: Actor,
    operation: &WriteOperation,
    updated_at_ms: i64,
) -> Result<StoredStorageMutationReceipt, DomainError> {
    if operation.value.len() > MAX_VALUE_BYTES {
        return Err(invalid("invalid_storage_value"));
    }
    let previous = staged
        .get(&operation.key)
        .cloned()
        .ok_or_else(|| data_loss("storage_batch_lock_missing"))?;
    // Insert-only retains actor/owner binding, but an existing key rejects the
    // version independently of its write ACL, matching the upstream INSERT path.
    let acl_object = if operation.expected == VersionCheck::MustNotExist {
        None
    } else {
        previous.as_ref().map(|stored| &stored.object)
    };
    authorize_write(actor, &operation.key, acl_object)?;
    validate_version(
        previous.as_ref().map(|stored| &stored.object),
        operation.expected,
    )?;

    let version = ContentVersion::from_value(&operation.value);
    let integrity_digest = IntegrityDigest::from_value(&operation.value);
    if let Some(stored) = previous.as_ref() {
        let existing = &stored.object;
        if existing.version == version && existing.value != operation.value {
            return Err(data_loss(
                "storage_public_version_collision_or_integrity_mismatch",
            ));
        }
        // A blind write of identical content and ACLs preserves update time.
        // Exact-version writes still issue UPDATE, including identical content.
        // Authorization, OCC and integrity checks precede this no-op receipt.
        if operation.expected == VersionCheck::Any
            && existing.version == version
            && existing.read_permission == operation.read_permission
            && existing.write_permission == operation.write_permission
        {
            return Ok(StoredStorageMutationReceipt {
                receipt: MutationReceipt {
                    key: operation.key.clone(),
                    previous_version: Some(existing.version),
                    current_version: Some(existing.version),
                },
                times: stored.times,
            });
        }
    }
    let integrity = integrity_digest.get();
    let read_permission = operation.read_permission as i16;
    let write_permission = operation.write_permission as i16;
    let returned = if previous.is_some() {
        transaction
            .query_opt(
                "UPDATE public.trnm_storage_objects \
                 SET value_bytes = $4, version_digest = $5, read_permission = $6, \
                     write_permission = $7, updated_at_ms = $8, update_time = pg_catalog.now() \
                 WHERE collection = $1 AND object_key = $2 AND user_id = $3 \
                 RETURNING create_time, update_time",
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
            .query_opt(
                "INSERT INTO public.trnm_storage_objects \
                 (collection, object_key, user_id, value_bytes, version_digest, \
                  read_permission, write_permission, updated_at_ms, create_time, update_time) \
                 VALUES ($1, $2, $3, $4, $5, $6, $7, $8, pg_catalog.now(), pg_catalog.now()) \
                 RETURNING create_time, update_time",
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
    let returned = returned.ok_or_else(|| data_loss("storage_write_row_count_mismatch"))?;
    let times = decode_storage_times(&returned, 0)?;
    if times.update.is_none()
        || (previous.is_none() && (times.create.is_none() || times.create != times.update))
    {
        return Err(data_loss("storage_write_timestamp_mismatch"));
    }
    let next = StorageObject {
        key: operation.key.clone(),
        value: operation.value.clone(),
        version,
        integrity_digest,
        read_permission: operation.read_permission,
        write_permission: operation.write_permission,
    };
    staged.insert(
        operation.key.clone(),
        Some(StoredStorageObject {
            object: next,
            times,
        }),
    );
    Ok(StoredStorageMutationReceipt {
        receipt: MutationReceipt {
            key: operation.key.clone(),
            previous_version: previous.map(|stored| stored.object.version),
            current_version: Some(version),
        },
        times,
    })
}
