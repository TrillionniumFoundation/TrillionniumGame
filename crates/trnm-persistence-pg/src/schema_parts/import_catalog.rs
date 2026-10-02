// Exact PostgreSQL17/CockroachDB26 native catalog observations for frozen0004.
// Fixture OIDs/database names are not portable identities. No semantic normalization.
fn import_catalog_objects(profile: DatabaseProfile, table: &str) -> Vec<ColumnDescriptor> {
    let mut result = Vec::new();
    match (profile, table) {
        (DatabaseProfile::CockroachDb, "trnm_storage_import_jobs") => {
            result.push(ColumnDescriptor {
                table: "trnm_storage_import_jobs",
                name: "@table",
                kind: "BASE TABLE",
                nullable: false,
                default_zero: false,
                character_maximum_length: None,
            });
            result.push(ColumnDescriptor {
                table: "trnm_storage_import_jobs",
                name: "@constraint:storage_import_jobs_audit",
                kind: "c|true|[9]|None|None|None|None|None|CHECK ((audit_at_ms >= 0))",
                nullable: false,
                default_zero: false,
                character_maximum_length: None,
            });
            result.push(ColumnDescriptor { table: "trnm_storage_import_jobs", name: "@constraint:storage_import_jobs_counts", kind: "c|true|[10, 11, 12, 13]|None|None|None|None|None|CHECK (((((((total_rows BETWEEN 0 AND 10000) AND (total_pages BETWEEN 0 AND 100)) AND (next_page BETWEEN 0 AND total_pages)) AND (committed_rows BETWEEN 0 AND total_rows)) AND (((total_rows = 0) AND (total_pages = 0)) OR (((total_pages > 0) AND (total_pages <= total_rows)) AND (total_rows <= (100 * total_pages))))) AND (committed_rows BETWEEN next_page AND (100 * next_page))))", nullable: false, default_zero: false, character_maximum_length: None });
            result.push(ColumnDescriptor { table: "trnm_storage_import_jobs", name: "@constraint:storage_import_jobs_digests", kind: "c|true|[2, 3, 4, 5, 6]|None|None|None|None|None|CHECK (((((((((((octet_length(manifest_digest) = 32) AND (octet_length(custody_digest) = 32)) AND (octet_length(source_inventory_digest) = 32)) AND (octet_length(target_schema_guard_digest) = 32)) AND (octet_length(prefix_digest) = 32)) AND (manifest_digest != decode(repeat('0'::STRING, 64), 'hex'::STRING))) AND (custody_digest != decode(repeat('0'::STRING, 64), 'hex'::STRING))) AND (source_inventory_digest != decode(repeat('0'::STRING, 64), 'hex'::STRING))) AND (target_schema_guard_digest != decode(repeat('0'::STRING, 64), 'hex'::STRING))) AND (prefix_digest != decode(repeat('0'::STRING, 64), 'hex'::STRING))))", nullable: false, default_zero: false, character_maximum_length: None });
            result.push(ColumnDescriptor {
                table: "trnm_storage_import_jobs",
                name: "@constraint:storage_import_jobs_manifest_key",
                kind: "u|true|[2]|None|None|None|None|None|UNIQUE (manifest_digest ASC)",
                nullable: false,
                default_zero: false,
                character_maximum_length: None,
            });
            result.push(ColumnDescriptor {
                table: "trnm_storage_import_jobs",
                name: "@constraint:storage_import_jobs_pk",
                kind: "p|true|[1]|None|None|None|None|None|PRIMARY KEY (singleton ASC)",
                nullable: false,
                default_zero: false,
                character_maximum_length: None,
            });
            result.push(ColumnDescriptor {
                table: "trnm_storage_import_jobs",
                name: "@constraint:storage_import_jobs_singleton",
                kind: "c|true|[1]|None|None|None|None|None|CHECK ((singleton = 1))",
                nullable: false,
                default_zero: false,
                character_maximum_length: None,
            });
            result.push(ColumnDescriptor { table: "trnm_storage_import_jobs", name: "@constraint:storage_import_jobs_source", kind: "c|true|[7, 8]|None|None|None|None|None|CHECK (((((source_profile = 'postgresql'::STRING) OR (source_profile = 'cockroachdb'::STRING)) AND (length(source_snapshot) BETWEEN 1 AND 256)) AND (source_snapshot !~ '[[:cntrl:]]'::STRING)))", nullable: false, default_zero: false, character_maximum_length: None });
            result.push(ColumnDescriptor { table: "trnm_storage_import_jobs", name: "@constraint:storage_import_jobs_status", kind: "c|true|[10, 11, 12, 13, 14]|None|None|None|None|None|CHECK (((status IN (0, 1)) AND ((status != 1) OR ((next_page = total_pages) AND (committed_rows = total_rows)))))", nullable: false, default_zero: false, character_maximum_length: None });
            result.push(ColumnDescriptor { table: "trnm_storage_import_jobs", name: "@index:storage_import_jobs_manifest_key", kind: "true|false|true|false|None|CREATE UNIQUE INDEX storage_import_jobs_manifest_key ON {database}.public.trnm_storage_import_jobs USING btree (manifest_digest ASC)", nullable: false, default_zero: false, character_maximum_length: None });
            result.push(ColumnDescriptor { table: "trnm_storage_import_jobs", name: "@index:storage_import_jobs_pk", kind: "true|true|true|false|None|CREATE UNIQUE INDEX storage_import_jobs_pk ON {database}.public.trnm_storage_import_jobs USING btree (singleton ASC)", nullable: false, default_zero: false, character_maximum_length: None });
        }
        (DatabaseProfile::CockroachDb, "trnm_storage_import_pages") => {
            result.push(ColumnDescriptor {
                table: "trnm_storage_import_pages",
                name: "@table",
                kind: "BASE TABLE",
                nullable: false,
                default_zero: false,
                character_maximum_length: None,
            });
            result.push(ColumnDescriptor {
                table: "trnm_storage_import_pages",
                name: "@constraint:storage_import_pages_audit",
                kind: "c|true|[7]|None|None|None|None|None|CHECK ((audit_at_ms >= 0))",
                nullable: false,
                default_zero: false,
                character_maximum_length: None,
            });
            result.push(ColumnDescriptor { table: "trnm_storage_import_pages", name: "@constraint:storage_import_pages_bounds", kind: "c|true|[2, 3, 4]|None|None|None|None|None|CHECK (((((page_index BETWEEN 0 AND 99) AND (first_ordinal BETWEEN 0 AND 9999)) AND (row_count BETWEEN 1 AND 100)) AND ((first_ordinal + row_count) <= 10000)))", nullable: false, default_zero: false, character_maximum_length: None });
            result.push(ColumnDescriptor { table: "trnm_storage_import_pages", name: "@constraint:storage_import_pages_digests", kind: "c|true|[1, 5, 6]|None|None|None|None|None|CHECK (((((((octet_length(manifest_digest) = 32) AND (octet_length(page_digest) = 32)) AND (octet_length(prefix_digest) = 32)) AND (manifest_digest != decode(repeat('0'::STRING, 64), 'hex'::STRING))) AND (page_digest != decode(repeat('0'::STRING, 64), 'hex'::STRING))) AND (prefix_digest != decode(repeat('0'::STRING, 64), 'hex'::STRING))))", nullable: false, default_zero: false, character_maximum_length: None });
            result.push(ColumnDescriptor { table: "trnm_storage_import_pages", name: "@constraint:storage_import_pages_job_fk", kind: "f|true|[1]|Some([2])|Some(\"a\")|Some(\"r\")|Some(\"public\")|Some(\"trnm_storage_import_jobs\")|FOREIGN KEY (manifest_digest) REFERENCES trnm_storage_import_jobs(manifest_digest) ON DELETE RESTRICT", nullable: false, default_zero: false, character_maximum_length: None });
            result.push(ColumnDescriptor { table: "trnm_storage_import_pages", name: "@constraint:storage_import_pages_pk", kind: "p|true|[1, 2]|None|None|None|None|None|PRIMARY KEY (manifest_digest ASC, page_index ASC)", nullable: false, default_zero: false, character_maximum_length: None });
            result.push(ColumnDescriptor { table: "trnm_storage_import_pages", name: "@index:storage_import_pages_pk", kind: "true|true|true|false|None|CREATE UNIQUE INDEX storage_import_pages_pk ON {database}.public.trnm_storage_import_pages USING btree (manifest_digest ASC, page_index ASC)", nullable: false, default_zero: false, character_maximum_length: None });
        }
        (DatabaseProfile::PostgreSql, "trnm_storage_import_jobs") => {
            result.push(ColumnDescriptor {
                table: "trnm_storage_import_jobs",
                name: "@table",
                kind: "BASE TABLE",
                nullable: false,
                default_zero: false,
                character_maximum_length: None,
            });
            result.push(ColumnDescriptor {
                table: "trnm_storage_import_jobs",
                name: "@constraint:storage_import_jobs_audit",
                kind:
                    "c|true|[9]|None|Some(\" \")|Some(\" \")|None|None|CHECK ((audit_at_ms >= 0))",
                nullable: false,
                default_zero: false,
                character_maximum_length: None,
            });
            result.push(ColumnDescriptor { table: "trnm_storage_import_jobs", name: "@constraint:storage_import_jobs_counts", kind: "c|true|[10, 11, 12, 13]|None|Some(\" \")|Some(\" \")|None|None|CHECK ((((total_rows >= 0) AND (total_rows <= 10000)) AND ((total_pages >= 0) AND (total_pages <= 100)) AND ((next_page >= 0) AND (next_page <= total_pages)) AND ((committed_rows >= 0) AND (committed_rows <= total_rows)) AND (((total_rows = 0) AND (total_pages = 0)) OR ((total_pages > 0) AND (total_pages <= total_rows) AND (total_rows <= (100 * total_pages)))) AND ((committed_rows >= next_page) AND (committed_rows <= (100 * next_page)))))", nullable: false, default_zero: false, character_maximum_length: None });
            result.push(ColumnDescriptor { table: "trnm_storage_import_jobs", name: "@constraint:storage_import_jobs_digests", kind: "c|true|[2, 3, 4, 5, 6]|None|Some(\" \")|Some(\" \")|None|None|CHECK (((octet_length(manifest_digest) = 32) AND (octet_length(custody_digest) = 32) AND (octet_length(source_inventory_digest) = 32) AND (octet_length(target_schema_guard_digest) = 32) AND (octet_length(prefix_digest) = 32) AND (manifest_digest <> decode(repeat('0'::text, 64), 'hex'::text)) AND (custody_digest <> decode(repeat('0'::text, 64), 'hex'::text)) AND (source_inventory_digest <> decode(repeat('0'::text, 64), 'hex'::text)) AND (target_schema_guard_digest <> decode(repeat('0'::text, 64), 'hex'::text)) AND (prefix_digest <> decode(repeat('0'::text, 64), 'hex'::text))))", nullable: false, default_zero: false, character_maximum_length: None });
            result.push(ColumnDescriptor {
                table: "trnm_storage_import_jobs",
                name: "@constraint:storage_import_jobs_manifest_key",
                kind: "u|true|[2]|None|Some(\" \")|Some(\" \")|None|None|UNIQUE (manifest_digest)",
                nullable: false,
                default_zero: false,
                character_maximum_length: None,
            });
            result.push(ColumnDescriptor {
                table: "trnm_storage_import_jobs",
                name: "@constraint:storage_import_jobs_pk",
                kind: "p|true|[1]|None|Some(\" \")|Some(\" \")|None|None|PRIMARY KEY (singleton)",
                nullable: false,
                default_zero: false,
                character_maximum_length: None,
            });
            result.push(ColumnDescriptor {
                table: "trnm_storage_import_jobs",
                name: "@constraint:storage_import_jobs_singleton",
                kind: "c|true|[1]|None|Some(\" \")|Some(\" \")|None|None|CHECK ((singleton = 1))",
                nullable: false,
                default_zero: false,
                character_maximum_length: None,
            });
            result.push(ColumnDescriptor { table: "trnm_storage_import_jobs", name: "@constraint:storage_import_jobs_source", kind: "c|true|[7, 8]|None|Some(\" \")|Some(\" \")|None|None|CHECK ((((source_profile = 'postgresql'::text) OR (source_profile = 'cockroachdb'::text)) AND ((length(source_snapshot) >= 1) AND (length(source_snapshot) <= 256)) AND (source_snapshot !~ '[[:cntrl:]]'::text)))", nullable: false, default_zero: false, character_maximum_length: None });
            result.push(ColumnDescriptor { table: "trnm_storage_import_jobs", name: "@constraint:storage_import_jobs_status", kind: "c|true|[14, 12, 11, 13, 10]|None|Some(\" \")|Some(\" \")|None|None|CHECK (((status = ANY (ARRAY[0, 1])) AND ((status <> 1) OR ((next_page = total_pages) AND (committed_rows = total_rows)))))", nullable: false, default_zero: false, character_maximum_length: None });
            result.push(ColumnDescriptor { table: "trnm_storage_import_jobs", name: "@index:storage_import_jobs_manifest_key", kind: "true|false|true|true|None|CREATE UNIQUE INDEX storage_import_jobs_manifest_key ON public.trnm_storage_import_jobs USING btree (manifest_digest)", nullable: false, default_zero: false, character_maximum_length: None });
            result.push(ColumnDescriptor { table: "trnm_storage_import_jobs", name: "@index:storage_import_jobs_pk", kind: "true|true|true|true|None|CREATE UNIQUE INDEX storage_import_jobs_pk ON public.trnm_storage_import_jobs USING btree (singleton)", nullable: false, default_zero: false, character_maximum_length: None });
        }
        (DatabaseProfile::PostgreSql, "trnm_storage_import_pages") => {
            result.push(ColumnDescriptor {
                table: "trnm_storage_import_pages",
                name: "@table",
                kind: "BASE TABLE",
                nullable: false,
                default_zero: false,
                character_maximum_length: None,
            });
            result.push(ColumnDescriptor {
                table: "trnm_storage_import_pages",
                name: "@constraint:storage_import_pages_audit",
                kind:
                    "c|true|[7]|None|Some(\" \")|Some(\" \")|None|None|CHECK ((audit_at_ms >= 0))",
                nullable: false,
                default_zero: false,
                character_maximum_length: None,
            });
            result.push(ColumnDescriptor { table: "trnm_storage_import_pages", name: "@constraint:storage_import_pages_bounds", kind: "c|true|[2, 3, 4]|None|Some(\" \")|Some(\" \")|None|None|CHECK ((((page_index >= 0) AND (page_index <= 99)) AND ((first_ordinal >= 0) AND (first_ordinal <= 9999)) AND ((row_count >= 1) AND (row_count <= 100)) AND ((first_ordinal + row_count) <= 10000)))", nullable: false, default_zero: false, character_maximum_length: None });
            result.push(ColumnDescriptor { table: "trnm_storage_import_pages", name: "@constraint:storage_import_pages_digests", kind: "c|true|[1, 5, 6]|None|Some(\" \")|Some(\" \")|None|None|CHECK (((octet_length(manifest_digest) = 32) AND (octet_length(page_digest) = 32) AND (octet_length(prefix_digest) = 32) AND (manifest_digest <> decode(repeat('0'::text, 64), 'hex'::text)) AND (page_digest <> decode(repeat('0'::text, 64), 'hex'::text)) AND (prefix_digest <> decode(repeat('0'::text, 64), 'hex'::text))))", nullable: false, default_zero: false, character_maximum_length: None });
            result.push(ColumnDescriptor { table: "trnm_storage_import_pages", name: "@constraint:storage_import_pages_job_fk", kind: "f|true|[1]|Some([2])|Some(\"a\")|Some(\"r\")|Some(\"public\")|Some(\"trnm_storage_import_jobs\")|FOREIGN KEY (manifest_digest) REFERENCES trnm_storage_import_jobs(manifest_digest) ON DELETE RESTRICT", nullable: false, default_zero: false, character_maximum_length: None });
            result.push(ColumnDescriptor { table: "trnm_storage_import_pages", name: "@constraint:storage_import_pages_pk", kind: "p|true|[1, 2]|None|Some(\" \")|Some(\" \")|None|None|PRIMARY KEY (manifest_digest, page_index)", nullable: false, default_zero: false, character_maximum_length: None });
            result.push(ColumnDescriptor { table: "trnm_storage_import_pages", name: "@index:storage_import_pages_pk", kind: "true|true|true|true|None|CREATE UNIQUE INDEX storage_import_pages_pk ON public.trnm_storage_import_pages USING btree (manifest_digest, page_index)", nullable: false, default_zero: false, character_maximum_length: None });
        }
        _ => unreachable!("closed import journal table"),
    }
    result
}

