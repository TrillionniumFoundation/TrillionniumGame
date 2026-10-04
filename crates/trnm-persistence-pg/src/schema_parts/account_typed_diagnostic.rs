// Test-only access to the same typed executor used by the gated public API.
// This is not startup admission, prior-release qualification or restore credit.
use super::account_capture_diagnostic::{capture, raw};
use super::*;
use serde_json::json;
use std::{env, fs, path::Path};

const METADATA_PREFIX: &str = "SELECT singleton::TEXT,schema_version::TEXT,profile::TEXT,source_commit::TEXT,applied_at_ms::TEXT,chain_digest::TEXT,digest_algorithm::TEXT,storage_writer_epoch::TEXT,upgrade_source_commit::TEXT,v2_apply_source_commit::TEXT,v3_apply_source_commit::TEXT,";

fn raw_metadata(repository: &mut PgRepository) -> serde_json::Value {
    let catalog = read_catalog(&mut *repository.client).unwrap();
    let publisher = if catalog.contains_key(&(
        "trnm_schema_metadata".to_owned(),
        "v4_apply_source_commit".to_owned(),
    )) {
        "v4_apply_source_commit::TEXT"
    } else {
        "NULL::TEXT AS v4_apply_source_commit"
    };
    raw(
        &mut *repository.client,
        &format!(
            "{METADATA_PREFIX}{publisher} FROM trnm_schema_metadata ORDER BY singleton LIMIT 2"
        ),
    )
}
const IMPORT_JOB: &str = "INSERT INTO trnm_storage_import_jobs (singleton,manifest_digest,custody_digest,source_inventory_digest,target_schema_guard_digest,prefix_digest,source_profile,source_snapshot,audit_at_ms,total_rows,total_pages,next_page,committed_rows,status) VALUES (1,decode(repeat('1',64),'hex'),decode(repeat('2',64),'hex'),decode(repeat('3',64),'hex'),decode(repeat('4',64),'hex'),decode(repeat('5',64),'hex'),'postgresql','typed-diagnostic',1,0,0,0,0,0)";

fn snapshot(repository: &mut PgRepository) -> RecordedMetadata {
    let catalog = read_catalog(&mut *repository.client).unwrap();
    read_metadata(&mut *repository.client, &catalog)
        .unwrap()
        .unwrap()
}

const STORAGE_QUERY: &str = "SELECT collection::TEXT,object_key::TEXT,encode(user_id,'hex') AS user_id,value_jsonb::TEXT,public_version::TEXT,encode(value_projection_digest,'hex') AS value_projection_digest,value_origin::TEXT,encode(value_bytes,'hex') AS value_bytes,encode(version_digest,'hex') AS version_digest,encode(source_manifest_digest,'hex') AS source_manifest_digest,read_permission::TEXT,write_permission::TEXT,updated_at_ms::TEXT,create_time::TEXT,update_time::TEXT FROM public.trnm_storage_objects ORDER BY collection,object_key,user_id";

fn seed_v4_storage(repository: &mut PgRepository) {
    let native = "null";
    let digest = IntegrityDigest::from_value(native.as_bytes())
        .get()
        .as_bytes()
        .to_vec();
    let version = crate::ContentVersion::from_value(native.as_bytes())
        .as_str()
        .to_owned();
    // Legal published-v4 identifiers and ACLs that unpublished v3 must reject.
    repository.client.execute("INSERT INTO public.trnm_storage_objects (collection,object_key,user_id,value_jsonb,public_version,value_projection_digest,value_origin,value_bytes,version_digest,source_manifest_digest,read_permission,write_permission,updated_at_ms) VALUES ('','',decode(repeat('1',32),'hex'),'null'::JSONB,$1,$2,'write-request-bytes',$3,$2,NULL,32767,32767,0), ('','',decode(repeat('2',32),'hex'),'null'::JSONB,'',$2,'nakama-export-unknown-request',NULL,NULL,decode(repeat('3',64),'hex'),3,2,1)", &[&version,&digest,&native.as_bytes()]).unwrap();
}

