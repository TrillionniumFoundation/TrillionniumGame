use std::collections::BTreeSet;
use std::fmt::Write as _;
use std::fs;
use std::path::{Path, PathBuf};

use openssl::hash::{hash, MessageDigest};
use serde_json::Value;

pub fn generate() {
    let manifest = PathBuf::from(std::env::var_os("CARGO_MANIFEST_DIR").expect("manifest path"));
    let root = manifest.join("../..");
    let lock_path = root.join("migrations/MIGRATION_CHAIN.lock.json");
    println!("cargo:rerun-if-changed={}", lock_path.display());
    println!("cargo:rerun-if-changed=schema_build.rs");
    let lock: Value =
        serde_json::from_slice(&fs::read(&lock_path).expect("migration lock readable"))
            .expect("migration lock JSON");
    assert_eq!(lock["schema"], "trillionnium.migration-chain-lock.v1");
    assert_eq!(lock["project_id"], "trillionnium-game");
    assert_eq!(lock["schema_version"], 5);
    assert_eq!(lock["default_runtime_schema_version"], 4);
    let profiles = lock["profiles"].as_object().expect("profile object");
    assert_eq!(profiles.len(), 2);
    let mut generated = String::new();
    let mut all_paths = BTreeSet::new();
    for profile in ["postgresql", "cockroachdb"] {
        let row = &profiles[profile];
        let directory = format!("migrations/{profile}");
        assert_eq!(row["directory"], directory);
        let ordered = row["ordered_files"]
            .as_array()
            .expect("ordered migration list");
        // A new schema version must deliberately extend the engine's state machine.
        assert_eq!(
            ordered.len(),
            5,
            "engine embeds the locked v1 -> v2 -> v3 -> v4 -> opt-in v5 chain"
        );
        let mut inventory = Vec::new();
        collect_sql(&root, &root.join(&directory), &mut inventory);
        inventory.sort();
        let mut listed = Vec::new();
        let mut digest_input = Vec::new();
        let mut steps = String::new();
        let mut baseline = String::new();
        let mut actions = String::new();
        let mut revisions = String::new();
        let mut action_ids = BTreeSet::new();
        let mut action_count = 0;
        for (index, item) in ordered.iter().enumerate() {
            let path = item["path"].as_str().expect("migration path");
            assert!(path.starts_with(&format!("{directory}/")));
            assert!(!path.split('/').any(|part| matches!(part, ".." | ".")));
            assert!(
                all_paths.insert(path.to_owned()),
                "duplicate migration path"
            );
            assert_eq!(
                path,
                format!(
                    "{directory}/{}",
                    match index {
                        0 => "0001_foundation_up.sql",
                        1 => "0002_storage_timestamps_up.sql",
                        2 => "0003_storage_jsonb_up.sql",
                        3 => "0004_storage_source_import_up.sql",
                        4 => "0005_nakama_accounts_up.sql",
                        _ => unreachable!(),
                    }
                )
            );
            listed.push(path.to_owned());
            let absolute = root.join(path);
            println!("cargo:rerun-if-changed={}", absolute.display());
            let bytes = fs::read(&absolute).expect("locked SQL readable");
            let sql = std::str::from_utf8(&bytes).expect("locked SQL UTF-8");
            let mut blob_input = format!("blob {}\0", bytes.len()).into_bytes();
            blob_input.extend_from_slice(&bytes);
            let blob: [u8; 20] = hash(MessageDigest::sha1(), &blob_input)
                .expect("reviewed native SHA-1 provider")
                .as_ref()
                .try_into()
                .unwrap();
            let blob_hex = hex(&blob);
            assert_eq!(item["git_blob_sha1"], blob_hex, "locked Git blob drift");
            digest_input.extend_from_slice(&(index as u64).to_be_bytes());
            digest_input.extend_from_slice(path.as_bytes());
            digest_input.push(0);
            digest_input.extend_from_slice(&blob);
            writeln!(steps, "MigrationStep {{ version: {}, path: {path:?}, git_blob: {blob:?}, sql: {sql:?} }},", index + 1).unwrap();
            let action_start = action_count;
            if index == 0 {
                baseline = baseline_columns(sql);
            } else {
                let (declared, count) = declared_actions(sql, &mut action_ids, profile, index);
                assert_eq!(
                    count,
                    match index {
                        1 => 6,
                        2 => 16,
                        3 if profile == "postgresql" => 8,
                        3 => 12,
                        4 => 5,
                        _ => unreachable!(),
                    },
                    "locked typed action inventory"
                );
                actions.push_str(&declared);
                action_count += count;
            }
            let prefix: [u8; 32] = hash(MessageDigest::sha256(), &digest_input)
                .expect("reviewed native SHA-256 provider")
                .as_ref()
                .try_into()
                .unwrap();
            // Historical v1 predates the stored writer fence. Future revisions
            // must deliberately declare their ABI rather than infer it from version.
            let storage_writer_epoch = match index {
                0 => None,
                1 => Some(2_u64),
                2 => Some(3_u64),
                3 => Some(4_u64),
                4 => Some(4_u64), // Accounts schema is distinct from storage writer ownership.
                _ => panic!("unreviewed authoritative writer epoch"),
            };
            writeln!(revisions, "RevisionDescriptor {{ version: {}, chain_digest: {prefix:?}, storage_writer_epoch: {storage_writer_epoch:?}, action_range: {action_start}..{action_count} }},", index + 1).unwrap();
        }
        assert_eq!(
            inventory, listed,
            "unlisted, missing or unordered authoritative SQL"
        );
        let chain: [u8; 32] = hash(MessageDigest::sha256(), &digest_input)
            .expect("reviewed native SHA-256 provider")
            .as_ref()
            .try_into()
            .unwrap();
        let symbol = profile.to_ascii_uppercase();
        writeln!(
            generated,
            "const {symbol}_STEPS: &[MigrationStep] = &[{steps}];"
        )
        .unwrap();
        writeln!(
            generated,
            "const {symbol}_BASELINE: &[ColumnDescriptor] = &[{baseline}];"
        )
        .unwrap();
        writeln!(
            generated,
            "const {symbol}_ACTIONS: &[MigrationAction] = &[{actions}];"
        )
        .unwrap();
        writeln!(
            generated,
            "const {symbol}_REVISIONS: &[RevisionDescriptor] = &[{revisions}];"
        )
        .unwrap();
        writeln!(
            generated,
            "const {symbol}_CHAIN_BYTES: [u8;32] = {chain:?};"
        )
        .unwrap();
    }
    for key in [
        "ordered_files_are_complete",
        "unlisted_sql_is_failure",
        "duplicate_path_is_failure",
        "profile_conclusions_are_separate",
        "semantic_change_requires_adr_and_lock_update",
    ] {
        assert_eq!(lock["rules"][key], true);
    }
    assert_eq!(
        lock["rules"]["drop_based_production_rollback_allowed"],
        false
    );
    fs::write(
        PathBuf::from(std::env::var_os("OUT_DIR").expect("OUT_DIR"))
            .join("authoritative_schema.rs"),
        generated,
    )
    .expect("schema embeds generated");
}

