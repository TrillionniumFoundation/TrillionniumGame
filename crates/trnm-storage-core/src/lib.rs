#![forbid(unsafe_code)]

use std::collections::{BTreeMap, BTreeSet};

use trnm_contracts::{Digest32, DomainError, RetryClass, StableCode, UserId};

const MAX_COLLECTION_BYTES: usize = 128;
const MAX_KEY_BYTES: usize = 128;
const MAX_VALUE_BYTES: usize = 1024 * 1024;
const MAX_BATCH_OPERATIONS: usize = 100;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Actor {
    Server,
    User(UserId),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(u8)]
pub enum ReadPermission {
    None = 0,
    Owner = 1,
    Public = 2,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(u8)]
pub enum WritePermission {
    None = 0,
    Owner = 1,
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct StorageObjectKey {
    collection: String,
    key: String,
    user_id: UserId,
}

impl StorageObjectKey {
    pub fn new(
        collection: impl Into<String>,
        key: impl Into<String>,
        user_id: UserId,
    ) -> Result<Self, DomainError> {
        let collection = collection.into();
        let key = key.into();
        validate_component(&collection, MAX_COLLECTION_BYTES, "invalid_storage_collection")?;
        validate_component(&key, MAX_KEY_BYTES, "invalid_storage_key")?;
        Ok(Self {
            collection,
            key,
            user_id,
        })
    }

    #[must_use]
    pub fn collection(&self) -> &str {
        &self.collection
    }

    #[must_use]
    pub fn key(&self) -> &str {
        &self.key
    }

