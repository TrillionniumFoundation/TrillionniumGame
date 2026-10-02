//! Authoritative-schema lifecycle candidates. These tests create isolated
//! databases and roles; a local result grants no accepted evidence credit.
//! Required lanes must provide TRNM_SCHEMA_UPGRADE_ADMIN_DATABASE_URL and
//! TRNM_DATABASE_PROFILE, with CREATE DATABASE and CREATE ROLE privileges.

use std::env;
use std::str::FromStr;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use postgres::{Client, Config, NoTls};
use trnm_contracts::StableCode;
use trnm_persistence_pg::{
    authoritative_chain_digest, DatabaseProfile, IntegrityDigest, PgRepository, SchemaIdentity,
};

const ORIGINAL_SOURCE: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const UPGRADE_SOURCE: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const LATER_BINARY_SOURCE: &str = "cccccccccccccccccccccccccccccccccccccccc";
const ROLE_PASSWORD: &str = "isolated_schema_upgrade_fixture_only";
const LEGACY_VALUE: &[u8] = b"{\"legacy\":true}";
const TABLES: &[&str] = &[
    "trnm_schema_metadata",
    "trnm_entity_heads",
    "trnm_command_receipts",
    "trnm_events",
    "trnm_outbox",
    "trnm_command_outbox",
    "trnm_authority_leases",
    "trnm_session_families",
    "trnm_refresh_tokens",
    "trnm_storage_objects",
];
static NEXT_DATABASE: AtomicU64 = AtomicU64::new(0);

struct LiveEnvironment {
    admin_url: String,
    profile: DatabaseProfile,
}

fn required_flag(name: &str) -> bool {
    match env::var(name) {
        Err(env::VarError::NotPresent) => false,
        Err(error) => panic!("cannot read {name}: {error}"),
        Ok(value) if matches!(value.as_str(), "1" | "true" | "TRUE" | "yes" | "YES") => true,
        Ok(value) if matches!(value.as_str(), "0" | "false" | "FALSE" | "no" | "NO") => false,
        Ok(value) => panic!("invalid {name}={value:?}; expected a boolean flag"),
    }
}

fn live_environment() -> Option<LiveEnvironment> {
    // Evaluate both flags, so an invalid alias never disappears behind ||.
    let standard_required = required_flag("TRNM_REQUIRE_LIVE_DATABASE");
    let alias_required = required_flag("TRNM_LIVE_TEST_REQUIRED");
    let required = standard_required || alias_required;
    let admin_url = match env::var("TRNM_SCHEMA_UPGRADE_ADMIN_DATABASE_URL") {
        Ok(value) if !value.is_empty() => value,
        Ok(_) | Err(env::VarError::NotPresent) if !required => {
            eprintln!(
                "schema upgrade: dedicated admin URL absent; developer-only live test skip (no evidence credit)"
            );
            return None;
        }
        Ok(_) | Err(env::VarError::NotPresent) => {
            panic!("required TRNM_SCHEMA_UPGRADE_ADMIN_DATABASE_URL is absent or empty")
        }
        Err(error) => panic!("cannot read TRNM_SCHEMA_UPGRADE_ADMIN_DATABASE_URL: {error}"),
    };
    let profile = match env::var("TRNM_DATABASE_PROFILE").as_deref() {
        Ok("postgresql") => DatabaseProfile::PostgreSql,
        Ok("cockroachdb") => DatabaseProfile::CockroachDb,
        Ok(_) => panic!("unsupported TRNM_DATABASE_PROFILE for schema upgrade"),
        Err(_) => panic!("TRNM_DATABASE_PROFILE is required with the schema admin URL"),
    };
    Config::from_str(&admin_url).expect("schema admin URL must be a valid PostgreSQL URI");
    Some(LiveEnvironment { admin_url, profile })
}

// The repository accepts a URI rather than Config. Preserve the administrator's
// host and connection parameters, replacing only the generated database and,
// for role checks, generated credentials. Config validates the resulting URI.
fn target_uri(base: &str, database: &str, role: Option<&str>) -> String {
    let (scheme, rest) = base
        .split_once("://")
        .expect("schema admin configuration must use a PostgreSQL URI");
    assert!(matches!(scheme, "postgres" | "postgresql"));
    let authority_end = rest.find(['/', '?']).unwrap_or(rest.len());
    let authority = &rest[..authority_end];
    let authority = if let Some(role) = role {
        let host = authority
            .rsplit_once('@')
            .map_or(authority, |(_, host)| host);
        format!("{role}:{ROLE_PASSWORD}@{host}")
    } else {
        authority.to_owned()
    };
    let query = rest[authority_end..]
        .split_once('?')
        .map_or("", |(_, query)| query);
    let query = if query.is_empty() {
        "connect_timeout=10".to_owned()
    } else {
        format!("{query}&connect_timeout=10")
    };
    let result = format!("{scheme}://{authority}/{database}?{query}");
    let parsed = Config::from_str(&result).expect("generated schema database URI is invalid");
    assert_eq!(parsed.get_dbname(), Some(database));
    if let Some(role) = role {
        assert_eq!(parsed.get_user(), Some(role));
    }
    result
}

struct IsolatedDatabase {
    admin: Client,
    config: Config,
    base_url: String,
    database_url: String,
    name: String,
    profile: DatabaseProfile,
    created: bool,
    roles: Vec<String>,
}

impl IsolatedDatabase {
    fn new(environment: &LiveEnvironment) -> Self {
        let unique = NEXT_DATABASE.fetch_add(1, Ordering::Relaxed);
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock precedes UNIX epoch")
            .as_nanos();
        let name = format!("trnm_schema_{}_{nanos:x}_{unique}", std::process::id());
        assert!(name.len() < 48);
        assert!(name
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_'));
        let mut admin_config = Config::from_str(&environment.admin_url).unwrap();
        admin_config.connect_timeout(Duration::from_secs(10));
        let mut admin = admin_config
            .connect(NoTls)
            .expect("connect schema administrator");
        admin
            .batch_execute("SET statement_timeout = '15s'")
            .unwrap();
        let database_url = target_uri(&environment.admin_url, &name, None);
        let mut config = admin_config;
        config.dbname(&name);
        let mut fixture = Self {
            admin,
            config,
            base_url: environment.admin_url.clone(),
            database_url,
            name,
            profile: environment.profile,
            created: false,
            roles: Vec::new(),
        };
        fixture
            .admin
            .batch_execute(&format!("CREATE DATABASE {}", fixture.name))
            .expect("create isolated schema database");
        fixture.created = true;
        fixture
    }

    fn client(&self) -> Client {
        let mut client = self
            .config
            .connect(NoTls)
            .expect("connect isolated database");
        client
            .batch_execute("SET statement_timeout = '15s'")
            .unwrap();
        client
    }

    fn repository(&self) -> PgRepository {
        PgRepository::connect(&self.database_url, self.profile).unwrap()
    }

    fn create_role(&mut self, suffix: &str) -> String {
        assert!(suffix
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte == b'_'));
        let role = format!("{}_{}", self.name, suffix);
        assert!(role.len() <= 63);
        // The isolated CockroachDB lane is explicitly insecure and rejects
        // password DDL. It still uses a distinct actual pgwire role identity;
        // this fixture grants no authentication/TLS acceptance credit.
        let options = if self.profile == DatabaseProfile::PostgreSql {
            format!("WITH LOGIN PASSWORD '{ROLE_PASSWORD}'")
        } else {
            "WITH LOGIN".to_owned()
        };
        self.admin
            .batch_execute(&format!("CREATE ROLE {role} {options}"))
            .expect("create independent fixture role");
        self.roles.push(role.clone());
        self.admin
            .batch_execute(&format!(
                "GRANT CONNECT ON DATABASE {} TO {role}",
                self.name
            ))
            .unwrap();
        self.client()
            .batch_execute(&format!("GRANT USAGE ON SCHEMA public TO {role}"))
            .unwrap();
        role
    }

