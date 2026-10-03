// Source5 AccountsV5 admission remains closed until typed migration and
// profile-owned restore/interruption evidence completes. This reader binds only
// exact native observations; it never publishes account readiness.
const ACCOUNT_CATALOG_CAPTURE_READY: bool = false;

fn require_account_catalog_capture(target: AuthoritativeSchemaTarget) -> Result<(), DomainError> {
    if target == AuthoritativeSchemaTarget::NakamaAccountsV5 && !ACCOUNT_CATALOG_CAPTURE_READY {
        return Err(failed_precondition(
            "schema5_native_catalog_capture_pending",
        ));
    }
    Ok(())
}

fn read_account_catalog(
    client: &mut impl GenericClient,
    catalog: &mut Catalog,
    users: bool,
    devices: bool,
) -> Result<(), DomainError> {
    if !users && !devices {
        return Ok(());
    }
    let native_profile = require_account_native_attribute_binding(client, catalog)?;
    require_account_relation_semantics(client, native_profile, users, devices)?;
    // Relations retain foundation-owner custody; read-only callers need not be
    // the migration role. No fixed diagnostic username or transient OID is ABI.
    let owners = client.query("SELECT c.relname,c.relkind::TEXT,c.relowner,m.relowner FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace JOIN pg_catalog.pg_class m ON m.relnamespace=n.oid AND m.relname='trnm_schema_metadata' WHERE n.nspname='public' AND c.relname IN ('users','user_device') LIMIT 3",&[]).map_err(map_postgres_error)?;
    if owners.len() != usize::from(users) + usize::from(devices) {
        return Err(failed_precondition("schema5_account_relation_drift"));
    }
    let mut owner_seen = std::collections::BTreeSet::new();
    for row in owners {
        let table: String = row.try_get(0).map_err(map_postgres_error)?;
        let kind: String = row.try_get(1).map_err(map_postgres_error)?;
        let owner: u32 = row.try_get(2).map_err(map_postgres_error)?;
        let foundation_owner: u32 = row.try_get(3).map_err(map_postgres_error)?;
        if kind != "r" || owner != foundation_owner || !owner_seen.insert(table.clone()) {
            return Err(failed_precondition("schema5_account_owner_drift"));
        }
        insert_catalog_object(catalog, &table, "@table", "BASE TABLE".to_owned())?;
    }
    let rows=client.query("SELECT t.relname,c.conname,c.contype::TEXT,c.convalidated,c.condeferrable,c.condeferred,CASE WHEN octet_length(c.conkey::TEXT) <= 512 THEN c.conkey::TEXT ELSE NULL END,CASE WHEN octet_length(c.confkey::TEXT) <= 512 THEN c.confkey::TEXT ELSE NULL END,c.confupdtype::TEXT,c.confdeltype::TEXT,c.confmatchtype::TEXT,rn.nspname,r.relname,CASE WHEN octet_length(pg_catalog.pg_get_constraintdef(c.oid)) <= 4096 THEN pg_catalog.pg_get_constraintdef(c.oid) ELSE NULL END, (COALESCE(octet_length(c.conkey::TEXT) > 512,false) OR COALESCE(octet_length(c.confkey::TEXT) > 512,false) OR COALESCE(octet_length(pg_catalog.pg_get_constraintdef(c.oid)) > 4096,false)) FROM pg_catalog.pg_constraint c JOIN pg_catalog.pg_class t ON t.oid=c.conrelid JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace LEFT JOIN pg_catalog.pg_class r ON r.oid=c.confrelid LEFT JOIN pg_catalog.pg_namespace rn ON rn.oid=r.relnamespace WHERE n.nspname='public' AND t.relname IN ('users','user_device') LIMIT 17",&[]).map_err(map_postgres_error)?;
    if rows.len() > 16 {
        return Err(failed_precondition("schema5_account_constraint_budget"));
    }
    for row in rows {
        if row.try_get::<_, bool>(14).map_err(map_postgres_error)? {
            return Err(failed_precondition(
                "schema5_account_catalog_wire_value_budget",
            ));
        }
        let table: String = row.try_get(0).map_err(map_postgres_error)?;
        let name: String = row.try_get(1).map_err(map_postgres_error)?;
        let kind: String = row.try_get(2).map_err(map_postgres_error)?;
        let validated: bool = row.try_get(3).map_err(map_postgres_error)?;
        let deferred: bool = row.try_get(4).map_err(map_postgres_error)?;
        let initially_deferred: bool = row.try_get(5).map_err(map_postgres_error)?;
        let keys: String = row.try_get(6).map_err(map_postgres_error)?;
        let reference_keys: Option<String> = row.try_get(7).map_err(map_postgres_error)?;
        let update: Option<String> = row.try_get(8).map_err(map_postgres_error)?;
        let delete: Option<String> = row.try_get(9).map_err(map_postgres_error)?;
        let match_type: Option<String> = row.try_get(10).map_err(map_postgres_error)?;
        let reference_schema: Option<String> = row.try_get(11).map_err(map_postgres_error)?;
        let reference_table: Option<String> = row.try_get(12).map_err(map_postgres_error)?;
        let definition: String = row.try_get(13).map_err(map_postgres_error)?;
        insert_catalog_object(catalog,&table,&format!("@constraint:{name}"),format!("{kind}|{validated}|{deferred}|{initially_deferred}|{keys}|{reference_keys:?}|{update:?}|{delete:?}|{match_type:?}|{reference_schema:?}|{reference_table:?}|{definition}"))?;
    }
    let database: String = client
        .query_one(
            "SELECT pg_catalog.quote_ident(pg_catalog.current_database())::TEXT",
            &[],
        )
        .map_err(map_postgres_error)?
        .try_get(0)
        .map_err(map_postgres_error)?;
    let rows=client.query("SELECT t.relname,x.relname,i.indisunique,i.indisprimary,i.indisvalid,i.indisready,CASE WHEN octet_length(i.indkey::TEXT) <= 512 THEN i.indkey::TEXT ELSE NULL END,CASE WHEN octet_length(pg_catalog.pg_get_expr(i.indpred,i.indrelid)) <= 4096 THEN pg_catalog.pg_get_expr(i.indpred,i.indrelid) ELSE NULL END,CASE WHEN octet_length(pg_catalog.pg_get_indexdef(i.indexrelid)) <= 8192 THEN pg_catalog.pg_get_indexdef(i.indexrelid) ELSE NULL END,x.relkind::TEXT,x.relowner,t.relowner,xn.nspname, (COALESCE(octet_length(i.indkey::TEXT) > 512,false) OR COALESCE(octet_length(pg_catalog.pg_get_expr(i.indpred,i.indrelid)) > 4096,false) OR COALESCE(octet_length(pg_catalog.pg_get_indexdef(i.indexrelid)) > 8192,false)) FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class t ON t.oid=i.indrelid JOIN pg_catalog.pg_class x ON x.oid=i.indexrelid JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace JOIN pg_catalog.pg_namespace xn ON xn.oid=x.relnamespace WHERE n.nspname='public' AND t.relname IN ('users','user_device') LIMIT 14",&[]).map_err(map_postgres_error)?;
    if rows.len() > 13 {
        return Err(failed_precondition("schema5_account_index_budget"));
    }
    for row in rows {
        if row.try_get::<_, bool>(13).map_err(map_postgres_error)? {
            return Err(failed_precondition(
                "schema5_account_catalog_wire_value_budget",
            ));
        }
        let table: String = row.try_get(0).map_err(map_postgres_error)?;
        let name: String = row.try_get(1).map_err(map_postgres_error)?;
        let unique: bool = row.try_get(2).map_err(map_postgres_error)?;
        let primary: bool = row.try_get(3).map_err(map_postgres_error)?;
        let valid: bool = row.try_get(4).map_err(map_postgres_error)?;
        let ready: bool = row.try_get(5).map_err(map_postgres_error)?;
        let keys: String = row.try_get(6).map_err(map_postgres_error)?;
        let predicate: Option<String> = row.try_get(7).map_err(map_postgres_error)?;
        let definition: String = row.try_get(8).map_err(map_postgres_error)?;
        let relkind: String = row.try_get(9).map_err(map_postgres_error)?;
        let owner: u32 = row.try_get(10).map_err(map_postgres_error)?;
        let table_owner: u32 = row.try_get(11).map_err(map_postgres_error)?;
        let schema: String = row.try_get(12).map_err(map_postgres_error)?;
        if relkind != "i" || owner != table_owner || schema != "public" {
            return Err(failed_precondition("schema5_account_index_owner_drift"));
        }
        let object_name = format!("@index:{name}");
        let actual =
            format!("{unique}|{primary}|{valid}|{ready}|{keys}|{predicate:?}|{definition}");
        let expected = [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb]
            .into_iter()
            .flat_map(|p| account_catalog_objects(p, &table))
            .find(|o| o.name == object_name && o.kind.replace("{database}", &database) == actual)
            .ok_or_else(|| failed_precondition("schema5_account_index_drift"))?;
        insert_catalog_object(catalog, &table, &object_name, expected.kind.to_owned())?;
    }
    // FK triggers are verified independently from the constraint record.
    require_account_trigger_binding(client, devices, catalog)?;
    if users {
        let rows=client.query("SELECT username FROM public.users WHERE id='00000000-0000-0000-0000-000000000000'::UUID LIMIT 2",&[]).map_err(map_postgres_error)?;
        if rows.len() > 1 {
            return Err(failed_precondition("schema5_system_user_drift"));
        }
        if let Some(row) = rows.first() {
            let username: String = row.try_get(0).map_err(map_postgres_error)?;
            if !username.is_empty() {
                return Err(failed_precondition("schema5_system_user_drift"));
            }
            insert_catalog_object(catalog, "users", "@system-user", "present".to_owned())?;
        }
    }
    Ok(())
}
fn matches_account_column(
    actual: &CatalogColumn,
    expected: ColumnDescriptor,
    profile: DatabaseProfile,
) -> bool {
    if expected.name.starts_with('@') {
        actual.kind == expected.kind
            && !actual.nullable
            && actual.default.is_none()
            && actual.character_maximum_length.is_none()
    } else {
        matches_observed_account_column(actual, expected, profile)
    }
}

#[cfg(test)]
mod account_catalog_tests {
    use super::*;
    #[test]
    fn pending_accounts_capture_rejects_before_ddl_and_preserves_storage_target() {
        assert!(require_account_catalog_capture(AuthoritativeSchemaTarget::StorageV4).is_ok());
        assert_eq!(
            require_account_catalog_capture(AuthoritativeSchemaTarget::NakamaAccountsV5)
                .unwrap_err()
                .reason(),
            "schema5_native_catalog_capture_pending"
        );
        assert_eq!(AuthoritativeSchemaTarget::StorageV4.version(), 4);
        assert_eq!(AuthoritativeSchemaTarget::NakamaAccountsV5.version(), 5);
        assert_eq!(AUTHORITATIVE_STORAGE_WRITER_EPOCH, 4);
    }
}
