use core::fmt;
use std::collections::BTreeMap;

use crate::types::{
    ConnectionGeneration, ConnectionRef, JoinPresenceRequest, LeavePresenceRequest,
    PresenceIdentity, PresenceRecord, RemoveConnectionRequest, SnapshotVisibility,
    StreamKey, UpdatePresenceRequest,
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MutationDisposition {
    Applied,
    Idempotent,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PresenceDelta {
    pub disposition: MutationDisposition,
    pub revision: Option<u64>,
    pub joins: Vec<PresenceRecord>,
    pub updates: Vec<PresenceRecord>,
    pub leaves: Vec<PresenceRecord>,
    pub hidden_changes: usize,
}

impl PresenceDelta {
    fn idempotent() -> Self {
        Self {
            disposition: MutationDisposition::Idempotent,
            revision: None,
            joins: Vec::new(),
            updates: Vec::new(),
            leaves: Vec::new(),
            hidden_changes: 0,
        }
    }

    fn applied(
        revision: u64,
        joins: Vec<PresenceRecord>,
        updates: Vec<PresenceRecord>,
        leaves: Vec<PresenceRecord>,
        hidden_changes: usize,
    ) -> Self {
        Self {
            disposition: MutationDisposition::Applied,
            revision: Some(revision),
            joins,
            updates,
            leaves,
            hidden_changes,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum PresenceError {
    GenerationNotEstablished {
        connection: ConnectionRef,
        received: ConnectionGeneration,
    },
    StaleGeneration {
        connection: ConnectionRef,
        current: ConnectionGeneration,
        received: ConnectionGeneration,
    },
    GenerationAhead {
        connection: ConnectionRef,
        current: ConnectionGeneration,
        received: ConnectionGeneration,
    },
    IdentityConflict {
        connection: ConnectionRef,
        existing: PresenceIdentity,
        received: PresenceIdentity,
    },
    PresenceNotJoined {
        connection: ConnectionRef,
        stream: StreamKey,
    },
    RevisionExhausted,
    InvariantViolation(&'static str),
}

impl fmt::Display for PresenceError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::GenerationNotEstablished {
                connection,
                received,
            } => write!(
                formatter,
                "generation {received} is not established for {}/{}",
                connection.node_id, connection.connection_id
            ),
            Self::StaleGeneration {
                connection,
                current,
                received,
            } => write!(
                formatter,
                "stale generation {received} for {}/{}; current generation is {current}",
                connection.node_id, connection.connection_id
            ),
            Self::GenerationAhead {
                connection,
                current,
                received,
            } => write!(
                formatter,
                "generation {received} is ahead of established generation {current} for {}/{}; join must establish it first",
                connection.node_id, connection.connection_id
            ),
            Self::IdentityConflict {
                connection,
                existing,
                received,
            } => write!(
                formatter,
                "identity conflict for {}/{}: existing session {}, received session {}",
                connection.node_id,
                connection.connection_id,
                existing.session_id,
                received.session_id
            ),
            Self::PresenceNotJoined { connection, stream } => write!(
                formatter,
                "presence is not joined for {}/{} on stream mode {} label {:?}",
                connection.node_id,
                connection.connection_id,
                stream.mode(),
                stream.label()
            ),
            Self::RevisionExhausted => formatter.write_str("presence revision exhausted"),
            Self::InvariantViolation(message) => {
                write!(formatter, "presence router invariant violation: {message}")
            }
        }
    }
}

impl std::error::Error for PresenceError {}

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
struct PresenceKey {
    connection: ConnectionRef,
    stream: StreamKey,
}

#[derive(Clone, Debug, Default)]
pub struct PresenceRouter {
    revision: u64,
    generations: BTreeMap<ConnectionRef, ConnectionGeneration>,
    entries: BTreeMap<PresenceKey, PresenceRecord>,
}

impl PresenceRouter {
    pub fn new() -> Self {
        Self::default()
    }

    pub const fn revision(&self) -> u64 {
        self.revision
    }

    pub fn entry_count(&self) -> usize {
        self.entries.len()
    }

    pub fn connection_count(&self) -> usize {
        self.generations.len()
    }

    pub fn established_generation(
        &self,
        connection: &ConnectionRef,
    ) -> Option<ConnectionGeneration> {
        self.generations.get(connection).copied()
    }

    pub fn join_presence(
        &mut self,
        request: JoinPresenceRequest,
    ) -> Result<PresenceDelta, PresenceError> {
        let current = self.generations.get(&request.connection).copied();
        if let Some(current) = current {
            if request.generation < current {
                return Err(PresenceError::StaleGeneration {
                    connection: request.connection,
                    current,
                    received: request.generation,
                });
            }
        }

        let key = PresenceKey {
            connection: request.connection.clone(),
            stream: request.stream.clone(),
        };
        let generation_advance = current
            .map(|current| request.generation > current)
            .unwrap_or(true);

        if !generation_advance {
            if let Some(existing) = self.entries.get(&key) {
                self.require_record_invariants(&key, existing)?;
                if existing.identity != request.identity {
                    return Err(PresenceError::IdentityConflict {
                        connection: request.connection,
                        existing: existing.identity.clone(),
                        received: request.identity,
                    });
                }
                if existing.status == request.status && existing.hidden == request.hidden {
                    return Ok(PresenceDelta::idempotent());
                }
            } else if let Some(existing_identity) =
                self.identity_for_connection(&request.connection)?
            {
                if existing_identity != &request.identity {
                    return Err(PresenceError::IdentityConflict {
                        connection: request.connection,
                        existing: existing_identity.clone(),
                        received: request.identity,
                    });
                }
            }
        }

        let next_revision = self.next_revision()?;
        let mut joins = Vec::new();
        let mut updates = Vec::new();
        let mut leaves = Vec::new();
        let mut hidden_changes = 0usize;

        if generation_advance {
            let removed = self.remove_connection_records(&request.connection)?;
            for record in removed {
                if record.hidden {
                    hidden_changes = hidden_changes.saturating_add(1);
                } else {
                    leaves.push(record);
                }
            }
            self.generations
                .insert(request.connection.clone(), request.generation);
        }

        let record = PresenceRecord {
            connection: request.connection,
            generation: request.generation,
            stream: request.stream,
            identity: request.identity,
            status: request.status,
            hidden: request.hidden,
        };

        if generation_advance {
            if record.hidden {
                hidden_changes = hidden_changes.saturating_add(1);
            } else {
                joins.push(record.clone());
            }
            self.entries.insert(key, record);
        } else if let Some(existing) = self.entries.get_mut(&key) {
            let old = existing.clone();
            *existing = record.clone();
            classify_visibility_change(
                old,
                record,
                &mut joins,
                &mut updates,
                &mut leaves,
                &mut hidden_changes,
            );
        } else {
            if record.hidden {
                hidden_changes = hidden_changes.saturating_add(1);
            } else {
                joins.push(record.clone());
            }
            self.entries.insert(key, record);
        }

        self.revision = next_revision;
        debug_assert!(self.verify_invariants().is_ok());
        Ok(PresenceDelta::applied(
            next_revision,
            joins,
            updates,
            leaves,
            hidden_changes,
        ))
    }

    pub fn update_presence(
        &mut self,
        request: UpdatePresenceRequest,
    ) -> Result<PresenceDelta, PresenceError> {
        self.require_exact_generation(&request.connection, request.generation)?;
        let key = PresenceKey {
            connection: request.connection.clone(),
            stream: request.stream.clone(),
        };
        let existing = self
            .entries
            .get(&key)
            .ok_or_else(|| PresenceError::PresenceNotJoined {
                connection: request.connection.clone(),
                stream: request.stream.clone(),
            })?;
        self.require_record_invariants(&key, existing)?;
        if existing.status == request.status && existing.hidden == request.hidden {
            return Ok(PresenceDelta::idempotent());
        }

        let next_revision = self.next_revision()?;
        let old = existing.clone();
        let mut record = old.clone();
        record.status = request.status;
        record.hidden = request.hidden;
        let mut joins = Vec::new();
        let mut updates = Vec::new();
        let mut leaves = Vec::new();
        let mut hidden_changes = 0usize;
        classify_visibility_change(
            old,
            record.clone(),
            &mut joins,
            &mut updates,
            &mut leaves,
            &mut hidden_changes,
        );
        self.entries.insert(key, record);
        self.revision = next_revision;
        debug_assert!(self.verify_invariants().is_ok());
        Ok(PresenceDelta::applied(
            next_revision,
            joins,
            updates,
            leaves,
            hidden_changes,
        ))
    }

    pub fn leave_presence(
        &mut self,
        request: LeavePresenceRequest,
    ) -> Result<PresenceDelta, PresenceError> {
        self.require_exact_generation(&request.connection, request.generation)?;
        let key = PresenceKey {
            connection: request.connection,
            stream: request.stream,
        };
        let Some(existing) = self.entries.get(&key) else {
            return Ok(PresenceDelta::idempotent());
        };
        self.require_record_invariants(&key, existing)?;
        let next_revision = self.next_revision()?;
        let removed = self
            .entries
            .remove(&key)
            .ok_or(PresenceError::InvariantViolation(
                "entry disappeared during leave",
            ))?;
        self.revision = next_revision;
        debug_assert!(self.verify_invariants().is_ok());
        if removed.hidden {
            Ok(PresenceDelta::applied(
                next_revision,
                Vec::new(),
                Vec::new(),
                Vec::new(),
                1,
            ))
        } else {
            Ok(PresenceDelta::applied(
                next_revision,
                Vec::new(),
                Vec::new(),
                vec![removed],
                0,
            ))
        }
    }

    pub fn remove_connection(
        &mut self,
        request: RemoveConnectionRequest,
    ) -> Result<PresenceDelta, PresenceError> {
        self.require_exact_generation(&request.connection, request.generation)?;
        let keys = self.keys_for_connection(&request.connection);
        if keys.is_empty() {
            return Ok(PresenceDelta::idempotent());
        }
        let next_revision = self.next_revision()?;
        let mut leaves = Vec::new();
        let mut hidden_changes = 0usize;
        for key in keys {
            let removed = self
                .entries
                .remove(&key)
                .ok_or(PresenceError::InvariantViolation(
                    "entry disappeared during connection removal",
                ))?;
            if removed.hidden {
                hidden_changes = hidden_changes.saturating_add(1);
            } else {
                leaves.push(removed);
            }
        }
        self.revision = next_revision;
        debug_assert!(self.verify_invariants().is_ok());
        Ok(PresenceDelta::applied(
            next_revision,
            Vec::new(),
            Vec::new(),
            leaves,
            hidden_changes,
        ))
    }

    pub fn snapshot(
        &self,
        stream: &StreamKey,
        visibility: SnapshotVisibility,
    ) -> Result<Vec<PresenceRecord>, PresenceError> {
        self.verify_invariants()?;
        let mut records: Vec<_> = self
            .entries
            .values()
            .filter(|record| {
                &record.stream == stream
                    && (visibility == SnapshotVisibility::IncludeHidden || !record.hidden)
            })
            .cloned()
            .collect();
        records.sort_by(|left, right| {
            (
                &left.identity.session_id,
                &left.connection.node_id,
                &left.connection.connection_id,
            )
                .cmp(&(
                    &right.identity.session_id,
                    &right.connection.node_id,
                    &right.connection.connection_id,
                ))
        });
        Ok(records)
    }

    pub fn verify_invariants(&self) -> Result<(), PresenceError> {
        let mut identities: BTreeMap<&ConnectionRef, &PresenceIdentity> = BTreeMap::new();
        for (key, record) in &self.entries {
            self.require_record_invariants(key, record)?;
            match identities.get(&record.connection) {
                Some(existing) if *existing != &record.identity => {
                    return Err(PresenceError::InvariantViolation(
                        "one connection generation contains multiple identities",
                    ));
                }
                Some(_) => {}
                None => {
                    identities.insert(&record.connection, &record.identity);
                }
            }
        }
        Ok(())
    }

    fn next_revision(&self) -> Result<u64, PresenceError> {
        self.revision
            .checked_add(1)
            .ok_or(PresenceError::RevisionExhausted)
    }

    fn require_exact_generation(
        &self,
        connection: &ConnectionRef,
        received: ConnectionGeneration,
    ) -> Result<(), PresenceError> {
        let Some(current) = self.generations.get(connection).copied() else {
            return Err(PresenceError::GenerationNotEstablished {
                connection: connection.clone(),
                received,
            });
        };
        if received < current {
            return Err(PresenceError::StaleGeneration {
                connection: connection.clone(),
                current,
                received,
            });
        }
        if received > current {
            return Err(PresenceError::GenerationAhead {
                connection: connection.clone(),
                current,
                received,
            });
        }
        Ok(())
    }

    fn require_record_invariants(
        &self,
        key: &PresenceKey,
        record: &PresenceRecord,
    ) -> Result<(), PresenceError> {
        if key.connection != record.connection || key.stream != record.stream {
            return Err(PresenceError::InvariantViolation(
                "presence key differs from stored record",
            ));
        }
        if self.generations.get(&record.connection) != Some(&record.generation) {
            return Err(PresenceError::InvariantViolation(
                "stored record generation differs from high-water generation",
            ));
        }
        Ok(())
    }

    fn identity_for_connection(
        &self,
        connection: &ConnectionRef,
    ) -> Result<Option<&PresenceIdentity>, PresenceError> {
        let mut identity = None;
        for (key, record) in &self.entries {
            if &key.connection != connection {
                continue;
            }
            self.require_record_invariants(key, record)?;
            match identity {
                Some(existing) if existing != &record.identity => {
                    return Err(PresenceError::InvariantViolation(
                        "connection contains inconsistent identities",
                    ));
                }
                Some(_) => {}
                None => identity = Some(&record.identity),
            }
        }
        Ok(identity)
    }

    fn keys_for_connection(&self, connection: &ConnectionRef) -> Vec<PresenceKey> {
        self.entries
            .keys()
            .filter(|key| &key.connection == connection)
            .cloned()
            .collect()
    }

    fn remove_connection_records(
        &mut self,
        connection: &ConnectionRef,
    ) -> Result<Vec<PresenceRecord>, PresenceError> {
        let keys = self.keys_for_connection(connection);
        let mut records = Vec::with_capacity(keys.len());
        for key in keys {
            records.push(
                self.entries
                    .remove(&key)
                    .ok_or(PresenceError::InvariantViolation(
                        "entry disappeared during generation advance",
                    ))?,
            );
        }
        Ok(records)
    }
}

fn classify_visibility_change(
    old: PresenceRecord,
    new: PresenceRecord,
    joins: &mut Vec<PresenceRecord>,
    updates: &mut Vec<PresenceRecord>,
    leaves: &mut Vec<PresenceRecord>,
    hidden_changes: &mut usize,
) {
    match (old.hidden, new.hidden) {
        (false, false) => updates.push(new),
        (false, true) => leaves.push(old),
        (true, false) => joins.push(new),
        (true, true) => *hidden_changes = hidden_changes.saturating_add(1),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{
        ConnectionId, NodeId, PresenceStatus, SessionId, UserId, Username,
    };

    fn connection(name: &str) -> ConnectionRef {
        ConnectionRef::new(
            NodeId::new("node-a").unwrap(),
            ConnectionId::new(name).unwrap(),
        )
    }

    fn generation(value: u64) -> ConnectionGeneration {
        ConnectionGeneration::new(value).unwrap()
    }

    fn stream(label: &str) -> StreamKey {
        StreamKey::new(1, [1; 16], [2; 16], label).unwrap()
    }

    fn identity(session: &str) -> PresenceIdentity {
        PresenceIdentity::new(
            UserId::new("user-a").unwrap(),
            SessionId::new(session).unwrap(),
            Username::new("alice").unwrap(),
        )
    }

    fn join(
        connection: ConnectionRef,
        generation: u64,
        stream: StreamKey,
        identity: PresenceIdentity,
        status: &str,
        hidden: bool,
    ) -> JoinPresenceRequest {
        JoinPresenceRequest {
            connection,
            generation: self::generation(generation),
            stream,
            identity,
            status: PresenceStatus::new(status).unwrap(),
            hidden,
        }
    }

    #[test]
    fn typed_join_is_applied_once_and_duplicate_is_idempotent() {
        let mut router = PresenceRouter::new();
        let request = join(
            connection("connection-a"),
            1,
            stream("match-a"),
            identity("session-a"),
            "ready",
            false,
        );
        let first = router.join_presence(request.clone()).unwrap();
        assert_eq!(first.disposition, MutationDisposition::Applied);
        assert_eq!(first.revision, Some(1));
        assert_eq!(first.joins.len(), 1);
        assert!(first.updates.is_empty());
        assert!(first.leaves.is_empty());
        assert_eq!(router.entry_count(), 1);

        let duplicate = router.join_presence(request).unwrap();
        assert_eq!(duplicate, PresenceDelta::idempotent());
        assert_eq!(router.revision(), 1);
        assert_eq!(router.entry_count(), 1);
    }

    #[test]
    fn same_generation_join_updates_status_without_identity_drift() {
        let mut router = PresenceRouter::new();
        let connection = connection("connection-a");
        let stream = stream("match-a");
        let identity = identity("session-a");
        router
            .join_presence(join(
                connection.clone(),
                1,
                stream.clone(),
                identity.clone(),
                "ready",
                false,
            ))
            .unwrap();
        let delta = router
            .join_presence(join(
                connection,
                1,
                stream,
                identity,
                "playing",
                false,
            ))
            .unwrap();
        assert_eq!(delta.updates.len(), 1);
        assert_eq!(delta.updates[0].status.as_str(), "playing");
        assert_eq!(router.revision(), 2);
    }

    #[test]
    fn visibility_transitions_map_to_public_leave_and_join() {
        let mut router = PresenceRouter::new();
        let connection = connection("connection-a");
        let stream = stream("match-a");
        router
            .join_presence(join(
                connection.clone(),
                1,
                stream.clone(),
                identity("session-a"),
                "ready",
                false,
            ))
            .unwrap();
        let hidden = router
            .update_presence(UpdatePresenceRequest {
                connection: connection.clone(),
                generation: generation(1),
                stream: stream.clone(),
                status: PresenceStatus::new("quiet").unwrap(),
                hidden: true,
            })
            .unwrap();
        assert_eq!(hidden.leaves.len(), 1);
        assert!(hidden.joins.is_empty());
        assert!(router
            .snapshot(&stream, SnapshotVisibility::PublicOnly)
            .unwrap()
            .is_empty());

        let visible = router
            .update_presence(UpdatePresenceRequest {
                connection,
                generation: generation(1),
                stream: stream.clone(),
                status: PresenceStatus::new("back").unwrap(),
                hidden: false,
            })
            .unwrap();
        assert_eq!(visible.joins.len(), 1);
        assert!(visible.leaves.is_empty());
        assert_eq!(
            router
                .snapshot(&stream, SnapshotVisibility::PublicOnly)
                .unwrap()
                .len(),
            1
        );
    }

    #[test]
    fn hidden_mutations_are_tracked_without_public_delta() {
        let mut router = PresenceRouter::new();
        let connection = connection("connection-a");
        let stream = stream("match-a");
        let joined = router
            .join_presence(join(
                connection.clone(),
                1,
                stream.clone(),
                identity("session-a"),
                "hidden",
                true,
            ))
            .unwrap();
        assert_eq!(joined.hidden_changes, 1);
        assert!(joined.joins.is_empty());
        assert!(router
            .snapshot(&stream, SnapshotVisibility::PublicOnly)
            .unwrap()
            .is_empty());
        assert_eq!(
            router
                .snapshot(&stream, SnapshotVisibility::IncludeHidden)
                .unwrap()
                .len(),
            1
        );

        let update = router
            .update_presence(UpdatePresenceRequest {
                connection,
                generation: generation(1),
                stream,
                status: PresenceStatus::new("still-hidden").unwrap(),
                hidden: true,
            })
            .unwrap();
        assert_eq!(update.hidden_changes, 1);
        assert!(update.joins.is_empty());
        assert!(update.updates.is_empty());
        assert!(update.leaves.is_empty());
    }

    #[test]
    fn stale_generation_is_rejected_and_high_water_survives_leave() {
        let mut router = PresenceRouter::new();
        let connection = connection("connection-a");
        let stream = stream("match-a");
        router
            .join_presence(join(
                connection.clone(),
                2,
                stream.clone(),
                identity("session-a"),
                "ready",
                false,
            ))
            .unwrap();
        router
            .leave_presence(LeavePresenceRequest {
                connection: connection.clone(),
                generation: generation(2),
                stream: stream.clone(),
            })
            .unwrap();
        assert_eq!(router.entry_count(), 0);
        assert_eq!(router.established_generation(&connection), Some(generation(2)));
        assert!(matches!(
            router.join_presence(join(
                connection,
                1,
                stream,
                identity("session-a"),
                "stale",
                false,
            )),
            Err(PresenceError::StaleGeneration { .. })
        ));
    }

    #[test]
    fn higher_generation_atomically_leaves_old_streams_and_joins_new_identity() {
        let mut router = PresenceRouter::new();
        let connection = connection("connection-a");
        let first = stream("match-a");
        let second = stream("party-a");
        router
            .join_presence(join(
                connection.clone(),
                1,
                first.clone(),
                identity("session-old"),
                "one",
                false,
            ))
            .unwrap();
        router
            .join_presence(join(
                connection.clone(),
                1,
                second,
                identity("session-old"),
                "two",
                false,
            ))
            .unwrap();
        let delta = router
            .join_presence(join(
                connection.clone(),
                2,
                first.clone(),
                identity("session-new"),
                "new",
                false,
            ))
            .unwrap();
        assert_eq!(delta.leaves.len(), 2);
        assert_eq!(delta.joins.len(), 1);
        assert_eq!(router.entry_count(), 1);
        assert_eq!(router.established_generation(&connection), Some(generation(2)));
        assert_eq!(
            router
                .snapshot(&first, SnapshotVisibility::PublicOnly)
                .unwrap()[0]
                .identity
                .session_id
                .as_str(),
            "session-new"
        );
    }

    #[test]
    fn same_generation_identity_conflict_is_atomic() {
        let mut router = PresenceRouter::new();
        let connection = connection("connection-a");
        let first = stream("match-a");
        router
            .join_presence(join(
                connection.clone(),
                1,
                first.clone(),
                identity("session-a"),
                "ready",
                false,
            ))
            .unwrap();
        let revision = router.revision();
        let error = router.join_presence(join(
            connection,
            1,
            stream("party-a"),
            identity("session-b"),
            "conflict",
            false,
        ));
        assert!(matches!(error, Err(PresenceError::IdentityConflict { .. })));
        assert_eq!(router.revision(), revision);
        assert_eq!(router.entry_count(), 1);
        assert_eq!(
            router
                .snapshot(&first, SnapshotVisibility::PublicOnly)
                .unwrap()
                .len(),
            1
        );
    }

    #[test]
    fn update_requires_join_and_cannot_establish_future_generation() {
        let mut router = PresenceRouter::new();
        let connection = connection("connection-a");
        let stream = stream("match-a");
        assert!(matches!(
            router.update_presence(UpdatePresenceRequest {
                connection: connection.clone(),
                generation: generation(1),
                stream: stream.clone(),
                status: PresenceStatus::new("missing").unwrap(),
                hidden: false,
            }),
            Err(PresenceError::GenerationNotEstablished { .. })
        ));
        router
            .join_presence(join(
                connection.clone(),
                1,
                stream.clone(),
                identity("session-a"),
                "ready",
                false,
            ))
            .unwrap();
        assert!(matches!(
            router.update_presence(UpdatePresenceRequest {
                connection,
                generation: generation(2),
                stream,
                status: PresenceStatus::new("ahead").unwrap(),
                hidden: false,
            }),
            Err(PresenceError::GenerationAhead { .. })
        ));
    }

    #[test]
    fn leave_and_remove_connection_are_idempotent_and_deterministic() {
        let mut router = PresenceRouter::new();
        let connection = connection("connection-a");
        let first = stream("a");
        let second = stream("b");
        for stream in [first.clone(), second] {
            router
                .join_presence(join(
                    connection.clone(),
                    1,
                    stream,
                    identity("session-a"),
                    "ready",
                    false,
                ))
                .unwrap();
        }
        let leave = router
            .leave_presence(LeavePresenceRequest {
                connection: connection.clone(),
                generation: generation(1),
                stream: first.clone(),
            })
            .unwrap();
        assert_eq!(leave.leaves.len(), 1);
        assert_eq!(
            router
                .leave_presence(LeavePresenceRequest {
                    connection: connection.clone(),
                    generation: generation(1),
                    stream: first,
                })
                .unwrap()
                .disposition,
            MutationDisposition::Idempotent
        );
        let removed = router
            .remove_connection(RemoveConnectionRequest {
                connection: connection.clone(),
                generation: generation(1),
            })
            .unwrap();
        assert_eq!(removed.leaves.len(), 1);
        assert_eq!(router.entry_count(), 0);
        assert_eq!(
            router
                .remove_connection(RemoveConnectionRequest {
                    connection,
                    generation: generation(1),
                })
                .unwrap()
                .disposition,
            MutationDisposition::Idempotent
        );
    }

    #[test]
    fn snapshots_sort_by_session_node_and_connection() {
        let mut router = PresenceRouter::new();
        let stream = stream("match-a");
        for (node, connection_id, session) in [
            ("node-b", "connection-b", "session-b"),
            ("node-a", "connection-c", "session-a"),
            ("node-a", "connection-a", "session-a"),
        ] {
            let connection = ConnectionRef::new(
                NodeId::new(node).unwrap(),
                ConnectionId::new(connection_id).unwrap(),
            );
            router
                .join_presence(join(
                    connection,
                    1,
                    stream.clone(),
                    identity(session),
                    "ready",
                    false,
                ))
                .unwrap();
        }
        let snapshot = router
            .snapshot(&stream, SnapshotVisibility::PublicOnly)
            .unwrap();
        let order: Vec<_> = snapshot
            .iter()
            .map(|record| {
                (
                    record.identity.session_id.as_str(),
                    record.connection.node_id.as_str(),
                    record.connection.connection_id.as_str(),
                )
            })
            .collect();
        assert_eq!(
            order,
            [
                ("session-a", "node-a", "connection-a"),
                ("session-a", "node-a", "connection-c"),
                ("session-b", "node-b", "connection-b"),
            ]
        );
    }
}
