struct LiveEnvironment {
    admin_url: String,
    profile: DatabaseProfile,
    upstream_directory: PathBuf,
    producer_commit: String,
    producer_tree: String,
}

fn live_environment() -> Option<LiveEnvironment> {
    let required = match env::var("TRNM_REQUIRE_LIVE_DATABASE").as_deref() {
        Ok("1" | "true" | "TRUE" | "yes" | "YES") => true,
        Err(env::VarError::NotPresent) | Ok("0" | "false" | "FALSE" | "no" | "NO") => false,
        _ => panic!("storage import fixture: invalid required-live flag"),
    };
    let names = [
        "TRNM_SCHEMA_UPGRADE_ADMIN_DATABASE_URL",
        "TRNM_DATABASE_PROFILE",
        "TRNM_STORAGE_PINNED_UPSTREAM_DIRECTORY",
        "TRNM_STORAGE_TEST_PRODUCER_COMMIT",
        "TRNM_STORAGE_TEST_PRODUCER_TREE",
    ];
    let mut values = Vec::new();
    for name in names {
        match env::var(name) {
            Ok(value) if !value.is_empty() => values.push(value),
            _ if required => panic!("storage import fixture: required {name} is absent"),
            _ => return None,
        }
    }
    let profile = match values[1].as_str() {
        "postgresql" => DatabaseProfile::PostgreSql,
        "cockroachdb" => DatabaseProfile::CockroachDb,
        _ => panic!("storage import fixture: invalid profile"),
    };
    assert!(
        Config::from_str(&values[0]).is_ok(),
        "invalid private database configuration"
    );
    for index in [3, 4] {
        assert!(
            values[index].len() == 40
                && values[index]
                    .bytes()
                    .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
                && values[index].bytes().any(|byte| byte != b'0'),
            "invalid external fixture producer identity"
        );
    }
    let directory = PathBuf::from(&values[2]);
    for name in ["initial-schema.sql", "core-storage.go", "LICENSE"] {
        assert!(
            directory.join(name).is_file(),
            "required pinned upstream bytes absent"
        );
    }
    Some(LiveEnvironment {
        admin_url: values.remove(0),
        profile,
        upstream_directory: directory,
        producer_commit: values[2].clone(),
        producer_tree: values[3].clone(),
    })
}

fn unique_name(kind: &str) -> String {
    let counter = NEXT_FIXTURE.fetch_add(1, Ordering::Relaxed);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let name = format!(
        "trnm_imp_{kind}_{:x}_{nanos:x}_{counter:x}",
        std::process::id()
    );
    assert!(
        name.len() < 60
            && name
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
    );
    name
}

fn target_uri(base: &str, database: &str) -> String {
    let (scheme, rest) = base
        .split_once("://")
        .expect("private fixture URI required");
    assert!(matches!(scheme, "postgres" | "postgresql"));
    let end = rest.find(['/', '?']).unwrap_or(rest.len());
    let query = rest[end..].split_once('?').map_or("", |(_, query)| query);
    let separator = if query.is_empty() { "" } else { "&" };
    let result = format!(
        "{scheme}://{}/{database}?{query}{separator}connect_timeout=10",
        &rest[..end]
    );
    assert_eq!(
        Config::from_str(&result).unwrap().get_dbname(),
        Some(database)
    );
    result
}

struct Database {
    admin: Client,
    url: String,
    name: String,
    role: Option<String>,
    extra_roles: Vec<String>,
    profile: DatabaseProfile,
}

impl Database {
    fn new(environment: &LiveEnvironment, kind: &str) -> Self {
        let mut config = Config::from_str(&environment.admin_url).unwrap();
        config.connect_timeout(Duration::from_secs(10));
        let mut admin = config
            .connect(NoTls)
            .expect("connect isolated fixture administrator");
        admin
            .batch_execute("SET statement_timeout = '30s'")
            .unwrap();
        let name = unique_name(kind);
        admin
            .batch_execute(&format!("CREATE DATABASE {name}"))
            .expect("create dedicated import fixture database");
        Self {
            admin,
            url: target_uri(&environment.admin_url, &name),
            name,
            role: None,
            extra_roles: Vec::new(),
            profile: environment.profile,
        }
    }

