//! Source-model draft only; identity/supplied projection is not native JSONB.
use super::*;

fn owner() -> UserId {
    UserId::new([0x91; 16])
}
fn key(name: &str, user: UserId) -> StorageObjectKey {
    StorageObjectKey::new("duplicate-draft", name, user).unwrap()
}
fn write(
    key: &StorageObjectKey,
    value: &[u8],
    expected: VersionCheck,
    acl: WritePermission,
) -> BatchOperation {
    BatchOperation::Write(WriteOperation {
        key: key.clone(),
        value: value.to_vec(),
        expected,
        read_permission: ReadPermission::OWNER,
        write_permission: acl,
    })
}
fn delete(key: &StorageObjectKey, expected_version: Option<ExpectedVersion>) -> BatchOperation {
    BatchOperation::Delete(DeleteOperation {
        key: key.clone(),
        expected_version,
    })
}

#[test]
fn nakama_two_client_writes_keep_occurrence_receipts_and_last_step_state() {
    let key = key("client", owner());
    let a = br#"{"foo":"bar"}"#;
    let b = br#"{"foo":"baz"}"#;
    let mut state = StorageState::default();
    let operations = [
        write(&key, a, VersionCheck::Any, WritePermission::OWNER),
        write(&key, b, VersionCheck::Any, WritePermission::NONE),
    ];
    let receipts = state
        .apply_nakama_batch(Actor::User(owner()), &operations, NakamaBatchKind::Write)
        .unwrap();
    assert_eq!(receipts.len(), 2);
    assert_eq!(receipts[0].previous_version, None);
    assert_eq!(
        receipts[0].current_version,
        Some(ContentVersion::from_value(a))
    );
    assert_eq!(
        receipts[1]
            .previous_version
            .as_ref()
            .map(PublicVersion::as_str),
        Some(ContentVersion::from_value(a).as_str())
    );
    assert_eq!(
        receipts[1].current_version,
        Some(ContentVersion::from_value(b))
    );
    let final_row = state.read(Actor::Server, &key).unwrap();
    assert_eq!(final_row.value, b);
    assert_eq!(final_row.write_permission, WritePermission::NONE);
    assert_eq!(state.object_count(), 1);
}

#[test]
fn nakama_three_server_writes_bypass_step_acl_without_collapsing_acks() {
    let global = key("server", UserId::new([0; 16]));
    let values = [
        br#"{"v":1}"#.as_slice(),
        br#"{"v":2}"#.as_slice(),
        br#"{"v":3}"#.as_slice(),
    ];
    let operations = [
        write(
            &global,
            values[0],
            VersionCheck::Any,
            WritePermission::OWNER,
        ),
        write(&global, values[1], VersionCheck::Any, WritePermission::NONE),
        write(
            &global,
            values[2],
            VersionCheck::Any,
            WritePermission::OWNER,
        ),
    ];
    let mut state = StorageState::default();
    let receipts = state
        .apply_nakama_batch(Actor::Server, &operations, NakamaBatchKind::Write)
        .unwrap();
    assert_eq!(receipts.len(), 3);
    for (ordinal, value) in values.iter().enumerate() {
        assert_eq!(
            receipts[ordinal].current_version,
            Some(ContentVersion::from_value(value))
        );
    }
    assert_eq!(state.read(Actor::Server, &global).unwrap().value, values[2]);
}

#[test]
fn nakama_exact_condition_observes_the_preceding_same_key_insert() {
    let key = key("exact", owner());
    let a = br#"{"v":1}"#;
    let b = br#"{"v":2}"#;
    let operations = [
        write(&key, a, VersionCheck::Any, WritePermission::OWNER),
        write(
            &key,
            b,
            VersionCheck::Exact(ContentVersion::from_value(a).into()),
            WritePermission::OWNER,
        ),
    ];
    let mut state = StorageState::default();
    let receipts = state
        .apply_nakama_batch(Actor::User(owner()), &operations, NakamaBatchKind::Write)
        .unwrap();
    assert_eq!(
        receipts[1]
            .previous_version
            .as_ref()
            .map(PublicVersion::as_str),
        Some(ContentVersion::from_value(a).as_str())
    );
    assert_eq!(state.read(Actor::Server, &key).unwrap().value, b);
}

