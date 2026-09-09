use audit_events::AuditEvent;
use std::collections::{HashMap, HashSet};

pub type ProposalId = u64;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ProposalStatus {
    Pending,
    Queued,
    Executed,
    Cancelled,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ProposalKind {
    ParamChange,
    EmergencyUnpause,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Proposal {
    pub id: ProposalId,
    pub kind: ProposalKind,
    pub proposer: String,
    pub executor: Option<String>,
    pub eta: u64,
    pub param_key: String,
    pub old_value: String,
    pub new_value: String,
    pub base_version: Option<u64>,
    pub reason_hash: String,
    pub status: ProposalStatus,
    pub executed_at: Option<u64>,
}

#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct GovernanceBridgeState {
    pub gov_params: HashMap<String, String>,
    pub param_versions: HashMap<String, u64>,
    pub emergency_paused: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GovernanceEvent {
    ProposalProposed {
        proposal_id: ProposalId,
        proposer: String,
        kind: ProposalKind,
        param_key: Option<String>,
        eta: u64,
    },
    ProposalQueued {
        proposal_id: ProposalId,
        actor: String,
    },
    ProposalExecuted {
        proposal_id: ProposalId,
        actor: String,
        kind: ProposalKind,
        param_key: Option<String>,
        old_value: Option<String>,
        new_value: Option<String>,
        version_before: Option<u64>,
        version_after: Option<u64>,
    },
    ProposalCancelled {
        proposal_id: ProposalId,
        actor: String,
    },
    PauseSet {
        actor: String,
        previous_state: bool,
        next_state: bool,
        reason_hash: String,
    },
    PauseRestoreScheduled {
        proposal_id: ProposalId,
        proposer: String,
        eta: u64,
        reason_hash: String,
    },
    PauseRestoreExecuted {
        proposal_id: ProposalId,
        actor: String,
        reason_hash: String,
    },
    AuditLogCleared,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Error {
    Unauthorized,
    InvalidEta,
    InvalidParamKey,
    ProposalNotFound,
    NotQueued,
    NotReady,
    AlreadyFinalized,
    WrongProposalKind,
    SelfExecutionForbidden,
    PauseNotActive,
    GuardianExecutorConflict,
    WrongProposer,
    CurrentValueMismatch,
    ParamVersionMismatch,
    PauseRestoreAlreadyScheduled,
}

pub type Result<T> = std::result::Result<T, Error>;

#[derive(Debug, Clone)]
pub struct GovernanceGuard {
    admin: String,
    min_timelock_delay_secs: u64,
    nonce: ProposalId,
    proposals: HashMap<ProposalId, Proposal>,
    allowed_param_keys: HashSet<String>,
    proposers: HashSet<String>,
    executors: HashSet<String>,
    guardians: HashSet<String>,
    bridge: GovernanceBridgeState,
    audit_log: Vec<GovernanceEvent>,
}

impl GovernanceGuard {
    pub fn new(
        admin: impl Into<String>,
        guardian: impl Into<String>,
        min_timelock_delay_secs: u64,
    ) -> Self {
        let admin = admin.into();
        let guardian = guardian.into();
        let mut guardians = HashSet::new();
        guardians.insert(guardian);

        Self {
            admin,
            min_timelock_delay_secs,
            nonce: 1,
            proposals: HashMap::new(),
            allowed_param_keys: HashSet::new(),
            proposers: HashSet::new(),
            executors: HashSet::new(),
            guardians,
            bridge: GovernanceBridgeState::default(),
            audit_log: Vec::new(),
        }
    }

    pub fn set_role(
        &mut self,
        caller: &str,
        who: impl Into<String>,
        can_propose: bool,
        can_execute: bool,
    ) -> Result<()> {
        self.require_admin(caller)?;
        let who = who.into();
        Self::set_membership(&mut self.proposers, &who, can_propose);
        Self::set_membership(&mut self.executors, &who, can_execute);
        Ok(())
    }

    pub fn set_guardian(
        &mut self,
        caller: &str,
        who: impl Into<String>,
        enabled: bool,
    ) -> Result<()> {
        self.require_admin(caller)?;
        let who = who.into();
        Self::set_membership(&mut self.guardians, &who, enabled);
        Ok(())
    }

    pub fn set_allowed_param_key(
        &mut self,
        caller: &str,
        key: impl Into<String>,
        enabled: bool,
    ) -> Result<()> {
        self.require_admin(caller)?;
        let key = key.into();
        Self::set_membership(&mut self.allowed_param_keys, &key, enabled);
        Ok(())
    }

    pub fn propose(
        &mut self,
        caller: &str,
        param_key: impl Into<String>,
        old_value: impl Into<String>,
        new_value: impl Into<String>,
        eta: u64,
        reason_hash: impl Into<String>,
        now: u64,
    ) -> Result<ProposalId> {
        self.require_proposer(caller)?;
        if eta < now.saturating_add(self.min_timelock_delay_secs) {
            return Err(Error::InvalidEta);
        }

        let param_key = param_key.into();
        if !self.allowed_param_keys.contains(&param_key) {
            return Err(Error::InvalidParamKey);
        }

        let id = self.next_id();
        let base_version = self
            .bridge
            .param_versions
            .get(&param_key)
            .copied()
            .unwrap_or_default();

        let proposal = Proposal {
            id,
            kind: ProposalKind::ParamChange,
            proposer: caller.to_owned(),
            executor: None,
            eta,
            param_key: param_key.clone(),
            old_value: old_value.into(),
            new_value: new_value.into(),
            base_version: Some(base_version),
            reason_hash: reason_hash.into(),
            status: ProposalStatus::Pending,
            executed_at: None,
        };
        self.proposals.insert(id, proposal);
        self.audit_log.push(GovernanceEvent::ProposalProposed {
            proposal_id: id,
            proposer: caller.to_owned(),
            kind: ProposalKind::ParamChange,
            param_key: Some(param_key.clone()),
            eta,
        });
        Ok(id)
    }

    pub fn queue(&mut self, caller: &str, proposal_id: ProposalId) -> Result<()> {
        self.require_proposer(caller)?;
        let proposal = self.proposal_mut(proposal_id)?;
        if matches!(
            proposal.status,
            ProposalStatus::Executed | ProposalStatus::Cancelled
        ) {
            return Err(Error::AlreadyFinalized);
        }
        if proposal.proposer != caller {
            return Err(Error::WrongProposer);
        }
        if proposal.kind != ProposalKind::ParamChange {
            return Err(Error::WrongProposalKind);
        }
        if proposal.status == ProposalStatus::Queued {
            return Ok(());
        }
        if proposal.status != ProposalStatus::Pending {
            return Err(Error::NotQueued);
        }
        proposal.status = ProposalStatus::Queued;
        self.audit_log.push(GovernanceEvent::ProposalQueued {
            proposal_id,
            actor: caller.to_owned(),
        });
        Ok(())
    }

    pub fn execute(&mut self, caller: &str, proposal_id: ProposalId, now: u64) -> Result<()> {
        self.require_executor(caller)?;

        let (param_key, new_value, old_value, base_version) = {
            let proposal = self.proposal_mut(proposal_id)?;

            if proposal.kind != ProposalKind::ParamChange {
                return Err(Error::WrongProposalKind);
            }
            if matches!(
                proposal.status,
                ProposalStatus::Executed | ProposalStatus::Cancelled
            ) {
                return Err(Error::AlreadyFinalized);
            }
            if proposal.status != ProposalStatus::Queued {
                return Err(Error::NotQueued);
            }
            if now < proposal.eta {
                return Err(Error::NotReady);
            }

            (
                proposal.param_key.clone(),
                proposal.new_value.clone(),
                proposal.old_value.clone(),
                proposal.base_version,
            )
        };

        if !self.allowed_param_keys.contains(&param_key) {
            return Err(Error::InvalidParamKey);
        }

        let version = self
            .bridge
            .param_versions
            .get(&param_key)
            .copied()
            .unwrap_or_default();
        if base_version != Some(version) {
            return Err(Error::ParamVersionMismatch);
        }

        let current = self
            .bridge
            .gov_params
            .get(&param_key)
            .cloned()
            .unwrap_or_default();
        if current != old_value {
            return Err(Error::CurrentValueMismatch);
        }

        self.bridge
            .gov_params
            .insert(param_key.clone(), new_value.clone());
        let next_version = version.saturating_add(1);
        self.bridge
            .param_versions
            .insert(param_key.clone(), next_version);

        let proposal = self.proposal_mut(proposal_id)?;
        proposal.status = ProposalStatus::Executed;
        proposal.executor = Some(caller.to_owned());
        proposal.executed_at = Some(now);
        self.audit_log.push(GovernanceEvent::ProposalExecuted {
            proposal_id,
            actor: caller.to_owned(),
            kind: ProposalKind::ParamChange,
            param_key: Some(param_key),
            old_value: Some(old_value),
            new_value: Some(new_value),
            version_before: Some(version),
            version_after: Some(next_version),
        });
        Ok(())
    }

    pub fn cancel(&mut self, caller: &str, proposal_id: ProposalId) -> Result<()> {
        let (status, proposer, kind) = {
            let proposal = self.proposal_mut(proposal_id)?;
            if matches!(
                proposal.status,
                ProposalStatus::Executed | ProposalStatus::Cancelled
            ) {
                return Err(Error::AlreadyFinalized);
            }

            (
                proposal.status.clone(),
                proposal.proposer.clone(),
                proposal.kind.clone(),
            )
        };

        let authorized = if self.guardians.contains(caller) {
            true
        } else if proposer == caller {
            match kind {
                ProposalKind::ParamChange => self.proposers.contains(caller),
                ProposalKind::EmergencyUnpause => self.guardians.contains(caller),
            }
        } else {
            false
        };
        if !authorized {
            return Err(Error::Unauthorized);
        }

        let proposal = self.proposal_mut(proposal_id)?;
        if proposal.status != status {
            return Err(Error::AlreadyFinalized);
        }

        proposal.status = ProposalStatus::Cancelled;
        self.audit_log.push(GovernanceEvent::ProposalCancelled {
            proposal_id,
            actor: caller.to_owned(),
        });
        Ok(())
    }

    pub fn emergency_pause(&mut self, caller: &str, reason_hash: impl Into<String>) -> Result<()> {
        self.require_guardian(caller)?;
        let previous_state = self.bridge.emergency_paused;
        if previous_state {
            return Ok(());
        }

        self.bridge.emergency_paused = true;
        self.audit_log.push(GovernanceEvent::PauseSet {
            actor: caller.to_owned(),
            previous_state,
            next_state: true,
            reason_hash: reason_hash.into(),
        });
        Ok(())
    }

    pub fn schedule_unpause(
        &mut self,
        caller: &str,
        eta: u64,
        reason_hash: impl Into<String>,
        now: u64,
    ) -> Result<ProposalId> {
        self.require_guardian(caller)?;
        if !self.bridge.emergency_paused {
            return Err(Error::PauseNotActive);
        }
        if eta < now.saturating_add(self.min_timelock_delay_secs) {
            return Err(Error::InvalidEta);
        }
        if self.proposals.values().any(|proposal| {
            proposal.kind == ProposalKind::EmergencyUnpause
                && matches!(
                    proposal.status,
                    ProposalStatus::Pending | ProposalStatus::Queued
                )
        }) {
            return Err(Error::PauseRestoreAlreadyScheduled);
        }

        let id = self.next_id();
        let reason_hash = reason_hash.into();
        let proposal = Proposal {
            id,
            kind: ProposalKind::EmergencyUnpause,
            proposer: caller.to_owned(),
            executor: None,
            eta,
            param_key: "emergency_pause".to_string(),
            old_value: "true".to_string(),
            new_value: "false".to_string(),
            base_version: None,
            reason_hash: reason_hash.clone(),
            status: ProposalStatus::Queued,
            executed_at: None,
        };
        self.proposals.insert(id, proposal);
        self.audit_log.push(GovernanceEvent::PauseRestoreScheduled {
            proposal_id: id,
            proposer: caller.to_owned(),
            eta,
            reason_hash,
        });
        Ok(id)
    }

    pub fn execute_unpause(
        &mut self,
        caller: &str,
        proposal_id: ProposalId,
        now: u64,
    ) -> Result<()> {
        self.require_executor(caller)?;
        if self.guardians.contains(caller) {
            return Err(Error::GuardianExecutorConflict);
        }

        let proposal_proposer = {
            let proposal = self.proposal_mut(proposal_id)?;

            if proposal.kind != ProposalKind::EmergencyUnpause {
                return Err(Error::WrongProposalKind);
            }
            if matches!(
                proposal.status,
                ProposalStatus::Executed | ProposalStatus::Cancelled
            ) {
                return Err(Error::AlreadyFinalized);
            }
            if proposal.status != ProposalStatus::Queued {
                return Err(Error::NotQueued);
            }
            if now < proposal.eta {
                return Err(Error::NotReady);
            }

            proposal.proposer.clone()
        };

        if !self.guardians.contains(&proposal_proposer) {
            return Err(Error::Unauthorized);
        }
        if proposal_proposer == caller {
            return Err(Error::SelfExecutionForbidden);
        }

        if !self.bridge.emergency_paused {
            return Err(Error::PauseNotActive);
        }

        self.bridge.emergency_paused = false;

        let proposal = self.proposal_mut(proposal_id)?;
        proposal.status = ProposalStatus::Executed;
        proposal.executor = Some(caller.to_owned());
        proposal.executed_at = Some(now);
        let reason_hash = proposal.reason_hash.clone();
        self.audit_log.push(GovernanceEvent::PauseRestoreExecuted {
            proposal_id,
            actor: caller.to_owned(),
            reason_hash,
        });
        self.audit_log.push(GovernanceEvent::ProposalExecuted {
            proposal_id,
            actor: caller.to_owned(),
            kind: ProposalKind::EmergencyUnpause,
            param_key: Some("emergency_pause".to_string()),
            old_value: Some("true".to_string()),
            new_value: Some("false".to_string()),
            version_before: None,
            version_after: None,
        });
        Ok(())
    }

    pub fn proposal(&self, id: ProposalId) -> Option<&Proposal> {
        self.proposals.get(&id)
    }

    pub fn bridge_state(&self) -> &GovernanceBridgeState {
        &self.bridge
    }

    pub fn audit_log(&self) -> &[GovernanceEvent] {
        &self.audit_log
    }

    pub fn consume_audit_log(&mut self) -> Vec<GovernanceEvent> {
        let mut consumed = std::mem::take(&mut self.audit_log);
        consumed.push(GovernanceEvent::AuditLogCleared);
        consumed
    }

    pub fn normalized_audit_log(&self) -> Vec<AuditEvent> {
        self.audit_log
            .iter()
            .map(|event| self.normalize_audit_event(event))
            .collect()
    }

    fn apply_normalized_proposal_context(
        &self,
        proposal_id: ProposalId,
        normalized: &mut AuditEvent,
    ) {
        let Some(proposal) = self.proposals.get(&proposal_id) else {
            return;
        };

        normalized.reason = Some(format!("kind={:?}", proposal.kind));
        if !proposal.param_key.is_empty() {
            normalized.related_id = Some(proposal.param_key.clone());
        }
    }

    fn normalize_audit_event(&self, event: &GovernanceEvent) -> AuditEvent {
        match event {
            GovernanceEvent::ProposalProposed {
                proposal_id,
                proposer,
                kind,
                param_key,
                eta,
            } => {
                let mut normalized =
                    AuditEvent::new("governance-guard", "governance.proposal_proposed");
                normalized.actor = Some(proposer.clone());
                normalized.object_id = Some(proposal_id.to_string());
                normalized.reason = Some(format!("kind={kind:?}"));
                if let Some(key) = param_key {
                    normalized.related_id = Some(key.clone());
                }
                normalized.note = Some(format!("eta={eta}"));
                normalized
            }
            GovernanceEvent::ProposalQueued { proposal_id, actor } => {
                let mut normalized =
                    AuditEvent::new("governance-guard", "governance.proposal_queued");
                normalized.actor = Some(actor.clone());
                normalized.object_id = Some(proposal_id.to_string());
                self.apply_normalized_proposal_context(*proposal_id, &mut normalized);
                normalized
            }
            GovernanceEvent::ProposalExecuted {
                proposal_id,
                actor,
                kind,
                param_key,
                old_value,
                new_value,
                version_before,
                version_after,
            } => {
                let mut normalized =
                    AuditEvent::new("governance-guard", "governance.proposal_executed");
                normalized.actor = Some(actor.clone());
                normalized.object_id = Some(proposal_id.to_string());
                normalized.reason = Some(format!("kind={kind:?}"));
                if let Some(key) = param_key {
                    normalized.related_id = Some(key.clone());
                }
                if let (Some(before), Some(after)) = (version_before, version_after) {
                    normalized.amount = Some((*after - *before) as u128);
                    normalized.note = Some(format!("version={before}->{after}"));
                }
                if let (Some(old), Some(new_value)) = (old_value, new_value) {
                    normalized.note = Some(match normalized.note.take() {
                        Some(existing_note) => {
                            format!("value={old}->{new_value}, {existing_note}")
                        }
                        None => format!("value={old}->{new_value}"),
                    });
                }
                normalized
            }
            GovernanceEvent::ProposalCancelled { proposal_id, actor } => {
                let mut normalized =
                    AuditEvent::new("governance-guard", "governance.proposal_cancelled");
                normalized.actor = Some(actor.clone());
                normalized.object_id = Some(proposal_id.to_string());
                self.apply_normalized_proposal_context(*proposal_id, &mut normalized);
                normalized
            }
            GovernanceEvent::PauseSet {
                actor,
                previous_state,
                next_state,
                reason_hash,
            } => {
                let mut normalized = AuditEvent::new("governance-guard", "governance.pause_set");
                normalized.actor = Some(actor.clone());
                normalized.object_id = Some("emergency_pause".to_string());
                normalized.related_id = Some("pause_state".to_string());
                normalized.reason = Some("pause_activation".to_string());
                normalized.note = Some(format!(
                    "state={previous_state}->{next_state}, reason_hash={reason_hash}"
                ));
                normalized
            }
            GovernanceEvent::PauseRestoreScheduled {
                proposal_id,
                proposer,
                eta,
                reason_hash,
            } => {
                let mut normalized =
                    AuditEvent::new("governance-guard", "governance.pause_restore_scheduled");
                normalized.actor = Some(proposer.clone());
                normalized.object_id = Some("emergency_pause".to_string());
                normalized.related_id = Some(proposal_id.to_string());
                normalized.reason = Some("pause_restore_schedule".to_string());
                normalized.note = Some(format!("eta={eta}, reason_hash={reason_hash}"));
                normalized
            }
            GovernanceEvent::PauseRestoreExecuted {
                proposal_id,
                actor,
                reason_hash,
            } => {
                let mut normalized =
                    AuditEvent::new("governance-guard", "governance.pause_restore_executed");
                normalized.actor = Some(actor.clone());
                normalized.object_id = Some("emergency_pause".to_string());
                normalized.related_id = Some(proposal_id.to_string());
                normalized.reason = Some("pause_restore_execution".to_string());
                normalized.note = Some(format!("reason_hash={reason_hash}"));
                normalized
            }
            GovernanceEvent::AuditLogCleared => {
                AuditEvent::new("governance-guard", "governance.audit_log_cleared")
            }
        }
    }

    fn require_admin(&self, caller: &str) -> Result<()> {
        if caller == self.admin {
            Ok(())
        } else {
            Err(Error::Unauthorized)
        }
    }

    fn require_proposer(&self, caller: &str) -> Result<()> {
        if self.proposers.contains(caller) {
            Ok(())
        } else {
            Err(Error::Unauthorized)
        }
    }

    fn require_executor(&self, caller: &str) -> Result<()> {
        if self.executors.contains(caller) {
            Ok(())
        } else {
            Err(Error::Unauthorized)
        }
    }

    fn require_guardian(&self, caller: &str) -> Result<()> {
        if self.guardians.contains(caller) {
            Ok(())
        } else {
            Err(Error::Unauthorized)
        }
    }

    fn proposal_mut(&mut self, id: ProposalId) -> Result<&mut Proposal> {
        self.proposals.get_mut(&id).ok_or(Error::ProposalNotFound)
    }

    fn next_id(&mut self) -> ProposalId {
        let id = self.nonce;
        self.nonce = self.nonce.saturating_add(1);
        id
    }

    fn set_membership(set: &mut HashSet<String>, value: &str, enabled: bool) {
        if enabled {
            set.insert(value.to_owned());
        } else {
            set.remove(value);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn setup() -> GovernanceGuard {
        let mut gov = GovernanceGuard::new("admin", "guardian", 60);
        gov.set_role("admin", "alice", true, false).unwrap();
        gov.set_role("admin", "exec", false, true).unwrap();
        gov.set_allowed_param_key("admin", "challenge_window_blocks", true)
            .unwrap();
        gov.bridge
            .gov_params
            .insert("challenge_window_blocks".to_string(), "100".to_string());
        gov
    }

    fn seed_param(gov: &mut GovernanceGuard, value: &str) {
        gov.bridge
            .gov_params
            .insert("challenge_window_blocks".to_string(), value.to_string());
    }

    #[test]
    fn timelock_bypass_fails_closed_without_side_effects() {
        let mut gov = setup();
        let now = 1_000;
        let eta = now + 60;

        let pid = gov
            .propose(
                "alice",
                "challenge_window_blocks",
                "100",
                "120",
                eta,
                "reason-1",
                now,
            )
            .unwrap();
        gov.queue("alice", pid).unwrap();

        let before = gov
            .bridge_state()
            .gov_params
            .get("challenge_window_blocks")
            .cloned();
        let err = gov.execute("exec", pid, eta - 1).unwrap_err();
        assert_eq!(err, Error::NotReady);

        let p = gov.proposal(pid).unwrap();
        assert_eq!(p.status, ProposalStatus::Queued);
        assert_eq!(
            before,
            gov.bridge_state()
                .gov_params
                .get("challenge_window_blocks")
                .cloned()
        );
    }

    #[test]
    fn duplicate_execute_is_blocked() {
        let mut gov = setup();
        let now = 2_000;
        let eta = now + 60;

        let pid = gov
            .propose(
                "alice",
                "challenge_window_blocks",
                "100",
                "160",
                eta,
                "reason-2",
                now,
            )
            .unwrap();
        gov.queue("alice", pid).unwrap();

        gov.execute("exec", pid, eta).unwrap();
        let second = gov.execute("exec", pid, eta + 1).unwrap_err();
        assert_eq!(second, Error::AlreadyFinalized);
    }

    #[test]
    fn execute_rejects_if_param_value_drifts_before_execution() {
        let mut gov = setup();
        let now = 2_500;
        let eta = now + 60;

        let pid = gov
            .propose(
                "alice",
                "challenge_window_blocks",
                "100",
                "200",
                eta,
                "reason-stale",
                now,
            )
            .unwrap();
        gov.queue("alice", pid).unwrap();

        seed_param(&mut gov, "99");
        let audit_len_before_rejected_execute = gov.audit_log().len();
        let err = gov.execute("exec", pid, eta).unwrap_err();
        assert_eq!(err, Error::CurrentValueMismatch);

        let p = gov.proposal(pid).unwrap();
        assert_eq!(p.status, ProposalStatus::Queued);
        assert_eq!(p.executor, None);
        assert_eq!(p.executed_at, None);
        assert_eq!(
            gov.bridge_state()
                .gov_params
                .get("challenge_window_blocks")
                .map(String::as_str),
            Some("99")
        );
        assert_eq!(
            gov.bridge_state()
                .param_versions
                .get("challenge_window_blocks")
                .copied(),
            None
        );
        assert_eq!(gov.audit_log().len(), audit_len_before_rejected_execute);
        assert!(!gov.audit_log().iter().any(|event| matches!(
            event,
            GovernanceEvent::ProposalExecuted { proposal_id, .. } if *proposal_id == pid
        )));
    }

    #[test]
    fn execute_rejects_if_param_version_shifted() {
        let mut gov = setup();
        let now = 3_200;
        let eta = now + 60;

        let pid = gov
            .propose(
                "alice",
                "challenge_window_blocks",
                "100",
                "130",
                eta,
                "reason-a",
                now,
            )
            .unwrap();
        let pid2 = gov
            .propose(
                "alice",
                "challenge_window_blocks",
                "100",
                "140",
                eta,
                "reason-b",
                now,
            )
            .unwrap();

        gov.queue("alice", pid).unwrap();
        gov.queue("alice", pid2).unwrap();

        gov.execute("exec", pid, eta).unwrap();
        let audit_len_before_rejected_execute = gov.audit_log().len();

        let err = gov.execute("exec", pid2, eta).unwrap_err();
        assert_eq!(err, Error::ParamVersionMismatch);

        let p = gov.proposal(pid2).unwrap();
        assert_eq!(p.status, ProposalStatus::Queued);
        assert_eq!(p.executor, None);
        assert_eq!(p.executed_at, None);
        assert_eq!(
            gov.bridge_state()
                .gov_params
                .get("challenge_window_blocks")
                .map(String::as_str),
            Some("130")
        );
        assert_eq!(
            gov.bridge_state()
                .param_versions
                .get("challenge_window_blocks")
                .copied(),
            Some(1)
        );
        assert_eq!(gov.audit_log().len(), audit_len_before_rejected_execute);
        assert!(!gov.audit_log().iter().any(|event| matches!(
            event,
            GovernanceEvent::ProposalExecuted { proposal_id, .. } if *proposal_id == pid2
        )));
    }

    #[test]
    fn execute_rejects_if_param_version_drifts_without_value_change() {
        let mut gov = setup();
        let now = 3_250;
        let eta = now + 60;

        let pid = gov
            .propose(
                "alice",
                "challenge_window_blocks",
                "100",
                "141",
                eta,
                "reason-version-drift",
                now,
            )
            .unwrap();
        gov.queue("alice", pid).unwrap();

        gov.bridge
            .param_versions
            .insert("challenge_window_blocks".to_string(), 7);
        let audit_len_before_rejected_execute = gov.audit_log().len();

        let err = gov.execute("exec", pid, eta).unwrap_err();
        assert_eq!(err, Error::ParamVersionMismatch);

        let proposal = gov.proposal(pid).unwrap();
        assert_eq!(proposal.status, ProposalStatus::Queued);
        assert_eq!(proposal.executor, None);
        assert_eq!(proposal.executed_at, None);
        assert_eq!(
            gov.bridge_state()
                .gov_params
                .get("challenge_window_blocks")
                .map(String::as_str),
            Some("100")
        );
        assert_eq!(
            gov.bridge_state()
                .param_versions
                .get("challenge_window_blocks")
                .copied(),
            Some(7)
        );
        assert_eq!(gov.audit_log().len(), audit_len_before_rejected_execute);
        assert!(!gov.audit_log().iter().any(|event| matches!(
            event,
            GovernanceEvent::ProposalExecuted { proposal_id, .. } if *proposal_id == pid
        )));
    }

    #[test]
    fn execute_rejects_if_param_version_and_value_both_drift() {
        let mut gov = setup();
        let now = 3_275;
        let eta = now + 60;

        let pid = gov
            .propose(
                "alice",
                "challenge_window_blocks",
                "100",
                "142",
                eta,
                "reason-version-and-value-drift",
                now,
            )
            .unwrap();
        gov.queue("alice", pid).unwrap();

        gov.bridge
            .gov_params
            .insert("challenge_window_blocks".to_string(), "777".to_string());
        gov.bridge
            .param_versions
            .insert("challenge_window_blocks".to_string(), 9);
        let audit_len_before_rejected_execute = gov.audit_log().len();

        let err = gov.execute("exec", pid, eta).unwrap_err();
        assert_eq!(err, Error::ParamVersionMismatch);

        let proposal = gov.proposal(pid).unwrap();
        assert_eq!(proposal.status, ProposalStatus::Queued);
        assert_eq!(proposal.executor, None);
        assert_eq!(proposal.executed_at, None);
        assert_eq!(
            gov.bridge_state()
                .gov_params
                .get("challenge_window_blocks")
                .map(String::as_str),
            Some("777")
        );
        assert_eq!(
            gov.bridge_state()
                .param_versions
                .get("challenge_window_blocks")
                .copied(),
            Some(9)
        );
        assert_eq!(gov.audit_log().len(), audit_len_before_rejected_execute);
        assert!(!gov.audit_log().iter().any(|event| matches!(
            event,
            GovernanceEvent::ProposalExecuted { proposal_id, .. } if *proposal_id == pid
        )));
    }

    #[test]
    fn execute_rejects_if_param_key_removed_after_queue() {
        let mut gov = setup();
        let now = 3_300;
        let eta = now + 60;

        let pid = gov
            .propose(
                "alice",
                "challenge_window_blocks",
                "100",
                "135",
                eta,
                "reason-key-revoked",
                now,
            )
            .unwrap();
        gov.queue("alice", pid).unwrap();

        gov.set_allowed_param_key("admin", "challenge_window_blocks", false)
            .unwrap();
        let audit_len_before_rejected_execute = gov.audit_log().len();

        let err = gov.execute("exec", pid, eta).unwrap_err();
        assert_eq!(err, Error::InvalidParamKey);

        let p = gov.proposal(pid).unwrap();
        assert_eq!(p.status, ProposalStatus::Queued);
        assert_eq!(p.executor, None);
        assert_eq!(p.executed_at, None);
        assert_eq!(
            gov.bridge_state()
                .gov_params
                .get("challenge_window_blocks")
                .map(String::as_str),
            Some("100")
        );
        assert_eq!(
            gov.bridge_state()
                .param_versions
                .get("challenge_window_blocks")
                .copied(),
            None
        );
        assert_eq!(gov.audit_log().len(), audit_len_before_rejected_execute);
        assert!(!gov.audit_log().iter().any(|event| matches!(
            event,
            GovernanceEvent::ProposalExecuted { proposal_id, .. } if *proposal_id == pid
        )));
    }

    #[test]
    fn audit_log_records_param_and_pause_events() {
        let mut gov = setup();
        let now = 8_000;
        let eta = now + 60;

        let pid = gov
            .propose(
                "alice",
                "challenge_window_blocks",
                "100",
                "170",
                eta,
                "reason-audit",
                now,
            )
            .unwrap();
        gov.queue("alice", pid).unwrap();
        gov.execute("exec", pid, eta - 1).unwrap_err();

        gov.execute("exec", pid, eta).unwrap();

        gov.emergency_pause("guardian", "incident").unwrap();
        let restore_eta = eta + 60;
        let restore_id = gov
            .schedule_unpause("guardian", restore_eta, "recover", eta)
            .unwrap();
        gov.execute_unpause("exec", restore_id, restore_eta)
            .unwrap();

        let normalized = gov.normalized_audit_log();
        let logs = gov.consume_audit_log();

        assert!(logs.iter().any(|event| matches!(
            event,
            GovernanceEvent::ProposalProposed { proposal_id, proposer, kind: ProposalKind::ParamChange, eta: logged_eta, .. }
                if *proposal_id == pid
                    && proposer == "alice"
                    && *logged_eta == eta
        )));
        assert!(logs.iter().any(|event| matches!(
            event,
            GovernanceEvent::ProposalQueued { proposal_id, actor } if *proposal_id == pid && actor == "alice"
        )));
        assert!(logs.iter().any(|event| matches!(
            event,
            GovernanceEvent::ProposalExecuted {
                proposal_id,
                actor,
                kind: ProposalKind::ParamChange,
                version_before,
                version_after,
                ..
            } if *proposal_id == pid
                && actor == "exec"
                && version_before == &Some(0)
                && version_after == &Some(1)
        )));
        assert!(logs.iter().any(|event| matches!(
            event,
            GovernanceEvent::PauseSet { actor, previous_state, next_state, reason_hash }
                if actor == "guardian"
                    && !*previous_state
                    && *next_state
                    && reason_hash == "incident"
        )));
        assert!(logs.iter().any(|event| matches!(
            event,
            GovernanceEvent::PauseRestoreScheduled {
                proposal_id,
                proposer,
                eta: logged_restore_eta,
                reason_hash,
            } if *proposal_id == restore_id
                && proposer == "guardian"
                && *logged_restore_eta == restore_eta
                && reason_hash == "recover"
        )));
        assert!(logs.iter().any(|event| matches!(
            event,
            GovernanceEvent::PauseRestoreExecuted {
                proposal_id,
                actor,
                reason_hash,
            } if *proposal_id == restore_id
                && actor == "exec"
                && reason_hash == "recover"
        )));

        assert!(normalized
            .iter()
            .any(|event| event.event_type == "governance.proposal_executed"));
        assert!(normalized.iter().any(|event| {
            event.event_type == "governance.pause_set"
                && event.object_id.as_deref() == Some("emergency_pause")
                && event.related_id.as_deref() == Some("pause_state")
                && event.reason.as_deref() == Some("pause_activation")
                && event.note.as_deref() == Some("state=false->true, reason_hash=incident")
        }));
        assert!(normalized.iter().any(|event| {
            event.event_type == "governance.pause_restore_scheduled"
                && event.object_id.as_deref() == Some("emergency_pause")
                && event.related_id.as_deref() == Some(&restore_id.to_string())
                && event.reason.as_deref() == Some("pause_restore_schedule")
                && event.note.as_deref() == Some("eta=8120, reason_hash=recover")
        }));
        assert!(normalized.iter().any(|event| {
            event.event_type == "governance.pause_restore_executed"
                && event.object_id.as_deref() == Some("emergency_pause")
                && event.related_id.as_deref() == Some(&restore_id.to_string())
                && event.reason.as_deref() == Some("pause_restore_execution")
                && event.note.as_deref() == Some("reason_hash=recover")
        }));
        assert!(normalized
            .iter()
            .any(|event| event.source == "governance-guard"));
        assert!(logs
            .iter()
            .any(|event| matches!(event, GovernanceEvent::AuditLogCleared)));

        assert!(gov.audit_log().is_empty());
    }

    #[test]
    fn execute_fails_closed_if_executor_role_revoked_after_queue() {
        let mut gov = setup();
        let now = 3_360;
        let eta = now + 60;

        let pid = gov
            .propose(
                "alice",
                "challenge_window_blocks",
                "100",
                "140",
                eta,
                "reason-executor-role-drift",
                now,
            )
            .unwrap();
        gov.queue("alice", pid).unwrap();
        gov.set_role("admin", "exec", true, false).unwrap();
        let audit_len_before_rejected_execute = gov.audit_log().len();

        let err = gov.execute("exec", pid, eta).unwrap_err();
        assert_eq!(err, Error::Unauthorized);

        let proposal = gov.proposal(pid).unwrap();
        assert_eq!(proposal.status, ProposalStatus::Queued);
        assert_eq!(proposal.executor, None);
        assert_eq!(proposal.executed_at, None);
        assert_eq!(
            gov.bridge_state()
                .gov_params
                .get("challenge_window_blocks")
                .map(String::as_str),
            Some("100")
        );
        assert_eq!(
            gov.bridge_state()
                .param_versions
                .get("challenge_window_blocks")
                .copied(),
            None
        );
        assert_eq!(gov.audit_log().len(), audit_len_before_rejected_execute);
        assert!(!gov.audit_log().iter().any(|event| matches!(
            event,
            GovernanceEvent::ProposalExecuted { proposal_id, .. } if *proposal_id == pid
        )));
    }

    #[test]
    fn queue_rejects_non_proposer_caller() {
        let mut gov = setup();
        gov.set_role("admin", "bob", true, false).unwrap();

        let now = 2_800;
        let eta = now + 60;
        let pid = gov
            .propose(
                "alice",
                "challenge_window_blocks",
                "100",
                "130",
                eta,
                "reason-3",
                now,
            )
            .unwrap();

        let err = gov.queue("bob", pid).unwrap_err();
        assert_eq!(err, Error::WrongProposer);

        let p = gov.proposal(pid).unwrap();
        assert_eq!(p.status, ProposalStatus::Pending);
    }

    #[test]
    fn queue_is_idempotent_without_duplicate_audit_events() {
        let mut gov = setup();
        let now = 2_850;
        let eta = now + 60;
        let pid = gov
            .propose(
                "alice",
                "challenge_window_blocks",
                "100",
                "131",
                eta,
                "reason-queue-idempotent",
                now,
            )
            .unwrap();

        gov.queue("alice", pid).unwrap();
        let audit_len_after_first_queue = gov.audit_log().len();

        gov.queue("alice", pid).unwrap();

        let proposal = gov.proposal(pid).unwrap();
        assert_eq!(proposal.status, ProposalStatus::Queued);
        assert_eq!(gov.audit_log().len(), audit_len_after_first_queue);
        assert_eq!(
            gov.audit_log()
                .iter()
                .filter(|event| matches!(
                    event,
                    GovernanceEvent::ProposalQueued { proposal_id, actor }
                        if *proposal_id == pid && actor == "alice"
                ))
                .count(),
            1
        );
    }

    #[test]
    fn queue_rejects_scheduled_unpause_even_if_guardian_is_also_proposer() {
        let mut gov = setup();
        gov.set_role("admin", "guardian", true, false).unwrap();

        let now = 2_860;
        let eta = now + 60;
        gov.emergency_pause("guardian", "incident-queue-wrong-kind")
            .unwrap();
        let pid = gov
            .schedule_unpause("guardian", eta, "recover-queue-wrong-kind", now)
            .unwrap();
        let audit_len_before = gov.audit_log().len();

        assert_eq!(
            gov.queue("guardian", pid).unwrap_err(),
            Error::WrongProposalKind
        );

        let proposal = gov.proposal(pid).unwrap();
        assert_eq!(proposal.kind, ProposalKind::EmergencyUnpause);
        assert_eq!(proposal.status, ProposalStatus::Queued);
        assert_eq!(proposal.proposer, "guardian");
        assert_eq!(proposal.executor, None);
        assert_eq!(proposal.executed_at, None);
        assert!(gov.bridge_state().emergency_paused);
        assert_eq!(gov.audit_log().len(), audit_len_before);
        assert_eq!(
            gov.audit_log()
                .iter()
                .filter(|event| matches!(
                    event,
                    GovernanceEvent::ProposalQueued { proposal_id, actor }
                        if *proposal_id == pid && actor == "guardian"
                ))
                .count(),
            0
        );
    }

    #[test]
    fn queue_rejects_proposer_after_proposer_role_revoked() {
        let mut gov = setup();
        let now = 2_875;
        let eta = now + 60;
        let pid = gov
            .propose(
                "alice",
                "challenge_window_blocks",
                "100",
                "132",
                eta,
                "reason-queue-role-drift",
                now,
            )
            .unwrap();
        let audit_len_before = gov.audit_log().len();

        gov.set_role("admin", "alice", false, false).unwrap();

        assert_eq!(gov.queue("alice", pid).unwrap_err(), Error::Unauthorized);

        let proposal = gov.proposal(pid).unwrap();
        assert_eq!(proposal.status, ProposalStatus::Pending);
        assert_eq!(proposal.executor, None);
        assert_eq!(proposal.executed_at, None);
        assert_eq!(gov.audit_log().len(), audit_len_before);
        assert!(!gov.audit_log().iter().any(|event| matches!(
            event,
            GovernanceEvent::ProposalQueued { proposal_id, .. } if *proposal_id == pid
        )));
    }

    #[test]
    fn proposer_can_cancel_own_proposal() {
        let mut gov = setup();
        let now = 2_900;
        let eta = now + 60;
        let pid = gov
            .propose(
                "alice",
                "challenge_window_blocks",
                "100",
                "140",
                eta,
                "reason-cancel",
                now,
            )
            .unwrap();

        gov.cancel("alice", pid).unwrap();
        let p = gov.proposal(pid).unwrap();
        assert_eq!(p.status, ProposalStatus::Cancelled);
    }

    #[test]
    fn cancelled_proposal_cannot_be_requeued_or_emit_duplicate_queue_audit() {
        let mut gov = setup();
        let now = 2_950;
        let eta = now + 60;
        let pid = gov
            .propose(
                "alice",
                "challenge_window_blocks",
                "100",
                "145",
                eta,
                "reason-cancelled-requeue",
                now,
            )
            .unwrap();

        gov.cancel("alice", pid).unwrap();
        let audit_len_before_requeue = gov.audit_log().len();

        assert_eq!(
            gov.queue("alice", pid).unwrap_err(),
            Error::AlreadyFinalized
        );

        let proposal = gov.proposal(pid).unwrap();
        assert_eq!(proposal.status, ProposalStatus::Cancelled);
        assert_eq!(gov.audit_log().len(), audit_len_before_requeue);
        assert_eq!(
            gov.audit_log()
                .iter()
                .filter(|event| matches!(
                    event,
                    GovernanceEvent::ProposalQueued { proposal_id, actor }
                        if *proposal_id == pid && actor == "alice"
                ))
                .count(),
            0
        );
    }

    #[test]
    fn guardian_can_cancel_any_active_proposal() {
        let mut gov = setup();
        let now = 3_000;
        let eta = now + 60;
        let pid = gov
            .propose(
                "alice",
                "challenge_window_blocks",
                "100",
                "150",
                eta,
                "reason-4",
                now,
            )
            .unwrap();

        gov.cancel("guardian", pid).unwrap();
        let p = gov.proposal(pid).unwrap();
        assert_eq!(p.status, ProposalStatus::Cancelled);
    }

    #[test]
    fn unauthorized_cancel_is_rejected() {
        let mut gov = setup();
        let now = 3_100;
        let eta = now + 60;
        gov.set_role("admin", "bob", true, false).unwrap();

        let pid = gov
            .propose(
                "alice",
                "challenge_window_blocks",
                "100",
                "160",
                eta,
                "reason-5",
                now,
            )
            .unwrap();

        let err = gov.cancel("bob", pid).unwrap_err();
        assert_eq!(err, Error::Unauthorized);

        let p = gov.proposal(pid).unwrap();
        assert_eq!(p.status, ProposalStatus::Pending);
    }

    #[test]
    fn permission_drift_revocation_fails_closed() {
        let mut gov = setup();
        let now = 3_000;
        let eta = now + 60;

        gov.set_role("admin", "alice", false, false).unwrap();
        assert_eq!(
            gov.propose(
                "alice",
                "challenge_window_blocks",
                "100",
                "140",
                eta,
                "reason-4",
                now,
            )
            .unwrap_err(),
            Error::Unauthorized
        );

        gov.set_role("admin", "alice", true, false).unwrap();
        let pid = gov
            .propose(
                "alice",
                "challenge_window_blocks",
                "100",
                "150",
                eta,
                "reason-5",
                now,
            )
            .unwrap();
        gov.queue("alice", pid).unwrap();

        gov.set_role("admin", "exec", false, false).unwrap();
        assert_eq!(
            gov.execute("exec", pid, eta).unwrap_err(),
            Error::Unauthorized
        );

        gov.set_guardian("admin", "guardian", false).unwrap();
        assert_eq!(
            gov.cancel("guardian", pid).unwrap_err(),
            Error::Unauthorized
        );
    }

    #[test]
    fn pause_immediate_unpause_timelocked() {
        let mut gov = setup();
        let now = 4_000;
        gov.emergency_pause("guardian", "incident-1").unwrap();
        assert!(gov.bridge_state().emergency_paused);

        let eta = now + 60;
        let pid = gov
            .schedule_unpause("guardian", eta, "recover-1", now)
            .unwrap();

        assert_eq!(
            gov.execute_unpause("exec", pid, eta - 1).unwrap_err(),
            Error::NotReady
        );
        assert!(gov.bridge_state().emergency_paused);

        gov.execute_unpause("exec", pid, eta).unwrap();
        assert!(!gov.bridge_state().emergency_paused);
    }

    #[test]
    fn repeated_emergency_pause_is_idempotent_and_does_not_duplicate_audit() {
        let mut gov = setup();

        gov.emergency_pause("guardian", "incident-initial").unwrap();
        let audit_len_after_first_pause = gov.audit_log().len();

        gov.emergency_pause("guardian", "incident-repeat").unwrap();

        assert!(gov.bridge_state().emergency_paused);
        assert_eq!(gov.audit_log().len(), audit_len_after_first_pause);
        assert_eq!(
            gov.audit_log()
                .iter()
                .filter(|event| matches!(event, GovernanceEvent::PauseSet { .. }))
                .count(),
            1
        );
        assert!(gov.audit_log().iter().any(|event| matches!(
            event,
            GovernanceEvent::PauseSet {
                actor,
                previous_state,
                next_state,
                reason_hash,
            } if actor == "guardian"
                && !*previous_state
                && *next_state
                && reason_hash == "incident-initial"
        )));
    }

    #[test]
    fn emergency_unpause_requires_distinct_executor() {
        let mut gov = setup();
        gov.set_role("admin", "guardian", false, true).unwrap();

        let now = 5_000;
        let eta = now + 60;
        gov.emergency_pause("guardian", "incident-2").unwrap();
        let pid = gov
            .schedule_unpause("guardian", eta, "recover-2", now)
            .unwrap();

        assert_eq!(
            gov.execute_unpause("guardian", pid, eta).unwrap_err(),
            Error::GuardianExecutorConflict
        );
        assert!(gov.bridge_state().emergency_paused);

        gov.execute_unpause("exec", pid, eta).unwrap();
        assert!(!gov.bridge_state().emergency_paused);
    }

    #[test]
    fn emergency_unpause_rejects_guardian_executor_even_when_not_proposer() {
        let mut gov = setup();
        gov.set_guardian("admin", "guardian2", true).unwrap();
        gov.set_role("admin", "guardian2", false, true).unwrap();

        let now = 5_500;
        let eta = now + 60;
        gov.emergency_pause("guardian", "incident-2b").unwrap();
        let pid = gov
            .schedule_unpause("guardian", eta, "recover-2b", now)
            .unwrap();

        assert_eq!(
            gov.execute_unpause("guardian2", pid, eta).unwrap_err(),
            Error::GuardianExecutorConflict
        );
        assert!(gov.bridge_state().emergency_paused);

        gov.execute_unpause("exec", pid, eta).unwrap();
        assert!(!gov.bridge_state().emergency_paused);
    }

    #[test]
    fn emergency_unpause_self_execution_rejection_preserves_queue_and_audit_state() {
        let mut gov = setup();
        gov.set_role("admin", "guardian", false, true).unwrap();

        let now = 5_800;
        let eta = now + 60;
        gov.emergency_pause("guardian", "incident-self-exec")
            .unwrap();
        let pid = gov
            .schedule_unpause("guardian", eta, "recover-self-exec", now)
            .unwrap();
        let audit_len_before = gov.audit_log().len();

        assert_eq!(
            gov.execute_unpause("guardian", pid, eta).unwrap_err(),
            Error::GuardianExecutorConflict
        );

        let proposal = gov.proposal(pid).unwrap();
        assert_eq!(proposal.status, ProposalStatus::Queued);
        assert_eq!(proposal.executor, None);
        assert_eq!(proposal.executed_at, None);
        assert!(gov.bridge_state().emergency_paused);
        assert_eq!(gov.audit_log().len(), audit_len_before);
        assert!(!gov.audit_log().iter().any(|event| matches!(
            event,
            GovernanceEvent::PauseRestoreExecuted { proposal_id, .. }
                if *proposal_id == pid
        )));
        assert!(!gov.audit_log().iter().any(|event| matches!(
            event,
            GovernanceEvent::ProposalExecuted { proposal_id, .. }
                if *proposal_id == pid
        )));
    }

    #[test]
    fn schedule_unpause_rejects_when_pause_is_not_active() {
        let mut gov = setup();
        let now = 6_000;
        let eta = now + 60;

        assert_eq!(
            gov.schedule_unpause("guardian", eta, "noop-recover", now)
                .unwrap_err(),
            Error::PauseNotActive
        );
    }

    #[test]
    fn schedule_unpause_rejects_eta_shorter_than_min_timelock_without_side_effects() {
        let mut gov = setup();
        let now = 6_100;
        let eta = now + 59;

        gov.emergency_pause("guardian", "incident-short-timelock")
            .unwrap();
        let audit_len_before = gov.audit_log().len();

        assert_eq!(
            gov.schedule_unpause("guardian", eta, "recover-too-early", now)
                .unwrap_err(),
            Error::InvalidEta
        );

        assert!(gov.bridge_state().emergency_paused);
        assert_eq!(gov.proposals.len(), 0);
        assert_eq!(gov.audit_log().len(), audit_len_before);
        assert!(!gov
            .audit_log()
            .iter()
            .any(|event| matches!(event, GovernanceEvent::PauseRestoreScheduled { .. })));
    }

    #[test]
    fn schedule_unpause_rejects_duplicate_active_restore_without_side_effects() {
        let mut gov = setup();
        let now = 6_150;
        let eta = now + 60;

        gov.emergency_pause("guardian", "incident-duplicate-restore")
            .unwrap();
        let first_pid = gov
            .schedule_unpause("guardian", eta, "recover-first", now)
            .unwrap();
        let audit_len_before = gov.audit_log().len();

        assert_eq!(
            gov.schedule_unpause("guardian", eta + 60, "recover-second", now)
                .unwrap_err(),
            Error::PauseRestoreAlreadyScheduled
        );

        assert!(gov.bridge_state().emergency_paused);
        assert_eq!(gov.proposals.len(), 1);
        let proposal = gov.proposal(first_pid).unwrap();
        assert_eq!(proposal.kind, ProposalKind::EmergencyUnpause);
        assert_eq!(proposal.status, ProposalStatus::Queued);
        assert_eq!(proposal.eta, eta);
        assert_eq!(proposal.reason_hash, "recover-first");
        assert_eq!(gov.audit_log().len(), audit_len_before);
        assert_eq!(
            gov.audit_log()
                .iter()
                .filter(|event| matches!(event, GovernanceEvent::PauseRestoreScheduled { .. }))
                .count(),
            1
        );
    }

    #[test]
    fn execute_unpause_rejects_non_executor_caller() {
        let mut gov = setup();
        let now = 6_500;
        let eta = now + 60;

        gov.emergency_pause("guardian", "incident-non-exec")
            .unwrap();
        let pid = gov
            .schedule_unpause("guardian", eta, "recover-non-exec", now)
            .unwrap();

        assert_eq!(
            gov.execute_unpause("alice", pid, eta).unwrap_err(),
            Error::Unauthorized
        );

        let proposal = gov.proposal(pid).unwrap();
        assert_eq!(proposal.status, ProposalStatus::Queued);
        assert_eq!(proposal.executor, None);
        assert_eq!(proposal.executed_at, None);
        assert!(gov.bridge_state().emergency_paused);
        assert!(!gov.audit_log().iter().any(|event| matches!(
            event,
            GovernanceEvent::PauseRestoreExecuted { proposal_id, .. }
                if *proposal_id == pid
        )));
        assert!(!gov.audit_log().iter().any(|event| matches!(
            event,
            GovernanceEvent::ProposalExecuted { proposal_id, .. }
                if *proposal_id == pid
        )));
    }

    #[test]
    fn execute_unpause_fails_closed_if_executor_role_revoked_after_schedule() {
        let mut gov = setup();
        let now = 6_750;
        let eta = now + 60;

        gov.emergency_pause("guardian", "incident-revoke-exec")
            .unwrap();
        let pid = gov
            .schedule_unpause("guardian", eta, "recover-revoke-exec", now)
            .unwrap();
        let audit_len_before = gov.audit_log().len();

        gov.set_role("admin", "exec", false, false).unwrap();

        assert_eq!(
            gov.execute_unpause("exec", pid, eta).unwrap_err(),
            Error::Unauthorized
        );

        let proposal = gov.proposal(pid).unwrap();
        assert_eq!(proposal.status, ProposalStatus::Queued);
        assert_eq!(proposal.executor, None);
        assert_eq!(proposal.executed_at, None);
        assert!(gov.bridge_state().emergency_paused);
        assert_eq!(gov.audit_log().len(), audit_len_before);
        assert!(!gov.audit_log().iter().any(|event| matches!(
            event,
            GovernanceEvent::PauseRestoreExecuted { proposal_id, .. }
                if *proposal_id == pid
        )));
        assert!(!gov.audit_log().iter().any(|event| matches!(
            event,
            GovernanceEvent::ProposalExecuted { proposal_id, .. }
                if *proposal_id == pid
        )));
    }

    #[test]
    fn execute_unpause_rejects_if_pause_cleared_before_eta() {
        let mut gov = setup();
        let now = 7_000;
        let eta = now + 60;

        gov.emergency_pause("guardian", "incident-3").unwrap();
        let pid = gov
            .schedule_unpause("guardian", eta, "recover-3", now)
            .unwrap();
        let audit_len_before = gov.audit_log().len();

        gov.bridge.emergency_paused = false;

        assert_eq!(
            gov.execute_unpause("exec", pid, eta).unwrap_err(),
            Error::PauseNotActive
        );

        let proposal = gov.proposal(pid).unwrap();
        assert_eq!(proposal.kind, ProposalKind::EmergencyUnpause);
        assert_eq!(proposal.status, ProposalStatus::Queued);
        assert_eq!(proposal.executor, None);
        assert_eq!(proposal.executed_at, None);
        assert!(!gov.bridge_state().emergency_paused);
        assert_eq!(gov.audit_log().len(), audit_len_before);
        assert!(!gov.audit_log().iter().any(|event| matches!(
            event,
            GovernanceEvent::PauseRestoreExecuted { proposal_id, .. }
                if *proposal_id == pid
        )));
        assert!(!gov.audit_log().iter().any(|event| matches!(
            event,
            GovernanceEvent::ProposalExecuted { proposal_id, .. }
                if *proposal_id == pid
        )));
    }

    #[test]
    fn execute_unpause_fails_closed_if_guardian_role_revoked_after_schedule() {
        let mut gov = setup();
        let now = 7_050;
        let eta = now + 60;

        gov.emergency_pause("guardian", "incident-guardian-revoked")
            .unwrap();
        let pid = gov
            .schedule_unpause("guardian", eta, "recover-guardian-revoked", now)
            .unwrap();
        let audit_len_before = gov.audit_log().len();

        gov.set_guardian("admin", "guardian", false).unwrap();

        assert_eq!(
            gov.execute_unpause("exec", pid, eta).unwrap_err(),
            Error::Unauthorized
        );

        let proposal = gov.proposal(pid).unwrap();
        assert_eq!(proposal.kind, ProposalKind::EmergencyUnpause);
        assert_eq!(proposal.status, ProposalStatus::Queued);
        assert_eq!(proposal.executor, None);
        assert_eq!(proposal.executed_at, None);
        assert!(gov.bridge_state().emergency_paused);
        assert_eq!(gov.audit_log().len(), audit_len_before);
        assert!(!gov.audit_log().iter().any(|event| matches!(
            event,
            GovernanceEvent::PauseRestoreExecuted { proposal_id, .. }
                if *proposal_id == pid
        )));
        assert!(!gov.audit_log().iter().any(|event| matches!(
            event,
            GovernanceEvent::ProposalExecuted { proposal_id, .. }
                if *proposal_id == pid
        )));
    }

    #[test]
    fn normalized_pause_restore_schedule_carries_reason_and_eta() {
        let mut gov = setup();
        let now = 7_080;
        let eta = now + 60;

        gov.emergency_pause("guardian", "incident-schedule-note")
            .unwrap();
        let pid = gov
            .schedule_unpause("guardian", eta, "recover-schedule-note", now)
            .unwrap();

        let normalized = gov.normalized_audit_log();
        let pid_s = pid.to_string();
        let event = normalized
            .iter()
            .find(|event| {
                event.event_type == "governance.pause_restore_scheduled"
                    && event.object_id.as_deref() == Some(pid_s.as_str())
            })
            .unwrap();

        assert_eq!(event.actor.as_deref(), Some("guardian"));
        assert_eq!(event.related_id.as_deref(), Some("emergency_pause"));
        assert_eq!(event.reason.as_deref(), Some("recover-schedule-note"));
        assert_eq!(event.note.as_deref(), Some("eta=7140"));
    }

    #[test]
    fn normalized_unpause_execution_note_omits_placeholder_version_suffix() {
        let mut gov = setup();
        let now = 7_100;
        let eta = now + 60;

        gov.emergency_pause("guardian", "incident-note").unwrap();
        let pid = gov
            .schedule_unpause("guardian", eta, "recover-note", now)
            .unwrap();
        gov.execute_unpause("exec", pid, eta).unwrap();

        let normalized = gov.normalized_audit_log();
        let pid_s = pid.to_string();
        let event = normalized
            .iter()
            .find(|event| {
                event.event_type == "governance.proposal_executed"
                    && event.object_id.as_deref() == Some(pid_s.as_str())
            })
            .unwrap();

        assert_eq!(event.related_id.as_deref(), Some("emergency_pause"));
        assert_eq!(event.note.as_deref(), Some("value=true->false"));
    }

    #[test]
    fn normalized_param_execution_carries_value_and_version_delta() {
        let mut gov = setup();
        let now = 7_150;
        let eta = now + 60;

        let pid = gov
            .propose(
                "alice",
                "challenge_window_blocks",
                "100",
                "175",
                eta,
                "reason-normalized-version",
                now,
            )
            .unwrap();
        gov.queue("alice", pid).unwrap();
        gov.execute("exec", pid, eta).unwrap();

        let normalized = gov.normalized_audit_log();
        let pid_s = pid.to_string();
        let queued = normalized
            .iter()
            .find(|event| {
                event.event_type == "governance.proposal_queued"
                    && event.object_id.as_deref() == Some(pid_s.as_str())
            })
            .unwrap();
        let event = normalized
            .iter()
            .find(|event| {
                event.event_type == "governance.proposal_executed"
                    && event.object_id.as_deref() == Some(pid_s.as_str())
            })
            .unwrap();

        assert_eq!(
            queued.related_id.as_deref(),
            Some("challenge_window_blocks")
        );
        assert_eq!(queued.reason.as_deref(), Some("kind=ParamChange"));
        assert_eq!(event.related_id.as_deref(), Some("challenge_window_blocks"));
        assert_eq!(event.amount, Some(1));
        assert_eq!(event.note.as_deref(), Some("value=100->175, version=0->1"));
        assert_eq!(event.reason.as_deref(), Some("kind=ParamChange"));
    }

    #[test]
    fn execute_rejects_cancelled_param_proposal_as_already_finalized() {
        let mut gov = setup();
        let now = 7_200;
        let eta = now + 60;

        let pid = gov
            .propose(
                "alice",
                "challenge_window_blocks",
                "100",
                "180",
                eta,
                "reason-finalized-param",
                now,
            )
            .unwrap();
        gov.queue("alice", pid).unwrap();
        gov.cancel("alice", pid).unwrap();

        assert_eq!(
            gov.execute("exec", pid, eta).unwrap_err(),
            Error::AlreadyFinalized
        );
    }

    #[test]
    fn execute_unpause_rejects_finalized_proposal_as_already_finalized() {
        let mut gov = setup();
        let now = 7_300;
        let eta = now + 60;

        gov.emergency_pause("guardian", "incident-finalized")
            .unwrap();
        let pid = gov
            .schedule_unpause("guardian", eta, "recover-finalized", now)
            .unwrap();
        gov.execute_unpause("exec", pid, eta).unwrap();

        assert_eq!(
            gov.execute_unpause("exec", pid, eta + 1).unwrap_err(),
            Error::AlreadyFinalized
        );
    }

    #[test]
    fn cancelled_unpause_cannot_execute_and_keeps_pause_active() {
        let mut gov = setup();
        let now = 7_400;
        let eta = now + 60;

        gov.emergency_pause("guardian", "incident-cancelled-unpause")
            .unwrap();
        let pid = gov
            .schedule_unpause("guardian", eta, "recover-cancelled-unpause", now)
            .unwrap();
        let audit_len_before_cancel = gov.audit_log().len();

        gov.cancel("guardian", pid).unwrap();

        let proposal = gov.proposal(pid).unwrap();
        let normalized = gov.normalized_audit_log();
        let pid_s = pid.to_string();
        assert_eq!(proposal.status, ProposalStatus::Cancelled);
        assert!(gov.bridge_state().emergency_paused);
        assert_eq!(gov.audit_log().len(), audit_len_before_cancel + 1);
        assert!(gov.audit_log().iter().any(|event| matches!(
            event,
            GovernanceEvent::ProposalCancelled { proposal_id, actor }
                if *proposal_id == pid && actor == "guardian"
        )));
        assert!(normalized.iter().any(|event| {
            event.event_type == "governance.proposal_cancelled"
                && event.object_id.as_deref() == Some(pid_s.as_str())
                && event.related_id.as_deref() == Some("emergency_pause")
                && event.reason.as_deref() == Some("kind=EmergencyUnpause")
        }));

        let audit_len_before_failed_execute = gov.audit_log().len();
        assert_eq!(
            gov.execute_unpause("exec", pid, eta).unwrap_err(),
            Error::AlreadyFinalized
        );
        assert!(gov.bridge_state().emergency_paused);
        assert_eq!(gov.audit_log().len(), audit_len_before_failed_execute);
        assert!(!gov.audit_log().iter().any(|event| matches!(
            event,
            GovernanceEvent::PauseRestoreExecuted { proposal_id, .. }
                if *proposal_id == pid
        )));
        assert!(!gov.audit_log().iter().any(|event| matches!(
            event,
            GovernanceEvent::ProposalExecuted { proposal_id, .. }
                if *proposal_id == pid
        )));
    }

    #[test]
    fn unauthorized_cancel_of_scheduled_unpause_preserves_queue_and_pause_state() {
        let mut gov = setup();
        let now = 7_450;
        let eta = now + 60;

        gov.emergency_pause("guardian", "incident-unauthorized-cancel")
            .unwrap();
        let pid = gov
            .schedule_unpause("guardian", eta, "recover-unauthorized-cancel", now)
            .unwrap();
        let audit_len_before = gov.audit_log().len();

        assert_eq!(gov.cancel("alice", pid).unwrap_err(), Error::Unauthorized);

        let proposal = gov.proposal(pid).unwrap();
        assert_eq!(proposal.kind, ProposalKind::EmergencyUnpause);
        assert_eq!(proposal.status, ProposalStatus::Queued);
        assert_eq!(proposal.proposer, "guardian");
        assert_eq!(proposal.executor, None);
        assert_eq!(proposal.executed_at, None);
        assert!(gov.bridge_state().emergency_paused);
        assert_eq!(gov.audit_log().len(), audit_len_before);
        assert!(!gov.audit_log().iter().any(|event| matches!(
            event,
            GovernanceEvent::ProposalCancelled { proposal_id, .. }
                if *proposal_id == pid
        )));
    }

    #[test]
    fn scheduled_unpause_cancel_rejects_proposer_after_guardian_role_revoked() {
        let mut gov = setup();
        let now = 7_500;
        let eta = now + 60;

        gov.emergency_pause("guardian", "incident-guardian-role-drift")
            .unwrap();
        let pid = gov
            .schedule_unpause("guardian", eta, "recover-guardian-role-drift", now)
            .unwrap();
        let audit_len_before = gov.audit_log().len();

        gov.set_guardian("admin", "guardian", false).unwrap();

        assert_eq!(
            gov.cancel("guardian", pid).unwrap_err(),
            Error::Unauthorized
        );

        let proposal = gov.proposal(pid).unwrap();
        assert_eq!(proposal.kind, ProposalKind::EmergencyUnpause);
        assert_eq!(proposal.status, ProposalStatus::Queued);
        assert_eq!(proposal.proposer, "guardian");
        assert_eq!(proposal.executor, None);
        assert_eq!(proposal.executed_at, None);
        assert!(gov.bridge_state().emergency_paused);
        assert_eq!(gov.audit_log().len(), audit_len_before);
        assert!(!gov.audit_log().iter().any(|event| matches!(
            event,
            GovernanceEvent::ProposalCancelled { proposal_id, .. }
                if *proposal_id == pid
        )));
    }

    #[test]
    fn active_guardian_can_cancel_scheduled_unpause_after_original_guardian_revoked() {
        let mut gov = setup();
        let now = 7_525;
        let eta = now + 60;

        gov.emergency_pause("guardian", "incident-guardian-handoff")
            .unwrap();
        let pid = gov
            .schedule_unpause("guardian", eta, "recover-guardian-handoff", now)
            .unwrap();
        let audit_len_before = gov.audit_log().len();

        gov.set_guardian("admin", "guardian", false).unwrap();
        gov.set_guardian("admin", "guardian2", true).unwrap();

        gov.cancel("guardian2", pid).unwrap();

        let proposal = gov.proposal(pid).unwrap();
        assert_eq!(proposal.kind, ProposalKind::EmergencyUnpause);
        assert_eq!(proposal.status, ProposalStatus::Cancelled);
        assert_eq!(proposal.proposer, "guardian");
        assert_eq!(proposal.executor, None);
        assert_eq!(proposal.executed_at, None);
        assert!(gov.bridge_state().emergency_paused);
        assert_eq!(gov.audit_log().len(), audit_len_before + 1);
        assert!(gov.audit_log().iter().any(|event| matches!(
            event,
            GovernanceEvent::ProposalCancelled { proposal_id, actor }
                if *proposal_id == pid && actor == "guardian2"
        )));
    }

    #[test]
    fn cancelled_unpause_allows_new_restore_schedule() {
        let mut gov = setup();
        let now = 7_540;
        let eta = now + 60;

        gov.emergency_pause("guardian", "incident-reschedule")
            .unwrap();
        let first_pid = gov
            .schedule_unpause("guardian", eta, "recover-first", now)
            .unwrap();
        gov.cancel("guardian", first_pid).unwrap();

        let second_pid = gov
            .schedule_unpause("guardian", eta + 60, "recover-second", now)
            .unwrap();

        let first = gov.proposal(first_pid).unwrap();
        assert_eq!(first.kind, ProposalKind::EmergencyUnpause);
        assert_eq!(first.status, ProposalStatus::Cancelled);
        assert!(gov.bridge_state().emergency_paused);

        let second = gov.proposal(second_pid).unwrap();
        assert_eq!(second.kind, ProposalKind::EmergencyUnpause);
        assert_eq!(second.status, ProposalStatus::Queued);
        assert_eq!(second.eta, eta + 60);
        assert_eq!(second.reason_hash, "recover-second");

        assert_eq!(
            gov.audit_log()
                .iter()
                .filter(|event| matches!(event, GovernanceEvent::PauseRestoreScheduled { .. }))
                .count(),
            2
        );
        assert!(gov.audit_log().iter().any(|event| matches!(
            event,
            GovernanceEvent::ProposalCancelled { proposal_id, actor }
                if *proposal_id == first_pid && actor == "guardian"
        )));
    }

    #[test]
    fn param_proposal_cancel_rejects_proposer_after_proposer_role_revoked() {
        let mut gov = setup();
        let now = 7_550;
        let eta = now + 60;
        let pid = gov
            .propose(
                "alice",
                "challenge_window_blocks",
                "100",
                "181",
                eta,
                "reason-proposer-role-drift-cancel",
                now,
            )
            .unwrap();
        let audit_len_before = gov.audit_log().len();

        gov.set_role("admin", "alice", false, false).unwrap();

        assert_eq!(gov.cancel("alice", pid).unwrap_err(), Error::Unauthorized);

        let proposal = gov.proposal(pid).unwrap();
        assert_eq!(proposal.kind, ProposalKind::ParamChange);
        assert_eq!(proposal.status, ProposalStatus::Pending);
        assert_eq!(proposal.proposer, "alice");
        assert_eq!(proposal.executor, None);
        assert_eq!(proposal.executed_at, None);
        assert_eq!(gov.audit_log().len(), audit_len_before);
        assert!(!gov.audit_log().iter().any(|event| matches!(
            event,
            GovernanceEvent::ProposalCancelled { proposal_id, .. }
                if *proposal_id == pid
        )));
    }
}