fn old_storage_domain_checks(profile: DatabaseProfile) -> Vec<ColumnDescriptor> {
    let mut result = Vec::new();
    for (column, pg, cr) in [
        (
            "collection",
            "CHECK (((length(collection) >= 1) AND (length(collection) <= 128)))",
            "CHECK ((length(collection) BETWEEN 1 AND 128))",
        ),
        (
            "object_key",
            "CHECK (((length(object_key) >= 1) AND (length(object_key) <= 128)))",
            "CHECK ((length(object_key) BETWEEN 1 AND 128))",
        ),
        (
            "read_permission",
            "CHECK (((read_permission >= 0) AND (read_permission <= 2)))",
            "CHECK ((read_permission BETWEEN 0 AND 2))",
        ),
        (
            "write_permission",
            "CHECK (((write_permission >= 0) AND (write_permission <= 1)))",
            "CHECK ((write_permission BETWEEN 0 AND 1))",
        ),
    ] {
        let name = storage_domain_check_name(profile, column);
        result.push(ColumnDescriptor {
            table: "trnm_storage_objects",
            name,
            kind: if profile == DatabaseProfile::PostgreSql {
                pg
            } else {
                cr
            },
            nullable: false,
            default_zero: false,
            character_maximum_length: None,
        });
    }
    result
}

