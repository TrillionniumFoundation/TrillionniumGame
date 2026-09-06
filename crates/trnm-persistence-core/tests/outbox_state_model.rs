#![forbid(unsafe_code)]

use trnm_contracts::{CommandId, Digest32};
use trnm_persistence_core::{
    CommandIntent, DurableState, EntityId, EventId, EventInput, IntentId, IntentKind, NodeId,
    OutboxInput, OutboxRecord, OutboxState, PrepareOutcome,
};

const MAX_ATTEMPTS: u64 = 32;
const WORKER_COUNT: usize = 2;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Action {
    Lease(usize),
    Retry(usize),
    Apply(usize, u8),
    DeadLetter(usize, u8),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Phase {
    Pending,
    Leased { worker: usize, generation: u64 },
    Applied { receipt: u8 },
    DeadLetter { reason: u8 },
    AttemptLimit,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct Model {
    attempt: u64,
    lease_generation: u64,
    phase: Phase,
    tokens: [Option<u64>; WORKER_COUNT],
}

impl Default for Model {
    fn default() -> Self {
        Self {
            attempt: 0,
            lease_generation: 0,
            phase: Phase::Pending,
            tokens: [None; WORKER_COUNT],
        }
    }
}

impl Model {
    fn execute(&mut self, action: Action) -> Result<(), &'static str> {
        match action {
            Action::Lease(worker) => {
                if self.phase != Phase::Pending {
                    return Err("outbox_not_pending");
                }
                self.lease_generation = self
                    .lease_generation
                    .checked_add(1)
                    .expect("bounded model generation");
                self.phase = Phase::Leased {
                    worker,
                    generation: self.lease_generation,
                };
                self.tokens[worker] = Some(self.lease_generation);
                Ok(())
            }
            Action::Retry(worker) => {
                self.require_fence(worker)?;
                if self.attempt >= MAX_ATTEMPTS {
                    self.phase = Phase::AttemptLimit;
                    return Ok(());
                }
                self.attempt = self.attempt.checked_add(1).expect("bounded model attempt");
                self.phase = Phase::Pending;
                Ok(())
            }
            Action::Apply(worker, receipt) => {
                if let Phase::Applied { receipt: current } = self.phase {
                    return if current == receipt {
                        Ok(())
                    } else {
                        Err("outbox_receipt_mismatch")
                    };
                }
                self.require_fence(worker)?;
                self.phase = Phase::Applied { receipt };
                Ok(())
            }
            Action::DeadLetter(worker, reason) => {
                self.require_fence(worker)?;
                self.phase = Phase::DeadLetter { reason };
                Ok(())
            }
        }
    }

    fn require_fence(&self, worker: usize) -> Result<(), &'static str> {
        let Some(generation) = self.tokens[worker] else {
            return Err("outbox_lease_mismatch");
        };
        match self.phase {
            Phase::Leased {
                worker: current_worker,
                generation: current_generation,
            } if current_worker == worker && current_generation == generation => Ok(()),
            _ => Err("outbox_lease_mismatch"),
        }
    }
}

fn id(value: u8) -> [u8; 16] {
    [value; 16]
}

fn digest(value: u8) -> Digest32 {
    Digest32::new([value; 32])
}

fn worker(index: usize) -> NodeId {
    NodeId::new(id(u8::try_from(index + 11).expect("bounded worker")))
}

fn intent() -> CommandIntent {
    CommandIntent {
        entity: EntityId::new(id(1)),
        command: CommandId::new(id(2)),
        fingerprint: digest(3),
        expected_revision: 0,
        authority_generation: 1,
        next_state: digest(4),
        events: vec![EventInput {
            id: EventId::new(id(5)),
            payload: digest(6),
        }],
        outbox: vec![OutboxInput {
            id: IntentId::new(id(7)),
            kind: IntentKind::ExternalEffect,
            payload: digest(8),
        }],
    }
}