    #[must_use]
    pub const fn user_id(&self) -> UserId {
        self.user_id
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StorageObject {
    pub key: StorageObjectKey,
    pub value: Vec<u8>,
    pub version: Digest32,
    pub read_permission: ReadPermission,
    pub write_permission: WritePermission,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum VersionCheck {
    Any,
    MustNotExist,
    Exact(Digest32),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WriteOperation {
    pub key: StorageObjectKey,
    pub value: Vec<u8>,
    pub version: Digest32,
    pub expected: VersionCheck,
    pub read_permission: ReadPermission,
    pub write_permission: WritePermission,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DeleteOperation {
    pub key: StorageObjectKey,
    pub expected_version: Option<Digest32>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum BatchOperation {
    Write(WriteOperation),
    Delete(DeleteOperation),
}

impl BatchOperation {
    #[must_use]
    pub fn key(&self) -> &StorageObjectKey {
        match self {
            Self::Write(operation) => &operation.key,
            Self::Delete(operation) => &operation.key,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MutationReceipt {
    pub key: StorageObjectKey,
    pub previous_version: Option<Digest32>,
    pub current_version: Option<Digest32>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct StorageState {
    objects: BTreeMap<StorageObjectKey, StorageObject>,
}

impl StorageState {
    #[must_use]
    pub fn object_count(&self) -> usize {
        self.objects.len()
    }

    pub fn read(
        &self,
        actor: Actor,
        key: &StorageObjectKey,
    ) -> Result<StorageObject, DomainError> {
        let object = self.objects.get(key).ok_or_else(|| {
            error(
                StableCode::NotFound,
                "storage_object_not_found",
                RetryClass::Never,
            )
        })?;
        if !can_read(actor, object) {
            return Err(error(
                StableCode::PermissionDenied,
                "storage_read_permission_denied",
                RetryClass::Never,
            ));
        }
        Ok(object.clone())
    }

    pub fn apply_batch(
        &mut self,
        actor: Actor,
        operations: &[BatchOperation],
    ) -> Result<Vec<MutationReceipt>, DomainError> {
        validate_batch(operations)?;
        let mut staged = self.objects.clone();
        let mut receipts = Vec::with_capacity(operations.len());
        for operation in operations {
            let receipt = match operation {
                BatchOperation::Write(write) => apply_write(&mut staged, actor, write)?,
                BatchOperation::Delete(delete) => apply_delete(&mut staged, actor, delete)?,
            };
            receipts.push(receipt);
        }
        self.objects = staged;
        Ok(receipts)
    }
}

fn apply_write(
    objects: &mut BTreeMap<StorageObjectKey, StorageObject>,
    actor: Actor,
    operation: &WriteOperation,
) -> Result<MutationReceipt, DomainError> {
    validate_value(&operation.value, operation.version)?;
    let previous = objects.get(&operation.key).cloned();
    validate_write_actor(actor, &operation.key, previous.as_ref())?;
    validate_version_check(previous.as_ref(), operation.expected)?;

    if let Some(object) = previous.as_ref() {
        if object.version == operation.version && object.value != operation.value {
            return Err(error(
                StableCode::DataLoss,
                "storage_version_value_mismatch",
                RetryClass::Never,
            ));
        }
    }

    let next = StorageObject {
        key: operation.key.clone(),
        value: operation.value.clone(),
        version: operation.version,
        read_permission: operation.read_permission,
        write_permission: operation.write_permission,
    };
    objects.insert(operation.key.clone(), next);
    Ok(MutationReceipt {
        key: operation.key.clone(),
        previous_version: previous.map(|object| object.version),
        current_version: Some(operation.version),
    })
}

fn apply_delete(
    objects: &mut BTreeMap<StorageObjectKey, StorageObject>,
    actor: Actor,
    operation: &DeleteOperation,
) -> Result<MutationReceipt, DomainError> {
    let previous = objects.get(&operation.key).cloned().ok_or_else(|| {
        error(
            StableCode::NotFound,
            "storage_object_not_found",
            RetryClass::Never,
        )
    })?;
    validate_write_actor(actor, &operation.key, Some(&previous))?;
    if let Some(expected) = operation.expected_version {
        if previous.version != expected {
            return Err(version_error());
        }
    }
    objects.remove(&operation.key);
    Ok(MutationReceipt {
        key: operation.key.clone(),
        previous_version: Some(previous.version),
        current_version: None,
    })
}

fn validate_batch(operations: &[BatchOperation]) -> Result<(), DomainError> {
    if operations.is_empty() || operations.len() > MAX_BATCH_OPERATIONS {
        return Err(error(
            StableCode::InvalidArgument,
            "invalid_storage_batch_size",
            RetryClass::Never,
        ));
    }
    let mut keys = BTreeSet::new();
    for operation in operations {
        if !keys.insert(operation.key()) {
            return Err(error(
                StableCode::InvalidArgument,
                "duplicate_storage_key_in_batch",
                RetryClass::Never,
            ));
        }
    }
    Ok(())
}

fn validate_component(value: &str, maximum: usize, reason: &'static str) -> Result<(), DomainError> {
    if value.is_empty()
        || value.len() > maximum
        || value.chars().any(char::is_control)
        || value.starts_with('.')
    {
        return Err(error(StableCode::InvalidArgument, reason, RetryClass::Never));
    }
    Ok(())
}

fn validate_value(value: &[u8], version: Digest32) -> Result<(), DomainError> {
    if value.len() > MAX_VALUE_BYTES || version.is_zero() {
        return Err(error(
            StableCode::InvalidArgument,
            "invalid_storage_value_or_version",
            RetryClass::Never,
        ));
    }
    Ok(())
}

fn validate_write_actor(
    actor: Actor,
    key: &StorageObjectKey,
    existing: Option<&StorageObject>,
) -> Result<(), DomainError> {
    match actor {
        Actor::Server => Ok(()),
        Actor::User(user_id) => {
            if user_id.is_zero() || user_id != key.user_id {
                return Err(permission_error());
            }
            if existing.is_some_and(|object| object.write_permission != WritePermission::Owner) {
                return Err(permission_error());
            }
            Ok(())
        }
    }
}

fn validate_version_check(
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

fn can_read(actor: Actor, object: &StorageObject) -> bool {
    match actor {
        Actor::Server => true,
        Actor::User(user_id) => {
            object.read_permission == ReadPermission::Public
                || (user_id == object.key.user_id
                    && object.read_permission == ReadPermission::Owner)
        }
    }
}

const fn permission_error() -> DomainError {
    error(
        StableCode::PermissionDenied,
        "storage_write_permission_denied",
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

const fn error(code: StableCode, reason: &'static str, retry: RetryClass) -> DomainError {
    DomainError::new(code, reason, retry)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn user(value: u8) -> UserId {
        UserId::new([value; 16])
    }

    fn digest(value: u8) -> Digest32 {
        Digest32::new([value; 32])
    }

    fn key(owner: u8, name: &str) -> StorageObjectKey {
        StorageObjectKey::new("profile", name, user(owner)).unwrap()
    }

    fn write(
        owner: u8,
        name: &str,
        value: &[u8],
        version: u8,
        expected: VersionCheck,
        read: ReadPermission,
        write: WritePermission,
    ) -> BatchOperation {
        BatchOperation::Write(WriteOperation {
            key: key(owner, name),
            value: value.to_vec(),
            version: digest(version),
            expected,
            read_permission: read,
            write_permission: write,
        })
    }

    #[test]
    fn owner_write_and_read_respects_occ() {
        let mut state = StorageState::default();
        state
            .apply_batch(
                Actor::User(user(1)),
                &[write(
                    1,
                    "main",
                    b"v1",
                    1,
                    VersionCheck::MustNotExist,
                    ReadPermission::Owner,
                    WritePermission::Owner,
                )],
            )
            .unwrap();
        assert_eq!(state.read(Actor::User(user(1)), &key(1, "main")).unwrap().value, b"v1");
    }

    #[test]
    fn public_and_private_read_permissions_are_distinct() {
        let mut state = StorageState::default();
        state
            .apply_batch(
                Actor::Server,
                &[
                    write(1, "public", b"p", 1, VersionCheck::Any, ReadPermission::Public, WritePermission::Owner),
                    write(1, "private", b"s", 2, VersionCheck::Any, ReadPermission::None, WritePermission::Owner),
                ],
            )
            .unwrap();
        assert_eq!(state.read(Actor::User(user(2)), &key(1, "public")).unwrap().value, b"p");
        assert_eq!(state.read(Actor::User(user(2)), &key(1, "private")).unwrap_err().reason(), "storage_read_permission_denied");
    }

    #[test]
    fn stale_version_rejects_without_mutation() {
        let mut state = StorageState::default();
        state.apply_batch(Actor::Server, &[write(1, "main", b"v1", 1, VersionCheck::Any, ReadPermission::Owner, WritePermission::Owner)]).unwrap();
        let error = state.apply_batch(Actor::User(user(1)), &[write(1, "main", b"v2", 2, VersionCheck::Exact(digest(9)), ReadPermission::Owner, WritePermission::Owner)]).unwrap_err();
        assert_eq!(error.reason(), "storage_version_mismatch");
        assert_eq!(state.read(Actor::Server, &key(1, "main")).unwrap().value, b"v1");
    }

    #[test]
    fn multi_operation_batch_rolls_back_on_any_failure() {
        let mut state = StorageState::default();
        state.apply_batch(Actor::Server, &[write(1, "existing", b"v1", 1, VersionCheck::Any, ReadPermission::Owner, WritePermission::Owner)]).unwrap();
        let before = state.clone();
        let operations = [
            write(1, "new", b"new", 2, VersionCheck::MustNotExist, ReadPermission::Owner, WritePermission::Owner),
            write(1, "existing", b"bad", 3, VersionCheck::Exact(digest(9)), ReadPermission::Owner, WritePermission::Owner),
        ];
        assert!(state.apply_batch(Actor::User(user(1)), &operations).is_err());
        assert_eq!(state, before);
    }

    #[test]
    fn duplicate_key_in_batch_is_rejected() {
        let mut state = StorageState::default();
        let operations = [
            write(1, "same", b"v1", 1, VersionCheck::Any, ReadPermission::Owner, WritePermission::Owner),
            write(1, "same", b"v2", 2, VersionCheck::Any, ReadPermission::Owner, WritePermission::Owner),
        ];
        assert_eq!(state.apply_batch(Actor::Server, &operations).unwrap_err().reason(), "duplicate_storage_key_in_batch");
    }

    #[test]
    fn server_owned_object_cannot_be_mutated_by_user() {
        let mut state = StorageState::default();
        let server_key = StorageObjectKey::new("system", "config", UserId::new([0; 16])).unwrap();
        state.apply_batch(Actor::Server, &[BatchOperation::Write(WriteOperation { key: server_key.clone(), value: b"v1".to_vec(), version: digest(1), expected: VersionCheck::MustNotExist, read_permission: ReadPermission::Public, write_permission: WritePermission::None })]).unwrap();
        let attempted = BatchOperation::Write(WriteOperation { key: server_key, value: b"v2".to_vec(), version: digest(2), expected: VersionCheck::Exact(digest(1)), read_permission: ReadPermission::Public, write_permission: WritePermission::None });
        assert_eq!(state.apply_batch(Actor::User(user(1)), &[attempted]).unwrap_err().reason(), "storage_write_permission_denied");
    }

    #[test]
    fn delete_requires_exact_version_when_supplied() {
        let mut state = StorageState::default();
        state.apply_batch(Actor::Server, &[write(1, "main", b"v1", 1, VersionCheck::Any, ReadPermission::Owner, WritePermission::Owner)]).unwrap();
        let delete = BatchOperation::Delete(DeleteOperation { key: key(1, "main"), expected_version: Some(digest(1)) });
        let receipt = state.apply_batch(Actor::User(user(1)), &[delete]).unwrap().remove(0);
        assert_eq!(receipt.previous_version, Some(digest(1)));
        assert_eq!(receipt.current_version, None);
        assert_eq!(state.object_count(), 0);
    }

    #[test]
    fn identical_version_cannot_name_different_value() {
        let mut state = StorageState::default();
        state.apply_batch(Actor::Server, &[write(1, "main", b"v1", 1, VersionCheck::Any, ReadPermission::Owner, WritePermission::Owner)]).unwrap();
        let error = state.apply_batch(Actor::Server, &[write(1, "main", b"different", 1, VersionCheck::Any, ReadPermission::Owner, WritePermission::Owner)]).unwrap_err();
        assert_eq!(error.reason(), "storage_version_value_mismatch");
    }

    #[test]
    fn must_not_exist_rejects_existing_object() {
        let mut state = StorageState::default();
        state.apply_batch(Actor::Server, &[write(1, "main", b"v1", 1, VersionCheck::Any, ReadPermission::Owner, WritePermission::Owner)]).unwrap();
        assert_eq!(state.apply_batch(Actor::Server, &[write(1, "main", b"v2", 2, VersionCheck::MustNotExist, ReadPermission::Owner, WritePermission::Owner)]).unwrap_err().reason(), "storage_object_already_exists");
    }
}
