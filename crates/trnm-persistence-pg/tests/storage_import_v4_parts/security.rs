fn native_search_path_attack(target: &Database, repository: &mut PgRepository) -> String {
    let mut control = target.client();
    control.batch_execute("CREATE SCHEMA import_attack; CREATE TABLE import_attack.path_observations(note TEXT PRIMARY KEY,path TEXT NOT NULL)").unwrap();
    // Actual SQL UDF shadowing is observed before testing the guarded importer.
    // Each native fixture must actually demonstrate SQL scalar UDF shadowing.
    control.batch_execute("CREATE FUNCTION import_attack.octet_length(TEXT) RETURNS INTEGER LANGUAGE SQL AS $$ SELECT 0 $$").unwrap();
    let path = "import_attack, pg_catalog, public";
    control
        .batch_execute(&format!("SET search_path TO {path}"))
        .unwrap();
    assert_eq!(
        control
            .query_one("SELECT octet_length('native payload'::TEXT)::INT8", &[])
            .unwrap()
            .get::<_, i64>(0),
        0
    );
    repository
        .execute_migration_batch(&format!("SET search_path TO {path}"))
        .unwrap();
    repository.execute_migration_batch("INSERT INTO import_attack.path_observations VALUES('before',current_setting('search_path'))").unwrap();
    control
        .query_one(
            "SELECT path FROM import_attack.path_observations WHERE note='before'",
            &[],
        )
        .unwrap()
        .get(0)
}

fn assert_search_path_restored(
    target: &Database,
    repository: &mut PgRepository,
    expected: &str,
    note: &str,
) {
    assert!(matches!(note, "preflight" | "first-page"));
    repository.execute_migration_batch(&format!("INSERT INTO import_attack.path_observations VALUES('{note}',current_setting('search_path'))")).unwrap();
    let actual: String = target
        .client()
        .query_one(
            "SELECT path FROM import_attack.path_observations WHERE note=$1",
            &[&note],
        )
        .unwrap()
        .get(0);
    assert_eq!(
        actual, expected,
        "import SET LOCAL leaked into the caller session"
    );
}

fn business_ids() -> (EntityId, CreateSessionFamily) {
    (
        EntityId::new([0x51; 16]),
        CreateSessionFamily {
            family: SessionFamilyId::new([0x52; 16]),
            user: UserId::new([0x11; 16]),
            refresh: RefreshTokenCredential {
                id: RefreshTokenId::new([0x53; 16]),
                digest: Digest32::new([0x54; 32]),
            },
            issued_at_ms: 42,
        },
    )
}

struct AlternativeGuards<'packet> {
    role_b: trnm_persistence_pg::CheckedStorageImport<'packet>,
    scope: trnm_persistence_pg::CheckedStorageImport<'packet>,
}

fn alternative_guards<'packet>(
    target: &mut Database,
    repository: &mut PgRepository,
    packet: &'packet VerifiedStorageExport,
    options: &StorageImportOptions,
) -> AlternativeGuards<'packet> {
    authority_grant_fence_negatives(packet);
    postgres_business_waiters_cannot_miss_import_registration(packet);
    // Acquire both checked identities on the SAME truly empty target, before
    // A begins. After A's first page, actual resume with B/scope must reject;
    // a failed preflight alone cannot prove the resume binding.
    let role_b = format!("{}_other", target.name);
    target
        .admin
        .batch_execute(&format!("CREATE ROLE {role_b}"))
        .unwrap();
    target.extra_roles.push(role_b.clone());
    let mut changed_role = options.clone();
    changed_role.legacy_writer_role = role_b;
    let role_b = repository
        .preflight_storage_import(packet, changed_role)
        .unwrap();
    let mut changed_scope = options.clone();
    changed_scope.expected_target_scope =
        IntegrityDigest::from_value(b"different independently configured target scope");
    let scope = repository
        .preflight_storage_import(packet, changed_scope)
        .unwrap();
    AlternativeGuards { role_b, scope }
}

fn fixture_identifier(value: &str) -> String {
    assert!(!value.is_empty() && value.len() <= 63);
    format!("\"{}\"", value.replace('"', "\"\""))
}

