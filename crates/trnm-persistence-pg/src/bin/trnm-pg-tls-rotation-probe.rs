#![forbid(unsafe_code)]

use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::Duration;

use trnm_persistence_pg::{DatabaseProfile, PgPool, PgPoolConfig, PgTlsConfig};

const MAX_DATABASE_URL_BYTES: usize = 4096;
const MAX_PEM_BYTES: usize = 1024 * 1024;

struct Inputs {
    old_database_url: String,
    new_database_url: String,
    old_root_path: PathBuf,
    new_root_path: PathBuf,
    old_generation: u64,
    new_generation: u64,
}

impl Inputs {
    fn from_environment() -> Result<Self, String> {
        let old_database_url = required_environment("TRNM_TLS_ROTATION_OLD_DATABASE_URL")?;
        let new_database_url = required_environment("TRNM_TLS_ROTATION_NEW_DATABASE_URL")?;
        validate_database_url(&old_database_url)?;
        validate_database_url(&new_database_url)?;
        if old_database_url == new_database_url {
            return Err("tls_rotation_database_endpoints_must_differ".to_owned());
        }

        let old_root_path = path_environment("TRNM_TLS_ROTATION_OLD_ROOT_CERT_PEM")?;
        let new_root_path = path_environment("TRNM_TLS_ROTATION_NEW_ROOT_CERT_PEM")?;
        let old_generation = generation_environment("TRNM_TLS_ROTATION_OLD_GENERATION")?;
        let new_generation = generation_environment("TRNM_TLS_ROTATION_NEW_GENERATION")?;
        validate_generation_transition(old_generation, new_generation)?;

        Ok(Self {
            old_database_url,
            new_database_url,
            old_root_path,
            new_root_path,
            old_generation,
            new_generation,
        })
    }
}

fn main() -> ExitCode {
    match Inputs::from_environment().and_then(run) {
        Ok(()) => ExitCode::SUCCESS,
        Err(reason) => {
            eprintln!("trnm-pg-tls-rotation-probe failed: {reason}");
            ExitCode::FAILURE
        }
    }
}

fn run(inputs: Inputs) -> Result<(), String> {
    let old_root = read_bounded_pem(&inputs.old_root_path)?;
    let new_root = read_bounded_pem(&inputs.new_root_path)?;
    if old_root == new_root {
        return Err("tls_rotation_root_material_unchanged".to_owned());
    }

    let old_tls = root_only_tls(old_root)?;
    let new_tls = root_only_tls(new_root)?;

    probe(
        "old_generation_connects_old_endpoint",
        &inputs.old_database_url,
        &old_tls,
    )?;
    probe(
        "new_generation_connects_new_endpoint",
        &inputs.new_database_url,
        &new_tls,
    )?;
    require_rejection(
        "old_generation_rejected_by_new_endpoint",
        &inputs.new_database_url,
        &old_tls,
    )?;
    require_rejection(
        "new_generation_rejected_by_old_endpoint",
        &inputs.old_database_url,
        &new_tls,
    )?;

    let invalid_tls = root_only_tls(
        b"-----BEGIN CERTIFICATE-----\ninvalid\n-----END CERTIFICATE-----\n".to_vec(),
    )?;
    require_rejection(
        "invalid_root_rejected",
        &inputs.old_database_url,
        &invalid_tls,
    )?;

    println!("schema=trillionnium.pg-tls-rotation-probe.v1");
    println!("old_generation={}", inputs.old_generation);
    println!("new_generation={}", inputs.new_generation);
    println!("assertion=old_generation_connects_old_endpoint");
    println!("assertion=new_generation_connects_new_endpoint");
    println!("assertion=old_generation_rejected_by_new_endpoint");
    println!("assertion=new_generation_rejected_by_old_endpoint");
    println!("assertion=invalid_root_rejected");
    println!("rotation_mode=rolling_pool_replacement");
    println!("hot_reload_claim=false");
    println!("production_ready=false");
    Ok(())
}

fn root_only_tls(root_certificate_pem: Vec<u8>) -> Result<PgTlsConfig, String> {
    PgTlsConfig::new(Some(root_certificate_pem), None, None)
        .map_err(|error| error.reason().to_owned())
}

fn probe(assertion: &str, database_url: &str, tls: &PgTlsConfig) -> Result<(), String> {
    let pool = PgPool::connect_tls(
        database_url,
        DatabaseProfile::PostgreSql,
        probe_policy(),
        tls,
    )
    .map_err(|error| format!("{assertion}:{}", error.reason()))?;
    let mut repository = pool
        .acquire()
        .map_err(|error| format!("{assertion}:{}", error.reason()))?;
    repository
        .table_exists("trnm_schema_metadata")
        .map_err(|error| format!("{assertion}:{}", error.reason()))?;
    Ok(())
}

