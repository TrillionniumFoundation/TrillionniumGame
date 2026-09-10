fn validate_page_limit(limit: usize) -> Result<(), SocialError> {
    if limit == 0 || limit > MAX_PAGE_SIZE {
        return Err(SocialError::new(
            SocialErrorCode::InvalidArgument,
            "page_limit_invalid",
        ));
    }
    Ok(())
}

fn next_revision(value: u64, reason: &'static str) -> Result<u64, SocialError> {
    value
        .checked_add(1)
        .ok_or_else(|| SocialError::new(SocialErrorCode::OutOfRange, reason))
}

fn require_group_role(
    group: &GroupRecord,
    actor: AccountId,
    minimum: GroupRole,
) -> Result<GroupRole, SocialError> {
    let role = group.members.get(&actor).copied().ok_or_else(|| {
        SocialError::new(SocialErrorCode::PermissionDenied, "group_actor_not_member")
    })?;
    if role.rank() < minimum.rank() {
        return Err(SocialError::new(
            SocialErrorCode::PermissionDenied,
            "group_role_insufficient",
        ));
    }
    Ok(role)
}

fn superadmin_count(group: &GroupRecord) -> usize {
    group
        .members
        .values()
        .filter(|role| **role == GroupRole::Superadmin)
        .count()
}

fn invariant_error() -> SocialError {
    SocialError::new(SocialErrorCode::DataLoss, "social_invariant_violation")
}