fn authority_metadata(client: &mut Client) -> Value {
    let row=client.query_one("SELECT singleton,schema_version,profile,source_commit,applied_at_ms,chain_digest,digest_algorithm,storage_writer_epoch,upgrade_source_commit,v2_apply_source_commit,v3_apply_source_commit FROM public.trnm_schema_metadata WHERE singleton=1",&[]).unwrap();
    json!([
        row.get::<_, i16>(0),
        row.get::<_, i64>(1),
        row.get::<_, String>(2),
        row.get::<_, String>(3),
        row.get::<_, i64>(4),
        row.get::<_, Option<String>>(5),
        row.get::<_, Option<String>>(6),
        row.get::<_, Option<i64>>(7),
        row.get::<_, Option<String>>(8),
        row.get::<_, Option<String>>(9),
        row.get::<_, Option<String>>(10)
    ])
}

fn authority_role_client(target: &Database, role: &str) -> Client {
    let mut config = Config::from_str(&target.url).unwrap();
    config
        .user(role)
        .password("isolated-import-authority-fixture");
    config
        .connect(NoTls)
        .expect("connect independent authority fixture role")
}

fn assert_authority_begin_rejected(
    target: &Database,
    repository: &mut PgRepository,
    checked: &trnm_persistence_pg::CheckedStorageImport<'_>,
    metadata: &Value,
) {
    let error = repository.begin_storage_import(checked).unwrap_err();
    assert_eq!(error.code(), StableCode::FailedPrecondition);
    assert!(
        matches!(
            error.reason(),
            "legacy_import_authority_writer_not_fenced"
                | "legacy_import_namespace_owner_not_fenced"
                | "legacy_import_authority_trigger_present"
                | "legacy_storage_writer_not_fenced"
        ),
        "authority grant rejected for a different reason: {}",
        error.reason()
    );
    let mut control = target.client();
    assert_eq!(counts(&mut control), (0, 0, 0));
    assert_eq!(authority_metadata(&mut control), *metadata);
}

