// Copyright 2021 The Nakama Authors
// Copyright 2026 Trillionnium Foundation contributors
// SPDX-License-Identifier: Apache-2.0
//
// Source-derived state transitions: heroiclabs/nakama server/session_cache.go,
// commit d4d92f93f78bbbe62c7fc50a3f85c772ec121a09,
// Git blob 2f3d4153bfadd6ad398d24173fe4413c12bf9b04.

#![forbid(unsafe_code)]
#![deny(missing_debug_implementations)]

//! Owned, bounded Nakama legacy blacklist state, without authentication.
//!
//! This is separate from refresh families. IDs are raw cache keys, including
//! nil UUID bytes, and do not establish a principal. Add and Unban are literal
//! no-ops. Session and refresh revocations have separate maps. Signed timestamp
//! arithmetic deliberately wraps, including expiry-plus-one and expiry-minus-
//! TTL. The caller supplies observed UTC seconds to invalidation and sweep;
//! clocks, the upstream ticker, mutex ownership and service hooks are outside
//! this module. No session ID, generation or family authority is introduced.
//!
//! Limits are explicit local resource policy, narrower than the source cache.
//! Multi-user mutations validate every limit and prepare token buffers before
//! changing state. There is no automatic eviction of live revocations. Standard
//! BTreeMap/BTreeSet node allocations are bounded but infallible; this is not a
//! global allocator-failure recovery guarantee.

use core::fmt;
use std::collections::{BTreeMap, BTreeSet};

const MAX_USERS: usize = 10_000;
const MAX_TOKENS_PER_USER: usize = 4_096;
const MAX_TOTAL_TOKENS: usize = 65_536;
const MAX_TOKEN_ID_BYTES: usize = 4_096;
const MAX_TOTAL_ID_BYTES: usize = 16 * 1024 * 1024;
const MAX_MUTATION_ITEMS: usize = 10_000;

/// Exact native UUID bytes; nil is a valid cache key, not a principal.
#[derive(Clone, Copy, Eq, Ord, PartialEq, PartialOrd)]
pub struct NakamaLegacyCacheUserId([u8; 16]);

impl NakamaLegacyCacheUserId {
    #[must_use]
    pub const fn from_bytes(bytes: [u8; 16]) -> Self {
        Self(bytes)
    }

    #[must_use]
    pub const fn as_bytes(&self) -> &[u8; 16] {
        &self.0
    }
}

impl fmt::Debug for NakamaLegacyCacheUserId {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("NakamaLegacyCacheUserId(<redacted-16-byte-key>)")
    }
}

/// Borrowed exact Go-string bytes; operation limits are checked by the cache.
#[derive(Clone, Copy, Eq, PartialEq)]
pub struct NakamaLegacyCacheTokenId<'a>(&'a [u8]);

impl<'a> NakamaLegacyCacheTokenId<'a> {
    #[must_use]
    pub const fn from_bytes(bytes: &'a [u8]) -> Self {
        Self(bytes)
    }

    #[must_use]
    pub const fn as_bytes(self) -> &'a [u8] {
        self.0
    }
}

impl fmt::Debug for NakamaLegacyCacheTokenId<'_> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("NakamaLegacyCacheTokenId")
            .field("bytes", &self.0.len())
            .field("value", &"<redacted>")
            .finish()
    }
}

/// Caller-chosen finite budgets, with local ceilings unrelated to Go validation.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct NakamaLegacyBlacklistPolicy {
    pub max_users: usize,
    /// Combined distinct session and refresh keys for each user.
    pub max_tokens_per_user: usize,
    pub max_total_tokens: usize,
    pub max_token_id_bytes: usize,
    /// Distinct owned key bytes across both maps, not the caller's input memory.
    pub max_total_token_id_bytes: usize,
    /// Supplied Ban/Remove/RemoveAll items, including repeated user IDs.
    pub max_mutation_items: usize,
    /// Token copies prepared for one mutation, including repeated updates.
    pub max_prepared_token_id_bytes: usize,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct NakamaLegacyBlacklistLimits(NakamaLegacyBlacklistPolicy);

