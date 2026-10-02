// Historical fixture construction uses the immutable v1/v2 SQL and exact
// two-file chain digest. Only the real current runner performs the v3 cutover.
// These tests grant no import, compatibility, race-CAS or acceptance credit.
const V3_SHAPE_CASES: usize = 8;
const V3_ILLEGAL_CASES: usize = 9;
const V3_CATALOG_DRIFT_CASES: usize = 6;
const V3_PARTIAL_CASES: usize = 3;
const V3_METADATA_CASES: usize = 9;
const V3_OPAQUE_CASES: usize = 6;

fn historical_v2_digest(profile: DatabaseProfile) -> &'static str {
    // Source control checks recompute these prefixes from the first two Git
    // blobs in MIGRATION_CHAIN.lock.json; the v3 digest is never rebound as v2.
    match profile {
        DatabaseProfile::PostgreSql => {
            "b063c33fce9a7c3c506f82c0204b545d8a5ef915b0ff234680fca15057c4a9df"
        }
        DatabaseProfile::CockroachDb => {
            "85892562d78571090202d5b2d6bb2941364614399a82542f4cf809370c0e4b0c"
        }
    }
}

fn install_v2(fixture: &IsolatedDatabase) {
    install_v1(fixture, true);
    let migration = match fixture.profile {
        DatabaseProfile::PostgreSql => {
            include_str!("../../../../migrations/postgresql/0002_storage_timestamps_up.sql")
        }
        DatabaseProfile::CockroachDb => {
            include_str!("../../../../migrations/cockroachdb/0002_storage_timestamps_up.sql")
        }
    };
    let mut client = fixture.client();
    client.batch_execute(migration).unwrap();
    assert_eq!(
        client
            .execute(
                "UPDATE public.trnm_schema_metadata SET schema_version=2,chain_digest=$1, \
         digest_algorithm='ordered-path-git-blob-sha256.v1',storage_writer_epoch=2, \
         upgrade_source_commit=$2 WHERE singleton=1",
                &[&historical_v2_digest(fixture.profile), &V2_PUBLISHER_SOURCE],
            )
            .unwrap(),
        1
    );
}

fn seed_v2_value(client: &mut Client, key: &str, raw: &[u8], known_times: bool) {
    let digest = IntegrityDigest::from_value(raw);
    let create = known_times.then_some("1969-12-31 23:59:59.999999+00");
    let update = known_times.then_some("2024-02-29 00:00:00.123456+00");
    assert_eq!(client.execute(
        "INSERT INTO public.trnm_storage_objects \
         (collection,object_key,user_id,value_bytes,version_digest,read_permission,write_permission, \
          updated_at_ms,create_time,update_time) \
         VALUES ('v3-upgrade',$1,$2,$3,$4,2,1,42,$5::TEXT::TIMESTAMPTZ,$6::TEXT::TIMESTAMPTZ)",
        &[&key,&[0x11_u8;16].as_slice(),&raw,&digest.get().as_bytes().as_slice(),&create,&update],
    ).unwrap(), 1);
}

#[derive(Debug, Eq, PartialEq)]
struct EntireDatabaseSnapshot {
    catalog: Vec<Vec<Option<String>>>,
    constraints: Vec<Vec<Option<String>>>,
    data: Vec<(String, Vec<Vec<Option<String>>>)>,
}

fn text_rows(client: &mut Client, sql: &str) -> Vec<Vec<Option<String>>> {
    client
        .query(sql, &[])
        .unwrap()
        .into_iter()
        .map(|row| {
            (0..row.len())
                .map(|column| row.get::<_, Option<String>>(column))
                .collect()
        })
        .collect()
}

