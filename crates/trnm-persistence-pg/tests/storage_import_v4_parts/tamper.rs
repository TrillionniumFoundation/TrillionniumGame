fn journal_snapshot(client: &mut Client) -> Value {
    let jobs:Vec<Value>=client.query("SELECT singleton,manifest_digest,custody_digest,source_inventory_digest,target_schema_guard_digest,prefix_digest,source_profile,source_snapshot,audit_at_ms,total_rows,total_pages,next_page,committed_rows,status FROM public.trnm_storage_import_jobs ORDER BY singleton",&[]).unwrap().iter().map(|row| json!([
        row.get::<_,i16>(0),hex_bytes(&row.get::<_,Vec<u8>>(1)),hex_bytes(&row.get::<_,Vec<u8>>(2)),hex_bytes(&row.get::<_,Vec<u8>>(3)),hex_bytes(&row.get::<_,Vec<u8>>(4)),hex_bytes(&row.get::<_,Vec<u8>>(5)),row.get::<_,String>(6),row.get::<_,String>(7),row.get::<_,i64>(8),row.get::<_,i64>(9),row.get::<_,i64>(10),row.get::<_,i64>(11),row.get::<_,i64>(12),row.get::<_,i16>(13)
    ])).collect();
    let pages:Vec<Value>=client.query("SELECT manifest_digest,page_index,first_ordinal,row_count,page_digest,prefix_digest,audit_at_ms FROM public.trnm_storage_import_pages ORDER BY page_index",&[]).unwrap().iter().map(|row|json!([
        hex_bytes(&row.get::<_,Vec<u8>>(0)),row.get::<_,i64>(1),row.get::<_,i64>(2),row.get::<_,i64>(3),hex_bytes(&row.get::<_,Vec<u8>>(4)),hex_bytes(&row.get::<_,Vec<u8>>(5)),row.get::<_,i64>(6)
    ])).collect();
    json!({"jobs":jobs,"pages":pages})
}

