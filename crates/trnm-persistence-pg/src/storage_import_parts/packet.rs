const UPSTREAM_REPOSITORY: &str = "heroiclabs/nakama";
const UPSTREAM_COMMIT: &str = "d4d92f93f78bbbe62c7fc50a3f85c772ec121a09";
const UPSTREAM_TREE: &str = "f3c9cfc2726d5543da1564629170f35b98e3797d";
const STORAGE_EXPORT_QUERY: &str = "WITH page AS MATERIALIZED (SELECT collection, key, user_id, value, version, read, write, create_time, update_time FROM public.storage WHERE ($4::BOOL = FALSE OR (collection, key, user_id) > ($1::TEXT, $2::TEXT, $3::TEXT::UUID)) ORDER BY collection, key, user_id LIMIT $5), budget AS (SELECT SUM(octet_length(value::TEXT)) AS value_bytes FROM page) SELECT collection, key, user_id, CASE WHEN octet_length(value::TEXT) <= 16777216 AND budget.value_bytes <= 33554432 THEN value::TEXT ELSE NULL END AS native_value, version, read, write, create_time, update_time FROM page CROSS JOIN budget ORDER BY collection, key, user_id";
const PINNED_SOURCE_MEMBERS: [(&str, usize, &str); 3] = [
    (
        "upstream/initial-schema.sql",
        9530,
        "aa128be66236fca4255db9c674bb2ca630cee3eaf3c8927bdeb23f528bc10d01",
    ),
    (
        "upstream/core-storage.go",
        32454,
        "e9632afa6b83e5692bcc149e59c23d35c3a6acfe68a9913ec78c5c27ccc50502",
    ),
    (
        "upstream/LICENSE",
        11358,
        "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
    ),
];

/// Explicit external trust anchors. These values must be supplied by the
/// authorized custodian independently of the untrusted packet; matching
/// self-described hashes cannot establish that a source database was Nakama.
/// This candidate does not implement signature/issuer trust for production.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StorageImportCustody {
    expected_manifest: IntegrityDigest,
    expected_receipt: IntegrityDigest,
    producer_commit: String,
    producer_tree: String,
    producer_source: IntegrityDigest,
    producer_binary: IntegrityDigest,
    execution_id: String,
}

