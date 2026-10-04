//! Disposable actual-server diagnostic. No production admission or parity claim.
use super::{read_catalog, validate_source_commit, AdmittedTarget};
use crate::runtime::config::{AuthAuthorityConfig, DatabaseTlsMode, ServerConfig};
use crate::runtime::legacy_service_exports::LegacyServerAuthConfig;
use crate::{AuthoritativeSchemaTarget, DatabaseProfile, PgPoolConfig, PgRepository};
use base64::engine::general_purpose::STANDARD;
use base64::Engine;
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::fs::OpenOptions;
use std::io::Write;
use std::net::SocketAddr;
use std::path::Path;
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

#[path = "account_native_auth_wire.rs"]
mod wire;
use wire::{require, Result};

const FIXTURE: &str = include_str!("../../../../oracle/immutable/auth-reference-cases.json");
// Exact reference projection, wrapped only to decode the native JSON as TEXT.
const PROJECTION: &str = "SELECT (SELECT json_build_object(\n 'users', (SELECT coalesce(json_agg(t ORDER BY id), '[]') FROM\n   (SELECT id, username, custom_id, create_time, update_time, disable_time, metadata, wallet, edge_count FROM users WHERE id <> '00000000-0000-0000-0000-000000000000' LIMIT 3) t),\n 'user_device', (SELECT coalesce(json_agg(t ORDER BY id), '[]') FROM\n   (SELECT id, user_id FROM user_device LIMIT 3) t)))::TEXT";