fn entire_database_snapshot(client: &mut Client) -> EntireDatabaseSnapshot {
    let catalog = text_rows(client,
        "SELECT table_name::TEXT,column_name::TEXT,udt_name::TEXT,is_nullable::TEXT, \
         column_default::TEXT,character_maximum_length::TEXT FROM information_schema.columns \
         WHERE table_schema='public' AND left(table_name,5)='trnm_' ORDER BY table_name,ordinal_position");
    let constraints = text_rows(
        client,
        "SELECT t.relname::TEXT,c.conname::TEXT,c.contype::TEXT,c.convalidated::TEXT, \
         pg_catalog.pg_get_constraintdef(c.oid)::TEXT FROM pg_catalog.pg_constraint c \
         JOIN pg_catalog.pg_class t ON t.oid=c.conrelid \
         JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace \
         WHERE n.nspname='public' AND left(t.relname,5)='trnm_' ORDER BY t.relname,c.conname",
    );
    let mut data = Vec::new();
    for table in TABLES {
        let columns: Vec<(String, String)> = client
            .query(
                "SELECT column_name::TEXT,udt_name::TEXT FROM information_schema.columns \
             WHERE table_schema='public' AND table_name=$1 ORDER BY ordinal_position",
                &[table],
            )
            .unwrap()
            .into_iter()
            .map(|row| (row.get(0), row.get(1)))
            .collect();
        // Everything is cast by the database, including raw binary and exact
        // timestamp text. This snapshot is never an original-request export.
        let projections = columns
            .iter()
            .map(|(column, kind)| {
                if kind == "bytea" {
                    format!("encode(\"{column}\",'hex')::TEXT")
                } else if kind == "timestamptz" {
                    // CR can persist an infinity value whose TEXT cast fails.
                    // Preserve native calendar fields and exact fractional
                    // seconds instead of reducing distinct invalid times to
                    // a common sentinel. Every original row remains compared.
                    let fields = ["year", "month", "day", "hour", "minute", "second", "timezone"]
                        .into_iter()
                        .map(|field| format!("extract({field} FROM \"{column}\")::TEXT"))
                        .collect::<Vec<_>>()
                        .join(",");
                    format!("CASE WHEN \"{column}\" IS NULL THEN NULL ELSE jsonb_build_array({fields})::TEXT END")
                } else {
                    format!("\"{column}\"::TEXT")
                }
            })
            .collect::<Vec<_>>()
            .join(",");
        let order = columns
            .iter()
            .enumerate()
            .map(|(index, _)| (index + 1).to_string())
            .collect::<Vec<_>>()
            .join(",");
        data.push((
            (*table).to_owned(),
            text_rows(
                client,
                &format!("SELECT {projections} FROM public.{table} ORDER BY {order}"),
            ),
        ));
    }
    EntireDatabaseSnapshot {
        catalog,
        constraints,
        data,
    }
}

fn v3_first_six_actions(fixture: &IsolatedDatabase, count: usize) {
    assert!((1..=6).contains(&count));
    let source = match fixture.profile {
        DatabaseProfile::PostgreSql => {
            include_str!("../../../../migrations/postgresql/0003_storage_jsonb_up.sql")
        }
        DatabaseProfile::CockroachDb => {
            include_str!("../../../../migrations/cockroachdb/0003_storage_jsonb_up.sql")
        }
    };
    let mut client = fixture.client();
    let actions: Vec<&str> = source
        .split("-- trnm:action ")
        .skip(1)
        .take(count)
        .map(|block| block.split_once('\n').unwrap().1.trim())
        .collect();
    assert_eq!(actions.len(), count);
    for action in actions {
        assert!(action.starts_with("ALTER TABLE ") && action.ends_with(';'));
        client.batch_execute(action).unwrap();
    }
}

fn assert_published_v3(
    report: &trnm_persistence_pg::SchemaMigrationReport,
    profile: DatabaseProfile,
) {
    assert!(report.migration_applied);
    assert_eq!(report.applied_steps, 1);
    assert_eq!(report.identity.schema_version, 3);
    assert_eq!(report.identity.storage_writer_epoch, 3);
    assert_eq!(report.identity.source_commit, ORIGINAL_SOURCE);
    assert_eq!(report.identity.upgrade_source_commit, UPGRADE_SOURCE);
    assert_eq!(report.identity.v2_apply_source_commit, V2_PUBLISHER_SOURCE);
    assert_eq!(
        report.identity.chain_digest,
        authoritative_chain_digest(profile)
    );
}

