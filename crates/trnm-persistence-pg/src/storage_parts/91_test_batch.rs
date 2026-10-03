#[test]
fn batch_validation_rejects_empty_duplicate_and_excess() {
    assert_eq!(
        validate_batch(&[]).unwrap_err().reason(),
        "invalid_storage_batch_size"
    );
    let duplicate = vec![write(1), write(1)];
    assert_eq!(
        validate_batch(&duplicate).unwrap_err().reason(),
        "duplicate_storage_key_in_batch"
    );
    let excess = (0..=MAX_BATCH_OPERATIONS)
        .map(|index| write(u8::try_from(index + 1).unwrap()))
        .collect::<Vec<_>>();
    assert_eq!(
        validate_batch(&excess).unwrap_err().reason(),
        "invalid_storage_batch_size"
    );
}

fn any_candidate_operation() -> WriteOperation {
    WriteOperation {
        key: key(71),
        value: br#" {"new":1} "#.to_vec(),
        expected: VersionCheck::Any,
        read_permission: ReadPermission::OWNER,
        write_permission: WritePermission::OWNER,
    }
}

fn any_candidate_known_row(operation: &WriteOperation) -> AnyNativeRow {
    // Fixed native-render fixture bytes; no host JSONB renderer/normalizer.
    let native = br#"{"new": 1}"#.to_vec();
    let version = ContentVersion::from_value(&operation.value);
    let witness = CollisionWitness::from_request(&operation.value, &native).unwrap();
    let time = crate::StorageTimestamp::new(1_700_000_000, 123_456_000).unwrap();
    AnyNativeRow {
        stored: StoredStorageObject {
            object: StorageObject {
                key: operation.key.clone(),
                version: version.into(),
                integrity_digest: IntegrityDigest::from_value(&native),
                collision_witness: Some(witness),
                value: native,
                read_permission: operation.read_permission,
                write_permission: operation.write_permission,
            },
            times: StorageTimes {
                create: Some(time),
                update: Some(time),
            },
        },
        origin: "write-request-bytes".to_owned(),
        raw_value: Some(operation.value.clone()),
        raw_digest: Some(
            IntegrityDigest::from_value(&operation.value)
                .get()
                .as_bytes()
                .to_vec(),
        ),
        manifest: None,
        updated_at_ms: 2468,
    }
}

#[test]
fn any_batch_isolation_is_explicitly_pg_all_any_and_bounded() {
    let operation = BatchOperation::Write(any_candidate_operation());
    let duplicate = vec![operation.clone(), operation.clone(), operation.clone()];
    assert!(matches!(
        storage_batch_isolation(
            DatabaseProfile::PostgreSql,
            Some(NakamaBatchKind::Write),
            &duplicate
        ),
        IsolationLevel::ReadCommitted
    ));
    for (profile, kind) in [
        (DatabaseProfile::CockroachDb, Some(NakamaBatchKind::Write)),
        (DatabaseProfile::PostgreSql, None),
        (DatabaseProfile::PostgreSql, Some(NakamaBatchKind::Delete)),
    ] {
        assert!(matches!(
            storage_batch_isolation(profile, kind, &duplicate),
            IsolationLevel::Serializable
        ));
    }
    for batch in [vec![], vec![operation; MAX_BATCH_OPERATIONS + 1]] {
        assert!(matches!(
            storage_batch_isolation(
                DatabaseProfile::PostgreSql,
                Some(NakamaBatchKind::Write),
                &batch
            ),
            IsolationLevel::Serializable
        ));
    }
}

