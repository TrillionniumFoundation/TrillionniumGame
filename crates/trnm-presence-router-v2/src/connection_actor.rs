//! Bounded, deterministic connection-actor state.
//!
//! This module deliberately contains no network I/O.  It is the source-level
//! ownership contract used by a transport adapter: one actor owns one socket,
//! all ingress/egress queues are bounded, correlation identifiers cannot be
//! reused while pending, and the write sequence advances only when a frame is
//! accepted into the outbound queue.

use std::collections::{BTreeMap, VecDeque};
use std::fmt;

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct CorrelationId(u64);

impl CorrelationId {
    pub fn new(value: u64) -> Result<Self, ConnectionActorError> {
        if value == 0 {
            return Err(ConnectionActorError::ZeroCorrelationId);
        }
        Ok(Self(value))
    }

    pub const fn get(self) -> u64 {
        self.0
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ConnectionActorConfig {
    pub inbound_capacity: usize,
    pub outbound_capacity: usize,
    pub pending_capacity: usize,
    pub max_frame_bytes: usize,
}

impl ConnectionActorConfig {
    pub fn validate(self) -> Result<Self, ConnectionActorError> {
        if self.inbound_capacity == 0
            || self.outbound_capacity == 0
            || self.pending_capacity == 0
            || self.max_frame_bytes == 0
        {
            return Err(ConnectionActorError::InvalidCapacity);
        }
        Ok(self)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ConnectionActorState {
    Open,
    Draining,
    Closed,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct InboundFrame {
    pub correlation: Option<CorrelationId>,
    pub payload: Vec<u8>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OutboundFrame {
    pub sequence: u64,
    pub correlation: Option<CorrelationId>,
    pub payload: Vec<u8>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct PendingRequest {
    pub admitted_at_sequence: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ConnectionActorError {
    InvalidCapacity,
    ZeroCorrelationId,
    FrameTooLarge { limit: usize, actual: usize },
    InboundQueueFull { capacity: usize },
    OutboundQueueFull { capacity: usize },
    PendingQueueFull { capacity: usize },
    DuplicateCorrelation(CorrelationId),
    UnknownCorrelation(CorrelationId),
    Draining,
    Closed,
    WriteSequenceExhausted,
    CannotCloseWithPendingWork,
}

impl fmt::Display for ConnectionActorError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidCapacity => formatter.write_str("connection actor capacities must be positive"),
            Self::ZeroCorrelationId => formatter.write_str("correlation id must be positive"),
            Self::FrameTooLarge { limit, actual } => {
                write!(formatter, "frame length {actual} exceeds {limit} bytes")
            }
            Self::InboundQueueFull { capacity } => {
                write!(formatter, "inbound queue is full at capacity {capacity}")
            }
            Self::OutboundQueueFull { capacity } => {
                write!(formatter, "outbound queue is full at capacity {capacity}")
            }
            Self::PendingQueueFull { capacity } => {
                write!(formatter, "pending request table is full at capacity {capacity}")
            }
            Self::DuplicateCorrelation(value) => {
                write!(formatter, "correlation {} is already pending", value.get())
            }
            Self::UnknownCorrelation(value) => {
                write!(formatter, "correlation {} is not pending", value.get())
            }
            Self::Draining => formatter.write_str("connection actor is draining"),
            Self::Closed => formatter.write_str("connection actor is closed"),
            Self::WriteSequenceExhausted => formatter.write_str("connection write sequence exhausted"),
            Self::CannotCloseWithPendingWork => {
                formatter.write_str("connection cannot close while queued or pending work remains")
            }
        }
    }
}

impl std::error::Error for ConnectionActorError {}

#[derive(Clone, Debug)]
pub struct ConnectionActor {
    config: ConnectionActorConfig,
    state: ConnectionActorState,
    next_write_sequence: u64,
    inbound: VecDeque<InboundFrame>,
    outbound: VecDeque<OutboundFrame>,
    pending: BTreeMap<CorrelationId, PendingRequest>,
}

impl ConnectionActor {
    pub fn new(config: ConnectionActorConfig) -> Result<Self, ConnectionActorError> {
        Ok(Self {
            config: config.validate()?,
            state: ConnectionActorState::Open,
            next_write_sequence: 1,
            inbound: VecDeque::new(),
            outbound: VecDeque::new(),
            pending: BTreeMap::new(),
        })
    }

    pub const fn state(&self) -> ConnectionActorState {
        self.state
    }

    pub fn inbound_len(&self) -> usize {
        self.inbound.len()
    }

    pub fn outbound_len(&self) -> usize {
        self.outbound.len()
    }

    pub fn pending_len(&self) -> usize {
        self.pending.len()
    }

    pub fn enqueue_inbound(&mut self, frame: InboundFrame) -> Result<(), ConnectionActorError> {
        self.require_open()?;
        self.require_frame_size(frame.payload.len())?;
        if self.inbound.len() >= self.config.inbound_capacity {
            return Err(ConnectionActorError::InboundQueueFull {
                capacity: self.config.inbound_capacity,
            });
        }
        self.inbound.push_back(frame);
        Ok(())
    }

    pub fn pop_inbound(&mut self) -> Option<InboundFrame> {
        self.inbound.pop_front()
    }

    pub fn begin_request(
        &mut self,
        correlation: CorrelationId,
    ) -> Result<PendingRequest, ConnectionActorError> {
        self.require_open()?;
        if self.pending.contains_key(&correlation) {
            return Err(ConnectionActorError::DuplicateCorrelation(correlation));
        }
        if self.pending.len() >= self.config.pending_capacity {
            return Err(ConnectionActorError::PendingQueueFull {
                capacity: self.config.pending_capacity,
            });
        }
        let request = PendingRequest {
            admitted_at_sequence: self.next_write_sequence,
        };
        self.pending.insert(correlation, request);
        Ok(request)
    }

    pub fn complete_request(
        &mut self,
        correlation: CorrelationId,
    ) -> Result<PendingRequest, ConnectionActorError> {
        self.pending
            .remove(&correlation)
            .ok_or(ConnectionActorError::UnknownCorrelation(correlation))
    }

    pub fn enqueue_outbound(
        &mut self,
        correlation: Option<CorrelationId>,
        payload: Vec<u8>,
    ) -> Result<u64, ConnectionActorError> {
        self.require_not_closed()?;
        self.require_frame_size(payload.len())?;
        if self.outbound.len() >= self.config.outbound_capacity {
            return Err(ConnectionActorError::OutboundQueueFull {
                capacity: self.config.outbound_capacity,
            });
        }
        let sequence = self.next_write_sequence;
        let next = sequence
            .checked_add(1)
            .ok_or(ConnectionActorError::WriteSequenceExhausted)?;
        self.outbound.push_back(OutboundFrame {
            sequence,
            correlation,
            payload,
        });
        self.next_write_sequence = next;
        Ok(sequence)
    }

    pub fn pop_outbound(&mut self) -> Option<OutboundFrame> {
        self.outbound.pop_front()
    }

    pub fn begin_drain(&mut self) -> Result<(), ConnectionActorError> {
        match self.state {
            ConnectionActorState::Open => {
                self.state = ConnectionActorState::Draining;
                Ok(())
            }
            ConnectionActorState::Draining => Ok(()),
            ConnectionActorState::Closed => Err(ConnectionActorError::Closed),
        }
    }

    pub fn close(&mut self) -> Result<(), ConnectionActorError> {
        if self.state == ConnectionActorState::Closed {
            return Ok(());
        }
        if !self.inbound.is_empty() || !self.outbound.is_empty() || !self.pending.is_empty() {
            return Err(ConnectionActorError::CannotCloseWithPendingWork);
        }
        self.state = ConnectionActorState::Closed;
        Ok(())
    }

    fn require_open(&self) -> Result<(), ConnectionActorError> {
        match self.state {
            ConnectionActorState::Open => Ok(()),
            ConnectionActorState::Draining => Err(ConnectionActorError::Draining),
            ConnectionActorState::Closed => Err(ConnectionActorError::Closed),
        }
    }

    fn require_not_closed(&self) -> Result<(), ConnectionActorError> {
        if self.state == ConnectionActorState::Closed {
            Err(ConnectionActorError::Closed)
        } else {
            Ok(())
        }
    }

    fn require_frame_size(&self, actual: usize) -> Result<(), ConnectionActorError> {
        if actual > self.config.max_frame_bytes {
            return Err(ConnectionActorError::FrameTooLarge {
                limit: self.config.max_frame_bytes,
                actual,
            });
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn actor() -> ConnectionActor {
        ConnectionActor::new(ConnectionActorConfig {
            inbound_capacity: 2,
            outbound_capacity: 2,
            pending_capacity: 2,
            max_frame_bytes: 8,
        })
        .unwrap()
    }

    #[test]
    fn queues_and_pending_requests_are_strictly_bounded() {
        let mut actor = actor();
        actor
            .enqueue_inbound(InboundFrame {
                correlation: None,
                payload: vec![1],
            })
            .unwrap();
        actor
            .enqueue_inbound(InboundFrame {
                correlation: None,
                payload: vec![2],
            })
            .unwrap();
        assert!(matches!(
            actor.enqueue_inbound(InboundFrame {
                correlation: None,
                payload: vec![3],
            }),
            Err(ConnectionActorError::InboundQueueFull { capacity: 2 })
        ));

        for value in 1..=2 {
            actor.begin_request(CorrelationId::new(value).unwrap()).unwrap();
        }
        assert!(matches!(
            actor.begin_request(CorrelationId::new(3).unwrap()),
            Err(ConnectionActorError::PendingQueueFull { capacity: 2 })
        ));
    }

    #[test]
    fn write_sequence_advances_only_after_successful_admission() {
        let mut actor = actor();
        assert_eq!(actor.enqueue_outbound(None, vec![1]).unwrap(), 1);
        assert_eq!(actor.enqueue_outbound(None, vec![2]).unwrap(), 2);
        assert!(matches!(
            actor.enqueue_outbound(None, vec![3]),
            Err(ConnectionActorError::OutboundQueueFull { capacity: 2 })
        ));
        assert_eq!(actor.pop_outbound().unwrap().sequence, 1);
        assert_eq!(actor.enqueue_outbound(None, vec![4]).unwrap(), 3);
    }

    #[test]
    fn duplicate_correlation_is_rejected_without_mutation() {
        let mut actor = actor();
        let correlation = CorrelationId::new(9).unwrap();
        let original = actor.begin_request(correlation).unwrap();
        assert_eq!(
            actor.begin_request(correlation),
            Err(ConnectionActorError::DuplicateCorrelation(correlation))
        );
        assert_eq!(actor.pending_len(), 1);
        assert_eq!(actor.complete_request(correlation).unwrap(), original);
    }

    #[test]
    fn drain_rejects_new_ingress_but_flushes_existing_egress() {
        let mut actor = actor();
        actor.enqueue_outbound(None, vec![1]).unwrap();
        actor.begin_drain().unwrap();
        assert_eq!(actor.begin_drain(), Ok(()));
        assert_eq!(
            actor.enqueue_inbound(InboundFrame {
                correlation: None,
                payload: vec![1],
            }),
            Err(ConnectionActorError::Draining)
        );
        assert_eq!(actor.pop_outbound().unwrap().sequence, 1);
        actor.close().unwrap();
        assert_eq!(actor.state(), ConnectionActorState::Closed);
    }

    #[test]
    fn oversized_frames_never_consume_queue_or_sequence_capacity() {
        let mut actor = actor();
        assert!(matches!(
            actor.enqueue_outbound(None, vec![0; 9]),
            Err(ConnectionActorError::FrameTooLarge { limit: 8, actual: 9 })
        ));
        assert_eq!(actor.outbound_len(), 0);
        assert_eq!(actor.enqueue_outbound(None, vec![1]).unwrap(), 1);
    }
}