fn authority_grant_fence_negatives(packet: &VerifiedStorageExport) {
    // A separate owned database keeps this finite privilege matrix outside
    // the main lifecycle's checked-token deadlines and retained evidence.
    let environment = live_environment().expect("authority fixture environment");
    let mut target = Database::new(&environment, "authority");
    let options = target.initialize_target(&environment);
    let role = options.legacy_writer_role.clone();
    let quoted = fixture_identifier(&role);
    let ancestor = format!("{}_ancestor", target.name);
    target
        .admin
        .batch_execute(&format!("CREATE ROLE {}", fixture_identifier(&ancestor)))
        .unwrap();
    target.extra_roles.push(ancestor.clone());
    let mut control = target.client();
    let login = if target.profile == DatabaseProfile::PostgreSql {
        "WITH LOGIN PASSWORD 'isolated-import-authority-fixture'"
    } else {
        "WITH LOGIN"
    };
    target
        .admin
        .batch_execute(&format!("ALTER ROLE {quoted} {login}"))
        .unwrap();
    target
        .admin
        .batch_execute(&format!(
            "GRANT CONNECT ON DATABASE {} TO {quoted}",
            fixture_identifier(&target.name)
        ))
        .unwrap();
    control
        .batch_execute(&format!("GRANT USAGE ON SCHEMA public TO {quoted}"))
        .unwrap();
    const TABLES: [&str; 3] = [
        "trnm_schema_metadata",
        "trnm_storage_import_jobs",
        "trnm_storage_import_pages",
    ];
    for table in TABLES {
        control
            .batch_execute(&format!(
                "GRANT SELECT ON public.{table} TO {quoted},{}",
                fixture_identifier(&ancestor)
            ))
            .unwrap();
    }
    let metadata = authority_metadata(&mut control);
    let identity = target.repository().verify_authoritative_schema().unwrap();
    let mut repository = target.repository();
    let checked = repository
        .preflight_storage_import(packet, options)
        .unwrap();
    // Direct native mutation grants, observed on a fresh role connection. No
    // role connection remains during fencing: privilege rejection cannot be
    // accidentally satisfied by the independent active-session drain check.
    for (table,privilege,probe) in [
        (TABLES[0],"UPDATE",Some("UPDATE public.trnm_schema_metadata SET source_commit=source_commit WHERE singleton=1")),
        (TABLES[1],"UPDATE",Some("UPDATE public.trnm_storage_import_jobs SET status=status,next_page=next_page,committed_rows=committed_rows WHERE singleton=1")),
        (TABLES[2],"INSERT",None),
        (TABLES[2],"DELETE",Some("DELETE FROM public.trnm_storage_import_pages WHERE page_index=-1")),
    ] {
        control.batch_execute(&format!("GRANT {privilege} ON public.{table} TO {quoted}")).unwrap();
        {
            let mut login=authority_role_client(&target,&role);
            assert!(login.query_one("SELECT pg_catalog.has_table_privilege(current_user,$1::TEXT,$2::TEXT)",&[&format!("public.{table}"),&privilege]).unwrap().get::<_,bool>(0));
            if let Some(sql)=probe {let mut transaction=login.transaction().unwrap();transaction.execute(sql,&[]).unwrap();transaction.rollback().unwrap();}
            login.close().unwrap();
        }
        assert_authority_begin_rejected(&target,&mut repository,&checked,&metadata);
        control.batch_execute(&format!("REVOKE {privilege} ON public.{table} FROM {quoted}")).unwrap();
        let mut login=authority_role_client(&target,&role);
        assert!(!login.query_one("SELECT pg_catalog.has_table_privilege(current_user,$1::TEXT,$2::TEXT)",&[&format!("public.{table}"),&privilege]).unwrap().get::<_,bool>(0));login.close().unwrap();
    }
    if target.profile == DatabaseProfile::PostgreSql {
        for (table, privilege, kind) in [
            (TABLES[0], "UPDATE(source_commit)", "UPDATE"),
            (
                TABLES[1],
                "UPDATE(status,next_page,committed_rows)",
                "UPDATE",
            ),
            (TABLES[2], "INSERT(page_index)", "INSERT"),
        ] {
            control
                .batch_execute(&format!("GRANT {privilege} ON public.{table} TO {quoted}"))
                .unwrap();
            let mut login = authority_role_client(&target, &role);
            assert!(login
                .query_one(
                    "SELECT pg_catalog.has_any_column_privilege(current_user,$1::TEXT,$2::TEXT)",
                    &[&format!("public.{table}"), &kind]
                )
                .unwrap()
                .get::<_, bool>(0));
            login.close().unwrap();
            assert_authority_begin_rejected(&target, &mut repository, &checked, &metadata);
            control
                .batch_execute(&format!(
                    "REVOKE {privilege} ON public.{table} FROM {quoted}"
                ))
                .unwrap();
        }
    }
    let destructive: &[&str] = if target.profile == DatabaseProfile::PostgreSql {
        &["TRUNCATE", "TRIGGER"]
    } else {
        &["CREATE", "DROP", "TRIGGER", "ALL"]
    };
    for table in TABLES {
        for privilege in destructive {
            control
                .batch_execute(&format!("GRANT {privilege} ON public.{table} TO {quoted}"))
                .unwrap();
            assert_authority_begin_rejected(&target, &mut repository, &checked, &metadata);
            control
                .batch_execute(&format!(
                    "REVOKE {privilege} ON public.{table} FROM {quoted}"
                ))
                .unwrap();
        }
    }
    control
        .batch_execute(&format!("GRANT UPDATE ON public.{} TO PUBLIC", TABLES[1]))
        .unwrap();
    assert_authority_begin_rejected(&target, &mut repository, &checked, &metadata);
    control
        .batch_execute(&format!(
            "REVOKE UPDATE ON public.{} FROM PUBLIC",
            TABLES[1]
        ))
        .unwrap();
    // Both profiles' inherited rights, plus PG's separate SET-only membership.
    for inherit in if target.profile == DatabaseProfile::PostgreSql {
        &[true, false][..]
    } else {
        &[true][..]
    } {
        let membership = if *inherit {
            format!("GRANT {} TO {quoted}", fixture_identifier(&ancestor))
        } else {
            format!(
                "GRANT {} TO {quoted} WITH INHERIT FALSE, SET TRUE",
                fixture_identifier(&ancestor)
            )
        };
        control.batch_execute(&membership).unwrap();
        control
            .batch_execute(&format!(
                "GRANT UPDATE ON public.{} TO {}",
                TABLES[0],
                fixture_identifier(&ancestor)
            ))
            .unwrap();
        let mut login = authority_role_client(&target, &role);
        if !inherit {
            login
                .batch_execute(&format!("SET ROLE {}", fixture_identifier(&ancestor)))
                .unwrap();
        }
        let mut transaction = login.transaction().unwrap();
        transaction.execute("UPDATE public.trnm_schema_metadata SET source_commit=source_commit WHERE singleton=1",&[]).unwrap();
        transaction.rollback().unwrap();
        login.close().unwrap();
        assert_authority_begin_rejected(&target, &mut repository, &checked, &metadata);
        control
            .batch_execute(&format!(
                "REVOKE UPDATE ON public.{} FROM {}; REVOKE {} FROM {quoted}",
                TABLES[0],
                fixture_identifier(&ancestor),
                fixture_identifier(&ancestor)
            ))
            .unwrap();
    }
    for table in TABLES {
        let owner:String=control.query_one("SELECT r.rolname::TEXT FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace JOIN pg_catalog.pg_roles r ON r.oid=c.relowner WHERE n.nspname='public' AND c.relname=$1",&[&table]).unwrap().get(0);
        control
            .batch_execute(&format!("ALTER TABLE public.{table} OWNER TO {quoted}"))
            .unwrap();
        assert_authority_begin_rejected(&target, &mut repository, &checked, &metadata);
        control
            .batch_execute(&format!(
                "ALTER TABLE public.{table} OWNER TO {}",
                fixture_identifier(&owner)
            ))
            .unwrap();
    }
    let schema_owner:String=control.query_one("SELECT r.rolname::TEXT FROM pg_catalog.pg_namespace n JOIN pg_catalog.pg_roles r ON r.oid=n.nspowner WHERE n.nspname='public'",&[]).unwrap().get(0);
    control
        .batch_execute(&format!("ALTER SCHEMA public OWNER TO {quoted}"))
        .unwrap();
    assert_authority_begin_rejected(&target, &mut repository, &checked, &metadata);
    control
        .batch_execute(&format!(
            "ALTER SCHEMA public OWNER TO {}",
            fixture_identifier(&schema_owner)
        ))
        .unwrap();
    let database_owner:String=control.query_one("SELECT r.rolname::TEXT FROM pg_catalog.pg_database d JOIN pg_catalog.pg_roles r ON r.oid=d.datdba WHERE d.datname=pg_catalog.current_database()",&[]).unwrap().get(0);
    target
        .admin
        .batch_execute(&format!(
            "ALTER DATABASE {} OWNER TO {quoted}",
            fixture_identifier(&target.name)
        ))
        .unwrap();
    assert_authority_begin_rejected(&target, &mut repository, &checked, &metadata);
    target
        .admin
        .batch_execute(&format!(
            "ALTER DATABASE {} OWNER TO {}",
            fixture_identifier(&target.name),
            fixture_identifier(&database_owner)
        ))
        .unwrap();
    authority_existing_trigger_negative(&target, &mut repository, &checked, &metadata);
    assert_eq!(
        target.repository().verify_authoritative_schema().unwrap(),
        identity
    );
    assert_eq!(authority_metadata(&mut control), metadata);
    assert_eq!(counts(&mut control), (0, 0, 0));
    let progress = repository.begin_storage_import(&checked).unwrap();
    assert_progress(&progress, 0, false);
    // The original checked token remains valid after actual grant/owner restore.
    // This separate fixture ends incomplete and is dropped as an owned DB.
}

