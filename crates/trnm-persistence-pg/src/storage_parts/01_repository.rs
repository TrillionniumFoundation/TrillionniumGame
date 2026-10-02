impl PgRepository {
    /// Read a bounded batch under one read-only serializable snapshot.
    /// Missing and inaccessible rows are omitted before integrity decoding.
    /// Caller order and repeated keys are retained by this source candidate;
    /// the pinned upstream single-SELECT ordering/multiplicity is not implied.
    pub fn read_storage_objects(
        &mut self,
        actor: Actor,
        keys: &[StorageObjectKey],
    ) -> Result<Vec<StorageObject>, DomainError> {
        Ok(self
            .read_storage_objects_with_metadata(actor, keys)?
            .into_iter()
            .map(|stored| stored.object)
            .collect())
    }

    pub fn read_storage_objects_with_metadata(
        &mut self,
        actor: Actor,
        keys: &[StorageObjectKey],
    ) -> Result<Vec<StoredStorageObject>, DomainError> {
        validate_read_batch(actor, keys)?;
        if keys.is_empty() {
            return Ok(Vec::new());
        }
        let actor_bytes = match actor {
            Actor::Server => None,
            Actor::User(user) => Some(user.as_bytes().to_vec()),
        };
        let mut transaction = self
            .client
            .build_transaction()
            .isolation_level(IsolationLevel::Serializable)
            .read_only(true)
            .start()
            .map_err(map_postgres_error)?;
        verify_storage_writer_epoch(&mut transaction, self.profile)?;
        let columns = storage_row_columns(self.profile, "TRUE");
        let query = format!(
            "SELECT {columns} FROM public.trnm_storage_objects \
             WHERE collection = $1 AND object_key = $2 AND user_id = $3 \
               AND ($4::bytea IS NULL OR read_permission = 2 \
                    OR (user_id = $4 AND read_permission = 1))"
        );
        let mut result_bytes = 0;
        let mut objects = Vec::with_capacity(keys.len());
        for key in keys {
            let row = transaction
                .query_opt(
                    &query,
                    &[
                        &key.collection(),
                        &key.key(),
                        &key.user_id().as_bytes().as_slice(),
                        &actor_bytes,
                    ],
                )
                .map_err(map_stored_storage_error)?;
            if let Some(row) = row {
                let object = decode_stored_storage_object(key.clone(), &row)?;
                consume_storage_result_budget(&mut result_bytes, object.object.value.len())?;
                objects.push(object);
            }
        }
        transaction.commit().map_err(map_postgres_error)?;
        Ok(objects)
    }

    pub fn read_storage_object(
        &mut self,
        actor: Actor,
        key: &StorageObjectKey,
    ) -> Result<StorageObject, DomainError> {
        Ok(self.read_storage_object_with_metadata(actor, key)?.object)
    }

    pub fn read_storage_object_with_metadata(
        &mut self,
        actor: Actor,
        key: &StorageObjectKey,
    ) -> Result<StoredStorageObject, DomainError> {
        validate_actor_and_owner(actor, None)?;
        let actor_bytes = match actor {
            Actor::Server => None,
            Actor::User(user) => Some(user.as_bytes().to_vec()),
        };
        let visible =
            "$4::BYTEA IS NULL OR read_permission = 2 OR (user_id = $4 AND read_permission = 1)";
        let columns = storage_row_columns(self.profile, visible);
        let query = format!(
            "SELECT {columns}, ({visible}) AS storage_visible \
             FROM public.trnm_storage_objects \
             WHERE collection = $1 AND object_key = $2 AND user_id = $3"
        );
        let mut transaction = self
            .client
            .build_transaction()
            .isolation_level(IsolationLevel::Serializable)
            .read_only(true)
            .start()
            .map_err(map_postgres_error)?;
        verify_storage_writer_epoch(&mut transaction, self.profile)?;
        let row = transaction
            .query_opt(
                &query,
                &[
                    &key.collection(),
                    &key.key(),
                    &key.user_id().as_bytes().as_slice(),
                    &actor_bytes,
                ],
            )
            .map_err(map_stored_storage_error)?
            .ok_or_else(storage_not_found)?;
        if !row
            .try_get::<_, bool>(14)
            .map_err(|_| data_loss("invalid_storage_read_visibility"))?
        {
            return Err(error(
                StableCode::PermissionDenied,
                "storage_read_permission_denied",
                RetryClass::Never,
            ));
        }
        let object = decode_stored_storage_object(key.clone(), &row)?;
        authorize_read(actor, &object.object)?;
        transaction.commit().map_err(map_postgres_error)?;
        Ok(object)
    }

    /// List one bounded storage page using a typed keyset cursor.
    ///
    /// Ordering is stable by canonical UTF-8 key bytes followed by `user_id`
    /// inside one collection. The optional owner narrows the page to one exact
    /// storage owner. ACL filtering is performed by the database before the
    /// limit is applied, so inaccessible rows neither consume page capacity nor
    /// become cursors. The returned cursor is the last visible object in the
    /// page and is present only when one bounded sentinel row proves that
    /// another visible object exists.
    pub fn list_storage_objects(
        &mut self,
        actor: Actor,
        collection: &str,
        owner: Option<UserId>,
        after: Option<&StorageListCursor>,
        limit: usize,
    ) -> Result<StorageListPage, DomainError> {
        validate_list_request(actor, collection, owner, after, limit)?;
        let fetch_limit = limit
            .checked_add(1)
            .and_then(|value| i64::try_from(value).ok())
            .ok_or_else(|| invalid("invalid_storage_list_limit"))?;

        let owner_bytes = owner.map(|user| user.as_bytes().to_vec());
        let actor_bytes = match actor {
            Actor::Server => None,
            Actor::User(user) => Some(user.as_bytes().to_vec()),
        };
        let (after_key, after_user) = after.map_or_else(
            || (String::new(), vec![0_u8; 16]),
            |cursor| {
                (
                    cursor.2.key().to_owned(),
                    cursor.2.user_id().as_bytes().to_vec(),
                )
            },
        );

        let mut transaction = self
            .client
            .build_transaction()
            .isolation_level(IsolationLevel::Serializable)
            .read_only(true)
            .start()
            .map_err(map_postgres_error)?;
        verify_storage_writer_epoch(&mut transaction, self.profile)?;
        let query = storage_list_query(self.profile);
        let parameters: [&(dyn ToSql + Sync); 6] = [
            &collection,
            &owner_bytes,
            &after_key,
            &after_user,
            &actor_bytes,
            &fetch_limit,
        ];
        let mut rows = transaction
            .query_raw(&query, parameters)
            .map_err(map_stored_storage_error)?;
        let mut result_bytes = 0;
        let mut objects = Vec::with_capacity(limit);
        let mut has_more = false;
        while let Some(row) = rows.next().map_err(map_stored_storage_error)? {
            if objects.len() == limit {
                has_more = true;
                continue;
            }
            let object = decode_listed_storage_object(collection, &row)?;
            consume_storage_result_budget(&mut result_bytes, object.value.len())?;
            objects.push(object);
        }
        drop(rows);
        transaction.commit().map_err(map_postgres_error)?;
        Ok(finish_visible_storage_page(objects, actor, owner, has_more))
    }

    /// List a bounded Nakama client page under one read-only serializable snapshot.
    /// Public-all, own-owner and foreign/global-owner listings use the pinned
    /// SQL text ordering and independently filter ACL before the limit sentinel.
    /// The cursor is an untrusted offset rather than a scope-bound credential.
    pub fn list_storage_objects_nakama(
        &mut self,
        actor: Actor,
        collection: &str,
        owner: Option<UserId>,
        after: Option<&StorageListPosition>,
        limit: usize,
    ) -> Result<StorageClientListPage, DomainError> {
        let page =
            self.list_storage_objects_nakama_with_metadata(actor, collection, owner, after, limit)?;
        Ok(StorageClientListPage {
            objects: page
                .objects
                .into_iter()
                .map(|stored| stored.object)
                .collect(),
            next: page.next,
        })
    }

    pub fn list_storage_objects_nakama_with_metadata(
        &mut self,
        actor: Actor,
        collection: &str,
        owner: Option<UserId>,
        after: Option<&StorageListPosition>,
        limit: usize,
    ) -> Result<StoredStorageClientListPage, DomainError> {
        let user = validate_client_list_request(actor, collection, after, limit)?;
        let fetch_limit =
            i64::try_from(limit + 1).map_err(|_| invalid("invalid_storage_list_limit"))?;
        let after_key = after.map(|position| position.key.as_str());
        let mut transaction = self
            .client
            .build_transaction()
            .isolation_level(IsolationLevel::Serializable)
            .read_only(true)
            .start()
            .map_err(map_postgres_error)?;
        verify_storage_writer_epoch(&mut transaction, self.profile)?;
        let after_user = after.map_or_else(
            || vec![0_u8; 16],
            |position| position.user_id.as_bytes().to_vec(),
        );
        let owner_bytes = owner.map(|owner| owner.as_bytes().to_vec());
        let after_read = after.map_or(0, |position| position.read);
        let (query, parameters): (String, Vec<&(dyn ToSql + Sync)>) = match owner {
            None => (
                storage_client_list_public_query(self.profile),
                vec![&collection, &after_key, &after_user, &fetch_limit],
            ),
            Some(owner) if owner == user => (
                storage_client_list_own_query(self.profile),
                vec![
                    &collection,
                    &owner_bytes,
                    &after_key,
                    &after_read,
                    &fetch_limit,
                ],
            ),
            Some(_) => (
                storage_client_list_foreign_query(self.profile),
                vec![&collection, &owner_bytes, &after_key, &fetch_limit],
            ),
        };
        let mut rows = transaction
            .query_raw(&query, parameters)
            .map_err(map_stored_storage_error)?;
        let mut result_bytes = 0;
        let mut objects = Vec::with_capacity(limit);
        let mut has_more = false;
        while let Some(row) = rows.next().map_err(map_stored_storage_error)? {
            if objects.len() == limit {
                has_more = true;
                continue;
            }
            let object = decode_nakama_listed_storage_object_with_metadata(collection, &row)?;
            consume_storage_result_budget(&mut result_bytes, object.object.value.len())?;
            objects.push(object);
        }
        drop(rows);
        let page = finish_stored_client_storage_page(objects, has_more);
        transaction.commit().map_err(map_postgres_error)?;
        Ok(page)
    }

    pub fn apply_storage_batch(
        &mut self,
        actor: Actor,
        operations: &[BatchOperation],
        updated_at_ms: u64,
    ) -> Result<Vec<MutationReceipt>, DomainError> {
        Ok(self
            .apply_storage_batch_with_metadata(actor, operations, updated_at_ms)?
            .into_iter()
            .map(|stored| stored.receipt)
            .collect())
    }

    pub fn apply_storage_batch_with_metadata(
        &mut self,
        actor: Actor,
        operations: &[BatchOperation],
        updated_at_ms: u64,
    ) -> Result<Vec<StoredStorageMutationReceipt>, DomainError> {
        validate_batch(operations)?;
        for operation in operations {
            authorize_write_permission(actor, operation.key(), None)?;
        }
        let updated_at_i64 = to_i64(updated_at_ms)?;
        let mut transaction = self
            .client
            .build_transaction()
            .isolation_level(IsolationLevel::Serializable)
            .start()
            .map_err(map_postgres_error)?;
        verify_storage_writer_epoch(&mut transaction, self.profile)?;

        let mut locked = BTreeMap::new();
        for key in sorted_keys(operations) {
            locked.insert(key.clone(), lock_storage_access(&mut transaction, &key)?);
        }
        let mut staged = BTreeMap::new();
        let mut receipts = Vec::with_capacity(operations.len());
        for operation in operations {
            let access = locked
                .get(operation.key())
                .ok_or_else(|| data_loss("storage_batch_lock_missing"))?;
            validate_locked_operation(&mut transaction, actor, operation, access.as_ref())?;
            staged.insert(
                operation.key().clone(),
                load_for_update(&mut transaction, operation.key(), self.profile)?,
            );
            verify_storage_staged_budget(&staged)?;
            let receipt = match operation {
                BatchOperation::Write(write) => apply_write(
                    &mut transaction,
                    &mut staged,
                    actor,
                    write,
                    updated_at_i64,
                    self.profile,
                )?,
                BatchOperation::Delete(delete) => {
                    apply_delete(&mut transaction, &mut staged, actor, delete)?
                }
            };
            verify_storage_staged_budget(&staged)?;
            receipts.push(receipt);
        }
        transaction.commit().map_err(map_postgres_error)?;
        Ok(receipts)
    }
}

fn validate_read_batch(actor: Actor, keys: &[StorageObjectKey]) -> Result<(), DomainError> {
    if keys.len() > MAX_BATCH_OPERATIONS {
        return Err(invalid("invalid_storage_batch_size"));
    }
    validate_actor_and_owner(actor, None)
}
