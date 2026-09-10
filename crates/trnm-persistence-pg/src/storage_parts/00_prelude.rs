use std::collections::{BTreeMap, BTreeSet};

use postgres::{IsolationLevel, Row, Transaction};
use trnm_contracts::{DomainError, RetryClass, StableCode, UserId};
use trnm_storage_core::{
    Actor, BatchOperation, ContentVersion, DeleteOperation, IntegrityDigest, MutationReceipt,
    ReadPermission, StorageObject, StorageObjectKey, VersionCheck, WriteOperation, WritePermission,
};

use super::{
    data_loss, decode_digest, decode_id16, error, invalid, map_postgres_error, to_i64, PgRepository,
};

const MAX_BATCH_OPERATIONS: usize = 100;
const MAX_LIST_LIMIT: usize = 100;
const MAX_VALUE_BYTES: usize = 1024 * 1024;
const MAX_COLLECTION_BYTES: usize = 128;

/// In-process storage-list continuation bound to authorization and query scope.
///
/// The tuple carries `(actor, optional owner scope, last key)`. Public wire
/// adapters must encode and authenticate every element before accepting a
/// continuation from an untrusted client; a bare `StorageObjectKey` is not a
/// valid cursor.
pub type StorageListCursor = (Actor, Option<UserId>, StorageObjectKey);

/// One bounded visible page and its optional scope-bound continuation.
pub type StorageListPage = (Vec<StorageObject>, Option<StorageListCursor>);