fn fixture() -> (DurableState, IntentId) {
    let mut state = DurableState::default();
    state
        .bootstrap(EntityId::new(id(1)), 1, digest(9))
        .expect("bootstrap fixture");
    let prepared = match state.prepare(intent()).expect("prepare fixture") {
        PrepareOutcome::Prepared(value) => value,
        PrepareOutcome::Duplicate(_) => panic!("fresh fixture cannot be duplicate"),
    };
    let receipt = state.commit(prepared).expect("commit fixture");
    let intent_id = *receipt.outbox.first().expect("one outbox intent");
    (state, intent_id)
}

fn execute_actual(
    state: &mut DurableState,
    intent_id: IntentId,
    tokens: &mut [Option<u64>; WORKER_COUNT],
    action: Action,
) -> Result<OutboxRecord, &'static str> {
    let result = match action {
        Action::Lease(index) => state.lease(intent_id, worker(index)),
        Action::Retry(index) => {
            state.retry(intent_id, worker(index), tokens[index].unwrap_or_default())
        }
        Action::Apply(index, receipt) => state.apply(
            intent_id,
            worker(index),
            tokens[index].unwrap_or_default(),
            digest(receipt),
        ),
        Action::DeadLetter(index, reason) => state.dead_letter(
            intent_id,
            worker(index),
            tokens[index].unwrap_or_default(),
            digest(reason),
        ),
    };
    match result {
        Ok(record) => {
            if let Action::Lease(index) = action {
                tokens[index] = Some(record.lease_generation);
            }
            Ok(record)
        }
        Err(error) => Err(error.reason()),
    }
}

fn project(record: OutboxRecord) -> (u64, u64, Phase) {
    let phase = match record.state {
        OutboxState::Pending => Phase::Pending,
        OutboxState::Leased { owner, generation } => {
            let worker = (0..WORKER_COUNT)
                .find(|index| owner == self::worker(*index))
                .expect("model only uses known workers");
            Phase::Leased { worker, generation }
        }
        OutboxState::Applied { receipt } if receipt == digest(70) => Phase::Applied { receipt: 70 },
        OutboxState::Applied { receipt } if receipt == digest(71) => Phase::Applied { receipt: 71 },
        OutboxState::Applied { .. } => panic!("model only uses known receipts"),
        OutboxState::DeadLetter { reason } if reason == digest(90) => {
            Phase::DeadLetter { reason: 90 }
        }
        OutboxState::DeadLetter { reason } if reason == digest(91) => {
            Phase::DeadLetter { reason: 91 }
        }
        OutboxState::DeadLetter { reason } => {
            assert!(!reason.is_zero(), "attempt-limit reason must be nonzero");
            Phase::AttemptLimit
        }
    };
    (record.attempt, record.lease_generation, phase)
}

fn action(value: u64) -> Action {
    match value % 10 {
        0 => Action::Lease(0),
        1 => Action::Lease(1),
        2 => Action::Retry(0),
        3 => Action::Retry(1),
        4 => Action::Apply(0, 70),
        5 => Action::Apply(1, 70),
        6 => Action::Apply(0, 71),
        7 => Action::Apply(1, 71),
        8 => Action::DeadLetter(0, 90),
        _ => Action::DeadLetter(1, 91),
    }
}

fn next(value: &mut u64) -> u64 {
    *value = value
        .wrapping_mul(6_364_136_223_846_793_005)
        .wrapping_add(1_442_695_040_888_963_407);
    *value
}

fn assert_model(seed: u64, step: usize, model: &Model, record: OutboxRecord) {
    assert_eq!(
        project(record),
        (model.attempt, model.lease_generation, model.phase),
        "state divergence at seed={seed} step={step}"
    );
}

