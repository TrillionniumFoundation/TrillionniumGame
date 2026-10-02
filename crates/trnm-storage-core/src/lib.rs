#![forbid(unsafe_code)]

use std::collections::{BTreeMap, BTreeSet};
use std::fmt;

use sha2::{Digest as _, Sha256};
use trnm_contracts::{Digest32, DomainError, RetryClass, StableCode, UserId};

mod projection;
mod stored_domain;
pub use projection::{
    CollisionWitness, PublicVersion, MAX_PROJECTION_VALUE_BYTES, MAX_REQUEST_VALUE_BYTES,
};
pub use stored_domain::{ReadPermission, WritePermission};

const MAX_COLLECTION_BYTES: usize = 128;
const MAX_KEY_BYTES: usize = 128;
const MAX_VALUE_BYTES: usize = MAX_REQUEST_VALUE_BYTES;
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

/// Generated write-request version: lowercase hexadecimal MD5 of the exact
/// request bytes. Native rendered values and opaque persisted public tokens
/// must not be interpreted as this type or hashed to reconstruct their token.
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

/// An exact input condition, preserving the supplied string without parsing,
/// case folding, trimming or a stored-version length restriction. Adapters
/// classify empty/write-star sentinels before constructing the operation and
/// bound untrusted input with their complete request budget. This pure type
/// does not qualify a database profile's native text-parameter error behavior.
#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct ExpectedVersion(String);

impl ExpectedVersion {
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl From<String> for ExpectedVersion {
    fn from(value: String) -> Self {
        Self(value)
    }
}

impl From<&str> for ExpectedVersion {
    fn from(value: &str) -> Self {
        Self(value.to_owned())
    }
}

impl From<ContentVersion> for ExpectedVersion {
    fn from(value: ContentVersion) -> Self {
        Self(value.as_str().to_owned())
    }
}

impl From<PublicVersion> for ExpectedVersion {
    fn from(value: PublicVersion) -> Self {
        Self(value.as_str().to_owned())
    }
}

impl From<&PublicVersion> for ExpectedVersion {
    fn from(value: &PublicVersion) -> Self {
        Self(value.as_str().to_owned())
    }
}

/// Internal SHA-256 content-integrity identity. It is intentionally a
/// different type from the public Nakama-compatible MD5 version. Callers do
/// not choose this value: the storage boundary derives it from the exact value
/// bytes and persistence verifies it again on every read.
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
    pub fn from_value(value: &[u8]) -> Self {
        let digest: [u8; 32] = Sha256::digest(value).into();
        Self(Digest32::new(digest))
    }

