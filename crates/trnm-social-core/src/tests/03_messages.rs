#[test]
fn group_chat_sequences_and_cursor_scope_are_exact() {
    let mut registry = registry_with_users(2);
    let first_group = group(1);
    let second_group = group(2);
    create_open_group(&mut registry, account(1), first_group);
    registry
        .create_group(
            command(101),
            fingerprint(101),
            CreateGroupRequest::new(
                second_group,
                account(1),
                GroupName::new("second").expect("name"),
                GroupJoinMode::Open,
                10,
            ),
        )
        .expect("second group");
    registry
        .join_group(command(1), fingerprint(1), first_group, account(2))
        .expect("join");
    for value in 1_u8..=3 {
        registry
            .send_group_message(
                command(value + 10),
                fingerprint(value + 10),
                SendGroupMessageRequest::new(
                    first_group,
                    account(2),
                    message(value),
                    MessageBody::new(vec![value]).expect("body"),
                ),
            )
            .expect("message");
    }
    let first = registry
        .list_group_messages(first_group, None, 2)
        .expect("page");
    assert_eq!(first.items()[0].sequence(), 1);
    assert_eq!(first.items()[1].sequence(), 2);
    let second = registry
        .list_group_messages(first_group, first.next(), 2)
        .expect("page");
    assert_eq!(second.items()[0].sequence(), 3);
    assert_eq!(
        registry
            .list_group_messages(second_group, first.next(), 2)
            .expect_err("cursor mismatch")
            .reason(),
        "message_cursor_group_mismatch"
    );
}

#[test]
fn message_command_replay_does_not_duplicate_history() {
    let mut registry = registry_with_users(1);
    let group_id = group(1);
    create_open_group(&mut registry, account(1), group_id);
    let request = SendGroupMessageRequest::new(
        group_id,
        account(1),
        message(1),
        MessageBody::new(b"payload".to_vec()).expect("body"),
    );
    let first = registry
        .send_group_message(command(1), fingerprint(1), request.clone())
        .expect("send");
    let second = registry
        .send_group_message(command(1), fingerprint(1), request)
        .expect("replay");
    assert_eq!(first, second);
    assert_eq!(
        registry
            .list_group_messages(group_id, None, 10)
            .expect("history")
            .items()
            .len(),
        1
    );
}
