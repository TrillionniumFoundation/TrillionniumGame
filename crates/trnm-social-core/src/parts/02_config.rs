#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SocialLimits {
    pub max_users: usize,
    pub max_relationships_per_user: usize,
    pub max_groups: usize,
    pub max_group_members: usize,
    pub max_messages_per_group: usize,
    pub max_message_bytes: usize,
    pub max_notifications_per_user: usize,
    pub max_notification_text_bytes: usize,
    pub max_receipts: usize,
    pub max_outbox_intents: usize,
}

impl Default for SocialLimits {
    fn default() -> Self {
        Self {
            max_users: 100_000,
            max_relationships_per_user: 10_000,
            max_groups: 100_000,
            max_group_members: 10_000,
            max_messages_per_group: 100_000,
            max_message_bytes: 4_096,
            max_notifications_per_user: 100_000,
            max_notification_text_bytes: 4_096,
            max_receipts: 1_000_000,
            max_outbox_intents: 2_000_000,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SocialConfig {
    limits: SocialLimits,
}

impl SocialConfig {
    pub fn new(limits: SocialLimits) -> Result<Self, SocialError> {
        validate_limit(limits.max_users, HARD_MAX_USERS, "user_capacity_invalid")?;
        validate_limit(
            limits.max_relationships_per_user,
            HARD_MAX_RELATIONSHIPS_PER_USER,
            "relationship_capacity_invalid",
        )?;
        validate_limit(limits.max_groups, HARD_MAX_GROUPS, "group_capacity_invalid")?;
        validate_limit(
            limits.max_group_members,
            HARD_MAX_GROUP_MEMBERS,
            "group_member_capacity_invalid",
        )?;
        validate_limit(
            limits.max_messages_per_group,
            HARD_MAX_MESSAGES_PER_GROUP,
            "message_capacity_invalid",
        )?;
        validate_limit(
            limits.max_message_bytes,
            MAX_MESSAGE_BYTES,
            "message_byte_limit_invalid",
        )?;
        validate_limit(
            limits.max_notifications_per_user,
            HARD_MAX_NOTIFICATIONS_PER_USER,
            "notification_capacity_invalid",
        )?;
        validate_limit(
            limits.max_notification_text_bytes,
            MAX_NOTIFICATION_TEXT_BYTES,
            "notification_text_limit_invalid",
        )?;
        validate_limit(limits.max_receipts, HARD_MAX_RECEIPTS, "receipt_capacity_invalid")?;
        validate_limit(
            limits.max_outbox_intents,
            HARD_MAX_OUTBOX_INTENTS,
            "outbox_capacity_invalid",
        )?;
        Ok(Self { limits })
    }

    #[must_use]
    pub const fn limits(&self) -> SocialLimits {
        self.limits
    }
}

impl Default for SocialConfig {
    fn default() -> Self {
        Self {
            limits: SocialLimits::default(),
        }
    }
}

fn validate_limit(value: usize, maximum: usize, reason: &'static str) -> Result<(), SocialError> {
    if value == 0 || value > maximum {
        return Err(SocialError::new(SocialErrorCode::InvalidArgument, reason));
    }
    Ok(())
}
