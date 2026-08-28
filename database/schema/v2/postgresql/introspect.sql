-- Stable, machine-readable PostgreSQL schema inventory.

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
    relation.relname AS table_name,
    constraint_row.conname AS constraint_name,
    constraint_row.contype AS constraint_type,
    pg_get_constraintdef(constraint_row.oid, true) AS definition
FROM pg_constraint AS constraint_row
JOIN pg_class AS relation ON relation.oid = constraint_row.conrelid
JOIN pg_namespace AS namespace_row ON namespace_row.oid = relation.relnamespace
WHERE namespace_row.nspname = current_schema()
  AND relation.relname IN (
      'trnm_schema_migrations',
      'trnm_entity_heads',
      'trnm_command_receipts',
      'trnm_events',
      'trnm_outbox'
  )
ORDER BY table_name, constraint_name;

SELECT
    tablename AS table_name,
    indexname AS index_name,
    indexdef AS definition
FROM pg_indexes
WHERE schemaname = current_schema()
  AND tablename IN (
      'trnm_schema_migrations',
      'trnm_entity_heads',
      'trnm_command_receipts',
      'trnm_events',
      'trnm_outbox'
  )
ORDER BY table_name, index_name;