    fn role_client(&self, role: &str) -> Client {
        let mut config = self.config.clone();
        config.user(role).password(ROLE_PASSWORD);
        let mut client = config
            .connect(NoTls)
            .expect("connect independent fixture role");
        client
            .batch_execute("SET statement_timeout = '15s'")
            .unwrap();
        let actual: String = client.query_one("SELECT current_user", &[]).unwrap().get(0);
        assert_eq!(
            actual, role,
            "role fixture must not inherit the admin identity"
        );
        client
    }

    fn role_repository(&self, role: &str) -> PgRepository {
        let url = target_uri(&self.base_url, &self.name, Some(role));
        PgRepository::connect(&url, self.profile).unwrap()
    }

    fn grant_reads(&self, role: &str) {
        let mut client = self.client();
        for table in TABLES {
            client
                .batch_execute(&format!("GRANT SELECT ON TABLE {table} TO {role}"))
                .unwrap();
        }
    }

    fn legacy_writer(&mut self) -> String {
        let role = self.create_role("legacy");
        self.grant_reads(&role);
        let mut client = self.client();
        client
            .batch_execute(&format!(
                "GRANT INSERT, UPDATE, DELETE ON TABLE trnm_storage_objects TO {role}"
            ))
            .unwrap();
        if self.profile == DatabaseProfile::PostgreSql {
            // Column grants survive a table-only REVOKE; verify they are fenced too.
            client
                .batch_execute(&format!(
                    "GRANT INSERT (value_bytes), UPDATE (value_bytes) ON TABLE trnm_storage_objects TO {role}"
                ))
                .unwrap();
        }
        role
    }

    fn revoke_legacy_writes(&self, role: &str) {
        // This is an explicit fixture/operator action. The authoritative runner
        // verifies the resulting barrier and must never revoke production roles.
        let mut client = self.client();
        client
            .batch_execute(&format!(
                "REVOKE INSERT, UPDATE, DELETE ON TABLE trnm_storage_objects FROM {role}"
            ))
            .unwrap();
        if self.profile == DatabaseProfile::PostgreSql {
            client
                .batch_execute(&format!(
                    "REVOKE INSERT (value_bytes), UPDATE (value_bytes) ON TABLE trnm_storage_objects FROM {role}"
                ))
                .unwrap();
        }
    }
}

impl Drop for IsolatedDatabase {
    fn drop(&mut self) {
        let mut cleanup_failed = false;
        if self.created {
            let cascade = if self.profile == DatabaseProfile::CockroachDb {
                " CASCADE"
            } else {
                ""
            };
            if let Err(error) = self
                .admin
                .batch_execute(&format!("DROP DATABASE {}{cascade}", self.name))
            {
                cleanup_failed = true;
                eprintln!("isolated schema database cleanup failed: {error}");
            }
        }
        for role in self.roles.iter().rev() {
            if let Err(error) = self.admin.batch_execute(&format!("DROP ROLE {role}")) {
                cleanup_failed = true;
                eprintln!("isolated schema role cleanup failed: {error}");
            }
        }
        assert!(
            !cleanup_failed || std::thread::panicking(),
            "isolated schema fixture cleanup failed"
        );
    }
}

fn with_database(environment: &LiveEnvironment, run: impl FnOnce(&mut IsolatedDatabase)) {
    // Locals inside run (including every repository/role connection) are dropped
    // before the owning guard removes this generated database and its roles.
    let mut fixture = IsolatedDatabase::new(environment);
    run(&mut fixture);
}

fn install_v1(fixture: &IsolatedDatabase, bound: bool) {
    let migration = match fixture.profile {
        DatabaseProfile::PostgreSql => {
            include_str!("../../../migrations/postgresql/0001_foundation_up.sql")
        }
        DatabaseProfile::CockroachDb => {
            include_str!("../../../migrations/cockroachdb/0001_foundation_up.sql")
        }
    };
    let mut client = fixture.client();
    client.batch_execute(migration).unwrap();
    if bound {
        client
            .execute(
                "INSERT INTO trnm_schema_metadata \
                 (singleton, schema_version, profile, source_commit, applied_at_ms) \
                 VALUES (1, 1, $1, $2, 17)",
                &[&fixture.profile.metadata_value(), &ORIGINAL_SOURCE],
            )
            .unwrap();
    }
}

fn seed_legacy_storage(client: &mut Client) {
    let integrity = IntegrityDigest::from_value(LEGACY_VALUE).get();
    client
        .execute(
            "INSERT INTO trnm_storage_objects \
             (collection, object_key, user_id, value_bytes, version_digest, \
              read_permission, write_permission, updated_at_ms) \
             VALUES ('schema-upgrade', 'history', $1, $2, $3, 2, 1, 42)",
            &[
                &[0x11_u8; 16].as_slice(),
                &LEGACY_VALUE,
                &integrity.as_bytes().as_slice(),
            ],
        )
        .unwrap();
}

fn assert_identity(
    identity: &SchemaIdentity,
    profile: DatabaseProfile,
    source: &str,
    upgrade: &str,
) {
    assert_eq!(identity.profile, profile);
    assert_eq!(identity.schema_version, 2);
    assert_eq!(identity.storage_writer_epoch, 2);
    assert_eq!(identity.source_commit, source);
    assert_eq!(identity.upgrade_source_commit, upgrade);
    assert_eq!(identity.digest_algorithm, "ordered-path-git-blob-sha256.v1");
    assert_eq!(identity.chain_digest, authoritative_chain_digest(profile));
}

