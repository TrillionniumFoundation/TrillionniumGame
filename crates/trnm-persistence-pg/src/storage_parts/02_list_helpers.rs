fn validate_list_request(
    actor: Actor,
    collection: &str,
    owner: Option<UserId>,
    after: Option<&StorageListCursor>,
    limit: usize,
) -> Result<(), DomainError> {
    if collection.is_empty()
        || collection.len() > MAX_COLLECTION_BYTES
        || collection.chars().any(char::is_control)
        || collection.starts_with('.')
    {
        return Err(invalid("invalid_storage_collection"));
    }
    if limit == 0 || limit > MAX_LIST_LIMIT {
        return Err(invalid("invalid_storage_list_limit"));
    }
    validate_actor_and_owner(actor, owner)?;
    if let Some(cursor) = after {
        if cursor.0 != actor
            || cursor.1 != owner
            || cursor.2.collection() != collection
            || owner.is_some_and(|user| user != cursor.2.user_id())
        {
            return Err(invalid("storage_cursor_scope_mismatch"));
        }
    }
    Ok(())
}

fn validate_actor_and_owner(actor: Actor, owner: Option<UserId>) -> Result<(), DomainError> {
    if matches!(actor, Actor::User(user) if user.is_zero()) {
        return Err(invalid("invalid_storage_actor"));
    }
    if owner.is_some_and(|user| user.is_zero()) {
        return Err(invalid("invalid_storage_owner"));
    }
    Ok(())
}

/// Return the profile-specific query that implements one canonical ordering:
/// lexicographic UTF-8 bytes, followed by the raw 16-byte user id.
///
/// Collection scope equality uses the same exact UTF-8-byte representation as
/// the object-key continuation and ordering boundary. Rust `String::cmp` is
/// byte-equivalent for valid UTF-8. CockroachDB uses explicit `::BYTES` casts;
/// PostgreSQL uses `pg_catalog.convert_to(..., 'UTF8')`. Ambient locale or collation is
/// therefore not authoritative for collection identity, page boundaries, or
/// cursor continuation.
fn storage_list_query(profile: DatabaseProfile) -> String {
    let (predicate, ordering) = match profile {
        DatabaseProfile::PostgreSql => (
            "pg_catalog.convert_to(collection, 'UTF8') = pg_catalog.convert_to($1, 'UTF8') \
             AND ($2::bytea IS NULL OR user_id = $2) \
             AND (NOT $7::BOOL OR pg_catalog.convert_to(object_key, 'UTF8') > pg_catalog.convert_to($3, 'UTF8') \
                  OR (pg_catalog.convert_to(object_key, 'UTF8') = pg_catalog.convert_to($3, 'UTF8') AND user_id > $4)) \
             AND ($5::bytea IS NULL OR read_permission = 2 OR (user_id = $5 AND read_permission = 1))",
            "pg_catalog.convert_to(object_key, 'UTF8') ASC, user_id ASC",
        ),
        DatabaseProfile::CockroachDb => (
            "collection::BYTES = $1::STRING::BYTES \
             AND ($2::bytea IS NULL OR user_id = $2) \
             AND (NOT $7::BOOL OR object_key::BYTES > $3::STRING::BYTES \
                  OR (object_key::BYTES = $3::STRING::BYTES AND user_id > $4)) \
             AND ($5::bytea IS NULL OR read_permission = 2 OR (user_id = $5 AND read_permission = 1))",
            "object_key::BYTES ASC, user_id ASC",
        ),
    };
    storage_paged_query(profile, predicate, ordering, 6)
}

#[cfg(test)]
fn finish_storage_page(
    mut objects: Vec<StorageObject>,
    actor: Actor,
    owner: Option<UserId>,
    limit: usize,
) -> StorageListPage {
    let has_more = objects.len() > limit;
    if has_more {
        objects.truncate(limit);
    }
    finish_visible_storage_page(objects, actor, owner, has_more)
}

fn finish_visible_storage_page(
    objects: Vec<StorageObject>,
    actor: Actor,
    owner: Option<UserId>,
    has_more: bool,
) -> StorageListPage {
    let next = has_more.then(|| {
        (
            actor,
            owner,
            objects
                .last()
                .expect("validated positive list limit retains a last page object")
                .key
                .clone(),
        )
    });
    (objects, next)
}

fn validate_client_list_request(
    actor: Actor,
    collection: &str,
    after: Option<&StorageListPosition>,
    limit: usize,
) -> Result<UserId, DomainError> {
    let user = match actor {
        Actor::User(user) if !user.is_zero() => user,
        _ => return Err(invalid("invalid_storage_actor")),
    };
    if collection.len() > MAX_CLIENT_LIST_COLLECTION_BYTES {
        return Err(invalid("invalid_storage_collection"));
    }
    if limit == 0 || limit > MAX_LIST_LIMIT {
        return Err(invalid("invalid_storage_list_limit"));
    }
    if after.is_some_and(|position| position.key.len() > MAX_LIST_POSITION_KEY_BYTES) {
        return Err(invalid("invalid_storage_list_position"));
    }
    Ok(user)
}

// These queries intentionally use SQL text ordering, as the pinned Nakama
// client-list projection does. The existing typed list retains byte ordering.
fn storage_client_list_public_query(profile: DatabaseProfile) -> String {
    storage_paged_query(
        profile,
        "collection = $1 AND read_permission >= 2 \
         AND ($2::TEXT IS NULL OR (collection, read_permission, object_key, user_id) > ($1, 2, $2, $3))",
        "read_permission ASC, object_key ASC, user_id ASC",
        4,
    )
}

fn storage_client_list_own_query(profile: DatabaseProfile) -> String {
    storage_paged_query(
        profile,
        "collection = $1 AND user_id = $2 AND read_permission >= 1 \
         AND ($3::TEXT IS NULL OR (collection, user_id, read_permission, object_key) > ($1, $2, $4::INT4, $3))",
        "read_permission ASC, object_key ASC",
        5,
    )
}

fn storage_client_list_foreign_query(profile: DatabaseProfile) -> String {
    storage_paged_query(
        profile,
        "collection = $1 AND user_id = $2 AND read_permission = 2 \
         AND ($3::TEXT IS NULL OR (collection, read_permission, user_id, object_key) > ($1, 2, $2, $3))",
        "object_key ASC",
        4,
    )
}

fn finish_stored_client_storage_page(
    objects: Vec<StoredStorageObject>,
    has_more: bool,
) -> StoredStorageClientListPage {
    let next = has_more.then(|| {
        let object = &objects
            .last()
            .expect("validated positive list limit retains a last page object")
            .object;
        StorageListPosition {
            key: object.key.key().to_owned(),
            user_id: object.key.user_id(),
            read: object.read_permission.as_i32(),
        }
    });
    StoredStorageClientListPage { objects, next }
}
