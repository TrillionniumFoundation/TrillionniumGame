// Compiled only into the explicitly ignored library test. No target admission.
use super::*;
use serde_json::json;
use std::{env, fs, path::Path};

const CASES: &[(&str, &str)] = &[
    ("baseline", ""),
    ("type", "ALTER TABLE users ALTER COLUMN display_name TYPE VARCHAR(254)"),
    ("default", "ALTER TABLE users ALTER COLUMN lang_tag SET DEFAULT 'fr'"),
    ("check", "ALTER TABLE users DROP CONSTRAINT users_edge_count_check; ALTER TABLE users ADD CONSTRAINT users_edge_count_check CHECK (edge_count >= -1)"),
    ("key", "ALTER TABLE user_device DROP CONSTRAINT user_device_user_id_id_key; ALTER TABLE user_device ADD CONSTRAINT user_device_user_id_id_key UNIQUE (id, user_id)"),
    ("predicate", "ALTER TABLE users DROP CONSTRAINT users_custom_id_key; CREATE UNIQUE INDEX users_custom_id_key ON users(custom_id) WHERE custom_id IS NOT NULL"),
    ("trigger", "ALTER TABLE user_device DISABLE TRIGGER ALL"),
    ("foreign_key", "ALTER TABLE user_device DROP CONSTRAINT user_device_user_id_fkey; ALTER TABLE user_device ADD CONSTRAINT user_device_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE NO ACTION"),
];

pub(super) fn raw(client: &mut impl GenericClient, sql: &str) -> serde_json::Value {
    let rows = client.query(sql, &[]).expect("bounded raw catalog query");
    assert!(rows.len() <= 128, "raw catalog row budget");
    let mut result = Vec::new();
    for row in rows {
        let mut object = serde_json::Map::new();
        for (index, column) in row.columns().iter().enumerate() {
            let value: Option<String> = row.try_get(index).expect("native text or NULL");
            assert!(value.as_ref().is_none_or(|v| v.len() <= 16384));
            object.insert(column.name().to_owned(), json!(value));
        }
        result.push(object);
    }
    json!({"query": sql, "rows": result})
}

pub(super) fn capture(client: &mut impl GenericClient) -> serde_json::Value {
    let mut captures = serde_json::Map::new();
    for (name, query) in QUERIES {
        captures.insert((*name).to_owned(), raw(client, query));
    }
    json!(captures)
}

