//! Native database projection fixtures. Synthetic export manifests exercise the
//! decoder boundary; they do not prove a source-bound Nakama data importer.
use std::env;
use std::time::{SystemTime, UNIX_EPOCH};

use postgres::{Client, NoTls};
use trnm_contracts::{StableCode, UserId};
use trnm_persistence_pg::{
    classify_sqlstate, ContentVersion, DatabaseProfile, IntegrityDigest, PgRepository,
    ReadPermission, StorageActor, StorageBatchOperation, StorageDeleteOperation, StorageObjectKey,
    StorageTimes, StorageWriteOperation, VersionCheck, WritePermission,
};

const OWNER: UserId = UserId::new([0xd1; 16]);
const OTHER: UserId = UserId::new([0xd2; 16]);

fn environment() -> Option<(String, DatabaseProfile)> {
    let required = match env::var("TRNM_REQUIRE_LIVE_DATABASE") {
        Err(env::VarError::NotPresent) => false,
        Ok(value) if matches!(value.as_str(), "1" | "true" | "TRUE" | "yes" | "YES") => true,
        Ok(value) if matches!(value.as_str(), "0" | "false" | "FALSE" | "no" | "NO") => false,
        _ => panic!("invalid TRNM_REQUIRE_LIVE_DATABASE"),
    };
    let url = match env::var("TRNM_DATABASE_URL") {
        Ok(value) if !value.is_empty() => value,
        Ok(_) | Err(env::VarError::NotPresent) if !required => return None,
        _ => panic!("required storage JSONB database environment is unavailable"),
    };
    let profile = match env::var("TRNM_DATABASE_PROFILE").as_deref() {
        Ok("postgresql") => DatabaseProfile::PostgreSql,
        Ok("cockroachdb") => DatabaseProfile::CockroachDb,
        _ => panic!("TRNM_DATABASE_PROFILE must identify the actual database"),
    };
    Some((url, profile))
}

fn native(control: &mut Client, request: &[u8]) -> Vec<u8> {
    control
        .query_one(
            "SELECT $1::TEXT::JSONB::TEXT",
            &[&std::str::from_utf8(request).unwrap()],
        )
        .expect("native JSONB fixture projection failed")
        .get::<_, String>(0)
        .into_bytes()
}

fn write(key: &StorageObjectKey, request: &[u8], expected: VersionCheck) -> StorageBatchOperation {
    StorageBatchOperation::Write(StorageWriteOperation {
        key: key.clone(),
        value: request.to_vec(),
        expected,
        read_permission: ReadPermission::PUBLIC,
        write_permission: WritePermission::OWNER,
    })
}

#[derive(Debug, Eq, PartialEq)]
struct Physical {
    value: String,
    public_version: String,
    projection_digest: Vec<u8>,
    origin: String,
    raw: Option<Vec<u8>>,
    raw_digest: Option<Vec<u8>>,
    manifest: Option<Vec<u8>>,
    times: StorageTimes,
    updated_at_ms: i64,
    read: i16,
    write: i16,
}

fn physical(control: &mut Client, key: &StorageObjectKey) -> Physical {
    let row = control.query_one(
        "SELECT value_jsonb::TEXT, public_version::TEXT, value_projection_digest, value_origin, \
         value_bytes, version_digest, source_manifest_digest, create_time, update_time, \
         updated_at_ms, read_permission, write_permission FROM public.trnm_storage_objects \
         WHERE collection = $1 AND object_key = $2 AND user_id = $3",
        &[&key.collection(), &key.key(), &key.user_id().as_bytes().as_slice()],
    ).expect("native JSONB fixture physical snapshot failed");
    Physical {
        value: row.get(0),
        public_version: row.get(1),
        projection_digest: row.get(2),
        origin: row.get(3),
        raw: row.get(4),
        raw_digest: row.get(5),
        manifest: row.get(6),
        times: StorageTimes {
            create: row.get(7),
            update: row.get(8),
        },
        updated_at_ms: row.get(9),
        read: row.get(10),
        write: row.get(11),
    }
}

