use std::env;

use trnm_contracts::{CommandId, Digest32, StableCode, UserId};
use trnm_persistence_pg::{
    CommitRequest, DatabaseProfile, EntityId, NodeId, PgRepository, ReadPermission, StorageActor,
    StorageBatchOperation, StorageDeleteOperation, StorageObjectKey, StorageWriteOperation,
    VersionCheck, WritePermission,
};

fn digest(value: u8) -> Digest32 {
    Digest32::new([value; 32])
}

fn profile(value: &str) -> DatabaseProfile {
    match value {
        "postgresql" => DatabaseProfile::PostgreSql,
        "cockroachdb" => DatabaseProfile::CockroachDb,
        other => panic!("unsupported TRNM_DATABASE_PROFILE={other}"),
    }
}

fn live_database_environment(label: &str) -> Option<(String, DatabaseProfile)> {
    let required = match env::var("TRNM_REQUIRE_LIVE_DATABASE") {
        Err(env::VarError::NotPresent) => false,
        Err(error) => panic!("cannot read TRNM_REQUIRE_LIVE_DATABASE: {error}"),
        Ok(value) if matches!(value.as_str(), "1" | "true" | "TRUE" | "yes" | "YES") => true,
        Ok(value) if matches!(value.as_str(), "0" | "false" | "FALSE" | "no" | "NO") => false,
        Ok(value) => panic!("invalid TRNM_REQUIRE_LIVE_DATABASE={value:?}"),
    };
    let database_url = match env::var("TRNM_DATABASE_URL") {
        Ok(value) if !value.is_empty() => value,
        Ok(_) if required => panic!("{label}: empty TRNM_DATABASE_URL"),
        Ok(_) => return None,
        Err(env::VarError::NotPresent) if required => {
            panic!("{label}: required TRNM_DATABASE_URL is absent")
        }
        Err(env::VarError::NotPresent) => return None,
        Err(error) => panic!("{label}: cannot read TRNM_DATABASE_URL: {error}"),
    };
    let profile_value = env::var("TRNM_DATABASE_PROFILE")
        .unwrap_or_else(|_| panic!("{label}: TRNM_DATABASE_PROFILE is required"));
    Some((database_url, profile(&profile_value)))
}