#[test]
#[ignore = "disposable hosted AccountsV5 raw-fixture diagnostic only; no native migration credit"]
fn accounts_capture_disposable_fixture() {
    // The hosted wrapper creates this database inside its verified disposable
    // container. No arbitrary production DSN or caller-selected database name.
    assert_eq!(
        env::var("TRNM_ACCOUNTS_CAPTURE_DISPOSABLE").as_deref(),
        Ok("hosted-container-fixture")
    );
    let profile_name = env::var("TRNM_ACCOUNTS_CAPTURE_PROFILE").unwrap();
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
    let case = env::var("TRNM_ACCOUNTS_CAPTURE_CASE").unwrap();
    assert!(case != "trigger" || profile == DatabaseProfile::PostgreSql);
    let mutation = CASES.iter().find(|(name, _)| *name == case).unwrap().1;
    let database = format!("trnm_accounts_capture_{case}");
    let url = format!("{base}{database}");
    let source = env::var("TRNM_SCHEMA_SOURCE_COMMIT").unwrap();
    validate_source_commit(&source).unwrap();
    let output = env::var("TRNM_ACCOUNTS_CAPTURE_OUTPUT").unwrap();
    let output = Path::new(&output);
    assert!(output.is_absolute() && output.is_dir());
    let mut repository = PgRepository::connect(&url, profile).unwrap();
    let custody: String = repository
        .client
        .query_one("SELECT marker FROM fixture_custody", &[])
        .unwrap()
        .get(0);
    assert_eq!(custody, format!("{source}:{profile_name}:{case}"));
    assert!(read_catalog(&mut *repository.client).unwrap().is_empty());
    let initial = repository
        .migrate_authoritative_schema(&source, 1, None)
        .unwrap();
    assert_eq!(initial.identity.schema_version, 4);
    assert_eq!(initial.identity.upgrade_source_commit, source);
    let before_catalog = read_catalog(&mut *repository.client).unwrap();
    let before = read_metadata(&mut *repository.client, &before_catalog)
        .unwrap()
        .unwrap();
    let metadata_before = raw(&mut *repository.client, METADATA4);
    // The embedded step has already passed the build-time locked Git-blob
    // checks. This is raw fixture DDL, deliberately NOT the typed migrator.
    let fixture = steps(profile)
        .iter()
        .find(|step| step.version == 5)
        .unwrap();
    repository.client.batch_execute(fixture.sql).unwrap();
    let baseline_raw = capture(&mut *repository.client);
    // Save native observations before assertions, retaining useful diagnostics
    // even when previously hard-coded catalog bindings disagree with reality.
    fs::write(
        output.join(format!("{case}-raw.json")),
        serde_json::to_vec_pretty(&baseline_raw).unwrap(),
    )
    .unwrap();
    let catalog = read_catalog(&mut *repository.client).unwrap();
    assert_eq!(
        catalog_prefix(&catalog, profile).unwrap(),
        revision(profile, 5).unwrap().action_range.end
    );
    let after = read_metadata(&mut *repository.client, &catalog)
        .unwrap()
        .unwrap();
    assert_eq!(
        before, after,
        "raw fixture must not publish schema5 metadata"
    );
    assert_eq!(after.version, 4);
    assert_eq!(after.v4_apply_source_commit, None);
    let refusal = repository.verify_authoritative_schema().unwrap_err();
    assert_eq!(refusal.reason(), "authoritative_schema_upgrade_incomplete");
    let gate = repository
        .verify_authoritative_schema_target(AuthoritativeSchemaTarget::NakamaAccountsV5)
        .unwrap_err();
    assert_eq!(gate.reason(), "schema5_native_catalog_capture_pending");
    let metadata_after = raw(&mut *repository.client, METADATA4);
    assert_eq!(metadata_before, metadata_after);
    let rejection = if mutation.is_empty() {
        None
    } else {
        repository.client.batch_execute(mutation).unwrap();
        fs::write(
            output.join(format!("{case}-altered-raw.json")),
            serde_json::to_vec_pretty(&capture(&mut *repository.client)).unwrap(),
        )
        .unwrap();
        let error = read_catalog(&mut *repository.client)
            .and_then(|c| catalog_prefix(&c, profile))
            .unwrap_err();
        assert!(
            error.reason().starts_with("schema5_")
                || error.reason() == "authoritative_schema_partial_action_drift"
        );
        Some(error.reason().to_owned())
    };
    let result = json!({
        "schema":"trillionnium.accounts-raw-fixture-case.v1", "profile":profile_name, "case":case,
        "database":database, "source_commit":source, "fixture_path":fixture.path,
        "fixture_execution":"raw-sql5-after-real-storage-v4", "metadata_before":metadata_before,
        "metadata_after":metadata_after, "v4_apply_source_commit":after.v4_apply_source_commit, "metadata_retained":true,
        "storage_v4_refusal":refusal.reason(), "accounts_v5_refusal":gate.reason(),
        "mutation_sql":mutation, "drift_rejection":rejection,
        "native_migration_executed":false, "schema5_activation":false,
        "compatibility_credit":false, "accepted":false
    });
    fs::write(
        output.join(format!("{case}-result.json")),
        serde_json::to_vec_pretty(&result).unwrap(),
    )
    .unwrap();
}

