#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum GroupJoinMode {
    Open,
    AdminApproval,
    Closed,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum GroupRole {
    Superadmin,
    Admin,
    Member,
}

impl GroupRole {
    const fn rank(self) -> u8 {
        match self {
            Self::Member => 1,
            Self::Admin => 2,
            Self::Superadmin => 3,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CreateGroupRequest {
    group: GroupId,
    creator: AccountId,
    name: GroupName,
    join_mode: GroupJoinMode,
    max_members: usize,
}

impl CreateGroupRequest {
    #[must_use]
    pub fn new(
        group: GroupId,
        creator: AccountId,
        name: GroupName,
        join_mode: GroupJoinMode,
        max_members: usize,
    ) -> Self {
        Self {
            group,
            creator,
            name,
            join_mode,
            max_members,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct GroupRecord {
    id: GroupId,
    name: GroupName,
    join_mode: GroupJoinMode,
    max_members: usize,
    revision: u64,
    members: BTreeMap<AccountId, GroupRole>,
    join_requests: BTreeSet<AccountId>,
    banned: BTreeSet<AccountId>,
    next_message_sequence: u64,
}

impl GroupRecord {
    #[must_use]
    pub const fn id(&self) -> GroupId {
        self.id
    }

    #[must_use]
    pub fn name(&self) -> &GroupName {
        &self.name
    }

    #[must_use]
    pub const fn join_mode(&self) -> GroupJoinMode {
        self.join_mode
    }

    #[must_use]
    pub const fn max_members(&self) -> usize {
        self.max_members
    }

    #[must_use]
    pub const fn revision(&self) -> u64 {
        self.revision
    }

    #[must_use]
    pub fn role(&self, account: AccountId) -> Option<GroupRole> {
        self.members.get(&account).copied()
    }

    #[must_use]
    pub fn member_count(&self) -> usize {
        self.members.len()
    }

    #[must_use]
    pub fn has_join_request(&self, account: AccountId) -> bool {
        self.join_requests.contains(&account)
    }

    #[must_use]
    pub fn is_banned(&self, account: AccountId) -> bool {
        self.banned.contains(&account)
    }

    pub fn members(&self) -> impl ExactSizeIterator<Item = (AccountId, GroupRole)> + '_ {
        self.members.iter().map(|(account, role)| (*account, *role))
    }
}
