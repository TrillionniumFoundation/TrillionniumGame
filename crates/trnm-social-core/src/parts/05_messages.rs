#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SendGroupMessageRequest {
    group: GroupId,
    sender: AccountId,
    message: MessageId,
    body: MessageBody,
}

impl SendGroupMessageRequest {
    #[must_use]
    pub fn new(
        group: GroupId,
        sender: AccountId,
        message: MessageId,
        body: MessageBody,
    ) -> Self {
        Self {
            group,
            sender,
            message,
            body,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ChatMessage {
    id: MessageId,
    group: GroupId,
    sender: AccountId,
    sequence: u64,
    body: MessageBody,
}

impl ChatMessage {
    #[must_use]
    pub const fn id(&self) -> MessageId {
        self.id
    }

    #[must_use]
    pub const fn group(&self) -> GroupId {
        self.group
    }

    #[must_use]
    pub const fn sender(&self) -> AccountId {
        self.sender
    }

    #[must_use]
    pub const fn sequence(&self) -> u64 {
        self.sequence
    }

    #[must_use]
    pub fn body(&self) -> &[u8] {
        self.body.as_bytes()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct MessageCursor {
    group: GroupId,
    sequence: u64,
}

impl MessageCursor {
    #[must_use]
    pub const fn new(group: GroupId, sequence: u64) -> Self {
        Self { group, sequence }
    }

    #[must_use]
    pub const fn group(&self) -> GroupId {
        self.group
    }

    #[must_use]
    pub const fn sequence(&self) -> u64 {
        self.sequence
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MessagePage {
    items: Vec<ChatMessage>,
    next: Option<MessageCursor>,
}

impl MessagePage {
    #[must_use]
    pub fn items(&self) -> &[ChatMessage] {
        &self.items
    }

    #[must_use]
    pub const fn next(&self) -> Option<MessageCursor> {
        self.next
    }
}