fn collect_sql(root: &Path, path: &Path, output: &mut Vec<String>) {
    // A new unlisted file/directory must also invalidate Cargo's cached build.
    println!("cargo:rerun-if-changed={}", path.display());
    for entry in fs::read_dir(path).expect("migration directory readable") {
        let path = entry.expect("migration entry readable").path();
        assert!(!path.is_symlink(), "migration symlinks are not authority");
        if path.is_dir() {
            collect_sql(root, &path, output);
        } else if path.extension().is_some_and(|extension| extension == "sql") {
            output.push(
                path.strip_prefix(root)
                    .unwrap()
                    .to_str()
                    .expect("UTF-8 source path")
                    .replace('\\', "/"),
            );
        }
    }
}

fn baseline_columns(sql: &str) -> String {
    let mut table = None;
    let mut output = String::new();
    for line in sql.lines() {
        if let Some(value) = line.strip_prefix("CREATE TABLE ") {
            table = Some(value.trim_end_matches(" (").to_owned());
        } else if line == ");" {
            table = None;
        } else if let Some(table) = &table {
            let mut tokens = line.split_whitespace();
            let Some(name) = tokens.next() else { continue };
            let Some(kind) = tokens.next() else { continue };
            // Bare nullable declarations end at the type token itself.
            let kind = kind.trim_end_matches(',');
            if matches!(
                kind,
                "TEXT"
                    | "STRING"
                    | "BYTEA"
                    | "BYTES"
                    | "SMALLINT"
                    | "INT2"
                    | "INTEGER"
                    | "INT4"
                    | "BIGINT"
                    | "INT8"
            ) {
                let nullable = !line.contains("NOT NULL") && !line.contains("PRIMARY KEY");
                let default_zero = line.contains("DEFAULT 0");
                writeln!(output, "ColumnDescriptor {{ table: {table:?}, name: {name:?}, kind: {:?}, nullable: {nullable}, default_zero: {default_zero}, character_maximum_length: None }},", normalized_kind(kind)).unwrap();
            }
        }
    }
    assert!(!output.is_empty());
    output
}

