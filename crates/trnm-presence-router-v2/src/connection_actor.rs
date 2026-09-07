//! Bounded, deterministic connection-actor state.
//!
//! This module deliberately contains no network I/O. One actor owns one
//! socket. Ingress, egress, pending work and retained payload bytes are all
//! bounded. Request admission is distinct from response admission, correlated
//! egress is accepted only for live pending work, and drain permits at most one
//! response for each request admitted before the fence.

use std::collections::{BTreeMap, VecDeque};
use std::fmt;

pub const MAX_CONNECTION_ACTOR_QUEUE_ITEMS: usize = 65_536;
pub const MAX_CONNECTION_ACTOR_FRAME_BYTES: usize = 4 * 1024 * 1024;
pub const MAX_CONNECTION_ACTOR_BUFFER_BYTES: usize = 64 * 1024 * 1024;

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
        for (field, value) in [
            ("inbound_capacity", self.inbound_capacity),
            ("outbound_capacity", self.outbound_capacity),
            ("pending_capacity", self.pending_capacity),
            ("max_frame_bytes", self.max_frame_bytes),
        ] {
            if value == 0 {
                return Err(ConnectionActorError::InvalidCapacity(field));
            }
        }
        for (field, value) in [
            ("inbound_capacity", self.inbound_capacity),
            ("outbound_capacity", self.outbound_capacity),
            ("pending_capacity", self.pending_capacity),
        ] {
            if value > MAX_CONNECTION_ACTOR_QUEUE_ITEMS {
                return Err(ConnectionActorError::CapacityTooLarge {
                    field,
                    value,
                    maximum: MAX_CONNECTION_ACTOR_QUEUE_ITEMS,
                });
            }
        }
        if self.max_frame_bytes > MAX_CONNECTION_ACTOR_FRAME_BYTES {
            return Err(ConnectionActorError::CapacityTooLarge {
                field: "max_frame_bytes",
                value: self.max_frame_bytes,
                maximum: MAX_CONNECTION_ACTOR_FRAME_BYTES,
            });
        }
        let queue_slots = self
            .inbound_capacity
            .checked_add(self.outbound_capacity)
            .ok_or(ConnectionActorError::BufferBudgetOverflow)?;
        let retained_bytes = queue_slots
            .checked_mul(self.max_frame_bytes)
            .ok_or(ConnectionActorError::BufferBudgetOverflow)?;
        if retained_bytes > MAX_CONNECTION_ACTOR_BUFFER_BYTES {
            return Err(ConnectionActorError::BufferBudgetExceeded {
                requested: retained_bytes,
                maximum: MAX_CONNECTION_ACTOR_BUFFER_BYTES,
            });
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
    pub payload: Box<[u8]>,
}

