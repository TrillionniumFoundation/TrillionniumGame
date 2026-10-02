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
    assert_eq!(lock["schema_version"], 3);
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
            3,
            "engine supports the locked v1 -> v2 -> v3 chain"
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
                    if index == 1 { 6 } else { 16 },
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
            writeln!(output, "MigrationAction {{ id: {id:?}, kind: MigrationActionKind::BackfillStorageJsonbV3, column: ColumnDescriptor {{ table: \"trnm_storage_objects\", name: \"@backfill\", kind: \"backfilled\", nullable: false, default_zero: false, character_maximum_length: None }}, sql: \"\" }},").unwrap();
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
            writeln!(output, "MigrationAction {{ id: {id:?}, kind: MigrationActionKind::{action_kind}, column: ColumnDescriptor {{ table: {table:?}, name: {name:?}, kind: {kind:?}, nullable: {nullable}, default_zero: false, character_maximum_length: {max_len:?} }}, sql: {line:?} }},").unwrap();
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
