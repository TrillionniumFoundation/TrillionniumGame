fn counts(client: &mut Client) -> (i64, i64, i64) {
    let row=client.query_one("SELECT (SELECT count(*)::INT8 FROM public.trnm_storage_objects),(SELECT count(*)::INT8 FROM public.trnm_storage_import_jobs),(SELECT count(*)::INT8 FROM public.trnm_storage_import_pages)",&[]).unwrap();
    (row.get(0), row.get(1), row.get(2))
}

fn native_preflight_negatives(
    environment: &LiveEnvironment,
    files: &PacketDirectory,
    original: &Path,
) {
    let mut target = Database::new(environment, "preflight");
    let options = target.initialize_target(environment);
    let mut control = target.client();
    assert_eq!(counts(&mut control), (0, 0, 0));
    let before = target.repository().verify_authoritative_schema().unwrap();
    for (index, text) in ["{", "[1,", "not JSON"].into_iter().enumerate() {
        let path = files.path.join(format!("native-invalid-{index}"));
        copy_packet(original, &path);
        change_packet_value(&path, 0, text);
        let packet = reanchor_packet(environment, &path);
        assert!(target
            .repository()
            .preflight_storage_import(&packet, options.clone())
            .is_err());
        assert_eq!(
            counts(&mut control),
            (0, 0, 0),
            "native preflight must not register a job"
        );
        assert_eq!(
            target.repository().verify_authoritative_schema().unwrap(),
            before
        );
    }
}

fn dedicated_target_begin_rechecks_emptiness(
    environment: &LiveEnvironment,
    packet: &VerifiedStorageExport,
) {
    let mut target = Database::new(environment, "nonempty");
    let options = target.initialize_target(environment);
    let mut repository = target.repository();
    let checked = repository
        .preflight_storage_import(packet, options.clone())
        .unwrap();
    let key =
        StorageObjectKey::new("race", "insert-after-preflight", UserId::new([0x11; 16])).unwrap();
    let operation = StorageBatchOperation::Write(StorageWriteOperation {
        key,
        value: b"{\"raced\":true}".to_vec(),
        expected: VersionCheck::Any,
        read_permission: ReadPermission::OWNER,
        write_permission: WritePermission::OWNER,
    });
    repository
        .apply_storage_batch(StorageActor::Server, &[operation], 1)
        .unwrap();
    let mut control = target.client();
    let before = target_rows(&mut control);
    let rejection = repository.begin_storage_import(&checked).unwrap_err();
    assert_eq!(rejection.code(), StableCode::FailedPrecondition, "begin rejected with {}", rejection.reason());
    assert_eq!(counts(&mut control), (1, 0, 0));
    assert_eq!(target_rows(&mut control), before);
    assert!(repository
        .preflight_storage_import(packet, options)
        .is_err());
    assert_eq!(counts(&mut control), (1, 0, 0));
}

fn assert_blocked(repository: &mut PgRepository, key: &StorageObjectKey) {
    assert_eq!(
        repository
            .verify_storage_import_serving()
            .unwrap_err()
            .code(),
        StableCode::FailedPrecondition
    );
    assert_eq!(
        repository
            .read_storage_objects(StorageActor::Server, std::slice::from_ref(key))
            .unwrap_err()
            .code(),
        StableCode::FailedPrecondition
    );
    let write = StorageBatchOperation::Write(StorageWriteOperation {
        key: key.clone(),
        value: b"{\"blocked\":true}".to_vec(),
        expected: VersionCheck::Any,
        read_permission: ReadPermission::OWNER,
        write_permission: WritePermission::OWNER,
    });
    assert_eq!(
        repository
            .apply_storage_batch(StorageActor::Server, &[write], 42)
            .unwrap_err()
            .code(),
        StableCode::FailedPrecondition
    );
}

