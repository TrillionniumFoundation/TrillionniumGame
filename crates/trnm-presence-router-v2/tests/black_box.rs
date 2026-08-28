use trnm_presence_router_v2::{
    ConnectionGeneration, ConnectionId, ConnectionRef, JoinPresenceRequest, LeavePresenceRequest,
    MutationDisposition, NodeId, PresenceError, PresenceIdentity, PresenceRouter, PresenceStatus,
    RemoveConnectionRequest, SessionId, SnapshotVisibility, StreamKey, UpdatePresenceRequest,
    UserId, Username, ValidationError, MAX_CONNECTION_ID_BYTES, MAX_STATUS_BYTES,
};

fn connection(node: &str, connection_id: &str) -> ConnectionRef {
    ConnectionRef::new(
        NodeId::new(node).unwrap(),
        ConnectionId::new(connection_id).unwrap(),
    )
}

fn generation(value: u64) -> ConnectionGeneration {
    ConnectionGeneration::new(value).unwrap()
}

fn make_stream(mode: u8, label: &str, seed: u8) -> StreamKey {
    StreamKey::new(mode, [seed; 16], [seed.wrapping_add(1); 16], label).unwrap()
}

fn identity(user: &str, session: &str, username: &str) -> PresenceIdentity {
    PresenceIdentity::new(
        UserId::new(user).unwrap(),
        SessionId::new(session).unwrap(),
        Username::new(username).unwrap(),
    )
}

fn join_request(
    connection: ConnectionRef,
    generation_value: u64,
    stream: StreamKey,
    identity: PresenceIdentity,
    status: &str,
    hidden: bool,
) -> JoinPresenceRequest {
    JoinPresenceRequest {
        connection,
        generation: generation(generation_value),
        stream,
        identity,
        status: PresenceStatus::new(status).unwrap(),
        hidden,
    }
}

#[test]
fn exact_duplicate_and_visibility_transitions_are_deterministic() {
    let mut router = PresenceRouter::new();
    let connection = connection("node-a", "connection-a");
    let match_stream = make_stream(1, "match-a", 1);
    let identity = identity("user-a", "session-a", "alice");
    let request = join_request(
        connection.clone(),
        1,
        match_stream.clone(),
        identity,
        "ready",
        false,
    );
    let first = router.join_presence(request.clone()).unwrap();
    assert_eq!(first.disposition, MutationDisposition::Applied);
    assert_eq!(first.revision, Some(1));
    assert_eq!(first.joins.len(), 1);

    let duplicate = router.join_presence(request).unwrap();
    assert_eq!(duplicate.disposition, MutationDisposition::Idempotent);
    assert_eq!(router.revision(), 1);

    let hidden = router
        .update_presence(UpdatePresenceRequest {
            connection: connection.clone(),
            generation: generation(1),
            stream: match_stream.clone(),
            status: PresenceStatus::new("quiet").unwrap(),
            hidden: true,
        })
        .unwrap();
    assert_eq!(hidden.leaves.len(), 1);
    assert!(router
        .snapshot(&match_stream, SnapshotVisibility::PublicOnly)
        .unwrap()
        .is_empty());

    let visible = router
        .update_presence(UpdatePresenceRequest {
            connection,
            generation: generation(1),
            stream: match_stream.clone(),
            status: PresenceStatus::new("back").unwrap(),
            hidden: false,
        })
        .unwrap();
    assert_eq!(visible.joins.len(), 1);
    assert_eq!(
        router
            .snapshot(&match_stream, SnapshotVisibility::PublicOnly)
            .unwrap()[0]
            .status
            .as_str(),
        "back"
    );
}

