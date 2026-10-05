//! Native protobuf storage mutations over the existing admitted transaction path.
//! Pinned Nakama d4d92f93 api_storage.go/core_storage.go, Apache-2.0.
//! Repository success follows whole-batch commit. Projection/delivery failure
//! after that point never permits replay, compensation or an assertion of rollback.
use std::time::{SystemTime, UNIX_EPOCH};

use prost::Message;
use serde_json::value::RawValue;
use tonic::Status;
use trnm_contracts::{DomainError, StableCode, UserId};
use trnm_persistence_pg::{
    ContentVersion, ReadPermission, StorageActor, StorageBatchOperation as Operation,
    StorageDeleteOperation, StorageNakamaBatchKind as Kind, StorageObjectKey, StorageTimestamp,
    StorageWriteOperation, StoredStorageMutationReceipt, VersionCheck, WritePermission,
};

use super::super::super::app::Repository;
use super::super::super::legacy_uuid::uuid_string;
use super::super::generated::google::protobuf::{Empty, Timestamp};
use super::super::generated::nakama::api::{
    DeleteStorageObjectsRequest, StorageObjectAck, StorageObjectAcks, WriteStorageObjectsRequest,
};
use super::MAX_RESPONSE_BYTES;

const MAX_BATCH: usize = 100;
const MAX_VALUE_BYTES: usize = 1024 * 1024;
const MAX_JSON_DEPTH: usize = 10_000;
const INVALID_KEYS: &str = "Invalid collection or key value supplied. They must be set.";
const INVALID_READ: &str = "Invalid Read permission supplied. It must be either 0, 1 or 2.";
const INVALID_WRITE: &str = "Invalid Write permission supplied. It must be either 0 or 1.";
const INVALID_VALUE: &str = "Value must be a JSON object.";
const WRITE_FAILURE: &str = "Error writing storage objects.";
const DELETE_FAILURE: &str = "Error deleting storage objects.";

pub(super) fn write_objects<R: Repository>(
    repository: &mut R,
    input: WriteStorageObjectsRequest,
    user: UserId,
) -> Result<StorageObjectAcks, Status> {
    admit(user, input.objects.len())?;
    let mut operations = Vec::with_capacity(input.objects.len());
    // Validate the entire batch before native access. Never parse/re-serialize
    // the value or normalize an opaque OCC condition. Defaults depend on wrapper
    // presence: absent is 1; present zero is 0.
    for object in input.objects {
        if object.collection.is_empty() || object.key.is_empty() || object.value.is_empty() {
            return Err(Status::invalid_argument(INVALID_KEYS));
        }
        let read_permission = match object.permission_read.map_or(1, |v| v.value) {
            0 => ReadPermission::NONE,
            1 => ReadPermission::OWNER,
            2 => ReadPermission::PUBLIC,
            _ => return Err(Status::invalid_argument(INVALID_READ)),
        };
        let write_permission = match object.permission_write.map_or(1, |v| v.value) {
            0 => WritePermission::NONE,
            1 => WritePermission::OWNER,
            _ => return Err(Status::invalid_argument(INVALID_WRITE)),
        };
        if object.value.len() > MAX_VALUE_BYTES {
            return Err(Status::resource_exhausted(
                "Storage value exceeds the 1048576 byte limit.",
            ));
        }
        if object
            .value
            .bytes()
            .find(|byte| !matches!(byte, b' ' | b'\t' | b'\r' | b'\n'))
            != Some(b'{')
            || !within_json_depth(&object.value)
            || serde_json::from_str::<Box<RawValue>>(&object.value).is_err()
        {
            return Err(Status::invalid_argument(INVALID_VALUE));
        }
        let key = key(object.collection, object.key, user)?;
        let expected = match object.version.as_str() {
            "" => VersionCheck::Any,
            "*" => VersionCheck::MustNotExist,
            _ => VersionCheck::Exact(object.version.into()),
        };
        operations.push(Operation::Write(StorageWriteOperation {
            key,
            value: object.value.into_bytes(),
            expected,
            read_permission,
            write_permission,
        }));
    }
    if operations.is_empty() {
        return Ok(StorageObjectAcks { acks: Vec::new() });
    }
    let receipts = repository
        .apply_storage_batch_nakama(
            StorageActor::User(user),
            &operations,
            now_ms(WRITE_FAILURE)?,
            Kind::Write,
        )
        .map_err(|error| mutation_error(error, Kind::Write))?;
    encode_acks(&operations, receipts)
}

