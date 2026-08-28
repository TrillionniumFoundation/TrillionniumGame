use std::collections::BTreeMap;

use super::{PresenceDelta, PresenceError};
use crate::types::{
    ConnectionGeneration, ConnectionRef, JoinPresenceRequest, LeavePresenceRequest,
    PresenceIdentity, PresenceRecord, RemoveConnectionRequest, SnapshotVisibility, StreamKey,
    UpdatePresenceRequest,
};

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
struct PresenceKey {
    connection: ConnectionRef,
    stream: StreamKey,
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct ConnectionState {
    generation: ConnectionGeneration,
    identity: PresenceIdentity,
}

#[derive(Clone, Debug, Default)]
pub struct PresenceRouter {
    revision: u64,
    connections: BTreeMap<ConnectionRef, ConnectionState>,
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
        self.connections.len()
    }

    pub fn established_generation(
        &self,
        connection: &ConnectionRef,
    ) -> Option<ConnectionGeneration> {
        self.connections
            .get(connection)
            .map(|state| state.generation)
    }

    pub fn established_identity(&self, connection: &ConnectionRef) -> Option<&PresenceIdentity> {
        self.connections
            .get(connection)
            .map(|state| &state.identity)
    }

    pub fn join_presence(
        &mut self,
        request: JoinPresenceRequest,
    ) -> Result<PresenceDelta, PresenceError> {
        let current = self.connections.get(&request.connection).cloned();
        if let Some(state) = &current {
            if request.generation < state.generation {
                return Err(PresenceError::StaleGeneration {
                    connection: request.connection,
                    current: state.generation,
                    received: request.generation,
                });
            }
            if request.generation == state.generation && request.identity != state.identity {
                return Err(PresenceError::IdentityConflict {
                    connection: request.connection,
                    existing: Box::new(state.identity.clone()),
                    received: Box::new(request.identity),
                });
            }
        }

        let key = PresenceKey {
            connection: request.connection.clone(),
            stream: request.stream.clone(),
        };
        let generation_advance = current
            .as_ref()
            .map(|state| request.generation > state.generation)
            .unwrap_or(true);

        if !generation_advance {
            if let Some(existing) = self.entries.get(&key) {
                self.require_record_invariants(&key, existing)?;
                if existing.status == request.status && existing.hidden == request.hidden {
                    return Ok(PresenceDelta::idempotent());
                }
            }
        }

        // Validate the complete old generation before changing any state. This
        // makes generation replacement fail closed and atomic if stored state is
        // inconsistent.
        let retired_records = if generation_advance {
            self.records_for_connection(&request.connection)?
        } else {
            Vec::new()
        };
        let next_revision = self.next_revision()?;
        let mut joins = Vec::new();
        let mut updates = Vec::new();
        let mut leaves = Vec::new();
        let mut hidden_changes = 0usize;

        if generation_advance {
            for record in retired_records {
                let retired_key = PresenceKey {
                    connection: record.connection.clone(),
                    stream: record.stream.clone(),
                };
                let removed =
                    self.entries
                        .remove(&retired_key)
                        .ok_or(PresenceError::InvariantViolation(
                            "entry disappeared during generation advance",
                        ))?;
                if removed.hidden {
                    hidden_changes = hidden_changes.saturating_add(1);
                } else {
                    leaves.push(removed);
                }
            }
            self.connections.insert(
                request.connection.clone(),
                ConnectionState {
                    generation: request.generation,
                    identity: request.identity.clone(),
                },
            );
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
        for (key, record) in &self.entries {
            self.require_record_invariants(key, record)?;
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
        let Some(state) = self.connections.get(connection) else {
            return Err(PresenceError::GenerationNotEstablished {
                connection: connection.clone(),
                received,
            });
        };
        if received < state.generation {
            return Err(PresenceError::StaleGeneration {
                connection: connection.clone(),
                current: state.generation,
                received,
            });
        }
        if received > state.generation {
            return Err(PresenceError::GenerationAhead {
                connection: connection.clone(),
                current: state.generation,
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
        let Some(state) = self.connections.get(&record.connection) else {
            return Err(PresenceError::InvariantViolation(
                "stored record has no connection high-water state",
            ));
        };
        if state.generation != record.generation {
            return Err(PresenceError::InvariantViolation(
                "stored record generation differs from high-water generation",
            ));
        }
        if state.identity != record.identity {
            return Err(PresenceError::InvariantViolation(
                "record identity differs from generation-bound identity",
            ));
        }
        Ok(())
    }

    fn records_for_connection(
        &self,
        connection: &ConnectionRef,
    ) -> Result<Vec<PresenceRecord>, PresenceError> {
        let mut records = Vec::new();
        for (key, record) in &self.entries {
            if &key.connection != connection {
                continue;
            }
            self.require_record_invariants(key, record)?;
            records.push(record.clone());
        }
        Ok(records)
    }

    fn keys_for_connection(&self, connection: &ConnectionRef) -> Vec<PresenceKey> {
        self.entries
            .keys()
            .filter(|key| &key.connection == connection)
            .cloned()
            .collect()
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
