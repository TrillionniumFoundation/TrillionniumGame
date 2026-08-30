use std::env;

use trnm_contracts::{CommandId, Digest32, StableCode};
use trnm_persistence_pg::{
    CommitRequest, DatabaseProfile, EntityId, EventId, EventInput, IntentId, IntentKind, NodeId,
    OutboxInput, OutboxRetryOutcome, OutboxState, PgRepository,
};

fn live_configuration() -> Option<(String, DatabaseProfile)> {
    let required = env::var("TRNM_LIVE_TEST_REQUIRED").ok().as_deref() == Some("1");
    let url = env::var("TRNM_PG_TEST_URL").ok();
    let profile = env::var("TRNM_PG_TEST_PROFILE").ok();
    match (url, profile.as_deref()) {
        (Some(url), Some("postgresql")) => Some((url, DatabaseProfile::PostgreSql)),
        (Some(url), Some("cockroachdb")) => Some((url, DatabaseProfile::CockroachDb)),
        _ if required => panic!("required outbox live-test configuration is absent or invalid"),
        _ => None,
    }
}

fn commit_request(
    entity: EntityId,
    command_byte: u8,
    expected_revision: u64,
    state_byte: u8,
    intent_byte: u8,
    committed_at_ms: u64,
) -> CommitRequest {
    CommitRequest {
        entity,
        command: CommandId::new([command_byte; 16]),
        fingerprint: Digest32::new([command_byte.saturating_add(32); 32]),
        expected_revision,
        authority_generation: 1,
        next_state: Digest32::new([state_byte; 32]),
        committed_at_ms,
        events: vec![EventInput {
            id: EventId::new([command_byte.saturating_add(64); 16]),
            payload: Digest32::new([command_byte.saturating_add(96); 32]),
        }],
        outbox: vec![OutboxInput {
            id: IntentId::new([intent_byte; 16]),
            kind: IntentKind::ExternalEffect,
            payload: Digest32::new([intent_byte.saturating_add(1); 32]),
            available_at_ms: committed_at_ms,
        }],
    }
}

#[test]
fn outbox_claim_retry_crash_reclaim_deadletter_and_completion_are_durable() {
    let Some((url, profile)) = live_configuration() else {
        return;
    };
    let entity = EntityId::new([11; 16]);
    let first_intent = IntentId::new([21; 16]);
    let second_intent = IntentId::new([22; 16]);
    let owner_one = NodeId::new([31; 16]);
    let owner_two = NodeId::new([32; 16]);
    let owner_three = NodeId::new([33; 16]);
    let exhausted = Digest32::new([41; 32]);

    let mut repository = PgRepository::connect(&url, profile).unwrap();
    repository
        .bootstrap_entity(entity, 1, Digest32::new([12; 32]), 90)
        .unwrap();
    repository
        .commit_command(&commit_request(entity, 13, 0, 14, 21, 100))
        .unwrap();

    let first = repository
        .claim_outbox(owner_one, 100, 10, 2, exhausted, 64)
        .unwrap();
    assert_eq!(first.len(), 1);
    let first_lease = first[0];
    assert_eq!(first_lease.id, first_intent);
    assert_eq!(first_lease.attempt, 1);
    assert_eq!(first_lease.lease_generation, 1);
    assert_eq!(first_lease.lease_expires_at_ms, 110);

    let pending = repository
        .retry_or_dead_letter_outbox(&first_lease, 105, 110, 2, exhausted)
        .unwrap();
    assert_eq!(
        pending,
        OutboxRetryOutcome::Pending {
            next_available_at_ms: 110,
            attempt: 1,
        }
    );

    let second = repository
        .claim_outbox(owner_two, 110, 10, 2, exhausted, 64)
        .unwrap();
    assert_eq!(second.len(), 1);
    let second_lease = second[0];
    assert_eq!(second_lease.id, first_intent);
    assert_eq!(second_lease.attempt, 2);
    assert_eq!(second_lease.lease_generation, 2);

    let stale = repository
        .complete_outbox(&first_lease, Digest32::new([42; 32]), 109)
        .unwrap_err();
    assert_eq!(stale.code(), StableCode::Aborted);

    drop(repository);
    let mut repository = PgRepository::connect(&url, profile).unwrap();
    let reclaimed = repository
        .claim_outbox(owner_three, 120, 10, 2, exhausted, 64)
        .unwrap();
    assert!(reclaimed.is_empty());
    let dead = repository
        .load_outbox_record(first_intent)
        .unwrap()
        .unwrap();
    assert_eq!(dead.state, OutboxState::DeadLetter);
    assert_eq!(dead.attempt, 2);
    assert_eq!(dead.lease_generation, 2);
    assert_eq!(dead.owner, None);
    assert_eq!(dead.dead_reason, Some(exhausted));

    repository
        .commit_command(&commit_request(entity, 14, 1, 15, 22, 130))
        .unwrap();
    let deliverable = repository
        .claim_outbox(owner_three, 130, 10, 3, exhausted, 64)
        .unwrap();
    assert_eq!(deliverable.len(), 1);
    assert_eq!(deliverable[0].id, second_intent);
    let receipt = Digest32::new([43; 32]);
    repository
        .complete_outbox(&deliverable[0], receipt, 135)
        .unwrap();
    let delivered = repository
        .load_outbox_record(second_intent)
        .unwrap()
        .unwrap();
    assert_eq!(delivered.state, OutboxState::Delivered);
    assert_eq!(delivered.owner, None);
    assert_eq!(delivered.receipt, Some(receipt));

    drop(repository);
    let mut repository = PgRepository::connect(&url, profile).unwrap();
    assert_eq!(
        repository
            .load_outbox_record(first_intent)
            .unwrap()
            .unwrap()
            .state,
        OutboxState::DeadLetter
    );
    assert_eq!(
        repository
            .load_outbox_record(second_intent)
            .unwrap()
            .unwrap()
            .state,
        OutboxState::Delivered
    );
}
