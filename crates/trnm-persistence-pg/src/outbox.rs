use postgres::Transaction;
use trnm_contracts::{CommandId, Digest32, DomainError, RetryClass, StableCode};

use super::{
    counter_overflow, data_loss, decode_digest, decode_id16, error, from_i64, invalid,
    map_postgres_error, to_i64, EntityId, IntentId, IntentKind, NodeId, PgRepository,
};

const MAX_CLAIM_BATCH: usize = 64;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct OutboxLease {
    pub id: IntentId,
    pub entity: EntityId,
    pub command: CommandId,
    pub kind: IntentKind,
    pub payload: Digest32,
    pub attempt: u32,
    pub lease_generation: u64,
    pub owner: NodeId,
    pub lease_expires_at_ms: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum OutboxRetryOutcome {
    Pending {
        next_available_at_ms: u64,
        attempt: u32,
    },
    DeadLetter {
        attempt: u32,
        reason: Digest32,
    },
}

impl PgRepository {
    pub fn claim_outbox(
        &mut self,
        owner: NodeId,
        now_ms: u64,
        lease_duration_ms: u64,
        max_attempts: u32,
        limit: usize,
    ) -> Result<Vec<OutboxLease>, DomainError> {
        validate_claim(owner, lease_duration_ms, max_attempts, limit)?;
        let lease_expires_at_ms = now_ms
            .checked_add(lease_duration_ms)
            .ok_or_else(counter_overflow)?;
        let now_ms_i64 = to_i64(now_ms)?;
        let lease_expires_at_ms_i64 = to_i64(lease_expires_at_ms)?;
        let max_attempts_i32 = i32::try_from(max_attempts).map_err(|_| counter_overflow())?;
        let mut transaction = self
            .client
            .build_transaction()
            .isolation_level(postgres::IsolationLevel::Serializable)
            .start()
            .map_err(map_postgres_error)?;
        let mut leases = Vec::with_capacity(limit);
        for _ in 0..limit {
            let Some(row) = transaction
                .query_opt(
                    "SELECT intent_id, entity_id, command_id, kind, payload_digest, \
                     attempt, lease_generation, state \
                     FROM trnm_outbox \
                     WHERE state IN (0, 1) AND available_at_ms <= $1 AND attempt < $2 \
                     ORDER BY available_at_ms, intent_id LIMIT 1 FOR UPDATE",
                    &[&now_ms_i64, &max_attempts_i32],
                )
                .map_err(map_postgres_error)?
            else {
                break;
            };
            let intent_id = decode_id16::<IntentId>(
                row.get(0),
                IntentId::new,
                "invalid_outbox_intent_id_bytes",
            )?;
            let entity = decode_id16::<EntityId>(
                row.get(1),
                EntityId::new,
                "invalid_outbox_entity_id_bytes",
            )?;
            let command = decode_command(row.get(2))?;
            let kind_value: i16 = row.get(3);
            let kind = IntentKind::from_database_value(kind_value)
                .ok_or_else(|| data_loss("invalid_outbox_kind"))?;
            let payload = decode_digest(row.get(4), "invalid_outbox_payload_digest_bytes")?;
            let prior_attempt_i32: i32 = row.get(5);
            let prior_attempt = u32::try_from(prior_attempt_i32)
                .map_err(|_| data_loss("invalid_outbox_attempt"))?;
            let prior_generation = from_i64(row.get(6), "negative_outbox_lease_generation")?;
            let prior_state: i16 = row.get(7);
            let attempt = prior_attempt.checked_add(1).ok_or_else(counter_overflow)?;
            let generation = prior_generation.checked_add(1).ok_or_else(counter_overflow)?;
            let attempt_i32 = i32::try_from(attempt).map_err(|_| counter_overflow())?;
            let generation_i64 = to_i64(generation)?;
            let prior_generation_i64 = to_i64(prior_generation)?;
            let updated = transaction
                .execute(
                    "UPDATE trnm_outbox SET state = 1, owner_node = $2, attempt = $3, \
                     lease_generation = $4, available_at_ms = $5, updated_at_ms = $1 \
                     WHERE intent_id = $6 AND state = $7 AND lease_generation = $8",
                    &[
                        &now_ms_i64,
                        &owner.as_bytes().as_slice(),
                        &attempt_i32,
                        &generation_i64,
                        &lease_expires_at_ms_i64,
                        &intent_id.as_bytes().as_slice(),
                        &prior_state,
                        &prior_generation_i64,
                    ],
                )
                .map_err(map_postgres_error)?;
            if updated != 1 {
                return Err(error(
                    StableCode::Aborted,
                    "outbox_claim_compare_and_swap_failed",
                    RetryClass::SafeImmediate,
                ));
            }
            leases.push(OutboxLease {
                id: intent_id,
                entity,
                command,
                kind,
                payload,
                attempt,
                lease_generation: generation,
                owner,
                lease_expires_at_ms,
            });
        }
        transaction.commit().map_err(map_postgres_error)?;
        Ok(leases)
    }

    pub fn complete_outbox(
        &mut self,
        lease: &OutboxLease,
        receipt: Digest32,
        completed_at_ms: u64,
    ) -> Result<(), DomainError> {
        validate_lease(lease)?;
        if receipt.is_zero() {
            return Err(invalid("invalid_outbox_receipt"));
        }
        let generation = to_i64(lease.lease_generation)?;
        let completed_at_ms = to_i64(completed_at_ms)?;
        let updated = self
            .client
            .execute(
                "UPDATE trnm_outbox SET state = 2, owner_node = NULL, \
                 receipt_digest = $4, dead_reason_digest = NULL, updated_at_ms = $5 \
                 WHERE intent_id = $1 AND state = 1 AND owner_node = $2 \
                 AND lease_generation = $3",
                &[
                    &lease.id.as_bytes().as_slice(),
                    &lease.owner.as_bytes().as_slice(),
                    &generation,
                    &receipt.as_bytes().as_slice(),
                    &completed_at_ms,
                ],
            )
            .map_err(map_postgres_error)?;
        require_one_fenced_update(updated, "outbox_complete_stale_lease")
    }

    pub fn retry_or_dead_letter_outbox(
        &mut self,
        lease: &OutboxLease,
        now_ms: u64,
        next_available_at_ms: u64,
        max_attempts: u32,
        dead_reason: Digest32,
    ) -> Result<OutboxRetryOutcome, DomainError> {
        validate_lease(lease)?;
        if max_attempts == 0
            || lease.attempt == 0
            || next_available_at_ms < now_ms
            || dead_reason.is_zero()
        {
            return Err(invalid("invalid_outbox_retry_transition"));
        }
        let generation = to_i64(lease.lease_generation)?;
        let now_ms_i64 = to_i64(now_ms)?;
        let next_available_at_ms_i64 = to_i64(next_available_at_ms)?;
        let max_attempts_i32 = i32::try_from(max_attempts).map_err(|_| counter_overflow())?;
        let mut transaction = self
            .client
            .build_transaction()
            .isolation_level(postgres::IsolationLevel::Serializable)
            .start()
            .map_err(map_postgres_error)?;
        let attempt = load_fenced_attempt(&mut transaction, lease, generation)?;
        let outcome = if attempt >= max_attempts_i32 {
            let updated = transaction
                .execute(
                    "UPDATE trnm_outbox SET state = 3, owner_node = NULL, \
                     receipt_digest = NULL, dead_reason_digest = $4, updated_at_ms = $5 \
                     WHERE intent_id = $1 AND state = 1 AND owner_node = $2 \
                     AND lease_generation = $3",
                    &[
                        &lease.id.as_bytes().as_slice(),
                        &lease.owner.as_bytes().as_slice(),
                        &generation,
                        &dead_reason.as_bytes().as_slice(),
                        &now_ms_i64,
                    ],
                )
                .map_err(map_postgres_error)?;
            require_one_fenced_update(updated, "outbox_dead_letter_stale_lease")?;
            OutboxRetryOutcome::DeadLetter {
                attempt: u32::try_from(attempt)
                    .map_err(|_| data_loss("invalid_outbox_attempt"))?,
                reason: dead_reason,
            }
        } else {
            let updated = transaction
                .execute(
                    "UPDATE trnm_outbox SET state = 0, owner_node = NULL, \
                     receipt_digest = NULL, dead_reason_digest = NULL, \
                     available_at_ms = $4, updated_at_ms = $5 \
                     WHERE intent_id = $1 AND state = 1 AND owner_node = $2 \
                     AND lease_generation = $3",
                    &[
                        &lease.id.as_bytes().as_slice(),
                        &lease.owner.as_bytes().as_slice(),
                        &generation,
                        &next_available_at_ms_i64,
                        &now_ms_i64,
                    ],
                )
                .map_err(map_postgres_error)?;
            require_one_fenced_update(updated, "outbox_retry_stale_lease")?;
            OutboxRetryOutcome::Pending {
                next_available_at_ms,
                attempt: u32::try_from(attempt)
                    .map_err(|_| data_loss("invalid_outbox_attempt"))?,
            }
        };
        transaction.commit().map_err(map_postgres_error)?;
        Ok(outcome)
    }
}

fn validate_claim(
    owner: NodeId,
    lease_duration_ms: u64,
    max_attempts: u32,
    limit: usize,
) -> Result<(), DomainError> {
    if owner.is_zero()
        || lease_duration_ms == 0
        || max_attempts == 0
        || limit == 0
        || limit > MAX_CLAIM_BATCH
    {
        return Err(invalid("invalid_outbox_claim"));
    }
    Ok(())
}

fn validate_lease(lease: &OutboxLease) -> Result<(), DomainError> {
    if lease.id.is_zero()
        || lease.entity.is_zero()
        || lease.command.is_zero()
        || lease.payload.is_zero()
        || lease.owner.is_zero()
        || lease.attempt == 0
        || lease.lease_generation == 0
    {
        return Err(invalid("invalid_outbox_lease"));
    }
    Ok(())
}

fn load_fenced_attempt(
    transaction: &mut Transaction<'_>,
    lease: &OutboxLease,
    generation: i64,
) -> Result<i32, DomainError> {
    let row = transaction
        .query_opt(
            "SELECT attempt FROM trnm_outbox WHERE intent_id = $1 AND state = 1 \
             AND owner_node = $2 AND lease_generation = $3 FOR UPDATE",
            &[
                &lease.id.as_bytes().as_slice(),
                &lease.owner.as_bytes().as_slice(),
                &generation,
            ],
        )
        .map_err(map_postgres_error)?
        .ok_or_else(|| {
            error(
                StableCode::Aborted,
                "outbox_retry_stale_lease",
                RetryClass::SafeImmediate,
            )
        })?;
    let attempt: i32 = row.get(0);
    if attempt <= 0 || u32::try_from(attempt).ok() != Some(lease.attempt) {
        return Err(error(
            StableCode::Aborted,
            "outbox_retry_attempt_mismatch",
            RetryClass::SafeImmediate,
        ));
    }
    Ok(attempt)
}

fn require_one_fenced_update(updated: u64, reason: &'static str) -> Result<(), DomainError> {
    if updated == 1 {
        Ok(())
    } else {
        Err(error(
            StableCode::Aborted,
            reason,
            RetryClass::SafeImmediate,
        ))
    }
}

fn decode_command(value: Vec<u8>) -> Result<CommandId, DomainError> {
    if value.len() != 16 {
        return Err(data_loss("invalid_outbox_command_id_bytes"));
    }
    let mut bytes = [0_u8; 16];
    bytes.copy_from_slice(&value);
    let command = CommandId::new(bytes);
    if command.is_zero() {
        return Err(data_loss("invalid_outbox_command_id_bytes"));
    }
    Ok(command)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lease() -> OutboxLease {
        OutboxLease {
            id: IntentId::new([1; 16]),
            entity: EntityId::new([2; 16]),
            command: CommandId::new([3; 16]),
            kind: IntentKind::Broadcast,
            payload: Digest32::new([4; 32]),
            attempt: 1,
            lease_generation: 1,
            owner: NodeId::new([5; 16]),
            lease_expires_at_ms: 100,
        }
    }

    #[test]
    fn claim_limits_fail_closed() {
        assert_eq!(
            validate_claim(NodeId::new([0; 16]), 1, 1, 1)
                .unwrap_err()
                .reason(),
            "invalid_outbox_claim"
        );
        assert_eq!(
            validate_claim(NodeId::new([1; 16]), 1, 1, MAX_CLAIM_BATCH + 1)
                .unwrap_err()
                .reason(),
            "invalid_outbox_claim"
        );
    }

    #[test]
    fn lease_validation_fences_zero_generation_and_owner() {
        let mut value = lease();
        value.lease_generation = 0;
        assert_eq!(
            validate_lease(&value).unwrap_err().reason(),
            "invalid_outbox_lease"
        );
        value = lease();
        value.owner = NodeId::new([0; 16]);
        assert_eq!(
            validate_lease(&value).unwrap_err().reason(),
            "invalid_outbox_lease"
        );
    }

    #[test]
    fn intent_kind_database_decoder_is_total_for_declared_values() {
        for (value, expected) in [
            (0, IntentKind::Broadcast),
            (1, IntentKind::SearchIndex),
            (2, IntentKind::Notification),
            (3, IntentKind::ExternalEffect),
            (4, IntentKind::Completion),
        ] {
            assert_eq!(IntentKind::from_database_value(value), Some(expected));
        }
        assert_eq!(IntentKind::from_database_value(-1), None);
        assert_eq!(IntentKind::from_database_value(5), None);
    }
}
