#[derive(Clone, Copy, Debug)]
struct AccountFkTriggerBinding {
    table: &'static str,
    prefix: &'static str,
    function_oid: u32,
    trigger_type: i16,
    native_tail: &'static str,
}
const POSTGRESQL_ACCOUNT_FK_TRIGGERS: &[AccountFkTriggerBinding] = &[
AccountFkTriggerBinding {table:"user_device",prefix:"RI_ConstraintTrigger_c_",function_oid:1644,trigger_type:5,native_tail:"AFTER INSERT ON public.user_device FROM users NOT DEFERRABLE INITIALLY IMMEDIATE FOR EACH ROW EXECUTE FUNCTION \"RI_FKey_check_ins\"()"},
AccountFkTriggerBinding {table:"user_device",prefix:"RI_ConstraintTrigger_c_",function_oid:1645,trigger_type:17,native_tail:"AFTER UPDATE ON public.user_device FROM users NOT DEFERRABLE INITIALLY IMMEDIATE FOR EACH ROW EXECUTE FUNCTION \"RI_FKey_check_upd\"()"},
AccountFkTriggerBinding {table:"users",prefix:"RI_ConstraintTrigger_a_",function_oid:1646,trigger_type:9,native_tail:"AFTER DELETE ON public.users FROM user_device NOT DEFERRABLE INITIALLY IMMEDIATE FOR EACH ROW EXECUTE FUNCTION \"RI_FKey_cascade_del\"()"},
AccountFkTriggerBinding {table:"users",prefix:"RI_ConstraintTrigger_a_",function_oid:1655,trigger_type:17,native_tail:"AFTER UPDATE ON public.users FROM user_device NOT DEFERRABLE INITIALLY IMMEDIATE FOR EACH ROW EXECUTE FUNCTION \"RI_FKey_noaction_upd\"()"},
];
fn require_account_trigger_binding(
    client: &mut impl GenericClient,
    devices: bool,
    catalog: &mut Catalog,
) -> Result<(), DomainError> {
    let rows=client.query("SELECT c.relname,t.oid,t.tgname,t.tgisinternal,t.tgenabled::TEXT,t.tgfoid,t.tgtype,t.tgconstraint,k.conname,k.contype::TEXT,t.tgdeferrable,t.tginitdeferred,CASE WHEN octet_length(t.tgargs::TEXT) <= 512 THEN t.tgargs::TEXT ELSE NULL END,CASE WHEN octet_length(pg_catalog.pg_get_triggerdef(t.oid)) <= 4096 THEN pg_catalog.pg_get_triggerdef(t.oid) ELSE NULL END,ct.relname,cn.nspname,rt.relname,rn.nspname, (COALESCE(octet_length(t.tgargs::TEXT) > 512,false) OR COALESCE(octet_length(pg_catalog.pg_get_triggerdef(t.oid)) > 4096,false)) FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_catalog.pg_constraint k ON k.oid=t.tgconstraint LEFT JOIN pg_catalog.pg_class ct ON ct.oid=k.conrelid LEFT JOIN pg_catalog.pg_namespace cn ON cn.oid=ct.relnamespace LEFT JOIN pg_catalog.pg_class rt ON rt.oid=k.confrelid LEFT JOIN pg_catalog.pg_namespace rn ON rn.oid=rt.relnamespace WHERE n.nspname='public' AND c.relname IN ('users','user_device','trnm_schema_metadata') LIMIT 6",&[]).map_err(map_postgres_error)?;
    if !devices {
        if !rows.is_empty() {
            return Err(failed_precondition("schema5_account_trigger_drift"));
        }
        return Ok(());
    }
    if rows.is_empty() {
        return insert_catalog_object(
            catalog,
            "user_device",
            "@fk-triggers",
            "cockroachdb-native-no-pg-trigger-rows".to_owned(),
        );
    }
    if rows.len() != 4 {
        return Err(failed_precondition("schema5_account_trigger_drift"));
    }
    let mut seen = std::collections::BTreeSet::new();
    let mut constraints = std::collections::BTreeSet::new();
    for row in rows {
        if row.try_get::<_, bool>(18).map_err(map_postgres_error)? {
            return Err(failed_precondition(
                "schema5_account_catalog_wire_value_budget",
            ));
        }
        let table: String = row.try_get(0).map_err(map_postgres_error)?;
        let oid: u32 = row.try_get(1).map_err(map_postgres_error)?;
        let name: String = row.try_get(2).map_err(map_postgres_error)?;
        let internal: bool = row.try_get(3).map_err(map_postgres_error)?;
        let enabled: String = row.try_get(4).map_err(map_postgres_error)?;
        let function_oid: u32 = row.try_get(5).map_err(map_postgres_error)?;
        let trigger_type: i16 = row.try_get(6).map_err(map_postgres_error)?;
        let constraint_oid: u32 = row.try_get(7).map_err(map_postgres_error)?;
        let constraint_name: Option<String> = row.try_get(8).map_err(map_postgres_error)?;
        let constraint_type: Option<String> = row.try_get(9).map_err(map_postgres_error)?;
        let deferred: bool = row.try_get(10).map_err(map_postgres_error)?;
        let initially_deferred: bool = row.try_get(11).map_err(map_postgres_error)?;
        let arguments: String = row.try_get(12).map_err(map_postgres_error)?;
        let definition: String = row.try_get(13).map_err(map_postgres_error)?;
        let constraint_table: Option<String> = row.try_get(14).map_err(map_postgres_error)?;
        let constraint_schema: Option<String> = row.try_get(15).map_err(map_postgres_error)?;
        let reference_table: Option<String> = row.try_get(16).map_err(map_postgres_error)?;
        let reference_schema: Option<String> = row.try_get(17).map_err(map_postgres_error)?;
        let b = POSTGRESQL_ACCOUNT_FK_TRIGGERS
            .iter()
            .find(|b| {
                b.table == table && b.function_oid == function_oid && b.trigger_type == trigger_type
            })
            .ok_or_else(|| failed_precondition("schema5_account_trigger_drift"))?;
        if oid == 0
            || constraint_oid == 0
            || !internal
            || enabled != "O"
            || deferred
            || initially_deferred
            || arguments != "\\x"
            || name != format!("{}{oid}", b.prefix)
            || definition != format!("CREATE CONSTRAINT TRIGGER \"{name}\" {}", b.native_tail)
            || constraint_name.as_deref() != Some("user_device_user_id_fkey")
            || constraint_type.as_deref() != Some("f")
            || constraint_table.as_deref() != Some("user_device")
            || constraint_schema.as_deref() != Some("public")
            || reference_table.as_deref() != Some("users")
            || reference_schema.as_deref() != Some("public")
            || !seen.insert((table, function_oid, trigger_type))
        {
            return Err(failed_precondition("schema5_account_trigger_drift"));
        }
        constraints.insert(constraint_oid);
    }
    if constraints.len() != 1 {
        return Err(failed_precondition("schema5_account_trigger_drift"));
    }
    insert_catalog_object(
        catalog,
        "user_device",
        "@fk-triggers",
        "postgresql-native-four-fk-triggers".to_owned(),
    )
}
