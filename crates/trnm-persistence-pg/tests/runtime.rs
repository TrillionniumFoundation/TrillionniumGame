use std::env;

use trnm_contracts::{CommandId, Digest32, StableCode};
use trnm_persistence_pg::{
    CommitOutcome, CommitRequest, DatabaseProfile, EntityId, EventId, EventInput, IntentId,
    IntentKind, OutboxInput, PgRepository,
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
        Ok(value) => {
            panic!("invalid TRNM_REQUIRE_LIVE_DATABASE={value:?}; expected 0/1 or false/true")
        }
    };

    let database_url = match env::var("TRNM_DATABASE_URL") {
        Ok(value) if !value.is_empty() => value,
        Ok(_) if required => panic!("{label}: TRNM_DATABASE_URL is required and must not be empty"),
        Ok(_) => {
            eprintln!("{label}: empty TRNM_DATABASE_URL; developer-only live test skip (no evidence credit)");
            return None;
        }
        Err(env::VarError::NotPresent) if required => {
            panic!("{label}: TRNM_REQUIRE_LIVE_DATABASE=1 but TRNM_DATABASE_URL is absent")
        }
        Err(env::VarError::NotPresent) => {
            eprintln!("{label}: TRNM_DATABASE_URL absent; developer-only live test skip (no evidence credit)");
            return None;
        }
        Err(error) => panic!("{label}: cannot read TRNM_DATABASE_URL: {error}"),
    };

    let profile_value = env::var("TRNM_DATABASE_PROFILE").unwrap_or_else(|_| {
        panic!("{label}: TRNM_DATABASE_PROFILE is required with TRNM_DATABASE_URL")
    });
    Some((database_url, profile(&profile_value)))
}

fn request() -> CommitRequest {
    CommitRequest {
        entity: EntityId::new([0x31; 16]),
        command: CommandId::new([0x32; 16]),
        fingerprint: digest(0x33),
        expected_revision: 0,
        authority_generation: 1,
        next_state: digest(0x34),
        committed_at_ms: 100,
        events: vec![EventInput {
            id: EventId::new([0x35; 16]),
            payload: digest(0x36),
        }],
        outbox: vec![OutboxInput {
            id: IntentId::new([0x37; 16]),
            kind: IntentKind::Broadcast,
            payload: digest(0x38),
            available_at_ms: 100,
        }],
    }
}

#[test]
fn pgwire_commit_duplicate_conflict_and_fence_contract() {
    let Some((database_url, profile)) = live_database_environment("PG-wire runtime contract")
    else {
        return;
    };
    let source_commit = env::var("TRNM_SCHEMA_SOURCE_COMMIT")
        .unwrap_or_else(|_| "e9b63462fa91383b06706894afed31b378f6b48c".to_owned());

    let mut repository = PgRepository::connect(&database_url, profile).unwrap();
    repository.bind_schema_metadata(&source_commit, 1).unwrap();
    let initial = repository
        .bootstrap_entity(EntityId::new([0x31; 16]), 1, digest(0x30), 1)
        .unwrap();
    assert_eq!(initial.revision, 0);
    assert_eq!(initial.last_event_sequence, 0);

    let request = request();
    let applied = match repository.commit_command(&request).unwrap() {
        CommitOutcome::Applied(receipt) => receipt,
        CommitOutcome::Duplicate(_) => panic!("fresh command reported duplicate"),
    };
    assert_eq!(applied.revision, 1);
    assert_eq!(applied.first_event_sequence, Some(1));
    assert_eq!(applied.last_event_sequence, 1);
    assert_eq!(applied.event_count, 1);
    assert_eq!(applied.outbox, vec![IntentId::new([0x37; 16])]);

    drop(repository);
    let mut repository = PgRepository::connect(&database_url, profile).unwrap();
    let replayed = match repository.commit_command(&request).unwrap() {
        CommitOutcome::Duplicate(receipt) => receipt,
        CommitOutcome::Applied(_) => panic!("post-commit replay applied a second visible effect"),
    };
    assert_eq!(replayed, applied);

    let mut conflict = request.clone();
    conflict.fingerprint = digest(0x39);
    let conflict_error = repository.commit_command(&conflict).unwrap_err();
    assert_eq!(conflict_error.code(), StableCode::AlreadyExists);
    assert_eq!(conflict_error.reason(), "command_id_conflict");

    let mut stale = request.clone();
    stale.command = CommandId::new([0x40; 16]);
    stale.fingerprint = digest(0x41);
    stale.events[0].id = EventId::new([0x42; 16]);
    stale.outbox[0].id = IntentId::new([0x43; 16]);
    let stale_error = repository.commit_command(&stale).unwrap_err();
    assert_eq!(stale_error.code(), StableCode::Aborted);
    assert_eq!(stale_error.reason(), "entity_revision_mismatch");

    let head = repository
        .load_head(EntityId::new([0x31; 16]))
        .unwrap()
        .expect("entity head must remain visible");
    assert_eq!(head.revision, 1);
    assert_eq!(head.last_event_sequence, 1);
    assert_eq!(head.state, digest(0x34));
}