impl InboundFrame {
    #[must_use]
    pub fn from_vec(correlation: Option<CorrelationId>, payload: Vec<u8>) -> Self {
        Self {
            correlation,
            payload: payload.into_boxed_slice(),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OutboundFrame {
    pub sequence: u64,
    pub correlation: Option<CorrelationId>,
    pub payload: Box<[u8]>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct PendingRequest {
    /// Unique request-admission sequence. This is intentionally independent of
    /// the socket write sequence and therefore never makes a premature write
    /// reservation claim.
    pub admitted_at_sequence: u64,
    pub response_sequence: Option<u64>,
    pub response_dequeued: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ConnectionActorError {
    InvalidCapacity(&'static str),
    CapacityTooLarge {
        field: &'static str,
        value: usize,
        maximum: usize,
    },
    BufferBudgetOverflow,
    BufferBudgetExceeded {
        requested: usize,
        maximum: usize,
    },
    ZeroCorrelationId,
    FrameTooLarge {
        limit: usize,
        actual: usize,
    },
    InboundQueueFull {
        capacity: usize,
    },
    OutboundQueueFull {
        capacity: usize,
    },
    PendingQueueFull {
        capacity: usize,
    },
    DuplicateCorrelation(CorrelationId),
    UnknownCorrelation(CorrelationId),
    ResponseAlreadyQueued(CorrelationId),
    ResponseNotDequeued(CorrelationId),
    CannotCancelQueuedResponse(CorrelationId),
    Draining,
    Closed,
    RequestSequenceExhausted,
    WriteSequenceExhausted,
    MustDrainBeforeClose,
    CannotCloseWithPendingWork,
}

impl fmt::Display for ConnectionActorError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidCapacity(field) => write!(formatter, "{field} must be positive"),
            Self::CapacityTooLarge {
                field,
                value,
                maximum,
            } => write!(formatter, "{field} {value} exceeds hard maximum {maximum}"),
            Self::BufferBudgetOverflow => {
                formatter.write_str("connection actor buffer budget arithmetic overflow")
            }
            Self::BufferBudgetExceeded { requested, maximum } => write!(
                formatter,
                "connection actor may retain {requested} payload bytes, above hard maximum {maximum}"
            ),
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
            Self::ResponseAlreadyQueued(value) => write!(
                formatter,
                "correlation {} already owns an outbound response",
                value.get()
            ),
            Self::ResponseNotDequeued(value) => write!(
                formatter,
                "correlation {} response has not left the actor queue",
                value.get()
            ),
            Self::CannotCancelQueuedResponse(value) => write!(
                formatter,
                "correlation {} cannot be cancelled after response admission",
                value.get()
            ),
            Self::Draining => formatter.write_str("connection actor is draining"),
            Self::Closed => formatter.write_str("connection actor is closed"),
            Self::RequestSequenceExhausted => {
                formatter.write_str("connection request sequence exhausted")
            }
            Self::WriteSequenceExhausted => {
                formatter.write_str("connection write sequence exhausted")
            }
            Self::MustDrainBeforeClose => {
                formatter.write_str("connection must enter drain before close")
            }
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
    next_request_sequence: u64,
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
            next_request_sequence: 1,
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

    pub fn drain_converged(&self) -> bool {
        self.state == ConnectionActorState::Draining
            && self.inbound.is_empty()
            && self.outbound.is_empty()
            && self.pending.is_empty()
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

    /// Admit asynchronous work without claiming or reserving a socket write.
    pub fn begin_request(
        &mut self,
        correlation: CorrelationId,
    ) -> Result<PendingRequest, ConnectionActorError> {
        self.require_request_capacity(correlation)?;
        let admitted_at_sequence = self.next_request_sequence;
        let next_request_sequence = admitted_at_sequence
            .checked_add(1)
            .ok_or(ConnectionActorError::RequestSequenceExhausted)?;
        let request = PendingRequest {
            admitted_at_sequence,
            response_sequence: None,
            response_dequeued: false,
        };
        self.pending.insert(correlation, request);
        self.next_request_sequence = next_request_sequence;
        Ok(request)
    }

    /// Atomically admit a request and its immediate response. Every predicate is
    /// checked before pending state, queue contents, or either sequence changes.
    pub fn admit_immediate_response(
        &mut self,
        correlation: CorrelationId,
        payload: Vec<u8>,
    ) -> Result<PendingRequest, ConnectionActorError> {
        self.require_request_capacity(correlation)?;
        self.require_outbound_capacity()?;
        self.require_frame_size(payload.len())?;
        let admitted_at_sequence = self.next_request_sequence;
        let next_request_sequence = admitted_at_sequence
            .checked_add(1)
            .ok_or(ConnectionActorError::RequestSequenceExhausted)?;
        let response_sequence = self.next_write_sequence;
        let next_write_sequence = response_sequence
            .checked_add(1)
            .ok_or(ConnectionActorError::WriteSequenceExhausted)?;
        let request = PendingRequest {
            admitted_at_sequence,
            response_sequence: Some(response_sequence),
            response_dequeued: false,
        };
        self.pending.insert(correlation, request);
        self.outbound.push_back(OutboundFrame {
            sequence: response_sequence,
            correlation: Some(correlation),
            payload: payload.into_boxed_slice(),
        });
        self.next_request_sequence = next_request_sequence;
        self.next_write_sequence = next_write_sequence;
        Ok(request)
    }

    /// Admit exactly one response for previously admitted work. During drain
    /// this remains valid only for the finite pre-fence pending set.
    pub fn enqueue_response(
        &mut self,
        correlation: CorrelationId,
        payload: Vec<u8>,
    ) -> Result<u64, ConnectionActorError> {
        self.require_not_closed()?;
        self.require_frame_size(payload.len())?;
        self.require_outbound_capacity()?;
        let current = self
            .pending
            .get(&correlation)
            .copied()
            .ok_or(ConnectionActorError::UnknownCorrelation(correlation))?;
        if current.response_sequence.is_some() {
            return Err(ConnectionActorError::ResponseAlreadyQueued(correlation));
        }
        let sequence = self.next_write_sequence;
        let next_write_sequence = sequence
            .checked_add(1)
            .ok_or(ConnectionActorError::WriteSequenceExhausted)?;
        let next = PendingRequest {
            response_sequence: Some(sequence),
            ..current
        };
        self.outbound.push_back(OutboundFrame {
            sequence,
            correlation: Some(correlation),
            payload: payload.into_boxed_slice(),
        });
        self.pending.insert(correlation, next);
        self.next_write_sequence = next_write_sequence;
        Ok(sequence)
    }

    /// Uncorrelated egress is a new unit of work and is therefore rejected once
    /// the drain fence is active.
    pub fn enqueue_control(&mut self, payload: Vec<u8>) -> Result<u64, ConnectionActorError> {
        self.require_open()?;
        self.require_frame_size(payload.len())?;
        self.require_outbound_capacity()?;
        let sequence = self.next_write_sequence;
        let next_write_sequence = sequence
            .checked_add(1)
            .ok_or(ConnectionActorError::WriteSequenceExhausted)?;
        self.outbound.push_back(OutboundFrame {
            sequence,
            correlation: None,
            payload: payload.into_boxed_slice(),
        });
        self.next_write_sequence = next_write_sequence;
        Ok(sequence)
    }

    /// Compatibility entry point with strict correlation semantics.
    pub fn enqueue_outbound(
        &mut self,
        correlation: Option<CorrelationId>,
        payload: Vec<u8>,
    ) -> Result<u64, ConnectionActorError> {
        match correlation {
            Some(value) => self.enqueue_response(value, payload),
            None => self.enqueue_control(payload),
        }
    }

    pub fn pop_outbound(&mut self) -> Option<OutboundFrame> {
        let frame = self.outbound.pop_front()?;
        if let Some(correlation) = frame.correlation {
            if let Some(current) = self.pending.get(&correlation).copied() {
                self.pending.insert(
                    correlation,
                    PendingRequest {
                        response_dequeued: true,
                        ..current
                    },
                );
            }
        }
        Some(frame)
    }

    pub fn complete_request(
        &mut self,
        correlation: CorrelationId,
    ) -> Result<PendingRequest, ConnectionActorError> {
        let current = self
            .pending
            .get(&correlation)
            .copied()
            .ok_or(ConnectionActorError::UnknownCorrelation(correlation))?;
        if !current.response_dequeued {
            return Err(ConnectionActorError::ResponseNotDequeued(correlation));
        }
        self.pending.remove(&correlation);
        Ok(current)
    }

    pub fn cancel_request(
        &mut self,
        correlation: CorrelationId,
    ) -> Result<PendingRequest, ConnectionActorError> {
        let current = self
            .pending
            .get(&correlation)
            .copied()
            .ok_or(ConnectionActorError::UnknownCorrelation(correlation))?;
        if current.response_sequence.is_some() {
            return Err(ConnectionActorError::CannotCancelQueuedResponse(correlation));
        }
        self.pending.remove(&correlation);
        Ok(current)
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
        match self.state {
            ConnectionActorState::Open => return Err(ConnectionActorError::MustDrainBeforeClose),
            ConnectionActorState::Closed => return Ok(()),
            ConnectionActorState::Draining => {}
        }
        if !self.drain_converged() {
            return Err(ConnectionActorError::CannotCloseWithPendingWork);
        }
        self.state = ConnectionActorState::Closed;
        Ok(())
    }

    fn require_request_capacity(
        &self,
        correlation: CorrelationId,
    ) -> Result<(), ConnectionActorError> {
        self.require_open()?;
        if self.pending.contains_key(&correlation) {
            return Err(ConnectionActorError::DuplicateCorrelation(correlation));
        }
        if self.pending.len() >= self.config.pending_capacity {
            return Err(ConnectionActorError::PendingQueueFull {
                capacity: self.config.pending_capacity,
            });
        }
        Ok(())
    }

    fn require_outbound_capacity(&self) -> Result<(), ConnectionActorError> {
        if self.outbound.len() >= self.config.outbound_capacity {
            Err(ConnectionActorError::OutboundQueueFull {
                capacity: self.config.outbound_capacity,
            })
        } else {
            Ok(())
        }
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
            Err(ConnectionActorError::FrameTooLarge {
                limit: self.config.max_frame_bytes,
                actual,
            })
        } else {
            Ok(())
        }
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
    fn configuration_has_checked_hard_memory_budget() {
        assert!(matches!(
            ConnectionActor::new(ConnectionActorConfig {
                inbound_capacity: MAX_CONNECTION_ACTOR_QUEUE_ITEMS,
                outbound_capacity: MAX_CONNECTION_ACTOR_QUEUE_ITEMS,
                pending_capacity: 1,
                max_frame_bytes: MAX_CONNECTION_ACTOR_FRAME_BYTES,
            }),
            Err(ConnectionActorError::BufferBudgetExceeded { .. })
        ));
        assert!(matches!(
            ConnectionActor::new(ConnectionActorConfig {
                inbound_capacity: MAX_CONNECTION_ACTOR_QUEUE_ITEMS + 1,
                outbound_capacity: 1,
                pending_capacity: 1,
                max_frame_bytes: 1,
            }),
            Err(ConnectionActorError::CapacityTooLarge {
                field: "inbound_capacity",
                ..
            })
        ));
    }

    #[test]
    fn caller_vec_spare_capacity_is_not_retained() {
        let mut actor = actor();
        let mut payload = Vec::with_capacity(1_000_000);
        payload.push(7);
        actor
            .enqueue_inbound(InboundFrame::from_vec(None, payload))
            .unwrap();
        let stored = actor.pop_inbound().unwrap();
        assert_eq!(&*stored.payload, &[7]);
        assert_eq!(std::mem::size_of_val(&*stored.payload), 1);
    }

    #[test]
    fn immediate_request_and_response_admission_is_atomic() {
        let mut actor = ConnectionActor::new(ConnectionActorConfig {
            inbound_capacity: 1,
            outbound_capacity: 1,
            pending_capacity: 1,
            max_frame_bytes: 8,
        })
        .unwrap();
        let first = CorrelationId::new(1).unwrap();
        let admitted = actor
            .admit_immediate_response(first, vec![1])
            .unwrap();
        assert_eq!(admitted.admitted_at_sequence, 1);
        assert_eq!(admitted.response_sequence, Some(1));

        let before_pending = actor.pending_len();
        let before_outbound = actor.outbound_len();
        assert!(matches!(
            actor.admit_immediate_response(CorrelationId::new(2).unwrap(), vec![2]),
            Err(ConnectionActorError::PendingQueueFull { capacity: 1 })
        ));
        assert_eq!(actor.pending_len(), before_pending);
        assert_eq!(actor.outbound_len(), before_outbound);
    }

    #[test]
    fn correlated_egress_requires_live_pending_request_and_is_unique() {
        let mut actor = actor();
        let correlation = CorrelationId::new(9).unwrap();
        assert_eq!(
            actor.enqueue_outbound(Some(correlation), vec![1]),
            Err(ConnectionActorError::UnknownCorrelation(correlation))
        );
        let request = actor.begin_request(correlation).unwrap();
        assert_eq!(request.admitted_at_sequence, 1);
        assert_eq!(actor.enqueue_response(correlation, vec![1]).unwrap(), 1);
        assert_eq!(
            actor.enqueue_response(correlation, vec![2]),
            Err(ConnectionActorError::ResponseAlreadyQueued(correlation))
        );
        assert_eq!(
            actor.complete_request(correlation),
            Err(ConnectionActorError::ResponseNotDequeued(correlation))
        );
        assert_eq!(actor.pop_outbound().unwrap().correlation, Some(correlation));
        actor.complete_request(correlation).unwrap();
    }

    #[test]
    fn drain_has_a_finite_pre_admitted_response_set() {
        let mut actor = actor();
        let first = CorrelationId::new(1).unwrap();
        let second = CorrelationId::new(2).unwrap();
        actor.begin_request(first).unwrap();
        actor.begin_request(second).unwrap();
        actor.begin_drain().unwrap();

        assert_eq!(
            actor.begin_request(CorrelationId::new(3).unwrap()),
            Err(ConnectionActorError::Draining)
        );
        assert_eq!(actor.enqueue_control(vec![9]), Err(ConnectionActorError::Draining));
        actor.enqueue_response(first, vec![1]).unwrap();
        actor.enqueue_response(second, vec![2]).unwrap();
        assert_eq!(
            actor.enqueue_response(first, vec![3]),
            Err(ConnectionActorError::ResponseAlreadyQueued(first))
        );

        while let Some(frame) = actor.pop_outbound() {
            actor.complete_request(frame.correlation.unwrap()).unwrap();
        }
        assert!(actor.drain_converged());
        actor.close().unwrap();
        assert_eq!(actor.state(), ConnectionActorState::Closed);
    }

    #[test]
    fn async_admission_does_not_claim_write_sequence() {
        let mut actor = actor();
        let first = CorrelationId::new(1).unwrap();
        let second = CorrelationId::new(2).unwrap();
        assert_eq!(actor.begin_request(first).unwrap().admitted_at_sequence, 1);
        assert_eq!(actor.begin_request(second).unwrap().admitted_at_sequence, 2);
        assert_eq!(actor.enqueue_response(second, vec![2]).unwrap(), 1);
        assert_eq!(actor.enqueue_response(first, vec![1]).unwrap(), 2);
    }

    #[test]
    fn oversized_frames_do_not_consume_state() {
        let mut actor = actor();
        let correlation = CorrelationId::new(1).unwrap();
        actor.begin_request(correlation).unwrap();
        assert!(matches!(
            actor.enqueue_response(correlation, vec![0; 9]),
            Err(ConnectionActorError::FrameTooLarge {
                limit: 8,
                actual: 9
            })
        ));
        assert_eq!(actor.outbound_len(), 0);
        assert_eq!(actor.enqueue_response(correlation, vec![1]).unwrap(), 1);
    }
}
