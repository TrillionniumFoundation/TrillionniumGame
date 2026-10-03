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
    let projected = if binding == StorageWriteBinding::Nakama {
        // Native JSONB text input also applies to insert-only, which deliberately
        // skipped the previous-row acquisition query. Typed projection retains
        // its separate TEXT input policy.
        project_nakama_storage_request(transaction, &operation.value)?
    } else {
        project_storage_request(transaction, &operation.value)?
    };
    let projection_digest = IntegrityDigest::from_value(&projected).get();
    let request_digest = IntegrityDigest::from_value(&operation.value).get();
    let native_payload = RawStorageJsonb::new(&operation.value)?;
    let request_text = if binding == StorageWriteBinding::Typed {
        std::str::from_utf8(&operation.value).map_err(|_| invalid("invalid_storage_value_utf8"))?
    } else {
        // Nakama projection already bound these original bytes as JSONB,
        // including insert-only without a previous-row acquisition query.
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
        .map_err(|source| {
            // Only this actual ordinary INSERT's native unique violation is an
            // insert-only version rejection. Projection, typed, UPDATE and every
            // other SQLSTATE retain their existing classification.
            let rejection = nakama_insert_only_unique_rejection(
                binding,
                &operation.expected,
                previous.is_some(),
                source.code().map(|code| code.code()),
            );
            rejection.unwrap_or_else(|| map_postgres_error(source))
        })?
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

fn nakama_insert_only_unique_rejection(
    binding: StorageWriteBinding,
    expected: &VersionCheck,
    previous_exists: bool,
    sqlstate: Option<&str>,
) -> Option<DomainError> {
    if binding == StorageWriteBinding::Nakama
        && *expected == VersionCheck::MustNotExist
        && !previous_exists
        && sqlstate == Some("23505")
    {
        Some(error(
            StableCode::AlreadyExists,
            "storage_object_already_exists",
            RetryClass::Never,
        ))
    } else {
        None
    }
}

// Private actual native row custody. The shared decoder's fourteen computed
// fields are followed by the stored legacy clock and three actual key fields.
// These observations must not be reconstructed from the public object DTO.
#[derive(Eq, PartialEq)]
struct AnyNativeRow {
    stored: StoredStorageObject,
    origin: String,
    raw_value: Option<Vec<u8>>,
    raw_digest: Option<Vec<u8>>,
    manifest: Option<Vec<u8>>,
    updated_at_ms: i64,
}

enum AnyWriteStep {
    Applied(StoredStorageMutationReceipt),
    Semantic(DomainError),
}

fn decode_any_native_row(key: &StorageObjectKey, row: &Row) -> Result<AnyNativeRow, DomainError> {
    let collection: String = row
        .try_get(15)
        .map_err(|_| data_loss("storage_any_native_key_mismatch"))?;
    let object_key: String = row
        .try_get(16)
        .map_err(|_| data_loss("storage_any_native_key_mismatch"))?;
    let owner: Vec<u8> = row
        .try_get(17)
        .map_err(|_| data_loss("storage_any_native_key_mismatch"))?;
    if collection != key.collection()
        || object_key != key.key()
        || owner.as_slice() != key.user_id().as_bytes().as_slice()
    {
        return Err(data_loss("storage_any_native_key_mismatch"));
    }
    let stored = decode_stored_storage_object(key.clone(), row)?;
    let origin = row
        .try_get(6)
        .map_err(|_| data_loss("invalid_storage_value_origin"))?;
    let raw_value = row
        .try_get(7)
        .map_err(|_| data_loss("invalid_storage_request_witness"))?;
    let raw_digest = row
        .try_get(9)
        .map_err(|_| data_loss("invalid_storage_request_witness"))?;
    let manifest = row
        .try_get(10)
        .map_err(|_| data_loss("invalid_storage_source_manifest"))?;
    let updated_at_ms: i64 = row
        .try_get(14)
        .map_err(|_| data_loss("storage_any_native_clock_mismatch"))?;
    if updated_at_ms < 0 {
        return Err(data_loss("storage_any_native_clock_mismatch"));
    }
    Ok(AnyNativeRow {
        stored,
        origin,
        raw_value,
        raw_digest,
        manifest,
        updated_at_ms,
    })
}

fn read_nakama_any_prior(
    transaction: &mut Transaction<'_>,
    key: &StorageObjectKey,
    profile: DatabaseProfile,
) -> Result<AnyNativeRow, DomainError> {
    // PostgreSQL retains the real reservation conflict lock. CockroachDB does
    // not: its actual prior SELECT explicitly locks the row after reservation.
    // Neither branch borrows an earlier staged read or guesses missing prior.
    let query = storage_any_prior_query(profile);
    let row = transaction
        .query_opt(
            &query,
            &[
                &key.collection(),
                &key.key(),
                &key.user_id().as_bytes().as_slice(),
            ],
        )
        .map_err(map_stored_storage_error)?
        .ok_or_else(|| data_loss("storage_any_conflict_prior_missing"))?;
    decode_any_native_row(key, &row)
}

fn any_conflict_noop(
    prior: &AnyNativeRow,
    operation: &WriteOperation,
    version: ContentVersion,
) -> Result<bool, DomainError> {
    prior.stored.object.verify_integrity()?;
    let existing = &prior.stored.object;
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
    Ok(existing.version.as_str() == version.as_str()
        && existing.read_permission == operation.read_permission
        && existing.write_permission == operation.write_permission)
}

fn verify_any_written_row(
    next: &AnyNativeRow,
    operation: &WriteOperation,
    version: ContentVersion,
    updated_at_ms: i64,
    prior: Option<&AnyNativeRow>,
) -> Result<(), DomainError> {
    next.stored.object.verify_integrity()?;
    let expected_digest = IntegrityDigest::from_value(&operation.value).get();
    if next.stored.object.key != operation.key
        || next.stored.object.version.as_str() != version.as_str()
        || next.stored.object.read_permission != operation.read_permission
        || next.stored.object.write_permission != operation.write_permission
        || !next
            .stored
            .object
            .collision_witness
            .as_ref()
            .is_some_and(|witness| witness.matches_request(&operation.value))
        || next.origin != "write-request-bytes"
        || next.raw_value.as_deref() != Some(operation.value.as_slice())
        || next.raw_digest.as_deref() != Some(expected_digest.as_bytes().as_slice())
        || next.manifest.is_some()
        || next.updated_at_ms != updated_at_ms
    {
        return Err(data_loss("storage_any_native_returning_mismatch"));
    }
    let times = next.stored.times;
    if times.update.is_none()
        || prior.is_none() && (times.create.is_none() || times.create != times.update)
        || prior.is_some_and(|old| old.stored.times.create != times.create)
    {
        return Err(data_loss("storage_write_timestamp_mismatch"));
    }
    Ok(())
}

fn apply_nakama_any_write(
    transaction: &mut Transaction<'_>,
    staged: &mut BTreeMap<StorageObjectKey, Option<StoredStorageObject>>,
    actor: Actor,
    operation: &WriteOperation,
    updated_at_ms: i64,
    profile: DatabaseProfile,
) -> Result<AnyWriteStep, DomainError> {
    if operation.expected != VersionCheck::Any {
        return Err(data_loss("storage_any_plan_mismatch"));
    }
    // The outer batch already validates principal/owner for all occurrences.
    // Recheck that pure authority before the first possible reservation effect.
    authorize_write_permission(actor, &operation.key, None)?;
    let payload = RawStorageJsonb::new(&operation.value)?;
    let version = ContentVersion::from_value(&operation.value);
    let request_digest = IntegrityDigest::from_value(&operation.value).get();
    let collection = operation.key.collection();
    let object_key = operation.key.key();
    let owner = operation.key.user_id();
    let owner_bytes = owner.as_bytes().as_slice();
    let request_bytes = operation.value.as_slice();
    let request_fingerprint = request_digest.as_bytes().as_slice();
    let generated_version = version.as_str();
    let read_permission = operation.read_permission.get();
    let write_permission = operation.write_permission.get();
    let mut parameters: Vec<&(dyn ToSql + Sync)> = vec![
        &collection,
        &object_key,
        &owner_bytes,
        &payload,
        &request_bytes,
        &request_fingerprint,
        &generated_version,
        &read_permission,
        &write_permission,
        &updated_at_ms,
    ];
    // Native prospective SHA32 does not return/materialize the prospective
    // rendering in Rust. In particular, a valid unknown no-op may have an input
    // whose native rendering exceeds16MiB. The actual inserted/updated value
    // still goes through the shared bounded native decoder before any receipt.
    let reservation = storage_any_upsert_query(profile, true);
    if let Some(row) = transaction
        .query_opt(&reservation, &parameters)
        .map_err(map_postgres_error)?
    {
        let inserted = decode_any_native_row(&operation.key, &row)?;
        verify_any_written_row(&inserted, operation, version, updated_at_ms, None)?;
        let times = inserted.stored.times;
        staged.insert(operation.key.clone(), Some(inserted.stored));
        verify_storage_staged_budget(staged)?;
        return Ok(AnyWriteStep::Applied(StoredStorageMutationReceipt {
            receipt: MutationReceipt {
                key: operation.key.clone(),
                previous_version: None,
                current_version: Some(version),
            },
            times,
        }));
    }
    // Zero RETURNING means conflict, never absence. CockroachDB first acquires
    // an actual row lock with this skinny ACL SELECT after raw bind/reservation;
    // PostgreSQL already holds its reservation conflict lock. Denied hidden
    // payload/witness must not be decoded before the semantic ACL rejection.
    let access_query = storage_any_access_query(profile);
    let access = transaction
        .query_opt(&access_query, &[&collection, &object_key, &owner_bytes])
        .map_err(map_postgres_error)?
        .map(decode_locked_storage_access)
        .transpose()?
        .ok_or_else(|| data_loss("storage_any_conflict_prior_missing"))?;
    match authorize_write_permission(actor, &operation.key, Some(access.write)) {
        Ok(()) => {}
        Err(rejection) if rejection == write_permission_error() => {
            return Ok(AnyWriteStep::Semantic(rejection));
        }
        Err(hard_error) => return Err(hard_error),
    }
    let prior = read_nakama_any_prior(transaction, &operation.key, profile)?;
    if prior.stored.object.version != access.version
        || prior.stored.object.write_permission != access.write
    {
        return Err(data_loss("storage_any_conflict_prior_changed"));
    }
    if any_conflict_noop(&prior, operation, version)? {
        // No second DML for a matching token+ACL. Independently reread the
        // complete retained native row and compare every private physical
        // observation, including origin/raw/manifest/legacyclock/NULL times.
        let observed = read_nakama_any_prior(transaction, &operation.key, profile)?;
        if observed != prior {
            return Err(data_loss("storage_any_noop_prior_changed"));
        }
        let times = prior.stored.times;
        let previous_version = prior.stored.object.version.clone();
        staged.insert(operation.key.clone(), Some(prior.stored));
        verify_storage_staged_budget(staged)?;
        return Ok(AnyWriteStep::Applied(StoredStorageMutationReceipt {
            receipt: MutationReceipt {
                key: operation.key.clone(),
                previous_version: Some(previous_version),
                current_version: Some(version),
            },
            times,
        }));
    }
    let authoritative = actor == Actor::Server;
    parameters.push(&authoritative);
    // The incoming SELECT is restricted by the actual prior key. If the prior
    // is lost/invisible, zero rows is a hard invariant error, never a new insert
    // or inferred previous=None. No xmax/ctid/time insertion heuristic exists.
    let update = storage_any_upsert_query(profile, false);
    let row = transaction
        .query_opt(&update, &parameters)
        .map_err(map_postgres_error)?
        .ok_or_else(|| data_loss("storage_any_write_row_count_mismatch"))?;
    let next = decode_any_native_row(&operation.key, &row)?;
    verify_any_written_row(&next, operation, version, updated_at_ms, Some(&prior))?;
    let times = next.stored.times;
    let previous_version = prior.stored.object.version;
    staged.insert(operation.key.clone(), Some(next.stored));
    verify_storage_staged_budget(staged)?;
    Ok(AnyWriteStep::Applied(StoredStorageMutationReceipt {
        receipt: MutationReceipt {
            key: operation.key.clone(),
            previous_version: Some(previous_version),
            current_version: Some(version),
        },
        times,
    }))
}