impl NakamaLegacyBlacklistLimits {
    pub fn new(policy: NakamaLegacyBlacklistPolicy) -> Result<Self, NakamaLegacyBlacklistError> {
        if !(1..=MAX_USERS).contains(&policy.max_users)
            || !(1..=MAX_TOKENS_PER_USER).contains(&policy.max_tokens_per_user)
            || !(1..=MAX_TOTAL_TOKENS).contains(&policy.max_total_tokens)
            || !(1..=MAX_TOKEN_ID_BYTES).contains(&policy.max_token_id_bytes)
            || !(1..=MAX_TOTAL_ID_BYTES).contains(&policy.max_total_token_id_bytes)
            || !(1..=MAX_MUTATION_ITEMS).contains(&policy.max_mutation_items)
            || !(1..=MAX_TOTAL_ID_BYTES).contains(&policy.max_prepared_token_id_bytes)
        {
            return Err(NakamaLegacyBlacklistError::InvalidLimits);
        }
        Ok(Self(policy))
    }

    #[must_use]
    pub const fn policy(self) -> NakamaLegacyBlacklistPolicy {
        self.0
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum NakamaLegacyBlacklistError {
    InvalidLimits,
    UserLimit,
    TokensPerUserLimit,
    TotalTokensLimit,
    TokenIdBytesLimit,
    TotalTokenIdBytesLimit,
    MutationItemsLimit,
    PreparedTokenIdBytesLimit,
    AllocationFailed,
}

impl fmt::Display for NakamaLegacyBlacklistError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::InvalidLimits => "invalid legacy blacklist resource limits",
            Self::UserLimit => "legacy blacklist user limit",
            Self::TokensPerUserLimit => "legacy blacklist per-user token limit",
            Self::TotalTokensLimit => "legacy blacklist total token limit",
            Self::TokenIdBytesLimit => "legacy blacklist token ID byte limit",
            Self::TotalTokenIdBytesLimit => "legacy blacklist total token ID byte limit",
            Self::MutationItemsLimit => "legacy blacklist mutation item limit",
            Self::PreparedTokenIdBytesLimit => "legacy blacklist prepared token ID byte limit",
            Self::AllocationFailed => "legacy blacklist token allocation failed",
        })
    }
}

impl std::error::Error for NakamaLegacyBlacklistError {}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct NakamaLegacyTokenRemoval<'a> {
    pub user: NakamaLegacyCacheUserId,
    pub session_exp: i64,
    pub session_token_id: NakamaLegacyCacheTokenId<'a>,
    pub refresh_exp: i64,
    pub refresh_token_id: NakamaLegacyCacheTokenId<'a>,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct NakamaLegacyBlacklistStats {
    pub users: usize,
    pub tokens: usize,
    pub token_id_bytes: usize,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct NakamaLegacyBlacklistSweep {
    pub users_removed: usize,
    pub session_tokens_removed: usize,
    pub refresh_tokens_removed: usize,
    pub token_id_bytes_removed: usize,
}

#[derive(Clone, Default, Eq, PartialEq)]
struct User {
    last_invalidation: i64,
    sessions: BTreeMap<Vec<u8>, i64>,
    refreshes: BTreeMap<Vec<u8>, i64>,
}

impl User {
    fn map(&self, kind: Kind) -> &BTreeMap<Vec<u8>, i64> {
        match kind {
            Kind::Session => &self.sessions,
            Kind::Refresh => &self.refreshes,
        }
    }

    fn map_mut(&mut self, kind: Kind) -> &mut BTreeMap<Vec<u8>, i64> {
        match kind {
            Kind::Session => &mut self.sessions,
            Kind::Refresh => &mut self.refreshes,
        }
    }

    fn counts(&self) -> (usize, usize) {
        (
            self.sessions.len() + self.refreshes.len(),
            self.sessions
                .keys()
                .chain(self.refreshes.keys())
                .map(Vec::len)
                .sum(),
        )
    }
}

#[derive(Clone, Copy, Eq, Ord, PartialEq, PartialOrd)]
enum Kind {
    Session,
    Refresh,
}

struct PreparedToken {
    user: NakamaLegacyCacheUserId,
    kind: Kind,
    id: Vec<u8>,
    stored_exp: i64,
}

/// Single owned state. The service supplies its shared locking boundary.
pub struct NakamaLegacyBlacklist {
    session_ttl: i64,
    refresh_ttl: i64,
    limits: NakamaLegacyBlacklistLimits,
    users: BTreeMap<NakamaLegacyCacheUserId, User>,
    total_tokens: usize,
    total_id_bytes: usize,
}

impl fmt::Debug for NakamaLegacyBlacklist {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("NakamaLegacyBlacklist")
            .field("session_ttl", &self.session_ttl)
            .field("refresh_ttl", &self.refresh_ttl)
            .field("limits", &self.limits)
            .field("stats", &self.stats())
            .field("records", &"<redacted>")
            .finish()
    }
}

