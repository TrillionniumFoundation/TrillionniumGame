//! Actual source-DDL fixture → Rust export → external custody → native import.
//! The fixture does not execute Nakama or notarize an upstream production DB.
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::str::FromStr;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use postgres::{Client, Config, NoTls};
use serde_json::{json, Value};
use trnm_contracts::{Digest32, RefreshTokenId, SessionFamilyId, StableCode, UserId};
use trnm_persistence_pg::{
    verify_storage_export, CreateSessionFamily, DatabaseProfile, EntityId, IntegrityDigest,
    PgRepository, ReadPermission, RefreshTokenCredential, StorageActor, StorageBatchOperation,
    StorageExportOptions, StorageImportCustody, StorageImportOptions, StorageObjectKey,
    StorageTimestamp, StorageWriteOperation, VerifiedStorageExport, VersionCheck, WritePermission,
};

const AUDIT_MS: u64 = 2468;
const PAGE_ROWS: usize = 2;
const SOURCE_ROWS: usize = 8;
static NEXT_FIXTURE: AtomicU64 = AtomicU64::new(0);

include!("storage_import_v4_parts/environment.rs");
include!("storage_import_v4_parts/packet.rs");
include!("storage_import_v4_parts/lifecycle.rs");
include!("storage_import_v4_parts/tamper.rs");
include!("storage_import_v4_parts/security.rs");

#[test]
fn storage_v4_source_export_custody_resume_finish_and_tamper_are_native() {
    let Some(environment) = live_environment() else {
        eprintln!("storage_v4_import_live_skipped");
        return;
    };
    let source = Database::new(&environment, "source");
    seed_source(&source, &environment.upstream_directory);
    let original_source = source_rows(&mut source.client());
    assert_eq!(original_source.len(), SOURCE_ROWS);
    let files = PacketDirectory::new();
    let output = if let Some(root) = evidence_root() {
        fs::create_dir_all(&root).unwrap();
        assert!(
            fs::read_dir(&root).unwrap().next().is_none(),
            "import evidence directory must be empty"
        );
        root.join("packet")
    } else {
        files.path.join("original")
    };
    let original = export(&environment, &source, output);
    retain_custody(&environment, &original);
    let custody = independent_custody(&environment, &original);
    let packet = verify_storage_export(&original, &custody).unwrap();
    assert_eq!(packet.summary().rows, SOURCE_ROWS);
    assert_eq!(packet.summary().pages, SOURCE_ROWS / PAGE_ROWS);
    assert_eq!(
        packet.summary().source_execution_class,
        "native-source-ddl-fixture"
    );
    assert!(!packet.summary().compatibility_credit);
    assert!(!packet.summary().production_ready);
    assert!(!packet.summary().full_nakama_replacement);

    packet_admission_negatives(&environment, &files, &original);
    native_preflight_negatives(&environment, &files, &original);
    dedicated_target_begin_rechecks_emptiness(&environment, &packet);
    dedicated_target_rejects_outbox_history(&environment, &packet);
    lifecycle_and_tamper(&environment, &source, &files, &original_source, &packet);
    native_source_collation_is_not_silently_normalized(&environment);
    println!(
        "\nstorage_v4_import_live_executed profile={}",
        environment.profile.metadata_value()
    );
}
