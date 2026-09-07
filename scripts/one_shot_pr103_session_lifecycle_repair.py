#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} anchor count={count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def append_before_last_brace(path: Path, addition: str) -> None:
    text = path.read_text(encoding="utf-8")
    index = text.rfind("\n}")
    if index < 0:
        raise SystemExit(f"{path}: final brace missing")
    path.write_text(text[:index] + addition + text[index:], encoding="utf-8")


def patch_session_registry() -> None:
    path = ROOT / "crates/trnm-presence-router-v2/src/session_registry.rs"
    generation_display = '''impl fmt::Display for SessionRouteGeneration {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.fmt(formatter)
    }
}
'''
    namespace_types = generation_display + r'''

#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct SessionRouteNamespace(u64);

impl SessionRouteNamespace {
    pub fn new(value: u64) -> Result<Self, SessionRouteError> {
        if value == 0 {
            return Err(SessionRouteError::InvalidNamespace);
        }
        Ok(Self(value))
    }

    pub const fn get(self) -> u64 {
        self.0
    }
}

impl fmt::Display for SessionRouteNamespace {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.fmt(formatter)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SessionRouteRolloverProof {
    pub retired_namespace: SessionRouteNamespace,
    pub next_namespace: SessionRouteNamespace,
    pub registry_revision: u64,
    pub router_revision: u64,
    pub tracked_connections: usize,
    pub tracked_sessions: usize,
    pub revocation_high_waters: usize,
    pub checkpoint_digest: [u8; 32],
    pub producer_barrier_digest: [u8; 32],
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SessionRouteCheckpoint {
    pub retired_namespace: SessionRouteNamespace,
    pub active_namespace: SessionRouteNamespace,
    pub registry_revision: u64,
    pub retired_router_revision: u64,
    pub retired_tracked_connections: usize,
    pub retired_tracked_sessions: usize,
    pub retired_revocation_high_waters: usize,
    pub checkpoint_digest: [u8; 32],
    pub producer_barrier_digest: [u8; 32],
}

pub trait SessionRouteCheckpointVerifier: fmt::Debug + Send + Sync {
    fn verify_rollover(&self, proof: &SessionRouteRolloverProof) -> bool;
    fn verify_checkpoint(&self, checkpoint: &SessionRouteCheckpoint) -> bool;
}
'''
    replace_once(path, generation_display, namespace_types, "namespace types")

    for name in (
        "SessionJoinRequest",
        "SessionUpdateRequest",
        "SessionLeaveRequest",
        "SessionRemoveConnectionRequest",
        "SessionRevocationRequest",
    ):
        replace_once(
            path,
            f"pub struct {name} {{\n",
            f"pub struct {name} {{\n    pub namespace: SessionRouteNamespace,\n",
            f"{name} namespace",
        )

    replace_once(
        path,
        "pub enum SessionRouteError {\n    InvalidSessionGeneration,",
        "pub enum SessionRouteError {\n"
        "    InvalidSessionGeneration,\n"
        "    InvalidNamespace,\n"
        "    NamespaceMismatch {\n"
        "        current: SessionRouteNamespace,\n"
        "        received: SessionRouteNamespace,\n"
        "    },\n"
        "    NamespaceNotAdvanced {\n"
        "        current: SessionRouteNamespace,\n"
        "        received: SessionRouteNamespace,\n"
        "    },\n"
        "    ZeroRolloverDigest(&'static str),\n"
        "    RolloverRequiresQuiescence,\n"
        "    RolloverProofMismatch,\n"
        "    RolloverVerificationFailed,\n"
        "    CheckpointVerificationFailed,",
        "session errors",
    )
    replace_once(
        path,
        "            Self::InvalidSessionGeneration => {\n"
        "                formatter.write_str(\"session route generation must be positive\")\n"
        "            }\n"
        "            Self::InvalidLimit {",
        "            Self::InvalidSessionGeneration => {\n"
        "                formatter.write_str(\"session route generation must be positive\")\n"
        "            }\n"
        "            Self::InvalidNamespace => {\n"
        "                formatter.write_str(\"session route namespace must be positive\")\n"
        "            }\n"
        "            Self::NamespaceMismatch { current, received } => write!(\n"
        "                formatter,\n"
        "                \"session route namespace {received} does not match active namespace {current}\"\n"
        "            ),\n"
        "            Self::NamespaceNotAdvanced { current, received } => write!(\n"
        "                formatter,\n"
        "                \"session route namespace must advance beyond {current}, not {received}\"\n"
        "            ),\n"
        "            Self::ZeroRolloverDigest(field) => {\n"
        "                write!(formatter, \"{field} must not be the zero digest\")\n"
        "            }\n"
        "            Self::RolloverRequiresQuiescence => formatter.write_str(\n"
        "                \"session route namespace rollover requires zero active bindings and entries\",\n"
        "            ),\n"
        "            Self::RolloverProofMismatch => {\n"
        "                formatter.write_str(\"session route rollover proof does not bind current state\")\n"
        "            }\n"
        "            Self::RolloverVerificationFailed => {\n"
        "                formatter.write_str(\"session route rollover proof was not accepted\")\n"
        "            }\n"
        "            Self::CheckpointVerificationFailed => {\n"
        "                formatter.write_str(\"session route checkpoint was not accepted\")\n"
        "            }\n"
        "            Self::InvalidLimit {",
        "session error display",
    )

    replace_once(
        path,
        "pub struct SessionRouteRegistry {\n    limits: SessionRouteLimits,\n    revision: u64,",
        "pub struct SessionRouteRegistry {\n"
        "    limits: SessionRouteLimits,\n"
        "    namespace: SessionRouteNamespace,\n"
        "    revision: u64,\n"
        "    checkpoint: Option<SessionRouteCheckpoint>,",
        "registry fields",
    )
    replace_once(
        path,
        "        Self {\n            limits,\n            revision: 0,",
        "        Self {\n"
        "            limits,\n"
        "            namespace: SessionRouteNamespace(1),\n"
        "            revision: 0,\n"
        "            checkpoint: None,",
        "registry initialization",
    )
    replace_once(
        path,
        "    pub const fn revision(&self) -> u64 {\n"
        "        self.revision\n"
        "    }\n\n"
        "    pub fn active_connection_count(&self) -> usize {",
        r'''    pub const fn revision(&self) -> u64 {
        self.revision
    }

    pub const fn namespace(&self) -> SessionRouteNamespace {
        self.namespace
    }

    pub const fn checkpoint(&self) -> Option<SessionRouteCheckpoint> {
        self.checkpoint
    }

    pub fn from_checkpoint(
        limits: SessionRouteLimits,
        checkpoint: SessionRouteCheckpoint,
        verifier: &dyn SessionRouteCheckpointVerifier,
    ) -> Result<Self, SessionRouteError> {
        if checkpoint.active_namespace <= checkpoint.retired_namespace {
            return Err(SessionRouteError::NamespaceNotAdvanced {
                current: checkpoint.retired_namespace,
                received: checkpoint.active_namespace,
            });
        }
        require_nonzero_rollover_digest(
            "checkpoint_digest",
            checkpoint.checkpoint_digest,
        )?;
        require_nonzero_rollover_digest(
            "producer_barrier_digest",
            checkpoint.producer_barrier_digest,
        )?;
        if !verifier.verify_checkpoint(&checkpoint) {
            return Err(SessionRouteError::CheckpointVerificationFailed);
        }
        Ok(Self {
            limits,
            namespace: checkpoint.active_namespace,
            revision: checkpoint.registry_revision,
            checkpoint: Some(checkpoint),
            router: PresenceRouter::new(),
            bindings: BTreeMap::new(),
            connections_by_session: BTreeMap::new(),
            revoked_through: BTreeMap::new(),
        })
    }

    pub fn propose_rollover(
        &self,
        next_namespace: SessionRouteNamespace,
        checkpoint_digest: [u8; 32],
        producer_barrier_digest: [u8; 32],
    ) -> Result<SessionRouteRolloverProof, SessionRouteError> {
        self.verify_invariants()?;
        if next_namespace <= self.namespace {
            return Err(SessionRouteError::NamespaceNotAdvanced {
                current: self.namespace,
                received: next_namespace,
            });
        }
        require_nonzero_rollover_digest("checkpoint_digest", checkpoint_digest)?;
        require_nonzero_rollover_digest(
            "producer_barrier_digest",
            producer_barrier_digest,
        )?;
        Ok(SessionRouteRolloverProof {
            retired_namespace: self.namespace,
            next_namespace,
            registry_revision: self.revision,
            router_revision: self.router.revision(),
            tracked_connections: self.tracked_connection_count(),
            tracked_sessions: self.tracked_session_count(),
            revocation_high_waters: self.revocation_high_water_count(),
            checkpoint_digest,
            producer_barrier_digest,
        })
    }

    pub fn rollover_namespace(
        &mut self,
        proof: SessionRouteRolloverProof,
        verifier: &dyn SessionRouteCheckpointVerifier,
    ) -> Result<SessionRouteCheckpoint, SessionRouteError> {
        self.verify_invariants()?;
        if !self.bindings.is_empty()
            || !self.connections_by_session.is_empty()
            || self.router.entry_count() != 0
        {
            return Err(SessionRouteError::RolloverRequiresQuiescence);
        }
        let expected = self.propose_rollover(
            proof.next_namespace,
            proof.checkpoint_digest,
            proof.producer_barrier_digest,
        )?;
        if proof != expected {
            return Err(SessionRouteError::RolloverProofMismatch);
        }
        if !verifier.verify_rollover(&proof) {
            return Err(SessionRouteError::RolloverVerificationFailed);
        }
        let next_revision = self.next_revision()?;
        let checkpoint = SessionRouteCheckpoint {
            retired_namespace: proof.retired_namespace,
            active_namespace: proof.next_namespace,
            registry_revision: next_revision,
            retired_router_revision: proof.router_revision,
            retired_tracked_connections: proof.tracked_connections,
            retired_tracked_sessions: proof.tracked_sessions,
            retired_revocation_high_waters: proof.revocation_high_waters,
            checkpoint_digest: proof.checkpoint_digest,
            producer_barrier_digest: proof.producer_barrier_digest,
        };
        self.namespace = proof.next_namespace;
        self.revision = next_revision;
        self.checkpoint = Some(checkpoint);
        self.router = PresenceRouter::new();
        self.revoked_through.clear();
        self.verify_invariants()?;
        Ok(checkpoint)
    }

    pub fn active_connection_count(&self) -> usize {''',
        "checkpoint API",
    )

    replace_once(
        path,
        "        self.verify_invariants()?;\n"
        "        let connection = request.presence.connection.clone();",
        "        self.verify_invariants()?;\n"
        "        self.require_namespace(request.namespace)?;\n"
        "        let connection = request.presence.connection.clone();",
        "join namespace validation",
    )
    for method, type_name in (
        ("update_presence", "SessionUpdateRequest"),
        ("leave_presence", "SessionLeaveRequest"),
        ("remove_connection", "SessionRemoveConnectionRequest"),
    ):
        old = (
            f"        let {type_name} {{\n"
            "            presence,\n"
            "            session_id,\n"
            "            session_generation,\n"
            "        } = request;\n"
            "        let connection = presence.connection.clone();"
        )
        new = (
            f"        let {type_name} {{\n"
            "            namespace,\n"
            "            presence,\n"
            "            session_id,\n"
            "            session_generation,\n"
            "        } = request;\n"
            "        self.require_namespace(namespace)?;\n"
            "        let connection = presence.connection.clone();"
        )
        replace_once(path, old, new, f"{method} namespace validation")
    replace_once(
        path,
        "        self.verify_invariants()?;\n"
        "        if self\n"
        "            .revoked_through\n"
        "            .get(&request.session_id)",
        "        self.verify_invariants()?;\n"
        "        self.require_namespace(request.namespace)?;\n"
        "        if self\n"
        "            .revoked_through\n"
        "            .get(&request.session_id)",
        "revoke namespace validation",
    )

    replace_once(
        path,
        "    pub fn verify_invariants(&self) -> Result<(), SessionRouteError> {\n"
        "        self.router.verify_invariants()?;",
        "    pub fn verify_invariants(&self) -> Result<(), SessionRouteError> {\n"
        "        self.router.verify_invariants()?;\n"
        "        if let Some(checkpoint) = self.checkpoint {\n"
        "            if checkpoint.active_namespace != self.namespace\n"
        "                || checkpoint.active_namespace <= checkpoint.retired_namespace\n"
        "                || checkpoint.registry_revision > self.revision\n"
        "                || checkpoint.checkpoint_digest.iter().all(|byte| *byte == 0)\n"
        "                || checkpoint.producer_barrier_digest.iter().all(|byte| *byte == 0)\n"
        "            {\n"
        "                return Err(SessionRouteError::InvariantViolation(\n"
        "                    \"checkpoint does not bind active namespace\",\n"
        "                ));\n"
        "            }\n"
        "        }",
        "checkpoint invariants",
    )

    capacity_start = "    fn require_join_capacity(&self, request: &SessionJoinRequest) -> Result<(), SessionRouteError> {\n"
    capacity_end = "    fn require_tracked_session_capacity(\n"
    text = path.read_text(encoding="utf-8")
    if text.count(capacity_start) != 1 or text.count(capacity_end) != 1:
        raise SystemExit("join capacity block anchors drift")
    before, tail = text.split(capacity_start, 1)
    _, after = tail.split(capacity_end, 1)
    capacity_impl = r'''    fn require_join_capacity(&self, request: &SessionJoinRequest) -> Result<(), SessionRouteError> {
        let connection = &request.presence.connection;
        let session_id = &request.presence.identity.session_id;
        let established_generation = self.router.established_generation(connection);
        let generation_advance = established_generation
            .map(|current| request.presence.generation > current)
            .unwrap_or(true);

        let projected_active_connections = self
            .bindings
            .len()
            .checked_add(usize::from(!self.bindings.contains_key(connection)))
            .ok_or(SessionRouteError::InvariantViolation(
                "active connection projection overflow",
            ))?;
        if projected_active_connections > self.limits.max_active_connections {
            return Err(SessionRouteError::ResourceExhausted {
                resource: "active_connections",
                limit: self.limits.max_active_connections,
            });
        }

        let projected_tracked_connections = self
            .router
            .connection_count()
            .checked_add(usize::from(established_generation.is_none()))
            .ok_or(SessionRouteError::InvariantViolation(
                "tracked connection projection overflow",
            ))?;
        if projected_tracked_connections > self.limits.max_tracked_connections {
            return Err(SessionRouteError::ResourceExhausted {
                resource: "tracked_connections",
                limit: self.limits.max_tracked_connections,
            });
        }

        let projected_tracked_sessions = self.projected_tracked_session_count(
            connection,
            session_id,
            generation_advance,
        )?;
        if projected_tracked_sessions > self.limits.max_tracked_sessions {
            return Err(SessionRouteError::ResourceExhausted {
                resource: "tracked_sessions",
                limit: self.limits.max_tracked_sessions,
            });
        }

        self.require_session_capacity(connection, session_id)?;
        self.require_presence_capacity(&request.presence, generation_advance)
    }

    fn projected_tracked_session_count(
        &self,
        connection: &ConnectionRef,
        session_id: &SessionId,
        generation_advance: bool,
    ) -> Result<usize, SessionRouteError> {
        let target_already_tracked = self.connections_by_session.contains_key(session_id)
            || self.revoked_through.contains_key(session_id);
        let mut projected = self
            .tracked_session_count()
            .checked_add(usize::from(!target_already_tracked))
            .ok_or(SessionRouteError::InvariantViolation(
                "tracked session projection overflow",
            ))?;
        if generation_advance {
            if let Some(previous) = self.bindings.get(connection) {
                let previous_is_exclusively_replaced = previous.session_id != *session_id
                    && !self.revoked_through.contains_key(&previous.session_id)
                    && self
                        .connections_by_session
                        .get(&previous.session_id)
                        .is_some_and(|connections| {
                            connections.len() == 1 && connections.contains(connection)
                        });
                if previous_is_exclusively_replaced {
                    projected = projected.checked_sub(1).ok_or(
                        SessionRouteError::InvariantViolation(
                            "tracked session replacement projection underflow",
                        ),
                    )?;
                }
            }
        }
        Ok(projected)
    }

'''
    path.write_text(before + capacity_impl + capacity_end + after, encoding="utf-8")

    old_presence = r'''    fn require_presence_capacity(
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
'''
    new_presence = r'''    fn require_presence_capacity(
        &self,
        request: &JoinPresenceRequest,
        generation_advance: bool,
    ) -> Result<(), SessionRouteError> {
        let retired_entries = if generation_advance
            && self.router.established_generation(&request.connection).is_some()
        {
            self.router.entry_count_for_connection(&request.connection)?
        } else {
            0
        };
        let already_joined = !generation_advance
            && self
                .router
                .snapshot(&request.stream, SnapshotVisibility::IncludeHidden)?
                .iter()
                .any(|record| {
                    record.connection == request.connection
                        && record.generation == request.generation
                });
        let projected = self
            .router
            .entry_count()
            .checked_sub(retired_entries)
            .and_then(|value| value.checked_add(usize::from(!already_joined)))
            .ok_or(SessionRouteError::InvariantViolation(
                "presence entry projection overflow or underflow",
            ))?;
        if projected > self.limits.max_presence_entries {
            Err(SessionRouteError::ResourceExhausted {
                resource: "presence_entries",
                limit: self.limits.max_presence_entries,
            })
        } else {
            Ok(())
        }
    }

    fn require_namespace(
        &self,
        received: SessionRouteNamespace,
    ) -> Result<(), SessionRouteError> {
        if received != self.namespace {
            Err(SessionRouteError::NamespaceMismatch {
                current: self.namespace,
                received,
            })
        } else {
            Ok(())
        }
    }
'''
    replace_once(path, old_presence, new_presence, "projected presence capacity")

    replace_once(
        path,
        "fn validate_limit(\n",
        "fn require_nonzero_rollover_digest(\n"
        "    field: &'static str,\n"
        "    digest: [u8; 32],\n"
        ") -> Result<(), SessionRouteError> {\n"
        "    if digest.iter().all(|byte| *byte == 0) {\n"
        "        Err(SessionRouteError::ZeroRolloverDigest(field))\n"
        "    } else {\n"
        "        Ok(())\n"
        "    }\n"
        "}\n\n"
        "fn validate_limit(\n",
        "rollover digest helper",
    )

    # Existing test helpers remain namespace 1 by default.
    for struct_name in (
        "SessionJoinRequest",
        "SessionUpdateRequest",
        "SessionLeaveRequest",
        "SessionRemoveConnectionRequest",
        "SessionRevocationRequest",
    ):
        replace_once(
            path,
            f"        {struct_name} {{\n",
            f"        {struct_name} {{\n"
            "            namespace: SessionRouteNamespace::new(1).expect(\"namespace\"),\n",
            f"test helper {struct_name} namespace",
        )

    addition = r'''

    #[derive(Debug)]
    struct ExactCheckpointVerifier(u8);

    impl SessionRouteCheckpointVerifier for ExactCheckpointVerifier {
        fn verify_rollover(&self, proof: &SessionRouteRolloverProof) -> bool {
            proof.checkpoint_digest[0] == self.0
                && proof.producer_barrier_digest[0] == self.0
        }

        fn verify_checkpoint(&self, checkpoint: &SessionRouteCheckpoint) -> bool {
            checkpoint.checkpoint_digest[0] == self.0
                && checkpoint.producer_barrier_digest[0] == self.0
        }
    }

    fn join_in_namespace(
        namespace: SessionRouteNamespace,
        connection_id: &str,
        connection_generation: u64,
        session_id: &str,
        session_generation: u64,
        label: &str,
    ) -> SessionJoinRequest {
        let mut request = join(
            connection_id,
            connection_generation,
            session_id,
            session_generation,
            label,
            false,
        );
        request.namespace = namespace;
        request
    }

    #[test]
    fn full_presence_capacity_allows_same_connection_generation_takeover() {
        let limits = SessionRouteLimits::new(1, 1, 1, 1, 1, 1).unwrap();
        let mut registry = SessionRouteRegistry::with_limits(limits);
        registry
            .join_presence(join("connection-a", 1, "session-a", 1, "room", false))
            .unwrap();
        registry
            .join_presence(join("connection-a", 2, "session-a", 1, "room", false))
            .expect("replacement does not increase occupancy");
        assert_eq!(registry.active_connection_count(), 1);
        assert_eq!(registry.presence_entry_count(), 1);
        registry.verify_invariants().unwrap();
    }

    #[test]
    fn full_presence_capacity_allows_multi_stream_to_one_replacement() {
        let limits = SessionRouteLimits::new(1, 1, 1, 1, 1, 2).unwrap();
        let mut registry = SessionRouteRegistry::with_limits(limits);
        registry
            .join_presence(join("connection-a", 1, "session-a", 1, "one", false))
            .unwrap();
        registry
            .join_presence(join("connection-a", 1, "session-a", 1, "two", false))
            .unwrap();
        assert_eq!(registry.presence_entry_count(), 2);
        registry
            .join_presence(join("connection-a", 2, "session-a", 1, "three", false))
            .expect("new generation atomically retires both old streams");
        assert_eq!(registry.presence_entry_count(), 1);
        registry.verify_invariants().unwrap();
    }

    #[test]
    fn tracked_session_capacity_uses_projected_exclusive_replacement() {
        let limits = SessionRouteLimits::new(1, 1, 1, 1, 1, 1).unwrap();
        let mut registry = SessionRouteRegistry::with_limits(limits);
        registry
            .join_presence(join("connection-a", 1, "session-a", 1, "room", false))
            .unwrap();
        registry
            .join_presence(join("connection-a", 2, "session-b", 1, "room", false))
            .expect("exclusive old session is retired by the same atomic takeover");
        assert_eq!(registry.tracked_session_count(), 1);
        assert_eq!(registry.active_connections_for_session(
            &SessionId::new("session-b").unwrap()
        ), 1);
    }

    #[test]
    fn shared_session_is_not_subtracted_from_projected_inventory() {
        let limits = SessionRouteLimits::new(2, 2, 2, 1, 1, 2).unwrap();
        let mut registry = SessionRouteRegistry::with_limits(limits);
        registry
            .join_presence(join("connection-a", 1, "session-a", 1, "one", false))
            .unwrap();
        registry
            .join_presence(join("connection-b", 1, "session-a", 1, "two", false))
            .unwrap();
        let revision = registry.revision();
        assert_eq!(
            registry.join_presence(join(
                "connection-a",
                2,
                "session-b",
                1,
                "three",
                false,
            )),
            Err(SessionRouteError::ResourceExhausted {
                resource: "tracked_sessions",
                limit: 1,
            })
        );
        assert_eq!(registry.revision(), revision);
        assert_eq!(registry.tracked_session_count(), 1);
    }

    #[test]
    fn verified_quiescent_rollover_recovers_identity_capacity_and_fences_old_messages() {
        let limits = SessionRouteLimits::new(1, 1, 1, 1, 1, 1).unwrap();
        let mut registry = SessionRouteRegistry::with_limits(limits);
        let namespace_one = registry.namespace();
        registry
            .join_presence(join("connection-a", 1, "session-a", 1, "room", false))
            .unwrap();
        registry
            .remove_connection(remove("connection-a", 1, "session-a", 1))
            .unwrap();
        registry.revoke_session(revoke("session-a", 1)).unwrap();
        assert!(matches!(
            registry.join_presence(join("connection-b", 1, "session-b", 1, "room", false)),
            Err(SessionRouteError::ResourceExhausted { .. })
        ));

        let namespace_two = SessionRouteNamespace::new(2).unwrap();
        let proof = registry
            .propose_rollover(namespace_two, [7; 32], [7; 32])
            .unwrap();
        let checkpoint = registry
            .rollover_namespace(proof, &ExactCheckpointVerifier(7))
            .unwrap();
        assert_eq!(registry.namespace(), namespace_two);
        assert_eq!(registry.tracked_connection_count(), 0);
        assert_eq!(registry.revocation_high_water_count(), 0);
        assert_eq!(registry.checkpoint(), Some(checkpoint));

        let old = join_in_namespace(
            namespace_one,
            "connection-a",
            2,
            "session-a",
            2,
            "room",
        );
        assert_eq!(
            registry.join_presence(old),
            Err(SessionRouteError::NamespaceMismatch {
                current: namespace_two,
                received: namespace_one,
            })
        );
        registry
            .join_presence(join_in_namespace(
                namespace_two,
                "connection-b",
                1,
                "session-b",
                1,
                "room",
            ))
            .expect("new namespace reopens a bounded identity window");

        registry
            .remove_connection(SessionRemoveConnectionRequest {
                namespace: namespace_two,
                presence: RemoveConnectionRequest {
                    connection: connection("connection-b"),
                    generation: ConnectionGeneration::new(1).unwrap(),
                },
                session_id: SessionId::new("session-b").unwrap(),
                session_generation: SessionRouteGeneration::new(1).unwrap(),
            })
            .unwrap();
        let restored = SessionRouteRegistry::from_checkpoint(
            limits,
            checkpoint,
            &ExactCheckpointVerifier(7),
        )
        .unwrap();
        assert_eq!(restored.namespace(), namespace_two);
        assert_eq!(restored.revision(), checkpoint.registry_revision);
    }

    #[test]
    fn rollover_rejects_active_or_unverified_state_without_mutation() {
        let limits = SessionRouteLimits::new(1, 1, 1, 1, 1, 1).unwrap();
        let mut registry = SessionRouteRegistry::with_limits(limits);
        registry
            .join_presence(join("connection-a", 1, "session-a", 1, "room", false))
            .unwrap();
        let next = SessionRouteNamespace::new(2).unwrap();
        let proof = registry.propose_rollover(next, [7; 32], [7; 32]).unwrap();
        let revision = registry.revision();
        assert_eq!(
            registry.rollover_namespace(proof, &ExactCheckpointVerifier(7)),
            Err(SessionRouteError::RolloverRequiresQuiescence)
        );
        assert_eq!(registry.revision(), revision);

        registry
            .remove_connection(remove("connection-a", 1, "session-a", 1))
            .unwrap();
        let proof = registry.propose_rollover(next, [7; 32], [7; 32]).unwrap();
        let revision = registry.revision();
        assert_eq!(
            registry.rollover_namespace(proof, &ExactCheckpointVerifier(8)),
            Err(SessionRouteError::RolloverVerificationFailed)
        );
        assert_eq!(registry.revision(), revision);
        assert_eq!(registry.namespace(), SessionRouteNamespace::new(1).unwrap());
    }
'''
    append_before_last_brace(path, addition)


