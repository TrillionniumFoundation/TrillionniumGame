#![forbid(unsafe_code)]
#![deny(missing_debug_implementations)]

//! Typed, generation-fenced realtime ownership and recovery controls.
//!
//! Public mutation APIs accept validated request objects rather than positional
//! argument lists. A connection generation is established only by a join;
//! updates, leaves and removals must match the exact established generation.
//! Higher-generation joins atomically retire every old record for the
//! connection before publishing the new presence. Connection actors, replay
//! cursors and disconnect effects are separately bounded and fail closed.

mod connection_actor;
mod disconnect_journal;
mod reconnect_cursor;
mod router;
mod types;

pub use connection_actor::{
    ConnectionActor, ConnectionActorConfig, ConnectionActorError, ConnectionActorState,
    CorrelationId, InboundFrame, OutboundFrame, PendingRequest,
};
pub use disconnect_journal::{
    DisconnectIntentId, DisconnectJournal, DisconnectJournalError, DisconnectOperation,
    DisconnectReceipt, DisconnectRecord, DisconnectState, LeaseToken, RetryDisposition, WorkerId,
};
pub use reconnect_cursor::{
    ReconnectCursor, ReconnectEvent, ReconnectJournal, ReconnectJournalConfig,
    ReconnectJournalError,
};
pub use router::{MutationDisposition, PresenceDelta, PresenceError, PresenceRouter};
pub use types::{
    ConnectionGeneration, ConnectionId, ConnectionRef, JoinPresenceRequest, LeavePresenceRequest,
    NodeId, PresenceIdentity, PresenceRecord, PresenceStatus, RemoveConnectionRequest, SessionId,
    SnapshotVisibility, StreamKey, UpdatePresenceRequest, UserId, Username, ValidationError,
    MAX_CONNECTION_ID_BYTES, MAX_NODE_ID_BYTES, MAX_SESSION_ID_BYTES, MAX_STATUS_BYTES,
    MAX_STREAM_LABEL_BYTES, MAX_USERNAME_BYTES, MAX_USER_ID_BYTES,
};
