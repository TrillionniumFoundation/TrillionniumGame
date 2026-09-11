\set ON_ERROR_STOP on
BEGIN;
LOCK TABLE trnm_outbox IN ACCESS EXCLUSIVE MODE;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM trnm_outbox WHERE state = 1) THEN
    RAISE EXCEPTION 'active outbox lease blocks recovery quarantine';
  END IF;
END
$$;
COPY (
  SELECT encode(intent_id, 'hex') AS intent_id,
         encode(entity_id, 'hex') AS entity_id,
         encode(command_id, 'hex') AS command_id,
         kind,
         encode(payload_digest, 'hex') AS payload_digest,
         attempt,
         lease_generation,
         state,
         available_at_ms,
         updated_at_ms
  FROM trnm_outbox
  WHERE state = 0
  ORDER BY intent_id
) TO STDOUT WITH (FORMAT csv, HEADER true);
UPDATE trnm_outbox
SET state = 3,
    owner_node = NULL,
    receipt_digest = NULL,
    dead_reason_digest = decode(repeat('f0', 32), 'hex'),
    updated_at_ms = GREATEST(updated_at_ms, available_at_ms)
WHERE state = 0;
ALTER ROLE trnm_runtime IN DATABASE trnm_source
  SET default_transaction_read_only = on;
COMMIT;
