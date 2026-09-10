#![forbid(unsafe_code)]

//! Bounded account and provider-identity state transitions.
//!
//! This crate is transport- and persistence-independent. Durable adapters must
//! enforce one writer and atomically persist account state and command receipts.

use std::collections::{BTreeMap, BTreeSet};

pub const MAX_USERNAME_BYTES: usize = 128;
pub const MAX_DISPLAY_NAME_BYTES: usize = 256;
pub const MAX_PROVIDER_IDENTITY_BYTES: usize = 512;
pub const HARD_MAX_ACCOUNTS: usize = 1_000_000;
pub const HARD_MAX_IDENTITIES_PER_ACCOUNT: usize = 16;
pub const HARD_MAX_RECEIPTS: usize = 4_000_000;

#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct AccountId([u8; 16]);

impl AccountId {
    pub fn new(bytes: [u8; 16]) -> Result<Self, IdentityError> {
        if bytes == [0; 16] {
            return Err(IdentityError::new(
                IdentityErrorCode::InvalidArgument,
                "zero_account_id",
            ));
        }
        Ok(Self(bytes))
    }

    #[must_use]
    pub const fn as_bytes(&self) -> &[u8; 16] {
        &self.0
    }
}

#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct CommandId([u8; 16]);

impl CommandId {
    pub fn new(bytes: [u8; 16]) -> Result<Self, IdentityError> {
        if bytes == [0; 16] {
            return Err(IdentityError::new(
                IdentityErrorCode::InvalidArgument,
                "zero_command_id",
            ));
        }
        Ok(Self(bytes))
    }
}

#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct Fingerprint([u8; 32]);

impl Fingerprint {
    pub fn new(bytes: [u8; 32]) -> Result<Self, IdentityError> {
        if bytes == [0; 32] {
            return Err(IdentityError::new(
                IdentityErrorCode::InvalidArgument,
                "zero_command_fingerprint",
            ));
        }
        Ok(Self(bytes))
    }
}

#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub enum IdentityProvider {
    Custom,
    Device,
    Email,
    Facebook,
    FacebookInstantGame,
    Apple,
    Google,
    GameCenter,
    Steam,
}

#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct ProviderIdentity {
    provider: IdentityProvider,
    external_id: String,
}

impl ProviderIdentity {
    pub fn new(
        provider: IdentityProvider,
        external_id: impl Into<String>,
    ) -> Result<Self, IdentityError> {
        let external_id = external_id.into();
        if external_id.is_empty()
            || external_id.len() > MAX_PROVIDER_IDENTITY_BYTES
            || external_id.trim() != external_id
            || external_id.chars().any(char::is_control)
        {
            return Err(IdentityError::new(
                IdentityErrorCode::InvalidArgument,
                "provider_identity_invalid",
            ));
        }
        Ok(Self {
            provider,
            external_id,
        })
    }

    #[must_use]
    pub const fn provider(&self) -> IdentityProvider {
        self.provider
    }

    #[must_use]
    pub fn external_id(&self) -> &str {
        &self.external_id
    }
}

#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct Username(String);

