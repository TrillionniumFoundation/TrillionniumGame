// The field is private to this module: production callers can acquire a token
// only through the unchanged public AccountsV5 admission gate. The diagnostic
// constructor is absent from non-test binaries. Test admission grants only
// same-body diagnostic execution, never production startup qualification.
mod migration_admission {
    #[cfg(test)]
    use super::failed_precondition;
    use super::{require_account_catalog_capture, AuthoritativeSchemaTarget, DomainError};

    /// Opaque admission issued only after the production capture gate.
    /// No Default, deserialization, public fields or production diagnostic issuer.
    ///
    /// ```compile_fail
    /// let _ = trnm_persistence_pg::SchemaAdmission::diagnostic(None);
    /// ```
    /// ```compile_fail
    /// let _ = trnm_persistence_pg::SchemaAdmission {
    ///     target: trnm_persistence_pg::AuthoritativeSchemaTarget::NakamaAccountsV5,
    /// };
    /// ```
    /// ```compile_fail
    /// let _: trnm_persistence_pg::SchemaAdmission = Default::default();
    /// ```
    #[derive(Clone, Copy, Debug)]
    pub struct AdmittedTarget {
        target: AuthoritativeSchemaTarget,
        #[cfg(test)]
        cut_after_action: Option<usize>,
    }

    impl AdmittedTarget {
        pub fn production(target: AuthoritativeSchemaTarget) -> Result<Self, DomainError> {
            require_account_catalog_capture(target)?;
            Ok(Self {
                target,
                #[cfg(test)]
                cut_after_action: None,
            })
        }

        pub fn target(self) -> AuthoritativeSchemaTarget {
            self.target
        }

        #[cfg(test)]
        pub(crate) fn diagnostic(cut_after_action: Option<usize>) -> Self {
            Self {
                target: AuthoritativeSchemaTarget::NakamaAccountsV5,
                cut_after_action,
            }
        }

        #[cfg(test)]
        pub(super) fn after_action(self, next: usize) -> Result<(), DomainError> {
            if self.cut_after_action == Some(next) {
                return Err(failed_precondition("schema5_diagnostic_action_cut"));
            }
            Ok(())
        }
    }
}
pub use migration_admission::AdmittedTarget;

/// Complete selected catalog and recorded provenance validation in the caller's
/// transaction. AccountsV5 remains closed before the first catalog read.
pub(crate) fn verify_serving_schema_target(
    client: &mut impl GenericClient,
    profile: DatabaseProfile,
    target: AuthoritativeSchemaTarget,
) -> Result<SchemaIdentity, DomainError> {
    let admitted = AdmittedTarget::production(target)?;
    verify_serving_schema_admitted(client, profile, admitted)
}

pub(crate) fn verify_serving_schema_admitted(
    client: &mut impl GenericClient,
    profile: DatabaseProfile,
    admitted: AdmittedTarget,
) -> Result<SchemaIdentity, DomainError> {
    let target = admitted.target();
    let catalog = read_catalog(client)?;
    let prefix = catalog_prefix(&catalog, profile)?;
    let recorded = read_metadata(client, &catalog)?
        .ok_or_else(|| failed_precondition("schema_metadata_missing"))?;
    if match target {
        AuthoritativeSchemaTarget::StorageV4 => {
            next_revision_for_prefix(&recorded, profile, prefix)?
        }
        AuthoritativeSchemaTarget::NakamaAccountsV5 => {
            next_revision_for_prefix_target(&recorded, profile, prefix, target)?
        }
    }
    .is_some()
    {
        return Err(failed_precondition(
            "authoritative_schema_upgrade_incomplete",
        ));
    }
    verify_ready_metadata_admitted(&recorded, profile, admitted)
}

impl PgRepository {
    /// Verify ready identity/catalog in a READ ONLY transaction. Never binds or
    /// rewrites metadata and does not compare provenance to a new binary SHA.
    pub fn verify_authoritative_schema(&mut self) -> Result<SchemaIdentity, DomainError> {
        self.verify_authoritative_schema_target(AuthoritativeSchemaTarget::StorageV4)
    }

    /// Explicit schema5 inspection is gated by raw native catalog bindings.
    pub fn verify_authoritative_schema_target(
        &mut self,
        target: AuthoritativeSchemaTarget,
    ) -> Result<SchemaIdentity, DomainError> {
        require_account_catalog_capture(target)?;
        let admitted = AdmittedTarget::production(target)?;
        self.verify_authoritative_schema_admitted(admitted)
    }