// This is a bounded parser for the reviewed marker grammar, not a SQL splitter.
// A marker owns exactly one complete ALTER statement for one declared column.
fn declared_actions(
    sql: &str,
    names: &mut BTreeSet<String>,
    profile: &str,
    revision: usize,
) -> (String, usize) {
    if revision == 3 {
        return declared_import_actions(sql, names, profile);
    }
    if revision == 4 {
        return declared_accounts_actions(sql, names, profile);
    }
    let mut pending = None;
    let mut output = String::new();
    let previous_count = names.len();
    for line in sql.lines().map(str::trim) {
        if let Some(name) = line.strip_prefix("-- trnm:action ") {
            assert!(pending.is_none(), "action without SQL");
            assert!(names.insert(name.to_owned()), "duplicate action");
            pending = Some(name.to_owned());
        } else if line == "-- trnm:backfill storage_jsonb_v3" {
            assert_eq!(revision, 2);
            let id = pending.take().expect("marked typed backfill");
            assert_eq!(id, "storage_native_backfill");
            writeln!(output, "MigrationAction {{ id: {id:?}, kind: MigrationActionKind::BackfillStorageJsonbV3, descriptor: ActionDescriptor::Column(ColumnDescriptor {{ table: \"trnm_storage_objects\", name: \"@backfill\", kind: \"backfilled\", nullable: false, default_zero: false, character_maximum_length: None }}), sql: \"\" }},").unwrap();
        } else if line.starts_with("--") || line.is_empty() || matches!(line, "BEGIN;" | "COMMIT;")
        {
            assert!(!line.starts_with("-- trnm:"), "unknown runner directive");
            continue;
        } else {
            let id = pending.take().expect("unmarked migration statement");
            let tokens: Vec<_> = line.split_whitespace().collect();
            assert_eq!(&tokens[..2], ["ALTER", "TABLE"]);
            let table = tokens[2];
            assert!(matches!(
                table,
                "trnm_schema_metadata" | "trnm_storage_objects"
            ));
            let (action_kind, name, kind, nullable, max_len) = if tokens[3..5] == ["ADD", "COLUMN"]
            {
                assert_eq!(tokens.len(), 7);
                let declared = tokens[6].strip_suffix(';').expect("terminated action");
                (
                    "AddColumn",
                    tokens[5].to_owned(),
                    normalized_kind(declared),
                    true,
                    if declared == "VARCHAR(32)" {
                        Some(32_i64)
                    } else {
                        None
                    },
                )
            } else if tokens[3..5] == ["ALTER", "COLUMN"] {
                assert_eq!(revision, 2);
                assert_eq!(tokens.len(), 9);
                assert_eq!(&tokens[7..], ["NOT", "NULL;"]);
                let nullable = match tokens[6] {
                    "DROP" => true,
                    "SET" => false,
                    _ => panic!("unknown nullability transition"),
                };
                let kind = match tokens[5] {
                    "value_bytes" | "version_digest" | "value_projection_digest" => "bytea",
                    "value_jsonb" => "jsonb",
                    "public_version" => "varchar",
                    "value_origin" => "text",
                    _ => panic!("unknown native ABI column"),
                };
                (
                    "SetNullability",
                    tokens[5].to_owned(),
                    kind,
                    nullable,
                    if kind == "varchar" {
                        Some(32_i64)
                    } else {
                        None
                    },
                )
            } else {
                assert_eq!(revision, 2);
                assert_eq!(&tokens[3..5], ["ADD", "CONSTRAINT"]);
                let definition = reviewed_check_definition(profile, tokens[5], line);
                (
                    "AddCheck",
                    format!("@check:{}", tokens[5]),
                    definition,
                    false,
                    None,
                )
            };
            writeln!(output, "MigrationAction {{ id: {id:?}, kind: MigrationActionKind::{action_kind}, descriptor: ActionDescriptor::Column(ColumnDescriptor {{ table: {table:?}, name: {name:?}, kind: {kind:?}, nullable: {nullable}, default_zero: false, character_maximum_length: {max_len:?} }}), sql: {line:?} }},").unwrap();
        }
    }
    assert!(pending.is_none());
    (output, names.len() - previous_count)
}

fn reviewed_check_definition(profile: &str, name: &str, sql: &str) -> &'static str {
    // Exact native deparsers on the pinned PostgreSQL 17 / CockroachDB 26.2.
    // Parentheses and literal contents remain significant: no semantic eraser.
    match name {
        "storage_projection_digest" => {
            assert_eq!(sql, "ALTER TABLE trnm_storage_objects ADD CONSTRAINT storage_projection_digest CHECK (octet_length(value_projection_digest) = 32);");
            "CHECK ((octet_length(value_projection_digest) = 32))"
        }
        "metadata_v2_history" => {
            assert_eq!(sql, "ALTER TABLE trnm_schema_metadata ADD CONSTRAINT metadata_v2_history CHECK (schema_version < 3 OR (v2_apply_source_commit IS NOT NULL AND length(v2_apply_source_commit) = 40));");
            "CHECK (((schema_version < 3) OR ((v2_apply_source_commit IS NOT NULL) AND (length(v2_apply_source_commit) = 40))))"
        }
        "storage_origin_witness" => {
            assert_eq!(sql, "ALTER TABLE trnm_storage_objects ADD CONSTRAINT storage_origin_witness CHECK (((value_origin = 'legacy-rust-v2-bytes' OR value_origin = 'write-request-bytes') AND value_bytes IS NOT NULL AND version_digest IS NOT NULL AND octet_length(version_digest) = 32 AND source_manifest_digest IS NULL) OR (value_origin = 'nakama-export-unknown-request' AND value_bytes IS NULL AND version_digest IS NULL AND source_manifest_digest IS NOT NULL AND octet_length(source_manifest_digest) = 32 AND source_manifest_digest <> decode(repeat('0',64),'hex')));");
            if profile == "postgresql" {
                "CHECK (((((value_origin = 'legacy-rust-v2-bytes'::text) OR (value_origin = 'write-request-bytes'::text)) AND (value_bytes IS NOT NULL) AND (version_digest IS NOT NULL) AND (octet_length(version_digest) = 32) AND (source_manifest_digest IS NULL)) OR ((value_origin = 'nakama-export-unknown-request'::text) AND (value_bytes IS NULL) AND (version_digest IS NULL) AND (source_manifest_digest IS NOT NULL) AND (octet_length(source_manifest_digest) = 32) AND (source_manifest_digest <> decode(repeat('0'::text, 64), 'hex'::text)))))"
            } else {
                "CHECK ((((((((value_origin = 'legacy-rust-v2-bytes'::STRING) OR (value_origin = 'write-request-bytes'::STRING)) AND (value_bytes IS NOT NULL)) AND (version_digest IS NOT NULL)) AND (octet_length(version_digest) = 32)) AND (source_manifest_digest IS NULL)) OR ((((((value_origin = 'nakama-export-unknown-request'::STRING) AND (value_bytes IS NULL)) AND (version_digest IS NULL)) AND (source_manifest_digest IS NOT NULL)) AND (octet_length(source_manifest_digest) = 32)) AND (source_manifest_digest != decode(repeat('0'::STRING, 64), 'hex'::STRING)))))"
            }
        }
        _ => panic!("unreviewed authoritative check"),
    }
}

