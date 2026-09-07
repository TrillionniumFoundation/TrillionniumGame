use core::fmt;
use std::collections::{BTreeMap, BTreeSet};

use crate::{
    ConnectionGeneration, ConnectionRef, JoinPresenceRequest, LeavePresenceRequest,
    MutationDisposition, PresenceDelta, PresenceError, PresenceRecord, PresenceRouter,
    RemoveConnectionRequest, SessionId, SnapshotVisibility, StreamKey, UpdatePresenceRequest,
};

pub const MAX_CONNECTIONS_PER_SESSION: usize = 1_024;
pub const MAX_ACTIVE_CONNECTIONS: usize = 65_536;
pub const MAX_TRACKED_CONNECTIONS: usize = 131_072;
pub const MAX_TRACKED_SESSIONS: usize = 131_072;
pub const MAX_REVOCATION_HIGH_WATERS: usize = 131_072;
pub const MAX_PRESENCE_ENTRIES: usize = 262_144;

pub const DEFAULT_MAX_ACTIVE_CONNECTIONS: usize = 16_384;
pub const DEFAULT_MAX_TRACKED_CONNECTIONS: usize = 32_768;
pub const DEFAULT_MAX_TRACKED_SESSIONS: usize = 32_768;
pub const DEFAULT_MAX_REVOCATION_HIGH_WATERS: usize = 32_768;
pub const DEFAULT_MAX_PRESENCE_ENTRIES: usize = 65_536;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SessionRouteLimits {
    max_connections_per_session: usize,
    max_active_connections: usize,
    max_tracked_connections: usize,
    max_tracked_sessions: usize,
    max_revocation_high_waters: usize,
    max_presence_entries: usize,
}

impl SessionRouteLimits {
    pub fn new(
        max_connections_per_session: usize,
        max_active_connections: usize,
        max_tracked_connections: usize,
        max_tracked_sessions: usize,
        max_revocation_high_waters: usize,
        max_presence_entries: usize,
    ) -> Result<Self, SessionRouteError> {
        validate_limit(
            "connections_per_session",
            max_connections_per_session,
            MAX_CONNECTIONS_PER_SESSION,
        )?;
        validate_limit(
            "active_connections",
            max_active_connections,
            MAX_ACTIVE_CONNECTIONS,
        )?;
        validate_limit(
            "tracked_connections",
            max_tracked_connections,
            MAX_TRACKED_CONNECTIONS,
        )?;
        validate_limit(
            "tracked_sessions",
            max_tracked_sessions,
            MAX_TRACKED_SESSIONS,
        )?;
        validate_limit(
            "revocation_high_waters",
            max_revocation_high_waters,
            MAX_REVOCATION_HIGH_WATERS,
        )?;
        validate_limit(
            "presence_entries",
            max_presence_entries,
            MAX_PRESENCE_ENTRIES,
        )?;
        if max_connections_per_session > max_active_connections {
            return Err(SessionRouteError::InvalidLimit {
                resource: "connections_per_session",
                value: max_connections_per_session,
                maximum: max_active_connections,
            });
        }
        if max_revocation_high_waters > max_tracked_sessions {
            return Err(SessionRouteError::InvalidLimit {
                resource: "revocation_high_waters",
                value: max_revocation_high_waters,
                maximum: max_tracked_sessions,
            });
        }
        Ok(Self {
            max_connections_per_session,
            max_active_connections,
            max_tracked_connections,
            max_tracked_sessions,
            max_revocation_high_waters,
            max_presence_entries,
        })
    }

    pub const fn max_connections_per_session(self) -> usize {
        self.max_connections_per_session
    }

    pub const fn max_active_connections(self) -> usize {
        self.max_active_connections
    }

    pub const fn max_tracked_connections(self) -> usize {
        self.max_tracked_connections
    }

    pub const fn max_tracked_sessions(self) -> usize {
        self.max_tracked_sessions
    }