#[test]
fn identity_remains_bound_after_last_stream_is_left() {
    let mut router = PresenceRouter::new();
    let connection = connection("node-a", "connection-a");
    let match_stream = make_stream(1, "match-a", 1);
    let original = identity("user-a", "session-a", "alice");
    router
        .join_presence(join_request(
            connection.clone(),
            3,
            match_stream.clone(),
            original.clone(),
            "ready",
            false,
        ))
        .unwrap();
    router
        .leave_presence(LeavePresenceRequest {
            connection: connection.clone(),
            generation: generation(3),
            stream: match_stream.clone(),
        })
        .unwrap();
    assert_eq!(router.entry_count(), 0);
    assert_eq!(router.connection_count(), 1);
    assert_eq!(
        router.established_generation(&connection),
        Some(generation(3))
    );
    assert_eq!(router.established_identity(&connection), Some(&original));

    let revision = router.revision();
    assert!(matches!(
        router.join_presence(join_request(
            connection.clone(),
            3,
            match_stream.clone(),
            identity("user-b", "session-b", "bob"),
            "forbidden",
            false,
        )),
        Err(PresenceError::IdentityConflict { .. })
    ));
    assert_eq!(router.revision(), revision);
    assert_eq!(router.entry_count(), 0);

    let rejoin = router
        .join_presence(join_request(
            connection,
            3,
            match_stream,
            original,
            "back",
            false,
        ))
        .unwrap();
    assert_eq!(rejoin.joins.len(), 1);
}

#[test]
fn remove_connection_keeps_generation_and_identity_tombstone() {
    let mut router = PresenceRouter::new();
    let connection = connection("node-a", "connection-a");
    let original = identity("user-a", "session-a", "alice");
    for (label, seed) in [("match-a", 1), ("party-a", 2)] {
        router
            .join_presence(join_request(
                connection.clone(),
                5,
                make_stream(1, label, seed),
                original.clone(),
                "ready",
                false,
            ))
            .unwrap();
    }
    let removed = router
        .remove_connection(RemoveConnectionRequest {
            connection: connection.clone(),
            generation: generation(5),
        })
        .unwrap();
    assert_eq!(removed.leaves.len(), 2);
    assert_eq!(router.entry_count(), 0);
    assert_eq!(
        router.established_generation(&connection),
        Some(generation(5))
    );
    assert_eq!(router.established_identity(&connection), Some(&original));

    assert!(matches!(
        router.join_presence(join_request(
            connection,
            5,
            make_stream(1, "match-a", 1),
            identity("other", "other-session", "other"),
            "forbidden",
            false,
        )),
        Err(PresenceError::IdentityConflict { .. })
    ));
}

#[test]
fn higher_generation_atomically_retires_all_old_streams() {
    let mut router = PresenceRouter::new();
    let connection = connection("node-a", "connection-a");
    let old_identity = identity("user-a", "session-old", "alice");
    let first_stream = make_stream(1, "match-a", 1);
    let second_stream = make_stream(2, "party-a", 2);
    router
        .join_presence(join_request(
            connection.clone(),
            7,
            first_stream.clone(),
            old_identity.clone(),
            "one",
            false,
        ))
        .unwrap();
    router
        .join_presence(join_request(
            connection.clone(),
            7,
            second_stream,
            old_identity,
            "two",
            false,
        ))
        .unwrap();

    let replacement_identity = identity("user-a", "session-new", "alice");
    let delta = router
        .join_presence(join_request(
            connection.clone(),
            8,
            first_stream.clone(),
            replacement_identity.clone(),
            "new",
            false,
        ))
        .unwrap();
    assert_eq!(delta.leaves.len(), 2);
    assert_eq!(delta.joins.len(), 1);
    assert_eq!(delta.revision, Some(3));
    assert_eq!(router.entry_count(), 1);
    assert_eq!(
        router.established_generation(&connection),
        Some(generation(8))
    );
    assert_eq!(
        router.established_identity(&connection),
        Some(&replacement_identity)
    );
    assert_eq!(
        router
            .snapshot(&first_stream, SnapshotVisibility::PublicOnly)
            .unwrap()[0]
            .identity
            .session_id
            .as_str(),
        "session-new"
    );
}