fn normalized_kind(kind: &str) -> &'static str {
    match kind {
        "TEXT" | "STRING" => "text",
        "BYTEA" | "BYTES" => "bytea",
        "SMALLINT" | "INT2" => "int2",
        "INTEGER" | "INT4" => "int4",
        "BIGINT" | "INT8" => "int8",
        "TIMESTAMPTZ" => "timestamptz",
        "JSONB" => "jsonb",
        "VARCHAR(32)" => "varchar",
        _ => panic!("unreviewed authoritative column type"),
    }
}

fn hex(bytes: &[u8]) -> String {
    let mut value = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        write!(value, "{byte:02x}").unwrap();
    }
    value
}

// v4 extends the reviewed marker grammar with complete named table creations
// and explicit CHECK transitions. A marker owns a whole statement; semicolons
// in arbitrary SQL/literals or unmarked statements are never split/adopted.
fn declared_import_actions(
    sql: &str,
    names: &mut BTreeSet<String>,
    profile: &str,
) -> (String, usize) {
    let mut statements = Vec::new();
    let mut pending = None;
    let mut body = Vec::new();
    for line in sql.lines().map(str::trim) {
        if let Some(id) = line.strip_prefix("-- trnm:action ") {
            assert!(pending.is_none() && body.is_empty(), "incomplete v4 action");
            assert!(names.insert(id.to_owned()), "duplicate action");
            pending = Some(id.to_owned());
        } else if line.is_empty() || line.starts_with("--") || matches!(line, "BEGIN;" | "COMMIT;")
        {
            assert!(!line.starts_with("-- trnm:"), "unknown runner directive");
        } else {
            assert!(pending.is_some(), "unmarked v4 statement");
            assert!(!line.contains("IF EXISTS") && !line.contains("IF NOT EXISTS"));
            body.push(line.to_owned());
            if line.ends_with(';') {
                let id = pending.take().unwrap();
                statements.push((id, body.join("\n")));
                body.clear();
            }
        }
    }
    assert!(
        pending.is_none() && body.is_empty(),
        "unterminated v4 action"
    );
    let mut expected = vec!["metadata_v3_apply_source_commit".to_owned()];
    for column in [
        "collection",
        "object_key",
        "read_permission",
        "write_permission",
    ] {
        if profile == "postgresql" {
            expected.push(format!("storage_{column}_check_v4"));
        } else {
            expected.push(format!("storage_{column}_check_v3_remove"));
            expected.push(format!("storage_{column}_check_v4_add"));
        }
    }
    expected.extend(
        [
            "storage_import_jobs",
            "storage_import_pages",
            "metadata_v3_history",
        ]
        .map(str::to_owned),
    );
    assert_eq!(
        statements.iter().map(|(id, _)| id).collect::<Vec<_>>(),
        expected.iter().collect::<Vec<_>>()
    );
    let mut output = String::new();
    for (id, statement) in &statements {
        let (kind, descriptor) = if id == "metadata_v3_apply_source_commit" {
            assert_eq!(
                statement,
                "ALTER TABLE trnm_schema_metadata ADD COLUMN v3_apply_source_commit TEXT;"
            );
            ("AddColumn", "ActionDescriptor::Column(ColumnDescriptor { table: \"trnm_schema_metadata\", name: \"v3_apply_source_commit\", kind: \"text\", nullable: true, default_zero: false, character_maximum_length: None })".to_owned())
        } else if id == "metadata_v3_history" {
            assert_eq!(statement, "ALTER TABLE trnm_schema_metadata ADD CONSTRAINT metadata_v3_history CHECK (schema_version < 4 OR (v3_apply_source_commit IS NOT NULL AND length(v3_apply_source_commit) = 40));");
            let definition = "CHECK (((schema_version < 4) OR ((v3_apply_source_commit IS NOT NULL) AND (length(v3_apply_source_commit) = 40))))";
            ("AddCheck", format!("ActionDescriptor::Column(ColumnDescriptor {{ table: \"trnm_schema_metadata\", name: \"@check:metadata_v3_history\", kind: {definition:?}, nullable: false, default_zero: false, character_maximum_length: None }})"))
        } else if id == "storage_import_jobs" || id == "storage_import_pages" {
            let table = format!("trnm_{id}");
            assert!(
                statement.starts_with(&format!("CREATE TABLE {table} (\n"))
                    && statement.ends_with("\n);")
            );
            let columns = baseline_columns(statement);
            let column_names = if id == "storage_import_jobs" {
                vec![
                    "singleton",
                    "manifest_digest",
                    "custody_digest",
                    "source_inventory_digest",
                    "target_schema_guard_digest",
                    "prefix_digest",
                    "source_profile",
                    "source_snapshot",
                    "audit_at_ms",
                    "total_rows",
                    "total_pages",
                    "next_page",
                    "committed_rows",
                    "status",
                ]
            } else {
                vec![
                    "manifest_digest",
                    "page_index",
                    "first_ordinal",
                    "row_count",
                    "page_digest",
                    "prefix_digest",
                    "audit_at_ms",
                ]
            };
            let actual: Vec<_> = statement
                .lines()
                .skip(1)
                .filter(|line| !line.starts_with("CONSTRAINT") && *line != ");")
                .map(|line| line.split_whitespace().next().unwrap())
                .collect();
            assert_eq!(actual, column_names, "closed journal column inventory");
            assert!(
                statement
                    .lines()
                    .skip(1)
                    .take(column_names.len())
                    .all(|line| line.ends_with("NOT NULL,")),
                "no journal default/nullability changes"
            );
            for (name, line) in column_names.iter().zip(statement.lines().skip(1)) {
                let kind = match *name {
                    "singleton" | "status" => "SMALLINT",
                    "manifest_digest"
                    | "custody_digest"
                    | "source_inventory_digest"
                    | "target_schema_guard_digest"
                    | "prefix_digest"
                    | "page_digest" => "BYTEA",
                    "source_profile" | "source_snapshot" => "TEXT",
                    _ => "BIGINT",
                };
                assert_eq!(
                    line,
                    format!("{name} {kind} NOT NULL,"),
                    "closed journal column type"
                );
            }
            reviewed_import_constraints(id, statement);
            ("CreateImportTable", format!("ActionDescriptor::ImportTable(ImportTableDescriptor {{ table: {table:?}, columns: &[{columns}] }})"))
        } else {
            let column = [
                "collection",
                "object_key",
                "read_permission",
                "write_permission",
            ]
            .into_iter()
            .find(|column| id.starts_with(&format!("storage_{column}_check_v")))
            .unwrap();
            let name = if profile == "postgresql" {
                format!("trnm_storage_objects_{column}_check")
            } else {
                format!("check_{column}")
            };
            let expression = match column {
                "collection" | "object_key" => format!("length({column}) BETWEEN 0 AND 128"),
                _ => format!("{column} >= 0"),
            };
            let (kind, expected_sql) = if id.ends_with("_remove") {
                (
                    "RemoveStorageCheck",
                    format!("ALTER TABLE trnm_storage_objects DROP CONSTRAINT {name};"),
                )
            } else if id.ends_with("_add") {
                ("AddStorageCheck", format!("ALTER TABLE trnm_storage_objects ADD CONSTRAINT {name} CHECK ({expression});"))
            } else {
                ("ReplaceStorageCheck", format!("ALTER TABLE trnm_storage_objects DROP CONSTRAINT {name}, ADD CONSTRAINT {name} CHECK ({expression});"))
            };
            assert_eq!(statement, &expected_sql, "closed storage domain action");
            let definition = match (profile, column) {
                ("postgresql", "collection") => {
                    "CHECK (((length(collection) >= 0) AND (length(collection) <= 128)))"
                }
                ("postgresql", "object_key") => {
                    "CHECK (((length(object_key) >= 0) AND (length(object_key) <= 128)))"
                }
                ("cockroachdb", "collection") => "CHECK ((length(collection) BETWEEN 0 AND 128))",
                ("cockroachdb", "object_key") => "CHECK ((length(object_key) BETWEEN 0 AND 128))",
                (_, "read_permission") => "CHECK ((read_permission >= 0))",
                (_, "write_permission") => "CHECK ((write_permission >= 0))",
                _ => unreachable!(),
            };
            (kind, format!("ActionDescriptor::StorageCheck(ColumnDescriptor {{ table: \"trnm_storage_objects\", name: {:?}, kind: {definition:?}, nullable: false, default_zero: false, character_maximum_length: None }})", format!("@check:{name}")))
        };
        writeln!(output, "MigrationAction {{ id: {id:?}, kind: MigrationActionKind::{kind}, descriptor: {descriptor}, sql: {statement:?} }},").unwrap();
    }
    (output, statements.len())
}