fn journal_and_prefix_tamper(
    target: &Database,
    repository: &mut PgRepository,
    checked: &trnm_persistence_pg::CheckedStorageImport<'_>,
) {
    let mut control = target.client();
    let before = journal_snapshot(&mut control);
    let data = target_rows(&mut control);
    for column in [
        "prefix_digest",
        "custody_digest",
        "source_inventory_digest",
        "target_schema_guard_digest",
    ] {
        let original: Vec<u8> = control
            .query_one(
                &format!("SELECT {column} FROM public.trnm_storage_import_jobs WHERE singleton=1"),
                &[],
            )
            .unwrap()
            .get(0);
        control
            .execute(
                &format!(
                    "UPDATE public.trnm_storage_import_jobs SET {column}=$1 WHERE singleton=1"
                ),
                &[&[0x91_u8; 32].as_slice()],
            )
            .unwrap();
        let corrupted = journal_snapshot(&mut control);
        assert!(
            repository.resume_storage_import(checked).is_err(),
            "journal {column} drift accepted"
        );
        assert_eq!(journal_snapshot(&mut control), corrupted);
        assert_eq!(target_rows(&mut control), data);
        control
            .execute(
                &format!(
                    "UPDATE public.trnm_storage_import_jobs SET {column}=$1 WHERE singleton=1"
                ),
                &[&original],
            )
            .unwrap();
    }
    for column in ["page_digest", "prefix_digest"] {
        let original: Vec<u8> = control
            .query_one(
                &format!(
                    "SELECT {column} FROM public.trnm_storage_import_pages WHERE page_index=0"
                ),
                &[],
            )
            .unwrap()
            .get(0);
        control
            .execute(
                &format!(
                    "UPDATE public.trnm_storage_import_pages SET {column}=$1 WHERE page_index=0"
                ),
                &[&[0x92_u8; 32].as_slice()],
            )
            .unwrap();
        let corrupted = journal_snapshot(&mut control);
        assert!(repository.resume_storage_import(checked).is_err());
        assert_eq!(journal_snapshot(&mut control), corrupted);
        assert_eq!(target_rows(&mut control), data);
        control
            .execute(
                &format!(
                    "UPDATE public.trnm_storage_import_pages SET {column}=$1 WHERE page_index=0"
                ),
                &[&original],
            )
            .unwrap();
    }
    for (column, wrong) in [
        ("first_ordinal", 1_i64),
        ("row_count", 1),
        ("audit_at_ms", 9876),
    ] {
        let original: i64 = control
            .query_one(
                &format!(
                    "SELECT {column} FROM public.trnm_storage_import_pages WHERE page_index=0"
                ),
                &[],
            )
            .unwrap()
            .get(0);
        control
            .execute(
                &format!(
                    "UPDATE public.trnm_storage_import_pages SET {column}=$1 WHERE page_index=0"
                ),
                &[&wrong],
            )
            .unwrap();
        let corrupted = journal_snapshot(&mut control);
        assert!(repository.resume_storage_import(checked).is_err());
        assert_eq!(journal_snapshot(&mut control), corrupted);
        control
            .execute(
                &format!(
                    "UPDATE public.trnm_storage_import_pages SET {column}=$1 WHERE page_index=0"
                ),
                &[&original],
            )
            .unwrap();
    }
    assert_eq!(journal_snapshot(&mut control), before);
    // A legal row-count/prefix checkpoint cannot substitute for missing source
    // rows or extra dedicated-target rows, even before the final page.
    control.batch_execute("UPDATE public.trnm_storage_objects SET object_key='missing-original' WHERE collection='' AND object_key='' ").unwrap();
    assert!(repository.resume_storage_import(checked).is_err());
    control.batch_execute("UPDATE public.trnm_storage_objects SET object_key='' WHERE collection='' AND object_key='missing-original'").unwrap();
    control.batch_execute("INSERT INTO public.trnm_storage_objects(collection,object_key,user_id,value_bytes,version_digest,read_permission,write_permission,updated_at_ms,create_time,update_time,value_jsonb,public_version,value_projection_digest,value_origin,source_manifest_digest) SELECT collection,'unexpected-extra',user_id,value_bytes,version_digest,read_permission,write_permission,updated_at_ms,create_time,update_time,value_jsonb,public_version,value_projection_digest,value_origin,source_manifest_digest FROM public.trnm_storage_objects WHERE collection='' AND object_key=''").unwrap();
    assert!(repository.resume_storage_import(checked).is_err());
    control.batch_execute("DELETE FROM public.trnm_storage_objects WHERE collection='' AND object_key='unexpected-extra'").unwrap();
    assert_eq!(target_rows(&mut control), data);
    assert_eq!(journal_snapshot(&mut control), before);
}

fn role_and_scope_binding(
    target: &mut Database,
    repository: &mut PgRepository,
    checked: &trnm_persistence_pg::CheckedStorageImport<'_>,
    packet: &VerifiedStorageExport,
    options: &StorageImportOptions,
    alternative: &AlternativeGuards<'_>,
) {
    let mut control = target.client();
    let before = journal_snapshot(&mut control);
    let data = target_rows(&mut control);
    let mut changed = options.clone();
    changed.expected_target_scope =
        IntegrityDigest::from_value(b"different independently configured target scope");
    let error = repository
        .preflight_storage_import(packet, changed)
        .unwrap_err();
    assert_eq!(error.reason(), "storage_import_journal_custody_mismatch");
    control
        .batch_execute(&format!(
            "GRANT INSERT ON public.trnm_storage_objects TO {}",
            options.legacy_writer_role
        ))
        .unwrap();
    assert!(repository
        .preflight_storage_import(packet, options.clone())
        .is_err());
    assert!(repository.apply_next_storage_import_page(checked).is_err());
    let mut swapped = options.clone();
    swapped.legacy_writer_role = target.extra_roles[0].clone();
    let error = repository
        .preflight_storage_import(packet, swapped)
        .unwrap_err();
    assert_eq!(error.reason(), "storage_import_journal_custody_mismatch");
    assert_eq!(
        repository
            .resume_storage_import(&alternative.role_b)
            .unwrap_err()
            .reason(),
        "storage_import_journal_custody_mismatch"
    );
    assert_eq!(journal_snapshot(&mut control), before);
    assert_eq!(target_rows(&mut control), data);
    control
        .batch_execute(&format!(
            "REVOKE INSERT ON public.trnm_storage_objects FROM {}",
            options.legacy_writer_role
        ))
        .unwrap();
    repository.resume_storage_import(checked).unwrap();
    assert_eq!(
        repository
            .resume_storage_import(&alternative.scope)
            .unwrap_err()
            .reason(),
        "storage_import_journal_custody_mismatch"
    );
    let source_commit: String = control
        .query_one(
            "SELECT source_commit FROM public.trnm_schema_metadata WHERE singleton=1",
            &[],
        )
        .unwrap()
        .get(0);
    control
        .execute(
            "UPDATE public.trnm_schema_metadata SET source_commit=$1 WHERE singleton=1",
            &[&"abababababababababababababababababababab"],
        )
        .unwrap();
    assert!(repository.resume_storage_import(checked).is_err());
    assert_eq!(journal_snapshot(&mut control), before);
    assert_eq!(target_rows(&mut control), data);
    control
        .execute(
            "UPDATE public.trnm_schema_metadata SET source_commit=$1 WHERE singleton=1",
            &[&source_commit],
        )
        .unwrap();
    repository.resume_storage_import(checked).unwrap();
}