#[test]
fn implementation_matches_adversarial_state_model() {
    for seed in 0..4_096_u64 {
        let (mut state, intent_id) = fixture();
        let mut model = Model::default();
        let mut actual_tokens = [None; WORKER_COUNT];
        let mut random = seed ^ 0x9e37_79b9_7f4a_7c15;

        for step in 0..96 {
            let operation = action(next(&mut random));
            let before_record = state.outbox(intent_id).expect("record remains durable");
            let before_tokens = actual_tokens;
            let before_model = model.clone();

            let expected = model.execute(operation);
            let observed = execute_actual(&mut state, intent_id, &mut actual_tokens, operation);

            match (expected, observed) {
                (Ok(()), Ok(record)) => assert_model(seed, step, &model, record),
                (Err(expected_reason), Err(observed_reason)) => {
                    assert_eq!(
                        observed_reason, expected_reason,
                        "error divergence at seed={seed} step={step} operation={operation:?}"
                    );
                    assert_eq!(
                        state.outbox(intent_id),
                        Some(before_record),
                        "failed operation mutated durable state at seed={seed} step={step}"
                    );
                    assert_eq!(
                        actual_tokens, before_tokens,
                        "failed operation mutated worker token at seed={seed} step={step}"
                    );
                    assert_eq!(
                        model, before_model,
                        "failed model operation mutated state at seed={seed} step={step}"
                    );
                }
                (expected, observed) => panic!(
                    "outcome divergence at seed={seed} step={step} operation={operation:?}: expected={expected:?} observed={observed:?}"
                ),
            }
            assert_eq!(
                actual_tokens, model.tokens,
                "worker-token divergence at seed={seed} step={step}"
            );
            assert_model(
                seed,
                step,
                &model,
                state.outbox(intent_id).expect("record remains durable"),
            );
        }
    }
}

#[test]
fn stale_generations_never_mutate_after_release() {
    let (mut state, intent_id) = fixture();
    let mut tokens = [None; WORKER_COUNT];

    let first =
        execute_actual(&mut state, intent_id, &mut tokens, Action::Lease(0)).expect("first lease");
    execute_actual(&mut state, intent_id, &mut tokens, Action::Retry(0))
        .expect("release first lease");
    let second =
        execute_actual(&mut state, intent_id, &mut tokens, Action::Lease(1)).expect("second lease");

    let before = state.outbox(intent_id).expect("record");
    for receipt in [70, 71] {
        assert_eq!(
            state
                .apply(
                    intent_id,
                    worker(0),
                    first.lease_generation,
                    digest(receipt),
                )
                .expect_err("stale apply must fail")
                .reason(),
            "outbox_lease_mismatch"
        );
        assert_eq!(state.outbox(intent_id), Some(before));
    }
    assert_eq!(
        state
            .retry(intent_id, worker(0), first.lease_generation)
            .expect_err("stale retry must fail")
            .reason(),
        "outbox_lease_mismatch"
    );
    assert_eq!(state.outbox(intent_id), Some(before));
    assert_eq!(
        state
            .dead_letter(intent_id, worker(0), first.lease_generation, digest(90),)
            .expect_err("stale dead-letter must fail")
            .reason(),
        "outbox_lease_mismatch"
    );
    assert_eq!(state.outbox(intent_id), Some(before));

    state
        .apply(intent_id, worker(1), second.lease_generation, digest(70))
        .expect("current generation applies");
}

#[test]
fn attempt_limit_is_terminal_and_stable_under_all_later_actions() {
    let (mut state, intent_id) = fixture();
    let mut tokens = [None; WORKER_COUNT];

    for _ in 0..MAX_ATTEMPTS {
        execute_actual(&mut state, intent_id, &mut tokens, Action::Lease(0))
            .expect("lease before retry");
        execute_actual(&mut state, intent_id, &mut tokens, Action::Retry(0))
            .expect("bounded retry");
    }
    execute_actual(&mut state, intent_id, &mut tokens, Action::Lease(1)).expect("final lease");
    let terminal = execute_actual(&mut state, intent_id, &mut tokens, Action::Retry(1))
        .expect("attempt limit transitions to terminal state");
    assert_eq!(terminal.attempt, MAX_ATTEMPTS);
    assert!(matches!(terminal.state, OutboxState::DeadLetter { .. }));

    for operation in [
        Action::Lease(0),
        Action::Lease(1),
        Action::Retry(0),
        Action::Retry(1),
        Action::Apply(0, 70),
        Action::Apply(1, 71),
        Action::DeadLetter(0, 90),
        Action::DeadLetter(1, 91),
    ] {
        let before = state.outbox(intent_id).expect("terminal record");
        let result = execute_actual(&mut state, intent_id, &mut tokens, operation);
        assert!(
            result.is_err(),
            "terminal action unexpectedly succeeded: {operation:?}"
        );
        assert_eq!(state.outbox(intent_id), Some(before));
    }
}
