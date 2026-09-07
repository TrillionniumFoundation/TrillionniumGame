#![forbid(unsafe_code)]
#![deny(missing_debug_implementations)]

//! Strict bounded realtime routing primitives.
//!
//! Connection-owned presence records are generation fenced. The optional
//! session registry composes those records with a monotonic session-generation
//! revocation high-water so logout, credential reset, refresh replay, or an
//! administrator revocation can retire every bound socket without permitting a
//! stale generation to rejoin.

mod router;
mod session_registry;
mod types;

pub use router::{MutationDisposition, PresenceDelta, PresenceError, PresenceRouter};
pub use session_registry::{
    SessionJoinRequest, SessionLeaveRequest, SessionMutationDelta, SessionRemoveConnectionRequest,
    SessionRevocationDelta, SessionRevocationRequest, SessionRouteError, SessionRouteGeneration,
    SessionRouteLimits, SessionRouteRegistry, SessionUpdateRequest, DEFAULT_MAX_ACTIVE_CONNECTIONS,
    DEFAULT_MAX_PRESENCE_ENTRIES, DEFAULT_MAX_REVOCATION_HIGH_WATERS,
    DEFAULT_MAX_TRACKED_CONNECTIONS, DEFAULT_MAX_TRACKED_SESSIONS, MAX_ACTIVE_CONNECTIONS,
    MAX_CONNECTIONS_PER_SESSION, MAX_PRESENCE_ENTRIES, MAX_REVOCATION_HIGH_WATERS,
    MAX_TRACKED_CONNECTIONS, MAX_TRACKED_SESSIONS,
};
pub use types::{
    ConnectionGeneration, ConnectionId, ConnectionRef, JoinPresenceRequest, LeavePresenceRequest,
    NodeId, PresenceIdentity, PresenceRecord, PresenceStatus, RemoveConnectionRequest, SessionId,
    SnapshotVisibility, StreamKey, UpdatePresenceRequest, UserId, Username, ValidationError,
    MAX_CONNECTION_ID_BYTES, MAX_NODE_ID_BYTES, MAX_SESSION_ID_BYTES, MAX_STATUS_BYTES,
    MAX_STREAM_LABEL_BYTES, MAX_USERNAME_BYTES, MAX_USER_ID_BYTES,
};