#[test]
fn any_batch_mixed_literal_exact_star_and_delete_never_select_rc() {
    let operation = any_candidate_operation();
    for expected in [
        VersionCheck::MustNotExist,
        VersionCheck::Exact("".into()),
        VersionCheck::Exact("*".into()),
        VersionCheck::Exact("nonhex条件\0".into()),
    ] {
        let mut other = operation.clone();
        other.expected = expected;
        let mixed = [
            BatchOperation::Write(operation.clone()),
            BatchOperation::Write(other),
        ];
        assert!(matches!(
            storage_batch_isolation(
                DatabaseProfile::PostgreSql,
                Some(NakamaBatchKind::Write),
                &mixed
            ),
            IsolationLevel::Serializable
        ));
    }
    let mixed = [
        BatchOperation::Write(operation.clone()),
        BatchOperation::Delete(DeleteOperation {
            key: operation.key,
            expected_version: None,
        }),
    ];
    assert!(matches!(
        storage_batch_isolation(
            DatabaseProfile::PostgreSql,
            Some(NakamaBatchKind::Write),
            &mixed
        ),
        IsolationLevel::Serializable
    ));
}

#[test]
fn any_occurrence_route_is_only_canonical_write_any() {
    let mut operation = any_candidate_operation();
    let any = BatchOperation::Write(operation.clone());
    assert!(nakama_any_write(Some(NakamaBatchKind::Write), &any).is_some());
    assert!(nakama_any_write(None, &any).is_none());
    assert!(nakama_any_write(Some(NakamaBatchKind::Delete), &any).is_none());
    for expected in [VersionCheck::MustNotExist, VersionCheck::Exact("".into())] {
        operation.expected = expected;
        assert!(nakama_any_write(
            Some(NakamaBatchKind::Write),
            &BatchOperation::Write(operation.clone())
        )
        .is_none());
    }
    let delete = BatchOperation::Delete(DeleteOperation {
        key: operation.key,
        expected_version: None,
    });
    assert!(nakama_any_write(Some(NakamaBatchKind::Write), &delete).is_none());
}