    pub fn verify_authoritative_schema_admitted(
        &mut self,
        admitted: AdmittedTarget,
    ) -> Result<SchemaIdentity, DomainError> {
        let profile = self.profile;
        let mut transaction = self
            .client
            .build_transaction()
            .isolation_level(IsolationLevel::Serializable)
            .read_only(true)
            .start()
            .map_err(map_postgres_error)?;
        let identity = verify_serving_schema_admitted(&mut transaction, profile, admitted)?;
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
        self.migrate_authoritative_schema_target(
            source_commit,
            applied_at_ms,
            legacy_writer_role,
            AuthoritativeSchemaTarget::StorageV4,
        )
    }

    /// Schema version and storage writer epoch are independent. This explicit
    /// source frontier rejects AccountsV5 before namespace reads or DDL until
    /// profile-bound native catalog observations are retained and reviewed.
    pub fn migrate_authoritative_schema_target(
        &mut self,
        source_commit: &str,
        applied_at_ms: u64,
        legacy_writer_role: Option<&str>,
        target: AuthoritativeSchemaTarget,
    ) -> Result<SchemaMigrationReport, DomainError> {
        require_account_catalog_capture(target)?;
        let admitted = AdmittedTarget::production(target)?;
        self.migrate_authoritative_schema_admitted(
            source_commit,
            applied_at_ms,
            legacy_writer_role,
            admitted,
        )
    }