fn require_rejection(
    assertion: &str,
    database_url: &str,
    tls: &PgTlsConfig,
) -> Result<(), String> {
    match probe(assertion, database_url, tls) {
        Ok(()) => Err(format!("{assertion}:unexpected_tls_acceptance")),
        Err(_) => Ok(()),
    }
}

fn probe_policy() -> PgPoolConfig {
    PgPoolConfig {
        max_size: 1,
        min_idle: 0,
        acquire_timeout: Duration::from_secs(2),
        idle_timeout: Duration::from_secs(5),
        max_lifetime: Duration::from_secs(30),
        statement_timeout: Duration::from_secs(2),
        lock_timeout: Duration::from_secs(1),
        idle_transaction_timeout: Duration::from_secs(2),
    }
}

fn required_environment(name: &str) -> Result<String, String> {
    match env::var(name) {
        Ok(value) if !value.is_empty() => Ok(value),
        Ok(_) | Err(_) => Err(format!("{name}_missing")),
    }
}

fn path_environment(name: &str) -> Result<PathBuf, String> {
    let value = required_environment(name)?;
    if value.len() > 4096 || value.bytes().any(|byte| byte.is_ascii_control()) {
        return Err(format!("{name}_invalid"));
    }
    Ok(PathBuf::from(value))
}

fn generation_environment(name: &str) -> Result<u64, String> {
    let value = required_environment(name)?;
    parse_generation(name, &value)
}

fn parse_generation(name: &str, value: &str) -> Result<u64, String> {
    let generation = value
        .parse::<u64>()
        .map_err(|_| format!("{name}_invalid"))?;
    if generation == 0 {
        return Err(format!("{name}_invalid"));
    }
    Ok(generation)
}

fn validate_generation_transition(old: u64, new: u64) -> Result<(), String> {
    if new <= old {
        return Err("tls_rotation_generation_must_increase".to_owned());
    }
    Ok(())
}

fn validate_database_url(value: &str) -> Result<(), String> {
    if value.len() > MAX_DATABASE_URL_BYTES
        || value
            .bytes()
            .any(|byte| byte.is_ascii_whitespace() || byte.is_ascii_control())
    {
        return Err("tls_rotation_database_url_invalid".to_owned());
    }
    Ok(())
}

fn read_bounded_pem(path: &Path) -> Result<Vec<u8>, String> {
    let metadata = fs::metadata(path).map_err(|_| "tls_rotation_pem_unreadable".to_owned())?;
    let size = usize::try_from(metadata.len())
        .map_err(|_| "tls_rotation_pem_too_large".to_owned())?;
    if size == 0 || size > MAX_PEM_BYTES {
        return Err("tls_rotation_pem_size_invalid".to_owned());
    }
    let value = fs::read(path).map_err(|_| "tls_rotation_pem_unreadable".to_owned())?;
    if value.len() != size {
        return Err("tls_rotation_pem_changed_while_reading".to_owned());
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn generation_transition_is_nonzero_and_strictly_monotonic() {
        assert_eq!(
            parse_generation("GENERATION", "0").unwrap_err(),
            "GENERATION_invalid"
        );
        assert_eq!(
            parse_generation("GENERATION", "not-a-number").unwrap_err(),
            "GENERATION_invalid"
        );
        assert!(validate_generation_transition(41, 42).is_ok());
        assert_eq!(
            validate_generation_transition(42, 42).unwrap_err(),
            "tls_rotation_generation_must_increase"
        );
        assert_eq!(
            validate_generation_transition(42, 41).unwrap_err(),
            "tls_rotation_generation_must_increase"
        );
    }

    #[test]
    fn database_urls_are_bounded_and_never_allow_control_bytes() {
        assert!(validate_database_url("host=127.0.0.1 port=5432").is_err());
        assert!(validate_database_url("host=127.0.0.1\npassword=secret").is_err());
        assert!(validate_database_url("postgresql://localhost/database").is_ok());
    }

    #[test]
    fn probe_policy_is_bounded_and_valid() {
        let policy = probe_policy().validate().unwrap();
        assert_eq!(policy.max_size, 1);
        assert_eq!(policy.min_idle, 0);
        assert!(policy.acquire_timeout <= policy.statement_timeout);
        assert!(policy.idle_timeout < policy.max_lifetime);
    }
}