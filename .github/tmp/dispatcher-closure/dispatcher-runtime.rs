use std::collections::{BTreeMap, VecDeque};
use std::env;

use trnm_contracts::{CommandId, Digest32, DomainError};
use trnm_persistence_pg::{
    CommitRequest, DatabaseProfile, DeliveryFailure, DispatchClock, DispatchConfig, EntityId,
    EventId, EventInput, IntentId, IntentKind, NodeId, OutboxDispatcher, OutboxInput, OutboxSink,
    OutboxState, PgRepository,
};

fn live_configuration() -> Option<(String, DatabaseProfile)> {
    let required = env::var("TRNM_LIVE_TEST_REQUIRED").ok().as_deref() == Some("1");
    let url = env::var("TRNM_PG_TEST_URL").ok();
    let profile = env::var("TRNM_PG_TEST_PROFILE").ok();
    match (url, profile.as_deref()) {
        (Some(url), Some("postgresql")) => Some((url, DatabaseProfile::PostgreSql)),
        (Some(url), Some("cockroachdb")) => Some((url, DatabaseProfile::CockroachDb)),
        _ if required => panic!("required dispatcher live-test configuration is absent or invalid"),
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

#[derive(Clone, Copy, Debug)]
enum SinkAction {
    Reconcile(Digest32),
    Deliver(Digest32),
    Retryable(Digest32),
    Terminal(Digest32),
}

#[derive(Debug)]
struct ScriptedSink {
    actions: BTreeMap<IntentId, SinkAction>,
    reconcile_calls: usize,
    delivery_calls: usize,
}

impl ScriptedSink {
    fn one(intent: IntentId, action: SinkAction) -> Self {
        Self {
            actions: BTreeMap::from([(intent, action)]),
            reconcile_calls: 0,
            delivery_calls: 0,
        }
    }

    fn action(&self, intent: IntentId) -> SinkAction {
        self.actions
            .get(&intent)
            .copied()
            .unwrap_or(SinkAction::Terminal(Digest32::new([99; 32])))
    }
}

impl OutboxSink for ScriptedSink {
    fn reconcile(
        &mut self,
        idempotency_key: IntentId,
        lease: &trnm_persistence_pg::OutboxLease,
    ) -> Result<Option<Digest32>, DeliveryFailure> {
        assert_eq!(idempotency_key, lease.id);
        self.reconcile_calls = self.reconcile_calls.saturating_add(1);
        match self.action(idempotency_key) {
            SinkAction::Reconcile(receipt) => Ok(Some(receipt)),
            _ => Ok(None),
        }
    }

    fn deliver(
        &mut self,
        idempotency_key: IntentId,
        lease: &trnm_persistence_pg::OutboxLease,
    ) -> Result<Digest32, DeliveryFailure> {
        assert_eq!(idempotency_key, lease.id);
        self.delivery_calls = self.delivery_calls.saturating_add(1);
        match self.action(idempotency_key) {
            SinkAction::Deliver(receipt) => Ok(receipt),
            SinkAction::Retryable(reason) => Err(DeliveryFailure::retryable(reason)),
            SinkAction::Terminal(reason) => Err(DeliveryFailure::terminal(reason)),
            SinkAction::Reconcile(_) => panic!("reconciled intent must not be delivered again"),
        }
    }
}

#[derive(Debug)]
struct SequenceClock {
    values: VecDeque<u64>,
}

impl SequenceClock {
    fn new(values: impl IntoIterator<Item = u64>) -> Self {
        Self {
            values: values.into_iter().collect(),
        }
    }
}

impl DispatchClock for SequenceClock {
    fn now_ms(&mut self) -> Result<u64, DomainError> {
        self.values.pop_front().ok_or_else(|| {
            DomainError::new(
                trnm_contracts::StableCode::Internal,
                "dispatcher_test_clock_exhausted",
                trnm_contracts::RetryClass::Never,
            )
        })
    }
}

fn config() -> DispatchConfig {
    DispatchConfig {
        lease_duration_ms: 100,
        retry_delay_ms: 10,
        max_attempts: 3,
        batch_limit: 8,
        exhausted_reason: Digest32::new([90; 32]),
    }
}

#[test]
fn dispatcher_reconciles_delivers_retries_and_deadletters_on_live_profile() {
    let Some((url, profile)) = live_configuration() else {
        return;
    };
    let entity = EntityId::new([51; 16]);
    let reconcile_intent = IntentId::new([61; 16]);
    let deliver_intent = IntentId::new([62; 16]);
    let retry_intent = IntentId::new([63; 16]);
    let owner = NodeId::new([64; 16]);

    let mut repository = PgRepository::connect(&url, profile).unwrap();
    repository
        .bootstrap_entity(entity, 1, Digest32::new([52; 32]), 900)
        .unwrap();

    repository
        .commit_command(&commit_request(entity, 53, 0, 54, 61, 1000))
        .unwrap();
    let reconcile_receipt = Digest32::new([71; 32]);
    let mut sink = ScriptedSink::one(
        reconcile_intent,
        SinkAction::Reconcile(reconcile_receipt),
    );
    let mut clock = SequenceClock::new([1000, 1001]);
    let report = {
        let mut dispatcher =
            OutboxDispatcher::new(&mut repository, &mut sink, &mut clock, owner, config())
                .unwrap();
        dispatcher.run_once().unwrap()
    };
    assert_eq!(report.claimed, 1);
    assert_eq!(report.reconciled, 1);
    assert_eq!(report.delivered, 0);
    assert_eq!(sink.reconcile_calls, 1);
    assert_eq!(sink.delivery_calls, 0);
    let reconciled = repository
        .load_outbox_record(reconcile_intent)
        .unwrap()
        .unwrap();
    assert_eq!(reconciled.state, OutboxState::Delivered);
    assert_eq!(reconciled.receipt, Some(reconcile_receipt));

    repository
        .commit_command(&commit_request(entity, 54, 1, 55, 62, 1100))
        .unwrap();
    let delivery_receipt = Digest32::new([72; 32]);
    let mut sink = ScriptedSink::one(deliver_intent, SinkAction::Deliver(delivery_receipt));
    let mut clock = SequenceClock::new([1100, 1101]);
    let report = {
        let mut dispatcher =
            OutboxDispatcher::new(&mut repository, &mut sink, &mut clock, owner, config())
                .unwrap();
        dispatcher.run_once().unwrap()
    };
    assert_eq!(report.claimed, 1);
    assert_eq!(report.delivered, 1);
    assert_eq!(sink.delivery_calls, 1);
    let delivered = repository
        .load_outbox_record(deliver_intent)
        .unwrap()
        .unwrap();
    assert_eq!(delivered.state, OutboxState::Delivered);
    assert_eq!(delivered.receipt, Some(delivery_receipt));

    repository
        .commit_command(&commit_request(entity, 55, 2, 56, 63, 1200))
        .unwrap();
    let retry_reason = Digest32::new([73; 32]);
    let mut sink = ScriptedSink::one(retry_intent, SinkAction::Retryable(retry_reason));
    let mut clock = SequenceClock::new([1200, 1201]);
    let report = {
        let mut dispatcher =
            OutboxDispatcher::new(&mut repository, &mut sink, &mut clock, owner, config())
                .unwrap();
        dispatcher.run_once().unwrap()
    };
    assert_eq!(report.claimed, 1);
    assert_eq!(report.retried, 1);
    let pending = repository
        .load_outbox_record(retry_intent)
        .unwrap()
        .unwrap();
    assert_eq!(pending.state, OutboxState::Pending);
    assert_eq!(pending.attempt, 1);
    assert_eq!(pending.available_at_ms, 1211);

    let terminal_reason = Digest32::new([74; 32]);
    let mut sink = ScriptedSink::one(retry_intent, SinkAction::Terminal(terminal_reason));
    let mut clock = SequenceClock::new([1211, 1212]);
    let report = {
        let mut dispatcher =
            OutboxDispatcher::new(&mut repository, &mut sink, &mut clock, owner, config())
                .unwrap();
        dispatcher.run_once().unwrap()
    };
    assert_eq!(report.claimed, 1);
    assert_eq!(report.dead_lettered, 1);
    let dead = repository
        .load_outbox_record(retry_intent)
        .unwrap()
        .unwrap();
    assert_eq!(dead.state, OutboxState::DeadLetter);
    assert_eq!(dead.attempt, 2);
    assert_eq!(dead.dead_reason, Some(terminal_reason));

    drop(repository);
    let mut repository = PgRepository::connect(&url, profile).unwrap();
    assert_eq!(
        repository
            .load_outbox_record(reconcile_intent)
            .unwrap()
            .unwrap()
            .state,
        OutboxState::Delivered
    );
    assert_eq!(
        repository
            .load_outbox_record(deliver_intent)
            .unwrap()
            .unwrap()
            .state,
        OutboxState::Delivered
    );
    assert_eq!(
        repository
            .load_outbox_record(retry_intent)
            .unwrap()
            .unwrap()
            .state,
        OutboxState::DeadLetter
    );
}
