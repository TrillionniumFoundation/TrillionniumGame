#[test]
fn exact_replay_is_scoped_to_operation_and_actor() {
    let mut registry = registry_with_users(3);
    let receipt = registry
        .send_friend_request(command(1), fingerprint(1), account(1), account(2))
        .unwrap();
    let unchanged = registry.clone();
    let replay = registry
        .send_friend_request(command(1), fingerprint(1), account(1), account(2))
        .unwrap();
    assert_eq!(receipt, replay);
    assert_eq!(unchanged, registry);

    let before_operation = registry.clone();
    let error = registry
        .block_user(command(1), fingerprint(1), account(1), account(2))
        .unwrap_err();
    assert_eq!(error.code(), SocialErrorCode::Conflict);
    assert_eq!(error.reason(), "social_command_operation_mismatch");
    assert_eq!(before_operation, registry);

    let before_actor = registry.clone();
    let error = registry
        .send_friend_request(command(1), fingerprint(1), account(3), account(2))
        .unwrap_err();
    assert_eq!(error.code(), SocialErrorCode::Conflict);
    assert_eq!(error.reason(), "social_command_actor_mismatch");
    assert_eq!(before_actor, registry);
}

#[test]
fn join_group_replay_accepts_both_operation_outcomes_only() {
    let mut registry = registry_with_users(2);
    registry
        .create_group(
            command(1),
            fingerprint(1),
            CreateGroupRequest::new(
                group(1),
                account(1),
                GroupName::new("approval").unwrap(),
                GroupJoinMode::AdminApproval,
                10,
            ),
        )
        .unwrap();
    let receipt = registry
        .join_group(command(2), fingerprint(2), group(1), account(2))
        .unwrap();
    assert_eq!(receipt.outcome(), ReceiptOutcome::GroupJoinRequested);
    let snapshot = registry.clone();
    let replay = registry
        .join_group(command(2), fingerprint(2), group(1), account(2))
        .unwrap();
    assert_eq!(receipt, replay);
    assert_eq!(snapshot, registry);

    let error = registry
        .leave_group(command(2), fingerprint(2), group(1), account(2))
        .unwrap_err();
    assert_eq!(error.code(), SocialErrorCode::Conflict);
    assert_eq!(error.reason(), "social_command_operation_mismatch");
    assert_eq!(snapshot, registry);
}
