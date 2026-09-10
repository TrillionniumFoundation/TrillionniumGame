#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ReceiptOutcome {
    FriendRequestSent,
    FriendRequestAccepted,
    FriendRemoved,
    UserBlocked,
    UserUnblocked,
    GroupCreated,
    GroupJoined,
    GroupJoinRequested,
    GroupJoinApproved,
    GroupRoleChanged,
    GroupLeft,
    GroupMemberBanned,
    GroupMemberUnbanned,
    ChatMessageSent,
    NotificationCreated,
    NotificationMarkedRead,
    NotificationDeleted,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct CommandReceipt {
    command: SocialCommandId,
    fingerprint: CommandFingerprint,
    actor: AccountId,
    revision: u64,
    outcome: ReceiptOutcome,
    outbox_count: u16,
}

impl CommandReceipt {
    #[must_use]
    pub const fn command(&self) -> SocialCommandId {
        self.command
    }

    #[must_use]
    pub const fn actor(&self) -> AccountId {
        self.actor
    }

    #[must_use]
    pub const fn revision(&self) -> u64 {
        self.revision
    }

    #[must_use]
    pub const fn outcome(&self) -> ReceiptOutcome {
        self.outcome
    }

    #[must_use]
    pub const fn outbox_count(&self) -> u16 {
        self.outbox_count
    }
}

#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct OutboxIntentId {
    command: SocialCommandId,
    ordinal: u16,
}

impl OutboxIntentId {
    #[must_use]
    pub const fn command(&self) -> SocialCommandId {
        self.command
    }

    #[must_use]
    pub const fn ordinal(&self) -> u16 {
        self.ordinal
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum OutboxIntentKind {
    FriendRequest,
    FriendAccepted,
    GroupJoinRequested,
    GroupJoinAccepted,
    GroupRoleChanged,
    ChatMessage,
    NotificationDelivery,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct OutboxIntent {
    id: OutboxIntentId,
    recipient: Option<AccountId>,
    kind: OutboxIntentKind,
}

impl OutboxIntent {
    #[must_use]
    pub const fn id(&self) -> OutboxIntentId {
        self.id
    }

    #[must_use]
    pub const fn recipient(&self) -> Option<AccountId> {
        self.recipient
    }

    #[must_use]
    pub const fn kind(&self) -> OutboxIntentKind {
        self.kind
    }
}

#[derive(Clone, Copy, Debug)]
struct CommandCompletion<'a> {
    command: SocialCommandId,
    fingerprint: CommandFingerprint,
    actor: AccountId,
    revision: u64,
    outcome: ReceiptOutcome,
    intents: &'a [(Option<AccountId>, OutboxIntentKind)],
}