fn real_different_packet_cannot_resume(
    environment: &LiveEnvironment,
    source: &Database,
    files: &PacketDirectory,
    target: &Database,
    original: &VerifiedStorageExport,
    options: &StorageImportOptions,
) {
    single_row_export_preserves_all_zero_continuation(environment, source, files);
    let mut control = source.client();
    let version: String = control
        .query_one(
            "SELECT version FROM public.storage WHERE collection='' AND key=''",
            &[],
        )
        .unwrap()
        .get(0);
    control.batch_execute("UPDATE public.storage SET version='actual-source-change' WHERE collection='' AND key=''").unwrap();
    let path = export(
        environment,
        source,
        files.path.join("different-actual-snapshot"),
    );
    control
        .execute(
            "UPDATE public.storage SET version=$1 WHERE collection='' AND key=''",
            &[&version],
        )
        .unwrap();
    let packet = verify_storage_export(&path, &independent_custody(environment, &path)).unwrap();
    assert_ne!(
        packet.summary().manifest_sha256,
        original.summary().manifest_sha256
    );
    let before = journal_snapshot(&mut target.client());
    let error = target
        .repository()
        .preflight_storage_import(&packet, options.clone())
        .unwrap_err();
    assert_eq!(error.reason(), "storage_import_journal_custody_mismatch");
    assert_eq!(journal_snapshot(&mut target.client()), before);
}

fn page_failure_rolls_back(
    target: &Database,
    repository: &mut PgRepository,
    checked: &trnm_persistence_pg::CheckedStorageImport<'_>,
) {
    let mut control = target.client();
    let before = journal_snapshot(&mut control);
    let rows = target_rows(&mut control);
    let identity = target.repository().verify_authoritative_schema().unwrap();
    // Native fault injection, not an ABI-equivalence claim. The shared runner
    // qualifies only its reviewed named storage checks; this temporary extra
    // CHECK does not change that projection. Existing first-page rows pass;
    // the next page's all/empty row passes and all/nonempty row fails second.
    control.batch_execute("ALTER TABLE public.trnm_storage_objects ADD CONSTRAINT import_fixture_reject_second CHECK(collection <> 'all' OR object_key='')").unwrap();
    assert_eq!(
        target.repository().verify_authoritative_schema().unwrap(),
        identity
    );
    {
        // Establish native reachability independently of the Rust import loop:
        // an all/empty tuple is accepted and the next all/nonempty tuple gets
        // SQLSTATE23514. These cloned fault probes are rolled back and confer
        // no source-import/full-tuple acceptance credit.
        let mut probe = control.transaction().unwrap();
        let sql="INSERT INTO public.trnm_storage_objects(collection,object_key,user_id,value_bytes,version_digest,read_permission,write_permission,updated_at_ms,create_time,update_time,value_jsonb,public_version,value_projection_digest,value_origin,source_manifest_digest) SELECT $1,$2,user_id,value_bytes,version_digest,read_permission,write_permission,updated_at_ms,create_time,update_time,value_jsonb,public_version,value_projection_digest,value_origin,source_manifest_digest FROM public.trnm_storage_objects WHERE collection='' AND object_key=''";
        assert_eq!(probe.execute(sql, &[&"all", &""]).unwrap(), 1);
        let error = probe.execute(sql, &[&"all", &"number"]).unwrap_err();
        assert_eq!(error.code().map(|code| code.code()), Some("23514"));
        probe.rollback().unwrap();
    }
    assert_eq!(journal_snapshot(&mut control), before);
    assert_eq!(target_rows(&mut control), rows);
    let error = repository
        .apply_next_storage_import_page(checked)
        .unwrap_err();
    assert_eq!(error.code(), StableCode::InvalidArgument);
    assert_eq!(error.reason(), "database_constraint_violation");
    assert_eq!(journal_snapshot(&mut control), before);
    assert_eq!(target_rows(&mut control), rows);
    assert_eq!(counts(&mut control), (2, 1, 1));
    control
        .batch_execute(
            "ALTER TABLE public.trnm_storage_objects DROP CONSTRAINT import_fixture_reject_second",
        )
        .unwrap();
}