def patch_router() -> None:
    path = ROOT / "crates/trnm-presence-router-v2/src/router/state.rs"
    replace_once(
        path,
        "    pub fn connection_count(&self) -> usize {\n"
        "        self.connections.len()\n"
        "    }\n\n"
        "    pub fn established_generation(",
        "    pub fn connection_count(&self) -> usize {\n"
        "        self.connections.len()\n"
        "    }\n\n"
        "    pub fn entry_count_for_connection(\n"
        "        &self,\n"
        "        connection: &ConnectionRef,\n"
        "    ) -> Result<usize, PresenceError> {\n"
        "        Ok(self.records_for_connection(connection)?.len())\n"
        "    }\n\n"
        "    pub fn established_generation(",
        "router projected count API",
    )


def patch_exports() -> None:
    path = ROOT / "crates/trnm-presence-router-v2/src/lib.rs"
    replace_once(
        path,
        "    SessionRevocationDelta, SessionRevocationRequest, SessionRouteError, SessionRouteGeneration,\n"
        "    SessionRouteLimits, SessionRouteRegistry, SessionUpdateRequest, DEFAULT_MAX_ACTIVE_CONNECTIONS,",
        "    SessionRevocationDelta, SessionRevocationRequest, SessionRouteCheckpoint,\n"
        "    SessionRouteCheckpointVerifier, SessionRouteError, SessionRouteGeneration,\n"
        "    SessionRouteLimits, SessionRouteNamespace, SessionRouteRegistry,\n"
        "    SessionRouteRolloverProof, SessionUpdateRequest, DEFAULT_MAX_ACTIVE_CONNECTIONS,",
        "session lifecycle exports",
    )