fn authority_existing_trigger_negative(
    target: &Database,
    repository: &mut PgRepository,
    checked: &trnm_persistence_pg::CheckedStorageImport<'_>,
    metadata: &Value,
) {
    // Inventory must reject a benign existing user trigger without dropping
    // it. This case does not establish a legacy install/revoke sequence.
    // Native grammar is actually executed
    // by both profiles rather than assumed from a PostgreSQL-shaped catalog.
    let mut control = target.client();
    control.batch_execute("CREATE SCHEMA import_trigger_fixture; CREATE FUNCTION import_trigger_fixture.passthrough() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$; CREATE TRIGGER import_authority_fixture BEFORE UPDATE ON public.trnm_storage_import_jobs FOR EACH ROW EXECUTE FUNCTION import_trigger_fixture.passthrough()").unwrap();
    assert!(control.query_one("SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname='trnm_storage_import_jobs' AND NOT t.tgisinternal)",&[]).unwrap().get::<_,bool>(0));
    assert_eq!(
        repository
            .begin_storage_import(checked)
            .unwrap_err()
            .reason(),
        "legacy_import_authority_trigger_present"
    );
    assert_authority_begin_rejected(target, repository, checked, metadata);
    control.batch_execute("DROP TRIGGER import_authority_fixture ON public.trnm_storage_import_jobs; DROP SCHEMA import_trigger_fixture CASCADE").unwrap();
}