#[test]
#[ignore = "disposable hosted internal typed migration diagnostic; public AccountsV5 stays closed"]
fn accounts_typed_disposable_migration() {
    assert_eq!(
        env::var("TRNM_ACCOUNTS_TYPED_DISPOSABLE").as_deref(),
        Ok("hosted-container-fixture")
    );
    let profile_name = env::var("TRNM_ACCOUNTS_TYPED_PROFILE").unwrap();
    let (profile, base) = match profile_name.as_str() {
        "postgresql" => (
            DatabaseProfile::PostgreSql,
            "postgresql://trnm:trnm_live_password@127.0.0.1:55435/",
        ),
        "cockroachdb" => (
            DatabaseProfile::CockroachDb,
            "postgresql://root@127.0.0.1:26257/",
        ),
        _ => panic!("unsupported disposable profile"),
    };
    let case = env::var("TRNM_ACCOUNTS_TYPED_CASE").unwrap();
    let cut = case
        .strip_prefix("cut_")
        .map(|v| v.parse::<usize>().unwrap());
    assert!(matches!(case.as_str(), "fresh" | "populated" | "writer" | "import") || cut.is_some());
    let database = format!("trnm_accounts_typed_{case}");
    let source = env::var("TRNM_SCHEMA_SOURCE_COMMIT").unwrap();
    validate_source_commit(&source).unwrap();
    let output = env::var("TRNM_ACCOUNTS_TYPED_OUTPUT").unwrap();
    let output = Path::new(&output);
    assert!(output.is_absolute() && output.is_dir());
    let mut repository = PgRepository::connect(&format!("{base}{database}"), profile).unwrap();
    let custody: String = repository
        .client
        .query_one("SELECT marker FROM fixture_custody", &[])
        .unwrap()
        .get(0);
    assert_eq!(custody, format!("{source}:{profile_name}:{case}"));
    assert!(read_catalog(&mut *repository.client).unwrap().is_empty());
    // This source actually publishes4. No fabricated prior-release SHA is used.
    let initial = repository
        .migrate_authoritative_schema(&source, 1, None)
        .unwrap();
    assert_eq!(initial.identity.schema_version, 4);
    assert_eq!(initial.identity.upgrade_source_commit, source);
    seed_v4_storage(&mut repository);
    let storage_before = raw(&mut *repository.client, STORAGE_QUERY);
    assert_eq!(storage_before["rows"].as_array().unwrap().len(), 2);
    let before = snapshot(&mut repository);
    let metadata_before = raw_metadata(&mut repository);
    let v5 = revision(profile, 5).unwrap();
    let action_count = v5.action_range.len();
    if let Some(cut) = cut {
        assert!((1..=action_count).contains(&cut));
    }
    let role = format!("accounts_typed_{profile_name}_{case}");
    // Fixture/operator role custody; the typed runner only observes its grants.
    repository
        .client
        .batch_execute(&format!("CREATE ROLE {role} NOLOGIN"))
        .unwrap();
    let public_gate = repository
        .migrate_authoritative_schema_target(
            &source,
            2,
            Some(&role),
            AuthoritativeSchemaTarget::NakamaAccountsV5,
        )
        .unwrap_err();
    assert_eq!(
        public_gate.reason(),
        "schema5_native_catalog_capture_pending"
    );
    assert_eq!(snapshot(&mut repository), before);
    let missing_role = repository
        .migrate_authoritative_schema_admitted(&source, 2, None, AdmittedTarget::diagnostic(None))
        .unwrap_err();
    assert_eq!(
        missing_role.reason(),
        "legacy_storage_writer_barrier_required"
    );
    assert_eq!(snapshot(&mut repository), before);
    let mut corruption_rejections = Vec::new();
    if case == "populated" {
        for (mutation, expected) in [
            ("UPDATE trnm_storage_objects SET value_projection_digest=decode(repeat('0',64),'hex')", "schema_storage_projection_digest_invalid"),
            ("UPDATE trnm_storage_objects SET version_digest=decode(repeat('0',64),'hex') WHERE value_origin='write-request-bytes'", "schema_storage_known_witness_invalid"),
            ("UPDATE trnm_storage_objects SET value_bytes=decode('ff','hex') WHERE value_origin='write-request-bytes'", "schema_storage_native_projection_invalid"),
        ] {
            repository.client.batch_execute(mutation).unwrap();
            let corrupt = raw(&mut *repository.client, STORAGE_QUERY);
            let metadata = raw_metadata(&mut repository);
            let error = repository.migrate_authoritative_schema_admitted(&source, 2, Some(&role), AdmittedTarget::diagnostic(None)).unwrap_err();
            assert_eq!(error.reason(), expected);
            assert_eq!(raw(&mut *repository.client, STORAGE_QUERY), corrupt);
            assert_eq!(raw_metadata(&mut repository), metadata);
            assert_eq!(catalog_prefix(&read_catalog(&mut *repository.client).unwrap(), profile).unwrap(), v5.action_range.start);
            corruption_rejections.push(json!({"reason":error.reason(),"data_retained":true,"metadata_retained":true,"catalog_retained":true}));
            repository.client.batch_execute("DELETE FROM trnm_storage_objects").unwrap();
            seed_v4_storage(&mut repository);
            assert_eq!(raw(&mut *repository.client, STORAGE_QUERY), storage_before);
        }
    }
    let mut barrier_rejection = None;
    if case == "writer" || case == "import" {
        if case == "writer" {
            repository
                .client
                .batch_execute(&format!(
                    "GRANT INSERT, UPDATE, DELETE ON trnm_storage_objects TO {role}"
                ))
                .unwrap();
        } else {
            repository.client.batch_execute(IMPORT_JOB).unwrap();
        }
        let error = repository
            .migrate_authoritative_schema_admitted(
                &source,
                2,
                Some(&role),
                AdmittedTarget::diagnostic(None),
            )
            .unwrap_err();
        let expected = if case == "writer" {
            "legacy_storage_writer_not_fenced"
        } else {
            "schema_unpublished_import_journal_nonempty"
        };
        assert_eq!(error.reason(), expected);
        barrier_rejection = Some(error.reason().to_owned());
        assert_eq!(snapshot(&mut repository), before);
        assert_eq!(
            catalog_prefix(&read_catalog(&mut *repository.client).unwrap(), profile).unwrap(),
            v5.action_range.start
        );
        if case == "writer" {
            repository
                .client
                .batch_execute(&format!(
                    "REVOKE INSERT, UPDATE, DELETE ON trnm_storage_objects FROM {role}"
                ))
                .unwrap();
        } else {
            let count: i64 = repository
                .client
                .query_one("SELECT count(*) FROM trnm_storage_import_jobs", &[])
                .unwrap()
                .get(0);
            assert_eq!(count, 1, "barrier must not consume the import journal");
            repository
                .client
                .batch_execute("DELETE FROM trnm_storage_import_jobs")
                .unwrap();
        }
    }
    let mut interrupted = None;
    if let Some(cut) = cut {
        let error = repository
            .migrate_authoritative_schema_admitted(
                &source,
                2,
                Some(&role),
                AdmittedTarget::diagnostic(Some(v5.action_range.start + cut)),
            )
            .unwrap_err();
        assert_eq!(error.reason(), "schema5_diagnostic_action_cut");
        let observed =
            catalog_prefix(&read_catalog(&mut *repository.client).unwrap(), profile).unwrap();
        let expected = if profile == DatabaseProfile::PostgreSql {
            v5.action_range.start
        } else {
            v5.action_range.start + cut
        };
        assert_eq!(observed, expected);
        assert_eq!(
            snapshot(&mut repository),
            before,
            "unpublished publisher history is unchanged"
        );
        assert_eq!(raw(&mut *repository.client, STORAGE_QUERY), storage_before);
        let metadata_cut = raw_metadata(&mut repository);
        fs::write(
            output.join(format!("{case}-cut-raw.json")),
            serde_json::to_vec_pretty(&capture(&mut *repository.client)).unwrap(),
        )
        .unwrap();
        // After every interruption, new writer grants or import work must block
        // resume before another DDL action or metadata publication occurs.
        repository
            .client
            .batch_execute(&format!(
                "GRANT INSERT, UPDATE, DELETE ON trnm_storage_objects TO {role}"
            ))
            .unwrap();
        let writer = repository
            .migrate_authoritative_schema_admitted(
                &source,
                2,
                Some(&role),
                AdmittedTarget::diagnostic(None),
            )
            .unwrap_err();
        assert_eq!(writer.reason(), "legacy_storage_writer_not_fenced");
        assert_eq!(snapshot(&mut repository), before);
        assert_eq!(
            catalog_prefix(&read_catalog(&mut *repository.client).unwrap(), profile).unwrap(),
            observed
        );
        repository
            .client
            .batch_execute(&format!(
                "REVOKE INSERT, UPDATE, DELETE ON trnm_storage_objects FROM {role}"
            ))
            .unwrap();
        repository.client.batch_execute(IMPORT_JOB).unwrap();
        let import = repository
            .migrate_authoritative_schema_admitted(
                &source,
                2,
                Some(&role),
                AdmittedTarget::diagnostic(None),
            )
            .unwrap_err();
        assert_eq!(
            import.reason(),
            "schema_unpublished_import_journal_nonempty"
        );
        assert_eq!(snapshot(&mut repository), before);
        assert_eq!(
            catalog_prefix(&read_catalog(&mut *repository.client).unwrap(), profile).unwrap(),
            observed
        );
        let count: i64 = repository
            .client
            .query_one("SELECT count(*) FROM trnm_storage_import_jobs", &[])
            .unwrap()
            .get(0);
        assert_eq!(count, 1);
        repository
            .client
            .batch_execute("DELETE FROM trnm_storage_import_jobs")
            .unwrap();
        interrupted = Some(
            json!({"fault_kind":"returned-error-after-native-action-catalog-check","writer_rejection":writer.reason(),"import_rejection":import.reason(),"blocked_resume_catalog_unchanged":true,
"reason":error.reason(),"after_action":cut,"observed_prefix":observed,"expected_prefix":expected,"metadata":metadata_cut,"old_metadata_retained":true,"postgres_transaction_rolled_back":profile == DatabaseProfile::PostgreSql}),
        );
    }
    let report = repository
        .migrate_authoritative_schema_admitted(
            &source,
            2,
            Some(&role),
            AdmittedTarget::diagnostic(None),
        )
        .unwrap();
    assert_eq!(report.identity.schema_version, 5);
    assert_eq!(report.identity.storage_writer_epoch, 4);
    assert_eq!(report.table_count, 14);
    assert_eq!(report.applied_steps, 1);
    assert!(report.migration_applied);
    let after = snapshot(&mut repository);
    assert_eq!(after.v4_apply_source_commit, before.upgrade_source_commit);
    assert_eq!(after.source_commit, before.source_commit);
    assert_eq!(after.v2_apply_source_commit, before.v2_apply_source_commit);
    assert_eq!(after.v3_apply_source_commit, before.v3_apply_source_commit);
    assert_eq!(after.applied_at_ms, before.applied_at_ms);
    let storage_after = raw(&mut *repository.client, STORAGE_QUERY);
    assert_eq!(storage_after, storage_before);
    let metadata_after = raw_metadata(&mut repository);
    let replay = repository
        .migrate_authoritative_schema_admitted(&source, 3, None, AdmittedTarget::diagnostic(None))
        .unwrap();
    assert_eq!(replay.identity, report.identity);
    assert!(!replay.migration_applied);
    assert_eq!(replay.applied_steps, 0);
    assert_eq!(raw_metadata(&mut repository), metadata_after);
    assert_eq!(
        repository
            .verify_authoritative_schema_target(AuthoritativeSchemaTarget::NakamaAccountsV5)
            .unwrap_err()
            .reason(),
        public_gate.reason()
    );
    let public_after = repository
        .migrate_authoritative_schema_target(
            &source,
            3,
            None,
            AuthoritativeSchemaTarget::NakamaAccountsV5,
        )
        .unwrap_err();
    assert_eq!(public_after.reason(), public_gate.reason());
    assert_eq!(raw_metadata(&mut repository), metadata_after);
    let storage_gate = repository.verify_authoritative_schema().unwrap_err();
    assert_eq!(
        storage_gate.reason(),
        "authoritative_schema_target_downgrade_rejected"
    );
    let users: i64 = repository.client.query_one("SELECT count(*) FROM users WHERE id='00000000-0000-0000-0000-000000000000' AND username=''", &[]).unwrap().get(0);
    assert_eq!(users, 1);
    fs::write(
        output.join(format!("{case}-raw.json")),
        serde_json::to_vec_pretty(&capture(&mut *repository.client)).unwrap(),
    )
    .unwrap();
    let result = json!({"schema":"trillionnium.accounts-internal-typed-case.v1","profile":profile_name,"case":case,"database":database,"source_commit":source,
        "execution":"internal-admitted-typed-4-to-5","publisher_scope":"same-candidate-real-storage4-publisher-only","prior_release_producer_qualified":false,
        "storage_before":storage_before,"storage_after":storage_after,"corruption_rejections":corruption_rejections,"populated_storage_retained":true,
        "metadata_before":metadata_before,"metadata_after":metadata_after,"preserved_prior4_publisher":before.upgrade_source_commit,"all_prior_publishers_preserved":true,
        "action_count":action_count,"interruption":interrupted,"barrier_rejection":barrier_rejection,"applied_steps":report.applied_steps,"replay_applied_steps":replay.applied_steps,"replay_metadata_unchanged":true,
        "missing_writer_rejection":missing_role.reason(),"public_accounts_refusal":public_gate.reason(),"public_accounts_after_migration_refusal":public_after.reason(),"public_storage_refusal":storage_gate.reason(),"internal_typed_migration_executed":true,
        "process_crash_qualified":false,"power_loss_qualified":false,"public_migration_qualified":false,"schema5_activation":false,"startup_qualified":false,"backup_restore_qualified":false,"compatibility_credit":false,"accepted":false});
    fs::write(
        output.join(format!("{case}-result.json")),
        serde_json::to_vec_pretty(&result).unwrap(),
    )
    .unwrap();
}
