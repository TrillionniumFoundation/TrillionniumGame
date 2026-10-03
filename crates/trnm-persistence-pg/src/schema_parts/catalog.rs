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
            "SELECT c.relname, CASE WHEN c.relkind='r' THEN 'BASE TABLE' ELSE c.relkind::TEXT END FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f') AND (left(c.relname,5)='trnm_' OR c.relname IN ('users','user_device')) LIMIT 15",
            &[],
        )
        .map_err(map_postgres_error)?;
    if !tables.is_empty() {
        if !(10..=14).contains(&tables.len()) {
            return Err(failed_precondition(
                "authoritative_schema_table_inventory_drift",
            ));
        }
        let mut seen = std::collections::BTreeSet::new();
        for row in &tables {
            let table: String = row.try_get(0).map_err(map_postgres_error)?;
            let kind: String = row.try_get(1).map_err(map_postgres_error)?;
            if (!REQUIRED_TABLES.contains(&table.as_str())
                && !matches!(table.as_str(), "users" | "user_device"))
                || kind != "BASE TABLE"
                || !seen.insert(table)
            {
                return Err(failed_precondition("authoritative_schema_table_kind_drift"));
            }
        }
        if REQUIRED_TABLES[..10]
            .iter()
            .any(|table| !seen.contains(*table))
        {
            return Err(failed_precondition(
                "authoritative_schema_table_inventory_drift",
            ));
        }
    }
    let rows = client.query(
        "SELECT table_name, column_name, udt_name, is_nullable, CASE WHEN octet_length(column_default) <= 4096 THEN column_default ELSE NULL END, character_maximum_length::BIGINT, COALESCE(octet_length(column_default) > 4096,false) \
         FROM information_schema.columns \
         WHERE table_schema = 'public' AND (left(table_name, 5) = 'trnm_' OR table_name IN ('users','user_device')) \
         ORDER BY table_name, ordinal_position LIMIT 257", &[]).map_err(map_postgres_error)?;
    if rows.len() > 256 {
        return Err(failed_precondition("authoritative_schema_catalog_budget"));
    }
    let mut catalog = BTreeMap::new();
    for row in rows {
        if row.try_get::<_, bool>(6).map_err(map_postgres_error)? {
            return Err(failed_precondition("schema_catalog_wire_value_budget"));
        }
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
        let storage_table_count = tables
            .iter()
            .filter(|row| row.get::<_, String>(0).starts_with("trnm_"))
            .count();
        verify_primary_keys(client, storage_table_count)?;
        let checks = client.query("SELECT t.relname, c.conname, c.convalidated, pg_catalog.pg_get_constraintdef(c.oid), c.conkey \
            FROM pg_catalog.pg_constraint c JOIN pg_catalog.pg_class t ON t.oid=c.conrelid \
            JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace \
            WHERE n.nspname='public' AND (c.conname IN ('storage_projection_digest','storage_origin_witness','metadata_v2_history','metadata_v3_history','metadata_v4_history','trnm_storage_objects_collection_check','trnm_storage_objects_object_key_check','trnm_storage_objects_read_permission_check','trnm_storage_objects_write_permission_check','check_collection','check_object_key','check_read_permission','check_write_permission')) LIMIT 14", &[]).map_err(map_postgres_error)?;
        if checks.len() > 13 {
            return Err(failed_precondition("authoritative_schema_check_drift"));
        }
        for row in checks {
            let table: String = row.try_get(0).map_err(map_postgres_error)?;
            let name: String = row.try_get(1).map_err(map_postgres_error)?;
            let validated: bool = row.try_get(2).map_err(map_postgres_error)?;
            let definition: String = row.try_get(3).map_err(map_postgres_error)?;
            let keys: Vec<i16> = row.try_get(4).map_err(map_postgres_error)?;
            let is_domain = name.starts_with("trnm_storage_objects_") || name.starts_with("check_");
            if is_domain {
                insert_catalog_object(
                    &mut catalog,
                    &table,
                    &format!("@checkkeys:{name}"),
                    format!("{keys:?}"),
                )?;
            }
            if name == "metadata_v4_history" && keys != [2, 12] {
                return Err(failed_precondition("authoritative_schema_check_drift"));
            }
            if name == "metadata_v3_history" && keys != [2, 11] {
                return Err(failed_precondition("authoritative_schema_check_drift"));
            }
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
        read_import_catalog(client, &mut catalog)?;
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
    let physical_users = tables.iter().any(|row| row.get::<_, String>(0) == "users");
    let physical_devices = tables
        .iter()
        .any(|row| row.get::<_, String>(0) == "user_device");
    read_account_catalog(client, &mut catalog, physical_users, physical_devices)?;
    Ok(catalog)
}

fn matches_column(
    actual: &CatalogColumn,
    expected: ColumnDescriptor,
    profile: DatabaseProfile,
) -> bool {
    if matches!(expected.table, "users" | "user_device") {
        return matches_account_column(actual, expected, profile);
    }
    (actual.kind == expected.kind
        || matches_import_constraint_serialization(profile, expected, &actual.kind))
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

fn insert_catalog_object(
    catalog: &mut Catalog,
    table: &str,
    name: &str,
    kind: String,
) -> Result<(), DomainError> {
    if catalog
        .insert(
            (table.to_owned(), name.to_owned()),
            CatalogColumn {
                kind,
                nullable: false,
                default: None,
                character_maximum_length: None,
            },
        )
        .is_some()
    {
        return Err(failed_precondition(
            "authoritative_schema_catalog_duplicate_object",
        ));
    }
    Ok(())
}

fn expected_catalog_baseline(
    profile: DatabaseProfile,
) -> BTreeMap<(&'static str, &'static str), ColumnDescriptor> {
    let mut expected: BTreeMap<_, _> = baseline(profile)
        .iter()
        .map(|column| ((column.table, column.name), *column))
        .collect();
    for descriptor in old_storage_domain_checks(profile) {
        expected.insert((descriptor.table, descriptor.name), descriptor);
        let keys = storage_check_key_descriptor(descriptor.name);
        expected.insert((keys.table, keys.name), keys);
    }
    expected
}

fn advance_expected_catalog(
    expected: &mut BTreeMap<(&'static str, &'static str), ColumnDescriptor>,
    action: &MigrationAction,
    profile: DatabaseProfile,
) {
    match action.descriptor {
        ActionDescriptor::Column(column) => {
            if matches!(action.kind, MigrationActionKind::SetNullability) {
                expected
                    .get_mut(&(column.table, column.name))
                    .expect("reviewed prior column")
                    .nullable = column.nullable;
            } else {
                expected.insert((column.table, column.name), column);
            }
        }
        ActionDescriptor::StorageCheck(check) => {
            let keys = storage_check_key_descriptor(check.name);
            if matches!(action.kind, MigrationActionKind::RemoveStorageCheck) {
                expected.remove(&(check.table, check.name));
                expected.remove(&(keys.table, keys.name));
            } else {
                expected.insert((check.table, check.name), check);
                expected.insert((keys.table, keys.name), keys);
            }
        }
        ActionDescriptor::AccountTable(table) => {
            for column in table.columns {
                expected.insert((column.table, column.name), *column);
            }
            for object in account_catalog_objects(profile, table.table) {
                expected.insert((object.table, object.name), object);
            }
        }
        ActionDescriptor::ImportTable(table) => {
            for column in table.columns {
                expected.insert((column.table, column.name), *column);
            }
            for object in import_catalog_objects(profile, table.table) {
                expected.insert((object.table, object.name), object);
            }
        }
    }
}

fn catalog_prefix(catalog: &Catalog, profile: DatabaseProfile) -> Result<usize, DomainError> {
    let mut expected = expected_catalog_baseline(profile);
    for prefix in 0..=actions(profile).len() {
        if expected.len() == catalog.len()
            && expected.iter().all(|((table, name), column)| {
                catalog
                    .get(&(table.to_string(), name.to_string()))
                    .is_some_and(|actual| matches_column(actual, *column, profile))
            })
        {
            return Ok(prefix);
        }
        if let Some(action) = actions(profile).get(prefix) {
            advance_expected_catalog(&mut expected, action, profile);
        }
    }
    Err(failed_precondition(
        "authoritative_schema_partial_action_drift",
    ))
}

fn read_import_catalog(
    client: &mut impl GenericClient,
    catalog: &mut Catalog,
) -> Result<(), DomainError> {
    let rows=client.query("SELECT t.relname,c.conname,c.contype::TEXT,c.convalidated,c.conkey,c.confkey,c.confupdtype::TEXT,c.confdeltype::TEXT,rn.nspname,r.relname,pg_catalog.pg_get_constraintdef(c.oid) FROM pg_catalog.pg_constraint c JOIN pg_catalog.pg_class t ON t.oid=c.conrelid JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace LEFT JOIN pg_catalog.pg_class r ON r.oid=c.confrelid LEFT JOIN pg_catalog.pg_namespace rn ON rn.oid=r.relnamespace WHERE n.nspname='public' AND t.relname IN ('trnm_storage_import_jobs','trnm_storage_import_pages') LIMIT 14",&[]).map_err(map_postgres_error)?;
    if rows.len() > 13 {
        return Err(failed_precondition(
            "authoritative_schema_import_catalog_budget",
        ));
    }
    for row in rows {
        let table: String = row.try_get(0).map_err(map_postgres_error)?;
        let name: String = row.try_get(1).map_err(map_postgres_error)?;
        let kind: String = row.try_get(2).map_err(map_postgres_error)?;
        let validated: bool = row.try_get(3).map_err(map_postgres_error)?;
        let keys: Vec<i16> = row.try_get(4).map_err(map_postgres_error)?;
        let reference_keys: Option<Vec<i16>> = row.try_get(5).map_err(map_postgres_error)?;
        let update: Option<String> = row.try_get(6).map_err(map_postgres_error)?;
        let delete: Option<String> = row.try_get(7).map_err(map_postgres_error)?;
        let reference_schema: Option<String> = row.try_get(8).map_err(map_postgres_error)?;
        let reference_table: Option<String> = row.try_get(9).map_err(map_postgres_error)?;
        let definition: String = row.try_get(10).map_err(map_postgres_error)?;
        insert_catalog_object(catalog,&table,&format!("@constraint:{name}"),format!("{kind}|{validated}|{keys:?}|{reference_keys:?}|{update:?}|{delete:?}|{reference_schema:?}|{reference_table:?}|{definition}"))?;
    }
    let indexes=client.query("SELECT t.relname,x.relname,i.indisunique,i.indisprimary,pg_catalog.pg_get_expr(i.indpred,i.indrelid),pg_catalog.pg_get_indexdef(i.indexrelid),i.indisvalid,i.indisready FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class t ON t.oid=i.indrelid JOIN pg_catalog.pg_class x ON x.oid=i.indexrelid JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='public' AND t.relname IN ('trnm_storage_import_jobs','trnm_storage_import_pages') LIMIT 4",&[]).map_err(map_postgres_error)?;
    if indexes.len() > 3 {
        return Err(failed_precondition(
            "authoritative_schema_import_index_drift",
        ));
    }
    if indexes.is_empty() {
        return Ok(());
    }
    let namespace = client
        .query_one(
            "SELECT pg_catalog.quote_ident(pg_catalog.current_database())::TEXT",
            &[],
        )
        .map_err(map_postgres_error)?;
    let database: String = namespace.try_get(0).map_err(map_postgres_error)?;
    for row in indexes {
        let table: String = row.try_get(0).map_err(map_postgres_error)?;
        let name: String = row.try_get(1).map_err(map_postgres_error)?;
        let unique: bool = row.try_get(2).map_err(map_postgres_error)?;
        let primary: bool = row.try_get(3).map_err(map_postgres_error)?;
        let predicate: Option<String> = row.try_get(4).map_err(map_postgres_error)?;
        let definition: String = row.try_get(5).map_err(map_postgres_error)?;
        let valid: bool = row.try_get(6).map_err(map_postgres_error)?;
        let ready: bool = row.try_get(7).map_err(map_postgres_error)?;
        let object_name = format!("@index:{name}");
        let actual = format!("{unique}|{primary}|{valid}|{ready}|{predicate:?}|{definition}");
        // Instantiate the captured native namespace template, then compare the
        // complete actual bytes. No database-name erasure before validation.
        let expected = [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb]
            .into_iter()
            .flat_map(|profile| import_catalog_objects(profile, &table))
            .find(|object| {
                object.name == object_name && object.kind.replace("{database}", &database) == actual
            });
        let expected = expected
            .ok_or_else(|| failed_precondition("authoritative_schema_import_index_drift"))?;
        insert_catalog_object(catalog, &table, &object_name, expected.kind.to_owned())?;
    }
    for table in ["trnm_storage_import_jobs", "trnm_storage_import_pages"] {
        if catalog.keys().any(|(name, _)| name == table) {
            insert_catalog_object(catalog, table, "@table", "BASE TABLE".to_owned())?;
        }
    }
    Ok(())
}

fn verify_primary_keys(
    client: &mut impl GenericClient,
    table_count: usize,
) -> Result<(), DomainError> {
    // information_schema.table_constraints hides keys from SELECT-only roles.
    // Native catalogs permit a genuinely read-only readiness client to inspect
    // the same exact primary-key shapes without receiving mutation privileges.
    let rows=client.query("SELECT t.relname,pg_catalog.pg_get_constraintdef(c.oid) FROM pg_catalog.pg_constraint c \
        JOIN pg_catalog.pg_class t ON t.oid=c.conrelid JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace \
        WHERE n.nspname='public' AND left(t.relname,5)='trnm_' AND c.contype='p' LIMIT 13",&[]).map_err(map_postgres_error)?;
    if rows.len() != table_count {
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
        ("trnm_storage_import_jobs", vec!["singleton"]),
        (
            "trnm_storage_import_pages",
            vec!["manifest_digest", "page_index"],
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
    for table in REQUIRED_TABLES[..10]
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

pub(crate) fn validate_legacy_role(role: &str) -> Result<(), DomainError> {
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

pub(crate) fn verify_legacy_writer_barrier(
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
               OR pg_catalog.has_table_privilege(target.rolname, 'public.trnm_storage_objects', 'TRIGGER') \
               OR pg_catalog.has_any_column_privilege(target.rolname, 'public.trnm_storage_objects', 'INSERT') \
               OR pg_catalog.has_any_column_privilege(target.rolname, 'public.trnm_storage_objects', 'UPDATE')))",
        DatabaseProfile::CockroachDb =>
            // CR's has_table_privilege('TRUNCATE') maps to DELETE, whereas
            // actual TRUNCATE checks DROP. CREATE authorizes ALTER TABLE, and
            // TRIGGER installs user triggers. Observe descriptor grants rather
            // than relying on unsupported or differently mapped inquiries.
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
                  AND grant_row.privilege_type IN ('CREATE', 'DROP', 'TRIGGER', 'ALL') \
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

#[derive(Clone, Copy)]
enum ImportAuthorityTable {
    Storage,
    Metadata,
    Jobs,
    Pages,
}

impl ImportAuthorityTable {
    const fn names(self) -> (&'static str, &'static str) {
        match self {
            Self::Storage => ("trnm_storage_objects", "public.trnm_storage_objects"),
            Self::Metadata => ("trnm_schema_metadata", "public.trnm_schema_metadata"),
            Self::Jobs => (
                "trnm_storage_import_jobs",
                "public.trnm_storage_import_jobs",
            ),
            Self::Pages => (
                "trnm_storage_import_pages",
                "public.trnm_storage_import_pages",
            ),
        }
    }
}

/// Import registration must also fence its completion/provenance authority.
/// Historical migration entry points deliberately retain their storage-only
/// table scope, including prefixes where the new journal does not yet exist.
/// No caller supplies a relation name or extends this fixed four-table set.
pub(crate) fn verify_storage_import_legacy_writer_barrier(
    client: &mut impl GenericClient,
    role: &str,
    profile: DatabaseProfile,
) -> Result<(), DomainError> {
    verify_legacy_writer_barrier(client, role, profile)?;
    let reachability = match profile {
        DatabaseProfile::PostgreSql => "SET",
        DatabaseProfile::CockroachDb => "MEMBER",
    };
    for table in [
        ImportAuthorityTable::Storage,
        ImportAuthorityTable::Metadata,
        ImportAuthorityTable::Jobs,
        ImportAuthorityTable::Pages,
    ] {
        let (name, qualified) = table.names();
        let row = client
            .query_one(
                "SELECT \
              (SELECT count(*)::INT8 FROM pg_catalog.pg_class c \
                JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace \
                JOIN pg_catalog.pg_roles owner_role ON owner_role.oid=c.relowner \
                WHERE n.nspname='public' AND c.relname=$4), \
              EXISTS(SELECT 1 FROM pg_catalog.pg_roles target \
                WHERE (target.rolname=$1 OR pg_catalog.pg_has_role($1,target.rolname,$2)) \
                AND (pg_catalog.has_table_privilege(target.rolname,$3::TEXT,'INSERT') \
                  OR pg_catalog.has_table_privilege(target.rolname,$3::TEXT,'UPDATE') \
                  OR pg_catalog.has_table_privilege(target.rolname,$3::TEXT,'DELETE') \
                  OR pg_catalog.has_any_column_privilege(target.rolname,$3::TEXT,'INSERT') \
                  OR pg_catalog.has_any_column_privilege(target.rolname,$3::TEXT,'UPDATE'))), \
              EXISTS(SELECT 1 FROM pg_catalog.pg_class c \
                JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace \
                JOIN pg_catalog.pg_roles owner_role ON owner_role.oid=c.relowner \
                JOIN pg_catalog.pg_roles target ON (target.rolname=$1 \
                  OR pg_catalog.pg_has_role($1,target.rolname,$2)) \
                WHERE n.nspname='public' AND c.relname=$4 \
                  AND (pg_catalog.pg_has_role(target.rolname,owner_role.rolname,$2) \
                    OR pg_catalog.pg_has_role(target.rolname,owner_role.rolname,'USAGE')))",
                &[&role, &reachability, &qualified, &name],
            )
            .map_err(map_postgres_error)?;
        if row.try_get::<_, i64>(0).map_err(map_postgres_error)? != 1 {
            return Err(failed_precondition(
                "legacy_import_authority_catalog_missing",
            ));
        }
        if row.try_get::<_, bool>(1).map_err(map_postgres_error)?
            || row.try_get::<_, bool>(2).map_err(map_postgres_error)?
        {
            return Err(failed_precondition(
                "legacy_import_authority_writer_not_fenced",
            ));
        }
        // DELETE is a table privilege; PostgreSQL has no column DELETE grant.
        // CR's TRUNCATE inquiry checks DELETE, not actual DROP authority.
        let clearing = match profile {
            DatabaseProfile::PostgreSql => client.query_one(
                "SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_roles target \
                  WHERE (target.rolname=$1 OR pg_catalog.pg_has_role($1,target.rolname,'SET')) \
                    AND (pg_catalog.has_table_privilege(target.rolname,$2::TEXT,'TRUNCATE') \
                      OR pg_catalog.has_table_privilege(target.rolname,$2::TEXT,'TRIGGER')))",
                &[&role,&qualified],
            ),
            DatabaseProfile::CockroachDb => client.query_one(
                "SELECT EXISTS(SELECT 1 FROM information_schema.table_privileges \
                    WHERE table_catalog=pg_catalog.current_database() AND table_schema='public' AND table_name=$2), \
                  EXISTS(SELECT 1 FROM information_schema.table_privileges grant_row \
                    WHERE grant_row.table_catalog=pg_catalog.current_database() \
                      AND grant_row.table_schema='public' AND grant_row.table_name=$2 \
                      AND grant_row.privilege_type IN ('CREATE','DROP','TRIGGER','ALL') \
                      AND (grant_row.grantee='public' OR grant_row.grantee=$1 \
                        OR pg_catalog.pg_has_role($1,grant_row.grantee,'MEMBER')))",
                &[&role,&name],
            ),
        }.map_err(map_postgres_error)?;
        let index = if profile == DatabaseProfile::CockroachDb {
            if !clearing.try_get::<_, bool>(0).map_err(map_postgres_error)? {
                return Err(failed_precondition(
                    "legacy_import_authority_catalog_missing",
                ));
            }
            1
        } else {
            0
        };
        if clearing
            .try_get::<_, bool>(index)
            .map_err(map_postgres_error)?
        {
            return Err(failed_precondition(
                "legacy_import_authority_writer_not_fenced",
            ));
        }
    }
    // Revoking a trigger grant does not remove previously installed triggers.
    // Reject every user trigger on these four relations before registration or
    // continuation; never remove operator objects automatically. Internal FK
    // triggers are part of the reviewed native journal catalog.
    let triggers = client.query_one(
        "SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_trigger trigger_row \
          JOIN pg_catalog.pg_class c ON c.oid=trigger_row.tgrelid \
          JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace \
          WHERE n.nspname='public' AND c.relname IN \
            ('trnm_storage_objects','trnm_schema_metadata','trnm_storage_import_jobs','trnm_storage_import_pages') \
            AND NOT trigger_row.tgisinternal)", &[],
    ).map_err(map_postgres_error)?;
    if triggers.try_get::<_, bool>(0).map_err(map_postgres_error)? {
        return Err(failed_precondition(
            "legacy_import_authority_trigger_present",
        ));
    }
    // A schema owner may drop tables owned by another role. The default public
    // owner pg_database_owner also implicitly grants this to the current DB
    // owner. Resolve BOTH actual ownership catalogs; unknown placeholders or
    // missing role identities are rejected, including on the CR candidate.
    let ownership = client.query_one(
        "SELECT \
          (SELECT count(*)::INT8 FROM pg_catalog.pg_namespace n JOIN pg_catalog.pg_roles o ON o.oid=n.nspowner WHERE n.nspname='public'), \
          (SELECT count(*)::INT8 FROM pg_catalog.pg_database d JOIN pg_catalog.pg_roles o ON o.oid=d.datdba WHERE d.datname=pg_catalog.current_database()), \
          EXISTS(SELECT 1 FROM pg_catalog.pg_roles target \
            JOIN pg_catalog.pg_namespace n ON n.nspname='public' \
            JOIN pg_catalog.pg_roles owner_role ON owner_role.oid=n.nspowner \
            WHERE (target.rolname=$1 OR pg_catalog.pg_has_role($1,target.rolname,$2)) \
              AND (pg_catalog.pg_has_role(target.rolname,owner_role.rolname,$2) \
                OR pg_catalog.pg_has_role(target.rolname,owner_role.rolname,'USAGE'))), \
          EXISTS(SELECT 1 FROM pg_catalog.pg_roles target \
            JOIN pg_catalog.pg_database d ON d.datname=pg_catalog.current_database() \
            JOIN pg_catalog.pg_roles owner_role ON owner_role.oid=d.datdba \
            WHERE (target.rolname=$1 OR pg_catalog.pg_has_role($1,target.rolname,$2)) \
              AND (pg_catalog.pg_has_role(target.rolname,owner_role.rolname,$2) \
                OR pg_catalog.pg_has_role(target.rolname,owner_role.rolname,'USAGE')))",
        &[&role,&reachability],
    ).map_err(map_postgres_error)?;
    if ownership.try_get::<_, i64>(0).map_err(map_postgres_error)? != 1
        || ownership.try_get::<_, i64>(1).map_err(map_postgres_error)? != 1
    {
        return Err(failed_precondition(
            "legacy_import_ownership_catalog_missing",
        ));
    }
    if ownership
        .try_get::<_, bool>(2)
        .map_err(map_postgres_error)?
        || ownership
            .try_get::<_, bool>(3)
            .map_err(map_postgres_error)?
    {
        return Err(failed_precondition(
            "legacy_import_namespace_owner_not_fenced",
        ));
    }
    Ok(())
}

#[cfg(test)]
mod catalog_v3_tests {
    use super::*;

    // Preserve the complete original v3/v4 regression range when the embedded
    // source frontier grows. Pending account source has no native fixture ABI.
    fn storage_v4_prefix(profile: DatabaseProfile) -> usize {
        revision(profile, 4).unwrap().action_range.end
    }

    fn snapshot(profile: DatabaseProfile, prefix: usize) -> Catalog {
        let mut expected = expected_catalog_baseline(profile);
        for action in actions(profile).iter().take(prefix) {
            advance_expected_catalog(&mut expected, action, profile);
        }
        let mut out = Catalog::new();
        for column in expected.values() {
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
    fn source_account_columns_never_substitute_for_captured_native_catalog() {
        for profile in [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb] {
            let full_source = snapshot(profile, revision(profile, 5).unwrap().action_range.end);
            assert!(catalog_prefix(&full_source, profile).is_err());
            assert_eq!(
                catalog_prefix(&snapshot(profile, storage_v4_prefix(profile)), profile).unwrap(),
                storage_v4_prefix(profile)
            );
        }
    }

    #[test]
    fn native_catalog_recognizes_each_typed_prefix_including_data_completion() {
        for profile in [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb] {
            for prefix in 0..=storage_v4_prefix(profile) {
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
    #[test]
    fn v4_catalog_rejects_partial_journal_foreign_key_and_wrong_native_check() {
        for profile in [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb] {
            let ready = snapshot(profile, storage_v4_prefix(profile));
            let mut missing = ready.clone();
            missing.remove(&(
                "trnm_storage_import_jobs".to_owned(),
                "custody_digest".to_owned(),
            ));
            let mut foreign = ready.clone();
            let changed_foreign = foreign
                .get(&(
                    "trnm_storage_import_pages".to_owned(),
                    "@constraint:storage_import_pages_job_fk".to_owned(),
                ))
                .unwrap()
                .kind
                .replace("ON DELETE RESTRICT", "ON DELETE CASCADE");
            foreign
                .get_mut(&(
                    "trnm_storage_import_pages".to_owned(),
                    "@constraint:storage_import_pages_job_fk".to_owned(),
                ))
                .unwrap()
                .kind = changed_foreign;
            let mut state = ready.clone();
            state
                .get_mut(&(
                    "trnm_storage_import_jobs".to_owned(),
                    "@constraint:storage_import_jobs_status".to_owned(),
                ))
                .unwrap()
                .kind
                .push(' ');
            let mut extra = ready;
            extra.insert(
                (
                    "trnm_storage_import_jobs".to_owned(),
                    "@index:extra".to_owned(),
                ),
                CatalogColumn {
                    kind: "true".to_owned(),
                    nullable: false,
                    default: None,
                    character_maximum_length: None,
                },
            );
            for malformed in [missing, foreign, state, extra] {
                assert!(catalog_prefix(&malformed, profile).is_err());
            }
        }
    }

    #[test]
    fn v4_index_catalog_keeps_each_profiles_native_valid_and_ready_flags() {
        for profile in [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb] {
            let ready = snapshot(profile, storage_v4_prefix(profile));
            let key = (
                "trnm_storage_import_jobs".to_owned(),
                "@index:storage_import_jobs_pk".to_owned(),
            );
            let expected = if profile == DatabaseProfile::PostgreSql {
                "true|true|true|true|"
            } else {
                "true|true|true|false|"
            };
            assert!(ready[&key].kind.starts_with(expected));
            for replacement in [
                "true|true|false|true|",
                "true|true|false|false|",
                if profile == DatabaseProfile::PostgreSql {
                    "true|true|true|false|"
                } else {
                    "true|true|true|true|"
                },
            ] {
                let mut changed = ready.clone();
                let kind = &mut changed.get_mut(&key).unwrap().kind;
                *kind = kind.replacen(expected, replacement, 1);
                assert!(catalog_prefix(&changed, profile).is_err());
            }
        }
    }

    #[test]
    fn v4_removed_cockroach_check_has_one_explicit_nonready_prefix() {
        let profile = DatabaseProfile::CockroachDb;
        let remove = actions(profile)
            .iter()
            .position(|action| action.id == "storage_collection_check_v3_remove")
            .unwrap();
        let gap = snapshot(profile, remove + 1);
        assert!(!gap.contains_key(&(
            "trnm_storage_objects".to_owned(),
            "@check:check_collection".to_owned()
        )));
        assert_eq!(catalog_prefix(&gap, profile).unwrap(), remove + 1);
    }

    #[test]
    fn v4_postgresql_catalog_accepts_only_captured_fresh_and_restore_check_forms() {
        let profile = DatabaseProfile::PostgreSql;
        let complete = storage_v4_prefix(profile);
        for mask in 0..4 {
            let mut catalog = snapshot(profile, complete);
            for (index, &(table, name, fresh, restored)) in
                POSTGRESQL17_IMPORT_CHECK_FORMS.iter().enumerate()
            {
                let key = (table.to_owned(), name.to_owned());
                assert_eq!(catalog[&key].kind, fresh);
                if mask & (1 << index) != 0 {
                    catalog.get_mut(&key).unwrap().kind = restored.to_owned();
                }
            }
            let raw_before = catalog
                .iter()
                .map(|(key, column)| (key.clone(), column.kind.clone()))
                .collect::<Vec<_>>();
            assert_eq!(catalog_prefix(&catalog, profile).unwrap(), complete);
            let raw_after = catalog
                .iter()
                .map(|(key, column)| (key.clone(), column.kind.clone()))
                .collect::<Vec<_>>();
            assert_eq!(raw_before, raw_after);
        }
    }

    #[test]
    fn v4_postgresql_restore_forms_are_bound_to_profile_and_original_descriptor() {
        for &(table, name, fresh, restored) in &POSTGRESQL17_IMPORT_CHECK_FORMS {
            let expected = import_catalog_objects(DatabaseProfile::PostgreSql, table)
                .into_iter()
                .find(|column| column.name == name)
                .unwrap();
            assert_eq!(expected.kind, fresh);
            assert!(matches_import_constraint_serialization(
                DatabaseProfile::PostgreSql,
                expected,
                restored,
            ));
            for changed in [
                ColumnDescriptor {
                    table: "trnm_storage_objects",
                    ..expected
                },
                ColumnDescriptor {
                    name: "@constraint:other",
                    ..expected
                },
                ColumnDescriptor {
                    kind: "CHECK (false)",
                    ..expected
                },
            ] {
                assert!(!matches_import_constraint_serialization(
                    DatabaseProfile::PostgreSql,
                    changed,
                    restored,
                ));
            }
            assert!(!matches_import_constraint_serialization(
                DatabaseProfile::CockroachDb,
                expected,
                restored,
            ));
            let mut wrong_profile = snapshot(
                DatabaseProfile::CockroachDb,
                actions(DatabaseProfile::CockroachDb).len(),
            );
            wrong_profile
                .get_mut(&(table.to_owned(), name.to_owned()))
                .unwrap()
                .kind = restored.to_owned();
            assert!(catalog_prefix(&wrong_profile, DatabaseProfile::CockroachDb).is_err());
        }
    }

    #[test]
    fn v4_postgresql_check_forms_reject_bounds_logic_and_native_field_drift() {
        let profile = DatabaseProfile::PostgreSql;
        for &(table, name, fresh, restored) in &POSTGRESQL17_IMPORT_CHECK_FORMS {
            let (keys, reordered, removed) = if table == "trnm_storage_import_jobs" {
                (
                    "[10, 11, 12, 13]",
                    "[11, 10, 12, 13]",
                    " AND ((next_page >= 0) AND (next_page <= total_pages))",
                )
            } else {
                (
                    "[2, 3, 4]",
                    "[3, 2, 4]",
                    " AND ((row_count >= 1) AND (row_count <= 100))",
                )
            };
            for captured in [fresh, restored] {
                for (from, to) in [
                    ("10000", "10001"),
                    ("AND", "OR"),
                    ("<=", ">="),
                    (removed, ""),
                    (keys, reordered),
                    ("c|true|", "c|false|"),
                    ("c|true|", "u|true|"),
                    (
                        "|None|Some(\" \")|Some(\" \")|None|None|CHECK",
                        "|Some([1])|Some(\" \")|Some(\" \")|None|None|CHECK",
                    ),
                    ("|None|None|CHECK", "|Some(\"shadow\")|None|CHECK"),
                ] {
                    let changed = captured.replacen(from, to, 1);
                    assert_ne!(changed, captured, "mutation did not change {name}");
                    let mut bad = snapshot(profile, storage_v4_prefix(profile));
                    bad.get_mut(&(table.to_owned(), name.to_owned()))
                        .unwrap()
                        .kind = changed;
                    assert!(
                        catalog_prefix(&bad, profile).is_err(),
                        "{name}: {from} -> {to}"
                    );
                }
                // Extra whitespace or another grouping is not a third captured form.
                for changed in [
                    format!("{captured} "),
                    format!(" {captured}"),
                    format!("{})", captured.replacen("CHECK (", "CHECK ((", 1)),
                ] {
                    let mut bad = snapshot(profile, storage_v4_prefix(profile));
                    bad.get_mut(&(table.to_owned(), name.to_owned()))
                        .unwrap()
                        .kind = changed;
                    assert!(catalog_prefix(&bad, profile).is_err());
                }
            }
        }
    }

    #[test]
    fn v4_postgresql_restore_forms_preserve_other_properties_and_catalog_keys() {
        let profile = DatabaseProfile::PostgreSql;
        let mut restored_catalog = snapshot(profile, storage_v4_prefix(profile));
        for &(table, name, _, restored) in &POSTGRESQL17_IMPORT_CHECK_FORMS {
            restored_catalog
                .get_mut(&(table.to_owned(), name.to_owned()))
                .unwrap()
                .kind = restored.to_owned();
        }
        for &(table, name, _, _) in &POSTGRESQL17_IMPORT_CHECK_FORMS {
            let key = (table.to_owned(), name.to_owned());
            let mut nullable = restored_catalog.clone();
            nullable.get_mut(&key).unwrap().nullable = true;
            let mut default = restored_catalog.clone();
            default.get_mut(&key).unwrap().default = Some("0".to_owned());
            let mut typmod = restored_catalog.clone();
            typmod.get_mut(&key).unwrap().character_maximum_length = Some(32);
            let mut renamed = restored_catalog.clone();
            let value = renamed.remove(&key).unwrap();
            renamed.insert(
                (table.to_owned(), "@constraint:other".to_owned()),
                value.clone(),
            );
            let mut wrong_table = restored_catalog.clone();
            wrong_table.remove(&key);
            wrong_table.insert(("trnm_storage_objects".to_owned(), name.to_owned()), value);
            for bad in [nullable, default, typmod, renamed, wrong_table] {
                assert!(catalog_prefix(&bad, profile).is_err());
            }
        }
        let mut swapped = restored_catalog.clone();
        let (jobs_table, jobs_name, _, jobs_kind) = POSTGRESQL17_IMPORT_CHECK_FORMS[0];
        let (pages_table, pages_name, _, pages_kind) = POSTGRESQL17_IMPORT_CHECK_FORMS[1];
        swapped
            .get_mut(&(jobs_table.to_owned(), jobs_name.to_owned()))
            .unwrap()
            .kind = pages_kind.to_owned();
        swapped
            .get_mut(&(pages_table.to_owned(), pages_name.to_owned()))
            .unwrap()
            .kind = jobs_kind.to_owned();
        assert!(catalog_prefix(&swapped, profile).is_err());
        let mut foreign_schema = restored_catalog;
        let foreign = foreign_schema
            .get_mut(&(
                "trnm_storage_import_pages".to_owned(),
                "@constraint:storage_import_pages_job_fk".to_owned(),
            ))
            .unwrap();
        assert!(foreign.kind.contains("Some(\"public\")"));
        foreign.kind = foreign
            .kind
            .replacen("Some(\"public\")", "Some(\"shadow\")", 1);
        assert!(catalog_prefix(&foreign_schema, profile).is_err());
    }
}