fn v3_preserves_native_legacy_shapes_and_v2_history(environment: &LiveEnvironment) {
    let shapes: [&[u8]; V3_SHAPE_CASES] = [
        b" { \"object\" : true } ",
        b"{\"z\":1,\"a\":2}",
        b"{\"duplicate\":1,\"duplicate\":2}",
        b"{\"number\":1.20e3}",
        br#"{"escaped":"\u0061\n\t"}"#,
        b"[1, true, null, {\"a\":2}]",
        b"\"legacy scalar\"",
        b"null",
    ];
    with_database(environment, |fixture| {
        install_v2(fixture);
        let legacy = fixture.legacy_writer();
        let mut inspector = fixture.client();
        for (index, raw) in shapes.iter().enumerate() {
            seed_v2_value(
                &mut inspector,
                &format!("shape-{index}"),
                raw,
                index % 2 == 0,
            );
        }
        fixture.revoke_legacy_writes(&legacy);
        let before = legacy_storage_snapshot(&mut inspector);
        let old_times = text_rows(
            &mut inspector,
            "SELECT object_key::TEXT,create_time::TEXT,update_time::TEXT \
            FROM public.trnm_storage_objects ORDER BY object_key",
        );
        let mut repository = fixture.repository();
        assert_eq!(
            repository.verify_authoritative_schema().unwrap_err().code(),
            StableCode::FailedPrecondition
        );
        let report = repository
            .migrate_authoritative_schema(UPGRADE_SOURCE, 23, Some(&legacy))
            .unwrap();
        assert_published_v3(&report, fixture.profile);
        assert_eq!(legacy_storage_snapshot(&mut inspector), before);
        assert_eq!(
            text_rows(
                &mut inspector,
                "SELECT object_key::TEXT,create_time::TEXT,update_time::TEXT \
            FROM public.trnm_storage_objects ORDER BY object_key"
            ),
            old_times
        );
        let metadata = metadata_snapshot(&mut inspector);
        assert_eq!(metadata.3, 17);
        assert_eq!(metadata.8, V2_PUBLISHER_SOURCE);
        let mut different_render_md5 = false;
        for (index, raw) in shapes.iter().enumerate() {
            let raw_text = std::str::from_utf8(raw).unwrap();
            let projected: String = inspector
                .query_one("SELECT $1::TEXT::JSONB::TEXT", &[&raw_text])
                .unwrap()
                .get(0);
            let key = format!("shape-{index}");
            let row = inspector.query_one("SELECT value_jsonb::TEXT,public_version::TEXT,value_projection_digest, \
                value_origin::TEXT,source_manifest_digest,value_bytes,version_digest FROM public.trnm_storage_objects \
                WHERE object_key=$1", &[&key]).unwrap();
            assert_eq!(row.get::<_, String>(0), projected);
            assert_eq!(
                row.get::<_, String>(1),
                ContentVersion::from_value(raw).as_str()
            );
            assert_eq!(
                row.get::<_, Vec<u8>>(2),
                IntegrityDigest::from_value(projected.as_bytes())
                    .get()
                    .as_bytes()
            );
            assert_eq!(row.get::<_, String>(3), "legacy-rust-v2-bytes");
            assert!(row.get::<_, Option<Vec<u8>>>(4).is_none());
            assert_eq!(row.get::<_, Vec<u8>>(5), *raw);
            assert_eq!(
                row.get::<_, Vec<u8>>(6),
                IntegrityDigest::from_value(raw).get().as_bytes()
            );
            different_render_md5 |=
                ContentVersion::from_value(raw) != ContentVersion::from_value(projected.as_bytes());
            let storage_key =
                StorageObjectKey::new("v3-upgrade", key, UserId::new([0x11; 16])).unwrap();
            let stored = repository
                .read_storage_object_with_metadata(StorageActor::Server, &storage_key)
                .unwrap();
            assert_eq!(stored.object.value, projected.as_bytes());
            let witness = stored.object.collision_witness.as_ref().unwrap();
            assert!(witness.matches_request(raw));
            stored.object.verify_integrity().unwrap();
            assert_eq!(stored.times.create.is_some(), index % 2 == 0);
            assert_eq!(stored.times.update.is_some(), index % 2 == 0);
        }
        assert!(
            different_render_md5,
            "request MD5 must not be replaced by native-render MD5"
        );
        let published = entire_database_snapshot(&mut inspector);
        assert_eq!(
            repository.verify_authoritative_schema().unwrap(),
            report.identity
        );
        let repeat = repository
            .migrate_authoritative_schema(LATER_BINARY_SOURCE, 99, None)
            .unwrap();
        assert!(!repeat.migration_applied);
        assert_eq!(repeat.applied_steps, 0);
        assert_eq!(entire_database_snapshot(&mut inspector), published);
    });
    println!(
        "\nschema_v3_shapes_executed profile={} extra_cases={V3_SHAPE_CASES}",
        environment.profile.metadata_value()
    );
}

