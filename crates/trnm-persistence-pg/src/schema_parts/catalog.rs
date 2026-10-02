#[derive(Clone, Debug)]
struct CatalogColumn {
    kind: String,
    nullable: bool,
    default: Option<String>,
    character_maximum_length: Option<i64>,
}

type Catalog = BTreeMap<(String, String), CatalogColumn>;

fn read_catalog(client: &mut impl GenericClient) -> Result<Catalog, DomainError> {
    let tables = client
        .query(
            "SELECT table_name, table_type FROM information_schema.tables \
         WHERE table_schema = 'public' AND left(table_name, 5) = 'trnm_'",
            &[],
        )
        .map_err(map_postgres_error)?;
    if !tables.is_empty() {
        if tables.len() != REQUIRED_TABLES.len() {
            return Err(failed_precondition(
                "authoritative_schema_table_inventory_drift",
            ));
        }
        for row in &tables {
            let table: String = row.try_get(0).map_err(map_postgres_error)?;
            let kind: String = row.try_get(1).map_err(map_postgres_error)?;
            if !REQUIRED_TABLES.contains(&table.as_str()) || kind != "BASE TABLE" {
                return Err(failed_precondition("authoritative_schema_table_kind_drift"));
            }
        }
    }
    let rows = client.query(
        "SELECT table_name, column_name, udt_name, is_nullable, column_default, character_maximum_length::BIGINT \
         FROM information_schema.columns \
         WHERE table_schema = 'public' AND left(table_name, 5) = 'trnm_' \
         ORDER BY table_name, ordinal_position", &[]).map_err(map_postgres_error)?;
    if rows.len() > 256 {
        return Err(failed_precondition("authoritative_schema_catalog_budget"));
    }
    let mut catalog = BTreeMap::new();
    for row in rows {
        let table: String = row.try_get(0).map_err(map_postgres_error)?;
        let name: String = row.try_get(1).map_err(map_postgres_error)?;
        let kind = row.try_get(2).map_err(map_postgres_error)?;
        let nullable: String = row.try_get(3).map_err(map_postgres_error)?;
        let default = row.try_get(4).map_err(map_postgres_error)?;
        let character_maximum_length = row.try_get(5).map_err(map_postgres_error)?;
        if !matches!(nullable.as_str(), "YES" | "NO") {
            return Err(data_loss("schema_catalog_nullability_invalid"));
        }
        if catalog
            .insert(
                (table, name),
                CatalogColumn {
                    kind,
                    nullable: nullable == "YES",
                    default,
                    character_maximum_length,
                },
            )
            .is_some()
        {
            return Err(data_loss("schema_catalog_duplicate_column"));
        }
    }
    if !catalog.is_empty() {
        verify_primary_keys(client)?;
        let checks = client.query("SELECT t.relname, c.conname, c.convalidated, pg_catalog.pg_get_constraintdef(c.oid) \
            FROM pg_catalog.pg_constraint c JOIN pg_catalog.pg_class t ON t.oid=c.conrelid \
            JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace \
            WHERE n.nspname='public' AND c.conname IN ('storage_projection_digest','storage_origin_witness','metadata_v2_history')", &[]).map_err(map_postgres_error)?;
        for row in checks {
            let table: String = row.try_get(0).map_err(map_postgres_error)?;
            let name: String = row.try_get(1).map_err(map_postgres_error)?;
            let validated: bool = row.try_get(2).map_err(map_postgres_error)?;
            let definition: String = row.try_get(3).map_err(map_postgres_error)?;
            if !validated
                || catalog
                    .insert(
                        (table, format!("@check:{name}")),
                        CatalogColumn {
                            kind: definition,
                            nullable: false,
                            default: None,
                            character_maximum_length: None,
                        },
                    )
                    .is_some()
            {
                return Err(failed_precondition("authoritative_schema_check_drift"));
            }
        }
        if [
            "value_jsonb",
            "public_version",
            "value_projection_digest",
            "value_origin",
            "source_manifest_digest",
        ]
        .iter()
        .all(|name| catalog.contains_key(&("trnm_storage_objects".to_owned(), (*name).to_owned())))
        {
            let row = client.query_one("SELECT NOT EXISTS (SELECT 1 FROM public.trnm_storage_objects WHERE value_jsonb IS NULL OR public_version IS NULL OR value_projection_digest IS NULL OR value_origin IS NULL LIMIT 1)", &[]).map_err(map_postgres_error)?;
            if row.try_get::<_, bool>(0).map_err(map_postgres_error)? {
                catalog.insert(
                    ("trnm_storage_objects".to_owned(), "@backfill".to_owned()),
                    CatalogColumn {
                        kind: "backfilled".to_owned(),
                        nullable: false,
                        default: None,
                        character_maximum_length: None,
                    },
                );
            }
        }
    }
    Ok(catalog)
}

