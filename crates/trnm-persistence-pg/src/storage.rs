use std::collections::{BTreeMap, BTreeSet};

use postgres::{IsolationLevel, Row, Transaction};
#[cfg(test)]
use trnm_contracts::UserId;
use trnm_contracts::{DomainError, RetryClass, StableCode};
use trnm_storage_core::{
    Actor, BatchOperation, ContentVersion, DeleteOperation, IntegrityDigest, MutationReceipt,
    ReadPermission, StorageObject, StorageObjectKey, VersionCheck, WriteOperation, WritePermission,
};

#[cfg(test)]
use super::decode_id16;
use super::{data_loss, decode_digest, error, invalid, map_postgres_error, to_i64, PgRepository};

const MAX_BATCH_OPERATIONS: usize = 100;
const MAX_VALUE_BYTES: usize = 1024 * 1024;

impl PgRepository {
    pub fn read_storage_object(
        &mut self,
        actor: Actor,
        key: &StorageObjectKey,
    ) -> Result<StorageObject, DomainError> {
        let row = self
            .client
            .query_opt(
                "SELECT value_bytes, version_digest, read_permission, write_permission \
                 FROM trnm_storage_objects \
                 WHERE collection = $1 AND object_key = $2 AND user_id = $3",
                &[
                    &key.collection(),
                    &key.key(),
                    &key.user_id().as_bytes().as_slice(),
                ],
            )
            .map_err(map_postgres_error)?
            .ok_or_else(storage_not_found)?;
        let object = decode_storage_object(key.clone(), &row)?;
        authorize_read(actor, &object)?;
        Ok(object)
    }

    pub fn apply_storage_batch(
        &mut self,
        actor: Actor,
        operations: &[BatchOperation],
        updated_at_ms: u64,
    ) -> Result<Vec<MutationReceipt>, DomainError> {
        validate_batch(operations)?;
        let updated_at_i64 = to_i64(updated_at_ms)?;
        let mut transaction = self
            .client
            .build_transaction()
            .isolation_level(IsolationLevel::Serializable)
            .start()
            .map_err(map_postgres_error)?;

        let mut staged = BTreeMap::new();
        for key in sorted_keys(operations) {
            let object = load_for_update(&mut transaction, &key)?;
            staged.insert(key, object);
        }

        let mut receipts = Vec::with_capacity(operations.len());
        for operation in operations {
            let receipt = match operation {
                BatchOperation::Write(write) => {
                    apply_write(&mut transaction, &mut staged, actor, write, updated_at_i64)?
                }
                BatchOperation::Delete(delete) => {
                    apply_delete(&mut transaction, &mut staged, actor, delete)?
                }
            };
            receipts.push(receipt);
        }
        transaction.commit().map_err(map_postgres_error)?;
        Ok(receipts)
    }
}

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
    if let Some(existing) = previous.as_ref() {
        if existing.version == version
            && (existing.value != operation.value
                || existing.integrity_digest != operation.integrity_digest)
        {
            return Err(data_loss(
                "storage_public_version_collision_or_integrity_mismatch",
            ));
        }
    }
    let integrity = operation.integrity_digest.get();
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
        integrity_digest: operation.integrity_digest,
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

fn decode_storage_object(key: StorageObjectKey, row: &Row) -> Result<StorageObject, DomainError> {
    let value: Vec<u8> = row.get(0);
    if value.len() > MAX_VALUE_BYTES {
        return Err(data_loss("invalid_storage_value_bytes"));
    }
    let integrity_digest = IntegrityDigest::new(decode_digest(
        row.get(1),
        "invalid_storage_integrity_digest",
    )?)
    .map_err(|_| data_loss("invalid_storage_integrity_digest"))?;
    let read_permission = match row.get::<_, i16>(2) {
        0 => ReadPermission::None,
        1 => ReadPermission::Owner,
        2 => ReadPermission::Public,
        _ => return Err(data_loss("invalid_storage_read_permission")),
    };
    let write_permission = match row.get::<_, i16>(3) {
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

#[cfg(test)]
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

#[cfg(test)]
mod tests {
    use super::*;
    use trnm_contracts::Digest32;

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
            integrity_digest: IntegrityDigest::new(Digest32::new([value; 32])).unwrap(),
            expected: VersionCheck::Any,
            read_permission: ReadPermission::Owner,
            write_permission: WritePermission::Owner,
        })
    }

    #[test]
    fn batch_validation_rejects_empty_duplicate_and_excess() {
        assert_eq!(
            validate_batch(&[]).unwrap_err().reason(),
            "invalid_storage_batch_size"
        );
        let duplicate = vec![write(1), write(1)];
        assert_eq!(
            validate_batch(&duplicate).unwrap_err().reason(),
            "duplicate_storage_key_in_batch"
        );
        let excess = (0..=MAX_BATCH_OPERATIONS)
            .map(|index| write(u8::try_from(index + 1).unwrap()))
            .collect::<Vec<_>>();
        assert_eq!(
            validate_batch(&excess).unwrap_err().reason(),
            "invalid_storage_batch_size"
        );
    }

    #[test]
    fn permission_and_version_contract_matches_storage_core() {
        let owner = UserId::new([1; 16]);
        let object = StorageObject {
            key: key(1),
            value: b"v1".to_vec(),
            version: ContentVersion::from_value(b"v1"),
            integrity_digest: IntegrityDigest::new(Digest32::new([1; 32])).unwrap(),
            read_permission: ReadPermission::Owner,
            write_permission: WritePermission::Owner,
        };
        authorize_read(Actor::User(owner), &object).unwrap();
        assert_eq!(
            authorize_read(Actor::User(UserId::new([2; 16])), &object)
                .unwrap_err()
                .reason(),
            "storage_read_permission_denied"
        );
        validate_version(Some(&object), VersionCheck::Exact(object.version)).unwrap();
        assert_eq!(
            validate_version(
                Some(&object),
                VersionCheck::Exact(ContentVersion::from_value(b"other")),
            )
            .unwrap_err()
            .reason(),
            "storage_version_mismatch"
        );
    }

    #[test]
    fn database_permission_decoders_are_total() {
        assert_eq!(
            decode_storage_key("a".to_owned(), "b".to_owned(), vec![1; 16])
                .unwrap()
                .user_id(),
            UserId::new([1; 16])
        );
    }
}