fn v3_illegal_legacy_preflight_cases(environment: &LiveEnvironment) {
    // The immutable v2 raw-byte CHECK already rejects requests above 1MiB.
    // Exercise a real storable legacy request whose legal PG native projection
    // exceeds 16MiB instead; CR parser acceptance is observed independently.
    let over_budget = format!("{{\"values\":[{}]}}", vec!["1e131071"; 129].join(",")).into_bytes();
    assert!(over_budget.len() < 1024 * 1024);
    let illegal = [
        ("empty", Vec::new()),
        ("binary", vec![0, 1, 2, 3]),
        ("non_utf8", vec![b'"', 0xff, b'"']),
        ("malformed", b"{\"missing\":".to_vec()),
        (
            "native_number_range",
            b"1e9999999999999999999999999999".to_vec(),
        ),
        ("wrong_raw_digest", b"{\"valid\":true}".to_vec()),
        ("native_projection_budget", over_budget),
    ];
    assert_eq!(illegal.len() + 2, V3_ILLEGAL_CASES);
    for (label, raw) in illegal {
        with_database(environment, |fixture| {
            install_v2(fixture);
            let legacy = fixture.legacy_writer();
            let mut inspector = fixture.client();
            seed_v2_value(&mut inspector, "a-valid", b"{\"valid\":1}", true);
            seed_v2_value(&mut inspector, "z-invalid", &raw, false);
            if label == "wrong_raw_digest" {
                inspector.execute("UPDATE public.trnm_storage_objects SET version_digest=$1 WHERE object_key='z-invalid'",&[&[0x7f_u8;32].as_slice()]).unwrap();
            }
            // The range case is observed using the profile's real parser.
            if label == "native_number_range" {
                let raw_text = std::str::from_utf8(&raw).unwrap();
                let native_error = inspector
                    .query_one("SELECT $1::TEXT::JSONB::TEXT", &[&raw_text])
                    .unwrap_err();
                assert!(native_error
                    .as_db_error()
                    .unwrap()
                    .code()
                    .code()
                    .starts_with("22"));
            }
            let expected_code = if label == "native_projection_budget" {
                match inspector.query_one(
                    "SELECT octet_length($1::TEXT::JSONB::TEXT)::BIGINT",
                    &[&std::str::from_utf8(&raw).unwrap()],
                ) {
                    Ok(row) => {
                        assert!(row.get::<_, i64>(0) > 16 * 1024 * 1024);
                        StableCode::ResourceExhausted
                    }
                    Err(error) => {
                        assert!(error.code().unwrap().code().starts_with("22"));
                        StableCode::DataLoss
                    }
                }
            } else {
                StableCode::DataLoss
            };
            fixture.revoke_legacy_writes(&legacy);
            let before = entire_database_snapshot(&mut inspector);
            let mut repository = fixture.repository();
            let rejected = repository
                .migrate_authoritative_schema(UPGRADE_SOURCE, 23, Some(&legacy))
                .unwrap_err();
            assert_eq!(rejected.code(), expected_code, "{label}");
            assert_eq!(
                entire_database_snapshot(&mut inspector),
                before,
                "{label}: preflight executed DDL or data/metadata mutation"
            );
        });
    }
    for (label, literal) in [
        ("infinity", "infinity"),
        ("protobuf_range", "10000-01-01 00:00:00+00"),
    ] {
        with_database(environment, |fixture| {
            install_v2(fixture);
            let legacy = fixture.legacy_writer();
            fixture.revoke_legacy_writes(&legacy);
            let mut inspector = fixture.client();
            seed_v2_value(&mut inspector, "history", b"{\"valid\":true}", false);
            let before_native = entire_database_snapshot(&mut inspector);
            match inspector.execute(
                "UPDATE public.trnm_storage_objects SET create_time=$1::TEXT::TIMESTAMPTZ",
                &[&literal],
            ) {
                Ok(changed) => {
                    assert_eq!(changed, 1);
                    let before = entire_database_snapshot(&mut inspector);
                    let error = fixture
                        .repository()
                        .migrate_authoritative_schema(UPGRADE_SOURCE, 23, Some(&legacy))
                        .unwrap_err();
                    assert_eq!(error.code(), StableCode::DataLoss, "{label}");
                    assert_eq!(
                        entire_database_snapshot(&mut inspector),
                        before,
                        "{label}: timestamp preflight executed DDL"
                    );
                    println!("\nschema_v3_legacy_timestamp_probe profile={} input={label} native_stored=true",fixture.profile.metadata_value());
                }
                Err(error) => {
                    let state = error
                        .as_db_error()
                        .expect("timestamp admission must fail in database parser")
                        .code()
                        .code();
                    assert!(
                        state.starts_with("22"),
                        "unexpected timestamp probe SQLSTATE {state}"
                    );
                    assert_eq!(entire_database_snapshot(&mut inspector), before_native);
                    println!("\nschema_v3_legacy_timestamp_probe profile={} input={label} native_stored=false sqlstate={state}",fixture.profile.metadata_value());
                }
            }
        });
    }
    println!(
        "\nschema_v3_illegal_legacy_executed profile={} extra_cases={V3_ILLEGAL_CASES}",
        environment.profile.metadata_value()
    );
}

