//! Nakama-profile storage list source candidate. Original unsigned gob cursors
//! are untrusted offsets; only the authenticated actor selects the SQL ACL.

use std::collections::BTreeSet;

use trnm_contracts::UserId;
use trnm_persistence_pg::{
    ReadPermission, StorageActor, StorageClientListPage, StorageListPosition,
};

use super::app::{Repository, STORAGE_LIST_ROUTES};
use super::http::{Request, Response};
use super::storage_api::{encode_storage_object, gateway_error};
use super::storage_cursor::{decode_cursor, encode_cursor};
use super::storage_list_query::{self, ListQuery};

pub(super) fn is_list_target(target: &str) -> bool {
    let Some(path) = storage_list_query::routing_path(target) else {
        return false;
    };
    STORAGE_LIST_ROUTES.iter().any(|(_, template)| {
        let Some(prefix) = template.split('{').next() else {
            return false;
        };
        let Some(suffix) = path.strip_prefix(prefix) else {
            return false;
        };
        suffix.split('/').count() == template.matches('{').count()
    })
}

pub(super) fn handle<R: Repository>(
    repository: &mut R,
    request: &Request,
    user: UserId,
) -> Response {
    if user.is_zero() {
        return gateway_error(401, 16, "Auth token invalid");
    }
    let query = match storage_list_query::parse(&request.target) {
        Ok(query) => query,
        Err(error) => return gateway_error(error.0, error.1, error.2),
    };
    let after = if query.cursor.is_empty() {
        None
    } else {
        match decode_cursor(&query.cursor) {
            Ok(position) => Some(position),
            Err(()) => return gateway_error(400, 3, "Malformed cursor was used."),
        }
    };
    match repository.list_storage_objects_nakama(
        StorageActor::User(user),
        &query.collection,
        query.owner,
        after.as_ref(),
        query.limit,
    ) {
        Ok(page) => encode_page(&query, user, page),
        Err(_) => internal_error(),
    }
}

fn encode_page(query: &ListQuery, user: UserId, page: StorageClientListPage) -> Response {
    if page.objects.len() > query.limit {
        return internal_error();
    }
    let mut keys = BTreeSet::new();
    let mut objects = Vec::with_capacity(page.objects.len());
    for object in &page.objects {
        let visible = match query.owner {
            None => object.read_permission == ReadPermission::Public,
            Some(owner) if owner == user => {
                object.key.user_id() == owner && object.read_permission != ReadPermission::None
            }
            Some(owner) => {
                object.key.user_id() == owner && object.read_permission == ReadPermission::Public
            }
        };
        if !visible
            || object.key.collection() != query.collection
            || !keys.insert(object.key.clone())
        {
            return internal_error();
        }
        match encode_storage_object(object) {
            Some(encoded) => objects.push(encoded),
            None => return internal_error(),
        }
    }
    let mut fields = Vec::new();
    if !objects.is_empty() {
        fields.push(format!("\"objects\":[{}]", objects.join(",")));
    }
    if let Some(next) = page.next {
        if page.objects.len() != query.limit
            || page.objects.last().is_none_or(|object| {
                next != StorageListPosition {
                    key: object.key.key().to_owned(),
                    user_id: object.key.user_id(),
                    read: i32::from(object.read_permission as u8),
                }
            })
        {
            return internal_error();
        }
        let cursor = match encode_cursor(&next) {
            Ok(cursor) => cursor,
            Err(()) => return internal_error(),
        };
        // Preserve upstream's literal equal-cursor guard, including when an
        // external caller supplied an equivalent but differently encoded gob.
        if cursor != query.cursor {
            fields.push(format!("\"cursor\":{}", serde_json::Value::from(cursor)));
        }
    }
    Response::json(200, format!("{{{}}}", fields.join(",")))
}

fn internal_error() -> Response {
    gateway_error(500, 13, "Error listing storage objects.")
}
