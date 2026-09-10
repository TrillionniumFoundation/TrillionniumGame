#[test]
fn invalid_configuration_is_rejected() {
    let limits = SocialLimits {
        max_users: 0,
        ..SocialLimits::default()
    };
    assert_eq!(
        SocialConfig::new(limits).expect_err("invalid").reason(),
        "user_capacity_invalid"
    );
    let limits = SocialLimits {
        max_message_bytes: MAX_MESSAGE_BYTES + 1,
        ..SocialLimits::default()
    };
    assert_eq!(
        SocialConfig::new(limits).expect_err("invalid").reason(),
        "message_byte_limit_invalid"
    );
}
