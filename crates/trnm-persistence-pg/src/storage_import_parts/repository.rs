// Source custody is checked before this module receives any immutable packet.
// Mutable transactions perform only SQL; no packet paths/files/network effects.
const STORAGE_IMPORT_OPERATION_SECONDS: u64 = 300;
const STORAGE_IMPORT_STATEMENT_MILLIS: u64 = 5_000;
const IMPORT_JOB_COLUMNS: &str = "singleton,manifest_digest,custody_digest,source_inventory_digest,target_schema_guard_digest,prefix_digest,source_profile,source_snapshot,audit_at_ms,total_rows,total_pages,next_page,committed_rows,status";
const IMPORT_NATIVE_COLUMNS: &str = "collection,object_key,user_id,octet_length(value_jsonb::TEXT)::BIGINT,CASE WHEN octet_length(value_jsonb::TEXT)<=16777216 THEN value_jsonb::TEXT END,public_version::TEXT,value_projection_digest,read_permission,write_permission,create_time,update_time,updated_at_ms,value_origin,value_bytes,version_digest,source_manifest_digest";

#[derive(Clone, Debug)]
pub struct StorageImportOptions {
    pub audit_at_ms: u64,
    pub legacy_writer_role: String,
    /// Independently supplied by the operator, never copied from the packet.
    pub expected_target_scope: IntegrityDigest,
}

/// Only a complete read-only native/custody/target preflight creates this token.
/// Its packet borrow and fixed deadline prevent unchecked or stale registration.
pub struct CheckedStorageImport<'packet> {
    packet: &'packet VerifiedStorageExport,
    options: StorageImportOptions,
    target_guard: IntegrityDigest,
    target_identity_classification: &'static str,
    projections: Vec<IntegrityDigest>,
    pages: Vec<ImportPagePlan>,
    initial_prefix: IntegrityDigest,
    budget: ImportBudget,
}

