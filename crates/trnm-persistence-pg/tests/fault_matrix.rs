use std::env;

use postgres::{Client, NoTls};
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

fn request(seed: u8) -> CommitRequest {
    CommitRequest {
        entity: EntityId::new([seed; 16]),
        command: CommandId::new([seed.wrapping_add(1); 16]),
        fingerprint: digest(seed.wrapping_add(2)),
        expected_revision: 0,
        authority_generation: 1,
        next_state: digest(seed.wrapping_add(3)),
        committed_at_ms: u64::from(seed) + 1_000,
        events: vec![EventInput {
            id: EventId::new([seed.wrapping_add(4); 16]),
            payload: digest(seed.wrapping_add(5)),
        }],
        outbox: vec![OutboxInput {
            id: IntentId::new([seed.wrapping_add(6); 16]),
            kind: IntentKind::Broadcast,
            payload: digest(seed.wrapping_add(7)),
            available_at_ms: u64::from(seed) + 1_000,
        }],
    }
}

fn count_for_entity(client: &mut Client, table: &str, entity: EntityId) -> i64 {
    let sql = format!("SELECT count(*) FROM {table} WHERE entity_id = $1");
    client
        .query_one(&sql, &[&entity.as_bytes().as_slice()])
        .unwrap()
        .get(0)
}

fn assert_pristine(repository: &mut PgRepository, client: &mut Client, request: &CommitRequest) {
    let head = repository.load_head(request.entity).unwrap().unwrap();
    assert_eq!(head.revision, 0);
    assert_eq!(head.last_event_sequence, 0);
    for table in [
        "trnm_events",
        "trnm_outbox",
        "trnm_command_outbox",
    ] {
        assert_eq!(count_for_entity(client, table, request.entity), 0, "{table}");
    }
    let receipt_count: i64 = client
        .query_one(
            "SELECT count(*) FROM trnm_command_receipts WHERE entity_id = $1 AND command_id = $2",
            &[
                &request.entity.as_bytes().as_slice(),
                &request.command.as_bytes().as_slice(),
            ],
        )
        .unwrap()
        .get(0);
    assert_eq!(receipt_count, 0);
}

fn bootstrap(repository: &mut PgRepository, request: &CommitRequest, state_seed: u8) {
    repository
        .bootstrap_entity(request.entity, 1, digest(state_seed), 1)
        .unwrap();
}

