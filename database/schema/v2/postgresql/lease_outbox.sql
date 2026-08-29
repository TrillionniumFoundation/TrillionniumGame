-- PostgreSQL outbox lease transaction.
-- Parameters:
--   $1 tenant UUID
--   $2 worker UUID
--   $3 lease duration in whole seconds
--   $4 maximum records
-- Run both statements inside one SERIALIZABLE transaction.

UPDATE trnm_outbox
SET state = 'pending',
    lease_owner = NULL,
    lease_expires_at = NULL,
    available_at = CURRENT_TIMESTAMP,
    updated_at = CURRENT_TIMESTAMP
WHERE tenant_id = $1
  AND state = 'leased'
  AND lease_expires_at <= CURRENT_TIMESTAMP;

WITH candidates AS (
    SELECT tenant_id, intent_id
    FROM trnm_outbox
    WHERE tenant_id = $1
      AND state = 'pending'
      AND available_at <= CURRENT_TIMESTAMP
      AND attempt < 32
    ORDER BY available_at, intent_id
    FOR UPDATE SKIP LOCKED
    LIMIT $4
)
UPDATE trnm_outbox AS outbox
SET state = 'leased',
    lease_owner = $2,
    lease_generation = outbox.lease_generation + 1,
    lease_expires_at = CURRENT_TIMESTAMP + make_interval(secs => $3),
    attempt = outbox.attempt + 1,
    updated_at = CURRENT_TIMESTAMP
FROM candidates
WHERE outbox.tenant_id = candidates.tenant_id
  AND outbox.intent_id = candidates.intent_id
  AND outbox.state = 'pending'
RETURNING
    outbox.tenant_id,
    outbox.intent_id,
    outbox.entity_id,
    outbox.command_id,
    outbox.kind,
    outbox.payload_digest,
    outbox.payload_bytes,
    outbox.attempt,
    outbox.lease_generation,
    outbox.lease_owner,
    outbox.lease_expires_at;

-- Completion must use all fence columns and must compare the canonical receipt
-- digest. A zero-row result means stale ownership, duplicate completion with a
-- different digest, or a terminal record; callers must not publish success.
--
-- UPDATE trnm_outbox
-- SET state = 'applied', lease_owner = NULL, lease_expires_at = NULL,
--     applied_receipt_digest = $5, updated_at = CURRENT_TIMESTAMP
-- WHERE tenant_id = $1 AND intent_id = $6 AND state = 'leased'
--   AND lease_owner = $2 AND lease_generation = $7
--   AND octet_length($5) = 32
-- RETURNING applied_receipt_digest;
