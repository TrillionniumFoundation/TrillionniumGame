from __future__ import annotations

from pathlib import Path
import sys


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one source shape, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: apply-partition-compat.py <world-root>")
    root = Path(sys.argv[1]).resolve()
    tests = root / "trillionnium/crates/trnm-game-server/tests"
    support = tests / "support"
    support.mkdir(parents=True, exist_ok=True)
    (support / "mod.rs").write_text(r'''use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};

fn read_nonempty(path: &Path) -> String {
    let source = fs::read_to_string(path)
        .unwrap_or_else(|error| panic!("read direct source {}: {error}", path.display()));
    assert!(!source.is_empty(), "direct source {} is empty", path.display());
    source
}

fn record_path<'a>(record: &'a Value, field: &str) -> &'a str {
    record
        .get(field)
        .and_then(Value::as_str)
        .unwrap_or_else(|| panic!("direct-source manifest record has no {field}"))
}

pub fn read_crate_source_bundle(relative: &str) -> String {
    let crate_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let entry = crate_root.join(relative);
    let mut source = read_nonempty(&entry);
    let manifest_path = entry
        .parent()
        .expect("source entrypoint has a parent")
        .join("lib_parts/manifest.json");
    if !manifest_path.exists() {
        return source;
    }
    let manifest: Value = serde_json::from_str(&read_nonempty(&manifest_path))
        .expect("direct-source manifest is valid JSON");
    assert_eq!(
        manifest.get("semantic_generation").and_then(Value::as_bool),
        Some(false),
        "direct-source manifest must prohibit semantic generation"
    );
    let mut listed = BTreeSet::new();
    for field in ["parts", "nested_test_parts"] {
        let records = manifest
            .get(field)
            .and_then(Value::as_array)
            .unwrap_or_else(|| panic!("direct-source manifest {field} is not an array"));
        for record in records {
            let relative = record_path(record, "path");
            assert!(relative.starts_with("lib_parts/"));
            let path = entry.parent().expect("entrypoint parent").join(relative);
            let bytes = fs::read(&path)
                .unwrap_or_else(|error| panic!("read direct source {}: {error}", path.display()));
            assert_eq!(
                record.get("bytes").and_then(Value::as_u64),
                Some(bytes.len() as u64),
                "direct-source byte length drifted for {relative}"
            );
            let actual_sha = format!("{:x}", Sha256::digest(&bytes));
            assert_eq!(
                record.get("sha256").and_then(Value::as_str),
                Some(actual_sha.as_str()),
                "direct-source SHA-256 drifted for {relative}"
            );
            assert!(listed.insert(path));
            source.push('\n');
            source.push_str(std::str::from_utf8(&bytes).expect("direct source is UTF-8"));
        }
    }
    let parts_dir = entry.parent().expect("entrypoint parent").join("lib_parts");
    let mut discovered = BTreeSet::new();
    fn walk(directory: &Path, output: &mut BTreeSet<PathBuf>) {
        for entry in fs::read_dir(directory)
            .unwrap_or_else(|error| panic!("walk direct source {}: {error}", directory.display()))
        {
            let path = entry.expect("directory entry is readable").path();
            if path.is_dir() {
                walk(&path, output);
            } else if path.extension().is_some_and(|extension| extension == "rs") {
                output.insert(path);
            }
        }
    }
    walk(&parts_dir, &mut discovered);
    assert_eq!(discovered, listed, "direct-source manifest/file set drifted");
    source
}

pub fn read_settlement_worker_bundle() -> String {
    [
        read_crate_source_bundle("src/settlement_worker.rs"),
        read_crate_source_bundle("src/settlement_worker_legacy.rs"),
        read_crate_source_bundle("src/settlement_worker_runtime_v2.rs"),
    ]
    .join("\n")
}
''', encoding="utf-8")

    (tests / "settlement_game_server_boundary.rs").write_text(r'''mod support;

use support::{read_crate_source_bundle, read_settlement_worker_bundle};

#[test]
fn game_server_does_not_execute_terminal_economy_settlement() {
    let source = read_crate_source_bundle("src/lib.rs");
    assert!(!source.contains("trnm_game_server_lib_generated.rs"));
    assert!(!source.contains("OUT_DIR"));
    assert!(!source.contains("reconcile_economy(&state.cex"));
    assert!(!source.contains("settle_pending_matches(&settlement_state"));
    assert!(source.contains(
        "terminal settlement is owned by trnm-settlement-worker; in-process settlement is prohibited"
    ));
}

#[test]
fn both_runtime_entrypoints_register_the_complete_settlement_migration_chain() {
    let game_server = read_crate_source_bundle("src/lib.rs");
    let worker = read_settlement_worker_bundle();
    let worker_entry = read_crate_source_bundle("src/settlement_worker.rs");
    assert!(!worker_entry.contains("OUT_DIR"));
    assert!(!worker_entry.contains("trnm_settlement_worker_generated.rs"));
    assert!(worker_entry.contains("settlement_worker_legacy.rs"));
    assert!(worker_entry.contains("settlement_worker_runtime_v2.rs"));
    for marker in [
        "0016_online_settlement_outbox_v1",
        "0017_online_settlement_worker_runtime_v1",
        "0018_online_settlement_operator_controls_v1",
        "0019_online_settlement_quarantine_v1",
    ] {
        assert!(game_server.contains(marker), "direct game server lost {marker}");
        assert!(worker.contains(marker), "direct settlement worker lost {marker}");
    }
}

#[test]
fn direct_migration_includes_use_an_unambiguous_environment_macro() {
    for source in [
        read_crate_source_bundle("src/lib.rs"),
        read_settlement_worker_bundle(),
    ] {
        assert!(source.contains("concat!(::std::env!(\"CARGO_MANIFEST_DIR\")"));
        assert!(!source.contains("concat!(env!(\"CARGO_MANIFEST_DIR\")"));
    }
}
''', encoding="utf-8")

    fault = tests / "settlement_fault_model.rs"
    text = fault.read_text(encoding="utf-8")
    text = replace_once(
        text,
        'use std::fs;\nuse std::path::PathBuf;\n',
        'mod support;\n\nuse support::read_crate_source_bundle;\n',
        "fault-model imports",
    )
    text = replace_once(
        text,
        '    let source_path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/lib.rs");\n'
        '    let source = fs::read_to_string(source_path).expect("read game-server source");\n',
        '    let source = read_crate_source_bundle("src/lib.rs");\n',
        "fault-model source bundle",
    )
    fault.write_text(text, encoding="utf-8")

    (tests / "direct_source_bundle.rs").write_text(r'''mod support;

use support::read_crate_source_bundle;

#[test]
fn game_server_partition_is_hash_bound_and_non_generated() {
    let source = read_crate_source_bundle("src/lib.rs");
    assert!(source.contains("Ownership section: authority_foundation"));
    assert!(source.contains("Ownership section: campaign_persistence"));
    assert!(!source.contains("trnm_game_server_lib_generated.rs"));
    assert!(!source.contains("src/lib.rs.in"));
}
''', encoding="utf-8")

    print("partition compatibility tests and manifest reader installed")


if __name__ == "__main__":
    main()
