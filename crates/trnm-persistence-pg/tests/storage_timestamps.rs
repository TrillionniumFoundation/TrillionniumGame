//! Database-backed storage timestamp candidates. Use an isolated, fully migrated
//! database: this fixture temporarily faults and then restores its metadata row.
//! Execution is diagnostic and does not grant migration or compatibility credit.

use std::env;
use std::time::{SystemTime, UNIX_EPOCH};

use postgres::{Client, NoTls};
use trnm_contracts::{StableCode, UserId};
use trnm_persistence_pg::{
    ContentVersion, DatabaseProfile, IntegrityDigest, PgRepository, ReadPermission, StorageActor,
    StorageBatchOperation, StorageDeleteOperation, StorageObjectKey, StorageTimes,
    StorageTimestamp, StorageWriteOperation, VersionCheck, WritePermission,
};

const COLLECTION: &str = "storage-timestamps-contract";
const OWNER: UserId = UserId::new([0xc1; 16]);
const OTHER: UserId = UserId::new([0xc2; 16]);
const VALUE: &[u8] = br#"{"timestamp":true}"#;

fn live_environment() -> Option<(String, DatabaseProfile)> {
    let required = match env::var("TRNM_REQUIRE_LIVE_DATABASE") {
        Err(env::VarError::NotPresent) => false,
        Err(_) => panic!("cannot read TRNM_REQUIRE_LIVE_DATABASE"),
        Ok(value) if matches!(value.as_str(), "1" | "true" | "TRUE" | "yes" | "YES") => true,
        Ok(value) if matches!(value.as_str(), "0" | "false" | "FALSE" | "no" | "NO") => false,
        Ok(_) => panic!("TRNM_REQUIRE_LIVE_DATABASE must be a boolean flag"),
    };
    let database_url = match env::var("TRNM_DATABASE_URL") {
        Ok(value) if !value.is_empty() => value,
        Ok(_) | Err(env::VarError::NotPresent) if !required => return None,
        Ok(_) | Err(env::VarError::NotPresent) => panic!("required TRNM_DATABASE_URL is absent"),
        Err(_) => panic!("cannot read TRNM_DATABASE_URL"),
    };
    let profile = match env::var("TRNM_DATABASE_PROFILE").as_deref() {
        Ok("postgresql") => DatabaseProfile::PostgreSql,
        Ok("cockroachdb") => DatabaseProfile::CockroachDb,
        _ => panic!("TRNM_DATABASE_PROFILE must select the actual database profile"),
    };
    Some((database_url, profile))
}

fn key(name: &str, owner: UserId) -> StorageObjectKey {
    StorageObjectKey::new(COLLECTION, name, owner).unwrap()
}

fn write(
    key: &StorageObjectKey,
    expected: VersionCheck,
    read: ReadPermission,
    write: WritePermission,
) -> StorageBatchOperation {
    StorageBatchOperation::Write(StorageWriteOperation {
        key: key.clone(),
        value: VALUE.to_vec(),
        expected,
        read_permission: read,
        write_permission: write,
    })
}

fn owner_write(key: &StorageObjectKey, expected: VersionCheck) -> StorageBatchOperation {
    write(key, expected, ReadPermission::OWNER, WritePermission::OWNER)
}

fn physical_row(control: &mut Client, key: &StorageObjectKey) -> (StorageTimes, i64) {
    let row = control
        .query_one(
            "SELECT create_time, update_time, updated_at_ms FROM trnm_storage_objects \
             WHERE collection = $1 AND object_key = $2 AND user_id = $3",
            &[
                &key.collection(),
                &key.key(),
                &key.user_id().as_bytes().as_slice(),
            ],
        )
        .expect("timestamp fixture physical row query failed");
    (
        StorageTimes {
            create: row.try_get(0).unwrap(),
            update: row.try_get(1).unwrap(),
        },
        row.try_get(2).unwrap(),
    )
}

fn native_fixture_value(control: &mut Client, value: &[u8]) -> Vec<u8> {
    control
        .query_one(
            "SELECT $1::TEXT::JSONB::TEXT",
            &[&std::str::from_utf8(value).unwrap()],
        )
        .expect("timestamp fixture native projection failed")
        .get::<_, String>(0)
        .into_bytes()
}