pub(super) fn delete_objects<R: Repository>(
    repository: &mut R,
    input: DeleteStorageObjectsRequest,
    user: UserId,
) -> Result<Empty, Status> {
    admit(user, input.object_ids.len())?;
    let mut operations = Vec::with_capacity(input.object_ids.len());
    for object in input.object_ids {
        if object.collection.is_empty() || object.key.is_empty() {
            return Err(Status::invalid_argument(INVALID_KEYS));
        }
        operations.push(Operation::Delete(StorageDeleteOperation {
            key: key(object.collection, object.key, user)?,
            // Every nonempty delete condition, including '*', is exact.
            expected_version: (!object.version.is_empty()).then(|| object.version.into()),
        }));
    }
    if operations.is_empty() {
        return Ok(Empty {});
    }
    let receipts = repository
        .apply_storage_batch_nakama(
            StorageActor::User(user),
            &operations,
            now_ms(DELETE_FAILURE)?,
            Kind::Delete,
        )
        .map_err(|error| mutation_error(error, Kind::Delete))?;
    if receipts.len() != operations.len()
        || operations.iter().zip(&receipts).any(|(operation, stored)| {
            let Operation::Delete(delete) = operation else {
                return true;
            };
            let receipt = &stored.receipt;
            receipt.key != delete.key
                || receipt.current_version.is_some()
                || receipt.previous_version.is_none()
                || delete.expected_version.as_ref().is_some_and(|expected| {
                    receipt.previous_version.as_ref().map(|v| v.as_str()) != Some(expected.as_str())
                })
        })
    {
        return Err(Status::internal(DELETE_FAILURE));
    }
    Ok(Empty {})
}

fn admit(user: UserId, count: usize) -> Result<(), Status> {
    if user.is_zero() {
        return Err(Status::unauthenticated("Auth token invalid"));
    }
    if count > MAX_BATCH {
        return Err(Status::resource_exhausted(
            "Storage batch must contain at most 100 objects.",
        ));
    }
    Ok(())
}

fn key(collection: String, key: String, user: UserId) -> Result<StorageObjectKey, Status> {
    // Nakama client API checks presence, not the stricter internal identifier
    // grammar. Keep the native column's bounded Unicode-character domain.
    StorageObjectKey::new_nakama(collection, key, user).map_err(|_| {
        Status::resource_exhausted(
            "Storage collection or key exceeds the local 128-character limit.",
        )
    })
}

/// Go 1.26.5 encoding/json.Valid caps total object/array nesting at 10,000.
/// RawValue preserves the required raw lexemes, but its iterative syntax scanner
/// has no depth ceiling. This constant-space preflight only bounds containers;
/// RawValue still validates grammar, escapes and matching delimiters. Delimiters
/// inside strings, including after escaped quotes/backslashes, are not nesting.
fn within_json_depth(value: &str) -> bool {
    let mut depth = 0_usize;
    let mut quoted = false;
    let mut escaped = false;
    for byte in value.bytes() {
        if quoted {
            if escaped {
                escaped = false;
            } else if byte == b'\\' {
                escaped = true;
            } else if byte == b'"' {
                quoted = false;
            }
        } else {
            match byte {
                b'"' => quoted = true,
                b'{' | b'[' => {
                    depth += 1;
                    if depth > MAX_JSON_DEPTH {
                        return false;
                    }
                }
                b'}' | b']' => depth = depth.saturating_sub(1),
                _ => {}
            }
        }
    }
    true
}

fn now_ms(message: &'static str) -> Result<u64, Status> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .ok()
        .and_then(|duration| u64::try_from(duration.as_millis()).ok())
        .ok_or_else(|| Status::internal(message))
}