fn v3_ready_catalog_drift_cases(environment: &LiveEnvironment) {
    for drift in [
        "set_not_null",
        "varchar_width",
        "projection_check",
        "origin_check",
        "metadata_check",
        "primary_key",
    ] {
        with_database(environment, |fixture| {
            let mut repository = fixture.repository();
            repository
                .migrate_authoritative_schema(UPGRADE_SOURCE, 23, None)
                .unwrap();
            let mut inspector = fixture.client();
            match drift {
                "set_not_null" => inspector.batch_execute("ALTER TABLE public.trnm_storage_objects ALTER COLUMN value_jsonb DROP NOT NULL").unwrap(),
                "varchar_width" => inspector.batch_execute("ALTER TABLE public.trnm_storage_objects ALTER COLUMN public_version TYPE VARCHAR(31)").unwrap(),
                "projection_check" => inspector.batch_execute("ALTER TABLE public.trnm_storage_objects DROP CONSTRAINT storage_projection_digest; \
                    ALTER TABLE public.trnm_storage_objects ADD CONSTRAINT storage_projection_digest CHECK (octet_length(value_projection_digest)>=0)").unwrap(),
                "origin_check" => inspector.batch_execute("ALTER TABLE public.trnm_storage_objects DROP CONSTRAINT storage_origin_witness").unwrap(),
                "metadata_check" => inspector.batch_execute("ALTER TABLE public.trnm_schema_metadata DROP CONSTRAINT metadata_v2_history").unwrap(),
                "primary_key" if fixture.profile==DatabaseProfile::CockroachDb => inspector.batch_execute(
                    "ALTER TABLE public.trnm_storage_objects ALTER PRIMARY KEY USING COLUMNS (object_key,collection,user_id)").unwrap(),
                "primary_key" => {
                    let name:String=inspector.query_one("SELECT c.conname::TEXT FROM pg_catalog.pg_constraint c JOIN pg_catalog.pg_class t ON t.oid=c.conrelid \
                        JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='public' AND t.relname='trnm_storage_objects' AND c.contype='p'",&[]).unwrap().get(0);
                    inspector.batch_execute(&format!("ALTER TABLE public.trnm_storage_objects DROP CONSTRAINT \"{name}\"; \
                        ALTER TABLE public.trnm_storage_objects ADD PRIMARY KEY(object_key,collection,user_id)")).unwrap();
                },
                _=>unreachable!(),
            }
            let before = entire_database_snapshot(&mut inspector);
            assert_eq!(
                repository.verify_authoritative_schema().unwrap_err().code(),
                StableCode::FailedPrecondition,
                "{drift}"
            );
            assert_eq!(
                repository
                    .migrate_authoritative_schema(LATER_BINARY_SOURCE, 99, None)
                    .unwrap_err()
                    .code(),
                StableCode::FailedPrecondition,
                "{drift}"
            );
            assert_eq!(
                entire_database_snapshot(&mut inspector),
                before,
                "{drift}: drift was repaired implicitly"
            );
        });
    }
    println!(
        "\nschema_v3_catalog_drift_executed profile={} extra_cases={V3_CATALOG_DRIFT_CASES}",
        environment.profile.metadata_value()
    );
}

