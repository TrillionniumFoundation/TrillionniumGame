#[test]
fn approval_group_join_requires_admin_and_is_bounded() {
    let mut registry = registry_with_users(3);
    let group_id = group(1);
    registry
        .create_group(
            command(1),
            fingerprint(1),
            CreateGroupRequest::new(
                group_id,
                account(1),
                GroupName::new("approval").expect("name"),
                GroupJoinMode::AdminApproval,
                2,
            ),
        )
        .expect("create");
    let request = registry
        .join_group(command(2), fingerprint(2), group_id, account(2))
        .expect("request");
    assert_eq!(request.outcome(), ReceiptOutcome::GroupJoinRequested);
    assert!(registry.group(group_id).expect("group").has_join_request(account(2)));
    registry
        .approve_group_join(command(3), fingerprint(3), group_id, account(1), account(2))
        .expect("approve");
    assert_eq!(
        registry.group(group_id).expect("group").role(account(2)),
        Some(GroupRole::Member)
    );
    registry
        .join_group(command(4), fingerprint(4), group_id, account(3))
        .expect("request");
    assert_eq!(
        registry
            .approve_group_join(command(5), fingerprint(5), group_id, account(1), account(3))
            .expect_err("capacity")
            .reason(),
        "group_member_capacity_exhausted"
    );
}

#[test]
fn last_superadmin_is_never_removed() {
    let mut registry = registry_with_users(2);
    let group_id = group(1);
    create_open_group(&mut registry, account(1), group_id);
    registry
        .join_group(command(1), fingerprint(1), group_id, account(2))
        .expect("join");
    let before = registry.clone();
    assert_eq!(
        registry
            .leave_group(command(2), fingerprint(2), group_id, account(1))
            .expect_err("last superadmin")
            .reason(),
        "last_superadmin_cannot_leave"
    );
    assert_eq!(registry, before);
    registry
        .change_group_role(
            command(3),
            fingerprint(3),
            group_id,
            account(1),
            account(2),
            GroupRole::Superadmin,
        )
        .expect("promote");
    registry
        .change_group_role(
            command(4),
            fingerprint(4),
            group_id,
            account(1),
            account(1),
            GroupRole::Member,
        )
        .expect("demote self");
    registry
        .leave_group(command(5), fingerprint(5), group_id, account(1))
        .expect("leave");
    assert_eq!(
        registry.group(group_id).expect("group").role(account(2)),
        Some(GroupRole::Superadmin)
    );
}

#[test]
fn ban_clears_join_request_and_blocks_rejoin() {
    let mut registry = registry_with_users(2);
    let group_id = group(1);
    registry
        .create_group(
            command(1),
            fingerprint(1),
            CreateGroupRequest::new(
                group_id,
                account(1),
                GroupName::new("approval").expect("name"),
                GroupJoinMode::AdminApproval,
                10,
            ),
        )
        .expect("create");
    registry
        .join_group(command(2), fingerprint(2), group_id, account(2))
        .expect("request");
    registry
        .ban_group_member(command(3), fingerprint(3), group_id, account(1), account(2))
        .expect("ban");
    let record = registry.group(group_id).expect("group");
    assert!(record.is_banned(account(2)));
    assert!(!record.has_join_request(account(2)));
    assert_eq!(
        registry
            .join_group(command(4), fingerprint(4), group_id, account(2))
            .expect_err("banned")
            .reason(),
        "group_member_banned"
    );
    registry
        .unban_group_member(command(5), fingerprint(5), group_id, account(1), account(2))
        .expect("unban");
}