impl NakamaLegacyBlacklist {
    #[must_use]
    pub fn new(session_ttl: i64, refresh_ttl: i64, limits: NakamaLegacyBlacklistLimits) -> Self {
        Self {
            session_ttl,
            refresh_ttl,
            limits,
            users: BTreeMap::new(),
            total_tokens: 0,
            total_id_bytes: 0,
        }
    }

    #[must_use]
    pub fn stats(&self) -> NakamaLegacyBlacklistStats {
        NakamaLegacyBlacklistStats {
            users: self.users.len(),
            tokens: self.total_tokens,
            token_id_bytes: self.total_id_bytes,
        }
    }

    pub fn is_valid_session(
        &self,
        user: NakamaLegacyCacheUserId,
        exp: i64,
        token: NakamaLegacyCacheTokenId<'_>,
    ) -> Result<bool, NakamaLegacyBlacklistError> {
        self.is_valid(user, exp, token, Kind::Session, self.session_ttl)
    }

    pub fn is_valid_refresh(
        &self,
        user: NakamaLegacyCacheUserId,
        exp: i64,
        token: NakamaLegacyCacheTokenId<'_>,
    ) -> Result<bool, NakamaLegacyBlacklistError> {
        self.is_valid(user, exp, token, Kind::Refresh, self.refresh_ttl)
    }

    fn is_valid(
        &self,
        user: NakamaLegacyCacheUserId,
        exp: i64,
        token: NakamaLegacyCacheTokenId<'_>,
        kind: Kind,
        ttl: i64,
    ) -> Result<bool, NakamaLegacyBlacklistError> {
        self.token_bytes(token.as_bytes())?;
        let Some(record) = self.users.get(&user) else {
            return Ok(true);
        };
        if record.last_invalidation > 0 && exp.wrapping_sub(ttl) < record.last_invalidation {
            return Ok(false);
        }
        Ok(!record.map(kind).contains_key(token.as_bytes()))
    }

    /// Literal source no-op: no insertion, validation, clock or allocation.
    pub fn add(
        &mut self,
        _user: NakamaLegacyCacheUserId,
        _session_exp: i64,
        _session_token: NakamaLegacyCacheTokenId<'_>,
        _refresh_exp: i64,
        _refresh_token: NakamaLegacyCacheTokenId<'_>,
    ) {
    }

    pub fn remove(
        &mut self,
        removal: NakamaLegacyTokenRemoval<'_>,
    ) -> Result<(), NakamaLegacyBlacklistError> {
        self.remove_many(&[removal])
    }