fn reviewed_import_constraints(id: &str, statement: &str) {
    let expected: &[&str] = if id == "storage_import_jobs" {
        &[
            "CONSTRAINT storage_import_jobs_pk PRIMARY KEY (singleton),",
            "CONSTRAINT storage_import_jobs_manifest_key UNIQUE (manifest_digest),",
            "CONSTRAINT storage_import_jobs_singleton CHECK (singleton = 1),",
            "CONSTRAINT storage_import_jobs_digests CHECK (octet_length(manifest_digest) = 32 AND octet_length(custody_digest) = 32 AND octet_length(source_inventory_digest) = 32 AND octet_length(target_schema_guard_digest) = 32 AND octet_length(prefix_digest) = 32 AND manifest_digest <> decode(repeat('0', 64), 'hex') AND custody_digest <> decode(repeat('0', 64), 'hex') AND source_inventory_digest <> decode(repeat('0', 64), 'hex') AND target_schema_guard_digest <> decode(repeat('0', 64), 'hex') AND prefix_digest <> decode(repeat('0', 64), 'hex')),",
            "CONSTRAINT storage_import_jobs_source CHECK ((source_profile = 'postgresql' OR source_profile = 'cockroachdb') AND length(source_snapshot) BETWEEN 1 AND 256 AND source_snapshot !~ '[[:cntrl:]]'),",
            "CONSTRAINT storage_import_jobs_audit CHECK (audit_at_ms >= 0),",
            "CONSTRAINT storage_import_jobs_counts CHECK (total_rows BETWEEN 0 AND 10000 AND total_pages BETWEEN 0 AND 100 AND next_page BETWEEN 0 AND total_pages AND committed_rows BETWEEN 0 AND total_rows AND ((total_rows = 0 AND total_pages = 0) OR (total_pages > 0 AND total_pages <= total_rows AND total_rows <= 100 * total_pages)) AND committed_rows BETWEEN next_page AND 100 * next_page),",
            "CONSTRAINT storage_import_jobs_status CHECK (status IN (0, 1) AND (status <> 1 OR (next_page = total_pages AND committed_rows = total_rows)))",
        ]
    } else {
        &[
            "CONSTRAINT storage_import_pages_pk PRIMARY KEY (manifest_digest, page_index),",
            "CONSTRAINT storage_import_pages_job_fk FOREIGN KEY (manifest_digest) REFERENCES trnm_storage_import_jobs (manifest_digest) ON UPDATE NO ACTION ON DELETE RESTRICT,",
            "CONSTRAINT storage_import_pages_digests CHECK (octet_length(manifest_digest) = 32 AND octet_length(page_digest) = 32 AND octet_length(prefix_digest) = 32 AND manifest_digest <> decode(repeat('0', 64), 'hex') AND page_digest <> decode(repeat('0', 64), 'hex') AND prefix_digest <> decode(repeat('0', 64), 'hex')),",
            "CONSTRAINT storage_import_pages_bounds CHECK (page_index BETWEEN 0 AND 99 AND first_ordinal BETWEEN 0 AND 9999 AND row_count BETWEEN 1 AND 100 AND first_ordinal + row_count <= 10000),",
            "CONSTRAINT storage_import_pages_audit CHECK (audit_at_ms >= 0)",
        ]
    };
    let actual: Vec<_> = statement
        .lines()
        .filter(|line| line.starts_with("CONSTRAINT"))
        .collect();
    assert_eq!(actual, expected, "closed journal constraint inventory");
}