fn metadata_snapshot(
    client: &mut Client,
) -> (i64, String, String, i64, String, String, i64, String) {
    let row = client
        .query_one(
            "SELECT schema_version, profile, source_commit, applied_at_ms, \
             chain_digest, digest_algorithm, storage_writer_epoch, upgrade_source_commit \
             FROM trnm_schema_metadata WHERE singleton = 1",
            &[],
        )
        .unwrap();
    (
        row.get(0),
        row.get(1),
        row.get(2),
        row.get(3),
        row.get(4),
        row.get(5),
        row.get(6),
        row.get(7),
    )
}

fn assert_permission_denied(result: Result<u64, postgres::Error>) {
    let error = result.expect_err("revoked role unexpectedly mutated durable storage");
    assert_eq!(
        error
            .as_db_error()
            .expect("denial must be a database error")
            .code()
            .code(),
        "42501",
        "an unrelated SQL failure is not a writer fence"
    );
}

#[derive(Debug, Eq, PartialEq)]
struct LegacyStorageRow {
    collection: String,
    object_key: String,
    user_id: Vec<u8>,
    value_bytes: Vec<u8>,
    version_digest: Vec<u8>,
    read_permission: i16,
    write_permission: i16,
    updated_at_ms: i64,
}

fn legacy_storage_snapshot(client: &mut Client) -> Vec<LegacyStorageRow> {
    client
        .query(
            "SELECT collection, object_key, user_id, value_bytes, version_digest, \
             read_permission, write_permission, updated_at_ms FROM public.trnm_storage_objects \
             ORDER BY collection, object_key, user_id",
            &[],
        )
        .unwrap()
        .into_iter()
        .map(|row| LegacyStorageRow {
            collection: row.get(0),
            object_key: row.get(1),
            user_id: row.get(2),
            value_bytes: row.get(3),
            version_digest: row.get(4),
            read_permission: row.get(5),
            write_permission: row.get(6),
            updated_at_ms: row.get(7),
        })
        .collect()
}

fn v1_metadata_snapshot(client: &mut Client) -> (i64, String, String, i64) {
    let row = client
        .query_one(
            "SELECT schema_version, profile, source_commit, applied_at_ms \
             FROM public.trnm_schema_metadata WHERE singleton = 1",
            &[],
        )
        .unwrap();
    (row.get(0), row.get(1), row.get(2), row.get(3))
}

fn assert_barrier_rejects_without_mutation(
    repository: &mut PgRepository,
    inspector: &mut Client,
    legacy: &str,
) {
    let metadata_before = v1_metadata_snapshot(inspector);
    assert_eq!(metadata_before.0, 1);
    let storage_before = legacy_storage_snapshot(inspector);
    assert_eq!(storage_before.len(), 1);
    let error = repository
        .migrate_authoritative_schema(UPGRADE_SOURCE, 23, Some(legacy))
        .unwrap_err();
    assert_eq!(error.code(), StableCode::FailedPrecondition);
    assert_eq!(error.reason(), "legacy_storage_writer_not_fenced");
    assert_eq!(v1_metadata_snapshot(inspector), metadata_before);
    assert_eq!(legacy_storage_snapshot(inspector), storage_before);
    let appended_columns: i64 = inspector
        .query_one(
            "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' \
             AND ((table_name = 'trnm_schema_metadata' AND column_name IN \
                  ('chain_digest', 'digest_algorithm', 'storage_writer_epoch', 'upgrade_source_commit')) \
               OR (table_name = 'trnm_storage_objects' AND column_name IN ('create_time', 'update_time')))",
            &[],
        )
        .unwrap()
        .get(0);
    assert_eq!(appended_columns, 0, "unfenced upgrade executed append DDL");
}

fn assert_role_has_no_current_crud(client: &mut Client, role: &str) {
    let row = client
        .query_one(
            "SELECT pg_catalog.has_table_privilege($1, 'public.trnm_storage_objects', 'INSERT'), \
             pg_catalog.has_table_privilege($1, 'public.trnm_storage_objects', 'UPDATE'), \
             pg_catalog.has_table_privilege($1, 'public.trnm_storage_objects', 'DELETE'), \
             pg_catalog.has_any_column_privilege($1, 'public.trnm_storage_objects', 'INSERT'), \
             pg_catalog.has_any_column_privilege($1, 'public.trnm_storage_objects', 'UPDATE')",
            &[&role],
        )
        .unwrap();
    for column in 0..5 {
        assert!(!row.get::<_, bool>(column));
    }
}

#[test]
fn authoritative_fresh_repeat_and_readonly_verification() {
    let Some(environment) = live_environment() else {
        return;
    };
    with_database(&environment, |fixture| {
        let mut repository = fixture.repository();
        repository.execute_migration_batch(
            "CREATE SCHEMA migration_shadow; SET search_path = migration_shadow, public, pg_catalog",
        ).unwrap();
        let rejected = repository
            .migrate_authoritative_schema(UPGRADE_SOURCE, 23, None)
            .unwrap_err();
        assert_eq!(rejected.code(), StableCode::FailedPrecondition);
        let mut inspector = fixture.client();
        let untouched: i64 = inspector.query_one(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema IN ('public', 'migration_shadow')",
            &[],
        ).unwrap().get(0);
        assert_eq!(untouched, 0, "wrong search_path must not execute any DDL");
        repository
            .execute_migration_batch("SET search_path = DEFAULT")
            .unwrap();
        let report = repository
            .migrate_authoritative_schema(UPGRADE_SOURCE, 23, None)
            .unwrap();
        assert!(report.migration_applied);
        assert_eq!(report.applied_steps, 2);
        assert_eq!(report.table_count, TABLES.len());
        assert_identity(
            &report.identity,
            fixture.profile,
            UPGRADE_SOURCE,
            UPGRADE_SOURCE,
        );
        let mut inspector = fixture.client();
        let before = metadata_snapshot(&mut inspector);
        let repeated = repository
            .migrate_authoritative_schema(LATER_BINARY_SOURCE, 99, None)
            .unwrap();
        assert!(!repeated.migration_applied);
        assert_eq!(repeated.applied_steps, 0);
        assert_eq!(repeated.identity.chain_digest, report.identity.chain_digest);
        assert_identity(
            &repeated.identity,
            fixture.profile,
            UPGRADE_SOURCE,
            UPGRADE_SOURCE,
        );
        assert_eq!(metadata_snapshot(&mut inspector), before);

        let reader = fixture.create_role("reader");
        fixture.grant_reads(&reader);
        let mut readonly_client = fixture.role_client(&reader);
        assert_permission_denied(readonly_client.execute(
            "UPDATE trnm_schema_metadata SET applied_at_ms = applied_at_ms WHERE singleton = 1",
            &[],
        ));
        let mut readonly_repository = fixture.role_repository(&reader);
        let verified = readonly_repository.verify_authoritative_schema().unwrap();
        assert_identity(&verified, fixture.profile, UPGRADE_SOURCE, UPGRADE_SOURCE);
        assert_eq!(verified.chain_digest, report.identity.chain_digest);
        readonly_repository.verify_authoritative_schema().unwrap();
        assert_eq!(metadata_snapshot(&mut inspector), before);
    });
}

