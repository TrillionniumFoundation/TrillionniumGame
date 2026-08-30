#![forbid(unsafe_code)]

mod auth;
mod pool;
mod session;

pub use auth::{
    parse_refresh_credential, AccessTokenVerifier, ParsedRefreshCredential, SessionPrincipal,
};
pub use pool::{PgPool, PgPoolConfig, PgPoolSnapshot, PgTlsConfig};
pub use session::{
    CreateSessionFamily, RefreshRotationOutcome, RefreshTokenCredential, RotateRefreshToken,
    SessionFamilyRecord,
};

use std::collections::BTreeSet;
use std::fmt;

use postgres::{Client, IsolationLevel, NoTls, Transaction};
use trnm_contracts::{CommandId, Digest32, DomainError, RetryClass, StableCode};

const MAX_EVENTS: usize = 64;
const MAX_OUTBOX: usize = 64;

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
            pub const fn as_bytes(&self) -> &[u8; 16] {
                &self.0
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
pub enum DatabaseProfile {
    PostgreSql,
    CockroachDb,
}

impl DatabaseProfile {
    #[must_use]
    pub const fn metadata_value(self) -> &'static str {
        match self {
            Self::PostgreSql => "postgresql",
            Self::CockroachDb => "cockroachdb",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum IntentKind {
    Broadcast = 0,
    SearchIndex = 1,
    Notification = 2,
    ExternalEffect = 3,
    Completion = 4,
}

impl IntentKind {
    const fn database_value(self) -> i16 {
        self as i16
    }
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
    pub available_at_ms: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CommitRequest {
    pub entity: EntityId,
    pub command: CommandId,
    pub fingerprint: Digest32,
    pub expected_revision: u64,
    pub authority_generation: u64,
    pub next_state: Digest32,
    pub committed_at_ms: u64,
    pub events: Vec<EventInput>,
    pub outbox: Vec<OutboxInput>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct EntityHead {
    pub entity: EntityId,
    pub revision: u64,
    pub last_event_sequence: u64,
    pub authority_generation: u64,
    pub state: Digest32,
    pub updated_at_ms: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CommitReceipt {
    pub entity: EntityId,
    pub command: CommandId,
    pub fingerprint: Digest32,
    pub revision: u64,
    pub state: Digest32,
    pub first_event_sequence: Option<u64>,
    pub last_event_sequence: u64,
    pub event_count: usize,
    pub outbox: Vec<IntentId>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CommitOutcome {
    Applied(CommitReceipt),
    Duplicate(CommitReceipt),
}

pub struct PgRepository {
    profile: DatabaseProfile,
    client: pool::ClientHandle,
}

impl fmt::Debug for PgRepository {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("PgRepository")
            .field("profile", &self.profile)
            .finish_non_exhaustive()
    }
}

impl PgRepository {
    pub fn connect(database_url: &str, profile: DatabaseProfile) -> Result<Self, DomainError> {
        if database_url.is_empty() {
            return Err(invalid("database_url_empty"));
        }
        let client = Client::connect(database_url, NoTls).map_err(map_postgres_error)?;
        Ok(Self {
            profile,
            client: pool::ClientHandle::direct(client),
        })
    }

    #[must_use]
    pub const fn profile(&self) -> DatabaseProfile {
        self.profile
    }

    pub fn table_exists(&mut self, table: &str) -> Result<bool, DomainError> {
        if table.is_empty() || table.len() > 63 {
            return Err(invalid("invalid_table_name"));
        }
        let row = self
            .client
            .query_one(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables \
                 WHERE table_schema = 'public' AND table_name = $1)",
                &[&table],
            )
            .map_err(map_postgres_error)?;
        Ok(row.get(0))
    }

    pub fn execute_migration_batch(&mut self, migration: &str) -> Result<(), DomainError> {
        if migration.trim().is_empty() {
            return Err(invalid("migration_batch_empty"));
        }
        self.client
            .batch_execute(migration)
            .map_err(map_postgres_error)
    }

    pub fn bind_schema_metadata(
        &mut self,
        source_commit: &str,
        applied_at_ms: u64,
    ) -> Result<(), DomainError> {
        if source_commit.len() != 40 || !source_commit.bytes().all(|byte| byte.is_ascii_hexdigit())
        {
            return Err(invalid("invalid_schema_source_commit"));
        }
        let applied_at_ms = to_i64(applied_at_ms)?;
        self.client
            .execute(
                "INSERT INTO trnm_schema_metadata \
                 (singleton, schema_version, profile, source_commit, applied_at_ms) \
                 VALUES (1, 1, $1, $2, $3) ON CONFLICT (singleton) DO NOTHING",
                &[
                    &self.profile.metadata_value(),
                    &source_commit,
                    &applied_at_ms,
                ],
            )
            .map_err(map_postgres_error)?;
        let row = self
            .client
            .query_opt(
                "SELECT schema_version, profile, source_commit \
                 FROM trnm_schema_metadata WHERE singleton = 1",
                &[],
            )
            .map_err(map_postgres_error)?
            .ok_or_else(|| failed_precondition("schema_metadata_missing"))?;
        let version: i64 = row.get(0);
        let profile: String = row.get(1);
        let recorded_commit: String = row.get(2);
        if version != 1
            || profile != self.profile.metadata_value()
            || recorded_commit != source_commit
        {
            return Err(failed_precondition("schema_metadata_mismatch"));
        }
        Ok(())
    }

    pub fn bootstrap_entity(
        &mut self,
        entity: EntityId,
        authority_generation: u64,
        state: Digest32,
        updated_at_ms: u64,
    ) -> Result<EntityHead, DomainError> {
        if entity.is_zero() || authority_generation == 0 || state.is_zero() {
            return Err(invalid("invalid_entity_bootstrap"));
        }
        let authority_generation = to_i64(authority_generation)?;
        let updated_at_ms = to_i64(updated_at_ms)?;
        let inserted = self
            .client
            .execute(
                "INSERT INTO trnm_entity_heads \
                 (entity_id, revision, last_event_sequence, authority_generation, \
                  state_digest, updated_at_ms) \
                 VALUES ($1, 0, 0, $2, $3, $4) ON CONFLICT (entity_id) DO NOTHING",
                &[
                    &entity.as_bytes().as_slice(),
                    &authority_generation,
                    &state.as_bytes().as_slice(),
                    &updated_at_ms,
                ],
            )
            .map_err(map_postgres_error)?;
        if inserted != 1 {
            return Err(error(
                StableCode::AlreadyExists,
                "entity_already_exists",
                RetryClass::Never,
            ));
        }
        self.load_head(entity)?.ok_or_else(|| {
            error(
                StableCode::DataLoss,
                "entity_bootstrap_lost",
                RetryClass::Never,
            )
        })
    }

    pub fn load_head(&mut self, entity: EntityId) -> Result<Option<EntityHead>, DomainError> {
        if entity.is_zero() {
            return Err(invalid("invalid_entity_id"));
        }
        let row = self
            .client
            .query_opt(
                "SELECT revision, last_event_sequence, authority_generation, \
                 state_digest, updated_at_ms FROM trnm_entity_heads WHERE entity_id = $1",
                &[&entity.as_bytes().as_slice()],
            )
            .map_err(map_postgres_error)?;
        row.map(|row| decode_head(entity, &row)).transpose()
    }

    pub fn commit_command(
        &mut self,
        request: &CommitRequest,
    ) -> Result<CommitOutcome, DomainError> {
        validate_request(request)?;
        let mut transaction = self
            .client
            .build_transaction()
            .isolation_level(IsolationLevel::Serializable)
            .start()
            .map_err(map_postgres_error)?;

        if let Some(receipt) = load_receipt(&mut transaction, request.entity, request.command)? {
            if receipt.fingerprint == request.fingerprint {
                transaction.commit().map_err(map_postgres_error)?;
                return Ok(CommitOutcome::Duplicate(receipt));
            }
            return Err(error(
                StableCode::AlreadyExists,
                "command_id_conflict",
                RetryClass::Never,
            ));
        }

        let head_row = transaction
            .query_opt(
                "SELECT revision, last_event_sequence, authority_generation \
                 FROM trnm_entity_heads WHERE entity_id = $1 FOR UPDATE",
                &[&request.entity.as_bytes().as_slice()],
            )
            .map_err(map_postgres_error)?
            .ok_or_else(|| error(StableCode::NotFound, "entity_not_found", RetryClass::Never))?;
        let revision = from_i64(head_row.get(0), "negative_entity_revision")?;
        let last_event_sequence = from_i64(head_row.get(1), "negative_event_sequence")?;
        let authority_generation = from_i64(head_row.get(2), "negative_authority_generation")?;
        if authority_generation != request.authority_generation {
            return Err(error(
                StableCode::Aborted,
                "authority_generation_mismatch",
                RetryClass::ResyncRequired,
            ));
        }
        if revision != request.expected_revision {
            return Err(error(
                StableCode::Aborted,
                "entity_revision_mismatch",
                RetryClass::ResyncRequired,
            ));
        }

        let next_revision = revision.checked_add(1).ok_or_else(counter_overflow)?;
        let event_count = u64::try_from(request.events.len()).map_err(|_| counter_overflow())?;
        let first_event_sequence = if event_count == 0 {
            None
        } else {
            Some(
                last_event_sequence
                    .checked_add(1)
                    .ok_or_else(counter_overflow)?,
            )
        };
        let next_last_event_sequence = last_event_sequence
            .checked_add(event_count)
            .ok_or_else(counter_overflow)?;

        let next_revision_i64 = to_i64(next_revision)?;
        let expected_revision_i64 = to_i64(request.expected_revision)?;
        let authority_generation_i64 = to_i64(request.authority_generation)?;
        let next_last_event_sequence_i64 = to_i64(next_last_event_sequence)?;
        let committed_at_ms_i64 = to_i64(request.committed_at_ms)?;
        let updated = transaction
            .execute(
                "UPDATE trnm_entity_heads SET revision = $2, last_event_sequence = $3, \
                 state_digest = $4, updated_at_ms = $5 \
                 WHERE entity_id = $1 AND revision = $6 AND authority_generation = $7",
                &[
                    &request.entity.as_bytes().as_slice(),
                    &next_revision_i64,
                    &next_last_event_sequence_i64,
                    &request.next_state.as_bytes().as_slice(),
                    &committed_at_ms_i64,
                    &expected_revision_i64,
                    &authority_generation_i64,
                ],
            )
            .map_err(map_postgres_error)?;
        if updated != 1 {
            return Err(error(
                StableCode::Aborted,
                "entity_compare_and_swap_failed",
                RetryClass::ResyncRequired,
            ));
        }

        let first_event_sequence_i64 = first_event_sequence.map(to_i64).transpose()?;
        let event_count_i32 =
            i32::try_from(request.events.len()).map_err(|_| counter_overflow())?;
        transaction
            .execute(
                "INSERT INTO trnm_command_receipts \
                 (entity_id, command_id, fingerprint, revision, state_digest, \
                  first_event_sequence, last_event_sequence, event_count, committed_at_ms) \
                 VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                &[
                    &request.entity.as_bytes().as_slice(),
                    &request.command.as_bytes().as_slice(),
                    &request.fingerprint.as_bytes().as_slice(),
                    &next_revision_i64,
                    &request.next_state.as_bytes().as_slice(),
                    &first_event_sequence_i64,
                    &next_last_event_sequence_i64,
                    &event_count_i32,
                    &committed_at_ms_i64,
                ],
            )
            .map_err(map_postgres_error)?;

        let mut sequence = last_event_sequence;
        for event in &request.events {
            sequence = sequence.checked_add(1).ok_or_else(counter_overflow)?;
            let sequence_i64 = to_i64(sequence)?;
            transaction
                .execute(
                    "INSERT INTO trnm_events \
                     (entity_id, sequence, event_id, command_id, payload_digest, created_at_ms) \
                     VALUES ($1, $2, $3, $4, $5, $6)",
                    &[
                        &request.entity.as_bytes().as_slice(),
                        &sequence_i64,
                        &event.id.as_bytes().as_slice(),
                        &request.command.as_bytes().as_slice(),
                        &event.payload.as_bytes().as_slice(),
                        &committed_at_ms_i64,
                    ],
                )
                .map_err(map_postgres_error)?;
        }

        for (position, intent) in request.outbox.iter().enumerate() {
            let available_at_ms = to_i64(intent.available_at_ms)?;
            transaction
                .execute(
                    "INSERT INTO trnm_outbox \
                     (intent_id, entity_id, command_id, kind, payload_digest, \
                      attempt, lease_generation, state, owner_node, receipt_digest, \
                      dead_reason_digest, available_at_ms, updated_at_ms) \
                     VALUES ($1, $2, $3, $4, $5, 0, 0, 0, NULL, NULL, NULL, $6, $7)",
                    &[
                        &intent.id.as_bytes().as_slice(),
                        &request.entity.as_bytes().as_slice(),
                        &request.command.as_bytes().as_slice(),
                        &intent.kind.database_value(),
                        &intent.payload.as_bytes().as_slice(),
                        &available_at_ms,
                        &committed_at_ms_i64,
                    ],
                )
                .map_err(map_postgres_error)?;
            let position = i32::try_from(position).map_err(|_| counter_overflow())?;
            transaction
                .execute(
                    "INSERT INTO trnm_command_outbox \
                     (entity_id, command_id, position, intent_id) VALUES ($1, $2, $3, $4)",
                    &[
                        &request.entity.as_bytes().as_slice(),
                        &request.command.as_bytes().as_slice(),
                        &position,
                        &intent.id.as_bytes().as_slice(),
                    ],
                )
                .map_err(map_postgres_error)?;
        }

        transaction.commit().map_err(map_postgres_error)?;
        Ok(CommitOutcome::Applied(CommitReceipt {
            entity: request.entity,
            command: request.command,
            fingerprint: request.fingerprint,
            revision: next_revision,
            state: request.next_state,
            first_event_sequence,
            last_event_sequence: next_last_event_sequence,
            event_count: request.events.len(),
            outbox: request.outbox.iter().map(|intent| intent.id).collect(),
        }))
    }
}

fn load_receipt(
    transaction: &mut Transaction<'_>,
    entity: EntityId,
    command: CommandId,
) -> Result<Option<CommitReceipt>, DomainError> {
    let row = transaction
        .query_opt(
            "SELECT fingerprint, revision, state_digest, first_event_sequence, \
             last_event_sequence, event_count FROM trnm_command_receipts \
             WHERE entity_id = $1 AND command_id = $2",
            &[
                &entity.as_bytes().as_slice(),
                &command.as_bytes().as_slice(),
            ],
        )
        .map_err(map_postgres_error)?;
    let Some(row) = row else {
        return Ok(None);
    };
    let outbox_rows = transaction
        .query(
            "SELECT intent_id FROM trnm_command_outbox \
             WHERE entity_id = $1 AND command_id = $2 ORDER BY position",
            &[
                &entity.as_bytes().as_slice(),
                &command.as_bytes().as_slice(),
            ],
        )
        .map_err(map_postgres_error)?;
    let outbox = outbox_rows
        .iter()
        .map(|row| decode_id16::<IntentId>(row.get(0), IntentId::new, "invalid_intent_id_bytes"))
        .collect::<Result<Vec<_>, _>>()?;
    let event_count_i32: i32 = row.get(5);
    let event_count =
        usize::try_from(event_count_i32).map_err(|_| data_loss("invalid_receipt_event_count"))?;
    Ok(Some(CommitReceipt {
        entity,
        command,
        fingerprint: decode_digest(row.get(0), "invalid_fingerprint_bytes")?,
        revision: from_i64(row.get(1), "negative_receipt_revision")?,
        state: decode_digest(row.get(2), "invalid_state_digest_bytes")?,
        first_event_sequence: row
            .get::<_, Option<i64>>(3)
            .map(|value| from_i64(value, "negative_first_event_sequence"))
            .transpose()?,
        last_event_sequence: from_i64(row.get(4), "negative_last_event_sequence")?,
        event_count,
        outbox,
    }))
}

fn validate_request(request: &CommitRequest) -> Result<(), DomainError> {
    if request.entity.is_zero()
        || request.command.is_zero()
        || request.fingerprint.is_zero()
        || request.next_state.is_zero()
        || request.authority_generation == 0
    {
        return Err(invalid("invalid_commit_request"));
    }
    if request.events.len() > MAX_EVENTS || request.outbox.len() > MAX_OUTBOX {
        return Err(error(
            StableCode::ResourceExhausted,
            "commit_batch_limit_exceeded",
            RetryClass::Never,
        ));
    }
    to_i64(request.expected_revision)?;
    to_i64(request.authority_generation)?;
    to_i64(request.committed_at_ms)?;
    let mut event_ids = BTreeSet::new();
    for event in &request.events {
        if event.id.is_zero() || event.payload.is_zero() || !event_ids.insert(event.id) {
            return Err(invalid("invalid_or_duplicate_event"));
        }
    }
    let mut intent_ids = BTreeSet::new();
    for intent in &request.outbox {
        if intent.id.is_zero()
            || intent.payload.is_zero()
            || !intent_ids.insert(intent.id)
            || to_i64(intent.available_at_ms).is_err()
        {
            return Err(invalid("invalid_or_duplicate_outbox_intent"));
        }
    }
    Ok(())
}

fn decode_head(entity: EntityId, row: &postgres::Row) -> Result<EntityHead, DomainError> {
    Ok(EntityHead {
        entity,
        revision: from_i64(row.get(0), "negative_entity_revision")?,
        last_event_sequence: from_i64(row.get(1), "negative_event_sequence")?,
        authority_generation: from_i64(row.get(2), "negative_authority_generation")?,
        state: decode_digest(row.get(3), "invalid_state_digest_bytes")?,
        updated_at_ms: from_i64(row.get(4), "negative_updated_at_ms")?,
    })
}

fn decode_digest(bytes: Vec<u8>, reason: &'static str) -> Result<Digest32, DomainError> {
    let value: [u8; 32] = bytes.try_into().map_err(|_| data_loss(reason))?;
    Ok(Digest32::new(value))
}

fn decode_id16<T>(
    bytes: Vec<u8>,
    constructor: impl FnOnce([u8; 16]) -> T,
    reason: &'static str,
) -> Result<T, DomainError> {
    let value: [u8; 16] = bytes.try_into().map_err(|_| data_loss(reason))?;
    Ok(constructor(value))
}

fn to_i64(value: u64) -> Result<i64, DomainError> {
    i64::try_from(value).map_err(|_| counter_overflow())
}

fn from_i64(value: i64, reason: &'static str) -> Result<u64, DomainError> {
    u64::try_from(value).map_err(|_| data_loss(reason))
}

#[must_use]
pub fn classify_sqlstate(code: &str) -> DomainError {
    match code {
        "40001" => error(
            StableCode::Aborted,
            "database_serialization_failure",
            RetryClass::SafeImmediate,
        ),
        "40P01" => error(
            StableCode::Aborted,
            "database_deadlock",
            RetryClass::SafeBackoff,
        ),
        "23505" => error(
            StableCode::AlreadyExists,
            "database_unique_violation",
            RetryClass::Never,
        ),
        "23503" => error(
            StableCode::FailedPrecondition,
            "database_foreign_key_violation",
            RetryClass::Never,
        ),
        "23502" | "23514" | "22P02" => error(
            StableCode::InvalidArgument,
            "database_constraint_violation",
            RetryClass::Never,
        ),
        "42P01" => failed_precondition("database_schema_missing"),
        value if value.starts_with("08") => error(
            StableCode::Unavailable,
            "database_connection_failure",
            RetryClass::SafeBackoff,
        ),
        _ => error(
            StableCode::Internal,
            "database_internal_error",
            RetryClass::Never,
        ),
    }
}

fn map_postgres_error(source: postgres::Error) -> DomainError {
    source
        .code()
        .map(|code| classify_sqlstate(code.code()))
        .unwrap_or_else(|| {
            error(
                StableCode::Unavailable,
                "database_transport_failure",
                RetryClass::SafeBackoff,
            )
        })
}

const fn invalid(reason: &'static str) -> DomainError {
    error(StableCode::InvalidArgument, reason, RetryClass::Never)
}

const fn failed_precondition(reason: &'static str) -> DomainError {
    error(StableCode::FailedPrecondition, reason, RetryClass::Never)
}

const fn data_loss(reason: &'static str) -> DomainError {
    error(StableCode::DataLoss, reason, RetryClass::Never)
}

const fn counter_overflow() -> DomainError {
    error(
        StableCode::OutOfRange,
        "counter_overflow",
        RetryClass::Never,
    )
}

const fn error(code: StableCode, reason: &'static str, retry: RetryClass) -> DomainError {
    DomainError::new(code, reason, retry)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn digest(value: u8) -> Digest32 {
        Digest32::new([value; 32])
    }

    fn request() -> CommitRequest {
        CommitRequest {
            entity: EntityId::new([1; 16]),
            command: CommandId::new([2; 16]),
            fingerprint: digest(3),
            expected_revision: 0,
            authority_generation: 1,
            next_state: digest(4),
            committed_at_ms: 10,
            events: vec![EventInput {
                id: EventId::new([5; 16]),
                payload: digest(6),
            }],
            outbox: vec![OutboxInput {
                id: IntentId::new([7; 16]),
                kind: IntentKind::Broadcast,
                payload: digest(8),
                available_at_ms: 10,
            }],
        }
    }

    #[test]
    fn request_validation_rejects_duplicate_ids_and_overflow() {
        let mut duplicate = request();
        duplicate.events.push(duplicate.events[0]);
        assert_eq!(
            validate_request(&duplicate).unwrap_err().reason(),
            "invalid_or_duplicate_event"
        );
        let mut overflow = request();
        overflow.committed_at_ms = u64::MAX;
        assert_eq!(
            validate_request(&overflow).unwrap_err().reason(),
            "counter_overflow"
        );
    }

    #[test]
    fn sqlstate_mapping_is_stable_and_retry_aware() {
        assert_eq!(classify_sqlstate("40001").code(), StableCode::Aborted);
        assert_eq!(
            classify_sqlstate("40001").retry(),
            RetryClass::SafeImmediate
        );
        assert_eq!(classify_sqlstate("23505").code(), StableCode::AlreadyExists);
        assert_eq!(classify_sqlstate("08006").code(), StableCode::Unavailable);
        assert_eq!(
            classify_sqlstate("XX000").reason(),
            "database_internal_error"
        );
    }

    #[test]
    fn profiles_have_distinct_schema_metadata_values() {
        assert_eq!(DatabaseProfile::PostgreSql.metadata_value(), "postgresql");
        assert_eq!(DatabaseProfile::CockroachDb.metadata_value(), "cockroachdb");
    }
}
