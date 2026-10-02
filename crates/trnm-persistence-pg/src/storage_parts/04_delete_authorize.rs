// Only the explicit Nakama delete policy permits an authoritative missing-row
// no-op. The typed mixed batch still rejects missing deletes as before.
fn apply_nakama_delete(
    transaction: &mut Transaction<'_>,
    staged: &mut BTreeMap<StorageObjectKey, Option<StoredStorageObject>>,
    actor: Actor,
    operation: &DeleteOperation,
) -> Result<StoredStorageMutationReceipt, DomainError> {
    if actor == Actor::Server
        && operation.expected_version.is_none()
        && staged.get(&operation.key).is_some_and(Option::is_none)
    {
        return Ok(StoredStorageMutationReceipt {
            receipt: MutationReceipt {
                key: operation.key.clone(),
                previous_version: None,
                current_version: None,
            },
            times: StorageTimes::default(),
        });
    }
    apply_delete(transaction, staged, actor, operation)
}

fn apply_delete(
    transaction: &mut Transaction<'_>,
    staged: &mut BTreeMap<StorageObjectKey, Option<StoredStorageObject>>,
    actor: Actor,
    operation: &DeleteOperation,
) -> Result<StoredStorageMutationReceipt, DomainError> {
    let previous = staged
        .get(&operation.key)
        .cloned()
        .ok_or_else(|| data_loss("storage_batch_lock_missing"))?
        .ok_or_else(storage_not_found)?;
    authorize_delete_permission(
        actor,
        &operation.key,
        Some(previous.object.write_permission),
    )?;
    if operation
        .expected_version
        .as_ref()
        .is_some_and(|expected| expected.as_str() != previous.object.version.as_str())
    {
        return Err(version_error());
    }
    let deleted = transaction
        .execute(
            "DELETE FROM public.trnm_storage_objects \
             WHERE collection = $1 AND object_key = $2 AND user_id = $3",
            &[
                &operation.key.collection(),
                &operation.key.key(),
                &operation.key.user_id().as_bytes().as_slice(),
            ],
        )
        .map_err(map_postgres_error)?;
    if deleted != 1 {
        return Err(data_loss("storage_delete_row_count_mismatch"));
    }
    staged.insert(operation.key.clone(), None);
    Ok(StoredStorageMutationReceipt {
        receipt: MutationReceipt {
            key: operation.key.clone(),
            previous_version: Some(previous.object.version),
            current_version: None,
        },
        times: previous.times,
    })
}

pub(crate) fn verify_storage_writer_epoch(
    transaction: &mut Transaction<'_>,
    profile: DatabaseProfile,
) -> Result<(), DomainError> {
    // This snapshot check is deliberately nonlocking. It complements a real
    // credential/session upgrade barrier; it cannot fence already-unchecked v1 writers.
    let row = transaction
        .query_opt(
            "SELECT schema_version, profile, storage_writer_epoch, chain_digest, digest_algorithm \
         FROM public.trnm_schema_metadata WHERE singleton = 1",
            &[],
        )
        .map_err(|source| {
            let error = map_postgres_error(source);
            if error.code() == StableCode::FailedPrecondition {
                data_loss("storage_schema_not_ready")
            } else {
                error
            }
        })?
        .ok_or_else(|| data_loss("storage_schema_not_ready"))?;
    let version: i64 = row
        .try_get(0)
        .map_err(|_| data_loss("storage_schema_not_ready"))?;
    let recorded_profile: String = row
        .try_get(1)
        .map_err(|_| data_loss("storage_schema_not_ready"))?;
    let epoch: Option<i64> = row
        .try_get(2)
        .map_err(|_| data_loss("storage_schema_not_ready"))?;
    let digest: Option<String> = row
        .try_get(3)
        .map_err(|_| data_loss("storage_schema_not_ready"))?;
    let algorithm: Option<String> = row
        .try_get(4)
        .map_err(|_| data_loss("storage_schema_not_ready"))?;
    if u64::try_from(version).ok() != Some(crate::AUTHORITATIVE_SCHEMA_VERSION)
        || epoch.and_then(|value| u64::try_from(value).ok())
            != Some(crate::AUTHORITATIVE_STORAGE_WRITER_EPOCH)
        || recorded_profile != profile.metadata_value()
    {
        return Err(data_loss("storage_writer_epoch_mismatch"));
    }
    use std::fmt::Write as _;
    let mut expected = String::with_capacity(64);
    for byte in crate::authoritative_chain_digest(profile).get().as_bytes() {
        write!(expected, "{byte:02x}")
            .map_err(|_| data_loss("storage_schema_identity_mismatch"))?;
    }
    if digest.as_deref() != Some(expected.as_str())
        || algorithm.as_deref() != Some(crate::AUTHORITATIVE_CHAIN_DIGEST_ALGORITHM)
    {
        return Err(data_loss("storage_schema_identity_mismatch"));
    }
    crate::storage_import::verify_storage_import_serving(transaction)?;
    Ok(())
}

fn validate_batch(operations: &[BatchOperation]) -> Result<(), DomainError> {
    if operations.is_empty() || operations.len() > MAX_BATCH_OPERATIONS {
        return Err(invalid("invalid_storage_batch_size"));
    }
    let mut keys = BTreeSet::new();
    for operation in operations {
        if !keys.insert(operation.key()) {
            return Err(invalid("duplicate_storage_key_in_batch"));
        }
    }
    Ok(())
}

fn validate_version(
    existing: Option<&StorageObject>,
    check: &VersionCheck,
) -> Result<(), DomainError> {
    match check {
        VersionCheck::Any => Ok(()),
        VersionCheck::MustNotExist if existing.is_none() => Ok(()),
        VersionCheck::MustNotExist => Err(error(
            StableCode::AlreadyExists,
            "storage_object_already_exists",
            RetryClass::Never,
        )),
        VersionCheck::Exact(expected) => match existing {
            Some(object) if object.version.as_str() == expected.as_str() => Ok(()),
            _ => Err(version_error()),
        },
    }
}

fn authorize_read(actor: Actor, object: &StorageObject) -> Result<(), DomainError> {
    let allowed = match actor {
        Actor::Server => true,
        Actor::User(user) => object
            .read_permission
            .allows_batch_read(user == object.key.user_id()),
    };
    if allowed {
        Ok(())
    } else {
        Err(error(
            StableCode::PermissionDenied,
            "storage_read_permission_denied",
            RetryClass::Never,
        ))
    }
}

fn authorize_write(
    actor: Actor,
    key: &StorageObjectKey,
    existing: Option<&StorageObject>,
) -> Result<(), DomainError> {
    authorize_write_permission(actor, key, existing.map(|object| object.write_permission))
}

fn authorize_write_permission(
    actor: Actor,
    key: &StorageObjectKey,
    existing: Option<WritePermission>,
) -> Result<(), DomainError> {
    match actor {
        Actor::Server => Ok(()),
        Actor::User(user) => {
            if user.is_zero() || user != key.user_id() {
                return Err(write_permission_error());
            }
            if existing.is_some_and(|permission| !permission.allows_client_write()) {
                return Err(write_permission_error());
            }
            Ok(())
        }
    }
}

fn authorize_delete_permission(
    actor: Actor,
    key: &StorageObjectKey,
    existing: Option<WritePermission>,
) -> Result<(), DomainError> {
    match actor {
        Actor::Server => Ok(()),
        Actor::User(user) => {
            if user.is_zero() || user != key.user_id() {
                return Err(write_permission_error());
            }
            if existing.is_some_and(|permission| !permission.allows_client_delete()) {
                return Err(write_permission_error());
            }
            Ok(())
        }
    }
}