#[test]
fn stale_and_future_non_join_mutations_are_rejected_atomically() {
    let mut router = PresenceRouter::new();
    let connection = connection("node-a", "connection-a");
    let match_stream = make_stream(1, "match-a", 1);
    router
        .join_presence(join_request(
            connection.clone(),
            10,
            match_stream.clone(),
            identity("user-a", "session-a", "alice"),
            "ready",
            false,
        ))
        .unwrap();
    let revision = router.revision();
    let before = router
        .snapshot(&match_stream, SnapshotVisibility::IncludeHidden)
        .unwrap();

    assert!(matches!(
        router.update_presence(UpdatePresenceRequest {
            connection: connection.clone(),
            generation: generation(9),
            stream: match_stream.clone(),
            status: PresenceStatus::new("stale").unwrap(),
            hidden: false,
        }),
        Err(PresenceError::StaleGeneration { .. })
    ));
    assert!(matches!(
        router.leave_presence(LeavePresenceRequest {
            connection: connection.clone(),
            generation: generation(11),
            stream: match_stream.clone(),
        }),
        Err(PresenceError::GenerationAhead { .. })
    ));
    assert!(matches!(
        router.remove_connection(RemoveConnectionRequest {
            connection,
            generation: generation(9),
        }),
        Err(PresenceError::StaleGeneration { .. })
    ));
    assert_eq!(router.revision(), revision);
    assert_eq!(
        router
            .snapshot(&match_stream, SnapshotVisibility::IncludeHidden)
            .unwrap(),
        before
    );
}

#[test]
fn snapshots_are_sorted_and_filter_hidden_records() {
    let mut router = PresenceRouter::new();
    let match_stream = make_stream(1, "match-a", 1);
    for (node, connection_id, session, hidden) in [
        ("node-b", "connection-b", "session-b", false),
        ("node-a", "connection-c", "session-a", true),
        ("node-a", "connection-a", "session-a", false),
    ] {
        router
            .join_presence(join_request(
                connection(node, connection_id),
                1,
                match_stream.clone(),
                identity("user-a", session, "alice"),
                "ready",
                hidden,
            ))
            .unwrap();
    }

    let public = router
        .snapshot(&match_stream, SnapshotVisibility::PublicOnly)
        .unwrap();
    let public_order: Vec<_> = public
        .iter()
        .map(|record| record.connection.connection_id.as_str())
        .collect();
    assert_eq!(public_order, ["connection-a", "connection-b"]);

    let all = router
        .snapshot(&match_stream, SnapshotVisibility::IncludeHidden)
        .unwrap();
    let all_order: Vec<_> = all
        .iter()
        .map(|record| record.connection.connection_id.as_str())
        .collect();
    assert_eq!(all_order, ["connection-a", "connection-c", "connection-b"]);
}

#[test]
fn validated_types_reject_zero_empty_control_and_oversize_inputs() {
    assert_eq!(
        ConnectionGeneration::new(0),
        Err(ValidationError::ZeroGeneration)
    );
    assert!(matches!(
        ConnectionId::new(""),
        Err(ValidationError::Empty {
            field: "connection_id"
        })
    ));
    assert!(matches!(
        ConnectionId::new("a\nb"),
        Err(ValidationError::ControlCharacter {
            field: "connection_id"
        })
    ));
    assert!(matches!(
        ConnectionId::new("x".repeat(MAX_CONNECTION_ID_BYTES + 1)),
        Err(ValidationError::TooLong {
            field: "connection_id",
            ..
        })
    ));
    assert!(matches!(
        PresenceStatus::new("x".repeat(MAX_STATUS_BYTES + 1)),
        Err(ValidationError::TooLong {
            field: "status",
            ..
        })
    ));
    assert_eq!(
        StreamKey::new(0, [0; 16], [0; 16], ""),
        Err(ValidationError::InvalidStreamMode)
    );
}

#[test]
fn invariant_check_remains_green_after_mixed_mutations() {
    let mut router = PresenceRouter::new();
    let match_stream = make_stream(1, "match-a", 1);
    for index in 0..16 {
        let connection_id = format!("connection-{index:02}");
        let session_id = format!("session-{index:02}");
        let connection = connection("node-a", &connection_id);
        router
            .join_presence(join_request(
                connection.clone(),
                1,
                match_stream.clone(),
                identity("user-a", &session_id, "alice"),
                "ready",
                index % 3 == 0,
            ))
            .unwrap();
        if index % 2 == 0 {
            router
                .update_presence(UpdatePresenceRequest {
                    connection,
                    generation: generation(1),
                    stream: match_stream.clone(),
                    status: PresenceStatus::new("playing").unwrap(),
                    hidden: index % 4 == 0,
                })
                .unwrap();
        }
    }
    router.verify_invariants().unwrap();
    assert_eq!(router.entry_count(), 16);
    assert_eq!(router.connection_count(), 16);
}
