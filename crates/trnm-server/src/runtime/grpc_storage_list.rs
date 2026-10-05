//! Native protobuf list adapter over the canonical native SQL and gob offset.
//! Pinned Nakama d4d92f93 api_storage.go/core_storage.go, Apache-2.0.
//! Cursor fields are untrusted offsets; only the access principal selects ACL.
use std::collections::BTreeSet;

use prost::Message;
use tonic::Status;
use trnm_contracts::{StableCode, UserId};
use trnm_persistence_pg::{
    StorageActor, StorageListPosition, StorageTimestamp, StoredStorageClientListPage,
};

use crate::runtime::app::Repository;
use crate::runtime::legacy_uuid::{parse_uuid, uuid_string};
use crate::runtime::storage_cursor::{decode_cursor, encode_cursor};

use super::super::generated::google::protobuf::Timestamp;
use super::super::generated::nakama::api::{
    ListStorageObjectsRequest, StorageObject, StorageObjectList,
};
use super::MAX_RESPONSE_BYTES;

const MAX_COLLECTION_BYTES: usize = 4096;
const INVALID_LIMIT: &str = "Invalid limit - limit must be between 1 and 100.";
const INVALID_USER: &str = "Invalid user ID - make sure user ID is a valid UUID.";
const MALFORMED_CURSOR: &str = "Malformed cursor was used.";
const LIST_FAILURE: &str = "Error listing storage objects.";

pub(super) fn list_objects<R: Repository>(
    repository: &mut R,
    input: ListStorageObjectsRequest,
    user: UserId,
) -> Result<StorageObjectList, Status> {
    if user.is_zero() {
        return Err(Status::unauthenticated("Auth token invalid"));
    }
    // Match pinned API precedence: optional limit, owner UUID, then cursor.
    // Empty collection is valid. Explicit zero UUID is a foreign-owner filter.
    let limit = input.limit.as_ref().map_or(1, |value| value.value);
    if !(1..=100).contains(&limit) {
        return Err(Status::invalid_argument(INVALID_LIMIT));
    }
    let owner = if input.user_id.is_empty() {
        None
    } else {
        Some(parse_uuid(&input.user_id).ok_or_else(|| Status::invalid_argument(INVALID_USER))?)
    };
    let after = if input.cursor.is_empty() {
        None
    } else {
        Some(
            decode_cursor(&input.cursor)
                .map_err(|()| Status::invalid_argument(MALFORMED_CURSOR))?,
        )
    };
    // Local bounded profile difference, not an upstream API restriction.
    if input.collection.len() > MAX_COLLECTION_BYTES {
        return Err(Status::resource_exhausted(LIST_FAILURE));
    }
    let page = repository
        .list_storage_objects_nakama(
            StorageActor::User(user),
            &input.collection,
            owner,
            after.as_ref(),
            limit as usize,
        )
        .map_err(|error| {
            if error.code() == StableCode::ResourceExhausted {
                Status::resource_exhausted(LIST_FAILURE)
            } else {
                Status::internal(LIST_FAILURE)
            }
        })?;
    encode_page(input, owner, user, limit as usize, page)
}

fn encode_page(
    input: ListStorageObjectsRequest,
    owner: Option<UserId>,
    user: UserId,
    limit: usize,
    page: StoredStorageClientListPage,
) -> Result<StorageObjectList, Status> {
    if page.objects.len() > limit {
        return Err(Status::internal(LIST_FAILURE));
    }
    let cursor = if let Some(next) = page.next {
        if page.objects.len() != limit
            || page.objects.last().is_none_or(|stored| {
                let object = &stored.object;
                next != StorageListPosition {
                    key: object.key.key().to_owned(),
                    user_id: object.key.user_id(),
                    read: object.read_permission.as_i32(),
                }
            })
        {
            return Err(Status::internal(LIST_FAILURE));
        }
        let encoded = encode_cursor(&next).map_err(|()| Status::internal(LIST_FAILURE))?;
        // Preserve the upstream literal guard, never normalize cursor equality.
        if encoded == input.cursor {
            String::new()
        } else {
            encoded
        }
    } else {
        String::new()
    };
    let mut result = StorageObjectList {
        objects: Vec::with_capacity(page.objects.len()),
        cursor,
    };
    let mut bytes = result.encoded_len();
    let mut keys = BTreeSet::new();
    for stored in page.objects {
        let object = stored.object;
        let visible = match owner {
            None => object.read_permission.allows_public_listing(),
            Some(owner) if owner == user => {
                object.key.user_id() == owner && object.read_permission.allows_owner_listing()
            }
            Some(owner) => {
                object.key.user_id() == owner && object.read_permission.allows_foreign_listing()
            }
        };
        if !visible
            || object.key.collection() != input.collection
            || !keys.insert(object.key.clone())
        {
            return Err(Status::internal(LIST_FAILURE));
        }
        object
            .verify_integrity()
            .map_err(|_| Status::internal(LIST_FAILURE))?;
        let encoded = StorageObject {
            collection: object.key.collection().to_owned(),
            key: object.key.key().to_owned(),
            user_id: uuid_string(object.key.user_id()),
            value: String::from_utf8(object.value).map_err(|_| Status::internal(LIST_FAILURE))?,
            version: object.version.as_str().to_owned(),
            permission_read: object.read_permission.as_i32(),
            permission_write: object.write_permission.as_i32(),
            create_time: Some(project_time(stored.times.create)?),
            update_time: Some(project_time(stored.times.update)?),
        };
        let length = encoded.encoded_len();
        bytes = bytes
            .checked_add(1 + prost::length_delimiter_len(length))
            .and_then(|total| total.checked_add(length))
            .filter(|total| *total <= MAX_RESPONSE_BYTES)
            .ok_or_else(|| Status::resource_exhausted(LIST_FAILURE))?;
        // Preserve database SQL text order; no Rust sort or deduplication.
        result.objects.push(encoded);
    }
    Ok(result)
}

fn project_time(value: Option<StorageTimestamp>) -> Result<Timestamp, Status> {
    let value = value.ok_or_else(|| Status::internal(LIST_FAILURE))?;
    value
        .validate()
        .map_err(|_| Status::internal(LIST_FAILURE))?;
    // Pinned list uses Time.Unix(): floor seconds, unlike write ACK precision.
    Ok(Timestamp {
        seconds: value.seconds,
        nanos: 0,
    })
}

#[cfg(test)]
#[path = "grpc_storage_list_tests.rs"]
mod tests;
