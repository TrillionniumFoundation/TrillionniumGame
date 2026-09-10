#[derive(Clone, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct GroupName(String);

impl GroupName {
    pub fn new(value: impl Into<String>) -> Result<Self, SocialError> {
        let value = value.into();
        if value.is_empty()
            || value.len() > MAX_GROUP_NAME_BYTES
            || value.trim() != value
            || value.chars().any(char::is_control)
        {
            return Err(SocialError::new(
                SocialErrorCode::InvalidArgument,
                "group_name_invalid",
            ));
        }
        Ok(Self(value))
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for GroupName {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("GroupName")
            .field("byte_len", &self.0.len())
            .finish()
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct MessageBody(Vec<u8>);

impl MessageBody {
    pub fn new(value: impl Into<Vec<u8>>) -> Result<Self, SocialError> {
        let value = value.into();
        if value.is_empty() || value.len() > MAX_MESSAGE_BYTES {
            return Err(SocialError::new(
                SocialErrorCode::InvalidArgument,
                "message_body_invalid",
            ));
        }
        Ok(Self(value))
    }

    #[must_use]
    pub fn as_bytes(&self) -> &[u8] {
        &self.0
    }

    #[must_use]
    pub fn len(&self) -> usize {
        self.0.len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }
}

impl fmt::Debug for MessageBody {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("MessageBody")
            .field("byte_len", &self.0.len())
            .finish()
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct NotificationText(String);

impl NotificationText {
    pub fn new(value: impl Into<String>) -> Result<Self, SocialError> {
        let value = value.into();
        if value.is_empty()
            || value.len() > MAX_NOTIFICATION_TEXT_BYTES
            || value.chars().any(char::is_control)
        {
            return Err(SocialError::new(
                SocialErrorCode::InvalidArgument,
                "notification_text_invalid",
            ));
        }
        Ok(Self(value))
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }

    #[must_use]
    pub fn len(&self) -> usize {
        self.0.len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }
}

impl fmt::Debug for NotificationText {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("NotificationText")
            .field("byte_len", &self.0.len())
            .finish()
    }
}
