#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SocialRegistry {
    config: SocialConfig,
    revision: u64,
    users: BTreeSet<AccountId>,
    friendships: BTreeSet<UserPair>,
    friend_requests: BTreeSet<(AccountId, AccountId)>,
    blocks: BTreeSet<(AccountId, AccountId)>,
    groups: BTreeMap<GroupId, GroupRecord>,
    messages: BTreeMap<GroupId, Vec<ChatMessage>>,
    notifications: BTreeMap<AccountId, BTreeMap<NotificationId, NotificationRecord>>,
    receipts: BTreeMap<SocialCommandId, CommandReceipt>,
    outbox: BTreeMap<OutboxIntentId, OutboxIntent>,
}
