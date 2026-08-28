-- Stable, machine-readable CockroachDB schema inventory.

SELECT
    table_name,
    column_name,
    data_type,
    is_nullable,
    COALESCE(column_default, '') AS column_default
FROM information_schema.columns
WHERE table_schema = current_schema()
  AND table_name IN (
      'trnm_schema_migrations',
      'trnm_entity_heads',
      'trnm_command_receipts',
      'trnm_events',
      'trnm_outbox'
  )
ORDER BY table_name, ordinal_position;

SELECT
    table_name,
    constraint_name,
    constraint_type
FROM information_schema.table_constraints
WHERE table_schema = current_schema()
  AND table_name IN (
      'trnm_schema_migrations',
      'trnm_entity_heads',
      'trnm_command_receipts',
      'trnm_events',
      'trnm_outbox'
  )
ORDER BY table_name, constraint_name;

SELECT
    descriptor_name AS table_name,
    index_name,
    is_unique,
    is_inverted,
    is_partial,
    index_definition
FROM crdb_internal.table_indexes
WHERE descriptor_name IN (
    'trnm_schema_migrations',
    'trnm_entity_heads',
    'trnm_command_receipts',
    'trnm_events',
    'trnm_outbox'
)
ORDER BY table_name, index_name;