fn matches_column(actual: &CatalogColumn, expected: ColumnDescriptor) -> bool {
    actual.kind == expected.kind
        && actual.nullable == expected.nullable
        && actual.character_maximum_length == expected.character_maximum_length
        && if expected.default_zero {
            actual.default.as_deref().is_some_and(zero_default)
        } else {
            actual.default.is_none()
        }
}

// PostgreSQL and CockroachDB spell their pinned zero cast differently.
// Accept only a numeric zero with an integer cast, not arbitrary expressions.
fn zero_default(value: &str) -> bool {
    let value: String = value.chars().filter(|c| !c.is_ascii_whitespace()).collect();
    matches!(
        value.as_str(),
        "0" | "0::bigint"
            | "0::smallint"
            | "0:::INT8"
            | "0:::INT2"
            | "'0'::bigint"
            | "'0'::smallint"
    )
}

fn catalog_prefix(catalog: &Catalog, profile: DatabaseProfile) -> Result<usize, DomainError> {
    let mut expected: BTreeMap<(&str, &str), ColumnDescriptor> = baseline(profile)
        .iter()
        .map(|column| ((column.table, column.name), *column))
        .collect();
    for prefix in 0..=actions(profile).len() {
        if expected.len() == catalog.len()
            && expected.iter().all(|((table, name), column)| {
                catalog
                    .get(&(table.to_string(), name.to_string()))
                    .is_some_and(|actual| matches_column(actual, *column))
            })
        {
            return Ok(prefix);
        }
        if let Some(action) = actions(profile).get(prefix) {
            match action.kind {
                MigrationActionKind::SetNullability => {
                    let old = expected
                        .get_mut(&(action.column.table, action.column.name))
                        .expect("reviewed prior column");
                    old.nullable = action.column.nullable;
                }
                _ => {
                    expected.insert((action.column.table, action.column.name), action.column);
                }
            }
        }
    }
    Err(failed_precondition(
        "authoritative_schema_partial_action_drift",
    ))
}

fn verify_primary_keys(client: &mut impl GenericClient) -> Result<(), DomainError> {
    // information_schema.table_constraints hides keys from SELECT-only roles.
    // Native catalogs permit a genuinely read-only readiness client to inspect
    // the same exact primary-key shapes without receiving mutation privileges.
    let rows=client.query("SELECT t.relname,pg_catalog.pg_get_constraintdef(c.oid) FROM pg_catalog.pg_constraint c \
        JOIN pg_catalog.pg_class t ON t.oid=c.conrelid JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace \
        WHERE n.nspname='public' AND left(t.relname,5)='trnm_' AND c.contype='p'",&[]).map_err(map_postgres_error)?;
    if rows.len() != REQUIRED_TABLES.len() {
        return Err(failed_precondition(
            "authoritative_schema_primary_key_drift",
        ));
    }
    let expected: BTreeMap<&str, Vec<&str>> = [
        ("trnm_schema_metadata", vec!["singleton"]),
        ("trnm_entity_heads", vec!["entity_id"]),
        ("trnm_command_receipts", vec!["entity_id", "command_id"]),
        ("trnm_events", vec!["entity_id", "sequence"]),
        ("trnm_outbox", vec!["intent_id"]),
        (
            "trnm_command_outbox",
            vec!["entity_id", "command_id", "\"position\""],
        ),
        ("trnm_authority_leases", vec!["entity_id"]),
        ("trnm_session_families", vec!["family_id"]),
        ("trnm_refresh_tokens", vec!["family_id", "token_id"]),
        (
            "trnm_storage_objects",
            vec!["collection", "object_key", "user_id"],
        ),
    ]
    .into_iter()
    .collect();
    let mut seen = std::collections::BTreeSet::new();
    for row in rows {
        let table: String = row.try_get(0).map_err(map_postgres_error)?;
        let definition: String = row.try_get(1).map_err(map_postgres_error)?;
        let columns = expected
            .get(table.as_str())
            .ok_or_else(|| failed_precondition("authoritative_schema_primary_key_drift"))?;
        let pg = format!("PRIMARY KEY ({})", columns.join(", "));
        let cr = format!(
            "PRIMARY KEY ({})",
            columns
                .iter()
                .map(|column| format!("{column} ASC"))
                .collect::<Vec<_>>()
                .join(", ")
        );
        if !seen.insert(table) || (definition != pg && definition != cr) {
            return Err(failed_precondition(
                "authoritative_schema_primary_key_drift",
            ));
        }
    }
    Ok(())
}