impl Username {
    pub fn new(value: impl Into<String>) -> Result<Self, IdentityError> {
        let value = value.into();
        if value.is_empty()
            || value.len() > MAX_USERNAME_BYTES
            || value.trim() != value
            || value.chars().any(char::is_control)
        {
            return Err(IdentityError::new(
                IdentityErrorCode::InvalidArgument,
                "username_invalid",
            ));
        }
        Ok(Self(value))
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DisplayName(String);

impl DisplayName {
    pub fn new(value: impl Into<String>) -> Result<Self, IdentityError> {
        let value = value.into();
        if value.len() > MAX_DISPLAY_NAME_BYTES || value.chars().any(char::is_control) {
            return Err(IdentityError::new(
                IdentityErrorCode::InvalidArgument,
                "display_name_invalid",
            ));
        }
        Ok(Self(value))
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AccountStatus {
    Active,
    Disabled,
    Banned,
    Deleted,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum IdentityErrorCode {
    InvalidArgument,
    Unauthenticated,
    NotFound,
    AlreadyExists,
    FailedPrecondition,
    PermissionDenied,
    ResourceExhausted,
    Conflict,
    OutOfRange,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct IdentityError {
    code: IdentityErrorCode,
    reason: &'static str,
}

impl IdentityError {
    const fn new(code: IdentityErrorCode, reason: &'static str) -> Self {
        Self { code, reason }
    }

    #[must_use]
    pub const fn code(&self) -> IdentityErrorCode {
        self.code
    }

    #[must_use]
    pub const fn reason(&self) -> &'static str {
        self.reason
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct IdentityConfig {
    max_accounts: usize,
    max_identities_per_account: usize,
    max_receipts: usize,
}

impl IdentityConfig {
    pub fn new(
        max_accounts: usize,
        max_identities_per_account: usize,
        max_receipts: usize,
    ) -> Result<Self, IdentityError> {
        if max_accounts == 0 || max_accounts > HARD_MAX_ACCOUNTS {
            return Err(IdentityError::new(
                IdentityErrorCode::InvalidArgument,
                "account_capacity_invalid",
            ));
        }
        if max_identities_per_account == 0
            || max_identities_per_account > HARD_MAX_IDENTITIES_PER_ACCOUNT
        {
            return Err(IdentityError::new(
                IdentityErrorCode::InvalidArgument,
                "provider_identity_capacity_invalid",
            ));
        }
        if max_receipts == 0 || max_receipts > HARD_MAX_RECEIPTS {
            return Err(IdentityError::new(
                IdentityErrorCode::InvalidArgument,
                "receipt_capacity_invalid",
            ));
        }
        Ok(Self {
            max_accounts,
            max_identities_per_account,
            max_receipts,
        })
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ReceiptOutcome {
    Created,
    Authenticated,
    Linked,
    Unlinked,
    Updated,
    StatusChanged,
    Deleted,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct CommandReceipt {
    command: CommandId,
    fingerprint: Fingerprint,
    account: AccountId,
    revision: u64,
    outcome: ReceiptOutcome,
}

impl CommandReceipt {
    #[must_use]
    pub const fn command(&self) -> CommandId {
        self.command
    }

    #[must_use]
    pub const fn account(&self) -> AccountId {
        self.account
    }

    #[must_use]
    pub const fn revision(&self) -> u64 {
        self.revision
    }

    #[must_use]
    pub const fn outcome(&self) -> ReceiptOutcome {
        self.outcome
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AccountRecord {
    id: AccountId,
    username: Username,
    display_name: DisplayName,
    revision: u64,
    status: AccountStatus,
    providers: BTreeSet<ProviderIdentity>,
}

impl AccountRecord {
    #[must_use]
    pub const fn id(&self) -> AccountId {
        self.id
    }

    #[must_use]
    pub fn username(&self) -> &Username {
        &self.username
    }

    #[must_use]
    pub fn display_name(&self) -> &DisplayName {
        &self.display_name
    }

    #[must_use]
    pub const fn revision(&self) -> u64 {
        self.revision
    }

    #[must_use]
    pub const fn status(&self) -> AccountStatus {
        self.status
    }

    pub fn providers(&self) -> impl ExactSizeIterator<Item = &ProviderIdentity> {
        self.providers.iter()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct IdentityRegistry {
    config: IdentityConfig,
    accounts: BTreeMap<AccountId, AccountRecord>,
    provider_owner: BTreeMap<ProviderIdentity, AccountId>,
    username_owner: BTreeMap<Username, AccountId>,
    retired_providers: BTreeSet<ProviderIdentity>,
    retired_usernames: BTreeSet<Username>,
    receipts: BTreeMap<CommandId, CommandReceipt>,
}

impl IdentityRegistry {
    #[must_use]
    pub fn new(config: IdentityConfig) -> Self {
        Self {
            config,
            accounts: BTreeMap::new(),
            provider_owner: BTreeMap::new(),
            username_owner: BTreeMap::new(),
            retired_providers: BTreeSet::new(),
            retired_usernames: BTreeSet::new(),
            receipts: BTreeMap::new(),
        }
    }

    #[must_use]
    pub fn account(&self, id: AccountId) -> Option<&AccountRecord> {
        self.accounts.get(&id)
    }

    #[must_use]
    pub fn owner_of(&self, identity: &ProviderIdentity) -> Option<AccountId> {
        self.provider_owner.get(identity).copied()
    }

    pub fn create_account(
        &mut self,
        command: CommandId,
        fingerprint: Fingerprint,
        account: AccountId,
        username: Username,
        display_name: DisplayName,
        provider: ProviderIdentity,
    ) -> Result<CommandReceipt, IdentityError> {
        if let Some(receipt) =
            self.existing_receipt(command, fingerprint, ReceiptOutcome::Created)?
        {
            return Ok(receipt);
        }
        self.ensure_receipt_capacity()?;
        if self.accounts.len() >= self.config.max_accounts {
            return Err(IdentityError::new(
                IdentityErrorCode::ResourceExhausted,
                "account_capacity_exhausted",
            ));
        }
        if self.accounts.contains_key(&account) {
            return Err(IdentityError::new(
                IdentityErrorCode::AlreadyExists,
                "account_id_exists",
            ));
        }
        if self.provider_owner.contains_key(&provider) || self.retired_providers.contains(&provider)
        {
            return Err(IdentityError::new(
                IdentityErrorCode::AlreadyExists,
                "provider_identity_exists_or_retired",
            ));
        }
        if self.username_owner.contains_key(&username) || self.retired_usernames.contains(&username)
        {
            return Err(IdentityError::new(
                IdentityErrorCode::AlreadyExists,
                "username_exists_or_retired",
            ));
        }

        let mut providers = BTreeSet::new();
        providers.insert(provider.clone());
        let record = AccountRecord {
            id: account,
            username: username.clone(),
            display_name,
            revision: 1,
            status: AccountStatus::Active,
            providers,
        };
        let receipt = CommandReceipt {
            command,
            fingerprint,
            account,
            revision: 1,
            outcome: ReceiptOutcome::Created,
        };
        let mut candidate = self.clone();
        candidate.accounts.insert(account, record);
        candidate.provider_owner.insert(provider, account);
        candidate.username_owner.insert(username, account);
        candidate.receipts.insert(command, receipt);
        candidate.validate_invariants()?;
        *self = candidate;
        Ok(receipt)
    }

    pub fn authenticate(
        &mut self,
        command: CommandId,
        fingerprint: Fingerprint,
        provider: &ProviderIdentity,
    ) -> Result<CommandReceipt, IdentityError> {
        if let Some(receipt) =
            self.existing_receipt(command, fingerprint, ReceiptOutcome::Authenticated)?
        {
            if receipt.outcome != ReceiptOutcome::Authenticated {
                return Err(IdentityError::new(
                    IdentityErrorCode::Conflict,
                    "command_operation_conflict",
                ));
            }
            let account = self.provider_owner.get(provider).copied().ok_or_else(|| {
                IdentityError::new(IdentityErrorCode::Unauthenticated, "identity_unknown")
            })?;
            if account != receipt.account {
                return Err(IdentityError::new(
                    IdentityErrorCode::Unauthenticated,
                    "identity_binding_changed",
                ));
            }
            let record = self.accounts.get(&account).ok_or_else(invariant_error)?;
            if record.status != AccountStatus::Active {
                return Err(IdentityError::new(
                    IdentityErrorCode::PermissionDenied,
                    "account_not_active",
                ));
            }
            if record.revision != receipt.revision {
                return Err(IdentityError::new(
                    IdentityErrorCode::Conflict,
                    "authentication_state_changed",
                ));
            }
            return Ok(receipt);
        }
        self.ensure_receipt_capacity()?;
        let account = self.provider_owner.get(provider).copied().ok_or_else(|| {
            IdentityError::new(IdentityErrorCode::Unauthenticated, "identity_unknown")
        })?;
        let record = self.accounts.get(&account).ok_or_else(invariant_error)?;
        if record.status != AccountStatus::Active {
            return Err(IdentityError::new(
                IdentityErrorCode::PermissionDenied,
                "account_not_active",
            ));
        }
        let receipt = CommandReceipt {
            command,
            fingerprint,
            account,
            revision: record.revision,
            outcome: ReceiptOutcome::Authenticated,
        };
        let mut candidate = self.clone();
        candidate.receipts.insert(command, receipt);
        candidate.validate_invariants()?;
        *self = candidate;
        Ok(receipt)
    }
    pub fn link_provider(
        &mut self,
        command: CommandId,
        fingerprint: Fingerprint,
        account: AccountId,
        expected_revision: u64,
        provider: ProviderIdentity,
    ) -> Result<CommandReceipt, IdentityError> {
        if let Some(receipt) =
            self.existing_receipt(command, fingerprint, ReceiptOutcome::Linked)?
        {
            return Ok(receipt);
        }
        self.ensure_receipt_capacity()?;
        let current = self.active_account(account, expected_revision)?.clone();
        if current.providers.len() >= self.config.max_identities_per_account {
            return Err(IdentityError::new(
                IdentityErrorCode::ResourceExhausted,
                "provider_identity_capacity_exhausted",
            ));
        }
        if self.retired_providers.contains(&provider) {
            return Err(IdentityError::new(
                IdentityErrorCode::FailedPrecondition,
                "provider_identity_retired",
            ));
        }
        if self.provider_owner.contains_key(&provider) {
            return Err(IdentityError::new(
                IdentityErrorCode::AlreadyExists,
                "provider_identity_exists",
            ));
        }
        let next = next_revision(current.revision)?;
        let mut candidate = self.clone();
        candidate
            .accounts
            .get_mut(&account)
            .ok_or_else(invariant_error)?
            .providers
            .insert(provider.clone());
        candidate
            .accounts
            .get_mut(&account)
            .ok_or_else(invariant_error)?
            .revision = next;
        candidate.provider_owner.insert(provider, account);
        let receipt = CommandReceipt {
            command,
            fingerprint,
            account,
            revision: next,
            outcome: ReceiptOutcome::Linked,
        };
        candidate.receipts.insert(command, receipt);
        candidate.validate_invariants()?;
        *self = candidate;
        Ok(receipt)
    }

    pub fn unlink_provider(
        &mut self,
        command: CommandId,
        fingerprint: Fingerprint,
        account: AccountId,
        expected_revision: u64,
        provider: &ProviderIdentity,
    ) -> Result<CommandReceipt, IdentityError> {
        if let Some(receipt) =
            self.existing_receipt(command, fingerprint, ReceiptOutcome::Unlinked)?
        {
            return Ok(receipt);
        }
        self.ensure_receipt_capacity()?;
        let current = self.active_account(account, expected_revision)?.clone();
        if !current.providers.contains(provider) {
            return Err(IdentityError::new(
                IdentityErrorCode::NotFound,
                "provider_identity_not_linked",
            ));
        }
        if current.providers.len() == 1 {
            return Err(IdentityError::new(
                IdentityErrorCode::FailedPrecondition,
                "last_provider_identity_cannot_be_unlinked",
            ));
        }
        let next = next_revision(current.revision)?;
        let mut candidate = self.clone();
        let removed = candidate
            .accounts
            .get_mut(&account)
            .ok_or_else(invariant_error)?
            .providers
            .remove(provider);
        if !removed {
            return Err(invariant_error());
        }
        candidate
            .accounts
            .get_mut(&account)
            .ok_or_else(invariant_error)?
            .revision = next;
        candidate.provider_owner.remove(provider);
        let receipt = CommandReceipt {
            command,
            fingerprint,
            account,
            revision: next,
            outcome: ReceiptOutcome::Unlinked,
        };
        candidate.receipts.insert(command, receipt);
        candidate.validate_invariants()?;
        *self = candidate;
        Ok(receipt)
    }

    pub fn update_profile(
        &mut self,
        command: CommandId,
        fingerprint: Fingerprint,
        account: AccountId,
        expected_revision: u64,
        username: Option<Username>,
        display_name: Option<DisplayName>,
    ) -> Result<CommandReceipt, IdentityError> {
        if let Some(receipt) =
            self.existing_receipt(command, fingerprint, ReceiptOutcome::Updated)?
        {
            return Ok(receipt);
        }
        self.ensure_receipt_capacity()?;
        if username.is_none() && display_name.is_none() {
            return Err(IdentityError::new(
                IdentityErrorCode::InvalidArgument,
                "profile_update_empty",
            ));
        }
        let current = self.active_account(account, expected_revision)?.clone();
        if let Some(next_username) = username.as_ref() {
            if next_username != &current.username
                && (self.username_owner.contains_key(next_username)
                    || self.retired_usernames.contains(next_username))
            {
                return Err(IdentityError::new(
                    IdentityErrorCode::AlreadyExists,
                    "username_exists_or_retired",
                ));
            }
        }
        let changes_username = username
            .as_ref()
            .is_some_and(|value| value != &current.username);
        let changes_display = display_name
            .as_ref()
            .is_some_and(|value| value != &current.display_name);
        if !changes_username && !changes_display {
            return Err(IdentityError::new(
                IdentityErrorCode::AlreadyExists,
                "profile_unchanged",
            ));
        }
        let next = next_revision(current.revision)?;
        let mut candidate = self.clone();
        if let Some(next_username) = username {
            if next_username != current.username {
                candidate.username_owner.remove(&current.username);
                candidate
                    .username_owner
                    .insert(next_username.clone(), account);
                candidate
                    .accounts
                    .get_mut(&account)
                    .ok_or_else(invariant_error)?
                    .username = next_username;
            }
        }
        if let Some(next_display) = display_name {
            candidate
                .accounts
                .get_mut(&account)
                .ok_or_else(invariant_error)?
                .display_name = next_display;
        }
        candidate
            .accounts
            .get_mut(&account)
            .ok_or_else(invariant_error)?
            .revision = next;
        let receipt = CommandReceipt {
            command,
            fingerprint,
            account,
            revision: next,
            outcome: ReceiptOutcome::Updated,
        };
        candidate.receipts.insert(command, receipt);
        candidate.validate_invariants()?;
        *self = candidate;
        Ok(receipt)
    }

    pub fn set_status(
        &mut self,
        command: CommandId,
        fingerprint: Fingerprint,
        account: AccountId,
        expected_revision: u64,
        target: AccountStatus,
    ) -> Result<CommandReceipt, IdentityError> {
        if let Some(receipt) =
            self.existing_receipt(command, fingerprint, ReceiptOutcome::StatusChanged)?
        {
            return Ok(receipt);
        }
        self.ensure_receipt_capacity()?;
        if target == AccountStatus::Deleted {
            return Err(IdentityError::new(
                IdentityErrorCode::InvalidArgument,
                "delete_requires_delete_account",
            ));
        }
        let current = self
            .account_with_revision(account, expected_revision)?
            .clone();
        if current.status == AccountStatus::Deleted {
            return Err(IdentityError::new(
                IdentityErrorCode::FailedPrecondition,
                "account_deleted",
            ));
        }
        if current.status == target {
            return Err(IdentityError::new(
                IdentityErrorCode::AlreadyExists,
                "account_status_unchanged",
            ));
        }
        let next = next_revision(current.revision)?;
        let mut candidate = self.clone();
        let record = candidate
            .accounts
            .get_mut(&account)
            .ok_or_else(invariant_error)?;
        record.status = target;
        record.revision = next;
        let receipt = CommandReceipt {
            command,
            fingerprint,
            account,
            revision: next,
            outcome: ReceiptOutcome::StatusChanged,
        };
        candidate.receipts.insert(command, receipt);
        candidate.validate_invariants()?;
        *self = candidate;
        Ok(receipt)
    }

    pub fn delete_account(
        &mut self,
        command: CommandId,
        fingerprint: Fingerprint,
        account: AccountId,
        expected_revision: u64,
    ) -> Result<CommandReceipt, IdentityError> {
        if let Some(receipt) =
            self.existing_receipt(command, fingerprint, ReceiptOutcome::Deleted)?
        {
            return Ok(receipt);
        }
        self.ensure_receipt_capacity()?;
        let current = self
            .account_with_revision(account, expected_revision)?
            .clone();
        if current.status == AccountStatus::Deleted {
            return Err(IdentityError::new(
                IdentityErrorCode::FailedPrecondition,
                "account_deleted",
            ));
        }
        let next = next_revision(current.revision)?;
        let mut candidate = self.clone();
        candidate.username_owner.remove(&current.username);
        candidate.retired_usernames.insert(current.username.clone());
        for provider in &current.providers {
            candidate.provider_owner.remove(provider);
            candidate.retired_providers.insert(provider.clone());
        }
        let record = candidate
            .accounts
            .get_mut(&account)
            .ok_or_else(invariant_error)?;
        record.status = AccountStatus::Deleted;
        record.revision = next;
        let receipt = CommandReceipt {
            command,
            fingerprint,
            account,
            revision: next,
            outcome: ReceiptOutcome::Deleted,
        };
        candidate.receipts.insert(command, receipt);
        candidate.validate_invariants()?;
        *self = candidate;
        Ok(receipt)
    }

    pub fn validate_invariants(&self) -> Result<(), IdentityError> {
        if self.accounts.len() > self.config.max_accounts
            || self.receipts.len() > self.config.max_receipts
        {
            return Err(invariant_error());
        }
        let mut active_usernames = 0_usize;
        let mut active_providers = 0_usize;
        for (id, record) in &self.accounts {
            if id != &record.id
                || record.providers.is_empty()
                || record.providers.len() > self.config.max_identities_per_account
            {
                return Err(invariant_error());
            }
            if record.status == AccountStatus::Deleted {
                if self.username_owner.contains_key(&record.username)
                    || !self.retired_usernames.contains(&record.username)
                {
                    return Err(invariant_error());
                }
                for provider in &record.providers {
                    if self.provider_owner.contains_key(provider)
                        || !self.retired_providers.contains(provider)
                    {
                        return Err(invariant_error());
                    }
                }
            } else {
                active_usernames = active_usernames
                    .checked_add(1)
                    .ok_or_else(invariant_error)?;
                if self.username_owner.get(&record.username) != Some(id)
                    || self.retired_usernames.contains(&record.username)
                {
                    return Err(invariant_error());
                }
                for provider in &record.providers {
                    active_providers = active_providers
                        .checked_add(1)
                        .ok_or_else(invariant_error)?;
                    if self.provider_owner.get(provider) != Some(id)
                        || self.retired_providers.contains(provider)
                    {
                        return Err(invariant_error());
                    }
                }
            }
        }
        if active_usernames != self.username_owner.len()
            || active_providers != self.provider_owner.len()
        {
            return Err(invariant_error());
        }
        for (command, receipt) in &self.receipts {
            if command != &receipt.command || !self.accounts.contains_key(&receipt.account) {
                return Err(invariant_error());
            }
        }
        Ok(())
    }

    fn existing_receipt(
        &self,
        command: CommandId,
        fingerprint: Fingerprint,
        expected_outcome: ReceiptOutcome,
    ) -> Result<Option<CommandReceipt>, IdentityError> {
        let Some(receipt) = self.receipts.get(&command) else {
            return Ok(None);
        };
        if receipt.fingerprint != fingerprint {
            return Err(IdentityError::new(
                IdentityErrorCode::Conflict,
                "command_fingerprint_conflict",
            ));
        }
        if receipt.outcome != expected_outcome {
            return Err(IdentityError::new(
                IdentityErrorCode::Conflict,
                "command_operation_conflict",
            ));
        }
        Ok(Some(*receipt))
    }
    fn ensure_receipt_capacity(&self) -> Result<(), IdentityError> {
        if self.receipts.len() >= self.config.max_receipts {
            return Err(IdentityError::new(
                IdentityErrorCode::ResourceExhausted,
                "receipt_capacity_exhausted",
            ));
        }
        Ok(())
    }

    fn account_with_revision(
        &self,
        account: AccountId,
        expected_revision: u64,
    ) -> Result<&AccountRecord, IdentityError> {
        let record = self
            .accounts
            .get(&account)
            .ok_or_else(|| IdentityError::new(IdentityErrorCode::NotFound, "account_not_found"))?;
        if record.revision != expected_revision {
            return Err(IdentityError::new(
                IdentityErrorCode::Conflict,
                "account_revision_conflict",
            ));
        }
        Ok(record)
    }

    fn active_account(
        &self,
        account: AccountId,
        expected_revision: u64,
    ) -> Result<&AccountRecord, IdentityError> {
        let record = self.account_with_revision(account, expected_revision)?;
        if record.status != AccountStatus::Active {
            return Err(IdentityError::new(
                IdentityErrorCode::PermissionDenied,
                "account_not_active",
            ));
        }
        Ok(record)
    }
}

fn next_revision(current: u64) -> Result<u64, IdentityError> {
    current.checked_add(1).ok_or_else(|| {
        IdentityError::new(IdentityErrorCode::OutOfRange, "account_revision_overflow")
    })
}

const fn invariant_error() -> IdentityError {
    IdentityError::new(
        IdentityErrorCode::FailedPrecondition,
        "identity_invariant_violation",
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bytes16(value: u8) -> [u8; 16] {
        let mut bytes = [0; 16];
        bytes[15] = value;
        bytes
    }

    fn bytes32(value: u8) -> [u8; 32] {
        let mut bytes = [0; 32];
        bytes[31] = value;
        bytes
    }

    fn account(value: u8) -> AccountId {
        AccountId::new(bytes16(value)).unwrap()
    }

    fn command(value: u8) -> CommandId {
        CommandId::new(bytes16(value)).unwrap()
    }

    fn fingerprint(value: u8) -> Fingerprint {
        Fingerprint::new(bytes32(value)).unwrap()
    }

    fn identity(provider: IdentityProvider, value: &str) -> ProviderIdentity {
        ProviderIdentity::new(provider, value).unwrap()
    }

    fn registry(accounts: usize, identities: usize, receipts: usize) -> IdentityRegistry {
        IdentityRegistry::new(IdentityConfig::new(accounts, identities, receipts).unwrap())
    }

    fn create(registry: &mut IdentityRegistry, seed: u8, provider: ProviderIdentity) {
        registry
            .create_account(
                command(seed),
                fingerprint(seed),
                account(seed),
                Username::new(format!("user-{seed}")).unwrap(),
                DisplayName::new(format!("User {seed}")).unwrap(),
                provider,
            )
            .unwrap();
    }

    #[test]
    fn identifiers_and_configuration_are_bounded() {
        assert_eq!(
            AccountId::new([0; 16]).unwrap_err().reason(),
            "zero_account_id"
        );
        assert!(IdentityConfig::new(0, 1, 1).is_err());
        assert!(IdentityConfig::new(1, 0, 1).is_err());
        assert!(IdentityConfig::new(1, 1, 0).is_err());
        assert!(Username::new(" bad ").is_err());
        assert!(ProviderIdentity::new(IdentityProvider::Device, "").is_err());
    }

    #[test]
    fn create_authenticate_and_exact_command_replay_are_deterministic() {
        let provider = identity(IdentityProvider::Device, "device-1");
        let mut registry = registry(4, 4, 16);
        let receipt = registry
            .create_account(
                command(1),
                fingerprint(1),
                account(1),
                Username::new("alpha").unwrap(),
                DisplayName::new("Alpha").unwrap(),
                provider.clone(),
            )
            .unwrap();
        let snapshot = registry.clone();
        let duplicate = registry
            .create_account(
                command(1),
                fingerprint(1),
                account(2),
                Username::new("ignored").unwrap(),
                DisplayName::new("ignored").unwrap(),
                identity(IdentityProvider::Email, "ignored@example.invalid"),
            )
            .unwrap();
        assert_eq!(receipt, duplicate);
        assert_eq!(snapshot, registry);
        let auth = registry
            .authenticate(command(2), fingerprint(2), &provider)
            .unwrap();
        assert_eq!(auth.account(), account(1));
        assert_eq!(auth.outcome(), ReceiptOutcome::Authenticated);
        registry.validate_invariants().unwrap();
    }

    #[test]
    fn changed_fingerprint_replay_fails_without_mutation() {
        let provider = identity(IdentityProvider::Custom, "custom-1");
        let mut registry = registry(4, 4, 16);
        create(&mut registry, 1, provider);
        let snapshot = registry.clone();
        let error = registry
            .authenticate(
                command(1),
                fingerprint(9),
                &identity(IdentityProvider::Custom, "custom-1"),
            )
            .unwrap_err();
        assert_eq!(error.code(), IdentityErrorCode::Conflict);
        assert_eq!(snapshot, registry);
    }

    #[test]
    fn provider_and_username_collisions_are_atomic() {
        let provider = identity(IdentityProvider::Google, "google-1");
        let mut registry = registry(4, 4, 16);
        create(&mut registry, 1, provider.clone());
        let snapshot = registry.clone();
        let error = registry
            .create_account(
                command(2),
                fingerprint(2),
                account(2),
                Username::new("user-2").unwrap(),
                DisplayName::new("User 2").unwrap(),
                provider,
            )
            .unwrap_err();
        assert_eq!(error.code(), IdentityErrorCode::AlreadyExists);
        assert_eq!(snapshot, registry);
        let error = registry
            .create_account(
                command(3),
                fingerprint(3),
                account(3),
                Username::new("user-1").unwrap(),
                DisplayName::new("User 3").unwrap(),
                identity(IdentityProvider::Steam, "steam-3"),
            )
            .unwrap_err();
        assert_eq!(error.code(), IdentityErrorCode::AlreadyExists);
        assert_eq!(snapshot, registry);
    }

    #[test]
    fn stale_revision_never_mutates_provider_links() {
        let mut registry = registry(4, 4, 16);
        create(
            &mut registry,
            1,
            identity(IdentityProvider::Device, "device-1"),
        );
        let snapshot = registry.clone();
        let error = registry
            .link_provider(
                command(2),
                fingerprint(2),
                account(1),
                0,
                identity(IdentityProvider::Email, "a@example.invalid"),
            )
            .unwrap_err();
        assert_eq!(error.reason(), "account_revision_conflict");
        assert_eq!(snapshot, registry);
    }

    #[test]
    fn last_provider_cannot_be_unlinked_and_second_provider_can() {
        let first = identity(IdentityProvider::Device, "device-1");
        let second = identity(IdentityProvider::Apple, "apple-1");
        let mut registry = registry(4, 4, 16);
        create(&mut registry, 1, first.clone());
        let snapshot = registry.clone();
        assert_eq!(
            registry
                .unlink_provider(command(2), fingerprint(2), account(1), 1, &first)
                .unwrap_err()
                .reason(),
            "last_provider_identity_cannot_be_unlinked"
        );
        assert_eq!(snapshot, registry);
        registry
            .link_provider(command(3), fingerprint(3), account(1), 1, second.clone())
            .unwrap();
        registry
            .unlink_provider(command(4), fingerprint(4), account(1), 2, &first)
            .unwrap();
        assert_eq!(registry.owner_of(&first), None);
        assert_eq!(registry.owner_of(&second), Some(account(1)));
    }

    #[test]
    fn profile_update_preserves_unique_username_and_revision_fence() {
        let mut registry = registry(4, 4, 16);
        create(
            &mut registry,
            1,
            identity(IdentityProvider::Device, "device-1"),
        );
        create(
            &mut registry,
            2,
            identity(IdentityProvider::Device, "device-2"),
        );
        let snapshot = registry.clone();
        assert!(registry
            .update_profile(
                command(3),
                fingerprint(3),
                account(1),
                1,
                Some(Username::new("user-2").unwrap()),
                None,
            )
            .is_err());
        assert_eq!(snapshot, registry);
        let receipt = registry
            .update_profile(
                command(4),
                fingerprint(4),
                account(1),
                1,
                Some(Username::new("renamed").unwrap()),
                Some(DisplayName::new("Renamed").unwrap()),
            )
            .unwrap();
        assert_eq!(receipt.revision(), 2);
        assert_eq!(
            registry.account(account(1)).unwrap().username().as_str(),
            "renamed"
        );
    }

    #[test]
    fn disabled_and_banned_accounts_cannot_authenticate() {
        let provider = identity(IdentityProvider::Facebook, "fb-1");
        let mut registry = registry(4, 4, 16);
        create(&mut registry, 1, provider.clone());
        registry
            .set_status(
                command(2),
                fingerprint(2),
                account(1),
                1,
                AccountStatus::Disabled,
            )
            .unwrap();
        assert_eq!(
            registry
                .authenticate(command(3), fingerprint(3), &provider)
                .unwrap_err()
                .code(),
            IdentityErrorCode::PermissionDenied
        );
        registry
            .set_status(
                command(4),
                fingerprint(4),
                account(1),
                2,
                AccountStatus::Active,
            )
            .unwrap();
        assert!(registry
            .authenticate(command(5), fingerprint(5), &provider)
            .is_ok());
    }

    #[test]
    fn deleted_identity_and_username_are_retired() {
        let provider = identity(IdentityProvider::Steam, "steam-1");
        let mut registry = registry(4, 4, 16);
        create(&mut registry, 1, provider.clone());
        registry
            .delete_account(command(2), fingerprint(2), account(1), 1)
            .unwrap();
        assert_eq!(registry.owner_of(&provider), None);
        assert_eq!(
            registry
                .authenticate(command(3), fingerprint(3), &provider)
                .unwrap_err()
                .code(),
            IdentityErrorCode::Unauthenticated
        );
        let snapshot = registry.clone();
        assert!(registry
            .create_account(
                command(4),
                fingerprint(4),
                account(2),
                Username::new("user-1").unwrap(),
                DisplayName::new("Replacement").unwrap(),
                provider,
            )
            .is_err());
        assert_eq!(snapshot, registry);
    }

    #[test]
    fn receipt_capacity_failure_is_atomic() {
        let provider = identity(IdentityProvider::GameCenter, "gc-1");
        let mut registry = registry(4, 4, 1);
        create(&mut registry, 1, provider.clone());
        let snapshot = registry.clone();
        assert_eq!(
            registry
                .authenticate(command(2), fingerprint(2), &provider)
                .unwrap_err()
                .code(),
            IdentityErrorCode::ResourceExhausted
        );
        assert_eq!(snapshot, registry);
    }

    #[test]
    fn authentication_exact_replay_requires_unchanged_active_authority() {
        let provider = identity(IdentityProvider::Device, "device-auth-replay");
        let mut registry = registry(4, 4, 32);
        create(&mut registry, 1, provider.clone());
        let receipt = registry
            .authenticate(command(2), fingerprint(2), &provider)
            .unwrap();
        let snapshot = registry.clone();
        let replay = registry
            .authenticate(command(2), fingerprint(2), &provider)
            .unwrap();
        assert_eq!(receipt, replay);
        assert_eq!(snapshot, registry);

        registry
            .update_profile(
                command(3),
                fingerprint(3),
                account(1),
                1,
                None,
                Some(DisplayName::new("Changed").unwrap()),
            )
            .unwrap();
        let changed = registry.clone();
        let error = registry
            .authenticate(command(2), fingerprint(2), &provider)
            .unwrap_err();
        assert_eq!(error.code(), IdentityErrorCode::Conflict);
        assert_eq!(error.reason(), "authentication_state_changed");
        assert_eq!(changed, registry);
    }

    #[test]
    fn authentication_replay_after_disabled_or_banned_is_denied_atomically() {
        for (index, target) in [AccountStatus::Disabled, AccountStatus::Banned]
            .into_iter()
            .enumerate()
        {
            let provider = identity(
                IdentityProvider::Facebook,
                &format!("provider-status-{index}"),
            );
            let mut registry = registry(4, 4, 32);
            create(&mut registry, 1, provider.clone());
            registry
                .authenticate(command(2), fingerprint(2), &provider)
                .unwrap();
            registry
                .set_status(command(3), fingerprint(3), account(1), 1, target)
                .unwrap();
            let snapshot = registry.clone();
            let error = registry
                .authenticate(command(2), fingerprint(2), &provider)
                .unwrap_err();
            assert_eq!(error.code(), IdentityErrorCode::PermissionDenied);
            assert_eq!(error.reason(), "account_not_active");
            assert_eq!(snapshot, registry);
        }
    }

    #[test]
    fn authentication_replay_after_reactivation_remains_stale() {
        let provider = identity(IdentityProvider::Google, "provider-reactivated");
        let mut registry = registry(4, 4, 32);
        create(&mut registry, 1, provider.clone());
        registry
            .authenticate(command(2), fingerprint(2), &provider)
            .unwrap();
        registry
            .set_status(
                command(3),
                fingerprint(3),
                account(1),
                1,
                AccountStatus::Disabled,
            )
            .unwrap();
        registry
            .set_status(
                command(4),
                fingerprint(4),
                account(1),
                2,
                AccountStatus::Active,
            )
            .unwrap();
        let snapshot = registry.clone();
        let error = registry
            .authenticate(command(2), fingerprint(2), &provider)
            .unwrap_err();
        assert_eq!(error.code(), IdentityErrorCode::Conflict);
        assert_eq!(error.reason(), "authentication_state_changed");
        assert_eq!(snapshot, registry);
    }

    #[test]
    fn authentication_replay_after_deletion_is_denied_atomically() {
        let provider = identity(IdentityProvider::Steam, "provider-deleted");
        let mut registry = registry(4, 4, 32);
        create(&mut registry, 1, provider.clone());
        registry
            .authenticate(command(2), fingerprint(2), &provider)
            .unwrap();
        registry
            .delete_account(command(3), fingerprint(3), account(1), 1)
            .unwrap();
        let snapshot = registry.clone();
        let error = registry
            .authenticate(command(2), fingerprint(2), &provider)
            .unwrap_err();
        assert_eq!(error.code(), IdentityErrorCode::Unauthenticated);
        assert_eq!(error.reason(), "identity_unknown");
        assert_eq!(snapshot, registry);
    }

    #[test]
    fn authentication_replay_after_unlink_or_rebind_is_denied_atomically() {
        let provider = identity(IdentityProvider::Device, "provider-movable");
        let fallback = identity(IdentityProvider::Email, "fallback@example.invalid");
        let mut registry = registry(8, 4, 64);
        create(&mut registry, 1, provider.clone());
        registry
            .link_provider(command(2), fingerprint(2), account(1), 1, fallback)
            .unwrap();
        registry
            .authenticate(command(3), fingerprint(3), &provider)
            .unwrap();
        registry
            .unlink_provider(command(4), fingerprint(4), account(1), 2, &provider)
            .unwrap();
        let unlinked = registry.clone();
        let error = registry
            .authenticate(command(3), fingerprint(3), &provider)
            .unwrap_err();
        assert_eq!(error.code(), IdentityErrorCode::Unauthenticated);
        assert_eq!(error.reason(), "identity_unknown");
        assert_eq!(unlinked, registry);

        registry
            .create_account(
                command(5),
                fingerprint(5),
                account(2),
                Username::new("rebind-target").unwrap(),
                DisplayName::new("Rebind Target").unwrap(),
                identity(IdentityProvider::Apple, "apple-rebind-target"),
            )
            .unwrap();
        registry
            .link_provider(command(6), fingerprint(6), account(2), 1, provider.clone())
            .unwrap();
        let rebound = registry.clone();
        let error = registry
            .authenticate(command(3), fingerprint(3), &provider)
            .unwrap_err();
        assert_eq!(error.code(), IdentityErrorCode::Unauthenticated);
        assert_eq!(error.reason(), "identity_binding_changed");
        assert_eq!(rebound, registry);
    }

    #[test]
    fn authentication_replay_rejects_receipt_from_another_operation() {
        let provider = identity(IdentityProvider::Custom, "provider-operation");
        let mut registry = registry(4, 4, 16);
        create(&mut registry, 1, provider.clone());
        let snapshot = registry.clone();
        let error = registry
            .authenticate(command(1), fingerprint(1), &provider)
            .unwrap_err();
        assert_eq!(error.code(), IdentityErrorCode::Conflict);
        assert_eq!(error.reason(), "command_operation_conflict");
        assert_eq!(snapshot, registry);
    }

    #[test]
    fn revision_overflow_fails_before_mutation() {
        let provider = identity(IdentityProvider::Email, "a@example.invalid");
        let mut registry = registry(4, 4, 16);
        create(&mut registry, 1, provider.clone());
        registry.accounts.get_mut(&account(1)).unwrap().revision = u64::MAX;
        let snapshot = registry.clone();
        assert_eq!(
            registry
                .link_provider(
                    command(2),
                    fingerprint(2),
                    account(1),
                    u64::MAX,
                    identity(IdentityProvider::Device, "device-2"),
                )
                .unwrap_err()
                .code(),
            IdentityErrorCode::OutOfRange
        );
        assert_eq!(snapshot, registry);
    }
}