fn native_tuple_tamper(
    target: &Database,
    repository: &mut PgRepository,
    checked: &trnm_persistence_pg::CheckedStorageImport<'_>,
    source: &[Value],
) {
    let mut control = target.client();
    let journal = journal_snapshot(&mut control);
    let data = target_rows(&mut control);
    let row = &source[0];
    let key = key_from_row(row);
    let current=control.query_one("SELECT value_jsonb::TEXT,public_version::TEXT,value_projection_digest,read_permission,write_permission,create_time,update_time,updated_at_ms,source_manifest_digest FROM public.trnm_storage_objects WHERE collection=$1 AND object_key=$2 AND user_id=$3",&[&key.collection(),&key.key(),&key.user_id().as_bytes().as_slice()]).unwrap();
    for mutation in [
        "value_jsonb='false'::JSONB", "public_version='tampered'", "read_permission=32767", "write_permission=32767",
        "create_time=TIMESTAMPTZ '2001-01-01 00:00:00.000001+00'", "update_time=TIMESTAMPTZ '2002-01-01 00:00:00.000002+00'", "updated_at_ms=777",
        "value_projection_digest=decode(repeat('ab',32),'hex')", "source_manifest_digest=decode(repeat('ac',32),'hex')",
        "value_origin='write-request-bytes',value_bytes=decode('7b7d','hex'),version_digest=decode(repeat('ad',32),'hex'),source_manifest_digest=NULL",
        "value_origin='legacy-rust-v2-bytes',value_bytes=decode('7b7d','hex'),version_digest=decode(repeat('ae',32),'hex'),source_manifest_digest=NULL",
    ] {
        control.execute(&format!("UPDATE public.trnm_storage_objects SET {mutation} WHERE collection=$1 AND object_key=$2 AND user_id=$3"),&[&key.collection(),&key.key(),&key.user_id().as_bytes().as_slice()]).unwrap();
        let corrupted=target_rows(&mut control);
        assert_eq!(repository.finalize_storage_import(checked).unwrap_err().code(),StableCode::DataLoss,"tuple drift accepted: {mutation}");
        assert_blocked(repository,&key); assert_eq!(journal_snapshot(&mut control),journal);assert_eq!(target_rows(&mut control),corrupted);
        control.execute("UPDATE public.trnm_storage_objects SET value_jsonb=$4::TEXT::JSONB,public_version=$5,value_projection_digest=$6,read_permission=$7,write_permission=$8,create_time=$9,update_time=$10,updated_at_ms=$11,source_manifest_digest=$12,value_origin='nakama-export-unknown-request',value_bytes=NULL,version_digest=NULL WHERE collection=$1 AND object_key=$2 AND user_id=$3",&[&key.collection(),&key.key(),&key.user_id().as_bytes().as_slice(),&current.get::<_,String>(0),&current.get::<_,String>(1),&current.get::<_,Vec<u8>>(2),&current.get::<_,i16>(3),&current.get::<_,i16>(4),&current.get::<_,StorageTimestamp>(5),&current.get::<_,StorageTimestamp>(6),&current.get::<_,i64>(7),&current.get::<_,Vec<u8>>(8)]).unwrap();
    }
    assert_eq!(target_rows(&mut control), data);
    assert_eq!(journal_snapshot(&mut control), journal);
}