    #[must_use]
    pub fn matches_value(self, value: &[u8]) -> bool {
        self == Self::from_value(value)
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

    /// Decode a stored Nakama-profile row, preserving its exact zero-to-128
    /// Unicode-character collection and key. Empty and dot/control names are
    /// legal at this pure boundary; adapters check native text validity.
    /// This is not new HTTP request admission. `new` retains the stricter
    /// internal byte and identifier policy, and HTTP validates required fields.
    pub fn new_nakama(
        collection: impl Into<String>,
        key: impl Into<String>,
        user_id: UserId,
    ) -> Result<Self, DomainError> {
        let collection = collection.into();
        let key = key.into();
        for (value, reason) in [
            (&collection, "invalid_storage_collection"),
            (&key, "invalid_storage_key"),
        ] {
            let count = value.chars().take(129).count();
            if count > 128 {
                return Err(error(
                    StableCode::InvalidArgument,
                    reason,
                    RetryClass::Never,
                ));
            }
        }
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
    /// Externally supplied projection bytes; the default pure model uses the
    /// identity projection and does not implement a native JSONB renderer.
    pub value: Vec<u8>,
    pub version: PublicVersion,
    pub integrity_digest: IntegrityDigest,
    pub collision_witness: Option<CollisionWitness>,
    pub read_permission: ReadPermission,
    pub write_permission: WritePermission,
}

impl StorageObject {
    pub fn verify_integrity(&self) -> Result<(), DomainError> {
        verify_object_integrity(self)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum VersionCheck {
    /// Nakama `version == ""`: last-write-wins upsert, subject to permission.
    Any,
    /// Nakama `version == "*"`: insert only when the object does not exist.
    MustNotExist,
    /// Exact literal if-match condition. Wire adapters select Any for empty
    /// versions and MustNotExist for the write-only star sentinel.
    Exact(ExpectedVersion),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WriteOperation {
    pub key: StorageObjectKey,
    pub value: Vec<u8>,
    pub expected: VersionCheck,
    pub read_permission: ReadPermission,
    pub write_permission: WritePermission,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DeleteOperation {
    pub key: StorageObjectKey,
    /// None means unconditional; every Some token, including star, is literal.
    pub expected_version: Option<ExpectedVersion>,
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
    pub previous_version: Option<PublicVersion>,
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
        verify_object_integrity(object)?;
        Ok(object.clone())
    }

    pub fn apply_batch(
        &mut self,
        actor: Actor,
        operations: &[BatchOperation],
    ) -> Result<Vec<MutationReceipt>, DomainError> {
        self.apply_batch_projected(actor, operations, |request| Ok(request.to_vec()))
    }

    /// Apply a batch with an adapter-supplied projection. The closure must be
    /// bounded and side-effect-free; only this model's staged objects roll back.
    /// Authorization/OCC/integrity and blind no-op checks precede projection.
    pub fn apply_batch_projected<F>(
        &mut self,
        actor: Actor,
        operations: &[BatchOperation],
        mut projector: F,
    ) -> Result<Vec<MutationReceipt>, DomainError>
    where
        F: FnMut(&[u8]) -> Result<Vec<u8>, DomainError>,
    {
        validate_batch(operations)?;
        let mut staged = self.objects.clone();
        let mut receipts = Vec::with_capacity(operations.len());
        for operation in operations {
            let receipt = match operation {
                BatchOperation::Write(write) => {
                    apply_write(&mut staged, actor, write, &mut projector)?
                }
                BatchOperation::Delete(delete) => apply_delete(&mut staged, actor, delete)?,
            };
            receipts.push(receipt);
        }
        self.objects = staged;
        Ok(receipts)
    }
}

fn apply_write<F>(
    objects: &mut BTreeMap<StorageObjectKey, StorageObject>,
    actor: Actor,
    operation: &WriteOperation,
    projector: &mut F,
) -> Result<MutationReceipt, DomainError>
where
    F: FnMut(&[u8]) -> Result<Vec<u8>, DomainError>,
{
    validate_value(&operation.value)?;
    let version = ContentVersion::from_value(&operation.value);
    let previous = objects.get(&operation.key).cloned();
    // Insert-only checks owner authority without consulting an existing row's
    // write ACL. An existing key rejects the version even when write is disabled.
    let acl_object = if operation.expected == VersionCheck::MustNotExist {
        None
    } else {
        previous.as_ref()
    };
    validate_write_actor(actor, &operation.key, acl_object)?;
    validate_version_check(previous.as_ref(), &operation.expected)?;

    if let Some(object) = previous.as_ref() {
        verify_object_integrity(object)?;
        if object.version.as_str() == version.as_str()
            && object
                .collision_witness
                .is_some_and(|witness| !witness.matches_request(&operation.value))
        {
            return Err(error(
                StableCode::DataLoss,
                "storage_public_version_collision_or_integrity_mismatch",
                RetryClass::Never,
            ));
        }
        if operation.expected == VersionCheck::Any
            && object.version.as_str() == version.as_str()
            && object.read_permission == operation.read_permission
            && object.write_permission == operation.write_permission
        {
            return Ok(MutationReceipt {
                key: operation.key.clone(),
                previous_version: Some(object.version.clone()),
                current_version: Some(version),
            });
        }
    }

    let value = projector(&operation.value)?;
    let collision_witness = CollisionWitness::from_request(&operation.value, &value)?;
    let next = StorageObject {
        key: operation.key.clone(),
        value,
        version: version.into(),
        integrity_digest: collision_witness.projection_digest(),
        collision_witness: Some(collision_witness),
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
    validate_delete_actor(actor, &operation.key, &previous)?;
    if let Some(expected) = operation.expected_version.as_ref() {
        if previous.version.as_str() != expected.as_str() {
            return Err(version_error());
        }
    }
    verify_object_integrity(&previous)?;
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
        if let BatchOperation::Write(write) = operation {
            validate_value(&write.value)?;
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
    validate_owner_actor(actor, key)?;
    if matches!(actor, Actor::User(_))
        && existing.is_some_and(|object| !object.write_permission.allows_client_write())
    {
        return Err(permission_error());
    }
    Ok(())
}

fn validate_delete_actor(
    actor: Actor,
    key: &StorageObjectKey,
    existing: &StorageObject,
) -> Result<(), DomainError> {
    validate_owner_actor(actor, key)?;
    if matches!(actor, Actor::User(_)) && !existing.write_permission.allows_client_delete() {
        return Err(permission_error());
    }
    Ok(())
}

fn validate_owner_actor(actor: Actor, key: &StorageObjectKey) -> Result<(), DomainError> {
    if let Actor::User(user_id) = actor {
        if user_id.is_zero() || user_id != key.user_id {
            return Err(permission_error());
        }
    }
    Ok(())
}

fn validate_version_check(
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

fn can_read(actor: Actor, object: &StorageObject) -> bool {
    match actor {
        Actor::Server => true,
        Actor::User(user_id) => {
            !user_id.is_zero()
                && object
                    .read_permission
                    .allows_batch_read(user_id == object.key.user_id)
        }
    }
}

fn verify_object_integrity(object: &StorageObject) -> Result<(), DomainError> {
    projection::validate_projection_budget(&object.value)?;
    if !object.integrity_digest.matches_value(&object.value) {
        return Err(error(
            StableCode::DataLoss,
            "storage_integrity_digest_mismatch",
            RetryClass::Never,
        ));
    }
    if let Some(witness) = object.collision_witness.as_ref() {
        witness.validate_projection(&object.version, &object.value)?;
    }
    Ok(())
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
mod projection_tests;

#[cfg(test)]
mod stored_domain_tests;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nakama_row_identifier_projection_keeps_unicode_schema_bounds_separate() {
        let owner = UserId::new([1; 16]);
        let name = "中".repeat(128);
        assert!(StorageObjectKey::new_nakama(&name, &name, owner).is_ok());
        assert!(StorageObjectKey::new(&name, "key", owner).is_err());
        assert!(StorageObjectKey::new_nakama(".a\u{1}", ".key\u{2}", owner).is_ok());
        assert!(StorageObjectKey::new_nakama("", "", owner).is_ok());
        assert!(StorageObjectKey::new("", "key", owner).is_err());
        assert!(StorageObjectKey::new("collection", "", owner).is_err());
        let too_long = "中".repeat(129);
        assert!(StorageObjectKey::new_nakama(&too_long, "key", owner).is_err());
        assert!(StorageObjectKey::new_nakama("collection", &too_long, owner).is_err());
    }

    fn user(value: u8) -> UserId {
        UserId::new([value; 16])
    }

    fn integrity(value: &[u8]) -> IntegrityDigest {
        IntegrityDigest::from_value(value)
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
    fn integrity_digest_is_canonical_sha256_over_exact_value_bytes() {
        let digest = IntegrityDigest::from_value(b"abc").get();
        assert_eq!(
            digest.as_bytes(),
            &[
                0xba, 0x78, 0x16, 0xbf, 0x8f, 0x01, 0xcf, 0xea, 0x41, 0x41, 0x40, 0xde, 0x5d, 0xae,
                0x22, 0x23, 0xb0, 0x03, 0x61, 0xa3, 0x96, 0x17, 0x7a, 0x9c, 0xb4, 0x10, 0xff, 0x61,
                0xf2, 0x00, 0x15, 0xad,
            ]
        );
        assert!(IntegrityDigest::from_value(b"abc").matches_value(b"abc"));
        assert!(!IntegrityDigest::from_value(b"abc").matches_value(b"abd"));
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

    fn opaque_tokens() -> Vec<String> {
        let version = ContentVersion::from_value(b"v1");
        vec![
            version.as_str().to_ascii_uppercase(),
            "arbitrary nonhex".to_owned(),
            "版本🔑".to_owned(),
            String::new(),
            "*".to_owned(),
            "f".repeat(33),
            "f".repeat(64 * 1024),
            format!("{}suffix", version.as_str()),
            format!(" {}\t", version.as_str()),
        ]
    }

    #[test]
    fn expected_version_preserves_strings_without_stored_version_constraints() {
        let mut tokens = opaque_tokens();
        tokens.extend(["é".to_owned(), "e\u{301}".to_owned(), "line\r\n".to_owned()]);
        // Pure input preservation does not qualify native SQL text errors.
        tokens.push("nul\0token".to_owned());
        for raw in tokens {
            let owned = ExpectedVersion::from(raw.clone());
            let borrowed = ExpectedVersion::from(raw.as_str());
            assert_eq!(owned.as_str().as_bytes(), raw.as_bytes());
            assert_eq!(owned, borrowed);
            assert_eq!(owned.clone(), owned);
        }
        assert_ne!(
            ExpectedVersion::from("é"),
            ExpectedVersion::from("e\u{301}")
        );
        let generated = ContentVersion::from_value(b"v1");
        assert_eq!(
            ExpectedVersion::from(generated).as_str(),
            generated.as_str()
        );
        // Retaining opaque input must not weaken the separate MD5 parser.
        assert!(ContentVersion::parse(&generated.as_str().to_ascii_uppercase()).is_err());
    }

    #[test]
    fn opaque_write_tokens_are_exact_and_keep_permission_precedence() {
        for acl in [WritePermission::NONE, WritePermission::OWNER] {
            let mut seeded = StorageState::default();
            seeded
                .apply_batch(
                    Actor::Server,
                    &[write(
                        1,
                        "main",
                        b"v1",
                        VersionCheck::Any,
                        ReadPermission::OWNER,
                        acl,
                    )],
                )
                .unwrap();
            for raw in opaque_tokens() {
                for actor in [Actor::User(user(1)), Actor::Server] {
                    let mut state = seeded.clone();
                    let check = VersionCheck::Exact(raw.as_str().into());
                    let operation = write(
                        1,
                        "main",
                        b"v2",
                        check.clone(),
                        ReadPermission::PUBLIC,
                        WritePermission::OWNER,
                    );
                    let error = state
                        .apply_batch(actor, std::slice::from_ref(&operation))
                        .unwrap_err();
                    let (code, reason) =
                        if actor == Actor::User(user(1)) && acl == WritePermission::NONE {
                            (
                                StableCode::PermissionDenied,
                                "storage_write_permission_denied",
                            )
                        } else {
                            (StableCode::FailedPrecondition, "storage_version_mismatch")
                        };
                    assert_eq!(
                        (error.code(), error.reason()),
                        (code, reason),
                        "token bytes={}",
                        raw.len()
                    );
                    assert_eq!(state, seeded);
                    let BatchOperation::Write(operation) = operation else {
                        unreachable!()
                    };
                    assert_eq!(
                        operation.expected, check,
                        "checking must not consume or alter the condition"
                    );
                }
                let mut state = seeded.clone();
                let error = state
                    .apply_batch(
                        Actor::User(user(1)),
                        &[write(
                            1,
                            "missing",
                            b"v2",
                            VersionCheck::Exact(raw.into()),
                            ReadPermission::OWNER,
                            WritePermission::OWNER,
                        )],
                    )
                    .unwrap_err();
                assert_eq!(error.reason(), "storage_version_mismatch");
                assert_eq!(state, seeded);
            }
        }
        let mut state = StorageState::default();
        state
            .apply_batch(
                Actor::User(user(1)),
                &[write(
                    1,
                    "main",
                    b"v1",
                    VersionCheck::MustNotExist,
                    ReadPermission::OWNER,
                    WritePermission::OWNER,
                )],
            )
            .unwrap();
        let raw = state
            .read(Actor::User(user(1)), &key(1, "main"))
            .unwrap()
            .version
            .as_str()
            .to_owned();
        let receipt = state
            .apply_batch(
                Actor::User(user(1)),
                &[write(
                    1,
                    "main",
                    b"v2",
                    VersionCheck::Exact(raw.into()),
                    ReadPermission::PUBLIC,
                    WritePermission::OWNER,
                )],
            )
            .unwrap()
            .remove(0);
        assert_eq!(
            receipt.previous_version,
            Some(ContentVersion::from_value(b"v1").into())
        );
        assert_eq!(
            receipt.current_version,
            Some(ContentVersion::from_value(b"v2"))
        );
        let current = state.read(Actor::Server, &key(1, "main")).unwrap();
        assert_eq!(current.value, b"v2");
        assert!(current.integrity_digest.matches_value(b"v2"));
    }

    #[test]
    fn opaque_delete_tokens_are_literal_and_keep_permission_precedence() {
        for acl in [WritePermission::NONE, WritePermission::OWNER] {
            let mut seeded = StorageState::default();
            seeded
                .apply_batch(
                    Actor::Server,
                    &[write(
                        1,
                        "main",
                        b"v1",
                        VersionCheck::Any,
                        ReadPermission::OWNER,
                        acl,
                    )],
                )
                .unwrap();
            for raw in opaque_tokens() {
                for actor in [Actor::User(user(1)), Actor::Server] {
                    let mut state = seeded.clone();
                    let operation = BatchOperation::Delete(DeleteOperation {
                        key: key(1, "main"),
                        expected_version: Some(raw.as_str().into()),
                    });
                    let error = state
                        .apply_batch(actor, std::slice::from_ref(&operation))
                        .unwrap_err();
                    let (code, reason) =
                        if actor == Actor::User(user(1)) && acl == WritePermission::NONE {
                            (
                                StableCode::PermissionDenied,
                                "storage_write_permission_denied",
                            )
                        } else {
                            (StableCode::FailedPrecondition, "storage_version_mismatch")
                        };
                    assert_eq!(
                        (error.code(), error.reason()),
                        (code, reason),
                        "token bytes={}",
                        raw.len()
                    );
                    assert_eq!(state, seeded);
                }
            }
        }
        let mut state = StorageState::default();
        state
            .apply_batch(
                Actor::Server,
                &[write(
                    1,
                    "main",
                    b"v1",
                    VersionCheck::Any,
                    ReadPermission::OWNER,
                    WritePermission::OWNER,
                )],
            )
            .unwrap();
        // Delete's star remains a literal condition, unlike write MustNotExist.
        let before = state.clone();
        let star = BatchOperation::Delete(DeleteOperation {
            key: key(1, "main"),
            expected_version: Some("*".into()),
        });
        assert_eq!(
            state
                .apply_batch(Actor::User(user(1)), &[star])
                .unwrap_err()
                .reason(),
            "storage_version_mismatch"
        );
        assert_eq!(state, before);
        // An actual matching string condition still produces a committed delete.
        let current = state.read(Actor::Server, &key(1, "main")).unwrap().version;
        let delete = BatchOperation::Delete(DeleteOperation {
            key: key(1, "main"),
            expected_version: Some(current.as_str().into()),
        });
        let receipt = state
            .apply_batch(Actor::User(user(1)), &[delete])
            .unwrap()
            .remove(0);
        assert_eq!(receipt.previous_version, Some(current));
        assert_eq!(receipt.current_version, None);
        assert_eq!(state.object_count(), 0);
    }

    #[test]
    fn opaque_conditions_do_not_override_owner_binding() {
        let mut seeded = StorageState::default();
        seeded
            .apply_batch(
                Actor::Server,
                &[
                    write(
                        1,
                        "foreign",
                        b"v1",
                        VersionCheck::Any,
                        ReadPermission::PUBLIC,
                        WritePermission::OWNER,
                    ),
                    write(
                        0,
                        "global",
                        b"v1",
                        VersionCheck::Any,
                        ReadPermission::PUBLIC,
                        WritePermission::OWNER,
                    ),
                ],
            )
            .unwrap();
        for raw in opaque_tokens() {
            for actor in [Actor::User(user(2)), Actor::User(user(0))] {
                for (owner, name) in [(1, "foreign"), (0, "global")] {
                    let operations = [
                        write(
                            owner,
                            name,
                            b"v2",
                            VersionCheck::Exact(raw.as_str().into()),
                            ReadPermission::PUBLIC,
                            WritePermission::OWNER,
                        ),
                        BatchOperation::Delete(DeleteOperation {
                            key: key(owner, name),
                            expected_version: Some(raw.as_str().into()),
                        }),
                    ];
                    for operation in operations {
                        let mut state = seeded.clone();
                        let error = state.apply_batch(actor, &[operation]).unwrap_err();
                        assert_eq!(error.code(), StableCode::PermissionDenied);
                        assert_eq!(error.reason(), "storage_write_permission_denied");
                        assert_eq!(state, seeded);
                    }
                }
            }
        }
    }

    #[test]
    fn opaque_condition_rejections_roll_back_prior_writes_and_deletes() {
        let mut seeded = StorageState::default();
        seeded
            .apply_batch(
                Actor::Server,
                &[
                    write(
                        1,
                        "main",
                        b"v1",
                        VersionCheck::Any,
                        ReadPermission::OWNER,
                        WritePermission::OWNER,
                    ),
                    write(
                        1,
                        "prior_delete",
                        b"keep",
                        VersionCheck::Any,
                        ReadPermission::PUBLIC,
                        WritePermission::OWNER,
                    ),
                ],
            )
            .unwrap();
        for raw in opaque_tokens() {
            for failed in [
                write(
                    1,
                    "main",
                    b"v2",
                    VersionCheck::Exact(raw.as_str().into()),
                    ReadPermission::PUBLIC,
                    WritePermission::NONE,
                ),
                BatchOperation::Delete(DeleteOperation {
                    key: key(1, "main"),
                    expected_version: Some(raw.as_str().into()),
                }),
            ] {
                let mut state = seeded.clone();
                let operations = [
                    BatchOperation::Delete(DeleteOperation {
                        key: key(1, "prior_delete"),
                        expected_version: None,
                    }),
                    write(
                        1,
                        "staged",
                        b"new",
                        VersionCheck::MustNotExist,
                        ReadPermission::OWNER,
                        WritePermission::OWNER,
                    ),
                    failed,
                ];
                let error = state
                    .apply_batch(Actor::User(user(1)), &operations)
                    .unwrap_err();
                assert_eq!(error.reason(), "storage_version_mismatch");
                assert_eq!(
                    state, seeded,
                    "failure must restore a preceding deletion and insertion"
                );
            }
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
                    ReadPermission::OWNER,
                    WritePermission::OWNER,
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
                        ReadPermission::PUBLIC,
                        WritePermission::OWNER,
                    ),
                    write(
                        1,
                        "private",
                        b"s",
                        VersionCheck::Any,
                        ReadPermission::NONE,
                        WritePermission::OWNER,
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
                    ReadPermission::OWNER,
                    WritePermission::OWNER,
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
                    VersionCheck::Exact(ContentVersion::from_value(b"stale").into()),
                    ReadPermission::OWNER,
                    WritePermission::OWNER,
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
                    ReadPermission::OWNER,
                    WritePermission::OWNER,
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
                ReadPermission::OWNER,
                WritePermission::OWNER,
            ),
            write(
                1,
                "existing",
                b"bad",
                VersionCheck::Exact(ContentVersion::from_value(b"stale").into()),
                ReadPermission::OWNER,
                WritePermission::OWNER,
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
                ReadPermission::OWNER,
                WritePermission::OWNER,
            ),
            write(
                1,
                "same",
                b"v2",
                VersionCheck::Any,
                ReadPermission::OWNER,
                WritePermission::OWNER,
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
                    expected: VersionCheck::MustNotExist,
                    read_permission: ReadPermission::PUBLIC,
                    write_permission: WritePermission::NONE,
                })],
            )
            .unwrap();
        let attempted = BatchOperation::Write(WriteOperation {
            key: server_key,
            value: b"v2".to_vec(),
            expected: VersionCheck::Exact(ContentVersion::from_value(b"v1").into()),
            read_permission: ReadPermission::PUBLIC,
            write_permission: WritePermission::NONE,
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
                    ReadPermission::OWNER,
                    WritePermission::OWNER,
                )],
            )
            .unwrap();
        let version = ContentVersion::from_value(b"v1");
        let delete = BatchOperation::Delete(DeleteOperation {
            key: key(1, "main"),
            expected_version: Some(version.into()),
        });
        let receipt = state
            .apply_batch(Actor::User(user(1)), &[delete])
            .unwrap()
            .remove(0);
        assert_eq!(receipt.previous_version, Some(version.into()));
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
                version: ContentVersion::from_value(b"v1").into(),
                integrity_digest: integrity(b"corrupt-different-value"),
                collision_witness: Some(CollisionWitness::from_request(b"v1", b"v1").unwrap()),
                read_permission: ReadPermission::OWNER,
                write_permission: WritePermission::OWNER,
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
                    ReadPermission::OWNER,
                    WritePermission::OWNER,
                )],
            )
            .unwrap_err();
        assert_eq!(error.reason(), "storage_collision_witness_binding_mismatch");
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
                    ReadPermission::OWNER,
                    WritePermission::OWNER,
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
                        ReadPermission::OWNER,
                        WritePermission::OWNER,
                    )]
                )
                .unwrap_err()
                .reason(),
            "storage_object_already_exists"
        );
    }

    #[test]
    fn create_only_occ_precedence_preserves_owner_authority_and_batch_state() {
        let current = ContentVersion::from_value(b"v1");
        let stale = ContentVersion::from_value(b"stale");
        for acl in [WritePermission::NONE, WritePermission::OWNER] {
            let mut seeded = StorageState::default();
            seeded
                .apply_batch(
                    Actor::Server,
                    &[write(
                        1,
                        "main",
                        b"v1",
                        VersionCheck::Any,
                        ReadPermission::OWNER,
                        acl,
                    )],
                )
                .unwrap();
            let permission_failure = (
                StableCode::PermissionDenied,
                "storage_write_permission_denied",
            );
            let denied = Some(permission_failure);
            let exists = Some((StableCode::AlreadyExists, "storage_object_already_exists"));
            let stale_error = Some((StableCode::FailedPrecondition, "storage_version_mismatch"));
            for (actor, expected, failure) in [
                (Actor::User(user(1)), VersionCheck::MustNotExist, exists),
                (Actor::Server, VersionCheck::MustNotExist, exists),
                (
                    Actor::User(user(1)),
                    VersionCheck::Any,
                    if acl == WritePermission::NONE {
                        denied
                    } else {
                        None
                    },
                ),
                (
                    Actor::User(user(1)),
                    VersionCheck::Exact(current.into()),
                    if acl == WritePermission::NONE {
                        denied
                    } else {
                        None
                    },
                ),
                (
                    Actor::User(user(1)),
                    VersionCheck::Exact(stale.into()),
                    if acl == WritePermission::NONE {
                        denied
                    } else {
                        stale_error
                    },
                ),
                (Actor::Server, VersionCheck::Any, None),
                (Actor::Server, VersionCheck::Exact(current.into()), None),
                (
                    Actor::Server,
                    VersionCheck::Exact(stale.into()),
                    stale_error,
                ),
            ] {
                let mut state = seeded.clone();
                let attempted = write(
                    1,
                    "main",
                    b"v2",
                    expected,
                    ReadPermission::PUBLIC,
                    WritePermission::OWNER,
                );
                let outcome = state.apply_batch(actor, std::slice::from_ref(&attempted));
                if let Some((code, reason)) = failure {
                    let error = outcome.unwrap_err();
                    assert_eq!((error.code(), error.reason()), (code, reason));
                    assert_eq!(state, seeded);
                    // A preceding permitted insert must roll back with the rejection.
                    let error = state
                        .apply_batch(
                            actor,
                            &[
                                write(
                                    1,
                                    "staged",
                                    b"new",
                                    VersionCheck::MustNotExist,
                                    ReadPermission::OWNER,
                                    WritePermission::OWNER,
                                ),
                                attempted,
                            ],
                        )
                        .unwrap_err();
                    assert_eq!((error.code(), error.reason()), (code, reason));
                    assert_eq!(state, seeded);
                } else {
                    let receipt = outcome.unwrap();
                    assert_eq!(receipt[0].previous_version, Some(current.into()));
                    assert_eq!(
                        receipt[0].current_version,
                        Some(ContentVersion::from_value(b"v2"))
                    );
                    let object = state.read(Actor::Server, &key(1, "main")).unwrap();
                    assert_eq!(object.value, b"v2");
                    assert_eq!(object.read_permission, ReadPermission::PUBLIC);
                    assert_eq!(object.write_permission, WritePermission::OWNER);
                }
            }
            for actor in [Actor::User(user(2)), Actor::User(user(0))] {
                for expected in [
                    VersionCheck::Any,
                    VersionCheck::MustNotExist,
                    VersionCheck::Exact(current.into()),
                    VersionCheck::Exact(stale.into()),
                ] {
                    for name in ["main", "missing"] {
                        let mut state = seeded.clone();
                        let error = state
                            .apply_batch(
                                actor,
                                &[write(
                                    1,
                                    name,
                                    b"v2",
                                    expected.clone(),
                                    ReadPermission::PUBLIC,
                                    WritePermission::OWNER,
                                )],
                            )
                            .unwrap_err();
                        assert_eq!((error.code(), error.reason()), permission_failure);
                        assert_eq!(state, seeded);
                    }
                }
            }
        }

        // Global rows remain server-owned, including the create-only condition.
        let mut global = StorageState::default();
        global
            .apply_batch(
                Actor::Server,
                &[write(
                    0,
                    "global",
                    b"v1",
                    VersionCheck::MustNotExist,
                    ReadPermission::PUBLIC,
                    WritePermission::NONE,
                )],
            )
            .unwrap();
        let before = global.clone();
        for actor in [Actor::User(user(1)), Actor::User(user(0))] {
            for name in ["global", "missing"] {
                let error = global
                    .apply_batch(
                        actor,
                        &[write(
                            0,
                            name,
                            b"v2",
                            VersionCheck::MustNotExist,
                            ReadPermission::PUBLIC,
                            WritePermission::OWNER,
                        )],
                    )
                    .unwrap_err();
                assert_eq!(error.code(), StableCode::PermissionDenied);
                assert_eq!(global, before);
            }
        }
        let error = global
            .apply_batch(
                Actor::Server,
                &[write(
                    0,
                    "global",
                    b"v2",
                    VersionCheck::MustNotExist,
                    ReadPermission::PUBLIC,
                    WritePermission::OWNER,
                )],
            )
            .unwrap_err();
        assert_eq!(error.code(), StableCode::AlreadyExists);
        assert_eq!(global, before);
    }
}
