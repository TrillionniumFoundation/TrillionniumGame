use std::io::Write;
use std::path::PathBuf;
use std::time::{Duration, Instant};

/// Explicit custodian inputs. Source/binary hashes are checked against actual
/// files, but the supplied Git identity is not proof of a clean Git build.
#[derive(Clone, Debug)]
pub struct StorageExportOptions {
    pub output_directory: PathBuf,
    pub page_rows: usize,
    pub execution_class: String,
    pub producer_commit: String,
    pub producer_tree: String,
    pub producer_source_file: PathBuf,
    pub producer_source_sha256: String,
    pub producer_binary_sha256: String,
    pub execution_id: String,
    pub upstream_directory: PathBuf,
}

#[derive(Clone, Debug, Serialize)]
pub struct StorageExportSummary {
    pub schema: &'static str,
    pub profile: &'static str,
    pub row_count: usize,
    pub page_count: usize,
    pub manifest_sha256: String,
    pub receipt_sha256: String,
    pub producer_source_sha256: String,
    pub producer_binary_sha256: String,
    pub execution_class: String,
    pub compatibility_credit: bool,
    pub production_ready: bool,
    pub full_nakama_replacement: bool,
}

const EXPORT_OPERATION_BUDGET: Duration = Duration::from_secs(300);
const EXPORT_STATEMENT_BUDGET: Duration = Duration::from_secs(5);
const SOURCE_OWNER_REFERENCE_QUERY: &str = "SELECT COUNT(*)::BIGINT, COUNT(*) FILTER (WHERE u.id IS NULL)::BIGINT FROM public.storage s LEFT JOIN public.users u ON u.id = s.user_id";
const SOURCE_TIMESTAMP_EQUALITY_QUERY: &str = "SELECT create_time = $4::TIMESTAMPTZ AND update_time = $5::TIMESTAMPTZ FROM public.storage WHERE collection = $1::TEXT AND key = $2::TEXT AND user_id = $3::TEXT::UUID";
const SOURCE_COLUMN_QUERY: &str = "SELECT column_name, udt_name, is_nullable = 'YES', character_maximum_length::BIGINT, CASE WHEN octet_length(column_default) <= 16384 THEN column_default ELSE NULL END, column_default IS NULL OR octet_length(column_default) <= 16384 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'storage' ORDER BY ordinal_position LIMIT 10";
const SOURCE_CONSTRAINT_QUERY: &str = "SELECT CASE WHEN octet_length(pg_catalog.pg_get_constraintdef(c.oid)) <= 8192 THEN jsonb_build_object('name', c.conname, 'kind', c.contype::TEXT, 'validated', c.convalidated, 'definition', pg_catalog.pg_get_constraintdef(c.oid), 'columns', c.conkey, 'parent_columns', c.confkey, 'parent_schema', pn.nspname, 'parent_table', pr.relname, 'delete_action', c.confdeltype::TEXT)::TEXT ELSE NULL END FROM pg_catalog.pg_constraint c JOIN pg_catalog.pg_class r ON r.oid = c.conrelid JOIN pg_catalog.pg_namespace n ON n.oid = r.relnamespace LEFT JOIN pg_catalog.pg_class pr ON pr.oid = c.confrelid LEFT JOIN pg_catalog.pg_namespace pn ON pn.oid = pr.relnamespace WHERE n.nspname = 'public' AND r.relname = 'storage' ORDER BY c.conname LIMIT 5";

const SOURCE_POSTGRES_COLLATION_QUERY: &str = "SELECT pg_catalog.pg_encoding_to_char(d.encoding), d.datlocprovider::TEXT, CASE WHEN octet_length(d.datcollate) <= 1024 THEN d.datcollate ELSE NULL END AS datcollate, CASE WHEN octet_length(d.datctype) <= 1024 THEN d.datctype ELSE NULL END AS datctype, a.attname::TEXT, c.collname::TEXT, cn.nspname::TEXT, c.collprovider::TEXT, c.collisdeterministic FROM pg_catalog.pg_database d CROSS JOIN pg_catalog.pg_class r JOIN pg_catalog.pg_namespace n ON n.oid = r.relnamespace JOIN pg_catalog.pg_attribute a ON a.attrelid = r.oid JOIN pg_catalog.pg_collation c ON c.oid = a.attcollation JOIN pg_catalog.pg_namespace cn ON cn.oid = c.collnamespace WHERE d.datname = current_database() AND n.nspname = 'public' AND r.relname = 'storage' AND a.attname IN ('collection', 'key') AND NOT a.attisdropped ORDER BY a.attname";
const SOURCE_COCKROACH_COLLATION_QUERY: &str = "SELECT column_name, collation_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'storage' AND column_name IN ('collection', 'key') ORDER BY column_name";