#[test]
fn authoritative_v1_preserves_history_and_observes_actual_legacy_writer_revocation() {
    let Some(environment) = live_environment() else {
        return;
    };
    with_database(&environment, |fixture| {
        install_v1(fixture, true);
        let legacy = fixture.legacy_writer();
        let mut legacy_client = fixture.role_client(&legacy);
        // A live connection proves the named role had effective write access.
        seed_legacy_storage(&mut legacy_client);
        let mut repository = fixture.repository();
        let missing_fence = repository
            .migrate_authoritative_schema(UPGRADE_SOURCE, 23, None)
            .unwrap_err();
        assert_eq!(missing_fence.code(), StableCode::FailedPrecondition);
        let mut inspector = fixture.client();
        let version: i64 = inspector
            .query_one(
                "SELECT schema_version FROM trnm_schema_metadata WHERE singleton = 1",
                &[],
            )
            .unwrap()
            .get(0);
        assert_eq!(version, 1, "unfenced upgrade must not advance metadata");
        let unfenced_role = repository
            .migrate_authoritative_schema(UPGRADE_SOURCE, 23, Some(&legacy))
            .unwrap_err();
        assert_eq!(unfenced_role.code(), StableCode::FailedPrecondition);
        assert_eq!(unfenced_role.reason(), "legacy_storage_writer_not_fenced");
        fixture.revoke_legacy_writes(&legacy);
        let report = repository
            .migrate_authoritative_schema(UPGRADE_SOURCE, 23, Some(&legacy))
            .unwrap();
        assert!(report.migration_applied);
        assert_eq!(report.applied_steps, 1);
        assert_identity(
            &report.identity,
            fixture.profile,
            ORIGINAL_SOURCE,
            UPGRADE_SOURCE,
        );
        let row = inspector.query_one(
            "SELECT user_id, value_bytes, version_digest, read_permission, write_permission, \
             updated_at_ms, create_time IS NULL, update_time IS NULL \
             FROM trnm_storage_objects WHERE collection = 'schema-upgrade' AND object_key = 'history'", &[],
        ).unwrap();
        assert_eq!(row.get::<_, Vec<u8>>(0), vec![0x11; 16]);
        assert_eq!(row.get::<_, Vec<u8>>(1), LEGACY_VALUE);
        assert_eq!(
            row.get::<_, Vec<u8>>(2).as_slice(),
            IntegrityDigest::from_value(LEGACY_VALUE)
                .get()
                .as_bytes()
                .as_slice()
        );
        assert_eq!(row.get::<_, i16>(3), 2);
        assert_eq!(row.get::<_, i16>(4), 1);
        assert_eq!(row.get::<_, i64>(5), 42);
        assert!(
            row.get::<_, bool>(6),
            "unknown historical create time must remain NULL"
        );
        assert!(
            row.get::<_, bool>(7),
            "legacy integer must not become a public timestamp"
        );
        let metadata = metadata_snapshot(&mut inspector);
        assert_eq!(
            metadata.3, 17,
            "v1 migration provenance must survive upgrade"
        );
        assert_permission_denied(legacy_client.execute(
            "INSERT INTO trnm_storage_objects \
             (collection, object_key, user_id, value_bytes, version_digest, read_permission, write_permission, updated_at_ms) \
             VALUES ('schema-upgrade', 'blocked', decode(repeat('22',16),'hex'), decode('01','hex'), decode(repeat('33',32),'hex'), 2, 1, 43)", &[],
        ));
        assert_permission_denied(legacy_client.execute(
            "UPDATE trnm_storage_objects SET value_bytes = decode('02','hex') WHERE collection = 'schema-upgrade'", &[],
        ));
        assert_permission_denied(legacy_client.execute(
            "DELETE FROM trnm_storage_objects WHERE collection = 'schema-upgrade'",
            &[],
        ));
        let count: i64 = inspector
            .query_one("SELECT count(*) FROM trnm_storage_objects", &[])
            .unwrap()
            .get(0);
        assert_eq!(count, 1);
        let current = repository.verify_authoritative_schema().unwrap();
        assert_eq!(current.chain_digest, report.identity.chain_digest);
        assert_eq!(metadata_snapshot(&mut inspector), metadata);
    });
}