fn snapshot(repository: &mut PgRepository) -> Result<Value> {
    let row = repository
        .client
        .query_one(PROJECTION, &[])
        .map_err(|_| "snapshot query")?;
    let text: String = row.try_get(0).map_err(|_| "snapshot type")?;
    let value = wire::parse(text.as_bytes())?;
    require(
        value["users"].as_array().is_some_and(|a| a.len() <= 2)
            && value["user_device"]
                .as_array()
                .is_some_and(|a| a.len() <= 1),
        "snapshot contamination",
    )?;
    Ok(value)
}
fn fresh_key() -> Result<String> {
    let mut bytes = [0_u8; 32];
    openssl::rand::rand_bytes(&mut bytes).map_err(|_| "fixture entropy")?;
    Ok(STANDARD.encode(bytes))
}
fn resolve(value: &Value, tokens: &BTreeMap<String, String>) -> Result<Value> {
    match value {
        Value::String(s) if s.starts_with('$') => tokens
            .get(&s[1..])
            .map(|s| json!(s))
            .ok_or("unexecuted token dependency"),
        Value::Object(o) => o
            .iter()
            .map(|(k, v)| Ok((k.clone(), resolve(v, tokens)?)))
            .collect::<Result<serde_json::Map<_, _>>>()
            .map(Value::Object),
        _ => Ok(value.clone()),
    }
}
struct Keys {
    server: String,
    access: String,
    refresh: String,
    admin: String,
}
impl std::fmt::Debug for Keys {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("FixtureKeys(<redacted>)")
    }
}
impl Keys {
    fn new() -> Result<Self> {
        Ok(Self {
            server: fresh_key()?,
            access: fresh_key()?,
            refresh: fresh_key()?,
            admin: fresh_key()?,
        })
    }
    fn forbidden(&self) -> [&[u8]; 4] {
        [
            self.server.as_bytes(),
            self.access.as_bytes(),
            self.refresh.as_bytes(),
            self.admin.as_bytes(),
        ]
    }
}
fn capture_cases(
    repository: &mut PgRepository,
    address: SocketAddr,
    keys: &Keys,
    records: &mut Vec<Value>,
    attempted_requests: &mut usize,
) -> Result<()> {
    let fixture = wire::parse(FIXTURE.as_bytes())?;
    let cases = fixture["cases"].as_array().ok_or("fixture cases")?;
    require(cases.len() == 15, "fixture count")?;
    let mut tokens = BTreeMap::<String, String>::new();
    let basic = format!("Basic {}", STANDARD.encode(format!("{}:", keys.server)));
    for case in cases {
        let id = case["id"].as_str().ok_or("fixture id")?;
        let path = case["path"].as_str().ok_or("fixture path")?;
        let authorization = if case["auth"] == "server" {
            basic.clone()
        } else {
            let reference = case["auth"].as_str().ok_or("fixture auth")?;
            format!(
                "Bearer {}",
                tokens
                    .get(reference.strip_prefix('$').ok_or("fixture auth ref")?)
                    .ok_or("unexecuted auth dependency")?
            )
        };
        let payload =
            serde_json::to_vec(&resolve(&case["body"], &tokens)?).map_err(|_| "request JSON")?;
        let before = snapshot(repository)?;
        *attempted_requests += 1;
        let mut response = wire::exchange(
            address,
            "POST",
            path,
            &payload,
            Some(&authorization),
            (keys.access.as_bytes(), keys.refresh.as_bytes()),
            &keys.forbidden(),
        )?;
        let after = snapshot(repository)?;
        response.record["id"] = json!(id);
        response.record["before"] = before;
        response.record["after"] = after;
        records.push(response.record);
        for field in ["token", "refresh_token"] {
            if let Some(token) = response.live_body.get(field).and_then(Value::as_str) {
                require(tokens.len() < 32, "token vault bound")?;
                tokens.insert(format!("{id}.{field}"), token.to_owned());
            }
        }
    }
    Ok(())
}
fn write_report(output: &Path, value: &Value) -> Result<()> {
    let bytes = serde_json::to_vec(value).map_err(|_| "report encode")?;
    require(bytes.len() <= wire::LIMIT, "report bound")?;
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(output.join("native-auth-result.json"))
        .map_err(|_| "report create")?;
    file.write_all(&bytes).map_err(|_| "report write")
}
fn run_disposable() -> Result<()> {
    require(
        wire::digest(FIXTURE.as_bytes())
            == "sha256:fcde649cdcca60c4c2a09971f9c3b89d160bdc0662edcde14e80c5dedf0eca63",
        "fixture identity",
    )?;
    require(
        std::env::var("TRNM_NATIVE_AUTH_DISPOSABLE").as_deref() == Ok("hosted-container-fixture"),
        "disposable custody required",
    )?;
    let profile_name = std::env::var("TRNM_NATIVE_AUTH_PROFILE").map_err(|_| "profile required")?;
    let (profile, database_url) = match profile_name.as_str() {
        "postgresql" => (
            DatabaseProfile::PostgreSql,
            "postgresql://trnm:trnm_live_password@127.0.0.1:55435/trnm_native_auth",
        ),
        "cockroachdb" => (
            DatabaseProfile::CockroachDb,
            "postgresql://root@127.0.0.1:26257/trnm_native_auth",
        ),
        _ => return Err("unsupported fixture profile"),
    };
    let source = std::env::var("TRNM_SCHEMA_SOURCE_COMMIT").map_err(|_| "source required")?;
    validate_source_commit(&source).map_err(|_| "source identity")?;
    let output = std::env::var("TRNM_NATIVE_AUTH_OUTPUT").map_err(|_| "output required")?;
    let output = Path::new(&output);
    require(
        output.is_absolute() && output.is_dir(),
        "owned output directory",
    )?;
    let mut repository =
        PgRepository::connect(database_url, profile).map_err(|_| "fixture connection")?;
    let marker: String = repository
        .client
        .query_one("SELECT marker FROM fixture_custody", &[])
        .map_err(|_| "fixture marker")?
        .try_get(0)
        .map_err(|_| "fixture marker type")?;
    require(
        marker == format!("{source}:{profile_name}:native-auth15"),
        "fixture marker mismatch",
    )?;
    require(
        read_catalog(&mut *repository.client)
            .map_err(|_| "initial catalog")?
            .is_empty(),
        "nonempty fixture catalog",
    )?;
    let v4 = repository
        .migrate_authoritative_schema(&source, 1, None)
        .map_err(|_| "typed migration4")?;
    require(v4.identity.schema_version == 4, "migration4 identity")?;
    let role = format!("native_auth_{profile_name}");
    repository
        .client
        .batch_execute(&format!("CREATE ROLE {role} NOLOGIN"))
        .map_err(|_| "fixture role")?;
    let admission = AdmittedTarget::diagnostic(None);
    let v5 = repository
        .migrate_authoritative_schema_admitted(&source, 2, Some(&role), admission)
        .map_err(|_| "typed migration5")?;
    require(
        v5.identity.schema_version == 5
            && v5.identity.v4_apply_source_commit.as_deref() == Some(source.as_str()),
        "migration5 publisher",
    )?;
    require(
        repository
            .verify_authoritative_schema_target(AuthoritativeSchemaTarget::NakamaAccountsV5)
            .err()
            .is_some_and(|error| error.reason() == "schema5_native_catalog_capture_pending"),
        "public gate changed",
    )?;
    require(
        snapshot(&mut repository)? == json!({"users":[],"user_device":[]}),
        "nonempty initial projection",
    )?;
    let keys = Keys::new()?;
    let address: SocketAddr = "127.0.0.1:17361".parse().map_err(|_| "fixture address")?;
    let config = ServerConfig {
        bind: address,
        grpc_bind: None,
        database_url: database_url.to_owned(),
        database_profile: profile,
        database_tls_mode: DatabaseTlsMode::PlaintextCandidate,
        database_tls_root_cert: None,
        database_tls_identity_cert: None,
        database_tls_identity_key: None,
        database_pool: PgPoolConfig {
            max_size: 2,
            ..PgPoolConfig::default()
        },
        schema_source_commit: source.clone(),
        schema_target: AuthoritativeSchemaTarget::NakamaAccountsV5,
        admin_token: keys.admin.clone(),
        auth_authority: AuthAuthorityConfig::NakamaLegacy(
            LegacyServerAuthConfig::new(
                keys.server.as_bytes().to_vec(),
                keys.access.as_bytes().to_vec(),
                keys.refresh.as_bytes().to_vec(),
                3600,
                86400,
                false,
            )
            .map_err(|_| "typed legacy configuration")?,
        ),
        max_request_bytes: 128 * 1024,
        read_timeout: wire::TIMEOUT,
        write_timeout: wire::TIMEOUT,
    };
    let pooled = crate::runtime::schema::open_verified_repository_admitted(&config, admission)
        .map_err(|_| "actual startup pool/schema")?;
    let (send, receive) = mpsc::sync_channel(1);
    let handle = thread::spawn(move || {
        let ok = crate::runtime::server::serve_admitted(&config, pooled, admission).is_ok();
        let _ = send.send(ok);
    });
    let mut records = Vec::new();
    let mut attempted_requests = 0;
    let execution = (|| {
        let deadline = Instant::now() + wire::TIMEOUT;
        loop {
            require(!handle.is_finished(), "server exited before ready")?;
            if let Ok(response) = wire::exchange(
                address,
                "GET",
                "/healthcheck",
                b"",
                None,
                (keys.access.as_bytes(), keys.refresh.as_bytes()),
                &keys.forbidden(),
            ) {
                require(response.record["status"] == 200, "actual listener health")?;
                require(Instant::now() <= deadline, "late startup response")?;
                break;
            }
            require(Instant::now() < deadline, "startup deadline")?;
            thread::sleep(Duration::from_millis(20));
        }
        capture_cases(
            &mut repository,
            address,
            &keys,
            &mut records,
            &mut attempted_requests,
        )
    })();
    // Always request the real authenticated drain, even if capture failed.
    let drain = wire::exchange(
        address,
        "POST",
        "/-/drain",
        b"",
        Some(&format!("Bearer {}", keys.admin)),
        (keys.access.as_bytes(), keys.refresh.as_bytes()),
        &keys.forbidden(),
    );
    let join_deadline = Instant::now() + wire::TIMEOUT;
    let returned = receive.recv_timeout(wire::TIMEOUT).ok();
    let joined = join_until(handle, join_deadline);
    let drain_ok =
        drain.as_ref().is_ok_and(|r| r.record["status"] == 200) && returned == Some(true) && joined;
    let report = json!({"schema":"trillionnium.native-auth15-diagnostic.v1","source_commit":source,
        "profile":profile_name,"database":"trnm_native_auth","fixture_sha256":wire::digest(FIXTURE.as_bytes()),
        "execution":"actual-server-tcp-pool-native-accounts-test-admission","records":records,
        "completed_case_count":records.len(),"attempted_request_count":attempted_requests,
        "incomplete_attempt_may_have_effect":attempted_requests > records.len(),"capture_complete":execution.is_ok() && records.len()==15 && drain_ok,
        "failure":execution.err(),"actual_authenticated_drain_joined":drain_ok,
        "same_candidate_migration4_to5":true,"prior4_publisher_retained":true,"public_gate_closed":true,
        "raw_tokens_retained":false,"keys_retained":false,"normalization_applied":false,
        "paired_exact_oracle":false,"production_ready":false,"public_accounts_v5_activation":false,
        "startup_qualified":false,"restore_qualified":false,"compatibility_credit":false});
    write_report(output, &report)?;
    execution?;
    require(drain_ok, "drain/join incomplete")
}

