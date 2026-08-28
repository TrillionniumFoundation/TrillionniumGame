#![forbid(unsafe_code)]
#![deny(missing_debug_implementations)]

//! Typed, monotonic-generation presence routing core.
//!
//! Public mutation APIs accept validated request objects rather than positional
//! argument lists. A connection generation is established only by a join;
//! updates, leaves and removals must match the exact established generation.
//! Higher-generation joins atomically retire every old record for the
//! connection before publishing the new presence.

mod router;
mod types;

pub use router::{MutationDisposition, PresenceDelta, PresenceError, PresenceRouter};
pub use types::{
    ConnectionGeneration, ConnectionId, ConnectionRef, JoinPresenceRequest, LeavePresenceRequest,
    NodeId, PresenceIdentity, PresenceRecord, PresenceStatus, RemoveConnectionRequest, SessionId,
    SnapshotVisibility, StreamKey, UpdatePresenceRequest, UserId, Username, ValidationError,
    MAX_CONNECTION_ID_BYTES, MAX_NODE_ID_BYTES, MAX_SESSION_ID_BYTES, MAX_STATUS_BYTES,
    MAX_STREAM_LABEL_BYTES, MAX_USERNAME_BYTES, MAX_USER_ID_BYTES,
};