fn seed_unknown(control: &mut Client, key: &StorageObjectKey, read: i16) {
    let integrity = IntegrityDigest::from_value(VALUE).get();
    let native = native_fixture_value(control, VALUE);
    let projected = IntegrityDigest::from_value(&native).get();
    let version = ContentVersion::from_value(VALUE);
    control
        .execute(
            "INSERT INTO trnm_storage_objects \
             (collection, object_key, user_id, value_bytes, version_digest, value_jsonb, \
              public_version, value_projection_digest, value_origin, read_permission, write_permission, updated_at_ms) \
             VALUES ($1, $2, $3, $4, $5, $7::TEXT::JSONB, $8, $9, 'legacy-rust-v2-bytes', $6, 1, 7)",
            &[
                &key.collection(),
                &key.key(),
                &key.user_id().as_bytes().as_slice(),
                &VALUE,
                &integrity.as_bytes().as_slice(),
                &read,
                &std::str::from_utf8(VALUE).unwrap(),
                &version.as_str(),
                &projected.as_bytes().as_slice(),
            ],
        )
        .expect("timestamp fixture historical seed failed");
}

fn set_times(control: &mut Client, key: &StorageObjectKey, create: &str, update: &str) {
    assert_eq!(
        control
            .execute(
                "UPDATE trnm_storage_objects SET create_time = $4::text::timestamptz, \
         update_time = $5::text::timestamptz \
         WHERE collection = $1 AND object_key = $2 AND user_id = $3",
                &[
                    &key.collection(),
                    &key.key(),
                    &key.user_id().as_bytes().as_slice(),
                    &create,
                    &update
                ],
            )
            .expect("timestamp fixture deterministic time seed failed"),
        1
    );
}

#[derive(Clone)]
struct Metadata {
    version: i64,
    profile: String,
    epoch: Option<i64>,
    digest: Option<String>,
    algorithm: Option<String>,
}

impl Metadata {
    fn read(control: &mut Client) -> Self {
        let row = control.query_one(
            "SELECT schema_version, profile, storage_writer_epoch, chain_digest, digest_algorithm \
             FROM trnm_schema_metadata WHERE singleton = 1", &[],
        ).expect("timestamp fixture requires published schema metadata");
        Self {
            version: row.try_get(0).unwrap(),
            profile: row.try_get(1).unwrap(),
            epoch: row.try_get(2).unwrap(),
            digest: row.try_get(3).unwrap(),
            algorithm: row.try_get(4).unwrap(),
        }
    }

    fn replace(&self, control: &mut Client) {
        assert_eq!(
            control
                .execute(
                    "UPDATE trnm_schema_metadata SET schema_version = $1, profile = $2, \
             storage_writer_epoch = $3, chain_digest = $4, digest_algorithm = $5 \
             WHERE singleton = 1",
                    &[
                        &self.version,
                        &self.profile,
                        &self.epoch,
                        &self.digest,
                        &self.algorithm
                    ],
                )
                .expect("timestamp fixture metadata update failed"),
            1
        );
    }
}

fn cleanup(control: &mut Client, metadata: &Metadata) {
    metadata.replace(control);
    control
        .execute(
            "DELETE FROM trnm_storage_objects WHERE collection = $1 AND user_id IN ($2, $3)",
            &[
                &COLLECTION,
                &OWNER.as_bytes().as_slice(),
                &OTHER.as_bytes().as_slice(),
            ],
        )
        .expect("timestamp fixture scoped cleanup failed");
}

