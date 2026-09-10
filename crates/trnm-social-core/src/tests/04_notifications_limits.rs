#[test]
fn notifications_are_ordered_marked_and_deleted() {
    let mut registry = registry_with_users(2);
    for value in 1_u8..=3 {
        registry
            .create_notification(
                command(value),
                fingerprint(value),
                CreateNotificationRequest::new(
                    Some(account(1)),
                    account(2),
                    notification(value),
                    NotificationKind::Custom(u16::from(value)),
                    NotificationText::new(format!("subject-{value}")).expect("subject"),
                    NotificationText::new(format!("content-{value}")).expect("content"),
                ),
            )
            .expect("notification");
    }
    let first = registry
        .list_notifications(account(2), None, 2)
        .expect("page");
    assert_eq!(first.items().len(), 2);
    let second = registry
        .list_notifications(account(2), first.next(), 2)
        .expect("page");
    assert_eq!(second.items().len(), 1);
    registry
        .mark_notification_read(command(10), fingerprint(10), account(2), notification(1))
        .expect("read");
    assert!(registry
        .list_notifications(account(2), None, 10)
        .expect("list")
        .items()
        .iter()
        .find(|record| record.id() == notification(1))
        .expect("record")
        .is_read());
    registry
        .delete_notification(command(11), fingerprint(11), account(2), notification(1))
        .expect("delete");
    assert_eq!(
        registry
            .list_notifications(account(2), None, 10)
            .expect("list")
            .items()
            .len(),
        2
    );
}

#[test]
fn profile_limits_fail_before_mutating_state() {
    let limits = SocialLimits {
        max_users: 2,
        max_relationships_per_user: 1,
        max_groups: 1,
        max_group_members: 2,
        max_messages_per_group: 1,
        max_message_bytes: 2,
        max_notifications_per_user: 1,
        max_notification_text_bytes: 8,
        max_receipts: 20,
        max_outbox_intents: 20,
    };
    let mut registry = SocialRegistry::new(SocialConfig::new(limits).expect("config"));
    registry.register_user(account(1)).expect("user");
    registry.register_user(account(2)).expect("user");
    let before = registry.clone();
    assert_eq!(
        registry.register_user(account(3)).expect_err("capacity").reason(),
        "user_capacity_exhausted"
    );
    assert_eq!(registry, before);
    registry
        .send_friend_request(command(1), fingerprint(1), account(1), account(2))
        .expect("request");
    let before = registry.clone();
    assert_eq!(
        registry
            .create_notification(
                command(2),
                fingerprint(2),
                CreateNotificationRequest::new(
                    None,
                    account(1),
                    notification(1),
                    NotificationKind::Custom(1),
                    NotificationText::new("too-long-subject").expect("subject"),
                    NotificationText::new("too-long-content").expect("content"),
                ),
            )
            .expect_err("text limit")
            .reason(),
        "notification_text_exceeds_profile_limit"
    );
    assert_eq!(registry, before);
}

#[test]
fn payload_debug_is_redacted() {
    let body = MessageBody::new(b"secret-message".to_vec()).expect("body");
    let text = NotificationText::new("secret-notification").expect("text");
    let group_name = GroupName::new("secret-group").expect("name");
    assert!(!format!("{body:?}").contains("secret-message"));
    assert!(!format!("{text:?}").contains("secret-notification"));
    assert!(!format!("{group_name:?}").contains("secret-group"));
}
