use trnm_presence_router_v2::{
    ConnectionGeneration, ConnectionId, ConnectionRef, JoinPresenceRequest, LeavePresenceRequest,
    NodeId, PresenceDelta, PresenceError, PresenceIdentity, PresenceRouter, PresenceStatus,
    SessionId, SnapshotVisibility, StreamKey, UpdatePresenceRequest, UserId, Username,
};

fn connection(node: &str, id: &str) -> ConnectionRef {
    ConnectionRef::new(NodeId::new(node).unwrap(), ConnectionId::new(id).unwrap())
}

fn identity(session: &str, username: &str) -> PresenceIdentity {
    PresenceIdentity::new(
        UserId::new("user-a").unwrap(),
        SessionId::new(session).unwrap(),
        Username::new(username).unwrap(),
    )
}

fn generation(value: u64) -> ConnectionGeneration {
    ConnectionGeneration::new(value).unwrap()
}

fn request(
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

fn print_delta(name: &str, delta: &PresenceDelta) {
    println!(
        "{name}=applied:{}:{}:{}:{}:{}",
        delta.revision.unwrap_or(0),
        delta.joins.len(),
        delta.updates.len(),
        delta.leaves.len(),
        delta.hidden_changes
    );
}

fn main() {
    let mut router = PresenceRouter::new();
    let match_stream = StreamKey::new(1, [1; 16], [2; 16], "match-a").unwrap();
    let first_connection = connection("node-a", "connection-a");
    let hidden_connection = connection("node-b", "connection-b");

    let join_visible = router
        .join_presence(request(
            first_connection.clone(),
            1,
            match_stream.clone(),
            identity("session-a", "alice"),
            "ready",
            false,
        ))
        .unwrap();
    print_delta("join_visible", &join_visible);

    let join_hidden = router
        .join_presence(request(
            hidden_connection,
            1,
            match_stream.clone(),
            identity("session-b", "bob"),
            "hidden",
            true,
        ))
        .unwrap();
    print_delta("join_hidden", &join_hidden);

    let update = router
        .update_presence(UpdatePresenceRequest {
            connection: first_connection.clone(),
            generation: generation(1),
            stream: match_stream.clone(),
            status: PresenceStatus::new("playing").unwrap(),
            hidden: false,
        })
        .unwrap();
    print_delta("update", &update);

    let leave = router
        .leave_presence(LeavePresenceRequest {
            connection: first_connection.clone(),
            generation: generation(1),
            stream: match_stream.clone(),
        })
        .unwrap();
    print_delta("leave", &leave);

    let rejoin = router
        .join_presence(request(
            first_connection.clone(),
            1,
            match_stream.clone(),
            identity("session-a", "alice"),
            "back",
            false,
        ))
        .unwrap();
    print_delta("rejoin", &rejoin);

    let replacement = router
        .join_presence(request(
            first_connection.clone(),
            2,
            match_stream.clone(),
            identity("session-c", "alice"),
            "new",
            false,
        ))
        .unwrap();
    print_delta("replacement", &replacement);

    let stale = router.join_presence(request(
        first_connection,
        1,
        match_stream.clone(),
        identity("session-a", "alice"),
        "stale",
        false,
    ));
    match stale {
        Err(PresenceError::StaleGeneration { .. }) => println!("stale=stale_generation"),
        other => panic!("unexpected stale result: {other:?}"),
    }

    println!("revision={}", router.revision());
    println!("entry_count={}", router.entry_count());
    println!("connection_count={}", router.connection_count());

    let public = router
        .snapshot(&match_stream, SnapshotVisibility::PublicOnly)
        .unwrap();
    let public_rows: Vec<_> = public
        .iter()
        .map(|record| {
            format!(
                "{}@{}/{}:{}",
                record.identity.session_id,
                record.connection.node_id,
                record.connection.connection_id,
                record.status
            )
        })
        .collect();
    println!("public={}", public_rows.join("|"));

    let all = router
        .snapshot(&match_stream, SnapshotVisibility::IncludeHidden)
        .unwrap();
    let all_rows: Vec<_> = all
        .iter()
        .map(|record| {
            format!(
                "{}@{}/{}:{}:{}",
                record.identity.session_id,
                record.connection.node_id,
                record.connection.connection_id,
                record.status,
                if record.hidden { "hidden" } else { "visible" }
            )
        })
        .collect();
    println!("all={}", all_rows.join("|"));
}