fn v3_partial_prefix_and_real_backfill_resume(environment: &LiveEnvironment) {
    for early in [true, false] {
        with_database(environment, |fixture| {
            install_v2(fixture);
            let legacy = fixture.legacy_writer();
            fixture.revoke_legacy_writes(&legacy);
            let mut inspector = fixture.client();
            seed_v2_value(&mut inspector, "damaged", b"{\"old\":true}", true);
            v3_first_six_actions(fixture, if early { 2 } else { 6 });
            inspector
                .batch_execute(
                    "UPDATE public.trnm_storage_objects SET value_jsonb='{\"wrong\":true}'::JSONB",
                )
                .unwrap();
            let before = entire_database_snapshot(&mut inspector);
            let rejected = fixture
                .repository()
                .migrate_authoritative_schema(UPGRADE_SOURCE, 23, Some(&legacy))
                .unwrap_err();
            assert_eq!(rejected.code(), StableCode::DataLoss);
            assert_eq!(rejected.reason(), "schema_storage_partial_backfill_drift");
            assert_eq!(
                entire_database_snapshot(&mut inspector),
                before,
                "damaged prefix advanced before global preflight"
            );
        });
    }
    with_database(environment, |fixture| {
        install_v2(fixture);
        let legacy = fixture.legacy_writer();
        fixture.revoke_legacy_writes(&legacy);
        let mut inspector = fixture.client();
        seed_v2_value(&mut inspector, "a-commits-first", b"[1,2]", true);
        seed_v2_value(&mut inspector, "b-forced-stop", b"null", false);
        v3_first_six_actions(fixture, 6);
        // This isolated extra check forces the real typed UPDATE to fail on
        // row two. CR commits row one; PostgreSQL rolls the revision back.
        // Removing the fixture-only obstruction is an explicit operator step.
        inspector
            .batch_execute(
                "ALTER TABLE public.trnm_storage_objects ADD CONSTRAINT fixture_stop_backfill \
            CHECK (object_key <> 'b-forced-stop' OR value_jsonb IS NULL)",
            )
            .unwrap();
        let before = entire_database_snapshot(&mut inspector);
        let before_metadata=text_rows(&mut inspector,"SELECT singleton::TEXT,schema_version::TEXT,profile::TEXT,source_commit::TEXT,applied_at_ms::TEXT, \
            chain_digest::TEXT,digest_algorithm::TEXT,storage_writer_epoch::TEXT,upgrade_source_commit::TEXT,v2_apply_source_commit::TEXT \
            FROM public.trnm_schema_metadata");
        let mut repository = fixture.repository();
        let rejected = repository
            .migrate_authoritative_schema(UPGRADE_SOURCE, 23, Some(&legacy))
            .unwrap_err();
        assert_eq!(rejected.code(), StableCode::InvalidArgument);
        assert_eq!(rejected.reason(), "database_constraint_violation");
        assert_eq!(text_rows(&mut inspector,"SELECT singleton::TEXT,schema_version::TEXT,profile::TEXT,source_commit::TEXT,applied_at_ms::TEXT, \
            chain_digest::TEXT,digest_algorithm::TEXT,storage_writer_epoch::TEXT,upgrade_source_commit::TEXT,v2_apply_source_commit::TEXT \
            FROM public.trnm_schema_metadata"),before_metadata);
        let after = entire_database_snapshot(&mut inspector);
        assert_eq!(after.catalog, before.catalog);
        assert_eq!(after.constraints, before.constraints);
        let first:bool=inspector.query_one("SELECT value_jsonb IS NOT NULL FROM public.trnm_storage_objects WHERE object_key='a-commits-first'",&[]).unwrap().get(0);
        assert_eq!(first, fixture.profile == DatabaseProfile::CockroachDb);
        let second:bool=inspector.query_one("SELECT value_jsonb IS NULL FROM public.trnm_storage_objects WHERE object_key='b-forced-stop'",&[]).unwrap().get(0);
        assert!(second);
        if fixture.profile == DatabaseProfile::PostgreSql {
            assert_eq!(after, before);
        }
        inspector
            .batch_execute(
                "ALTER TABLE public.trnm_storage_objects DROP CONSTRAINT fixture_stop_backfill",
            )
            .unwrap();
        let report = repository
            .migrate_authoritative_schema(UPGRADE_SOURCE, 23, Some(&legacy))
            .unwrap();
        assert_published_v3(&report, fixture.profile);
        let complete: i64 = inspector
            .query_one(
                "SELECT count(*) FROM public.trnm_storage_objects WHERE value_jsonb IS NOT NULL \
            AND value_origin='legacy-rust-v2-bytes' AND source_manifest_digest IS NULL",
                &[],
            )
            .unwrap()
            .get(0);
        assert_eq!(complete, 2);
        assert_eq!(
            repository.verify_authoritative_schema().unwrap(),
            report.identity
        );
    });
    println!(
        "\nschema_v3_partial_resume_executed profile={} extra_cases={V3_PARTIAL_CASES}",
        environment.profile.metadata_value()
    );
}