#[test]
fn constraint_failures_roll_back_every_prior_write() {
    let Ok(database_url) = env::var("TRNM_DATABASE_URL") else {
        eprintln!("TRNM_DATABASE_URL absent; live fault matrix skipped");
        return;
    };
    let profile = profile(
        &env::var("TRNM_DATABASE_PROFILE")
            .expect("TRNM_DATABASE_PROFILE is required with TRNM_DATABASE_URL"),
    );

    // Failure at receipt insertion after the entity CAS: reserve revision 1 with
    // a different command. The attempted command must leave no partial state.
    let receipt_request = request(0x50);
    let mut repository = PgRepository::connect(&database_url, profile).unwrap();
    bootstrap(&mut repository, &receipt_request, 0x4f);
    let mut client = Client::connect(&database_url, NoTls).unwrap();
    client
        .execute(
            "INSERT INTO trnm_command_receipts \
             (entity_id, command_id, fingerprint, revision, state_digest, \
              first_event_sequence, last_event_sequence, event_count, committed_at_ms) \
             VALUES ($1, $2, $3, 1, $4, NULL, 0, 0, 1)",
            &[
                &receipt_request.entity.as_bytes().as_slice(),
                &[0x5f_u8; 16].as_slice(),
                &[0x5e_u8; 32].as_slice(),
                &[0x5d_u8; 32].as_slice(),
            ],
        )
        .unwrap();
    let error = repository.commit_command(&receipt_request).unwrap_err();
    assert_eq!(error.code(), StableCode::AlreadyExists);
    assert_pristine(&mut repository, &mut client, &receipt_request);
    client
        .execute(
            "DELETE FROM trnm_command_receipts WHERE entity_id = $1 AND command_id = $2",
            &[
                &receipt_request.entity.as_bytes().as_slice(),
                &[0x5f_u8; 16].as_slice(),
            ],
        )
        .unwrap();
    assert!(matches!(
        repository.commit_command(&receipt_request).unwrap(),
        CommitOutcome::Applied(_)
    ));

    // Failure at event insertion: another entity already owns the globally
    // unique event ID. Receipt and head updates for the target must roll back.
    let donor_event = request(0x60);
    let mut repository = PgRepository::connect(&database_url, profile).unwrap();
    bootstrap(&mut repository, &donor_event, 0x5f);
    repository.commit_command(&donor_event).unwrap();
    let mut event_request = request(0x70);
    event_request.events[0].id = donor_event.events[0].id;
    bootstrap(&mut repository, &event_request, 0x6f);
    let error = repository.commit_command(&event_request).unwrap_err();
    assert_eq!(error.code(), StableCode::AlreadyExists);
    let mut client = Client::connect(&database_url, NoTls).unwrap();
    assert_pristine(&mut repository, &mut client, &event_request);
    event_request.events[0].id = EventId::new([0x7e; 16]);
    assert!(matches!(
        repository.commit_command(&event_request).unwrap(),
        CommitOutcome::Applied(_)
    ));

    // Failure at outbox insertion: another command owns the globally unique
    // intent ID. The event inserted earlier in this transaction must roll back.
    let donor_outbox = request(0x80);
    let mut repository = PgRepository::connect(&database_url, profile).unwrap();
    bootstrap(&mut repository, &donor_outbox, 0x7f);
    repository.commit_command(&donor_outbox).unwrap();
    let mut outbox_request = request(0x90);
    outbox_request.outbox[0].id = donor_outbox.outbox[0].id;
    bootstrap(&mut repository, &outbox_request, 0x8f);
    let error = repository.commit_command(&outbox_request).unwrap_err();
    assert_eq!(error.code(), StableCode::AlreadyExists);
    let mut client = Client::connect(&database_url, NoTls).unwrap();
    assert_pristine(&mut repository, &mut client, &outbox_request);
    outbox_request.outbox[0].id = IntentId::new([0x9e; 16]);
    assert!(matches!(
        repository.commit_command(&outbox_request).unwrap(),
        CommitOutcome::Applied(_)
    ));
}

#[test]
fn committed_response_loss_replays_exact_receipt_after_reconnect() {
    let Ok(database_url) = env::var("TRNM_DATABASE_URL") else {
        eprintln!("TRNM_DATABASE_URL absent; response-loss contract skipped");
        return;
    };
    let profile = profile(
        &env::var("TRNM_DATABASE_PROFILE")
            .expect("TRNM_DATABASE_PROFILE is required with TRNM_DATABASE_URL"),
    );
    let request = request(0xb0);
    let mut repository = PgRepository::connect(&database_url, profile).unwrap();
    bootstrap(&mut repository, &request, 0xaf);
    let applied = match repository.commit_command(&request).unwrap() {
        CommitOutcome::Applied(receipt) => receipt,
        CommitOutcome::Duplicate(_) => panic!("fresh request was duplicate"),
    };

    // Simulate a server that committed successfully but lost the response.
    drop(repository);
    let mut repository = PgRepository::connect(&database_url, profile).unwrap();
    let replayed = match repository.commit_command(&request).unwrap() {
        CommitOutcome::Duplicate(receipt) => receipt,
        CommitOutcome::Applied(_) => panic!("ambiguous retry duplicated visible state"),
    };
    assert_eq!(replayed, applied);

    let mut client = Client::connect(&database_url, NoTls).unwrap();
    assert_eq!(count_for_entity(&mut client, "trnm_command_receipts", request.entity), 1);
    assert_eq!(count_for_entity(&mut client, "trnm_events", request.entity), 1);
    assert_eq!(count_for_entity(&mut client, "trnm_outbox", request.entity), 1);
    assert_eq!(count_for_entity(&mut client, "trnm_command_outbox", request.entity), 1);
}
