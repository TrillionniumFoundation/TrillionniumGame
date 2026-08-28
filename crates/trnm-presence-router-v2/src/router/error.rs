use core::fmt;

use crate::types::{ConnectionGeneration, ConnectionRef, PresenceIdentity, StreamKey};

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
        existing: Box<PresenceIdentity>,
        received: Box<PresenceIdentity>,
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