fn shadow_namespace_cannot_replace_public_storage_or_clock(
    control: &mut Client,
    repository: &mut PgRepository,
    metadata: &Metadata,
) {
    repository.verify_authoritative_schema().unwrap();
    let name = format!(
        "trnm_timestamp_shadow_{}_{:x}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    );
    assert!(name.len() <= 63);
    assert!(name
        .bytes()
        .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_'));
    control
        .batch_execute(&format!("CREATE SCHEMA {name}"))
        .unwrap();
    let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let public_key = key("h-namespace", OWNER);
        let initial = repository
            .apply_storage_batch_with_metadata(
                StorageActor::User(OWNER),
                &[write(
                    &public_key,
                    VersionCheck::MustNotExist,
                    ReadPermission::PUBLIC,
                    WritePermission::OWNER,
                )],
                30,
            )
            .unwrap();
        let version = initial[0].receipt.current_version.unwrap();
        control
            .execute(
                &format!(
            "CREATE TABLE {name}.trnm_storage_objects AS SELECT * FROM public.trnm_storage_objects \
             WHERE collection = $1 AND object_key = $2 AND user_id = $3"),
                &[&COLLECTION, &public_key.key(), &OWNER.as_bytes().as_slice()],
            )
            .unwrap();
        control
            .batch_execute(&format!(
            "CREATE TABLE {name}.trnm_schema_metadata AS SELECT * FROM public.trnm_schema_metadata"
        ))
            .unwrap();
        control
            .batch_execute(&format!(
                "CREATE FUNCTION {name}.now() RETURNS TIMESTAMPTZ LANGUAGE SQL AS \
             $$ SELECT '2000-01-01 00:00:00.123456+00'::TIMESTAMPTZ $$"
            ))
            .unwrap();
        if repository.profile() == DatabaseProfile::PostgreSql {
            control
                .batch_execute(&format!(
                    "CREATE FUNCTION {name}.convert_to(value TEXT, encoding NAME) RETURNS BYTEA \
                 LANGUAGE SQL AS $$ SELECT NULL::BYTEA $$"
                ))
                .unwrap();
        }
        let shadow_value: &[u8] = br#"{"shadow":true}"#;
        let shadow_integrity = IntegrityDigest::from_value(shadow_value).get();
        control
            .execute(
                &format!(
            "UPDATE {name}.trnm_storage_objects SET value_bytes = $1, version_digest = $2, \
             create_time = '2000-01-01 00:00:00.123456+00'::TIMESTAMPTZ, \
             update_time = '2000-01-01 00:00:00.123456+00'::TIMESTAMPTZ"),
                &[&shadow_value, &shadow_integrity.as_bytes().as_slice()],
            )
            .unwrap();
        let forged_clock = StorageTimestamp::new(946_684_800, 123_456_000).unwrap();
        let path = format!("SET search_path = {name}, public, pg_catalog");
        // The fixture proves that an ambient now() call really resolves to the
        // shadow function, rather than merely assuming search_path precedence.
        control.batch_execute(&path).unwrap();
        let ambient_clock: StorageTimestamp = control
            .query_one("SELECT now()", &[])
            .unwrap()
            .try_get(0)
            .unwrap();
        control.batch_execute("RESET search_path").unwrap();
        assert_eq!(ambient_clock, forged_clock);
        if repository.profile() == DatabaseProfile::PostgreSql {
            control.batch_execute(&path).unwrap();
            let ambient_bytes: Option<Vec<u8>> = control
                .query_one("SELECT convert_to('namespace', 'UTF8')", &[])
                .unwrap()
                .try_get(0)
                .unwrap();
            control.batch_execute("RESET search_path").unwrap();
            assert!(
                ambient_bytes.is_none(),
                "the shadow function can poison an ambient typed-list conversion"
            );
        }
        repository.execute_migration_batch(&path).unwrap();
        let read = repository
            .read_storage_object_with_metadata(StorageActor::User(OWNER), &public_key)
            .unwrap();
        assert_eq!(read.object.value, native_fixture_value(control, VALUE));
        assert_eq!(read.times, initial[0].times);
        let batch_read = repository
            .read_storage_objects_with_metadata(StorageActor::User(OWNER), &[public_key.clone()])
            .unwrap();
        assert_eq!(batch_read, vec![read.clone()]);
        let typed = repository
            .list_storage_objects(
                StorageActor::User(OWNER),
                COLLECTION,
                Some(OWNER),
                None,
                100,
            )
            .unwrap();
        assert_eq!(
            typed
                .0
                .iter()
                .find(|object| object.key == public_key)
                .unwrap()
                .value,
            native_fixture_value(control, VALUE)
        );
        for (actor, owner) in [
            (StorageActor::User(OTHER), None),
            (StorageActor::User(OWNER), Some(OWNER)),
            (StorageActor::User(OTHER), Some(OWNER)),
        ] {
            let listed = repository
                .list_storage_objects_nakama_with_metadata(actor, COLLECTION, owner, None, 100)
                .unwrap();
            let object = listed
                .objects
                .iter()
                .find(|object| object.object.key == public_key)
                .unwrap();
            assert_eq!(object.object.value, native_fixture_value(control, VALUE));
            assert_eq!(object.times, initial[0].times);
        }
        // Wrong shadow metadata must not deny a public v2 mutation.
        control
            .batch_execute(&format!(
                "UPDATE {name}.trnm_schema_metadata SET storage_writer_epoch = 1"
            ))
            .unwrap();
        let inserted_key = key("i-namespace-insert", OWNER);
        let inserted = repository
            .apply_storage_batch_with_metadata(
                StorageActor::User(OWNER),
                &[owner_write(&inserted_key, VersionCheck::MustNotExist)],
                31,
            )
            .unwrap();
        assert_ne!(inserted[0].times.create, Some(forged_clock));
        assert_eq!(inserted[0].times.create, inserted[0].times.update);
        assert_eq!(
            physical_row(control, &inserted_key),
            (inserted[0].times, 31)
        );
        let updated = repository
            .apply_storage_batch_with_metadata(
                StorageActor::User(OWNER),
                &[write(
                    &public_key,
                    VersionCheck::Exact(version.into()),
                    ReadPermission::PUBLIC,
                    WritePermission::OWNER,
                )],
                32,
            )
            .unwrap();
        assert_eq!(updated[0].times.create, initial[0].times.create);
        assert_ne!(updated[0].times.update, Some(forged_clock));
        assert_eq!(physical_row(control, &public_key), (updated[0].times, 32));
        // Ready shadow metadata must not conceal a faulted public epoch.
        control
            .batch_execute(&format!(
                "UPDATE {name}.trnm_schema_metadata SET storage_writer_epoch = 4"
            ))
            .unwrap();
        let mut bad = metadata.clone();
        bad.epoch = Some(1);
        bad.replace(control);
        let missing = key("j-namespace-fenced", OWNER);
        for operation in [
            write(
                &public_key,
                VersionCheck::Any,
                ReadPermission::PUBLIC,
                WritePermission::OWNER,
            ),
            owner_write(&missing, VersionCheck::MustNotExist),
            StorageBatchOperation::Delete(StorageDeleteOperation {
                key: public_key.clone(),
                expected_version: Some(version.into()),
            }),
        ] {
            assert_eq!(
                repository
                    .apply_storage_batch_with_metadata(StorageActor::User(OWNER), &[operation], 33)
                    .unwrap_err()
                    .code(),
                StableCode::DataLoss
            );
            assert_eq!(physical_row(control, &public_key), (updated[0].times, 32));
        }
        metadata.replace(control);
        let deleted = repository
            .apply_storage_batch_with_metadata(
                StorageActor::User(OWNER),
                &[StorageBatchOperation::Delete(StorageDeleteOperation {
                    key: public_key.clone(),
                    expected_version: Some(version.into()),
                })],
                34,
            )
            .unwrap();
        assert_eq!(deleted[0].times, updated[0].times);
        assert!(repository
            .read_storage_objects_with_metadata(StorageActor::User(OWNER), &[public_key])
            .unwrap()
            .is_empty());
        let shadow = control
            .query_one(
                &format!(
                    "SELECT value_bytes, create_time, update_time, updated_at_ms, \
             (SELECT count(*) FROM {name}.trnm_storage_objects) FROM {name}.trnm_storage_objects"
                ),
                &[],
            )
            .unwrap();
        assert_eq!(shadow.try_get::<_, Vec<u8>>(0).unwrap(), shadow_value);
        assert_eq!(
            shadow.try_get::<_, StorageTimestamp>(1).unwrap(),
            forged_clock
        );
        assert_eq!(
            shadow.try_get::<_, StorageTimestamp>(2).unwrap(),
            forged_clock
        );
        assert_eq!(shadow.try_get::<_, i64>(3).unwrap(), 30);
        assert_eq!(shadow.try_get::<_, i64>(4).unwrap(), 1);
    }));
    // Restore both clients before removing only this fixture's unique schema,
    // including after an assertion panic. The outer fixture restores public rows.
    control.batch_execute("RESET search_path").unwrap();
    repository
        .execute_migration_batch("RESET search_path")
        .unwrap();
    metadata.replace(control);
    control
        .batch_execute(&format!("DROP SCHEMA {name} CASCADE"))
        .unwrap();
    if let Err(payload) = outcome {
        std::panic::resume_unwind(payload);
    }
}

