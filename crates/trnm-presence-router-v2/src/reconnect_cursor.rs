//! Bounded reconnect journal with authenticated, domain-bound cursors.
//!
//! A cursor is not a bare sequence number. It binds the journal identity,
//! stream identity, session generation and producer incarnation, and carries a
//! tag supplied by the trusted transport authenticator. Cross-stream, restart,
//! takeover and tampered cursors fail closed before replay-window evaluation.

use std::collections::VecDeque;
use std::fmt;

pub const MAX_RECONNECT_JOURNAL_CAPACITY: usize = 65_536;

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct ReconnectJournalId([u8; 16]);

impl ReconnectJournalId {
    pub fn new(value: [u8; 16]) -> Result<Self, ReconnectJournalError> {
        require_nonzero("journal_id", &value)?;
        Ok(Self(value))
    }

    pub const fn as_bytes(self) -> [u8; 16] {
        self.0
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct ReconnectStreamId([u8; 32]);

impl ReconnectStreamId {
    pub fn new(value: [u8; 32]) -> Result<Self, ReconnectJournalError> {
        require_nonzero("stream_id", &value)?;
        Ok(Self(value))
    }

    pub const fn as_bytes(self) -> [u8; 32] {
        self.0
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct ReconnectSessionGeneration(u64);

impl ReconnectSessionGeneration {
    pub fn new(value: u64) -> Result<Self, ReconnectJournalError> {
        if value == 0 {
            return Err(ReconnectJournalError::ZeroIdentifier(
                "session_generation",
            ));
        }
        Ok(Self(value))
    }

    pub const fn get(self) -> u64 {
        self.0
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct ReconnectProducerEpoch(u64);

impl ReconnectProducerEpoch {
    pub fn new(value: u64) -> Result<Self, ReconnectJournalError> {
        if value == 0 {
            return Err(ReconnectJournalError::ZeroIdentifier("producer_epoch"));
        }
        Ok(Self(value))
    }

    pub const fn get(self) -> u64 {
        self.0
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ReconnectIdentity {
    pub journal_id: ReconnectJournalId,
    pub stream_id: ReconnectStreamId,
    pub session_generation: ReconnectSessionGeneration,
    pub producer_epoch: ReconnectProducerEpoch,
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub struct ReconnectCursor {
    identity: ReconnectIdentity,
    sequence: u64,
    tag: [u8; 32],
}

impl ReconnectCursor {
    pub const fn identity(self) -> ReconnectIdentity {
        self.identity
    }

    pub const fn sequence(self) -> u64 {
        self.sequence
    }

    pub const fn tag(self) -> [u8; 32] {
        self.tag
    }
}

impl fmt::Debug for ReconnectCursor {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ReconnectCursor")
            .field("identity", &self.identity)
            .field("sequence", &self.sequence)
            .field("tag", &"<authenticated-tag>")
            .finish()
    }
}

/// Trust boundary implemented by the transport composition root, normally
/// using a domain-separated HMAC/KMS operation. The journal recomputes the tag
/// for every cursor; it never accepts a caller-supplied verification boolean.
pub trait ReconnectCursorAuthenticator {
    fn tag(&self, identity: ReconnectIdentity, sequence: u64) -> [u8; 32];
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ReconnectEvent {
    pub sequence: u64,
    pub digest: [u8; 32],
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ReconnectJournalConfig {
    pub capacity: usize,
    pub identity: ReconnectIdentity,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ReconnectJournalError {
    ZeroCapacity,
    CapacityTooLarge {
        received: usize,
        maximum: usize,
    },
    ZeroIdentifier(&'static str),
    ZeroDigest(&'static str),
    SequenceExhausted,
    CursorIdentityMismatch,
    CursorAuthenticationFailed,
    AuthenticatorReturnedZeroTag,
    CursorAhead {
        cursor: u64,
        latest: u64,
    },
    CursorExpired {
        requested_next: u64,
        oldest_available: u64,
    },
    InvariantViolation(&'static str),
}

impl fmt::Display for ReconnectJournalError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::ZeroCapacity => formatter.write_str("reconnect journal capacity must be positive"),
            Self::CapacityTooLarge { received, maximum } => write!(
                formatter,
                "reconnect journal capacity {received} exceeds hard maximum {maximum}"
            ),
            Self::ZeroIdentifier(field) => write!(formatter, "{field} must be positive"),
            Self::ZeroDigest(field) => write!(formatter, "{field} must not be the zero digest"),
            Self::SequenceExhausted => formatter.write_str("reconnect event sequence exhausted"),
            Self::CursorIdentityMismatch => formatter.write_str(
                "reconnect cursor belongs to a different journal, stream, session generation, or producer epoch",
            ),
            Self::CursorAuthenticationFailed => {
                formatter.write_str("reconnect cursor authentication failed")
            }
            Self::AuthenticatorReturnedZeroTag => {
                formatter.write_str("reconnect cursor authenticator returned a zero tag")
            }
            Self::CursorAhead { cursor, latest } => write!(
                formatter,
                "reconnect cursor {cursor} is ahead of latest sequence {latest}"
            ),
            Self::CursorExpired {
                requested_next,
                oldest_available,
            } => write!(
                formatter,
                "reconnect cursor requires sequence {requested_next}, but oldest retained sequence is {oldest_available}"
            ),
            Self::InvariantViolation(message) => {
                write!(formatter, "reconnect journal invariant violation: {message}")
            }
        }
    }
}

impl std::error::Error for ReconnectJournalError {}

#[derive(Clone, Debug)]
pub struct ReconnectJournal {
    config: ReconnectJournalConfig,
    next_sequence: u64,
    events: VecDeque<ReconnectEvent>,
}

impl ReconnectJournal {
    pub fn new(config: ReconnectJournalConfig) -> Result<Self, ReconnectJournalError> {
        if config.capacity == 0 {
            return Err(ReconnectJournalError::ZeroCapacity);
        }
        if config.capacity > MAX_RECONNECT_JOURNAL_CAPACITY {
            return Err(ReconnectJournalError::CapacityTooLarge {
                received: config.capacity,
                maximum: MAX_RECONNECT_JOURNAL_CAPACITY,
            });
        }
        Ok(Self {
            config,
            next_sequence: 1,
            events: VecDeque::with_capacity(config.capacity),
        })
    }

    pub const fn identity(&self) -> ReconnectIdentity {
        self.config.identity
    }

    pub const fn capacity(&self) -> usize {
        self.config.capacity
    }

    pub fn len(&self) -> usize {
        self.events.len()
    }

    pub fn is_empty(&self) -> bool {
        self.events.is_empty()
    }

    pub fn latest_sequence(&self) -> u64 {
        self.next_sequence.saturating_sub(1)
    }

    pub fn oldest_sequence(&self) -> Option<u64> {
        self.events.front().map(|event| event.sequence)
    }

    pub fn append(&mut self, digest: [u8; 32]) -> Result<ReconnectEvent, ReconnectJournalError> {
        require_nonzero("event_digest", &digest)?;
        self.verify_invariants()?;
        let sequence = self.next_sequence;
        let next = sequence
            .checked_add(1)
            .ok_or(ReconnectJournalError::SequenceExhausted)?;
        if self.events.len() == self.config.capacity {
            self.events.pop_front();
        }
        let event = ReconnectEvent { sequence, digest };
        self.events.push_back(event);
        self.next_sequence = next;
        self.verify_invariants()?;
        Ok(event)
    }

    pub fn before_first_cursor(
        &self,
        authenticator: &impl ReconnectCursorAuthenticator,
    ) -> Result<ReconnectCursor, ReconnectJournalError> {
        self.issue_cursor(0, authenticator)
    }

    pub fn latest_cursor(
        &self,
        authenticator: &impl ReconnectCursorAuthenticator,
    ) -> Result<ReconnectCursor, ReconnectJournalError> {
        self.issue_cursor(self.latest_sequence(), authenticator)
    }

    pub fn issue_cursor(
        &self,
        sequence: u64,
        authenticator: &impl ReconnectCursorAuthenticator,
    ) -> Result<ReconnectCursor, ReconnectJournalError> {
        self.verify_invariants()?;
        let latest = self.latest_sequence();
        if sequence > latest {
            return Err(ReconnectJournalError::CursorAhead {
                cursor: sequence,
                latest,
            });
        }
        let tag = authenticator.tag(self.config.identity, sequence);
        if tag.iter().all(|byte| *byte == 0) {
            return Err(ReconnectJournalError::AuthenticatorReturnedZeroTag);
        }
        Ok(ReconnectCursor {
            identity: self.config.identity,
            sequence,
            tag,
        })
    }

    pub fn replay_after(
        &self,
        cursor: ReconnectCursor,
        authenticator: &impl ReconnectCursorAuthenticator,
    ) -> Result<Vec<ReconnectEvent>, ReconnectJournalError> {
        self.verify_invariants()?;
        if cursor.identity != self.config.identity {
            return Err(ReconnectJournalError::CursorIdentityMismatch);
        }
        let expected_tag = authenticator.tag(cursor.identity, cursor.sequence);
        if expected_tag.iter().all(|byte| *byte == 0)
            || !constant_time_eq(&expected_tag, &cursor.tag)
        {
            return Err(ReconnectJournalError::CursorAuthenticationFailed);
        }
        let latest = self.latest_sequence();
        if cursor.sequence > latest {
            return Err(ReconnectJournalError::CursorAhead {
                cursor: cursor.sequence,
                latest,
            });
        }
        if self.events.is_empty() || cursor.sequence == latest {
            return Ok(Vec::new());
        }
        let requested_next = cursor
            .sequence
            .checked_add(1)
            .ok_or(ReconnectJournalError::SequenceExhausted)?;
        let oldest = self
            .oldest_sequence()
            .ok_or(ReconnectJournalError::InvariantViolation(
                "non-empty replay has no oldest event",
            ))?;
        if requested_next < oldest {
            return Err(ReconnectJournalError::CursorExpired {
                requested_next,
                oldest_available: oldest,
            });
        }
        Ok(self
            .events
            .iter()
            .copied()
            .filter(|event| event.sequence > cursor.sequence)
            .collect())
    }

    pub fn verify_invariants(&self) -> Result<(), ReconnectJournalError> {
        if self.config.capacity == 0 || self.config.capacity > MAX_RECONNECT_JOURNAL_CAPACITY {
            return Err(ReconnectJournalError::InvariantViolation(
                "capacity is outside the validated range",
            ));
        }
        if self.events.len() > self.config.capacity {
            return Err(ReconnectJournalError::InvariantViolation(
                "retained event count exceeds capacity",
            ));
        }
        let mut previous = None;
        for event in &self.events {
            if event.digest.iter().all(|byte| *byte == 0) {
                return Err(ReconnectJournalError::InvariantViolation(
                    "retained event has zero digest",
                ));
            }
            if let Some(sequence) = previous {
                if event.sequence != sequence + 1 {
                    return Err(ReconnectJournalError::InvariantViolation(
                        "retained event sequences are not contiguous",
                    ));
                }
            }
            previous = Some(event.sequence);
        }
        if let Some(last) = previous {
            if last != self.latest_sequence() {
                return Err(ReconnectJournalError::InvariantViolation(
                    "last retained sequence differs from producer high-water mark",
                ));
            }
        } else if self.latest_sequence() != 0 {
            return Err(ReconnectJournalError::InvariantViolation(
                "empty journal has a nonzero high-water mark",
            ));
        }
        Ok(())
    }
}

fn require_nonzero(field: &'static str, value: &[u8]) -> Result<(), ReconnectJournalError> {
    if value.iter().all(|byte| *byte == 0) {
        Err(ReconnectJournalError::ZeroDigest(field))
    } else {
        Ok(())
    }
}

fn constant_time_eq(left: &[u8; 32], right: &[u8; 32]) -> bool {
    let mut difference = 0_u8;
    for (left, right) in left.iter().zip(right.iter()) {
        difference |= left ^ right;
    }
    difference == 0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Clone, Copy)]
    struct TestAuthenticator([u8; 32]);

    impl ReconnectCursorAuthenticator for TestAuthenticator {
        fn tag(&self, identity: ReconnectIdentity, sequence: u64) -> [u8; 32] {
            let mut result = self.0;
            for (index, byte) in identity.journal_id.0.iter().enumerate() {
                result[index] ^= *byte;
            }
            for (index, byte) in identity.stream_id.0.iter().enumerate() {
                result[index] ^= byte.rotate_left((index % 8) as u32);
            }
            for (offset, byte) in identity
                .session_generation
                .0
                .to_be_bytes()
                .into_iter()
                .chain(identity.producer_epoch.0.to_be_bytes())
                .chain(sequence.to_be_bytes())
                .enumerate()
            {
                result[offset % 32] ^= byte.rotate_left((offset % 8) as u32);
            }
            result
        }
    }

    fn identity(journal: u8, stream: u8, session: u64, epoch: u64) -> ReconnectIdentity {
        ReconnectIdentity {
            journal_id: ReconnectJournalId::new([journal; 16]).unwrap(),
            stream_id: ReconnectStreamId::new([stream; 32]).unwrap(),
            session_generation: ReconnectSessionGeneration::new(session).unwrap(),
            producer_epoch: ReconnectProducerEpoch::new(epoch).unwrap(),
        }
    }

    fn journal(identity: ReconnectIdentity, capacity: usize) -> ReconnectJournal {
        ReconnectJournal::new(ReconnectJournalConfig { capacity, identity }).unwrap()
    }

    fn digest(value: u8) -> [u8; 32] {
        [value; 32]
    }

    #[test]
    fn replay_is_contiguous_and_authenticated() {
        let auth = TestAuthenticator([0x55; 32]);
        let mut journal = journal(identity(1, 2, 3, 4), 4);
        for value in 1..=3 {
            journal.append(digest(value)).unwrap();
        }
        let cursor = journal.issue_cursor(1, &auth).unwrap();
        assert_eq!(
            journal.replay_after(cursor, &auth).unwrap(),
            vec![
                ReconnectEvent {
                    sequence: 2,
                    digest: digest(2),
                },
                ReconnectEvent {
                    sequence: 3,
                    digest: digest(3),
                },
            ]
        );
    }

    #[test]
    fn cross_journal_stream_session_and_epoch_cursors_fail_closed() {
        let auth = TestAuthenticator([0x44; 32]);
        let source_identity = identity(1, 2, 3, 4);
        let source = journal(source_identity, 2);
        let cursor = source.before_first_cursor(&auth).unwrap();
        for foreign_identity in [
            identity(9, 2, 3, 4),
            identity(1, 9, 3, 4),
            identity(1, 2, 9, 4),
            identity(1, 2, 3, 9),
        ] {
            let target = journal(foreign_identity, 2);
            assert_eq!(
                target.replay_after(cursor, &auth),
                Err(ReconnectJournalError::CursorIdentityMismatch)
            );
        }
    }

    #[test]
    fn restart_or_generation_takeover_rejects_same_numeric_cursor() {
        let auth = TestAuthenticator([0x33; 32]);
        let mut before = journal(identity(1, 2, 3, 7), 2);
        before.append(digest(1)).unwrap();
        let old = before.latest_cursor(&auth).unwrap();

        let mut after = journal(identity(1, 2, 3, 8), 2);
        after.append(digest(9)).unwrap();
        assert_eq!(old.sequence(), after.latest_sequence());
        assert_eq!(
            after.replay_after(old, &auth),
            Err(ReconnectJournalError::CursorIdentityMismatch)
        );
    }

    #[test]
    fn tampered_or_wrong_key_cursor_is_rejected() {
        let auth = TestAuthenticator([0x22; 32]);
        let wrong = TestAuthenticator([0x23; 32]);
        let journal = journal(identity(1, 2, 3, 4), 2);
        let cursor = journal.before_first_cursor(&auth).unwrap();
        assert_eq!(
            journal.replay_after(cursor, &wrong),
            Err(ReconnectJournalError::CursorAuthenticationFailed)
        );
        let mut tampered = cursor;
        tampered.tag[0] ^= 1;
        assert_eq!(
            journal.replay_after(tampered, &auth),
            Err(ReconnectJournalError::CursorAuthenticationFailed)
        );
    }

    #[test]
    fn cursor_older_than_retained_window_is_not_silently_accepted() {
        let auth = TestAuthenticator([0x11; 32]);
        let mut journal = journal(identity(1, 2, 3, 4), 2);
        let before_first = journal.before_first_cursor(&auth).unwrap();
        for value in 1..=3 {
            journal.append(digest(value)).unwrap();
        }
        assert_eq!(journal.oldest_sequence(), Some(2));
        assert_eq!(
            journal.replay_after(before_first, &auth),
            Err(ReconnectJournalError::CursorExpired {
                requested_next: 1,
                oldest_available: 2,
            })
        );
    }

    #[test]
    fn cursor_ahead_of_producer_fails_closed() {
        let auth = TestAuthenticator([0x77; 32]);
        let mut journal = journal(identity(1, 2, 3, 4), 2);
        journal.append(digest(1)).unwrap();
        assert_eq!(
            journal.issue_cursor(2, &auth),
            Err(ReconnectJournalError::CursorAhead {
                cursor: 2,
                latest: 1,
            })
        );
    }

    #[test]
    fn zero_event_digest_and_unbounded_capacity_are_rejected() {
        let mut journal = journal(identity(1, 2, 3, 4), 2);
        assert_eq!(
            journal.append([0; 32]),
            Err(ReconnectJournalError::ZeroDigest("event_digest"))
        );
        assert!(matches!(
            ReconnectJournal::new(ReconnectJournalConfig {
                capacity: MAX_RECONNECT_JOURNAL_CAPACITY + 1,
                identity: identity(1, 2, 3, 4),
            }),
            Err(ReconnectJournalError::CapacityTooLarge { .. })
        ));
    }
}