#[test]
fn authoritative_populated_unbound_and_catalog_drift_fail_closed() {
    let Some(environment) = live_environment() else {
        return;
    };
    with_database(&environment, |fixture| {
        install_v1(fixture, false);
        let legacy = fixture.legacy_writer();
        let mut inspector = fixture.client();
        seed_legacy_storage(&mut inspector);
        fixture.revoke_legacy_writes(&legacy);
        let mut repository = fixture.repository();
        let rejected = repository
            .migrate_authoritative_schema(UPGRADE_SOURCE, 23, Some(&legacy))
            .unwrap_err();
        assert_eq!(rejected.code(), StableCode::FailedPrecondition);
        assert_eq!(rejected.reason(), "unbound_populated_schema_rejected");
        let count: i64 = inspector
            .query_one("SELECT count(*) FROM trnm_schema_metadata", &[])
            .unwrap()
            .get(0);
        assert_eq!(
            count, 0,
            "populated unbound schema must not acquire invented provenance"
        );
        let count: i64 = inspector
            .query_one("SELECT count(*) FROM trnm_storage_objects", &[])
            .unwrap()
            .get(0);
        assert_eq!(count, 1);
    });
    with_database(&environment, |fixture| {
        install_v1(fixture, true);
        let legacy = fixture.legacy_writer();
        fixture.revoke_legacy_writes(&legacy);
        let mut inspector = fixture.client();
        // A column outside the declared upgrade is not a resumable action.
        inspector
            .batch_execute(
                "ALTER TABLE trnm_storage_objects ADD COLUMN unexpected_timestamp TIMESTAMPTZ",
            )
            .unwrap();
        let mut repository = fixture.repository();
        let rejected = repository
            .migrate_authoritative_schema(UPGRADE_SOURCE, 23, Some(&legacy))
            .unwrap_err();
        assert_eq!(rejected.code(), StableCode::FailedPrecondition);
        let version: i64 = inspector
            .query_one(
                "SELECT schema_version FROM trnm_schema_metadata WHERE singleton = 1",
                &[],
            )
            .unwrap()
            .get(0);
        assert_eq!(version, 1);
    });
    with_database(&environment, |fixture| {
        let mut repository = fixture.repository();
        repository
            .migrate_authoritative_schema(UPGRADE_SOURCE, 23, None)
            .unwrap();
        let mut inspector = fixture.client();
        let changed = inspector
            .execute(
                "UPDATE trnm_schema_metadata SET chain_digest = $1 WHERE singleton = 1",
                &[&"7f".repeat(32)],
            )
            .unwrap();
        assert_eq!(changed, 1);
        let before = metadata_snapshot(&mut inspector);
        assert_eq!(
            repository.verify_authoritative_schema().unwrap_err().code(),
            StableCode::FailedPrecondition
        );
        assert_eq!(
            repository
                .migrate_authoritative_schema(LATER_BINARY_SOURCE, 99, None)
                .unwrap_err()
                .code(),
            StableCode::FailedPrecondition
        );
        assert_eq!(
            metadata_snapshot(&mut inspector),
            before,
            "verification must not repair drift"
        );
    });
    with_database(&environment, |fixture| {
        install_v1(fixture, true);
        let mut inspector = fixture.client();
        inspector.batch_execute(
            "DROP TABLE trnm_storage_objects; \
             CREATE VIEW trnm_storage_objects AS SELECT \
             'view'::TEXT AS collection, 'fixture'::TEXT AS object_key, \
             decode(repeat('11',16),'hex') AS user_id, decode('01','hex') AS value_bytes, \
             decode(repeat('22',32),'hex') AS version_digest, \
             2::SMALLINT AS read_permission, 1::SMALLINT AS write_permission, 42::BIGINT AS updated_at_ms"
        ).unwrap();
        let mut repository = fixture.repository();
        let view = repository
            .migrate_authoritative_schema(UPGRADE_SOURCE, 23, None)
            .unwrap_err();
        assert_eq!(view.code(), StableCode::FailedPrecondition);
        assert_eq!(view.reason(), "authoritative_schema_table_kind_drift");
        let version: i64 = inspector
            .query_one(
                "SELECT schema_version FROM trnm_schema_metadata WHERE singleton = 1",
                &[],
            )
            .unwrap()
            .get(0);
        assert_eq!(
            version, 1,
            "a matching view name must not publish schema identity"
        );
    });
}

#[test]
fn authoritative_declared_partial_prefix_resumes_and_malformed_prefixes_reject() {
    let Some(environment) = live_environment() else {
        return;
    };
    with_database(&environment, |fixture| {
        install_v1(fixture, true);
        let legacy = fixture.legacy_writer();
        let mut inspector = fixture.client();
        seed_legacy_storage(&mut inspector);
        fixture.revoke_legacy_writes(&legacy);
        let first_action = match fixture.profile {
            DatabaseProfile::PostgreSql => {
                "ALTER TABLE trnm_schema_metadata ADD COLUMN chain_digest TEXT"
            }
            DatabaseProfile::CockroachDb => {
                "ALTER TABLE trnm_schema_metadata ADD COLUMN chain_digest STRING"
            }
        };
        // Simulate the committed first DDL action with metadata still at v1,
        // which is possible after an interrupted CockroachDB schema change.
        inspector.batch_execute(first_action).unwrap();
        let version: i64 = inspector
            .query_one(
                "SELECT schema_version FROM trnm_schema_metadata WHERE singleton = 1",
                &[],
            )
            .unwrap()
            .get(0);
        assert_eq!(version, 1);
        let mut repository = fixture.repository();
        repository.execute_migration_batch(
            "CREATE SCHEMA migration_shadow; \
             CREATE TABLE migration_shadow.trnm_schema_metadata AS SELECT * FROM public.trnm_schema_metadata; \
             CREATE TABLE migration_shadow.trnm_storage_objects AS SELECT * FROM public.trnm_storage_objects; \
             SET search_path = migration_shadow, public, pg_catalog",
        ).unwrap();
        let shadow_columns_before: i64 = inspector.query_one(
            "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'migration_shadow'",
            &[],
        ).unwrap().get(0);
        let rejected = repository
            .migrate_authoritative_schema(UPGRADE_SOURCE, 23, Some(&legacy))
            .unwrap_err();
        assert_eq!(rejected.code(), StableCode::FailedPrecondition);
        let shadow_columns_after: i64 = inspector.query_one(
            "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'migration_shadow'",
            &[],
        ).unwrap().get(0);
        assert_eq!(
            shadow_columns_before, shadow_columns_after,
            "refused append must not commit DDL into shadow tables"
        );
        repository
            .execute_migration_batch("SET search_path = DEFAULT")
            .unwrap();
        let report = repository
            .migrate_authoritative_schema(UPGRADE_SOURCE, 23, Some(&legacy))
            .unwrap();
        assert!(report.migration_applied);
        assert_eq!(report.applied_steps, 1);
        assert_identity(
            &report.identity,
            fixture.profile,
            ORIGINAL_SOURCE,
            UPGRADE_SOURCE,
        );
        let row = inspector
            .query_one(
                "SELECT create_time IS NULL, update_time IS NULL, value_bytes \
             FROM trnm_storage_objects WHERE collection = 'schema-upgrade'",
                &[],
            )
            .unwrap();
        assert!(row.get::<_, bool>(0));
        assert!(row.get::<_, bool>(1));
        assert_eq!(row.get::<_, Vec<u8>>(2), LEGACY_VALUE);
        assert_permission_denied(fixture.role_client(&legacy).execute(
            "DELETE FROM trnm_storage_objects WHERE collection = 'schema-upgrade'",
            &[],
        ));
        assert_eq!(
            repository
                .verify_authoritative_schema()
                .unwrap()
                .chain_digest,
            report.identity.chain_digest
        );
    });

    for malformed in ["missing_middle", "wrong_type", "unexpected_default"] {
        with_database(&environment, |fixture| {
            install_v1(fixture, true);
            let legacy = fixture.legacy_writer();
            fixture.revoke_legacy_writes(&legacy);
            let mut inspector = fixture.client();
            seed_legacy_storage(&mut inspector);
            let sql = match (malformed, fixture.profile) {
                ("missing_middle", _) => "ALTER TABLE trnm_storage_objects ADD COLUMN update_time TIMESTAMPTZ",
                ("wrong_type", DatabaseProfile::PostgreSql) => "ALTER TABLE trnm_schema_metadata ADD COLUMN chain_digest BIGINT",
                ("wrong_type", DatabaseProfile::CockroachDb) => "ALTER TABLE trnm_schema_metadata ADD COLUMN chain_digest INT8",
                ("unexpected_default", DatabaseProfile::PostgreSql) => "ALTER TABLE trnm_schema_metadata ADD COLUMN chain_digest TEXT DEFAULT 'invented'",
                ("unexpected_default", DatabaseProfile::CockroachDb) => "ALTER TABLE trnm_schema_metadata ADD COLUMN chain_digest STRING DEFAULT 'invented'",
                _ => unreachable!("fixture cases are statically bounded"),
            };
            inspector.batch_execute(sql).unwrap();
            let mut repository = fixture.repository();
            let error = repository
                .migrate_authoritative_schema(UPGRADE_SOURCE, 23, Some(&legacy))
                .unwrap_err();
            assert_eq!(error.code(), StableCode::FailedPrecondition, "{malformed}");
            let version: i64 = inspector
                .query_one(
                    "SELECT schema_version FROM trnm_schema_metadata WHERE singleton = 1",
                    &[],
                )
                .unwrap()
                .get(0);
            assert_eq!(version, 1, "{malformed} must not advance metadata");
            let value: Vec<u8> = inspector.query_one(
                "SELECT value_bytes FROM trnm_storage_objects WHERE collection = 'schema-upgrade'", &[],
            ).unwrap().get(0);
            assert_eq!(value, LEGACY_VALUE, "{malformed} must preserve legacy data");
        });
    }
}

