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