fn assert_progress(
    progress: &trnm_persistence_pg::StorageImportProgress,
    page: usize,
    completed: bool,
) {
    assert_eq!(progress.next_page, page);
    assert_eq!(progress.committed_rows, page * PAGE_ROWS);
    assert_eq!(progress.total_rows, SOURCE_ROWS);
    assert_eq!(progress.total_pages, SOURCE_ROWS / PAGE_ROWS);
    assert_eq!(progress.completed, completed);
    assert!(
        !progress.compatibility_credit
            && !progress.production_ready
            && !progress.full_nakama_replacement
    );
}

fn lifecycle_and_tamper(
    environment: &LiveEnvironment,
    source: &Database,
    files: &PacketDirectory,
    original_source: &[Value],
    packet: &VerifiedStorageExport,
) {
    let mut target = Database::new(environment, "target");
    let options = target.initialize_target(environment);
    let mut repository = target.repository();
    let path_attack = native_search_path_attack(&target, &mut repository);
    let alternative = alternative_guards(&mut target, &mut repository, packet, &options);
    let checked = repository
        .preflight_storage_import(packet, options.clone())
        .unwrap();
    assert_search_path_restored(&target, &mut repository, &path_attack, "preflight");
    let begin = repository.begin_storage_import(&checked).unwrap();
    assert_progress(&begin, 0, false);
    assert_eq!(repository.begin_storage_import(&checked).unwrap(), begin);
    let key = key_from_row(&original_source[0]);
    assert_blocked(&mut repository, &key);
    assert_business_blocked(&target, &mut repository);
    let first = repository.apply_next_storage_import_page(&checked).unwrap();
    assert_eq!(
        (first.page_index, first.first_ordinal, first.row_count),
        (0, 0, PAGE_ROWS)
    );
    assert_progress(&first.progress, 1, false);
    assert_search_path_restored(&target, &mut repository, &path_attack, "first-page");
    assert_eq!(counts(&mut target.client()), (2, 1, 1));
    assert_blocked(&mut repository, &key);
    assert_eq!(
        repository
            .finalize_storage_import(&checked)
            .unwrap_err()
            .code(),
        StableCode::FailedPrecondition
    );
    drop(repository);
    // A genuinely new native connection verifies the exact committed prefix;
    // it never reconstructs/imports a packet from a progress report.
    let mut resumed_repository = target.repository();
    let resumed_checked = resumed_repository
        .preflight_storage_import(packet, options.clone())
        .unwrap();
    let resumed = resumed_repository
        .resume_storage_import(&resumed_checked)
        .unwrap();
    assert_eq!(resumed, first.progress);
    journal_and_prefix_tamper(&target, &mut resumed_repository, &resumed_checked);
    role_and_scope_binding(
        &mut target,
        &mut resumed_repository,
        &resumed_checked,
        packet,
        &options,
        &alternative,
    );
    real_different_packet_cannot_resume(environment, source, files, &target, packet, &options);
    page_failure_rolls_back(&target, &mut resumed_repository, &resumed_checked);
    let mut remaining = Vec::new();
    for index in 1..4 {
        let receipt = resumed_repository
            .apply_next_storage_import_page(&resumed_checked)
            .unwrap();
        assert_eq!(
            (receipt.page_index, receipt.first_ordinal, receipt.row_count),
            (index, index * PAGE_ROWS, PAGE_ROWS)
        );
        assert_progress(&receipt.progress, index + 1, false);
        remaining.push(receipt);
    }
    assert_blocked(&mut resumed_repository, &key);
    native_tuple_tamper(
        &target,
        &mut resumed_repository,
        &resumed_checked,
        original_source,
    );
    let finished = resumed_repository
        .finalize_storage_import(&resumed_checked)
        .unwrap();
    assert_progress(&finished, 4, true);
    assert_eq!(
        resumed_repository
            .finalize_storage_import(&resumed_checked)
            .unwrap(),
        finished
    );
    let verified = resumed_repository
        .verify_applied_storage_import(&resumed_checked)
        .unwrap();
    assert_eq!(verified, finished);
    resumed_repository.verify_storage_import_serving().unwrap();
    let keys: Vec<_> = original_source.iter().map(key_from_row).collect();
    let objects = resumed_repository
        .read_storage_objects_with_metadata(StorageActor::Server, &keys)
        .unwrap();
    assert_eq!(objects.len(), SOURCE_ROWS);
    for (stored, source) in objects.iter().zip(original_source) {
        assert_eq!(
            stored.object.value,
            source["native_text"].as_str().unwrap().as_bytes()
        );
        assert_eq!(
            stored.object.version.as_str(),
            source["public_version"].as_str().unwrap()
        );
        assert_eq!(
            stored.object.read_permission.as_i32(),
            source["read"].as_i64().unwrap() as i32
        );
        assert_eq!(
            stored.object.write_permission.as_i32(),
            source["write"].as_i64().unwrap() as i32
        );
        assert!(stored.object.collision_witness.is_none());
        stored.object.verify_integrity().unwrap();
        assert_eq!(
            timestamp_json(stored.times.create.unwrap()),
            source["create_time"]
        );
        assert_eq!(
            timestamp_json(stored.times.update.unwrap()),
            source["update_time"]
        );
    }
    let actual = target_rows(&mut target.client());
    assert_native_rows(original_source, &actual, &packet.summary().manifest_sha256);
    assert_business_restored(&mut resumed_repository);
    if let Some(root) = evidence_root() {
        let table_count:i64=target.client().query_one("SELECT count(*)::INT8 FROM information_schema.tables WHERE table_schema='public' AND table_name LIKE 'trnm_%' AND table_type='BASE TABLE'",&[]).unwrap().get(0);
        assert_eq!(table_count, 12);
        let report = trnm_persistence_pg::SchemaMigrationReport {
            identity: resumed_repository.verify_authoritative_schema().unwrap(),
            migration_applied: false,
            table_count: usize::try_from(table_count).unwrap(),
            applied_steps: 0,
        };
        write_json(
            root.join("target-schema-verify.json"),
            &schema_report_json(&report),
        );
        write_json(
            root.join("lifecycle.json"),
            &json!({
                "schema":"trillionnium.storage-import-native-lifecycle.v1","profile":environment.profile.metadata_value(),
                "begin":begin,"first_page":first,"resumed":resumed,"remaining_pages":remaining,"finished":finished,"verified":verified,
                "compatibility_credit":false,"production_ready":false,"full_nakama_replacement":false
            }),
        );
        let source_json = json!(original_source);
        let target_json = json!(actual);
        write_json(root.join("source-rows.json"), &source_json);
        write_json(root.join("target-rows.json"), &target_json);
        write_json(
            root.join("native-rows.json"),
            &json!({
                "schema":"trillionnium.storage-import-native-rows.v1","profile":environment.profile.metadata_value(),
                "source_path":"source-rows.json","target_path":"target-rows.json",
                "source_sha256":digest_hex(&fs::read(root.join("source-rows.json")).unwrap()),
                "target_sha256":digest_hex(&fs::read(root.join("target-rows.json")).unwrap()),
                "exact_source_projection_and_metadata_preserved":true,"unknown_request_witnesses":true,
                "compatibility_credit":false,"production_ready":false,"full_nakama_replacement":false
            }),
        );
        retain_journal(environment, &root, &mut target.client());
    }
}

fn assert_native_rows(source: &[Value], target: &[Value], manifest: &str) {
    assert_eq!(source.len(), target.len());
    for (source, target) in source.iter().zip(target) {
        for field in [
            "collection",
            "key",
            "user_id",
            "native_text",
            "public_version",
            "read",
            "write",
            "create_time",
            "update_time",
        ] {
            assert_eq!(
                target[field], source[field],
                "native field {field} changed during import"
            );
        }
        assert_eq!(
            target["projection_sha256"],
            digest_hex(target["native_text"].as_str().unwrap().as_bytes())
        );
        assert_eq!(target["source_manifest_sha256"], manifest);
        assert_eq!(target["value_origin"], "nakama-export-unknown-request");
        assert_eq!(target["raw_value_is_null"], true);
        assert_eq!(target["raw_digest_is_null"], true);
        assert_eq!(target["updated_at_ms"], AUDIT_MS);
    }
}