fn v3_recorded_metadata_negative_cases(environment: &LiveEnvironment) {
    // These are persisted-input validation cases, not a concurrency proof of
    // the nine-field NULL-safe publication CAS. A valid provenance replacement
    // before the runner reads metadata has no historical tuple to compare.
    let mutations = [
        ("schema_version", "schema_version=4"),
        ("profile", "profile='unsupported'"),
        ("source_commit", "source_commit=repeat('z',40)"),
        ("applied_at_ms", "applied_at_ms=-1"),
        ("chain_digest", "chain_digest=repeat('0',64)"),
        ("digest_algorithm", "digest_algorithm='unrecognized'"),
        ("storage_writer_epoch", "storage_writer_epoch=3"),
        (
            "upgrade_source_commit",
            "upgrade_source_commit=repeat('z',40)",
        ),
        (
            "v2_apply_source_commit",
            "v2_apply_source_commit=repeat('d',40)",
        ),
    ];
    assert_eq!(mutations.len(), V3_METADATA_CASES);
    for (field, assignment) in mutations {
        with_database(environment, |fixture| {
            install_v2(fixture);
            let legacy = fixture.legacy_writer();
            fixture.revoke_legacy_writes(&legacy);
            let mut inspector = fixture.client();
            seed_v2_value(&mut inspector, "history", b"true", false);
            v3_first_six_actions(fixture, 1);
            if matches!(field, "profile" | "applied_at_ms") {
                // Explicit isolated catalog corruption lets the shared runner
                // see invalid persisted metadata that normal CHECKs prevent.
                let checks:Vec<(String,String)>=inspector.query("SELECT c.conname::TEXT,pg_catalog.pg_get_constraintdef(c.oid)::TEXT \
                    FROM pg_catalog.pg_constraint c JOIN pg_catalog.pg_class t ON t.oid=c.conrelid \
                    JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace \
                    WHERE n.nspname='public' AND t.relname='trnm_schema_metadata' AND c.contype='c'",&[])
                    .unwrap().into_iter().map(|row|(row.get(0),row.get(1))).collect();
                let mut removed = 0;
                for (name, definition) in checks {
                    if definition.contains(field) {
                        inspector.batch_execute(&format!("ALTER TABLE public.trnm_schema_metadata DROP CONSTRAINT \"{name}\"")).unwrap();
                        removed += 1;
                    }
                }
                assert_eq!(removed, 1);
            }
            inspector
                .batch_execute(&format!(
                    "UPDATE public.trnm_schema_metadata SET {assignment} WHERE singleton=1"
                ))
                .unwrap();
            let before = entire_database_snapshot(&mut inspector);
            let rejected = fixture
                .repository()
                .migrate_authoritative_schema(UPGRADE_SOURCE, 23, Some(&legacy))
                .unwrap_err();
            assert_eq!(rejected.code(), StableCode::FailedPrecondition, "{field}");
            assert_eq!(
                entire_database_snapshot(&mut inspector),
                before,
                "{field}: invalid metadata was rebound"
            );
        });
    }
    println!(
        "\nschema_v3_metadata_validation_executed profile={} extra_cases={V3_METADATA_CASES}",
        environment.profile.metadata_value()
    );
}

