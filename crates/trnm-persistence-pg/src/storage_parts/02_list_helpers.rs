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
/// PostgreSQL uses `convert_to(..., 'UTF8')`. Ambient locale or collation is
/// therefore not authoritative for collection identity, page boundaries, or
/// cursor continuation.
fn storage_list_query(profile: DatabaseProfile) -> &'static str {
    match profile {
        DatabaseProfile::PostgreSql => {
            "SELECT object_key, user_id, value_bytes, version_digest, \
                    read_permission, write_permission \
             FROM trnm_storage_objects \
             WHERE convert_to(collection, 'UTF8') = convert_to($1, 'UTF8') \
               AND ($2::bytea IS NULL OR user_id = $2) \
               AND (convert_to(object_key, 'UTF8') > convert_to($3, 'UTF8') \
                    OR (convert_to(object_key, 'UTF8') = convert_to($3, 'UTF8') \
                        AND user_id > $4)) \
               AND ($5::bytea IS NULL OR read_permission = 2 \
                    OR (user_id = $5 AND read_permission = 1)) \
             ORDER BY convert_to(object_key, 'UTF8') ASC, user_id ASC \
             LIMIT $6"
        }
        DatabaseProfile::CockroachDb => {
            "SELECT object_key, user_id, value_bytes, version_digest, \
                    read_permission, write_permission \
             FROM trnm_storage_objects \
             WHERE collection::BYTES = $1::STRING::BYTES \
               AND ($2::bytea IS NULL OR user_id = $2) \
               AND (object_key::BYTES > $3::STRING::BYTES \
                    OR (object_key::BYTES = $3::STRING::BYTES AND user_id > $4)) \
               AND ($5::bytea IS NULL OR read_permission = 2 \
                    OR (user_id = $5 AND read_permission = 1)) \
             ORDER BY object_key::BYTES ASC, user_id ASC \
             LIMIT $6"
        }
    }
}

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
