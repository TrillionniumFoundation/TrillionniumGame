-- Storage-only database-clock fields. Historical NULL means unknown.
-- The shared runner executes these declared actions and metadata publication
-- in one PostgreSQL transaction. Direct SQL application preserves that fence.
BEGIN;

-- trnm:action metadata_chain_digest
ALTER TABLE trnm_schema_metadata ADD COLUMN chain_digest TEXT;
-- trnm:action metadata_digest_algorithm
ALTER TABLE trnm_schema_metadata ADD COLUMN digest_algorithm TEXT;
-- trnm:action metadata_storage_writer_epoch
ALTER TABLE trnm_schema_metadata ADD COLUMN storage_writer_epoch BIGINT;
-- trnm:action metadata_upgrade_source_commit
ALTER TABLE trnm_schema_metadata ADD COLUMN upgrade_source_commit TEXT;
-- trnm:action storage_create_time
ALTER TABLE trnm_storage_objects ADD COLUMN create_time TIMESTAMPTZ;
-- trnm:action storage_update_time
ALTER TABLE trnm_storage_objects ADD COLUMN update_time TIMESTAMPTZ;

COMMIT;