// Schema5 is a separately named account target. These are source declarations;
// account native catalog tuples must be independently captured before activation.
fn declared_accounts_actions(
    sql: &str,
    names: &mut BTreeSet<String>,
    profile: &str,
) -> (String, usize) {
    assert!(matches!(profile, "postgresql" | "cockroachdb"));
    let expected: &[(&str, &str)] = &[
("metadata_v4_apply_source_commit", "ALTER TABLE trnm_schema_metadata ADD COLUMN v4_apply_source_commit TEXT;"),
("nakama_users", "CREATE TABLE public.users (\nid UUID NOT NULL,\nusername VARCHAR(128) NOT NULL,\ndisplay_name VARCHAR(255),\navatar_url VARCHAR(512),\nlang_tag VARCHAR(18) NOT NULL DEFAULT 'en',\nlocation VARCHAR(255),\ntimezone VARCHAR(255),\nmetadata JSONB NOT NULL DEFAULT '{}',\nwallet JSONB NOT NULL DEFAULT '{}',\nemail VARCHAR(255),\npassword BYTEA,\nfacebook_id VARCHAR(128),\ngoogle_id VARCHAR(128),\ngamecenter_id VARCHAR(128),\nsteam_id VARCHAR(128),\ncustom_id VARCHAR(128),\nedge_count INT NOT NULL DEFAULT 0,\ncreate_time TIMESTAMPTZ NOT NULL DEFAULT now(),\nupdate_time TIMESTAMPTZ NOT NULL DEFAULT now(),\nverify_time TIMESTAMPTZ NOT NULL DEFAULT '1970-01-01 00:00:00 UTC',\ndisable_time TIMESTAMPTZ NOT NULL DEFAULT '1970-01-01 00:00:00 UTC',\nfacebook_instant_game_id VARCHAR(128),\napple_id VARCHAR(128),\nCONSTRAINT users_pkey PRIMARY KEY (id),\nCONSTRAINT users_username_key UNIQUE (username),\nCONSTRAINT users_email_key UNIQUE (email),\nCONSTRAINT users_facebook_id_key UNIQUE (facebook_id),\nCONSTRAINT users_google_id_key UNIQUE (google_id),\nCONSTRAINT users_gamecenter_id_key UNIQUE (gamecenter_id),\nCONSTRAINT users_steam_id_key UNIQUE (steam_id),\nCONSTRAINT users_custom_id_key UNIQUE (custom_id),\nCONSTRAINT users_facebook_instant_game_id_key UNIQUE (facebook_instant_game_id),\nCONSTRAINT users_apple_id_key UNIQUE (apple_id),\nCONSTRAINT users_password_check CHECK (length(password) < 32000),\nCONSTRAINT users_edge_count_check CHECK (edge_count >= 0)\n);"),
("nakama_system_user", "INSERT INTO public.users (id, username) VALUES ('00000000-0000-0000-0000-000000000000', '') ON CONFLICT (id) DO NOTHING;"),
("nakama_user_device", "CREATE TABLE public.user_device (\nid VARCHAR(128) NOT NULL,\nuser_id UUID NOT NULL,\npreferences JSONB NOT NULL DEFAULT '{}',\npush_token_amazon VARCHAR(512) NOT NULL DEFAULT '',\npush_token_android VARCHAR(512) NOT NULL DEFAULT '',\npush_token_huawei VARCHAR(512) NOT NULL DEFAULT '',\npush_token_ios VARCHAR(512) NOT NULL DEFAULT '',\npush_token_web VARCHAR(512) NOT NULL DEFAULT '',\nCONSTRAINT user_device_pkey PRIMARY KEY (id),\nCONSTRAINT user_device_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users (id) ON UPDATE NO ACTION ON DELETE CASCADE,\nCONSTRAINT user_device_user_id_id_key UNIQUE (user_id, id)\n);"),
("metadata_v4_history", "ALTER TABLE trnm_schema_metadata ADD CONSTRAINT metadata_v4_history CHECK (schema_version < 5 OR (v4_apply_source_commit IS NOT NULL AND length(v4_apply_source_commit) = 40));"),
];
    let mut actual = Vec::new();
    let mut pending = None;
    let mut body = Vec::new();
    for line in sql.lines().map(str::trim) {
        if let Some(id) = line.strip_prefix("-- trnm:action ") {
            assert!(
                pending.is_none() && body.is_empty(),
                "incomplete account action"
            );
            assert!(names.insert(id.to_owned()), "duplicate account action");
            pending = Some(id.to_owned());
        } else if line.is_empty() || line.starts_with("--") || matches!(line, "BEGIN;" | "COMMIT;")
        {
            assert!(!line.starts_with("-- trnm:"), "unknown account directive");
        } else {
            assert!(pending.is_some(), "unmarked account statement");
            body.push(line.to_owned());
            if line.ends_with(';') {
                actual.push((pending.take().unwrap(), body.join("\n")));
                body.clear();
            }
        }
    }
    assert!(
        pending.is_none() && body.is_empty(),
        "unterminated account action"
    );
    assert_eq!(
        actual
            .iter()
            .map(|(id, sql)| (id.as_str(), sql.as_str()))
            .collect::<Vec<_>>(),
        expected
    );
    let mut output = String::new();
    for (id, statement) in actual {
        let (kind, descriptor) = match id.as_str() {
            "metadata_v4_apply_source_commit" => ("AddColumn", "ActionDescriptor::Column(ColumnDescriptor { table: \"trnm_schema_metadata\", name: \"v4_apply_source_commit\", kind: \"text\", nullable: true, default_zero: false, character_maximum_length: None })".to_owned()),
            "metadata_v4_history" => ("AddCheck", "ActionDescriptor::Column(ColumnDescriptor { table: \"trnm_schema_metadata\", name: \"@check:metadata_v4_history\", kind: \"CHECK (((schema_version < 5) OR ((v4_apply_source_commit IS NOT NULL) AND (length(v4_apply_source_commit) = 40))))\", nullable: false, default_zero: false, character_maximum_length: None })".to_owned()),
            "nakama_system_user" => ("SeedSystemUser", "ActionDescriptor::Column(ColumnDescriptor { table: \"users\", name: \"@system-user\", kind: \"present\", nullable: false, default_zero: false, character_maximum_length: None })".to_owned()),
            "nakama_users" => {
                let declarations = if profile == "cockroachdb" { "ColumnDescriptor { table: \"users\", name: \"id\", kind: \"uuid\", nullable: false, default_zero: false, character_maximum_length: None },\nColumnDescriptor { table: \"users\", name: \"username\", kind: \"varchar\", nullable: false, default_zero: false, character_maximum_length: Some(128_i64) },\nColumnDescriptor { table: \"users\", name: \"display_name\", kind: \"varchar\", nullable: true, default_zero: false, character_maximum_length: Some(255_i64) },\nColumnDescriptor { table: \"users\", name: \"avatar_url\", kind: \"varchar\", nullable: true, default_zero: false, character_maximum_length: Some(512_i64) },\nColumnDescriptor { table: \"users\", name: \"lang_tag\", kind: \"varchar\", nullable: false, default_zero: false, character_maximum_length: Some(18_i64) },\nColumnDescriptor { table: \"users\", name: \"location\", kind: \"varchar\", nullable: true, default_zero: false, character_maximum_length: Some(255_i64) },\nColumnDescriptor { table: \"users\", name: \"timezone\", kind: \"varchar\", nullable: true, default_zero: false, character_maximum_length: Some(255_i64) },\nColumnDescriptor { table: \"users\", name: \"metadata\", kind: \"jsonb\", nullable: false, default_zero: false, character_maximum_length: None },\nColumnDescriptor { table: \"users\", name: \"wallet\", kind: \"jsonb\", nullable: false, default_zero: false, character_maximum_length: None },\nColumnDescriptor { table: \"users\", name: \"email\", kind: \"varchar\", nullable: true, default_zero: false, character_maximum_length: Some(255_i64) },\nColumnDescriptor { table: \"users\", name: \"password\", kind: \"bytea\", nullable: true, default_zero: false, character_maximum_length: None },\nColumnDescriptor { table: \"users\", name: \"facebook_id\", kind: \"varchar\", nullable: true, default_zero: false, character_maximum_length: Some(128_i64) },\nColumnDescriptor { table: \"users\", name: \"google_id\", kind: \"varchar\", nullable: true, default_zero: false, character_maximum_length: Some(128_i64) },\nColumnDescriptor { table: \"users\", name: \"gamecenter_id\", kind: \"varchar\", nullable: true, default_zero: false, character_maximum_length: Some(128_i64) },\nColumnDescriptor { table: \"users\", name: \"steam_id\", kind: \"varchar\", nullable: true, default_zero: false, character_maximum_length: Some(128_i64) },\nColumnDescriptor { table: \"users\", name: \"custom_id\", kind: \"varchar\", nullable: true, default_zero: false, character_maximum_length: Some(128_i64) },\nColumnDescriptor { table: \"users\", name: \"edge_count\", kind: \"int8\", nullable: false, default_zero: true, character_maximum_length: None },\nColumnDescriptor { table: \"users\", name: \"create_time\", kind: \"timestamptz\", nullable: false, default_zero: false, character_maximum_length: None },\nColumnDescriptor { table: \"users\", name: \"update_time\", kind: \"timestamptz\", nullable: false, default_zero: false, character_maximum_length: None },\nColumnDescriptor { table: \"users\", name: \"verify_time\", kind: \"timestamptz\", nullable: false, default_zero: false, character_maximum_length: None },\nColumnDescriptor { table: \"users\", name: \"disable_time\", kind: \"timestamptz\", nullable: false, default_zero: false, character_maximum_length: None },\nColumnDescriptor { table: \"users\", name: \"facebook_instant_game_id\", kind: \"varchar\", nullable: true, default_zero: false, character_maximum_length: Some(128_i64) },\nColumnDescriptor { table: \"users\", name: \"apple_id\", kind: \"varchar\", nullable: true, default_zero: false, character_maximum_length: Some(128_i64) }" } else { "ColumnDescriptor { table: \"users\", name: \"id\", kind: \"uuid\", nullable: false, default_zero: false, character_maximum_length: None },\nColumnDescriptor { table: \"users\", name: \"username\", kind: \"varchar\", nullable: false, default_zero: false, character_maximum_length: Some(128_i64) },\nColumnDescriptor { table: \"users\", name: \"display_name\", kind: \"varchar\", nullable: true, default_zero: false, character_maximum_length: Some(255_i64) },\nColumnDescriptor { table: \"users\", name: \"avatar_url\", kind: \"varchar\", nullable: true, default_zero: false, character_maximum_length: Some(512_i64) },\nColumnDescriptor { table: \"users\", name: \"lang_tag\", kind: \"varchar\", nullable: false, default_zero: false, character_maximum_length: Some(18_i64) },\nColumnDescriptor { table: \"users\", name: \"location\", kind: \"varchar\", nullable: true, default_zero: false, character_maximum_length: Some(255_i64) },\nColumnDescriptor { table: \"users\", name: \"timezone\", kind: \"varchar\", nullable: true, default_zero: false, character_maximum_length: Some(255_i64) },\nColumnDescriptor { table: \"users\", name: \"metadata\", kind: \"jsonb\", nullable: false, default_zero: false, character_maximum_length: None },\nColumnDescriptor { table: \"users\", name: \"wallet\", kind: \"jsonb\", nullable: false, default_zero: false, character_maximum_length: None },\nColumnDescriptor { table: \"users\", name: \"email\", kind: \"varchar\", nullable: true, default_zero: false, character_maximum_length: Some(255_i64) },\nColumnDescriptor { table: \"users\", name: \"password\", kind: \"bytea\", nullable: true, default_zero: false, character_maximum_length: None },\nColumnDescriptor { table: \"users\", name: \"facebook_id\", kind: \"varchar\", nullable: true, default_zero: false, character_maximum_length: Some(128_i64) },\nColumnDescriptor { table: \"users\", name: \"google_id\", kind: \"varchar\", nullable: true, default_zero: false, character_maximum_length: Some(128_i64) },\nColumnDescriptor { table: \"users\", name: \"gamecenter_id\", kind: \"varchar\", nullable: true, default_zero: false, character_maximum_length: Some(128_i64) },\nColumnDescriptor { table: \"users\", name: \"steam_id\", kind: \"varchar\", nullable: true, default_zero: false, character_maximum_length: Some(128_i64) },\nColumnDescriptor { table: \"users\", name: \"custom_id\", kind: \"varchar\", nullable: true, default_zero: false, character_maximum_length: Some(128_i64) },\nColumnDescriptor { table: \"users\", name: \"edge_count\", kind: \"int4\", nullable: false, default_zero: true, character_maximum_length: None },\nColumnDescriptor { table: \"users\", name: \"create_time\", kind: \"timestamptz\", nullable: false, default_zero: false, character_maximum_length: None },\nColumnDescriptor { table: \"users\", name: \"update_time\", kind: \"timestamptz\", nullable: false, default_zero: false, character_maximum_length: None },\nColumnDescriptor { table: \"users\", name: \"verify_time\", kind: \"timestamptz\", nullable: false, default_zero: false, character_maximum_length: None },\nColumnDescriptor { table: \"users\", name: \"disable_time\", kind: \"timestamptz\", nullable: false, default_zero: false, character_maximum_length: None },\nColumnDescriptor { table: \"users\", name: \"facebook_instant_game_id\", kind: \"varchar\", nullable: true, default_zero: false, character_maximum_length: Some(128_i64) },\nColumnDescriptor { table: \"users\", name: \"apple_id\", kind: \"varchar\", nullable: true, default_zero: false, character_maximum_length: Some(128_i64) }" };
                ("CreateAccountTable", format!("ActionDescriptor::AccountTable(ImportTableDescriptor {{ table: {:?}, columns: &[{declarations}] }})", "users"))
            }
            "nakama_user_device" => {
                let declarations = "ColumnDescriptor { table: \"user_device\", name: \"id\", kind: \"varchar\", nullable: false, default_zero: false, character_maximum_length: Some(128_i64) },\nColumnDescriptor { table: \"user_device\", name: \"user_id\", kind: \"uuid\", nullable: false, default_zero: false, character_maximum_length: None },\nColumnDescriptor { table: \"user_device\", name: \"preferences\", kind: \"jsonb\", nullable: false, default_zero: false, character_maximum_length: None },\nColumnDescriptor { table: \"user_device\", name: \"push_token_amazon\", kind: \"varchar\", nullable: false, default_zero: false, character_maximum_length: Some(512_i64) },\nColumnDescriptor { table: \"user_device\", name: \"push_token_android\", kind: \"varchar\", nullable: false, default_zero: false, character_maximum_length: Some(512_i64) },\nColumnDescriptor { table: \"user_device\", name: \"push_token_huawei\", kind: \"varchar\", nullable: false, default_zero: false, character_maximum_length: Some(512_i64) },\nColumnDescriptor { table: \"user_device\", name: \"push_token_ios\", kind: \"varchar\", nullable: false, default_zero: false, character_maximum_length: Some(512_i64) },\nColumnDescriptor { table: \"user_device\", name: \"push_token_web\", kind: \"varchar\", nullable: false, default_zero: false, character_maximum_length: Some(512_i64) }";
                ("CreateAccountTable", format!("ActionDescriptor::AccountTable(ImportTableDescriptor {{ table: {:?}, columns: &[{declarations}] }})", "user_device"))
            }
            _ => panic!("unreviewed account action"),
        };
        writeln!(output, "MigrationAction {{ id: {id:?}, kind: MigrationActionKind::{kind}, descriptor: {descriptor}, sql: {statement:?} }},").unwrap();
    }
    (output, expected.len())
}
