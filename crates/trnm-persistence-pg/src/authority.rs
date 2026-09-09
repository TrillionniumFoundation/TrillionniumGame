use postgres::{IsolationLevel, Row};
use trnm_contracts::{DomainError, RetryClass, StableCode};

use super::{
    data_loss, decode_id16, error, from_i64, invalid, map_postgres_error, to_i64, EntityId, NodeId,
    PgRepository,
};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct AuthorityLease {
    pub entity: EntityId,
    pub owner: NodeId,
    pub lease_generation: u64,
    pub authority_generation: u64,
    pub expires_at_ms: u64,
    pub updated_at_ms: u64,
}

impl PgRepository {
    pub fn load_authority_lease(
        &mut self,
        entity: EntityId,
    ) -> Result<Option<AuthorityLease>, DomainError> {
        if entity.is_zero() {
            return Err(invalid("invalid_authority_entity"));
        }
        self.client
            .query_opt(
                "SELECT owner_node, lease_generation, authority_generation, \
                 expires_at_ms, updated_at_ms FROM trnm_authority_leases \
                 WHERE entity_id = $1",
                &[&entity.as_bytes().as_slice()],
            )
            .map_err(map_postgres_error)?
            .map(|row| decode_lease(entity, &row))
            .transpose()
    }

    pub fn acquire_authority_lease(
        &mut self,
        entity: EntityId,
        owner: NodeId,
        expected_authority_generation: u64,
        now_ms: u64,
        expires_at_ms: u64,
    ) -> Result<AuthorityLease, DomainError> {
        validate_lease_request(
            entity,
            owner,
            expected_authority_generation,
            now_ms,
            expires_at_ms,
        )?;
        let now_i64 = to_i64(now_ms)?;
        let expires_i64 = to_i64(expires_at_ms)?;
        let expected_authority_i64 = to_i64(expected_authority_generation)?;
        let mut transaction = self
            .client
            .build_transaction()
            .isolation_level(IsolationLevel::Serializable)
            .start()
            .map_err(map_postgres_error)?;

        let head = transaction
            .query_opt(
                "SELECT authority_generation, updated_at_ms FROM trnm_entity_heads \
                 WHERE entity_id = $1 FOR UPDATE",
                &[&entity.as_bytes().as_slice()],
            )
            .map_err(map_postgres_error)?
            .ok_or_else(|| {
                error(
                    StableCode::NotFound,
                    "authority_entity_not_found",
                    RetryClass::Never,
                )
            })?;
        let current_authority = from_i64(head.get(0), "negative_authority_generation")?;
        let head_updated_at = from_i64(head.get(1), "negative_head_updated_at")?;
        if current_authority != expected_authority_generation {
            return Err(error(
                StableCode::Aborted,
                "authority_generation_mismatch",
                RetryClass::ResyncRequired,
            ));
        }
        if now_ms < head_updated_at {
            return Err(invalid("authority_clock_regression"));
        }

        let existing = transaction
            .query_opt(
                "SELECT owner_node, lease_generation, authority_generation, \
                 expires_at_ms, updated_at_ms FROM trnm_authority_leases \
                 WHERE entity_id = $1 FOR UPDATE",
                &[&entity.as_bytes().as_slice()],
            )
            .map_err(map_postgres_error)?
            .map(|row| decode_lease(entity, &row))
            .transpose()?;

        let lease = match existing {
            None => {
                transaction
                    .execute(
                        "INSERT INTO trnm_authority_leases \
                         (entity_id, owner_node, lease_generation, authority_generation, \
                          expires_at_ms, updated_at_ms) \
                         VALUES ($1, $2, 1, $3, $4, $5)",
                        &[
                            &entity.as_bytes().as_slice(),
                            &owner.as_bytes().as_slice(),
                            &expected_authority_i64,
                            &expires_i64,
                            &now_i64,
                        ],
                    )
                    .map_err(map_postgres_error)?;
                AuthorityLease {
                    entity,
                    owner,
                    lease_generation: 1,
                    authority_generation: expected_authority_generation,
                    expires_at_ms,
                    updated_at_ms: now_ms,
                }
            }
            Some(previous) => {
                if previous.authority_generation != current_authority {
                    return Err(data_loss("authority_lease_head_generation_mismatch"));
                }
                if now_ms < previous.updated_at_ms {
                    return Err(invalid("authority_clock_regression"));
                }
                if previous.expires_at_ms > now_ms {
                    return Err(error(
                        StableCode::Aborted,
                        "authority_lease_held",
                        RetryClass::SafeBackoff,
                    ));
                }
                let next_lease_generation = previous
                    .lease_generation
                    .checked_add(1)
                    .ok_or_else(authority_counter_overflow)?;
                let next_authority_generation = current_authority
                    .checked_add(1)
                    .ok_or_else(authority_counter_overflow)?;
                let next_lease_i64 = to_i64(next_lease_generation)?;
                let next_authority_i64 = to_i64(next_authority_generation)?;
                let updated_head = transaction
                    .execute(
                        "UPDATE trnm_entity_heads \
                         SET authority_generation = $2, updated_at_ms = $3 \
                         WHERE entity_id = $1 AND authority_generation = $4",
                        &[
                            &entity.as_bytes().as_slice(),
                            &next_authority_i64,
                            &now_i64,
                            &expected_authority_i64,
                        ],
                    )
                    .map_err(map_postgres_error)?;
                if updated_head != 1 {
                    return Err(error(
                        StableCode::Aborted,
                        "authority_head_compare_and_swap_failed",
                        RetryClass::ResyncRequired,
                    ));
                }
                let previous_lease_i64 = to_i64(previous.lease_generation)?;
                let updated_lease = transaction
                    .execute(
                        "UPDATE trnm_authority_leases \
                         SET owner_node = $2, lease_generation = $3, \
                             authority_generation = $4, expires_at_ms = $5, \
                             updated_at_ms = $6 \
                         WHERE entity_id = $1 AND lease_generation = $7 \
                           AND authority_generation = $8",
                        &[
                            &entity.as_bytes().as_slice(),
                            &owner.as_bytes().as_slice(),
                            &next_lease_i64,
                            &next_authority_i64,
                            &expires_i64,
                            &now_i64,
                            &previous_lease_i64,
                            &expected_authority_i64,
                        ],
                    )
                    .map_err(map_postgres_error)?;
                if updated_lease != 1 {
                    return Err(error(
                        StableCode::Aborted,
                        "authority_lease_compare_and_swap_failed",
                        RetryClass::ResyncRequired,
                    ));
                }
                AuthorityLease {
                    entity,
                    owner,
                    lease_generation: next_lease_generation,
                    authority_generation: next_authority_generation,
                    expires_at_ms,
                    updated_at_ms: now_ms,
                }
            }
        };
        transaction.commit().map_err(map_postgres_error)?;
        Ok(lease)
    }

