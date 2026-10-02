use std::collections::{BTreeMap, BTreeSet};

use postgres::fallible_iterator::FallibleIterator;
use postgres::types::ToSql;
use postgres::{IsolationLevel, Row, Transaction};
use trnm_contracts::{DomainError, RetryClass, StableCode, UserId};
use trnm_storage_core::{
    Actor, BatchOperation, CollisionWitness, ContentVersion, DeleteOperation, IntegrityDigest,
    MutationReceipt, PublicVersion, ReadPermission, StorageObject, StorageObjectKey, VersionCheck,
    WriteOperation, WritePermission,
};

use super::{
    data_loss, decode_digest, decode_id16, error, invalid, map_postgres_error, to_i64,
    DatabaseProfile, PgRepository, StorageTimes, StoredStorageClientListPage,
    StoredStorageMutationReceipt, StoredStorageObject,
};

const MAX_BATCH_OPERATIONS: usize = 100;
const MAX_LIST_LIMIT: usize = 100;
const MAX_VALUE_BYTES: usize = 1024 * 1024;
const MAX_NATIVE_VALUE_BYTES: usize = 16 * 1024 * 1024;
// This bounds accumulated native payloads. The HTTP adapter separately bounds
// the actual encoded response, including JSON escaping and metadata.
const MAX_RESULT_VALUE_BYTES: usize = 32 * 1024 * 1024;
const MAX_COLLECTION_BYTES: usize = 128;
const MAX_CLIENT_LIST_COLLECTION_BYTES: usize = 4096;
const MAX_LIST_POSITION_KEY_BYTES: usize = 4096;

/// An untrusted Nakama client-list offset, without actor or query-scope binding.
/// Each query applies the current actor's ACL independently of these fields.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StorageListPosition {
    pub key: String,
    pub user_id: UserId,
    pub read: i32,
}

/// One bounded Nakama client-list page and its last returned-object offset.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StorageClientListPage {
    pub objects: Vec<StorageObject>,
    pub next: Option<StorageListPosition>,
}

/// In-process storage-list continuation bound to authorization and query scope.
///
/// The tuple carries `(actor, optional owner scope, last key)`. Public wire
/// adapters exposing this typed profile must encode and authenticate every
/// element before accepting an untrusted continuation. The separate Nakama
/// client-list profile uses an original unsigned offset and independently
/// revalidates the principal's SQL ACL; it does not expose this typed cursor.
/// A bare `StorageObjectKey` is not a valid cursor for this internal profile.
pub type StorageListCursor = (Actor, Option<UserId>, StorageObjectKey);

/// One bounded visible page and its optional scope-bound continuation.
pub type StorageListPage = (Vec<StorageObject>, Option<StorageListCursor>);