    fn migrate_authoritative_schema_admitted(
        &mut self,
        source_commit: &str,
        applied_at_ms: u64,
        legacy_writer_role: Option<&str>,
        admitted: AdmittedTarget,
    ) -> Result<SchemaMigrationReport, DomainError> {
        let target = admitted.target();
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
            if next_revision_for_prefix_target(metadata, profile, prefix, target)?.is_none() {
                let identity = self.verify_authoritative_schema_admitted(admitted)?;
                return Ok(SchemaMigrationReport {
                    identity,
                    migration_applied: applied_steps != 0,
                    table_count: target.table_count(),
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
            if prefix
                > revision(profile, 2)
                    .expect("locked revision2")
                    .action_range
                    .end
            {
                return Err(failed_precondition(
                    "unbound_schema_revision_prefix_rejected",
                ));
            }
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
        if original.version < 3 {
            preflight_native_storage(&mut *self.client, profile, false)?;
        } else {
            preflight_storage_revision(&mut *self.client, original.version)?;
            require_empty_import_journals(&mut *self.client, &catalog)?;
        }
        // Revisions4 and5 share epoch4 storage/import safety. Both require one
        // exact catalog action per step and empty import journals through publish.
        // Earlier backfill grammars remain unchanged; no future revision is admitted.
        loop {
            let catalog = read_catalog(&mut *self.client)?;
            let prefix = catalog_prefix(&catalog, profile)?;
            let current = read_metadata(&mut *self.client, &catalog)?
                .ok_or_else(|| failed_precondition("schema_metadata_missing"))?;
            ensure_metadata_unchanged(&original, &current)?;
            let Some(revision_target) =
                next_revision_for_prefix_target(&current, profile, prefix, target)?
            else {
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
                    let locked_target =
                        next_revision_for_prefix_target(&current, profile, locked_prefix, target)?
                            .ok_or_else(|| {
                                failed_precondition("schema_metadata_publish_conflict")
                            })?;
                    if locked_target.version != revision_target.version {
                        return Err(failed_precondition("schema_metadata_publish_conflict"));
                    }
                    if revision_target.version >= 3 {
                        transaction
                            .batch_execute(
                                "LOCK TABLE public.trnm_storage_objects IN ACCESS EXCLUSIVE MODE",
                            )
                            .map_err(map_postgres_error)?;
                        if revision_target.version == 3 {
                            preflight_native_storage(&mut transaction, profile, false)?;
                        } else {
                            preflight_storage_revision(&mut transaction, original.version)?;
                            require_empty_import_journals(&mut transaction, &locked_catalog)?;
                        }
                    }
                    let mut next = locked_prefix;
                    while next < revision_target.action_range.end {
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
                        if advanced <= next
                            || advanced > revision_target.action_range.end
                            || (matches!(revision_target.version, 4 | 5) && advanced != next + 1)
                        {
                            return Err(failed_precondition("schema_action_catalog_not_ready"));
                        }
                        #[cfg(test)]
                        admitted.after_action(advanced)?;
                        next = advanced;
                    }
                    if revision_target.version == 3 {
                        preflight_native_storage(&mut transaction, profile, true)?;
                    } else if matches!(revision_target.version, 4 | 5) {
                        preflight_storage_revision(&mut transaction, original.version)?;
                    }
                    let complete = read_catalog(&mut transaction)?;
                    if catalog_prefix(&complete, profile)? != revision_target.action_range.end {
                        return Err(failed_precondition(
                            "authoritative_schema_upgrade_incomplete",
                        ));
                    }
                    if matches!(revision_target.version, 4 | 5) {
                        require_empty_import_journals(&mut transaction, &complete)?;
                    }
                    let published = publish_metadata(
                        &mut transaction,
                        profile,
                        &original,
                        revision_target,
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
                    if revision_target.version == 3 {
                        preflight_native_storage(&mut *self.client, profile, false)?;
                    } else if matches!(revision_target.version, 4 | 5) {
                        preflight_storage_revision(&mut *self.client, original.version)?;
                        require_empty_import_journals(&mut *self.client, &catalog)?;
                    }
                    let mut index = prefix;
                    while index < revision_target.action_range.end {
                        let before = read_catalog(&mut *self.client)?;
                        if catalog_prefix(&before, profile)? != index {
                            return Err(failed_precondition("schema_action_catalog_not_ready"));
                        }
                        let current = read_metadata(&mut *self.client, &before)?
                            .ok_or_else(|| failed_precondition("schema_metadata_missing"))?;
                        ensure_metadata_unchanged(&original, &current)?;
                        if matches!(revision_target.version, 4 | 5) {
                            preflight_storage_revision(&mut *self.client, original.version)?;
                            require_empty_import_journals(&mut *self.client, &before)?;
                        }
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
                        if advanced <= index
                            || advanced > revision_target.action_range.end
                            || (matches!(revision_target.version, 4 | 5) && advanced != index + 1)
                        {
                            return Err(failed_precondition("schema_action_catalog_not_ready"));
                        }
                        let after_metadata = read_metadata(&mut *self.client, &updated)?
                            .ok_or_else(|| failed_precondition("schema_metadata_missing"))?;
                        ensure_metadata_unchanged(&original, &after_metadata)?;
                        if matches!(revision_target.version, 4 | 5) {
                            require_empty_import_journals(&mut *self.client, &updated)?;
                        }
                        #[cfg(test)]
                        admitted.after_action(advanced)?;
                        index = advanced;
                    }
                    let mut transaction = self
                        .client
                        .build_transaction()
                        .isolation_level(IsolationLevel::Serializable)
                        .start()
                        .map_err(map_postgres_error)?;
                    transaction.query_one("SELECT singleton FROM public.trnm_schema_metadata WHERE singleton=1 FOR UPDATE",&[]).map_err(map_postgres_error)?;
                    if !fresh {
                        verify_legacy_writer_barrier(
                            &mut transaction,
                            legacy_writer_role.unwrap(),
                            profile,
                        )?;
                    }
                    let complete = read_catalog(&mut transaction)?;
                    if catalog_prefix(&complete, profile)? != revision_target.action_range.end {
                        return Err(failed_precondition(
                            "authoritative_schema_upgrade_incomplete",
                        ));
                    }
                    let current = read_metadata(&mut transaction, &complete)?
                        .ok_or_else(|| failed_precondition("schema_metadata_missing"))?;
                    ensure_metadata_unchanged(&original, &current)?;
                    validate_recorded_revision(&current, profile)?;
                    if revision_target.version == 3 {
                        preflight_native_storage(&mut transaction, profile, true)?;
                    } else if matches!(revision_target.version, 4 | 5) {
                        preflight_storage_revision(&mut transaction, original.version)?;
                        require_empty_import_journals(&mut transaction, &complete)?;
                    }
                    let published = publish_metadata(
                        &mut transaction,
                        profile,
                        &original,
                        revision_target,
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
        let identity = self.verify_authoritative_schema_admitted(admitted)?;
        Ok(SchemaMigrationReport {
            identity,
            migration_applied: applied_steps != 0,
            table_count: target.table_count(),
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

#[cfg(test)]
mod migration_admission_tests {
    use super::*;

    #[test]
    fn production_admission_remains_closed_and_diagnostic_cut_is_local() {
        assert_eq!(
            AdmittedTarget::production(AuthoritativeSchemaTarget::NakamaAccountsV5)
                .err()
                .unwrap()
                .reason(),
            "schema5_native_catalog_capture_pending"
        );
        assert_eq!(
            AdmittedTarget::production(AuthoritativeSchemaTarget::StorageV4)
                .unwrap()
                .target(),
            AuthoritativeSchemaTarget::StorageV4
        );
        let cut = AdmittedTarget::diagnostic(Some(2));
        cut.after_action(1).unwrap();
        assert_eq!(
            cut.after_action(2).unwrap_err().reason(),
            "schema5_diagnostic_action_cut"
        );
        AdmittedTarget::diagnostic(None).after_action(2).unwrap();
        assert_eq!(
            AuthoritativeSchemaTarget::NakamaAccountsV5
                .require_capture_ready()
                .unwrap_err()
                .reason(),
            "schema5_native_catalog_capture_pending"
        );
    }
}
