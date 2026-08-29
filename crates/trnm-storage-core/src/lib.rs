#![forbid(unsafe_code)]

use std::collections::{BTreeMap, BTreeSet};
use std::fmt;

use trnm_contracts::{Digest32, DomainError, RetryClass, StableCode, UserId};

const MAX_COLLECTION_BYTES: usize = 128;
const MAX_KEY_BYTES: usize = 128;
const MAX_VALUE_BYTES: usize = 1024 * 1024;
const MAX_BATCH_OPERATIONS: usize = 100;
const HEX: &[u8; 16] = b"0123456789abcdef";

// RFC 1321 rotation schedule and integer sine constants. MD5 is used only to
// reproduce the pinned Nakama public storage-version contract. It is not used
// as an authentication, signature, password or internal integrity primitive.
const MD5_SHIFTS: [u32; 64] = [
    7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9,
    14, 20, 5, 9, 14, 20, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 6, 10, 15,
    21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
];
const MD5_CONSTANTS: [u32; 64] = [
    0xd76a_a478,
    0xe8c7_b756,
    0x2420_70db,
    0xc1bd_ceee,
    0xf57c_0faf,
    0x4787_c62a,
    0xa830_4613,
    0xfd46_9501,
    0x6980_98d8,
    0x8b44_f7af,
    0xffff_5bb1,
    0x895c_d7be,
    0x6b90_1122,
    0xfd98_7193,
    0xa679_438e,
    0x49b4_0821,
    0xf61e_2562,
    0xc040_b340,
    0x265e_5a51,
    0xe9b6_c7aa,
    0xd62f_105d,
    0x0244_1453,
    0xd8a1_e681,
    0xe7d3_fbc8,
    0x21e1_cde6,
    0xc337_07d6,
    0xf4d5_0d87,
    0x455a_14ed,
    0xa9e3_e905,
    0xfcef_a3f8,
    0x676f_02d9,
    0x8d2a_4c8a,
    0xfffa_3942,
    0x8771_f681,
    0x6d9d_6122,
    0xfde5_380c,
    0xa4be_ea44,
    0x4bde_cfa9,
    0xf6bb_4b60,
    0xbebf_bc70,
    0x289b_7ec6,
    0xeaa1_27fa,
    0xd4ef_3085,
    0x0488_1d05,
    0xd9d4_d039,
    0xe6db_99e5,
    0x1fa2_7cf8,
    0xc4ac_5665,
    0xf429_2244,
    0x432a_ff97,
    0xab94_23a7,
    0xfc93_a039,
    0x655b_59c3,
    0x8f0c_cc92,
    0xffef_f47d,
    0x8584_5dd1,
    0x6fa8_7e4f,
    0xfe2c_e6e0,
    0xa301_4314,
    0x4e08_11a1,
    0xf753_7e82,
    0xbd3a_f235,
    0x2ad7_d2bb,
    0xeb86_d391,
];

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

/// Public Nakama-compatible storage version: lowercase hexadecimal MD5 of the
/// exact stored value bytes. The type cannot be confused with an internal
/// integrity digest.
#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct ContentVersion([u8; 32]);

impl ContentVersion {
    #[must_use]
    pub fn from_value(value: &[u8]) -> Self {
        let digest = md5_digest(value);
        let mut encoded = [0_u8; 32];
        for (index, byte) in digest.iter().copied().enumerate() {
            encoded[index * 2] = HEX[usize::from(byte >> 4)];
            encoded[index * 2 + 1] = HEX[usize::from(byte & 0x0f)];
        }
        Self(encoded)
    }

    pub fn parse(value: &str) -> Result<Self, DomainError> {
        if value.len() != 32
            || !value
                .bytes()
                .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
        {
            return Err(error(
                StableCode::InvalidArgument,
                "invalid_storage_content_version",
                RetryClass::Never,
            ));
        }
        let mut encoded = [0_u8; 32];
        encoded.copy_from_slice(value.as_bytes());
        Ok(Self(encoded))
    }

    #[must_use]
    pub const fn as_bytes(&self) -> &[u8; 32] {
        &self.0
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        std::str::from_utf8(&self.0).expect("ContentVersion is constructed from ASCII hex")
    }
}

impl fmt::Display for ContentVersion {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

/// Internal content-integrity identity. It is intentionally a different type
/// from the public MD5 version. The service adapter is responsible for
/// supplying a reviewed digest (normally SHA-256) of the same exact value.
#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct IntegrityDigest(Digest32);

impl IntegrityDigest {
    pub fn new(value: Digest32) -> Result<Self, DomainError> {
        if value.is_zero() {
            return Err(error(
                StableCode::InvalidArgument,
                "invalid_storage_integrity_digest",
                RetryClass::Never,
            ));
        }
        Ok(Self(value))
    }

