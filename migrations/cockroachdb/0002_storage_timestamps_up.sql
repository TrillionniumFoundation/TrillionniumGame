-- Storage-only database-clock fields. Historical NULL means unknown.
-- Each declared action is independently committed. The shared runner verifies
-- the exact completed prefix and publishes ready metadata only after all six.

-- trnm:action metadata_chain_digest
ALTER TABLE trnm_schema_metadata ADD COLUMN chain_digest STRING;
-- trnm:action metadata_digest_algorithm
ALTER TABLE trnm_schema_metadata ADD COLUMN digest_algorithm STRING;
-- trnm:action metadata_storage_writer_epoch
ALTER TABLE trnm_schema_metadata ADD COLUMN storage_writer_epoch INT8;
-- trnm:action metadata_upgrade_source_commit
ALTER TABLE trnm_schema_metadata ADD COLUMN upgrade_source_commit STRING;
-- trnm:action storage_create_time
ALTER TABLE trnm_storage_objects ADD COLUMN create_time TIMESTAMPTZ;
-- trnm:action storage_update_time
ALTER TABLE trnm_storage_objects ADD COLUMN update_time TIMESTAMPTZ;