#[test]
fn nakama_step_acl_occ_and_insert_only_failures_restore_the_whole_model() {
    let key = key("rollback", owner());
    let base = br#"{"old":true}"#;
    let a = br#"{"v":1}"#;
    let b = br#"{"v":2}"#;
    let mut state = StorageState::default();
    state
        .apply_batch(
            Actor::Server,
            &[write(&key, base, VersionCheck::Any, WritePermission::OWNER)],
        )
        .unwrap();
    let original = state.clone();
    let cases = [
        (
            vec![
                write(&key, a, VersionCheck::Any, WritePermission::NONE),
                write(
                    &key,
                    b,
                    VersionCheck::Exact("stale".into()),
                    WritePermission::OWNER,
                ),
            ],
            StableCode::PermissionDenied,
        ),
        (
            vec![
                write(
                    &key,
                    a,
                    VersionCheck::Exact(ContentVersion::from_value(base).into()),
                    WritePermission::OWNER,
                ),
                write(
                    &key,
                    b,
                    VersionCheck::Exact(ContentVersion::from_value(base).into()),
                    WritePermission::OWNER,
                ),
            ],
            StableCode::FailedPrecondition,
        ),
        (
            vec![
                write(&key, a, VersionCheck::Any, WritePermission::NONE),
                write(&key, b, VersionCheck::MustNotExist, WritePermission::OWNER),
            ],
            StableCode::AlreadyExists,
        ),
    ];
    for (operations, code) in cases {
        let error = state
            .apply_nakama_batch(Actor::User(owner()), &operations, NakamaBatchKind::Write)
            .unwrap_err();
        assert_eq!(error.code(), code);
        assert_eq!(state, original);
    }
    let absent = key.clone();
    let mut state = StorageState::default();
    let operations = [
        write(
            &absent,
            a,
            VersionCheck::MustNotExist,
            WritePermission::NONE,
        ),
        write(
            &absent,
            b,
            VersionCheck::MustNotExist,
            WritePermission::OWNER,
        ),
    ];
    assert_eq!(
        state
            .apply_nakama_batch(Actor::User(owner()), &operations, NakamaBatchKind::Write)
            .unwrap_err()
            .code(),
        StableCode::AlreadyExists
    );
    assert_eq!(state.object_count(), 0);
}

#[test]
fn nakama_duplicate_client_delete_rolls_back_and_server_missing_is_explicit_noop() {
    let key = key("delete", owner());
    let mut state = StorageState::default();
    state
        .apply_batch(
            Actor::Server,
            &[write(
                &key,
                br#"{"v":1}"#,
                VersionCheck::Any,
                WritePermission::from_stored(32767).unwrap(),
            )],
        )
        .unwrap();
    let original = state.clone();
    let operations = [delete(&key, None), delete(&key, None)];
    assert_eq!(
        state
            .apply_nakama_batch(Actor::User(owner()), &operations, NakamaBatchKind::Delete)
            .unwrap_err()
            .code(),
        StableCode::NotFound
    );
    assert_eq!(state, original);
    let receipts = state
        .apply_nakama_batch(Actor::Server, &operations, NakamaBatchKind::Delete)
        .unwrap();
    assert!(receipts[0].previous_version.is_some());
    assert_eq!(receipts[1].previous_version, None);
    assert_eq!(receipts[1].current_version, None);
    assert_eq!(state.object_count(), 0);
    assert_eq!(
        state
            .apply_nakama_batch(
                Actor::Server,
                &[delete(&key, Some("*".into()))],
                NakamaBatchKind::Delete
            )
            .unwrap_err()
            .code(),
        StableCode::NotFound
    );
}

