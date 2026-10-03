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
    apply_write_with_binding(
        transaction,
        staged,
        actor,
        operation,
        updated_at_ms,
        profile,
        StorageWriteBinding::Typed,
    )
}

fn apply_nakama_write(
    transaction: &mut Transaction<'_>,
    staged: &mut BTreeMap<StorageObjectKey, Option<StoredStorageObject>>,
    actor: Actor,
    operation: &WriteOperation,
    updated_at_ms: i64,
    profile: DatabaseProfile,
) -> Result<StoredStorageMutationReceipt, DomainError> {
    apply_write_with_binding(
        transaction,
        staged,
        actor,
        operation,
        updated_at_ms,
        profile,
        StorageWriteBinding::Nakama,
    )
}

#[derive(Clone, Copy, Eq, PartialEq)]
enum StorageWriteBinding {
    Typed,
    Nakama,
}

fn apply_write_with_binding(
    transaction: &mut Transaction<'_>,
    staged: &mut BTreeMap<StorageObjectKey, Option<StoredStorageObject>>,
    actor: Actor,
    operation: &WriteOperation,
    updated_at_ms: i64,
    profile: DatabaseProfile,
    binding: StorageWriteBinding,
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
    let native_payload = RawStorageJsonb::new(&operation.value)?;
    let request_text = if binding == StorageWriteBinding::Typed {
        std::str::from_utf8(&operation.value).map_err(|_| invalid("invalid_storage_value_utf8"))?
    } else {
        // The Nakama acquisition already bound these original bytes as JSONB.
        // The DML repeats that same native input instead of binding a TEXT cast.
        ""
    };
    let request_parameter: &(dyn ToSql + Sync) = if binding == StorageWriteBinding::Nakama {
        &native_payload
    } else {
        &request_text
    };
    let value_parameter = if binding == StorageWriteBinding::Nakama {
        "$4::JSONB"
    } else {
        "$4::TEXT::JSONB"
    };
    let condition = if binding == StorageWriteBinding::Nakama {
        match &operation.expected {
            VersionCheck::Exact(token) => Some(RawStorageCondition(token.as_str())),
            _ => None,
        }
    } else {
        None
    };
    let authoritative = actor == Actor::Server;
    let exact_predicate = if condition.is_some() {
        " AND public_version::TEXT=$12::TEXT AND ($13::BOOL OR write_permission=1)"
    } else {
        ""
    };
    let read_permission = operation.read_permission.get();
    let write_permission = operation.write_permission.get();
    let columns = storage_row_columns(profile, "TRUE");
    let query = if previous.is_some() {
        format!(
            "UPDATE public.trnm_storage_objects \
             SET value_jsonb = {value_parameter}, value_bytes = $5, version_digest = $6, \
                 public_version = $7, value_projection_digest = $8, \
                 value_origin = 'write-request-bytes', source_manifest_digest = NULL, \
                 read_permission = $9, write_permission = $10, updated_at_ms = $11, \
                 update_time = pg_catalog.now() \
             WHERE collection = $1 AND object_key = $2 AND user_id = $3{exact_predicate} \
             RETURNING {columns}"
        )
    } else {
        format!(
            "INSERT INTO public.trnm_storage_objects \
             (collection, object_key, user_id, value_jsonb, value_bytes, version_digest, \
              public_version, value_projection_digest, value_origin, source_manifest_digest, \
              read_permission, write_permission, updated_at_ms, create_time, update_time) \
             VALUES ($1, $2, $3, {value_parameter}, $5, $6, $7, $8, 'write-request-bytes', NULL, \
                     $9, $10, $11, pg_catalog.now(), pg_catalog.now()) RETURNING {columns}"
        )
    };
    let collection = operation.key.collection();
    let object_key = operation.key.key();
    let owner = operation.key.user_id();
    let owner_bytes = owner.as_bytes().as_slice();
    let request_bytes = operation.value.as_slice();
    let request_fingerprint = request_digest.as_bytes().as_slice();
    let generated_version = version.as_str();
    let projection_fingerprint = projection_digest.as_bytes().as_slice();
    let mut parameters: Vec<&(dyn ToSql + Sync)> = vec![
        &collection,
        &object_key,
        &owner_bytes,
        request_parameter,
        &request_bytes,
        &request_fingerprint,
        &generated_version,
        &projection_fingerprint,
        &read_permission,
        &write_permission,
        &updated_at_ms,
    ];
    if let Some(condition) = condition.as_ref() {
        parameters.push(condition);
        parameters.push(&authoritative);
    }
    let returned = transaction
        .query_opt(&query, &parameters)
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
