use super::*;

fn key(name: &str) -> StorageObjectKey {
    StorageObjectKey::new("projection", name, UserId::new([1; 16])).unwrap()
}

fn write(name: &str, value: &[u8], expected: VersionCheck) -> BatchOperation {
    BatchOperation::Write(WriteOperation {
        key: key(name),
        value: value.to_vec(),
        expected,
        read_permission: ReadPermission::Owner,
        write_permission: WritePermission::Owner,
    })
}

fn unknown(name: &str, version: PublicVersion, value: &[u8]) -> StorageObject {
    StorageObject {
        key: key(name),
        value: value.to_vec(),
        version,
        integrity_digest: IntegrityDigest::from_value(value),
        collision_witness: None,
        read_permission: ReadPermission::Owner,
        write_permission: WritePermission::Owner,
    }
}

#[test]
fn public_version_preserves_opaque_history_and_unicode_character_bounds() {
    for raw in [
        "",
        "*",
        "NOT-HEX",
        "ABCDEF0123456789ABCDEF0123456789",
        "nul\0token",
        "line\r\n",
        "é",
        "e\u{301}",
    ] {
        let version = PublicVersion::new(raw).unwrap();
        assert_eq!(version.as_str().as_bytes(), raw.as_bytes());
        assert_eq!(version.as_bytes(), raw.as_bytes());
        assert_eq!(ExpectedVersion::from(&version).as_str(), raw);
        assert_eq!(ExpectedVersion::from(version.clone()).as_str(), raw);
        assert_eq!(version.to_string(), raw);
    }
    let maximum = "🔑".repeat(32);
    assert_eq!(PublicVersion::parse(&maximum).unwrap().as_str(), maximum);
    for invalid in ["a".repeat(33), "🔑".repeat(33)] {
        assert_eq!(
            PublicVersion::new(invalid).unwrap_err().reason(),
            "invalid_storage_public_version"
        );
    }
    assert_ne!(
        PublicVersion::new("é").unwrap(),
        PublicVersion::new("e\u{301}").unwrap()
    );
    let generated = ContentVersion::from_value(b"write request");
    assert_eq!(PublicVersion::from(generated).as_str(), generated.as_str());
    assert!(ContentVersion::parse("*").is_err());
    assert_eq!(
        ExpectedVersion::from("x".repeat(65536)).as_str().len(),
        65536
    );
}

