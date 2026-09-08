#![forbid(unsafe_code)]
#![deny(missing_debug_implementations)]

//! Strict bounded realtime routing, ownership and recovery primitives.
//!
//! Connection-owned presence records are generation fenced. The session
//! registry composes them with a monotonic session-generation revocation
//! high-water. Connection actors, authenticated replay cursors and reconciled
//! disconnect effects are independently bounded and fail closed across
//! response loss, reconnect, drain and stale-generation attempts. Disconnect
//! admission also reserves a checked epoch-wide verifier-receipt budget; each
//! accepted receipt consumes its reservation before owner-map mutation, so
//! retries cannot multiply retained evidence beyond the configured hard cap.

mod connection_actor;
mod disconnect_journal;
mod reconnect_cursor;
mod router;
mod session_registry;
mod types;

pub use connection_actor::{
    ConnectionActor, ConnectionActorConfig, ConnectionActorError, ConnectionActorState,
    CorrelationId, InboundFrame, OutboundFrame, PendingRequest, RequestHandle,
    MAX_CONNECTION_ACTOR_BUFFER_BYTES, MAX_CONNECTION_ACTOR_FRAME_BYTES,
    MAX_CONNECTION_ACTOR_QUEUE_ITEMS,
};
pub use disconnect_journal::{
    DisconnectArchiveTombstone, DisconnectDispatchBinding, DisconnectIntentId, DisconnectJournal,
    DisconnectJournalConfig, DisconnectJournalEpoch, DisconnectJournalError, DisconnectJournalId,
    DisconnectOperation, DisconnectOutcomeEvidence, DisconnectOutcomeKind,
    DisconnectOutcomeVerifier, DisconnectRecord, DisconnectState, DisconnectUnknownEvidence,
    LeaseToken, ReconciliationDisposition, RetryDisposition, WorkerId,
    DISCONNECT_VERIFIER_RECEIPTS_PER_ATTEMPT, MAX_DISCONNECT_ACTIVE_RECORDS,
    MAX_DISCONNECT_ATTEMPTS, MAX_DISCONNECT_TOMBSTONES, MAX_DISCONNECT_VERIFIER_RECEIPTS,
};
pub use reconnect_cursor::{
    ReconnectCursor, ReconnectCursorAuthenticator, ReconnectEvent, ReconnectIdentity,
    ReconnectJournal, ReconnectJournalConfig, ReconnectJournalError, ReconnectJournalId,
    ReconnectProducerEpoch, ReconnectSessionGeneration, ReconnectStreamId,
    MAX_RECONNECT_JOURNAL_CAPACITY,
};
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
