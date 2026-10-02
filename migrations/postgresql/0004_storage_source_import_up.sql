-- Storage source import ABI v4. Immutable prior files stay unchanged.
-- DDL is executed only by the closed typed runner; metadata is published last.
BEGIN;

-- trnm:action metadata_v3_apply_source_commit
ALTER TABLE trnm_schema_metadata ADD COLUMN v3_apply_source_commit TEXT;

-- trnm:action storage_collection_check_v4
ALTER TABLE trnm_storage_objects DROP CONSTRAINT trnm_storage_objects_collection_check, ADD CONSTRAINT trnm_storage_objects_collection_check CHECK (length(collection) BETWEEN 0 AND 128);

-- trnm:action storage_object_key_check_v4
ALTER TABLE trnm_storage_objects DROP CONSTRAINT trnm_storage_objects_object_key_check, ADD CONSTRAINT trnm_storage_objects_object_key_check CHECK (length(object_key) BETWEEN 0 AND 128);

-- trnm:action storage_read_permission_check_v4
ALTER TABLE trnm_storage_objects DROP CONSTRAINT trnm_storage_objects_read_permission_check, ADD CONSTRAINT trnm_storage_objects_read_permission_check CHECK (read_permission >= 0);

-- trnm:action storage_write_permission_check_v4
ALTER TABLE trnm_storage_objects DROP CONSTRAINT trnm_storage_objects_write_permission_check, ADD CONSTRAINT trnm_storage_objects_write_permission_check CHECK (write_permission >= 0);

-- trnm:action storage_import_jobs
CREATE TABLE trnm_storage_import_jobs (
    singleton SMALLINT NOT NULL,
    manifest_digest BYTEA NOT NULL,
    custody_digest BYTEA NOT NULL,
    source_inventory_digest BYTEA NOT NULL,
    target_schema_guard_digest BYTEA NOT NULL,
    prefix_digest BYTEA NOT NULL,
    source_profile TEXT NOT NULL,
    source_snapshot TEXT NOT NULL,
    audit_at_ms BIGINT NOT NULL,
    total_rows BIGINT NOT NULL,
    total_pages BIGINT NOT NULL,
    next_page BIGINT NOT NULL,
    committed_rows BIGINT NOT NULL,
    status SMALLINT NOT NULL,
    CONSTRAINT storage_import_jobs_pk PRIMARY KEY (singleton),
    CONSTRAINT storage_import_jobs_manifest_key UNIQUE (manifest_digest),
    CONSTRAINT storage_import_jobs_singleton CHECK (singleton = 1),
    CONSTRAINT storage_import_jobs_digests CHECK (octet_length(manifest_digest) = 32 AND octet_length(custody_digest) = 32 AND octet_length(source_inventory_digest) = 32 AND octet_length(target_schema_guard_digest) = 32 AND octet_length(prefix_digest) = 32 AND manifest_digest <> decode(repeat('0', 64), 'hex') AND custody_digest <> decode(repeat('0', 64), 'hex') AND source_inventory_digest <> decode(repeat('0', 64), 'hex') AND target_schema_guard_digest <> decode(repeat('0', 64), 'hex') AND prefix_digest <> decode(repeat('0', 64), 'hex')),
    CONSTRAINT storage_import_jobs_source CHECK ((source_profile = 'postgresql' OR source_profile = 'cockroachdb') AND length(source_snapshot) BETWEEN 1 AND 256 AND source_snapshot !~ '[[:cntrl:]]'),
    CONSTRAINT storage_import_jobs_audit CHECK (audit_at_ms >= 0),
    CONSTRAINT storage_import_jobs_counts CHECK (total_rows BETWEEN 0 AND 10000 AND total_pages BETWEEN 0 AND 100 AND next_page BETWEEN 0 AND total_pages AND committed_rows BETWEEN 0 AND total_rows AND ((total_rows = 0 AND total_pages = 0) OR (total_pages > 0 AND total_pages <= total_rows AND total_rows <= 100 * total_pages)) AND committed_rows BETWEEN next_page AND 100 * next_page),
    CONSTRAINT storage_import_jobs_status CHECK (status IN (0, 1) AND (status <> 1 OR (next_page = total_pages AND committed_rows = total_rows)))
);

-- trnm:action storage_import_pages
CREATE TABLE trnm_storage_import_pages (
    manifest_digest BYTEA NOT NULL,
    page_index BIGINT NOT NULL,
    first_ordinal BIGINT NOT NULL,
    row_count BIGINT NOT NULL,
    page_digest BYTEA NOT NULL,
    prefix_digest BYTEA NOT NULL,
    audit_at_ms BIGINT NOT NULL,
    CONSTRAINT storage_import_pages_pk PRIMARY KEY (manifest_digest, page_index),
    CONSTRAINT storage_import_pages_job_fk FOREIGN KEY (manifest_digest) REFERENCES trnm_storage_import_jobs (manifest_digest) ON UPDATE NO ACTION ON DELETE RESTRICT,
    CONSTRAINT storage_import_pages_digests CHECK (octet_length(manifest_digest) = 32 AND octet_length(page_digest) = 32 AND octet_length(prefix_digest) = 32 AND manifest_digest <> decode(repeat('0', 64), 'hex') AND page_digest <> decode(repeat('0', 64), 'hex') AND prefix_digest <> decode(repeat('0', 64), 'hex')),
    CONSTRAINT storage_import_pages_bounds CHECK (page_index BETWEEN 0 AND 99 AND first_ordinal BETWEEN 0 AND 9999 AND row_count BETWEEN 1 AND 100 AND first_ordinal + row_count <= 10000),
    CONSTRAINT storage_import_pages_audit CHECK (audit_at_ms >= 0)
);

-- trnm:action metadata_v3_history
ALTER TABLE trnm_schema_metadata ADD CONSTRAINT metadata_v3_history CHECK (schema_version < 4 OR (v3_apply_source_commit IS NOT NULL AND length(v3_apply_source_commit) = 40));

COMMIT;
