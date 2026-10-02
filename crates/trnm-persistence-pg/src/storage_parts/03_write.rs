fn sorted_keys(operations: &[BatchOperation]) -> BTreeSet<StorageObjectKey> {
    operations
        .iter()
        .map(|operation| operation.key().clone())
        .collect()
}

fn load_for_update(
    transaction: &mut Transaction<'_>,
    key: &StorageObjectKey,
    profile: DatabaseProfile,
) -> Result<Option<StoredStorageObject>, DomainError> {
    let columns = storage_row_columns(profile, "TRUE");
    let query = format!(
        "SELECT {columns} FROM public.trnm_storage_objects \
        WHERE collection = $1 AND object_key = $2 AND user_id = $3 FOR UPDATE"
    );
    transaction
        .query_opt(
            &query,
            &[
                &key.collection(),
                &key.key(),
                &key.user_id().as_bytes().as_slice(),
            ],
        )
        .map_err(map_stored_storage_error)?
        .map(|row| decode_stored_storage_object(key.clone(), &row))
        .transpose()
}

fn apply_write(
    transaction: &mut Transaction<'_>,
    staged: &mut BTreeMap<StorageObjectKey, Option<StoredStorageObject>>,
    actor: Actor,
    operation: &WriteOperation,
    updated_at_ms: i64,
    profile: DatabaseProfile,
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
        &operation.expected,
    )?;

    let version = ContentVersion::from_value(&operation.value);
    if let Some(stored) = previous.as_ref() {
        let existing = &stored.object;
        if existing.version.as_str() == version.as_str()
            && existing
                .collision_witness
                .as_ref()
                .is_some_and(|witness| !witness.matches_request(&operation.value))
        {
            return Err(data_loss(
                "storage_public_version_collision_or_integrity_mismatch",
            ));
        }
        // Unknown request provenance is legitimate. A matching public token and
        // unchanged ACLs retain the existing native payload, time and provenance
        // even when the supplied request would render a different JSONB value.
        if operation.expected == VersionCheck::Any
            && existing.version.as_str() == version.as_str()
            && existing.read_permission == operation.read_permission
            && existing.write_permission == operation.write_permission
        {
            // Even an upstream conflict no-op binds the incoming JSONB value.
            // Validate native input without replacing historical payload/witness.
            validate_storage_request_native(transaction, &operation.value)?;
            return Ok(StoredStorageMutationReceipt {
                receipt: MutationReceipt {
                    key: operation.key.clone(),
                    previous_version: Some(existing.version.clone()),
                    current_version: Some(version),
                },
                times: stored.times,
            });
        }
    }
    let projected = project_storage_request(transaction, &operation.value)?;
    let projection_digest = IntegrityDigest::from_value(&projected).get();
    let request_digest = IntegrityDigest::from_value(&operation.value).get();
    let request_text =
        std::str::from_utf8(&operation.value).map_err(|_| invalid("invalid_storage_value_utf8"))?;
    let read_permission = operation.read_permission.get();
    let write_permission = operation.write_permission.get();
    let columns = storage_row_columns(profile, "TRUE");
    let query = if previous.is_some() {
        format!(
            "UPDATE public.trnm_storage_objects \
             SET value_jsonb = $4::TEXT::JSONB, value_bytes = $5, version_digest = $6, \
                 public_version = $7, value_projection_digest = $8, \
                 value_origin = 'write-request-bytes', source_manifest_digest = NULL, \
                 read_permission = $9, write_permission = $10, updated_at_ms = $11, \
                 update_time = pg_catalog.now() \
             WHERE collection = $1 AND object_key = $2 AND user_id = $3 \
             RETURNING {columns}"
        )
    } else {
        format!(
            "INSERT INTO public.trnm_storage_objects \
             (collection, object_key, user_id, value_jsonb, value_bytes, version_digest, \
              public_version, value_projection_digest, value_origin, source_manifest_digest, \
              read_permission, write_permission, updated_at_ms, create_time, update_time) \
             VALUES ($1, $2, $3, $4::TEXT::JSONB, $5, $6, $7, $8, 'write-request-bytes', NULL, \
                     $9, $10, $11, pg_catalog.now(), pg_catalog.now()) RETURNING {columns}"
        )
    };
    let returned = transaction
        .query_opt(
            &query,
            &[
                &operation.key.collection(),
                &operation.key.key(),
                &operation.key.user_id().as_bytes().as_slice(),
                &request_text,
                &operation.value.as_slice(),
                &request_digest.as_bytes().as_slice(),
                &version.as_str(),
                &projection_digest.as_bytes().as_slice(),
                &read_permission,
                &write_permission,
                &updated_at_ms,
            ],
        )
        .map_err(map_postgres_error)?
        .ok_or_else(|| data_loss("storage_write_row_count_mismatch"))?;
    let next = decode_stored_storage_object(operation.key.clone(), &returned)?;
    let times = next.times;
    if times.update.is_none()
        || (previous.is_none() && (times.create.is_none() || times.create != times.update))
        || previous
            .as_ref()
            .is_some_and(|old| old.times.create != times.create)
    {
        return Err(data_loss("storage_write_timestamp_mismatch"));
    }
    if next.object.version.as_str() != version.as_str()
        || next.object.value != projected
        || next.object.read_permission != operation.read_permission
        || next.object.write_permission != operation.write_permission
        || !next
            .object
            .collision_witness
            .as_ref()
            .is_some_and(|witness| witness.matches_request(&operation.value))
    {
        return Err(data_loss("storage_write_native_returning_mismatch"));
    }
    staged.insert(operation.key.clone(), Some(next));
    Ok(StoredStorageMutationReceipt {
        receipt: MutationReceipt {
            key: operation.key.clone(),
            previous_version: previous.map(|stored| stored.object.version),
            current_version: Some(version),
        },
        times,
    })
}