#[test]
fn storage_timestamps_database_clock_no_op_and_atomicity() {
    let Some((database_url, profile)) = live_environment() else {
        eprintln!("storage_timestamps_live_skipped: optional database is absent");
        return;
    };
    let mut control = Client::connect(&database_url, NoTls)
        .unwrap_or_else(|_| panic!("timestamp fixture control connection failed"));
    let metadata = Metadata::read(&mut control);
    assert_eq!(metadata.version, 4);
    assert_eq!(metadata.epoch, Some(4));
    assert_eq!(metadata.profile, profile.metadata_value());
    cleanup(&mut control, &metadata);
    let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let mut repository = PgRepository::connect(&database_url, profile)
            .unwrap_or_else(|_| panic!("timestamp fixture repository connection failed"));
        let first = key("a-new", OWNER);
        let second = key("b-new", OWNER);
        let inserted = repository
            .apply_storage_batch_with_metadata(
                StorageActor::User(OWNER),
                &[
                    owner_write(&first, VersionCheck::MustNotExist),
                    owner_write(&second, VersionCheck::MustNotExist),
                ],
                7,
            )
            .unwrap();
        assert_eq!(inserted.len(), 2);
        let pair = inserted[0].times;
        assert!(pair.create.is_some());
        assert_eq!(pair.create, pair.update);
        assert_eq!(
            pair, inserted[1].times,
            "one transaction shares the actual DB clock"
        );
        assert_eq!(physical_row(&mut control, &first), (pair, 7));
        assert_eq!(physical_row(&mut control, &second), (pair, 7));
        assert_ne!(
            pair.update.unwrap().seconds,
            0,
            "caller milliseconds cannot supply the public clock"
        );
        let version = inserted[0].receipt.current_version.unwrap();
        assert_eq!(version, ContentVersion::from_value(VALUE));
        let read = repository
            .read_storage_objects_with_metadata(
                StorageActor::User(OWNER),
                &[first.clone(), second.clone()],
            )
            .unwrap();
        assert_eq!(read[0].times, pair);
        assert_eq!(read[1].times, pair);
        let native = native_fixture_value(&mut control, VALUE);
        assert_eq!(read[0].object.value, native);
        assert_eq!(
            read[0].object.integrity_digest,
            IntegrityDigest::from_value(&native)
        );
        let page = repository
            .list_storage_objects_nakama_with_metadata(
                StorageActor::User(OWNER),
                COLLECTION,
                Some(OWNER),
                None,
                1,
            )
            .unwrap();
        assert_eq!(page.objects[0].object.key, first);
        assert_eq!(page.objects[0].times, pair);
        assert_eq!(page.next.as_ref().unwrap().key, "a-new");

        shadow_namespace_cannot_replace_public_storage_or_clock(
            &mut control,
            &mut repository,
            &metadata,
        );

        // A deterministic SQL sentinel distinguishes UPDATE from a blind no-op,
        // without assuming that successive transaction clocks must increase.
        set_times(
            &mut control,
            &first,
            "2000-01-01 00:00:00.123456+00",
            "1969-12-31 23:59:59.999999+00",
        );
        let sentinel = StorageTimes {
            create: Some(StorageTimestamp::new(946_684_800, 123_456_000).unwrap()),
            update: Some(StorageTimestamp::new(-1, 999_999_000).unwrap()),
        };
        assert_eq!(physical_row(&mut control, &first), (sentinel, 7));
        let unchanged = repository
            .apply_storage_batch_with_metadata(
                StorageActor::User(OWNER),
                &[owner_write(&first, VersionCheck::Any)],
                8,
            )
            .unwrap();
        assert_eq!(unchanged[0].times, sentinel);
        assert_eq!(physical_row(&mut control, &first), (sentinel, 7));
        let exact = repository
            .apply_storage_batch_with_metadata(
                StorageActor::User(OWNER),
                &[owner_write(&first, VersionCheck::Exact(version.into()))],
                9,
            )
            .unwrap();
        assert_eq!(exact[0].times.create, sentinel.create);
        assert_ne!(exact[0].times.update, sentinel.update);
        assert_eq!(physical_row(&mut control, &first), (exact[0].times, 9));

        set_times(
            &mut control,
            &first,
            "2000-01-01 00:00:00.123456+00",
            "1969-12-31 23:59:59.999999+00",
        );
        let acl = repository
            .apply_storage_batch_with_metadata(
                StorageActor::User(OWNER),
                &[write(
                    &first,
                    VersionCheck::Any,
                    ReadPermission::PUBLIC,
                    WritePermission::OWNER,
                )],
                10,
            )
            .unwrap();
        assert_eq!(acl[0].receipt.current_version, Some(version));
        assert_eq!(acl[0].times.create, sentinel.create);
        assert_ne!(acl[0].times.update, sentinel.update);
        assert_eq!(physical_row(&mut control, &first), (acl[0].times, 10));

        let historical = key("c-historical", OWNER);
        set_times(
            &mut control,
            &second,
            "2000-01-01 00:00:00.123456+00",
            "1969-12-31 23:59:59.999999+00",
        );
        let changed_value = br#"{"timestamp":"changed"}"#;
        let changed = repository
            .apply_storage_batch_with_metadata(
                StorageActor::User(OWNER),
                &[StorageBatchOperation::Write(StorageWriteOperation {
                    key: second.clone(),
                    value: changed_value.to_vec(),
                    expected: VersionCheck::Any,
                    read_permission: ReadPermission::OWNER,
                    write_permission: WritePermission::OWNER,
                })],
                19,
            )
            .unwrap();
        assert_eq!(changed[0].times.create, sentinel.create);
        assert_ne!(changed[0].times.update, sentinel.update);
        assert_eq!(
            changed[0].receipt.current_version,
            Some(ContentVersion::from_value(changed_value))
        );
        assert_eq!(physical_row(&mut control, &second), (changed[0].times, 19));
        let changed_object = repository
            .read_storage_object_with_metadata(StorageActor::User(OWNER), &second)
            .unwrap();
        let changed_native = native_fixture_value(&mut control, changed_value);
        assert_eq!(changed_object.object.value, changed_native);
        assert_eq!(
            changed_object.object.integrity_digest,
            IntegrityDigest::from_value(&changed_native)
        );

        seed_unknown(&mut control, &historical, 1);
        let unknown = repository
            .read_storage_object_with_metadata(StorageActor::User(OWNER), &historical)
            .unwrap();
        assert_eq!(unknown.times, StorageTimes::default());
        let historical_no_op = repository
            .apply_storage_batch_with_metadata(
                StorageActor::User(OWNER),
                &[owner_write(&historical, VersionCheck::Any)],
                11,
            )
            .unwrap();
        assert_eq!(historical_no_op[0].times, StorageTimes::default());
        assert_eq!(
            physical_row(&mut control, &historical),
            (StorageTimes::default(), 7)
        );
        let historical_updated = repository
            .apply_storage_batch_with_metadata(
                StorageActor::User(OWNER),
                &[owner_write(
                    &historical,
                    VersionCheck::Exact(version.into()),
                )],
                12,
            )
            .unwrap();
        assert_eq!(historical_updated[0].times.create, None);
        assert!(historical_updated[0].times.update.is_some());
        assert_eq!(
            physical_row(&mut control, &historical),
            (historical_updated[0].times, 12)
        );

        let before_failure = repository
            .read_storage_object_with_metadata(StorageActor::Server, &first)
            .unwrap();
        let missing = key("z-missing", OWNER);
        let failed = repository
            .apply_storage_batch_with_metadata(
                StorageActor::User(OWNER),
                &[
                    owner_write(&first, VersionCheck::Exact(version.into())),
                    owner_write(&missing, VersionCheck::Exact(version.into())),
                ],
                13,
            )
            .unwrap_err();
        assert_eq!(failed.code(), StableCode::FailedPrecondition);
        assert_eq!(
            repository
                .read_storage_object_with_metadata(StorageActor::Server, &first)
                .unwrap(),
            before_failure
        );
        assert_eq!(
            physical_row(&mut control, &first),
            (before_failure.times, 10)
        );
        let denied = repository
            .apply_storage_batch_with_metadata(
                StorageActor::User(OTHER),
                &[owner_write(&first, VersionCheck::Any)],
                14,
            )
            .unwrap_err();
        assert_eq!(denied.code(), StableCode::PermissionDenied);
        assert_eq!(
            physical_row(&mut control, &first),
            (before_failure.times, 10)
        );
        let wrong_version = ContentVersion::from_value(b"wrong");
        let stale = repository
            .apply_storage_batch_with_metadata(
                StorageActor::User(OWNER),
                &[owner_write(
                    &first,
                    VersionCheck::Exact(wrong_version.into()),
                )],
                15,
            )
            .unwrap_err();
        assert_eq!(stale.code(), StableCode::FailedPrecondition);
        assert_eq!(
            physical_row(&mut control, &first),
            (before_failure.times, 10)
        );

        let guarded = key("g-server-write-only", OWNER);
        let guarded_receipt = repository
            .apply_storage_batch_with_metadata(
                StorageActor::Server,
                &[write(
                    &guarded,
                    VersionCheck::MustNotExist,
                    ReadPermission::OWNER,
                    WritePermission::NONE,
                )],
                20,
            )
            .unwrap();
        let denied_no_op = repository
            .apply_storage_batch_with_metadata(
                StorageActor::User(OWNER),
                &[write(
                    &guarded,
                    VersionCheck::Any,
                    ReadPermission::OWNER,
                    WritePermission::NONE,
                )],
                21,
            )
            .unwrap_err();
        assert_eq!(denied_no_op.code(), StableCode::PermissionDenied);
        assert_eq!(
            physical_row(&mut control, &guarded),
            (guarded_receipt[0].times, 20)
        );
        let denied_batch = repository
            .apply_storage_batch_with_metadata(
                StorageActor::User(OWNER),
                &[
                    owner_write(&first, VersionCheck::Exact(version.into())),
                    owner_write(&guarded, VersionCheck::Any),
                ],
                22,
            )
            .unwrap_err();
        assert_eq!(denied_batch.code(), StableCode::PermissionDenied);
        assert_eq!(
            repository
                .read_storage_object_with_metadata(StorageActor::Server, &first)
                .unwrap(),
            before_failure
        );
        assert_eq!(
            physical_row(&mut control, &first),
            (before_failure.times, 10)
        );

        // The per-mutation epoch check runs even for an otherwise successful
        // blind no-op. Every fault must fail as server integrity, never client OCC.
        let wrong_profile = if profile == DatabaseProfile::PostgreSql {
            DatabaseProfile::CockroachDb
        } else {
            DatabaseProfile::PostgreSql
        };
        let mut wrong_profile_repository = PgRepository::connect(&database_url, wrong_profile)
            .unwrap_or_else(|_| panic!("timestamp fixture profile mismatch connection failed"));
        for fault in 0..8 {
            let mut bad = metadata.clone();
            match fault {
                0 => bad.epoch = Some(1),
                1 => bad.epoch = Some(5),
                2 => bad.epoch = None,
                3 => bad.version = 1,
                4 => bad.version = 5,
                5 => bad.digest = Some("0".repeat(64)),
                6 => bad.algorithm = Some("wrong-algorithm".to_owned()),
                // Immutable per-profile DDL rejects a different metadata
                // profile. Fault the repository's expected profile instead.
                _ => {}
            }
            bad.replace(&mut control);
            for operation in [
                write(
                    &first,
                    VersionCheck::Any,
                    ReadPermission::PUBLIC,
                    WritePermission::OWNER,
                ),
                owner_write(&missing, VersionCheck::MustNotExist),
                StorageBatchOperation::Delete(StorageDeleteOperation {
                    key: first.clone(),
                    expected_version: Some(version.into()),
                }),
            ] {
                let error = if fault == 7 {
                    wrong_profile_repository.apply_storage_batch_with_metadata(
                        StorageActor::User(OWNER),
                        &[operation],
                        16,
                    )
                } else {
                    repository.apply_storage_batch_with_metadata(
                        StorageActor::User(OWNER),
                        &[operation],
                        16,
                    )
                }
                .unwrap_err();
                assert_eq!(error.code(), StableCode::DataLoss, "fault {fault}");
                assert_eq!(
                    physical_row(&mut control, &first),
                    (before_failure.times, 10)
                );
                let count: i64 = control.query_one(
                    "SELECT count(*) FROM public.trnm_storage_objects WHERE collection = $1 AND object_key = $2 AND user_id = $3",
                    &[&COLLECTION, &missing.key(), &OWNER.as_bytes().as_slice()],
                ).unwrap().get(0);
                assert_eq!(count, 0);
                let read_repository = if fault == 7 {
                    &mut wrong_profile_repository
                } else {
                    &mut repository
                };
                assert_eq!(
                    read_repository
                        .read_storage_objects_with_metadata(
                            StorageActor::Server,
                            &[missing.clone()]
                        )
                        .unwrap_err()
                        .code(),
                    StableCode::DataLoss
                );
            }
            metadata.replace(&mut control);
        }

        // Actual pgwire binary timestamps exercise epoch normalization and both
        // protobuf calendar bounds on each profile, independent of app clocks.
        for (name, literal, expected) in [
            (
                "d-before-epoch",
                "1969-12-31 23:59:59.123456+00",
                StorageTimestamp::new(-1, 123_456_000).unwrap(),
            ),
            (
                "e-minimum",
                "0001-01-01 00:00:00+00",
                StorageTimestamp::new(-62_135_596_800, 0).unwrap(),
            ),
            (
                "f-maximum",
                "9999-12-31 23:59:59.999999+00",
                StorageTimestamp::new(253_402_300_799, 999_999_000).unwrap(),
            ),
        ] {
            let seeded = key(name, OWNER);
            seed_unknown(&mut control, &seeded, 2);
            set_times(&mut control, &seeded, literal, literal);
            let object = repository
                .read_storage_object_with_metadata(StorageActor::Server, &seeded)
                .unwrap();
            assert_eq!(
                object.times,
                StorageTimes {
                    create: Some(expected),
                    update: Some(expected)
                }
            );
            let listed = repository
                .list_storage_objects_nakama_with_metadata(
                    StorageActor::User(OTHER),
                    COLLECTION,
                    Some(OWNER),
                    None,
                    100,
                )
                .unwrap();
            assert_eq!(
                listed
                    .objects
                    .iter()
                    .find(|object| object.object.key == seeded)
                    .unwrap()
                    .times,
                object.times
            );
        }

        {
            let visible = key("y-valid-time", OTHER);
            seed_unknown(&mut control, &visible, 2);
            let corrupt = key("z-invalid-time", OTHER);
            seed_unknown(&mut control, &corrupt, 0);
            let invalid_times: &[&str] = match profile {
                DatabaseProfile::PostgreSql => &[
                    "infinity",
                    "-infinity",
                    "10000-01-01 00:00:00+00",
                    "0001-01-01 00:00:00 BC",
                ],
                DatabaseProfile::CockroachDb => &["10000-01-01 00:00:00+00"],
            };
            for invalid in invalid_times {
                set_times(&mut control, &corrupt, invalid, invalid);
                let error = repository
                    .read_storage_objects_with_metadata(StorageActor::Server, &[corrupt.clone()])
                    .unwrap_err();
                assert_eq!(error.code(), StableCode::DataLoss);
                assert!(repository
                    .read_storage_objects_with_metadata(
                        StorageActor::User(OWNER),
                        &[corrupt.clone()]
                    )
                    .unwrap()
                    .is_empty());
                // Public filtering occurs before a corrupt hidden row is decoded.
                let hidden_page = repository
                    .list_storage_objects_nakama_with_metadata(
                        StorageActor::User(OWNER),
                        COLLECTION,
                        Some(OTHER),
                        None,
                        100,
                    )
                    .unwrap();
                assert_eq!(hidden_page.objects.len(), 1);
                assert_eq!(hidden_page.objects[0].object.key, visible);
            }
            control.execute("UPDATE trnm_storage_objects SET read_permission = 2 WHERE collection = $1 AND object_key = $2 AND user_id = $3", &[&COLLECTION, &corrupt.key(), &OTHER.as_bytes().as_slice()]).unwrap();
            assert_eq!(
                repository
                    .list_storage_objects_nakama_with_metadata(
                        StorageActor::User(OWNER),
                        COLLECTION,
                        Some(OTHER),
                        None,
                        100
                    )
                    .unwrap_err()
                    .code(),
                StableCode::DataLoss
            );
            let sentinel_page = repository
                .list_storage_objects_nakama_with_metadata(
                    StorageActor::User(OWNER),
                    COLLECTION,
                    Some(OTHER),
                    None,
                    1,
                )
                .unwrap();
            assert_eq!(sentinel_page.objects.len(), 1);
            assert_eq!(sentinel_page.objects[0].object.key, visible);
            assert!(sentinel_page.next.is_some());
        }

        let deleted = repository
            .apply_storage_batch_with_metadata(
                StorageActor::User(OWNER),
                &[StorageBatchOperation::Delete(StorageDeleteOperation {
                    key: first.clone(),
                    expected_version: Some(version.into()),
                })],
                17,
            )
            .unwrap();
        assert_eq!(deleted[0].receipt.current_version, None);
        assert_eq!(deleted[0].times, before_failure.times);
        assert!(repository
            .read_storage_objects_with_metadata(StorageActor::User(OWNER), &[first.clone()])
            .unwrap()
            .is_empty());
        let recreated = repository
            .apply_storage_batch_with_metadata(
                StorageActor::User(OWNER),
                &[owner_write(&first, VersionCheck::MustNotExist)],
                18,
            )
            .unwrap();
        assert_eq!(recreated[0].receipt.previous_version, None);
        assert_eq!(recreated[0].times.create, recreated[0].times.update);
        assert!(recreated[0].times.create.is_some());
        assert_ne!(recreated[0].times.create, sentinel.create);
        assert_eq!(physical_row(&mut control, &first), (recreated[0].times, 18));
    }));
    cleanup(&mut control, &metadata);
    let remaining: i64 = control.query_one(
        "SELECT COUNT(*) FROM trnm_storage_objects WHERE collection = $1 AND user_id IN ($2, $3)",
        &[&COLLECTION, &OWNER.as_bytes().as_slice(), &OTHER.as_bytes().as_slice()],
    ).expect("timestamp fixture cleanup assertion failed").try_get(0).unwrap();
    assert_eq!(remaining, 0);
    if let Err(payload) = outcome {
        std::panic::resume_unwind(payload);
    }
    println!(
        "\nstorage_timestamps_live_executed profile={}",
        profile.metadata_value()
    );
}