fn business_counts(target: &Database) -> (i64, i64, i64) {
    let row=target.client().query_one("SELECT (SELECT count(*)::INT8 FROM public.trnm_entity_heads),(SELECT count(*)::INT8 FROM public.trnm_session_families),(SELECT count(*)::INT8 FROM public.trnm_refresh_tokens)",&[]).unwrap();
    (row.get(0), row.get(1), row.get(2))
}

fn assert_business_blocked(target: &Database, repository: &mut PgRepository) {
    let before = business_counts(target);
    let (entity, session) = business_ids();
    assert_eq!(
        repository
            .bootstrap_entity(entity, 1, Digest32::new([0x55; 32]), 42)
            .unwrap_err()
            .code(),
        StableCode::FailedPrecondition
    );
    assert_eq!(
        repository
            .create_session_family(&session)
            .unwrap_err()
            .code(),
        StableCode::FailedPrecondition
    );
    assert_eq!(
        repository
            .verify_access_session(session.family, session.user, 0)
            .unwrap_err()
            .code(),
        StableCode::FailedPrecondition
    );
    assert_eq!(business_counts(target), before);
    let lease = trnm_persistence_pg::OutboxLease {
        id: trnm_persistence_pg::IntentId::new([0x61; 16]),
        entity,
        command: trnm_contracts::CommandId::new([0x62; 16]),
        kind: trnm_persistence_pg::IntentKind::ExternalEffect,
        payload: Digest32::new([0x63; 32]),
        attempt: 1,
        lease_generation: 1,
        owner: trnm_persistence_pg::NodeId::new([0x64; 16]),
        lease_expires_at_ms: 142,
    };
    assert_eq!(
        repository
            .claim_outbox_batch(lease.owner, 42, 100, 2, 1)
            .unwrap_err()
            .code(),
        StableCode::FailedPrecondition
    );
    assert_eq!(
        repository
            .complete_outbox(&lease, Digest32::new([0x65; 32]), 43)
            .unwrap_err()
            .code(),
        StableCode::FailedPrecondition
    );
    assert_eq!(
        repository
            .retry_or_dead_letter_outbox(&lease, 43, 44, 2, Digest32::new([0x66; 32]))
            .unwrap_err()
            .code(),
        StableCode::FailedPrecondition
    );
    assert_eq!(
        target
            .client()
            .query_one("SELECT count(*)::INT8 FROM public.trnm_outbox", &[])
            .unwrap()
            .get::<_, i64>(0),
        0
    );
}