    /// Bounded extension: all source Remove occurrences apply in input order.
    pub fn remove_many(
        &mut self,
        removals: &[NakamaLegacyTokenRemoval<'_>],
    ) -> Result<(), NakamaLegacyBlacklistError> {
        self.mutation_items(removals.len())?;
        let mut new_users = BTreeSet::new();
        let mut new_tokens = BTreeSet::new();
        let mut new_per_user = BTreeMap::<NakamaLegacyCacheUserId, usize>::new();
        let mut added_bytes = 0_usize;
        let mut prepared_bytes = 0_usize;
        for removal in removals {
            let record = self.users.get(&removal.user);
            if record.is_none() {
                new_users.insert(removal.user);
            }
            for (kind, token) in [
                (Kind::Session, removal.session_token_id.as_bytes()),
                (Kind::Refresh, removal.refresh_token_id.as_bytes()),
            ] {
                self.token_bytes(token)?;
                if token.is_empty() {
                    continue;
                }
                prepared_bytes = prepared_bytes
                    .checked_add(token.len())
                    .filter(|size| *size <= self.limits.0.max_prepared_token_id_bytes)
                    .ok_or(NakamaLegacyBlacklistError::PreparedTokenIdBytesLimit)?;
                let exists = record.is_some_and(|record| record.map(kind).contains_key(token));
                if !exists && new_tokens.insert((removal.user, kind, token)) {
                    added_bytes += token.len();
                    *new_per_user.entry(removal.user).or_default() += 1;
                }
            }
        }
        self.user_capacity(new_users.len())?;
        for (user, added) in new_per_user {
            let current = self.users.get(&user).map_or(0, |record| record.counts().0);
            if current + added > self.limits.0.max_tokens_per_user {
                return Err(NakamaLegacyBlacklistError::TokensPerUserLimit);
            }
        }
        if self.total_tokens + new_tokens.len() > self.limits.0.max_total_tokens {
            return Err(NakamaLegacyBlacklistError::TotalTokensLimit);
        }
        if self.total_id_bytes + added_bytes > self.limits.0.max_total_token_id_bytes {
            return Err(NakamaLegacyBlacklistError::TotalTokenIdBytesLimit);
        }
        let mut prepared = Vec::new();
        prepared
            .try_reserve_exact(removals.len() * 2)
            .map_err(|_| NakamaLegacyBlacklistError::AllocationFailed)?;
        for removal in removals {
            for (kind, token, exp) in [
                (
                    Kind::Session,
                    removal.session_token_id.as_bytes(),
                    removal.session_exp,
                ),
                (
                    Kind::Refresh,
                    removal.refresh_token_id.as_bytes(),
                    removal.refresh_exp,
                ),
            ] {
                if token.is_empty() {
                    continue;
                }
                let mut id = Vec::new();
                id.try_reserve_exact(token.len())
                    .map_err(|_| NakamaLegacyBlacklistError::AllocationFailed)?;
                id.extend_from_slice(token);
                prepared.push(PreparedToken {
                    user: removal.user,
                    kind,
                    id,
                    stored_exp: exp.wrapping_add(1),
                });
            }
        }
        // No fallible policy/preparation steps remain before the first write.
        for removal in removals {
            self.users.entry(removal.user).or_default();
        }
        for token in prepared {
            let size = token.id.len();
            let map = self
                .users
                .entry(token.user)
                .or_default()
                .map_mut(token.kind);
            if map.insert(token.id, token.stored_exp).is_none() {
                self.total_tokens += 1;
                self.total_id_bytes += size;
            }
        }
        Ok(())
    }

    pub fn remove_all(
        &mut self,
        user: NakamaLegacyCacheUserId,
        now_seconds: i64,
    ) -> Result<(), NakamaLegacyBlacklistError> {
        self.remove_all_many(&[user], now_seconds)
    }

    pub fn remove_all_many(
        &mut self,
        users: &[NakamaLegacyCacheUserId],
        now_seconds: i64,
    ) -> Result<(), NakamaLegacyBlacklistError> {
        self.preflight_users(users)?;
        for user in users {
            let record = self.users.entry(*user).or_default();
            if now_seconds > record.last_invalidation {
                record.last_invalidation = now_seconds;
                let (tokens, bytes) = record.counts();
                record.sessions.clear();
                record.refreshes.clear();
                self.total_tokens -= tokens;
                self.total_id_bytes -= bytes;
            }
        }
        Ok(())
    }

    pub fn ban(
        &mut self,
        users: &[NakamaLegacyCacheUserId],
        now_seconds: i64,
    ) -> Result<(), NakamaLegacyBlacklistError> {
        self.preflight_users(users)?;
        for user in users {
            let record = self.users.entry(*user).or_default();
            if now_seconds > record.last_invalidation {
                record.last_invalidation = now_seconds;
            }
        }
        Ok(())
    }

    /// Literal source no-op, including duplicate or unknown users.
    pub fn unban(&mut self, _users: &[NakamaLegacyCacheUserId]) {}

    #[must_use]
    pub fn sweep(&mut self, now_seconds: i64) -> NakamaLegacyBlacklistSweep {
        let mut swept = NakamaLegacyBlacklistSweep::default();
        self.users.retain(|_, record| {
            record.sessions.retain(|id, exp| {
                if *exp <= now_seconds {
                    swept.session_tokens_removed += 1;
                    swept.token_id_bytes_removed += id.len();
                    false
                } else {
                    true
                }
            });
            record.refreshes.retain(|id, exp| {
                if *exp <= now_seconds {
                    swept.refresh_tokens_removed += 1;
                    swept.token_id_bytes_removed += id.len();
                    false
                } else {
                    true
                }
            });
            let remove = record.sessions.is_empty()
                && record.refreshes.is_empty()
                && (record.last_invalidation == 0
                    || (record.last_invalidation < now_seconds.wrapping_sub(self.session_ttl)
                        && record.last_invalidation < now_seconds.wrapping_sub(self.refresh_ttl)));
            if remove {
                swept.users_removed += 1;
            }
            !remove
        });
        self.total_tokens -= swept.session_tokens_removed + swept.refresh_tokens_removed;
        self.total_id_bytes -= swept.token_id_bytes_removed;
        swept
    }

    fn mutation_items(&self, items: usize) -> Result<(), NakamaLegacyBlacklistError> {
        if items > self.limits.0.max_mutation_items {
            Err(NakamaLegacyBlacklistError::MutationItemsLimit)
        } else {
            Ok(())
        }
    }

    fn token_bytes(&self, token: &[u8]) -> Result<(), NakamaLegacyBlacklistError> {
        if token.len() > self.limits.0.max_token_id_bytes {
            Err(NakamaLegacyBlacklistError::TokenIdBytesLimit)
        } else {
            Ok(())
        }
    }

    fn user_capacity(&self, added: usize) -> Result<(), NakamaLegacyBlacklistError> {
        if self.users.len() + added > self.limits.0.max_users {
            Err(NakamaLegacyBlacklistError::UserLimit)
        } else {
            Ok(())
        }
    }

    fn preflight_users(
        &self,
        users: &[NakamaLegacyCacheUserId],
    ) -> Result<(), NakamaLegacyBlacklistError> {
        self.mutation_items(users.len())?;
        let added = users
            .iter()
            .filter(|user| !self.users.contains_key(user))
            .collect::<BTreeSet<_>>();
        self.user_capacity(added.len())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn policy() -> NakamaLegacyBlacklistPolicy {
        NakamaLegacyBlacklistPolicy {
            max_users: 4,
            max_tokens_per_user: 8,
            max_total_tokens: 16,
            max_token_id_bytes: 32,
            max_total_token_id_bytes: 256,
            max_mutation_items: 8,
            max_prepared_token_id_bytes: 256,
        }
    }

    fn cache() -> NakamaLegacyBlacklist {
        NakamaLegacyBlacklist::new(10, 20, NakamaLegacyBlacklistLimits::new(policy()).unwrap())
    }

    fn user(byte: u8) -> NakamaLegacyCacheUserId {
        NakamaLegacyCacheUserId::from_bytes([byte; 16])
    }

    fn token(bytes: &[u8]) -> NakamaLegacyCacheTokenId<'_> {
        NakamaLegacyCacheTokenId::from_bytes(bytes)
    }

    fn removal<'a>(who: u8, session: &'a [u8], refresh: &'a [u8]) -> NakamaLegacyTokenRemoval<'a> {
        NakamaLegacyTokenRemoval {
            user: user(who),
            session_exp: 100,
            session_token_id: token(session),
            refresh_exp: 200,
            refresh_token_id: token(refresh),
        }
    }

    fn snapshot(
        cache: &NakamaLegacyBlacklist,
    ) -> (
        BTreeMap<NakamaLegacyCacheUserId, User>,
        NakamaLegacyBlacklistStats,
    ) {
        (cache.users.clone(), cache.stats())
    }

    fn unchanged(
        cache: &NakamaLegacyBlacklist,
        old: &(
            BTreeMap<NakamaLegacyCacheUserId, User>,
            NakamaLegacyBlacklistStats,
        ),
    ) {
        assert!(cache.users == old.0);
        assert_eq!(cache.stats(), old.1);
    }

    #[test]
    fn add_and_unban_are_literal_noops_even_for_over_budget_inputs() {
        let mut state = cache();
        state.add(
            user(0),
            i64::MIN,
            token(&[b'x'; 33]),
            i64::MAX,
            token(b"refresh"),
        );
        state.unban(&[user(1); 9]);
        assert_eq!(state.stats(), NakamaLegacyBlacklistStats::default());
        assert!(state
            .is_valid_session(user(0), i64::MIN, token(b"unknown"))
            .unwrap());
        assert!(state
            .is_valid_refresh(user(0), 0, token(b"unknown"))
            .unwrap());
    }

    #[test]
    fn nil_uuid_and_empty_remove_create_source_empty_user_record() {
        let mut state = cache();
        state.remove(removal(0, b"", b"")).unwrap();
        assert_eq!(user(0).as_bytes(), &[0; 16]);
        assert_eq!(state.stats().users, 1);
        assert_eq!(state.stats().tokens, 0);
        assert!(state.is_valid_session(user(0), 0, token(b"")).unwrap());
        assert_eq!(state.sweep(0).users_removed, 1);
    }

    #[test]
    fn same_raw_token_bytes_have_independent_session_and_refresh_revocations() {
        let mut state = cache();
        state.remove(removal(1, b"same", b"same")).unwrap();
        assert_eq!(state.stats().tokens, 2);
        assert_eq!(state.stats().token_id_bytes, 8);
        assert!(!state
            .is_valid_session(user(1), 999, token(b"same"))
            .unwrap());
        assert!(!state
            .is_valid_refresh(user(1), 999, token(b"same"))
            .unwrap());
        assert_eq!(state.sweep(101).session_tokens_removed, 1);
        assert!(state
            .is_valid_session(user(1), 999, token(b"same"))
            .unwrap());
        assert!(!state
            .is_valid_refresh(user(1), 999, token(b"same"))
            .unwrap());
        assert_eq!(state.sweep(201).refresh_tokens_removed, 1);
        assert_eq!(state.stats(), NakamaLegacyBlacklistStats::default());
    }

    #[test]
    fn remove_overwrites_expiry_in_occurrence_order_without_new_quota() {
        let mut p = policy();
        p.max_tokens_per_user = 1;
        p.max_total_tokens = 1;
        let mut state =
            NakamaLegacyBlacklist::new(10, 20, NakamaLegacyBlacklistLimits::new(p).unwrap());
        let mut late = removal(1, b"s", b"");
        late.session_exp = 300;
        state.remove_many(&[late, removal(1, b"s", b"")]).unwrap();
        assert_eq!(state.stats().tokens, 1);
        assert_eq!(state.sweep(100).session_tokens_removed, 0);
        assert_eq!(state.sweep(101).session_tokens_removed, 1);
    }

    #[test]
    fn remove_all_only_greater_second_resets_maps_and_same_second_token_survives() {
        let mut state = cache();
        state.remove(removal(1, b"old", b"old-r")).unwrap();
        state.remove_all(user(1), 100).unwrap();
        assert_eq!(state.stats().tokens, 0);
        state.remove(removal(1, b"same-second", b"r")).unwrap();
        let before = snapshot(&state);
        state.remove_all(user(1), 100).unwrap();
        state.remove_all(user(1), 99).unwrap();
        unchanged(&state, &before);
        assert!(state
            .is_valid_session(user(1), 110, token(b"unrecorded"))
            .unwrap());
        assert!(!state
            .is_valid_session(user(1), 109, token(b"unrecorded"))
            .unwrap());
        state.remove_all(user(1), 101).unwrap();
        assert_eq!(state.stats().tokens, 0);
    }

    #[test]
    fn ban_advances_invalidation_without_clearing_maps_and_unban_changes_nothing() {
        let mut state = cache();
        state.remove(removal(1, b"s", b"r")).unwrap();
        state.ban(&[user(1)], 90).unwrap();
        assert_eq!(state.stats().tokens, 2);
        let before = snapshot(&state);
        state.ban(&[user(1)], 90).unwrap();
        state.ban(&[user(1)], 89).unwrap();
        state.unban(&[user(1)]);
        unchanged(&state, &before);
        assert!(!state
            .is_valid_session(user(1), 99, token(b"other"))
            .unwrap());
        assert!(state
            .is_valid_session(user(1), 100, token(b"other"))
            .unwrap());
        assert!(!state.is_valid_refresh(user(1), 999, token(b"r")).unwrap());
    }

    #[test]
    fn invalidation_uses_each_token_kind_ttl_and_not_jwt_expiry_validation() {
        let mut state = cache();
        state.ban(&[user(1)], 100).unwrap();
        assert!(state.is_valid_session(user(1), 110, token(b"x")).unwrap());
        assert!(!state.is_valid_refresh(user(1), 110, token(b"x")).unwrap());
        assert!(state.is_valid_refresh(user(1), 120, token(b"x")).unwrap());
        assert!(state
            .is_valid_session(user(2), i64::MIN, token(b"x"))
            .unwrap());
    }

    #[test]
    fn token_membership_persists_until_exp_plus_one_sweep_including_expired_jwt() {
        let mut state = cache();
        state.remove(removal(1, b"s", b"")).unwrap();
        assert!(!state.is_valid_session(user(1), -9, token(b"s")).unwrap());
        assert_eq!(state.sweep(100).session_tokens_removed, 0);
        assert!(!state.is_valid_session(user(1), 100, token(b"s")).unwrap());
        assert_eq!(state.sweep(101).session_tokens_removed, 1);
        assert!(state.is_valid_session(user(1), 100, token(b"s")).unwrap());
    }

    #[test]
    fn sweep_requires_both_strict_invalidation_age_comparisons() {
        let mut state = cache();
        state.ban(&[user(1)], 100).unwrap();
        assert_eq!(state.sweep(110).users_removed, 0);
        assert_eq!(state.sweep(120).users_removed, 0);
        assert_eq!(state.sweep(121).users_removed, 1);
    }

    #[test]
    fn signed_expiry_addition_subtraction_and_sweep_age_wrap_like_source_int64() {
        let mut state =
            NakamaLegacyBlacklist::new(1, 1, NakamaLegacyBlacklistLimits::new(policy()).unwrap());
        let mut request = removal(1, b"s", b"");
        request.session_exp = i64::MAX;
        state.remove(request).unwrap();
        assert_eq!(state.users[&user(1)].sessions[b"s".as_slice()], i64::MIN);
        assert_eq!(state.sweep(0).session_tokens_removed, 1);
        state.ban(&[user(1)], 1).unwrap();
        assert!(state
            .is_valid_session(user(1), i64::MIN, token(b"x"))
            .unwrap());
        assert_eq!(state.sweep(i64::MIN).users_removed, 1);
        let mut state =
            NakamaLegacyBlacklist::new(-1, -1, NakamaLegacyBlacklistLimits::new(policy()).unwrap());
        state.ban(&[user(1)], 1).unwrap();
        assert!(!state
            .is_valid_session(user(1), i64::MAX, token(b"x"))
            .unwrap());
    }

    #[test]
    fn unknown_user_negative_invalidation_still_creates_zero_stamp_record() {
        let mut state = cache();
        state.remove_all(user(1), -1).unwrap();
        state.ban(&[user(2)], 0).unwrap();
        assert_eq!(state.stats().users, 2);
        assert_eq!(state.users[&user(1)].last_invalidation, 0);
        assert_eq!(state.users[&user(2)].last_invalidation, 0);
        assert!(state
            .is_valid_refresh(user(1), i64::MIN, token(b"x"))
            .unwrap());
    }

    #[test]
    fn multi_user_ban_and_remove_all_preflight_capacity_before_any_mutation() {
        let mut p = policy();
        p.max_users = 2;
        let mut state =
            NakamaLegacyBlacklist::new(10, 20, NakamaLegacyBlacklistLimits::new(p).unwrap());
        state.remove(removal(1, b"s", b"r")).unwrap();
        let before = snapshot(&state);
        assert_eq!(
            state.ban(&[user(1), user(2), user(3)], 500),
            Err(NakamaLegacyBlacklistError::UserLimit)
        );
        unchanged(&state, &before);
        assert_eq!(
            state.remove_all_many(&[user(1), user(2), user(3)], 500),
            Err(NakamaLegacyBlacklistError::UserLimit)
        );
        unchanged(&state, &before);
        state.ban(&[user(2), user(2)], 500).unwrap();
        assert_eq!(state.stats().users, 2);
    }

    #[test]
    fn combined_per_user_quota_keeps_two_map_mutation_atomic() {
        let mut p = policy();
        p.max_tokens_per_user = 1;
        let mut state =
            NakamaLegacyBlacklist::new(10, 20, NakamaLegacyBlacklistLimits::new(p).unwrap());
        let before = snapshot(&state);
        assert_eq!(
            state.remove(removal(1, b"s", b"r")),
            Err(NakamaLegacyBlacklistError::TokensPerUserLimit)
        );
        unchanged(&state, &before);
    }

    #[test]
    fn global_token_quota_rejects_later_user_before_overwriting_earlier_expiry() {
        let mut p = policy();
        p.max_total_tokens = 2;
        let mut state =
            NakamaLegacyBlacklist::new(10, 20, NakamaLegacyBlacklistLimits::new(p).unwrap());
        state.remove(removal(1, b"s", b"")).unwrap();
        let mut changed = removal(1, b"s", b"");
        changed.session_exp = 999;
        let before = snapshot(&state);
        assert_eq!(
            state.remove_many(&[changed, removal(2, b"t", b"r")]),
            Err(NakamaLegacyBlacklistError::TotalTokensLimit)
        );
        unchanged(&state, &before);
    }

    #[test]
    fn key_byte_and_aggregate_byte_quotas_preserve_all_earlier_records() {
        let mut p = policy();
        p.max_token_id_bytes = 4;
        p.max_total_token_id_bytes = 6;
        let mut state =
            NakamaLegacyBlacklist::new(10, 20, NakamaLegacyBlacklistLimits::new(p).unwrap());
        state.remove(removal(1, b"abc", b"")).unwrap();
        let before = snapshot(&state);
        assert_eq!(
            state.remove_many(&[removal(2, b"a", b""), removal(3, b"12345", b"")]),
            Err(NakamaLegacyBlacklistError::TokenIdBytesLimit)
        );
        unchanged(&state, &before);
        assert_eq!(
            state.remove_many(&[removal(2, b"ab", b"cd")]),
            Err(NakamaLegacyBlacklistError::TotalTokenIdBytesLimit)
        );
        unchanged(&state, &before);
        assert_eq!(
            state.is_valid_session(user(9), 0, token(b"12345")),
            Err(NakamaLegacyBlacklistError::TokenIdBytesLimit)
        );
    }

    #[test]
    fn prepared_copy_and_item_budgets_fail_before_source_state_changes() {
        let mut p = policy();
        p.max_prepared_token_id_bytes = 2;
        p.max_mutation_items = 2;
        let mut state =
            NakamaLegacyBlacklist::new(10, 20, NakamaLegacyBlacklistLimits::new(p).unwrap());
        let before = snapshot(&state);
        assert_eq!(
            state.remove_many(&[removal(1, b"ab", b""), removal(1, b"ab", b"")]),
            Err(NakamaLegacyBlacklistError::PreparedTokenIdBytesLimit)
        );
        unchanged(&state, &before);
        assert_eq!(
            state.ban(&[user(1); 3], 9),
            Err(NakamaLegacyBlacklistError::MutationItemsLimit)
        );
        unchanged(&state, &before);
        assert_eq!(
            state.remove_many(&[removal(1, b"", b""); 3]),
            Err(NakamaLegacyBlacklistError::MutationItemsLimit)
        );
        unchanged(&state, &before);
    }

    #[test]
    fn quota_failure_never_evicts_live_revocation_and_expired_sweep_releases_capacity() {
        let mut p = policy();
        p.max_users = 1;
        p.max_total_tokens = 1;
        let mut state =
            NakamaLegacyBlacklist::new(10, 20, NakamaLegacyBlacklistLimits::new(p).unwrap());
        state.remove(removal(1, b"s", b"")).unwrap();
        assert_eq!(
            state.remove(removal(2, b"t", b"")),
            Err(NakamaLegacyBlacklistError::UserLimit)
        );
        assert!(!state.is_valid_session(user(1), 100, token(b"s")).unwrap());
        let _ = state.sweep(101);
        state.remove(removal(2, b"t", b"")).unwrap();
        assert!(!state.is_valid_session(user(2), 100, token(b"t")).unwrap());
    }

    #[test]
    fn invalid_utf8_and_nul_token_ids_are_exact_unrepaired_byte_keys() {
        let mut state = cache();
        state.remove(removal(1, b"\xff\0", b"")).unwrap();
        assert!(!state
            .is_valid_session(user(1), 0, token(b"\xff\0"))
            .unwrap());
        assert!(state
            .is_valid_session(user(1), 0, token("\u{fffd}\0".as_bytes()))
            .unwrap());
        assert_eq!(state.stats().token_id_bytes, 2);
    }

    #[test]
    fn debug_and_error_text_redact_user_and_token_key_bytes() {
        let mut state = cache();
        let request = removal(0xab, b"private-token-secret", b"private-refresh-secret");
        state.remove(request).unwrap();
        for text in [
            format!("{state:?}"),
            format!("{request:?}"),
            format!("{:?}", user(0xab)),
            format!("{}", NakamaLegacyBlacklistError::TokenIdBytesLimit),
        ] {
            assert!(!text.contains("private-token-secret"));
            assert!(!text.contains("private-refresh-secret"));
            assert!(!text.contains("171"));
        }
    }

    #[test]
    fn every_policy_dimension_has_a_finite_typed_ceiling() {
        let mut excessive = [policy(); 7];
        excessive[0].max_users = usize::MAX;
        excessive[1].max_tokens_per_user = usize::MAX;
        excessive[2].max_total_tokens = usize::MAX;
        excessive[3].max_token_id_bytes = usize::MAX;
        excessive[4].max_total_token_id_bytes = usize::MAX;
        excessive[5].max_mutation_items = usize::MAX;
        excessive[6].max_prepared_token_id_bytes = usize::MAX;
        for limits in excessive {
            assert_eq!(
                NakamaLegacyBlacklistLimits::new(limits),
                Err(NakamaLegacyBlacklistError::InvalidLimits)
            );
        }
        let mut limits = policy();
        limits.max_token_id_bytes = 0;
        assert_eq!(
            NakamaLegacyBlacklistLimits::new(limits),
            Err(NakamaLegacyBlacklistError::InvalidLimits)
        );
        let mut limits = policy();
        limits.max_prepared_token_id_bytes = MAX_TOTAL_ID_BYTES + 1;
        assert_eq!(
            NakamaLegacyBlacklistLimits::new(limits),
            Err(NakamaLegacyBlacklistError::InvalidLimits)
        );
        let state = cache();
        assert_eq!(state.limits.policy(), policy());
    }
}
