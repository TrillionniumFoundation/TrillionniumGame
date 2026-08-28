#![forbid(unsafe_code)]

use std::collections::{BTreeMap, BTreeSet};

use trnm_contracts::{DomainError, RetryClass, StableCode, UserId};

macro_rules! id16 {
    ($name:ident) => {
        #[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
        pub struct $name([u8; 16]);

        impl $name {
            #[must_use]
            pub const fn new(value: [u8; 16]) -> Self {
                Self(value)
            }

            #[must_use]
            pub fn is_zero(self) -> bool {
                self.0.iter().all(|byte| *byte == 0)
            }
        }
    };
}

id16!(ConnectionId);
id16!(SessionId);
id16!(NodeId);
id16!(StreamId);

#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct PresenceKey {
    pub stream: StreamId,
    pub user: UserId,
    pub session: SessionId,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ConnectionState {
    Connected,
    Draining,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct RouterLimits {
    pub max_connections: usize,
    pub max_connections_per_session: usize,
    pub max_presences: usize,
    pub max_presences_per_stream: usize,
    pub max_status_bytes: usize,
    pub max_outbound_messages: u32,
    pub max_outbound_bytes: u64,
}

impl Default for RouterLimits {
    fn default() -> Self {
        Self {
            max_connections: 100_000,
            max_connections_per_session: 4,
            max_presences: 500_000,
            max_presences_per_stream: 10_000,
            max_status_bytes: 2_048,
            max_outbound_messages: 256,
            max_outbound_bytes: 4 * 1024 * 1024,
        }
    }
}

impl RouterLimits {
    fn validate(self) -> Result<(), DomainError> {
        if self.max_connections == 0
            || self.max_connections_per_session == 0
            || self.max_presences == 0
            || self.max_presences_per_stream == 0
            || self.max_status_bytes == 0
            || self.max_outbound_messages == 0
            || self.max_outbound_bytes == 0
        {
            return Err(invalid("invalid_router_limits"));
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct OpenConnection {
    pub connection: ConnectionId,
    pub session: SessionId,
    pub user: UserId,
    pub node: NodeId,
    pub route_generation: u64,
    pub user_revocation_epoch: u64,
    pub now_ms: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ConnectionRecord {
    pub connection: ConnectionId,
    pub session: SessionId,
    pub user: UserId,
    pub node: NodeId,
    pub route_generation: u64,
    pub user_revocation_epoch: u64,
    pub last_seen_ms: u64,
    pub state: ConnectionState,
    pub queued_messages: u32,
    pub queued_bytes: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PresenceRecord {
    pub key: PresenceKey,
    pub connection: ConnectionId,
    pub username: String,
    pub status: String,
    pub hidden: bool,
    pub persistence: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct RouteTarget {
    pub connection: ConnectionId,
    pub session: SessionId,
    pub user: UserId,
    pub node: NodeId,
    pub route_generation: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct CloseResult {
    pub connection: ConnectionId,
    pub removed_presences: usize,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RouterState {
    limits: RouterLimits,
    connections: BTreeMap<ConnectionId, ConnectionRecord>,
    sessions: BTreeMap<SessionId, BTreeSet<ConnectionId>>,
    users: BTreeMap<UserId, BTreeSet<ConnectionId>>,
    user_revocation_epochs: BTreeMap<UserId, u64>,
    presences: BTreeMap<PresenceKey, PresenceRecord>,
    streams: BTreeMap<StreamId, BTreeSet<PresenceKey>>,
}

impl RouterState {
    pub fn new(limits: RouterLimits) -> Result<Self, DomainError> {
        limits.validate()?;
        Ok(Self {
            limits,
            connections: BTreeMap::new(),
            sessions: BTreeMap::new(),
            users: BTreeMap::new(),
            user_revocation_epochs: BTreeMap::new(),
            presences: BTreeMap::new(),
            streams: BTreeMap::new(),
        })
    }

    #[must_use]
    pub fn connection_count(&self) -> usize {
        self.connections.len()
    }

    #[must_use]
    pub fn presence_count(&self) -> usize {
        self.presences.len()
    }

    #[must_use]
    pub fn connection(&self, id: ConnectionId) -> Option<ConnectionRecord> {
        self.connections.get(&id).copied()
    }

    pub fn open(&mut self, request: OpenConnection) -> Result<ConnectionRecord, DomainError> {
        validate_open(request)?;
        if self.connections.len() >= self.limits.max_connections {
            return Err(exhausted("connection_limit_exceeded"));
        }
        if self.connections.contains_key(&request.connection) {
            return Err(error(
                StableCode::AlreadyExists,
                "connection_id_exists",
                RetryClass::Never,
            ));
        }
        let current_epoch = self
            .user_revocation_epochs
            .get(&request.user)
            .copied()
            .unwrap_or(request.user_revocation_epoch);
        if current_epoch != request.user_revocation_epoch {
            return Err(unauthenticated("user_revocation_epoch_mismatch"));
        }
        let session_count = self.sessions.get(&request.session).map_or(0, BTreeSet::len);
        if session_count >= self.limits.max_connections_per_session {
            return Err(exhausted("session_connection_limit_exceeded"));
        }

        let record = ConnectionRecord {
            connection: request.connection,
            session: request.session,
            user: request.user,
            node: request.node,
            route_generation: request.route_generation,
            user_revocation_epoch: request.user_revocation_epoch,
            last_seen_ms: request.now_ms,
            state: ConnectionState::Connected,
            queued_messages: 0,
            queued_bytes: 0,
        };
        self.user_revocation_epochs
            .entry(request.user)
            .or_insert(request.user_revocation_epoch);
        self.connections.insert(request.connection, record);
        self.sessions
            .entry(request.session)
            .or_default()
            .insert(request.connection);
        self.users
            .entry(request.user)
            .or_default()
            .insert(request.connection);
        Ok(record)
    }

    pub fn route(
        &self,
        connection: ConnectionId,
        node: NodeId,
        generation: u64,
    ) -> Result<RouteTarget, DomainError> {
        let record = self.connections.get(&connection).ok_or_else(connection_not_found)?;
        route_fence(record, node, generation)?;
        Ok(target(*record))
    }

    pub fn rebind(
        &mut self,
        connection: ConnectionId,
        node: NodeId,
        generation: u64,
        new_node: NodeId,
    ) -> Result<RouteTarget, DomainError> {
        if new_node.is_zero() {
            return Err(invalid("invalid_node_id"));
        }
        let current = self.connections.get(&connection).copied().ok_or_else(connection_not_found)?;
        route_fence(&current, node, generation)?;
        let next_generation = current.route_generation.checked_add(1).ok_or_else(overflow)?;
        let record = self.connections.get_mut(&connection).ok_or_else(connection_not_found)?;
        record.node = new_node;
        record.route_generation = next_generation;
        record.state = ConnectionState::Connected;
        Ok(target(*record))
    }

    pub fn touch(
        &mut self,
        connection: ConnectionId,
        node: NodeId,
        generation: u64,
        now_ms: u64,
    ) -> Result<ConnectionRecord, DomainError> {
        let current = self.connections.get(&connection).copied().ok_or_else(connection_not_found)?;
        route_fence(&current, node, generation)?;
        if now_ms < current.last_seen_ms {
            return Err(invalid("heartbeat_time_regression"));
        }
        let record = self.connections.get_mut(&connection).ok_or_else(connection_not_found)?;
        record.last_seen_ms = now_ms;
        Ok(*record)
    }

    pub fn begin_node_drain(&mut self, node: NodeId) -> Result<Vec<RouteTarget>, DomainError> {
        if node.is_zero() {
            return Err(invalid("invalid_node_id"));
        }
        let mut targets = Vec::new();
        for record in self.connections.values_mut() {
            if record.node == node {
                record.state = ConnectionState::Draining;
                targets.push(target(*record));
            }
        }
        Ok(targets)
    }

    pub fn join_presence(
        &mut self,
        connection: ConnectionId,
        node: NodeId,
        generation: u64,
        stream: StreamId,
        username: String,
        status: String,
        hidden: bool,
        persistence: bool,
    ) -> Result<PresenceRecord, DomainError> {
        if stream.is_zero() || username.is_empty() || username.len() > 128 {
            return Err(invalid("invalid_presence_identity"));
        }
        if status.len() > self.limits.max_status_bytes {
            return Err(exhausted("presence_status_limit_exceeded"));
        }
        if self.presences.len() >= self.limits.max_presences {
            return Err(exhausted("presence_limit_exceeded"));
        }
        let connection_record = self
            .connections
            .get(&connection)
            .copied()
            .ok_or_else(connection_not_found)?;
        route_fence(&connection_record, node, generation)?;
        if connection_record.state != ConnectionState::Connected {
            return Err(error(
                StableCode::FailedPrecondition,
                "connection_draining",
                RetryClass::SafeBackoff,
            ));
        }
        let key = PresenceKey {
            stream,
            user: connection_record.user,
            session: connection_record.session,
        };
        if self.presences.contains_key(&key) {
            return Err(error(
                StableCode::AlreadyExists,
                "presence_exists",
                RetryClass::Never,
            ));
        }
        let stream_count = self.streams.get(&stream).map_or(0, BTreeSet::len);
        if stream_count >= self.limits.max_presences_per_stream {
            return Err(exhausted("stream_presence_limit_exceeded"));
        }
        let presence = PresenceRecord {
            key,
            connection,
            username,
            status,
            hidden,
            persistence,
        };
        self.presences.insert(key, presence.clone());
        self.streams.entry(stream).or_default().insert(key);
        Ok(presence)
    }

    pub fn leave_presence(
        &mut self,
        connection: ConnectionId,
        node: NodeId,
        generation: u64,
        stream: StreamId,
    ) -> Result<PresenceRecord, DomainError> {
        let record = self.connections.get(&connection).copied().ok_or_else(connection_not_found)?;
        route_fence(&record, node, generation)?;
        let key = PresenceKey {
            stream,
            user: record.user,
            session: record.session,
        };
        self.remove_presence(key).ok_or_else(|| {
            error(
                StableCode::NotFound,
                "presence_not_found",
                RetryClass::Never,
            )
        })
    }

    pub fn reserve_outbound(
        &mut self,
        connection: ConnectionId,
        node: NodeId,
        generation: u64,
        bytes: u64,
    ) -> Result<ConnectionRecord, DomainError> {
        if bytes == 0 {
            return Err(invalid("outbound_bytes_must_be_positive"));
        }
        let current = self.connections.get(&connection).copied().ok_or_else(connection_not_found)?;
        route_fence(&current, node, generation)?;
        let messages = current.queued_messages.checked_add(1).ok_or_else(overflow)?;
        let queued_bytes = current.queued_bytes.checked_add(bytes).ok_or_else(overflow)?;
        if messages > self.limits.max_outbound_messages
            || queued_bytes > self.limits.max_outbound_bytes
        {
            return Err(exhausted("slow_consumer_budget_exceeded"));
        }
        let record = self.connections.get_mut(&connection).ok_or_else(connection_not_found)?;
        record.queued_messages = messages;
        record.queued_bytes = queued_bytes;
        Ok(*record)
    }

    pub fn release_outbound(
        &mut self,
        connection: ConnectionId,
        node: NodeId,
        generation: u64,
        bytes: u64,
    ) -> Result<ConnectionRecord, DomainError> {
        if bytes == 0 {
            return Err(invalid("outbound_bytes_must_be_positive"));
        }
        let current = self.connections.get(&connection).copied().ok_or_else(connection_not_found)?;
        route_fence(&current, node, generation)?;
        let messages = current.queued_messages.checked_sub(1).ok_or_else(queue_underflow)?;
        let queued_bytes = current.queued_bytes.checked_sub(bytes).ok_or_else(queue_underflow)?;
        let record = self.connections.get_mut(&connection).ok_or_else(connection_not_found)?;
        record.queued_messages = messages;
        record.queued_bytes = queued_bytes;
        Ok(*record)
    }

    pub fn close_connection(
        &mut self,
        connection: ConnectionId,
        node: NodeId,
        generation: u64,
    ) -> Result<CloseResult, DomainError> {
        let record = self.connections.get(&connection).copied().ok_or_else(connection_not_found)?;
        route_fence(&record, node, generation)?;
        self.remove_connection(connection).ok_or_else(connection_not_found)
    }

    pub fn close_session(&mut self, session: SessionId) -> Result<Vec<CloseResult>, DomainError> {
        if session.is_zero() {
            return Err(invalid("invalid_session_id"));
        }
        let connections = self
            .sessions
            .get(&session)
            .cloned()
            .unwrap_or_default();
        Ok(connections
            .into_iter()
            .filter_map(|connection| self.remove_connection(connection))
            .collect())
    }

    pub fn revoke_user(
        &mut self,
        user: UserId,
        expected_epoch: u64,
        new_epoch: u64,
    ) -> Result<Vec<CloseResult>, DomainError> {
        if user.is_zero() || new_epoch <= expected_epoch {
            return Err(invalid("invalid_user_revocation"));
        }
        let current = self.user_revocation_epochs.get(&user).copied().unwrap_or(0);
        if current != expected_epoch {
            return Err(error(
                StableCode::Aborted,
                "user_revocation_epoch_mismatch",
                RetryClass::ResyncRequired,
            ));
        }
        self.user_revocation_epochs.insert(user, new_epoch);
        let connections = self.users.get(&user).cloned().unwrap_or_default();
        Ok(connections
            .into_iter()
            .filter_map(|connection| self.remove_connection(connection))
            .collect())
    }

    pub fn expire_idle(
        &mut self,
        now_ms: u64,
        idle_timeout_ms: u64,
        max_expired: usize,
    ) -> Result<Vec<CloseResult>, DomainError> {
        if idle_timeout_ms == 0 || max_expired == 0 {
            return Err(invalid("invalid_idle_expiry_policy"));
        }
        let expired: Vec<_> = self
            .connections
            .iter()
            .filter_map(|(id, record)| {
                record
                    .last_seen_ms
                    .checked_add(idle_timeout_ms)
                    .filter(|deadline| now_ms >= *deadline)
                    .map(|_| *id)
            })
            .take(max_expired)
            .collect();
        Ok(expired
            .into_iter()
            .filter_map(|connection| self.remove_connection(connection))
            .collect())
    }

    #[must_use]
    pub fn recipients(&self, stream: StreamId) -> Vec<RouteTarget> {
        self.streams
            .get(&stream)
            .into_iter()
            .flat_map(|keys| keys.iter())
            .filter_map(|key| self.presences.get(key))
            .filter_map(|presence| self.connections.get(&presence.connection))
            .map(|record| target(*record))
            .collect()
    }

    fn remove_presence(&mut self, key: PresenceKey) -> Option<PresenceRecord> {
        let presence = self.presences.remove(&key)?;
        if let Some(keys) = self.streams.get_mut(&key.stream) {
            keys.remove(&key);
            if keys.is_empty() {
                self.streams.remove(&key.stream);
            }
        }
        Some(presence)
    }

    fn remove_connection(&mut self, connection: ConnectionId) -> Option<CloseResult> {
        let record = self.connections.remove(&connection)?;
        if let Some(connections) = self.sessions.get_mut(&record.session) {
            connections.remove(&connection);
            if connections.is_empty() {
                self.sessions.remove(&record.session);
            }
        }
        if let Some(connections) = self.users.get_mut(&record.user) {
            connections.remove(&connection);
            if connections.is_empty() {
                self.users.remove(&record.user);
            }
        }
        let keys: Vec<_> = self
            .presences
            .values()
            .filter(|presence| presence.connection == connection)
            .map(|presence| presence.key)
            .collect();
        let removed_presences = keys.len();
        for key in keys {
            self.remove_presence(key);
        }
        Some(CloseResult {
            connection,
            removed_presences,
        })
    }
}

fn validate_open(request: OpenConnection) -> Result<(), DomainError> {
    if request.connection.is_zero()
        || request.session.is_zero()
        || request.user.is_zero()
        || request.node.is_zero()
        || request.route_generation == 0
    {
        return Err(invalid("invalid_connection_identity"));
    }
    Ok(())
}

fn route_fence(
    record: &ConnectionRecord,
    node: NodeId,
    generation: u64,
) -> Result<(), DomainError> {
    if record.node != node {
        return Err(error(
            StableCode::Aborted,
            "route_owner_mismatch",
            RetryClass::ResyncRequired,
        ));
    }
    if record.route_generation != generation {
        return Err(error(
            StableCode::Aborted,
            "route_generation_mismatch",
            RetryClass::ResyncRequired,
        ));
    }
    Ok(())
}

const fn target(record: ConnectionRecord) -> RouteTarget {
    RouteTarget {
        connection: record.connection,
        session: record.session,
        user: record.user,
        node: record.node,
        route_generation: record.route_generation,
    }
}

const fn invalid(reason: &'static str) -> DomainError {
    error(StableCode::InvalidArgument, reason, RetryClass::Never)
}

const fn unauthenticated(reason: &'static str) -> DomainError {
    error(StableCode::Unauthenticated, reason, RetryClass::Never)
}

const fn exhausted(reason: &'static str) -> DomainError {
    error(
        StableCode::ResourceExhausted,
        reason,
        RetryClass::SafeBackoff,
    )
}

const fn connection_not_found() -> DomainError {
    error(
        StableCode::NotFound,
        "connection_not_found",
        RetryClass::Never,
    )
}

const fn queue_underflow() -> DomainError {
    error(
        StableCode::DataLoss,
        "outbound_queue_accounting_underflow",
        RetryClass::Never,
    )
}

const fn overflow() -> DomainError {
    error(StableCode::OutOfRange, "counter_overflow", RetryClass::Never)
}

const fn error(code: StableCode, reason: &'static str, retry: RetryClass) -> DomainError {
    DomainError::new(code, reason, retry)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn id(value: u8) -> [u8; 16] {
        [value; 16]
    }

    fn user(value: u8) -> UserId {
        UserId::new(id(value))
    }

    fn open(connection: u8, session: u8, user_id: u8, node: u8) -> OpenConnection {
        OpenConnection {
            connection: ConnectionId::new(id(connection)),
            session: SessionId::new(id(session)),
            user: user(user_id),
            node: NodeId::new(id(node)),
            route_generation: 1,
            user_revocation_epoch: 0,
            now_ms: 100,
        }
    }

    fn router() -> RouterState {
        RouterState::new(RouterLimits {
            max_connections: 8,
            max_connections_per_session: 2,
            max_presences: 8,
            max_presences_per_stream: 4,
            max_status_bytes: 16,
            max_outbound_messages: 2,
            max_outbound_bytes: 10,
        })
        .unwrap()
    }

    fn join(router: &mut RouterState, connection: u8, stream: u8) -> PresenceRecord {
        router
            .join_presence(
                ConnectionId::new(id(connection)),
                NodeId::new(id(1)),
                1,
                StreamId::new(id(stream)),
                "player".to_owned(),
                "ready".to_owned(),
                false,
                true,
            )
            .unwrap()
    }

    #[test]
    fn open_join_and_recipients_are_deterministic() {
        let mut router = router();
        router.open(open(1, 1, 1, 1)).unwrap();
        join(&mut router, 1, 9);
        assert_eq!(router.connection_count(), 1);
        assert_eq!(router.presence_count(), 1);
        assert_eq!(router.recipients(StreamId::new(id(9)))[0].connection, ConnectionId::new(id(1)));
    }

    #[test]
    fn duplicate_connection_and_session_limit_do_not_mutate() {
        let mut router = router();
        router.open(open(1, 1, 1, 1)).unwrap();
        let before = router.clone();
        assert_eq!(router.open(open(1, 2, 2, 1)).unwrap_err().reason(), "connection_id_exists");
        assert_eq!(router, before);
        router.open(open(2, 1, 1, 1)).unwrap();
        assert_eq!(router.open(open(3, 1, 1, 1)).unwrap_err().reason(), "session_connection_limit_exceeded");
    }

    #[test]
    fn rebind_increments_generation_and_fences_old_node() {
        let mut router = router();
        router.open(open(1, 1, 1, 1)).unwrap();
        let target = router.rebind(ConnectionId::new(id(1)), NodeId::new(id(1)), 1, NodeId::new(id(2))).unwrap();
        assert_eq!(target.route_generation, 2);
        assert_eq!(router.route(ConnectionId::new(id(1)), NodeId::new(id(1)), 1).unwrap_err().reason(), "route_owner_mismatch");
        assert!(router.route(ConnectionId::new(id(1)), NodeId::new(id(2)), 2).is_ok());
    }

    #[test]
    fn drain_blocks_new_presence_but_allows_heartbeat() {
        let mut router = router();
        router.open(open(1, 1, 1, 1)).unwrap();
        router.begin_node_drain(NodeId::new(id(1))).unwrap();
        assert_eq!(router.join_presence(ConnectionId::new(id(1)), NodeId::new(id(1)), 1, StreamId::new(id(2)), "player".to_owned(), "".to_owned(), false, false).unwrap_err().reason(), "connection_draining");
        assert!(router.touch(ConnectionId::new(id(1)), NodeId::new(id(1)), 1, 101).is_ok());
    }

    #[test]
    fn close_removes_all_presence_indexes() {
        let mut router = router();
        router.open(open(1, 1, 1, 1)).unwrap();
        join(&mut router, 1, 8);
        let result = router.close_connection(ConnectionId::new(id(1)), NodeId::new(id(1)), 1).unwrap();
        assert_eq!(result.removed_presences, 1);
        assert_eq!(router.connection_count(), 0);
        assert!(router.recipients(StreamId::new(id(8))).is_empty());
    }

    #[test]
    fn user_revocation_closes_connections_and_rejects_stale_epoch() {
        let mut router = router();
        router.open(open(1, 1, 1, 1)).unwrap();
        router.open(open(2, 2, 1, 1)).unwrap();
        let closed = router.revoke_user(user(1), 0, 1).unwrap();
        assert_eq!(closed.len(), 2);
        let mut stale = open(3, 3, 1, 1);
        stale.user_revocation_epoch = 0;
        assert_eq!(router.open(stale).unwrap_err().reason(), "user_revocation_epoch_mismatch");
        let mut current = open(3, 3, 1, 1);
        current.user_revocation_epoch = 1;
        assert!(router.open(current).is_ok());
    }

    #[test]
    fn outbound_budget_failure_has_zero_partial_mutation() {
        let mut router = router();
        router.open(open(1, 1, 1, 1)).unwrap();
        router.reserve_outbound(ConnectionId::new(id(1)), NodeId::new(id(1)), 1, 6).unwrap();
        let before = router.connection(ConnectionId::new(id(1))).unwrap();
        assert_eq!(router.reserve_outbound(ConnectionId::new(id(1)), NodeId::new(id(1)), 1, 5).unwrap_err().reason(), "slow_consumer_budget_exceeded");
        assert_eq!(router.connection(ConnectionId::new(id(1))).unwrap(), before);
        router.release_outbound(ConnectionId::new(id(1)), NodeId::new(id(1)), 1, 6).unwrap();
    }

    #[test]
    fn outbound_accounting_underflow_is_data_loss() {
        let mut router = router();
        router.open(open(1, 1, 1, 1)).unwrap();
        assert_eq!(router.release_outbound(ConnectionId::new(id(1)), NodeId::new(id(1)), 1, 1).unwrap_err().reason(), "outbound_queue_accounting_underflow");
    }

    #[test]
    fn idle_expiry_is_ordered_and_bounded() {
        let mut router = router();
        router.open(open(1, 1, 1, 1)).unwrap();
        router.open(open(2, 2, 2, 1)).unwrap();
        let expired = router.expire_idle(200, 50, 1).unwrap();
        assert_eq!(expired.len(), 1);
        assert_eq!(expired[0].connection, ConnectionId::new(id(1)));
        assert_eq!(router.connection_count(), 1);
    }

    #[test]
    fn close_session_removes_only_that_session() {
        let mut router = router();
        router.open(open(1, 1, 1, 1)).unwrap();
        router.open(open(2, 2, 1, 1)).unwrap();
        assert_eq!(router.close_session(SessionId::new(id(1))).unwrap().len(), 1);
        assert!(router.connection(ConnectionId::new(id(1))).is_none());
        assert!(router.connection(ConnectionId::new(id(2))).is_some());
    }

    #[test]
    fn duplicate_presence_and_status_limit_fail_closed() {
        let mut router = router();
        router.open(open(1, 1, 1, 1)).unwrap();
        join(&mut router, 1, 2);
        assert_eq!(router.join_presence(ConnectionId::new(id(1)), NodeId::new(id(1)), 1, StreamId::new(id(2)), "player".to_owned(), "".to_owned(), false, false).unwrap_err().reason(), "presence_exists");
        assert_eq!(router.join_presence(ConnectionId::new(id(1)), NodeId::new(id(1)), 1, StreamId::new(id(3)), "player".to_owned(), "status-is-longer-than-limit".to_owned(), false, false).unwrap_err().reason(), "presence_status_limit_exceeded");
    }

    #[test]
    fn heartbeat_time_regression_is_rejected() {
        let mut router = router();
        router.open(open(1, 1, 1, 1)).unwrap();
        assert_eq!(router.touch(ConnectionId::new(id(1)), NodeId::new(id(1)), 1, 99).unwrap_err().reason(), "heartbeat_time_regression");
    }
}
