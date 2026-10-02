impl PgRepository {
    /// Verify ready identity/catalog in a READ ONLY transaction. Never binds or
    /// rewrites metadata and does not compare provenance to a new binary SHA.
    pub fn verify_authoritative_schema(&mut self) -> Result<SchemaIdentity, DomainError> {
        let profile = self.profile;
        let mut transaction = self.client.build_transaction().isolation_level(IsolationLevel::Serializable)
            .read_only(true).start().map_err(map_postgres_error)?;
        let catalog = read_catalog(&mut transaction)?;
        if catalog_prefix(&catalog, profile)? != actions(profile).len() { return Err(failed_precondition("authoritative_schema_upgrade_incomplete")); }
        let recorded = read_metadata(&mut transaction, &catalog)?
            .ok_or_else(|| failed_precondition("schema_metadata_missing"))?;
        let identity = verify_ready_metadata(&recorded, profile)?;
        transaction.commit().map_err(map_postgres_error)?;
        Ok(identity)
    }

    /// Apply only the compile-time, Git-blob-validated ordered chain. Existing
    /// databases need an independently drained/revoked legacy writer role.
    /// The runner observes the barrier; it never revokes or kills sessions.
    pub fn migrate_authoritative_schema(&mut self, source_commit: &str, applied_at_ms: u64,
        legacy_writer_role: Option<&str>) -> Result<SchemaMigrationReport, DomainError> {
        validate_source_commit(source_commit)?;
        let applied_at_ms = to_i64(applied_at_ms)?;
        if let Some(role) = legacy_writer_role { validate_legacy_role(role)?; }
        let profile = self.profile;
        // Immutable historical SQL names its objects without a schema. Reject
        // a different effective namespace before executing any of those bytes.
        // Read-only verification uses explicit public catalog/metadata names.
        let namespace = self.client.query_one(
            "SELECT pg_catalog.current_schema()::TEXT, pg_catalog.current_schemas(true)::TEXT[]", &[])
            .map_err(map_postgres_error)?;
        let primary: Option<String> = namespace.try_get(0).map_err(map_postgres_error)?;
        let effective: Vec<String> = namespace.try_get(1).map_err(map_postgres_error)?;
        let expected: &[&str] = match profile {
            DatabaseProfile::PostgreSql => &["pg_catalog", "public"],
            DatabaseProfile::CockroachDb => &["pg_catalog", "pg_extension", "public"],
        };
        if primary.as_deref() != Some("public") || effective != expected {
            return Err(failed_precondition("authoritative_schema_namespace_rejected"));
        }
        let initial = read_catalog(&mut *self.client)?;
        let fresh = initial.is_empty();
        if fresh {
            // 0001 owns its original transaction boundaries. Do not concatenate
            // it with v2 or imply CockroachDB's DDL and DML are one transaction.
            self.client.batch_execute(steps(profile)[0].sql).map_err(map_postgres_error)?;
            bind_foundation_metadata(&mut *self.client, profile, source_commit, applied_at_ms)?;
        }
        let catalog = read_catalog(&mut *self.client)?;
        let prefix = catalog_prefix(&catalog, profile)?;
        let mut recorded = read_metadata(&mut *self.client, &catalog)?;
        if let Some(metadata) = &recorded {
            validate_metadata_base(metadata, profile)?;
            if metadata.version == 2 {
                if prefix != actions(profile).len() { return Err(failed_precondition("authoritative_schema_upgrade_incomplete")); }
                let identity = self.verify_authoritative_schema()?;
                return Ok(SchemaMigrationReport { identity, migration_applied:false, table_count:REQUIRED_TABLES.len(), applied_steps:0 });
            }
            validate_unpublished_metadata(metadata, profile)?;
        }
        if !fresh {
            let role = legacy_writer_role.ok_or_else(|| failed_precondition("legacy_storage_writer_barrier_required"))?;
            verify_legacy_writer_barrier(&mut *self.client, role)?;
        }
        if recorded.is_none() {
            if !business_data_empty(&mut *self.client)? { return Err(failed_precondition("unbound_populated_schema_rejected")); }
            bind_foundation_metadata(&mut *self.client, profile, source_commit, applied_at_ms)?;
            recorded = read_metadata(&mut *self.client, &catalog)?;
        }
        let original = recorded.ok_or_else(|| data_loss("schema_metadata_binding_failed"))?;
        match profile {
            DatabaseProfile::PostgreSql => {
                let mut transaction = self.client.build_transaction().isolation_level(IsolationLevel::Serializable)
                    .start().map_err(map_postgres_error)?;
                transaction.query_one("SELECT singleton FROM public.trnm_schema_metadata WHERE singleton=1 FOR UPDATE", &[])
                    .map_err(map_postgres_error)?;
                if !fresh { verify_legacy_writer_barrier(&mut transaction, legacy_writer_role.unwrap())?; }
                let locked_catalog = read_catalog(&mut transaction)?;
                let locked_prefix = catalog_prefix(&locked_catalog, profile)?;
                let current = read_metadata(&mut transaction, &locked_catalog)?.ok_or_else(|| failed_precondition("schema_metadata_missing"))?;
                validate_unpublished_metadata(&current, profile)?;
                if current.source_commit != original.source_commit || current.applied_at_ms != original.applied_at_ms {
                    return Err(failed_precondition("schema_metadata_publish_conflict"));
                }
                for action in &actions(profile)[locked_prefix..] {
                    transaction.batch_execute(action.sql).map_err(map_postgres_error)?;
                }
                let complete = read_catalog(&mut transaction)?;
                if catalog_prefix(&complete, profile)? != actions(profile).len() { return Err(failed_precondition("authoritative_schema_upgrade_incomplete")); }
                publish_metadata(&mut transaction, profile, &original, source_commit)?;
                transaction.commit().map_err(map_postgres_error)?;
            }
            DatabaseProfile::CockroachDb => {
                // Each marker-defined action owns a complete statement. An
                // interruption can resume only an exact committed prefix.
                // Concurrent/external DDL can fail this attempt; it is never
                // silently repaired or interpreted as successful migration.
                for action in &actions(profile)[prefix..] {
                    self.client.batch_execute(action.sql).map_err(map_postgres_error)?;
                    let updated = read_catalog(&mut *self.client)?;
                    let completed = catalog_prefix(&updated, profile)?;
                    let expected = actions(profile).iter().position(|candidate| candidate.id == action.id).unwrap() + 1;
                    if completed != expected { return Err(failed_precondition("schema_action_catalog_not_ready")); }
                }
                let mut transaction = self.client.build_transaction().isolation_level(IsolationLevel::Serializable)
                    .start().map_err(map_postgres_error)?;
                if !fresh { verify_legacy_writer_barrier(&mut transaction, legacy_writer_role.unwrap())?; }
                let complete = read_catalog(&mut transaction)?;
                if catalog_prefix(&complete, profile)? != actions(profile).len() { return Err(failed_precondition("authoritative_schema_upgrade_incomplete")); }
                let current = read_metadata(&mut transaction, &complete)?.ok_or_else(|| failed_precondition("schema_metadata_missing"))?;
                validate_unpublished_metadata(&current, profile)?;
                if current.source_commit != original.source_commit || current.applied_at_ms != original.applied_at_ms {
                    return Err(failed_precondition("schema_metadata_publish_conflict"));
                }
                publish_metadata(&mut transaction, profile, &original, source_commit)?;
                transaction.commit().map_err(map_postgres_error)?;
            }
        }
        let identity = self.verify_authoritative_schema()?;
        Ok(SchemaMigrationReport {identity, migration_applied:true, table_count:REQUIRED_TABLES.len(), applied_steps: if fresh {2} else {1}})
    }
}
