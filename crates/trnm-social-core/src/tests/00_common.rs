use super::*;

fn account(value: u8) -> AccountId {
    let mut bytes = [0_u8; 16];
    bytes[15] = value;
    AccountId::new(bytes).expect("valid account")
}

fn command(value: u8) -> SocialCommandId {
    let mut bytes = [0_u8; 16];
    bytes[15] = value;
    SocialCommandId::new(bytes).expect("valid command")
}

fn fingerprint(value: u8) -> CommandFingerprint {
    let mut bytes = [0_u8; 32];
    bytes[31] = value;
    CommandFingerprint::new(bytes).expect("valid fingerprint")
}

fn group(value: u8) -> GroupId {
    let mut bytes = [0_u8; 16];
    bytes[15] = value;
    GroupId::new(bytes).expect("valid group")
}

fn message(value: u8) -> MessageId {
    let mut bytes = [0_u8; 16];
    bytes[15] = value;
    MessageId::new(bytes).expect("valid message")
}

fn notification(value: u8) -> NotificationId {
    let mut bytes = [0_u8; 16];
    bytes[15] = value;
    NotificationId::new(bytes).expect("valid notification")
}

fn registry_with_users(count: u8) -> SocialRegistry {
    let mut registry = SocialRegistry::new(SocialConfig::default());
    for value in 1..=count {
        assert!(registry.register_user(account(value)).expect("register"));
    }
    registry
}

fn create_open_group(registry: &mut SocialRegistry, owner: AccountId, id: GroupId) {
    registry
        .create_group(
            command(100),
            fingerprint(100),
            CreateGroupRequest::new(
                id,
                owner,
                GroupName::new("group").expect("name"),
                GroupJoinMode::Open,
                10,
            ),
        )
        .expect("create group");
}
