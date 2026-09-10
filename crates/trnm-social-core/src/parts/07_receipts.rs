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

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum OutboxIntentPayload {
    FriendRequest {
        requester: AccountId,
        target: AccountId,
    },
    FriendAccepted {
        accepter: AccountId,
        requester: AccountId,
    },
    GroupJoinRequested {
        group: GroupId,
        requester: AccountId,
    },
    GroupJoinAccepted {
        group: GroupId,
        actor: AccountId,
        member: AccountId,
    },
    GroupRoleChanged {
        group: GroupId,
        actor: AccountId,
        member: AccountId,
        role: GroupRole,
    },
    ChatMessage {
        group: GroupId,
        message: MessageId,
        sender: AccountId,
        sequence: u64,
        body: MessageBody,
    },
    NotificationDelivery {
        sender: Option<AccountId>,
        recipient: AccountId,
        notification: NotificationId,
        kind: NotificationKind,
        subject: NotificationText,
        content: NotificationText,
        sequence: u64,
    },
}

impl OutboxIntentPayload {
    #[must_use]
    pub fn kind(&self) -> OutboxIntentKind {
        match self {
            Self::FriendRequest { .. } => OutboxIntentKind::FriendRequest,
            Self::FriendAccepted { .. } => OutboxIntentKind::FriendAccepted,
            Self::GroupJoinRequested { .. } => OutboxIntentKind::GroupJoinRequested,
            Self::GroupJoinAccepted { .. } => OutboxIntentKind::GroupJoinAccepted,
            Self::GroupRoleChanged { .. } => OutboxIntentKind::GroupRoleChanged,
            Self::ChatMessage { .. } => OutboxIntentKind::ChatMessage,
            Self::NotificationDelivery { .. } => OutboxIntentKind::NotificationDelivery,
        }
    }

    #[must_use]
    pub fn recipient(&self) -> Option<AccountId> {
        match self {
            Self::FriendRequest { target, .. } => Some(*target),
            Self::FriendAccepted { requester, .. } => Some(*requester),
            Self::GroupJoinRequested { .. } | Self::ChatMessage { .. } => None,
            Self::GroupJoinAccepted { member, .. }
            | Self::GroupRoleChanged { member, .. } => Some(*member),
            Self::NotificationDelivery { recipient, .. } => Some(*recipient),
        }
    }

    fn matches_receipt(&self, receipt: CommandReceipt) -> bool {
        match self {
            Self::FriendRequest { requester, .. } => {
                receipt.outcome == ReceiptOutcome::FriendRequestSent
                    && receipt.actor == *requester
            }
            Self::FriendAccepted { accepter, .. } => {
                receipt.outcome == ReceiptOutcome::FriendRequestAccepted
                    && receipt.actor == *accepter
            }
            Self::GroupJoinRequested { requester, .. } => {
                receipt.outcome == ReceiptOutcome::GroupJoinRequested
                    && receipt.actor == *requester
            }
            Self::GroupJoinAccepted { actor, member, .. } => {
                matches!(
                    receipt.outcome,
                    ReceiptOutcome::GroupJoined | ReceiptOutcome::GroupJoinApproved
                ) && receipt.actor == *actor
                    && (receipt.outcome != ReceiptOutcome::GroupJoined || actor == member)
            }
            Self::GroupRoleChanged { actor, .. } => {
                receipt.outcome == ReceiptOutcome::GroupRoleChanged && receipt.actor == *actor
            }
            Self::ChatMessage { sender, .. } => {
                receipt.outcome == ReceiptOutcome::ChatMessageSent && receipt.actor == *sender
            }
            Self::NotificationDelivery {
                sender, recipient, ..
            } => {
                receipt.outcome == ReceiptOutcome::NotificationCreated
                    && receipt.actor == (*sender).unwrap_or(*recipient)
            }
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OutboxIntent {
    id: OutboxIntentId,
    payload: OutboxIntentPayload,
}

impl OutboxIntent {
    #[must_use]
    pub const fn id(&self) -> OutboxIntentId {
        self.id
    }

    #[must_use]
    pub fn recipient(&self) -> Option<AccountId> {
        self.payload.recipient()
    }

    #[must_use]
    pub fn kind(&self) -> OutboxIntentKind {
        self.payload.kind()
    }

    #[must_use]
    pub fn payload(&self) -> &OutboxIntentPayload {
        &self.payload
    }
}

#[derive(Clone, Debug)]
struct CommandCompletion<'a> {
    command: SocialCommandId,
    fingerprint: CommandFingerprint,
    actor: AccountId,
    revision: u64,
    outcome: ReceiptOutcome,
    intents: &'a [OutboxIntentPayload],
}