#[test]
fn nakama_sorted_candidate_preserves_input_ack_ordinals_and_projected_witnesses() {
    let a = key("a", owner());
    let b = key("b", owner());
    let operations = [
        write(&b, b"b1", VersionCheck::Any, WritePermission::OWNER),
        write(&a, b"a1", VersionCheck::Any, WritePermission::OWNER),
        write(&b, b"b2", VersionCheck::Any, WritePermission::OWNER),
        write(&a, b"a2", VersionCheck::Any, WritePermission::OWNER),
    ];
    // Four-item pinned Go-sort permutation; request projection remains a model.
    assert_eq!(
        plan_nakama_batch(&operations, NakamaBatchKind::Write).unwrap(),
        [1, 3, 0, 2]
    );
    let mut calls = Vec::new();
    let mut state = StorageState::default();
    let receipts = state
        .apply_nakama_batch_projected(
            Actor::User(owner()),
            &operations,
            NakamaBatchKind::Write,
            |request| {
                calls.push(request.to_vec());
                let mut projected = b"supplied-projection:".to_vec();
                projected.extend_from_slice(request);
                Ok(projected)
            },
        )
        .unwrap();
    assert_eq!(
        calls,
        [
            b"a1".to_vec(),
            b"a2".to_vec(),
            b"b1".to_vec(),
            b"b2".to_vec()
        ]
    );
    for (ordinal, operation) in operations.iter().enumerate() {
        let BatchOperation::Write(write) = operation else {
            unreachable!()
        };
        assert_eq!(receipts[ordinal].key, write.key);
        assert_eq!(
            receipts[ordinal].current_version,
            Some(ContentVersion::from_value(&write.value))
        );
    }
    let object = state.read(Actor::Server, &b).unwrap();
    assert_eq!(object.value, b"supplied-projection:b2");
    assert!(object.collision_witness.unwrap().matches_request(b"b2"));
}

#[test]
fn nakama_policy_is_homogeneous_and_internal_mixed_policy_remains_distinct() {
    let a = key("a", owner());
    let b = key("b", owner());
    let mut state = StorageState::default();
    state
        .apply_batch(
            Actor::Server,
            &[write(&b, b"b", VersionCheck::Any, WritePermission::OWNER)],
        )
        .unwrap();
    let original = state.clone();
    let mixed = [
        write(&a, b"a", VersionCheck::Any, WritePermission::OWNER),
        delete(&b, None),
    ];
    assert_eq!(
        state
            .apply_nakama_batch(Actor::Server, &mixed, NakamaBatchKind::Write)
            .unwrap_err()
            .reason(),
        "mixed_nakama_storage_batch"
    );
    assert_eq!(state, original);
    state.apply_batch(Actor::Server, &mixed).unwrap();
    let duplicates = [
        write(&a, b"v1", VersionCheck::Any, WritePermission::OWNER),
        write(&a, b"v2", VersionCheck::Any, WritePermission::OWNER),
    ];
    assert_eq!(
        state
            .apply_batch(Actor::Server, &duplicates)
            .unwrap_err()
            .reason(),
        "duplicate_storage_key_in_batch"
    );
}

#[test]
fn thirteen_occurrences_follow_observed_go_order_without_reordering_acks() {
    let values: Vec<_> = (0..13)
        .map(|ordinal| format!("{{\"v\":{ordinal}}}").into_bytes())
        .collect();
    let operations: Vec<_> = values
        .iter()
        .enumerate()
        .map(|(ordinal, value)| {
            write(
                &key(if ordinal < 2 { "b" } else { "a" }, owner()),
                value,
                VersionCheck::Any,
                if ordinal == 0 {
                    WritePermission::NONE
                } else {
                    WritePermission::OWNER
                },
            )
        })
        .collect();
    assert_eq!(
        plan_nakama_batch(&operations, NakamaBatchKind::Write).unwrap(),
        vec![6, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12, 1, 0]
    );
    let mut state = StorageState::default();
    let receipts = state
        .apply_nakama_batch(Actor::User(owner()), &operations, NakamaBatchKind::Write)
        .unwrap();
    assert_eq!(receipts.len(), 13);
    for (ordinal, receipt) in receipts.iter().enumerate() {
        assert_eq!(receipt.key, *operations[ordinal].key());
        assert_eq!(
            receipt.current_version,
            Some(ContentVersion::from_value(&values[ordinal]))
        );
    }
    let a = state.read(Actor::Server, &key("a", owner())).unwrap();
    let b = state.read(Actor::Server, &key("b", owner())).unwrap();
    assert_eq!(a.value, values[12]);
    assert_eq!(b.value, values[0]);
    assert_eq!(b.write_permission, WritePermission::NONE);
    assert_eq!(state.object_count(), 2);
}