fn dedicated_target_rejects_outbox_history(
    environment: &LiveEnvironment,
    packet: &VerifiedStorageExport,
) {
    let mut target = Database::new(environment, "outbox");
    let options = target.initialize_target(environment);
    let mut repository = target.repository();
    let checked = repository
        .preflight_storage_import(packet, options.clone())
        .unwrap();
    let entity = EntityId::new([0x71; 16]);
    repository
        .bootstrap_entity(entity, 1, Digest32::new([0x72; 32]), 1)
        .unwrap();
    repository
        .commit_command(&trnm_persistence_pg::CommitRequest {
            entity,
            command: trnm_contracts::CommandId::new([0x73; 16]),
            fingerprint: Digest32::new([0x74; 32]),
            expected_revision: 0,
            authority_generation: 1,
            next_state: Digest32::new([0x75; 32]),
            committed_at_ms: 2,
            events: vec![],
            outbox: vec![trnm_persistence_pg::OutboxInput {
                id: trnm_persistence_pg::IntentId::new([0x76; 16]),
                kind: trnm_persistence_pg::IntentKind::ExternalEffect,
                payload: Digest32::new([0x77; 32]),
                available_at_ms: 2,
            }],
        })
        .unwrap();
    let rejected = |error: trnm_contracts::DomainError| {
        assert_eq!(error.code(), StableCode::FailedPrecondition);
        assert_eq!(error.reason(), "storage_import_target_outbox_not_empty");
    };
    // A committed pending row after preflight must prevent registration.
    rejected(repository.begin_storage_import(&checked).unwrap_err());
    let owner = trnm_persistence_pg::NodeId::new([0x78; 16]);
    let leases = repository.claim_outbox(owner, 2, 100, 2, 1).unwrap();
    assert_eq!(leases.len(), 1);
    rejected(repository.begin_storage_import(&checked).unwrap_err());
    repository
        .complete_outbox(&leases[0], Digest32::new([0x79; 32]), 3)
        .unwrap();
    // A completed row is still history; it is not a worker-stop attestation.
    rejected(repository.begin_storage_import(&checked).unwrap_err());
    rejected(
        repository
            .preflight_storage_import(packet, options)
            .unwrap_err(),
    );
    assert_eq!(counts(&mut target.client()), (0, 0, 0));
    assert_eq!(
        target
            .client()
            .query_one("SELECT state::INT8 FROM public.trnm_outbox", &[])
            .unwrap()
            .get::<_, i64>(0),
        2
    );
}

fn assert_business_restored(repository: &mut PgRepository) {
    let (entity, session) = business_ids();
    repository
        .bootstrap_entity(entity, 1, Digest32::new([0x55; 32]), 42)
        .unwrap();
    repository.create_session_family(&session).unwrap();
    repository
        .verify_access_session(session.family, session.user, 0)
        .unwrap();
}

fn retain_journal(environment: &LiveEnvironment, root: &Path, client: &mut Client) {
    // Actual SQL rows, not a reconstructed log. The consumer can independently
    // rebuild every domain-separated inventory/page/prefix preimage from the
    // retained packet and these bound target-guard bytes.
    let snapshot = journal_snapshot(client);
    let jobs: Vec<Value> = snapshot["jobs"]
        .as_array()
        .unwrap()
        .iter()
        .map(|array| {
            let names = [
                "singleton",
                "manifest_sha256",
                "custody_sha256",
                "source_inventory_sha256",
                "target_schema_guard_sha256",
                "prefix_sha256",
                "source_profile",
                "source_snapshot",
                "audit_at_ms",
                "total_rows",
                "total_pages",
                "next_page",
                "committed_rows",
                "status",
            ];
            let mut object = serde_json::Map::new();
            for (index, name) in names.into_iter().enumerate() {
                object.insert(name.to_owned(), array[index].clone());
            }
            Value::Object(object)
        })
        .collect();
    let pages: Vec<Value> = snapshot["pages"]
        .as_array()
        .unwrap()
        .iter()
        .map(|array| {
            let names = [
                "manifest_sha256",
                "page_index",
                "first_ordinal",
                "row_count",
                "page_sha256",
                "prefix_sha256",
                "audit_at_ms",
            ];
            let mut object = serde_json::Map::new();
            for (index, name) in names.into_iter().enumerate() {
                object.insert(name.to_owned(), array[index].clone());
            }
            Value::Object(object)
        })
        .collect();
    write_json(
        root.join("import-journal.json"),
        &json!({
            "schema":"trillionnium.storage-import-native-journal.v1","profile":environment.profile.metadata_value(),"jobs":jobs,"pages":pages,
            "compatibility_credit":false,"production_ready":false,"full_nakama_replacement":false
        }),
    );
}

