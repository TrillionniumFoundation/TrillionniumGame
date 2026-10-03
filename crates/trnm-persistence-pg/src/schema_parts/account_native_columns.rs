// Exact information_schema column observations from root-owned SQL5 diagnostics.
// The account target remains closed pending constraints, indexes, trigger and migration checks.
#[derive(Clone, Copy, Debug)]
struct AccountColumnBinding {
    table: &'static str,
    name: &'static str,
    kind: &'static str,
    nullable: bool,
    maximum_length: Option<i64>,
    default: Option<&'static str>,
}
const POSTGRESQL_ACCOUNT_COLUMNS: &[AccountColumnBinding] = &[
    AccountColumnBinding {
        table: "user_device",
        name: "id",
        kind: "varchar",
        nullable: false,
        maximum_length: Some(128),
        default: None,
    },
    AccountColumnBinding {
        table: "user_device",
        name: "user_id",
        kind: "uuid",
        nullable: false,
        maximum_length: None,
        default: None,
    },
    AccountColumnBinding {
        table: "user_device",
        name: "preferences",
        kind: "jsonb",
        nullable: false,
        maximum_length: None,
        default: Some("'{}'::jsonb"),
    },
    AccountColumnBinding {
        table: "user_device",
        name: "push_token_amazon",
        kind: "varchar",
        nullable: false,
        maximum_length: Some(512),
        default: Some("''::character varying"),
    },
    AccountColumnBinding {
        table: "user_device",
        name: "push_token_android",
        kind: "varchar",
        nullable: false,
        maximum_length: Some(512),
        default: Some("''::character varying"),
    },
    AccountColumnBinding {
        table: "user_device",
        name: "push_token_huawei",
        kind: "varchar",
        nullable: false,
        maximum_length: Some(512),
        default: Some("''::character varying"),
    },
    AccountColumnBinding {
        table: "user_device",
        name: "push_token_ios",
        kind: "varchar",
        nullable: false,
        maximum_length: Some(512),
        default: Some("''::character varying"),
    },
    AccountColumnBinding {
        table: "user_device",
        name: "push_token_web",
        kind: "varchar",
        nullable: false,
        maximum_length: Some(512),
        default: Some("''::character varying"),
    },
    AccountColumnBinding {
        table: "users",
        name: "id",
        kind: "uuid",
        nullable: false,
        maximum_length: None,
        default: None,
    },
    AccountColumnBinding {
        table: "users",
        name: "username",
        kind: "varchar",
        nullable: false,
        maximum_length: Some(128),
        default: None,
    },
    AccountColumnBinding {
        table: "users",
        name: "display_name",
        kind: "varchar",
        nullable: true,
        maximum_length: Some(255),
        default: None,
    },
    AccountColumnBinding {
        table: "users",
        name: "avatar_url",
        kind: "varchar",
        nullable: true,
        maximum_length: Some(512),
        default: None,
    },
    AccountColumnBinding {
        table: "users",
        name: "lang_tag",
        kind: "varchar",
        nullable: false,
        maximum_length: Some(18),
        default: Some("'en'::character varying"),
    },
    AccountColumnBinding {
        table: "users",
        name: "location",
        kind: "varchar",
        nullable: true,
        maximum_length: Some(255),
        default: None,
    },
    AccountColumnBinding {
        table: "users",
        name: "timezone",
        kind: "varchar",
        nullable: true,
        maximum_length: Some(255),
        default: None,
    },
    AccountColumnBinding {
        table: "users",
        name: "metadata",
        kind: "jsonb",
        nullable: false,
        maximum_length: None,
        default: Some("'{}'::jsonb"),
    },
    AccountColumnBinding {
        table: "users",
        name: "wallet",
        kind: "jsonb",
        nullable: false,
        maximum_length: None,
        default: Some("'{}'::jsonb"),
    },
    AccountColumnBinding {
        table: "users",
        name: "email",
        kind: "varchar",
        nullable: true,
        maximum_length: Some(255),
        default: None,
    },
    AccountColumnBinding {
        table: "users",
        name: "password",
        kind: "bytea",
        nullable: true,
        maximum_length: None,
        default: None,
    },
    AccountColumnBinding {
        table: "users",
        name: "facebook_id",
        kind: "varchar",
        nullable: true,
        maximum_length: Some(128),
        default: None,
    },
    AccountColumnBinding {
        table: "users",
        name: "google_id",
        kind: "varchar",
        nullable: true,
        maximum_length: Some(128),
        default: None,
    },
    AccountColumnBinding {
        table: "users",
        name: "gamecenter_id",
        kind: "varchar",
        nullable: true,
        maximum_length: Some(128),
        default: None,
    },
    AccountColumnBinding {
        table: "users",
        name: "steam_id",
        kind: "varchar",
        nullable: true,
        maximum_length: Some(128),
        default: None,
    },
    AccountColumnBinding {
        table: "users",
        name: "custom_id",
        kind: "varchar",
        nullable: true,
        maximum_length: Some(128),
        default: None,
    },
    AccountColumnBinding {
        table: "users",
        name: "edge_count",
        kind: "int4",
        nullable: false,
        maximum_length: None,
        default: Some("0"),
    },
    AccountColumnBinding {
        table: "users",
        name: "create_time",
        kind: "timestamptz",
        nullable: false,
        maximum_length: None,
        default: Some("now()"),
    },
    AccountColumnBinding {
        table: "users",
        name: "update_time",
        kind: "timestamptz",
        nullable: false,
        maximum_length: None,
        default: Some("now()"),
    },
    AccountColumnBinding {
        table: "users",
        name: "verify_time",
        kind: "timestamptz",
        nullable: false,
        maximum_length: None,
        default: Some("'1970-01-01 00:00:00+00'::timestamp with time zone"),
    },
    AccountColumnBinding {
        table: "users",
        name: "disable_time",
        kind: "timestamptz",
        nullable: false,
        maximum_length: None,
        default: Some("'1970-01-01 00:00:00+00'::timestamp with time zone"),
    },
    AccountColumnBinding {
        table: "users",
        name: "facebook_instant_game_id",
        kind: "varchar",
        nullable: true,
        maximum_length: Some(128),
        default: None,
    },
    AccountColumnBinding {
        table: "users",
        name: "apple_id",
        kind: "varchar",
        nullable: true,
        maximum_length: Some(128),
        default: None,
    },
];
const COCKROACHDB_ACCOUNT_COLUMNS: &[AccountColumnBinding] = &[
    AccountColumnBinding {
        table: "user_device",
        name: "id",
        kind: "varchar",
        nullable: false,
        maximum_length: Some(128),
        default: None,
    },
    AccountColumnBinding {
        table: "user_device",
        name: "user_id",
        kind: "uuid",
        nullable: false,
        maximum_length: None,
        default: None,
    },
    AccountColumnBinding {
        table: "user_device",
        name: "preferences",
        kind: "jsonb",
        nullable: false,
        maximum_length: None,
        default: Some("'{}'"),
    },
    AccountColumnBinding {
        table: "user_device",
        name: "push_token_amazon",
        kind: "varchar",
        nullable: false,
        maximum_length: Some(512),
        default: Some("''"),
    },
    AccountColumnBinding {
        table: "user_device",
        name: "push_token_android",
        kind: "varchar",
        nullable: false,
        maximum_length: Some(512),
        default: Some("''"),
    },
    AccountColumnBinding {
        table: "user_device",
        name: "push_token_huawei",
        kind: "varchar",
        nullable: false,
        maximum_length: Some(512),
        default: Some("''"),
    },
    AccountColumnBinding {
        table: "user_device",
        name: "push_token_ios",
        kind: "varchar",
        nullable: false,
        maximum_length: Some(512),
        default: Some("''"),
    },
    AccountColumnBinding {
        table: "user_device",
        name: "push_token_web",
        kind: "varchar",
        nullable: false,
        maximum_length: Some(512),
        default: Some("''"),
    },
    AccountColumnBinding {
        table: "users",
        name: "id",
        kind: "uuid",
        nullable: false,
        maximum_length: None,
        default: None,
    },
    AccountColumnBinding {
        table: "users",
        name: "username",
        kind: "varchar",
        nullable: false,
        maximum_length: Some(128),
        default: None,
    },
    AccountColumnBinding {
        table: "users",
        name: "display_name",
        kind: "varchar",
        nullable: true,
        maximum_length: Some(255),
        default: None,
    },
    AccountColumnBinding {
        table: "users",
        name: "avatar_url",
        kind: "varchar",
        nullable: true,
        maximum_length: Some(512),
        default: None,
    },
    AccountColumnBinding {
        table: "users",
        name: "lang_tag",
        kind: "varchar",
        nullable: false,
        maximum_length: Some(18),
        default: Some("'en'"),
    },
    AccountColumnBinding {
        table: "users",
        name: "location",
        kind: "varchar",
        nullable: true,
        maximum_length: Some(255),
        default: None,
    },
    AccountColumnBinding {
        table: "users",
        name: "timezone",
        kind: "varchar",
        nullable: true,
        maximum_length: Some(255),
        default: None,
    },
    AccountColumnBinding {
        table: "users",
        name: "metadata",
        kind: "jsonb",
        nullable: false,
        maximum_length: None,
        default: Some("'{}'"),
    },
    AccountColumnBinding {
        table: "users",
        name: "wallet",
        kind: "jsonb",
        nullable: false,
        maximum_length: None,
        default: Some("'{}'"),
    },
    AccountColumnBinding {
        table: "users",
        name: "email",
        kind: "varchar",
        nullable: true,
        maximum_length: Some(255),
        default: None,
    },
    AccountColumnBinding {
        table: "users",
        name: "password",
        kind: "bytea",
        nullable: true,
        maximum_length: None,
        default: None,
    },
    AccountColumnBinding {
        table: "users",
        name: "facebook_id",
        kind: "varchar",
        nullable: true,
        maximum_length: Some(128),
        default: None,
    },
    AccountColumnBinding {
        table: "users",
        name: "google_id",
        kind: "varchar",
        nullable: true,
        maximum_length: Some(128),
        default: None,
    },
    AccountColumnBinding {
        table: "users",
        name: "gamecenter_id",
        kind: "varchar",
        nullable: true,
        maximum_length: Some(128),
        default: None,
    },
    AccountColumnBinding {
        table: "users",
        name: "steam_id",
        kind: "varchar",
        nullable: true,
        maximum_length: Some(128),
        default: None,
    },
    AccountColumnBinding {
        table: "users",
        name: "custom_id",
        kind: "varchar",
        nullable: true,
        maximum_length: Some(128),
        default: None,
    },
    AccountColumnBinding {
        table: "users",
        name: "edge_count",
        kind: "int8",
        nullable: false,
        maximum_length: None,
        default: Some("0"),
    },
    AccountColumnBinding {
        table: "users",
        name: "create_time",
        kind: "timestamptz",
        nullable: false,
        maximum_length: None,
        default: Some("now()"),
    },
    AccountColumnBinding {
        table: "users",
        name: "update_time",
        kind: "timestamptz",
        nullable: false,
        maximum_length: None,
        default: Some("now()"),
    },
    AccountColumnBinding {
        table: "users",
        name: "verify_time",
        kind: "timestamptz",
        nullable: false,
        maximum_length: None,
        default: Some("'1970-01-01 00:00:00+00'"),
    },
    AccountColumnBinding {
        table: "users",
        name: "disable_time",
        kind: "timestamptz",
        nullable: false,
        maximum_length: None,
        default: Some("'1970-01-01 00:00:00+00'"),
    },
    AccountColumnBinding {
        table: "users",
        name: "facebook_instant_game_id",
        kind: "varchar",
        nullable: true,
        maximum_length: Some(128),
        default: None,
    },
    AccountColumnBinding {
        table: "users",
        name: "apple_id",
        kind: "varchar",
        nullable: true,
        maximum_length: Some(128),
        default: None,
    },
];
fn account_column_bindings(profile: DatabaseProfile) -> &'static [AccountColumnBinding] {
    match profile {
        DatabaseProfile::PostgreSql => POSTGRESQL_ACCOUNT_COLUMNS,
        DatabaseProfile::CockroachDb => COCKROACHDB_ACCOUNT_COLUMNS,
    }
}
fn matches_observed_account_column(
    actual: &CatalogColumn,
    expected: ColumnDescriptor,
    profile: DatabaseProfile,
) -> bool {
    let Some(binding) = account_column_bindings(profile)
        .iter()
        .find(|b| b.table == expected.table && b.name == expected.name)
    else {
        return false;
    };
    expected.kind == binding.kind
        && expected.nullable == binding.nullable
        && expected.character_maximum_length == binding.maximum_length
        && actual.kind == binding.kind
        && actual.nullable == binding.nullable
        && actual.character_maximum_length == binding.maximum_length
        && actual.default.as_deref() == binding.default
}
#[cfg(test)]
mod account_native_column_tests {
    use super::*;
    fn observed(b: AccountColumnBinding) -> (CatalogColumn, ColumnDescriptor) {
        (
            CatalogColumn {
                kind: b.kind.to_owned(),
                nullable: b.nullable,
                default: b.default.map(str::to_owned),
                character_maximum_length: b.maximum_length,
            },
            ColumnDescriptor {
                table: b.table,
                name: b.name,
                kind: b.kind,
                nullable: b.nullable,
                default_zero: false,
                character_maximum_length: b.maximum_length,
            },
        )
    }
    #[test]
    fn complete_two_profile_inventory_has_exact_23_and_8_columns() {
        for p in [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb] {
            let rows = account_column_bindings(p);
            assert_eq!(rows.len(), 31);
            for table in ["users", "user_device"] {
                assert_eq!(
                    rows.iter().filter(|b| b.table == table).count(),
                    if table == "users" { 23 } else { 8 }
                );
            }
            for b in rows {
                let (a, e) = observed(*b);
                assert!(matches_observed_account_column(&a, e, p));
            }
        }
    }
    #[test]
    fn one_field_drift_is_rejected_for_every_column() {
        for p in [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb] {
            for b in account_column_bindings(p) {
                let (mut a, e) = observed(*b);
                a.kind.push('x');
                assert!(!matches_observed_account_column(&a, e, p));
                let (mut a, e) = observed(*b);
                a.nullable = !a.nullable;
                assert!(!matches_observed_account_column(&a, e, p));
                let (mut a, e) = observed(*b);
                a.character_maximum_length = Some(129);
                assert!(!matches_observed_account_column(&a, e, p));
                let (mut a, e) = observed(*b);
                a.default = Some("unobserved()".to_owned());
                assert!(!matches_observed_account_column(&a, e, p));
            }
        }
    }
    #[test]
    fn int4_and_int8_are_separate_observed_edge_count_bindings() {
        let pg = POSTGRESQL_ACCOUNT_COLUMNS
            .iter()
            .find(|b| b.name == "edge_count")
            .unwrap();
        let cr = COCKROACHDB_ACCOUNT_COLUMNS
            .iter()
            .find(|b| b.name == "edge_count")
            .unwrap();
        assert_eq!(pg.kind, "int4");
        assert_eq!(cr.kind, "int8");
        let (a, e) = observed(*pg);
        assert!(!matches_observed_account_column(
            &a,
            e,
            DatabaseProfile::CockroachDb
        ));
        let (a, e) = observed(*cr);
        assert!(!matches_observed_account_column(
            &a,
            e,
            DatabaseProfile::PostgreSql
        ));
    }
    #[test]
    fn defaults_are_exact_native_bytes_and_null_is_distinct() {
        for p in [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb] {
            for b in account_column_bindings(p) {
                let (mut a, e) = observed(*b);
                a.default = if let Some(d) = b.default {
                    Some(format!(" {d}"))
                } else {
                    Some("NULL".to_owned())
                };
                assert!(!matches_observed_account_column(&a, e, p));
            }
        }
    }
}