fn business_data_empty(client: &mut impl GenericClient) -> Result<bool, DomainError> {
    for table in REQUIRED_TABLES
        .iter()
        .filter(|table| **table != "trnm_schema_metadata")
    {
        let row = client
            .query_one(
                &format!("SELECT EXISTS (SELECT 1 FROM public.{table} LIMIT 1)"),
                &[],
            )
            .map_err(map_postgres_error)?;
        if row.try_get::<_, bool>(0).map_err(map_postgres_error)? {
            return Ok(false);
        }
    }
    Ok(true)
}

fn validate_legacy_role(role: &str) -> Result<(), DomainError> {
    if role.is_empty()
        || role.len() > 63
        || matches!(
            role.to_ascii_lowercase().as_str(),
            "public" | "root" | "admin"
        )
        || !role
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
        || role.as_bytes()[0].is_ascii_digit()
    {
        return Err(invalid("invalid_legacy_storage_writer_role"));
    }
    Ok(())
}

fn verify_legacy_writer_barrier(
    client: &mut impl GenericClient,
    role: &str,
    profile: DatabaseProfile,
) -> Result<(), DomainError> {
    validate_legacy_role(role)?;
    // PostgreSQL distinguishes immediately inherited privileges from SET ROLE
    // reachability. CockroachDB 26.2 does not implement the SET inquiry: MEMBER
    // conservatively includes every reachable member identity instead.
    let reachability = match profile {
        DatabaseProfile::PostgreSql => "SET",
        DatabaseProfile::CockroachDb => "MEMBER",
    };
    let row = client.query_opt(
        "SELECT r.rolsuper, r.rolcreaterole, r.rolcreatedb, \
           EXISTS (SELECT 1 FROM pg_catalog.pg_roles elevated \
             WHERE (elevated.rolsuper OR elevated.rolcreaterole) \
             AND (pg_catalog.pg_has_role(r.rolname, elevated.rolname, $2) \
                  OR pg_catalog.pg_has_role(r.rolname, elevated.rolname, 'USAGE'))), \
           EXISTS (SELECT 1 FROM pg_catalog.pg_class c \
             JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace \
             JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = c.relowner \
             JOIN pg_catalog.pg_roles target ON (target.rolname = r.rolname \
                OR pg_catalog.pg_has_role(r.rolname, target.rolname, $2)) \
             WHERE n.nspname = 'public' AND c.relname = 'trnm_storage_objects' \
             AND (pg_catalog.pg_has_role(target.rolname, owner_role.rolname, $2) \
                  OR pg_catalog.pg_has_role(target.rolname, owner_role.rolname, 'USAGE'))), \
           pg_catalog.has_table_privilege(r.rolname, 'public.trnm_storage_objects', 'INSERT'), \
           pg_catalog.has_table_privilege(r.rolname, 'public.trnm_storage_objects', 'UPDATE'), \
           pg_catalog.has_table_privilege(r.rolname, 'public.trnm_storage_objects', 'DELETE'), \
           pg_catalog.has_any_column_privilege(r.rolname, 'public.trnm_storage_objects', 'INSERT'), \
           pg_catalog.has_any_column_privilege(r.rolname, 'public.trnm_storage_objects', 'UPDATE') \
         FROM pg_catalog.pg_roles r WHERE r.rolname = $1", &[&role, &reachability])
        .map_err(map_postgres_error)?.ok_or_else(|| failed_precondition("legacy_storage_writer_role_missing"))?;
    for index in 0..10 {
        if row.try_get::<_, bool>(index).map_err(map_postgres_error)? {
            return Err(failed_precondition("legacy_storage_writer_not_fenced"));
        }
    }
    if profile == DatabaseProfile::PostgreSql {
        // ADMIN OPTION can authorize a new SET/INHERIT grant even when the
        // existing membership permits neither. Reject it for every reachable
        // role: an otherwise unprivileged role may itself SET ROLE to a writer.
        // CockroachDB does not implement this PostgreSQL inquiry.
        let administration = client
            .query_one(
                "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_roles target \
                 WHERE pg_catalog.pg_has_role($1, target.rolname, 'MEMBER WITH ADMIN OPTION'))",
                &[&role],
            )
            .map_err(map_postgres_error)?;
        if administration
            .try_get::<_, bool>(0)
            .map_err(map_postgres_error)?
        {
            return Err(failed_precondition("legacy_storage_writer_not_fenced"));
        }
    }
    // A NOINHERIT login may have no current CRUD privileges yet SET ROLE to an
    // ordinary writer. Inspect each target's effective grants, including the
    // target's own inherited and column privileges, rather than role membership
    // alone. This observes the supplied identity; draining all writers and
    // privileged routines remains an independent operator prerequisite.
    let mutation_query = match profile {
        DatabaseProfile::PostgreSql =>
            "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_roles target \
             WHERE (target.rolname = $1 OR pg_catalog.pg_has_role($1, target.rolname, 'SET')) \
             AND (pg_catalog.has_table_privilege(target.rolname, 'public.trnm_storage_objects', 'INSERT') \
               OR pg_catalog.has_table_privilege(target.rolname, 'public.trnm_storage_objects', 'UPDATE') \
               OR pg_catalog.has_table_privilege(target.rolname, 'public.trnm_storage_objects', 'DELETE') \
               OR pg_catalog.has_table_privilege(target.rolname, 'public.trnm_storage_objects', 'TRUNCATE') \
               OR pg_catalog.has_any_column_privilege(target.rolname, 'public.trnm_storage_objects', 'INSERT') \
               OR pg_catalog.has_any_column_privilege(target.rolname, 'public.trnm_storage_objects', 'UPDATE')))",
        DatabaseProfile::CockroachDb =>
            // CR's has_table_privilege('TRUNCATE') maps to DELETE, whereas
            // actual TRUNCATE checks DROP. DROP is not a supported inquiry
            // string, so observe the actual descriptor grants instead.
            "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_roles target \
             WHERE (target.rolname = $1 OR pg_catalog.pg_has_role($1, target.rolname, 'MEMBER')) \
             AND (pg_catalog.has_table_privilege(target.rolname, 'public.trnm_storage_objects', 'INSERT') \
               OR pg_catalog.has_table_privilege(target.rolname, 'public.trnm_storage_objects', 'UPDATE') \
               OR pg_catalog.has_table_privilege(target.rolname, 'public.trnm_storage_objects', 'DELETE') \
               OR pg_catalog.has_any_column_privilege(target.rolname, 'public.trnm_storage_objects', 'INSERT') \
               OR pg_catalog.has_any_column_privilege(target.rolname, 'public.trnm_storage_objects', 'UPDATE'))) \
             OR EXISTS (SELECT 1 FROM information_schema.table_privileges grant_row \
                WHERE grant_row.table_catalog = pg_catalog.current_database() \
                  AND grant_row.table_schema = 'public' AND grant_row.table_name = 'trnm_storage_objects' \
                  AND grant_row.privilege_type IN ('DROP', 'ALL') \
                  AND (grant_row.grantee = 'public' OR grant_row.grantee = $1 \
                    OR pg_catalog.pg_has_role($1, grant_row.grantee, 'MEMBER')))",
    };
    if profile == DatabaseProfile::CockroachDb {
        let visible = client
            .query_one(
                "SELECT EXISTS (SELECT 1 FROM information_schema.table_privileges \
             WHERE table_catalog = pg_catalog.current_database() \
               AND table_schema = 'public' AND table_name = 'trnm_storage_objects')",
                &[],
            )
            .map_err(map_postgres_error)?;
        if !visible.try_get::<_, bool>(0).map_err(map_postgres_error)? {
            return Err(failed_precondition(
                "legacy_storage_writer_privilege_catalog_missing",
            ));
        }
    }
    let mutation = client
        .query_one(mutation_query, &[&role])
        .map_err(map_postgres_error)?;
    if mutation.try_get::<_, bool>(0).map_err(map_postgres_error)? {
        return Err(failed_precondition("legacy_storage_writer_not_fenced"));
    }
    Ok(())
}