impl StorageImportCustody {
    pub fn new(
        expected_manifest: &str,
        expected_receipt: &str,
        producer_commit: String,
        producer_tree: String,
        producer_source: &str,
        producer_binary: &str,
        execution_id: String,
    ) -> Result<Self, DomainError> {
        if !checked_commit(&producer_commit)
            || !checked_commit(&producer_tree)
            || execution_id.is_empty()
            || execution_id.len() > 256
            || execution_id.chars().any(char::is_control)
        {
            return Err(invalid("storage_import_custody_identity_invalid"));
        }
        Ok(Self {
            expected_manifest: parse_digest(expected_manifest)?,
            expected_receipt: parse_digest(expected_receipt)?,
            producer_commit,
            producer_tree,
            producer_source: parse_digest(producer_source)?,
            producer_binary: parse_digest(producer_binary)?,
            execution_id,
        })
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct PacketMember {
    path: String,
    bytes: usize,
    sha256: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ExportProducer {
    repository: String,
    commit: String,
    tree: String,
    source_sha256: String,
    binary_sha256: String,
    execution_id: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ExportSource {
    repository: String,
    commit: String,
    tree: String,
    profile: String,
    server_version: String,
    database_identity: String,
    snapshot_identity: String,
    isolation: String,
    execution_class: String,
    whole_table: bool,
    owner_references_valid: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ExportManifest {
    schema: String,
    project_id: String,
    completed: bool,
    source: ExportSource,
    producer: ExportProducer,
    total_rows: usize,
    page_rows: usize,
    members: Vec<PacketMember>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ExportSourceReceipt {
    schema: String,
    manifest_sha256: String,
    producer: ExportProducer,
    source: ExportSource,
    row_count: usize,
    completed: bool,
    compatibility_credit: bool,
    production_ready: bool,
    full_nakama_replacement: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ExportTimestamp {
    seconds: i64,
    nanos: u32,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ExportRowRecord {
    ordinal: usize,
    collection: String,
    key: String,
    user_id: String,
    public_version: String,
    read: i16,
    write: i16,
    create_time: ExportTimestamp,
    update_time: ExportTimestamp,
    value_path: String,
    value_sha256: String,
    value_bytes: usize,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ExportSourceColumn {
    name: String,
    udt: String,
    nullable: bool,
    character_maximum_length: Option<i64>,
    default_expression: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ExportSourceConstraint {
    name: String,
    kind: String,
    validated: bool,
    definition: String,
    columns: Vec<i16>,
    parent_columns: Option<Vec<i16>>,
    parent_schema: Option<String>,
    parent_table: Option<String>,
    delete_action: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ExportSourceCatalog {
    schema: String,
    profile: String,
    namespace: String,
    table: String,
    table_type: String,
    table_kind: String,
    collation_binding: Vec<String>,
    snapshot_identity: String,
    columns: Vec<ExportSourceColumn>,
    constraints: Vec<ExportSourceConstraint>,
    primary_key_columns: Vec<String>,
    owner_foreign_key_columns: Vec<String>,
    owner_foreign_key_table: String,
    owner_foreign_key_parent_schema: String,
    owner_foreign_key_parent_columns: Vec<String>,
    owner_foreign_key_delete_cascade: bool,
    owner_foreign_key_validated: bool,
    read_nonnegative_check_validated: bool,
    write_nonnegative_check_validated: bool,
}

fn check_source_collation_binding(binding: &[String], profile: &str) -> bool {
    match profile {
        "postgresql" => {
            binding.len() == 8
                && binding[0] == "postgresql"
                && binding[1] == "UTF8"
                && binding[2] == "c"
                && binding[3..5].iter().all(|value| {
                    !value.is_empty() && value.len() <= 1024 && !value.chars().any(char::is_control)
                })
                && binding[5] == "default"
                && binding[6] == "d"
                && binding[7] == "true"
        }
        "cockroachdb" => binding == ["cockroachdb", "UTF8", "uncollated"],
        _ => false,
    }
}

fn check_source_catalog(raw: &[u8], source: &ExportSource) -> Result<Vec<String>, DomainError> {
    let catalog: ExportSourceCatalog = decode_packet_json(raw)?;
    let required = [
        ("collection", "varchar", Some(128)),
        ("key", "varchar", Some(128)),
        ("user_id", "uuid", None),
        ("value", "jsonb", None),
        ("version", "varchar", Some(32)),
        ("read", "int2", None),
        ("write", "int2", None),
        ("create_time", "timestamptz", None),
        ("update_time", "timestamptz", None),
    ];
    if catalog.schema != "trillionnium.nakama-storage-source-catalog.v1"
        || catalog.profile != source.profile
        || catalog.namespace != "public"
        || catalog.table != "storage"
        || catalog.table_type != "BASE TABLE"
        || catalog.table_kind != "r"
        || !check_source_collation_binding(&catalog.collation_binding, &source.profile)
        || catalog.snapshot_identity != source.snapshot_identity
        || catalog.columns.len() != required.len()
        || catalog
            .columns
            .iter()
            .zip(required)
            .any(|(column, (name, udt, length))| {
                column.name != name
                    || column.udt != udt
                    || column.nullable
                    || column.character_maximum_length != length
            })
        || catalog.primary_key_columns != ["collection", "key", "user_id"]
        || catalog.owner_foreign_key_columns != ["user_id"]
        || catalog.owner_foreign_key_table != "users"
        || catalog.owner_foreign_key_parent_schema != "public"
        || catalog.owner_foreign_key_parent_columns != ["id"]
        || !catalog.owner_foreign_key_delete_cascade
        || !catalog.owner_foreign_key_validated
        || !catalog.read_nonnegative_check_validated
        || !catalog.write_nonnegative_check_validated
    {
        return Err(invalid("storage_import_source_catalog_invalid"));
    }
    let expected_defaults = [
        None,
        None,
        None,
        Some(if source.profile == "postgresql" {
            "'{}'::jsonb"
        } else {
            "'{}'"
        }),
        None,
        Some("1"),
        Some("1"),
        Some("now()"),
        Some("now()"),
    ];
    if catalog
        .columns
        .iter()
        .zip(expected_defaults)
        .any(|(column, expected)| column.default_expression.as_deref() != expected)
        || catalog.constraints.len() != 4
    {
        return Err(invalid("storage_import_source_catalog_invalid"));
    }
    let read_name = if source.profile == "postgresql" {
        "storage_read_check"
    } else {
        "check_read"
    };
    let write_name = if source.profile == "postgresql" {
        "storage_write_check"
    } else {
        "check_write"
    };
    let primary_definition = if source.profile == "postgresql" {
        "PRIMARY KEY (collection, key, user_id)"
    } else {
        "PRIMARY KEY (collection ASC, key ASC, user_id ASC)"
    };
    let expected = [
        ("storage_pkey", "p", primary_definition, &[1_i16, 2, 3][..]),
        (
            "storage_user_id_fkey",
            "f",
            "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE",
            &[3_i16][..],
        ),
        (read_name, "c", "CHECK ((read >= 0))", &[6_i16][..]),
        (write_name, "c", "CHECK ((write >= 0))", &[7_i16][..]),
    ];
    let mut seen = BTreeSet::new();
    for constraint in &catalog.constraints {
        let Some((_, kind, definition, columns)) = expected
            .iter()
            .find(|(name, _, _, _)| *name == constraint.name)
        else {
            return Err(invalid("storage_import_source_catalog_invalid"));
        };
        if !seen.insert(constraint.name.as_str())
            || !constraint.validated
            || constraint.kind != *kind
            || constraint.definition != *definition
            || constraint.columns != *columns
            || (*kind == "f"
                && (constraint.parent_columns.as_deref() != Some(&[1_i16][..])
                    || constraint.parent_schema.as_deref() != Some("public")
                    || constraint.parent_table.as_deref() != Some("users")
                    || constraint.delete_action.as_deref() != Some("c")))
            || (*kind != "f"
                && (constraint.parent_columns.is_some()
                    || constraint.parent_schema.is_some()
                    || constraint.parent_table.is_some()))
        {
            return Err(invalid("storage_import_source_catalog_invalid"));
        }
    }
    Ok(catalog.collation_binding)
}

#[derive(Clone, Debug)]
pub(crate) struct NativeStorageImportRow {
    pub(crate) ordinal: usize,
    pub(crate) key: StorageObjectKey,
    pub(crate) value: String,
    pub(crate) public_version: PublicVersion,
    pub(crate) read: ReadPermission,
    pub(crate) write: WritePermission,
    pub(crate) create: StorageTimestamp,
    pub(crate) update: StorageTimestamp,
    pub(crate) row_digest: IntegrityDigest,
}

/// Entire retained packet verified into immutable, bounded owned bytes before
/// any mutable target transaction. Repository code never reopens a packet path.
pub struct VerifiedStorageExport {
    pub(crate) manifest_digest: IntegrityDigest,
    pub(crate) custody_digest: IntegrityDigest,
    pub(crate) inventory_digest: IntegrityDigest,
    pub(crate) profile: DatabaseProfile,
    pub(crate) source_snapshot: String,
    pub(crate) source_collation_binding: Vec<String>,
    pub(crate) execution_class: String,
    pub(crate) page_rows: usize,
    pub(crate) rows: Vec<NativeStorageImportRow>,
    retained_bytes: usize,
}

impl std::fmt::Debug for VerifiedStorageExport {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("VerifiedStorageExport")
            .field("summary", &self.summary())
            .finish_non_exhaustive()
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct StorageImportPacketSummary {
    pub schema: &'static str,
    pub profile: &'static str,
    pub manifest_sha256: String,
    pub custody_sha256: String,
    pub source_execution_class: String,
    pub rows: usize,
    pub pages: usize,
    pub retained_bytes: usize,
    pub compatibility_credit: bool,
    pub production_ready: bool,
    pub full_nakama_replacement: bool,
}

impl VerifiedStorageExport {
    #[must_use]
    pub fn summary(&self) -> StorageImportPacketSummary {
        StorageImportPacketSummary {
            schema: "trillionnium.storage-import-packet-summary.v1",
            profile: self.profile.metadata_value(),
            manifest_sha256: digest_hex(self.manifest_digest),
            custody_sha256: digest_hex(self.custody_digest),
            source_execution_class: self.execution_class.clone(),
            rows: self.rows.len(),
            pages: self.rows.len().div_ceil(self.page_rows),
            retained_bytes: self.retained_bytes,
            compatibility_credit: false,
            production_ready: false,
            full_nakama_replacement: false,
        }
    }
}

/// Linux descriptor-relative opens reject symlinks at the root, directories
/// and files. Files are checked and read through the same held descriptor;
/// all later import work uses those already verified owned bytes.
#[cfg(target_os = "linux")]
fn read_packet_files(root: &Path) -> Result<BTreeMap<String, Vec<u8>>, DomainError> {
    use rustix::fs::{open, openat, Mode, OFlags};
    use std::os::fd::AsRawFd;

    let flags = OFlags::RDONLY | OFlags::CLOEXEC | OFlags::NOFOLLOW;
    let directory = File::from(
        open(root, flags | OFlags::DIRECTORY, Mode::empty())
            .map_err(|_| invalid("storage_import_root_invalid"))?,
    );
    let mut directories = vec![(String::new(), directory)];
    let mut members = BTreeMap::new();
    let mut total_bytes = 0_usize;
    let mut cursor = 0;
    let mut entry_count = 0_usize;
    while cursor < directories.len() {
        let (prefix, directory) = &directories[cursor];
        let names = std::fs::read_dir(format!("/proc/self/fd/{}", directory.as_raw_fd()))
            .map_err(|_| invalid("storage_import_directory_invalid"))?;
        let mut discovered = Vec::new();
        for name in names {
            entry_count += 1;
            if entry_count > MAX_ROWS + 12 {
                return Err(invalid("storage_import_inventory_budget"));
            }
            let name = name
                .map_err(|_| invalid("storage_import_directory_invalid"))?
                .file_name();
            let name = name
                .into_string()
                .map_err(|_| invalid("storage_import_path_invalid"))?;
            if name.is_empty() || name.contains(['/', '\\']) || name == "." || name == ".." {
                return Err(invalid("storage_import_path_invalid"));
            }
            let path = if prefix.is_empty() {
                name.clone()
            } else {
                format!("{prefix}/{name}")
            };
            if prefix.is_empty()
                && matches!(name.as_str(), "values" | "upstream" | "producer-source")
            {
                let child = File::from(
                    openat(
                        directory,
                        name.as_str(),
                        flags | OFlags::DIRECTORY,
                        Mode::empty(),
                    )
                    .map_err(|_| invalid("storage_import_directory_invalid"))?,
                );
                discovered.push((name, child));
                continue;
            }
            let allowed = match prefix.as_str() {
                "" => matches!(
                    name.as_str(),
                    "manifest.json"
                        | "source-receipt.json"
                        | "source-catalog.json"
                        | "source-query.sql"
                        | "rows.ndjson"
                ),
                "values" => {
                    name.len() == 13
                        && name.ends_with(".json")
                        && name[..8].bytes().all(|c| c.is_ascii_digit())
                }
                "upstream" => matches!(
                    name.as_str(),
                    "initial-schema.sql" | "core-storage.go" | "LICENSE"
                ),
                "producer-source" => name == "exporter.rs",
                _ => false,
            };
            if !allowed || members.len() >= MAX_ROWS + 9 {
                return Err(invalid("storage_import_inventory_invalid"));
            }
            let mut file = File::from(
                openat(
                    directory,
                    name.as_str(),
                    flags | OFlags::NONBLOCK,
                    Mode::empty(),
                )
                .map_err(|_| invalid("storage_import_file_invalid"))?,
            );
            let metadata = file
                .metadata()
                .map_err(|_| invalid("storage_import_file_invalid"))?;
            if !metadata.is_file() {
                return Err(invalid("storage_import_file_invalid"));
            }
            let maximum = match path.as_str() {
                "manifest.json" => MAX_MANIFEST_BYTES,
                "rows.ndjson" => MAX_ROWS_BYTES,
                _ if prefix == "values" => MAX_ROW_BYTES,
                _ => MAX_METADATA_BYTES,
            };
            let length = usize::try_from(metadata.len())
                .map_err(|_| invalid("storage_import_file_budget"))?;
            if length > maximum
                || total_bytes
                    .checked_add(length)
                    .is_none_or(|total| total > MAX_PACKET_BYTES)
            {
                return Err(invalid("storage_import_file_budget"));
            }
            let mut raw = Vec::with_capacity(length);
            (&mut file)
                .take(u64::try_from(maximum + 1).expect("bounded file budget"))
                .read_to_end(&mut raw)
                .map_err(|_| invalid("storage_import_file_invalid"))?;
            if raw.len() != length
                || file
                    .metadata()
                    .map_err(|_| invalid("storage_import_file_invalid"))?
                    .len()
                    != metadata.len()
            {
                return Err(invalid("storage_import_file_changed"));
            }
            total_bytes += raw.len();
            if members.insert(path, raw).is_some() {
                return Err(invalid("storage_import_inventory_invalid"));
            }
        }
        directories.extend(discovered);
        cursor += 1;
    }
    Ok(members)
}

#[cfg(not(target_os = "linux"))]
fn read_packet_files(_root: &Path) -> Result<BTreeMap<String, Vec<u8>>, DomainError> {
    Err(failed_precondition(
        "storage_import_descriptor_platform_unsupported",
    ))
}

fn decode_packet_json<T: serde::de::DeserializeOwned>(bytes: &[u8]) -> Result<T, DomainError> {
    // All DTOs are closed structs. Serde rejects repeated known fields and
    // unknown fields at every level; no unvalidated Value/Map is admitted.
    serde_json::from_slice(bytes).map_err(|_| invalid("storage_import_packet_json_invalid"))
}

fn source_profile(source: &ExportSource) -> Result<DatabaseProfile, DomainError> {
    let profile = match source.profile.as_str() {
        "postgresql" => DatabaseProfile::PostgreSql,
        "cockroachdb" => DatabaseProfile::CockroachDb,
        _ => return Err(invalid("storage_import_source_profile_invalid")),
    };
    if source.repository != UPSTREAM_REPOSITORY
        || source.commit != UPSTREAM_COMMIT
        || source.tree != UPSTREAM_TREE
        || !source.whole_table
        || !source.owner_references_valid
        || source.server_version.is_empty()
        || source.server_version.len() > 256
        || source.database_identity.is_empty()
        || source.database_identity.len() > 256
        || source.snapshot_identity.is_empty()
        || source.snapshot_identity.chars().count() > 256
        || [
            &source.server_version,
            &source.database_identity,
            &source.snapshot_identity,
        ]
        .into_iter()
        .any(|value| value.chars().any(char::is_control) || value.contains("://"))
        || !matches!(
            source.execution_class.as_str(),
            "native-source-ddl-fixture" | "custodian-native-database-snapshot"
        )
        || source.isolation
            != match profile {
                DatabaseProfile::PostgreSql => "repeatable-read-read-only",
                DatabaseProfile::CockroachDb => "serializable-read-only",
            }
    {
        return Err(invalid("storage_import_source_identity_invalid"));
    }
    Ok(profile)
}

fn check_producer(
    producer: &ExportProducer,
    custody: &StorageImportCustody,
) -> Result<(), DomainError> {
    if producer.repository != "TrillionniumFoundation/TrillionniumGame"
        || producer.commit != custody.producer_commit
        || producer.tree != custody.producer_tree
        || parse_digest(&producer.source_sha256)? != custody.producer_source
        || parse_digest(&producer.binary_sha256)? != custody.producer_binary
        || producer.execution_id != custody.execution_id
    {
        return Err(invalid("storage_import_producer_custody_mismatch"));
    }
    Ok(())
}

fn source_uuid(value: &str) -> Result<UserId, DomainError> {
    if value.len() != 36
        || ![8, 13, 18, 23]
            .into_iter()
            .all(|position| value.as_bytes()[position] == b'-')
    {
        return Err(invalid("storage_import_owner_invalid"));
    }
    let mut raw = [0_u8; 16];
    let digits: String = value.chars().filter(|c| *c != '-').collect();
    if digits.len() != 32
        || !digits
            .bytes()
            .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
    {
        return Err(invalid("storage_import_owner_invalid"));
    }
    for (index, byte) in raw.iter_mut().enumerate() {
        *byte = u8::from_str_radix(&digits[index * 2..index * 2 + 2], 16)
            .map_err(|_| invalid("storage_import_owner_invalid"))?;
    }
    Ok(UserId::new(raw))
}

fn source_time(time: &ExportTimestamp) -> Result<StorageTimestamp, DomainError> {
    let time = StorageTimestamp::new(time.seconds, time.nanos)?;
    if time.nanos % 1000 != 0 {
        return Err(invalid("storage_import_timestamp_precision_invalid"));
    }
    Ok(time)
}

fn frame_field(frame: &mut Vec<u8>, raw: &[u8]) {
    frame.extend_from_slice(
        &u64::try_from(raw.len())
            .expect("bounded frame field")
            .to_be_bytes(),
    );
    frame.extend_from_slice(raw);
}

fn source_row_digest(record: &ExportRowRecord) -> IntegrityDigest {
    let mut frame = b"trillionnium.storage-import-row.v1\0".to_vec();
    frame.extend_from_slice(
        &u64::try_from(record.ordinal)
            .expect("bounded ordinal")
            .to_be_bytes(),
    );
    for value in [
        &record.collection,
        &record.key,
        &record.user_id,
        &record.public_version,
        &record.value_sha256,
    ] {
        frame_field(&mut frame, value.as_bytes());
    }
    frame.extend_from_slice(&record.read.to_be_bytes());
    frame.extend_from_slice(&record.write.to_be_bytes());
    for time in [&record.create_time, &record.update_time] {
        frame.extend_from_slice(&time.seconds.to_be_bytes());
        frame.extend_from_slice(&time.nanos.to_be_bytes());
    }
    IntegrityDigest::from_value(&frame)
}

pub fn verify_storage_export(
    root: &Path,
    custody: &StorageImportCustody,
) -> Result<VerifiedStorageExport, DomainError> {
    let mut files = read_packet_files(root)?;
    let retained_bytes = files.values().map(Vec::len).sum();
    let manifest_raw = files
        .remove("manifest.json")
        .ok_or_else(|| invalid("storage_import_manifest_missing"))?;
    let receipt_raw = files
        .remove("source-receipt.json")
        .ok_or_else(|| invalid("storage_import_custody_missing"))?;
    if !custody.expected_manifest.matches_value(&manifest_raw)
        || !custody.expected_receipt.matches_value(&receipt_raw)
    {
        return Err(invalid("storage_import_external_custody_mismatch"));
    }
    let manifest: ExportManifest = decode_packet_json(&manifest_raw)?;
    let receipt: ExportSourceReceipt = decode_packet_json(&receipt_raw)?;
    if manifest.schema != "trillionnium.nakama-storage-native-export.v1"
        || manifest.project_id != "trillionnium-game"
        || !manifest.completed
        || manifest.total_rows > MAX_ROWS
        || !(1..=MAX_PAGE_ROWS).contains(&manifest.page_rows)
        || manifest.total_rows.div_ceil(manifest.page_rows.max(1)) > 100
        || manifest.members.len() > MAX_ROWS + 7
        || receipt.schema != "trillionnium.nakama-storage-source-receipt.v1"
        || !receipt.completed
        || receipt.row_count != manifest.total_rows
        || parse_digest(&receipt.manifest_sha256)? != custody.expected_manifest
        || receipt.compatibility_credit
        || receipt.production_ready
        || receipt.full_nakama_replacement
    {
        return Err(invalid("storage_import_manifest_invalid"));
    }
    let profile = source_profile(&manifest.source)?;
    if serde_json::to_vec(&manifest.source)
        .map_err(|_| invalid("storage_import_source_identity_invalid"))?
        != serde_json::to_vec(&receipt.source)
            .map_err(|_| invalid("storage_import_source_identity_invalid"))?
    {
        return Err(invalid("storage_import_source_receipt_mismatch"));
    }
    check_producer(&manifest.producer, custody)?;
    check_producer(&receipt.producer, custody)?;
    let mut declared = BTreeSet::new();
    for member in &manifest.members {
        if !declared.insert(member.path.as_str())
            || files
                .get(&member.path)
                .is_none_or(|raw| raw.len() != member.bytes)
            || !parse_digest(&member.sha256)?.matches_value(
                files
                    .get(&member.path)
                    .ok_or_else(|| invalid("storage_import_member_missing"))?,
            )
        {
            return Err(invalid("storage_import_member_custody_mismatch"));
        }
    }
    if declared != files.keys().map(String::as_str).collect() {
        return Err(invalid("storage_import_inventory_unclosed"));
    }
    for (path, length, digest) in PINNED_SOURCE_MEMBERS {
        let bytes = files
            .get(path)
            .ok_or_else(|| invalid("storage_import_upstream_source_missing"))?;
        if bytes.len() != length || !parse_digest(digest)?.matches_value(bytes) {
            return Err(invalid("storage_import_upstream_source_mismatch"));
        }
    }
    let producer = files
        .get("producer-source/exporter.rs")
        .ok_or_else(|| invalid("storage_import_producer_source_missing"))?;
    if !custody.producer_source.matches_value(producer) {
        return Err(invalid("storage_import_producer_source_mismatch"));
    }
    let rows_raw = files
        .remove("rows.ndjson")
        .ok_or_else(|| invalid("storage_import_rows_missing"))?;
    let catalog = files
        .get("source-catalog.json")
        .ok_or_else(|| invalid("storage_import_snapshot_evidence_missing"))?;
    let source_collation_binding = check_source_catalog(catalog, &manifest.source)?;
    if files.get("source-query.sql").map(Vec::as_slice) != Some(STORAGE_EXPORT_QUERY.as_bytes()) {
        return Err(invalid("storage_import_source_query_invalid"));
    }
    if !rows_raw.is_empty() && !rows_raw.ends_with(b"\n") {
        return Err(invalid("storage_import_rows_framing_invalid"));
    }
    let mut rows = Vec::with_capacity(manifest.total_rows);
    let mut keys = BTreeSet::new();
    let mut used_values = BTreeSet::new();
    let mut inventory = b"trillionnium.storage-import-inventory.v1\0".to_vec();
    let mut page_bytes = 0_usize;
    let value_paths: BTreeSet<String> = files
        .keys()
        .filter(|path| path.starts_with("values/"))
        .cloned()
        .collect();
    let framed_rows = rows_raw.strip_suffix(b"\n").unwrap_or(&rows_raw);
    for raw_line in framed_rows
        .split(|c| *c == b'\n')
        .filter(|_| !rows_raw.is_empty())
    {
        if raw_line.is_empty() {
            return Err(invalid("storage_import_rows_framing_invalid"));
        }
        if raw_line.len() > 16 * 1024 || rows.len() >= manifest.total_rows {
            return Err(invalid("storage_import_row_budget"));
        }
        let record: ExportRowRecord = decode_packet_json(raw_line)?;
        if record.ordinal != rows.len()
            || record.value_path != format!("values/{:08}.json", record.ordinal)
            || !used_values.insert(record.value_path.clone())
            || record.value_bytes > MAX_ROW_BYTES
        {
            return Err(invalid("storage_import_row_identity_invalid"));
        }
        let value = files
            .remove(&record.value_path)
            .ok_or_else(|| invalid("storage_import_value_missing"))?;
        if value.len() != record.value_bytes
            || !parse_digest(&record.value_sha256)?.matches_value(&value)
        {
            return Err(invalid("storage_import_value_custody_mismatch"));
        }
        page_bytes = page_bytes
            .checked_add(value.len())
            .ok_or_else(|| invalid("storage_import_page_budget"))?;
        if page_bytes > MAX_PAGE_BYTES {
            return Err(invalid("storage_import_page_budget"));
        }
        let key = StorageObjectKey::new_nakama(
            record.collection.clone(),
            record.key.clone(),
            source_uuid(&record.user_id)?,
        )?;
        if !keys.insert(key.clone()) {
            return Err(invalid("storage_import_duplicate_key"));
        }
        let row_digest = source_row_digest(&record);
        inventory.extend_from_slice(row_digest.get().as_bytes());
        rows.push(NativeStorageImportRow {
            ordinal: record.ordinal,
            key,
            value: String::from_utf8(value)
                .map_err(|_| invalid("storage_import_value_utf8_invalid"))?,
            public_version: PublicVersion::new(record.public_version)?,
            read: ReadPermission::from_stored(record.read)?,
            write: WritePermission::from_stored(record.write)?,
            create: source_time(&record.create_time)?,
            update: source_time(&record.update_time)?,
            row_digest,
        });
        if rows.len() % manifest.page_rows == 0 {
            page_bytes = 0;
        }
    }
    if rows.len() != manifest.total_rows || used_values != value_paths {
        return Err(invalid("storage_import_row_inventory_mismatch"));
    }
    Ok(VerifiedStorageExport {
        manifest_digest: custody.expected_manifest,
        custody_digest: custody.expected_receipt,
        inventory_digest: IntegrityDigest::from_value(&inventory),
        profile,
        source_snapshot: manifest.source.snapshot_identity,
        source_collation_binding,
        execution_class: manifest.source.execution_class,
        page_rows: manifest.page_rows,
        rows,
        retained_bytes,
    })
}
