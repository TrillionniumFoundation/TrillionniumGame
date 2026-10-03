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
        write_tail_drain::exercise_any(url.as_str(), profile, &collection);
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
    const ACCESS_SQL: &str = "SELECT public_version::TEXT, write_permission FROM public.trnm_storage_objects WHERE collection=$1 AND object_key=$2 AND user_id=$3 AND $4::JSONB IS NOT NULL FOR UPDATE";
    const EXACT_ACCESS_SQL: &str = "SELECT public_version::TEXT, write_permission FROM public.trnm_storage_objects WHERE collection=$1 AND object_key=$2 AND user_id=$3 AND $4::JSONB IS NOT NULL AND public_version::TEXT=$5::TEXT AND ($6::BOOL OR write_permission=1) FOR UPDATE";

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
        expected_source_sql: &str,
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
                    || if expected_source_sql == native_any_upsert::query_kind() {
                        !native_any_upsert::observed_wait_query(DatabaseProfile::PostgreSql, &query)
                    } else {
                        query != expected_source_sql
                    }
                {
                    return None;
                }
                if expected_source_sql == native_any_upsert::query_kind() {
                    native_any_upsert::log_observed_pg_query(
                        &query,
                        row.get::<_, i32>(0),
                        *pid,
                        application,
                    );
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
                // CR exposes native parameter-substituted query text rather
                // than this wire literal. Keep its actual bytes/digest; the
                // worker transaction and exact held-key join grant lock proof.
                // Require the new JSONB input guard, never the old skinny SQL.
                if expected_source_sql == native_any_upsert::query_kind() {
                    assert!(
                        native_any_upsert::observed_wait_query(DatabaseProfile::CockroachDb, &query),
                        "held Any query did not use a finite native CR reservation or skinny authority acquisition"
                    );
                    native_any_upsert::log_observed_cr_query(&query, &worker_txn, txn, application);
                } else {
                    assert!(
                        query.contains("trnm_storage_objects")
                            && query.contains("::JSONB")
                            && query.contains("IS NOT NULL")
                            && query.contains("FOR UPDATE")
                    );
                }
                if expected_source_sql == EXACT_ACCESS_SQL {
                    assert!(
                        query.contains("public_version")
                            && query.contains("write_permission")
                            && query.contains(" OR ")
                    );
                }
                println!(
                    "{}",
                    serde_json::json!({"schema":"local.storage-native-jsonb-lock-query.v1",
                    "profile":"cockroachdb","actual_native_query":query,"actual_native_query_sha256":query_digest(&query),
                    "expected_wire_query_sha256":if expected_source_sql == native_any_upsert::query_kind(){None}else{Some(query_digest(expected_source_sql))},
                    "expected_query_scope":if expected_source_sql == native_any_upsert::query_kind(){"profile-specific-any-acquisition"}else{"exact-wire-source"},
                    "query_match_scope":if expected_source_sql == native_any_upsert::query_kind(){"native-worker-transaction-exact-held-key-and-profile-acquisition"}else{"native-worker-transaction-exact-held-key-and-jsonb-guard"},
                    "wire_literal_equality":false,"accepted":false,"compatibility_credit":false})
                );
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
    fn log_outcome(profile: DatabaseProfile, case: &str, outcome: &Outcome, late: bool) {
        match &outcome.batch {
            Err(error) => println!(
                "nakama_write_tail_drain_actual_result profile={} case={} actual_batch_domain_code={:?} actual_batch_reason={} actual_batch_retry={:?} actual_batch_sqlstate=null late={late}",
                profile.metadata_value(), case, error.code(), error.reason(), error.retry()
            ),
            Ok(receipts) => eprintln!(
                "nakama_write_tail_drain_unexpected_receipts profile={} case={} receipt_count={} late={late}",
                profile.metadata_value(), case, receipts.len()
            ),
        }
    }
    fn wait_for_lock(
        observer: &mut Client,
        application: &str,
        holder: &Holder,
        expected_source_sql: &str,
        receive: &Receiver<Outcome>,
        outcome: &mut Option<Outcome>,
        started: Instant,
    ) -> WaitProof {
        loop {
            assert!(
                started.elapsed() < CAUSAL_BUDGET,
                "actual held-row wait was not observed within causal budget"
            );
            if let Some(proof) = observe_wait(observer, application, holder, expected_source_sql) {
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
        case: &str,
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
                    profile.metadata_value(), case, sqlstate(&error)
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
        case: &str,
    ) -> Option<std::thread::Result<()>> {
        if outcome.is_none() {
            match receive.recv_timeout(SETTLING_BUDGET.saturating_sub(started.elapsed())) {
                Ok(actual) => {
                    log_outcome(profile, case, &actual, true);
                    *outcome = Some(actual);
                }
                Err(_) => eprintln!(
                    "nakama_write_tail_drain_late_result_unavailable profile={} case={} original_result_preserved=true",
                    profile.metadata_value(), case
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
                profile.metadata_value(), case
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
        let progress = key("b-valid-progress");
        let bad = key("c-invalid");
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
            write(&progress, A, VersionCheck::Any, WritePermission::OWNER),
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
        for (index, seeded_key) in [&prelude, &rejected, &progress, &probe, &later]
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
            // Supersedes the earlier held-malformed-b fixture: native Bind now
            // rejects malformed input before that b lock can ever be reached.
            // A valid held b proves the first ACL was selected before an
            // independent malformed new c stops the tail while z stays held.
            operations.push(write(
                &progress,
                B,
                VersionCheck::Any,
                WritePermission::OWNER,
            ));
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
            hold(transaction, &progress);
            capture_holder(transaction, &mut control, profile, &progress)
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
                    native_any_upsert::query_kind(),
                    &receive,
                    &mut outcome,
                    started,
                );
                println!(
                    "nakama_write_tail_drain_lock_observed profile={} case={} worker={} blocker={} query_sha256={} proof=native-lock-view",
                    profile.metadata_value(), case.name(), proof.worker, proof.blocker, proof.query_sha256
                );
                if matches!(case, Case::AclNativeStops) {
                    rollback_ok &= release(&mut b, profile, case.name(), "b");
                } else {
                    rollback_ok &= release(&mut z, profile, case.name(), "z");
                }
                assert!(rollback_ok, "tail causal lock release failed");
            }
            let actual = receive
                .recv_timeout(CAUSAL_BUDGET.saturating_sub(started.elapsed()))
                .expect("tail actual result was unavailable within original causal budget");
            log_outcome(profile, case.name(), &actual, false);
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
        rollback_ok &= release(&mut b, profile, case.name(), "b-cleanup");
        rollback_ok &= release(&mut z, profile, case.name(), "z-cleanup");
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
            case.name(),
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
                log_outcome(profile, case.name(), actual, true);
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
        native_insert_only::exercise(url, profile, collection);
        native_exact_input::exercise(url, profile, collection);
    }

    // Source-bound, finite insert-only matrix. The official source PG/CR
    // packets qualify different committed-existing footprints. They do not
    // establish pgx pipeline, hidden SQLSTATE or complete isolation parity.
    pub(super) fn exercise_any(url: &str, profile: DatabaseProfile, collection: &str) {
        native_any_upsert::exercise(url, profile, collection);
    }

    mod native_insert_only {
        use super::*;

        const INSERT_COLUMNS: &str = "collection, object_key, user_id, value_jsonb, value_bytes, version_digest, public_version, value_projection_digest, value_origin, source_manifest_digest, read_permission, write_permission, updated_at_ms, create_time, update_time";

        #[derive(Clone, Copy)]
        enum InsertCase {
            AnyPositive,
            CommittedExisting,
            UncommittedDelete,
        }
        impl InsertCase {
            const fn name(self) -> &'static str {
                match self {
                    Self::AnyPositive => "any_positive",
                    Self::CommittedExisting => "committed_existing",
                    Self::UncommittedDelete => "uncommitted_delete",
                }
            }
            const fn expects_wait(self, profile: DatabaseProfile) -> bool {
                match self {
                    Self::AnyPositive | Self::UncommittedDelete => true,
                    Self::CommittedExisting => matches!(profile, DatabaseProfile::CockroachDb),
                }
            }
            const fn expected_error(self) -> DomainError {
                match self {
                    Self::AnyPositive => DomainError::new(
                        StableCode::PermissionDenied,
                        "storage_write_permission_denied",
                        RetryClass::Never,
                    ),
                    Self::CommittedExisting | Self::UncommittedDelete => DomainError::new(
                        StableCode::AlreadyExists,
                        "storage_object_already_exists",
                        RetryClass::Never,
                    ),
                }
            }
        }
        struct InsertHolder {
            identity: Holder,
            table_id: i64,
            index_id: i64,
            primary_name: String,
            granted_rows: serde_json::Value,
        }
        fn insert_application(case: InsertCase, role: &str, stamp: u128) -> String {
            let name = format!("star_{}_{}_{stamp:x}", case.name(), role);
            assert!(name.len() < 64);
            assert!(name.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_'));
            name
        }
        fn primary_catalog(control: &mut Client, profile: DatabaseProfile) -> (i64, i64, String) {
            let rows = match profile {
                DatabaseProfile::PostgreSql => control.query(
                    "SELECT c.oid::INT8, i.indexrelid::INT8, ci.relname::TEXT FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace JOIN pg_catalog.pg_index i ON i.indrelid=c.oid JOIN pg_catalog.pg_class ci ON ci.oid=i.indexrelid WHERE n.nspname='public' AND c.relname='trnm_storage_objects' AND i.indisprimary LIMIT 2", &[]),
                DatabaseProfile::CockroachDb => {
                    // This setting applies only to this fixture observer session.
                    // It does not fabricate the native view's empty index_name.
                    control.batch_execute("SET allow_unsafe_internals=true").unwrap();
                    control.query("SELECT descriptor_id::INT8, index_id::INT8, index_name::TEXT FROM crdb_internal.table_indexes WHERE descriptor_name='trnm_storage_objects' AND index_type='primary' LIMIT 2", &[])
                }
            }.unwrap_or_else(|error| panic!("insert-only primary catalog failed sqlstate={}", sqlstate(&error)));
            assert_eq!(
                rows.len(),
                1,
                "insert-only actual primary catalog was ambiguous"
            );
            let found = (
                rows[0].get::<_, i64>(0),
                rows[0].get::<_, i64>(1),
                rows[0].get::<_, String>(2),
            );
            assert!(found.0 > 0 && found.1 > 0 && !found.2.is_empty() && found.2.len() <= 128);
            if matches!(profile, DatabaseProfile::CockroachDb) {
                assert_eq!(found.1, 1);
            }
            found
        }
        fn fresh_granted(
            control: &mut Client,
            key: &StorageObjectKey,
            holder: &InsertHolder,
        ) -> serde_json::Value {
            match &holder.identity {
                Holder::PostgreSql { pid, xid } => {
                    let rows = control.query("SELECT a.pid::INTEGER, a.backend_xid::TEXT, a.state::TEXT, l.locktype::TEXT, l.transactionid::TEXT, l.mode::TEXT, l.granted FROM pg_catalog.pg_stat_activity a JOIN pg_catalog.pg_locks l ON l.pid=a.pid WHERE a.datname=current_database() AND a.pid=$1 AND l.locktype='transactionid' AND l.transactionid::TEXT=$2 AND l.mode='ExclusiveLock' AND l.granted LIMIT 2", &[pid, xid]).unwrap();
                    assert_eq!(
                        rows.len(),
                        1,
                        "insert-only same native holder no longer granted"
                    );
                    let r = &rows[0];
                    assert_eq!(r.get::<_, i32>(0), *pid);
                    assert_eq!(r.get::<_, String>(1), *xid);
                    assert_eq!(r.get::<_, String>(2), "idle in transaction");
                    assert_eq!(r.get::<_, String>(3), "transactionid");
                    assert_eq!(r.get::<_, String>(4), *xid);
                    assert_eq!(r.get::<_, String>(5), "ExclusiveLock");
                    assert!(r.get::<_, bool>(6));
                    serde_json::json!([[
                        pid,
                        xid,
                        "idle in transaction",
                        "transactionid",
                        xid,
                        "ExclusiveLock",
                        true
                    ]])
                }
                Holder::CockroachDb { txn, keys } => {
                    let prefix = format!("/Table/{}/1/", holder.table_id);
                    let rows=control.query("SELECT txn_id::TEXT, lock_key_pretty::TEXT, table_id::INT8, index_name::TEXT, lock_strength::TEXT, isolation_level::TEXT FROM crdb_internal.cluster_locks WHERE database_name=current_database() AND schema_name='public' AND table_name='trnm_storage_objects' AND table_id::INT8=$1::INT8 AND granted AND txn_id::TEXT=$2 AND lock_key_pretty=ANY($3::TEXT[]) ORDER BY lock_key_pretty LIMIT 64", &[&holder.table_id,txn,keys]).unwrap();
                    assert_eq!(
                        rows.len(),
                        keys.len(),
                        "insert-only native primary holder readback changed"
                    );
                    let mut raw = Vec::new();
                    for r in rows {
                        let t: String = r.get(0);
                        let k: String = r.get(1);
                        let id: i64 = r.get(2);
                        let name: Option<String> = r.get(3);
                        let strength: String = r.get(4);
                        let isolation: String = r.get(5);
                        assert_eq!(t, *txn);
                        assert!(
                            keys.contains(&k)
                                && k.starts_with(&prefix)
                                && k.contains(key.collection())
                                && k.contains(key.key())
                        );
                        assert_eq!(id, holder.table_id);
                        assert_eq!(strength, "Exclusive");
                        assert_eq!(isolation, "SERIALIZABLE");
                        raw.push(serde_json::json!([t, k, id, name, strength, isolation]));
                    }
                    serde_json::Value::Array(raw)
                }
            }
        }
        fn hold_insert_key(
            tx: &mut Transaction<'_>,
            control: &mut Client,
            profile: DatabaseProfile,
            key: &StorageObjectKey,
            delete_row: bool,
            primary: &(i64, i64, String),
        ) -> InsertHolder {
            tx.batch_execute("SET LOCAL idle_in_transaction_session_timeout='10s'")
                .unwrap();
            let relation = if matches!(profile, DatabaseProfile::CockroachDb) {
                assert!(primary
                    .2
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'_'));
                format!("public.trnm_storage_objects@\"{}\"", primary.2)
            } else {
                "public.trnm_storage_objects".to_owned()
            };
            let row=tx.query_one(&format!("SELECT collection, object_key, user_id FROM {relation} WHERE collection=$1 AND object_key=$2 AND user_id=$3 FOR UPDATE"), &[&key.collection(),&key.key(),&key.user_id().as_bytes().as_slice()]).unwrap();
            assert_eq!(row.get::<_, String>(0), key.collection());
            assert_eq!(row.get::<_, String>(1), key.key());
            assert_eq!(
                row.get::<_, Vec<u8>>(2).as_slice(),
                key.user_id().as_bytes().as_slice()
            );
            if delete_row {
                let deleted=tx.query_one("DELETE FROM public.trnm_storage_objects WHERE collection=$1 AND object_key=$2 AND user_id=$3 RETURNING collection, object_key, user_id", &[&key.collection(),&key.key(),&key.user_id().as_bytes().as_slice()]).unwrap();
                assert_eq!(deleted.get::<_, String>(0), key.collection());
                assert_eq!(deleted.get::<_, String>(1), key.key());
                assert_eq!(
                    deleted.get::<_, Vec<u8>>(2).as_slice(),
                    key.user_id().as_bytes().as_slice()
                );
            }
            let remaining=tx.query_one("SELECT count(*)::INT8 FROM public.trnm_storage_objects WHERE collection=$1 AND object_key=$2 AND user_id=$3", &[&key.collection(),&key.key(),&key.user_id().as_bytes().as_slice()]).unwrap().get::<_,i64>(0);
            assert_eq!(remaining, if delete_row { 0 } else { 1 });
            let identity = match profile {
                DatabaseProfile::PostgreSql => {
                    let r = tx
                        .query_one("SELECT pg_backend_pid(), pg_current_xact_id()::TEXT", &[])
                        .unwrap();
                    Holder::PostgreSql {
                        pid: r.get(0),
                        xid: r.get(1),
                    }
                }
                DatabaseProfile::CockroachDb => {
                    let prefix = format!("/Table/{}/1/", primary.0);
                    let rows=control.query("SELECT txn_id::TEXT, lock_key_pretty::TEXT FROM crdb_internal.cluster_locks WHERE database_name=current_database() AND schema_name='public' AND table_name='trnm_storage_objects' AND table_id::INT8=$1::INT8 AND granted AND strpos(lock_key_pretty,$2)=1 AND strpos(lock_key_pretty,$3)>0 AND strpos(lock_key_pretty,$4)>0 ORDER BY txn_id::TEXT,lock_key_pretty LIMIT 64", &[&primary.0,&prefix,&key.collection(),&key.key()]).unwrap();
                    assert!(
                        !rows.is_empty() && rows.len() < 64,
                        "insert-only owned PRIMARY holder missing"
                    );
                    let txn: String = rows[0].get(0);
                    assert!(!txn.is_empty() && txn.len() <= 128);
                    let mut keys = Vec::new();
                    for r in rows {
                        assert_eq!(
                            r.get::<_, String>(0),
                            txn,
                            "insert-only primary key had distinct holders"
                        );
                        let k: String = r.get(1);
                        assert!(k.starts_with(&prefix) && k.len() <= 8192);
                        keys.push(k);
                    }
                    keys.sort();
                    keys.dedup();
                    Holder::CockroachDb { txn, keys }
                }
            };
            let mut holder = InsertHolder {
                identity,
                table_id: primary.0,
                index_id: primary.1,
                primary_name: primary.2.clone(),
                granted_rows: serde_json::Value::Null,
            };
            holder.granted_rows = fresh_granted(control, key, &holder);
            holder
        }
        fn plain_insert_query(query: &str) -> bool {
            // Inspect the native statement without modifying its bytes. PG
            // exposes the wire literal; CR exposes its own substituted display.
            let Some(after_table) = query
                .trim_start()
                .strip_prefix("INSERT INTO public.trnm_storage_objects")
            else {
                return false;
            };
            query.len() <= 8192
                && after_table.trim_start().starts_with('(')
                && query.contains(INSERT_COLUMNS)
                && query.contains("VALUES (")
                && query.contains("'write-request-bytes'")
                && query.contains("RETURNING ")
                && query.contains("value_projection_digest")
                && query.contains("create_time")
                && query.contains("update_time")
                && !query.contains("ON CONFLICT")
                && !query.contains("FOR UPDATE")
                && !query.contains("WITH upd")
        }
        fn observe_insert_wait(
            control: &mut Client,
            application: &str,
            holder: &InsertHolder,
            case: InsertCase,
        ) -> Option<WaitProof> {
            if matches!(case, InsertCase::AnyPositive) {
                return observe_wait(
                    control,
                    application,
                    &holder.identity,
                    native_any_upsert::query_kind(),
                );
            }
            match &holder.identity {
                Holder::PostgreSql { pid, xid } => {
                    let rows=control.query("SELECT a.pid::INTEGER, a.backend_xid::TEXT, a.query::TEXT, a.state::TEXT, a.wait_event_type::TEXT, a.wait_event::TEXT, pg_blocking_pids(a.pid), l.locktype::TEXT, l.transactionid::TEXT, l.granted, h.pid::INTEGER, h.mode::TEXT, h.granted FROM pg_catalog.pg_stat_activity a JOIN pg_catalog.pg_locks l ON l.pid=a.pid JOIN pg_catalog.pg_locks h ON h.locktype=l.locktype AND h.transactionid=l.transactionid WHERE a.datname=current_database() AND a.application_name=$1 AND a.pid<>$2 AND $2=ANY(pg_blocking_pids(a.pid)) AND a.wait_event_type='Lock' AND l.locktype='transactionid' AND NOT l.granted AND h.pid=$2 AND h.transactionid::TEXT=$3 AND h.mode='ExclusiveLock' AND h.granted LIMIT 2", &[&application,pid,xid]).unwrap();
                    if rows.is_empty() {
                        return None;
                    }
                    assert_eq!(rows.len(), 1, "insert-only PG worker ambiguous");
                    let r = &rows[0];
                    let worker: i32 = r.get(0);
                    let worker_xid: Option<String> = r.get(1);
                    let query: String = r.get(2);
                    let phase: String = r.get(3);
                    let wait_type: String = r.get(4);
                    let wait: Option<String> = r.get(5);
                    let blocking: Vec<i32> = r.get(6);
                    let kind: String = r.get(7);
                    let blocked_xid: String = r.get(8);
                    let granted: bool = r.get(9);
                    let blocker: i32 = r.get(10);
                    let mode: String = r.get(11);
                    let blocker_granted: bool = r.get(12);
                    assert_ne!(worker, *pid);
                    assert!(worker_xid.as_ref().is_some_and(|x| x != xid));
                    assert_eq!(wait_type, "Lock");
                    assert!(blocking.contains(pid));
                    assert_eq!(kind, "transactionid");
                    assert_eq!(blocked_xid, *xid);
                    assert!(!granted);
                    assert_eq!(blocker, *pid);
                    assert_eq!(mode, "ExclusiveLock");
                    assert!(blocker_granted);
                    assert!(
                        plain_insert_query(&query),
                        "insert-only PG wait did not execute native plain INSERT"
                    );
                    println!(
                        "{}",
                        serde_json::json!({"schema":"local.storage-native-insert-only-lock-query.v1","profile":"postgresql","case":case.name(),"actual_native_query":query,"actual_native_query_sha256":query_digest(&query),"native_fields":[worker,worker_xid,query,phase,wait_type,wait,blocking,kind,blocked_xid,granted,blocker,mode,blocker_granted],"wire_literal_normalized":false,"hidden_batch_sqlstate":null,"accepted":false})
                    );
                    Some(WaitProof {
                        worker: worker.to_string(),
                        blocker: pid.to_string(),
                        query_sha256: query_digest(&query),
                    })
                }
                Holder::CockroachDb { txn, keys } => {
                    let rows=control.query("SELECT q.query_id::TEXT, q.txn_id::TEXT, q.query::TEXT, q.phase::TEXT, l.lock_key_pretty::TEXT, l.granted, l.contended, h.txn_id::TEXT, h.granted, l.table_id::INT8, l.index_name::TEXT, h.index_name::TEXT FROM crdb_internal.cluster_queries q JOIN crdb_internal.cluster_locks l ON q.txn_id::TEXT=l.txn_id::TEXT JOIN crdb_internal.cluster_locks h ON h.database_name=l.database_name AND h.schema_name=l.schema_name AND h.table_name=l.table_name AND h.lock_key_pretty=l.lock_key_pretty WHERE q.application_name=$1 AND l.database_name=current_database() AND l.schema_name='public' AND l.table_name='trnm_storage_objects' AND l.table_id::INT8=$4::INT8 AND l.lock_key_pretty=ANY($2::TEXT[]) AND NOT l.granted AND l.contended AND l.txn_id::TEXT<>$3 AND h.txn_id::TEXT=$3 AND h.granted ORDER BY q.query_id::TEXT,l.lock_key_pretty LIMIT 64", &[&application,keys,txn,&holder.table_id]).unwrap();
                    if rows.is_empty() {
                        return None;
                    }
                    assert!(rows.len() < 64);
                    let worker: String = rows[0].get(1);
                    assert_ne!(worker, *txn);
                    assert!(!worker.is_empty() && worker.len() <= 128);
                    let query: String = rows[0].get(2);
                    assert!(
                        plain_insert_query(&query),
                        "insert-only CR wait did not execute native plain INSERT"
                    );
                    let mut raw = Vec::new();
                    for r in rows {
                        let id: String = r.get(0);
                        let t: String = r.get(1);
                        let q: String = r.get(2);
                        let phase: String = r.get(3);
                        let key: String = r.get(4);
                        let granted: bool = r.get(5);
                        let contended: bool = r.get(6);
                        let h: String = r.get(7);
                        let hg: bool = r.get(8);
                        let table: i64 = r.get(9);
                        let name: Option<String> = r.get(10);
                        let hname: Option<String> = r.get(11);
                        assert!(!id.is_empty() && id.len() <= 128 && phase.len() <= 128);
                        assert_eq!(t, worker);
                        assert_eq!(q, query);
                        assert!(
                            keys.contains(&key)
                                && key.starts_with(&format!("/Table/{}/1/", holder.table_id))
                        );
                        assert!(!granted && contended && hg);
                        assert_eq!(h, *txn);
                        assert_eq!(table, holder.table_id);
                        raw.push(serde_json::json!([
                            id, t, q, phase, key, granted, contended, h, hg, table, name, hname
                        ]));
                    }
                    println!(
                        "{}",
                        serde_json::json!({"schema":"local.storage-native-insert-only-lock-query.v1","profile":"cockroachdb","case":case.name(),"actual_native_query":query,"actual_native_query_sha256":query_digest(&query),"native_fields":raw,"wire_literal_normalized":false,"hidden_batch_sqlstate":null,"accepted":false})
                    );
                    Some(WaitProof {
                        worker,
                        blocker: txn.clone(),
                        query_sha256: query_digest(&query),
                    })
                }
            }
        }
        fn hex_bytes(bytes: &[u8]) -> String {
            const HEX: &[u8; 16] = b"0123456789abcdef";
            let mut result = String::with_capacity(bytes.len() * 2);
            for byte in bytes {
                result.push(HEX[usize::from(byte >> 4)] as char);
                result.push(HEX[usize::from(byte & 0x0f)] as char);
            }
            result
        }
        fn tuple_json(r: &RawRow) -> serde_json::Value {
            serde_json::json!([
                r.collection,
                r.key,
                hex_bytes(&r.owner),
                r.native,
                r.raw.as_deref().map(hex_bytes),
                r.request_sha.as_deref().map(hex_bytes),
                r.public_version,
                hex_bytes(&r.projection_sha),
                r.origin,
                r.manifest_sha.as_deref().map(hex_bytes),
                r.read,
                r.write,
                r.updated_at_ms,
                r.create.map(|v| serde_json::json!([v.seconds, v.nanos])),
                r.update.map(|v| serde_json::json!([v.seconds, v.nanos]))
            ])
        }
        fn exercise_insert_case(
            url: &str,
            profile: DatabaseProfile,
            collection: &str,
            case: InsertCase,
        ) {
            let stamp = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            let key = |suffix: &str| {
                StorageObjectKey::new(
                    collection,
                    format!("native-star-{}-{suffix}", case.name()),
                    OWNER,
                )
                .unwrap()
            };
            let prelude = key("0-prelude");
            let rejected = key("a-acl0");
            let probe = key("y-readable");
            let later = key("z-held");
            let mut control = bounded_control(url);
            let primary_catalog = primary_catalog(&mut control, profile);
            let mut repo = bounded_repository(url, profile);
            let seeds = [
                write(&prelude, A, VersionCheck::Any, WritePermission::OWNER),
                write(&rejected, A, VersionCheck::Any, WritePermission::NONE),
                write(&probe, A, VersionCheck::Any, WritePermission::OWNER),
                write(&later, A, VersionCheck::Any, WritePermission::OWNER),
            ];
            let seeded = repo
                .apply_storage_batch_nakama_with_metadata(
                    StorageActor::Server,
                    &seeds,
                    401,
                    StorageNakamaBatchKind::Write,
                )
                .unwrap();
            assert_eq!(seeded.len(), seeds.len());
            for (operation, receipt) in seeds.iter().zip(&seeded) {
                let StorageBatchOperation::Write(w) = operation else {
                    unreachable!()
                };
                assert_known_row(
                    &mut control,
                    &w.key,
                    A,
                    w.read_permission,
                    w.write_permission,
                    401,
                    receipt.times,
                );
            }
            let before = snapshot(&mut control, collection);
            let mut worker_repo = bounded_repository(url, profile);
            let probe_before = worker_repo
                .read_storage_object_with_metadata(StorageActor::User(OWNER), &probe)
                .unwrap();
            let application = insert_application(case, "w", stamp);
            worker_repo
                .execute_migration_batch(&format!("SET application_name='{application}'"))
                .unwrap();
            let mut operations = vec![
                write(
                    &later,
                    B,
                    if matches!(case, InsertCase::AnyPositive) {
                        VersionCheck::Any
                    } else {
                        VersionCheck::MustNotExist
                    },
                    WritePermission::OWNER,
                ),
                write(&prelude, C, VersionCheck::Any, WritePermission::OWNER),
            ];
            if matches!(case, InsertCase::AnyPositive) {
                operations.push(write(
                    &rejected,
                    B,
                    VersionCheck::Any,
                    WritePermission::OWNER,
                ));
            }
            let mut holder_control = bounded_control(url);
            control_application(&mut holder_control, &insert_application(case, "h", stamp));
            let started = Instant::now();
            let mut held = Some(holder_control.transaction().unwrap());
            let holder = hold_insert_key(
                held.as_mut().unwrap(),
                &mut control,
                profile,
                &later,
                matches!(case, InsertCase::UncommittedDelete),
                &primary_catalog,
            );
            assert!(
                started.elapsed() < CAUSAL_BUDGET,
                "insert-only holder setup exceeded causal budget"
            );
            let (send, receive) = mpsc::sync_channel(1);
            let worker = std::thread::spawn(move || {
                let batch = worker_repo.apply_storage_batch_nakama_with_metadata(
                    StorageActor::User(OWNER),
                    &operations,
                    402,
                    StorageNakamaBatchKind::Write,
                );
                let completed_at = Instant::now();
                let reused = worker_repo
                    .read_storage_object_with_metadata(StorageActor::User(OWNER), &probe)
                    .map(|actual| actual == probe_before);
                send.send(Outcome {
                    batch,
                    reused,
                    completed_at,
                })
                .expect("insert-only result receiver unavailable");
            });
            let mut outcome = None;
            let mut rollback_ok = true;
            let body = catch_unwind(AssertUnwindSafe(|| {
                if case.expects_wait(profile) {
                    let proof = loop {
                        assert!(
                            started.elapsed() < CAUSAL_BUDGET,
                            "insert-only native wait absent within4s"
                        );
                        if let Some(p) =
                            observe_insert_wait(&mut control, &application, &holder, case)
                        {
                            assert!(started.elapsed() < CAUSAL_BUDGET);
                            break p;
                        }
                        match receive.try_recv() {
                            Ok(actual) => {
                                outcome = Some(actual);
                                panic!("insert-only worker finished without required native wait");
                            }
                            Err(TryRecvError::Disconnected) => {
                                panic!("insert-only worker disconnected before wait proof")
                            }
                            Err(TryRecvError::Empty) => std::thread::yield_now(),
                        }
                    };
                    println!("nakama_native_insert_only_lock_observed profile={} case={} worker={} blocker={} actual_query_sha256={} proof=native-lock-view",profile.metadata_value(),case.name(),proof.worker,proof.blocker,proof.query_sha256);
                    rollback_ok &=
                        release(&mut held, profile, case.name(), "insert-causal-release");
                    assert!(rollback_ok, "insert-only causal rollback failed");
                }
                let actual = receive
                    .recv_timeout(CAUSAL_BUDGET.saturating_sub(started.elapsed()))
                    .expect("insert-only actual result absent within4s");
                log_outcome(profile, case.name(), &actual, false);
                outcome = Some(actual);
                let actual = outcome.as_ref().unwrap();
                assert!(actual.completed_at.duration_since(started) < CAUSAL_BUDGET);
                assert_eq!(
                    actual.batch.as_ref().err().copied(),
                    Some(case.expected_error()),
                    "insert-only selected DomainError changed or ACK returned"
                );
                assert_eq!(
                    actual.reused,
                    Ok(true),
                    "insert-only same native lease health or probe changed"
                );
                if !case.expects_wait(profile) {
                    let fresh = fresh_granted(&mut control, &later, &holder);
                    assert_eq!(
                        fresh, holder.granted_rows,
                        "insert-only true completion lost fresh same native holder"
                    );
                    println!(
                        "{}",
                        serde_json::json!({"schema":"local.storage-native-insert-only-early-completion.v1","profile":profile.metadata_value(),"case":case.name(),"actual_holder_still_granted":fresh,"actual_completion_before_release":true,"accepted":false})
                    );
                }
                assert!(
                    started.elapsed() < CAUSAL_BUDGET,
                    "insert-only causal completion/readback exceeded4s"
                );
            }));
            let settling_started = Instant::now();
            rollback_ok &= release(&mut held, profile, case.name(), "insert-cleanup");
            let joined = settle(
                worker,
                &receive,
                &mut outcome,
                settling_started,
                profile,
                case.name(),
            );
            let check_state = if rollback_ok && joined.as_ref().is_some_and(Result::is_ok) {
                Some(catch_unwind(AssertUnwindSafe(|| {
                    let after = snapshot(&mut control, collection);
                    assert_eq!(
                        after, before,
                        "insert-only failed batch changed complete full15field native rows"
                    );
                    let own =
                        |r: &&RawRow| r.key.starts_with(&format!("native-star-{}-", case.name()));
                    let b: Vec<_> = before.iter().filter(own).map(tuple_json).collect();
                    let a: Vec<_> = after.iter().filter(own).map(tuple_json).collect();
                    assert_eq!(b.len(), 4);
                    assert_eq!(a, b);
                    let no_receipts = outcome.as_ref().map(|actual| actual.batch.is_err());
                    let same_lease_readable =
                        outcome.as_ref().map(|actual| actual.reused == Ok(true));
                    let causal_body_passed = body.is_ok();
                    let packet = serde_json::json!({"schema":"local.storage-native-insert-only-full-tuple.v1","profile":profile.metadata_value(),"case":case.name(),"fields":15,"before":b,"after":a,"holder_primary_catalog":[holder.table_id,holder.index_id,holder.primary_name],"holder_granted_before":holder.granted_rows,"hidden_batch_sqlstate":null,"no_receipts":no_receipts,"same_lease_readable":same_lease_readable,"causal_body_passed":causal_body_passed,"accepted":false,"full_protocol_parity":false});
                    let bytes = serde_json::to_vec(&packet).unwrap();
                    assert!(
                        bytes.len() <= 65536,
                        "insert-only native proof output exceeds64KiB"
                    );
                    println!("{}", String::from_utf8(bytes).unwrap());
                })))
            } else {
                None
            };
            if let Err(payload) = body {
                if check_state.as_ref().is_some_and(Result::is_err) {
                    eprintln!("nakama_native_insert_only_secondary_snapshot_failed profile={} case={} original_failure_preserved=true",profile.metadata_value(),case.name());
                }
                if let Some(actual) = &outcome {
                    log_outcome(profile, case.name(), actual, true);
                }
                if joined.as_ref().is_some_and(Result::is_err) {
                    eprintln!("nakama_native_insert_only_secondary_worker_panic profile={} case={} original_failure_preserved=true",profile.metadata_value(),case.name());
                }
                resume_unwind(payload);
            }
            match joined {
                Some(Ok(())) => {}
                Some(Err(payload)) => resume_unwind(payload),
                None => panic!(
                    "insert-only worker unsettled; outer owned processgroup deadline required"
                ),
            };
            assert!(rollback_ok, "insert-only holder cleanup failed");
            match check_state {
                Some(Ok(())) => {}
                Some(Err(payload)) => resume_unwind(payload),
                None => panic!("insert-only complete15field state unavailable"),
            };
            println!("nakama_native_insert_only_case_executed profile={} case={} native_wait={} fields=15 no_receipts=true same_lease_readable=true hidden_batch_sqlstate=null",profile.metadata_value(),case.name(),case.expects_wait(profile));
        }
        pub(super) fn exercise(url: &str, profile: DatabaseProfile, collection: &str) {
            for case in [
                InsertCase::AnyPositive,
                InsertCase::CommittedExisting,
                InsertCase::UncommittedDelete,
            ] {
                exercise_insert_case(url, profile, collection, case);
            }
            let (committed_existing_wait, committed_existing_no_wait) = match profile {
                DatabaseProfile::PostgreSql => (0_u8, 1_u8),
                DatabaseProfile::CockroachDb => (1_u8, 0_u8),
            };
            println!("nakama_native_insert_only_matrix_executed profile={} cases=3 committed_existing_wait={committed_existing_wait} committed_existing_no_wait={committed_existing_no_wait} uncommitted_delete_wait=1 fields=15",profile.metadata_value());
        }
    }

    // Future-production-candidate-only native input/Exact predicate matrix.
    // Real native observations are retained per profile; no pgx pipeline,
    // read-committed or upstream error-code equivalence follows from this test.
    mod native_exact_input {
        use super::*;
        use bytes::BytesMut;
        use postgres::types::{to_sql_checked, Format, IsNull, ToSql, Type};
        use std::{error::Error, fmt, io};

        const NUL_TOKEN: &str = "literal\0condition";
        const ESCAPED_NUL: &[u8] = br#"{"nul":"\u0000"}"#;
        const LEGAL_SURROGATE: &[u8] = br#"{"bad":"\ud800"}"#;

        #[derive(Clone, Copy)]
        enum MatrixCase {
            AclLateExactStale,
            AclLateExactWriteZero,
            ClientExactMatched,
            ServerExactMatchedWriteZero,
            ExactMissing,
            ExactNulAclZero,
            EscapedNul,
            BothBadPayloadAndToken,
            TypedNulAclZero,
            TypedMalformedAclZero,
        }
        impl MatrixCase {
            const fn name(self) -> &'static str {
                match self {
                    Self::AclLateExactStale => "acl_late_exact_stale",
                    Self::AclLateExactWriteZero => "acl_late_exact_write_zero",
                    Self::ClientExactMatched => "client_exact_matched_wait_commit",
                    Self::ServerExactMatchedWriteZero => "server_exact_write_zero_wait_commit",
                    Self::ExactMissing => "exact_missing_drain_wait_rollback",
                    Self::ExactNulAclZero => "exact_nul_before_acl_zero",
                    Self::EscapedNul => "escaped_nul_native_profile",
                    Self::BothBadPayloadAndToken => "both_bad_payload_token_native_priority",
                    Self::TypedNulAclZero => "typed_nul_acl_zero",
                    Self::TypedMalformedAclZero => "typed_malformed_acl_zero",
                }
            }
            const fn typed(self) -> bool {
                matches!(self, Self::TypedNulAclZero | Self::TypedMalformedAclZero)
            }
            const fn authoritative(self) -> bool {
                matches!(self, Self::ServerExactMatchedWriteZero)
            }
            const fn waits(self, profile: DatabaseProfile) -> bool {
                match self {
                    Self::ClientExactMatched
                    | Self::ServerExactMatchedWriteZero
                    | Self::ExactMissing => true,
                    Self::AclLateExactStale
                    | Self::AclLateExactWriteZero
                    | Self::ExactNulAclZero => {
                        matches!(profile, DatabaseProfile::CockroachDb)
                    }
                    _ => false,
                }
            }
        }
        enum Expected {
            Rejected(DomainError),
            Committed,
        }
        enum NativeObservation {
            Rendered(Vec<u8>),
            ServerError(String),
        }

        // Match the candidate's native input ABI without exposing a private
        // production helper or touching payload bytes with serde_json::Value.
        struct RawInput<'a>(&'a [u8]);
        impl fmt::Debug for RawInput<'_> {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.debug_struct("FixtureRawJsonb")
                    .field("bytes", &self.0.len())
                    .finish()
            }
        }
        impl ToSql for RawInput<'_> {
            fn to_sql(
                &self,
                kind: &Type,
                out: &mut BytesMut,
            ) -> Result<IsNull, Box<dyn Error + Sync + Send>> {
                if *kind != Type::JSONB || self.0.len() > 1024 * 1024 {
                    return Err(Box::new(io::Error::new(
                        io::ErrorKind::InvalidInput,
                        "fixture JSONB type/bound rejected",
                    )));
                }
                out.extend_from_slice(self.0);
                Ok(IsNull::No)
            }
            fn accepts(kind: &Type) -> bool {
                *kind == Type::JSONB
            }
            fn encode_format(&self, _: &Type) -> Format {
                Format::Text
            }
            to_sql_checked!();
        }
        struct RawToken<'a>(&'a str);
        impl fmt::Debug for RawToken<'_> {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.debug_struct("FixtureRawText")
                    .field("bytes", &self.0.len())
                    .finish()
            }
        }
        impl ToSql for RawToken<'_> {
            fn to_sql(
                &self,
                kind: &Type,
                out: &mut BytesMut,
            ) -> Result<IsNull, Box<dyn Error + Sync + Send>> {
                if *kind != Type::TEXT {
                    return Err(Box::new(io::Error::new(
                        io::ErrorKind::InvalidInput,
                        "fixture TEXT type rejected",
                    )));
                }
                out.extend_from_slice(self.0.as_bytes());
                Ok(IsNull::No)
            }
            fn accepts(kind: &Type) -> bool {
                *kind == Type::TEXT
            }
            fn encode_format(&self, _: &Type) -> Format {
                Format::Text
            }
            to_sql_checked!();
        }
        fn raw_hex(raw: &[u8]) -> String {
            const HEX: &[u8; 16] = b"0123456789abcdef";
            let mut out = String::with_capacity(raw.len() * 2);
            for byte in raw {
                out.push(char::from(HEX[usize::from(byte >> 4)]));
                out.push(char::from(HEX[usize::from(byte & 15)]));
            }
            out
        }
        fn native_probe(
            control: &mut Client,
            profile: DatabaseProfile,
            case: &str,
            payload: Option<&[u8]>,
            token: Option<&str>,
        ) -> NativeObservation {
            let (sql, types) = match (payload,token) {
                (Some(_),Some(_)) => ("SELECT CASE WHEN pg_catalog.octet_length($1::JSONB::TEXT)<=16384 THEN $1::JSONB::TEXT END, $2::TEXT", vec![Type::JSONB,Type::TEXT]),
                (Some(_),None) => ("SELECT CASE WHEN pg_catalog.octet_length($1::JSONB::TEXT)<=16384 THEN $1::JSONB::TEXT END", vec![Type::JSONB]),
                (None,Some(_)) => ("SELECT $1::TEXT",vec![Type::TEXT]),
                (None,None) => panic!("native fixture probe has no input"),
            };
            let statement = control.prepare_typed(sql, &types).unwrap_or_else(|error| {
                panic!(
                    "native input probe prepare failed sqlstate={}",
                    sqlstate(&error)
                )
            });
            assert_eq!(statement.params(), types.as_slice());
            let actual_oids: Vec<_> = statement.params().iter().map(Type::oid).collect();
            let jsonb = RawInput(payload.unwrap_or_default());
            let condition = RawToken(token.unwrap_or_default());
            let mut parameters: Vec<&(dyn ToSql + Sync)> = Vec::new();
            if payload.is_some() {
                parameters.push(&jsonb);
            }
            if token.is_some() {
                parameters.push(&condition);
            }
            let observation = match control.query_one(&statement, &parameters) {
                Ok(row) => {
                    let value: String = if payload.is_some() {
                        row.get::<_, Option<String>>(0)
                            .expect("synthetic native render exceeded independent probe bound")
                    } else {
                        row.get(0)
                    };
                    if let Some(token) = token {
                        let token_column = usize::from(payload.is_some());
                        let returned: String = row.get(token_column);
                        assert_eq!(returned.as_bytes(), token.as_bytes());
                    }
                    NativeObservation::Rendered(value.into_bytes())
                }
                Err(error) => {
                    let code = error
                        .code()
                        .expect("native probe driver error has no actual server SQLSTATE")
                        .code()
                        .to_owned();
                    assert!(
                        code.len() == 5
                            && code
                                .bytes()
                                .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit())
                    );
                    NativeObservation::ServerError(code)
                }
            };
            println!(
                "{}",
                serde_json::json!({"schema":"local.storage-native-jsonb-input-observation.v1",
                "profile":profile.metadata_value(),"case":case,"parameter_oids":actual_oids,"parameter_format":"Text",
                "payload_hex":payload.map(raw_hex),"token_hex":token.map(|s|raw_hex(s.as_bytes())),
                "actual_native_render_hex":match &observation {NativeObservation::Rendered(raw)=>Some(raw_hex(raw)),NativeObservation::ServerError(_)=>None},
                "independent_probe_sqlstate":match &observation {NativeObservation::ServerError(code)=>Some(code.as_str()),NativeObservation::Rendered(_)=>None},
                "batch_sqlstate":null,"payload_serde_roundtrip":false,"accepted":false,"compatibility_credit":false,
                "production_ready":false,"full_protocol_parity":false})
            );
            observation
        }
        fn permission_error() -> DomainError {
            DomainError::new(
                StableCode::PermissionDenied,
                "storage_write_permission_denied",
                RetryClass::Never,
            )
        }
        fn version_error() -> DomainError {
            DomainError::new(
                StableCode::FailedPrecondition,
                "storage_version_mismatch",
                RetryClass::ResyncRequired,
            )
        }
        fn expected_native(observation: &NativeObservation, accepted: Expected) -> Expected {
            match observation {
                NativeObservation::Rendered(_) => accepted,
                NativeObservation::ServerError(code) => {
                    // Independent native SQLSTATE is real. The business API
                    // intentionally carries only DomainError, never guessed
                    // hidden native fault state. Its actual mapper is public.
                    Expected::Rejected(trnm_persistence_pg::classify_sqlstate(code))
                }
            }
        }
        fn application_name(case: &str, role: &str, stamp: u128) -> String {
            let value = format!("exact_{case}_{role}_{stamp:x}");
            assert!(
                value.len() < 64
                    && value
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b == b'_')
            );
            value
        }
        fn log_actual(profile: DatabaseProfile, case: &str, outcome: &Outcome, late: bool) {
            match &outcome.batch {
                Err(error)=>println!("nakama_native_jsonb_exact_actual_result profile={} case={case} code={:?} reason={} retry={:?} batch_sqlstate=null receipt_count=0 late={late}",profile.metadata_value(),error.code(),error.reason(),error.retry()),
                Ok(receipts)=>println!("nakama_native_jsonb_exact_actual_result profile={} case={case} committed_receipt_count={} late={late}",profile.metadata_value(),receipts.len()),
            }
        }
        fn verify_committed(
            control: &mut Client,
            collection: &str,
            before: &[RawRow],
            operations: &[StorageBatchOperation],
            receipts: &[StoredStorageMutationReceipt],
            audit: i64,
        ) {
            assert_eq!(receipts.len(), operations.len());
            let mut expected = before.to_vec();
            for (operation, receipt) in operations.iter().zip(receipts) {
                let StorageBatchOperation::Write(write) = operation else {
                    panic!("native Exact matrix changed operation kind")
                };
                let old = expected.iter().find(|row| {
                    row.key == write.key.key()
                        && row.owner.as_slice() == write.key.user_id().as_bytes().as_slice()
                });
                let prior = old.map(|row| row.public_version.as_str());
                assert_eq!(receipt.receipt.key, write.key);
                assert_eq!(
                    receipt
                        .receipt
                        .previous_version
                        .as_ref()
                        .map(|value| value.as_str()),
                    prior
                );
                assert_eq!(
                    receipt.receipt.current_version,
                    Some(ContentVersion::from_value(&write.value))
                );
                assert_eq!(receipt.times.create, old.and_then(|row| row.create));
                assert!(receipt.times.update.is_some());
                let row = expected_known_row(
                    control,
                    &write.key,
                    &write.value,
                    write.read_permission,
                    write.write_permission,
                    audit,
                    receipt.times,
                );
                let position = expected
                    .iter()
                    .position(|old| old.key == row.key && old.owner == row.owner)
                    .expect("matrix success unexpectedly inserted a missing row");
                expected[position] = row;
            }
            assert_eq!(
                snapshot(control, collection),
                expected,
                "native Exact committed full15field tuple or unrelated row changed"
            );
        }

        fn exercise_case(
            url: &str,
            profile: DatabaseProfile,
            collection: &str,
            case: MatrixCase,
            bad_payload: &'static [u8],
            subvector_label: Option<&'static str>,
        ) {
            let stamp = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            let label = subvector_label.unwrap_or_else(|| case.name());
            let key = |suffix: &str| {
                StorageObjectKey::new(collection, format!("native-exact-{label}-{suffix}"), OWNER)
                    .unwrap()
            };
            let prelude = key("0-prelude");
            let rejected = key("a-rejected");
            let probe = key("y-readable");
            let later = key("z-held");
            let mut control = bounded_control(url);
            let mut repository = bounded_repository(url, profile);
            let rejected_acl = if matches!(
                case,
                MatrixCase::AclLateExactStale
                    | MatrixCase::AclLateExactWriteZero
                    | MatrixCase::ServerExactMatchedWriteZero
                    | MatrixCase::ExactNulAclZero
                    | MatrixCase::TypedNulAclZero
                    | MatrixCase::TypedMalformedAclZero
                    | MatrixCase::BothBadPayloadAndToken
            ) {
                WritePermission::NONE
            } else {
                WritePermission::OWNER
            };
            let later_acl = if matches!(case, MatrixCase::AclLateExactWriteZero) {
                WritePermission::NONE
            } else {
                WritePermission::OWNER
            };
            let mut seeds = vec![
                write(&prelude, A, VersionCheck::Any, WritePermission::OWNER),
                write(&probe, A, VersionCheck::Any, WritePermission::OWNER),
                write(&later, A, VersionCheck::Any, later_acl),
            ];
            if !matches!(case, MatrixCase::ExactMissing) {
                seeds.push(write(&rejected, A, VersionCheck::Any, rejected_acl));
            }
            let seeded = repository
                .apply_storage_batch_nakama_with_metadata(
                    StorageActor::Server,
                    &seeds,
                    301,
                    StorageNakamaBatchKind::Write,
                )
                .unwrap();
            assert_eq!(seeded.len(), seeds.len());
            for (operation, receipt) in seeds.iter().zip(&seeded) {
                let StorageBatchOperation::Write(write) = operation else {
                    unreachable!()
                };
                assert_known_row(
                    &mut control,
                    &write.key,
                    A,
                    write.read_permission,
                    write.write_permission,
                    301,
                    receipt.times,
                );
            }
            let current = ContentVersion::from_value(A).as_str().to_owned();
            let mut operations = vec![write(
                &prelude,
                C,
                VersionCheck::Any,
                WritePermission::OWNER,
            )];
            let expected = match case {
                MatrixCase::AclLateExactStale | MatrixCase::AclLateExactWriteZero => {
                    operations.push(write(
                        &rejected,
                        B,
                        VersionCheck::Any,
                        WritePermission::OWNER,
                    ));
                    let token = if matches!(case, MatrixCase::AclLateExactStale) {
                        "opaque-literal-stale".to_owned()
                    } else {
                        current.clone()
                    };
                    operations.push(write(
                        &later,
                        B,
                        VersionCheck::Exact(token.into()),
                        WritePermission::OWNER,
                    ));
                    Expected::Rejected(permission_error())
                }
                MatrixCase::ClientExactMatched | MatrixCase::ServerExactMatchedWriteZero => {
                    operations.push(write(
                        &rejected,
                        B,
                        VersionCheck::Exact(current.clone().into()),
                        WritePermission::OWNER,
                    ));
                    Expected::Committed
                }
                MatrixCase::ExactMissing => {
                    operations.push(write(
                        &rejected,
                        B,
                        VersionCheck::Exact(current.clone().into()),
                        WritePermission::OWNER,
                    ));
                    operations.push(write(&later, B, VersionCheck::Any, WritePermission::OWNER));
                    Expected::Rejected(version_error())
                }
                MatrixCase::ExactNulAclZero => {
                    let observation =
                        native_probe(&mut control, profile, label, Some(B), Some(NUL_TOKEN));
                    operations.push(write(
                        &rejected,
                        B,
                        VersionCheck::Exact(NUL_TOKEN.into()),
                        WritePermission::OWNER,
                    ));
                    operations.push(write(&later, B, VersionCheck::Any, WritePermission::OWNER));
                    expected_native(&observation, Expected::Rejected(permission_error()))
                }
                MatrixCase::EscapedNul => {
                    let observation =
                        native_probe(&mut control, profile, label, Some(ESCAPED_NUL), None);
                    operations = vec![write(
                        &rejected,
                        ESCAPED_NUL,
                        VersionCheck::Any,
                        WritePermission::OWNER,
                    )];
                    expected_native(&observation, Expected::Committed)
                }
                MatrixCase::BothBadPayloadAndToken => {
                    let token_probe_label = format!("{label}_token_only");
                    let _token_only = native_probe(
                        &mut control,
                        profile,
                        &token_probe_label,
                        None,
                        Some(NUL_TOKEN),
                    );
                    let observation = native_probe(
                        &mut control,
                        profile,
                        label,
                        Some(bad_payload),
                        Some(NUL_TOKEN),
                    );
                    assert!(matches!(&observation,NativeObservation::ServerError(code) if code=="22P02"),"native payload+token probe did not retain first actual malformed JSONB SQLSTATE");
                    operations.push(write(
                        &rejected,
                        bad_payload,
                        VersionCheck::Exact(NUL_TOKEN.into()),
                        WritePermission::OWNER,
                    ));
                    operations.push(write(&later, B, VersionCheck::Any, WritePermission::OWNER));
                    expected_native(&observation, Expected::Committed)
                }
                MatrixCase::TypedNulAclZero => {
                    operations = vec![write(
                        &rejected,
                        B,
                        VersionCheck::Exact(NUL_TOKEN.into()),
                        WritePermission::OWNER,
                    )];
                    Expected::Rejected(permission_error())
                }
                MatrixCase::TypedMalformedAclZero => {
                    operations = vec![write(
                        &rejected,
                        BAD_JSON,
                        VersionCheck::Any,
                        WritePermission::OWNER,
                    )];
                    Expected::Rejected(permission_error())
                }
            };
            // Matched Exact waits target the eligible row. Native CR also
            // waits on the two excluded late Exact rows, as qualified by the
            // official source primary-holder cases. Missing-Exact semantic
            // rejection drains a real later Any occurrence instead.
            let held_key = if matches!(
                case,
                MatrixCase::ClientExactMatched | MatrixCase::ServerExactMatchedWriteZero
            ) {
                rejected.clone()
            } else {
                later.clone()
            };
            let expected_query = if matches!(
                case,
                MatrixCase::ClientExactMatched | MatrixCase::ServerExactMatchedWriteZero
            ) || (matches!(profile, DatabaseProfile::CockroachDb)
                && matches!(
                    case,
                    MatrixCase::AclLateExactStale | MatrixCase::AclLateExactWriteZero
                )) {
                EXACT_ACCESS_SQL
            } else if case.typed() {
                ACCESS_SQL
            } else {
                native_any_upsert::query_kind()
            };
            let before = snapshot(&mut control, collection);
            let mut worker_repository = bounded_repository(url, profile);
            let probe_before = worker_repository
                .read_storage_object_with_metadata(StorageActor::User(OWNER), &probe)
                .unwrap();
            let worker_application = application_name(label, "w", stamp);
            worker_repository
                .execute_migration_batch(&format!("SET application_name='{worker_application}'"))
                .unwrap();
            let mut holder_control = bounded_control(url);
            control_application(&mut holder_control, &application_name(label, "h", stamp));
            let actor = if case.authoritative() {
                StorageActor::Server
            } else {
                StorageActor::User(OWNER)
            };
            let worker_operations = operations.clone();
            let started = Instant::now();
            let mut held = Some(holder_control.transaction().unwrap());
            hold(held.as_mut().unwrap(), &held_key);
            let holder = capture_holder(held.as_mut().unwrap(), &mut control, profile, &held_key);
            assert!(
                started.elapsed() < CAUSAL_BUDGET,
                "native Exact holder setup exceeded original causal budget"
            );
            let (send, receive) = mpsc::sync_channel(1);
            let worker = std::thread::spawn(move || {
                let batch = if case.typed() {
                    worker_repository.apply_storage_batch_with_metadata(
                        actor,
                        &worker_operations,
                        302,
                    )
                } else {
                    worker_repository.apply_storage_batch_nakama_with_metadata(
                        actor,
                        &worker_operations,
                        302,
                        StorageNakamaBatchKind::Write,
                    )
                };
                let completed_at = Instant::now();
                let reused = worker_repository
                    .read_storage_object_with_metadata(StorageActor::User(OWNER), &probe)
                    .map(|actual| actual == probe_before);
                send.send(Outcome {
                    batch,
                    reused,
                    completed_at,
                })
                .expect("native Exact result receiver unavailable");
            });
            let mut outcome = None;
            let mut rollback_ok = true;
            let primary = catch_unwind(AssertUnwindSafe(|| {
                if case.waits(profile) {
                    let proof = wait_for_lock(
                        &mut control,
                        &worker_application,
                        &holder,
                        expected_query,
                        &receive,
                        &mut outcome,
                        started,
                    );
                    let expected_wire_digest = if expected_query == native_any_upsert::query_kind()
                    {
                        "null".to_owned()
                    } else {
                        query_digest(expected_query)
                    };
                    println!("nakama_native_jsonb_exact_lock_observed profile={} case={label} worker={} blocker={} actual_query_sha256={} expected_wire_query_sha256={} proof=native-lock-view",profile.metadata_value(),proof.worker,proof.blocker,proof.query_sha256,expected_wire_digest);
                    rollback_ok &= release(&mut held, profile, label, "held-causal-release");
                    assert!(rollback_ok, "native Exact causal holder release failed");
                }
                let actual = receive
                    .recv_timeout(CAUSAL_BUDGET.saturating_sub(started.elapsed()))
                    .expect("native Exact actual result absent within original causal budget");
                log_actual(profile, label, &actual, false);
                outcome = Some(actual);
                let actual = outcome.as_ref().unwrap();
                assert!(actual.completed_at.duration_since(started) < CAUSAL_BUDGET);
                match &expected {
                    Expected::Rejected(error) => assert_eq!(
                        actual.batch.as_ref().err(),
                        Some(error),
                        "native Exact failure lost selected error or returned receipts"
                    ),
                    Expected::Committed => assert_eq!(
                        actual.batch.as_ref().map(Vec::len),
                        Ok(operations.len()),
                        "native Exact matched input did not return committed receipts"
                    ),
                }
                assert_eq!(
                    actual.reused,
                    Ok(true),
                    "native Exact same-lease read health changed or failed"
                );
                if !case.waits(profile) {
                    holder_still_open(&mut control, &held_key, &holder);
                }
                assert!(
                    started.elapsed() < CAUSAL_BUDGET,
                    "native Exact completion/held readback exceeded causal clock"
                );
            }));
            let settling_started = Instant::now();
            rollback_ok &= release(&mut held, profile, label, "held-cleanup");
            // The inherited settle helper joins only is_finished workers and
            // retains the first panic/result. Its six-second clock does not
            // hard interrupt this preceding five-second SQL rollback.
            let joined = settle(
                worker,
                &receive,
                &mut outcome,
                settling_started,
                profile,
                label,
            );
            let state_check = if rollback_ok && joined.as_ref().is_some_and(Result::is_ok) {
                Some(catch_unwind(AssertUnwindSafe(|| match &expected {
                    Expected::Rejected(_) => assert_eq!(
                        snapshot(&mut control, collection),
                        before,
                        "native Exact failed transaction changed a full15field tuple"
                    ),
                    Expected::Committed => verify_committed(
                        &mut control,
                        collection,
                        &before,
                        &operations,
                        outcome.as_ref().unwrap().batch.as_ref().unwrap(),
                        302,
                    ),
                })))
            } else {
                None
            };
            if let Err(payload) = primary {
                if state_check.as_ref().is_some_and(Result::is_err) {
                    eprintln!("nakama_native_jsonb_exact_secondary_snapshot_failed profile={} case={label} primary_panic_preserved=true",profile.metadata_value());
                }
                if let Some(actual) = &outcome {
                    log_actual(profile, label, actual, true);
                }
                if joined.as_ref().is_some_and(Result::is_err) {
                    eprintln!("nakama_native_jsonb_exact_secondary_worker_panic profile={} case={label} primary_panic_preserved=true",profile.metadata_value());
                }
                resume_unwind(payload);
            }
            match joined {Some(Ok(()))=>{},Some(Err(payload))=>resume_unwind(payload),None=>panic!("native Exact worker unsettled; original result retained; process-group deadline required")}
            assert!(
                rollback_ok,
                "native Exact holder rollback failed after successful causal body"
            );
            match state_check {
                Some(Ok(())) => {}
                Some(Err(payload)) => resume_unwind(payload),
                None => panic!("native Exact full15field state comparison unavailable"),
            }
            if subvector_label.is_some() {
                println!("nakama_native_jsonb_exact_subvector_executed profile={} case={label} main_case=both_bad_payload_token_native_priority input=legal_object_escaped_unpaired_surrogate fields=15 actual_domain=InvalidArgument actual_reason=database_constraint_violation retry=Never no_receipts=true same_lease_readable=true hidden_batch_sqlstate=null",profile.metadata_value());
            } else {
                println!("nakama_native_jsonb_exact_case_executed profile={} case={label} fields=15 native_wait={} committed={} same_lease_readable=true payload_serde_roundtrip=false",profile.metadata_value(),case.waits(profile),matches!(expected,Expected::Committed));
            }
        }

        fn duplicate_exact(url: &str, profile: DatabaseProfile, collection: &str) {
            let key = StorageObjectKey::new(collection, "native-exact-successful-duplicate", OWNER)
                .unwrap();
            let mut repository = bounded_repository(url, profile);
            let mut control = bounded_control(url);
            let seed = repository
                .apply_storage_batch_nakama_with_metadata(
                    StorageActor::Server,
                    &[write(&key, A, VersionCheck::Any, WritePermission::OWNER)],
                    401,
                    StorageNakamaBatchKind::Write,
                )
                .unwrap();
            assert_eq!(seed.len(), 1);
            assert_known_row(
                &mut control,
                &key,
                A,
                ReadPermission::OWNER,
                WritePermission::OWNER,
                401,
                seed[0].times,
            );
            let before = snapshot(&mut control, collection);
            let operations = [
                write(
                    &key,
                    B,
                    VersionCheck::Exact(ContentVersion::from_value(A).into()),
                    WritePermission::OWNER,
                ),
                write(
                    &key,
                    C,
                    VersionCheck::Exact(ContentVersion::from_value(B).into()),
                    WritePermission::OWNER,
                ),
            ];
            let receipts = repository
                .apply_storage_batch_nakama_with_metadata(
                    StorageActor::User(OWNER),
                    &operations,
                    402,
                    StorageNakamaBatchKind::Write,
                )
                .unwrap();
            verify_committed(
                &mut control,
                collection,
                &before,
                &operations,
                &receipts,
                402,
            );
            assert_eq!(receipts[0].times.create, seed[0].times.create);
            assert_eq!(receipts[1].times.create, seed[0].times.create);
            assert_eq!(receipts[0].times.update, receipts[1].times.update);
            let generated = ContentVersion::from_value(C);
            let row=control.query_one("SELECT public_version::TEXT=$4::TEXT, write_permission=1 FROM public.trnm_storage_objects WHERE collection=$1 AND object_key=$2 AND user_id=$3",
                &[&key.collection(),&key.key(),&key.user_id().as_bytes().as_slice(),&generated.as_str()]).unwrap();
            assert!(row.get::<_, bool>(0) && row.get::<_, bool>(1));
            let actual = repository
                .read_storage_object_with_metadata(StorageActor::User(OWNER), &key)
                .unwrap();
            assert_eq!(actual.object.version.as_str(), generated.as_str());
            assert_eq!(actual.times, receipts[1].times);
            assert!(actual
                .object
                .collision_witness
                .as_ref()
                .is_some_and(|witness| witness.matches_request(C)));
            actual.object.verify_integrity().unwrap();
            println!("nakama_native_jsonb_exact_case_executed profile={} case=duplicate_exact_step_receipts fields=15 occurrences=2 ack_positions=original committed=true native_returning_predicates=true same_lease_readable=true",profile.metadata_value());
        }
        pub(super) fn exercise(url: &str, profile: DatabaseProfile, collection: &str) {
            for case in [
                MatrixCase::AclLateExactStale,
                MatrixCase::AclLateExactWriteZero,
                MatrixCase::ClientExactMatched,
                MatrixCase::ServerExactMatchedWriteZero,
                MatrixCase::ExactMissing,
                MatrixCase::ExactNulAclZero,
                MatrixCase::EscapedNul,
                MatrixCase::BothBadPayloadAndToken,
                MatrixCase::TypedNulAclZero,
                MatrixCase::TypedMalformedAclZero,
            ] {
                exercise_case(url, profile, collection, case, BAD_JSON, None);
                if matches!(case, MatrixCase::BothBadPayloadAndToken) {
                    exercise_case(
                        url,
                        profile,
                        collection,
                        case,
                        LEGAL_SURROGATE,
                        Some("both_bad_input_legal_surrogate"),
                    );
                }
            }
            duplicate_exact(url, profile, collection);
            let (late_exact_wait_cases, late_exact_no_wait_cases) = match profile {
                DatabaseProfile::PostgreSql => (0_u8, 2_u8),
                DatabaseProfile::CockroachDb => (2_u8, 0_u8),
            };
            println!(
                "{}",
                serde_json::json!({"schema":"local.storage-native-jsonb-exact-fixture.v1","profile":profile.metadata_value(),
                "cases":11,"late_exact_excluded_cases":2,"late_exact_wait_cases":late_exact_wait_cases,
                "late_exact_no_wait_cases":late_exact_no_wait_cases,"matched_wait_commit_cases":2,"missing_exact_drain_wait_rollback_cases":1,
                "literal_native_text_cases":1,"escaped_nul_profile_observations":1,"both_bad_input_priority_cases":1,"both_bad_input_vectors":2,"legal_surrogate_bind_vectors":1,
                "duplicate_exact_commit_cases":1,"typed_policy_cases":2,"tuple_fields":15,
                "payload_serde_roundtrip":false,"automatic_mutation_retry":false,"accepted":false,"compatibility_credit":false,
                "production_ready":false,"full_protocol_parity":false,"full_nakama_replacement":false})
            );
            println!("nakama_native_jsonb_exact_matrix_executed profile={} cases=11 late_exact_excluded=2 matched_wait_commit=2 missing_exact_drain_wait=1 literal_native_text=1 escaped_nul=1 both_bad_input=1 both_bad_input_vectors=2 surrogate_bind=1 duplicate_exact=1 typed_policy=2 fields=15 late_exact_wait={late_exact_wait_cases} late_exact_no_wait={late_exact_no_wait_cases}",profile.metadata_value());
        }
    }
    // Native Any observations are a separate bounded target regression. They
    // neither execute the pinned Nakama server nor observe PG worker isolation.
    mod native_any_upsert {
        use super::*;

        const QUERY_KIND: &str = "native-any-profile-specific-acquisition";
        const CR_ACCESS_SQL: &str = "SELECT public_version::TEXT, write_permission FROM public.trnm_storage_objects WHERE collection=$1 AND object_key=$2 AND user_id=$3 FOR UPDATE";
        const COLUMNS: &str = "COLLECTION,OBJECT_KEY,USER_ID,VALUE_JSONB,VALUE_BYTES,VERSION_DIGEST,PUBLIC_VERSION,VALUE_PROJECTION_DIGEST,VALUE_ORIGIN,SOURCE_MANIFEST_DIGEST,READ_PERMISSION,WRITE_PERMISSION,UPDATED_AT_MS,CREATE_TIME,UPDATE_TIME";
        const SURROGATE: &[u8] = br#"{"bad":"\ud800"}"#;

        #[derive(Clone, Copy)]
        enum AnyCase {
            ExistingPermission,
            CreatorCommit,
            UnknownNoop,
            UnknownSurrogate,
        }
        impl AnyCase {
            const fn name(self) -> &'static str {
                match self {
                    Self::ExistingPermission => "existing_permission_wait_rollback",
                    Self::CreatorCommit => "concurrent_creator_commit_true_prior",
                    Self::UnknownNoop => "unknown_matching_token_noop",
                    Self::UnknownSurrogate => "unknown_surrogate_native_priority",
                }
            }
            const fn expects_wait(self) -> bool {
                !matches!(self, Self::UnknownSurrogate)
            }
            const fn commits(self) -> bool {
                matches!(self, Self::CreatorCommit | Self::UnknownNoop)
            }
        }

        struct AnyHolder {
            identity: Holder,
            table_id: i64,
            index_id: i64,
            primary_name: String,
            actual_granted_rows: serde_json::Value,
        }
        struct AnyWait {
            proof: WaitProof,
            actual_rows: serde_json::Value,
            worker_isolation: Option<String>,
            actual_query_kind: &'static str,
        }

        pub(super) const fn query_kind() -> &'static str {
            QUERY_KIND
        }
        pub(super) fn reservation_query(query: &str) -> bool {
            // This is a finite statement classifier, not SQL identity
            // normalization. Every observation retains the original query.
            if query.len() > 8192 {
                return false;
            }
            let upper = query.to_ascii_uppercase();
            let Some(after) = upper
                .trim_start()
                .strip_prefix("INSERT INTO PUBLIC.TRNM_STORAGE_OBJECTS AS STORED")
            else {
                return false;
            };
            let Some((columns, after_columns)) = after
                .trim_start()
                .strip_prefix('(')
                .and_then(|value| value.split_once(')'))
            else {
                return false;
            };
            let compact = |value: &str| {
                value
                    .chars()
                    .filter(|character| !character.is_ascii_whitespace())
                    .collect::<String>()
            };
            let Some((values, conflict)) = after_columns.split_once(" ON CONFLICT ") else {
                return false;
            };
            let Some((conflict_body, returning)) = conflict.split_once(" RETURNING ") else {
                return false;
            };
            compact(columns) == COLUMNS
                && values.trim_start().starts_with("VALUES (")
                && values.contains("::JSONB")
                && values.contains("'WRITE-REQUEST-BYTES'")
                && compact(conflict_body) == "(COLLECTION,OBJECT_KEY,USER_ID)DOUPDATESETOBJECT_KEY=EXCLUDED.OBJECT_KEYWHEREFALSE"
                && returning.contains("VALUE_PROJECTION_DIGEST")
                && returning.contains("CREATE_TIME")
                && returning.contains("UPDATE_TIME")
                && returning.contains("UPDATED_AT_MS")
                && !upper.contains("FOR UPDATE")
                && !upper.contains("WITH UPD")
        }

        fn access_query(query: &str) -> bool {
            if query.len() > 8192 {
                return false;
            }
            let upper = query.to_ascii_uppercase();
            let Some((head, predicate)) = upper
                .trim_start()
                .split_once(" FROM PUBLIC.TRNM_STORAGE_OBJECTS WHERE ")
            else {
                return false;
            };
            let fields: String = head.chars().filter(|c| !c.is_ascii_whitespace()).collect();
            (fields == "SELECTPUBLIC_VERSION::TEXT,WRITE_PERMISSION"
                || fields == "SELECTPUBLIC_VERSION::STRING,WRITE_PERMISSION")
                && predicate.trim_end().ends_with("FOR UPDATE")
                && predicate.contains("COLLECTION")
                && predicate.contains("OBJECT_KEY")
                && predicate.contains("USER_ID")
                && !predicate.contains("PUBLIC_VERSION")
                && !predicate.contains("WRITE_PERMISSION")
                && !predicate.contains(" OR ")
                && !predicate.contains(" UNION ")
                && !predicate.contains("VALUE_JSONB")
                && !predicate.contains("IS NOT NULL")
        }
        pub(super) fn legacy_wait_query(profile: DatabaseProfile, query: &str) -> bool {
            match profile {
                DatabaseProfile::PostgreSql => reservation_query(query),
                DatabaseProfile::CockroachDb => reservation_query(query) || access_query(query),
            }
        }
        fn actual_query_kind(query: &str) -> &'static str {
            if reservation_query(query) {
                "native-reservation-insert15-disabled-conflict"
            } else if access_query(query) {
                "native-skinny-acl-select-for-update"
            } else {
                panic!("unrecognized native Any acquisition query")
            }
        }
        fn case_query(profile: DatabaseProfile, case: AnyCase, query: &str) -> bool {
            if matches!(profile, DatabaseProfile::PostgreSql)
                || matches!(case, AnyCase::CreatorCommit)
            {
                reservation_query(query)
            } else {
                reservation_query(query) || access_query(query)
            }
        }
        // PG17.6 with the independently observed1KiB activity buffer exposes
        // exactly1023 bytes. A prefix match only observes that bounded prefix:
        // the full RETURNING remainder is source-bound, not activity-observed.
        // The frozen controls bind this complete template to the actual
        // storage_any_upsert_query(PostgreSql,true) source expansion.
        const PG_ANY_RESERVATION_WIRE: &str = r#"INSERT INTO public.trnm_storage_objects AS stored (collection,object_key,user_id,value_jsonb,value_bytes,version_digest,public_version,value_projection_digest,value_origin,source_manifest_digest,read_permission,write_permission,updated_at_ms,create_time,update_time) VALUES ($1,$2,$3,$4::JSONB,$5,$6,$7,pg_catalog.sha256(pg_catalog.convert_to($4::JSONB::TEXT,'UTF8')),'write-request-bytes',NULL,$8,$9,$10,pg_catalog.now(),pg_catalog.now()) ON CONFLICT (collection,object_key,user_id) DO UPDATE SET object_key=excluded.object_key WHERE FALSE RETURNING CASE WHEN TRUE THEN CASE WHEN pg_catalog.octet_length(value_jsonb::TEXT)::INT8 <= 16777216 THEN value_jsonb::TEXT END END, CASE WHEN TRUE THEN pg_catalog.octet_length(value_jsonb::TEXT)::INT8 END, CASE WHEN TRUE THEN public_version::TEXT END, CASE WHEN TRUE THEN value_projection_digest END, CASE WHEN TRUE THEN read_permission END, CASE WHEN TRUE THEN write_permission END, CASE WHEN TRUE THEN value_origin END, CASE WHEN TRUE THEN CASE WHEN pg_catalog.octet_length(value_bytes)::INT8 <= 1048576 THEN value_bytes END END, CASE WHEN TRUE THEN pg_catalog.octet_length(value_bytes)::INT8 END, CASE WHEN TRUE THEN version_digest END, CASE WHEN TRUE THEN source_manifest_digest END, CASE WHEN TRUE THEN CASE WHEN value_origin IN ('legacy-rust-v2-bytes', 'write-request-bytes') AND pg_catalog.octet_length(value_bytes)::INT8 <= 1048576 THEN CASE WHEN pg_catalog.octet_length(pg_catalog.convert_from(value_bytes, 'UTF8')::JSONB::TEXT) <= 16777216 THEN pg_catalog.convert_from(value_bytes, 'UTF8')::JSONB::TEXT END END END, CASE WHEN TRUE THEN create_time END, CASE WHEN TRUE THEN update_time END, updated_at_ms, collection, object_key, user_id"#;
        const PG_OBSERVED_QUERY_PREFIX_BYTES: usize = 1023;

        pub(super) fn pg_reservation_scope(query: &str) -> Option<&'static str> {
            if query == PG_ANY_RESERVATION_WIRE {
                Some("exact-complete-PG-wire-template")
            } else if query.len() == PG_OBSERVED_QUERY_PREFIX_BYTES
                && PG_ANY_RESERVATION_WIRE
                    .as_bytes()
                    .get(..PG_OBSERVED_QUERY_PREFIX_BYTES)
                    == Some(query.as_bytes())
            {
                Some("exact-observed-PG-wire-prefix-1023")
            } else {
                None
            }
        }

        // CR26.2.6 cluster_queries exposes this exact997-byte wire prefix
        // followed by a native U+2026 marker (1000 UTF-8 bytes total). The
        // complete RETURNING remainder stays source-bound, never observed.
        const CR_ANY_RESERVATION_WIRE: &str = r#"INSERT INTO public.trnm_storage_objects AS stored (collection,object_key,user_id,value_jsonb,value_bytes,version_digest,public_version,value_projection_digest,value_origin,source_manifest_digest,read_permission,write_permission,updated_at_ms,create_time,update_time) VALUES ($1,$2,$3,$4::JSONB,$5,$6,$7,pg_catalog.decode(pg_catalog.sha256(pg_catalog.convert_to($4::JSONB::TEXT,'UTF8')),'hex'),'write-request-bytes',NULL,$8,$9,$10,pg_catalog.now(),pg_catalog.now()) ON CONFLICT (collection,object_key,user_id) DO UPDATE SET object_key=excluded.object_key WHERE FALSE RETURNING CASE WHEN TRUE THEN CASE WHEN pg_catalog.octet_length(value_jsonb::TEXT)::INT8 <= 16777216 THEN value_jsonb::TEXT END END, CASE WHEN TRUE THEN pg_catalog.octet_length(value_jsonb::TEXT)::INT8 END, CASE WHEN TRUE THEN public_version::TEXT END, CASE WHEN TRUE THEN value_projection_digest END, CASE WHEN TRUE THEN read_permission END, CASE WHEN TRUE THEN write_permission END, CASE WHEN TRUE THEN value_origin END, CASE WHEN TRUE THEN CASE WHEN pg_catalog.octet_length(value_bytes)::INT8 <= 1048576 THEN value_bytes END END, CASE WHEN TRUE THEN pg_catalog.octet_length(value_bytes)::INT8 END, CASE WHEN TRUE THEN version_digest END, CASE WHEN TRUE THEN source_manifest_digest END, CASE WHEN TRUE THEN CASE WHEN value_origin IN ('legacy-rust-v2-bytes', 'write-request-bytes') AND pg_catalog.octet_length(value_bytes)::INT8 <= 1048576 THEN CASE WHEN pg_catalog.octet_length(pg_catalog.convert_from(value_bytes, 'UTF8')::JSONB::TEXT) <= 16777216 THEN pg_catalog.convert_from(value_bytes, 'UTF8')::JSONB::TEXT END END END, CASE WHEN TRUE THEN create_time END, CASE WHEN TRUE THEN update_time END, updated_at_ms, collection, object_key, user_id"#;
        const CR_OBSERVED_QUERY_PREFIX_BYTES: usize = 997;

        pub(super) fn cr_reservation_scope(query: &str) -> Option<&'static str> {
            if query == CR_ANY_RESERVATION_WIRE {
                Some("exact-complete-CR-wire-template")
            } else if query.len() == CR_OBSERVED_QUERY_PREFIX_BYTES + '…'.len_utf8()
                && query.strip_suffix('…').map(str::as_bytes)
                    == CR_ANY_RESERVATION_WIRE
                        .as_bytes()
                        .get(..CR_OBSERVED_QUERY_PREFIX_BYTES)
            {
                Some("exact-observed-CR-wire-prefix-997-ellipsis")
            } else {
                None
            }
        }

        fn cr_observed_scope(query: &str) -> Option<&'static str> {
            cr_reservation_scope(query).or_else(|| {
                access_query(query).then_some("complete-native-CR-skinny-authority-query")
            })
        }

        pub(super) fn observed_wait_query(profile: DatabaseProfile, query: &str) -> bool {
            match profile {
                DatabaseProfile::PostgreSql => pg_reservation_scope(query).is_some(),
                DatabaseProfile::CockroachDb => {
                    cr_reservation_scope(query).is_some()
                        || (access_query(query) && legacy_wait_query(profile, query))
                }
            }
        }

        fn observed_case_query(profile: DatabaseProfile, case: AnyCase, query: &str) -> bool {
            match profile {
                DatabaseProfile::PostgreSql => pg_reservation_scope(query).is_some(),
                DatabaseProfile::CockroachDb => {
                    if matches!(case, AnyCase::CreatorCommit) {
                        cr_reservation_scope(query).is_some()
                    } else {
                        cr_reservation_scope(query).is_some()
                            || (access_query(query) && case_query(profile, case, query))
                    }
                }
            }
        }

        fn observed_query_kind(profile: DatabaseProfile, query: &str) -> &'static str {
            match profile {
                DatabaseProfile::PostgreSql => {
                    assert!(pg_reservation_scope(query).is_some());
                    "native-reservation-insert15-disabled-conflict"
                }
                DatabaseProfile::CockroachDb => {
                    if cr_reservation_scope(query).is_some() {
                        "native-reservation-insert15-disabled-conflict"
                    } else {
                        assert!(access_query(query));
                        actual_query_kind(query)
                    }
                }
            }
        }

        pub(super) fn log_observed_pg_query(
            query: &str,
            worker_pid: i32,
            holder_pid: i32,
            application: &str,
        ) {
            let scope = pg_reservation_scope(query).unwrap();
            assert!(query.len() <= 8192 && application.len() <= 128);
            assert!(worker_pid > 0 && holder_pid > 0 && worker_pid != holder_pid);
            let packet = serde_json::json!({
                "schema": "local.storage-native-any-PG-query-scope.v1",
                "profile": "postgresql",
                "actual_native_query": query,
                "actual_native_query_utf8_bytes": query.len(),
                "actual_native_query_sha256": query_digest(query),
                "query_match_scope": scope,
                "activity_query_prefix_truncated": scope == "exact-observed-PG-wire-prefix-1023",
                "full_wire_returning_observed": scope == "exact-complete-PG-wire-template",
                "expected_complete_wire_template_sha256": query_digest(PG_ANY_RESERVATION_WIRE),
                "actual_worker_pid": worker_pid,
                "exact_owned_holder_pid": holder_pid,
                "actual_named_worker_application": application,
                "wire_literal_normalized": false,
                "accepted": false,
                "full_protocol_parity": false
            });
            let encoded = serde_json::to_string(&packet).unwrap();
            assert!(encoded.len() <= 32768);
            println!("{encoded}");
        }

        pub(super) fn log_observed_cr_query(
            query: &str,
            worker_txn: &str,
            holder_txn: &str,
            application: &str,
        ) {
            let scope = cr_observed_scope(query).unwrap();
            assert!(query.len() <= 8192 && application.len() <= 128);
            assert!(!worker_txn.is_empty() && worker_txn.len() <= 128);
            assert!(!holder_txn.is_empty() && holder_txn.len() <= 128);
            assert_ne!(worker_txn, holder_txn);
            let packet = serde_json::json!({
                "schema": "local.storage-native-any-CR-query-scope.v1",
                "profile": "cockroachdb",
                "actual_native_query": query,
                "actual_native_query_utf8_bytes": query.len(),
                "actual_native_query_sha256": query_digest(query),
                "query_match_scope": scope,
                "activity_query_prefix_truncated": scope == "exact-observed-CR-wire-prefix-997-ellipsis",
                "full_wire_returning_observed": scope == "exact-complete-CR-wire-template",
                "expected_complete_wire_template_sha256": cr_reservation_scope(query).map(|_| query_digest(CR_ANY_RESERVATION_WIRE)),
                "actual_worker_transaction": worker_txn,
                "exact_owned_holder_transaction": holder_txn,
                "actual_named_worker_application": application,
                "wire_literal_normalized": false,
                "accepted": false,
                "full_protocol_parity": false
            });
            let encoded = serde_json::to_string(&packet).unwrap();
            assert!(encoded.len() <= 32768);
            println!("{encoded}");
        }

        fn application(case: &str, role: &str, stamp: u128) -> String {
            let label = match case {
                "existing_permission_wait_rollback" => "permission",
                "concurrent_creator_commit_true_prior" => "creator",
                "unknown_matching_token_noop" => "noop",
                "unknown_surrogate_native_priority" => "surrogate",
                _ => panic!("unknown Any fixture application label"),
            };
            let name = format!("native_any_{label}_{role}_{stamp:x}");
            assert!(name.len() < 64);
            assert!(name
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_'));
            name
        }
        fn primary_catalog(control: &mut Client, profile: DatabaseProfile) -> (i64, i64, String) {
            let rows = match profile {
                DatabaseProfile::PostgreSql => control.query(
                    "SELECT c.oid::INT8,i.indexrelid::INT8,ci.relname::TEXT FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace JOIN pg_catalog.pg_index i ON i.indrelid=c.oid JOIN pg_catalog.pg_class ci ON ci.oid=i.indexrelid WHERE n.nspname='public' AND c.relname='trnm_storage_objects' AND i.indisprimary LIMIT 2", &[]),
                DatabaseProfile::CockroachDb => {
                    control.batch_execute("SET allow_unsafe_internals=true").unwrap();
                    control.query("SELECT descriptor_id::INT8,index_id::INT8,index_name::TEXT FROM crdb_internal.table_indexes WHERE descriptor_name='trnm_storage_objects' AND index_type='primary' LIMIT 2", &[])
                }
            }.unwrap_or_else(|error| panic!("Any actual primary catalog failed sqlstate={}", sqlstate(&error)));
            assert_eq!(rows.len(), 1, "Any native primary catalog ambiguous");
            let found = (
                rows[0].get::<_, i64>(0),
                rows[0].get::<_, i64>(1),
                rows[0].get::<_, String>(2),
            );
            assert!(found.0 > 0 && found.1 > 0 && !found.2.is_empty() && found.2.len() <= 128);
            if matches!(profile, DatabaseProfile::CockroachDb) {
                assert_eq!(found.1, 1);
            }
            found
        }
        fn fresh_granted(
            control: &mut Client,
            key: &StorageObjectKey,
            holder: &AnyHolder,
        ) -> serde_json::Value {
            match &holder.identity {
                Holder::PostgreSql { pid, xid } => {
                    let rows=control.query("SELECT a.pid::INTEGER,a.backend_xid::TEXT,a.state::TEXT,l.locktype::TEXT,l.transactionid::TEXT,l.mode::TEXT,l.granted FROM pg_catalog.pg_stat_activity a JOIN pg_catalog.pg_locks l ON l.pid=a.pid WHERE a.datname=current_database() AND a.pid=$1 AND l.locktype='transactionid' AND l.transactionid::TEXT=$2 AND l.mode='ExclusiveLock' AND l.granted LIMIT 2", &[pid,xid]).unwrap();
                    assert_eq!(rows.len(), 1, "Any same owned PG holder no longer granted");
                    let r = &rows[0];
                    assert_eq!(r.get::<_, i32>(0), *pid);
                    assert_eq!(r.get::<_, String>(1), *xid);
                    assert_eq!(r.get::<_, String>(2), "idle in transaction");
                    assert_eq!(r.get::<_, String>(3), "transactionid");
                    assert_eq!(r.get::<_, String>(4), *xid);
                    assert_eq!(r.get::<_, String>(5), "ExclusiveLock");
                    assert!(r.get::<_, bool>(6));
                    serde_json::json!([[
                        pid,
                        xid,
                        "idle in transaction",
                        "transactionid",
                        xid,
                        "ExclusiveLock",
                        true
                    ]])
                }
                Holder::CockroachDb { txn, keys } => {
                    let prefix = format!("/Table/{}/1/", holder.table_id);
                    let rows=control.query("SELECT txn_id::TEXT,lock_key_pretty::TEXT,table_id::INT8,index_name::TEXT,lock_strength::TEXT,isolation_level::TEXT FROM crdb_internal.cluster_locks WHERE database_name=current_database() AND schema_name='public' AND table_name='trnm_storage_objects' AND table_id::INT8=$1 AND granted AND txn_id::TEXT=$2 AND lock_key_pretty=ANY($3::TEXT[]) ORDER BY lock_key_pretty LIMIT 64", &[&holder.table_id,txn,keys]).unwrap();
                    assert_eq!(
                        rows.len(),
                        keys.len(),
                        "Any same native PRIMARY holder changed"
                    );
                    let mut raw = Vec::new();
                    for row in rows {
                        let t: String = row.get(0);
                        let k: String = row.get(1);
                        let id: i64 = row.get(2);
                        let name: Option<String> = row.get(3);
                        let strength: String = row.get(4);
                        let isolation: String = row.get(5);
                        assert_eq!(t, *txn);
                        assert_eq!(id, holder.table_id);
                        assert!(
                            keys.contains(&k)
                                && k.starts_with(&prefix)
                                && k.contains(key.collection())
                                && k.contains(key.key())
                        );
                        assert_eq!(strength, "Exclusive");
                        assert_eq!(isolation, "SERIALIZABLE");
                        raw.push(serde_json::json!([t, k, id, name, strength, isolation]));
                    }
                    serde_json::Value::Array(raw)
                }
            }
        }
        fn capture_holder(
            tx: &mut Transaction<'_>,
            control: &mut Client,
            profile: DatabaseProfile,
            key: &StorageObjectKey,
            primary: &(i64, i64, String),
        ) -> AnyHolder {
            let identity = match profile {
                DatabaseProfile::PostgreSql => {
                    let row = tx
                        .query_one("SELECT pg_backend_pid(),pg_current_xact_id()::TEXT", &[])
                        .unwrap();
                    Holder::PostgreSql {
                        pid: row.get(0),
                        xid: row.get(1),
                    }
                }
                DatabaseProfile::CockroachDb => {
                    let prefix = format!("/Table/{}/1/", primary.0);
                    let rows=control.query("SELECT txn_id::TEXT,lock_key_pretty::TEXT FROM crdb_internal.cluster_locks WHERE database_name=current_database() AND schema_name='public' AND table_name='trnm_storage_objects' AND table_id::INT8=$1 AND granted AND strpos(lock_key_pretty,$2)=1 AND strpos(lock_key_pretty,$3)>0 AND strpos(lock_key_pretty,$4)>0 ORDER BY txn_id::TEXT,lock_key_pretty LIMIT 64",&[&primary.0,&prefix,&key.collection(),&key.key()]).unwrap();
                    assert!(
                        !rows.is_empty() && rows.len() < 64,
                        "Any owned PRIMARY holder absent"
                    );
                    let txn: String = rows[0].get(0);
                    assert!(!txn.is_empty() && txn.len() <= 128);
                    let mut keys = Vec::new();
                    for row in rows {
                        assert_eq!(row.get::<_, String>(0), txn);
                        let pretty: String = row.get(1);
                        assert!(pretty.starts_with(&prefix) && pretty.len() <= 8192);
                        keys.push(pretty);
                    }
                    keys.sort();
                    keys.dedup();
                    Holder::CockroachDb { txn, keys }
                }
            };
            let mut holder = AnyHolder {
                identity,
                table_id: primary.0,
                index_id: primary.1,
                primary_name: primary.2.clone(),
                actual_granted_rows: serde_json::Value::Null,
            };
            holder.actual_granted_rows = fresh_granted(control, key, &holder);
            holder
        }
        fn hold_primary(
            tx: &mut Transaction<'_>,
            profile: DatabaseProfile,
            key: &StorageObjectKey,
            primary: &(i64, i64, String),
        ) {
            tx.batch_execute("SET LOCAL idle_in_transaction_session_timeout='10s'")
                .unwrap();
            assert!(primary
                .2
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_'));
            let relation = match profile {
                DatabaseProfile::PostgreSql => "public.trnm_storage_objects".to_owned(),
                DatabaseProfile::CockroachDb => {
                    format!("public.trnm_storage_objects@\"{}\"", primary.2)
                }
            };
            let row=tx.query_one(&format!("SELECT collection,object_key,user_id FROM {relation} WHERE collection=$1 AND object_key=$2 AND user_id=$3 FOR UPDATE"),&[&key.collection(),&key.key(),&key.user_id().as_bytes().as_slice()]).unwrap();
            assert_eq!(row.get::<_, String>(0), key.collection());
            assert_eq!(row.get::<_, String>(1), key.key());
            assert_eq!(
                row.get::<_, Vec<u8>>(2).as_slice(),
                key.user_id().as_bytes().as_slice()
            );
        }
        fn observe_wait(
            control: &mut Client,
            application: &str,
            holder: &AnyHolder,
            case: AnyCase,
        ) -> Option<AnyWait> {
            match &holder.identity {
                Holder::PostgreSql { pid, xid } => {
                    let rows=control.query("SELECT a.pid::INTEGER,a.backend_xid::TEXT,a.query::TEXT,a.state::TEXT,a.wait_event_type::TEXT,a.wait_event::TEXT,pg_blocking_pids(a.pid),l.locktype::TEXT,l.transactionid::TEXT,l.granted,h.pid::INTEGER,h.mode::TEXT,h.granted FROM pg_catalog.pg_stat_activity a JOIN pg_catalog.pg_locks l ON l.pid=a.pid JOIN pg_catalog.pg_locks h ON h.locktype=l.locktype AND h.transactionid=l.transactionid WHERE a.datname=current_database() AND a.application_name=$1 AND a.pid<>$2 AND $2=ANY(pg_blocking_pids(a.pid)) AND a.wait_event_type='Lock' AND l.locktype='transactionid' AND NOT l.granted AND h.pid=$2 AND h.transactionid::TEXT=$3 AND h.mode='ExclusiveLock' AND h.granted LIMIT 2",&[&application,pid,xid]).unwrap();
                    if rows.is_empty() {
                        return None;
                    }
                    assert_eq!(rows.len(), 1, "Any native PG worker ambiguous");
                    let row = &rows[0];
                    let worker: i32 = row.get(0);
                    let worker_xid: Option<String> = row.get(1);
                    let query: String = row.get(2);
                    let phase: String = row.get(3);
                    let wait_type: String = row.get(4);
                    let wait: Option<String> = row.get(5);
                    let blocking: Vec<i32> = row.get(6);
                    let kind: String = row.get(7);
                    let blocked: String = row.get(8);
                    let granted: bool = row.get(9);
                    let blocker: i32 = row.get(10);
                    let mode: String = row.get(11);
                    let blocker_granted: bool = row.get(12);
                    assert_ne!(worker, *pid);
                    if let Some(actual_xid) = &worker_xid {
                        assert_ne!(actual_xid, xid);
                    }
                    assert_eq!(wait_type, "Lock");
                    assert!(blocking.contains(pid));
                    assert_eq!(kind, "transactionid");
                    assert_eq!(blocked, *xid);
                    assert!(!granted);
                    assert_eq!(blocker, *pid);
                    assert_eq!(mode, "ExclusiveLock");
                    assert!(blocker_granted);
                    assert!(
                        observed_case_query(DatabaseProfile::PostgreSql, case, &query),
                        "Any PG waiter was not the native INSERT disabled-conflict reservation"
                    );
                    let rows = serde_json::json!([
                        worker,
                        worker_xid,
                        query,
                        phase,
                        wait_type,
                        wait,
                        blocking,
                        kind,
                        blocked,
                        granted,
                        blocker,
                        mode,
                        blocker_granted
                    ]);
                    println!(
                        "{}",
                        serde_json::json!({"schema":"local.storage-native-any-lock-query.v1","profile":"postgresql","actual_native_query":query,"actual_native_query_sha256":query_digest(&query),"native_fields":rows,"worker_isolation_observed":null,"worker_isolation_source":"unobserved-public-repository-has-no-same-transaction-show-seam","query_match_scope":pg_reservation_scope(&query).unwrap(),"activity_query_prefix_truncated":pg_reservation_scope(&query)==Some("exact-observed-PG-wire-prefix-1023"),"full_wire_returning_observed":pg_reservation_scope(&query)==Some("exact-complete-PG-wire-template"),"expected_complete_wire_template_sha256":query_digest(PG_ANY_RESERVATION_WIRE),"worker_xid_nullable":true,"hidden_batch_sqlstate":null,"wire_literal_normalized":false,"accepted":false})
                    );
                    Some(AnyWait {
                        proof: WaitProof {
                            worker: worker.to_string(),
                            blocker: pid.to_string(),
                            query_sha256: query_digest(&query),
                        },
                        actual_rows: rows,
                        worker_isolation: None,
                        actual_query_kind: observed_query_kind(DatabaseProfile::PostgreSql, &query),
                    })
                }
                Holder::CockroachDb { txn, keys } => {
                    let rows=control.query("SELECT q.query_id::TEXT,q.txn_id::TEXT,q.query::TEXT,q.phase::TEXT,l.lock_key_pretty::TEXT,l.granted,l.contended,h.txn_id::TEXT,h.granted,l.table_id::INT8,l.index_name::TEXT,h.index_name::TEXT,l.isolation_level::TEXT FROM crdb_internal.cluster_queries q JOIN crdb_internal.cluster_locks l ON q.txn_id::TEXT=l.txn_id::TEXT JOIN crdb_internal.cluster_locks h ON h.database_name=l.database_name AND h.schema_name=l.schema_name AND h.table_name=l.table_name AND h.lock_key_pretty=l.lock_key_pretty WHERE q.application_name=$1 AND l.database_name=current_database() AND l.schema_name='public' AND l.table_name='trnm_storage_objects' AND l.table_id::INT8=$4 AND l.lock_key_pretty=ANY($2::TEXT[]) AND NOT l.granted AND l.contended AND l.txn_id::TEXT<>$3 AND h.txn_id::TEXT=$3 AND h.granted ORDER BY q.query_id::TEXT,l.lock_key_pretty LIMIT 64",&[&application,keys,txn,&holder.table_id]).unwrap();
                    if rows.is_empty() {
                        return None;
                    }
                    assert!(rows.len() < 64);
                    let worker: String = rows[0].get(1);
                    let query: String = rows[0].get(2);
                    let isolation: Option<String> = rows[0].get(12);
                    assert_ne!(worker, *txn);
                    assert!(!worker.is_empty() && worker.len() <= 128);
                    assert!(
                        observed_case_query(DatabaseProfile::CockroachDb, case, &query),
                        "Any CR native wait query did not match the profile/case acquisition"
                    );
                    if let Some(level) = &isolation {
                        assert!(!level.is_empty() && level.len() <= 64);
                    }
                    let mut raw = Vec::new();
                    for row in rows {
                        let id: String = row.get(0);
                        let t: String = row.get(1);
                        let q: String = row.get(2);
                        let phase: String = row.get(3);
                        let key: String = row.get(4);
                        let granted: bool = row.get(5);
                        let contended: bool = row.get(6);
                        let h: String = row.get(7);
                        let hg: bool = row.get(8);
                        let table: i64 = row.get(9);
                        let name: Option<String> = row.get(10);
                        let hname: Option<String> = row.get(11);
                        let level: Option<String> = row.get(12);
                        assert!(!id.is_empty() && id.len() <= 128 && phase.len() <= 128);
                        assert_eq!(t, worker);
                        assert_eq!(q, query);
                        assert!(
                            keys.contains(&key)
                                && key.starts_with(&format!("/Table/{}/1/", holder.table_id))
                        );
                        assert!(!granted && contended && hg);
                        assert_eq!(h, *txn);
                        assert_eq!(table, holder.table_id);
                        assert_eq!(level, isolation);
                        raw.push(serde_json::json!([
                            id, t, q, phase, key, granted, contended, h, hg, table, name, hname,
                            level
                        ]));
                    }
                    let raw = serde_json::Value::Array(raw);
                    println!(
                        "{}",
                        serde_json::json!({"schema":"local.storage-native-any-lock-query.v1","profile":"cockroachdb","actual_native_query":query,"actual_native_query_sha256":query_digest(&query),"native_fields":raw,"worker_isolation_observed":isolation,"worker_isolation_source":"same-worker-native-cluster-locks","query_match_scope":cr_observed_scope(&query).unwrap(),"activity_query_prefix_truncated":cr_reservation_scope(&query)==Some("exact-observed-CR-wire-prefix-997-ellipsis"),"full_wire_returning_observed":cr_reservation_scope(&query)==Some("exact-complete-CR-wire-template"),"expected_complete_wire_template_sha256":cr_reservation_scope(&query).map(|_|query_digest(CR_ANY_RESERVATION_WIRE)),"index_name_raw_preserved":true,"hidden_batch_sqlstate":null,"wire_literal_normalized":false,"accepted":false})
                    );
                    Some(AnyWait {
                        proof: WaitProof {
                            worker,
                            blocker: txn.clone(),
                            query_sha256: query_digest(&query),
                        },
                        actual_rows: raw,
                        worker_isolation: isolation,
                        actual_query_kind: observed_query_kind(
                            DatabaseProfile::CockroachDb,
                            &query,
                        ),
                    })
                }
            }
        }
        fn wait_for_reservation(
            control: &mut Client,
            application: &str,
            holder: &AnyHolder,
            case: AnyCase,
            receive: &Receiver<Outcome>,
            outcome: &mut Option<Outcome>,
            started: Instant,
        ) -> AnyWait {
            loop {
                assert!(
                    started.elapsed() < CAUSAL_BUDGET,
                    "Any native wait was not observed within original four-second causal budget"
                );
                if let Some(proof) = observe_wait(control, application, holder, case) {
                    assert!(started.elapsed() < CAUSAL_BUDGET);
                    return proof;
                }
                match receive.try_recv() {
                    Ok(actual) => {
                        *outcome = Some(actual);
                        panic!("Any worker completed before actual reservation wait");
                    }
                    Err(TryRecvError::Disconnected) => {
                        panic!("Any worker disconnected before native wait")
                    }
                    Err(TryRecvError::Empty) => std::thread::yield_now(),
                }
            }
        }
        fn seed_unknown(control: &mut Client, key: &StorageObjectKey, token_request: &[u8]) {
            // Controlled synthetic unknown-origin data. The manifest digest is
            // only a nonzero synthetic fixture value, never import custody.
            let projected = native(control, C);
            let sha = IntegrityDigest::from_value(&projected).get();
            let token = ContentVersion::from_value(token_request);
            let manifest = [0xc7_u8; 32];
            let create = StorageTimestamp::new(-1, 123_456_000).unwrap();
            let update = StorageTimestamp::new(951_827_696, 654_321_000).unwrap();
            assert_eq!(control.execute("UPDATE public.trnm_storage_objects SET value_jsonb=$4::TEXT::JSONB,value_bytes=NULL,version_digest=NULL,public_version=$5,value_projection_digest=$6,value_origin='nakama-export-unknown-request',source_manifest_digest=$7,create_time=$8,update_time=$9 WHERE collection=$1 AND object_key=$2 AND user_id=$3",&[&key.collection(),&key.key(),&key.user_id().as_bytes().as_slice(),&std::str::from_utf8(C).unwrap(),&token.as_str(),&sha.as_bytes().as_slice(),&manifest.as_slice(),&create,&update]).unwrap(),1);
        }
        fn creator_insert(
            tx: &mut Transaction<'_>,
            key: &StorageObjectKey,
            projected: &[u8],
        ) -> RawRow {
            tx.batch_execute("SET LOCAL idle_in_transaction_session_timeout='10s'")
                .unwrap();
            let request_digest = IntegrityDigest::from_value(A).get();
            let projection_digest = IntegrityDigest::from_value(projected).get();
            let token = ContentVersion::from_value(A);
            let read = ReadPermission::OWNER.get();
            let write = WritePermission::OWNER.get();
            let clock = 601_i64;
            let row=tx.query_one("INSERT INTO public.trnm_storage_objects (collection,object_key,user_id,value_jsonb,value_bytes,version_digest,public_version,value_projection_digest,value_origin,source_manifest_digest,read_permission,write_permission,updated_at_ms,create_time,update_time) VALUES ($1,$2,$3,$4::TEXT::JSONB,$5,$6,$7,$8,'write-request-bytes',NULL,$9,$10,$11,pg_catalog.now(),pg_catalog.now()) RETURNING collection,object_key,user_id,value_jsonb::TEXT,value_bytes,version_digest,public_version::TEXT,value_projection_digest,value_origin,source_manifest_digest,read_permission,write_permission,updated_at_ms,create_time,update_time",&[&key.collection(),&key.key(),&key.user_id().as_bytes().as_slice(),&std::str::from_utf8(A).unwrap(),&A,&request_digest.as_bytes().as_slice(),&token.as_str(),&projection_digest.as_bytes().as_slice(),&read,&write,&clock]).unwrap_or_else(|error|panic!("Any own creator INSERT failed actual_sqlstate={}",sqlstate(&error)));
            RawRow {
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
            }
        }
        fn tuple_json(row: &RawRow) -> serde_json::Value {
            let hex = |bytes: &[u8]| {
                const HEX: &[u8; 16] = b"0123456789abcdef";
                let mut out = String::with_capacity(bytes.len() * 2);
                for byte in bytes {
                    out.push(char::from(HEX[usize::from(byte >> 4)]));
                    out.push(char::from(HEX[usize::from(byte & 15)]));
                }
                out
            };
            let time = |value: Option<StorageTimestamp>| {
                value.map(|timestamp| serde_json::json!([timestamp.seconds, timestamp.nanos]))
            };
            serde_json::json!([
                row.collection,
                row.key,
                hex(&row.owner),
                row.native,
                row.raw.as_deref().map(hex),
                row.request_sha.as_deref().map(hex),
                row.public_version,
                hex(&row.projection_sha),
                row.origin,
                row.manifest_sha.as_deref().map(hex),
                row.read,
                row.write,
                row.updated_at_ms,
                time(row.create),
                time(row.update)
            ])
        }
        fn log_actual(profile: DatabaseProfile, case: &str, outcome: &Outcome, late: bool) {
            let (code, reason, retry, receipts) = match &outcome.batch {
                Ok(receipts) => (None, None, None, Some(receipts.len())),
                Err(error) => (
                    Some(format!("{:?}", error.code())),
                    Some(error.reason()),
                    Some(format!("{:?}", error.retry())),
                    None,
                ),
            };
            println!(
                "{}",
                serde_json::json!({"schema":"local.storage-native-any-actual-result.v1","profile":profile.metadata_value(),"case":case,"actual_domain":code,"actual_reason":reason,"actual_retry":retry,"receipt_count":receipts,"hidden_batch_sqlstate":null,"same_lease_readable":outcome.reused.as_ref().ok(),"late_after_causal_release":late,"accepted":false})
            );
        }
        fn exercise_case(url: &str, profile: DatabaseProfile, collection: &str, case: AnyCase) {
            let stamp = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            let key = |name: &str| StorageObjectKey::new(collection, name, OWNER).unwrap();
            let prelude = key("0-prelude");
            let target = key("a-target");
            let probe = key("y-probe");
            let later = key("z-held");
            let mut control = bounded_control(url);
            let primary = primary_catalog(&mut control, profile);
            let mut seed_repo = bounded_repository(url, profile);
            let mut seeds = vec![
                write(&prelude, A, VersionCheck::Any, WritePermission::OWNER),
                write(&probe, A, VersionCheck::Any, WritePermission::OWNER),
                write(&later, A, VersionCheck::Any, WritePermission::OWNER),
            ];
            if !matches!(case, AnyCase::CreatorCommit) {
                seeds.push(write(
                    &target,
                    A,
                    VersionCheck::Any,
                    if matches!(case, AnyCase::ExistingPermission) {
                        WritePermission::NONE
                    } else {
                        WritePermission::OWNER
                    },
                ));
            }
            let seeded = seed_repo
                .apply_storage_batch_nakama_with_metadata(
                    StorageActor::Server,
                    &seeds,
                    601,
                    StorageNakamaBatchKind::Write,
                )
                .unwrap();
            assert_eq!(seeded.len(), seeds.len());
            for (operation, receipt) in seeds.iter().zip(&seeded) {
                let StorageBatchOperation::Write(write) = operation else {
                    unreachable!()
                };
                assert_known_row(
                    &mut control,
                    &write.key,
                    A,
                    write.read_permission,
                    write.write_permission,
                    601,
                    receipt.times,
                );
            }
            if matches!(case, AnyCase::UnknownNoop | AnyCase::UnknownSurrogate) {
                seed_unknown(
                    &mut control,
                    &target,
                    if matches!(case, AnyCase::UnknownSurrogate) {
                        SURROGATE
                    } else {
                        B
                    },
                );
            }
            let before = snapshot(&mut control, collection);
            assert_eq!(
                before.len(),
                if matches!(case, AnyCase::CreatorCommit) {
                    3
                } else {
                    4
                }
            );
            let probe_before = seed_repo
                .read_storage_object_with_metadata(StorageActor::User(OWNER), &probe)
                .unwrap();
            let mut worker_repo = bounded_repository(url, profile);
            let worker_name = application(case.name(), "w", stamp);
            worker_repo
                .execute_migration_batch(&format!("SET application_name='{worker_name}'"))
                .unwrap();
            // This independent native render is outside the business worker.
            // The worker performs no storage-absence read before its Any batch.
            let prospective_creator = native(&mut control, A);
            let mut holder_control = bounded_control(url);
            control_application(&mut holder_control, &application(case.name(), "h", stamp));
            let started = Instant::now();
            let mut held = Some(holder_control.transaction().unwrap());
            let creator = if matches!(case, AnyCase::CreatorCommit) {
                Some(creator_insert(
                    held.as_mut().unwrap(),
                    &target,
                    &prospective_creator,
                ))
            } else {
                None
            };
            let held_key = if matches!(case, AnyCase::UnknownSurrogate) {
                &later
            } else {
                &target
            };
            if creator.is_none() {
                hold_primary(held.as_mut().unwrap(), profile, held_key, &primary);
            }
            let holder = capture_holder(
                held.as_mut().unwrap(),
                &mut control,
                profile,
                held_key,
                &primary,
            );
            assert!(
                started.elapsed() < CAUSAL_BUDGET,
                "Any holder setup exhausted original causal budget"
            );
            let operations = match case {
                AnyCase::ExistingPermission => vec![
                    write(&prelude, B, VersionCheck::Any, WritePermission::OWNER),
                    write(&target, B, VersionCheck::Any, WritePermission::OWNER),
                ],
                AnyCase::CreatorCommit | AnyCase::UnknownNoop => {
                    vec![write(&target, B, VersionCheck::Any, WritePermission::OWNER)]
                }
                AnyCase::UnknownSurrogate => vec![
                    write(
                        &target,
                        SURROGATE,
                        VersionCheck::Any,
                        WritePermission::OWNER,
                    ),
                    write(&later, B, VersionCheck::Any, WritePermission::OWNER),
                ],
            };
            let worker_operations = operations.clone();
            let worker_probe = probe.clone();
            let (send, receive) = mpsc::sync_channel(1);
            let worker = std::thread::spawn(move || {
                let batch = worker_repo.apply_storage_batch_nakama_with_metadata(
                    StorageActor::User(OWNER),
                    &worker_operations,
                    602,
                    StorageNakamaBatchKind::Write,
                );
                let completed_at = Instant::now();
                let reused = worker_repo
                    .read_storage_object_with_metadata(StorageActor::User(OWNER), &worker_probe)
                    .map(|actual| actual == probe_before);
                send.send(Outcome {
                    batch,
                    reused,
                    completed_at,
                })
                .expect("Any result receiver unavailable");
            });
            let mut outcome = None;
            let mut actual_wait = None;
            let mut release_ok = true;
            let mut explicit_release = None;
            let body = catch_unwind(AssertUnwindSafe(|| {
                if case.expects_wait() {
                    let wait = wait_for_reservation(
                        &mut control,
                        &worker_name,
                        &holder,
                        case,
                        &receive,
                        &mut outcome,
                        started,
                    );
                    println!("nakama_native_any_lock_observed profile={} case={} worker={} blocker={} actual_query_sha256={} proof=native-primary-lock-view",profile.metadata_value(),case.name(),wait.proof.worker,wait.proof.blocker,wait.proof.query_sha256);
                    actual_wait = Some(wait);
                    if matches!(case, AnyCase::CreatorCommit) {
                        let transaction = held.take().unwrap();
                        transaction.commit().unwrap_or_else(|error| {
                            panic!(
                                "Any own creator COMMIT failed actual_sqlstate={}",
                                sqlstate(&error)
                            )
                        });
                        explicit_release = Some("COMMIT");
                    } else {
                        release_ok &=
                            release(&mut held, profile, case.name(), "Any-causal-release");
                        assert!(release_ok);
                        explicit_release = Some("ROLLBACK");
                    }
                }
                let actual = receive
                    .recv_timeout(CAUSAL_BUDGET.saturating_sub(started.elapsed()))
                    .expect(
                        "Any public repository actual result absent within original causal budget",
                    );
                log_actual(profile, case.name(), &actual, false);
                outcome = Some(actual);
                let actual = outcome.as_ref().unwrap();
                assert!(actual.completed_at.duration_since(started) < CAUSAL_BUDGET);
                assert_eq!(actual.reused, Ok(true), "Any same owned lease unreadable");
                if case.commits() {
                    assert_eq!(
                        actual.batch.as_ref().map(Vec::len),
                        Ok(operations.len()),
                        "Any expected committed step receipts were not returned"
                    );
                } else {
                    let expected = if matches!(case, AnyCase::ExistingPermission) {
                        DomainError::new(
                            StableCode::PermissionDenied,
                            "storage_write_permission_denied",
                            RetryClass::Never,
                        )
                    } else {
                        DomainError::new(
                            StableCode::InvalidArgument,
                            "database_constraint_violation",
                            RetryClass::Never,
                        )
                    };
                    assert_eq!(
                        actual.batch.as_ref().err(),
                        Some(&expected),
                        "Any native/semantic error changed or returned partial receipts"
                    );
                }
                if !case.expects_wait() {
                    let current = fresh_granted(&mut control, held_key, &holder);
                    assert_eq!(
                        current, holder.actual_granted_rows,
                        "Any native priority completed only after the owned holder changed"
                    );
                }
                assert!(
                    started.elapsed() < CAUSAL_BUDGET,
                    "Any causal readback exceeded original budget"
                );
            }));
            let settling_started = Instant::now();
            if held.is_some() {
                release_ok &= release(&mut held, profile, case.name(), "Any-cleanup");
                if explicit_release.is_none() {
                    explicit_release = Some("ROLLBACK-cleanup");
                }
            }
            let joined = settle(
                worker,
                &receive,
                &mut outcome,
                settling_started,
                profile,
                case.name(),
            );
            let after_read = catch_unwind(AssertUnwindSafe(|| snapshot(&mut control, collection)));
            let after = after_read.as_ref().ok();
            let state =
                if release_ok && joined.as_ref().is_some_and(Result::is_ok) && after.is_some() {
                    Some(catch_unwind(AssertUnwindSafe(|| {
                        if matches!(case, AnyCase::CreatorCommit) {
                            let actual = outcome.as_ref().unwrap().batch.as_ref().unwrap();
                            assert_eq!(actual.len(), 1);
                            let receipt = &actual[0];
                            let old = creator.as_ref().unwrap();
                            assert_eq!(receipt.receipt.key, target);
                            assert_eq!(
                                receipt
                                    .receipt
                                    .previous_version
                                    .as_ref()
                                    .map(|value| value.as_str()),
                                Some(old.public_version.as_str())
                            );
                            assert_eq!(
                                receipt.receipt.current_version,
                                Some(ContentVersion::from_value(B))
                            );
                            assert_eq!(receipt.times.create, old.create);
                            assert!(receipt.times.update.is_some());
                            let next = expected_known_row(
                                &mut control,
                                &target,
                                B,
                                ReadPermission::OWNER,
                                WritePermission::OWNER,
                                602,
                                receipt.times,
                            );
                            let mut expected = before.clone();
                            expected.push(next);
                            expected.sort_by(|a, b| {
                                (&a.collection, &a.key, &a.owner).cmp(&(
                                    &b.collection,
                                    &b.key,
                                    &b.owner,
                                ))
                            });
                            assert_eq!(
                                after.unwrap(),
                                &expected,
                                "Any concurrent creator prior/creation/full15 state changed"
                            );
                        } else {
                            assert_eq!(
                                after.unwrap(),
                                &before,
                                "Any rejected/no-op changed one of four actual raw15 tuples"
                            );
                            if matches!(case, AnyCase::UnknownNoop) {
                                let receipts = outcome.as_ref().unwrap().batch.as_ref().unwrap();
                                assert_eq!(receipts.len(), 1);
                                let old =
                                    before.iter().find(|row| row.key == target.key()).unwrap();
                                assert_eq!(receipts[0].receipt.key, target);
                                assert_eq!(
                                    receipts[0]
                                        .receipt
                                        .previous_version
                                        .as_ref()
                                        .map(|version| version.as_str()),
                                    Some(old.public_version.as_str())
                                );
                                assert_eq!(
                                    receipts[0].receipt.current_version,
                                    Some(ContentVersion::from_value(B))
                                );
                                assert_eq!(
                                    receipts[0].times,
                                    StorageTimes {
                                        create: old.create,
                                        update: old.update
                                    }
                                );
                                assert!(
                                    old.raw.is_none()
                                        && old.request_sha.is_none()
                                        && old.manifest_sha.is_some()
                                );
                                assert_ne!(
                                    old.native,
                                    String::from_utf8(native(&mut control, B)).unwrap()
                                );
                            }
                        }
                    })))
                } else {
                    None
                };
            let receipts=outcome.as_ref().and_then(|actual|actual.batch.as_ref().ok()).map(|receipts|receipts.iter().map(|receipt|serde_json::json!({"key":receipt.receipt.key.key(),"previous":receipt.receipt.previous_version.as_ref().map(|version|version.as_str()),"current":receipt.receipt.current_version.as_ref().map(|version|version.as_str()),"create":receipt.times.create.map(|time|[time.seconds,i64::from(time.nanos)]),"update":receipt.times.update.map(|time|[time.seconds,i64::from(time.nanos)])})).collect::<Vec<_>>());
            let packet = serde_json::json!({"schema":"local.storage-native-any-state.v1","profile":profile.metadata_value(),"case":case.name(),"fields":15,"before":before.iter().map(tuple_json).collect::<Vec<_>>(),"after":after.map(|rows|rows.iter().map(tuple_json).collect::<Vec<_>>()),"creator_uncommitted_actual_row":creator.as_ref().map(tuple_json),"holder_actual_granted_rows":holder.actual_granted_rows,"actual_primary":[holder.table_id,holder.index_id,holder.primary_name],"actual_wait_rows":actual_wait.as_ref().map(|proof|&proof.actual_rows),"actual_wait_query_kind":actual_wait.as_ref().map(|proof|proof.actual_query_kind),"cr_access_wire_sql":if matches!(profile,DatabaseProfile::CockroachDb){Some(CR_ACCESS_SQL)}else{None},"worker_isolation_observed":actual_wait.as_ref().and_then(|proof|proof.worker_isolation.as_deref()),"worker_isolation_source":if matches!(profile,DatabaseProfile::PostgreSql){"unobserved"}else{"same-worker-native-lock-view-or-unobserved"},"explicit_holder_release":explicit_release,"actual_receipts":receipts,"no_receipts":outcome.as_ref().map(|actual|actual.batch.is_err()),"causal_body_passed":body.is_ok(),"state_check_passed":state.as_ref().map(|result|result.is_ok()),"same_lease_readable":outcome.as_ref().and_then(|actual|actual.reused.as_ref().ok()).copied(),"hidden_batch_sqlstate":null,"synthetic_unknown_manifest_is_import_custody":false,"automatic_mutation_retry":false,"accepted":false,"compatibility_credit":false,"production_ready":false,"full_nakama_replacement":false});
            let encoded = serde_json::to_string(&packet).unwrap();
            assert!(
                encoded.len() <= 65536,
                "Any fixture raw tuple packet exceeded64KiB"
            );
            println!("{encoded}");
            if let Err(payload) = body {
                if let Some(actual) = &outcome {
                    log_actual(profile, case.name(), actual, true);
                }
                if after_read.is_err() || state.as_ref().is_some_and(Result::is_err) {
                    eprintln!("Any secondary full15 snapshot failure; primary assertion retained");
                }
                resume_unwind(payload);
            }
            match joined {
                Some(Ok(())) => {}
                Some(Err(payload)) => resume_unwind(payload),
                None => panic!("Any worker unsettled; external process-group deadline required"),
            }
            assert!(release_ok, "Any native holder cleanup failed");
            if let Err(payload) = after_read {
                resume_unwind(payload);
            }
            match state {
                Some(Ok(())) => {}
                Some(Err(payload)) => resume_unwind(payload),
                None => panic!("Any full15 tuple comparison unavailable"),
            }
            println!("nakama_native_any_case_executed profile={} case={} native_wait={} committed={} fields=15",profile.metadata_value(),case.name(),case.expects_wait(),case.commits());
        }

        fn step_receipts(url: &str, profile: DatabaseProfile, collection: &str) {
            let mut control = bounded_control(url);
            let mut repository = bounded_repository(url, profile);
            let known = StorageObjectKey::new(collection, "known-duplicate", OWNER).unwrap();
            let unknown = StorageObjectKey::new(collection, "unknown-duplicate", OWNER).unwrap();
            let aba = StorageObjectKey::new(collection, "own-aba-delete-reinsert", OWNER).unwrap();
            let seeds = [
                write(&known, A, VersionCheck::Any, WritePermission::OWNER),
                write(&unknown, A, VersionCheck::Any, WritePermission::OWNER),
                write(&aba, A, VersionCheck::Any, WritePermission::OWNER),
            ];
            let seed = repository
                .apply_storage_batch_nakama_with_metadata(
                    StorageActor::Server,
                    &seeds,
                    701,
                    StorageNakamaBatchKind::Write,
                )
                .unwrap();
            assert_eq!(seed.len(), 3);
            seed_unknown(&mut control, &unknown, B);
            let before = snapshot(&mut control, collection);
            assert_eq!(before.len(), 3);
            let ops = [
                write(&known, B, VersionCheck::Any, WritePermission::OWNER),
                write(&known, C, VersionCheck::Any, WritePermission::OWNER),
                write(&known, C, VersionCheck::Any, WritePermission::OWNER),
            ];
            let receipts = repository
                .apply_storage_batch_nakama_with_metadata(
                    StorageActor::User(OWNER),
                    &ops,
                    702,
                    StorageNakamaBatchKind::Write,
                )
                .unwrap();
            check_step_acks(&receipts, &[B, C, C]);
            assert_eq!(
                receipts[0]
                    .receipt
                    .previous_version
                    .as_ref()
                    .map(|value| value.as_str()),
                Some(ContentVersion::from_value(A).as_str())
            );
            assert_eq!(receipts[0].times.create, seed[0].times.create);
            assert_known_row(
                &mut control,
                &known,
                C,
                ReadPermission::OWNER,
                WritePermission::OWNER,
                702,
                receipts[2].times,
            );
            let unknown_before = snapshot(&mut control, collection)
                .into_iter()
                .find(|row| row.key == unknown.key())
                .unwrap();
            let ops = [
                write(&unknown, B, VersionCheck::Any, WritePermission::OWNER),
                write(&unknown, A, VersionCheck::Any, WritePermission::OWNER),
                write(&unknown, B, VersionCheck::Any, WritePermission::OWNER),
            ];
            let receipts = repository
                .apply_storage_batch_nakama_with_metadata(
                    StorageActor::User(OWNER),
                    &ops,
                    703,
                    StorageNakamaBatchKind::Write,
                )
                .unwrap();
            assert_eq!(receipts.len(), 3);
            for (index, value) in [B, A, B].into_iter().enumerate() {
                assert_eq!(receipts[index].receipt.key, unknown);
                assert_eq!(
                    receipts[index].receipt.current_version,
                    Some(ContentVersion::from_value(value))
                );
            }
            for (index, previous) in [B, B, A].into_iter().enumerate() {
                assert_eq!(
                    receipts[index]
                        .receipt
                        .previous_version
                        .as_ref()
                        .map(|value| value.as_str()),
                    Some(ContentVersion::from_value(previous).as_str())
                );
            }
            assert_eq!(
                receipts[0].times,
                StorageTimes {
                    create: unknown_before.create,
                    update: unknown_before.update
                }
            );
            assert_eq!(receipts[2].times.create, unknown_before.create);
            assert_known_row(
                &mut control,
                &unknown,
                B,
                ReadPermission::OWNER,
                WritePermission::OWNER,
                703,
                receipts[2].times,
            );
            let aba_before = snapshot(&mut control, collection)
                .into_iter()
                .find(|row| row.key == aba.key())
                .unwrap();
            let ops = [
                write(&aba, B, VersionCheck::Any, WritePermission::OWNER),
                write(&aba, A, VersionCheck::Any, WritePermission::OWNER),
            ];
            let receipts = repository
                .apply_storage_batch_nakama_with_metadata(
                    StorageActor::User(OWNER),
                    &ops,
                    704,
                    StorageNakamaBatchKind::Write,
                )
                .unwrap();
            check_step_acks(&receipts, &[B, A]);
            assert_eq!(
                receipts[0]
                    .receipt
                    .previous_version
                    .as_ref()
                    .map(|value| value.as_str()),
                Some(aba_before.public_version.as_str())
            );
            assert_eq!(receipts[1].times.create, aba_before.create);
            assert_known_row(
                &mut control,
                &aba,
                A,
                ReadPermission::OWNER,
                WritePermission::OWNER,
                704,
                receipts[1].times,
            );
            let removed = repository
                .apply_storage_batch_nakama_with_metadata(
                    StorageActor::User(OWNER),
                    &[delete(&aba, Some(ContentVersion::from_value(A).into()))],
                    705,
                    StorageNakamaBatchKind::Delete,
                )
                .unwrap();
            assert_eq!(removed.len(), 1);
            assert_eq!(
                removed[0]
                    .receipt
                    .previous_version
                    .as_ref()
                    .map(|value| value.as_str()),
                Some(ContentVersion::from_value(A).as_str())
            );
            assert!(removed[0].receipt.current_version.is_none());
            assert!(!snapshot(&mut control, collection)
                .iter()
                .any(|row| row.key == aba.key()));
            let reinserted = repository
                .apply_storage_batch_nakama_with_metadata(
                    StorageActor::User(OWNER),
                    &[write(&aba, C, VersionCheck::Any, WritePermission::OWNER)],
                    706,
                    StorageNakamaBatchKind::Write,
                )
                .unwrap();
            assert_eq!(reinserted.len(), 1);
            assert!(reinserted[0].receipt.previous_version.is_none());
            assert_eq!(reinserted[0].times.create, reinserted[0].times.update);
            assert_known_row(
                &mut control,
                &aba,
                C,
                ReadPermission::OWNER,
                WritePermission::OWNER,
                706,
                reinserted[0].times,
            );
            let after = snapshot(&mut control, collection);
            assert_eq!(after.len(), 3);
            println!(
                "{}",
                serde_json::json!({"schema":"local.storage-native-any-step-state.v1","profile":profile.metadata_value(),"fields":15,"known_duplicate_occurrences":3,"unknown_duplicate_occurrences":3,"aba_occurrences":2,"own_delete_reinsert_sequences":1,"before":before.iter().map(tuple_json).collect::<Vec<_>>(),"after":after.iter().map(tuple_json).collect::<Vec<_>>(),"synthetic_unknown_manifest_is_import_custody":false,"accepted":false,"compatibility_credit":false})
            );
            println!("nakama_native_any_steps_executed profile={} known_duplicate=3 unknown_duplicate=3 aba=2 own_delete_reinsert=1 fields=15",profile.metadata_value());
        }
        pub(super) fn exercise(url: &str, profile: DatabaseProfile, collection: &str) {
            for (index, case) in [
                AnyCase::ExistingPermission,
                AnyCase::CreatorCommit,
                AnyCase::UnknownNoop,
                AnyCase::UnknownSurrogate,
            ]
            .into_iter()
            .enumerate()
            {
                let namespace = format!("{collection}-any{index}");
                assert!(namespace.len() <= 128);
                let mut cleaner = bounded_control(url);
                cleanup(&mut cleaner, &namespace).unwrap();
                let result = catch_unwind(AssertUnwindSafe(|| {
                    exercise_case(url, profile, &namespace, case)
                }));
                preserve_primary_cleanup(result, cleanup(&mut cleaner, &namespace));
            }
            let namespace = format!("{collection}-any-steps");
            assert!(namespace.len() <= 128);
            let mut cleaner = bounded_control(url);
            cleanup(&mut cleaner, &namespace).unwrap();
            let result = catch_unwind(AssertUnwindSafe(|| step_receipts(url, profile, &namespace)));
            preserve_primary_cleanup(result, cleanup(&mut cleaner, &namespace));
            println!(
                "{}",
                serde_json::json!({"schema":"local.storage-native-any-matrix.v1","profile":profile.metadata_value(),"cases":4,"existing_permission_wait_rollback":1,"concurrent_creator_commit_true_prior":1,"unknown_matching_token_noop":1,"unknown_surrogate_native_priority":1,"known_duplicate_occurrences":3,"unknown_duplicate_occurrences":3,"aba_occurrences":2,"own_delete_reinsert_sequences":1,"tuple_fields":15,"pg_worker_isolation_observed":null,"synthetic_unknown_manifest_is_import_custody":false,"automatic_mutation_retry":false,"accepted":false,"compatibility_credit":false,"production_ready":false,"full_protocol_parity":false,"full_nakama_replacement":false})
            );
            println!("nakama_native_any_matrix_executed profile={} cases=4 permission_wait=1 creator_commit=1 unknown_noop=1 surrogate_priority=1 known_duplicate=3 unknown_duplicate=3 aba=2 own_delete_reinsert=1 fields=15 pg_worker_isolation=unobserved",profile.metadata_value());
        }
    }
}
