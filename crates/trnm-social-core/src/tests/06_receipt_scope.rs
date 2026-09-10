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

fn intent_for(registry: &SocialRegistry, command: SocialCommandId) -> OutboxIntent {
    registry
        .outbox_intents()
        .find(|intent| intent.id().command() == command)
        .expect("outbox intent")
}

#[test]
fn outbox_payloads_bind_complete_delivery_subjects() {
    let mut registry = registry_with_users(3);

    registry
        .send_friend_request(command(1), fingerprint(1), account(1), account(2))
        .expect("friend request");
    let intent = intent_for(&registry, command(1));
    assert_eq!(intent.kind(), OutboxIntentKind::FriendRequest);
    assert_eq!(intent.recipient(), Some(account(2)));
    assert_eq!(
        intent.payload(),
        &OutboxIntentPayload::FriendRequest {
            requester: account(1),
            target: account(2),
        }
    );

    registry
        .accept_friend_request(command(2), fingerprint(2), account(2), account(1))
        .expect("accept friend");
    assert_eq!(
        intent_for(&registry, command(2)).payload(),
        &OutboxIntentPayload::FriendAccepted {
            accepter: account(2),
            requester: account(1),
        }
    );

    let group_id = group(1);
    registry
        .create_group(
            command(3),
            fingerprint(3),
            CreateGroupRequest::new(
                group_id,
                account(1),
                GroupName::new("approval").expect("group name"),
                GroupJoinMode::AdminApproval,
                3,
            ),
        )
        .expect("create group");
    registry
        .join_group(command(4), fingerprint(4), group_id, account(2))
        .expect("request group join");
    assert_eq!(
        intent_for(&registry, command(4)).payload(),
        &OutboxIntentPayload::GroupJoinRequested {
            group: group_id,
            requester: account(2),
        }
    );

    registry
        .approve_group_join(command(5), fingerprint(5), group_id, account(1), account(2))
        .expect("approve group join");
    assert_eq!(
        intent_for(&registry, command(5)).payload(),
        &OutboxIntentPayload::GroupJoinAccepted {
            group: group_id,
            actor: account(1),
            member: account(2),
        }
    );

    registry
        .change_group_role(
            command(6),
            fingerprint(6),
            group_id,
            account(1),
            account(2),
            GroupRole::Admin,
        )
        .expect("change role");
    assert_eq!(
        intent_for(&registry, command(6)).payload(),
        &OutboxIntentPayload::GroupRoleChanged {
            group: group_id,
            actor: account(1),
            member: account(2),
            role: GroupRole::Admin,
        }
    );

    let body = MessageBody::new(b"group-message".to_vec()).expect("message body");
    registry
        .send_group_message(
            command(7),
            fingerprint(7),
            SendGroupMessageRequest::new(
                group_id,
                account(2),
                message(1),
                body.clone(),
            ),
        )
        .expect("send group message");
    assert_eq!(
        intent_for(&registry, command(7)).payload(),
        &OutboxIntentPayload::ChatMessage {
            group: group_id,
            message: message(1),
            sender: account(2),
            sequence: 1,
            body,
        }
    );

    let subject = NotificationText::new("subject").expect("subject");
    let content = NotificationText::new("content").expect("content");
    registry
        .create_notification(
            command(8),
            fingerprint(8),
            CreateNotificationRequest::new(
                Some(account(1)),
                account(3),
                notification(1),
                NotificationKind::Custom(7),
                subject.clone(),
                content.clone(),
            ),
        )
        .expect("create notification");
    let notification_intent = intent_for(&registry, command(8));
    assert_eq!(notification_intent.recipient(), Some(account(3)));
    assert_eq!(
        notification_intent.payload(),
        &OutboxIntentPayload::NotificationDelivery {
            sender: Some(account(1)),
            recipient: account(3),
            notification: notification(1),
            kind: NotificationKind::Custom(7),
            subject,
            content,
            sequence: registry.receipt(command(8)).expect("receipt").revision(),
        }
    );
}

#[test]
fn notification_delivery_payload_survives_source_record_deletion_and_is_redacted() {
    let mut registry = registry_with_users(2);
    registry
        .create_notification(
            command(1),
            fingerprint(1),
            CreateNotificationRequest::new(
                Some(account(1)),
                account(2),
                notification(1),
                NotificationKind::Custom(1),
                NotificationText::new("private-subject").expect("subject"),
                NotificationText::new("private-content").expect("content"),
            ),
        )
        .expect("notification");
    let retained = intent_for(&registry, command(1));

    registry
        .delete_notification(command(2), fingerprint(2), account(2), notification(1))
        .expect("delete notification");

    assert_eq!(retained.kind(), OutboxIntentKind::NotificationDelivery);
    match retained.payload() {
        OutboxIntentPayload::NotificationDelivery {
            notification: retained_id,
            subject,
            content,
            ..
        } => {
            assert_eq!(*retained_id, notification(1));
            assert_eq!(subject.as_str(), "private-subject");
            assert_eq!(content.as_str(), "private-content");
        }
        other => panic!("unexpected payload: {other:?}"),
    }
    let debug = format!("{retained:?}");
    assert!(!debug.contains("private-subject"));
    assert!(!debug.contains("private-content"));
}