    fn client(&self) -> Client {
        let mut client =
            Client::connect(&self.url, NoTls).expect("connect dedicated import fixture");
        client
            .batch_execute("SET statement_timeout = '15s'")
            .unwrap();
        client
    }

    fn repository(&self) -> PgRepository {
        PgRepository::connect(&self.url, self.profile).unwrap()
    }

    fn initialize_target(&mut self, environment: &LiveEnvironment) -> StorageImportOptions {
        let report = self
            .repository()
            .migrate_authoritative_schema(&environment.producer_commit, AUDIT_MS, None)
            .unwrap();
        assert_eq!(report.identity.schema_version, 4);
        assert_eq!(report.identity.storage_writer_epoch, 4);
        assert_eq!(report.table_count, 12);
        assert_eq!(report.applied_steps, 4);
        assert_eq!(report.identity.source_commit, environment.producer_commit);
        assert_eq!(
            report.identity.v2_apply_source_commit,
            environment.producer_commit
        );
        assert_eq!(
            report.identity.v3_apply_source_commit,
            environment.producer_commit
        );
        if self.name.starts_with("trnm_imp_target_") {
            if let Some(root) = evidence_root() {
                write_json(
                    root.join("target-schema-migrate.json"),
                    &schema_report_json(&report),
                );
            }
        }
        let role = format!("{}_old", self.name);
        self.admin
            .batch_execute(&format!("CREATE ROLE {role}"))
            .expect("create retired writer fixture role");
        self.role = Some(role.clone());
        // This external scope is chosen before any packet/target guard exists.
        // It adds an operator binding, not a physical Cockroach cluster proof.
        StorageImportOptions {
            audit_at_ms: AUDIT_MS,
            legacy_writer_role: role,
            expected_target_scope: IntegrityDigest::from_value(self.name.as_bytes()),
        }
    }
}

impl Drop for Database {
    fn drop(&mut self) {
        let cascade = if self.profile == DatabaseProfile::CockroachDb {
            " CASCADE"
        } else {
            ""
        };
        let mut failed = self
            .admin
            .batch_execute(&format!("DROP DATABASE {}{cascade}", self.name))
            .is_err();
        if let Some(role) = &self.role {
            failed |= self
                .admin
                .batch_execute(&format!("DROP ROLE {role}"))
                .is_err();
        }
        for role in &self.extra_roles {
            failed |= self
                .admin
                .batch_execute(&format!("DROP ROLE {role}"))
                .is_err();
        }
        if failed {
            if std::thread::panicking() {
                eprintln!("isolated import fixture cleanup failed");
            } else {
                panic!("isolated import fixture cleanup failed");
            }
        }
    }
}

struct PacketDirectory {
    path: PathBuf,
}
impl PacketDirectory {
    fn new() -> Self {
        let path = env::temp_dir().join(unique_name("packet"));
        fs::create_dir(&path).unwrap();
        Self { path }
    }
}
impl Drop for PacketDirectory {
    fn drop(&mut self) {
        if fs::remove_dir_all(&self.path).is_err() {
            if std::thread::panicking() {
                eprintln!("isolated import packet cleanup failed");
            } else {
                panic!("isolated import packet cleanup failed");
            }
        }
    }
}

fn digest_hex(raw: &[u8]) -> String {
    let mut result = String::new();
    for byte in IntegrityDigest::from_value(raw).get().as_bytes() {
        use std::fmt::Write;
        write!(result, "{byte:02x}").unwrap();
    }
    result
}

fn export(environment: &LiveEnvironment, source: &Database, output: PathBuf) -> PathBuf {
    export_with_page_rows(environment, source, output, PAGE_ROWS)
}