#[cfg(test)]
mod catalog_v3_tests {
    use super::*;

    fn snapshot(profile: DatabaseProfile, prefix: usize) -> Catalog {
        let mut out = Catalog::new();
        for column in baseline(profile).iter().copied().chain(
            actions(profile)
                .iter()
                .take(prefix)
                .map(|action| action.column),
        ) {
            out.insert(
                (column.table.to_owned(), column.name.to_owned()),
                CatalogColumn {
                    kind: column.kind.to_owned(),
                    nullable: column.nullable,
                    default: column.default_zero.then(|| "0".to_owned()),
                    character_maximum_length: column.character_maximum_length,
                },
            );
        }
        out
    }

    #[test]
    fn native_catalog_recognizes_each_typed_prefix_including_data_completion() {
        for profile in [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb] {
            for prefix in 0..=actions(profile).len() {
                assert_eq!(
                    catalog_prefix(&snapshot(profile, prefix), profile).unwrap(),
                    prefix
                );
            }
            let native = revision(profile, 3).unwrap();
            assert_eq!(native.action_range, 6..22);
            assert_eq!(native.storage_writer_epoch, Some(3));
        }
    }

    #[test]
    fn native_catalog_rejects_typmod_check_expression_and_nonprefix_nullability_drift() {
        for profile in [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb] {
            let complete = snapshot(profile, 22);
            let mut typmod = complete.clone();
            typmod
                .get_mut(&(
                    "trnm_storage_objects".to_owned(),
                    "public_version".to_owned(),
                ))
                .unwrap()
                .character_maximum_length = Some(31);
            let mut check = complete.clone();
            check
                .get_mut(&(
                    "trnm_storage_objects".to_owned(),
                    "@check:storage_origin_witness".to_owned(),
                ))
                .unwrap()
                .kind = "CHECK (true)".to_owned();
            let mut nonprefix = snapshot(profile, 12);
            nonprefix
                .get_mut(&("trnm_storage_objects".to_owned(), "value_bytes".to_owned()))
                .unwrap()
                .nullable = true;
            for bad in [typmod, check, nonprefix] {
                assert!(catalog_prefix(&bad, profile).is_err());
            }
        }
    }

    #[test]
    fn native_catalog_preserves_grouping_and_literal_contents_in_provenance_checks() {
        for profile in [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb] {
            let complete = snapshot(profile, 22);
            let key = (
                "trnm_storage_objects".to_owned(),
                "@check:storage_origin_witness".to_owned(),
            );
            for (from, to) in [
                ("legacy-rust-v2-bytes", "legacy-rust-v2-byte"),
                ("AND", "OR"),
                ("32", "31"),
            ] {
                let mut bad = complete.clone();
                let definition = &mut bad.get_mut(&key).unwrap().kind;
                *definition = definition.replacen(from, to, 1);
                assert!(catalog_prefix(&bad, profile).is_err());
            }
        }
    }
}
