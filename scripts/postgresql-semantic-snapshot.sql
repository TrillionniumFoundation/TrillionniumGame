\set ON_ERROR_STOP on
\pset tuples_only on
\pset format unaligned

WITH rows AS (
  SELECT 'trnm_schema_metadata' AS table_name,
         jsonb_build_object(
           'singleton', singleton,
           'schema_version', schema_version,
           'profile', profile,
           'source_commit', source_commit,
           'applied_at_ms', applied_at_ms
         ) AS value
  FROM trnm_schema_metadata
  UNION ALL
  SELECT 'trnm_entity_heads', jsonb_build_object(
           'entity_id', encode(entity_id, 'hex'),
           'revision', revision,
           'last_event_sequence', last_event_sequence,
           'authority_generation', authority_generation,
           'state_digest', encode(state_digest, 'hex'),
           'updated_at_ms', updated_at_ms
         )
  FROM trnm_entity_heads
  UNION ALL
  SELECT 'trnm_command_receipts', jsonb_build_object(
           'entity_id', encode(entity_id, 'hex'),
           'command_id', encode(command_id, 'hex'),
           'fingerprint', encode(fingerprint, 'hex'),
           'revision', revision,
           'state_digest', encode(state_digest, 'hex'),
           'first_event_sequence', first_event_sequence,
           'last_event_sequence', last_event_sequence,
           'event_count', event_count,
           'committed_at_ms', committed_at_ms
         )
  FROM trnm_command_receipts
  UNION ALL
  SELECT 'trnm_events', jsonb_build_object(
           'entity_id', encode(entity_id, 'hex'),
           'sequence', sequence,
           'event_id', encode(event_id, 'hex'),
           'command_id', encode(command_id, 'hex'),
           'payload_digest', encode(payload_digest, 'hex'),
           'created_at_ms', created_at_ms
         )
  FROM trnm_events
  UNION ALL
  SELECT 'trnm_outbox', jsonb_build_object(
           'intent_id', encode(intent_id, 'hex'),
           'entity_id', encode(entity_id, 'hex'),
           'command_id', encode(command_id, 'hex'),
           'kind', kind,
           'payload_digest', encode(payload_digest, 'hex'),
           'attempt', attempt,
           'lease_generation', lease_generation,
           'state', state,
           'owner_node', CASE WHEN owner_node IS NULL THEN NULL ELSE encode(owner_node, 'hex') END,
           'receipt_digest', CASE WHEN receipt_digest IS NULL THEN NULL ELSE encode(receipt_digest, 'hex') END,
           'dead_reason_digest', CASE WHEN dead_reason_digest IS NULL THEN NULL ELSE encode(dead_reason_digest, 'hex') END,
           'available_at_ms', available_at_ms,
           'updated_at_ms', updated_at_ms
         )
  FROM trnm_outbox
  UNION ALL
  SELECT 'trnm_command_outbox', jsonb_build_object(
           'entity_id', encode(entity_id, 'hex'),
           'command_id', encode(command_id, 'hex'),
           'position', position,
           'intent_id', encode(intent_id, 'hex')
         )
  FROM trnm_command_outbox
  UNION ALL
  SELECT 'trnm_authority_leases', jsonb_build_object(
           'entity_id', encode(entity_id, 'hex'),
           'owner_node', encode(owner_node, 'hex'),
           'lease_generation', lease_generation,
           'authority_generation', authority_generation,
           'expires_at_ms', expires_at_ms,
           'updated_at_ms', updated_at_ms
         )
  FROM trnm_authority_leases
  UNION ALL
  SELECT 'trnm_session_families', jsonb_build_object(
           'family_id', encode(family_id, 'hex'),
           'user_id', encode(user_id, 'hex'),
           'generation', generation,
           'active_token_id', CASE WHEN active_token_id IS NULL THEN NULL ELSE encode(active_token_id, 'hex') END,
           'revoked_reason', revoked_reason,
           'created_at_ms', created_at_ms,
           'updated_at_ms', updated_at_ms
         )
  FROM trnm_session_families
  UNION ALL
  SELECT 'trnm_refresh_tokens', jsonb_build_object(
           'family_id', encode(family_id, 'hex'),
           'token_id', encode(token_id, 'hex'),
           'token_digest', encode(token_digest, 'hex'),
           'generation', generation,
           'state', state,
           'issued_at_ms', issued_at_ms,
           'consumed_at_ms', consumed_at_ms
         )
  FROM trnm_refresh_tokens
  UNION ALL
  SELECT 'trnm_storage_objects', jsonb_build_object(
           'collection', collection,
           'object_key', object_key,
           'user_id', encode(user_id, 'hex'),
           'value_bytes', encode(value_bytes, 'hex'),
           'version_digest', encode(version_digest, 'hex'),
           'read_permission', read_permission,
           'write_permission', write_permission,
           'updated_at_ms', updated_at_ms
         )
  FROM trnm_storage_objects
)
SELECT table_name || '|' || value::text
FROM rows
ORDER BY table_name, value::text;