impl std::fmt::Debug for CheckedStorageImport<'_> {
    fn fmt(&self, output: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        output
            .debug_struct("CheckedStorageImport")
            .field("manifest_sha256", &digest_hex(self.packet.manifest_digest))
            .field("rows", &self.packet.rows.len())
            .field("pages", &self.pages.len())
            .finish_non_exhaustive()
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct StorageImportProgress {
    pub schema: &'static str,
    pub manifest_sha256: String,
    pub next_page: usize,
    pub total_pages: usize,
    pub total_rows: usize,
    pub committed_rows: usize,
    pub completed: bool,
    pub target_identity_classification: &'static str,
    pub compatibility_credit: bool,
    pub production_ready: bool,
    pub full_nakama_replacement: bool,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct StorageImportPageReceipt {
    pub schema: &'static str,
    pub page_index: usize,
    pub first_ordinal: usize,
    pub row_count: usize,
    pub page_sha256: String,
    pub prefix_sha256: String,
    pub progress: StorageImportProgress,
}

#[derive(Clone, Copy, Debug)]
struct ImportPagePlan {
    first: usize,
    count: usize,
    digest: IntegrityDigest,
    prefix: IntegrityDigest,
}

#[derive(Clone, Debug)]
struct ImportBudget {
    started: std::time::Instant,
    statement_millis: u64,
}

fn import_deadline() -> DomainError {
    DomainError::new(
        trnm_contracts::StableCode::Unavailable,
        "storage_import_operation_deadline_exceeded",
        trnm_contracts::RetryClass::Never,
    )
}

impl ImportBudget {
    fn remaining(&self) -> Result<std::time::Duration, DomainError> {
        std::time::Duration::from_secs(STORAGE_IMPORT_OPERATION_SECONDS)
            .checked_sub(self.started.elapsed())
            .filter(|duration| !duration.is_zero())
            .ok_or_else(import_deadline)
    }

    fn check(&self) -> Result<(), DomainError> {
        self.remaining().map(|_| ())
    }

    fn before_statement(&self, client: &mut impl GenericClient) -> Result<(), DomainError> {
        let remaining =
            u64::try_from(self.remaining()?.as_millis()).map_err(|_| import_deadline())?;
        let millis = remaining.min(self.statement_millis);
        if millis == 0 {
            return Err(import_deadline());
        }
        client
            .batch_execute(&format!("SET LOCAL statement_timeout = '{millis}ms'"))
            .map_err(map_postgres_error)?;
        self.check()
    }

    fn before_authority_checks(&self, client: &mut impl GenericClient) -> Result<(), DomainError> {
        // Shared closed schema/fence routines each issue fewer than64 statements.
        // Reserve their entire finite sequence inside the remaining wall budget.
        let remaining =
            u64::try_from(self.remaining()?.as_millis()).map_err(|_| import_deadline())? / 64;
        let millis = remaining.min(self.statement_millis);
        if millis == 0 {
            return Err(import_deadline());
        }
        client
            .batch_execute(&format!("SET LOCAL statement_timeout = '{millis}ms'"))
            .map_err(map_postgres_error)?;
        self.check()
    }
}

fn import_database_error(error: postgres::Error) -> DomainError {
    if error
        .code()
        .is_some_and(|state| state.code().starts_with("22"))
    {
        data_loss("storage_import_native_input_invalid")
    } else {
        map_postgres_error(error)
    }
}

fn import_preflight_database_error(error: postgres::Error) -> DomainError {
    if error
        .code()
        .is_some_and(|state| matches!(state.code(), "0A000" | "42601" | "42703" | "42883"))
    {
        failed_precondition("storage_import_native_key_authority_unsupported")
    } else {
        import_database_error(error)
    }
}

fn import_preflight_query(
    client: &mut impl GenericClient,
    budget: &ImportBudget,
    sql: &str,
    params: &[&(dyn postgres::types::ToSql + Sync)],
) -> Result<Vec<postgres::Row>, DomainError> {
    budget.before_statement(client)?;
    let rows = client
        .query(sql, params)
        .map_err(import_preflight_database_error)?;
    budget.check()?;
    Ok(rows)
}

fn import_query_one(
    client: &mut impl GenericClient,
    budget: &ImportBudget,
    sql: &str,
    params: &[&(dyn postgres::types::ToSql + Sync)],
) -> Result<postgres::Row, DomainError> {
    budget.before_statement(client)?;
    let row = client
        .query_one(sql, params)
        .map_err(import_database_error)?;
    budget.check()?;
    Ok(row)
}

fn import_query_opt(
    client: &mut impl GenericClient,
    budget: &ImportBudget,
    sql: &str,
    params: &[&(dyn postgres::types::ToSql + Sync)],
) -> Result<Option<postgres::Row>, DomainError> {
    budget.before_statement(client)?;
    let row = client
        .query_opt(sql, params)
        .map_err(import_database_error)?;
    budget.check()?;
    Ok(row)
}

fn import_query(
    client: &mut impl GenericClient,
    budget: &ImportBudget,
    sql: &str,
    params: &[&(dyn postgres::types::ToSql + Sync)],
) -> Result<Vec<postgres::Row>, DomainError> {
    budget.before_statement(client)?;
    let rows = client.query(sql, params).map_err(import_database_error)?;
    budget.check()?;
    Ok(rows)
}

fn import_execute(
    client: &mut impl GenericClient,
    budget: &ImportBudget,
    sql: &str,
    params: &[&(dyn postgres::types::ToSql + Sync)],
) -> Result<u64, DomainError> {
    budget.before_statement(client)?;
    let count = client.execute(sql, params).map_err(import_database_error)?;
    budget.check()?;
    Ok(count)
}

fn import_digest(raw: Vec<u8>) -> Result<IntegrityDigest, DomainError> {
    let bytes: [u8; 32] = raw
        .try_into()
        .map_err(|_| data_loss("storage_import_journal_digest_invalid"))?;
    IntegrityDigest::new(trnm_contracts::Digest32::new(bytes))
        .map_err(|_| data_loss("storage_import_journal_digest_invalid"))
}

fn import_number(value: usize) -> Result<i64, DomainError> {
    i64::try_from(value).map_err(|_| invalid("storage_import_count_invalid"))
}

fn import_audit(options: &StorageImportOptions) -> Result<i64, DomainError> {
    crate::schema::validate_legacy_role(&options.legacy_writer_role)?;
    i64::try_from(options.audit_at_ms).map_err(|_| invalid("storage_import_audit_invalid"))
}

fn import_initial_prefix(
    packet: &VerifiedStorageExport,
    guard: IntegrityDigest,
    audit: i64,
) -> IntegrityDigest {
    let mut frame = b"trillionnium.storage-import-empty-prefix.v1\0".to_vec();
    for digest in [
        packet.manifest_digest,
        packet.custody_digest,
        packet.inventory_digest,
        guard,
    ] {
        frame.extend_from_slice(digest.get().as_bytes());
    }
    frame_field(&mut frame, packet.profile.metadata_value().as_bytes());
    frame_field(&mut frame, packet.source_snapshot.as_bytes());
    frame.extend_from_slice(&audit.to_be_bytes());
    frame.extend_from_slice(&(packet.rows.len() as u64).to_be_bytes());
    frame.extend_from_slice(&(packet.rows.len().div_ceil(packet.page_rows) as u64).to_be_bytes());
    IntegrityDigest::from_value(&frame)
}

fn import_page_digest(
    packet: &VerifiedStorageExport,
    index: usize,
    rows: &[NativeStorageImportRow],
) -> IntegrityDigest {
    let mut frame = b"trillionnium.storage-import-page.v1\0".to_vec();
    frame.extend_from_slice(packet.manifest_digest.get().as_bytes());
    frame.extend_from_slice(&(index as u64).to_be_bytes());
    frame.extend_from_slice(&(rows.len() as u64).to_be_bytes());
    for row in rows {
        frame.extend_from_slice(&(row.ordinal as u64).to_be_bytes());
        frame.extend_from_slice(row.row_digest.get().as_bytes());
    }
    IntegrityDigest::from_value(&frame)
}

fn import_next_prefix(
    previous: IntegrityDigest,
    page_digest: IntegrityDigest,
    index: usize,
    first: usize,
    count: usize,
) -> IntegrityDigest {
    let mut frame = b"trillionnium.storage-import-page-prefix.v1\0".to_vec();
    frame.extend_from_slice(previous.get().as_bytes());
    frame.extend_from_slice(page_digest.get().as_bytes());
    for number in [index, first, count] {
        frame.extend_from_slice(&(number as u64).to_be_bytes());
    }
    IntegrityDigest::from_value(&frame)
}

fn import_plans(packet: &VerifiedStorageExport, initial: IntegrityDigest) -> Vec<ImportPagePlan> {
    let mut prefix = initial;
    packet
        .rows
        .chunks(packet.page_rows)
        .enumerate()
        .map(|(index, rows)| {
            let first = index * packet.page_rows;
            let digest = import_page_digest(packet, index, rows);
            prefix = import_next_prefix(prefix, digest, index, first, rows.len());
            ImportPagePlan {
                first,
                count: rows.len(),
                digest,
                prefix,
            }
        })
        .collect()
}

fn import_frame_optional(frame: &mut Vec<u8>, value: Option<&str>) {
    match value {
        None => frame.push(0),
        Some(value) => {
            frame.push(1);
            frame_field(frame, value.as_bytes());
        }
    }
}

fn import_validate_pg_key_collation(
    namespace: &str,
    name: &str,
    provider: &str,
    deterministic: bool,
) -> Result<(), DomainError> {
    if namespace != "pg_catalog" || name != "default" || provider != "d" || !deterministic {
        return Err(failed_precondition(
            "storage_import_native_key_collation_unsupported",
        ));
    }
    Ok(())
}

fn import_target_key_collation(
    transaction: &mut Transaction<'_>,
    profile: DatabaseProfile,
    budget: &ImportBudget,
    frame: &mut Vec<u8>,
) -> Result<Vec<String>, DomainError> {
    // Only the native default deterministic key collation is supported. The
    // complete packet is subsequently compared under that actual authority.
    // Include its catalog bytes in every target guard, without normalization.
    let binding = match profile {
        DatabaseProfile::PostgreSql => {
            let database = import_preflight_query(transaction, budget, "SELECT pg_catalog.pg_encoding_to_char(encoding)::TEXT,datlocprovider::TEXT,datcollate::TEXT,datctype::TEXT,datlocale::TEXT,daticurules::TEXT,datcollversion::TEXT FROM pg_catalog.pg_database WHERE datname=pg_catalog.current_database()", &[])?;
            if database.len() != 1 {
                return Err(failed_precondition(
                    "storage_import_native_key_collation_unsupported",
                ));
            }
            let encoding: String = database[0].try_get(0).map_err(map_postgres_error)?;
            let provider: String = database[0].try_get(1).map_err(map_postgres_error)?;
            if encoding != "UTF8" || provider != "c" {
                return Err(failed_precondition(
                    "storage_import_native_key_collation_unsupported",
                ));
            }
            frame_field(frame, encoding.as_bytes());
            frame_field(frame, provider.as_bytes());
            let mut locale = Vec::new();
            for column in 2..7 {
                let value: Option<String> =
                    database[0].try_get(column).map_err(map_postgres_error)?;
                if value.as_ref().is_some_and(|value| value.len() > 4096) {
                    return Err(failed_precondition(
                        "storage_import_native_key_collation_unsupported",
                    ));
                }
                import_frame_optional(frame, value.as_deref());
                locale.push(value);
            }
            let columns = import_preflight_query(transaction, budget, "SELECT a.attname::TEXT,a.attcollation::INT8,n.nspname::TEXT,c.collname::TEXT,c.collprovider::TEXT,c.collisdeterministic FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class t ON t.oid=a.attrelid JOIN pg_catalog.pg_namespace tn ON tn.oid=t.relnamespace JOIN pg_catalog.pg_collation c ON c.oid=a.attcollation JOIN pg_catalog.pg_namespace n ON n.oid=c.collnamespace WHERE tn.nspname='public' AND t.relname='trnm_storage_objects' AND a.attname IN ('collection','object_key') AND NOT a.attisdropped ORDER BY a.attname", &[])?;
            if columns.len() != 2 {
                return Err(failed_precondition(
                    "storage_import_native_key_collation_unsupported",
                ));
            }
            let mut prior_oid = None;
            for (row, expected) in columns.iter().zip(["collection", "object_key"]) {
                let column: String = row.try_get(0).map_err(map_postgres_error)?;
                let oid: i64 = row.try_get(1).map_err(map_postgres_error)?;
                let namespace: String = row.try_get(2).map_err(map_postgres_error)?;
                let name: String = row.try_get(3).map_err(map_postgres_error)?;
                let provider: String = row.try_get(4).map_err(map_postgres_error)?;
                let deterministic: bool = row.try_get(5).map_err(map_postgres_error)?;
                import_validate_pg_key_collation(&namespace, &name, &provider, deterministic)?;
                if column != expected || oid <= 0 || prior_oid.is_some_and(|prior| prior != oid) {
                    return Err(failed_precondition(
                        "storage_import_native_key_collation_unsupported",
                    ));
                }
                prior_oid = Some(oid);
                for value in [&column, &namespace, &name, &provider] {
                    frame_field(frame, value.as_bytes());
                }
                frame.extend_from_slice(&oid.to_be_bytes());
                frame.push(u8::from(deterministic));
            }
            vec![
                "postgresql".to_owned(),
                encoding,
                provider,
                locale[0].clone().ok_or_else(|| {
                    failed_precondition("storage_import_native_key_collation_unsupported")
                })?,
                locale[1].clone().ok_or_else(|| {
                    failed_precondition("storage_import_native_key_collation_unsupported")
                })?,
                "default".to_owned(),
                "d".to_owned(),
                "true".to_owned(),
            ]
        }
        DatabaseProfile::CockroachDb => {
            let columns = import_preflight_query(transaction, budget, "SELECT column_name::TEXT,collation_name::TEXT FROM information_schema.columns WHERE table_catalog=pg_catalog.current_database() AND table_schema='public' AND table_name='trnm_storage_objects' AND column_name IN ('collection','object_key') ORDER BY column_name", &[])?;
            if columns.len() != 2 {
                return Err(failed_precondition(
                    "storage_import_native_key_collation_unsupported",
                ));
            }
            for (row, expected) in columns.iter().zip(["collection", "object_key"]) {
                let column: String = row.try_get(0).map_err(map_postgres_error)?;
                let collation: Option<String> = row.try_get(1).map_err(map_postgres_error)?;
                if column != expected || collation.is_some() {
                    return Err(failed_precondition(
                        "storage_import_native_key_collation_unsupported",
                    ));
                }
                frame_field(frame, column.as_bytes());
                import_frame_optional(frame, collation.as_deref());
            }
            vec![
                "cockroachdb".to_owned(),
                "UTF8".to_owned(),
                "uncollated".to_owned(),
            ]
        }
    };
    if !check_source_collation_binding(&binding, profile.metadata_value()) {
        return Err(failed_precondition(
            "storage_import_native_key_collation_unsupported",
        ));
    }
    Ok(binding)
}

fn import_target_guard(
    transaction: &mut Transaction<'_>,
    profile: DatabaseProfile,
    scope: IntegrityDigest,
    budget: &ImportBudget,
    lock: bool,
    source_collation: &[String],
    legacy_writer_role: &str,
) -> Result<(IntegrityDigest, &'static str), DomainError> {
    // Bind native type/function resolution before any authority query in
    // every preflight, page, resume and finalization transaction.
    budget.before_statement(transaction)?;
    transaction
        .batch_execute("SET LOCAL search_path TO pg_catalog, public")
        .map_err(map_postgres_error)?;
    budget.check()?;
    budget.before_authority_checks(transaction)?;
    let schema = crate::schema::storage_import_schema_guard(transaction, profile, lock)?;
    budget.check()?;
    // This candidate requires a dedicated target without any outbox history.
    // An expired or reaped lease can still belong to a process performing
    // external delivery, so a lack of state=1 rows cannot prove worker drain.
    // Workers and command commits hold the shared metadata gate; import holds
    // the exclusive gate. No supported outbox operation deletes these rows.
    // Keep external delivery outside this mutable transaction.
    let existing_outbox = import_query_one(
        transaction,
        budget,
        "SELECT EXISTS(SELECT 1 FROM public.trnm_outbox)",
        &[],
    )?;
    if existing_outbox
        .try_get::<_, bool>(0)
        .map_err(map_postgres_error)?
    {
        return Err(failed_precondition(
            "storage_import_target_outbox_not_empty",
        ));
    }
    let sql=match profile {
        DatabaseProfile::PostgreSql=>"SELECT pg_catalog.current_database()::TEXT,d.oid::INT8,(SELECT system_identifier::TEXT FROM pg_catalog.pg_control_system()) FROM pg_catalog.pg_database d WHERE d.datname=pg_catalog.current_database()",
        DatabaseProfile::CockroachDb=>"SELECT pg_catalog.current_database()::TEXT,d.oid::INT8 FROM pg_catalog.pg_database d WHERE d.datname=pg_catalog.current_database()",
    };
    let row = import_query_one(transaction, budget, sql, &[])?;
    let name: String = row.try_get(0).map_err(map_postgres_error)?;
    let oid: i64 = row.try_get(1).map_err(map_postgres_error)?;
    if name.is_empty() || name.len() > 1024 || oid <= 0 {
        return Err(data_loss("storage_import_target_namespace_invalid"));
    }
    let mut frame = b"trillionnium.storage-import-target-guard.v1\0".to_vec();
    frame.extend_from_slice(schema.get().as_bytes());
    frame.extend_from_slice(scope.get().as_bytes());
    frame_field(&mut frame, legacy_writer_role.as_bytes());
    frame_field(&mut frame, profile.metadata_value().as_bytes());
    frame_field(&mut frame, name.as_bytes());
    frame.extend_from_slice(&oid.to_be_bytes());
    let classification = if profile == DatabaseProfile::PostgreSql {
        let system: String = row.try_get(2).map_err(map_postgres_error)?;
        if system.is_empty()
            || system.len() > 32
            || !system.bytes().all(|byte| byte.is_ascii_digit())
        {
            return Err(data_loss("storage_import_target_system_identity_invalid"));
        }
        frame_field(&mut frame, system.as_bytes());
        "native-system-database-and-external-scope"
    } else {
        // Pinned CR26 rejects the attempted cluster_id() internal API. Do not
        // enable unsafe internals or invent a cluster identity from namespace.
        frame_field(&mut frame, b"physical-cluster-id-unobserved");
        "namespace-and-external-scope-only"
    };
    let target_collation = import_target_key_collation(transaction, profile, budget, &mut frame)?;
    if source_collation != target_collation {
        return Err(failed_precondition(
            "storage_import_source_target_collation_mismatch",
        ));
    }
    Ok((IntegrityDigest::from_value(&frame), classification))
}

fn import_writer_fence(
    transaction: &mut Transaction<'_>,
    profile: DatabaseProfile,
    role: &str,
    budget: &ImportBudget,
) -> Result<(), DomainError> {
    budget.before_authority_checks(transaction)?;
    crate::schema::verify_storage_import_legacy_writer_barrier(transaction, role, profile)?;
    budget.check()?;
    let row=import_query_one(transaction,budget,"SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_stat_activity WHERE usename=$1 AND pid<>pg_catalog.pg_backend_pid())",&[&role])?;
    if row.try_get::<_, bool>(0).map_err(map_postgres_error)? {
        return Err(failed_precondition(
            "storage_import_legacy_sessions_not_drained",
        ));
    }
    Ok(())
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct ImportJob {
    manifest: IntegrityDigest,
    custody: IntegrityDigest,
    inventory: IntegrityDigest,
    guard: IntegrityDigest,
    prefix: IntegrityDigest,
    profile: String,
    snapshot: String,
    audit: i64,
    total_rows: usize,
    total_pages: usize,
    next_page: usize,
    committed_rows: usize,
    completed: bool,
}

fn import_job(
    client: &mut impl GenericClient,
    budget: &ImportBudget,
    lock: bool,
) -> Result<Option<ImportJob>, DomainError> {
    let sql = format!(
        "SELECT {IMPORT_JOB_COLUMNS} FROM public.trnm_storage_import_jobs LIMIT 2 {}",
        if lock { "FOR UPDATE" } else { "" }
    );
    let rows = import_query(client, budget, &sql, &[])?;
    if rows.len() > 1 {
        return Err(data_loss("storage_import_journal_cardinality_invalid"));
    }
    let Some(row) = rows.first() else {
        return Ok(None);
    };
    if row.try_get::<_, i16>(0).map_err(map_postgres_error)? != 1 {
        return Err(data_loss("storage_import_journal_singleton_invalid"));
    }
    let number = |index| -> Result<usize, DomainError> {
        usize::try_from(row.try_get::<_, i64>(index).map_err(map_postgres_error)?)
            .map_err(|_| data_loss("storage_import_journal_count_invalid"))
    };
    let status: i16 = row.try_get(13).map_err(map_postgres_error)?;
    if ![0, 1].contains(&status) {
        return Err(data_loss("storage_import_journal_status_invalid"));
    }
    Ok(Some(ImportJob {
        manifest: import_digest(row.try_get(1).map_err(map_postgres_error)?)?,
        custody: import_digest(row.try_get(2).map_err(map_postgres_error)?)?,
        inventory: import_digest(row.try_get(3).map_err(map_postgres_error)?)?,
        guard: import_digest(row.try_get(4).map_err(map_postgres_error)?)?,
        prefix: import_digest(row.try_get(5).map_err(map_postgres_error)?)?,
        profile: row.try_get(6).map_err(map_postgres_error)?,
        snapshot: row.try_get(7).map_err(map_postgres_error)?,
        audit: row.try_get(8).map_err(map_postgres_error)?,
        total_rows: number(9)?,
        total_pages: number(10)?,
        next_page: number(11)?,
        committed_rows: number(12)?,
        completed: status == 1,
    }))
}

fn import_validate_job(
    job: &ImportJob,
    checked: &CheckedStorageImport<'_>,
) -> Result<(), DomainError> {
    let packet = checked.packet;
    if job.manifest != packet.manifest_digest
        || job.custody != packet.custody_digest
        || job.inventory != packet.inventory_digest
        || job.guard != checked.target_guard
        || job.profile != packet.profile.metadata_value()
        || job.snapshot != packet.source_snapshot
        || job.audit != import_audit(&checked.options)?
        || job.total_rows != packet.rows.len()
        || job.total_pages != checked.pages.len()
        || job.next_page > checked.pages.len()
    {
        return Err(failed_precondition(
            "storage_import_journal_custody_mismatch",
        ));
    }
    let (rows, prefix) = if job.next_page == 0 {
        (0, checked.initial_prefix)
    } else {
        let page = &checked.pages[job.next_page - 1];
        (page.first + page.count, page.prefix)
    };
    if job.committed_rows != rows
        || job.prefix != prefix
        || (job.completed && job.next_page != checked.pages.len())
    {
        return Err(data_loss("storage_import_journal_prefix_invalid"));
    }
    Ok(())
}

fn import_progress(job: &ImportJob, checked: &CheckedStorageImport<'_>) -> StorageImportProgress {
    StorageImportProgress {
        schema: "trillionnium.storage-import-progress.v1",
        manifest_sha256: digest_hex(job.manifest),
        next_page: job.next_page,
        total_pages: job.total_pages,
        total_rows: job.total_rows,
        committed_rows: job.committed_rows,
        completed: job.completed,
        target_identity_classification: checked.target_identity_classification,
        compatibility_credit: false,
        production_ready: false,
        full_nakama_replacement: false,
    }
}

fn import_verify_native_tuple(
    row: &postgres::Row,
    source: &NativeStorageImportRow,
    projection: IntegrityDigest,
    manifest: IntegrityDigest,
    audit: i64,
) -> Result<(), DomainError> {
    let collection: String = row.try_get(0).map_err(map_postgres_error)?;
    let key: String = row.try_get(1).map_err(map_postgres_error)?;
    let owner: Vec<u8> = row.try_get(2).map_err(map_postgres_error)?;
    let length: i64 = row.try_get(3).map_err(map_postgres_error)?;
    if length < 0 || length > MAX_ROW_BYTES as i64 {
        return Err(DomainError::new(
            trnm_contracts::StableCode::ResourceExhausted,
            "storage_import_native_budget_exceeded",
            trnm_contracts::RetryClass::Never,
        ));
    }
    let value: Option<String> = row.try_get(4).map_err(map_postgres_error)?;
    let version: String = row.try_get(5).map_err(map_postgres_error)?;
    let digest: Vec<u8> = row.try_get(6).map_err(map_postgres_error)?;
    let read: i16 = row.try_get(7).map_err(map_postgres_error)?;
    let write: i16 = row.try_get(8).map_err(map_postgres_error)?;
    let create: Option<StorageTimestamp> = row
        .try_get(9)
        .map_err(|_| data_loss("storage_import_native_timestamp_invalid"))?;
    let update: Option<StorageTimestamp> = row
        .try_get(10)
        .map_err(|_| data_loss("storage_import_native_timestamp_invalid"))?;
    let clock: i64 = row.try_get(11).map_err(map_postgres_error)?;
    let origin: String = row.try_get(12).map_err(map_postgres_error)?;
    let raw: Option<Vec<u8>> = row.try_get(13).map_err(map_postgres_error)?;
    let raw_digest: Option<Vec<u8>> = row.try_get(14).map_err(map_postgres_error)?;
    let source_manifest: Option<Vec<u8>> = row.try_get(15).map_err(map_postgres_error)?;
    if collection != source.key.collection()
        || key != source.key.key()
        || owner.as_slice() != source.key.user_id().as_bytes()
        || usize::try_from(length).ok() != Some(source.value.len())
        || value.as_deref() != Some(source.value.as_str())
        || version != source.public_version.as_str()
        || digest.as_slice() != projection.get().as_bytes()
        || read != source.read.get()
        || write != source.write.get()
        || create != Some(source.create)
        || update != Some(source.update)
        || clock != audit
        || origin != "nakama-export-unknown-request"
        || raw.is_some()
        || raw_digest.is_some()
        || source_manifest.as_deref() != Some(manifest.get().as_bytes().as_slice())
    {
        return Err(data_loss("storage_import_native_tuple_mismatch"));
    }
    Ok(())
}

fn import_reconcile(
    client: &mut impl GenericClient,
    checked: &CheckedStorageImport<'_>,
    job: &ImportJob,
) -> Result<(), DomainError> {
    import_validate_job(job, checked)?;
    let rows=import_query(client,&checked.budget,"SELECT manifest_digest,page_index,first_ordinal,row_count,page_digest,prefix_digest,audit_at_ms FROM public.trnm_storage_import_pages ORDER BY page_index LIMIT 101",&[])?;
    if rows.len() != job.next_page || rows.len() > 100 {
        return Err(data_loss("storage_import_page_inventory_mismatch"));
    }
    for (index, row) in rows.iter().enumerate() {
        let page = &checked.pages[index];
        let manifest: Vec<u8> = row.try_get(0).map_err(map_postgres_error)?;
        let page_index: i64 = row.try_get(1).map_err(map_postgres_error)?;
        let first: i64 = row.try_get(2).map_err(map_postgres_error)?;
        let count: i64 = row.try_get(3).map_err(map_postgres_error)?;
        let digest: Vec<u8> = row.try_get(4).map_err(map_postgres_error)?;
        let prefix: Vec<u8> = row.try_get(5).map_err(map_postgres_error)?;
        let audit: i64 = row.try_get(6).map_err(map_postgres_error)?;
        if manifest.as_slice() != checked.packet.manifest_digest.get().as_bytes()
            || page_index != import_number(index)?
            || first != import_number(page.first)?
            || count != import_number(page.count)?
            || digest.as_slice() != page.digest.get().as_bytes()
            || prefix.as_slice() != page.prefix.get().as_bytes()
            || audit != job.audit
        {
            return Err(data_loss("storage_import_page_receipt_mismatch"));
        }
    }
    let count = import_query_one(
        client,
        &checked.budget,
        "SELECT count(*)::INT8 FROM public.trnm_storage_objects",
        &[],
    )?;
    let count: i64 = count.try_get(0).map_err(map_postgres_error)?;
    if count != import_number(job.committed_rows)? {
        return Err(data_loss("storage_import_target_inventory_mismatch"));
    }
    // Exact count plus every unique expected key's full tuple proves this
    // dedicated relation has neither missing nor extra rows. Values are fetched
    // individually, so no full-table result may inflate memory before a check.
    let sql=format!("SELECT {IMPORT_NATIVE_COLUMNS} FROM public.trnm_storage_objects WHERE collection=$1 AND object_key=$2 AND user_id=$3");
    for source in &checked.packet.rows[..job.committed_rows] {
        let row = import_query_opt(
            client,
            &checked.budget,
            &sql,
            &[
                &source.key.collection(),
                &source.key.key(),
                &source.key.user_id().as_bytes().as_slice(),
            ],
        )?
        .ok_or_else(|| data_loss("storage_import_target_row_missing"))?;
        import_verify_native_tuple(
            &row,
            source,
            checked.projections[source.ordinal],
            checked.packet.manifest_digest,
            job.audit,
        )?;
    }
    Ok(())
}

fn import_authority(
    transaction: &mut Transaction<'_>,
    profile: DatabaseProfile,
    checked: &CheckedStorageImport<'_>,
    lock: bool,
) -> Result<(), DomainError> {
    let (guard, classification) = import_target_guard(
        transaction,
        profile,
        checked.options.expected_target_scope,
        &checked.budget,
        lock,
        &checked.packet.source_collation_binding,
        &checked.options.legacy_writer_role,
    )?;
    if guard != checked.target_guard || classification != checked.target_identity_classification {
        return Err(failed_precondition("storage_import_target_guard_changed"));
    }
    import_writer_fence(
        transaction,
        profile,
        &checked.options.legacy_writer_role,
        &checked.budget,
    )?;
    if lock && profile == DatabaseProfile::PostgreSql {
        // Lock-only updates do not invalidate an older SERIALIZABLE snapshot.
        // A business waiter could otherwise miss the newly registered job.
        // Rewrite this tuple without changing any schema/provenance value so
        // that its FOR SHARE fails with 40001 after this importer commits.
        // Audit every user trigger and writer BEFORE executing this update.
        let count = import_execute(
            transaction,
            &checked.budget,
            "UPDATE public.trnm_schema_metadata SET singleton=singleton WHERE singleton=1",
            &[],
        )?;
        if count != 1 {
            return Err(failed_precondition("schema_metadata_missing"));
        }
    }
    Ok(())
}

fn import_uuid_text(user: UserId) -> String {
    let mut text = String::with_capacity(36);
    const HEX: &[u8; 16] = b"0123456789abcdef";
    for (index, byte) in user.as_bytes().iter().enumerate() {
        if [4, 6, 8, 10].contains(&index) {
            text.push('-');
        }
        text.push(char::from(HEX[usize::from(byte >> 4)]));
        text.push(char::from(HEX[usize::from(byte & 15)]));
    }
    text
}

fn import_check_packet_rows(
    packet: &VerifiedStorageExport,
) -> Result<Vec<IntegrityDigest>, DomainError> {
    if packet.retained_bytes > MAX_PACKET_BYTES
        || packet.rows.len() > MAX_ROWS
        || !(1..=MAX_PAGE_ROWS).contains(&packet.page_rows)
        || packet.rows.len().div_ceil(packet.page_rows.max(1)) > 100
        || packet.source_snapshot.is_empty()
        || packet.source_snapshot.chars().count() > 256
        || packet.source_snapshot.chars().any(char::is_control)
        || !check_source_collation_binding(
            &packet.source_collation_binding,
            packet.profile.metadata_value(),
        )
    {
        return Err(invalid("storage_import_packet_bounds_invalid"));
    }
    let mut keys = BTreeSet::new();
    let mut projections = Vec::with_capacity(packet.rows.len());
    let mut inventory = b"trillionnium.storage-import-inventory.v1\0".to_vec();
    let mut aggregate = 0_usize;
    let mut page_bytes = 0_usize;
    for (index, row) in packet.rows.iter().enumerate() {
        if row.ordinal != index
            || !keys.insert(row.key.clone())
            || row.value.len() > MAX_ROW_BYTES
            || row.key.collection().chars().count() > 128
            || row.key.key().chars().count() > 128
            || row.public_version.as_str().chars().count() > 32
            || row.read.get() < 0
            || row.write.get() < 0
        {
            return Err(invalid("storage_import_row_bounds_invalid"));
        }
        row.create.validate()?;
        row.update.validate()?;
        if row.create.nanos % 1000 != 0 || row.update.nanos % 1000 != 0 {
            return Err(invalid("storage_import_timestamp_precision_invalid"));
        }
        aggregate = aggregate
            .checked_add(row.value.len())
            .filter(|bytes| *bytes <= MAX_PACKET_BYTES)
            .ok_or_else(|| invalid("storage_import_packet_budget"))?;
        page_bytes = page_bytes
            .checked_add(row.value.len())
            .filter(|bytes| *bytes <= MAX_PAGE_BYTES)
            .ok_or_else(|| invalid("storage_import_page_budget"))?;
        let projection = IntegrityDigest::from_value(row.value.as_bytes());
        let record = ExportRowRecord {
            ordinal: index,
            collection: row.key.collection().to_owned(),
            key: row.key.key().to_owned(),
            user_id: import_uuid_text(row.key.user_id()),
            public_version: row.public_version.as_str().to_owned(),
            read: row.read.get(),
            write: row.write.get(),
            create_time: ExportTimestamp {
                seconds: row.create.seconds,
                nanos: row.create.nanos,
            },
            update_time: ExportTimestamp {
                seconds: row.update.seconds,
                nanos: row.update.nanos,
            },
            value_path: format!("values/{index:08}.json"),
            value_sha256: digest_hex(projection),
            value_bytes: row.value.len(),
        };
        if source_row_digest(&record) != row.row_digest {
            return Err(data_loss("storage_import_verified_row_changed"));
        }
        inventory.extend_from_slice(row.row_digest.get().as_bytes());
        projections.push(projection);
        if (index + 1) % packet.page_rows == 0 {
            page_bytes = 0;
        }
    }
    if IntegrityDigest::from_value(&inventory) != packet.inventory_digest {
        return Err(data_loss("storage_import_verified_inventory_changed"));
    }
    Ok(projections)
}

fn import_check_source_native(
    transaction: &mut Transaction<'_>,
    packet: &VerifiedStorageExport,
    budget: &ImportBudget,
) -> Result<(), DomainError> {
    for source in &packet.rows {
        let row=import_query_one(transaction,budget,"SELECT octet_length(native)::INT8,CASE WHEN octet_length(native)<=16777216 THEN native END,$2::TIMESTAMPTZ,$3::TIMESTAMPTZ,$4::TEXT,$5::TEXT,$6::BYTEA,$7::TEXT,$8::SMALLINT,$9::SMALLINT FROM (SELECT $1::TEXT::JSONB::TEXT AS native) AS projected",&[&source.value,&source.create,&source.update,&source.key.collection(),&source.key.key(),&source.key.user_id().as_bytes().as_slice(),&source.public_version.as_str(),&source.read.get(),&source.write.get()])?;
        let length: i64 = row.try_get(0).map_err(map_postgres_error)?;
        if length < 0 || length > MAX_ROW_BYTES as i64 {
            return Err(DomainError::new(
                trnm_contracts::StableCode::ResourceExhausted,
                "storage_import_native_budget_exceeded",
                trnm_contracts::RetryClass::Never,
            ));
        }
        let native: Option<String> = row.try_get(1).map_err(map_postgres_error)?;
        let create: StorageTimestamp = row
            .try_get(2)
            .map_err(|_| data_loss("storage_import_native_timestamp_invalid"))?;
        let update: StorageTimestamp = row
            .try_get(3)
            .map_err(|_| data_loss("storage_import_native_timestamp_invalid"))?;
        let collection: String = row.try_get(4).map_err(map_postgres_error)?;
        let key: String = row.try_get(5).map_err(map_postgres_error)?;
        let user: Vec<u8> = row.try_get(6).map_err(map_postgres_error)?;
        let version: String = row.try_get(7).map_err(map_postgres_error)?;
        let read: i16 = row.try_get(8).map_err(map_postgres_error)?;
        let write: i16 = row.try_get(9).map_err(map_postgres_error)?;
        if usize::try_from(length).ok() != Some(source.value.len())
            || native.as_deref() != Some(source.value.as_str())
            || create != source.create
            || update != source.update
            || collection != source.key.collection()
            || key != source.key.key()
            || user.as_slice() != source.key.user_id().as_bytes()
            || version != source.public_version.as_str()
            || read != source.read.get()
            || write != source.write.get()
        {
            return Err(data_loss("storage_import_native_projection_diverged"));
        }
    }
    import_check_native_keys(transaction, packet, budget)
}

fn import_validate_native_key_counts(
    total: i64,
    distinct: i64,
    expected: usize,
) -> Result<(), DomainError> {
    if expected > MAX_ROWS || usize::try_from(total).ok() != Some(expected) || distinct != total {
        return Err(failed_precondition(
            "storage_import_native_key_equivalence_conflict",
        ));
    }
    Ok(())
}

fn import_check_native_keys(
    transaction: &mut Transaction<'_>,
    packet: &VerifiedStorageExport,
    budget: &ImportBudget,
) -> Result<(), DomainError> {
    // Rust byte identity cannot certify the target primary key's equality.
    // With only native default collations admitted, all three UNNEST inputs
    // use the same native types/collations as the target primary-key columns.
    // Check the entire inventory together, including keys on later pages.
    let collections: Vec<&str> = packet.rows.iter().map(|row| row.key.collection()).collect();
    let keys: Vec<&str> = packet.rows.iter().map(|row| row.key.key()).collect();
    let users: Vec<Vec<u8>> = packet
        .rows
        .iter()
        .map(|row| row.key.user_id().as_bytes().to_vec())
        .collect();
    let rows = import_preflight_query(transaction, budget, "WITH native_keys AS (SELECT collection,object_key,user_id FROM UNNEST($1::TEXT[],$2::TEXT[],$3::BYTEA[]) AS packet_keys(collection,object_key,user_id)) SELECT (SELECT count(*)::BIGINT FROM native_keys),(SELECT count(*)::BIGINT FROM (SELECT DISTINCT collection,object_key,user_id FROM native_keys) AS distinct_keys)", &[&collections,&keys,&users])?;
    if rows.len() != 1 {
        return Err(failed_precondition(
            "storage_import_native_key_authority_unsupported",
        ));
    }
    let total: i64 = rows[0].try_get(0).map_err(map_postgres_error)?;
    let distinct: i64 = rows[0].try_get(1).map_err(map_postgres_error)?;
    import_validate_native_key_counts(total, distinct, packet.rows.len())
}

fn import_statement_millis(value: &str) -> Result<u64, DomainError> {
    if value == "0" {
        return Ok(STORAGE_IMPORT_STATEMENT_MILLIS);
    }
    let (digits, scale) = if let Some(digits) = value.strip_suffix("ms") {
        (digits, 1_u64)
    } else if let Some(digits) = value.strip_suffix("min") {
        (digits, 60_000)
    } else if let Some(digits) = value.strip_suffix('s') {
        (digits, 1_000)
    } else if let Some(digits) = value.strip_suffix('h') {
        (digits, 3_600_000)
    } else {
        (value, 1)
    };
    let millis = digits
        .parse::<u64>()
        .ok()
        .and_then(|number| number.checked_mul(scale))
        .ok_or_else(|| failed_precondition("storage_import_statement_timeout_invalid"))?;
    Ok(if millis == 0 {
        STORAGE_IMPORT_STATEMENT_MILLIS
    } else {
        millis.min(STORAGE_IMPORT_STATEMENT_MILLIS)
    })
}

impl crate::PgRepository {
    /// Call inside PgPool::run_with_deadline(300s, ...) to retain the existing
    /// transport cancellation authority. This token adds per-statement and
    /// per-stage checks across all subsequent page/resume/finalize methods.
    pub fn preflight_storage_import<'packet>(
        &mut self,
        packet: &'packet VerifiedStorageExport,
        options: StorageImportOptions,
    ) -> Result<CheckedStorageImport<'packet>, DomainError> {
        let started = std::time::Instant::now();
        let audit = import_audit(&options)?;
        if self.profile != packet.profile {
            return Err(failed_precondition("storage_import_cross_profile_rejected"));
        }
        let projections = import_check_packet_rows(packet)?;
        let timeout = self
            .client
            .query_one("SHOW statement_timeout", &[])
            .map_err(map_postgres_error)?;
        let value: String = timeout.try_get(0).map_err(map_postgres_error)?;
        let budget = ImportBudget {
            started,
            statement_millis: import_statement_millis(&value)?,
        };
        budget.check()?;
        let mut transaction = self
            .client
            .build_transaction()
            .isolation_level(postgres::IsolationLevel::Serializable)
            .read_only(true)
            .start()
            .map_err(map_postgres_error)?;
        let (target_guard, classification) = import_target_guard(
            &mut transaction,
            self.profile,
            options.expected_target_scope,
            &budget,
            false,
            &packet.source_collation_binding,
            &options.legacy_writer_role,
        )?;
        import_writer_fence(
            &mut transaction,
            self.profile,
            &options.legacy_writer_role,
            &budget,
        )?;
        // Validate the whole source before *any* target job/data/page mutation.
        import_check_source_native(&mut transaction, packet, &budget)?;
        let initial_prefix = import_initial_prefix(packet, target_guard, audit);
        let pages = import_plans(packet, initial_prefix);
        let checked = CheckedStorageImport {
            packet,
            options,
            target_guard,
            target_identity_classification: classification,
            projections,
            pages,
            initial_prefix,
            budget,
        };
        if let Some(job) = import_job(&mut transaction, &checked.budget, false)? {
            import_reconcile(&mut transaction, &checked, &job)?;
        } else {
            let row=import_query_one(&mut transaction,&checked.budget,"SELECT EXISTS(SELECT 1 FROM public.trnm_storage_objects LIMIT 1) OR EXISTS(SELECT 1 FROM public.trnm_storage_import_pages LIMIT 1)",&[])?;
            if row.try_get::<_, bool>(0).map_err(map_postgres_error)? {
                return Err(failed_precondition(
                    "storage_import_dedicated_empty_target_required",
                ));
            }
        }
        checked.budget.check()?;
        transaction.commit().map_err(map_postgres_error)?;
        checked.budget.check()?;
        Ok(checked)
    }

    /// Register exactly one checked packet or verify its exact existing prefix.
    pub fn begin_storage_import(
        &mut self,
        checked: &CheckedStorageImport<'_>,
    ) -> Result<StorageImportProgress, DomainError> {
        checked.budget.check()?;
        let mut transaction = self
            .client
            .build_transaction()
            .isolation_level(postgres::IsolationLevel::Serializable)
            .start()
            .map_err(map_postgres_error)?;
        import_authority(&mut transaction, self.profile, checked, true)?;
        let job = if let Some(job) = import_job(&mut transaction, &checked.budget, true)? {
            import_reconcile(&mut transaction, checked, &job)?;
            job
        } else {
            let row=import_query_one(&mut transaction,&checked.budget,"SELECT EXISTS(SELECT 1 FROM public.trnm_storage_objects LIMIT 1) OR EXISTS(SELECT 1 FROM public.trnm_storage_import_pages LIMIT 1)",&[])?;
            if row.try_get::<_, bool>(0).map_err(map_postgres_error)? {
                return Err(failed_precondition(
                    "storage_import_dedicated_empty_target_required",
                ));
            }
            let packet = checked.packet;
            let audit = import_audit(&checked.options)?;
            let total = import_number(packet.rows.len())?;
            let pages = import_number(checked.pages.len())?;
            let count=import_execute(&mut transaction,&checked.budget,"INSERT INTO public.trnm_storage_import_jobs (singleton,manifest_digest,custody_digest,source_inventory_digest,target_schema_guard_digest,prefix_digest,source_profile,source_snapshot,audit_at_ms,total_rows,total_pages,next_page,committed_rows,status) VALUES(1,$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,0,0,0)",&[&packet.manifest_digest.get().as_bytes().as_slice(),&packet.custody_digest.get().as_bytes().as_slice(),&packet.inventory_digest.get().as_bytes().as_slice(),&checked.target_guard.get().as_bytes().as_slice(),&checked.initial_prefix.get().as_bytes().as_slice(),&packet.profile.metadata_value(),&packet.source_snapshot,&audit,&total,&pages])?;
            if count != 1 {
                return Err(failed_precondition("storage_import_registration_conflict"));
            }
            let job = import_job(&mut transaction, &checked.budget, true)?
                .ok_or_else(|| data_loss("storage_import_journal_missing"))?;
            import_reconcile(&mut transaction, checked, &job)?;
            job
        };
        let progress = import_progress(&job, checked);
        checked.budget.check()?;
        transaction.commit().map_err(map_postgres_error)?;
        checked.budget.check()?;
        Ok(progress)
    }

    pub fn apply_next_storage_import_page(
        &mut self,
        checked: &CheckedStorageImport<'_>,
    ) -> Result<StorageImportPageReceipt, DomainError> {
        checked.budget.check()?;
        let mut transaction = self
            .client
            .build_transaction()
            .isolation_level(postgres::IsolationLevel::Serializable)
            .start()
            .map_err(map_postgres_error)?;
        import_authority(&mut transaction, self.profile, checked, true)?;
        let original = import_job(&mut transaction, &checked.budget, true)?
            .ok_or_else(|| failed_precondition("storage_import_registration_required"))?;
        import_reconcile(&mut transaction, checked, &original)?;
        if original.completed || original.next_page >= checked.pages.len() {
            return Err(failed_precondition("storage_import_no_next_page"));
        }
        let index = original.next_page;
        let page = &checked.pages[index];
        let audit = original.audit;
        let manifest = checked.packet.manifest_digest;
        let sql=format!("INSERT INTO public.trnm_storage_objects(collection,object_key,user_id,value_jsonb,public_version,value_projection_digest,read_permission,write_permission,create_time,update_time,updated_at_ms,value_origin,value_bytes,version_digest,source_manifest_digest) VALUES($1,$2,$3,$4::TEXT::JSONB,$5,$6,$7,$8,$9,$10,$11,'nakama-export-unknown-request',NULL,NULL,$12) RETURNING {IMPORT_NATIVE_COLUMNS}");
        for source in &checked.packet.rows[page.first..page.first + page.count] {
            let digest = checked.projections[source.ordinal];
            let row = import_query_one(
                &mut transaction,
                &checked.budget,
                &sql,
                &[
                    &source.key.collection(),
                    &source.key.key(),
                    &source.key.user_id().as_bytes().as_slice(),
                    &source.value,
                    &source.public_version.as_str(),
                    &digest.get().as_bytes().as_slice(),
                    &source.read.get(),
                    &source.write.get(),
                    &source.create,
                    &source.update,
                    &audit,
                    &manifest.get().as_bytes().as_slice(),
                ],
            )?;
            import_verify_native_tuple(&row, source, digest, manifest, audit)?;
        }
        let page_index = import_number(index)?;
        let first = import_number(page.first)?;
        let row_count = import_number(page.count)?;
        let count=import_execute(&mut transaction,&checked.budget,"INSERT INTO public.trnm_storage_import_pages(manifest_digest,page_index,first_ordinal,row_count,page_digest,prefix_digest,audit_at_ms) VALUES($1,$2,$3,$4,$5,$6,$7)",&[&manifest.get().as_bytes().as_slice(),&page_index,&first,&row_count,&page.digest.get().as_bytes().as_slice(),&page.prefix.get().as_bytes().as_slice(),&audit])?;
        if count != 1 {
            return Err(failed_precondition("storage_import_page_receipt_conflict"));
        }
        let mut next = original.clone();
        next.next_page += 1;
        next.committed_rows = page.first + page.count;
        next.prefix = page.prefix;
        import_checkpoint_cas(&mut transaction, checked, &original, &next)?;
        // Re-read the journal/page and complete native inventory before commit.
        let observed = import_job(&mut transaction, &checked.budget, true)?
            .ok_or_else(|| data_loss("storage_import_journal_missing"))?;
        if observed != next {
            return Err(data_loss("storage_import_checkpoint_readback_mismatch"));
        }
        import_reconcile(&mut transaction, checked, &observed)?;
        let receipt = StorageImportPageReceipt {
            schema: "trillionnium.storage-import-page-receipt.v1",
            page_index: index,
            first_ordinal: page.first,
            row_count: page.count,
            page_sha256: digest_hex(page.digest),
            prefix_sha256: digest_hex(page.prefix),
            progress: import_progress(&observed, checked),
        };
        checked.budget.check()?;
        transaction.commit().map_err(map_postgres_error)?;
        checked.budget.check()?;
        Ok(receipt)
    }

    /// Read-only exact prefix reconciliation; never advances or repairs a job.
    pub fn resume_storage_import(
        &mut self,
        checked: &CheckedStorageImport<'_>,
    ) -> Result<StorageImportProgress, DomainError> {
        checked.budget.check()?;
        let mut transaction = self
            .client
            .build_transaction()
            .isolation_level(postgres::IsolationLevel::Serializable)
            .read_only(true)
            .start()
            .map_err(map_postgres_error)?;
        import_authority(&mut transaction, self.profile, checked, false)?;
        let job = import_job(&mut transaction, &checked.budget, false)?
            .ok_or_else(|| failed_precondition("storage_import_registration_required"))?;
        import_reconcile(&mut transaction, checked, &job)?;
        let progress = import_progress(&job, checked);
        checked.budget.check()?;
        transaction.commit().map_err(map_postgres_error)?;
        checked.budget.check()?;
        Ok(progress)
    }

    /// Complete-source verification additionally requires persisted completion.
    pub fn verify_applied_storage_import(
        &mut self,
        checked: &CheckedStorageImport<'_>,
    ) -> Result<StorageImportProgress, DomainError> {
        let progress = self.resume_storage_import(checked)?;
        if !progress.completed {
            return Err(failed_precondition("storage_import_incomplete"));
        }
        Ok(progress)
    }

    pub fn finalize_storage_import(
        &mut self,
        checked: &CheckedStorageImport<'_>,
    ) -> Result<StorageImportProgress, DomainError> {
        checked.budget.check()?;
        let mut transaction = self
            .client
            .build_transaction()
            .isolation_level(postgres::IsolationLevel::Serializable)
            .start()
            .map_err(map_postgres_error)?;
        import_authority(&mut transaction, self.profile, checked, true)?;
        let original = import_job(&mut transaction, &checked.budget, true)?
            .ok_or_else(|| failed_precondition("storage_import_registration_required"))?;
        import_reconcile(&mut transaction, checked, &original)?;
        if original.next_page != checked.pages.len()
            || original.committed_rows != checked.packet.rows.len()
        {
            return Err(failed_precondition("storage_import_pages_incomplete"));
        }
        let mut next = original.clone();
        next.completed = true;
        if !original.completed {
            import_checkpoint_cas(&mut transaction, checked, &original, &next)?;
        }
        let observed = import_job(&mut transaction, &checked.budget, true)?
            .ok_or_else(|| data_loss("storage_import_journal_missing"))?;
        if observed != next {
            return Err(data_loss("storage_import_checkpoint_readback_mismatch"));
        }
        // Even zero-source completion requires the real exact final inventory.
        import_reconcile(&mut transaction, checked, &observed)?;
        let progress = import_progress(&observed, checked);
        checked.budget.check()?;
        transaction.commit().map_err(map_postgres_error)?;
        checked.budget.check()?;
        Ok(progress)
    }
}

fn import_checkpoint_cas(
    transaction: &mut Transaction<'_>,
    checked: &CheckedStorageImport<'_>,
    old: &ImportJob,
    next: &ImportJob,
) -> Result<(), DomainError> {
    import_validate_job(old, checked)?;
    import_validate_job(next, checked)?;
    import_validate_transition(old, next)?;
    let next_page = import_number(next.next_page)?;
    let committed = import_number(next.committed_rows)?;
    let status = i16::from(next.completed);
    let total_rows = import_number(old.total_rows)?;
    let total_pages = import_number(old.total_pages)?;
    let old_page = import_number(old.next_page)?;
    let old_rows = import_number(old.committed_rows)?;
    let old_status = i16::from(old.completed);
    let count=import_execute(transaction,&checked.budget,"UPDATE public.trnm_storage_import_jobs SET next_page=$1,committed_rows=$2,prefix_digest=$3,status=$4 WHERE singleton=1 AND manifest_digest=$5 AND custody_digest=$6 AND source_inventory_digest=$7 AND target_schema_guard_digest=$8 AND prefix_digest=$9 AND source_profile=$10 AND source_snapshot=$11 AND audit_at_ms=$12 AND total_rows=$13 AND total_pages=$14 AND next_page=$15 AND committed_rows=$16 AND status=$17",&[&next_page,&committed,&next.prefix.get().as_bytes().as_slice(),&status,&old.manifest.get().as_bytes().as_slice(),&old.custody.get().as_bytes().as_slice(),&old.inventory.get().as_bytes().as_slice(),&old.guard.get().as_bytes().as_slice(),&old.prefix.get().as_bytes().as_slice(),&old.profile,&old.snapshot,&old.audit,&total_rows,&total_pages,&old_page,&old_rows,&old_status])?;
    if count != 1 {
        return Err(failed_precondition("storage_import_checkpoint_conflict"));
    }
    Ok(())
}

fn import_validate_transition(old: &ImportJob, next: &ImportJob) -> Result<(), DomainError> {
    let page = !old.completed
        && !next.completed
        && old.next_page.checked_add(1) == Some(next.next_page)
        && next.committed_rows > old.committed_rows;
    let completion = !old.completed
        && next.completed
        && next.next_page == old.next_page
        && next.committed_rows == old.committed_rows
        && next.prefix == old.prefix
        && old.next_page == old.total_pages
        && old.committed_rows == old.total_rows;
    if !page && !completion {
        return Err(failed_precondition(
            "storage_import_checkpoint_transition_invalid",
        ));
    }
    Ok(())
}

#[cfg(test)]
mod storage_import_repository_tests {
    use super::*;

    fn fixture_packet() -> VerifiedStorageExport {
        let mut rows = Vec::new();
        let mut inventory = b"trillionnium.storage-import-inventory.v1\0".to_vec();
        for (ordinal, (key, value, version)) in [
            ("", "null", ""),
            (".历史", "[1, null]", "Opaque历史*"),
            ("line\nkey", "{\"a\": 1}", "UPPER"),
        ]
        .into_iter()
        .enumerate()
        {
            let record = ExportRowRecord {
                ordinal,
                collection: String::new(),
                key: key.to_owned(),
                user_id: "00000000-0000-0000-0000-000000000001".to_owned(),
                public_version: version.to_owned(),
                read: 32767,
                write: 2,
                create_time: ExportTimestamp {
                    seconds: -1,
                    nanos: 999_999_000,
                },
                update_time: ExportTimestamp {
                    seconds: -10,
                    nanos: 0,
                },
                value_path: format!("values/{ordinal:08}.json"),
                value_sha256: digest_hex(IntegrityDigest::from_value(value.as_bytes())),
                value_bytes: value.len(),
            };
            let row_digest = source_row_digest(&record);
            inventory.extend_from_slice(row_digest.get().as_bytes());
            rows.push(NativeStorageImportRow {
                ordinal,
                key: StorageObjectKey::new_nakama(
                    String::new(),
                    key,
                    source_uuid(&record.user_id).unwrap(),
                )
                .unwrap(),
                value: value.to_owned(),
                public_version: PublicVersion::new(version).unwrap(),
                read: ReadPermission::from_stored(32767).unwrap(),
                write: WritePermission::from_stored(2).unwrap(),
                create: source_time(&record.create_time).unwrap(),
                update: source_time(&record.update_time).unwrap(),
                row_digest,
            });
        }
        VerifiedStorageExport {
            manifest_digest: IntegrityDigest::from_value(b"independent manifest fixture"),
            custody_digest: IntegrityDigest::from_value(b"independent custody fixture"),
            inventory_digest: IntegrityDigest::from_value(&inventory),
            profile: DatabaseProfile::PostgreSql,
            source_snapshot: "snapshot fixture".to_owned(),
            execution_class: "source-derived-native-fixture-only".to_owned(),
            page_rows: 2,
            rows,
            retained_bytes: 1000,
            source_collation_binding: ["postgresql", "UTF8", "c", "C", "C", "default", "d", "true"]
                .map(str::to_owned)
                .to_vec(),
        }
    }

    fn checked(packet: &VerifiedStorageExport) -> CheckedStorageImport<'_> {
        let target_guard = IntegrityDigest::from_value(b"target guard fixture");
        let initial_prefix = import_initial_prefix(packet, target_guard, 17);
        CheckedStorageImport {
            packet,
            options: StorageImportOptions {
                audit_at_ms: 17,
                legacy_writer_role: "drained_fixture_role".to_owned(),
                expected_target_scope: IntegrityDigest::from_value(b"external target fixture"),
            },
            target_guard,
            target_identity_classification: "unit-no-native-observation",
            projections: import_check_packet_rows(packet).unwrap(),
            pages: import_plans(packet, initial_prefix),
            initial_prefix,
            budget: ImportBudget {
                started: std::time::Instant::now(),
                statement_millis: 5000,
            },
        }
    }

    fn job(checked: &CheckedStorageImport<'_>, pages: usize) -> ImportJob {
        let (rows, prefix) = if pages == 0 {
            (0, checked.initial_prefix)
        } else {
            let page = &checked.pages[pages - 1];
            (page.first + page.count, page.prefix)
        };
        ImportJob {
            manifest: checked.packet.manifest_digest,
            custody: checked.packet.custody_digest,
            inventory: checked.packet.inventory_digest,
            guard: checked.target_guard,
            prefix,
            profile: checked.packet.profile.metadata_value().to_owned(),
            snapshot: checked.packet.source_snapshot.clone(),
            audit: 17,
            total_rows: checked.packet.rows.len(),
            total_pages: checked.pages.len(),
            next_page: pages,
            committed_rows: rows,
            completed: false,
        }
    }

    #[test]
    fn repository_packet_checks_preserve_opaque_tokens_microseconds_and_raw_acl() {
        let packet = fixture_packet();
        assert_eq!(import_check_packet_rows(&packet).unwrap().len(), 3);
        assert!(packet.rows[0].public_version.as_str().is_empty());
        assert_eq!(packet.rows[0].read.get(), 32767);
        assert_eq!(packet.rows[0].write.get(), 2);
        assert_eq!(packet.rows[0].create.nanos, 999_999_000);
        assert!(packet.rows[0].create.seconds > packet.rows[0].update.seconds);
        assert_ne!(
            packet.rows[1].public_version.as_str(),
            crate::ContentVersion::from_value(packet.rows[1].value.as_bytes()).as_str()
        );
    }

    #[test]
    fn repository_packet_checks_reject_changed_values_times_acl_and_inventory() {
        for field in 0..6 {
            let mut packet = fixture_packet();
            match field {
                0 => packet.rows[0].value = "true".to_owned(),
                1 => packet.rows[0].create.seconds += 1,
                2 => packet.rows[0].write = WritePermission::from_stored(1).unwrap(),
                3 => packet.rows[0].public_version = PublicVersion::new("rehashed").unwrap(),
                4 => packet.inventory_digest = IntegrityDigest::from_value(b"different inventory"),
                _ => packet.source_collation_binding[7] = "false".to_owned(),
            };
            assert!(import_check_packet_rows(&packet).is_err());
        }
    }

    #[test]
    fn repository_initial_prefix_binds_custody_target_audit_and_snapshot_framing() {
        let packet = fixture_packet();
        let checked = checked(&packet);
        let baseline = checked.initial_prefix;
        assert_ne!(
            baseline,
            import_initial_prefix(&packet, checked.target_guard, 18)
        );
        assert_ne!(
            baseline,
            import_initial_prefix(
                &packet,
                IntegrityDigest::from_value(b"different target"),
                17
            )
        );
        let mut changed = fixture_packet();
        changed.custody_digest = IntegrityDigest::from_value(b"different custody");
        assert_ne!(
            baseline,
            import_initial_prefix(&changed, checked.target_guard, 17)
        );
        let mut a = Vec::new();
        frame_field(&mut a, b"a");
        frame_field(&mut a, b"bc");
        let mut b = Vec::new();
        frame_field(&mut b, b"ab");
        frame_field(&mut b, b"c");
        assert_ne!(a, b);
    }

    #[test]
    fn repository_resume_checks_exact_prefix_reject_missing_rows_and_changed_identity() {
        let packet = fixture_packet();
        let checked = checked(&packet);
        let original = job(&checked, 1);
        import_validate_job(&original, &checked).unwrap();
        for field in 0..11 {
            let mut changed = original.clone();
            match field {
                0 => changed.committed_rows -= 1,
                1 => changed.prefix = checked.initial_prefix,
                2 => changed.custody = IntegrityDigest::from_value(b"different custody"),
                3 => changed.audit += 1,
                4 => changed.guard = IntegrityDigest::from_value(b"different guard"),
                5 => changed.snapshot.push('_'),
                6 => changed.manifest = IntegrityDigest::from_value(b"different manifest"),
                7 => changed.inventory = IntegrityDigest::from_value(b"different inventory"),
                8 => changed.profile = "cockroachdb".to_owned(),
                9 => changed.total_rows += 1,
                _ => changed.total_pages += 1,
            };
            assert!(import_validate_job(&changed, &checked).is_err());
        }
    }

    #[test]
    fn repository_count_complete_prefix_remains_incomplete_until_explicit_reconciliation() {
        let packet = fixture_packet();
        let checked = checked(&packet);
        let mut complete = job(&checked, 2);
        import_validate_job(&complete, &checked).unwrap();
        assert!(!import_progress(&complete, &checked).completed);
        let before = complete.clone();
        complete.completed = true;
        import_validate_transition(&before, &complete).unwrap();
        let mut early = job(&checked, 1);
        early.completed = true;
        assert!(import_validate_job(&early, &checked).is_err());
        assert!(!import_progress(&complete, &checked).compatibility_credit);
    }

    #[test]
    fn repository_checkpoint_only_allows_one_page_or_final_completion() {
        let packet = fixture_packet();
        let checked = checked(&packet);
        let initial = job(&checked, 0);
        let first = job(&checked, 1);
        let second = job(&checked, 2);
        import_validate_transition(&initial, &first).unwrap();
        assert!(import_validate_transition(&initial, &second).is_err());
        assert!(import_validate_transition(&first, &initial).is_err());
        assert!(import_validate_transition(&first, &first).is_err());
        let mut completed = second.clone();
        completed.completed = true;
        import_validate_transition(&second, &completed).unwrap();
        assert!(import_validate_transition(&completed, &second).is_err());
    }

    #[test]
    fn repository_deadline_and_pool_timeout_never_extend_the_operation_budget() {
        assert_eq!(import_statement_millis("250ms").unwrap(), 250);
        assert_eq!(import_statement_millis("30s").unwrap(), 5000);
        assert_eq!(import_statement_millis("0").unwrap(), 5000);
        for invalid in ["-1ms", "private secret path", "1.5s", ""] {
            assert!(import_statement_millis(invalid).is_err());
        }
        let budget = ImportBudget {
            started: std::time::Instant::now() - std::time::Duration::from_secs(301),
            statement_millis: 5000,
        };
        assert_eq!(
            budget.check().unwrap_err().code(),
            trnm_contracts::StableCode::Unavailable
        );
    }

    #[test]
    fn repository_empty_source_still_requires_explicit_completion() {
        let mut packet = fixture_packet();
        packet.rows.clear();
        packet.inventory_digest =
            IntegrityDigest::from_value(b"trillionnium.storage-import-inventory.v1\0");
        let checked = checked(&packet);
        assert!(checked.pages.is_empty());
        let applying = job(&checked, 0);
        assert!(!import_progress(&applying, &checked).completed);
        let mut completed = applying.clone();
        completed.completed = true;
        import_validate_transition(&applying, &completed).unwrap();
        import_validate_job(&completed, &checked).unwrap();
    }

    #[test]
    fn repository_native_key_policy_rejects_explicit_and_nondeterministic_collations() {
        import_validate_pg_key_collation("pg_catalog", "default", "d", true).unwrap();
        for (namespace, name, provider, deterministic) in [
            ("public", "default", "d", true),
            ("pg_catalog", "C", "c", true),
            ("public", "case_insensitive", "i", false),
            ("pg_catalog", "default", "d", false),
            ("pg_catalog", "default", "unknown", true),
        ] {
            assert_eq!(
                import_validate_pg_key_collation(namespace, name, provider, deterministic)
                    .unwrap_err()
                    .code(),
                trnm_contracts::StableCode::FailedPrecondition
            );
        }
        let mut null = Vec::new();
        import_frame_optional(&mut null, None);
        let mut empty = Vec::new();
        import_frame_optional(&mut empty, Some(""));
        assert_ne!(null, empty);
    }

    #[test]
    fn repository_native_key_counts_require_the_entire_inventory_before_pages() {
        import_validate_native_key_counts(0, 0, 0).unwrap();
        import_validate_native_key_counts(10_000, 10_000, 10_000).unwrap();
        // A later-page key can be equivalent under the database authority
        // even though its UTF-8 bytes differ from an earlier-page key.
        for (total, distinct, expected) in [
            (101, 100, 101),
            (100, 100, 101),
            (-1, -1, 0),
            (10_001, 10_001, 10_001),
        ] {
            assert_eq!(
                import_validate_native_key_counts(total, distinct, expected)
                    .unwrap_err()
                    .code(),
                trnm_contracts::StableCode::FailedPrecondition
            );
        }
    }
}