fn storage_domain_check_name(profile: DatabaseProfile, column: &str) -> &'static str {
    match (profile, column) {
        (DatabaseProfile::PostgreSql, "collection") => {
            "@check:trnm_storage_objects_collection_check"
        }
        (DatabaseProfile::PostgreSql, "object_key") => {
            "@check:trnm_storage_objects_object_key_check"
        }
        (DatabaseProfile::PostgreSql, "read_permission") => {
            "@check:trnm_storage_objects_read_permission_check"
        }
        (DatabaseProfile::PostgreSql, "write_permission") => {
            "@check:trnm_storage_objects_write_permission_check"
        }
        (DatabaseProfile::CockroachDb, "collection") => "@check:check_collection",
        (DatabaseProfile::CockroachDb, "object_key") => "@check:check_object_key",
        (DatabaseProfile::CockroachDb, "read_permission") => "@check:check_read_permission",
        (DatabaseProfile::CockroachDb, "write_permission") => "@check:check_write_permission",
        _ => unreachable!("closed storage domain check"),
    }
}

fn storage_check_key_descriptor(name: &str) -> ColumnDescriptor {
    let (name, keys) = match name {
        "@check:trnm_storage_objects_collection_check" => {
            ("@checkkeys:trnm_storage_objects_collection_check", "[1]")
        }
        "@check:trnm_storage_objects_object_key_check" => {
            ("@checkkeys:trnm_storage_objects_object_key_check", "[2]")
        }
        "@check:trnm_storage_objects_read_permission_check" => (
            "@checkkeys:trnm_storage_objects_read_permission_check",
            "[6]",
        ),
        "@check:trnm_storage_objects_write_permission_check" => (
            "@checkkeys:trnm_storage_objects_write_permission_check",
            "[7]",
        ),
        "@check:check_collection" => ("@checkkeys:check_collection", "[1]"),
        "@check:check_object_key" => ("@checkkeys:check_object_key", "[2]"),
        "@check:check_read_permission" => ("@checkkeys:check_read_permission", "[6]"),
        "@check:check_write_permission" => ("@checkkeys:check_write_permission", "[7]"),
        _ => unreachable!("closed storage check key"),
    };
    ColumnDescriptor {
        table: "trnm_storage_objects",
        name,
        kind: keys,
        nullable: false,
        default_zero: false,
        character_maximum_length: None,
    }
}
