impl PgRepository {
    /// Verify ready identity/catalog in a READ ONLY transaction. Never binds or
    /// rewrites metadata and does not compare provenance to a new binary SHA.
    pub fn verify_authoritative_schema(&mut self) -> Result<SchemaIdentity, DomainError> {
        let profile = self.profile;
        let mut transaction = self
            .client
            .build_transaction()
            .isolation_level(IsolationLevel::Serializable)
            .read_only(true)
            .start()
            .map_err(map_postgres_error)?;
        let catalog = read_catalog(&mut transaction)?;
        let prefix = catalog_prefix(&catalog, profile)?;
        let recorded = read_metadata(&mut transaction, &catalog)?
            .ok_or_else(|| failed_precondition("schema_metadata_missing"))?;
        if next_revision_for_prefix(&recorded, profile, prefix)?.is_some() {
            return Err(failed_precondition(
                "authoritative_schema_upgrade_incomplete",
            ));
        }
        let identity = verify_ready_metadata(&recorded, profile)?;
        transaction.commit().map_err(map_postgres_error)?;
        Ok(identity)
    }

    /// Apply only the compile-time, Git-blob-validated ordered chain. Existing
    /// databases need an independently drained/revoked legacy writer role.
    /// The runner observes the barrier; it never revokes or kills sessions.
    /// The v3 typed backfill keeps raw witnesses and publishes epoch 3 last.
    pub fn migrate_authoritative_schema(
        &mut self,
        source_commit: &str,
        applied_at_ms: u64,
        legacy_writer_role: Option<&str>,
    ) -> Result<SchemaMigrationReport, DomainError> {
        validate_source_commit(source_commit)?;
        let applied_at_ms = to_i64(applied_at_ms)?;
        if let Some(role) = legacy_writer_role {
            validate_legacy_role(role)?;
        }
        let profile = self.profile;
        // Immutable historical SQL names its objects without a schema. Reject
        // a different effective namespace before executing any of those bytes.
        // Read-only verification uses explicit public catalog/metadata names.
        let namespace = self
            .client
            .query_one(
                "SELECT pg_catalog.current_schema()::TEXT, pg_catalog.current_schemas(true)::TEXT[]",
                &[],
            )
            .map_err(map_postgres_error)?;
        let primary: Option<String> = namespace.try_get(0).map_err(map_postgres_error)?;
        let effective: Vec<String> = namespace.try_get(1).map_err(map_postgres_error)?;
        let expected: &[&str] = match profile {
            DatabaseProfile::PostgreSql => &["pg_catalog", "public"],
            DatabaseProfile::CockroachDb => &["pg_catalog", "pg_extension", "public"],
        };
        if primary.as_deref() != Some("public") || effective != expected {
            return Err(failed_precondition(
                "authoritative_schema_namespace_rejected",
            ));
        }
        let initial = read_catalog(&mut *self.client)?;
        let fresh = initial.is_empty();
        let mut applied_steps = 0;
        if fresh {
            // The foundation file owns its original transaction boundaries.
            // Do not concatenate later actions or imply CR DDL/DML atomicity.
            let foundation = revisions(profile)
                .first()
                .expect("the locked foundation revision is embedded");
            let step = steps(profile)
                .first()
                .filter(|step| step.version == foundation.version)
                .ok_or_else(|| failed_precondition("schema_version_unsupported"))?;
            self.client
                .batch_execute(step.sql)
                .map_err(map_postgres_error)?;
            bind_foundation_metadata(&mut *self.client, profile, source_commit, applied_at_ms)?;
            applied_steps += 1;
        }
        let catalog = read_catalog(&mut *self.client)?;
        let prefix = catalog_prefix(&catalog, profile)?;
        let mut recorded = read_metadata(&mut *self.client, &catalog)?;
        if let Some(metadata) = &recorded {
            if next_revision_for_prefix(metadata, profile, prefix)?.is_none() {
                let identity = self.verify_authoritative_schema()?;
                return Ok(SchemaMigrationReport {
                    identity,
                    migration_applied: applied_steps != 0,
                    table_count: REQUIRED_TABLES.len(),
                    applied_steps,
                });
            }
        }
        if !fresh {
            let role = legacy_writer_role
                .ok_or_else(|| failed_precondition("legacy_storage_writer_barrier_required"))?;
            verify_legacy_writer_barrier(&mut *self.client, role, profile)?;
        }
        if recorded.is_none() {
            if !business_data_empty(&mut *self.client)? {
                return Err(failed_precondition("unbound_populated_schema_rejected"));
            }
            bind_foundation_metadata(&mut *self.client, profile, source_commit, applied_at_ms)?;
            recorded = read_metadata(&mut *self.client, &catalog)?;
        }
        let mut original = recorded.ok_or_else(|| data_loss("schema_metadata_binding_failed"))?;
        validate_recorded_revision(&original, profile)?;
        // Reject all illegal raw legacy data before any revision DDL. This also
        // protects a v1 -> v2 -> v3 invocation from partially upgrading v1.
        preflight_native_storage(&mut *self.client, profile, false)?;
        loop {
            let catalog = read_catalog(&mut *self.client)?;
            let prefix = catalog_prefix(&catalog, profile)?;
            let current = read_metadata(&mut *self.client, &catalog)?
                .ok_or_else(|| failed_precondition("schema_metadata_missing"))?;
            ensure_metadata_unchanged(&original, &current)?;
            let Some(target) = next_revision_for_prefix(&current, profile, prefix)? else {
                break;
            };
            original = match profile {
                DatabaseProfile::PostgreSql => {
                    let mut transaction = self
                        .client
                        .build_transaction()
                        .isolation_level(IsolationLevel::Serializable)
                        .start()
                        .map_err(map_postgres_error)?;
                    transaction
                        .query_one(
                            "SELECT singleton FROM public.trnm_schema_metadata WHERE singleton=1 FOR UPDATE",
                            &[],
                        )
                        .map_err(map_postgres_error)?;
                    if !fresh {
                        verify_legacy_writer_barrier(
                            &mut transaction,
                            legacy_writer_role.unwrap(),
                            profile,
                        )?;
                    }
                    let locked_catalog = read_catalog(&mut transaction)?;
                    let locked_prefix = catalog_prefix(&locked_catalog, profile)?;
                    let current = read_metadata(&mut transaction, &locked_catalog)?
                        .ok_or_else(|| failed_precondition("schema_metadata_missing"))?;
                    ensure_metadata_unchanged(&original, &current)?;
                    let locked_target = next_revision_for_prefix(&current, profile, locked_prefix)?
                        .ok_or_else(|| failed_precondition("schema_metadata_publish_conflict"))?;
                    if locked_target.version != target.version {
                        return Err(failed_precondition("schema_metadata_publish_conflict"));
                    }
                    if target.version == 3 {
                        transaction
                            .batch_execute(
                                "LOCK TABLE public.trnm_storage_objects IN ACCESS EXCLUSIVE MODE",
                            )
                            .map_err(map_postgres_error)?;
                        preflight_native_storage(&mut transaction, profile, false)?;
                    }
                    let mut next = locked_prefix;
                    while next < target.action_range.end {
                        let action = &actions(profile)[next];
                        match action.kind {
                            MigrationActionKind::BackfillStorageJsonbV3 => {
                                backfill_native_storage(&mut transaction)?
                            }
                            _ => transaction
                                .batch_execute(action.sql)
                                .map_err(map_postgres_error)?,
                        }
                        let catalog = read_catalog(&mut transaction)?;
                        let advanced = catalog_prefix(&catalog, profile)?;
                        if advanced <= next || advanced > target.action_range.end {
                            return Err(failed_precondition("schema_action_catalog_not_ready"));
                        }
                        next = advanced;
                    }
                    if target.version == 3 {
                        preflight_native_storage(&mut transaction, profile, true)?;
                    }
                    let complete = read_catalog(&mut transaction)?;
                    if catalog_prefix(&complete, profile)? != target.action_range.end {
                        return Err(failed_precondition(
                            "authoritative_schema_upgrade_incomplete",
                        ));
                    }
                    let published = publish_metadata(
                        &mut transaction,
                        profile,
                        &original,
                        target,
                        source_commit,
                    )?;
                    transaction.commit().map_err(map_postgres_error)?;
                    published
                }
                DatabaseProfile::CockroachDb => {
                    // Each marker-defined action owns a complete statement.
                    // Resume only the exact next-revision catalog prefix, with
                    // unchanged old metadata before each DDL action. External
                    // DDL can fail this attempt; it is not silently repaired.
                    if target.version == 3 {
                        preflight_native_storage(&mut *self.client, profile, false)?;
                    }
                    let mut index = prefix;
                    while index < target.action_range.end {
                        let before = read_catalog(&mut *self.client)?;
                        if catalog_prefix(&before, profile)? != index {
                            return Err(failed_precondition("schema_action_catalog_not_ready"));
                        }
                        let current = read_metadata(&mut *self.client, &before)?
                            .ok_or_else(|| failed_precondition("schema_metadata_missing"))?;
                        ensure_metadata_unchanged(&original, &current)?;
                        if !fresh {
                            verify_legacy_writer_barrier(
                                &mut *self.client,
                                legacy_writer_role.unwrap(),
                                profile,
                            )?;
                        }
                        let action = &actions(profile)[index];
                        match action.kind {
                            MigrationActionKind::BackfillStorageJsonbV3 => self
                                .backfill_native_storage_cockroach(
                                    &original,
                                    if fresh { None } else { legacy_writer_role },
                                )?,
                            _ => self
                                .client
                                .batch_execute(action.sql)
                                .map_err(map_postgres_error)?,
                        }
                        let updated = read_catalog(&mut *self.client)?;
                        let advanced = catalog_prefix(&updated, profile)?;
                        if advanced <= index || advanced > target.action_range.end {
                            return Err(failed_precondition("schema_action_catalog_not_ready"));
                        }
                        index = advanced;
                    }
                    let mut transaction = self
                        .client
                        .build_transaction()
                        .isolation_level(IsolationLevel::Serializable)
                        .start()
                        .map_err(map_postgres_error)?;
                    if !fresh {
                        verify_legacy_writer_barrier(
                            &mut transaction,
                            legacy_writer_role.unwrap(),
                            profile,
                        )?;
                    }
                    let complete = read_catalog(&mut transaction)?;
                    if catalog_prefix(&complete, profile)? != target.action_range.end {
                        return Err(failed_precondition(
                            "authoritative_schema_upgrade_incomplete",
                        ));
                    }
                    let current = read_metadata(&mut transaction, &complete)?
                        .ok_or_else(|| failed_precondition("schema_metadata_missing"))?;
                    ensure_metadata_unchanged(&original, &current)?;
                    validate_recorded_revision(&current, profile)?;
                    if target.version == 3 {
                        preflight_native_storage(&mut transaction, profile, true)?;
                    }
                    let published = publish_metadata(
                        &mut transaction,
                        profile,
                        &original,
                        target,
                        source_commit,
                    )?;
                    transaction.commit().map_err(map_postgres_error)?;
                    published
                }
            };
            // Count newly published migration files, not statements, adoption
            // of an already-applied foundation, or a read-only repeat call.
            applied_steps += 1;
        }
        let identity = self.verify_authoritative_schema()?;
        Ok(SchemaMigrationReport {
            identity,
            migration_applied: applied_steps != 0,
            table_count: REQUIRED_TABLES.len(),
            applied_steps,
        })
    }
    fn backfill_native_storage_cockroach(
        &mut self,
        original: &RecordedMetadata,
        role: Option<&str>,
    ) -> Result<(), DomainError> {
        let mut cursor = None;
        loop {
            let mut transaction = self
                .client
                .build_transaction()
                .isolation_level(IsolationLevel::Serializable)
                .start()
                .map_err(map_postgres_error)?;
            transaction.query_one("SELECT singleton FROM public.trnm_schema_metadata WHERE singleton=1 FOR UPDATE",&[]).map_err(map_postgres_error)?;
            let catalog = read_catalog(&mut transaction)?;
            let current = read_metadata(&mut transaction, &catalog)?
                .ok_or_else(|| failed_precondition("schema_metadata_missing"))?;
            ensure_metadata_unchanged(original, &current)?;
            if let Some(role) = role {
                verify_legacy_writer_barrier(&mut transaction, role, DatabaseProfile::CockroachDb)?;
            }
            let row = read_next_legacy_storage(&mut transaction, &catalog, cursor.as_ref(), true)?;
            if let Some(row) = row {
                fill_native_storage_row(&mut transaction, &row)?;
                cursor = Some((row.collection, row.key, row.user));
            } else {
                transaction.commit().map_err(map_postgres_error)?;
                break;
            }
            transaction.commit().map_err(map_postgres_error)?;
        }
        Ok(())
    }
}