fn export_collation_binding(
    transaction: &mut Transaction<'_>,
    profile: DatabaseProfile,
    deadline: Instant,
) -> Result<Vec<String>, DomainError> {
    export_statement(transaction, deadline)?;
    let rows = transaction
        .query(
            match profile {
                DatabaseProfile::PostgreSql => SOURCE_POSTGRES_COLLATION_QUERY,
                DatabaseProfile::CockroachDb => SOURCE_COCKROACH_COLLATION_QUERY,
            },
            &[],
        )
        .map_err(map_postgres_error)?;
    if rows.len() != 2 {
        return Err(invalid("storage_export_collation_unsupported"));
    }
    let mut binding = None;
    for (row, name) in rows.iter().zip(["collection", "key"]) {
        let current = match profile {
            DatabaseProfile::PostgreSql => {
                let field = |index| {
                    row.try_get::<_, String>(index)
                        .map_err(|_| invalid("storage_export_collation_unsupported"))
                };
                if field(4)? != name || field(6)? != "pg_catalog" {
                    return Err(invalid("storage_export_collation_unsupported"));
                }
                let locale = |index| {
                    row.try_get::<_, Option<String>>(index)
                        .map_err(|_| invalid("storage_export_collation_unsupported"))?
                        .ok_or_else(|| invalid("storage_export_collation_unsupported"))
                };
                let deterministic: bool = row
                    .try_get(8)
                    .map_err(|_| invalid("storage_export_collation_unsupported"))?;
                vec![
                    "postgresql".to_owned(),
                    field(0)?,
                    field(1)?,
                    locale(2)?,
                    locale(3)?,
                    field(5)?,
                    field(7)?,
                    deterministic.to_string(),
                ]
            }
            DatabaseProfile::CockroachDb => {
                let actual_name: String = row
                    .try_get(0)
                    .map_err(|_| invalid("storage_export_collation_unsupported"))?;
                let collation: Option<String> = row
                    .try_get(1)
                    .map_err(|_| invalid("storage_export_collation_unsupported"))?;
                if actual_name != name || collation.is_some() {
                    return Err(invalid("storage_export_collation_unsupported"));
                }
                vec![
                    "cockroachdb".to_owned(),
                    "UTF8".to_owned(),
                    "uncollated".to_owned(),
                ]
            }
        };
        if !check_source_collation_binding(&current, profile.metadata_value())
            || binding.as_ref().is_some_and(|prior| prior != &current)
        {
            return Err(invalid("storage_export_collation_unsupported"));
        }
        binding = Some(current);
    }
    binding.ok_or_else(|| invalid("storage_export_collation_unsupported"))
}

fn export_budget() -> DomainError {
    DomainError::new(
        trnm_contracts::StableCode::ResourceExhausted,
        "storage_export_resource_budget",
        trnm_contracts::RetryClass::Never,
    )
}

fn export_io() -> DomainError {
    DomainError::new(
        trnm_contracts::StableCode::Unavailable,
        "storage_export_file_operation_failed",
        trnm_contracts::RetryClass::Never,
    )
}

fn export_deadline(deadline: Instant) -> Result<Duration, DomainError> {
    deadline
        .checked_duration_since(Instant::now())
        .filter(|remaining| remaining.as_millis() > 0)
        .ok_or_else(export_budget)
}

fn export_statement(
    transaction: &mut Transaction<'_>,
    deadline: Instant,
) -> Result<(), DomainError> {
    let milliseconds = export_deadline(deadline)?
        .min(EXPORT_STATEMENT_BUDGET)
        .as_millis();
    // Only bounded internally generated numeric SQL enters SET LOCAL.
    transaction
        .batch_execute(&format!("SET LOCAL statement_timeout = {milliseconds}"))
        .map_err(map_postgres_error)
}

fn export_read_file(path: &Path, limit: usize, deadline: Instant) -> Result<Vec<u8>, DomainError> {
    export_deadline(deadline)?;
    let mut file = File::open(path).map_err(|_| export_io())?;
    let metadata = file.metadata().map_err(|_| export_io())?;
    if !metadata.is_file() || metadata.len() > u64::try_from(limit).map_err(|_| export_budget())? {
        return Err(export_budget());
    }
    let mut result =
        Vec::with_capacity(usize::try_from(metadata.len()).map_err(|_| export_budget())?);
    let mut chunk = [0_u8; 64 * 1024];
    loop {
        export_deadline(deadline)?;
        let count = file.read(&mut chunk).map_err(|_| export_io())?;
        if count == 0 {
            break;
        }
        if result
            .len()
            .checked_add(count)
            .is_none_or(|length| length > limit)
        {
            return Err(export_budget());
        }
        result.extend_from_slice(&chunk[..count]);
    }
    if result.len() != usize::try_from(metadata.len()).map_err(|_| export_budget())?
        || file.metadata().map_err(|_| export_io())?.len() != metadata.len()
    {
        return Err(data_loss("storage_export_input_changed"));
    }
    export_deadline(deadline)?;
    Ok(result)
}