fn export_with_page_rows(
    environment: &LiveEnvironment,
    source: &Database,
    output: PathBuf,
    page_rows: usize,
) -> PathBuf {
    let source_file =
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/storage_import_parts/exporter.rs");
    let source_digest = digest_hex(&fs::read(&source_file).unwrap());
    let binary_digest = digest_hex(&fs::read("/proc/self/exe").unwrap());
    let options = StorageExportOptions {
        output_directory: output.clone(),
        page_rows,
        execution_class: "native-source-ddl-fixture".to_owned(),
        producer_commit: environment.producer_commit.clone(),
        producer_tree: environment.producer_tree.clone(),
        producer_source_file: source_file,
        producer_source_sha256: source_digest,
        producer_binary_sha256: binary_digest,
        execution_id: fixture_execution_id(environment),
        upstream_directory: environment.upstream_directory.clone(),
    };
    let summary = source
        .repository()
        .export_storage_snapshot(&options)
        .unwrap();
    assert_eq!(summary.row_count, SOURCE_ROWS);
    assert_eq!(summary.page_count, SOURCE_ROWS.div_ceil(page_rows));
    assert!(
        !summary.compatibility_credit
            && !summary.production_ready
            && !summary.full_nakama_replacement
    );
    assert_eq!(
        summary.manifest_sha256,
        digest_hex(&fs::read(output.join("manifest.json")).unwrap())
    );
    assert_eq!(
        summary.receipt_sha256,
        digest_hex(&fs::read(output.join("source-receipt.json")).unwrap())
    );
    if let Some(root) = evidence_root() {
        if output == root.join("packet") {
            write_json(
                root.join("exporter-summary.json"),
                &serde_json::to_value(summary).unwrap(),
            );
        }
    }
    output
}

fn independent_custody(environment: &LiveEnvironment, path: &Path) -> StorageImportCustody {
    // Anchors come from independent file readback and externally configured
    // producer identity. They are not taken from export summary/packet fields.
    let producer_source =
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/storage_import_parts/exporter.rs");
    StorageImportCustody::new(
        &digest_hex(&fs::read(path.join("manifest.json")).unwrap()),
        &digest_hex(&fs::read(path.join("source-receipt.json")).unwrap()),
        environment.producer_commit.clone(),
        environment.producer_tree.clone(),
        &digest_hex(&fs::read(producer_source).unwrap()),
        &digest_hex(&fs::read("/proc/self/exe").unwrap()),
        fixture_execution_id(environment),
    )
    .unwrap()
}

fn fixture_execution_id(environment: &LiveEnvironment) -> String {
    // External fixture identity chosen before export, never imported from an
    // untrusted receipt. Hash anchors are independently read back below.
    format!(
        "storage-import-native-fixture-{}",
        environment.producer_commit
    )
}

fn evidence_root() -> Option<PathBuf> {
    env::var_os("TRNM_STORAGE_IMPORT_EVIDENCE_ROOT").map(PathBuf::from)
}

fn write_json(path: PathBuf, value: &Value) {
    fs::write(path, serde_json::to_vec(value).unwrap()).unwrap();
}

fn retain_custody(environment: &LiveEnvironment, packet: &Path) {
    let Some(root) = evidence_root() else {
        return;
    };
    let source =
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/storage_import_parts/exporter.rs");
    write_json(
        root.join("custody-anchors.json"),
        &json!({
            "schema":"trillionnium.storage-import-native-custody-anchors.v1",
            "profile":environment.profile.metadata_value(),
            "producer_commit":environment.producer_commit,"producer_tree":environment.producer_tree,
            "producer_source_sha256":digest_hex(&fs::read(source).unwrap()),
            "producer_binary_sha256":digest_hex(&fs::read("/proc/self/exe").unwrap()),
            "execution_id":fixture_execution_id(environment),
            "manifest_sha256":digest_hex(&fs::read(packet.join("manifest.json")).unwrap()),
            "receipt_sha256":digest_hex(&fs::read(packet.join("source-receipt.json")).unwrap()),
            "compatibility_credit":false,"production_ready":false,"full_nakama_replacement":false
        }),
    );
}

fn schema_report_json(report: &trnm_persistence_pg::SchemaMigrationReport) -> Value {
    let identity = &report.identity;
    json!({
        "schema":"trillionnium.authoritative-schema-report.v1","profile":identity.profile.metadata_value(),
        "schema_version":identity.schema_version,"storage_writer_epoch":identity.storage_writer_epoch,
        "chain_digest":hex_bytes(identity.chain_digest.get().as_bytes()),"digest_algorithm":identity.digest_algorithm,
        "source_commit":identity.source_commit,"upgrade_source_commit":identity.upgrade_source_commit,
        "v2_apply_source_commit":identity.v2_apply_source_commit,"v3_apply_source_commit":identity.v3_apply_source_commit,
        "migration_applied":report.migration_applied,"applied_steps":report.applied_steps,
        "table_count":report.table_count,"compatibility_credit":false
    })
}