def patch_readme() -> None:
    path = ROOT / "crates/trnm-presence-router-v2/README.md"
    replace_once(
        path,
        "- exact session ID and session-generation binding for every mutating operation;\n",
        "- exact session ID, session-generation and route-namespace binding for every mutating operation;\n",
        "README responsibilities",
    )
    replace_once(
        path,
        "`PresenceRouter` retains connection-generation high-water state after route removal. `SessionRouteRegistry` consequently has separate active-connection and tracked-connection limits. Removing a connection releases active capacity, while a new connection identity is still rejected once the tracked-connection universe is full. Reusing the same admitted identity requires a strictly newer connection generation.\n\n"
        "Revocation high-waters are security state and are not opportunistically evicted. Their configured hard limit is a finite admitted-session universe. Deployments requiring compaction must first establish a durable external generation floor and add a separately reviewed compaction protocol; this source candidate deliberately fails closed instead of deleting a fence.\n",
        "`PresenceRouter` retains connection-generation high-water state after route removal. `SessionRouteRegistry` consequently has separate active-connection and tracked-connection limits. Removing a connection releases active capacity, while a new connection identity is still rejected once the tracked-connection universe is full. Reusing the same admitted identity requires a strictly newer connection generation. Capacity admission computes the checked post-replacement inventory, so a higher generation that atomically retires old streams or an exclusively owned old session is accepted when it does not increase the bounded state.\n\n"
        "Revocation and connection high-waters are never opportunistically evicted. Long-lived deployments may compact only through a quiescent namespace rollover: every mutation carries the active `SessionRouteNamespace`; a trusted verifier must accept a proof binding the current registry/router revisions, retained cardinalities, a durable checkpoint digest and a producer-barrier digest; active bindings and presence entries must be zero. The rollover advances the namespace monotonically, emits a restartable `SessionRouteCheckpoint`, resets the bounded high-water window, and permanently rejects delayed messages from retired namespaces.\n",
        "README compaction contract",
    )
    replace_once(
        path,
        "`SessionJoinRequest` binds a `JoinPresenceRequest` to a positive `SessionRouteGeneration`.\n",
        "`SessionJoinRequest` binds a `JoinPresenceRequest` to a positive `SessionRouteGeneration` and the exact active `SessionRouteNamespace`.\n",
        "README public join",
    )
    replace_once(
        path,
        "Capacity checks run before cloning or allocating a candidate. Rejected stale, conflicting, ahead-of-current, revoked, or exhausted operations leave route state, indexes, revisions, and deltas unchanged.\n\n"
        "The implementation is an in-memory state machine. Process restart, durable replay, cross-node ordering, and network partitions remain adapter-level obligations.\n",
        "Capacity checks use checked projected post-transition cardinalities before cloning or allocating a candidate. Rejected stale, conflicting, ahead-of-current, revoked, exhausted, wrong-namespace or unverified rollover operations leave route state, indexes, revisions and deltas unchanged.\n\n"
        "The implementation remains an in-memory state machine, but namespace retirement now has an explicit durable handoff: adapters must persist the accepted checkpoint and producer barrier and must verify the latest checkpoint before restart. Cross-node barrier construction, storage durability, ordering and network partitions remain adapter-level obligations.\n",
        "README correctness",
    )
    replace_once(
        path,
        "- no mutation on rejection and safe active-capacity reuse inside the admitted identity universe;\n"
        "- invalid or incoherent limit configuration.\n",
        "- no mutation on rejection and safe active-capacity reuse inside the admitted identity universe;\n"
        "- full-capacity same-connection generation takeover and multi-stream-to-one replacement;\n"
        "- exclusive-session replacement without false tracked-session exhaustion;\n"
        "- verified quiescent namespace rollover, restart restore and stale-namespace rejection;\n"
        "- invalid, incoherent or unverified limit/rollover configuration.\n",
        "README tests",
    )


def main() -> None:
    patch_session_registry()
    patch_router()
    patch_exports()
    patch_readme()


if __name__ == "__main__":
    main()