    pub const fn max_revocation_high_waters(self) -> usize {
        self.max_revocation_high_waters
    }

    pub const fn max_presence_entries(self) -> usize {
        self.max_presence_entries
    }
}

impl Default for SessionRouteLimits {
    fn default() -> Self {
        Self {
            max_connections_per_session: MAX_CONNECTIONS_PER_SESSION,
            max_active_connections: DEFAULT_MAX_ACTIVE_CONNECTIONS,
            max_tracked_connections: DEFAULT_MAX_TRACKED_CONNECTIONS,
            max_tracked_sessions: DEFAULT_MAX_TRACKED_SESSIONS,
            max_revocation_high_waters: DEFAULT_MAX_REVOCATION_HIGH_WATERS,
            max_presence_entries: DEFAULT_MAX_PRESENCE_ENTRIES,
        }
    }
}

fn validate_limit(
    resource: &'static str,
    value: usize,
    maximum: usize,
) -> Result<(), SessionRouteError> {
    if value == 0 || value > maximum {
        return Err(SessionRouteError::InvalidLimit {
            resource,
            value,
            maximum,
        });
    }
    Ok(())
}

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
pub struct SessionUpdateRequest {
    pub presence: UpdatePresenceRequest,
    pub session_id: SessionId,
    pub session_generation: SessionRouteGeneration,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SessionLeaveRequest {
    pub presence: LeavePresenceRequest,
    pub session_id: SessionId,
    pub session_generation: SessionRouteGeneration,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SessionRemoveConnectionRequest {
    pub presence: RemoveConnectionRequest,
    pub session_id: SessionId,
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
    InvalidLimit {
        resource: &'static str,
        value: usize,
        maximum: usize,
    },
    ResourceExhausted {
        resource: &'static str,
        limit: usize,
    },
    SessionBindingMissing {
        connection: ConnectionRef,
    },
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
    SessionGenerationAhead {
        connection: ConnectionRef,
        current: SessionRouteGeneration,
        received: SessionRouteGeneration,
    },
    SessionBindingConflict {
        connection: ConnectionRef,
        current_session: SessionId,
        received_session: SessionId,
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
            Self::InvalidLimit {
                resource,
                value,
                maximum,
            } => write!(
                formatter,
                "{resource} limit {value} must be in 1..={maximum}"
            ),
            Self::ResourceExhausted { resource, limit } => {
                write!(formatter, "{resource} capacity is exhausted at {limit}")
            }
            Self::SessionBindingMissing { connection } => write!(
                formatter,
                "connection {}/{} has no session binding",
                connection.node_id, connection.connection_id
            ),
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
            Self::SessionGenerationAhead {
                connection,
                current,
                received,
            } => write!(
                formatter,
                "session generation {received} is ahead for {}/{}; current generation is {current}",
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

#[derive(Clone, Debug)]
pub struct SessionRouteRegistry {
    limits: SessionRouteLimits,
    revision: u64,
    router: PresenceRouter,
    bindings: BTreeMap<ConnectionRef, SessionBinding>,
    connections_by_session: BTreeMap<SessionId, BTreeSet<ConnectionRef>>,
    revoked_through: BTreeMap<SessionId, SessionRouteGeneration>,
}

impl Default for SessionRouteRegistry {
    fn default() -> Self {
        Self::with_limits(SessionRouteLimits::default())
    }
}

impl SessionRouteRegistry {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_limits(limits: SessionRouteLimits) -> Self {
        Self {
            limits,
            revision: 0,
            router: PresenceRouter::new(),
            bindings: BTreeMap::new(),
            connections_by_session: BTreeMap::new(),
            revoked_through: BTreeMap::new(),
        }
    }

    pub const fn limits(&self) -> SessionRouteLimits {
        self.limits
    }

    pub const fn revision(&self) -> u64 {
        self.revision
    }

    pub fn active_connection_count(&self) -> usize {
        self.bindings.len()
    }

    pub fn tracked_connection_count(&self) -> usize {
        self.router.connection_count()
    }

    pub fn presence_entry_count(&self) -> usize {
        self.router.entry_count()
    }

    pub fn tracked_session_count(&self) -> usize {
        self.revoked_through.len()
            + self
                .connections_by_session
                .keys()
                .filter(|session_id| !self.revoked_through.contains_key(*session_id))
                .count()
    }

    pub fn revocation_high_water_count(&self) -> usize {
        self.revoked_through.len()
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
        self.require_join_capacity(&request)?;

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
        request: SessionUpdateRequest,
    ) -> Result<SessionMutationDelta, SessionRouteError> {
        let SessionUpdateRequest {
            presence,
            session_id,
            session_generation,
        } = request;
        let connection = presence.connection.clone();
        let connection_generation = presence.generation;
        self.mutate_fenced_presence(
            &connection,
            connection_generation,
            &session_id,
            session_generation,
            false,
            |router| router.update_presence(presence),
        )
    }

    pub fn leave_presence(
        &mut self,
        request: SessionLeaveRequest,
    ) -> Result<SessionMutationDelta, SessionRouteError> {
        let SessionLeaveRequest {
            presence,
            session_id,
            session_generation,
        } = request;
        let connection = presence.connection.clone();
        let connection_generation = presence.generation;
        self.mutate_fenced_presence(
            &connection,
            connection_generation,
            &session_id,
            session_generation,
            false,
            |router| router.leave_presence(presence),
        )
    }

    pub fn remove_connection(
        &mut self,
        request: SessionRemoveConnectionRequest,
    ) -> Result<SessionMutationDelta, SessionRouteError> {
        let SessionRemoveConnectionRequest {
            presence,
            session_id,
            session_generation,
        } = request;
        let connection = presence.connection.clone();
        let connection_generation = presence.generation;
        self.mutate_fenced_presence(
            &connection,
            connection_generation,
            &session_id,
            session_generation,
            true,
            |router| router.remove_connection(presence),
        )
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
        self.require_tracked_session_capacity(&request.session_id)?;
        if !self.revoked_through.contains_key(&request.session_id)
            && self.revoked_through.len() >= self.limits.max_revocation_high_waters
        {
            return Err(SessionRouteError::ResourceExhausted {
                resource: "revocation_high_waters",
                limit: self.limits.max_revocation_high_waters,
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
        if self.bindings.len() > self.limits.max_active_connections {
            return Err(SessionRouteError::InvariantViolation(
                "active connection count exceeds configured capacity",
            ));
        }
        if self.router.connection_count() > self.limits.max_tracked_connections {
            return Err(SessionRouteError::InvariantViolation(
                "tracked connection count exceeds configured capacity",
            ));
        }
        if self.router.entry_count() > self.limits.max_presence_entries {
            return Err(SessionRouteError::InvariantViolation(
                "presence entry count exceeds configured capacity",
            ));
        }
        if self.revoked_through.len() > self.limits.max_revocation_high_waters {
            return Err(SessionRouteError::InvariantViolation(
                "revocation high-water count exceeds configured capacity",
            ));
        }
        if self.tracked_session_count() > self.limits.max_tracked_sessions {
            return Err(SessionRouteError::InvariantViolation(
                "tracked session count exceeds configured capacity",
            ));
        }

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
            if connections.len() > self.limits.max_connections_per_session {
                return Err(SessionRouteError::InvariantViolation(
                    "session index exceeds the configured connection limit",
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

    fn mutate_fenced_presence(
        &mut self,
        connection: &ConnectionRef,
        connection_generation: ConnectionGeneration,
        session_id: &SessionId,
        session_generation: SessionRouteGeneration,
        remove_binding: bool,
        mutation: impl FnOnce(&mut PresenceRouter) -> Result<PresenceDelta, PresenceError>,
    ) -> Result<SessionMutationDelta, SessionRouteError> {
        self.verify_invariants()?;
        self.require_exact_binding(
            connection,
            connection_generation,
            session_id,
            session_generation,
        )?;
        let mut candidate = self.clone();
        let presence = mutation(&mut candidate.router)?;
        let binding_removed = if remove_binding {
            candidate.remove_binding(connection).is_some()
        } else {
            false
        };
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

    fn require_exact_binding(
        &self,
        connection: &ConnectionRef,
        connection_generation: ConnectionGeneration,
        session_id: &SessionId,
        session_generation: SessionRouteGeneration,
    ) -> Result<(), SessionRouteError> {
        let binding = self.bindings.get(connection).ok_or_else(|| {
            SessionRouteError::SessionBindingMissing {
                connection: connection.clone(),
            }
        })?;
        if connection_generation < binding.connection_generation {
            return Err(PresenceError::StaleGeneration {
                connection: connection.clone(),
                current: binding.connection_generation,
                received: connection_generation,
            }
            .into());
        }
        if connection_generation > binding.connection_generation {
            return Err(PresenceError::GenerationAhead {
                connection: connection.clone(),
                current: binding.connection_generation,
                received: connection_generation,
            }
            .into());
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
        if session_generation > binding.session_generation {
            return Err(SessionRouteError::SessionGenerationAhead {
                connection: connection.clone(),
                current: binding.session_generation,
                received: session_generation,
            });
        }
        Ok(())
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

    fn require_join_capacity(&self, request: &SessionJoinRequest) -> Result<(), SessionRouteError> {
        let connection = &request.presence.connection;
        let session_id = &request.presence.identity.session_id;
        if !self.bindings.contains_key(connection)
            && self.bindings.len() >= self.limits.max_active_connections
        {
            return Err(SessionRouteError::ResourceExhausted {
                resource: "active_connections",
                limit: self.limits.max_active_connections,
            });
        }
        if self.router.established_generation(connection).is_none()
            && self.router.connection_count() >= self.limits.max_tracked_connections
        {
            return Err(SessionRouteError::ResourceExhausted {
                resource: "tracked_connections",
                limit: self.limits.max_tracked_connections,
            });
        }
        self.require_tracked_session_capacity(session_id)?;
        self.require_session_capacity(connection, session_id)?;
        self.require_presence_capacity(&request.presence)
    }

    fn require_tracked_session_capacity(
        &self,
        session_id: &SessionId,
    ) -> Result<(), SessionRouteError> {
        let already_tracked = self.connections_by_session.contains_key(session_id)
            || self.revoked_through.contains_key(session_id);
        if !already_tracked && self.tracked_session_count() >= self.limits.max_tracked_sessions {
            return Err(SessionRouteError::ResourceExhausted {
                resource: "tracked_sessions",
                limit: self.limits.max_tracked_sessions,
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
        if !connections.contains(connection)
            && connections.len() >= self.limits.max_connections_per_session
        {
            return Err(SessionRouteError::ResourceExhausted {
                resource: "connections_per_session",
                limit: self.limits.max_connections_per_session,
            });
        }
        Ok(())
    }

    fn require_presence_capacity(
        &self,
        request: &JoinPresenceRequest,
    ) -> Result<(), SessionRouteError> {
        if self.router.entry_count() < self.limits.max_presence_entries {
            return Ok(());
        }
        let already_joined = self
            .router
            .snapshot(&request.stream, SnapshotVisibility::IncludeHidden)?
            .iter()
            .any(|record| {
                record.connection == request.connection && record.generation == request.generation
            });
        if already_joined {
            Ok(())
        } else {
            Err(SessionRouteError::ResourceExhausted {
                resource: "presence_entries",
                limit: self.limits.max_presence_entries,
            })
        }
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

    fn update(
        connection_id: &str,
        connection_generation: u64,
        session_id: &str,
        session_generation: u64,
        label: &str,
        status: &str,
    ) -> SessionUpdateRequest {
        SessionUpdateRequest {
            presence: UpdatePresenceRequest {
                connection: connection(connection_id),
                generation: ConnectionGeneration::new(connection_generation).expect("generation"),
                stream: stream(label),
                status: PresenceStatus::new(status).expect("status"),
                hidden: false,
            },
            session_id: SessionId::new(session_id).expect("session"),
            session_generation: SessionRouteGeneration::new(session_generation)
                .expect("session generation"),
        }
    }

    fn leave(
        connection_id: &str,
        connection_generation: u64,
        session_id: &str,
        session_generation: u64,
        label: &str,
    ) -> SessionLeaveRequest {
        SessionLeaveRequest {
            presence: LeavePresenceRequest {
                connection: connection(connection_id),
                generation: ConnectionGeneration::new(connection_generation).expect("generation"),
                stream: stream(label),
            },
            session_id: SessionId::new(session_id).expect("session"),
            session_generation: SessionRouteGeneration::new(session_generation)
                .expect("session generation"),
        }
    }

    fn remove(
        connection_id: &str,
        connection_generation: u64,
        session_id: &str,
        session_generation: u64,
    ) -> SessionRemoveConnectionRequest {
        SessionRemoveConnectionRequest {
            presence: RemoveConnectionRequest {
                connection: connection(connection_id),
                generation: ConnectionGeneration::new(connection_generation).expect("generation"),
            },
            session_id: SessionId::new(session_id).expect("session"),
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
        assert!(matches!(
            registry.join_presence(join("connection-a", 2, "session-a", 1, "room", false)),
            Err(SessionRouteError::SessionRevoked { .. })
        ));
        assert_eq!(registry.revision(), before_revision);
        registry
            .join_presence(join("connection-a", 2, "session-a", 2, "room", false))
            .expect("new session generation");
        assert_eq!(registry.active_connection_count(), 1);
    }

    #[test]
    fn stale_session_epoch_cannot_update_leave_or_remove_new_epoch() {
        let mut registry = SessionRouteRegistry::new();
        registry
            .join_presence(join("connection-a", 1, "session-a", 1, "room", false))
            .expect("join");
        registry
            .join_presence(join("connection-a", 1, "session-a", 2, "room", false))
            .expect("advance session generation");
        let before_revision = registry.revision();
        let before = registry
            .snapshot(&stream("room"), SnapshotVisibility::IncludeHidden)
            .expect("snapshot");
        assert!(matches!(
            registry.update_presence(update("connection-a", 1, "session-a", 1, "room", "stale")),
            Err(SessionRouteError::StaleSessionGeneration { .. })
        ));
        assert!(matches!(
            registry.leave_presence(leave("connection-a", 1, "session-a", 1, "room")),
            Err(SessionRouteError::StaleSessionGeneration { .. })
        ));
        assert!(matches!(
            registry.remove_connection(remove("connection-a", 1, "session-a", 1)),
            Err(SessionRouteError::StaleSessionGeneration { .. })
        ));
        assert_eq!(registry.revision(), before_revision);
        assert_eq!(
            registry
                .snapshot(&stream("room"), SnapshotVisibility::IncludeHidden)
                .expect("snapshot"),
            before
        );
        registry
            .update_presence(update("connection-a", 1, "session-a", 2, "room", "current"))
            .expect("current session generation");
    }

    #[test]
    fn same_connection_generation_cannot_change_session_identity() {
        let mut registry = SessionRouteRegistry::new();
        registry
            .join_presence(join("connection-a", 1, "session-a", 1, "room", false))
            .expect("join");
        let before_revision = registry.revision();
        assert!(matches!(
            registry.join_presence(join("connection-a", 1, "session-b", 1, "room", false)),
            Err(SessionRouteError::SessionBindingConflict { .. })
        ));
        assert_eq!(registry.revision(), before_revision);
    }

    #[test]
    fn global_connection_capacity_fails_closed_and_is_reusable() {
        let limits = SessionRouteLimits::new(1, 1, 1, 2, 2, 2).expect("limits");
        let mut registry = SessionRouteRegistry::with_limits(limits);
        registry
            .join_presence(join("connection-a", 1, "session-a", 1, "room", false))
            .expect("first join");
        let before_revision = registry.revision();
        assert_eq!(
            registry.join_presence(join("connection-b", 1, "session-b", 1, "room", false)),
            Err(SessionRouteError::ResourceExhausted {
                resource: "active_connections",
                limit: 1,
            })
        );
        assert_eq!(registry.revision(), before_revision);
        registry
            .remove_connection(remove("connection-a", 1, "session-a", 1))
            .expect("remove first");
        assert_eq!(
            registry.join_presence(join("connection-b", 1, "session-b", 1, "room", false)),
            Err(SessionRouteError::ResourceExhausted {
                resource: "tracked_connections",
                limit: 1,
            })
        );
        registry
            .join_presence(join("connection-a", 2, "session-b", 1, "room", false))
            .expect("active capacity reused inside admitted identity universe");
        assert_eq!(registry.active_connection_count(), 1);
        assert_eq!(registry.tracked_connection_count(), 1);
        assert_eq!(registry.tracked_session_count(), 1);
    }

    #[test]
    fn unseen_zero_connection_revocations_are_globally_bounded() {
        let limits = SessionRouteLimits::new(1, 1, 3, 3, 2, 1).expect("limits");
        let mut registry = SessionRouteRegistry::with_limits(limits);
        registry
            .revoke_session(revoke("session-a", 1))
            .expect("first high-water");
        registry
            .revoke_session(revoke("session-b", 1))
            .expect("second high-water");
        let before_revision = registry.revision();
        assert_eq!(
            registry.revoke_session(revoke("session-c", 1)),
            Err(SessionRouteError::ResourceExhausted {
                resource: "revocation_high_waters",
                limit: 2,
            })
        );
        assert_eq!(registry.revision(), before_revision);
        assert_eq!(registry.revocation_high_water_count(), 2);
    }

    #[test]
    fn per_session_and_presence_capacities_fail_without_mutation() {
        let limits = SessionRouteLimits::new(1, 2, 2, 2, 2, 1).expect("limits");
        let mut registry = SessionRouteRegistry::with_limits(limits);
        registry
            .join_presence(join("connection-a", 1, "session-a", 1, "room", false))
            .expect("first join");
        let before_revision = registry.revision();
        assert_eq!(
            registry.join_presence(join("connection-b", 1, "session-a", 1, "room", false)),
            Err(SessionRouteError::ResourceExhausted {
                resource: "connections_per_session",
                limit: 1,
            })
        );
        assert_eq!(
            registry.join_presence(join("connection-a", 1, "session-a", 1, "second", false)),
            Err(SessionRouteError::ResourceExhausted {
                resource: "presence_entries",
                limit: 1,
            })
        );
        assert_eq!(registry.revision(), before_revision);
        assert_eq!(registry.presence_entry_count(), 1);
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

    #[test]
    fn limit_configuration_rejects_zero_excess_and_incoherent_values() {
        assert!(matches!(
            SessionRouteLimits::new(0, 1, 1, 1, 1, 1),
            Err(SessionRouteError::InvalidLimit { .. })
        ));
        assert!(matches!(
            SessionRouteLimits::new(2, 1, 2, 2, 2, 2),
            Err(SessionRouteError::InvalidLimit {
                resource: "connections_per_session",
                ..
            })
        ));
        assert!(matches!(
            SessionRouteLimits::new(1, 1, 1, 1, 2, 1),
            Err(SessionRouteError::InvalidLimit {
                resource: "revocation_high_waters",
                ..
            })
        ));
    }
}
