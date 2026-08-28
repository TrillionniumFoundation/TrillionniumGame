-- CockroachDB outbox lease transaction.
-- Parameters:
--   $1 tenant UUID
--   $2 worker UUID
--   $3 lease duration in whole seconds
--   $4 maximum records
-- Execute the complete transaction at SERIALIZABLE isolation. A retryable
-- transaction error restarts both statements with the same worker identity.

UPDATE trnm_outbox
SET state = 'pending',
    lease_owner = NULL,
    lease_expires_at = NULL,
    available_at = current_timestamp(),
    updated_at = current_timestamp()
WHERE tenant_id = $1
  AND state = 'leased'
  AND lease_expires_at <= current_timestamp();

WITH candidates AS (
    SELECT tenant_id, intent_id
    FROM trnm_outbox
    WHERE tenant_id = $1
      AND state = 'pending'
      AND available_at <= current_timestamp()
      AND attempt < 32
    ORDER BY available_at, intent_id
    LIMIT $4
)
UPDATE trnm_outbox AS outbox
SET state = 'leased',
    lease_owner = $2,
    lease_generation = outbox.lease_generation + 1,
    lease_expires_at = current_timestamp() + ($3 * INTERVAL '1 second'),
    attempt = outbox.attempt + 1,
    updated_at = current_timestamp()
WHERE (outbox.tenant_id, outbox.intent_id) IN (
    SELECT tenant_id, intent_id FROM candidates
)
  AND outbox.state = 'pending'
  AND outbox.available_at <= current_timestamp()
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

-- Completion uses state + owner + generation as a compare-and-swap fence.
-- A zero-row result is never success and must not advance an external cursor.
--
-- UPDATE trnm_outbox
-- SET state = 'applied', lease_owner = NULL, lease_expires_at = NULL,
--     applied_receipt_digest = $5, updated_at = current_timestamp()
-- WHERE tenant_id = $1 AND intent_id = $6 AND state = 'leased'
--   AND lease_owner = $2 AND lease_generation = $7
--   AND octet_length($5) = 32
-- RETURNING applied_receipt_digest;
