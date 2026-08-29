use crate::types::PresenceRecord;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MutationDisposition {
    Applied,
    Idempotent,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PresenceDelta {
    pub disposition: MutationDisposition,
    pub revision: Option<u64>,
    pub joins: Vec<PresenceRecord>,
    pub updates: Vec<PresenceRecord>,
    pub leaves: Vec<PresenceRecord>,
    pub hidden_changes: usize,
}

impl PresenceDelta {
    pub(super) fn idempotent() -> Self {
        Self {
            disposition: MutationDisposition::Idempotent,
            revision: None,
            joins: Vec::new(),
            updates: Vec::new(),
            leaves: Vec::new(),
            hidden_changes: 0,
        }
    }

    pub(super) fn applied(
        revision: u64,
        joins: Vec<PresenceRecord>,
        updates: Vec<PresenceRecord>,
        leaves: Vec<PresenceRecord>,
        hidden_changes: usize,
    ) -> Self {
        Self {
            disposition: MutationDisposition::Applied,
            revision: Some(revision),
            joins,
            updates,
            leaves,
            hidden_changes,
        }
    }
}
