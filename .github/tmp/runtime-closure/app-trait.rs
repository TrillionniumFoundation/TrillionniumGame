#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct RepositoryOperationalMetrics {
    pub pool_max_size: u64,
    pub pool_connections: u64,
    pub pool_idle_connections: u64,
    pub pool_acquire_attempts: u64,
    pub pool_acquire_failures: u64,
    pub pool_session_policy_failures: u64,
    pub retry_attempts: u64,
    pub retries: u64,
    pub retry_exhausted: u64,
    pub retry_sleep_milliseconds: u64,
    pub deadline_watchdogs: u64,
    pub deadline_cancellations: u64,
    pub deadline_cancel_failures: u64,
}

pub trait Repository: std::fmt::Debug {
    fn bootstrap_entity(
        &mut self,
        entity: EntityId,
        authority_generation: u64,
        state: Digest32,
        updated_at_ms: u64,
    ) -> Result<EntityHead, DomainError>;

    fn commit_command(&mut self, request: &CommitRequest) -> Result<CommitOutcome, DomainError>;

    fn operational_metrics(&self) -> RepositoryOperationalMetrics {
        RepositoryOperationalMetrics::default()
    }
}

