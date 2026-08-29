# ADR-0004: Target profile has no automatic legacy fallback

Status: proposed for exact-head review

A match selected for `world_transition_v1` remains on that authority profile for its runtime generation. Configuration failure, World outage, invalid result, stale fence, storage conflict or process restart may preserve/retry work or terminate the generation; none may route the same command through the legacy direct path.

Rollback stops new target admission and drains or quarantines existing work. It does not rewrite canonical history and does not promote World-local authority.

Trillionnium Chain is excluded.
