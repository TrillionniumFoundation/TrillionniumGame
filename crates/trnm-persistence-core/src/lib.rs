#![forbid(unsafe_code)]

use std::collections::{BTreeMap, BTreeSet};

use trnm_contracts::{CommandId, Digest32, DomainError, RetryClass, StableCode};

const MAX_EVENTS: usize = 64;
const MAX_INTENTS: usize = 64;
const MAX_ATTEMPTS: u64 = 32;

macro_rules! id16 {
    ($name:ident) => {
        #[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
        pub struct $name([u8; 16]);

        impl $name {
            #[must_use]
            pub const fn new(value: [u8; 16]) -> Self {
                Self(value)
            }

            #[must_use]
            pub fn is_zero(self) -> bool {
                self.0.iter().all(|byte| *byte == 0)
            }
        }
    };
}

id16!(EntityId);
id16!(EventId);
id16!(IntentId);
id16!(NodeId);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum IntentKind {
    Broadcast,
    SearchIndex,
    Notification,
    ExternalEffect,
    Completion,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct EventInput {
    pub id: EventId,
    pub payload: Digest32,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct OutboxInput {
    pub id: IntentId,
    pub kind: IntentKind,
    pub payload: Digest32,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CommandIntent {
    pub entity: EntityId,
    pub command: CommandId,
    pub fingerprint: Digest32,
    pub expected_revision: u64,
    pub authority_generation: u64,
    pub next_state: Digest32,
    pub events: Vec<EventInput>,
    pub outbox: Vec<OutboxInput>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PreparedCommit {
    intent: CommandIntent,
    next_revision: u64,
    first_sequence: Option<u64>,
    last_sequence: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct EntityHead {
    pub revision: u64,
    pub last_sequence: u64,
    pub authority_generation: u64,
    pub state: Digest32,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Receipt {
    pub entity: EntityId,
    pub command: CommandId,
    pub fingerprint: Digest32,
    pub revision: u64,
    pub state: Digest32,
    pub first_sequence: Option<u64>,
    pub last_sequence: u64,
    pub event_count: usize,
    pub outbox: Vec<IntentId>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum PrepareOutcome {
    Prepared(PreparedCommit),
    Duplicate(Receipt),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum OutboxState {
    Pending,
    Leased { owner: NodeId, generation: u64 },
    Applied { receipt: Digest32 },
    DeadLetter { reason: Digest32 },
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct OutboxRecord {
    pub id: IntentId,
    pub entity: EntityId,
    pub command: CommandId,
    pub kind: IntentKind,
    pub payload: Digest32,
    pub attempt: u64,
    pub lease_generation: u64,
    pub state: OutboxState,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct DurableState {
    entities: BTreeMap<EntityId, EntityHead>,
    commands: BTreeMap<(EntityId, CommandId), Receipt>,
    events: BTreeMap<(EntityId, u64), (EventId, Digest32, CommandId)>,
    outbox: BTreeMap<IntentId, OutboxRecord>,
}

impl DurableState {
    pub fn bootstrap(
        &mut self,
        entity: EntityId,
        authority_generation: u64,
        state: Digest32,
    ) -> Result<EntityHead, DomainError> {
        nonzero(entity.is_zero(), "invalid_entity_id")?;
        if authority_generation == 0 || state.is_zero() {
            return Err(invalid("invalid_entity_bootstrap"));
        }
        if self.entities.contains_key(&entity) {
            return Err(error(
                StableCode::AlreadyExists,
                "entity_already_exists",
                RetryClass::Never,
            ));
        }
        let head = EntityHead {
            revision: 0,
            last_sequence: 0,
            authority_generation,
            state,
        };
        self.entities.insert(entity, head);
        Ok(head)
    }

    #[must_use]
    pub fn head(&self, entity: EntityId) -> Option<EntityHead> {
        self.entities.get(&entity).copied()
    }

    #[must_use]
    pub fn outbox(&self, id: IntentId) -> Option<OutboxRecord> {
        self.outbox.get(&id).copied()
    }

    #[must_use]
    pub fn event_count(&self, entity: EntityId) -> usize {
        self.events
            .range((entity, 0)..=(entity, u64::MAX))
            .count()
    }

    pub fn prepare(&self, intent: CommandIntent) -> Result<PrepareOutcome, DomainError> {
        validate_intent(&intent)?;
        if let Some(receipt) = self.commands.get(&(intent.entity, intent.command)) {
            return if receipt.fingerprint == intent.fingerprint {
                Ok(PrepareOutcome::Duplicate(receipt.clone()))
            } else {
                Err(error(
                    StableCode::AlreadyExists,
                    "command_id_conflict",
                    RetryClass::Never,
                ))
            };
        }
        let head = self.entities.get(&intent.entity).ok_or_else(not_found)?;
        fence(head, intent.expected_revision, intent.authority_generation)?;
        if intent
            .outbox
            .iter()
            .any(|item| self.outbox.contains_key(&item.id))
        {
            return Err(error(
                StableCode::AlreadyExists,
                "outbox_intent_already_exists",
                RetryClass::Never,
            ));
        }
        let next_revision = head.revision.checked_add(1).ok_or_else(overflow)?;
        let event_count = u64::try_from(intent.events.len()).map_err(|_| overflow())?;
        let last_sequence = head
            .last_sequence
            .checked_add(event_count)
            .ok_or_else(overflow)?;
        let first_sequence = if event_count == 0 {
            None
        } else {
            Some(head.last_sequence.checked_add(1).ok_or_else(overflow)?)
        };
        Ok(PrepareOutcome::Prepared(PreparedCommit {
            intent,
            next_revision,
            first_sequence,
            last_sequence,
        }))
    }

    pub fn commit(&mut self, prepared: PreparedCommit) -> Result<Receipt, DomainError> {
        if let Some(receipt) = self
            .commands
            .get(&(prepared.intent.entity, prepared.intent.command))
        {
            return if receipt.fingerprint == prepared.intent.fingerprint {
                Ok(receipt.clone())
            } else {
                Err(error(
                    StableCode::AlreadyExists,
                    "command_id_conflict",
                    RetryClass::Never,
                ))
            };
        }
        let head = self
            .entities
            .get(&prepared.intent.entity)
            .copied()
            .ok_or_else(not_found)?;
        fence(
            &head,
            prepared.intent.expected_revision,
            prepared.intent.authority_generation,
        )?;
        if head.revision.checked_add(1).ok_or_else(overflow)? != prepared.next_revision {
            return Err(stale_prepare());
        }
        if prepared
            .intent
            .outbox
            .iter()
            .any(|item| self.outbox.contains_key(&item.id))
        {
            return Err(error(
                StableCode::AlreadyExists,
                "outbox_intent_already_exists",
                RetryClass::Never,
            ));
        }

        let mut events = self.events.clone();
        let mut outbox = self.outbox.clone();
        let mut sequence = head.last_sequence;
        for event in &prepared.intent.events {
            sequence = sequence.checked_add(1).ok_or_else(overflow)?;
            if events
                .insert(
                    (prepared.intent.entity, sequence),
                    (event.id, event.payload, prepared.intent.command),
                )
                .is_some()
            {
                return Err(error(
                    StableCode::DataLoss,
                    "event_sequence_collision",
                    RetryClass::Never,
                ));
            }
        }
        if sequence != prepared.last_sequence {
            return Err(stale_prepare());
        }
        let mut outbox_ids = Vec::with_capacity(prepared.intent.outbox.len());
        for item in &prepared.intent.outbox {
            let record = OutboxRecord {
                id: item.id,
                entity: prepared.intent.entity,
                command: prepared.intent.command,
                kind: item.kind,
                payload: item.payload,
                attempt: 0,
                lease_generation: 0,
                state: OutboxState::Pending,
            };
            if outbox.insert(item.id, record).is_some() {
                return Err(error(
                    StableCode::AlreadyExists,
                    "outbox_intent_already_exists",
                    RetryClass::Never,
                ));
            }
            outbox_ids.push(item.id);
        }

        let receipt = Receipt {
            entity: prepared.intent.entity,
            command: prepared.intent.command,
            fingerprint: prepared.intent.fingerprint,
            revision: prepared.next_revision,
            state: prepared.intent.next_state,
            first_sequence: prepared.first_sequence,
            last_sequence: prepared.last_sequence,
            event_count: prepared.intent.events.len(),
            outbox: outbox_ids,
        };
        self.entities.insert(
            prepared.intent.entity,
            EntityHead {
                revision: receipt.revision,
                last_sequence: receipt.last_sequence,
                authority_generation: prepared.intent.authority_generation,
                state: receipt.state,
            },
        );
        self.commands.insert(
            (prepared.intent.entity, prepared.intent.command),
            receipt.clone(),
        );
        self.events = events;
        self.outbox = outbox;
        Ok(receipt)
    }

    pub fn takeover(
        &mut self,
        entity: EntityId,
        expected_generation: u64,
    ) -> Result<EntityHead, DomainError> {
        let head = self.entities.get_mut(&entity).ok_or_else(not_found)?;
        if head.authority_generation != expected_generation {
            return Err(generation_mismatch());
        }
        head.authority_generation = head.authority_generation.checked_add(1).ok_or_else(overflow)?;
        Ok(*head)
    }

    pub fn lease(&mut self, id: IntentId, owner: NodeId) -> Result<OutboxRecord, DomainError> {
        nonzero(owner.is_zero(), "invalid_outbox_owner")?;
        let record = self.outbox.get_mut(&id).ok_or_else(outbox_not_found)?;
        if record.state != OutboxState::Pending {
            return Err(error(
                StableCode::FailedPrecondition,
                "outbox_not_pending",
                RetryClass::SafeBackoff,
            ));
        }
        record.lease_generation = record.lease_generation.checked_add(1).ok_or_else(overflow)?;
        record.state = OutboxState::Leased {
            owner,
            generation: record.lease_generation,
        };
        Ok(*record)
    }

    pub fn apply(
        &mut self,
        id: IntentId,
        owner: NodeId,
        generation: u64,
        receipt: Digest32,
    ) -> Result<OutboxRecord, DomainError> {
        if receipt.is_zero() {
            return Err(invalid("invalid_outbox_receipt"));
        }
        let record = self.outbox.get_mut(&id).ok_or_else(outbox_not_found)?;
        match record.state {
            OutboxState::Applied { receipt: current } if current == receipt => return Ok(*record),
            OutboxState::Applied { .. } => {
                return Err(error(
                    StableCode::DataLoss,
                    "outbox_receipt_mismatch",
                    RetryClass::Never,
                ));
            }
            _ => lease_fence(record, owner, generation)?,
        }
        record.state = OutboxState::Applied { receipt };
        Ok(*record)
    }

    pub fn retry(
        &mut self,
        id: IntentId,
        owner: NodeId,
        generation: u64,
    ) -> Result<OutboxRecord, DomainError> {
        let record = self.outbox.get_mut(&id).ok_or_else(outbox_not_found)?;
        lease_fence(record, owner, generation)?;
        record.attempt = record.attempt.checked_add(1).ok_or_else(overflow)?;
        if record.attempt > MAX_ATTEMPTS {
            return Err(error(
                StableCode::ResourceExhausted,
                "outbox_attempt_limit_exceeded",
                RetryClass::Never,
            ));
        }
        record.state = OutboxState::Pending;
        Ok(*record)
    }

    pub fn dead_letter(
        &mut self,
        id: IntentId,
        owner: NodeId,
        generation: u64,
        reason: Digest32,
    ) -> Result<OutboxRecord, DomainError> {
        if reason.is_zero() {
            return Err(invalid("invalid_dead_letter_reason"));
        }
        let record = self.outbox.get_mut(&id).ok_or_else(outbox_not_found)?;
        lease_fence(record, owner, generation)?;
        record.state = OutboxState::DeadLetter { reason };
        Ok(*record)
    }
}

fn validate_intent(intent: &CommandIntent) -> Result<(), DomainError> {
    nonzero(intent.entity.is_zero(), "invalid_entity_id")?;
    nonzero(intent.command.is_zero(), "invalid_command_id")?;
    if intent.authority_generation == 0 || intent.fingerprint.is_zero() || intent.next_state.is_zero() {
        return Err(invalid("invalid_command_intent"));
    }
    if intent.events.len() > MAX_EVENTS || intent.outbox.len() > MAX_INTENTS {
        return Err(error(
            StableCode::ResourceExhausted,
            "commit_batch_limit_exceeded",
            RetryClass::Never,
        ));
    }
    let mut event_ids = BTreeSet::new();
    for item in &intent.events {
        if item.id.is_zero() || item.payload.is_zero() || !event_ids.insert(item.id) {
            return Err(invalid("invalid_or_duplicate_event"));
        }
    }
    let mut intent_ids = BTreeSet::new();
    for item in &intent.outbox {
        if item.id.is_zero() || item.payload.is_zero() || !intent_ids.insert(item.id) {
            return Err(invalid("invalid_or_duplicate_outbox_intent"));
        }
    }
    Ok(())
}

fn fence(head: &EntityHead, revision: u64, generation: u64) -> Result<(), DomainError> {
    if head.authority_generation != generation {
        return Err(generation_mismatch());
    }
    if head.revision != revision {
        return Err(error(
            StableCode::Aborted,
            "entity_revision_mismatch",
            RetryClass::ResyncRequired,
        ));
    }
    Ok(())
}

fn lease_fence(record: &OutboxRecord, owner: NodeId, generation: u64) -> Result<(), DomainError> {
    match record.state {
        OutboxState::Leased {
            owner: current_owner,
            generation: current_generation,
        } if current_owner == owner && current_generation == generation => Ok(()),
        _ => Err(error(
            StableCode::Aborted,
            "outbox_lease_mismatch",
            RetryClass::SafeBackoff,
        )),
    }
}

fn nonzero(is_zero: bool, reason: &'static str) -> Result<(), DomainError> {
    if is_zero {
        Err(invalid(reason))
    } else {
        Ok(())
    }
}

const fn invalid(reason: &'static str) -> DomainError {
    error(StableCode::InvalidArgument, reason, RetryClass::Never)
}

const fn not_found() -> DomainError {
    error(StableCode::NotFound, "entity_not_found", RetryClass::Never)
}

const fn outbox_not_found() -> DomainError {
    error(
        StableCode::NotFound,
        "outbox_intent_not_found",
        RetryClass::Never,
    )
}

const fn overflow() -> DomainError {
    error(StableCode::OutOfRange, "counter_overflow", RetryClass::Never)
}

const fn stale_prepare() -> DomainError {
    error(
        StableCode::Aborted,
        "prepared_commit_stale",
        RetryClass::ResyncRequired,
    )
}

const fn generation_mismatch() -> DomainError {
    error(
        StableCode::Aborted,
        "authority_generation_mismatch",
        RetryClass::ResyncRequired,
    )
}

const fn error(code: StableCode, reason: &'static str, retry: RetryClass) -> DomainError {
    DomainError::new(code, reason, retry)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn id(value: u8) -> [u8; 16] {
        [value; 16]
    }

    fn digest(value: u8) -> Digest32 {
        Digest32::new([value; 32])
    }

    fn intent(command: u8, revision: u64, generation: u64) -> CommandIntent {
        CommandIntent {
            entity: EntityId::new(id(1)),
            command: CommandId::new(id(command)),
            fingerprint: digest(command),
            expected_revision: revision,
            authority_generation: generation,
            next_state: digest(command + 10),
            events: vec![EventInput {
                id: EventId::new(id(command)),
                payload: digest(command + 20),
            }],
            outbox: vec![OutboxInput {
                id: IntentId::new(id(command)),
                kind: IntentKind::Broadcast,
                payload: digest(command + 30),
            }],
        }
    }

    fn prepared(state: &DurableState, value: CommandIntent) -> PreparedCommit {
        match state.prepare(value).unwrap() {
            PrepareOutcome::Prepared(value) => value,
            PrepareOutcome::Duplicate(_) => panic!("unexpected duplicate"),
        }
    }

    fn state() -> DurableState {
        let mut state = DurableState::default();
        state
            .bootstrap(EntityId::new(id(1)), 1, digest(99))
            .unwrap();
        state
    }

    #[test]
    fn commit_is_atomic_and_records_events_and_outbox() {
        let mut state = state();
        let receipt = state.commit(prepared(&state, intent(1, 0, 1))).unwrap();
        assert_eq!(receipt.revision, 1);
        assert_eq!(receipt.first_sequence, Some(1));
        assert_eq!(state.event_count(EntityId::new(id(1))), 1);
        assert_eq!(state.outbox(IntentId::new(id(1))).unwrap().state, OutboxState::Pending);
    }

    #[test]
    fn exact_duplicate_replays_receipt_but_changed_fingerprint_conflicts() {
        let mut state = state();
        let original = intent(1, 0, 1);
        let receipt = state.commit(prepared(&state, original.clone())).unwrap();
        assert_eq!(state.prepare(original).unwrap(), PrepareOutcome::Duplicate(receipt));
        let mut changed = intent(1, 1, 1);
        changed.fingerprint = digest(88);
        assert_eq!(state.prepare(changed).unwrap_err().reason(), "command_id_conflict");
    }

    #[test]
    fn concurrent_commit_fences_stale_prepared_result() {
        let mut state = state();
        let first = prepared(&state, intent(1, 0, 1));
        let second = prepared(&state, intent(2, 0, 1));
        state.commit(first).unwrap();
        assert_eq!(state.commit(second).unwrap_err().reason(), "entity_revision_mismatch");
    }

    #[test]
    fn takeover_fences_prior_generation() {
        let mut state = state();
        let old = prepared(&state, intent(1, 0, 1));
        state.takeover(EntityId::new(id(1)), 1).unwrap();
        assert_eq!(state.commit(old).unwrap_err().reason(), "authority_generation_mismatch");
    }

    #[test]
    fn outbox_lease_generation_fences_stale_worker() {
        let mut state = state();
        state.commit(prepared(&state, intent(1, 0, 1))).unwrap();
        let first = state.lease(IntentId::new(id(1)), NodeId::new(id(1))).unwrap();
        state.retry(first.id, NodeId::new(id(1)), first.lease_generation).unwrap();
        let second = state.lease(first.id, NodeId::new(id(2))).unwrap();
        assert_eq!(
            state.apply(first.id, NodeId::new(id(1)), first.lease_generation, digest(70)).unwrap_err().reason(),
            "outbox_lease_mismatch"
        );
        state.apply(first.id, NodeId::new(id(2)), second.lease_generation, digest(70)).unwrap();
    }

    #[test]
    fn applied_receipt_is_exactly_idempotent() {
        let mut state = state();
        state.commit(prepared(&state, intent(1, 0, 1))).unwrap();
        let lease = state.lease(IntentId::new(id(1)), NodeId::new(id(1))).unwrap();
        let applied = state.apply(lease.id, NodeId::new(id(1)), lease.lease_generation, digest(70)).unwrap();
        assert_eq!(state.apply(lease.id, NodeId::new(id(1)), lease.lease_generation, digest(70)).unwrap(), applied);
        assert_eq!(state.apply(lease.id, NodeId::new(id(1)), lease.lease_generation, digest(71)).unwrap_err().reason(), "outbox_receipt_mismatch");
    }

    #[test]
    fn duplicate_outbox_id_rejects_without_partial_mutation() {
        let mut state = state();
        state.commit(prepared(&state, intent(1, 0, 1))).unwrap();
        let before = state.clone();
        let mut second = intent(2, 1, 1);
        second.outbox[0].id = IntentId::new(id(1));
        assert_eq!(state.prepare(second).unwrap_err().reason(), "outbox_intent_already_exists");
        assert_eq!(state, before);
    }

    #[test]
    fn invalid_identity_and_terminal_dead_letter_fail_closed() {
        let mut invalid_state = DurableState::default();
        assert_eq!(invalid_state.bootstrap(EntityId::new([0; 16]), 1, digest(1)).unwrap_err().reason(), "invalid_entity_id");

        let mut state = state();
        state.commit(prepared(&state, intent(1, 0, 1))).unwrap();
        let lease = state.lease(IntentId::new(id(1)), NodeId::new(id(1))).unwrap();
        let dead = state.dead_letter(lease.id, NodeId::new(id(1)), lease.lease_generation, digest(90)).unwrap();
        assert!(matches!(dead.state, OutboxState::DeadLetter { .. }));
        assert_eq!(state.lease(lease.id, NodeId::new(id(2))).unwrap_err().reason(), "outbox_not_pending");
    }
}
