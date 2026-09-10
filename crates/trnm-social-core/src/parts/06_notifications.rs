#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum NotificationKind {
    FriendRequest,
    FriendAccepted,
    GroupJoinRequested,
    GroupJoinAccepted,
    GroupRoleChanged,
    ChatMention,
    Custom(u16),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CreateNotificationRequest {
    sender: Option<AccountId>,
    recipient: AccountId,
    notification: NotificationId,
    kind: NotificationKind,
    subject: NotificationText,
    content: NotificationText,
}

impl CreateNotificationRequest {
    #[must_use]
    pub fn new(
        sender: Option<AccountId>,
        recipient: AccountId,
        notification: NotificationId,
        kind: NotificationKind,
        subject: NotificationText,
        content: NotificationText,
    ) -> Self {
        Self {
            sender,
            recipient,
            notification,
            kind,
            subject,
            content,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NotificationRecord {
    id: NotificationId,
    sender: Option<AccountId>,
    recipient: AccountId,
    kind: NotificationKind,
    subject: NotificationText,
    content: NotificationText,
    sequence: u64,
    read: bool,
}

impl NotificationRecord {
    #[must_use]
    pub const fn id(&self) -> NotificationId {
        self.id
    }

    #[must_use]
    pub const fn sender(&self) -> Option<AccountId> {
        self.sender
    }

    #[must_use]
    pub const fn recipient(&self) -> AccountId {
        self.recipient
    }

    #[must_use]
    pub const fn kind(&self) -> NotificationKind {
        self.kind
    }

    #[must_use]
    pub fn subject(&self) -> &str {
        self.subject.as_str()
    }

    #[must_use]
    pub fn content(&self) -> &str {
        self.content.as_str()
    }

    #[must_use]
    pub const fn sequence(&self) -> u64 {
        self.sequence
    }

    #[must_use]
    pub const fn is_read(&self) -> bool {
        self.read
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct NotificationCursor {
    recipient: AccountId,
    sequence: u64,
}

impl NotificationCursor {
    #[must_use]
    pub const fn new(recipient: AccountId, sequence: u64) -> Self {
        Self {
            recipient,
            sequence,
        }
    }

    #[must_use]
    pub const fn recipient(&self) -> AccountId {
        self.recipient
    }

    #[must_use]
    pub const fn sequence(&self) -> u64 {
        self.sequence
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NotificationPage {
    items: Vec<NotificationRecord>,
    next: Option<NotificationCursor>,
}

impl NotificationPage {
    #[must_use]
    pub fn items(&self) -> &[NotificationRecord] {
        &self.items
    }

    #[must_use]
    pub const fn next(&self) -> Option<NotificationCursor> {
        self.next
    }
}