    pub fn renew_authority_lease(
        &mut self,
        lease: AuthorityLease,
        now_ms: u64,
        expires_at_ms: u64,
    ) -> Result<AuthorityLease, DomainError> {
        validate_lease_request(
            lease.entity,
            lease.owner,
            lease.authority_generation,
            now_ms,
            expires_at_ms,
        )?;
        if lease.lease_generation == 0 {
            return Err(invalid("invalid_authority_lease_generation"));
        }
        let now_i64 = to_i64(now_ms)?;
        let expires_i64 = to_i64(expires_at_ms)?;
        let lease_generation_i64 = to_i64(lease.lease_generation)?;
        let authority_generation_i64 = to_i64(lease.authority_generation)?;
        let mut transaction = self
            .client
            .build_transaction()
            .isolation_level(IsolationLevel::Serializable)
            .start()
            .map_err(map_postgres_error)?;
        let current = transaction
            .query_opt(
                "SELECT owner_node, lease_generation, authority_generation, \
                 expires_at_ms, updated_at_ms FROM trnm_authority_leases \
                 WHERE entity_id = $1 FOR UPDATE",
                &[&lease.entity.as_bytes().as_slice()],
            )
            .map_err(map_postgres_error)?
            .map(|row| decode_lease(lease.entity, &row))
            .transpose()?
            .ok_or_else(|| {
                error(
                    StableCode::NotFound,
                    "authority_lease_not_found",
                    RetryClass::Never,
                )
            })?;
        if current.owner != lease.owner
            || current.lease_generation != lease.lease_generation
            || current.authority_generation != lease.authority_generation
        {
            return Err(error(
                StableCode::Aborted,
                "stale_authority_lease",
                RetryClass::ResyncRequired,
            ));
        }
        if current.expires_at_ms <= now_ms {
            return Err(error(
                StableCode::Aborted,
                "authority_lease_expired",
                RetryClass::ResyncRequired,
            ));
        }
        if now_ms < current.updated_at_ms {
            return Err(invalid("authority_clock_regression"));
        }
        let head_generation: i64 = transaction
            .query_one(
                "SELECT authority_generation FROM trnm_entity_heads \
                 WHERE entity_id = $1 FOR UPDATE",
                &[&lease.entity.as_bytes().as_slice()],
            )
            .map_err(map_postgres_error)?
            .get(0);
        if from_i64(head_generation, "negative_authority_generation")? != lease.authority_generation
        {
            return Err(data_loss("authority_lease_head_generation_mismatch"));
        }
        let updated = transaction
            .execute(
                "UPDATE trnm_authority_leases \
                 SET expires_at_ms = $5, updated_at_ms = $6 \
                 WHERE entity_id = $1 AND owner_node = $2 \
                   AND lease_generation = $3 AND authority_generation = $4",
                &[
                    &lease.entity.as_bytes().as_slice(),
                    &lease.owner.as_bytes().as_slice(),
                    &lease_generation_i64,
                    &authority_generation_i64,
                    &expires_i64,
                    &now_i64,
                ],
            )
            .map_err(map_postgres_error)?;
        if updated != 1 {
            return Err(error(
                StableCode::Aborted,
                "authority_lease_compare_and_swap_failed",
                RetryClass::ResyncRequired,
            ));
        }
        transaction.commit().map_err(map_postgres_error)?;
        Ok(AuthorityLease {
            expires_at_ms,
            updated_at_ms: now_ms,
            ..lease
        })
    }