    #[must_use]
    pub const fn get(self) -> Digest32 {
        self.0
    }
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
        validate_component(
            &collection,
            MAX_COLLECTION_BYTES,
            "invalid_storage_collection",
        )?;
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
    pub version: ContentVersion,
    pub integrity_digest: IntegrityDigest,
    pub read_permission: ReadPermission,
    pub write_permission: WritePermission,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum VersionCheck {
    /// Nakama `version == ""`: last-write-wins upsert, subject to permission.
    Any,
    /// Nakama `version == "*"`: insert only when the object does not exist.
    MustNotExist,
    /// Nakama non-empty/non-star version: exact if-match update.
    Exact(ContentVersion),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WriteOperation {
    pub key: StorageObjectKey,
    pub value: Vec<u8>,
    pub integrity_digest: IntegrityDigest,
    pub expected: VersionCheck,
    pub read_permission: ReadPermission,
    pub write_permission: WritePermission,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DeleteOperation {
    pub key: StorageObjectKey,
    pub expected_version: Option<ContentVersion>,
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
    pub previous_version: Option<ContentVersion>,
    pub current_version: Option<ContentVersion>,
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

    pub fn read(&self, actor: Actor, key: &StorageObjectKey) -> Result<StorageObject, DomainError> {
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
    validate_value(&operation.value)?;
    let version = ContentVersion::from_value(&operation.value);
    let previous = objects.get(&operation.key).cloned();
    validate_write_actor(actor, &operation.key, previous.as_ref())?;
    validate_version_check(previous.as_ref(), operation.expected)?;

    if let Some(object) = previous.as_ref() {
        if object.version == version
            && (object.value != operation.value
                || object.integrity_digest != operation.integrity_digest)
        {
            return Err(error(
                StableCode::DataLoss,
                "storage_public_version_collision_or_integrity_mismatch",
                RetryClass::Never,
            ));
        }
    }

    let next = StorageObject {
        key: operation.key.clone(),
        value: operation.value.clone(),
        version,
        integrity_digest: operation.integrity_digest,
        read_permission: operation.read_permission,
        write_permission: operation.write_permission,
    };
    objects.insert(operation.key.clone(), next);
    Ok(MutationReceipt {
        key: operation.key.clone(),
        previous_version: previous.map(|object| object.version),
        current_version: Some(version),
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

fn validate_component(
    value: &str,
    maximum: usize,
    reason: &'static str,
) -> Result<(), DomainError> {
    if value.is_empty()
        || value.len() > maximum
        || value.chars().any(char::is_control)
        || value.starts_with('.')
    {
        return Err(error(
            StableCode::InvalidArgument,
            reason,
            RetryClass::Never,
        ));
    }
    Ok(())
}

fn validate_value(value: &[u8]) -> Result<(), DomainError> {
    if value.len() > MAX_VALUE_BYTES {
        return Err(error(
            StableCode::InvalidArgument,
            "invalid_storage_value",
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

fn md5_digest(input: &[u8]) -> [u8; 16] {
    let bit_length = (input.len() as u64).wrapping_mul(8);
    let mut message = input.to_vec();
    message.push(0x80);
    while message.len() % 64 != 56 {
        message.push(0);
    }
    message.extend_from_slice(&bit_length.to_le_bytes());

    let mut state = [0x6745_2301_u32, 0xefcd_ab89, 0x98ba_dcfe, 0x1032_5476];
    for block in message.chunks_exact(64) {
        let mut words = [0_u32; 16];
        for (index, word) in words.iter_mut().enumerate() {
            let offset = index * 4;
            *word = u32::from_le_bytes(
                block[offset..offset + 4]
                    .try_into()
                    .expect("MD5 block word is exactly four bytes"),
            );
        }

        let [mut a, mut b, mut c, mut d] = state;
        for (index, (&shift, &constant)) in MD5_SHIFTS.iter().zip(MD5_CONSTANTS.iter()).enumerate()
        {
            let (function, word_index) = match index {
                0..=15 => ((b & c) | ((!b) & d), index),
                16..=31 => ((d & b) | ((!d) & c), (5 * index + 1) % 16),
                32..=47 => (b ^ c ^ d, (3 * index + 5) % 16),
                _ => (c ^ (b | (!d)), (7 * index) % 16),
            };
            let next = b.wrapping_add(
                a.wrapping_add(function)
                    .wrapping_add(constant)
                    .wrapping_add(words[word_index])
                    .rotate_left(shift),
            );
            a = d;
            d = c;
            c = b;
            b = next;
        }
        state[0] = state[0].wrapping_add(a);
        state[1] = state[1].wrapping_add(b);
        state[2] = state[2].wrapping_add(c);
        state[3] = state[3].wrapping_add(d);
    }

    let mut output = [0_u8; 16];
    for (index, word) in state.iter().enumerate() {
        output[index * 4..index * 4 + 4].copy_from_slice(&word.to_le_bytes());
    }
    output
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

    fn integrity(value: &[u8]) -> IntegrityDigest {
        let seed = value.iter().fold(1_u8, |state, byte| {
            state.wrapping_mul(31).wrapping_add(*byte)
        });
        IntegrityDigest::new(Digest32::new([seed; 32])).unwrap()
    }

    fn key(owner: u8, name: &str) -> StorageObjectKey {
        StorageObjectKey::new("profile", name, user(owner)).unwrap()
    }

    fn write(
        owner: u8,
        name: &str,
        value: &[u8],
        expected: VersionCheck,
        read: ReadPermission,
        write: WritePermission,
    ) -> BatchOperation {
        BatchOperation::Write(WriteOperation {
            key: key(owner, name),
            value: value.to_vec(),
            integrity_digest: integrity(value),
            expected,
            read_permission: read,
            write_permission: write,
        })
    }

    #[test]
    fn content_version_matches_pinned_nakama_md5_hex() {
        assert_eq!(
            ContentVersion::from_value(b"v1").as_str(),
            "6654c734ccab8f440ff0825eb443dc7f"
        );
        assert_eq!(
            ContentVersion::from_value(b"").as_str(),
            "d41d8cd98f00b204e9800998ecf8427e"
        );
        assert_eq!(
            ContentVersion::from_value(b"abc").as_str(),
            "900150983cd24fb0d6963f7d28e17f72"
        );
    }

    #[test]
    fn content_version_parser_is_strict_lowercase_hex() {
        assert!(ContentVersion::parse("6654c734ccab8f440ff0825eb443dc7f").is_ok());
        for invalid in [
            "",
            "6654C734CCAB8F440FF0825EB443DC7F",
            "6654c734ccab8f440ff0825eb443dc7",
            "z654c734ccab8f440ff0825eb443dc7f",
        ] {
            assert_eq!(
                ContentVersion::parse(invalid).unwrap_err().reason(),
                "invalid_storage_content_version"
            );
        }
    }

    #[test]
    fn owner_write_and_read_respects_occ() {
        let mut state = StorageState::default();
        let receipt = state
            .apply_batch(
                Actor::User(user(1)),
                &[write(
                    1,
                    "main",
                    b"v1",
                    VersionCheck::MustNotExist,
                    ReadPermission::Owner,
                    WritePermission::Owner,
                )],
            )
            .unwrap()
            .remove(0);
        assert_eq!(
            receipt.current_version.unwrap().as_str(),
            "6654c734ccab8f440ff0825eb443dc7f"
        );
        assert_eq!(
            state
                .read(Actor::User(user(1)), &key(1, "main"))
                .unwrap()
                .value,
            b"v1"
        );
    }

    #[test]
    fn public_and_private_read_permissions_are_distinct() {
        let mut state = StorageState::default();
        state
            .apply_batch(
                Actor::Server,
                &[
                    write(
                        1,
                        "public",
                        b"p",
                        VersionCheck::Any,
                        ReadPermission::Public,
                        WritePermission::Owner,
                    ),
                    write(
                        1,
                        "private",
                        b"s",
                        VersionCheck::Any,
                        ReadPermission::None,
                        WritePermission::Owner,
                    ),
                ],
            )
            .unwrap();
        assert_eq!(
            state
                .read(Actor::User(user(2)), &key(1, "public"))
                .unwrap()
                .value,
            b"p"
        );
        assert_eq!(
            state
                .read(Actor::User(user(2)), &key(1, "private"))
                .unwrap_err()
                .reason(),
            "storage_read_permission_denied"
        );
    }

    #[test]
    fn stale_version_rejects_without_mutation() {
        let mut state = StorageState::default();
        state
            .apply_batch(
                Actor::Server,
                &[write(
                    1,
                    "main",
                    b"v1",
                    VersionCheck::Any,
                    ReadPermission::Owner,
                    WritePermission::Owner,
                )],
            )
            .unwrap();
        let error = state
            .apply_batch(
                Actor::User(user(1)),
                &[write(
                    1,
                    "main",
                    b"v2",
                    VersionCheck::Exact(ContentVersion::from_value(b"stale")),
                    ReadPermission::Owner,
                    WritePermission::Owner,
                )],
            )
            .unwrap_err();
        assert_eq!(error.reason(), "storage_version_mismatch");
        assert_eq!(
            state.read(Actor::Server, &key(1, "main")).unwrap().value,
            b"v1"
        );
    }

    #[test]
    fn multi_operation_batch_rolls_back_on_any_failure() {
        let mut state = StorageState::default();
        state
            .apply_batch(
                Actor::Server,
                &[write(
                    1,
                    "existing",
                    b"v1",
                    VersionCheck::Any,
                    ReadPermission::Owner,
                    WritePermission::Owner,
                )],
            )
            .unwrap();
        let before = state.clone();
        let operations = [
            write(
                1,
                "new",
                b"new",
                VersionCheck::MustNotExist,
                ReadPermission::Owner,
                WritePermission::Owner,
            ),
            write(
                1,
                "existing",
                b"bad",
                VersionCheck::Exact(ContentVersion::from_value(b"stale")),
                ReadPermission::Owner,
                WritePermission::Owner,
            ),
        ];
        assert!(state
            .apply_batch(Actor::User(user(1)), &operations)
            .is_err());
        assert_eq!(state, before);
    }

    #[test]
    fn duplicate_key_in_batch_is_rejected() {
        let mut state = StorageState::default();
        let operations = [
            write(
                1,
                "same",
                b"v1",
                VersionCheck::Any,
                ReadPermission::Owner,
                WritePermission::Owner,
            ),
            write(
                1,
                "same",
                b"v2",
                VersionCheck::Any,
                ReadPermission::Owner,
                WritePermission::Owner,
            ),
        ];
        assert_eq!(
            state
                .apply_batch(Actor::Server, &operations)
                .unwrap_err()
                .reason(),
            "duplicate_storage_key_in_batch"
        );
    }

    #[test]
    fn server_owned_object_cannot_be_mutated_by_user() {
        let mut state = StorageState::default();
        let server_key = StorageObjectKey::new("system", "config", UserId::new([0; 16])).unwrap();
        state
            .apply_batch(
                Actor::Server,
                &[BatchOperation::Write(WriteOperation {
                    key: server_key.clone(),
                    value: b"v1".to_vec(),
                    integrity_digest: integrity(b"v1"),
                    expected: VersionCheck::MustNotExist,
                    read_permission: ReadPermission::Public,
                    write_permission: WritePermission::None,
                })],
            )
            .unwrap();
        let attempted = BatchOperation::Write(WriteOperation {
            key: server_key,
            value: b"v2".to_vec(),
            integrity_digest: integrity(b"v2"),
            expected: VersionCheck::Exact(ContentVersion::from_value(b"v1")),
            read_permission: ReadPermission::Public,
            write_permission: WritePermission::None,
        });
        assert_eq!(
            state
                .apply_batch(Actor::User(user(1)), &[attempted])
                .unwrap_err()
                .reason(),
            "storage_write_permission_denied"
        );
    }

    #[test]
    fn delete_requires_exact_version_when_supplied() {
        let mut state = StorageState::default();
        state
            .apply_batch(
                Actor::Server,
                &[write(
                    1,
                    "main",
                    b"v1",
                    VersionCheck::Any,
                    ReadPermission::Owner,
                    WritePermission::Owner,
                )],
            )
            .unwrap();
        let version = ContentVersion::from_value(b"v1");
        let delete = BatchOperation::Delete(DeleteOperation {
            key: key(1, "main"),
            expected_version: Some(version),
        });
        let receipt = state
            .apply_batch(Actor::User(user(1)), &[delete])
            .unwrap()
            .remove(0);
        assert_eq!(receipt.previous_version, Some(version));
        assert_eq!(receipt.current_version, None);
        assert_eq!(state.object_count(), 0);
    }

    #[test]
    fn identical_version_cannot_name_different_value() {
        let mut state = StorageState::default();
        let object_key = key(1, "main");
        state.objects.insert(
            object_key.clone(),
            StorageObject {
                key: object_key,
                value: b"corrupt-different-value".to_vec(),
                version: ContentVersion::from_value(b"v1"),
                integrity_digest: integrity(b"corrupt-different-value"),
                read_permission: ReadPermission::Owner,
                write_permission: WritePermission::Owner,
            },
        );
        let error = state
            .apply_batch(
                Actor::Server,
                &[write(
                    1,
                    "main",
                    b"v1",
                    VersionCheck::Any,
                    ReadPermission::Owner,
                    WritePermission::Owner,
                )],
            )
            .unwrap_err();
        assert_eq!(
            error.reason(),
            "storage_public_version_collision_or_integrity_mismatch"
        );
    }

    #[test]
    fn must_not_exist_rejects_existing_object() {
        let mut state = StorageState::default();
        state
            .apply_batch(
                Actor::Server,
                &[write(
                    1,
                    "main",
                    b"v1",
                    VersionCheck::Any,
                    ReadPermission::Owner,
                    WritePermission::Owner,
                )],
            )
            .unwrap();
        assert_eq!(
            state
                .apply_batch(
                    Actor::Server,
                    &[write(
                        1,
                        "main",
                        b"v2",
                        VersionCheck::MustNotExist,
                        ReadPermission::Owner,
                        WritePermission::Owner,
                    )]
                )
                .unwrap_err()
                .reason(),
            "storage_object_already_exists"
        );
    }
}