#[test]
fn any_unknown_noop_keeps_different_native_payload_and_private_custody() {
    let operation = any_candidate_operation();
    let mut prior = any_candidate_known_row(&operation);
    prior.stored.object.value = br#"["opaque", null]"#.to_vec();
    prior.stored.object.integrity_digest = IntegrityDigest::from_value(&prior.stored.object.value);
    prior.stored.object.collision_witness = None;
    prior.stored.times.create = None;
    prior.stored.times.update = None;
    prior.origin = "nakama-export-unknown-request".to_owned();
    prior.raw_value = None;
    prior.raw_digest = None;
    prior.manifest = Some(vec![0xac; 32]);
    assert!(any_conflict_noop(
        &prior,
        &operation,
        ContentVersion::from_value(&operation.value)
    )
    .unwrap());
    assert_eq!(prior.stored.object.value, br#"["opaque", null]"#);
    assert_eq!(prior.manifest.as_deref(), Some([0xac; 32].as_slice()));
    assert_eq!(prior.stored.times, StorageTimes::default());
    assert_eq!(prior.updated_at_ms, 2468);
    let mut changed_acl = operation.clone();
    changed_acl.read_permission = ReadPermission::PUBLIC;
    assert!(!any_conflict_noop(
        &prior,
        &changed_acl,
        ContentVersion::from_value(&operation.value)
    )
    .unwrap());
}

#[test]
fn any_conflict_verifies_known_projection_and_raw_request_identity() {
    let operation = any_candidate_operation();
    let mut prior = any_candidate_known_row(&operation);
    assert_ne!(
        ContentVersion::from_value(&prior.stored.object.value),
        ContentVersion::from_value(&operation.value)
    );
    assert!(any_conflict_noop(
        &prior,
        &operation,
        ContentVersion::from_value(&operation.value)
    )
    .unwrap());
    let mut changed_request = operation.clone();
    changed_request.value = br#"{"new":1}"#.to_vec();
    assert!(!any_conflict_noop(
        &prior,
        &changed_request,
        ContentVersion::from_value(&changed_request.value)
    )
    .unwrap());
    prior.stored.object.value = br#"{"new": 999}"#.to_vec();
    prior.stored.object.integrity_digest = IntegrityDigest::from_value(&prior.stored.object.value);
    let failure = any_conflict_noop(
        &prior,
        &operation,
        ContentVersion::from_value(&operation.value),
    )
    .unwrap_err();
    assert_eq!(failure.code(), StableCode::DataLoss);
    assert_eq!(failure.retry(), RetryClass::Never);
}

#[test]
fn any_returning_requires_actual_raw_origin_manifest_clock_and_acl() {
    let operation = any_candidate_operation();
    let version = ContentVersion::from_value(&operation.value);
    let correct = any_candidate_known_row(&operation);
    verify_any_written_row(&correct, &operation, version, 2468, None).unwrap();
    for field in 0..7 {
        let mut altered = any_candidate_known_row(&operation);
        match field {
            0 => altered.origin = "legacy-rust-v2-bytes".to_owned(),
            1 => altered.raw_value = Some(b"private-other-request".to_vec()),
            2 => altered.raw_digest = Some(vec![0xcc; 32]),
            3 => altered.manifest = Some(vec![0xac; 32]),
            4 => altered.updated_at_ms = 2469,
            5 => altered.stored.object.read_permission = ReadPermission::PUBLIC,
            _ => altered.stored.object.key = key(72),
        }
        let failure =
            verify_any_written_row(&altered, &operation, version, 2468, None).unwrap_err();
        assert_eq!(failure.code(), StableCode::DataLoss);
        assert_eq!(failure.retry(), RetryClass::Never);
    }
}

#[test]
fn any_returning_preserves_nullable_prior_create_and_rejects_fake_insert_time() {
    let operation = any_candidate_operation();
    let version = ContentVersion::from_value(&operation.value);
    let mut prior = any_candidate_known_row(&operation);
    prior.stored.times.create = None;
    let mut updated = any_candidate_known_row(&operation);
    updated.stored.times.create = None;
    verify_any_written_row(&updated, &operation, version, 2468, Some(&prior)).unwrap();
    assert!(verify_any_written_row(&updated, &operation, version, 2468, None).is_err());
    let different_create = any_candidate_known_row(&operation);
    assert!(
        verify_any_written_row(&different_create, &operation, version, 2468, Some(&prior)).is_err()
    );
}

#[test]
fn any_private_full_row_equality_does_not_drop_custody_or_legacy_time() {
    let operation = any_candidate_operation();
    let prior = any_candidate_known_row(&operation);
    for field in 0..4 {
        let mut changed = any_candidate_known_row(&operation);
        match field {
            0 => changed.origin = "legacy-rust-v2-bytes".to_owned(),
            1 => changed.raw_value = None,
            2 => changed.manifest = Some(vec![0xac; 32]),
            _ => changed.updated_at_ms += 1,
        }
        assert_eq!(changed.stored, prior.stored);
        assert!(changed != prior);
    }
}

#[test]
fn any_conflict_acquisition_preserves_acl_boundary_and_native_profile_lock() {
    for profile in [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb] {
        let access = storage_any_access_query(profile);
        let prior = storage_any_prior_query(profile);
        let reservation = storage_any_upsert_query(profile, true);
        // An ACL rejection can lock/check the real key without exposing its
        // native value, optional raw witness or manifest to a payload decoder.
        assert!(access.starts_with("SELECT public_version::TEXT, write_permission "));
        assert!(!access.contains("value_jsonb"));
        assert!(!access.contains("value_bytes"));
        assert!(!access.contains("source_manifest_digest"));
        assert!(prior.contains(", updated_at_ms, collection, object_key, user_id FROM "));
        assert!(prior.contains("CASE WHEN TRUE THEN"));
        for query in [&access, &prior] {
            assert!(query.contains("collection=$1 AND object_key=$2 AND user_id=$3"));
            match profile {
                DatabaseProfile::PostgreSql => assert!(!query.contains("FOR UPDATE")),
                DatabaseProfile::CockroachDb => assert!(query.ends_with(" FOR UPDATE")),
            }
        }
        assert!(reservation.contains("SET object_key=excluded.object_key WHERE FALSE"));
        assert!(!reservation.contains("FOR UPDATE"));
        assert!(!reservation.contains("SELECT"));
    }
}