#[test]
fn authoritative_existing_empty_v1_requires_a_real_unprivileged_writer_barrier() {
    let Some(environment) = live_environment() else {
        return;
    };
    with_database(&environment, |fixture| {
        install_v1(fixture, true);
        let mut repository = fixture.repository();
        let missing = repository
            .migrate_authoritative_schema(UPGRADE_SOURCE, 23, None)
            .unwrap_err();
        assert_eq!(missing.reason(), "legacy_storage_writer_barrier_required");
        let absent = format!("{}_absent", fixture.name);
        let missing_role = repository
            .migrate_authoritative_schema(UPGRADE_SOURCE, 23, Some(&absent))
            .unwrap_err();
        assert_eq!(missing_role.reason(), "legacy_storage_writer_role_missing");
        let mut inspector = fixture.client();
        let owner: String = inspector
            .query_one("SELECT current_user", &[])
            .unwrap()
            .get(0);
        let elevated = repository
            .migrate_authoritative_schema(UPGRADE_SOURCE, 23, Some(&owner))
            .unwrap_err();
        assert!(matches!(
            elevated.reason(),
            "invalid_legacy_storage_writer_role" | "legacy_storage_writer_not_fenced"
        ));
        let version: i64 = inspector
            .query_one("SELECT schema_version FROM trnm_schema_metadata", &[])
            .unwrap()
            .get(0);
        assert_eq!(version, 1, "even an empty existing v1 must remain fenced");
        let legacy = fixture.legacy_writer();
        fixture.revoke_legacy_writes(&legacy);
        let report = repository
            .migrate_authoritative_schema(UPGRADE_SOURCE, 23, Some(&legacy))
            .unwrap();
        assert_identity(
            &report.identity,
            fixture.profile,
            ORIGINAL_SOURCE,
            UPGRADE_SOURCE,
        );
    });
    with_database(&environment, |fixture| {
        // An empty, unbound 0001 left by interrupted fresh DDL is recoverable,
        // provided the explicit writer barrier precedes provenance binding.
        install_v1(fixture, false);
        let legacy = fixture.legacy_writer();
        fixture.revoke_legacy_writes(&legacy);
        let mut repository = fixture.repository();
        let report = repository
            .migrate_authoritative_schema(UPGRADE_SOURCE, 23, Some(&legacy))
            .unwrap();
        assert_eq!(report.applied_steps, 1);
        assert_identity(
            &report.identity,
            fixture.profile,
            UPGRADE_SOURCE,
            UPGRADE_SOURCE,
        );
    });
}

