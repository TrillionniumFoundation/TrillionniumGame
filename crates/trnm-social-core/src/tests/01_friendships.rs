#[test]
fn friend_request_acceptance_and_exact_replay_are_atomic() {
    let mut registry = registry_with_users(2);
    let sent = registry
        .send_friend_request(command(1), fingerprint(1), account(1), account(2))
        .expect("request");
    assert_eq!(sent.outcome(), ReceiptOutcome::FriendRequestSent);
    assert_eq!(sent.outbox_count(), 1);
    assert_eq!(
        registry.relationship(account(1), account(2)).expect("relationship"),
        RelationshipView::OutgoingRequest
    );
    let replay = registry
        .send_friend_request(command(1), fingerprint(1), account(1), account(2))
        .expect("replay");
    assert_eq!(replay, sent);
    let accepted = registry
        .accept_friend_request(command(2), fingerprint(2), account(2), account(1))
        .expect("accept");
    assert_eq!(accepted.outcome(), ReceiptOutcome::FriendRequestAccepted);
    assert_eq!(
        registry.relationship(account(1), account(2)).expect("relationship"),
        RelationshipView::Friends
    );
    assert_eq!(registry.outbox_intents().len(), 2);
}

#[test]
fn changed_fingerprint_rejects_without_state_change() {
    let mut registry = registry_with_users(2);
    registry
        .send_friend_request(command(1), fingerprint(1), account(1), account(2))
        .expect("request");
    let before = registry.clone();
    let error = registry
        .send_friend_request(command(1), fingerprint(9), account(1), account(2))
        .expect_err("conflict");
    assert_eq!(error.code(), SocialErrorCode::Conflict);
    assert_eq!(registry, before);
}

#[test]
fn blocking_removes_friend_state_and_prevents_reentry() {
    let mut registry = registry_with_users(2);
    registry
        .send_friend_request(command(1), fingerprint(1), account(1), account(2))
        .expect("request");
    registry
        .accept_friend_request(command(2), fingerprint(2), account(2), account(1))
        .expect("accept");
    registry
        .block_user(command(3), fingerprint(3), account(1), account(2))
        .expect("block");
    assert_eq!(
        registry.relationship(account(1), account(2)).expect("relationship"),
        RelationshipView::BlockedBySelf
    );
    let before = registry.clone();
    assert_eq!(
        registry
            .send_friend_request(command(4), fingerprint(4), account(2), account(1))
            .expect_err("blocked")
            .code(),
        SocialErrorCode::PermissionDenied
    );
    assert_eq!(registry, before);
    registry
        .unblock_user(command(5), fingerprint(5), account(1), account(2))
        .expect("unblock");
    assert_eq!(
        registry.relationship(account(1), account(2)).expect("relationship"),
        RelationshipView::None
    );
}

#[test]
fn friend_pagination_is_stable_and_cursor_scoped() {
    let mut registry = registry_with_users(5);
    for (index, target) in [2_u8, 3, 4].into_iter().enumerate() {
        let command_value = u8::try_from(index * 2 + 1).expect("command");
        registry
            .send_friend_request(
                command(command_value),
                fingerprint(command_value),
                account(1),
                account(target),
            )
            .expect("request");
        registry
            .accept_friend_request(
                command(command_value + 1),
                fingerprint(command_value + 1),
                account(target),
                account(1),
            )
            .expect("accept");
    }
    let first = registry
        .list_friends(account(1), None, 2)
        .expect("first page");
    assert_eq!(first.items(), &[account(2), account(3)]);
    let second = registry
        .list_friends(account(1), first.next(), 2)
        .expect("second page");
    assert_eq!(second.items(), &[account(4)]);
    assert!(second.next().is_none());
    assert_eq!(
        registry
            .list_friends(account(2), first.next(), 2)
            .expect_err("cursor owner")
            .reason(),
        "friend_cursor_owner_mismatch"
    );
}