const METADATA4: &str = "SELECT singleton::TEXT,schema_version::TEXT,profile::TEXT,source_commit::TEXT,applied_at_ms::TEXT,chain_digest::TEXT,digest_algorithm::TEXT,storage_writer_epoch::TEXT,upgrade_source_commit::TEXT,v2_apply_source_commit::TEXT,v3_apply_source_commit::TEXT FROM trnm_schema_metadata ORDER BY singleton LIMIT 2";
const QUERIES: &[(&str, &str)] = &[
("columns", "SELECT table_name::TEXT,ordinal_position::TEXT,column_name::TEXT,data_type::TEXT,udt_name::TEXT,is_nullable::TEXT,character_maximum_length::TEXT,column_default::TEXT FROM information_schema.columns WHERE table_schema='public' AND table_name IN ('users','user_device') ORDER BY table_name,ordinal_position LIMIT 33"),
("attributes", "SELECT c.relname::TEXT,a.attname::TEXT,a.attnum::TEXT,a.atttypid::TEXT,a.atttypmod::TEXT,a.attnotnull::TEXT,a.attgenerated::TEXT,a.attidentity::TEXT,a.attcollation::TEXT,tn.nspname::TEXT,t.typname::TEXT,t.typtype::TEXT,t.typbasetype::TEXT,cn.nspname::TEXT AS collation_namespace,co.collname::TEXT FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace JOIN pg_catalog.pg_type t ON t.oid=a.atttypid JOIN pg_catalog.pg_namespace tn ON tn.oid=t.typnamespace LEFT JOIN pg_catalog.pg_collation co ON co.oid=a.attcollation LEFT JOIN pg_catalog.pg_namespace cn ON cn.oid=co.collnamespace WHERE n.nspname='public' AND c.relname IN ('users','user_device') AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum LIMIT 33"),
("relations", "SELECT c.relname::TEXT,c.relkind::TEXT,c.relowner::TEXT,c.relpersistence::TEXT,c.relam::TEXT,c.relrowsecurity::TEXT,c.relforcerowsecurity::TEXT,c.relhassubclass::TEXT,c.relhasrules::TEXT,c.relispartition::TEXT,c.relrewrite::TEXT FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname IN ('users','user_device','trnm_schema_metadata') ORDER BY c.relname LIMIT 4"),
("constraints", "SELECT t.relname::TEXT,c.conname::TEXT,c.contype::TEXT,c.convalidated::TEXT,c.condeferrable::TEXT,c.condeferred::TEXT,c.conkey::TEXT,c.confkey::TEXT,c.confupdtype::TEXT,c.confdeltype::TEXT,c.confmatchtype::TEXT,rn.nspname::TEXT,r.relname::TEXT AS reference_table,pg_catalog.pg_get_constraintdef(c.oid)::TEXT AS definition FROM pg_catalog.pg_constraint c JOIN pg_catalog.pg_class t ON t.oid=c.conrelid JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace LEFT JOIN pg_catalog.pg_class r ON r.oid=c.confrelid LEFT JOIN pg_catalog.pg_namespace rn ON rn.oid=r.relnamespace WHERE n.nspname='public' AND t.relname IN ('users','user_device') ORDER BY t.relname,c.conname LIMIT 17"),
("indexes", "SELECT t.relname::TEXT,x.relname::TEXT AS index_name,i.indisunique::TEXT,i.indisprimary::TEXT,i.indisvalid::TEXT,i.indisready::TEXT,i.indkey::TEXT,pg_catalog.pg_get_expr(i.indpred,i.indrelid)::TEXT AS predicate,pg_catalog.pg_get_indexdef(i.indexrelid)::TEXT AS definition,x.relkind::TEXT,x.relowner::TEXT,t.relowner::TEXT AS table_owner,xn.nspname::TEXT FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class t ON t.oid=i.indrelid JOIN pg_catalog.pg_class x ON x.oid=i.indexrelid JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace JOIN pg_catalog.pg_namespace xn ON xn.oid=x.relnamespace WHERE n.nspname='public' AND t.relname IN ('users','user_device') ORDER BY t.relname,x.relname LIMIT 14"),
("triggers", "SELECT c.relname::TEXT,t.oid::TEXT,t.tgname::TEXT,t.tgisinternal::TEXT,t.tgenabled::TEXT,t.tgfoid::TEXT,t.tgtype::TEXT,t.tgconstraint::TEXT,k.conname::TEXT,k.contype::TEXT,t.tgdeferrable::TEXT,t.tginitdeferred::TEXT,t.tgargs::TEXT,pg_catalog.pg_get_triggerdef(t.oid)::TEXT AS definition,ct.relname::TEXT AS constraint_table,cn.nspname::TEXT AS constraint_namespace,rt.relname::TEXT AS reference_table,rn.nspname::TEXT AS reference_namespace FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_catalog.pg_constraint k ON k.oid=t.tgconstraint LEFT JOIN pg_catalog.pg_class ct ON ct.oid=k.conrelid LEFT JOIN pg_catalog.pg_namespace cn ON cn.oid=ct.relnamespace LEFT JOIN pg_catalog.pg_class rt ON rt.oid=k.confrelid LEFT JOIN pg_catalog.pg_namespace rn ON rn.oid=rt.relnamespace WHERE n.nspname='public' AND c.relname IN ('users','user_device','trnm_schema_metadata') ORDER BY c.relname,t.tgname LIMIT 6"),
];