#[test]
fn known_collision_witness_binds_request_version_and_projection_without_reconstruction() {
    let request = br#"{"b":1,"a":2,"b":3}"#;
    let native_projection = br#"{"a": 2, "b": 3}"#;
    let witness = CollisionWitness::from_request(request, native_projection).unwrap();
    let version = PublicVersion::from(ContentVersion::from_value(request));
    assert_eq!(witness.request_version().as_str(), version.as_str());
    assert!(witness.request_digest().matches_value(request));
    assert_eq!(witness.request_len(), request.len());
    assert!(witness.projection_digest().matches_value(native_projection));
    assert!(witness.matches_request(request));
    assert!(!witness.matches_request(native_projection));
    assert!(witness
        .validate_projection(&version, native_projection)
        .is_ok());
    assert_ne!(
        ContentVersion::from_value(native_projection).as_str(),
        version.as_str()
    );
    for (bad_version, bad_value) in [
        (
            PublicVersion::new("opaque-history").unwrap(),
            native_projection.as_slice(),
        ),
        (version.clone(), br#"{"a": 2, "b": 4}"#.as_slice()),
    ] {
        let error = witness
            .validate_projection(&bad_version, bad_value)
            .unwrap_err();
        assert_eq!(
            (error.code(), error.reason()),
            (
                StableCode::DataLoss,
                "storage_collision_witness_binding_mismatch"
            )
        );
    }
}

#[test]
fn request_and_projection_budgets_are_independent_and_legal_overflow_is_resource_exhausted() {
    let request = vec![b'x'; MAX_REQUEST_VALUE_BYTES];
    let native_projection = vec![b'y'; MAX_PROJECTION_VALUE_BYTES];
    let witness = CollisionWitness::from_request(&request, &native_projection).unwrap();
    assert!(witness.matches_request(&request));
    witness
        .validate_projection(&witness.request_version().into(), &native_projection)
        .unwrap();
    let excessive_request = vec![b'x'; MAX_REQUEST_VALUE_BYTES + 1];
    assert_eq!(
        CollisionWitness::from_request(&excessive_request, b"ok")
            .unwrap_err()
            .reason(),
        "invalid_storage_value"
    );
    assert!(!witness.matches_request(&excessive_request));
    let excessive_projection = vec![b'y'; MAX_PROJECTION_VALUE_BYTES + 1];
    for error in [
        CollisionWitness::from_request(b"ok", &excessive_projection).unwrap_err(),
        witness
            .validate_projection(&PublicVersion::new("wrong").unwrap(), &excessive_projection)
            .unwrap_err(),
        unknown(
            "oversized",
            PublicVersion::new("historical").unwrap(),
            &excessive_projection,
        )
        .verify_integrity()
        .unwrap_err(),
    ] {
        assert_eq!(
            (error.code(), error.reason()),
            (
                StableCode::ResourceExhausted,
                "storage_projection_value_budget_exceeded"
            )
        );
    }
}

#[test]
fn supplied_projection_seam_is_executed_and_raw_model_stays_explicit_identity() {
    let request = br#"{"a":1}"#;
    let projected = br#"{"a": 1}"#;
    let mut state = StorageState::default();
    let mut calls = 0;
    let receipt = state
        .apply_batch_projected(
            Actor::Server,
            &[write("main", request, VersionCheck::MustNotExist)],
            |input| {
                calls += 1;
                assert_eq!(input, request);
                Ok(projected.to_vec())
            },
        )
        .unwrap()
        .remove(0);
    assert_eq!(calls, 1);
    assert_eq!(receipt.previous_version, None);
    assert_eq!(
        receipt.current_version,
        Some(ContentVersion::from_value(request))
    );
    let object = state.read(Actor::Server, &key("main")).unwrap();
    assert_eq!(object.value, projected);
    assert_eq!(
        object.version.as_str(),
        ContentVersion::from_value(request).as_str()
    );
    assert!(object.integrity_digest.matches_value(projected));
    assert!(object.collision_witness.unwrap().matches_request(request));
    assert_ne!(
        object.version.as_str(),
        ContentVersion::from_value(projected).as_str()
    );

    let binary_request = [0, 0xff, 0x01];
    state
        .apply_batch(
            Actor::Server,
            &[write("raw-model", &binary_request, VersionCheck::Any)],
        )
        .unwrap();
    let raw_object = state.read(Actor::Server, &key("raw-model")).unwrap();
    assert_eq!(raw_object.value, binary_request);
    assert!(raw_object
        .collision_witness
        .unwrap()
        .matches_request(&binary_request));
}

#[test]
fn blind_known_no_op_preserves_projection_and_witness_but_exact_and_acl_changes_project() {
    let request = br#"{"a":1}"#;
    let projected = br#"{"a": 1}"#;
    let mut state = StorageState::default();
    state
        .apply_batch_projected(
            Actor::Server,
            &[write("main", request, VersionCheck::Any)],
            |_| Ok(projected.to_vec()),
        )
        .unwrap();
    let before = state.clone();
    let receipt = state
        .apply_batch_projected(
            Actor::Server,
            &[write("main", request, VersionCheck::Any)],
            |_| panic!("blind no-op must not invoke projection"),
        )
        .unwrap()
        .remove(0);
    assert_eq!(state, before);
    assert_eq!(
        receipt.previous_version.as_ref().unwrap().as_str(),
        ContentVersion::from_value(request).as_str()
    );
    assert_eq!(
        receipt.current_version,
        Some(ContentVersion::from_value(request))
    );
    let mut calls = 0;
    let exact = VersionCheck::Exact(
        state
            .read(Actor::Server, &key("main"))
            .unwrap()
            .version
            .into(),
    );
    state
        .apply_batch_projected(Actor::Server, &[write("main", request, exact)], |_| {
            calls += 1;
            Ok(projected.to_vec())
        })
        .unwrap();
    assert_eq!(
        calls, 1,
        "Exact must take the write path even for identical input"
    );
    let mut acl_change = write("main", request, VersionCheck::Any);
    let BatchOperation::Write(operation) = &mut acl_change else {
        unreachable!()
    };
    operation.read_permission = ReadPermission::Public;
    state
        .apply_batch_projected(Actor::Server, &[acl_change], |_| {
            calls += 1;
            Ok(projected.to_vec())
        })
        .unwrap();
    assert_eq!(calls, 2);
    assert_eq!(
        state
            .read(Actor::Server, &key("main"))
            .unwrap()
            .read_permission,
        ReadPermission::Public
    );
}

#[test]
fn unknown_matching_token_no_op_preserves_native_value_and_never_fabricates_witness() {
    let request = br#"{"new":true}"#;
    let old_projection = br#"[1, null, "history"]"#;
    let mut state = StorageState::default();
    state.objects.insert(
        key("main"),
        unknown(
            "main",
            ContentVersion::from_value(request).into(),
            old_projection,
        ),
    );
    let before = state.clone();
    state
        .apply_batch_projected(
            Actor::Server,
            &[write("main", request, VersionCheck::Any)],
            |_| panic!("unknown no-op must preserve its original native value"),
        )
        .unwrap();
    assert_eq!(state, before);
    assert_eq!(
        state
            .read(Actor::Server, &key("main"))
            .unwrap()
            .collision_witness,
        None
    );
    let expected = state
        .read(Actor::Server, &key("main"))
        .unwrap()
        .version
        .into();
    state
        .apply_batch_projected(
            Actor::Server,
            &[write("main", request, VersionCheck::Exact(expected))],
            |_| Ok(br#"{"new": true}"#.to_vec()),
        )
        .unwrap();
    let written = state.read(Actor::Server, &key("main")).unwrap();
    assert_eq!(written.value, br#"{"new": true}"#);
    assert!(written.collision_witness.unwrap().matches_request(request));
}

#[test]
fn opaque_and_empty_previous_tokens_remain_present_in_write_and_delete_receipts() {
    for raw in [
        "",
        "*",
        "opaque-history",
        "UPPERCASE",
        "版本🔑",
        "nul\0token",
    ] {
        let version = PublicVersion::new(raw).unwrap();
        let mut state = StorageState::default();
        state
            .objects
            .insert(key("main"), unknown("main", version.clone(), b"null"));
        let read = state.read(Actor::Server, &key("main")).unwrap();
        assert_eq!(read.version, version);
        let old = state.clone();
        let receipt = state
            .apply_batch_projected(
                Actor::Server,
                &[write(
                    "main",
                    br#"{"next":1}"#,
                    VersionCheck::Exact((&version).into()),
                )],
                |_| Ok(br#"{"next": 1}"#.to_vec()),
            )
            .unwrap()
            .remove(0);
        assert_eq!(receipt.previous_version, Some(version.clone()));
        assert_eq!(
            receipt.current_version,
            Some(ContentVersion::from_value(br#"{"next":1}"#))
        );
        let mut deleting = old;
        let deleted = deleting
            .apply_batch(
                Actor::Server,
                &[BatchOperation::Delete(DeleteOperation {
                    key: key("main"),
                    expected_version: Some((&version).into()),
                })],
            )
            .unwrap()
            .remove(0);
        assert_eq!(deleted.previous_version, Some(version));
        assert_eq!(deleted.current_version, None);
        assert_eq!(deleting.object_count(), 0);
    }
}

fn md5_collision_pair() -> (Vec<u8>, Vec<u8>) {
    // Binary MD5 regression inputs for the pure identity model. They are not
    // valid HTTP JSON writes and grant no native JSONB collision qualification.
    const FIRST: &str = concat!(
        "d131dd02c5e6eec4693d9a0698aff95c2fcab58712467eab4004583eb8fb7f89",
        "55ad340609f4b30283e488832571415a085125e8f7cdc99fd91dbdf280373c5b",
        "d8823e3156348f5bae6dacd436c919c6dd53e2b487da03fd02396306d248cda0",
        "e99f33420f577ee8ce54b67080a80d1ec69821bcb6a8839396f9652b6ff72a70"
    );
    const SECOND: &str = concat!(
        "d131dd02c5e6eec4693d9a0698aff95c2fcab50712467eab4004583eb8fb7f89",
        "55ad340609f4b30283e4888325f1415a085125e8f7cdc99fd91dbd7280373c5b",
        "d8823e3156348f5bae6dacd436c919c6dd53e23487da03fd02396306d248cda0",
        "e99f33420f577ee8ce54b67080280d1ec69821bcb6a8839396f965ab6ff72a70"
    );
    let decode = |hex: &str| {
        hex.as_bytes()
            .chunks_exact(2)
            .map(|pair| {
                let byte = |value: u8| match value {
                    b'0'..=b'9' => value - b'0',
                    b'a'..=b'f' => value - b'a' + 10,
                    _ => panic!("fixed hex fixture"),
                };
                (byte(pair[0]) << 4) | byte(pair[1])
            })
            .collect()
    };
    (decode(FIRST), decode(SECOND))
}

#[test]
fn known_md5_collision_uses_request_fingerprint_and_unknown_history_keeps_upstream_no_op() {
    let (first, second) = md5_collision_pair();
    assert_ne!(first, second);
    assert_eq!(
        ContentVersion::from_value(&first).as_str(),
        "79054025255fb1a26e4bc422aef54eb4"
    );
    assert_eq!(
        ContentVersion::from_value(&first),
        ContentVersion::from_value(&second)
    );
    assert_ne!(
        IntegrityDigest::from_value(&first),
        IntegrityDigest::from_value(&second)
    );
    let mut state = StorageState::default();
    state
        .apply_batch_projected(
            Actor::Server,
            &[write("main", &first, VersionCheck::Any)],
            |_| Ok(b"null".to_vec()),
        )
        .unwrap();
    let before = state.clone();
    for expected in [
        VersionCheck::Any,
        VersionCheck::Exact(ContentVersion::from_value(&first).into()),
    ] {
        let error = state
            .apply_batch_projected(Actor::Server, &[write("main", &second, expected)], |_| {
                panic!("collision must reject before projection")
            })
            .unwrap_err();
        assert_eq!(
            (error.code(), error.reason()),
            (
                StableCode::DataLoss,
                "storage_public_version_collision_or_integrity_mismatch"
            )
        );
        assert_eq!(state, before);
    }
    state
        .objects
        .get_mut(&key("main"))
        .unwrap()
        .collision_witness = None;
    let unknown_before = state.clone();
    state
        .apply_batch_projected(
            Actor::Server,
            &[write("main", &second, VersionCheck::Any)],
            |_| panic!("unknown matching token remains a no-op"),
        )
        .unwrap();
    assert_eq!(state, unknown_before);
}

#[test]
fn projected_batch_failures_roll_back_and_full_validation_precedes_projection() {
    let mut state = StorageState::default();
    state
        .apply_batch(Actor::Server, &[write("old", b"old", VersionCheck::Any)])
        .unwrap();
    let before = state.clone();
    let mut calls = 0;
    let error = state
        .apply_batch_projected(
            Actor::Server,
            &[
                write("one", b"valid", VersionCheck::Any),
                write("two", b"reject", VersionCheck::Any),
            ],
            |request| {
                calls += 1;
                if request == b"reject" {
                    Err(error(
                        StableCode::InvalidArgument,
                        "native_projection_rejected",
                        RetryClass::Never,
                    ))
                } else {
                    Ok(b"null".to_vec())
                }
            },
        )
        .unwrap_err();
    assert_eq!(calls, 2);
    assert_eq!(error.reason(), "native_projection_rejected");
    assert_eq!(state, before);
    let excessive = vec![0; MAX_REQUEST_VALUE_BYTES + 1];
    for operations in [
        vec![
            write("one", b"valid", VersionCheck::Any),
            write("two", &excessive, VersionCheck::Any),
        ],
        vec![
            write("same", b"a", VersionCheck::Any),
            write("same", b"b", VersionCheck::Any),
        ],
    ] {
        state
            .apply_batch_projected(Actor::Server, &operations, |_| {
                panic!("invalid complete batch must reject before projection")
            })
            .unwrap_err();
        assert_eq!(state, before);
    }
    let error = state
        .apply_batch_projected(
            Actor::Server,
            &[
                write("one", b"valid", VersionCheck::Any),
                write("two", b"huge", VersionCheck::Any),
            ],
            |request| {
                Ok(if request == b"huge" {
                    vec![b'x'; MAX_PROJECTION_VALUE_BYTES + 1]
                } else {
                    b"null".to_vec()
                })
            },
        )
        .unwrap_err();
    assert_eq!(error.code(), StableCode::ResourceExhausted);
    assert_eq!(state, before);
}

#[test]
fn projection_integrity_and_witness_corruption_fail_before_no_op_or_delete() {
    let request = br#"{"a":1}"#;
    let mut valid = StorageState::default();
    valid
        .apply_batch_projected(
            Actor::Server,
            &[write("main", request, VersionCheck::Any)],
            |_| Ok(br#"{"a": 1}"#.to_vec()),
        )
        .unwrap();
    for stale_witness in [false, true] {
        let mut state = valid.clone();
        let object = state.objects.get_mut(&key("main")).unwrap();
        if stale_witness {
            object.collision_witness =
                Some(CollisionWitness::from_request(request, b"different projection").unwrap());
        } else {
            object.integrity_digest = IntegrityDigest::from_value(b"different projection");
        }
        let before = state.clone();
        assert_eq!(
            state.read(Actor::Server, &key("main")).unwrap_err().code(),
            StableCode::DataLoss
        );
        assert_eq!(
            state
                .apply_batch_projected(
                    Actor::Server,
                    &[write("main", request, VersionCheck::Any)],
                    |_| panic!("corruption must reject before no-op/projection")
                )
                .unwrap_err()
                .code(),
            StableCode::DataLoss
        );
        assert_eq!(state, before);
        assert_eq!(
            state
                .apply_batch(
                    Actor::Server,
                    &[BatchOperation::Delete(DeleteOperation {
                        key: key("main"),
                        expected_version: None
                    })]
                )
                .unwrap_err()
                .code(),
            StableCode::DataLoss
        );
        assert_eq!(state, before);
    }
}

#[test]
fn projection_seam_keeps_permission_and_create_only_occ_precedence() {
    let request = br#"{"a":1}"#;
    let mut state = StorageState::default();
    let mut operation = write("main", request, VersionCheck::Any);
    let BatchOperation::Write(write) = &mut operation else {
        unreachable!()
    };
    write.write_permission = WritePermission::None;
    state.apply_batch(Actor::Server, &[operation]).unwrap();
    let before = state.clone();
    for (actor, expected, code) in [
        (
            Actor::User(UserId::new([1; 16])),
            VersionCheck::Any,
            StableCode::PermissionDenied,
        ),
        (
            Actor::User(UserId::new([1; 16])),
            VersionCheck::Exact("stale".into()),
            StableCode::PermissionDenied,
        ),
        (
            Actor::User(UserId::new([1; 16])),
            VersionCheck::MustNotExist,
            StableCode::AlreadyExists,
        ),
        (
            Actor::User(UserId::new([2; 16])),
            VersionCheck::MustNotExist,
            StableCode::PermissionDenied,
        ),
    ] {
        let error = state
            .apply_batch_projected(actor, &[self::write("main", request, expected)], |_| {
                panic!("failed authority/OCC must precede projection")
            })
            .unwrap_err();
        assert_eq!(error.code(), code);
        assert_eq!(state, before);
    }
}