struct ExportOwner([u8; 16]);

impl<'a> postgres::types::FromSql<'a> for ExportOwner {
    fn from_sql(
        kind: &postgres::types::Type,
        raw: &'a [u8],
    ) -> Result<Self, Box<dyn std::error::Error + Sync + Send>> {
        if !Self::accepts(kind) {
            return Err(Box::new(data_loss("storage_export_owner_invalid")));
        }
        Ok(Self(
            raw.try_into()
                .map_err(|_| data_loss("storage_export_owner_invalid"))?,
        ))
    }
    fn accepts(kind: &postgres::types::Type) -> bool {
        *kind == postgres::types::Type::UUID
    }
}

impl ExportOwner {
    fn text(&self) -> String {
        let mut result = String::with_capacity(36);
        const HEX: &[u8; 16] = b"0123456789abcdef";
        for (index, byte) in self.0.iter().enumerate() {
            if matches!(index, 4 | 6 | 8 | 10) {
                result.push('-');
            }
            result.push(char::from(HEX[usize::from(byte >> 4)]));
            result.push(char::from(HEX[usize::from(byte & 15)]));
        }
        result
    }
}

struct ExportFiles {
    directory: File,
    children: BTreeMap<String, File>,
    members: Vec<PacketMember>,
    total: usize,
    deadline: Instant,
    completed: bool,
}

impl ExportFiles {
    fn create(root: &Path, deadline: Instant) -> Result<Self, DomainError> {
        use std::os::unix::fs::DirBuilderExt;
        export_deadline(deadline)?;
        std::fs::DirBuilder::new()
            .mode(0o700)
            .create(root)
            .map_err(|_| export_io())?;
        let directory = File::from(
            rustix::fs::open(
                root,
                rustix::fs::OFlags::RDONLY
                    | rustix::fs::OFlags::DIRECTORY
                    | rustix::fs::OFlags::NOFOLLOW
                    | rustix::fs::OFlags::CLOEXEC,
                rustix::fs::Mode::empty(),
            )
            .map_err(|_| export_io())?,
        );
        let mut children = BTreeMap::new();
        for name in ["values", "upstream", "producer-source"] {
            rustix::fs::mkdirat(
                &directory,
                name,
                rustix::fs::Mode::RUSR | rustix::fs::Mode::WUSR | rustix::fs::Mode::XUSR,
            )
            .map_err(|_| export_io())?;
            let child = File::from(
                rustix::fs::openat(
                    &directory,
                    name,
                    rustix::fs::OFlags::RDONLY
                        | rustix::fs::OFlags::DIRECTORY
                        | rustix::fs::OFlags::NOFOLLOW
                        | rustix::fs::OFlags::CLOEXEC,
                    rustix::fs::Mode::empty(),
                )
                .map_err(|_| export_io())?,
            );
            children.insert(name.to_owned(), child);
        }
        Ok(Self {
            directory,
            children,
            members: Vec::new(),
            total: 0,
            deadline,
            completed: false,
        })
    }

    fn write(&mut self, path: &str, bytes: &[u8], declared: bool) -> Result<(), DomainError> {
        export_deadline(self.deadline)?;
        if self
            .total
            .checked_add(bytes.len())
            .is_none_or(|total| total > MAX_PACKET_BYTES)
            || (declared && self.members.len() >= MAX_ROWS + 7)
        {
            return Err(export_budget());
        }
        let (directory, leaf) = if let Some((prefix, leaf)) = path.split_once('/') {
            (self.children.get(prefix).ok_or_else(export_io)?, leaf)
        } else {
            (&self.directory, path)
        };
        if leaf.is_empty() || leaf.contains(['/', '\\']) || matches!(leaf, "." | "..") {
            return Err(export_io());
        }
        let mut file = File::from(
            rustix::fs::openat(
                directory,
                leaf,
                rustix::fs::OFlags::WRONLY
                    | rustix::fs::OFlags::CREATE
                    | rustix::fs::OFlags::EXCL
                    | rustix::fs::OFlags::NOFOLLOW
                    | rustix::fs::OFlags::CLOEXEC,
                rustix::fs::Mode::RUSR | rustix::fs::Mode::WUSR,
            )
            .map_err(|_| export_io())?,
        );
        for chunk in bytes.chunks(64 * 1024) {
            export_deadline(self.deadline)?;
            file.write_all(chunk).map_err(|_| export_io())?;
        }
        file.sync_all().map_err(|_| export_io())?;
        export_deadline(self.deadline)?;
        self.total += bytes.len();
        if declared {
            self.members.push(PacketMember {
                path: path.to_owned(),
                bytes: bytes.len(),
                sha256: digest_hex(IntegrityDigest::from_value(bytes)),
            });
        }
        Ok(())
    }

