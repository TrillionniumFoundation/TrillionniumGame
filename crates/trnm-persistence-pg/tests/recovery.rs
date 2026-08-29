use std::env;

use trnm_contracts::{CommandId, Digest32};
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

fn recovery_request() -> CommitRequest {
    CommitRequest {
        entity: EntityId::new([0xc0; 16]),
        command: CommandId::new([0xc1; 16]),
        fingerprint: digest(0xc2),
        expected_revision: 0,
        authority_generation: 1,
        next_state: digest(0xc3),
        committed_at_ms: 2_000,
        events: vec![EventInput {
            id: EventId::new([0xc4; 16]),
            payload: digest(0xc5),
        }],
        outbox: vec![OutboxInput {
            id: IntentId::new([0xc6; 16]),
            kind: IntentKind::Broadcast,
            payload: digest(0xc7),
            available_at_ms: 2_000,
        }],
    }
}

#[test]
fn state_survives_database_process_restart() {
    let Ok(database_url) = env::var("TRNM_DATABASE_URL") else {
        eprintln!("TRNM_DATABASE_URL absent; restart contract skipped");
        return;
    };
    let phase = env::var("TRNM_RECOVERY_PHASE").expect("TRNM_RECOVERY_PHASE is required");
    let profile = profile(
        &env::var("TRNM_DATABASE_PROFILE")
            .expect("TRNM_DATABASE_PROFILE is required with TRNM_DATABASE_URL"),
    );
    let request = recovery_request();
    let mut repository = PgRepository::connect(&database_url, profile).unwrap();
    match phase.as_str() {
        "seed" => {
            repository
                .bootstrap_entity(request.entity, 1, digest(0xbf), 1)
                .unwrap();
            assert!(matches!(
                repository.commit_command(&request).unwrap(),
                CommitOutcome::Applied(_)
            ));
        }
        "verify" => {
            let head = repository.load_head(request.entity).unwrap().unwrap();
            assert_eq!(head.revision, 1);
            assert_eq!(head.last_event_sequence, 1);
            assert_eq!(head.state, digest(0xc3));
            assert!(matches!(
                repository.commit_command(&request).unwrap(),
                CommitOutcome::Duplicate(_)
            ));
        }
        other => panic!("unsupported TRNM_RECOVERY_PHASE={other}"),
    }
}
