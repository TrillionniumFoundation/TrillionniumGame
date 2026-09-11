\set slot random(1, 1000)
\set payload random(1, 2000000000)
BEGIN;
INSERT INTO trnm_storage_objects
  (collection, object_key, user_id, value_bytes, version_digest,
   read_permission, write_permission, updated_at_ms)
VALUES
  ('capacity', 'object-' || :slot,
   decode(repeat('aa', 16), 'hex'),
   decode(lpad(to_hex(:payload), 8, '0'), 'hex'),
   decode(repeat('bb', 32), 'hex'), 2, 1, :payload)
ON CONFLICT (collection, object_key, user_id)
DO UPDATE SET value_bytes = EXCLUDED.value_bytes,
              version_digest = EXCLUDED.version_digest,
              read_permission = EXCLUDED.read_permission,
              write_permission = EXCLUDED.write_permission,
              updated_at_ms = EXCLUDED.updated_at_ms;
SELECT octet_length(value_bytes)
FROM trnm_storage_objects
WHERE collection = 'capacity'
  AND object_key = 'object-' || :slot
  AND user_id = decode(repeat('aa', 16), 'hex');
COMMIT;
