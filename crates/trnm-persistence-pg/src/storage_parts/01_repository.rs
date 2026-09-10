impl PgRepository {
    pub fn read_storage_object(
        &mut self,
        actor: Actor,
        key: &StorageObjectKey,
    ) -> Result<StorageObject, DomainError> {
        let row = self
            .client
            .query_opt(
                "SELECT value_bytes, version_digest, read_permission, write_permission \
                 FROM trnm_storage_objects \
                 WHERE collection = $1 AND object_key = $2 AND user_id = $3",
                &[
                    &key.collection(),
                    &key.key(),
                    &key.user_id().as_bytes().as_slice(),
                ],
            )
            .map_err(map_postgres_error)?
            .ok_or_else(storage_not_found)?;
        let object = decode_storage_object(key.clone(), &row)?;
        authorize_read(actor, &object)?;
        Ok(object)
    }

    /// List one bounded storage page using a typed keyset cursor.
    ///
    /// Ordering is stable by `(object_key, user_id)` inside one collection. The
    /// optional owner narrows the page to one exact storage owner. ACL filtering
    /// is performed by the database before the limit is applied, so inaccessible
    /// rows neither consume page capacity nor become cursors. The returned cursor
    /// is the last visible object in the page and is present only when one bounded
    /// sentinel row proves that another visible object exists.
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

        let rows = self
            .client
            .query(
                "SELECT object_key, user_id, value_bytes, version_digest, \
                        read_permission, write_permission \
                 FROM trnm_storage_objects \
                 WHERE collection = $1 \
                   AND ($2::bytea IS NULL OR user_id = $2) \
                   AND (object_key > $3 OR (object_key = $3 AND user_id > $4)) \
                   AND ($5::bytea IS NULL OR read_permission = 2 \
                        OR (user_id = $5 AND read_permission = 1)) \
                 ORDER BY object_key ASC, user_id ASC \
                 LIMIT $6",
                &[
                    &collection,
                    &owner_bytes,
                    &after_key,
                    &after_user,
                    &actor_bytes,
                    &fetch_limit,
                ],
            )
            .map_err(map_postgres_error)?;

        let objects = rows
            .iter()
            .map(|row| decode_listed_storage_object(collection, row))
            .collect::<Result<Vec<_>, _>>()?;
        Ok(finish_storage_page(objects, actor, owner, limit))
    }

    pub fn apply_storage_batch(
        &mut self,
        actor: Actor,
        operations: &[BatchOperation],
        updated_at_ms: u64,
    ) -> Result<Vec<MutationReceipt>, DomainError> {
        validate_batch(operations)?;
        let updated_at_i64 = to_i64(updated_at_ms)?;
        let mut transaction = self
            .client
            .build_transaction()
            .isolation_level(IsolationLevel::Serializable)
            .start()
            .map_err(map_postgres_error)?;

        let mut staged = BTreeMap::new();
        for key in sorted_keys(operations) {
            let object = load_for_update(&mut transaction, &key)?;
            staged.insert(key, object);
        }

        let mut receipts = Vec::with_capacity(operations.len());
        for operation in operations {
            let receipt = match operation {
                BatchOperation::Write(write) => {
                    apply_write(&mut transaction, &mut staged, actor, write, updated_at_i64)?
                }
                BatchOperation::Delete(delete) => {
                    apply_delete(&mut transaction, &mut staged, actor, delete)?
                }
            };
            receipts.push(receipt);
        }
        transaction.commit().map_err(map_postgres_error)?;
        Ok(receipts)
    }
}
