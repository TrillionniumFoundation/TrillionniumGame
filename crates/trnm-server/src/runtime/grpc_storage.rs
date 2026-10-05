//! Native protobuf read adapter over the existing admitted storage repository.
//! Pinned Nakama d4d92f93 api_storage.go/core_storage.go, Apache-2.0.
//! No HTTP/JSON round trip, second store, retries, hooks or fabricated timestamps.
use std::collections::BTreeMap;

use prost::Message;
use tonic::Status;
use trnm_contracts::UserId;
use trnm_persistence_pg::{StorageActor, StorageObjectKey, StorageTimestamp, StoredStorageObject};

use super::super::super::app::Repository;
use super::super::super::legacy_uuid::{parse_uuid, uuid_string};
use super::super::generated::google::protobuf::Timestamp;
use super::super::generated::nakama::api::{
    ReadStorageObjectsRequest, StorageObject, StorageObjects,
};
use super::MAX_RESPONSE_BYTES;

const MAX_BATCH: usize = 100;
const INVALID_KEYS: &str = "Invalid collection or key value supplied. They must be set.";
const INVALID_USER: &str = "Invalid user ID - make sure user ID is a valid UUID.";
const READ_FAILURE: &str = "Error reading storage objects.";

pub(super) fn read_objects<R: Repository>(
    repository: &mut R,
    input: ReadStorageObjectsRequest,
    user: UserId,
) -> Result<StorageObjects, Status> {
    // Caller has authenticated before business decoding. Retain a fail-closed
    // assertion against accidentally using server-actor/global user authority.
    if user.is_zero() {
        return Err(Status::unauthenticated("Auth token invalid"));
    }
    if input.object_ids.len() > MAX_BATCH {
        return Err(Status::resource_exhausted(
            "Storage batch must contain at most 100 objects.",
        ));
    }
    let mut keys = Vec::with_capacity(input.object_ids.len());
    for object in input.object_ids {
        // Match pinned per-object error precedence and validate the whole batch
        // before any native business query. Omitted owner is global, not caller.
        if object.collection.is_empty() || object.key.is_empty() {
            return Err(Status::invalid_argument(INVALID_KEYS));
        }
        let owner = if object.user_id.is_empty() {
            UserId::new([0; 16])
        } else {
            parse_uuid(&object.user_id)
                .filter(|owner| !owner.is_zero())
                .ok_or_else(|| Status::invalid_argument(INVALID_USER))?
        };
        keys.push(
            StorageObjectKey::new_nakama(object.collection, object.key, owner).map_err(|_| {
                Status::resource_exhausted(
                    "Storage collection or key exceeds the local 128-character limit.",
                )
            })?,
        );
    }
    if keys.is_empty() {
        return Ok(StorageObjects {
            objects: Vec::new(),
        });
    }
    // The production path is the existing deadline-bound native read-only
    // snapshot and import/writer/schema fences. No caller retry or sort here.
    let objects = repository
        .read_storage_objects(StorageActor::User(user), &keys)
        .map_err(|_| Status::internal(READ_FAILURE))?;
    encode_objects(&keys, objects, user)
}

fn encode_objects(
    keys: &[StorageObjectKey],
    objects: Vec<StoredStorageObject>,
    user: UserId,
) -> Result<StorageObjects, Status> {
    if objects.len() > keys.len() {
        return Err(Status::internal(READ_FAILURE));
    }
    let mut remaining = BTreeMap::new();
    for key in keys {
        *remaining.entry(key).or_insert(0_usize) += 1;
    }
    let mut encoded = Vec::with_capacity(objects.len());
    let mut bytes = 0_usize;
    for stored in objects {
        let object = stored.object;
        let count = remaining
            .get_mut(&object.key)
            .filter(|count| **count > 0)
            .ok_or_else(|| Status::internal(READ_FAILURE))?;
        *count -= 1;
        if !object
            .read_permission
            .allows_batch_read(object.key.user_id() == user)
        {
            return Err(Status::internal(READ_FAILURE));
        }
        object
            .verify_integrity()
            .map_err(|_| Status::internal(READ_FAILURE))?;
        // Unknown historical timestamps cannot be invented as epoch values.
        let create_time = Some(project_time(stored.times.create)?);
        let update_time = Some(project_time(stored.times.update)?);
        let result = StorageObject {
            collection: object.key.collection().to_owned(),
            key: object.key.key().to_owned(),
            user_id: uuid_string(object.key.user_id()),
            value: String::from_utf8(object.value).map_err(|_| Status::internal(READ_FAILURE))?,
            version: object.version.as_str().to_owned(),
            permission_read: object.read_permission.as_i32(),
            permission_write: object.write_permission.as_i32(),
            create_time,
            update_time,
        };
        // Account for repeated message tag and length prefix before tonic makes
        // an encoding buffer. Moving DB bytes avoids a second large value copy.
        let length = result.encoded_len();
        bytes = bytes
            .checked_add(1 + prost::length_delimiter_len(length))
            .and_then(|total| total.checked_add(length))
            .filter(|total| *total <= MAX_RESPONSE_BYTES)
            .ok_or_else(|| Status::resource_exhausted(READ_FAILURE))?;
        encoded.push(result);
    }
    Ok(StorageObjects { objects: encoded })
}

fn project_time(value: Option<StorageTimestamp>) -> Result<Timestamp, Status> {
    let value = value.ok_or_else(|| Status::internal(READ_FAILURE))?;
    value
        .validate()
        .map_err(|_| Status::internal(READ_FAILURE))?;
    // Pinned Get/Read projection uses Go Time.Unix(), i.e. floor seconds,
    // dropping fractions. Native stored precision remains unchanged.
    Ok(Timestamp {
        seconds: value.seconds,
        nanos: 0,
    })
}

#[cfg(test)]
#[path = "grpc_storage_tests.rs"]
mod tests;
