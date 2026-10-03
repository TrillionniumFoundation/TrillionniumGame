-- Native JSONB is the sole value authority; old bytes are an optional witness.
-- Only the typed trnm-schema runner may apply this revision. It validates all
-- legacy rows before DDL, runs the bounded backfill directive, then publishes
-- metadata last. No raw SQL execution of this file grants serve readiness.

-- trnm:action metadata_v2_apply_source_commit
ALTER TABLE trnm_schema_metadata ADD COLUMN v2_apply_source_commit TEXT;
-- trnm:action storage_value_jsonb
ALTER TABLE trnm_storage_objects ADD COLUMN value_jsonb JSONB;
-- trnm:action storage_public_version
ALTER TABLE trnm_storage_objects ADD COLUMN public_version VARCHAR(32);
-- trnm:action storage_value_projection_digest
ALTER TABLE trnm_storage_objects ADD COLUMN value_projection_digest BYTEA;
-- trnm:action storage_value_origin
ALTER TABLE trnm_storage_objects ADD COLUMN value_origin TEXT;
-- trnm:action storage_source_manifest_digest
ALTER TABLE trnm_storage_objects ADD COLUMN source_manifest_digest BYTEA;
-- trnm:action storage_native_backfill
-- trnm:backfill storage_jsonb_v3
-- trnm:action storage_value_bytes_optional
ALTER TABLE trnm_storage_objects ALTER COLUMN value_bytes DROP NOT NULL;
-- trnm:action storage_version_digest_optional
ALTER TABLE trnm_storage_objects ALTER COLUMN version_digest DROP NOT NULL;
-- trnm:action storage_value_jsonb_required
ALTER TABLE trnm_storage_objects ALTER COLUMN value_jsonb SET NOT NULL;
-- trnm:action storage_public_version_required
ALTER TABLE trnm_storage_objects ALTER COLUMN public_version SET NOT NULL;
-- trnm:action storage_value_projection_digest_required
ALTER TABLE trnm_storage_objects ALTER COLUMN value_projection_digest SET NOT NULL;
-- trnm:action storage_value_origin_required
ALTER TABLE trnm_storage_objects ALTER COLUMN value_origin SET NOT NULL;
-- trnm:action storage_projection_digest
ALTER TABLE trnm_storage_objects ADD CONSTRAINT storage_projection_digest CHECK (octet_length(value_projection_digest) = 32);
-- trnm:action storage_origin_witness
ALTER TABLE trnm_storage_objects ADD CONSTRAINT storage_origin_witness CHECK (((value_origin = 'legacy-rust-v2-bytes' OR value_origin = 'write-request-bytes') AND value_bytes IS NOT NULL AND version_digest IS NOT NULL AND octet_length(version_digest) = 32 AND source_manifest_digest IS NULL) OR (value_origin = 'nakama-export-unknown-request' AND value_bytes IS NULL AND version_digest IS NULL AND source_manifest_digest IS NOT NULL AND octet_length(source_manifest_digest) = 32 AND source_manifest_digest <> decode(repeat('0',64),'hex')));
-- trnm:action metadata_v2_history
ALTER TABLE trnm_schema_metadata ADD CONSTRAINT metadata_v2_history CHECK (schema_version < 3 OR (v2_apply_source_commit IS NOT NULL AND length(v2_apply_source_commit) = 40));