fn encode_acks(
    operations: &[Operation],
    receipts: Vec<StoredStorageMutationReceipt>,
) -> Result<StorageObjectAcks, Status> {
    if operations.len() != receipts.len() {
        return Err(Status::internal(WRITE_FAILURE));
    }
    let mut acks = Vec::with_capacity(receipts.len());
    let mut bytes = 0_usize;
    for (operation, stored) in operations.iter().zip(receipts) {
        let Operation::Write(write) = operation else {
            return Err(Status::internal(WRITE_FAILURE));
        };
        let receipt = stored.receipt;
        let version = receipt
            .current_version
            .ok_or_else(|| Status::internal(WRITE_FAILURE))?;
        let previous_matches = match &write.expected {
            VersionCheck::Any => true,
            VersionCheck::MustNotExist => receipt.previous_version.is_none(),
            VersionCheck::Exact(expected) => receipt
                .previous_version
                .as_ref()
                .is_some_and(|previous| previous.as_str() == expected.as_str()),
        };
        if receipt.key != write.key
            || version != ContentVersion::from_value(&write.value)
            || !previous_matches
            || (receipt.previous_version.is_none() && stored.times.create != stored.times.update)
        {
            return Err(Status::internal(WRITE_FAILURE));
        }
        // Upstream ACK uses timestamppb.New, retaining database precision. Unknown
        // historical times fail closed after commit, not invented or backfilled.
        let ack = StorageObjectAck {
            collection: receipt.key.collection().to_owned(),
            key: receipt.key.key().to_owned(),
            version: version.as_str().to_owned(),
            user_id: uuid_string(receipt.key.user_id()),
            create_time: Some(precise_time(stored.times.create)?),
            update_time: Some(precise_time(stored.times.update)?),
        };
        let length = ack.encoded_len();
        bytes = bytes
            .checked_add(1 + prost::length_delimiter_len(length))
            .and_then(|total| total.checked_add(length))
            .filter(|total| *total <= MAX_RESPONSE_BYTES)
            .ok_or_else(|| Status::resource_exhausted(WRITE_FAILURE))?;
        acks.push(ack);
    }
    Ok(StorageObjectAcks { acks })
}

fn precise_time(value: Option<StorageTimestamp>) -> Result<Timestamp, Status> {
    let value = value.ok_or_else(|| Status::internal(WRITE_FAILURE))?;
    value
        .validate()
        .map_err(|_| Status::internal(WRITE_FAILURE))?;
    Ok(Timestamp {
        seconds: value.seconds,
        nanos: value.nanos as i32,
    })
}

fn mutation_error(error: DomainError, kind: Kind) -> Status {
    match (kind, error.code(), error.reason()) {
        (Kind::Write, StableCode::AlreadyExists, "storage_object_already_exists")
        | (Kind::Write, StableCode::FailedPrecondition, "storage_version_mismatch") => {
            Status::invalid_argument("Storage write rejected - version check failed.")
        }
        (Kind::Write, StableCode::PermissionDenied, "storage_write_permission_denied") => {
            Status::invalid_argument("Storage write rejected - permission denied.")
        }
        (Kind::Delete, StableCode::NotFound, "storage_object_not_found")
        | (Kind::Delete, StableCode::FailedPrecondition, "storage_version_mismatch")
        | (Kind::Delete, StableCode::PermissionDenied, "storage_write_permission_denied") => {
            Status::invalid_argument(
                "Storage delete rejected - not found, version check failed, or permission denied.",
            )
        }
        (_, StableCode::ResourceExhausted, _) => Status::resource_exhausted(match kind {
            Kind::Write => WRITE_FAILURE,
            Kind::Delete => DELETE_FAILURE,
        }),
        // StableCode alone is not a semantic rejection: FailedPrecondition also
        // covers schema/import admission and native foreign-key errors. Only
        // the above storage-specific code/reason pairs denote OCC/ACL failures.
        // The native insert-only path already maps its unique violation to
        // storage_object_already_exists; arbitrary SQL uniqueness stays Internal.
        // None authorizes retry or proves that no mutation committed.
        _ => Status::internal(match kind {
            Kind::Write => WRITE_FAILURE,
            Kind::Delete => DELETE_FAILURE,
        }),
    }
}

#[cfg(test)]
#[path = "grpc_storage_mutation_tests.rs"]
mod tests;