    fn sync_directories(&self) -> Result<(), DomainError> {
        for directory in self.children.values() {
            export_deadline(self.deadline)?;
            directory.sync_all().map_err(|_| export_io())?;
        }
        self.directory.sync_all().map_err(|_| export_io())?;
        export_deadline(self.deadline)?;
        Ok(())
    }
}

impl Drop for ExportFiles {
    fn drop(&mut self) {
        if !self.completed {
            let _ = rustix::fs::unlinkat(
                &self.directory,
                "source-receipt.json",
                rustix::fs::AtFlags::empty(),
            );
            let _ = rustix::fs::unlinkat(
                &self.directory,
                "manifest.json",
                rustix::fs::AtFlags::empty(),
            );
        }
    }
}

fn export_catalog(
    transaction: &mut Transaction<'_>,
    profile: DatabaseProfile,
    snapshot: &str,
    deadline: Instant,
) -> Result<ExportSourceCatalog, DomainError> {
    export_statement(transaction, deadline)?;
    let table = transaction.query_one("SELECT t.table_type, r.relkind::TEXT FROM information_schema.tables t JOIN pg_catalog.pg_namespace n ON n.nspname=t.table_schema JOIN pg_catalog.pg_class r ON r.relnamespace=n.oid AND r.relname=t.table_name WHERE t.table_schema='public' AND t.table_name='storage'",&[]).map_err(map_postgres_error)?;
    export_statement(transaction, deadline)?;
    let columns = transaction
        .query(SOURCE_COLUMN_QUERY, &[])
        .map_err(map_postgres_error)?
        .into_iter()
        .map(|row| {
            if !row
                .try_get::<_, bool>(5)
                .map_err(|_| data_loss("storage_export_catalog_invalid"))?
            {
                return Err(export_budget());
            }
            Ok(ExportSourceColumn {
                name: row
                    .try_get(0)
                    .map_err(|_| data_loss("storage_export_catalog_invalid"))?,
                udt: row
                    .try_get(1)
                    .map_err(|_| data_loss("storage_export_catalog_invalid"))?,
                nullable: row
                    .try_get(2)
                    .map_err(|_| data_loss("storage_export_catalog_invalid"))?,
                character_maximum_length: row
                    .try_get(3)
                    .map_err(|_| data_loss("storage_export_catalog_invalid"))?,
                default_expression: row
                    .try_get(4)
                    .map_err(|_| data_loss("storage_export_catalog_invalid"))?,
            })
        })
        .collect::<Result<Vec<_>, DomainError>>()?;
    export_statement(transaction, deadline)?;
    let constraints = transaction
        .query(SOURCE_CONSTRAINT_QUERY, &[])
        .map_err(map_postgres_error)?
        .into_iter()
        .map(|row| {
            let raw: String = row
                .try_get::<_, Option<String>>(0)
                .map_err(|_| data_loss("storage_export_catalog_invalid"))?
                .ok_or_else(export_budget)?;
            if raw.len() > 16 * 1024 {
                return Err(export_budget());
            }
            decode_packet_json(raw.as_bytes())
        })
        .collect::<Result<Vec<ExportSourceConstraint>, DomainError>>()?;
    // These derived fields are accepted only with the complete actual constraint
    // descriptors below, checked against both profiles' native deparser pins.
    Ok(ExportSourceCatalog {
        schema: "trillionnium.nakama-storage-source-catalog.v1".to_owned(),
        profile: profile.metadata_value().to_owned(),
        namespace: "public".to_owned(),
        table: "storage".to_owned(),
        table_type: table
            .try_get(0)
            .map_err(|_| data_loss("storage_export_catalog_invalid"))?,
        table_kind: table
            .try_get(1)
            .map_err(|_| data_loss("storage_export_catalog_invalid"))?,
        collation_binding: export_collation_binding(transaction, profile, deadline)?,
        snapshot_identity: snapshot.to_owned(),
        columns,
        constraints,
        primary_key_columns: vec![
            "collection".to_owned(),
            "key".to_owned(),
            "user_id".to_owned(),
        ],
        owner_foreign_key_columns: vec!["user_id".to_owned()],
        owner_foreign_key_table: "users".to_owned(),
        owner_foreign_key_parent_schema: "public".to_owned(),
        owner_foreign_key_parent_columns: vec!["id".to_owned()],
        owner_foreign_key_delete_cascade: true,
        owner_foreign_key_validated: true,
        read_nonnegative_check_validated: true,
        write_nonnegative_check_validated: true,
    })
}

