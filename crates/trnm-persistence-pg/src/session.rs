use postgres::{IsolationLevel, Row, Transaction};
use trnm_contracts::{
    Digest32, DomainError, RefreshTokenId, RetryClass, SessionFamilyId, StableCode, UserId,
};
use trnm_session_core::RevocationReason;

use super::{data_loss, error, from_i64, invalid, map_postgres_error, to_i64, PgRepository};

const TOKEN_STATE_ACTIVE: i16 = 0;
const TOKEN_STATE_CONSUMED: i16 = 1;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct RefreshTokenCredential {
    pub id: RefreshTokenId,
    pub digest: Digest32,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct CreateSessionFamily {
    pub family: SessionFamilyId,
    pub user: UserId,
    pub refresh: RefreshTokenCredential,
    pub issued_at_ms: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct RotateRefreshToken {
    pub presented: RefreshTokenCredential,
    pub replacement: RefreshTokenCredential,
    pub rotated_at_ms: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SessionFamilyRecord {
    pub family: SessionFamilyId,
    pub user: UserId,
    pub generation: u64,
    pub active_token: Option<RefreshTokenId>,
    pub revoked_reason: Option<RevocationReason>,
    pub created_at_ms: u64,
    pub updated_at_ms: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RefreshRotationOutcome {
    Rotated(SessionFamilyRecord),
    ReplayRevoked(SessionFamilyRecord),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct RefreshTokenSnapshot {
    generation: u64,
    state: i16,
    issued_at_ms: u64,
    consumed_at_ms: Option<u64>,
}

impl PgRepository {
    pub fn create_session_family(
        &mut self,
        request: &CreateSessionFamily,
    ) -> Result<SessionFamilyRecord, DomainError> {
        validate_create(request)?;
        let issued_at_ms = to_i64(request.issued_at_ms)?;
        let mut transaction = self
            .client
            .build_transaction()
            .isolation_level(IsolationLevel::Serializable)
            .start()
            .map_err(map_postgres_error)?;
        transaction
            .execute(
                "INSERT INTO trnm_session_families \
                 (family_id, user_id, generation, active_token_id, revoked_reason, \
                  created_at_ms, updated_at_ms) \
                 VALUES ($1, $2, 0, $3, NULL, $4, $4)",
                &[
                    &request.family.as_bytes().as_slice(),
                    &request.user.as_bytes().as_slice(),
                    &request.refresh.id.as_bytes().as_slice(),
                    &issued_at_ms,
                ],
            )
            .map_err(map_postgres_error)?;
        transaction
            .execute(
                "INSERT INTO trnm_refresh_tokens \
                 (family_id, token_id, token_digest, generation, state, issued_at_ms, \
                  consumed_at_ms) VALUES ($1, $2, $3, 0, 0, $4, NULL)",
                &[
                    &request.family.as_bytes().as_slice(),
                    &request.refresh.id.as_bytes().as_slice(),
                    &request.refresh.digest.as_bytes().as_slice(),
                    &issued_at_ms,
                ],
            )
            .map_err(map_postgres_error)?;
        transaction.commit().map_err(map_postgres_error)?;
        Ok(SessionFamilyRecord {
            family: request.family,
            user: request.user,
            generation: 0,
            active_token: Some(request.refresh.id),
            revoked_reason: None,
            created_at_ms: request.issued_at_ms,
            updated_at_ms: request.issued_at_ms,
        })
    }

    pub fn load_session_family(
        &mut self,
        family: SessionFamilyId,
    ) -> Result<Option<SessionFamilyRecord>, DomainError> {
        if family.is_zero() {
            return Err(invalid("invalid_session_family_id"));
        }
        self.client
            .query_opt(
                "SELECT user_id, generation, active_token_id, revoked_reason, \
                 created_at_ms, updated_at_ms FROM trnm_session_families \
                 WHERE family_id = $1",
                &[&family.as_bytes().as_slice()],
            )
            .map_err(map_postgres_error)?
            .map(|row| decode_family(family, &row))
            .transpose()
    }

    pub fn verify_access_session(
        &mut self,
        family: SessionFamilyId,
        user: UserId,
        generation: u64,
    ) -> Result<SessionFamilyRecord, DomainError> {
        if family.is_zero() || user.is_zero() {
            return Err(unauthenticated());
        }
        let record = self
            .load_session_family(family)?
            .ok_or_else(unauthenticated)?;
        if record.user != user
            || record.generation != generation
            || record.active_token.is_none()
            || record.revoked_reason.is_some()
        {
            return Err(unauthenticated());
        }
        Ok(record)
    }

    pub fn rotate_refresh_token(
        &mut self,
        request: &RotateRefreshToken,
    ) -> Result<RefreshRotationOutcome, DomainError> {
        validate_rotation(request)?;
        let rotated_at_ms = to_i64(request.rotated_at_ms)?;
        let mut transaction = self
            .client
            .build_transaction()
            .isolation_level(IsolationLevel::Serializable)
            .start()
            .map_err(map_postgres_error)?;

        let token_row = transaction
            .query_opt(
                "SELECT family_id, generation, state, issued_at_ms, consumed_at_ms \
                 FROM trnm_refresh_tokens \
                 WHERE token_id = $1 AND token_digest = $2 FOR UPDATE",
                &[
                    &request.presented.id.as_bytes().as_slice(),
                    &request.presented.digest.as_bytes().as_slice(),
                ],
            )
            .map_err(map_postgres_error)?
            .ok_or_else(unauthenticated)?;
        let family = decode_session_family_id(token_row.get(0))?;
        let presented = RefreshTokenSnapshot {
            generation: from_i64(token_row.get(1), "negative_refresh_generation")?,
            state: token_row.get(2),
            issued_at_ms: from_i64(token_row.get(3), "negative_refresh_issued_at")?,
            consumed_at_ms: optional_from_i64(
                token_row.get(4),
                "negative_refresh_consumed_at",
            )?,
        };
        validate_refresh_token_snapshot(presented)?;
        if request.rotated_at_ms < presented.issued_at_ms {
            return Err(invalid("refresh_rotation_before_issue"));
        }

        // The family row is the serialization point for rotation. Read the exact
        // requested successor without taking a second token lock: locking two
        // caller-selected token identities would permit adversarial lock cycles.
        let replacement = if presented.state == TOKEN_STATE_CONSUMED {
            transaction
                .query_opt(
                    "SELECT generation, state, issued_at_ms, consumed_at_ms \
                     FROM trnm_refresh_tokens \
                     WHERE family_id = $1 AND token_id = $2 AND token_digest = $3",
                    &[
                        &family.as_bytes().as_slice(),
                        &request.replacement.id.as_bytes().as_slice(),
                        &request.replacement.digest.as_bytes().as_slice(),
                    ],
                )
                .map_err(map_postgres_error)?
                .map(|row| {
                    let snapshot = RefreshTokenSnapshot {
                        generation: from_i64(
                            row.get(0),
                            "negative_replacement_refresh_generation",
                        )?,
                        state: row.get(1),
                        issued_at_ms: from_i64(
                            row.get(2),
                            "negative_replacement_refresh_issued_at",
                        )?,
                        consumed_at_ms: optional_from_i64(
                            row.get(3),
                            "negative_replacement_refresh_consumed_at",
                        )?,
                    };
                    validate_refresh_token_snapshot(snapshot)?;
                    Ok::<RefreshTokenSnapshot, DomainError>(snapshot)
                })
                .transpose()?
        } else {
            None
        };

        let family_row = transaction
            .query_opt(
                "SELECT user_id, generation, active_token_id, revoked_reason, \
                 created_at_ms, updated_at_ms FROM trnm_session_families \
                 WHERE family_id = $1 FOR UPDATE",
                &[&family.as_bytes().as_slice()],
            )
            .map_err(map_postgres_error)?
            .ok_or_else(|| data_loss("refresh_family_missing"))?;
        let record = decode_family(family, &family_row)?;
        if record.revoked_reason.is_some() || record.active_token.is_none() {
            return Err(unauthenticated());
        }
        if request.rotated_at_ms < record.created_at_ms {
            return Err(invalid("refresh_rotation_before_family_creation"));
        }

        if presented.state == TOKEN_STATE_CONSUMED {
            if committed_rotation_retry_matches(
                record,
                presented,
                request.replacement.id,
                replacement,
                request.rotated_at_ms,
            ) {
                transaction.commit().map_err(map_postgres_error)?;
                return Ok(RefreshRotationOutcome::Rotated(record));
            }
            let revoked = revoke_for_replay(
                &mut transaction,
                record,
                request.rotated_at_ms,
                rotated_at_ms,
            )?;
            transaction.commit().map_err(map_postgres_error)?;
            return Ok(RefreshRotationOutcome::ReplayRevoked(revoked));
        }

        if record.active_token != Some(request.presented.id)
            || record.generation != presented.generation
        {
            let revoked = revoke_for_replay(
                &mut transaction,
                record,
                request.rotated_at_ms,
                rotated_at_ms,
            )?;
            transaction.commit().map_err(map_postgres_error)?;
            return Ok(RefreshRotationOutcome::ReplayRevoked(revoked));
        }

        let next_generation = record.generation.checked_add(1).ok_or_else(|| {
            error(
                StableCode::OutOfRange,
                "counter_overflow",
                RetryClass::Never,
            )
        })?;
        let next_generation_i64 = to_i64(next_generation)?;
        let consumed = transaction
            .execute(
                "UPDATE trnm_refresh_tokens SET state = 1, consumed_at_ms = $3 \
                 WHERE family_id = $1 AND token_id = $2 AND state = 0",
                &[
                    &family.as_bytes().as_slice(),
                    &request.presented.id.as_bytes().as_slice(),
                    &rotated_at_ms,
                ],
            )
            .map_err(map_postgres_error)?;
        if consumed != 1 {
            return Err(error(
                StableCode::Aborted,
                "refresh_compare_and_swap_failed",
                RetryClass::SafeImmediate,
            ));
        }
        transaction
            .execute(
                "INSERT INTO trnm_refresh_tokens \
                 (family_id, token_id, token_digest, generation, state, issued_at_ms, \
                  consumed_at_ms) VALUES ($1, $2, $3, $4, 0, $5, NULL)",
                &[
                    &family.as_bytes().as_slice(),
                    &request.replacement.id.as_bytes().as_slice(),
                    &request.replacement.digest.as_bytes().as_slice(),
                    &next_generation_i64,
                    &rotated_at_ms,
                ],
            )
            .map_err(map_postgres_error)?;
        let updated = transaction
            .execute(
                "UPDATE trnm_session_families \
                 SET generation = $2, active_token_id = $3, updated_at_ms = $4 \
                 WHERE family_id = $1 AND generation = $5 AND revoked_reason IS NULL \
                 AND active_token_id = $6",
                &[
                    &family.as_bytes().as_slice(),
                    &next_generation_i64,
                    &request.replacement.id.as_bytes().as_slice(),
                    &rotated_at_ms,
                    &to_i64(record.generation)?,
                    &request.presented.id.as_bytes().as_slice(),
                ],
            )
            .map_err(map_postgres_error)?;
        if updated != 1 {
            return Err(error(
                StableCode::Aborted,
                "refresh_family_compare_and_swap_failed",
                RetryClass::SafeImmediate,
            ));
        }
        transaction.commit().map_err(map_postgres_error)?;
        Ok(RefreshRotationOutcome::Rotated(SessionFamilyRecord {
            family,
            user: record.user,
            generation: next_generation,
            active_token: Some(request.replacement.id),
            revoked_reason: None,
            created_at_ms: record.created_at_ms,
            updated_at_ms: request.rotated_at_ms,
        }))
    }

    pub fn revoke_session_family(
        &mut self,
        family: SessionFamilyId,
        user: UserId,
        reason: RevocationReason,
        revoked_at_ms: u64,
    ) -> Result<SessionFamilyRecord, DomainError> {
        if family.is_zero() || user.is_zero() {
            return Err(unauthenticated());
        }
        let reason_code = revocation_reason_code(reason)?;
        let revoked_at_i64 = to_i64(revoked_at_ms)?;
        let mut transaction = self
            .client
            .build_transaction()
            .isolation_level(IsolationLevel::Serializable)
            .start()
            .map_err(map_postgres_error)?;
        let row = transaction
            .query_opt(
                "SELECT user_id, generation, active_token_id, revoked_reason, \
                 created_at_ms, updated_at_ms FROM trnm_session_families \
                 WHERE family_id = $1 FOR UPDATE",
                &[&family.as_bytes().as_slice()],
            )
            .map_err(map_postgres_error)?
            .ok_or_else(unauthenticated)?;
        let record = decode_family(family, &row)?;
        if record.user != user || revoked_at_ms < record.created_at_ms {
            return Err(unauthenticated());
        }
        if record.revoked_reason.is_some() {
            transaction.commit().map_err(map_postgres_error)?;
            return Ok(record);
        }
        if let Some(active) = record.active_token {
            transaction
                .execute(
                    "UPDATE trnm_refresh_tokens SET state = 1, consumed_at_ms = $3 \
                     WHERE family_id = $1 AND token_id = $2 AND state = 0",
                    &[
                        &family.as_bytes().as_slice(),
                        &active.as_bytes().as_slice(),
                        &revoked_at_i64,
                    ],
                )
                .map_err(map_postgres_error)?;
        }
        transaction
            .execute(
                "UPDATE trnm_session_families SET active_token_id = NULL, \
                 revoked_reason = $2, updated_at_ms = $3 WHERE family_id = $1",
                &[&family.as_bytes().as_slice(), &reason_code, &revoked_at_i64],
            )
            .map_err(map_postgres_error)?;
        transaction.commit().map_err(map_postgres_error)?;
        Ok(SessionFamilyRecord {
            active_token: None,
            revoked_reason: Some(reason),
            updated_at_ms: revoked_at_ms,
            ..record
        })
    }
}

fn committed_rotation_retry_matches(
    family: SessionFamilyRecord,
    presented: RefreshTokenSnapshot,
    replacement_id: RefreshTokenId,
    replacement: Option<RefreshTokenSnapshot>,
    retry_at_ms: u64,
) -> bool {
    let Some(consumed_at_ms) = presented.consumed_at_ms else {
        return false;
    };
    let Some(next_generation) = presented.generation.checked_add(1) else {
        return false;
    };
    let Some(replacement) = replacement else {
        return false;
    };

    presented.state == TOKEN_STATE_CONSUMED
        && family.revoked_reason.is_none()
        && family.active_token == Some(replacement_id)
        && family.created_at_ms <= presented.issued_at_ms
        && presented.issued_at_ms <= consumed_at_ms
        && retry_at_ms >= consumed_at_ms
        && family.generation == next_generation
        && family.updated_at_ms == consumed_at_ms
        && replacement.generation == next_generation
        && replacement.state == TOKEN_STATE_ACTIVE
        && replacement.issued_at_ms == consumed_at_ms
        && replacement.consumed_at_ms.is_none()
}

fn validate_refresh_token_snapshot(snapshot: RefreshTokenSnapshot) -> Result<(), DomainError> {
    match (snapshot.state, snapshot.consumed_at_ms) {
        (TOKEN_STATE_ACTIVE, None) => Ok(()),
        (TOKEN_STATE_CONSUMED, Some(consumed_at_ms))
            if consumed_at_ms >= snapshot.issued_at_ms =>
        {
            Ok(())
        }
        (TOKEN_STATE_ACTIVE, _) | (TOKEN_STATE_CONSUMED, _) => {
            Err(data_loss("invalid_refresh_token_lifecycle"))
        }
        _ => Err(data_loss("invalid_refresh_token_state")),
    }
}

fn optional_from_i64(
    value: Option<i64>,
    reason: &'static str,
) -> Result<Option<u64>, DomainError> {
    value.map(|item| from_i64(item, reason)).transpose()
}

fn revoke_for_replay(
    transaction: &mut Transaction<'_>,
    record: SessionFamilyRecord,
    revoked_at_ms: u64,
    revoked_at_i64: i64,
) -> Result<SessionFamilyRecord, DomainError> {
    if let Some(active) = record.active_token {
        transaction
            .execute(
                "UPDATE trnm_refresh_tokens SET state = 1, \
                 consumed_at_ms = COALESCE(consumed_at_ms, $3) \
                 WHERE family_id = $1 AND token_id = $2",
                &[
                    &record.family.as_bytes().as_slice(),
                    &active.as_bytes().as_slice(),
                    &revoked_at_i64,
                ],
            )
            .map_err(map_postgres_error)?;
    }
    transaction
        .execute(
            "UPDATE trnm_session_families SET active_token_id = NULL, \
             revoked_reason = 2, updated_at_ms = $2 WHERE family_id = $1",
            &[&record.family.as_bytes().as_slice(), &revoked_at_i64],
        )
        .map_err(map_postgres_error)?;
    Ok(SessionFamilyRecord {
        active_token: None,
        revoked_reason: Some(RevocationReason::RefreshReplay),
        updated_at_ms: revoked_at_ms,
        ..record
    })
}

fn validate_create(request: &CreateSessionFamily) -> Result<(), DomainError> {
    if request.family.is_zero()
        || request.user.is_zero()
        || request.refresh.id.is_zero()
        || request.refresh.digest.is_zero()
    {
        return Err(invalid("invalid_session_family_create"));
    }
    to_i64(request.issued_at_ms)?;
    Ok(())
}

fn validate_rotation(request: &RotateRefreshToken) -> Result<(), DomainError> {
    if request.presented.id.is_zero()
        || request.presented.digest.is_zero()
        || request.replacement.id.is_zero()
        || request.replacement.digest.is_zero()
        || request.presented.id == request.replacement.id
        || request.presented.digest == request.replacement.digest
    {
        return Err(invalid("invalid_refresh_rotation"));
    }
    to_i64(request.rotated_at_ms)?;
    Ok(())
}

fn decode_family(family: SessionFamilyId, row: &Row) -> Result<SessionFamilyRecord, DomainError> {
    let user = decode_user_id(row.get(0))?;
    let generation = from_i64(row.get(1), "negative_session_generation")?;
    let active_token = row
        .get::<_, Option<Vec<u8>>>(2)
        .map(decode_refresh_token_id)
        .transpose()?;
    let revoked_reason = row
        .get::<_, Option<i16>>(3)
        .map(decode_revocation_reason)
        .transpose()?;
    Ok(SessionFamilyRecord {
        family,
        user,
        generation,
        active_token,
        revoked_reason,
        created_at_ms: from_i64(row.get(4), "negative_session_created_at")?,
        updated_at_ms: from_i64(row.get(5), "negative_session_updated_at")?,
    })
}

fn decode_session_family_id(bytes: Vec<u8>) -> Result<SessionFamilyId, DomainError> {
    let value: [u8; 16] = bytes
        .try_into()
        .map_err(|_| data_loss("invalid_session_family_id_bytes"))?;
    Ok(SessionFamilyId::new(value))
}

fn decode_refresh_token_id(bytes: Vec<u8>) -> Result<RefreshTokenId, DomainError> {
    let value: [u8; 16] = bytes
        .try_into()
        .map_err(|_| data_loss("invalid_refresh_token_id_bytes"))?;
    Ok(RefreshTokenId::new(value))
}

fn decode_user_id(bytes: Vec<u8>) -> Result<UserId, DomainError> {
    let value: [u8; 16] = bytes
        .try_into()
        .map_err(|_| data_loss("invalid_session_user_id_bytes"))?;
    Ok(UserId::new(value))
}

fn revocation_reason_code(reason: RevocationReason) -> Result<i16, DomainError> {
    match reason {
        RevocationReason::Logout => Ok(0),
        RevocationReason::CredentialReset => Ok(1),
        RevocationReason::RefreshReplay => Ok(2),
        RevocationReason::Administrator => Ok(3),
    }
}

fn decode_revocation_reason(value: i16) -> Result<RevocationReason, DomainError> {
    match value {
        0 => Ok(RevocationReason::Logout),
        1 => Ok(RevocationReason::CredentialReset),
        2 => Ok(RevocationReason::RefreshReplay),
        3 => Ok(RevocationReason::Administrator),
        _ => Err(data_loss("invalid_session_revocation_reason")),
    }
}

const fn unauthenticated() -> DomainError {
    error(
        StableCode::Unauthenticated,
        "session_not_active",
        RetryClass::Never,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn digest(byte: u8) -> Digest32 {
        Digest32::new([byte; 32])
    }

    fn credential(id: u8, digest_byte: u8) -> RefreshTokenCredential {
        RefreshTokenCredential {
            id: RefreshTokenId::new([id; 16]),
            digest: digest(digest_byte),
        }
    }

    fn family_record() -> SessionFamilyRecord {
        SessionFamilyRecord {
            family: SessionFamilyId::new([1; 16]),
            user: UserId::new([2; 16]),
            generation: 8,
            active_token: Some(RefreshTokenId::new([5; 16])),
            revoked_reason: None,
            created_at_ms: 10,
            updated_at_ms: 20,
        }
    }

    fn consumed_predecessor() -> RefreshTokenSnapshot {
        RefreshTokenSnapshot {
            generation: 7,
            state: TOKEN_STATE_CONSUMED,
            issued_at_ms: 10,
            consumed_at_ms: Some(20),
        }
    }

    fn active_successor() -> RefreshTokenSnapshot {
        RefreshTokenSnapshot {
            generation: 8,
            state: TOKEN_STATE_ACTIVE,
            issued_at_ms: 20,
            consumed_at_ms: None,
        }
    }

    #[test]
    fn create_and_rotation_validation_fail_closed() {
        let valid = CreateSessionFamily {
            family: SessionFamilyId::new([1; 16]),
            user: UserId::new([2; 16]),
            refresh: credential(3, 4),
            issued_at_ms: 5,
        };
        assert!(validate_create(&valid).is_ok());

        let invalid = CreateSessionFamily {
            family: SessionFamilyId::new([0; 16]),
            ..valid
        };
        assert_eq!(
            validate_create(&invalid).unwrap_err().reason(),
            "invalid_session_family_create"
        );

        let invalid_rotation = RotateRefreshToken {
            presented: credential(5, 6),
            replacement: credential(5, 7),
            rotated_at_ms: 8,
        };
        assert_eq!(
            validate_rotation(&invalid_rotation).unwrap_err().reason(),
            "invalid_refresh_rotation"
        );
    }

    #[test]
    fn exact_immediate_successor_is_an_idempotent_response_loss_retry() {
        assert!(committed_rotation_retry_matches(
            family_record(),
            consumed_predecessor(),
            RefreshTokenId::new([5; 16]),
            Some(active_successor()),
            21,
        ));
    }

    #[test]
    fn response_loss_retry_requires_every_durable_fact_to_match() {
        let family = family_record();
        let predecessor = consumed_predecessor();
        let successor = active_successor();
        let replacement_id = RefreshTokenId::new([5; 16]);

        assert!(!committed_rotation_retry_matches(
            SessionFamilyRecord {
                active_token: Some(RefreshTokenId::new([6; 16])),
                ..family
            },
            predecessor,
            replacement_id,
            Some(successor),
            21,
        ));
        assert!(!committed_rotation_retry_matches(
            SessionFamilyRecord {
                generation: 9,
                ..family
            },
            predecessor,
            replacement_id,
            Some(successor),
            21,
        ));
        assert!(!committed_rotation_retry_matches(
            SessionFamilyRecord {
                updated_at_ms: 19,
                ..family
            },
            predecessor,
            replacement_id,
            Some(successor),
            21,
        ));
        assert!(!committed_rotation_retry_matches(
            family,
            predecessor,
            replacement_id,
            None,
            21,
        ));
        assert!(!committed_rotation_retry_matches(
            family,
            predecessor,
            replacement_id,
            Some(RefreshTokenSnapshot {
                generation: 9,
                ..successor
            }),
            21,
        ));
        assert!(!committed_rotation_retry_matches(
            family,
            predecessor,
            replacement_id,
            Some(RefreshTokenSnapshot {
                issued_at_ms: 19,
                ..successor
            }),
            21,
        ));
        assert!(!committed_rotation_retry_matches(
            family,
            predecessor,
            replacement_id,
            Some(successor),
            19,
        ));
    }

    #[test]
    fn consumed_successor_or_generation_ceiling_never_qualifies_as_response_loss() {
        assert!(!committed_rotation_retry_matches(
            family_record(),
            consumed_predecessor(),
            RefreshTokenId::new([5; 16]),
            Some(RefreshTokenSnapshot {
                state: TOKEN_STATE_CONSUMED,
                consumed_at_ms: Some(30),
                ..active_successor()
            }),
            31,
        ));
        assert!(!committed_rotation_retry_matches(
            SessionFamilyRecord {
                generation: u64::MAX,
                ..family_record()
            },
            RefreshTokenSnapshot {
                generation: u64::MAX,
                ..consumed_predecessor()
            },
            RefreshTokenId::new([5; 16]),
            Some(active_successor()),
            u64::MAX,
        ));
    }

    #[test]
    fn refresh_token_lifecycle_decode_is_fail_closed() {
        assert!(validate_refresh_token_snapshot(active_successor()).is_ok());
        assert!(validate_refresh_token_snapshot(consumed_predecessor()).is_ok());
        for invalid in [
            RefreshTokenSnapshot {
                state: TOKEN_STATE_ACTIVE,
                consumed_at_ms: Some(20),
                ..active_successor()
            },
            RefreshTokenSnapshot {
                state: TOKEN_STATE_CONSUMED,
                consumed_at_ms: None,
                ..consumed_predecessor()
            },
            RefreshTokenSnapshot {
                state: TOKEN_STATE_CONSUMED,
                issued_at_ms: 21,
                consumed_at_ms: Some(20),
                ..consumed_predecessor()
            },
            RefreshTokenSnapshot {
                state: 7,
                ..active_successor()
            },
        ] {
            assert_eq!(
                validate_refresh_token_snapshot(invalid)
                    .unwrap_err()
                    .code(),
                StableCode::DataLoss
            );
        }
    }

    #[test]
    fn persisted_revocation_reason_mapping_is_exact() {
        let cases = [
            (RevocationReason::Logout, 0),
            (RevocationReason::CredentialReset, 1),
            (RevocationReason::RefreshReplay, 2),
            (RevocationReason::Administrator, 3),
        ];
        for (reason, code) in cases {
            assert_eq!(revocation_reason_code(reason).unwrap(), code);
            assert_eq!(decode_revocation_reason(code).unwrap(), reason);
        }
        assert_eq!(
            decode_revocation_reason(4).unwrap_err().code(),
            StableCode::DataLoss
        );
    }

    #[test]
    fn generic_session_failure_does_not_disclose_identity_state() {
        let error = unauthenticated();
        assert_eq!(error.code(), StableCode::Unauthenticated);
        assert_eq!(error.reason(), "session_not_active");
        assert_eq!(error.retry(), RetryClass::Never);
    }
}
