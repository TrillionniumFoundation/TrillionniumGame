use core::fmt;
use std::collections::{BTreeMap, BTreeSet};

use crate::{
    ConnectionGeneration, ConnectionRef, JoinPresenceRequest, LeavePresenceRequest,
    MutationDisposition, PresenceDelta, PresenceError, PresenceRecord, PresenceRouter,
    RemoveConnectionRequest, SessionId, SnapshotVisibility, StreamKey, UpdatePresenceRequest,
};

pub const MAX_CONNECTIONS_PER_SESSION: usize = 1_024;

#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct SessionRouteGeneration(u64);

impl SessionRouteGeneration {
    pub fn new(value: u64) -> Result<Self, SessionRouteError> {
        if value == 0 {
            return Err(SessionRouteError::InvalidSessionGeneration);
        }
        Ok(Self(value))
    }

    pub const fn get(self) -> u64 {
        self.0
    }
}

impl fmt::Display for SessionRouteGeneration {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.fmt(formatter)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SessionJoinRequest {
    pub presence: JoinPresenceRequest,
    pub session_generation: SessionRouteGeneration,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SessionRevocationRequest {
    pub session_id: SessionId,
    pub through_generation: SessionRouteGeneration,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SessionMutationDelta {
    pub disposition: MutationDisposition,
    pub registry_revision: Option<u64>,
    pub presence: PresenceDelta,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SessionRevocationDelta {
    pub disposition: MutationDisposition,
    pub registry_revision: Option<u64>,
    pub revoked_through: SessionRouteGeneration,
    pub removed_connections: usize,
    pub leaves: Vec<PresenceRecord>,
    pub hidden_changes: usize,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum SessionRouteError {
    InvalidSessionGeneration,
    SessionRevoked {
        session_id: SessionId,
        revoked_through: SessionRouteGeneration,
        received: SessionRouteGeneration,
    },
    StaleSessionGeneration {
        connection: ConnectionRef,
        current: SessionRouteGeneration,
        received: SessionRouteGeneration,
    },
    SessionBindingConflict {
        connection: ConnectionRef,
        current_session: SessionId,
        received_session: SessionId,
    },
    SessionConnectionLimit {
        session_id: SessionId,
        limit: usize,
    },
    RevisionExhausted,
    InvariantViolation(&'static str),
    Presence(PresenceError),
}

impl fmt::Display for SessionRouteError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidSessionGeneration => {
                formatter.write_str("session route generation must be positive")
            }
            Self::SessionRevoked {
                session_id,
                revoked_through,
                received,
            } => write!(
                formatter,
                "session {session_id} generation {received} is revoked through {revoked_through}"
            ),
            Self::StaleSessionGeneration {
                connection,
                current,
                received,
            } => write!(
                formatter,
                "session generation {received} is stale for {}/{}; current generation is {current}",
                connection.node_id, connection.connection_id
            ),
            Self::SessionBindingConflict {
                connection,
                current_session,
                received_session,
            } => write!(
                formatter,
                "connection {}/{} is bound to session {current_session}, not {received_session}",
                connection.node_id, connection.connection_id
            ),
            Self::SessionConnectionLimit { session_id, limit } => write!(
                formatter,
                "session {session_id} exceeds the {limit}-connection route limit"
            ),
            Self::RevisionExhausted => formatter.write_str("session route revision exhausted"),
            Self::InvariantViolation(message) => {
                write!(formatter, "session route invariant violation: {message}")
            }
            Self::Presence(error) => error.fmt(formatter),
        }
    }
}

impl std::error::Error for SessionRouteError {}

impl From<PresenceError> for SessionRouteError {
    fn from(value: PresenceError) -> Self {
        Self::Presence(value)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct SessionBinding {
    session_id: SessionId,
    session_generation: SessionRouteGeneration,
    connection_generation: ConnectionGeneration,
}

#[derive(Clone, Debug, Default)]
pub struct SessionRouteRegistry {
    revision: u64,
    router: PresenceRouter,
    bindings: BTreeMap<ConnectionRef, SessionBinding>,
    connections_by_session: BTreeMap<SessionId, BTreeSet<ConnectionRef>>,
    revoked_through: BTreeMap<SessionId, SessionRouteGeneration>,
}

impl SessionRouteRegistry {
    pub fn new() -> Self {
        Self::default()
    }

    pub const fn revision(&self) -> u64 {
        self.revision
    }

    pub fn active_connection_count(&self) -> usize {
        self.bindings.len()
    }

    pub fn active_connections_for_session(&self, session_id: &SessionId) -> usize {
        self.connections_by_session
            .get(session_id)
            .map_or(0, BTreeSet::len)
    }

    pub fn revoked_through(&self, session_id: &SessionId) -> Option<SessionRouteGeneration> {
        self.revoked_through.get(session_id).copied()
    }

    pub fn snapshot(
        &self,
        stream: &StreamKey,
        visibility: SnapshotVisibility,
    ) -> Result<Vec<PresenceRecord>, SessionRouteError> {
        self.verify_invariants()?;
        self.router.snapshot(stream, visibility).map_err(Into::into)
    }

    pub fn join_presence(
        &mut self,
        request: SessionJoinRequest,
    ) -> Result<SessionMutationDelta, SessionRouteError> {
        self.verify_invariants()?;
        let connection = request.presence.connection.clone();
        let session_id = request.presence.identity.session_id.clone();
        let connection_generation = request.presence.generation;
        let session_generation = request.session_generation;
        self.validate_join_binding(
            &connection,
            connection_generation,
            &session_id,
            session_generation,
        )?;
        self.require_session_capacity(&connection, &session_id)?;

        let next_binding = SessionBinding {
            session_id: session_id.clone(),
            session_generation,
            connection_generation,
        };
        let previous_binding = self.bindings.get(&connection).cloned();
        let binding_changed = previous_binding.as_ref() != Some(&next_binding);
        let mut candidate = self.clone();
        let presence = candidate.router.join_presence(request.presence)?;
        if binding_changed {
            candidate.remove_binding(&connection);
            candidate.insert_binding(connection, next_binding);
        }
        let applied = binding_changed || presence.disposition == MutationDisposition::Applied;
        let registry_revision = if applied {
            let revision = self.next_revision()?;
            candidate.revision = revision;
            candidate.verify_invariants()?;
            *self = candidate;
            Some(revision)
        } else {
            None
        };
        Ok(SessionMutationDelta {
            disposition: if applied {
                MutationDisposition::Applied
            } else {
                MutationDisposition::Idempotent
            },
            registry_revision,
            presence,
        })
    }

    pub fn update_presence(
        &mut self,
        request: UpdatePresenceRequest,
    ) -> Result<SessionMutationDelta, SessionRouteError> {
        self.mutate_presence(|router| router.update_presence(request))
    }

    pub fn leave_presence(
        &mut self,
        request: LeavePresenceRequest,
    ) -> Result<SessionMutationDelta, SessionRouteError> {
        self.mutate_presence(|router| router.leave_presence(request))
    }

    pub fn remove_connection(
        &mut self,
        request: RemoveConnectionRequest,
    ) -> Result<SessionMutationDelta, SessionRouteError> {
        self.verify_invariants()?;
        let connection = request.connection.clone();
        let mut candidate = self.clone();
        let presence = candidate.router.remove_connection(request)?;
        let binding_removed = candidate.remove_binding(&connection).is_some();
        let applied = binding_removed || presence.disposition == MutationDisposition::Applied;
        let registry_revision = if applied {
            let revision = self.next_revision()?;
            candidate.revision = revision;
            candidate.verify_invariants()?;
            *self = candidate;
            Some(revision)
        } else {
            None
        };
        Ok(SessionMutationDelta {
            disposition: if applied {
                MutationDisposition::Applied
            } else {
                MutationDisposition::Idempotent
            },
            registry_revision,
            presence,
        })
    }

    pub fn revoke_session(
        &mut self,
        request: SessionRevocationRequest,
    ) -> Result<SessionRevocationDelta, SessionRouteError> {
        self.verify_invariants()?;
        if self
            .revoked_through
            .get(&request.session_id)
            .is_some_and(|current| *current >= request.through_generation)
        {
            return Ok(SessionRevocationDelta {
                disposition: MutationDisposition::Idempotent,
                registry_revision: None,
                revoked_through: self.revoked_through[&request.session_id],
                removed_connections: 0,
                leaves: Vec::new(),
                hidden_changes: 0,
            });
        }

        let mut candidate = self.clone();
        let connections = candidate
            .connections_by_session
            .get(&request.session_id)
            .cloned()
            .unwrap_or_default();
        let mut removed_connections = 0usize;
        let mut leaves = Vec::new();
        let mut hidden_changes = 0usize;
        for connection in connections {
            let binding = candidate.bindings.get(&connection).cloned().ok_or(
                SessionRouteError::InvariantViolation(
                    "session index references an absent connection binding",
                ),
            )?;
            if binding.session_generation > request.through_generation {
                continue;
            }
            let delta = candidate
                .router
                .remove_connection(RemoveConnectionRequest {
                    connection: connection.clone(),
                    generation: binding.connection_generation,
                })?;
            leaves.extend(delta.leaves);
            hidden_changes = hidden_changes.saturating_add(delta.hidden_changes);
            candidate.remove_binding(&connection);
            removed_connections = removed_connections.saturating_add(1);
        }
        candidate
            .revoked_through
            .insert(request.session_id, request.through_generation);
        let revision = self.next_revision()?;
        candidate.revision = revision;
        candidate.verify_invariants()?;
        *self = candidate;
        Ok(SessionRevocationDelta {
            disposition: MutationDisposition::Applied,
            registry_revision: Some(revision),
            revoked_through: request.through_generation,
            removed_connections,
            leaves,
            hidden_changes,
        })
    }

    pub fn verify_invariants(&self) -> Result<(), SessionRouteError> {
        self.router.verify_invariants()?;
        for (connection, binding) in &self.bindings {
            if self.router.established_generation(connection) != Some(binding.connection_generation)
            {
                return Err(SessionRouteError::InvariantViolation(
                    "binding generation differs from presence high-water generation",
                ));
            }
            let identity = self.router.established_identity(connection).ok_or(
                SessionRouteError::InvariantViolation("bound connection has no presence identity"),
            )?;
            if identity.session_id != binding.session_id {
                return Err(SessionRouteError::InvariantViolation(
                    "binding session differs from presence identity",
                ));
            }
            let indexed = self
                .connections_by_session
                .get(&binding.session_id)
                .is_some_and(|connections| connections.contains(connection));
            if !indexed {
                return Err(SessionRouteError::InvariantViolation(
                    "connection binding is absent from session index",
                ));
            }
            if self
                .revoked_through
                .get(&binding.session_id)
                .is_some_and(|generation| *generation >= binding.session_generation)
            {
                return Err(SessionRouteError::InvariantViolation(
                    "active connection is at or below session revocation high-water",
                ));
            }
        }
        for (session_id, connections) in &self.connections_by_session {
            if connections.is_empty() {
                return Err(SessionRouteError::InvariantViolation(
                    "session index contains an empty connection set",
                ));
            }
            if connections.len() > MAX_CONNECTIONS_PER_SESSION {
                return Err(SessionRouteError::InvariantViolation(
                    "session index exceeds the connection limit",
                ));
            }
            for connection in connections {
                let binding =
                    self.bindings
                        .get(connection)
                        .ok_or(SessionRouteError::InvariantViolation(
                            "session index references an absent binding",
                        ))?;
                if &binding.session_id != session_id {
                    return Err(SessionRouteError::InvariantViolation(
                        "session index points to a different session binding",
                    ));
                }
            }
        }
        Ok(())
    }

    fn mutate_presence(
        &mut self,
        mutation: impl FnOnce(&mut PresenceRouter) -> Result<PresenceDelta, PresenceError>,
    ) -> Result<SessionMutationDelta, SessionRouteError> {
        self.verify_invariants()?;
        let mut candidate = self.clone();
        let presence = mutation(&mut candidate.router)?;
        let registry_revision = if presence.disposition == MutationDisposition::Applied {
            let revision = self.next_revision()?;
            candidate.revision = revision;
            candidate.verify_invariants()?;
            *self = candidate;
            Some(revision)
        } else {
            None
        };
        Ok(SessionMutationDelta {
            disposition: presence.disposition,
            registry_revision,
            presence,
        })
    }

    fn validate_join_binding(
        &self,
        connection: &ConnectionRef,
        connection_generation: ConnectionGeneration,
        session_id: &SessionId,
        session_generation: SessionRouteGeneration,
    ) -> Result<(), SessionRouteError> {
        if let Some(revoked_through) = self.revoked_through.get(session_id) {
            if session_generation <= *revoked_through {
                return Err(SessionRouteError::SessionRevoked {
                    session_id: session_id.clone(),
                    revoked_through: *revoked_through,
                    received: session_generation,
                });
            }
        }
        let Some(binding) = self.bindings.get(connection) else {
            return Ok(());
        };
        if connection_generation < binding.connection_generation {
            return Err(PresenceError::StaleGeneration {
                connection: connection.clone(),
                current: binding.connection_generation,
                received: connection_generation,
            }
            .into());
        }
        if connection_generation > binding.connection_generation {
            return Ok(());
        }
        if &binding.session_id != session_id {
            return Err(SessionRouteError::SessionBindingConflict {
                connection: connection.clone(),
                current_session: binding.session_id.clone(),
                received_session: session_id.clone(),
            });
        }
        if session_generation < binding.session_generation {
            return Err(SessionRouteError::StaleSessionGeneration {
                connection: connection.clone(),
                current: binding.session_generation,
                received: session_generation,
            });
        }
        Ok(())
    }

    fn require_session_capacity(
        &self,
        connection: &ConnectionRef,
        session_id: &SessionId,
    ) -> Result<(), SessionRouteError> {
        let Some(connections) = self.connections_by_session.get(session_id) else {
            return Ok(());
        };
        if !connections.contains(connection) && connections.len() >= MAX_CONNECTIONS_PER_SESSION {
            return Err(SessionRouteError::SessionConnectionLimit {
                session_id: session_id.clone(),
                limit: MAX_CONNECTIONS_PER_SESSION,
            });
        }
        Ok(())
    }

    fn insert_binding(&mut self, connection: ConnectionRef, binding: SessionBinding) {
        self.connections_by_session
            .entry(binding.session_id.clone())
            .or_default()
            .insert(connection.clone());
        self.bindings.insert(connection, binding);
    }

    fn remove_binding(&mut self, connection: &ConnectionRef) -> Option<SessionBinding> {
        let binding = self.bindings.remove(connection)?;
        let remove_session_entry =
            if let Some(connections) = self.connections_by_session.get_mut(&binding.session_id) {
                connections.remove(connection);
                connections.is_empty()
            } else {
                false
            };
        if remove_session_entry {
            self.connections_by_session.remove(&binding.session_id);
        }
        Some(binding)
    }

    fn next_revision(&self) -> Result<u64, SessionRouteError> {
        self.revision
            .checked_add(1)
            .ok_or(SessionRouteError::RevisionExhausted)
    }
}

#[cfg(test)]
mod tests {
    use crate::{
        ConnectionId, NodeId, PresenceIdentity, PresenceStatus, StreamKey, UserId, Username,
    };

    use super::*;

    fn connection(value: &str) -> ConnectionRef {
        ConnectionRef::new(
            NodeId::new("node-a").expect("node"),
            ConnectionId::new(value).expect("connection"),
        )
    }

    fn stream(label: &str) -> StreamKey {
        StreamKey::new(1, [1; 16], [2; 16], label).expect("stream")
    }

    fn join(
        connection_id: &str,
        connection_generation: u64,
        session_id: &str,
        session_generation: u64,
        label: &str,
        hidden: bool,
    ) -> SessionJoinRequest {
        SessionJoinRequest {
            presence: JoinPresenceRequest {
                connection: connection(connection_id),
                generation: ConnectionGeneration::new(connection_generation)
                    .expect("connection generation"),
                stream: stream(label),
                identity: PresenceIdentity::new(
                    UserId::new(format!("user-{session_id}")).expect("user"),
                    SessionId::new(session_id).expect("session"),
                    Username::new(format!("name-{session_id}")).expect("username"),
                ),
                status: PresenceStatus::new("online").expect("status"),
                hidden,
            },
            session_generation: SessionRouteGeneration::new(session_generation)
                .expect("session generation"),
        }
    }

    fn revoke(session_id: &str, through_generation: u64) -> SessionRevocationRequest {
        SessionRevocationRequest {
            session_id: SessionId::new(session_id).expect("session"),
            through_generation: SessionRouteGeneration::new(through_generation)
                .expect("session generation"),
        }
    }

    #[test]
    fn revocation_disconnects_every_bound_socket_atomically() {
        let mut registry = SessionRouteRegistry::new();
        registry
            .join_presence(join("connection-a", 1, "session-a", 1, "room", false))
            .expect("first visible route");
        registry
            .join_presence(join("connection-a", 1, "session-a", 1, "hidden", true))
            .expect("hidden route");
        registry
            .join_presence(join("connection-b", 1, "session-a", 1, "room", false))
            .expect("second visible route");
        registry
            .join_presence(join("connection-c", 1, "session-b", 1, "room", false))
            .expect("unrelated route");

        let delta = registry
            .revoke_session(revoke("session-a", 1))
            .expect("revoke session");
        assert_eq!(delta.disposition, MutationDisposition::Applied);
        assert_eq!(delta.removed_connections, 2);
        assert_eq!(delta.leaves.len(), 2);
        assert_eq!(delta.hidden_changes, 1);
        assert_eq!(
            registry.active_connections_for_session(&SessionId::new("session-a").expect("session")),
            0
        );
        assert_eq!(registry.active_connection_count(), 1);
        assert_eq!(
            registry
                .snapshot(&stream("room"), SnapshotVisibility::PublicOnly)
                .expect("snapshot")
                .len(),
            1
        );
        registry.verify_invariants().expect("invariants");
    }

    #[test]
    fn revoked_generation_cannot_rejoin_but_newer_generation_can() {
        let mut registry = SessionRouteRegistry::new();
        registry
            .join_presence(join("connection-a", 1, "session-a", 1, "room", false))
            .expect("join");
        registry
            .revoke_session(revoke("session-a", 1))
            .expect("revoke");

        let before_revision = registry.revision();
        let error = registry
            .join_presence(join("connection-a", 2, "session-a", 1, "room", false))
            .expect_err("revoked generation must fail");
        assert!(matches!(error, SessionRouteError::SessionRevoked { .. }));
        assert_eq!(registry.revision(), before_revision);

        registry
            .join_presence(join("connection-a", 2, "session-a", 2, "room", false))
            .expect("new session generation");
        assert_eq!(registry.active_connection_count(), 1);
        registry.verify_invariants().expect("invariants");
    }

    #[test]
    fn session_generation_can_advance_without_replacing_the_socket() {
        let mut registry = SessionRouteRegistry::new();
        registry
            .join_presence(join("connection-a", 1, "session-a", 1, "room", false))
            .expect("join");
        let advanced = registry
            .join_presence(join("connection-a", 1, "session-a", 2, "room", false))
            .expect("advance session generation");
        assert_eq!(advanced.disposition, MutationDisposition::Applied);

        let old_revoke = registry
            .revoke_session(revoke("session-a", 1))
            .expect("old revocation");
        assert_eq!(old_revoke.removed_connections, 0);
        assert_eq!(registry.active_connection_count(), 1);

        let current_revoke = registry
            .revoke_session(revoke("session-a", 2))
            .expect("current revocation");
        assert_eq!(current_revoke.removed_connections, 1);
        assert_eq!(registry.active_connection_count(), 0);
    }

    #[test]
    fn connection_generation_takeover_moves_the_session_binding() {
        let mut registry = SessionRouteRegistry::new();
        registry
            .join_presence(join("connection-a", 1, "session-a", 1, "room", false))
            .expect("first join");
        registry
            .join_presence(join("connection-a", 2, "session-b", 1, "room", false))
            .expect("takeover join");

        let old = registry
            .revoke_session(revoke("session-a", 1))
            .expect("revoke old session");
        assert_eq!(old.removed_connections, 0);
        assert_eq!(registry.active_connection_count(), 1);
        assert_eq!(
            registry.active_connections_for_session(&SessionId::new("session-b").expect("session")),
            1
        );
        registry.verify_invariants().expect("invariants");
    }

    #[test]
    fn same_connection_generation_cannot_change_session_identity() {
        let mut registry = SessionRouteRegistry::new();
        registry
            .join_presence(join("connection-a", 1, "session-a", 1, "room", false))
            .expect("join");
        let before_revision = registry.revision();
        let before = registry
            .snapshot(&stream("room"), SnapshotVisibility::IncludeHidden)
            .expect("snapshot");

        let error = registry
            .join_presence(join("connection-a", 1, "session-b", 1, "room", false))
            .expect_err("identity replacement must fail");
        assert!(matches!(
            error,
            SessionRouteError::SessionBindingConflict { .. }
        ));
        assert_eq!(registry.revision(), before_revision);
        assert_eq!(
            registry
                .snapshot(&stream("room"), SnapshotVisibility::IncludeHidden)
                .expect("snapshot"),
            before
        );
    }

    #[test]
    fn stale_session_generation_is_rejected_without_mutation() {
        let mut registry = SessionRouteRegistry::new();
        registry
            .join_presence(join("connection-a", 1, "session-a", 2, "room", false))
            .expect("join");
        let before_revision = registry.revision();
        let error = registry
            .join_presence(join("connection-a", 1, "session-a", 1, "room", false))
            .expect_err("stale session generation must fail");
        assert!(matches!(
            error,
            SessionRouteError::StaleSessionGeneration { .. }
        ));
        assert_eq!(registry.revision(), before_revision);
        registry.verify_invariants().expect("invariants");
    }

    #[test]
    fn repeated_revocation_is_exactly_idempotent() {
        let mut registry = SessionRouteRegistry::new();
        registry
            .join_presence(join("connection-a", 1, "session-a", 1, "room", false))
            .expect("join");
        let first = registry
            .revoke_session(revoke("session-a", 1))
            .expect("first revoke");
        let revision = registry.revision();
        let second = registry
            .revoke_session(revoke("session-a", 1))
            .expect("duplicate revoke");
        assert_eq!(first.disposition, MutationDisposition::Applied);
        assert_eq!(second.disposition, MutationDisposition::Idempotent);
        assert_eq!(second.registry_revision, None);
        assert_eq!(second.removed_connections, 0);
        assert_eq!(registry.revision(), revision);
    }
}