fn postgres_business_waiters_cannot_miss_import_registration(packet: &VerifiedStorageExport) {
    let environment = live_environment().expect("business admission fixture environment");
    if environment.profile != DatabaseProfile::PostgreSql {
        // CR's native wait/snapshot behavior has a separate mechanism probe.
        // This regression exercises the PostgreSQL MVCC tuple-rewrite rule.
        return;
    }
    for verify_access in [false, true] {
        let mut target = Database::new(
            &environment,
            if verify_access {
                "wait_verify"
            } else {
                "wait_create"
            },
        );
        let options = target.initialize_target(&environment);
        let (_, request) = business_ids();
        if verify_access {
            target.repository().create_session_family(&request).unwrap();
        }
        let importer_tag = format!("{}_import", target.name);
        let business_tag = format!("{}_business", target.name);
        let mut importer = PgRepository::connect(
            &format!("{}&application_name={importer_tag}", target.url),
            target.profile,
        )
        .unwrap();
        let mut business = PgRepository::connect(
            &format!("{}&application_name={business_tag}", target.url),
            target.profile,
        )
        .unwrap();
        importer
            .execute_migration_batch("SET statement_timeout = '5s'")
            .unwrap();
        business
            .execute_migration_batch("SET statement_timeout = '5s'")
            .unwrap();
        let checked = importer.preflight_storage_import(packet, options).unwrap();
        let mut observer = target.client();
        observer
            .batch_execute("SET statement_timeout = '1s'")
            .unwrap();
        let metadata = authority_metadata(&mut observer);
        let before = business_counts(&target);
        let importer_pid: i32 = observer.query_one("SELECT pid FROM pg_catalog.pg_stat_activity WHERE datname=pg_catalog.current_database() AND application_name=$1", &[&importer_tag]).unwrap().get(0);
        let business_pid: i32 = observer.query_one("SELECT pid FROM pg_catalog.pg_stat_activity WHERE datname=pg_catalog.current_database() AND application_name=$1", &[&business_tag]).unwrap().get(0);
        let mut blocker = target.client();
        let blocker_pid: i32 = blocker
            .query_one("SELECT pg_catalog.pg_backend_pid()", &[])
            .unwrap()
            .get(0);
        let mut held = blocker
            .build_transaction()
            .isolation_level(postgres::IsolationLevel::Serializable)
            .start()
            .unwrap();
        held.query_one(
            "SELECT singleton FROM public.trnm_schema_metadata WHERE singleton=1 FOR UPDATE",
            &[],
        )
        .unwrap();
        let deadline = std::time::Instant::now() + Duration::from_secs(3);
        let (registered, admitted) = std::thread::scope(|scope| {
            let registering = scope.spawn(|| importer.begin_storage_import(&checked));
            wait_for_postgres_metadata_blocker(&mut observer, importer_pid, blocker_pid, deadline);
            let serving = scope.spawn(|| {
                if verify_access {
                    business
                        .verify_access_session(request.family, request.user, 0)
                        .map(|_| ())
                } else {
                    business.create_session_family(&request).map(|_| ())
                }
            });
            // pg_blocking_pids also reports an earlier incompatible waiter.
            // Confirm the real importer is ahead of business in the lock queue.
            wait_for_postgres_metadata_blocker(&mut observer, business_pid, importer_pid, deadline);
            held.commit().unwrap();
            (registering.join().unwrap(), serving.join().unwrap())
        });
        assert_progress(&registered.unwrap(), 0, false);
        let error = admitted.unwrap_err();
        assert_eq!(error.code(), StableCode::Aborted);
        assert_eq!(error.reason(), "database_serialization_failure");
        assert_eq!(business_counts(&target), before);
        assert_eq!(counts(&mut observer), (0, 1, 0));
        assert_eq!(authority_metadata(&mut observer), metadata);
    }
}

fn wait_for_postgres_metadata_blocker(
    observer: &mut Client,
    waiter: i32,
    blocker: i32,
    deadline: std::time::Instant,
) {
    loop {
        let blocked: bool = observer
            .query_one(
                "SELECT $1::INTEGER = ANY(pg_catalog.pg_blocking_pids($2::INTEGER))",
                &[&blocker, &waiter],
            )
            .unwrap()
            .get(0);
        if blocked {
            return;
        }
        assert!(
            std::time::Instant::now() < deadline,
            "actual PostgreSQL metadata wait not observed before the fixture deadline"
        );
        std::thread::sleep(Duration::from_millis(10));
    }
}
