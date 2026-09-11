\set ON_ERROR_STOP on
\pset tuples_only on
\pset format unaligned

SELECT 'column|' || table_name || '|' || lpad(ordinal_position::text, 4, '0') || '|' ||
       column_name || '|' || data_type || '|' || is_nullable || '|' ||
       coalesce(column_default, '')
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name LIKE 'trnm\_%' ESCAPE '\'
UNION ALL
SELECT 'constraint|' || c.conrelid::regclass::text || '|' || c.conname || '|' ||
       c.contype || '|' || pg_get_constraintdef(c.oid, true)
FROM pg_constraint AS c
WHERE c.connamespace = 'public'::regnamespace
  AND c.conrelid::regclass::text LIKE 'trnm\_%' ESCAPE '\'
UNION ALL
SELECT 'index|' || tablename || '|' || indexname || '|' || indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename LIKE 'trnm\_%' ESCAPE '\'
ORDER BY 1;
