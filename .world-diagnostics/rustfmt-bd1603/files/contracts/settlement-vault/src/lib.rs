use audit_events::AuditEvent;
use std::collections::HashMap;

pub type AccountId = String;
pub type RequestId = String;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum VaultError {
    Unauthorized,
    Paused,
    AlreadyPaused,
    NotPaused,
    InvalidAmount,
    InvalidBeneficiary,
    InvalidRequestId,
    InsufficientBalance,
    DuplicateRequest,
    RequestNotFound,
    InvalidStateTransition,
    BalanceOverflow,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum VaultEvent {
    Deposited {
        caller: String,
        account: String,
        amount: u128,
    },
    Locked {
        caller: String,
        request_id: String,
        account: String,
        amount: u128,
    },
    Released {
        caller: String,
        request_id: String,
        account: String,
        amount: u128,
    },
    Slashed {
        caller: String,
        request_id: String,
        account: String,
        beneficiary: String,
        amount: u128,
    },
    Transferred {
        caller: String,
        from: String,
        to: String,
        amount: u128,
    },
    Paused {
        caller: String,
    },
    Unpaused {
        caller: String,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LockStatus {
    Locked,
    Released,
    Slashed,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LockRecord {
    pub account: AccountId,
    pub amount: u128,
    pub status: LockStatus,
}

#[derive(Debug, Clone)]
pub struct SettlementVault {
    owner: AccountId,
    paused: bool,
    balances: HashMap<AccountId, u128>,
    locks: HashMap<RequestId, LockRecord>,
    audit_log: Vec<VaultEvent>,
}

impl SettlementVault {
    pub fn new(owner: impl Into<AccountId>) -> Self {
        Self {
            owner: owner.into(),
            paused: false,
            balances: HashMap::new(),
            locks: HashMap::new(),
            audit_log: Vec::new(),
        }
    }

    pub fn owner(&self) -> &str {
        &self.owner
    }

    pub fn is_paused(&self) -> bool {
        self.paused
    }

    pub fn balance_of(&self, account: &str) -> u128 {
        self.balances.get(account).copied().unwrap_or(0)
    }

    pub fn lock_record(&self, request_id: &str) -> Option<&LockRecord> {
        self.locks.get(request_id)
    }

    pub fn audit_log(&self) -> &[VaultEvent] {
        &self.audit_log
    }

    pub fn consume_audit_log(&mut self) -> Vec<VaultEvent> {
        std::mem::take(&mut self.audit_log)
    }

    pub fn normalized_audit_log(&self) -> Vec<AuditEvent> {
        self.audit_log
            .iter()
            .map(Self::normalize_audit_event)
            .collect()
    }

    fn normalize_audit_event(event: &VaultEvent) -> AuditEvent {
        match event {
            VaultEvent::Deposited {
                caller,
                account,
                amount,
            } => {
                let mut normalized = AuditEvent::new("settlement-vault", "vault.deposited");
                normalized.actor = Some(caller.clone());
                normalized.object_id = Some(account.clone());
                normalized.amount = Some(*amount);
                normalized
            }
            VaultEvent::Locked {
                caller,
                request_id,
                account,
                amount,
            } => {
                let mut normalized = AuditEvent::new("settlement-vault", "vault.locked");
                normalized.actor = Some(caller.clone());
                normalized.object_id = Some(request_id.clone());
                normalized.related_id = Some(account.clone());
                normalized.amount = Some(*amount);
                normalized
            }
            VaultEvent::Released {
                caller,
                request_id,
                account,
                amount,
            } => {
                let mut normalized = AuditEvent::new("settlement-vault", "vault.released");
                normalized.actor = Some(caller.clone());
                normalized.object_id = Some(request_id.clone());
                normalized.related_id = Some(account.clone());
                normalized.amount = Some(*amount);
                normalized
            }
            VaultEvent::Slashed {
                caller,
                request_id,
                account,
                beneficiary,
                amount,
            } => {
                let mut normalized = AuditEvent::new("settlement-vault", "vault.slashed");
                normalized.actor = Some(caller.clone());
                normalized.object_id = Some(request_id.clone());
                normalized.related_id = Some(account.clone());
                normalized.amount = Some(*amount);
                normalized.note = Some(format!("beneficiary={beneficiary}"));
                normalized
            }
            VaultEvent::Transferred {
                caller,
                from,
                to,
                amount,
            } => {
                let mut normalized = AuditEvent::new("settlement-vault", "vault.transferred");
                normalized.actor = Some(caller.clone());
                normalized.object_id = Some(from.clone());
                normalized.related_id = Some(to.clone());
                normalized.amount = Some(*amount);
                normalized
            }
            VaultEvent::Paused { caller } => {
                let mut normalized = AuditEvent::new("settlement-vault", "vault.paused");
                normalized.actor = Some(caller.clone());
                normalized
            }
            VaultEvent::Unpaused { caller } => {
                let mut normalized = AuditEvent::new("settlement-vault", "vault.unpaused");
                normalized.actor = Some(caller.clone());
                normalized
            }
        }
    }

    pub fn deposit(&mut self, caller: &str, account: &str, amount: u128) -> Result<(), VaultError> {
        self.ensure_owner(caller)?;
        self.ensure_not_paused()?;
        if amount == 0 {
            return Err(VaultError::InvalidAmount);
        }

        self.credit_balance(account, amount)?;
        self.audit_log.push(VaultEvent::Deposited {
            caller: caller.to_string(),
            account: account.to_string(),
            amount,
        });
        Ok(())
    }

    pub fn lock(
        &mut self,
        caller: &str,
        request_id: &str,
        account: &str,
        amount: u128,
    ) -> Result<(), VaultError> {
        self.ensure_owner(caller)?;
        self.ensure_not_paused()?;
        self.ensure_request_id(request_id)?;

        if amount == 0 {
            return Err(VaultError::InvalidAmount);
        }
        if self.locks.contains_key(request_id) {
            return Err(VaultError::DuplicateRequest);
        }

        let available = self.balance_of(account);
        if available < amount {
            return Err(VaultError::InsufficientBalance);
        }
        let balance = self
            .balances
            .get_mut(account)
            .ok_or(VaultError::InsufficientBalance)?;
        *balance -= amount;
        self.prune_zero_balance(account);

        self.locks.insert(
            request_id.to_string(),
            LockRecord {
                account: account.to_string(),
                amount,
                status: LockStatus::Locked,
            },
        );

        self.audit_log.push(VaultEvent::Locked {
            caller: caller.to_string(),
            request_id: request_id.to_string(),
            account: account.to_string(),
            amount,
        });

        Ok(())
    }

    pub fn release(&mut self, caller: &str, request_id: &str) -> Result<(), VaultError> {
        self.ensure_owner(caller)?;
        self.ensure_not_paused()?;
        self.ensure_request_id(request_id)?;

        let (account, amount) = {
            let lock = self
                .locks
                .get(request_id)
                .ok_or(VaultError::RequestNotFound)?;

            if lock.status != LockStatus::Locked {
                return Err(VaultError::InvalidStateTransition);
            }

            (lock.account.clone(), lock.amount)
        };

        self.ensure_creditable_balance(&account, amount)?;
        self.locks
            .get_mut(request_id)
            .ok_or(VaultError::RequestNotFound)?
            .status = LockStatus::Released;
        self.credit_balance(&account, amount)?;
        self.audit_log.push(VaultEvent::Released {
            caller: caller.to_string(),
            request_id: request_id.to_string(),
            account,
            amount,
        });
        Ok(())
    }

    pub fn slash(
        &mut self,
        caller: &str,
        request_id: &str,
        beneficiary: &str,
    ) -> Result<(), VaultError> {
        self.ensure_owner(caller)?;
        self.ensure_not_paused()?;
        self.ensure_request_id(request_id)?;

        let (account, amount) = {
            let lock = self
                .locks
                .get(request_id)
                .ok_or(VaultError::RequestNotFound)?;

            if lock.status != LockStatus::Locked {
                return Err(VaultError::InvalidStateTransition);
            }

            (lock.account.clone(), lock.amount)
        };

        if beneficiary.trim().is_empty() || beneficiary == account {
            return Err(VaultError::InvalidBeneficiary);
        }

        self.ensure_creditable_balance(beneficiary, amount)?;
        self.locks
            .get_mut(request_id)
            .ok_or(VaultError::RequestNotFound)?
            .status = LockStatus::Slashed;
        self.credit_balance(beneficiary, amount)?;
        self.audit_log.push(VaultEvent::Slashed {
            caller: caller.to_string(),
            request_id: request_id.to_string(),
            account,
            beneficiary: beneficiary.to_string(),
            amount,
        });
        Ok(())
    }

    pub fn transfer(
        &mut self,
        caller: &str,
        from: &str,
        to: &str,
        amount: u128,
    ) -> Result<(), VaultError> {
        self.ensure_owner(caller)?;
        self.ensure_not_paused()?;
        if amount == 0 {
            return Err(VaultError::InvalidAmount);
        }
        if to.trim().is_empty() {
            return Err(VaultError::InvalidBeneficiary);
        }

        let available = self.balance_of(from);
        if available < amount {
            return Err(VaultError::InsufficientBalance);
        }
        if from == to {
            self.audit_log.push(VaultEvent::Transferred {
                caller: caller.to_string(),
                from: from.to_string(),
                to: to.to_string(),
                amount,
            });
            return Ok(());
        }

        self.ensure_creditable_balance(to, amount)?;

        let from_entry = self.balances.entry(from.to_string()).or_insert(0);
        *from_entry -= amount;
        self.prune_zero_balance(from);

        self.credit_balance(to, amount)?;
        self.audit_log.push(VaultEvent::Transferred {
            caller: caller.to_string(),
            from: from.to_string(),
            to: to.to_string(),
            amount,
        });
        Ok(())
    }

    pub fn pause(&mut self, caller: &str) -> Result<(), VaultError> {
        self.ensure_owner(caller)?;
        if self.paused {
            return Err(VaultError::AlreadyPaused);
        }

        self.paused = true;
        self.audit_log.push(VaultEvent::Paused {
            caller: caller.to_string(),
        });
        Ok(())
    }

    pub fn unpause(&mut self, caller: &str) -> Result<(), VaultError> {
        self.ensure_owner(caller)?;
        if !self.paused {
            return Err(VaultError::NotPaused);
        }

        self.paused = false;
        self.audit_log.push(VaultEvent::Unpaused {
            caller: caller.to_string(),
        });
        Ok(())
    }

    fn ensure_owner(&self, caller: &str) -> Result<(), VaultError> {
        if caller != self.owner {
            return Err(VaultError::Unauthorized);
        }
        Ok(())
    }

    fn ensure_not_paused(&self) -> Result<(), VaultError> {
        if self.paused {
            return Err(VaultError::Paused);
        }
        Ok(())
    }

    fn ensure_request_id(&self, request_id: &str) -> Result<(), VaultError> {
        if request_id.trim().is_empty() || request_id != request_id.trim() {
            return Err(VaultError::InvalidRequestId);
        }
        Ok(())
    }

    fn ensure_creditable_balance(&self, account: &str, amount: u128) -> Result<(), VaultError> {
        self.balance_of(account)
            .checked_add(amount)
            .ok_or(VaultError::BalanceOverflow)?;
        Ok(())
    }

    fn credit_balance(&mut self, account: &str, amount: u128) -> Result<(), VaultError> {
        let entry = self.balances.entry(account.to_string()).or_insert(0);
        *entry = entry
            .checked_add(amount)
            .ok_or(VaultError::BalanceOverflow)?;
        Ok(())
    }

    fn prune_zero_balance(&mut self, account: &str) {
        if self.balance_of(account) == 0 {
            self.balances.remove(account);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn deposit_lock_release_happy_path() {
        let mut vault = SettlementVault::new("owner");

        vault.deposit("owner", "alice", 100).unwrap();
        assert_eq!(vault.balance_of("alice"), 100);

        vault.lock("owner", "req-1", "alice", 60).unwrap();
        assert_eq!(vault.balance_of("alice"), 40);
        assert_eq!(
            vault.lock_record("req-1").unwrap().status,
            LockStatus::Locked
        );

        vault.release("owner", "req-1").unwrap();
        assert_eq!(vault.balance_of("alice"), 100);
        assert_eq!(
            vault.lock_record("req-1").unwrap().status,
            LockStatus::Released
        );
    }

    #[test]
    fn duplicate_request_is_rejected() {
        let mut vault = SettlementVault::new("owner");

        vault.deposit("owner", "alice", 100).unwrap();
        vault.lock("owner", "req-dup", "alice", 10).unwrap();

        let err = vault
            .lock("owner", "req-dup", "alice", 10)
            .expect_err("duplicate request must fail");
        assert_eq!(err, VaultError::DuplicateRequest);
    }

    #[test]
    fn terminal_lock_records_keep_request_ids_reserved() {
        let mut vault = SettlementVault::new("owner");

        vault.deposit("owner", "alice", 100).unwrap();
        vault.lock("owner", "req-release", "alice", 20).unwrap();
        vault.release("owner", "req-release").unwrap();
        assert_eq!(
            vault
                .lock("owner", "req-release", "alice", 5)
                .expect_err("released request id must remain reserved"),
            VaultError::DuplicateRequest
        );

        vault.lock("owner", "req-slash", "alice", 10).unwrap();
        vault.slash("owner", "req-slash", "treasury").unwrap();
        assert_eq!(
            vault
                .lock("owner", "req-slash", "alice", 1)
                .expect_err("slashed request id must remain reserved"),
            VaultError::DuplicateRequest
        );
    }

    #[test]
    fn failed_lock_does_not_create_zero_balance_account_entry() {
        let mut vault = SettlementVault::new("owner");

        let err = vault
            .lock("owner", "req-missing", "ghost", 1)
            .expect_err("missing account should fail closed");
        assert_eq!(err, VaultError::InsufficientBalance);
        assert_eq!(vault.balance_of("ghost"), 0);
        assert!(vault.balances.get("ghost").is_none());
        assert!(vault.lock_record("req-missing").is_none());
        assert!(vault.audit_log().is_empty());
    }

    #[test]
    fn blank_or_padded_request_ids_are_rejected_without_state_or_audit_mutation() {
        let mut vault = SettlementVault::new("owner");

        vault.deposit("owner", "alice", 50).unwrap();
        let audit_len_after_deposit = vault.audit_log().len();

        assert_eq!(
            vault.lock("owner", "   ", "alice", 10).unwrap_err(),
            VaultError::InvalidRequestId
        );
        assert_eq!(vault.balance_of("alice"), 50);
        assert!(vault.lock_record("   ").is_none());
        assert_eq!(vault.audit_log().len(), audit_len_after_deposit);

        assert_eq!(
            vault.release("owner", "   ").unwrap_err(),
            VaultError::InvalidRequestId
        );
        assert_eq!(
            vault.slash("owner", "   ", "treasury").unwrap_err(),
            VaultError::InvalidRequestId
        );

        assert_eq!(
            vault
                .lock("owner", " req-padded ", "alice", 10)
                .unwrap_err(),
            VaultError::InvalidRequestId
        );
        assert!(vault.lock_record(" req-padded ").is_none());
        assert_eq!(
            vault.release("owner", " req-padded ").unwrap_err(),
            VaultError::InvalidRequestId
        );
        assert_eq!(
            vault
                .slash("owner", " req-padded ", "treasury")
                .unwrap_err(),
            VaultError::InvalidRequestId
        );

        assert_eq!(vault.audit_log().len(), audit_len_after_deposit);
        assert!(!vault.normalized_audit_log().iter().any(|event| {
            matches!(
                &event.event_type[..],
                "vault.locked" | "vault.released" | "vault.slashed"
            ) && matches!(
                event.object_id.as_deref(),
                Some("   ") | Some(" req-padded ")
            )
        }));
    }

    #[test]
    fn unauthorized_actions_are_rejected() {
        let mut vault = SettlementVault::new("owner");

        assert_eq!(
            vault.deposit("alice", "alice", 50).unwrap_err(),
            VaultError::Unauthorized
        );
        vault.deposit("owner", "alice", 50).unwrap();

        assert_eq!(
            vault.lock("mallory", "req-1", "alice", 10).unwrap_err(),
            VaultError::Unauthorized
        );
        assert_eq!(
            vault.pause("mallory").unwrap_err(),
            VaultError::Unauthorized
        );
        assert_eq!(
            vault.unpause("mallory").unwrap_err(),
            VaultError::Unauthorized
        );
    }

    #[test]
    fn rejected_actions_do_not_append_audit_events() {
        let mut vault = SettlementVault::new("owner");

        assert_eq!(
            vault.deposit("mallory", "alice", 10).unwrap_err(),
            VaultError::Unauthorized
        );
        assert!(vault.audit_log().is_empty());

        vault.deposit("owner", "alice", 10).unwrap();
        let audit_len_after_deposit = vault.audit_log().len();

        assert_eq!(
            vault
                .lock("mallory", "req-unauthorized", "alice", 5)
                .unwrap_err(),
            VaultError::Unauthorized
        );
        assert_eq!(vault.audit_log().len(), audit_len_after_deposit);

        vault.pause("owner").unwrap();
        let audit_len_after_pause = vault.audit_log().len();
        assert_eq!(
            vault.transfer("owner", "alice", "bob", 1).unwrap_err(),
            VaultError::Paused
        );
        assert_eq!(
            vault.release("owner", "req-missing").unwrap_err(),
            VaultError::Paused
        );
        assert_eq!(vault.audit_log().len(), audit_len_after_pause);
    }

    #[test]
    fn paused_owner_release_and_slash_fail_closed_without_state_or_audit_mutation() {
        let mut vault = SettlementVault::new("owner");

        vault.deposit("owner", "alice", 50).unwrap();
        vault
            .lock("owner", "req-paused-release-slash", "alice", 20)
            .unwrap();
        vault.pause("owner").unwrap();
        let audit_len_while_paused = vault.audit_log().len();

        assert_eq!(
            vault
                .release("owner", "req-paused-release-slash")
                .unwrap_err(),
            VaultError::Paused
        );
        assert_eq!(
            vault
                .slash("owner", "req-paused-release-slash", "treasury")
                .unwrap_err(),
            VaultError::Paused
        );
        assert_eq!(
            vault
                .lock_record("req-paused-release-slash")
                .unwrap()
                .status,
            LockStatus::Locked
        );
        assert_eq!(vault.balance_of("alice"), 30);
        assert_eq!(vault.balance_of("treasury"), 0);
        assert_eq!(vault.audit_log().len(), audit_len_while_paused);
    }

    #[test]
    fn unauthorized_release_slash_and_transfer_fail_closed() {
        let mut vault = SettlementVault::new("owner");

        vault.deposit("owner", "alice", 25).unwrap();
        vault.lock("owner", "req-auth", "alice", 20).unwrap();
        let audit_len_after_lock = vault.audit_log().len();

        assert_eq!(
            vault.release("mallory", "req-auth").unwrap_err(),
            VaultError::Unauthorized
        );
        assert_eq!(
            vault.slash("mallory", "req-auth", "treasury").unwrap_err(),
            VaultError::Unauthorized
        );
        assert_eq!(
            vault.transfer("mallory", "alice", "bob", 1).unwrap_err(),
            VaultError::Unauthorized
        );

        assert_eq!(vault.balance_of("alice"), 5);
        assert_eq!(vault.balance_of("bob"), 0);
        assert_eq!(vault.balance_of("treasury"), 0);
        assert_eq!(
            vault.lock_record("req-auth").unwrap().status,
            LockStatus::Locked
        );
        assert_eq!(vault.audit_log().len(), audit_len_after_lock);
    }

    #[test]
    fn unauthorized_actions_still_fail_closed_while_paused() {
        let mut vault = SettlementVault::new("owner");

        vault.deposit("owner", "alice", 25).unwrap();
        vault.lock("owner", "req-auth-paused", "alice", 20).unwrap();
        vault.pause("owner").unwrap();
        let audit_len_while_paused = vault.audit_log().len();

        assert_eq!(
            vault.deposit("mallory", "alice", 1).unwrap_err(),
            VaultError::Unauthorized
        );
        assert_eq!(
            vault
                .lock("mallory", "req-auth-paused-2", "alice", 1)
                .unwrap_err(),
            VaultError::Unauthorized
        );
        assert_eq!(
            vault.release("mallory", "req-auth-paused").unwrap_err(),
            VaultError::Unauthorized
        );
        assert_eq!(
            vault
                .slash("mallory", "req-auth-paused", "treasury")
                .unwrap_err(),
            VaultError::Unauthorized
        );
        assert_eq!(
            vault.transfer("mallory", "alice", "bob", 1).unwrap_err(),
            VaultError::Unauthorized
        );

        assert_eq!(vault.balance_of("alice"), 5);
        assert_eq!(vault.balance_of("bob"), 0);
        assert_eq!(vault.balance_of("treasury"), 0);
        assert_eq!(
            vault.lock_record("req-auth-paused").unwrap().status,
            LockStatus::Locked
        );
        assert!(vault.lock_record("req-auth-paused-2").is_none());
        assert_eq!(vault.audit_log().len(), audit_len_while_paused);
    }

    #[test]
    fn unauthorized_duplicate_lock_attempt_fails_closed_without_leaking_request_state() {
        let mut vault = SettlementVault::new("owner");

        vault.deposit("owner", "alice", 25).unwrap();
        vault.lock("owner", "req-owned", "alice", 10).unwrap();
        let audit_len_after_lock = vault.audit_log().len();

        assert_eq!(
            vault.lock("mallory", "req-owned", "alice", 1).unwrap_err(),
            VaultError::Unauthorized
        );
        assert_eq!(vault.balance_of("alice"), 15);
        assert_eq!(
            vault.lock_record("req-owned").unwrap(),
            &LockRecord {
                account: "alice".to_string(),
                amount: 10,
                status: LockStatus::Locked,
            }
        );
        assert_eq!(vault.audit_log().len(), audit_len_after_lock);
    }

    #[test]
    fn illegal_state_transition_is_rejected() {
        let mut vault = SettlementVault::new("owner");

        vault.deposit("owner", "alice", 100).unwrap();
        vault.lock("owner", "req-1", "alice", 20).unwrap();
        vault.release("owner", "req-1").unwrap();

        let err = vault
            .slash("owner", "req-1", "treasury")
            .expect_err("cannot slash after release");
        assert_eq!(err, VaultError::InvalidStateTransition);
    }

    #[test]
    fn terminal_lock_operations_reject_replays_without_mutating_state_or_audit() {
        let mut vault = SettlementVault::new("owner");

        vault.deposit("owner", "alice", 100).unwrap();
        vault
            .lock("owner", "req-release-replay", "alice", 30)
            .unwrap();
        vault.release("owner", "req-release-replay").unwrap();
        let released_audit_len = vault.audit_log().len();

        assert_eq!(
            vault
                .release("owner", "req-release-replay")
                .expect_err("released lock cannot be released twice"),
            VaultError::InvalidStateTransition
        );
        assert_eq!(vault.balance_of("alice"), 100);
        assert_eq!(
            vault.lock_record("req-release-replay").unwrap().status,
            LockStatus::Released
        );
        assert_eq!(vault.audit_log().len(), released_audit_len);

        vault
            .lock("owner", "req-slash-replay", "alice", 40)
            .unwrap();
        vault
            .slash("owner", "req-slash-replay", "treasury")
            .unwrap();
        let slashed_audit_len = vault.audit_log().len();

        assert_eq!(
            vault
                .slash("owner", "req-slash-replay", "treasury")
                .expect_err("slashed lock cannot be slashed twice"),
            VaultError::InvalidStateTransition
        );
        assert_eq!(vault.balance_of("alice"), 60);
        assert_eq!(vault.balance_of("treasury"), 40);
        assert_eq!(
            vault.lock_record("req-slash-replay").unwrap().status,
            LockStatus::Slashed
        );
        assert_eq!(vault.audit_log().len(), slashed_audit_len);
    }

    #[test]
    fn pause_blocks_state_changes_until_unpause() {
        let mut vault = SettlementVault::new("owner");

        vault.deposit("owner", "alice", 100).unwrap();
        vault.pause("owner").unwrap();

        assert_eq!(vault.pause("owner").unwrap_err(), VaultError::AlreadyPaused);
        assert_eq!(
            vault.deposit("owner", "alice", 1).unwrap_err(),
            VaultError::Paused
        );
        assert_eq!(
            vault.lock("owner", "req-paused", "alice", 1).unwrap_err(),
            VaultError::Paused
        );

        vault.unpause("owner").unwrap();
        assert_eq!(vault.unpause("owner").unwrap_err(), VaultError::NotPaused);

        vault.lock("owner", "req-paused", "alice", 1).unwrap();
    }

    #[test]
    fn repeated_pause_state_transitions_do_not_append_audit_events() {
        let mut vault = SettlementVault::new("owner");

        vault.pause("owner").unwrap();
        let audit_len_after_pause = vault.audit_log().len();
        assert_eq!(audit_len_after_pause, 1);
        assert_eq!(vault.pause("owner").unwrap_err(), VaultError::AlreadyPaused);
        assert_eq!(vault.audit_log().len(), audit_len_after_pause);

        vault.unpause("owner").unwrap();
        let audit_len_after_unpause = vault.audit_log().len();
        assert_eq!(audit_len_after_unpause, audit_len_after_pause + 1);
        assert_eq!(vault.unpause("owner").unwrap_err(), VaultError::NotPaused);
        assert_eq!(vault.audit_log().len(), audit_len_after_unpause);
    }

    #[test]
    fn unauthorized_pause_and_unpause_do_not_leak_state_or_append_audit_events() {
        let mut vault = SettlementVault::new("owner");

        assert_eq!(
            vault.pause("mallory").unwrap_err(),
            VaultError::Unauthorized
        );
        assert!(!vault.is_paused());
        assert!(vault.audit_log().is_empty());

        vault.pause("owner").unwrap();
        let audit_len_after_pause = vault.audit_log().len();
        assert!(vault.is_paused());

        assert_eq!(
            vault.pause("mallory").unwrap_err(),
            VaultError::Unauthorized
        );
        assert_eq!(
            vault.unpause("mallory").unwrap_err(),
            VaultError::Unauthorized
        );
        assert!(vault.is_paused());
        assert_eq!(vault.audit_log().len(), audit_len_after_pause);

        vault.unpause("owner").unwrap();
        let audit_len_after_unpause = vault.audit_log().len();
        assert!(!vault.is_paused());

        assert_eq!(
            vault.unpause("mallory").unwrap_err(),
            VaultError::Unauthorized
        );
        assert!(!vault.is_paused());
        assert_eq!(vault.audit_log().len(), audit_len_after_unpause);
    }

    #[test]
    fn paused_duplicate_lock_request_fails_closed_without_leaking_duplicate_state() {
        let mut vault = SettlementVault::new("owner");

        vault.deposit("owner", "alice", 20).unwrap();
        vault.lock("owner", "req-paused-dup", "alice", 5).unwrap();
        let audit_len_before_pause = vault.audit_log().len();

        vault.pause("owner").unwrap();
        let audit_len_while_paused = vault.audit_log().len();
        assert_eq!(audit_len_while_paused, audit_len_before_pause + 1);

        let err = vault
            .lock("owner", "req-paused-dup", "alice", 1)
            .expect_err("paused duplicate lock must fail before duplicate-request handling");
        assert_eq!(err, VaultError::Paused);
        assert_eq!(vault.balance_of("alice"), 15);
        assert_eq!(
            vault.lock_record("req-paused-dup").unwrap().status,
            LockStatus::Locked
        );
        assert_eq!(vault.lock_record("req-paused-dup").unwrap().amount, 5);
        assert_eq!(vault.audit_log().len(), audit_len_while_paused);
    }

    #[test]
    fn paused_slash_fails_closed_without_mutating_lock_or_audit_log() {
        let mut vault = SettlementVault::new("owner");

        vault.deposit("owner", "alice", 20).unwrap();
        vault
            .lock("owner", "req-paused-slash", "alice", 20)
            .unwrap();
        let audit_len_before_pause = vault.audit_log().len();
        vault.pause("owner").unwrap();
        let audit_len_while_paused = vault.audit_log().len();

        let err = vault
            .slash("owner", "req-paused-slash", "treasury")
            .expect_err("slash must fail while paused");
        assert_eq!(err, VaultError::Paused);
        assert_eq!(vault.balance_of("alice"), 0);
        assert_eq!(vault.balance_of("treasury"), 0);
        assert_eq!(
            vault.lock_record("req-paused-slash").unwrap().status,
            LockStatus::Locked
        );
        assert_eq!(vault.audit_log().len(), audit_len_while_paused);
        assert_eq!(audit_len_while_paused, audit_len_before_pause + 1);
    }

    #[test]
    fn paused_release_fails_closed_without_mutating_lock_or_audit_log() {
        let mut vault = SettlementVault::new("owner");

        vault.deposit("owner", "alice", 20).unwrap();
        vault
            .lock("owner", "req-paused-release", "alice", 20)
            .unwrap();
        let audit_len_before_pause = vault.audit_log().len();
        vault.pause("owner").unwrap();
        let audit_len_while_paused = vault.audit_log().len();

        let err = vault
            .release("owner", "req-paused-release")
            .expect_err("release must fail while paused");
        assert_eq!(err, VaultError::Paused);
        assert_eq!(vault.balance_of("alice"), 0);
        assert_eq!(
            vault.lock_record("req-paused-release").unwrap().status,
            LockStatus::Locked
        );
        assert_eq!(vault.audit_log().len(), audit_len_while_paused);
        assert_eq!(audit_len_while_paused, audit_len_before_pause + 1);
    }

    #[test]
    fn paused_missing_requests_fail_closed_without_leaking_existence_or_mutating_audit() {
        let mut vault = SettlementVault::new("owner");

        vault.deposit("owner", "alice", 20).unwrap();
        vault.pause("owner").unwrap();
        let audit_len_while_paused = vault.audit_log().len();

        assert_eq!(
            vault
                .release("owner", "req-missing")
                .expect_err("paused release should not reveal request absence"),
            VaultError::Paused
        );
        assert_eq!(
            vault
                .slash("owner", "req-missing", "treasury")
                .expect_err("paused slash should not reveal request absence"),
            VaultError::Paused
        );
        assert!(vault.lock_record("req-missing").is_none());
        assert_eq!(vault.balance_of("alice"), 20);
        assert_eq!(vault.balance_of("treasury"), 0);
        assert_eq!(vault.audit_log().len(), audit_len_while_paused);
    }

    #[test]
    fn slash_transfers_to_beneficiary() {
        let mut vault = SettlementVault::new("owner");

        vault.deposit("owner", "alice", 30).unwrap();
        vault.lock("owner", "req-2", "alice", 30).unwrap();
        vault.slash("owner", "req-2", "treasury").unwrap();

        assert_eq!(vault.balance_of("alice"), 0);
        assert_eq!(vault.balance_of("treasury"), 30);
        assert_eq!(
            vault.lock_record("req-2").unwrap().status,
            LockStatus::Slashed
        );
    }

    #[test]
    fn slash_to_locked_or_empty_beneficiary_is_rejected_without_state_or_audit_mutation() {
        let mut vault = SettlementVault::new("owner");

        vault.deposit("owner", "alice", 30).unwrap();
        vault.lock("owner", "req-self-slash", "alice", 30).unwrap();
        let audit_len_before = vault.audit_log().len();

        let err = vault
            .slash("owner", "req-self-slash", "alice")
            .expect_err("slash beneficiary must differ from locked account");
        assert_eq!(err, VaultError::InvalidBeneficiary);
        assert_eq!(vault.balance_of("alice"), 0);
        assert_eq!(
            vault.lock_record("req-self-slash").unwrap().status,
            LockStatus::Locked
        );
        assert_eq!(vault.audit_log().len(), audit_len_before);

        let err = vault
            .slash("owner", "req-self-slash", "")
            .expect_err("slash beneficiary must not be empty");
        assert_eq!(err, VaultError::InvalidBeneficiary);
        assert_eq!(vault.balance_of("alice"), 0);
        assert_eq!(vault.balance_of(""), 0);
        assert_eq!(
            vault.lock_record("req-self-slash").unwrap().status,
            LockStatus::Locked
        );
        assert_eq!(vault.audit_log().len(), audit_len_before);

        let err = vault
            .slash("owner", "req-self-slash", "   ")
            .expect_err("slash beneficiary must reject whitespace-only values");
        assert_eq!(err, VaultError::InvalidBeneficiary);
        assert_eq!(vault.balance_of("alice"), 0);
        assert_eq!(vault.balance_of("   "), 0);
        assert_eq!(
            vault.lock_record("req-self-slash").unwrap().status,
            LockStatus::Locked
        );
        assert_eq!(vault.audit_log().len(), audit_len_before);
    }

    #[test]
    fn audit_log_records_vault_events() {
        let mut vault = SettlementVault::new("owner");

        vault.deposit("owner", "alice", 100).unwrap();
        vault.lock("owner", "req-1", "alice", 25).unwrap();
        vault.release("owner", "req-1").unwrap();

        vault.deposit("owner", "alice", 30).unwrap();
        vault.transfer("owner", "alice", "bob", 20).unwrap();

        vault.pause("owner").unwrap();
        vault.unpause("owner").unwrap();
        assert_eq!(vault.unpause("owner").unwrap_err(), VaultError::NotPaused);

        vault.lock("owner", "req-2", "alice", 10).unwrap();
        vault.slash("owner", "req-2", "treasury").unwrap();

        let normalized = vault.normalized_audit_log();
        let logs = vault.consume_audit_log();

        assert!(logs.iter().any(|event| matches!(
            event,
            VaultEvent::Deposited { caller, account, amount }
                if caller == "owner" && account == "alice" && *amount == 100
        )));
        assert!(logs.iter().any(|event| matches!(
            event,
            VaultEvent::Locked { caller, request_id, account, amount }
                if caller == "owner" && request_id == "req-1" && account == "alice" && *amount == 25
        )));
        assert!(logs.iter().any(|event| matches!(
            event,
            VaultEvent::Released { caller, request_id, account, amount }
                if caller == "owner" && request_id == "req-1" && account == "alice" && *amount == 25
        )));
        assert!(logs.iter().any(|event| matches!(
            event,
            VaultEvent::Transferred { caller, from, to, amount }
                if caller == "owner" && from == "alice" && to == "bob" && *amount == 20
        )));
        assert!(logs.iter().any(|event| matches!(
            event,
            VaultEvent::Paused { caller } if caller == "owner"
        )));
        assert!(logs.iter().any(|event| matches!(
            event,
            VaultEvent::Unpaused { caller } if caller == "owner"
        )));
        assert!(logs.iter().any(|event| matches!(
            event,
            VaultEvent::Slashed { caller, request_id, account, beneficiary, amount }
                if caller == "owner"
                    && request_id == "req-2"
                    && account == "alice"
                    && beneficiary == "treasury"
                    && *amount == 10
        )));

        assert!(normalized
            .iter()
            .any(|event| event.event_type == "vault.deposited"));
        assert!(normalized
            .iter()
            .any(|event| event.event_type == "vault.locked"));
        assert!(normalized
            .iter()
            .any(|event| event.event_type == "vault.released"));
        assert!(normalized.iter().any(|event| {
            event.event_type == "vault.transferred"
                && event.actor.as_deref() == Some("owner")
                && event.object_id.as_deref() == Some("alice")
                && event.related_id.as_deref() == Some("bob")
                && event.amount == Some(20)
        }));
        assert!(normalized.iter().any(|event| {
            event.event_type == "vault.slashed"
                && event.object_id.as_deref() == Some("req-2")
                && event.related_id.as_deref() == Some("alice")
                && event.note.as_deref() == Some("beneficiary=treasury")
                && event.actor.as_deref() == Some("owner")
        }));
        assert!(normalized.iter().any(|event| {
            event.event_type == "vault.paused" && event.actor.as_deref() == Some("owner")
        }));
        assert!(normalized.iter().any(|event| {
            event.event_type == "vault.unpaused" && event.actor.as_deref() == Some("owner")
        }));
        assert!(normalized
            .iter()
            .any(|event| event.source == "settlement-vault"));
        assert!(normalized.iter().any(|event| {
            event.event_type == "vault.locked"
                && event.object_id.as_deref() == Some("req-1")
                && event.related_id.as_deref() == Some("alice")
        }));
        assert!(normalized.iter().any(|event| {
            event.event_type == "vault.released"
                && event.object_id.as_deref() == Some("req-1")
                && event.related_id.as_deref() == Some("alice")
        }));

        assert!(vault.audit_log().is_empty());
    }

    #[test]
    fn normalized_vault_audit_mapping_keeps_field_discipline() {
        let mut vault = SettlementVault::new("owner");

        vault.deposit("owner", "alice", 100).unwrap();
        vault.lock("owner", "req-map", "alice", 30).unwrap();
        vault.release("owner", "req-map").unwrap();

        vault.lock("owner", "req-slash-map", "alice", 10).unwrap();
        vault.slash("owner", "req-slash-map", "treasury").unwrap();

        let normalized = vault.normalized_audit_log();

        let deposited = normalized
            .iter()
            .find(|event| event.event_type == "vault.deposited")
            .expect("deposit should normalize");
        assert_eq!(deposited.actor.as_deref(), Some("owner"));
        assert_eq!(deposited.object_id.as_deref(), Some("alice"));
        assert_eq!(deposited.related_id, None);
        assert_eq!(deposited.amount, Some(100));
        assert_eq!(deposited.reason, None);
        assert_eq!(deposited.note, None);

        let locked = normalized
            .iter()
            .find(|event| event.event_type == "vault.locked")
            .expect("lock should normalize");
        assert_eq!(locked.actor.as_deref(), Some("owner"));
        assert_eq!(locked.object_id.as_deref(), Some("req-map"));
        assert_eq!(locked.related_id.as_deref(), Some("alice"));
        assert_eq!(locked.amount, Some(30));
        assert_eq!(locked.reason, None);
        assert_eq!(locked.note, None);

        let released = normalized
            .iter()
            .find(|event| event.event_type == "vault.released")
            .expect("release should normalize");
        assert_eq!(released.actor.as_deref(), Some("owner"));
        assert_eq!(released.object_id.as_deref(), Some("req-map"));
        assert_eq!(released.related_id.as_deref(), Some("alice"));
        assert_eq!(released.amount, Some(30));
        assert_eq!(released.reason, None);
        assert_eq!(released.note, None);

        let slashed = normalized
            .iter()
            .find(|event| event.event_type == "vault.slashed")
            .expect("slash should normalize");
        assert_eq!(slashed.actor.as_deref(), Some("owner"));
        assert_eq!(slashed.object_id.as_deref(), Some("req-slash-map"));
        assert_eq!(slashed.related_id.as_deref(), Some("alice"));
        assert_eq!(slashed.amount, Some(10));
        assert_eq!(slashed.reason, None);
        assert_eq!(slashed.note.as_deref(), Some("beneficiary=treasury"));
    }

    #[test]
    fn transfer_moves_balance_between_accounts() {
        let mut vault = SettlementVault::new("owner");

        vault.deposit("owner", "alice", 50).unwrap();
        vault.lock("owner", "req-1", "alice", 20).unwrap();

        let err = vault.transfer("mallory", "alice", "bob", 10).unwrap_err();
        assert_eq!(err, VaultError::Unauthorized);

        let err = vault.transfer("owner", "alice", "bob", 0).unwrap_err();
        assert_eq!(err, VaultError::InvalidAmount);

        vault.transfer("owner", "alice", "bob", 30).unwrap();
        assert_eq!(vault.balance_of("alice"), 0);
        assert_eq!(vault.balance_of("bob"), 30);

        assert_eq!(
            vault.transfer("owner", "alice", "bob", 999).unwrap_err(),
            VaultError::InsufficientBalance
        );
    }

    #[test]
    fn transfer_to_blank_beneficiary_fails_closed_without_state_or_audit_mutation() {
        let mut vault = SettlementVault::new("owner");

        vault.deposit("owner", "alice", 30).unwrap();
        let audit_len_before = vault.audit_log().len();

        let err = vault
            .transfer("owner", "alice", "   ", 10)
            .expect_err("blank transfer recipient must be rejected");
        assert_eq!(err, VaultError::InvalidBeneficiary);
        assert_eq!(vault.balance_of("alice"), 30);
        assert_eq!(vault.balance_of("   "), 0);
        assert_eq!(vault.audit_log().len(), audit_len_before);
        assert!(!vault.normalized_audit_log().iter().any(|event| {
            event.event_type == "vault.transferred"
                && event.actor.as_deref() == Some("owner")
                && event.object_id.as_deref() == Some("   ")
                && event.amount == Some(10)
        }));
    }

    #[test]
    fn self_transfer_preserves_balance_while_emitting_audit_event() {
        let mut vault = SettlementVault::new("owner");

        vault.deposit("owner", "alice", 40).unwrap();
        let audit_len_before = vault.audit_log().len();

        vault.transfer("owner", "alice", "alice", 15).unwrap();

        let normalized = vault.normalized_audit_log();

        assert_eq!(vault.balance_of("alice"), 40);
        assert_eq!(vault.audit_log().len(), audit_len_before + 1);
        assert!(matches!(
            vault.audit_log().last(),
            Some(VaultEvent::Transferred { caller, from, to, amount })
                if caller == "owner" && from == "alice" && to == "alice" && *amount == 15
        ));
        assert!(normalized.iter().any(|event| {
            event.event_type == "vault.transferred"
                && event.actor.as_deref() == Some("owner")
                && event.object_id.as_deref() == Some("alice")
                && event.related_id.as_deref() == Some("alice")
                && event.amount == Some(15)
        }));
    }

    #[test]
    fn paused_self_transfer_fails_closed_without_audit_mutation() {
        let mut vault = SettlementVault::new("owner");

        vault.deposit("owner", "alice", 40).unwrap();
        vault.pause("owner").unwrap();
        let audit_len_while_paused = vault.audit_log().len();

        let err = vault.transfer("owner", "alice", "alice", 15).unwrap_err();
        assert_eq!(err, VaultError::Paused);
        assert_eq!(vault.balance_of("alice"), 40);
        assert_eq!(vault.audit_log().len(), audit_len_while_paused);
        assert!(!vault.normalized_audit_log().iter().any(|event| {
            event.event_type == "vault.transferred"
                && event.actor.as_deref() == Some("owner")
                && event.object_id.as_deref() == Some("alice")
                && event.related_id.as_deref() == Some("alice")
                && event.amount == Some(15)
        }));
    }

    #[test]
    fn overflow_paths_fail_closed_without_partial_state_mutation() {
        let mut vault = SettlementVault::new("owner");
        vault.deposit("owner", "alice", 5).unwrap();
        vault.deposit("owner", "bob", u128::MAX).unwrap();

        vault.lock("owner", "req-slash", "alice", 5).unwrap();
        let audit_len_before_slash = vault.audit_log().len();
        let slash_err = vault.slash("owner", "req-slash", "bob").unwrap_err();
        assert_eq!(slash_err, VaultError::BalanceOverflow);
        assert_eq!(vault.balance_of("bob"), u128::MAX);
        assert_eq!(
            vault.lock_record("req-slash").unwrap().status,
            LockStatus::Locked
        );
        assert_eq!(vault.audit_log().len(), audit_len_before_slash);

        vault.deposit("owner", "carol", 1).unwrap();
        let audit_len_before_transfer = vault.audit_log().len();
        let transfer_err = vault.transfer("owner", "carol", "bob", 1).unwrap_err();
        assert_eq!(transfer_err, VaultError::BalanceOverflow);
        assert_eq!(vault.balance_of("bob"), u128::MAX);
        assert_eq!(vault.balance_of("carol"), 1);
        assert_eq!(vault.audit_log().len(), audit_len_before_transfer);
    }

    #[test]
    fn deposit_overflow_fails_closed_without_crediting_or_logging_event() {
        let mut vault = SettlementVault::new("owner");
        vault.deposit("owner", "alice", u128::MAX).unwrap();
        let audit_len_before = vault.audit_log().len();

        let err = vault.deposit("owner", "alice", 1).unwrap_err();
        assert_eq!(err, VaultError::BalanceOverflow);
        assert_eq!(vault.balance_of("alice"), u128::MAX);
        assert_eq!(vault.audit_log().len(), audit_len_before);
    }

    #[test]
    fn release_overflow_fails_closed_without_unlocking_or_logging_event() {
        let mut vault = SettlementVault::new("owner");
        vault.deposit("owner", "alice", 5).unwrap();
        vault.deposit("owner", "bob", u128::MAX).unwrap();

        vault.lock("owner", "req-release", "alice", 5).unwrap();
        vault.transfer("owner", "bob", "alice", u128::MAX).unwrap();
        assert_eq!(vault.balance_of("alice"), u128::MAX);

        let audit_len_before = vault.audit_log().len();
        let release_err = vault.release("owner", "req-release").unwrap_err();
        assert_eq!(release_err, VaultError::BalanceOverflow);
        assert_eq!(vault.balance_of("alice"), u128::MAX);
        assert_eq!(
            vault.lock_record("req-release").unwrap().status,
            LockStatus::Locked
        );
        assert_eq!(vault.audit_log().len(), audit_len_before);
    }

    #[test]
    fn zero_balances_are_pruned_after_lock_and_transfer() {
        let mut vault = SettlementVault::new("owner");

        vault.deposit("owner", "alice", 10).unwrap();
        vault.lock("owner", "req-prune", "alice", 10).unwrap();
        assert_eq!(vault.balance_of("alice"), 0);
        assert!(vault.balances.get("alice").is_none());

        vault.release("owner", "req-prune").unwrap();
        assert_eq!(vault.balance_of("alice"), 10);

        vault.transfer("owner", "alice", "bob", 10).unwrap();
        assert_eq!(vault.balance_of("alice"), 0);
        assert_eq!(vault.balance_of("bob"), 10);
        assert!(vault.balances.get("alice").is_none());
    }

    #[test]
    fn consume_audit_log_clears_buffer_and_preserves_normalized_semantics() {
        let mut vault = SettlementVault::new("owner");

        vault.deposit("owner", "alice", 50).unwrap();
        vault.lock("owner", "req-consume", "alice", 20).unwrap();
        vault.pause("owner").unwrap();

        let consumed = vault.consume_audit_log();
        assert_eq!(vault.audit_log().len(), 0);
        assert!(vault.normalized_audit_log().is_empty());
        assert_eq!(consumed.len(), 3);
        assert!(matches!(
            consumed[0],
            VaultEvent::Deposited {
                ref caller,
                ref account,
                amount
            } if caller == "owner" && account == "alice" && amount == 50
        ));
        assert!(matches!(
            consumed[1],
            VaultEvent::Locked {
                ref caller,
                ref request_id,
                ref account,
                amount
            } if caller == "owner"
                && request_id == "req-consume"
                && account == "alice"
                && amount == 20
        ));
        assert!(matches!(
            consumed[2],
            VaultEvent::Paused { ref caller } if caller == "owner"
        ));

        vault.unpause("owner").unwrap();
        let normalized = vault.normalized_audit_log();
        assert_eq!(normalized.len(), 1);
        assert_eq!(normalized[0].source, "settlement-vault");
        assert_eq!(normalized[0].event_type, "vault.unpaused");
        assert_eq!(normalized[0].actor.as_deref(), Some("owner"));
        assert_eq!(normalized[0].object_id, None);
        assert_eq!(normalized[0].related_id, None);
        assert_eq!(normalized[0].amount, None);
    }
}