fn join_until<T>(handle: std::thread::JoinHandle<T>, deadline: std::time::Instant) -> bool {
    while !handle.is_finished() {
        let Some(left) = deadline.checked_duration_since(std::time::Instant::now()) else {
            return false;
        };
        std::thread::sleep(left.min(std::time::Duration::from_millis(1)));
    }
    handle.join().is_ok()
}

#[test]
fn native_auth_join_waits_for_exit_after_notification() {
    let (send, receive) = std::sync::mpsc::channel();
    let handle = std::thread::spawn(move || {
        send.send(()).unwrap();
        std::thread::sleep(std::time::Duration::from_millis(20));
    });
    receive.recv().unwrap();
    assert!(join_until(
        handle,
        std::time::Instant::now() + std::time::Duration::from_secs(1)
    ));
    let handle = std::thread::spawn(|| std::thread::sleep(std::time::Duration::from_millis(20)));
    assert!(!join_until(handle, std::time::Instant::now()));
}

#[test]
#[ignore = "owned disposable native auth15 diagnostic; no production AccountsV5 admission"]
fn accounts_native_auth_disposable_listener() {
    if let Err(reason) = run_disposable() {
        panic!("native auth diagnostic failed: {reason}");
    }
}
#[test]
fn native_auth_fixture_is_exact_fifteen_and_custody_resolves_locally() {
    let fixture = wire::parse(FIXTURE.as_bytes()).unwrap();
    assert_eq!(fixture["cases"].as_array().unwrap().len(), 15);
    assert_eq!(
        wire::digest(FIXTURE.as_bytes()),
        "sha256:fcde649cdcca60c4c2a09971f9c3b89d160bdc0662edcde14e80c5dedf0eca63"
    );
    assert!(resolve(&json!("$missing.token"), &BTreeMap::new()).is_err());
    let tokens = BTreeMap::from([("case.token".to_owned(), "memory-only".to_owned())]);
    assert!(resolve(&json!({"token":"$case.token"}), &tokens).is_ok());
}
