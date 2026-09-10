#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RelationshipView {
    None,
    OutgoingRequest,
    IncomingRequest,
    Friends,
    BlockedBySelf,
    BlockedByOther,
    MutuallyBlocked,
}

#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
struct UserPair {
    low: AccountId,
    high: AccountId,
}

impl UserPair {
    fn new(first: AccountId, second: AccountId) -> Result<Self, SocialError> {
        if first == second {
            return Err(SocialError::new(
                SocialErrorCode::InvalidArgument,
                "self_relationship_forbidden",
            ));
        }
        let (low, high) = if first < second {
            (first, second)
        } else {
            (second, first)
        };
        Ok(Self { low, high })
    }

    fn other(self, account: AccountId) -> Option<AccountId> {
        if account == self.low {
            Some(self.high)
        } else if account == self.high {
            Some(self.low)
        } else {
            None
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct FriendCursor {
    owner: AccountId,
    after: AccountId,
}

impl FriendCursor {
    pub fn new(owner: AccountId, after: AccountId) -> Result<Self, SocialError> {
        UserPair::new(owner, after)?;
        Ok(Self { owner, after })
    }

    #[must_use]
    pub const fn owner(&self) -> AccountId {
        self.owner
    }

    #[must_use]
    pub const fn after(&self) -> AccountId {
        self.after
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct FriendPage {
    items: Vec<AccountId>,
    next: Option<FriendCursor>,
}

impl FriendPage {
    #[must_use]
    pub fn items(&self) -> &[AccountId] {
        &self.items
    }

    #[must_use]
    pub const fn next(&self) -> Option<FriendCursor> {
        self.next
    }
}
