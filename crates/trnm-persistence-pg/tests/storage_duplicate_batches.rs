//! Bounded native regression. Requires a dedicated migrated ABI4
//! database and creates only its own source/target databases for a separate
//! pinned-source-DDL export/import fixture. No normalization, invented custody
//! or automatic retry; these controlled synthetic rows do not execute Nakama.
use postgres::{Client, NoTls};
use std::{
    env,
    panic::{catch_unwind, resume_unwind, AssertUnwindSafe},
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use trnm_contracts::{RetryClass, StableCode, UserId};
use trnm_persistence_pg::{
    ContentVersion, DatabaseProfile, IntegrityDigest, PgPool, PgPoolConfig, PgRepository,
    ReadPermission, StorageActor, StorageBatchOperation, StorageDeleteOperation,
    StorageNakamaBatchKind, StorageObjectKey, StorageTimes, StorageTimestamp,
    StorageWriteOperation, StoredStorageMutationReceipt, VersionCheck, WritePermission,
};

const OWNER: UserId = UserId::new([0x93; 16]);
const A: &[u8] = br#" { "foo": "bar", "number": 1e0 } "#;
const B: &[u8] = br#"{"foo":"baz","number":2}"#;
const C: &[u8] = br#"{"foo":"qux","number":3}"#;

fn environment() -> Option<(String, DatabaseProfile)> {
    let required = match env::var("TRNM_REQUIRE_LIVE_DATABASE") {
        Err(env::VarError::NotPresent) => false,
        Ok(value) if matches!(value.as_str(), "1" | "true" | "TRUE" | "yes" | "YES") => true,
        Ok(value) if matches!(value.as_str(), "0" | "false" | "FALSE" | "no" | "NO") => false,
        _ => panic!("TRNM_REQUIRE_LIVE_DATABASE is invalid"),
    };
    let url = match env::var("TRNM_DATABASE_URL") {
        Ok(url) if !url.is_empty() => url,
        Ok(_) | Err(env::VarError::NotPresent) if !required => return None,
        _ => panic!("required duplicate-batch database URL unavailable"),
    };
    let profile = match env::var("TRNM_DATABASE_PROFILE").as_deref() {
        Ok("postgresql") => DatabaseProfile::PostgreSql,
        Ok("cockroachdb") => DatabaseProfile::CockroachDb,
        _ => panic!("duplicate-batch profile must be explicit"),
    };
    Some((url, profile))
}
fn write(
    key: &StorageObjectKey,
    value: &[u8],
    expected: VersionCheck,
    acl: WritePermission,
) -> StorageBatchOperation {
    StorageBatchOperation::Write(StorageWriteOperation {
        key: key.clone(),
        value: value.to_vec(),
        expected,
        read_permission: ReadPermission::OWNER,
        write_permission: acl,
    })
}
fn delete(
    key: &StorageObjectKey,
    expected: Option<trnm_storage_core::ExpectedVersion>,
) -> StorageBatchOperation {
    StorageBatchOperation::Delete(StorageDeleteOperation {
        key: key.clone(),
        expected_version: expected,
    })
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct RawRow {
    collection: String,
    key: String,
    owner: Vec<u8>,
    native: String,
    raw: Option<Vec<u8>>,
    request_sha: Option<Vec<u8>>,
    public_version: String,
    projection_sha: Vec<u8>,
    origin: String,
    manifest_sha: Option<Vec<u8>>,
    read: i16,
    write: i16,
    updated_at_ms: i64,
    create: Option<StorageTimestamp>,
    update: Option<StorageTimestamp>,
}
fn snapshot(control: &mut Client, collection: &str) -> Vec<RawRow> {
    control
        .query(
            "SELECT collection, object_key, user_id, value_jsonb::TEXT, value_bytes, \
         version_digest, public_version::TEXT, value_projection_digest, value_origin, \
         source_manifest_digest, read_permission, write_permission, updated_at_ms, \
         create_time, update_time FROM public.trnm_storage_objects WHERE collection=$1 \
         ORDER BY collection, object_key, user_id",
            &[&collection],
        )
        .unwrap_or_else(|_| panic!("duplicate fixture raw snapshot failed"))
        .into_iter()
        .map(|row| RawRow {
            collection: row.get(0),
            key: row.get(1),
            owner: row.get(2),
            native: row.get(3),
            raw: row.get(4),
            request_sha: row.get(5),
            public_version: row.get(6),
            projection_sha: row.get(7),
            origin: row.get(8),
            manifest_sha: row.get(9),
            read: row.get(10),
            write: row.get(11),
            updated_at_ms: row.get(12),
            create: row.get(13),
            update: row.get(14),
        })
        .collect()
}
fn native(control: &mut Client, request: &[u8]) -> Vec<u8> {
    control
        .query_one(
            "SELECT $1::TEXT::JSONB::TEXT",
            &[&std::str::from_utf8(request).unwrap()],
        )
        .unwrap_or_else(|_| panic!("duplicate fixture native render failed"))
        .get::<_, String>(0)
        .into_bytes()
}
fn cleanup(control: &mut Client, collection: &str) -> Result<(), postgres::Error> {
    control
        .execute(
            "DELETE FROM public.trnm_storage_objects WHERE collection=$1",
            &[&collection],
        )
        .map(|_| ())
}
fn check_step_acks(receipts: &[StoredStorageMutationReceipt], values: &[&[u8]]) {
    assert_eq!(receipts.len(), values.len());
    for (index, request) in values.iter().enumerate() {
        assert_eq!(
            receipts[index].receipt.current_version,
            Some(ContentVersion::from_value(request))
        );
        assert!(receipts[index].times.create.is_some());
        assert!(receipts[index].times.update.is_some());
        if index != 0 {
            assert_eq!(
                receipts[index]
                    .receipt
                    .previous_version
                    .as_ref()
                    .map(|v| v.as_str()),
                Some(ContentVersion::from_value(values[index - 1]).as_str())
            );
            assert_eq!(receipts[index].times.create, receipts[0].times.create);
            assert_eq!(receipts[index].times.update, receipts[0].times.update);
        }
    }
}

fn bounded_repository(url: &str, profile: DatabaseProfile) -> PgRepository {
    let policy = PgPoolConfig {
        max_size: 1,
        min_idle: 0,
        acquire_timeout: Duration::from_secs(5),
        statement_timeout: Duration::from_secs(5),
        lock_timeout: Duration::from_secs(5),
        ..PgPoolConfig::default()
    };
    let pool = PgPool::connect_plain(url, profile, policy)
        .unwrap_or_else(|_| panic!("duplicate fixture bounded pool connect failed"));
    pool.acquire()
        .unwrap_or_else(|_| panic!("duplicate fixture bounded repository acquire failed"))
}

fn bounded_control(url: &str) -> Client {
    let mut config = url
        .parse::<postgres::Config>()
        .unwrap_or_else(|_| panic!("duplicate fixture private config failed"));
    config.connect_timeout(Duration::from_secs(5));
    let mut control = config
        .connect(NoTls)
        .unwrap_or_else(|_| panic!("duplicate fixture control connect failed"));
    control
        .batch_execute("SET statement_timeout='5s'; SET lock_timeout='5s'")
        .unwrap_or_else(|_| panic!("duplicate fixture control deadlines failed"));
    control
}

fn expected_known_row(
    control: &mut Client,
    key: &StorageObjectKey,
    request: &[u8],
    read: ReadPermission,
    write: WritePermission,
    audit_ms: i64,
    times: StorageTimes,
) -> RawRow {
    let projected = native(control, request);
    RawRow {
        collection: key.collection().to_owned(),
        key: key.key().to_owned(),
        owner: key.user_id().as_bytes().to_vec(),
        native: String::from_utf8(projected.clone()).expect("native JSONB is UTF8"),
        raw: Some(request.to_vec()),
        request_sha: Some(
            IntegrityDigest::from_value(request)
                .get()
                .as_bytes()
                .to_vec(),
        ),
        public_version: ContentVersion::from_value(request).as_str().to_owned(),
        projection_sha: IntegrityDigest::from_value(&projected)
            .get()
            .as_bytes()
            .to_vec(),
        origin: "write-request-bytes".to_owned(),
        manifest_sha: None,
        read: read.get(),
        write: write.get(),
        updated_at_ms: audit_ms,
        create: times.create,
        update: times.update,
    }
}

fn assert_known_row(
    control: &mut Client,
    key: &StorageObjectKey,
    request: &[u8],
    read: ReadPermission,
    write: WritePermission,
    audit_ms: i64,
    times: StorageTimes,
) {
    let expected = expected_known_row(control, key, request, read, write, audit_ms, times);
    let actual = snapshot(control, key.collection());
    let rows: Vec<_> = actual
        .iter()
        .filter(|row| {
            row.key == key.key() && row.owner.as_slice() == key.user_id().as_bytes().as_slice()
        })
        .collect();
    assert_eq!(rows.len(), 1, "successful storage key cardinality changed");
    assert_eq!(
        *rows[0], expected,
        "successful full15field native row differs"
    );
}

fn preserve_primary_cleanup(
    primary: std::thread::Result<()>,
    cleanup_result: Result<(), postgres::Error>,
) {
    if let Err(payload) = primary {
        if let Err(error) = cleanup_result {
            eprintln!(
                "nakama_duplicate_cleanup_failed primary_panic_preserved=true sqlstate={}",
                error.code().map_or("unavailable", |code| code.code())
            );
        }
        // The original panic hook already emitted its trace. Resume the same
        // payload; never replace it with a secondary cleanup assertion.
        resume_unwind(payload);
    }
    if cleanup_result.is_err() {
        panic!("duplicate fixture scoped cleanup failed after successful body");
    }
}

#[test]
fn nakama_duplicate_batches_preserve_step_receipts_and_native_atomicity() {
    let Some((url, profile)) = environment() else {
        println!("nakama_duplicate_batches_skipped reason=optional_database_absent");
        return;
    };
    let collection = format!(
        "nakama-duplicate-draft-{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    );
    let mut repository = bounded_repository(&url, profile);
    let mut control = bounded_control(&url);
    cleanup(&mut control, &collection)
        .unwrap_or_else(|_| panic!("duplicate fixture initial owned cleanup failed"));
    let result = catch_unwind(AssertUnwindSafe(|| {
        assert_ne!(
            ContentVersion::from_value(A),
            ContentVersion::from_value(&native(&mut control, A)),
            "raw request MD5 and native render MD5 must remain distinct in this fixture"
        );
        let key = |name: &str, owner| StorageObjectKey::new(&collection, name, owner).unwrap();
        let client = key("client", OWNER);
        let receipts = repository
            .apply_storage_batch_nakama_with_metadata(
                StorageActor::User(OWNER),
                &[
                    write(&client, A, VersionCheck::Any, WritePermission::OWNER),
                    write(&client, B, VersionCheck::Any, WritePermission::NONE),
                ],
                10,
                StorageNakamaBatchKind::Write,
            )
            .unwrap();
        check_step_acks(&receipts, &[A, B]);
        let stored = repository
            .read_storage_object(StorageActor::Server, &client)
            .unwrap();
        assert_eq!(stored.value, native(&mut control, B));
        assert_eq!(stored.write_permission, WritePermission::NONE);
        assert!(stored.collision_witness.unwrap().matches_request(B));
        assert_known_row(
            &mut control,
            &client,
            B,
            ReadPermission::OWNER,
            WritePermission::NONE,
            10,
            receipts[1].times,
        );
        let before_noop = snapshot(&mut control, &collection);
        let noops = repository
            .apply_storage_batch_nakama_with_metadata(
                StorageActor::Server,
                &[
                    write(&client, B, VersionCheck::Any, WritePermission::NONE),
                    write(&client, B, VersionCheck::Any, WritePermission::NONE),
                ],
                999,
                StorageNakamaBatchKind::Write,
            )
            .unwrap();
        assert_eq!(noops[0].times, receipts[1].times);
        assert_eq!(noops[1].times, receipts[1].times);
        assert_eq!(
            snapshot(&mut control, &collection),
            before_noop,
            "both known Any no-ops must preserve all15fields including audit/provenance"
        );

        let server = key("server", UserId::new([0; 16]));
        let receipts = repository
            .apply_storage_batch_nakama_with_metadata(
                StorageActor::Server,
                &[
                    write(&server, A, VersionCheck::Any, WritePermission::OWNER),
                    write(&server, B, VersionCheck::Any, WritePermission::NONE),
                    write(&server, C, VersionCheck::Any, WritePermission::OWNER),
                ],
                20,
                StorageNakamaBatchKind::Write,
            )
            .unwrap();
        check_step_acks(&receipts, &[A, B, C]);
        assert_eq!(
            repository
                .read_storage_object(StorageActor::Server, &server)
                .unwrap()
                .value,
            native(&mut control, C)
        );
        assert_known_row(
            &mut control,
            &server,
            C,
            ReadPermission::OWNER,
            WritePermission::OWNER,
            20,
            receipts[2].times,
        );

        let exact = key("exact", OWNER);
        let receipts = repository
            .apply_storage_batch_nakama_with_metadata(
                StorageActor::User(OWNER),
                &[
                    write(&exact, A, VersionCheck::Any, WritePermission::OWNER),
                    write(
                        &exact,
                        B,
                        VersionCheck::Exact(ContentVersion::from_value(A).into()),
                        WritePermission::OWNER,
                    ),
                ],
                30,
                StorageNakamaBatchKind::Write,
            )
            .unwrap();
        check_step_acks(&receipts, &[A, B]);
        assert_known_row(
            &mut control,
            &exact,
            B,
            ReadPermission::OWNER,
            WritePermission::OWNER,
            30,
            receipts[1].times,
        );

        // Every late error must preserve the complete native 15-field snapshot,
        // including request/projection witnesses, ACLs, opaque tokens and times.
        for (operations, code) in [
            (
                vec![
                    write(&exact, C, VersionCheck::Any, WritePermission::NONE),
                    write(
                        &exact,
                        A,
                        VersionCheck::Exact("stale".into()),
                        WritePermission::OWNER,
                    ),
                ],
                StableCode::PermissionDenied,
            ),
            (
                vec![
                    write(
                        &exact,
                        C,
                        VersionCheck::Exact(ContentVersion::from_value(B).into()),
                        WritePermission::OWNER,
                    ),
                    write(
                        &exact,
                        A,
                        VersionCheck::Exact(ContentVersion::from_value(B).into()),
                        WritePermission::OWNER,
                    ),
                ],
                StableCode::FailedPrecondition,
            ),
            (
                vec![
                    write(&exact, C, VersionCheck::Any, WritePermission::NONE),
                    write(
                        &exact,
                        A,
                        VersionCheck::MustNotExist,
                        WritePermission::OWNER,
                    ),
                ],
                StableCode::AlreadyExists,
            ),
        ] {
            let before = snapshot(&mut control, &collection);
            assert_eq!(
                repository
                    .apply_storage_batch_nakama_with_metadata(
                        StorageActor::User(OWNER),
                        &operations,
                        40,
                        StorageNakamaBatchKind::Write
                    )
                    .unwrap_err()
                    .code(),
                code
            );
            assert_eq!(snapshot(&mut control, &collection), before);
        }
        let insert_only = key("insert-only", OWNER);
        let before = snapshot(&mut control, &collection);
        assert_eq!(
            repository
                .apply_storage_batch_nakama_with_metadata(
                    StorageActor::User(OWNER),
                    &[
                        write(
                            &insert_only,
                            A,
                            VersionCheck::MustNotExist,
                            WritePermission::NONE
                        ),
                        write(
                            &insert_only,
                            B,
                            VersionCheck::MustNotExist,
                            WritePermission::OWNER
                        ),
                    ],
                    50,
                    StorageNakamaBatchKind::Write
                )
                .unwrap_err()
                .code(),
            StableCode::AlreadyExists
        );
        assert_eq!(snapshot(&mut control, &collection), before);

        let before = snapshot(&mut control, &collection);
        let invalid = "{";
        let sqlstate = control
            .query_one("SELECT $1::TEXT::JSONB::TEXT", &[&invalid])
            .expect_err("malformed JSON unexpectedly parsed")
            .code()
            .map(|code| code.code().to_owned());
        let error = repository
            .apply_storage_batch_nakama_with_metadata(
                StorageActor::User(OWNER),
                &[
                    write(&exact, C, VersionCheck::Any, WritePermission::OWNER),
                    write(
                        &exact,
                        invalid.as_bytes(),
                        VersionCheck::Any,
                        WritePermission::OWNER,
                    ),
                ],
                55,
                StorageNakamaBatchKind::Write,
            )
            .unwrap_err();
        assert_eq!(error.code(), StableCode::InvalidArgument);
        assert_eq!(error.reason(), "database_constraint_violation");
        assert_eq!(error.retry(), RetryClass::Never);
        // This independent native probe is not the batch's discarded SQLSTATE.
        assert_eq!(sqlstate.as_deref(), Some("22P02"));
        assert_eq!(snapshot(&mut control, &collection), before);
        println!(
            "\nnakama_duplicate_late_json_rejection profile={} independent_probe_sqlstate={} actual_batch_domain_code={:?}",
            profile.metadata_value(),
            sqlstate.as_deref().unwrap_or("unavailable"),
            error.code()
        );

        // A blind no-op's ACK keeps the old microtime even when a later exact
        // occurrence updates the row; don't rebuild earlier ACKs from final row.
        control.execute("UPDATE public.trnm_storage_objects SET update_time='2000-01-01 00:00:07.123456+00'::TIMESTAMPTZ WHERE collection=$1 AND object_key='exact'", &[&collection]).unwrap();
        let before = snapshot(&mut control, &collection);
        let old_time = before.iter().find(|row| row.key == "exact").unwrap().update;
        let receipts = repository
            .apply_storage_batch_nakama_with_metadata(
                StorageActor::User(OWNER),
                &[
                    write(&exact, B, VersionCheck::Any, WritePermission::OWNER),
                    write(
                        &exact,
                        B,
                        VersionCheck::Exact(ContentVersion::from_value(B).into()),
                        WritePermission::OWNER,
                    ),
                ],
                60,
                StorageNakamaBatchKind::Write,
            )
            .unwrap();
        assert_eq!(receipts[0].times.update, old_time);
        assert_ne!(receipts[1].times.update, old_time);
        assert_eq!(receipts[0].times.create, receipts[1].times.create);
        assert_known_row(
            &mut control,
            &exact,
            B,
            ReadPermission::OWNER,
            WritePermission::OWNER,
            60,
            receipts[1].times,
        );

        // Client duplicate delete, and conditional authoritative duplicate
        // delete, reject the now-missing second occurrence and roll back.
        let before = snapshot(&mut control, &collection);
        assert_eq!(
            repository
                .apply_storage_batch_nakama_with_metadata(
                    StorageActor::User(OWNER),
                    &[delete(&exact, None), delete(&exact, None),],
                    70,
                    StorageNakamaBatchKind::Delete
                )
                .unwrap_err()
                .code(),
            StableCode::NotFound
        );
        assert_eq!(snapshot(&mut control, &collection), before);
        let token = ContentVersion::from_value(B);
        assert_eq!(
            repository
                .apply_storage_batch_nakama_with_metadata(
                    StorageActor::Server,
                    &[
                        delete(&exact, Some(token.into())),
                        delete(&exact, Some(token.into())),
                    ],
                    70,
                    StorageNakamaBatchKind::Delete
                )
                .unwrap_err()
                .code(),
            StableCode::NotFound
        );
        assert_eq!(snapshot(&mut control, &collection), before);
        assert_eq!(
            repository
                .apply_storage_batch_nakama_with_metadata(
                    StorageActor::Server,
                    &[delete(&exact, Some("*".into())),],
                    70,
                    StorageNakamaBatchKind::Delete
                )
                .unwrap_err()
                .code(),
            StableCode::FailedPrecondition
        );
        assert_eq!(snapshot(&mut control, &collection), before);
        let missing = key("absent", OWNER);
        let receipts = repository
            .apply_storage_batch_nakama_with_metadata(
                StorageActor::Server,
                &[
                    delete(&exact, None),
                    delete(&exact, None),
                    delete(&missing, None),
                ],
                80,
                StorageNakamaBatchKind::Delete,
            )
            .unwrap();
        assert!(receipts[0].receipt.previous_version.is_some());
        for receipt in &receipts[1..] {
            assert_eq!(receipt.receipt.previous_version, None);
            assert_eq!(receipt.receipt.current_version, None);
            assert_eq!(receipt.times, Default::default());
        }

        let after_delete = snapshot(&mut control, &collection);
        assert!(
            !after_delete
                .iter()
                .any(|row| row.key == "exact" || row.key == "absent"),
            "authoritative duplicate deletes left an existing or missing row"
        );
        let expected_after_delete: Vec<_> = before
            .iter()
            .filter(|row| row.key != "exact")
            .cloned()
            .collect();
        assert_eq!(
            after_delete, expected_after_delete,
            "authoritative duplicate delete changed an unrelated full15field row"
        );

        // Sorted occurrence execution must not reorder ACK positions.
        // Retain the source-derived occurrence plan.
        let a = key("sort-a", OWNER);
        let b = key("sort-b", OWNER);
        let operations = [
            write(&b, A, VersionCheck::Any, WritePermission::OWNER),
            write(&a, B, VersionCheck::Any, WritePermission::OWNER),
            write(&b, C, VersionCheck::Any, WritePermission::OWNER),
        ];
        let receipts = repository
            .apply_storage_batch_nakama_with_metadata(
                StorageActor::User(OWNER),
                &operations,
                90,
                StorageNakamaBatchKind::Write,
            )
            .unwrap();
        for (index, operation) in operations.iter().enumerate() {
            let StorageBatchOperation::Write(write) = operation else {
                unreachable!()
            };
            assert_eq!(receipts[index].receipt.key, write.key);
            assert_eq!(
                receipts[index].receipt.current_version,
                Some(ContentVersion::from_value(&write.value))
            );
        }
        assert_eq!(
            receipts[2]
                .receipt
                .previous_version
                .as_ref()
                .map(|v| v.as_str()),
            Some(ContentVersion::from_value(A).as_str())
        );

        assert_known_row(
            &mut control,
            &a,
            B,
            ReadPermission::OWNER,
            WritePermission::OWNER,
            90,
            receipts[1].times,
        );
        assert_known_row(
            &mut control,
            &b,
            C,
            ReadPermission::OWNER,
            WritePermission::OWNER,
            90,
            receipts[2].times,
        );

        // Actual pinned Nakama HTTP observation: b,b,a x11 sorts b ordinal1
        // before ordinal0. Ordinal0 removes write permission only after ordinal1.
        // ACKs remain at original positions; all15 final fields remain checked.
        let go_a = key("go13-a", OWNER);
        let go_b = key("go13-b", OWNER);
        let go_values: Vec<Vec<u8>> = (0..13)
            .map(|ordinal| format!("{{\"ordinal\":{ordinal}}}").into_bytes())
            .collect();
        let go_operations: Vec<_> = go_values
            .iter()
            .enumerate()
            .map(|(ordinal, value)| {
                write(
                    if ordinal < 2 { &go_b } else { &go_a },
                    value,
                    VersionCheck::Any,
                    if ordinal == 0 {
                        WritePermission::NONE
                    } else {
                        WritePermission::OWNER
                    },
                )
            })
            .collect();
        let go_receipts = repository
            .apply_storage_batch_nakama_with_metadata(
                StorageActor::User(OWNER),
                &go_operations,
                95,
                StorageNakamaBatchKind::Write,
            )
            .unwrap();
        assert_eq!(go_receipts.len(), 13);
        for (ordinal, receipt) in go_receipts.iter().enumerate() {
            assert_eq!(receipt.receipt.key, *go_operations[ordinal].key());
            assert_eq!(
                receipt.receipt.current_version,
                Some(ContentVersion::from_value(&go_values[ordinal]))
            );
        }
        assert_known_row(
            &mut control,
            &go_a,
            &go_values[12],
            ReadPermission::OWNER,
            WritePermission::OWNER,
            95,
            go_receipts[12].times,
        );
        assert_known_row(
            &mut control,
            &go_b,
            &go_values[0],
            ReadPermission::OWNER,
            WritePermission::NONE,
            95,
            go_receipts[0].times,
        );
        println!("nakama_duplicate_go13_executed profile={} occurrences=13 final_ordinals=a12_b0 ack_positions=original fields=15", profile.metadata_value());

        // Existing typed entry remains strict; no mutation from rejected dups.
        let before = snapshot(&mut control, &collection);
        let duplicates = [
            write(&a, A, VersionCheck::Any, WritePermission::OWNER),
            write(&a, B, VersionCheck::Any, WritePermission::OWNER),
        ];
        assert_eq!(
            repository
                .apply_storage_batch(StorageActor::Server, &duplicates, 100)
                .unwrap_err()
                .reason(),
            "duplicate_storage_key_in_batch"
        );
        assert_eq!(snapshot(&mut control, &collection), before);
        // A sequential delete rejects its earlier missing key while a later
        // existing key is locked by a separate native transaction. No lock
        // release or sleep causes this rejection; the blocker stays open until
        // the worker's real result reaches the bounded channel.
        let lock_missing = key("lock-a-missing", OWNER);
        let lock_later = key("lock-z-existing", OWNER);
        repository
            .apply_storage_batch_nakama_with_metadata(
                StorageActor::Server,
                &[write(
                    &lock_later,
                    A,
                    VersionCheck::Any,
                    WritePermission::OWNER,
                )],
                101,
                StorageNakamaBatchKind::Write,
            )
            .unwrap();
        let before_lock = snapshot(&mut control, &collection);
        let mut blocker_control = bounded_control(&url);
        let mut blocker = blocker_control.transaction().unwrap();
        let held = blocker.query_one(
            "SELECT object_key FROM public.trnm_storage_objects WHERE collection=$1 AND object_key=$2 AND user_id=$3 FOR UPDATE",
            &[&collection, &lock_later.key(), &OWNER.as_bytes().as_slice()],
        ).unwrap();
        assert_eq!(held.get::<_, String>(0), lock_later.key());
        let private_worker_url = url.clone();
        let (send, receive) = std::sync::mpsc::sync_channel(1);
        let worker = std::thread::spawn(move || {
            let mut worker_repository = bounded_repository(&private_worker_url, profile);
            let outcome = worker_repository.apply_storage_batch_nakama_with_metadata(
                StorageActor::User(OWNER),
                &[delete(&lock_missing, None), delete(&lock_later, None)],
                102,
                StorageNakamaBatchKind::Delete,
            );
            send.send(outcome)
                .expect("native occurrence result receiver unavailable");
        });
        let while_blocker_held = receive.recv_timeout(Duration::from_secs(4));
        let missing_original_result = while_blocker_held.is_err();
        if let Ok(Err(error)) = &while_blocker_held {
            println!(
                "nakama_duplicate_occurrence_actual_result profile={} actual_batch_domain_code={:?} actual_batch_reason={} actual_batch_retry={:?} actual_batch_sqlstate=null",
                profile.metadata_value(), error.code(), error.reason(), error.retry()
            );
        }
        // Capture the original four-second causal assertion before releasing
        // the blocker. A secondary rollback or worker panic must not replace it.
        let primary = catch_unwind(AssertUnwindSafe(|| {
            let error = while_blocker_held
                .expect("later storage lock delayed an earlier missing-delete rejection")
                .unwrap_err();
            assert_eq!(error.code(), StableCode::NotFound);
        }));
        let settling_started = std::time::Instant::now();
        let settling_budget = Duration::from_secs(6);
        let rollback = blocker.rollback();
        if let Err(error) = &rollback {
            eprintln!(
                "nakama_duplicate_occurrence_secondary_rollback_failed profile={} sqlstate={} primary_trace_preserved=true",
                profile.metadata_value(), error.code().map_or("none", |code| code.code())
            );
        }
        if missing_original_result {
            // Preserve a late real result for diagnostics without converting
            // an original causal timeout into a successful observation.
            let remaining = settling_budget.saturating_sub(settling_started.elapsed());
            match receive.recv_timeout(remaining) {
                Ok(Err(error)) => println!(
                    "nakama_duplicate_occurrence_late_result profile={} actual_batch_domain_code={:?} actual_batch_reason={} actual_batch_retry={:?} actual_batch_sqlstate=null original_causal_result_missing=true",
                    profile.metadata_value(), error.code(), error.reason(), error.retry()
                ),
                Ok(Ok(receipts)) => eprintln!(
                    "nakama_duplicate_occurrence_late_success profile={} receipt_count={} original_causal_result_missing=true",
                    profile.metadata_value(), receipts.len()
                ),
                Err(_) => eprintln!(
                    "nakama_duplicate_occurrence_late_result_unavailable profile={} original_causal_result_missing=true",
                    profile.metadata_value()
                ),
            }
        }
        // Parking only bounds post-release cleanup; it never establishes the
        // causal schedule. Join is allowed only after actual thread completion.
        while !worker.is_finished() && settling_started.elapsed() < settling_budget {
            let remaining = settling_budget.saturating_sub(settling_started.elapsed());
            std::thread::park_timeout(remaining.min(Duration::from_millis(5)));
        }
        let worker_finished = worker.is_finished();
        let joined = if worker_finished {
            Some(worker.join())
        } else {
            eprintln!(
                "nakama_duplicate_occurrence_worker_unsettled profile={} original_result_preserved=true cleanup_budget_seconds=6 outer_process_group_deadline_required=true",
                profile.metadata_value()
            );
            None
        };
        if let Err(payload) = primary {
            if joined.as_ref().is_some_and(Result::is_err) {
                eprintln!(
                    "nakama_duplicate_occurrence_secondary_worker_panic profile={} primary_panic_preserved=true",
                    profile.metadata_value()
                );
            }
            resume_unwind(payload);
        }
        if let Some(Err(payload)) = joined {
            resume_unwind(payload);
        }
        assert!(
            rollback.is_ok(),
            "native occurrence owned blocker rollback failed"
        );
        assert!(
            worker_finished,
            "native occurrence worker did not settle; original result retained and outer process-group deadline required"
        );
        assert_eq!(snapshot(&mut control, &collection), before_lock);
        println!("nakama_duplicate_occurrence_locks_executed profile={} missing_delete_rejected_before_late_lock=true fields=15", profile.metadata_value());

        write_tail_drain::exercise(&url, profile, &collection);
        imported_history::exercise(profile);
        println!(
            "nakama_duplicate_success_full_tuple_executed profile={} fields=15",
            profile.metadata_value()
        );
    }));
    preserve_primary_cleanup(result, cleanup(&mut control, &collection));
    println!(
        "nakama_duplicate_batches_executed profile={}",
        profile.metadata_value()
    );
}

// A separate controlled source-DDL fixture and actual Rust export/import path.
// This three-row packet is not the primary CI eight-row/four-page evidence packet,
// and neither synthetic source rows nor external anchors attest a Nakama DB.
mod imported_history {
    use std::{
        env, fs,
        path::PathBuf,
        time::{SystemTime, UNIX_EPOCH},
    };
    use trnm_persistence_pg::{
        verify_storage_export, StorageExportOptions, StorageImportCustody, StorageImportOptions,
    };

    fn required(name: &str) -> String {
        env::var(name)
            .ok()
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| panic!("duplicate imported fixture required {name} absent"))
    }
    fn unique(kind: &str) -> String {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let name = format!("trnm_dup_{kind}_{:x}_{nanos:x}", std::process::id());
        assert!(
            name.len() < 60
                && name
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
        );
        name
    }
    fn hex(bytes: &[u8]) -> String {
        use std::fmt::Write;
        let mut result = String::new();
        for byte in bytes {
            write!(&mut result, "{byte:02x}").unwrap();
        }
        result
    }
    fn digest(bytes: &[u8]) -> String {
        hex(super::IntegrityDigest::from_value(bytes).get().as_bytes())
    }
    fn target_url(base: &str, database: &str) -> String {
        let (scheme, remainder) = base
            .split_once("://")
            .unwrap_or_else(|| panic!("duplicate imported fixture requires private URI"));
        assert!(matches!(scheme, "postgres" | "postgresql"));
        let end = remainder.find(['/', '?']).unwrap_or(remainder.len());
        let query = remainder[end..]
            .split_once('?')
            .map_or("", |(_, query)| query);
        let separator = if query.is_empty() { "" } else { "&" };
        let private = format!(
            "{scheme}://{}/{database}?{query}{separator}connect_timeout=5",
            &remainder[..end]
        );
        let checked = private
            .parse::<postgres::Config>()
            .unwrap_or_else(|_| panic!("duplicate imported fixture target configuration invalid"));
        assert_eq!(checked.get_dbname(), Some(database));
        private
    }
    struct OwnedDatabase {
        admin: postgres::Client,
        name: String,
        url: String,
        profile: super::DatabaseProfile,
        role: Option<String>,
    }
    impl OwnedDatabase {
        fn new(admin_url: &str, profile: super::DatabaseProfile, kind: &str) -> Self {
            let mut admin = super::bounded_control(admin_url);
            let name = unique(kind);
            admin
                .batch_execute(&format!("CREATE DATABASE {name}"))
                .unwrap_or_else(|_| {
                    panic!("duplicate imported fixture create owned database failed")
                });
            Self {
                url: target_url(admin_url, &name),
                admin,
                name,
                profile,
                role: None,
            }
        }
        fn control(&self) -> postgres::Client {
            super::bounded_control(&self.url)
        }
        fn repository(&self) -> super::PgRepository {
            super::bounded_repository(&self.url, self.profile)
        }
    }
    impl Drop for OwnedDatabase {
        fn drop(&mut self) {
            if std::thread::panicking() {
                eprintln!("nakama_duplicate_import_owned_resources_retained primary_panic_preserved=true database={}", self.name);
                return;
            }
            let cascade = if self.profile == super::DatabaseProfile::CockroachDb {
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
            assert!(!failed, "duplicate imported fixture owned cleanup failed");
        }
    }
    struct OwnedPacketDirectory {
        path: PathBuf,
    }
    impl Drop for OwnedPacketDirectory {
        fn drop(&mut self) {
            if std::thread::panicking() {
                eprintln!(
                    "nakama_duplicate_import_owned_packet_retained primary_panic_preserved=true"
                );
            } else {
                assert!(
                    fs::remove_dir_all(&self.path).is_ok(),
                    "duplicate imported packet cleanup failed"
                );
            }
        }
    }
    pub(super) fn exercise(profile: super::DatabaseProfile) {
        let admin_url = required("TRNM_SCHEMA_UPGRADE_ADMIN_DATABASE_URL");
        let upstream = PathBuf::from(required("TRNM_STORAGE_PINNED_UPSTREAM_DIRECTORY"));
        assert!(
            upstream.is_absolute(),
            "duplicate imported fixture pinned annex path must be absolute"
        );
        let commit = required("TRNM_STORAGE_TEST_PRODUCER_COMMIT");
        let tree = required("TRNM_STORAGE_TEST_PRODUCER_TREE");
        for identity in [&commit, &tree] {
            assert!(
                identity.len() == 40
                    && identity
                        .bytes()
                        .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
                    && identity.bytes().any(|byte| byte != b'0')
            );
        }
        // All source annex bytes are checked before any native fixture mutation.
        let mut ddl = None;
        for (name, size, expected) in [
            (
                "initial-schema.sql",
                9530,
                "aa128be66236fca4255db9c674bb2ca630cee3eaf3c8927bdeb23f528bc10d01",
            ),
            (
                "core-storage.go",
                32454,
                "e9632afa6b83e5692bcc149e59c23d35c3a6acfe68a9913ec78c5c27ccc50502",
            ),
            (
                "LICENSE",
                11358,
                "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
            ),
        ] {
            let bytes = fs::read(upstream.join(name))
                .unwrap_or_else(|_| panic!("duplicate imported fixture pinned annex unavailable"));
            assert_eq!(bytes.len(), size);
            assert_eq!(digest(&bytes), expected);
            if name == "initial-schema.sql" {
                ddl = Some(String::from_utf8(bytes).unwrap());
            }
        }
        let source_file =
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/storage_import_parts/exporter.rs");
        let source_sha = digest(&fs::read(&source_file).unwrap());
        let binary_sha = digest(&fs::read("/proc/self/exe").unwrap());
        let execution_id = unique("export");
        let source = OwnedDatabase::new(&admin_url, profile, "source");
        let mut native_source = source.control();
        native_source
            .batch_execute("CREATE TABLE public.users(id UUID PRIMARY KEY)")
            .unwrap();
        let ddl = ddl.unwrap();
        let start = ddl.find("CREATE TABLE IF NOT EXISTS storage (").unwrap();
        let remaining = &ddl[start..];
        let mut end = 0;
        for _ in 0..3 {
            end += remaining[end..].find(';').unwrap() + 1;
        }
        native_source.batch_execute(&remaining[..end]).unwrap();
        let owner_uuid = "93939393-9393-9393-9393-939393939393";
        native_source
            .execute(
                "INSERT INTO public.users(id) VALUES($1::TEXT::UUID)",
                &[&owner_uuid],
            )
            .unwrap();
        let collection = unique("history");
        let create = super::StorageTimestamp::new(-1, 123_456_000).unwrap();
        let update = super::StorageTimestamp::new(951_827_696, 654_321_000).unwrap();
        let incoming_version = super::ContentVersion::from_value(super::A);
        // Intentionally synthetic public tokens. The historical payload differs
        // from A, and the source has no raw request witness to reconstruct.
        for (key, value, version) in [
            ("empty", " {\"history\":true} ", ""),
            (
                "same-token",
                "[\"historical\",null]",
                incoming_version.as_str(),
            ),
            ("opaque", "[true,{\"historical\":2}]", "Opaque-界"),
        ] {
            native_source.execute("INSERT INTO public.storage(collection,key,user_id,value,version,read,write,create_time,update_time) VALUES($1,$2,$3::TEXT::UUID,$4::TEXT::JSONB,$5,1::SMALLINT,1::SMALLINT,$6,$7)", &[&collection,&key,&owner_uuid,&value,&version,&create,&update]).unwrap();
        }
        let source_rows = native_source.query("SELECT collection,key,user_id::TEXT,value::TEXT,version,read,write,create_time,update_time FROM public.storage ORDER BY collection,key,user_id", &[]).unwrap();
        assert_eq!(source_rows.len(), 3);
        let files = OwnedPacketDirectory {
            path: env::temp_dir().join(unique("packet")),
        };
        fs::create_dir(&files.path).unwrap();
        let output = files.path.join("source-export");
        let options = StorageExportOptions {
            output_directory: output.clone(),
            page_rows: 1,
            execution_class: "native-source-ddl-fixture".to_owned(),
            producer_commit: commit.clone(),
            producer_tree: tree.clone(),
            producer_source_file: source_file,
            producer_source_sha256: source_sha.clone(),
            producer_binary_sha256: binary_sha.clone(),
            execution_id: execution_id.clone(),
            upstream_directory: upstream,
        };
        let summary = source
            .repository()
            .export_storage_snapshot(&options)
            .unwrap();
        assert_eq!((summary.row_count, summary.page_count), (3, 3));
        assert!(
            !summary.compatibility_credit
                && !summary.production_ready
                && !summary.full_nakama_replacement
        );
        // Independent readback, not summary or self-described manifest fields.
        let manifest_bytes = fs::read(output.join("manifest.json")).unwrap();
        let receipt_bytes = fs::read(output.join("source-receipt.json")).unwrap();
        let custody = StorageImportCustody::new(
            &digest(&manifest_bytes),
            &digest(&receipt_bytes),
            commit.clone(),
            tree,
            &source_sha,
            &binary_sha,
            execution_id,
        )
        .unwrap();
        let packet = verify_storage_export(&output, &custody).unwrap();
        assert_eq!((packet.summary().rows, packet.summary().pages), (3, 3));
        let manifest_raw = super::IntegrityDigest::from_value(&manifest_bytes)
            .get()
            .as_bytes()
            .to_vec();
        let mut target = OwnedDatabase::new(&admin_url, profile, "target");
        let mut repository = target.repository();
        let report = repository
            .migrate_authoritative_schema(&commit, 2468, None)
            .unwrap();
        assert_eq!(
            (
                report.identity.schema_version,
                report.identity.storage_writer_epoch,
                report.table_count,
                report.applied_steps
            ),
            (4, 4, 12, 4)
        );
        assert_eq!(report.identity.source_commit, commit);
        assert_eq!(report.identity.v2_apply_source_commit, commit);
        assert_eq!(report.identity.v3_apply_source_commit, commit);
        assert_eq!(report.identity.upgrade_source_commit, commit);
        let role = unique("oldrole");
        target
            .admin
            .batch_execute(&format!("CREATE ROLE {role}"))
            .unwrap();
        target.role = Some(role.clone());
        let import_options = StorageImportOptions {
            audit_at_ms: 2468,
            legacy_writer_role: role,
            expected_target_scope: super::IntegrityDigest::from_value(target.name.as_bytes()),
        };
        let checked = repository
            .preflight_storage_import(&packet, import_options)
            .unwrap();
        let phase_started = std::time::Instant::now();
        let mut progress = repository.begin_storage_import(&checked).unwrap();
        assert_eq!(
            (
                progress.next_page,
                progress.committed_rows,
                progress.total_pages
            ),
            (0, 0, 3)
        );
        for expected_next in 1..=3 {
            assert!(
                phase_started.elapsed() < std::time::Duration::from_secs(30),
                "duplicate imported page phase deadline exceeded"
            );
            progress = repository
                .apply_next_storage_import_page(&checked)
                .unwrap()
                .progress;
            assert_eq!(
                (
                    progress.next_page,
                    progress.committed_rows,
                    progress.total_pages
                ),
                (expected_next, expected_next, 3),
                "duplicate import page did not advance once"
            );
        }
        let finished = repository.finalize_storage_import(&checked).unwrap();
        assert_eq!(
            repository.verify_applied_storage_import(&checked).unwrap(),
            finished
        );
        assert!(finished.completed && finished.committed_rows == 3);
        let mut control = target.control();
        let before = super::snapshot(&mut control, &collection);
        let expected: Vec<_> = source_rows
            .iter()
            .map(|row| {
                assert_eq!(row.get::<_, String>(2), owner_uuid);
                let native: String = row.get(3);
                super::RawRow {
                    collection: row.get(0),
                    key: row.get(1),
                    owner: super::OWNER.as_bytes().to_vec(),
                    projection_sha: super::IntegrityDigest::from_value(native.as_bytes())
                        .get()
                        .as_bytes()
                        .to_vec(),
                    native,
                    raw: None,
                    request_sha: None,
                    public_version: row.get(4),
                    origin: "nakama-export-unknown-request".to_owned(),
                    manifest_sha: Some(manifest_raw.clone()),
                    read: row.get(5),
                    write: row.get(6),
                    updated_at_ms: 2468,
                    create: Some(row.get(7)),
                    update: Some(row.get(8)),
                }
            })
            .collect();
        assert_eq!(
            before, expected,
            "actual source-bound import full15field tuple differs"
        );
        let empty = super::StorageObjectKey::new(&collection, "empty", super::OWNER).unwrap();
        let matched =
            super::StorageObjectKey::new(&collection, "same-token", super::OWNER).unwrap();
        let opaque = super::StorageObjectKey::new(&collection, "opaque", super::OWNER).unwrap();
        assert!(repository
            .read_storage_object(super::StorageActor::Server, &empty)
            .unwrap()
            .collision_witness
            .is_none());
        assert!(repository
            .read_storage_object(super::StorageActor::Server, &matched)
            .unwrap()
            .collision_witness
            .is_none());
        let noops = repository
            .apply_storage_batch_nakama_with_metadata(
                super::StorageActor::User(super::OWNER),
                &[
                    super::write(
                        &matched,
                        super::A,
                        super::VersionCheck::Any,
                        super::WritePermission::OWNER,
                    ),
                    super::write(
                        &matched,
                        super::A,
                        super::VersionCheck::Any,
                        super::WritePermission::OWNER,
                    ),
                ],
                9999,
                super::StorageNakamaBatchKind::Write,
            )
            .unwrap();
        assert_eq!(noops.len(), 2, "unknown Any no-op ACK cardinality changed");
        for ack in noops {
            assert_eq!(ack.receipt.key, matched);
            assert_eq!(
                ack.receipt
                    .previous_version
                    .as_ref()
                    .map(|value| value.as_str()),
                Some(incoming_version.as_str())
            );
            assert_eq!(ack.receipt.current_version, Some(incoming_version));
            assert_eq!(
                ack.times,
                super::StorageTimes {
                    create: Some(create),
                    update: Some(update)
                }
            );
        }
        assert_eq!(
            super::snapshot(&mut control, &collection),
            before,
            "unknown same-token Any no-ops must preserve all15fields and NULL witness"
        );
        let receipts = repository
            .apply_storage_batch_nakama_with_metadata(
                super::StorageActor::User(super::OWNER),
                &[
                    super::write(
                        &empty,
                        super::A,
                        super::VersionCheck::Exact("".into()),
                        super::WritePermission::OWNER,
                    ),
                    super::write(
                        &empty,
                        super::B,
                        super::VersionCheck::Exact(
                            super::ContentVersion::from_value(super::A).into(),
                        ),
                        super::WritePermission::OWNER,
                    ),
                ],
                2470,
                super::StorageNakamaBatchKind::Write,
            )
            .unwrap();
        assert_eq!(
            receipts[0]
                .receipt
                .previous_version
                .as_ref()
                .map(|value| value.as_str()),
            Some("")
        );
        assert_eq!(
            receipts[1]
                .receipt
                .previous_version
                .as_ref()
                .map(|value| value.as_str()),
            Some(super::ContentVersion::from_value(super::A).as_str())
        );
        assert_eq!(receipts[0].times.create, Some(create));
        assert_eq!(receipts[1].times.create, Some(create));
        super::assert_known_row(
            &mut control,
            &empty,
            super::B,
            super::ReadPermission::OWNER,
            super::WritePermission::OWNER,
            2470,
            receipts[1].times,
        );
        let before_replace = super::snapshot(&mut control, &collection);
        let receipts = repository
            .apply_storage_batch_nakama_with_metadata(
                super::StorageActor::User(super::OWNER),
                &[
                    super::write(
                        &matched,
                        super::A,
                        super::VersionCheck::Any,
                        super::WritePermission::OWNER,
                    ),
                    super::write(
                        &matched,
                        super::B,
                        super::VersionCheck::Exact(incoming_version.into()),
                        super::WritePermission::OWNER,
                    ),
                ],
                2471,
                super::StorageNakamaBatchKind::Write,
            )
            .unwrap();
        assert_eq!(
            receipts[0].times,
            super::StorageTimes {
                create: Some(create),
                update: Some(update)
            }
        );
        assert_eq!(receipts[1].times.create, Some(create));
        assert_ne!(receipts[1].times.update, Some(update));
        super::assert_known_row(
            &mut control,
            &matched,
            super::B,
            super::ReadPermission::OWNER,
            super::WritePermission::OWNER,
            2471,
            receipts[1].times,
        );
        let after_replace = super::snapshot(&mut control, &collection);
        assert_eq!(
            before_replace.iter().find(|row| row.key == "empty"),
            after_replace.iter().find(|row| row.key == "empty")
        );
        let before_opaque = super::snapshot(&mut control, &collection);
        let rejection = repository
            .apply_storage_batch_nakama_with_metadata(
                super::StorageActor::User(super::OWNER),
                &[
                    super::write(
                        &opaque,
                        super::A,
                        super::VersionCheck::Exact("Opaque-界".into()),
                        super::WritePermission::OWNER,
                    ),
                    super::write(
                        &opaque,
                        super::B,
                        super::VersionCheck::Exact("Opaque-界".into()),
                        super::WritePermission::OWNER,
                    ),
                ],
                2472,
                super::StorageNakamaBatchKind::Write,
            )
            .unwrap_err();
        assert_eq!(rejection.code(), super::StableCode::FailedPrecondition);
        assert_eq!(rejection.reason(), "storage_version_mismatch");
        assert_eq!(rejection.retry(), super::RetryClass::ResyncRequired);
        assert_eq!(
            super::snapshot(&mut control, &collection),
            before_opaque,
            "opaque literal stale second occurrence must roll back complete15fields"
        );
        let receipts = repository
            .apply_storage_batch_nakama_with_metadata(
                super::StorageActor::User(super::OWNER),
                &[
                    super::write(
                        &opaque,
                        super::A,
                        super::VersionCheck::Exact("Opaque-界".into()),
                        super::WritePermission::OWNER,
                    ),
                    super::write(
                        &opaque,
                        super::B,
                        super::VersionCheck::Exact(
                            super::ContentVersion::from_value(super::A).into(),
                        ),
                        super::WritePermission::OWNER,
                    ),
                ],
                2473,
                super::StorageNakamaBatchKind::Write,
            )
            .unwrap();
        assert_eq!(
            receipts[0]
                .receipt
                .previous_version
                .as_ref()
                .map(|version| version.as_str()),
            Some("Opaque-界")
        );
        assert_eq!(receipts[1].times.create, Some(create));
        super::assert_known_row(
            &mut control,
            &opaque,
            super::B,
            super::ReadPermission::OWNER,
            super::WritePermission::OWNER,
            2473,
            receipts[1].times,
        );
        let after_opaque = super::snapshot(&mut control, &collection);
        assert_eq!(
            before_opaque
                .iter()
                .filter(|row| row.key != "opaque")
                .cloned()
                .collect::<Vec<_>>(),
            after_opaque
                .iter()
                .filter(|row| row.key != "opaque")
                .cloned()
                .collect::<Vec<_>>()
        );

        // Journal completion was verified before ordinary mutation. Its original
        // imported inventory intentionally no longer equals these later writes.
        println!("nakama_duplicate_imported_history_executed profile={} source_rows=3 pages=3 witness_null=true full_tuple_fields=15 source_execution_class=native-source-ddl-fixture", profile.metadata_value());
        drop(repository);
        drop(control);
        drop(native_source);
        // Success cleanup owns only the newly generated source/target/role.
    }
}

// Source candidate only until the complete native fixture executes. The lock
// observations below are causal evidence; polling duration and query phase are
// never substituted for a lock observation. No production telemetry is added.
mod write_tail_drain {
    use super::*;
    use postgres::Transaction;
    use std::{
        sync::mpsc::{self, Receiver, TryRecvError},
        thread::JoinHandle,
        time::Instant,
    };
    use trnm_contracts::DomainError;

    const CAUSAL_BUDGET: Duration = Duration::from_secs(4);
    const SETTLING_BUDGET: Duration = Duration::from_secs(6);
    const BAD_JSON: &[u8] = b"{invalid-json";
    const ACCESS_SQL: &str = "SELECT public_version::TEXT, write_permission FROM public.trnm_storage_objects WHERE collection = $1 AND object_key = $2 AND user_id = $3 FOR UPDATE";

    #[derive(Clone, Copy)]
    enum Case {
        AclWait,
        ExactWait,
        CreateOnlyStops,
        AclNativeStops,
        NativeStops,
    }
    impl Case {
        const fn name(self) -> &'static str {
            match self {
                Self::AclWait => "acl_wait",
                Self::ExactWait => "exact_wait",
                Self::CreateOnlyStops => "create_only_stops",
                Self::AclNativeStops => "acl_native_stops",
                Self::NativeStops => "native_stops",
            }
        }
        const fn error(self) -> DomainError {
            match self {
                Self::AclWait | Self::AclNativeStops => DomainError::new(
                    StableCode::PermissionDenied,
                    "storage_write_permission_denied",
                    RetryClass::Never,
                ),
                Self::ExactWait => DomainError::new(
                    StableCode::FailedPrecondition,
                    "storage_version_mismatch",
                    RetryClass::ResyncRequired,
                ),
                Self::CreateOnlyStops => DomainError::new(
                    StableCode::AlreadyExists,
                    "storage_object_already_exists",
                    RetryClass::Never,
                ),
                Self::NativeStops => DomainError::new(
                    StableCode::InvalidArgument,
                    "database_constraint_violation",
                    RetryClass::Never,
                ),
            }
        }
    }

    struct Outcome {
        batch: Result<Vec<StoredStorageMutationReceipt>, DomainError>,
        reused: Result<bool, DomainError>,
        completed_at: Instant,
    }
    #[derive(Clone, Debug, Eq, PartialEq)]
    enum Holder {
        PostgreSql { pid: i32, xid: String },
        CockroachDb { txn: String, keys: Vec<String> },
    }
    struct WaitProof {
        worker: String,
        blocker: String,
        query_sha256: String,
    }
    fn query_digest(query: &str) -> String {
        assert!(
            query.len() <= 8192,
            "native observer query exceeded fixture bound"
        );
        const HEX: &[u8; 16] = b"0123456789abcdef";
        let digest = IntegrityDigest::from_value(query.as_bytes()).get();
        let mut encoded = String::with_capacity(64);
        for byte in digest.as_bytes() {
            encoded.push(char::from(HEX[usize::from(byte >> 4)]));
            encoded.push(char::from(HEX[usize::from(byte & 15)]));
        }
        encoded
    }
    fn sqlstate(error: &postgres::Error) -> &str {
        error.code().map_or("unavailable", |code| code.code())
    }
    fn application(case: Case, role: &str, stamp: u128) -> String {
        let name = format!("tail_{}_{}_{stamp:x}", case.name(), role);
        assert!(name.len() < 64);
        assert!(name.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_'));
        name
    }
    fn control_application(control: &mut Client, application: &str) {
        control
            .batch_execute(&format!("SET application_name='{application}'"))
            .unwrap_or_else(|error| {
                panic!(
                    "tail control application failed sqlstate={}",
                    sqlstate(&error)
                )
            });
    }
    fn hold(transaction: &mut Transaction<'_>, key: &StorageObjectKey) {
        let row = transaction
            .query_one(
                "SELECT object_key FROM public.trnm_storage_objects WHERE collection=$1 AND object_key=$2 AND user_id=$3 FOR UPDATE",
                &[&key.collection(), &key.key(), &key.user_id().as_bytes().as_slice()],
            )
            .unwrap_or_else(|error| panic!("tail owned native row lock failed sqlstate={}", sqlstate(&error)));
        assert_eq!(row.get::<_, String>(0), key.key());
    }
    fn cr_holder(observer: &mut Client, key: &StorageObjectKey) -> Holder {
        // Capture the native pretty keys rather than inventing an encoding.
        // The generated collection/key identify only this fixture's exclusive
        // held row. If the native view cannot expose it, the fixture fails.
        let rows = observer
            .query(
                "SELECT txn_id::TEXT, lock_key_pretty::TEXT FROM crdb_internal.cluster_locks WHERE database_name=current_database() AND table_name='trnm_storage_objects' AND granted AND strpos(lock_key_pretty, $1::TEXT)>0 AND strpos(lock_key_pretty, $2::TEXT)>0 ORDER BY txn_id::TEXT, lock_key_pretty LIMIT 64",
                &[&key.collection(), &key.key()],
            )
            .unwrap_or_else(|error| panic!("tail CR holder observation failed sqlstate={}", sqlstate(&error)));
        assert!(
            !rows.is_empty() && rows.len() < 64,
            "CR native held key was absent or ambiguous"
        );
        let txn: String = rows[0].get(0);
        assert!(!txn.is_empty() && txn.len() <= 128);
        let mut keys = Vec::new();
        for row in rows {
            assert_eq!(
                row.get::<_, String>(0),
                txn,
                "owned CR key had multiple holders"
            );
            let pretty: String = row.get(1);
            assert!(pretty.len() <= 8192);
            keys.push(pretty);
        }
        keys.sort();
        keys.dedup();
        Holder::CockroachDb { txn, keys }
    }
    fn capture_holder(
        transaction: &mut Transaction<'_>,
        observer: &mut Client,
        profile: DatabaseProfile,
        key: &StorageObjectKey,
    ) -> Holder {
        match profile {
            DatabaseProfile::PostgreSql => {
                let row = transaction
                    .query_one("SELECT pg_backend_pid(), pg_current_xact_id()::TEXT", &[])
                    .unwrap_or_else(|error| {
                        panic!(
                            "tail PG holder identity failed sqlstate={}",
                            sqlstate(&error)
                        )
                    });
                Holder::PostgreSql {
                    pid: row.get(0),
                    xid: row.get(1),
                }
            }
            DatabaseProfile::CockroachDb => cr_holder(observer, key),
        }
    }
    fn holder_still_open(observer: &mut Client, key: &StorageObjectKey, holder: &Holder) {
        match holder {
            Holder::PostgreSql { pid, xid } => {
                let row = observer
                    .query_one(
                        "SELECT backend_xid::TEXT, state FROM pg_stat_activity WHERE datname=current_database() AND pid=$1::INTEGER",
                        &[pid],
                    )
                    .unwrap_or_else(|error| panic!("tail PG held identity readback failed sqlstate={}", sqlstate(&error)));
                assert_eq!(row.get::<_, Option<String>>(0).as_ref(), Some(xid));
                assert_eq!(row.get::<_, String>(1), "idle in transaction");
            }
            Holder::CockroachDb { .. } => assert_eq!(cr_holder(observer, key), *holder),
        }
    }
    fn observe_wait(
        observer: &mut Client,
        application: &str,
        holder: &Holder,
    ) -> Option<WaitProof> {
        match holder {
            Holder::PostgreSql { pid, .. } => {
                let rows = observer
                    .query(
                        "SELECT pid::INTEGER, query, wait_event_type, pg_blocking_pids(pid) FROM pg_stat_activity WHERE datname=current_database() AND application_name=$1",
                        &[&application],
                    )
                    .unwrap_or_else(|error| panic!("tail PG wait observer failed sqlstate={}", sqlstate(&error)));
                assert!(
                    rows.len() <= 1,
                    "worker native application identity was ambiguous"
                );
                let row = rows.first()?;
                let query: String = row.get(1);
                let blocked_by: Vec<i32> = row.get(3);
                if row.get::<_, Option<String>>(2).as_deref() != Some("Lock")
                    || !blocked_by.contains(pid)
                    || query != ACCESS_SQL
                {
                    return None;
                }
                Some(WaitProof {
                    worker: row.get::<_, i32>(0).to_string(),
                    blocker: pid.to_string(),
                    query_sha256: query_digest(&query),
                })
            }
            Holder::CockroachDb { txn, keys } => {
                // Neither query.phase nor elapsed time establishes a lock wait.
                // Join the actual worker transaction to an ungranted, contended
                // lock whose exact native pretty key has this distinct holder.
                let rows = observer
                    .query(
                        "SELECT q.query_id::TEXT, q.txn_id::TEXT, q.query::TEXT, q.phase::TEXT, l.lock_key_pretty::TEXT, l.granted, l.contended FROM crdb_internal.cluster_queries q JOIN crdb_internal.cluster_locks l ON q.txn_id::TEXT=l.txn_id::TEXT WHERE q.application_name=$1 AND l.database_name=current_database() AND l.table_name='trnm_storage_objects' AND l.lock_key_pretty=ANY($2::TEXT[]) AND NOT l.granted AND l.contended AND l.txn_id::TEXT<>$3 AND EXISTS(SELECT 1 FROM crdb_internal.cluster_locks h WHERE h.database_name=l.database_name AND h.table_name=l.table_name AND h.lock_key_pretty=l.lock_key_pretty AND h.txn_id::TEXT=$3 AND h.granted) ORDER BY q.query_id::TEXT, l.lock_key_pretty LIMIT 64",
                        &[&application, keys, txn],
                    )
                    .unwrap_or_else(|error| panic!("tail CR wait observer failed sqlstate={}", sqlstate(&error)));
                if rows.is_empty() {
                    return None;
                }
                assert!(
                    rows.len() < 64,
                    "CR native wait observation exceeded fixture bound"
                );
                let worker_txn: String = rows[0].get(1);
                assert!(!worker_txn.is_empty() && worker_txn.len() <= 128);
                assert_ne!(worker_txn, *txn);
                for row in &rows {
                    assert_eq!(row.get::<_, String>(1), worker_txn);
                    assert!(keys.contains(&row.get::<_, String>(4)));
                    assert!(!row.get::<_, bool>(5));
                    assert!(row.get::<_, bool>(6));
                }
                let query: String = rows[0].get(2);
                assert!(query.contains("trnm_storage_objects") && query.contains("FOR UPDATE"));
                // phase is read from the real view but does not grant wait credit.
                let phase: String = rows[0].get(3);
                assert!(phase.len() <= 128);
                let query_id: String = rows[0].get(0);
                assert!(!query_id.is_empty() && query_id.len() <= 128);
                Some(WaitProof {
                    worker: worker_txn,
                    blocker: txn.clone(),
                    query_sha256: query_digest(&query),
                })
            }
        }
    }
    fn log_outcome(profile: DatabaseProfile, case: Case, outcome: &Outcome, late: bool) {
        match &outcome.batch {
            Err(error) => println!(
                "nakama_write_tail_drain_actual_result profile={} case={} actual_batch_domain_code={:?} actual_batch_reason={} actual_batch_retry={:?} actual_batch_sqlstate=null late={late}",
                profile.metadata_value(), case.name(), error.code(), error.reason(), error.retry()
            ),
            Ok(receipts) => eprintln!(
                "nakama_write_tail_drain_unexpected_receipts profile={} case={} receipt_count={} late={late}",
                profile.metadata_value(), case.name(), receipts.len()
            ),
        }
    }
    fn wait_for_lock(
        observer: &mut Client,
        application: &str,
        holder: &Holder,
        receive: &Receiver<Outcome>,
        outcome: &mut Option<Outcome>,
        started: Instant,
    ) -> WaitProof {
        loop {
            assert!(
                started.elapsed() < CAUSAL_BUDGET,
                "actual held-row wait was not observed within causal budget"
            );
            if let Some(proof) = observe_wait(observer, application, holder) {
                assert!(
                    started.elapsed() < CAUSAL_BUDGET,
                    "native wait observation completed after causal budget"
                );
                return proof;
            }
            match receive.try_recv() {
                Ok(actual) => {
                    *outcome = Some(actual);
                    panic!("worker completed before native held-row wait observation");
                }
                Err(TryRecvError::Disconnected) => {
                    panic!("tail worker disconnected before native wait observation")
                }
                Err(TryRecvError::Empty) => std::thread::yield_now(),
            }
        }
    }
    fn release(
        transaction: &mut Option<Transaction<'_>>,
        profile: DatabaseProfile,
        case: Case,
        role: &str,
    ) -> bool {
        let Some(transaction) = transaction.take() else {
            return true;
        };
        match transaction.rollback() {
            Ok(()) => true,
            Err(error) => {
                eprintln!(
                    "nakama_write_tail_drain_secondary_rollback_failed profile={} case={} role={role} sqlstate={} primary_trace_preserved=true",
                    profile.metadata_value(), case.name(), sqlstate(&error)
                );
                false
            }
        }
    }
    fn settle(
        worker: JoinHandle<()>,
        receive: &Receiver<Outcome>,
        outcome: &mut Option<Outcome>,
        started: Instant,
        profile: DatabaseProfile,
        case: Case,
    ) -> Option<std::thread::Result<()>> {
        if outcome.is_none() {
            match receive.recv_timeout(SETTLING_BUDGET.saturating_sub(started.elapsed())) {
                Ok(actual) => {
                    log_outcome(profile, case, &actual, true);
                    *outcome = Some(actual);
                }
                Err(_) => eprintln!(
                    "nakama_write_tail_drain_late_result_unavailable profile={} case={} original_result_preserved=true",
                    profile.metadata_value(), case.name()
                ),
            }
        }
        // Only post-release settling parks; no sleep grants causal evidence.
        while !worker.is_finished() && started.elapsed() < SETTLING_BUDGET {
            std::thread::park_timeout(
                SETTLING_BUDGET
                    .saturating_sub(started.elapsed())
                    .min(Duration::from_millis(5)),
            );
        }
        if worker.is_finished() {
            Some(worker.join())
        } else {
            eprintln!(
                "nakama_write_tail_drain_worker_unsettled profile={} case={} cleanup_budget_seconds=6 outer_process_group_deadline_required=true original_result_preserved=true",
                profile.metadata_value(), case.name()
            );
            None
        }
    }

    fn exercise_case(url: &str, profile: DatabaseProfile, collection: &str, case: Case) {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let key = |suffix: &str| {
            StorageObjectKey::new(collection, format!("tail-{}-{suffix}", case.name()), OWNER)
                .unwrap()
        };
        let prelude = key("0-prelude");
        let rejected = key("a-rejected");
        let bad = key("b-invalid");
        let probe = key("y-readable");
        let later = key("z-held");
        let mut control = bounded_control(url);
        let mut repository = bounded_repository(url, profile);
        let rejected_acl = if matches!(case, Case::AclWait | Case::AclNativeStops) {
            WritePermission::NONE
        } else {
            WritePermission::OWNER
        };
        let seeds = [
            write(&prelude, A, VersionCheck::Any, WritePermission::OWNER),
            write(&rejected, A, VersionCheck::Any, rejected_acl),
            write(&bad, A, VersionCheck::Any, WritePermission::OWNER),
            write(&probe, A, VersionCheck::Any, WritePermission::OWNER),
            write(&later, A, VersionCheck::Any, WritePermission::OWNER),
        ];
        let seeds_receipts = repository
            .apply_storage_batch_nakama_with_metadata(
                StorageActor::Server,
                &seeds,
                201,
                StorageNakamaBatchKind::Write,
            )
            .unwrap();
        assert_eq!(seeds_receipts.len(), seeds.len());
        for (index, seeded_key) in [&prelude, &rejected, &bad, &probe, &later]
            .into_iter()
            .enumerate()
        {
            assert_known_row(
                &mut control,
                seeded_key,
                A,
                ReadPermission::OWNER,
                if index == 1 {
                    rejected_acl
                } else {
                    WritePermission::OWNER
                },
                201,
                seeds_receipts[index].times,
            );
        }
        if matches!(case, Case::AclNativeStops | Case::NativeStops) {
            let independent = control
                .query_one(
                    "SELECT $1::TEXT::JSONB::TEXT",
                    &[&std::str::from_utf8(BAD_JSON).unwrap()],
                )
                .unwrap_err();
            assert_eq!(independent.code().map(|code| code.code()), Some("22P02"));
            println!(
                "nakama_write_tail_drain_native_probe profile={} case={} independent_probe_sqlstate=22P02 hidden_tail_sqlstate=null",
                profile.metadata_value(), case.name()
            );
        }
        let before = snapshot(&mut control, collection);
        let mut worker_repository = bounded_repository(url, profile);
        let probe_before = worker_repository
            .read_storage_object_with_metadata(StorageActor::User(OWNER), &probe)
            .unwrap();
        let worker_application = application(case, "worker", stamp);
        worker_repository
            .execute_migration_batch(&format!("SET application_name='{worker_application}'"))
            .unwrap();
        let rejection = match case {
            Case::ExactWait => write(
                &rejected,
                B,
                VersionCheck::Exact("opaque-literal-that-does-not-match".into()),
                WritePermission::OWNER,
            ),
            Case::CreateOnlyStops => write(
                &rejected,
                B,
                VersionCheck::MustNotExist,
                WritePermission::OWNER,
            ),
            Case::NativeStops => write(
                &rejected,
                BAD_JSON,
                VersionCheck::Any,
                WritePermission::OWNER,
            ),
            Case::AclWait | Case::AclNativeStops => {
                write(&rejected, B, VersionCheck::Any, WritePermission::OWNER)
            }
        };
        // Deliberately present the late key first. The canonical Go occurrence
        // plan must still execute the earlier transient write before rejection.
        let mut operations = vec![
            write(&later, B, VersionCheck::Any, WritePermission::OWNER),
            rejection,
            write(&prelude, C, VersionCheck::Any, WritePermission::OWNER),
        ];
        if matches!(case, Case::AclNativeStops) {
            operations.push(write(
                &bad,
                BAD_JSON,
                VersionCheck::Any,
                WritePermission::OWNER,
            ));
        }
        let mut z_control = bounded_control(url);
        control_application(&mut z_control, &application(case, "z", stamp));
        let mut b_control = bounded_control(url);
        control_application(&mut b_control, &application(case, "b", stamp));
        // All connection setup and seeds precede the causal interval. The
        // interval includes acquiring/capturing both owned held locks, native
        // wait observation, release (if required), actual result and readback.
        let started = Instant::now();
        let mut z = Some(z_control.transaction().unwrap());
        hold(z.as_mut().unwrap(), &later);
        let z_holder = capture_holder(z.as_mut().unwrap(), &mut control, profile, &later);
        let mut b = if matches!(case, Case::AclNativeStops) {
            Some(b_control.transaction().unwrap())
        } else {
            None
        };
        let b_holder = b.as_mut().map(|transaction| {
            hold(transaction, &bad);
            capture_holder(transaction, &mut control, profile, &bad)
        });
        if let Some(holder) = &b_holder {
            match (holder, &z_holder) {
                (
                    Holder::PostgreSql { pid: b, xid: bx },
                    Holder::PostgreSql { pid: z, xid: zx },
                ) => {
                    assert_ne!(b, z);
                    assert_ne!(bx, zx);
                }
                (Holder::CockroachDb { txn: b, .. }, Holder::CockroachDb { txn: z, .. }) => {
                    assert_ne!(b, z)
                }
                _ => panic!("tail blocker profile identities disagreed"),
            }
        }
        assert!(
            started.elapsed() < CAUSAL_BUDGET,
            "tail owned held-lock capture exceeded original causal budget"
        );
        let (send, receive) = mpsc::sync_channel(1);
        let worker = std::thread::spawn(move || {
            let batch = worker_repository.apply_storage_batch_nakama_with_metadata(
                StorageActor::User(OWNER),
                &operations,
                202,
                StorageNakamaBatchKind::Write,
            );
            let completed_at = Instant::now();
            // Read a separate unlocked owned row through the same live lease.
            // Do not reconnect or implicitly retry an aborted transaction.
            let reused = worker_repository
                .read_storage_object_with_metadata(StorageActor::User(OWNER), &probe)
                .map(|actual| actual == probe_before);
            send.send(Outcome {
                batch,
                reused,
                completed_at,
            })
            .expect("tail actual result receiver unavailable");
        });
        let mut outcome = None;
        let mut rollback_ok = true;
        let primary = catch_unwind(AssertUnwindSafe(|| {
            let waited_holder = match case {
                Case::AclWait | Case::ExactWait => Some(&z_holder),
                Case::AclNativeStops => b_holder.as_ref(),
                Case::CreateOnlyStops | Case::NativeStops => None,
            };
            if let Some(holder) = waited_holder {
                let proof = wait_for_lock(
                    &mut control,
                    &worker_application,
                    holder,
                    &receive,
                    &mut outcome,
                    started,
                );
                println!(
                    "nakama_write_tail_drain_lock_observed profile={} case={} worker={} blocker={} query_sha256={} proof=native-lock-view",
                    profile.metadata_value(), case.name(), proof.worker, proof.blocker, proof.query_sha256
                );
                if matches!(case, Case::AclNativeStops) {
                    rollback_ok &= release(&mut b, profile, case, "b");
                } else {
                    rollback_ok &= release(&mut z, profile, case, "z");
                }
                assert!(rollback_ok, "tail causal lock release failed");
            }
            let actual = receive
                .recv_timeout(CAUSAL_BUDGET.saturating_sub(started.elapsed()))
                .expect("tail actual result was unavailable within original causal budget");
            log_outcome(profile, case, &actual, false);
            outcome = Some(actual);
            let actual = outcome.as_ref().unwrap();
            assert!(actual.completed_at.duration_since(started) < CAUSAL_BUDGET);
            assert_eq!(actual.batch.as_ref().err().copied(), Some(case.error()), "tail batch did not preserve the expected actual DomainError; successful receipts forbidden");
            assert_eq!(
                actual.reused,
                Ok(true),
                "tail failure left the same native lease unreadable or changed its probe"
            );
            if !matches!(case, Case::AclWait | Case::ExactWait) {
                // Actual completion and held-transaction readback establish
                // early rejection; a short channel timeout never does.
                holder_still_open(&mut control, &later, &z_holder);
            }
            assert!(
                started.elapsed() < CAUSAL_BUDGET,
                "tail causal readback exceeded original budget"
            );
        }));
        let settling_started = Instant::now();
        rollback_ok &= release(&mut b, profile, case, "b-cleanup");
        rollback_ok &= release(&mut z, profile, case, "z-cleanup");
        // SQL rollback/observation is bounded by its own five-second control
        // timeout. These synchronous calls are not hard-interrupted by the
        // four-second causal or six-second settling clock; the outer runner
        // must retain its process-group deadline and any original timeout.
        let joined = settle(
            worker,
            &receive,
            &mut outcome,
            settling_started,
            profile,
            case,
        );
        let state_check = if rollback_ok && joined.as_ref().is_some_and(Result::is_ok) {
            Some(catch_unwind(AssertUnwindSafe(|| {
                assert_eq!(
                    snapshot(&mut control, collection),
                    before,
                    "tail failed batch changed full15field native state"
                );
            })))
        } else {
            None
        };
        if let Err(payload) = primary {
            if state_check.as_ref().is_some_and(Result::is_err) {
                eprintln!("nakama_write_tail_drain_secondary_snapshot_failure profile={} case={} primary_panic_preserved=true", profile.metadata_value(), case.name());
            }
            if let Some(actual) = &outcome {
                log_outcome(profile, case, actual, true);
            }
            if joined.as_ref().is_some_and(Result::is_err) {
                eprintln!("nakama_write_tail_drain_secondary_worker_panic profile={} case={} primary_panic_preserved=true", profile.metadata_value(), case.name());
            }
            resume_unwind(payload);
        }
        match joined {
            Some(Ok(())) => {}
            Some(Err(payload)) => resume_unwind(payload),
            None => panic!("tail worker did not settle; actual primary result retained; outer process-group deadline required"),
        }
        assert!(
            rollback_ok,
            "tail owned blocker cleanup failed after successful causal body"
        );
        match state_check {
            Some(Ok(())) => {}
            Some(Err(payload)) => resume_unwind(payload),
            None => panic!("tail full15field snapshot was unavailable after cleanup"),
        }
        println!("nakama_write_tail_drain_case_executed profile={} case={} fields=15 no_receipts=true same_lease_readable=true", profile.metadata_value(), case.name());
    }
    pub(super) fn exercise(url: &str, profile: DatabaseProfile, collection: &str) {
        for case in [
            Case::AclWait,
            Case::ExactWait,
            Case::CreateOnlyStops,
            Case::AclNativeStops,
            Case::NativeStops,
        ] {
            exercise_case(url, profile, collection, case);
        }
        println!("nakama_write_tail_drain_executed profile={} held_wait_cases=2 early_reject_cases=3 fields=15", profile.metadata_value());
    }
}
