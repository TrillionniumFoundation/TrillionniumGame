fn apply_delete(
    transaction: &mut Transaction<'_>,
    staged: &mut BTreeMap<StorageObjectKey, Option<StorageObject>>,
    actor: Actor,
    operation: &DeleteOperation,
) -> Result<MutationReceipt, DomainError> {
    let previous = staged
        .get(&operation.key)
        .cloned()
        .ok_or_else(|| data_loss("storage_batch_lock_missing"))?
        .ok_or_else(storage_not_found)?;
    authorize_write(actor, &operation.key, Some(&previous))?;
    if operation
        .expected_version
        .is_some_and(|expected| expected != previous.version)
    {
        return Err(version_error());
    }
    let deleted = transaction
        .execute(
            "DELETE FROM trnm_storage_objects \
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
    Ok(MutationReceipt {
        key: operation.key.clone(),
        previous_version: Some(previous.version),
        current_version: None,
    })
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
    check: VersionCheck,
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
            Some(object) if object.version == expected => Ok(()),
            _ => Err(version_error()),
        },
    }
}

fn authorize_read(actor: Actor, object: &StorageObject) -> Result<(), DomainError> {
    let allowed = match actor {
        Actor::Server => true,
        Actor::User(user) => {
            object.read_permission == ReadPermission::Public
                || (user == object.key.user_id() && object.read_permission == ReadPermission::Owner)
        }
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
    match actor {
        Actor::Server => Ok(()),
        Actor::User(user) => {
            if user.is_zero() || user != key.user_id() {
                return Err(write_permission_error());
            }
            if existing.is_some_and(|object| object.write_permission != WritePermission::Owner) {
                return Err(write_permission_error());
            }
            Ok(())
        }
    }
}
