// Account relation behavior is part of readiness, alongside native columns.
// These exact profile values were captured from the owned pinned databases.
// Cockroach's NULL partition/rewrite fields are not normalized to PG's values.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct AccountRelationSemantics<'a> {
    table_name: &'a str,
    persistence: &'a str,
    table_access_method_oid: u32,
    row_security: bool,
    force_row_security: bool,
    has_subclass: bool,
    has_rules: bool,
    is_partition: Option<bool>,
    rewrite_oid: Option<u32>,
    has_policy: bool,
    has_inheritance: bool,
    has_rewrite: bool,
}

const fn account_relation_semantics(
    profile: DatabaseProfile,
    table_name: &str,
) -> AccountRelationSemantics<'_> {
    let (table_access_method_oid, is_partition, rewrite_oid) = match profile {
        DatabaseProfile::PostgreSql => (2, Some(false), Some(0)),
        DatabaseProfile::CockroachDb => (2_631_952_481, None, None),
    };
    AccountRelationSemantics {
        table_name,
        persistence: "p",
        table_access_method_oid,
        row_security: false,
        force_row_security: false,
        has_subclass: false,
        has_rules: false,
        is_partition,
        rewrite_oid,
        has_policy: false,
        has_inheritance: false,
        has_rewrite: false,
    }
}

fn matches_account_relation_semantics(
    profile: DatabaseProfile,
    actual: AccountRelationSemantics<'_>,
) -> bool {
    matches!(actual.table_name, "users" | "user_device")
        && actual == account_relation_semantics(profile, actual.table_name)
}

fn require_account_relation_semantics(
    client: &mut impl GenericClient,
    profile: DatabaseProfile,
    users: bool,
    devices: bool,
) -> Result<(), DomainError> {
    let rows = client.query(
        "SELECT c.relname,c.relpersistence::TEXT,c.relam,c.relrowsecurity,c.relforcerowsecurity,c.relhassubclass,c.relhasrules,c.relispartition,c.relrewrite,EXISTS(SELECT 1 FROM pg_catalog.pg_policy p WHERE p.polrelid=c.oid),EXISTS(SELECT 1 FROM pg_catalog.pg_inherits i WHERE i.inhrelid=c.oid OR i.inhparent=c.oid),EXISTS(SELECT 1 FROM pg_catalog.pg_rewrite r WHERE r.ev_class=c.oid) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname IN ('users','user_device') LIMIT 3",
        &[],
    ).map_err(map_postgres_error)?;
    if rows.len() != usize::from(users) + usize::from(devices) {
        return Err(failed_precondition(
            "schema5_account_relation_semantic_inventory",
        ));
    }
    let mut seen = std::collections::BTreeSet::new();
    for row in rows {
        // Names are selected from two fixed names; persistence is native CHAR.
        // All remaining fields are fixed-width bool/OID values or nullable OIDs.
        let table: String = row.try_get(0).map_err(map_postgres_error)?;
        let persistence: String = row.try_get(1).map_err(map_postgres_error)?;
        let actual = AccountRelationSemantics {
            table_name: &table,
            persistence: &persistence,
            table_access_method_oid: row.try_get(2).map_err(map_postgres_error)?,
            row_security: row.try_get(3).map_err(map_postgres_error)?,
            force_row_security: row.try_get(4).map_err(map_postgres_error)?,
            has_subclass: row.try_get(5).map_err(map_postgres_error)?,
            has_rules: row.try_get(6).map_err(map_postgres_error)?,
            is_partition: row.try_get(7).map_err(map_postgres_error)?,
            rewrite_oid: row.try_get(8).map_err(map_postgres_error)?,
            has_policy: row.try_get(9).map_err(map_postgres_error)?,
            has_inheritance: row.try_get(10).map_err(map_postgres_error)?,
            has_rewrite: row.try_get(11).map_err(map_postgres_error)?,
        };
        if !matches_account_relation_semantics(profile, actual)
            || !seen.insert(table.clone())
            || match table.as_str() {
                "users" => !users,
                "user_device" => !devices,
                _ => true,
            }
        {
            return Err(failed_precondition(
                "schema5_account_relation_semantic_drift",
            ));
        }
    }
    Ok(())
}

#[cfg(test)]
mod account_relation_semantic_tests {
    use super::*;

    #[test]
    fn profile_owned_relation_baselines_reject_cross_profile_and_unknown_table() {
        for profile in [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb] {
            for table in ["users", "user_device"] {
                let baseline = account_relation_semantics(profile, table);
                assert!(matches_account_relation_semantics(profile, baseline));
                let other = match profile {
                    DatabaseProfile::PostgreSql => DatabaseProfile::CockroachDb,
                    DatabaseProfile::CockroachDb => DatabaseProfile::PostgreSql,
                };
                assert!(!matches_account_relation_semantics(other, baseline));
            }
            assert!(!matches_account_relation_semantics(
                profile,
                account_relation_semantics(profile, "shadow_account_child")
            ));
        }
    }

    #[test]
    fn relation_behavior_and_durability_single_field_changes_are_rejected() {
        for profile in [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb] {
            for table in ["users", "user_device"] {
                let base = account_relation_semantics(profile, table);
                let changes = [
                    AccountRelationSemantics {
                        persistence: "u",
                        ..base
                    },
                    AccountRelationSemantics {
                        persistence: "t",
                        ..base
                    },
                    AccountRelationSemantics {
                        table_access_method_oid: 0,
                        ..base
                    },
                    AccountRelationSemantics {
                        row_security: true,
                        ..base
                    },
                    AccountRelationSemantics {
                        force_row_security: true,
                        ..base
                    },
                    AccountRelationSemantics {
                        has_subclass: true,
                        ..base
                    },
                    AccountRelationSemantics {
                        has_rules: true,
                        ..base
                    },
                    AccountRelationSemantics {
                        is_partition: Some(true),
                        ..base
                    },
                    AccountRelationSemantics {
                        rewrite_oid: Some(1),
                        ..base
                    },
                    AccountRelationSemantics {
                        has_policy: true,
                        ..base
                    },
                    AccountRelationSemantics {
                        has_inheritance: true,
                        ..base
                    },
                    AccountRelationSemantics {
                        has_rewrite: true,
                        ..base
                    },
                ];
                for changed in changes {
                    assert!(!matches_account_relation_semantics(profile, changed));
                }
            }
        }
    }

    #[test]
    fn null_placeholder_fields_cannot_borrow_postgres_false_or_zero() {
        let pg = account_relation_semantics(DatabaseProfile::PostgreSql, "users");
        let cr = account_relation_semantics(DatabaseProfile::CockroachDb, "users");
        for changed in [
            AccountRelationSemantics {
                is_partition: None,
                ..pg
            },
            AccountRelationSemantics {
                rewrite_oid: None,
                ..pg
            },
        ] {
            assert!(!matches_account_relation_semantics(
                DatabaseProfile::PostgreSql,
                changed
            ));
        }
        for changed in [
            AccountRelationSemantics {
                is_partition: Some(false),
                ..cr
            },
            AccountRelationSemantics {
                rewrite_oid: Some(0),
                ..cr
            },
        ] {
            assert!(!matches_account_relation_semantics(
                DatabaseProfile::CockroachDb,
                changed
            ));
        }
    }
}