impl crate::PgRepository {
    /// Produce an offline packet from one native read-only transaction. It does
    /// not acquire source write authority, execute Nakama, prove a clean build,
    /// or grant source export custody/production compatibility automatically.
    pub fn export_storage_snapshot(
        &mut self,
        options: &StorageExportOptions,
    ) -> Result<StorageExportSummary, DomainError> {
        let deadline = Instant::now()
            .checked_add(EXPORT_OPERATION_BUDGET)
            .ok_or_else(export_budget)?;
        if !(1..=MAX_PAGE_ROWS).contains(&options.page_rows)
            || !matches!(
                options.execution_class.as_str(),
                "native-source-ddl-fixture" | "custodian-native-database-snapshot"
            )
            || !checked_commit(&options.producer_commit)
            || !checked_commit(&options.producer_tree)
            || options.execution_id.is_empty()
            || options.execution_id.len() > 256
            || options.execution_id.chars().any(char::is_control)
            || options
                .producer_source_file
                .file_name()
                .and_then(|name| name.to_str())
                != Some("exporter.rs")
        {
            return Err(invalid("storage_export_options_invalid"));
        }
        let source_bytes =
            export_read_file(&options.producer_source_file, MAX_METADATA_BYTES, deadline)?;
        if !parse_digest(&options.producer_source_sha256)?.matches_value(&source_bytes) {
            return Err(invalid("storage_export_producer_source_mismatch"));
        }
        // Linux /proc/self/exe refers to the executing image inode, even if its
        // pathname is replaced. No Git-to-binary reproducibility claim follows.
        let executable = export_read_file(Path::new("/proc/self/exe"), MAX_PACKET_BYTES, deadline)?;
        if !parse_digest(&options.producer_binary_sha256)?.matches_value(&executable) {
            return Err(invalid("storage_export_producer_binary_mismatch"));
        }
        drop(executable);
        let mut upstream = Vec::with_capacity(PINNED_SOURCE_MEMBERS.len());
        for (path, length, hash) in PINNED_SOURCE_MEMBERS {
            let input = options.upstream_directory.join(
                Path::new(path)
                    .file_name()
                    .ok_or_else(|| invalid("storage_export_upstream_path_invalid"))?,
            );
            let bytes = export_read_file(&input, MAX_METADATA_BYTES, deadline)?;
            if bytes.len() != length || !parse_digest(hash)?.matches_value(&bytes) {
                return Err(invalid("storage_export_upstream_source_mismatch"));
            }
            upstream.push((path, bytes));
        }
        let profile = self.profile;
        export_deadline(deadline)?;
        let mut transaction = self
            .client
            .build_transaction()
            .isolation_level(match profile {
                DatabaseProfile::PostgreSql => postgres::IsolationLevel::RepeatableRead,
                DatabaseProfile::CockroachDb => postgres::IsolationLevel::Serializable,
            })
            .read_only(true)
            .start()
            .map_err(map_postgres_error)?;
        export_statement(&mut transaction, deadline)?;
        transaction
            .batch_execute("SET LOCAL search_path = pg_catalog, public")
            .map_err(map_postgres_error)?;
        export_statement(&mut transaction, deadline)?;
        let identity = transaction
            .query_one(
                match profile {
                    DatabaseProfile::PostgreSql => {
                        "SELECT version(), current_database(), pg_current_snapshot()::TEXT"
                    }
                    DatabaseProfile::CockroachDb => {
                        "SELECT version(), current_database(), transaction_timestamp()::TEXT"
                    }
                },
                &[],
            )
            .map_err(map_postgres_error)?;
        let native_snapshot: String = identity
            .try_get(2)
            .map_err(|_| data_loss("storage_export_identity_invalid"))?;
        let source = ExportSource {
            repository: UPSTREAM_REPOSITORY.to_owned(),
            commit: UPSTREAM_COMMIT.to_owned(),
            tree: UPSTREAM_TREE.to_owned(),
            profile: profile.metadata_value().to_owned(),
            server_version: identity
                .try_get(0)
                .map_err(|_| data_loss("storage_export_identity_invalid"))?,
            database_identity: identity
                .try_get(1)
                .map_err(|_| data_loss("storage_export_identity_invalid"))?,
            snapshot_identity: match profile {
                DatabaseProfile::PostgreSql => {
                    format!("postgresql-native-snapshot:{native_snapshot}")
                }
                DatabaseProfile::CockroachDb => {
                    format!("cockroachdb-native-transaction-clock:{native_snapshot}")
                }
            },
            isolation: match profile {
                DatabaseProfile::PostgreSql => "repeatable-read-read-only",
                DatabaseProfile::CockroachDb => "serializable-read-only",
            }
            .to_owned(),
            execution_class: options.execution_class.clone(),
            whole_table: true,
            owner_references_valid: true,
        };
        source_profile(&source)?;
        let catalog = export_catalog(
            &mut transaction,
            profile,
            &source.snapshot_identity,
            deadline,
        )?;
        let catalog_bytes =
            serde_json::to_vec(&catalog).map_err(|_| invalid("storage_export_catalog_invalid"))?;
        if catalog_bytes.len() > MAX_METADATA_BYTES {
            return Err(export_budget());
        }
        check_source_catalog(&catalog_bytes, &source)?;
        export_statement(&mut transaction, deadline)?;
        let counts = transaction
            .query_one(SOURCE_OWNER_REFERENCE_QUERY, &[])
            .map_err(map_postgres_error)?;
        let row_count = usize::try_from(
            counts
                .try_get::<_, i64>(0)
                .map_err(|_| data_loss("storage_export_count_invalid"))?,
        )
        .map_err(|_| export_budget())?;
        let orphan_count: i64 = counts
            .try_get(1)
            .map_err(|_| data_loss("storage_export_count_invalid"))?;
        if orphan_count != 0 {
            return Err(data_loss("storage_export_owner_reference_invalid"));
        }
        if row_count > MAX_ROWS || row_count.div_ceil(options.page_rows) > 100 {
            return Err(export_budget());
        }
        let producer = ExportProducer {
            repository: "TrillionniumFoundation/TrillionniumGame".to_owned(),
            commit: options.producer_commit.clone(),
            tree: options.producer_tree.clone(),
            source_sha256: options.producer_source_sha256.clone(),
            binary_sha256: options.producer_binary_sha256.clone(),
            execution_id: options.execution_id.clone(),
        };
        let mut files = ExportFiles::create(&options.output_directory, deadline)?;
        for (path, bytes) in &upstream {
            files.write(path, bytes, true)?;
        }
        files.write("producer-source/exporter.rs", &source_bytes, true)?;
        files.write("source-catalog.json", &catalog_bytes, true)?;
        files.write("source-query.sql", STORAGE_EXPORT_QUERY.as_bytes(), true)?;
        let mut records = Vec::new();
        let mut exported = 0_usize;
        let mut position = (String::new(), String::new(), ExportOwner([0; 16]).text());
        let mut after = false;
        loop {
            export_statement(&mut transaction, deadline)?;
            let limit = i64::try_from(if exported == row_count {
                1
            } else {
                options.page_rows.min(row_count - exported)
            })
            .map_err(|_| export_budget())?;
            let page = transaction
                .query(
                    STORAGE_EXPORT_QUERY,
                    &[&position.0, &position.1, &position.2, &after, &limit],
                )
                .map_err(map_postgres_error)?;
            if exported == row_count {
                if !page.is_empty() {
                    return Err(data_loss("storage_export_snapshot_count_changed"));
                }
                break;
            }
            if page.is_empty()
                || page.len() > options.page_rows
                || page.len() > row_count - exported
            {
                return Err(data_loss("storage_export_snapshot_count_changed"));
            }
            let mut page_bytes = 0_usize;
            for row in page {
                export_deadline(deadline)?;
                let value: String = row
                    .try_get::<_, Option<String>>(3)
                    .map_err(|_| data_loss("storage_export_native_value_invalid"))?
                    .ok_or_else(export_budget)?;
                page_bytes = page_bytes
                    .checked_add(value.len())
                    .ok_or_else(export_budget)?;
                if value.len() > MAX_ROW_BYTES || page_bytes > MAX_PAGE_BYTES {
                    return Err(export_budget());
                }
                let collection: String = row
                    .try_get(0)
                    .map_err(|_| data_loss("storage_export_key_invalid"))?;
                let key: String = row
                    .try_get(1)
                    .map_err(|_| data_loss("storage_export_key_invalid"))?;
                let owner: ExportOwner = row
                    .try_get(2)
                    .map_err(|_| data_loss("storage_export_owner_invalid"))?;
                StorageObjectKey::new_nakama(
                    collection.clone(),
                    key.clone(),
                    UserId::new(owner.0),
                )?;
                let version: String = row
                    .try_get(4)
                    .map_err(|_| data_loss("storage_export_version_invalid"))?;
                PublicVersion::new(version.clone())?;
                let read: i16 = row
                    .try_get(5)
                    .map_err(|_| data_loss("storage_export_permission_invalid"))?;
                let write: i16 = row
                    .try_get(6)
                    .map_err(|_| data_loss("storage_export_permission_invalid"))?;
                ReadPermission::from_stored(read)?;
                WritePermission::from_stored(write)?;
                let create: StorageTimestamp = row
                    .try_get(7)
                    .map_err(|_| data_loss("storage_export_timestamp_invalid"))?;
                let update: StorageTimestamp = row
                    .try_get(8)
                    .map_err(|_| data_loss("storage_export_timestamp_invalid"))?;
                let user_id = owner.text();
                export_statement(&mut transaction, deadline)?;
                let equal = transaction
                    .query_one(
                        SOURCE_TIMESTAMP_EQUALITY_QUERY,
                        &[&collection, &key, &user_id, &create, &update],
                    )
                    .map_err(map_postgres_error)?;
                if !equal
                    .try_get::<_, bool>(0)
                    .map_err(|_| data_loss("storage_export_timestamp_precision_invalid"))?
                {
                    return Err(data_loss("storage_export_timestamp_precision_invalid"));
                }
                let path = format!("values/{exported:08}.json");
                let record = ExportRowRecord {
                    ordinal: exported,
                    collection,
                    key,
                    user_id,
                    public_version: version,
                    read,
                    write,
                    create_time: ExportTimestamp {
                        seconds: create.seconds,
                        nanos: create.nanos,
                    },
                    update_time: ExportTimestamp {
                        seconds: update.seconds,
                        nanos: update.nanos,
                    },
                    value_path: path.clone(),
                    value_sha256: digest_hex(IntegrityDigest::from_value(value.as_bytes())),
                    value_bytes: value.len(),
                };
                let encoded = serde_json::to_vec(&record)
                    .map_err(|_| invalid("storage_export_row_metadata_invalid"))?;
                if encoded.len() > 16 * 1024
                    || records
                        .len()
                        .checked_add(encoded.len() + 1)
                        .is_none_or(|length| length > MAX_ROWS_BYTES)
                {
                    return Err(export_budget());
                }
                records.extend_from_slice(&encoded);
                records.push(b'\n');
                files.write(&path, value.as_bytes(), true)?;
                position = (record.collection, record.key, record.user_id);
                after = true;
                exported += 1;
            }
        }
        if exported != row_count {
            return Err(data_loss("storage_export_snapshot_incomplete"));
        }
        files.write("rows.ndjson", &records, true)?;
        files.sync_directories()?;
        export_statement(&mut transaction, deadline)?;
        transaction.commit().map_err(map_postgres_error)?;
        export_deadline(deadline)?;
        // Completion flags are published only after the native RO commit and
        // all described file bodies have succeeded and been fsynced.
        let manifest = ExportManifest {
            schema: "trillionnium.nakama-storage-native-export.v1".to_owned(),
            project_id: "trillionnium-game".to_owned(),
            completed: true,
            source: source.clone(),
            producer: producer.clone(),
            total_rows: exported,
            page_rows: options.page_rows,
            members: files.members.clone(),
        };
        let manifest_raw = serde_json::to_vec(&manifest)
            .map_err(|_| invalid("storage_export_manifest_invalid"))?;
        if manifest_raw.len() > MAX_MANIFEST_BYTES {
            return Err(export_budget());
        }
        let manifest_sha256 = digest_hex(IntegrityDigest::from_value(&manifest_raw));
        let receipt = ExportSourceReceipt {
            schema: "trillionnium.nakama-storage-source-receipt.v1".to_owned(),
            manifest_sha256: manifest_sha256.clone(),
            producer,
            source,
            row_count: exported,
            completed: true,
            compatibility_credit: false,
            production_ready: false,
            full_nakama_replacement: false,
        };
        let receipt_raw =
            serde_json::to_vec(&receipt).map_err(|_| invalid("storage_export_receipt_invalid"))?;
        if receipt_raw.len() > MAX_METADATA_BYTES {
            return Err(export_budget());
        }
        files.write("manifest.json", &manifest_raw, false)?;
        files.write("source-receipt.json", &receipt_raw, false)?;
        files.sync_directories()?;
        let summary = StorageExportSummary {
            schema: "trillionnium.storage-export-summary.v1",
            profile: profile.metadata_value(),
            row_count: exported,
            page_count: exported.div_ceil(options.page_rows),
            manifest_sha256,
            receipt_sha256: digest_hex(IntegrityDigest::from_value(&receipt_raw)),
            producer_source_sha256: options.producer_source_sha256.clone(),
            producer_binary_sha256: options.producer_binary_sha256.clone(),
            execution_class: options.execution_class.clone(),
            compatibility_credit: false,
            production_ready: false,
            full_nakama_replacement: false,
        };
        files.completed = true;
        Ok(summary)
    }
}

