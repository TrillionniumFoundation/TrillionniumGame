//! Bounded reconnect journal with contiguous, monotonic cursors.
//!
//! A transport may use this structure to retain a finite replay window.  A
//! cursor ahead of the producer or older than the retained window fails
//! explicitly; neither condition is silently normalized to an empty replay.

use std::collections::VecDeque;
use std::fmt;

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct ReconnectCursor(u64);

impl ReconnectCursor {
    pub const fn before_first() -> Self {
        Self(0)
    }

    pub const fn new(sequence: u64) -> Self {
        Self(sequence)
    }

    pub const fn get(self) -> u64 {
        self.0
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ReconnectEvent {
    pub sequence: u64,
    pub digest: [u8; 32],
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ReconnectJournalConfig {
    pub capacity: usize,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ReconnectJournalError {
    ZeroCapacity,
    SequenceExhausted,
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
            Self::SequenceExhausted => formatter.write_str("reconnect event sequence exhausted"),
            Self::CursorAhead { cursor, latest } => {
                write!(formatter, "reconnect cursor {cursor} is ahead of latest sequence {latest}")
            }
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
    capacity: usize,
    next_sequence: u64,
    events: VecDeque<ReconnectEvent>,
}

impl ReconnectJournal {
    pub fn new(config: ReconnectJournalConfig) -> Result<Self, ReconnectJournalError> {
        if config.capacity == 0 {
            return Err(ReconnectJournalError::ZeroCapacity);
        }
        Ok(Self {
            capacity: config.capacity,
            next_sequence: 1,
            events: VecDeque::with_capacity(config.capacity),
        })
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
        self.verify_invariants()?;
        let sequence = self.next_sequence;
        let next = sequence
            .checked_add(1)
            .ok_or(ReconnectJournalError::SequenceExhausted)?;
        if self.events.len() == self.capacity {
            self.events.pop_front();
        }
        let event = ReconnectEvent { sequence, digest };
        self.events.push_back(event);
        self.next_sequence = next;
        self.verify_invariants()?;
        Ok(event)
    }

    pub fn replay_after(
        &self,
        cursor: ReconnectCursor,
    ) -> Result<Vec<ReconnectEvent>, ReconnectJournalError> {
        self.verify_invariants()?;
        let latest = self.latest_sequence();
        if cursor.get() > latest {
            return Err(ReconnectJournalError::CursorAhead {
                cursor: cursor.get(),
                latest,
            });
        }
        if self.events.is_empty() || cursor.get() == latest {
            return Ok(Vec::new());
        }
        let requested_next = cursor
            .get()
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
            .filter(|event| event.sequence > cursor.get())
            .collect())
    }

    pub fn verify_invariants(&self) -> Result<(), ReconnectJournalError> {
        if self.capacity == 0 {
            return Err(ReconnectJournalError::InvariantViolation(
                "capacity changed to zero",
            ));
        }
        if self.events.len() > self.capacity {
            return Err(ReconnectJournalError::InvariantViolation(
                "retained event count exceeds capacity",
            ));
        }
        let mut previous = None;
        for event in &self.events {
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

#[cfg(test)]
mod tests {
    use super::*;

    fn digest(value: u8) -> [u8; 32] {
        [value; 32]
    }

    #[test]
    fn replay_is_contiguous_and_exclusive_of_the_cursor() {
        let mut journal = ReconnectJournal::new(ReconnectJournalConfig { capacity: 4 }).unwrap();
        for value in 1..=3 {
            journal.append(digest(value)).unwrap();
        }
        assert_eq!(
            journal.replay_after(ReconnectCursor::new(1)).unwrap(),
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
    fn cursor_older_than_retained_window_is_not_silently_accepted() {
        let mut journal = ReconnectJournal::new(ReconnectJournalConfig { capacity: 2 }).unwrap();
        for value in 1..=4 {
            journal.append(digest(value)).unwrap();
        }
        assert_eq!(journal.oldest_sequence(), Some(3));
        assert_eq!(
            journal.replay_after(ReconnectCursor::new(1)),
            Err(ReconnectJournalError::CursorExpired {
                requested_next: 2,
                oldest_available: 3,
            })
        );
        assert_eq!(
            journal.replay_after(ReconnectCursor::new(2)).unwrap(),
            vec![
                ReconnectEvent {
                    sequence: 3,
                    digest: digest(3),
                },
                ReconnectEvent {
                    sequence: 4,
                    digest: digest(4),
                },
            ]
        );
    }

    #[test]
    fn cursor_ahead_of_producer_fails_closed() {
        let mut journal = ReconnectJournal::new(ReconnectJournalConfig { capacity: 2 }).unwrap();
        journal.append(digest(1)).unwrap();
        assert_eq!(
            journal.replay_after(ReconnectCursor::new(2)),
            Err(ReconnectJournalError::CursorAhead {
                cursor: 2,
                latest: 1,
            })
        );
    }

    #[test]
    fn before_first_cursor_replays_only_when_first_event_is_retained() {
        let mut journal = ReconnectJournal::new(ReconnectJournalConfig { capacity: 2 }).unwrap();
        journal.append(digest(1)).unwrap();
        assert_eq!(
            journal.replay_after(ReconnectCursor::before_first()).unwrap(),
            vec![ReconnectEvent {
                sequence: 1,
                digest: digest(1),
            }]
        );
        journal.append(digest(2)).unwrap();
        journal.append(digest(3)).unwrap();
        assert_eq!(
            journal.replay_after(ReconnectCursor::before_first()),
            Err(ReconnectJournalError::CursorExpired {
                requested_next: 1,
                oldest_available: 2,
            })
        );
    }
}