fn v3_unknown_opaque_native_history(environment: &LiveEnvironment) {
    let tokens = [
        "",
        "UPPERCASE",
        "not-hex",
        "*",
        "界界界界界界界界界界界界界界界界界界界界界界界界界界界界界界界界",
        "opaque-legacy",
    ];
    let values = ["{}", "[1,null]", "null", "1.2e3", "\"scalar\"", "true"];
    assert_eq!(tokens.len(), V3_OPAQUE_CASES);
    with_database(environment, |fixture| {
        let mut repository = fixture.repository();
        repository
            .migrate_authoritative_schema(UPGRADE_SOURCE, 23, None)
            .unwrap();
        let mut inspector = fixture.client();
        for (index, (token, value)) in tokens.iter().zip(values.iter()).enumerate() {
            let native: String = inspector
                .query_one("SELECT $1::TEXT::JSONB::TEXT", &[value])
                .unwrap()
                .get(0);
            let digest = IntegrityDigest::from_value(native.as_bytes());
            let key = format!("unknown-{index}");
            inspector.execute("INSERT INTO public.trnm_storage_objects \
                (collection,object_key,user_id,value_bytes,version_digest,read_permission,write_permission,updated_at_ms, \
                 value_jsonb,public_version,value_projection_digest,value_origin,source_manifest_digest) \
                VALUES ('v3-upgrade',$1,$2,NULL,NULL,2,1,42,$3::TEXT::JSONB,$4,$5,'nakama-export-unknown-request',$6)",
                &[&key,&[0x11_u8;16].as_slice(),value,token,&digest.get().as_bytes().as_slice(),&[0x7e_u8;32].as_slice()]).unwrap();
            let storage_key =
                StorageObjectKey::new("v3-upgrade", key, UserId::new([0x11; 16])).unwrap();
            let stored = repository
                .read_storage_object_with_metadata(StorageActor::Server, &storage_key)
                .unwrap();
            assert_eq!(stored.object.version.as_str(), *token);
            assert_eq!(stored.object.value, native.as_bytes());
            assert!(stored.object.collision_witness.is_none());
            assert!(stored.times.create.is_none() && stored.times.update.is_none());
            stored.object.verify_integrity().unwrap();
        }
        let before = entire_database_snapshot(&mut inspector);
        repository.verify_authoritative_schema().unwrap();
        let repeated = repository
            .migrate_authoritative_schema(LATER_BINARY_SOURCE, 99, None)
            .unwrap();
        assert!(!repeated.migration_applied);
        assert_eq!(repeated.applied_steps, 0);
        assert_eq!(entire_database_snapshot(&mut inspector), before);
    });
    println!(
        "\nschema_v3_opaque_history_executed profile={} extra_cases={V3_OPAQUE_CASES}",
        environment.profile.metadata_value()
    );
}