fn seed_unknown(control: &mut Client, key: &StorageObjectKey, payload: &[u8], token: &str) {
    let projected = native(control, payload);
    let digest = IntegrityDigest::from_value(&projected).get();
    assert_eq!(control.execute(
        "INSERT INTO public.trnm_storage_objects \
         (collection, object_key, user_id, value_jsonb, public_version, value_projection_digest, \
          value_origin, source_manifest_digest, value_bytes, version_digest, \
          read_permission, write_permission, updated_at_ms) \
         VALUES ($1, $2, $3, $4::TEXT::JSONB, $5, $6, 'nakama-export-unknown-request', $7, NULL, NULL, 2, 1, 7)",
        &[&key.collection(), &key.key(), &key.user_id().as_bytes().as_slice(),
          &std::str::from_utf8(payload).unwrap(), &token, &digest.as_bytes().as_slice(), &[0x6a_u8; 32].as_slice()],
    ).expect("native JSONB fixture unknown provenance seed failed"), 1);
}

#[test]
fn storage_native_jsonb_versions_provenance_and_atomicity() {
    let Some((url, profile)) = environment() else {
        eprintln!("storage_native_jsonb_live_skipped: optional database is absent");
        return;
    };
    let collection = format!(
        "native-jsonb-{}-{:x}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    );
    let mut control = Client::connect(&url, NoTls)
        .unwrap_or_else(|_| panic!("native JSONB fixture control connection failed"));
    let mut repository = PgRepository::connect(&url, profile)
        .unwrap_or_else(|_| panic!("native JSONB fixture repository connection failed"));
    repository.verify_authoritative_schema().unwrap();
    let key = |name: &str| StorageObjectKey::new(&collection, name, OWNER).unwrap();
    let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let cases: &[&[u8]] = &[
            br#"{ "b":1e0, "a":"\u0061", "dup":1, "dup":2 }"#,
            br#"{"z":1,"a":2}"#,
            br#"{"nested":[1.0,1e2,"\n","\u00e9"],"bool":true}"#,
            br#"[null,1,"scalar"]"#,
            b"null",
            br#""historical scalar""#,
        ];
        for (index, raw) in cases.iter().enumerate() {
            let object_key = key(&format!("projection-{index}"));
            let expected_native = native(&mut control, raw);
            let receipt = repository
                .apply_storage_batch_with_metadata(
                    StorageActor::User(OWNER),
                    &[write(&object_key, raw, VersionCheck::MustNotExist)],
                    10,
                )
                .unwrap();
            let generated = ContentVersion::from_value(raw);
            assert_eq!(receipt[0].receipt.current_version, Some(generated));
            let read = repository
                .read_storage_object_with_metadata(StorageActor::User(OWNER), &object_key)
                .unwrap();
            assert_eq!(read.object.value, expected_native);
            assert_eq!(read.object.version.as_str(), generated.as_str());
            assert_eq!(
                read.object.integrity_digest,
                IntegrityDigest::from_value(&expected_native)
            );
            assert!(read
                .object
                .collision_witness
                .as_ref()
                .unwrap()
                .matches_request(raw));
            assert_eq!(read.times, receipt[0].times);
            let row = physical(&mut control, &object_key);
            assert_eq!(row.raw.as_deref(), Some(*raw));
            assert_eq!(row.origin, "write-request-bytes");
            assert_eq!(row.manifest, None);
            if index == 0 {
                assert_ne!(generated, ContentVersion::from_value(&expected_native));
            }
        }
        // Equivalent JSONB values still carry distinct request-byte versions.
        let object_key = key("whitespace");
        let first = br#"{"a":1}"#;
        let second = br#"{ "a" : 1 }"#;
        assert_eq!(native(&mut control, first), native(&mut control, second));
        repository
            .apply_storage_batch(
                StorageActor::User(OWNER),
                &[write(&object_key, first, VersionCheck::Any)],
                20,
            )
            .unwrap();
        let updated = repository
            .apply_storage_batch(
                StorageActor::User(OWNER),
                &[write(&object_key, second, VersionCheck::Any)],
                21,
            )
            .unwrap();
        assert_eq!(
            updated[0].previous_version.as_ref().unwrap().as_str(),
            ContentVersion::from_value(first).as_str()
        );
        assert_eq!(
            updated[0].current_version,
            Some(ContentVersion::from_value(second))
        );
        assert_eq!(
            physical(&mut control, &object_key).raw.as_deref(),
            Some(second.as_slice())
        );

        let opaque = [
            String::new(),
            "UPPER-not-HEX".to_owned(),
            "*".to_owned(),
            "版本🍀".to_owned(),
            "界".repeat(32),
        ];
        for (index, token) in opaque.iter().enumerate() {
            let object_key = key(&format!("history-{index}"));
            let payload = cases[index % cases.len()];
            seed_unknown(&mut control, &object_key, payload, token);
            let read = repository
                .read_storage_object_with_metadata(StorageActor::User(OTHER), &object_key)
                .unwrap();
            assert_eq!(read.object.value, native(&mut control, payload));
            assert_eq!(read.object.version.as_str(), token);
            assert_eq!(read.object.collision_witness, None);
            assert_eq!(read.times, StorageTimes::default());
            let deleted = repository
                .apply_storage_batch(
                    StorageActor::User(OWNER),
                    &[StorageBatchOperation::Delete(StorageDeleteOperation {
                        key: object_key.clone(),
                        expected_version: Some(token.as_str().into()),
                    })],
                    22,
                )
                .unwrap();
            assert_eq!(
                deleted[0].previous_version.as_ref().unwrap().as_str(),
                token
            );
            assert_eq!(deleted[0].current_version, None);
        }
        let unknown = key("unknown-noop");
        let incoming = br#"{"different":"incoming"}"#;
        let incoming_version = ContentVersion::from_value(incoming);
        seed_unknown(
            &mut control,
            &unknown,
            br#"{"retained":true}"#,
            incoming_version.as_str(),
        );
        let before = physical(&mut control, &unknown);
        let receipt = repository
            .apply_storage_batch_with_metadata(
                StorageActor::User(OWNER),
                &[write(&unknown, incoming, VersionCheck::Any)],
                30,
            )
            .unwrap();
        assert_eq!(receipt[0].receipt.current_version, Some(incoming_version));
        assert_eq!(receipt[0].times, StorageTimes::default());
        assert_eq!(physical(&mut control, &unknown), before);
        let receipt = repository
            .apply_storage_batch_with_metadata(
                StorageActor::User(OWNER),
                &[write(
                    &unknown,
                    incoming,
                    VersionCheck::Exact(incoming_version.into()),
                )],
                31,
            )
            .unwrap();
        assert_eq!(receipt[0].times.create, None);
        assert!(receipt[0].times.update.is_some());
        let row = physical(&mut control, &unknown);
        assert_eq!(row.origin, "write-request-bytes");
        assert_eq!(row.raw.as_deref(), Some(incoming.as_slice()));
        assert_eq!(row.manifest, None);

        let guarded = key("a-valid-page");
        let corrupt = key("z-corrupt-page");
        let raw = br#"{"known":true}"#;
        repository
            .apply_storage_batch(
                StorageActor::User(OWNER),
                &[
                    write(&guarded, raw, VersionCheck::Any),
                    write(&corrupt, raw, VersionCheck::Any),
                ],
                40,
            )
            .unwrap();
        // Scope this list to two rows so an invalid witness is exactly sentinel 2.
        control.execute("UPDATE public.trnm_storage_objects SET read_permission = 0 WHERE collection = $1 AND object_key NOT IN ($2,$3)",
            &[&collection, &guarded.key(), &corrupt.key()]).unwrap();
        control.execute("UPDATE public.trnm_storage_objects SET value_bytes = $4 WHERE collection = $1 AND object_key = $2 AND user_id = $3",
            &[&collection, &corrupt.key(), &OWNER.as_bytes().as_slice(), &b"invalid-json".as_slice()]).unwrap();
        let first_page = repository
            .list_storage_objects_nakama(
                StorageActor::User(OTHER),
                &collection,
                Some(OWNER),
                None,
                1,
            )
            .unwrap();
        assert_eq!(first_page.objects.len(), 1);
        assert_eq!(first_page.objects[0].key, guarded);
        assert!(first_page.next.is_some());
        assert_eq!(
            repository
                .list_storage_objects_nakama(
                    StorageActor::User(OTHER),
                    &collection,
                    Some(OWNER),
                    first_page.next.as_ref(),
                    1
                )
                .unwrap_err()
                .code(),
            StableCode::DataLoss
        );
        control.execute("UPDATE public.trnm_storage_objects SET read_permission = 0, write_permission = 0 WHERE collection = $1 AND object_key = $2",
            &[&collection, &corrupt.key()]).unwrap();
        assert!(repository
            .read_storage_objects(StorageActor::User(OTHER), &[corrupt.clone()])
            .unwrap()
            .is_empty());
        assert_eq!(
            repository
                .read_storage_object(StorageActor::User(OTHER), &corrupt)
                .unwrap_err()
                .code(),
            StableCode::PermissionDenied
        );
        assert_eq!(
            repository
                .apply_storage_batch(
                    StorageActor::User(OWNER),
                    &[write(&corrupt, raw, VersionCheck::Any)],
                    41
                )
                .unwrap_err()
                .code(),
            StableCode::PermissionDenied
        );
        assert_eq!(
            repository
                .apply_storage_batch(
                    StorageActor::User(OWNER),
                    &[write(&corrupt, raw, VersionCheck::MustNotExist)],
                    41
                )
                .unwrap_err()
                .code(),
            StableCode::AlreadyExists
        );
        assert_eq!(
            repository
                .read_storage_object(StorageActor::Server, &corrupt)
                .unwrap_err()
                .code(),
            StableCode::DataLoss
        );

        let baseline = physical(&mut control, &guarded);
        for (field, corrupt_bytes) in [
            ("value_projection_digest", vec![0x55_u8; 32]),
            ("value_projection_digest", vec![0_u8; 32]),
            ("version_digest", vec![0x55_u8; 32]),
            ("version_digest", vec![0_u8; 32]),
            ("value_bytes", vec![0xff_u8]),
        ] {
            // Identifiers come exclusively from this fixed fixture table.
            let sql = format!("UPDATE public.trnm_storage_objects SET {field} = $4 WHERE collection = $1 AND object_key = $2 AND user_id = $3");
            match control.execute(
                &sql,
                &[
                    &collection,
                    &guarded.key(),
                    &OWNER.as_bytes().as_slice(),
                    &corrupt_bytes,
                ],
            ) {
                Ok(1) => {
                    assert_eq!(
                        repository
                            .read_storage_object(StorageActor::Server, &guarded)
                            .unwrap_err()
                            .code(),
                        StableCode::DataLoss
                    );
                    let correct = match field {
                        "value_projection_digest" => baseline.projection_digest.as_slice(),
                        "version_digest" => baseline.raw_digest.as_deref().unwrap(),
                        "value_bytes" => baseline.raw.as_deref().unwrap(),
                        _ => unreachable!(),
                    };
                    control
                        .execute(
                            &sql,
                            &[
                                &collection,
                                &guarded.key(),
                                &OWNER.as_bytes().as_slice(),
                                &correct,
                            ],
                        )
                        .unwrap();
                }
                Err(error) => assert_eq!(error.code().map(|code| code.code()), Some("23514")),
                _ => panic!("corruption fixture did not affect exactly its own row"),
            }
            assert_eq!(physical(&mut control, &guarded), baseline);
        }
        // A caller cannot relabel a known raw witness with another request's
        // public MD5 and cause it to be acknowledged as an unchanged value.
        let collision_input = br#"{"different":"collision-candidate"}"#;
        let collision_token = ContentVersion::from_value(collision_input);
        control.execute("UPDATE public.trnm_storage_objects SET public_version = $4 WHERE collection = $1 AND object_key = $2 AND user_id = $3",
            &[&collection, &guarded.key(), &OWNER.as_bytes().as_slice(), &collision_token.as_str()]).unwrap();
        assert_eq!(
            repository
                .apply_storage_batch(
                    StorageActor::User(OWNER),
                    &[write(&guarded, collision_input, VersionCheck::Any)],
                    49
                )
                .unwrap_err()
                .code(),
            StableCode::DataLoss
        );
        control.execute("UPDATE public.trnm_storage_objects SET public_version = $4 WHERE collection = $1 AND object_key = $2 AND user_id = $3",
            &[&collection, &guarded.key(), &OWNER.as_bytes().as_slice(), &baseline.public_version]).unwrap();
        assert_eq!(physical(&mut control, &guarded), baseline);
        let bad_manifest = key("manifest-integrity");
        seed_unknown(&mut control, &bad_manifest, b"null", "opaque");
        let manifest_baseline = physical(&mut control, &bad_manifest);
        for value in [None, Some(vec![0_u8; 32])] {
            match control.execute("UPDATE public.trnm_storage_objects SET source_manifest_digest = $4 WHERE collection = $1 AND object_key = $2 AND user_id = $3",
                &[&collection, &bad_manifest.key(), &OWNER.as_bytes().as_slice(), &value]) {
                Ok(1) => {
                    assert_eq!(repository.read_storage_object(StorageActor::Server, &bad_manifest).unwrap_err().code(), StableCode::DataLoss);
                    control.execute("UPDATE public.trnm_storage_objects SET source_manifest_digest = $4 WHERE collection = $1 AND object_key = $2 AND user_id = $3",
                        &[&collection, &bad_manifest.key(), &OWNER.as_bytes().as_slice(), &manifest_baseline.manifest]).unwrap();
                }
                Err(error) => assert_eq!(error.code().map(|code| code.code()), Some("23514")),
                _ => panic!("manifest fixture did not affect exactly its own row"),
            }
            assert_eq!(physical(&mut control, &bad_manifest), manifest_baseline);
        }
        control.execute("UPDATE public.trnm_storage_objects SET read_permission = 0 WHERE collection = $1 AND object_key = $2", &[&collection, &bad_manifest.key()]).unwrap();
        for token in [
            "x".repeat(4096),
            format!("{}suffix", baseline.public_version),
            baseline.public_version.to_uppercase(),
        ] {
            assert_ne!(token, baseline.public_version);
            assert_eq!(
                repository
                    .apply_storage_batch(
                        StorageActor::User(OWNER),
                        &[write(
                            &guarded,
                            br#"{"changed":true}"#,
                            VersionCheck::Exact(token.into())
                        )],
                        50
                    )
                    .unwrap_err()
                    .code(),
                StableCode::FailedPrecondition
            );
            assert_eq!(physical(&mut control, &guarded), baseline);
        }
        let native_nul = control
            .query_one("SELECT $1::TEXT", &[&"nul\0condition"])
            .map(|_| None)
            .unwrap_or_else(|error| {
                Some(
                    classify_sqlstate(error.code().expect("native NUL error has SQLSTATE").code())
                        .code(),
                )
            });
        for operation in [
            write(&guarded, raw, VersionCheck::Exact("nul\0condition".into())),
            StorageBatchOperation::Delete(StorageDeleteOperation {
                key: guarded.clone(),
                expected_version: Some("nul\0condition".into()),
            }),
        ] {
            let error = repository
                .apply_storage_batch(StorageActor::User(OWNER), &[operation], 51)
                .unwrap_err();
            assert_eq!(
                error.code(),
                native_nul.unwrap_or(StableCode::FailedPrecondition)
            );
            assert_eq!(physical(&mut control, &guarded), baseline);
        }
        let native_invalid = br#"{"value":"\u0000"}"#;
        let native_error = control
            .query_one(
                "SELECT $1::TEXT::JSONB::TEXT",
                &[&std::str::from_utf8(native_invalid).unwrap()],
            )
            .err();
        // Unknown original-request history cannot bypass the native INSERT
        // parameter cast when MD5 and ACLs would otherwise select a blind no-op.
        let noop_native_invalid = key("unknown-matching-native-input");
        seed_unknown(
            &mut control,
            &noop_native_invalid,
            br#"{"historical":true}"#,
            ContentVersion::from_value(native_invalid).as_str(),
        );
        let noop_before = physical(&mut control, &noop_native_invalid);
        let noop_result = repository.apply_storage_batch(
            StorageActor::User(OWNER),
            &[write(
                &noop_native_invalid,
                native_invalid,
                VersionCheck::Any,
            )],
            52,
        );
        match &native_error {
            Some(error) => assert_eq!(
                noop_result.unwrap_err().code(),
                classify_sqlstate(error.code().unwrap().code()).code()
            ),
            None => assert_eq!(
                noop_result.unwrap()[0].current_version,
                Some(ContentVersion::from_value(native_invalid))
            ),
        }
        assert_eq!(physical(&mut control, &noop_native_invalid), noop_before);
        println!(
            "\nstorage_native_noop_input_executed profile={} native_accepted={}",
            profile.metadata_value(),
            native_error.is_none()
        );
        // Keep this separately exercised history out of the later ordered page
        // whose only sentinel is the deliberately over-budget native object.
        control.execute("UPDATE public.trnm_storage_objects SET read_permission=0 WHERE collection=$1 AND object_key=$2",&[&collection,&noop_native_invalid.key()]).unwrap();

        if let Some(error) = native_error {
            let expected = classify_sqlstate(
                error
                    .code()
                    .expect("native JSONB error has SQLSTATE")
                    .code(),
            )
            .code();
            let later = key("zz-native-invalid");
            let failed = repository
                .apply_storage_batch(
                    StorageActor::User(OWNER),
                    &[
                        write(&guarded, br#"{"changed":true}"#, VersionCheck::Any),
                        write(&later, native_invalid, VersionCheck::MustNotExist),
                    ],
                    52,
                )
                .unwrap_err();
            assert_eq!(failed.code(), expected);
            assert_eq!(physical(&mut control, &guarded), baseline);
            assert_eq!(
                repository
                    .read_storage_object(StorageActor::Server, &later)
                    .unwrap_err()
                    .code(),
                StableCode::NotFound
            );
        } else {
            let accepted = key("native-accepted-nul-json");
            let receipt = repository
                .apply_storage_batch(
                    StorageActor::User(OWNER),
                    &[write(&accepted, native_invalid, VersionCheck::MustNotExist)],
                    52,
                )
                .unwrap();
            assert_eq!(
                receipt[0].current_version,
                Some(ContentVersion::from_value(native_invalid))
            );
            assert_eq!(
                repository
                    .read_storage_object(StorageActor::Server, &accepted)
                    .unwrap()
                    .value,
                native(&mut control, native_invalid)
            );
            control.execute("UPDATE public.trnm_storage_objects SET read_permission = 0 WHERE collection = $1 AND object_key = $2", &[&collection, &accepted.key()]).unwrap();
        }
        // An unrelated late stale condition must roll back JSONB, raw witness,
        // version, ACL and precise timestamp changes made by an earlier write.
        let missing = key("zz-missing-occ");
        let failed = repository
            .apply_storage_batch(
                StorageActor::User(OWNER),
                &[
                    write(&guarded, br#"{"changed":true}"#, VersionCheck::Any),
                    write(&missing, raw, VersionCheck::Exact("absent".into())),
                ],
                53,
            )
            .unwrap_err();
        assert_eq!(failed.code(), StableCode::FailedPrecondition);
        assert_eq!(physical(&mut control, &guarded), baseline);

        if profile == DatabaseProfile::PostgreSql {
            // Valid PostgreSQL numeric expansion makes a tiny raw request render
            // above 1MiB. The old request-byte read cap must not reject that row.
            let request =
                format!("{{\"values\":[{}]}}", vec!["1e70000"; 16].join(",")).into_bytes();
            let projected = native(&mut control, &request);
            assert!(
                request.len() < 1024 * 1024
                    && projected.len() > 1024 * 1024
                    && projected.len() < 16 * 1024 * 1024
            );
            let big = key("large-native");
            repository
                .apply_storage_batch(
                    StorageActor::User(OWNER),
                    &[write(&big, &request, VersionCheck::Any)],
                    60,
                )
                .unwrap();
            assert_eq!(
                repository
                    .read_storage_object(StorageActor::User(OWNER), &big)
                    .unwrap()
                    .value,
                projected
            );
            let repetitions = (32 * 1024 * 1024) / projected.len() + 1;
            assert!(repetitions <= 100);
            assert_eq!(
                repository
                    .read_storage_objects(
                        StorageActor::User(OWNER),
                        &vec![big.clone(); repetitions]
                    )
                    .unwrap_err()
                    .code(),
                StableCode::ResourceExhausted
            );
            control.execute("UPDATE public.trnm_storage_objects SET read_permission = 0 WHERE collection = $1 AND object_key = $2", &[&collection, &big.key()]).unwrap();
            let over_budget = key("zz-over-native-budget");
            let large_request =
                format!("{{\"values\":[{}]}}", vec!["1e70000"; 240].join(",")).into_bytes();
            assert!(native(&mut control, &large_request).len() > 16 * 1024 * 1024);
            // A legitimate native expansion over the output budget rejects the
            // whole mutation batch and preserves an earlier staged update.
            let expansion_error = repository
                .apply_storage_batch(
                    StorageActor::User(OWNER),
                    &[
                        write(&guarded, br#"{"budgetStage":true}"#, VersionCheck::Any),
                        write(&over_budget, &large_request, VersionCheck::MustNotExist),
                    ],
                    61,
                )
                .unwrap_err();
            assert_eq!(expansion_error.code(), StableCode::ResourceExhausted);
            assert_eq!(physical(&mut control, &guarded), baseline);
            assert_eq!(
                repository
                    .read_storage_object(StorageActor::Server, &over_budget)
                    .unwrap_err()
                    .code(),
                StableCode::NotFound
            );
            seed_unknown(
                &mut control,
                &over_budget,
                &large_request,
                "oversized-native",
            );
            assert_eq!(
                repository
                    .read_storage_object(StorageActor::Server, &over_budget)
                    .unwrap_err()
                    .code(),
                StableCode::ResourceExhausted
            );
            let page = repository
                .list_storage_objects_nakama(
                    StorageActor::User(OTHER),
                    &collection,
                    Some(OWNER),
                    None,
                    1,
                )
                .unwrap();
            assert_eq!(page.objects.len(), 1);
            assert_eq!(page.objects[0].key, guarded);
            assert!(page.next.is_some());
            assert_eq!(
                repository
                    .list_storage_objects_nakama(
                        StorageActor::User(OTHER),
                        &collection,
                        Some(OWNER),
                        page.next.as_ref(),
                        1
                    )
                    .unwrap_err()
                    .code(),
                StableCode::ResourceExhausted
            );
            control.execute("UPDATE public.trnm_storage_objects SET read_permission = 0 WHERE collection = $1 AND object_key = $2", &[&collection, &over_budget.key()]).unwrap();
            assert!(repository
                .read_storage_objects(StorageActor::User(OTHER), &[over_budget])
                .unwrap()
                .is_empty());
            println!("storage_jsonb_postgresql_native_render_over_request_budget=true");
        }
    }));
    control
        .execute(
            "DELETE FROM public.trnm_storage_objects WHERE collection = $1",
            &[&collection],
        )
        .expect("native JSONB fixture scoped cleanup failed");
    let remaining: i64 = control
        .query_one(
            "SELECT count(*) FROM public.trnm_storage_objects WHERE collection = $1",
            &[&collection],
        )
        .unwrap()
        .get(0);
    assert_eq!(remaining, 0);
    if let Err(payload) = outcome {
        std::panic::resume_unwind(payload);
    }
    println!(
        "\nstorage_native_jsonb_live_executed profile={}",
        profile.metadata_value()
    );
}