    pub fn release_authority_lease(
        &mut self,
        lease: AuthorityLease,
        released_at_ms: u64,
    ) -> Result<AuthorityLease, DomainError> {
        if released_at_ms < lease.updated_at_ms {
            return Err(invalid("authority_clock_regression"));
        }
        let released_i64 = to_i64(released_at_ms)?;
        let lease_generation_i64 = to_i64(lease.lease_generation)?;
        let authority_generation_i64 = to_i64(lease.authority_generation)?;
        let updated = self
            .client
            .execute(
                "UPDATE trnm_authority_leases \
                 SET expires_at_ms = $5, updated_at_ms = $5 \
                 WHERE entity_id = $1 AND owner_node = $2 \
                   AND lease_generation = $3 AND authority_generation = $4 \
                   AND expires_at_ms > $5",
                &[
                    &lease.entity.as_bytes().as_slice(),
                    &lease.owner.as_bytes().as_slice(),
                    &lease_generation_i64,
                    &authority_generation_i64,
                    &released_i64,
                ],
            )
            .map_err(map_postgres_error)?;
        if updated != 1 {
            return Err(error(
                StableCode::Aborted,
                "stale_or_expired_authority_lease",
                RetryClass::ResyncRequired,
            ));
        }
        Ok(AuthorityLease {
            expires_at_ms: released_at_ms,
            updated_at_ms: released_at_ms,
            ..lease
        })
    }
}

fn validate_lease_request(
    entity: EntityId,
    owner: NodeId,
    authority_generation: u64,
    now_ms: u64,
    expires_at_ms: u64,
) -> Result<(), DomainError> {
    if entity.is_zero() || owner.is_zero() || authority_generation == 0 || expires_at_ms <= now_ms {
        return Err(invalid("invalid_authority_lease_request"));
    }
    to_i64(authority_generation)?;
    to_i64(now_ms)?;
    to_i64(expires_at_ms)?;
    Ok(())
}

fn decode_lease(entity: EntityId, row: &Row) -> Result<AuthorityLease, DomainError> {
    Ok(AuthorityLease {
        entity,
        owner: decode_id16(row.get(0), NodeId::new, "invalid_authority_owner_bytes")?,
        lease_generation: from_i64(row.get(1), "negative_authority_lease_generation")?,
        authority_generation: from_i64(row.get(2), "negative_authority_generation")?,
        expires_at_ms: from_i64(row.get(3), "negative_authority_expiration")?,
        updated_at_ms: from_i64(row.get(4), "negative_authority_updated_at")?,
    })
}

const fn authority_counter_overflow() -> DomainError {
    error(
        StableCode::OutOfRange,
        "authority_generation_overflow",
        RetryClass::Never,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lease_request_validation_is_fail_closed() {
        let entity = EntityId::new([1; 16]);
        let owner = NodeId::new([2; 16]);
        assert_eq!(
            validate_lease_request(entity, owner, 1, 10, 10)
                .unwrap_err()
                .reason(),
            "invalid_authority_lease_request"
        );
        assert_eq!(
            validate_lease_request(EntityId::new([0; 16]), owner, 1, 10, 20)
                .unwrap_err()
                .reason(),
            "invalid_authority_lease_request"
        );
        assert_eq!(
            validate_lease_request(entity, owner, 1, u64::MAX, u64::MAX)
                .unwrap_err()
                .reason(),
            "invalid_authority_lease_request"
        );
    }

    #[test]
    fn authority_generation_overflow_is_terminal() {
        assert_eq!(authority_counter_overflow().code(), StableCode::OutOfRange);
        assert_eq!(authority_counter_overflow().retry(), RetryClass::Never);
    }
}