#[cfg(test)]
mod export_tests {
    use super::*;
    use postgres::types::{FromSql, Type};

    #[test]
    fn native_source_uuid_codec_checks_actual_uuid_width_and_keeps_zero_owner() {
        let owner = ExportOwner::from_sql(&Type::UUID, &[0; 16]).unwrap();
        assert_eq!(owner.text(), "00000000-0000-0000-0000-000000000000");
        assert_eq!(source_uuid(&owner.text()).unwrap(), UserId::new([0; 16]));
        for raw in [vec![], vec![0; 15], vec![0; 17]] {
            assert!(ExportOwner::from_sql(&Type::UUID, &raw).is_err());
        }
        assert!(ExportOwner::from_sql(&Type::BYTEA, &[0; 16]).is_err());
    }

    #[test]
    fn native_export_packet_files_are_private_create_new_and_incomplete_on_failure() {
        use std::os::unix::fs::{symlink, PermissionsExt};
        let root = std::env::temp_dir().join(format!(
            "trnm-export-files-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let outside = root.with_extension("outside");
        std::fs::create_dir(&outside).unwrap();
        let deadline = Instant::now() + EXPORT_OPERATION_BUDGET;
        let mut files = ExportFiles::create(&root, deadline).unwrap();
        assert_eq!(
            std::fs::metadata(&root).unwrap().permissions().mode() & 0o777,
            0o700
        );
        assert!(ExportFiles::create(&root, deadline).is_err());
        files.write("source-catalog.json", b"first", true).unwrap();
        assert!(files.write("source-catalog.json", b"second", true).is_err());
        assert_eq!(
            std::fs::read(root.join("source-catalog.json")).unwrap(),
            b"first"
        );
        assert_eq!(
            std::fs::metadata(root.join("source-catalog.json"))
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
        // A replaced intermediate path cannot redirect the writer's held FD.
        std::fs::rename(root.join("values"), root.join("original-values")).unwrap();
        symlink(&outside, root.join("values")).unwrap();
        files.write("values/00000000.json", b"null", true).unwrap();
        assert_eq!(
            std::fs::read(root.join("original-values/00000000.json")).unwrap(),
            b"null"
        );
        assert!(!outside.join("00000000.json").exists());
        assert!(files.write("values/../escape", b"bad", true).is_err());
        files.write("manifest.json", b"incomplete", false).unwrap();
        files
            .write("source-receipt.json", b"incomplete", false)
            .unwrap();
        drop(files);
        assert!(!root.join("manifest.json").exists());
        assert!(!root.join("source-receipt.json").exists());
        assert_eq!(
            std::fs::read(root.join("source-catalog.json")).unwrap(),
            b"first"
        );
        std::fs::remove_dir_all(&root).unwrap();
        std::fs::remove_dir(&outside).unwrap();
    }

    #[test]
    fn native_export_expired_and_packet_budgets_fail_before_file_publication() {
        let root = std::env::temp_dir().join(format!(
            "trnm-export-budget-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        assert!(ExportFiles::create(&root, Instant::now() - Duration::from_secs(1)).is_err());
        assert!(!root.exists());
        let mut files =
            ExportFiles::create(&root, Instant::now() + EXPORT_OPERATION_BUDGET).unwrap();
        files.total = MAX_PACKET_BYTES;
        assert!(files.write("manifest.json", b"overflow", false).is_err());
        assert!(!root.join("manifest.json").exists());
        files.total = 0;
        files.members.resize_with(MAX_ROWS + 7, || PacketMember {
            path: String::new(),
            bytes: 0,
            sha256: String::new(),
        });
        assert!(files
            .write("rows.ndjson", b"too-many-members", true)
            .is_err());
        assert!(!root.join("rows.ndjson").exists());
        drop(files);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn native_export_collation_binding_rejects_unsupported_and_unbounded_catalog() {
        let pg = vec![
            "postgresql",
            "UTF8",
            "c",
            "C.UTF-8",
            "C.UTF-8",
            "default",
            "d",
            "true",
        ]
        .into_iter()
        .map(str::to_owned)
        .collect::<Vec<_>>();
        assert!(check_source_collation_binding(&pg, "postgresql"));
        for (index, value) in [
            (0, "cockroachdb"),
            (1, "LATIN1"),
            (2, "i"),
            (3, ""),
            (4, "C\n"),
            (5, "explicit"),
            (6, "c"),
            (7, "false"),
        ] {
            let mut bad = pg.clone();
            bad[index] = value.to_owned();
            assert!(!check_source_collation_binding(&bad, "postgresql"));
        }
        let mut too_long = pg.clone();
        too_long[3] = "C".repeat(1025);
        assert!(!check_source_collation_binding(&too_long, "postgresql"));
        let cr = vec!["cockroachdb", "UTF8", "uncollated"]
            .into_iter()
            .map(str::to_owned)
            .collect::<Vec<_>>();
        assert!(check_source_collation_binding(&cr, "cockroachdb"));
        assert!(!check_source_collation_binding(&pg, "cockroachdb"));
        assert!(!check_source_collation_binding(&cr, "postgresql"));
    }
}