#[test]
fn authoritative_inherited_storage_privileges_are_not_a_writer_barrier() {
    let Some(environment) = live_environment() else {
        return;
    };
    with_database(&environment, |fixture| {
        install_v1(fixture, true);
        let legacy = fixture.legacy_writer();
        let ancestor = fixture.create_role("ancestor");
        let mut inspector = fixture.client();
        inspector
            .batch_execute(&format!(
                "GRANT INSERT, UPDATE, DELETE ON TABLE trnm_storage_objects TO {ancestor}; \
                 GRANT {ancestor} TO {legacy}"
            ))
            .unwrap();
        fixture.revoke_legacy_writes(&legacy);
        let mut legacy_client = fixture.role_client(&legacy);
        seed_legacy_storage(&mut legacy_client);
        let mut repository = fixture.repository();
        let inherited = repository
            .migrate_authoritative_schema(UPGRADE_SOURCE, 23, Some(&legacy))
            .unwrap_err();
        assert_eq!(inherited.reason(), "legacy_storage_writer_not_fenced");
        let version: i64 = inspector
            .query_one("SELECT schema_version FROM trnm_schema_metadata", &[])
            .unwrap()
            .get(0);
        assert_eq!(version, 1);
        fixture.revoke_legacy_writes(&ancestor);
        repository
            .migrate_authoritative_schema(UPGRADE_SOURCE, 23, Some(&legacy))
            .unwrap();
        assert_permission_denied(legacy_client.execute(
            "UPDATE trnm_storage_objects SET value_bytes = decode('02','hex') WHERE collection = 'schema-upgrade'",
            &[],
        ));
        // Explicitly remove membership before the generated role cleanup.
        inspector
            .batch_execute(&format!("REVOKE {ancestor} FROM {legacy}"))
            .unwrap();
    });

    // A real old session can clear storage with no INSERT/UPDATE/DELETE grants.
    // PostgreSQL requires TRUNCATE; CockroachDB 26.2 requires DROP, and its
    // has_table_privilege('TRUNCATE') inquiry misleadingly checks DELETE.
    for inherited in [false, true] {
        with_database(&environment, |fixture| {
            install_v1(fixture, true);
            let legacy = fixture.create_role("clear_legacy");
            fixture.grant_reads(&legacy);
            let ancestor = if inherited {
                Some(fixture.create_role("clear_ancestor"))
            } else {
                None
            };
            let grantee = ancestor.as_deref().unwrap_or(&legacy);
            let privilege = match fixture.profile {
                DatabaseProfile::PostgreSql => "TRUNCATE",
                DatabaseProfile::CockroachDb => "DROP",
            };
            let mut inspector = fixture.client();
            inspector
                .batch_execute(&format!(
                    "GRANT {privilege} ON TABLE public.trnm_storage_objects TO {grantee}"
                ))
                .unwrap();
            if let Some(ancestor) = &ancestor {
                inspector
                    .batch_execute(&format!("GRANT {ancestor} TO {legacy}"))
                    .unwrap();
            }
            assert_role_has_no_current_crud(&mut inspector, &legacy);
            let truncate_inquiry: bool = inspector
                .query_one(
                    "SELECT pg_catalog.has_table_privilege($1, 'public.trnm_storage_objects', 'TRUNCATE')",
                    &[&legacy],
                )
                .unwrap()
                .get(0);
            assert_eq!(
                truncate_inquiry,
                fixture.profile == DatabaseProfile::PostgreSql,
                "CockroachDB's TRUNCATE inquiry must not stand in for its actual DROP requirement"
            );
            seed_legacy_storage(&mut inspector);
            let mut old_session = fixture.role_client(&legacy);
            old_session
                .execute("TRUNCATE TABLE public.trnm_storage_objects", &[])
                .expect("fixture grant must actually permit storage truncation");
            assert!(legacy_storage_snapshot(&mut inspector).is_empty());
            seed_legacy_storage(&mut inspector);
            let before = legacy_storage_snapshot(&mut inspector);
            let mut repository = fixture.repository();
            assert_barrier_rejects_without_mutation(&mut repository, &mut inspector, &legacy);
            inspector
                .batch_execute(&format!(
                    "REVOKE {privilege} ON TABLE public.trnm_storage_objects FROM {grantee}"
                ))
                .unwrap();
            let report = repository
                .migrate_authoritative_schema(UPGRADE_SOURCE, 23, Some(&legacy))
                .unwrap();
            assert_eq!(report.applied_steps, 1);
            assert_identity(
                &report.identity,
                fixture.profile,
                ORIGINAL_SOURCE,
                UPGRADE_SOURCE,
            );
            assert_permission_denied(
                old_session.execute("TRUNCATE TABLE public.trnm_storage_objects", &[]),
            );
            assert_eq!(legacy_storage_snapshot(&mut inspector), before);
            if let Some(ancestor) = &ancestor {
                inspector
                    .batch_execute(&format!("REVOKE {ancestor} FROM {legacy}"))
                    .unwrap();
            }
            eprintln!(
                "schema_writer_destructive_barrier_executed profile={} inherited={inherited} privilege={privilege}",
                fixture.profile.metadata_value()
            );
        });
    }

    if environment.profile == DatabaseProfile::PostgreSql {
        // An ordinary NOINHERIT login may SET ROLE to a table/column writer or
        // truncator. Neither role is an owner or administrator, so the former
        // owner/admin-only membership check could falsely admit this upgrade.
        for privilege in ["UPDATE", "UPDATE (value_bytes)", "TRUNCATE"] {
            with_database(&environment, |fixture| {
                install_v1(fixture, true);
                let legacy = fixture.create_role("set_legacy");
                let ancestor = fixture.create_role("set_ancestor");
                fixture.grant_reads(&legacy);
                fixture.grant_reads(&ancestor);
                let mut inspector = fixture.client();
                inspector
                    .batch_execute(&format!(
                        "ALTER ROLE {legacy} NOINHERIT; \
                         GRANT {privilege} ON TABLE public.trnm_storage_objects TO {ancestor}; \
                         GRANT {ancestor} TO {legacy} WITH INHERIT FALSE, SET TRUE"
                    ))
                    .unwrap();
                assert_role_has_no_current_crud(&mut inspector, &legacy);
                let can_set: bool = inspector
                    .query_one(
                        "SELECT pg_catalog.pg_has_role($1, $2, 'SET')",
                        &[&legacy, &ancestor],
                    )
                    .unwrap()
                    .get(0);
                assert!(can_set);
                seed_legacy_storage(&mut inspector);
                let mut old_session = fixture.role_client(&legacy);
                old_session
                    .batch_execute(&format!("SET ROLE {ancestor}"))
                    .unwrap();
                let actual: String = old_session
                    .query_one("SELECT current_user", &[])
                    .unwrap()
                    .get(0);
                assert_eq!(actual, ancestor);
                let mutation = if privilege == "TRUNCATE" {
                    "TRUNCATE TABLE public.trnm_storage_objects"
                } else {
                    "UPDATE public.trnm_storage_objects SET value_bytes = value_bytes \
                     WHERE collection = 'schema-upgrade'"
                };
                let affected = old_session
                    .execute(mutation, &[])
                    .expect("SET-only grant must actually permit storage mutation");
                if privilege == "TRUNCATE" {
                    assert!(legacy_storage_snapshot(&mut inspector).is_empty());
                    seed_legacy_storage(&mut inspector);
                } else {
                    assert_eq!(affected, 1);
                }
                old_session.batch_execute("RESET ROLE").unwrap();
                let before = legacy_storage_snapshot(&mut inspector);
                let mut repository = fixture.repository();
                assert_barrier_rejects_without_mutation(&mut repository, &mut inspector, &legacy);
                inspector
                    .batch_execute(&format!(
                        "REVOKE {privilege} ON TABLE public.trnm_storage_objects FROM {ancestor}"
                    ))
                    .unwrap();
                let report = repository
                    .migrate_authoritative_schema(UPGRADE_SOURCE, 23, Some(&legacy))
                    .unwrap();
                assert_eq!(report.applied_steps, 1);
                assert_identity(
                    &report.identity,
                    fixture.profile,
                    ORIGINAL_SOURCE,
                    UPGRADE_SOURCE,
                );
                // Membership still allows SET ROLE. The original connection
                // must observe revoked table/column/TRUNCATE grants after it.
                old_session
                    .batch_execute(&format!("SET ROLE {ancestor}"))
                    .unwrap();
                assert_permission_denied(old_session.execute(mutation, &[]));
                old_session.batch_execute("RESET ROLE").unwrap();
                assert_eq!(legacy_storage_snapshot(&mut inspector), before);
                inspector
                    .batch_execute(&format!("REVOKE {ancestor} FROM {legacy}"))
                    .unwrap();
                eprintln!(
                    "schema_writer_set_only_barrier_executed profile=postgresql privilege={privilege}"
                );
            });
        }

        // ADMIN OPTION is enough to self-grant SET, even with SET FALSE and
        // INHERIT FALSE initially. The administered role below has no current
        // storage grants, but can SET ROLE to a separate ordinary writer.
        for privilege in ["UPDATE", "TRUNCATE"] {
            with_database(&environment, |fixture| {
                install_v1(fixture, true);
                let legacy = fixture.create_role("admin_legacy");
                let control = fixture.create_role("admin_control");
                let writer = fixture.create_role("admin_writer");
                fixture.grant_reads(&legacy);
                fixture.grant_reads(&writer);
                let mut inspector = fixture.client();
                inspector
                    .batch_execute(&format!(
                        "ALTER ROLE {legacy} NOINHERIT; \
                         ALTER ROLE {control} NOINHERIT; \
                         GRANT {privilege} ON TABLE public.trnm_storage_objects TO {writer}; \
                         GRANT {writer} TO {control} WITH ADMIN FALSE, INHERIT FALSE, SET TRUE; \
                         GRANT {control} TO {legacy} WITH ADMIN TRUE, INHERIT FALSE, SET FALSE"
                    ))
                    .unwrap();
                // A superuser may record the bootstrap superuser as grantor,
                // rather than current_user. Revoke precisely the fixture grant.
                let original_grantor: String = inspector
                    .query_one(
                        "SELECT grantor.rolname::TEXT FROM pg_catalog.pg_auth_members membership \
                         JOIN pg_catalog.pg_roles granted ON granted.oid = membership.roleid \
                         JOIN pg_catalog.pg_roles member_role ON member_role.oid = membership.member \
                         JOIN pg_catalog.pg_roles grantor ON grantor.oid = membership.grantor \
                         WHERE granted.rolname = $1 AND member_role.rolname = $2 \
                           AND membership.admin_option AND NOT membership.inherit_option \
                           AND NOT membership.set_option",
                        &[&control, &legacy],
                    )
                    .unwrap()
                    .get(0);
                let quoted_grantor = format!("\"{}\"", original_grantor.replace('"', "\"\""));
                assert_role_has_no_current_crud(&mut inspector, &legacy);
                assert_role_has_no_current_crud(&mut inspector, &control);
                let reachability = inspector
                    .query_one(
                        "SELECT pg_catalog.pg_has_role($1, $2, 'MEMBER WITH ADMIN OPTION'), \
                                pg_catalog.pg_has_role($1, $2, 'SET'), \
                                pg_catalog.pg_has_role($1, $3, 'SET'), \
                                pg_catalog.pg_has_role($1, $3, 'MEMBER WITH ADMIN OPTION')",
                        &[&legacy, &control, &writer],
                    )
                    .unwrap();
                assert!(reachability.get::<_, bool>(0));
                assert!(!reachability.get::<_, bool>(1));
                assert!(!reachability.get::<_, bool>(2));
                assert!(!reachability.get::<_, bool>(3));
                seed_legacy_storage(&mut inspector);
                let mutation = if privilege == "TRUNCATE" {
                    "TRUNCATE TABLE public.trnm_storage_objects"
                } else {
                    "UPDATE public.trnm_storage_objects SET value_bytes = value_bytes \
                     WHERE collection = 'schema-upgrade'"
                };
                let self_grant = format!(
                    "GRANT {control} TO {legacy} WITH ADMIN FALSE, INHERIT FALSE, SET TRUE \
                     GRANTED BY {legacy}"
                );
                let set_writer = format!("SET ROLE {writer}");
                let mut old_session = fixture.role_client(&legacy);
                assert_permission_denied(old_session.execute(&set_writer, &[]));
                assert_permission_denied(old_session.execute(mutation, &[]));
                old_session
                    .execute(&self_grant, &[])
                    .expect("ADMIN OPTION must actually permit a self-granted SET membership");
                old_session.execute(&set_writer, &[]).unwrap();
                let actual: String = old_session
                    .query_one("SELECT current_user", &[])
                    .unwrap()
                    .get(0);
                assert_eq!(actual, writer);
                let affected = old_session
                    .execute(mutation, &[])
                    .expect("self-granted SET chain must actually permit storage mutation");
                if privilege == "TRUNCATE" {
                    assert!(legacy_storage_snapshot(&mut inspector).is_empty());
                    seed_legacy_storage(&mut inspector);
                } else {
                    assert_eq!(affected, 1);
                }
                old_session.batch_execute("RESET ROLE").unwrap();
                inspector
                    .batch_execute(&format!(
                        "REVOKE {control} FROM {legacy} GRANTED BY {legacy} CASCADE"
                    ))
                    .unwrap();
                // Remove the self-issued SET grant, retaining the original
                // ADMIN-only membership. The barrier must still reject it.
                let remaining = inspector
                    .query_one(
                        "SELECT membership.admin_option, membership.inherit_option, \
                                membership.set_option, grantor.rolname::TEXT \
                         FROM pg_catalog.pg_auth_members membership \
                         JOIN pg_catalog.pg_roles granted ON granted.oid = membership.roleid \
                         JOIN pg_catalog.pg_roles member_role ON member_role.oid = membership.member \
                         JOIN pg_catalog.pg_roles grantor ON grantor.oid = membership.grantor \
                         WHERE granted.rolname = $1 AND member_role.rolname = $2",
                        &[&control, &legacy],
                    )
                    .unwrap();
                assert!(remaining.get::<_, bool>(0));
                assert!(!remaining.get::<_, bool>(1));
                assert!(!remaining.get::<_, bool>(2));
                assert_eq!(remaining.get::<_, String>(3), original_grantor);
                assert_role_has_no_current_crud(&mut inspector, &legacy);
                assert_permission_denied(old_session.execute(&set_writer, &[]));
                let before = legacy_storage_snapshot(&mut inspector);
                let mut repository = fixture.repository();
                assert_barrier_rejects_without_mutation(&mut repository, &mut inspector, &legacy);
                inspector
                    .batch_execute(&format!(
                        "REVOKE ADMIN OPTION FOR {control} FROM {legacy} \
                         GRANTED BY {quoted_grantor} CASCADE"
                    ))
                    .unwrap();
                let can_admin: bool = inspector
                    .query_one(
                        "SELECT pg_catalog.pg_has_role($1, $2, 'MEMBER WITH ADMIN OPTION')",
                        &[&legacy, &control],
                    )
                    .unwrap()
                    .get(0);
                assert!(!can_admin);
                assert_permission_denied(old_session.execute(&self_grant, &[]));
                assert_permission_denied(old_session.execute(&set_writer, &[]));
                assert_permission_denied(old_session.execute(mutation, &[]));
                let report = repository
                    .migrate_authoritative_schema(UPGRADE_SOURCE, 23, Some(&legacy))
                    .unwrap();
                assert_eq!(report.applied_steps, 1);
                assert_identity(
                    &report.identity,
                    fixture.profile,
                    ORIGINAL_SOURCE,
                    UPGRADE_SOURCE,
                );
                // The original login must observe the revocation both before
                // and after the migration, including attempts to restore SET.
                assert_permission_denied(old_session.execute(&self_grant, &[]));
                assert_permission_denied(old_session.execute(&set_writer, &[]));
                assert_permission_denied(old_session.execute(mutation, &[]));
                assert_eq!(legacy_storage_snapshot(&mut inspector), before);
                inspector
                    .batch_execute(&format!(
                        "REVOKE {control} FROM {legacy} GRANTED BY {quoted_grantor} CASCADE; \
                         REVOKE {writer} FROM {control} CASCADE"
                    ))
                    .unwrap();
                eprintln!(
                    "schema_writer_admin_barrier_executed profile=postgresql privilege={privilege}"
                );
            });
        }
    }
}
