#[derive(Clone, Debug)]
struct CatalogColumn {
    kind: String,
    nullable: bool,
    default: Option<String>,
}

type Catalog = BTreeMap<(String, String), CatalogColumn>;

fn read_catalog(client: &mut impl GenericClient) -> Result<Catalog, DomainError> {
    let tables = client.query(
        "SELECT table_name, table_type FROM information_schema.tables \
         WHERE table_schema = 'public' AND left(table_name, 5) = 'trnm_'", &[])
        .map_err(map_postgres_error)?;
    if !tables.is_empty() {
        if tables.len() != REQUIRED_TABLES.len() { return Err(failed_precondition("authoritative_schema_table_inventory_drift")); }
        for row in &tables {
            let table: String = row.try_get(0).map_err(map_postgres_error)?;
            let kind: String = row.try_get(1).map_err(map_postgres_error)?;
            if !REQUIRED_TABLES.contains(&table.as_str()) || kind != "BASE TABLE" {
                return Err(failed_precondition("authoritative_schema_table_kind_drift"));
            }
        }
    }
    let rows = client.query(
        "SELECT table_name, column_name, udt_name, is_nullable, column_default \
         FROM information_schema.columns \
         WHERE table_schema = 'public' AND left(table_name, 5) = 'trnm_' \
         ORDER BY table_name, ordinal_position", &[]).map_err(map_postgres_error)?;
    if rows.len() > 256 { return Err(failed_precondition("authoritative_schema_catalog_budget")); }
    let mut catalog = BTreeMap::new();
    for row in rows {
        let table: String = row.try_get(0).map_err(map_postgres_error)?;
        let name: String = row.try_get(1).map_err(map_postgres_error)?;
        let kind = row.try_get(2).map_err(map_postgres_error)?;
        let nullable: String = row.try_get(3).map_err(map_postgres_error)?;
        let default = row.try_get(4).map_err(map_postgres_error)?;
        if !matches!(nullable.as_str(), "YES" | "NO") { return Err(data_loss("schema_catalog_nullability_invalid")); }
        if catalog.insert((table, name), CatalogColumn {kind, nullable:nullable == "YES", default}).is_some() {
            return Err(data_loss("schema_catalog_duplicate_column"));
        }
    }
    Ok(catalog)
}

fn matches_column(actual: &CatalogColumn, expected: ColumnDescriptor) -> bool {
    actual.kind == expected.kind && actual.nullable == expected.nullable &&
        if expected.default_zero {
            actual.default.as_deref().is_some_and(zero_default)
        } else { actual.default.is_none() }
}

// PostgreSQL and CockroachDB spell their pinned zero cast differently.
// Accept only a numeric zero with an integer cast, not arbitrary expressions.
fn zero_default(value: &str) -> bool {
    let value: String = value.chars().filter(|c| !c.is_ascii_whitespace()).collect();
    matches!(value.as_str(), "0" | "0::bigint" | "0::smallint" | "0:::INT8" | "0:::INT2" | "'0'::bigint" | "'0'::smallint")
}

fn catalog_prefix(catalog: &Catalog, profile: DatabaseProfile) -> Result<usize, DomainError> {
    for expected in baseline(profile) {
        let actual = catalog.get(&(expected.table.to_owned(), expected.name.to_owned()))
            .ok_or_else(|| failed_precondition("authoritative_schema_baseline_column_missing"))?;
        if !matches_column(actual, *expected) { return Err(failed_precondition("authoritative_schema_baseline_column_drift")); }
    }
    let declared = actions(profile);
    let mut prefix = 0;
    let mut missing = false;
    for action in declared {
        if let Some(actual) = catalog.get(&(action.column.table.to_owned(), action.column.name.to_owned())) {
            if missing || !matches_column(actual, action.column) {
                return Err(failed_precondition("authoritative_schema_partial_action_drift"));
            }
            prefix += 1;
        } else { missing = true; }
    }
    if catalog.len() != baseline(profile).len() + prefix {
        return Err(failed_precondition("authoritative_schema_unrecognized_column"));
    }
    Ok(prefix)
}

fn business_data_empty(client: &mut impl GenericClient) -> Result<bool, DomainError> {
    for table in REQUIRED_TABLES.iter().filter(|table| **table != "trnm_schema_metadata") {
        let row = client.query_one(&format!("SELECT EXISTS (SELECT 1 FROM public.{table} LIMIT 1)"), &[])
            .map_err(map_postgres_error)?;
        if row.try_get::<_, bool>(0).map_err(map_postgres_error)? { return Ok(false); }
    }
    Ok(true)
}

fn validate_legacy_role(role: &str) -> Result<(), DomainError> {
    if role.is_empty() || role.len() > 63 || matches!(role.to_ascii_lowercase().as_str(), "public" | "root" | "admin") ||
        !role.bytes().all(|byte| byte.is_ascii_alphanumeric() || byte == b'_') || role.as_bytes()[0].is_ascii_digit() {
        return Err(invalid("invalid_legacy_storage_writer_role"));
    }
    Ok(())
}

fn verify_legacy_writer_barrier(client: &mut impl GenericClient, role: &str) -> Result<(), DomainError> {
    validate_legacy_role(role)?;
    let row = client.query_opt(
        "SELECT r.rolsuper, r.rolcreaterole, r.rolcreatedb, \
           EXISTS (SELECT 1 FROM pg_catalog.pg_roles elevated \
             WHERE (elevated.rolsuper OR elevated.rolcreaterole) \
             AND pg_has_role(r.rolname, elevated.rolname, 'MEMBER')), \
           EXISTS (SELECT 1 FROM pg_catalog.pg_class c \
             JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace \
             JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = c.relowner \
             WHERE n.nspname = 'public' AND c.relname = 'trnm_storage_objects' \
             AND pg_has_role(r.rolname, owner_role.rolname, 'MEMBER')), \
           has_table_privilege(r.rolname, 'public.trnm_storage_objects', 'INSERT'), \
           has_table_privilege(r.rolname, 'public.trnm_storage_objects', 'UPDATE'), \
           has_table_privilege(r.rolname, 'public.trnm_storage_objects', 'DELETE'), \
           has_any_column_privilege(r.rolname, 'public.trnm_storage_objects', 'INSERT'), \
           has_any_column_privilege(r.rolname, 'public.trnm_storage_objects', 'UPDATE') \
         FROM pg_catalog.pg_roles r WHERE r.rolname = $1", &[&role])
        .map_err(map_postgres_error)?.ok_or_else(|| failed_precondition("legacy_storage_writer_role_missing"))?;
    for index in 0..10 {
        if row.try_get::<_, bool>(index).map_err(map_postgres_error)? {
            return Err(failed_precondition("legacy_storage_writer_not_fenced"));
        }
    }
    Ok(())
}
