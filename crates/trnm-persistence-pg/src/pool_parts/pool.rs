/// Account operation observation remains separate from the outer lease boundary.
/// An interrupted observed success is not permission to acknowledge or retry it.
/// `None` means no operation result was observed, not proof of no durable effect.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PgAccountLeaseCancellation {
    None,
    Deadline,
    Shutdown,
}

pub struct PgAccountLeaseOutcome<T> {
    pub observed: Option<Result<T, crate::NakamaAccountError>>,
    pub boundary_error: Option<DomainError>,
    pub setup_error: Option<DomainError>,
    pub cancellation: PgAccountLeaseCancellation,
    /// Actual retirement flag; retirement prevents recycling, not commit proof.
    pub lease_retired: bool,
}
impl<T> fmt::Debug for PgAccountLeaseOutcome<T> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("PgAccountLeaseOutcome")
            .field("operation_result_observed", &self.observed.is_some())
            .field(
                "observed_success",
                &self.observed.as_ref().map(Result::is_ok),
            )
            .field("boundary_code", &self.boundary_error.map(DomainError::code))
            .field("cancellation", &self.cancellation)
            .field("lease_retired", &self.lease_retired)
            .finish()
    }
}
impl<T> PgAccountLeaseOutcome<T> {
    fn not_observed(error: DomainError) -> Self {
        Self {
            observed: None,
            boundary_error: Some(error),
            setup_error: Some(error),
            cancellation: PgAccountLeaseCancellation::None,
            lease_retired: false,
        }
    }
}
fn account_lease_outcome<T>(
    observed: Option<Result<T, crate::NakamaAccountError>>,
    setup_error: Option<DomainError>,
    cancellation_reason: u8,
    expired: bool,
    lease_retired: bool,
) -> PgAccountLeaseOutcome<T> {
    let boundary_error = match cancellation_reason {
        CANCEL_DEADLINE => Some(operation_deadline_exceeded()),
        CANCEL_SHUTDOWN => Some(operation_shutdown_cancelled()),
        _ if expired => Some(operation_deadline_exceeded()),
        _ => setup_error,
    };
    let cancellation = match cancellation_reason {
        CANCEL_DEADLINE => PgAccountLeaseCancellation::Deadline,
        CANCEL_SHUTDOWN => PgAccountLeaseCancellation::Shutdown,
        _ => PgAccountLeaseCancellation::None,
    };
    PgAccountLeaseOutcome {
        observed,
        boundary_error,
        setup_error,
        cancellation,
        lease_retired,
    }
}

#[derive(Clone)]
pub struct PgPool {
    profile: DatabaseProfile,
    serving_schema_target: crate::AuthoritativeSchemaTarget,
    policy: PgPoolConfig,
    inner: PoolInner,
    metrics: Arc<PgPoolMetrics>,
    cancellations: Arc<CancelState>,
}

impl PgPool {
    pub fn connect_plain(
        database_url: &str,
        profile: DatabaseProfile,
        policy: PgPoolConfig,
    ) -> Result<Self, DomainError> {
        Self::connect_plain_for_target(
            database_url,
            profile,
            policy,
            crate::AuthoritativeSchemaTarget::StorageV4,
        )
    }

    pub fn connect_plain_for_target(
        database_url: &str,
        profile: DatabaseProfile,
        policy: PgPoolConfig,
        target: crate::AuthoritativeSchemaTarget,
    ) -> Result<Self, DomainError> {
        target.require_capture_ready()?;
        let policy = policy.validate()?;
        let mut database = Config::from_str(database_url).map_err(super::map_postgres_error)?;
        database.ssl_mode(SslMode::Disable);
        database.connect_timeout(policy.acquire_timeout);
        let manager = RetirementManager::new(PostgresConnectionManager::new(database, NoTls));
        let pool = pool_builder(&policy)
            .build(manager)
            .map_err(|_| operational_error("database_pool_initialization_failed"))?;
        let metrics = Arc::new(PgPoolMetrics::default());
        let cancellations = Arc::new(CancelState::new(Arc::clone(&metrics)));
        Ok(Self {
            profile,
            serving_schema_target: target,
            policy,
            inner: PoolInner::Plain(pool),
            metrics,
            cancellations,
        })
    }

    pub fn connect_tls(
        database_url: &str,
        profile: DatabaseProfile,
        policy: PgPoolConfig,
        tls: &PgTlsConfig,
    ) -> Result<Self, DomainError> {
        Self::connect_tls_for_target(
            database_url,
            profile,
            policy,
            tls,
            crate::AuthoritativeSchemaTarget::StorageV4,
        )
    }

    pub fn connect_tls_for_target(
        database_url: &str,
        profile: DatabaseProfile,
        policy: PgPoolConfig,
        tls: &PgTlsConfig,
        target: crate::AuthoritativeSchemaTarget,
    ) -> Result<Self, DomainError> {
        target.require_capture_ready()?;
        let policy = policy.validate()?;
        let mut database = Config::from_str(database_url).map_err(super::map_postgres_error)?;
        database.ssl_mode(SslMode::Require);
        database.connect_timeout(policy.acquire_timeout);
        let connector = tls.connector()?;
        let manager =
            RetirementManager::new(PostgresConnectionManager::new(database, connector.clone()));
        let pool = pool_builder(&policy)
            .build(manager)
            .map_err(|_| operational_error("database_tls_pool_initialization_failed"))?;
        let metrics = Arc::new(PgPoolMetrics::default());
        let cancellations = Arc::new(CancelState::new(Arc::clone(&metrics)));
        Ok(Self {
            profile,
            serving_schema_target: target,
            policy,
            inner: PoolInner::Tls {
                pool,
                cancellation_connector: connector,
            },
            metrics,
            cancellations,
        })
    }

    pub fn acquire(&self) -> Result<PgRepository, DomainError> {
        let mut repository = self.acquire_unconfigured(self.policy.acquire_timeout)?;
        if let Err(error) = configure_session(
            &mut repository.client,
            self.profile,
            self.policy,
            self.policy.statement_timeout,
        ) {
            self.metrics
                .session_policy_failures
                .fetch_add(1, Ordering::Relaxed);
            return Err(error);
        }
        Ok(repository)
    }

    pub fn run_with_deadline<T>(
        &self,
        total_budget: Duration,
        operation: impl FnOnce(&mut PgRepository) -> Result<T, DomainError>,
    ) -> Result<T, DomainError> {
        if total_budget < MINIMUM_OPERATION_BUDGET {
            return Err(operation_deadline_exceeded());
        }
        let started = Instant::now();
        let acquire_budget = total_budget.min(self.policy.acquire_timeout);
        let mut repository = match self.acquire_unconfigured(acquire_budget) {
            Ok(repository) => repository,
            Err(_) if started.elapsed() >= total_budget => {
                return Err(operation_deadline_exceeded());
            }
            Err(error) => return Err(error),
        };
        let remaining = total_budget.saturating_sub(started.elapsed());
        if remaining < MINIMUM_OPERATION_BUDGET {
            return Err(operation_deadline_exceeded());
        }

        let action = self.cancellation_action(
            repository.client.cancel_token(),
            repository.client.retirement_flag(),
        );
        let deadline = DeadlineGuard::start(Arc::clone(&self.cancellations), remaining, action)?;
        let result =
            match configure_session(&mut repository.client, self.profile, self.policy, remaining) {
                Ok(()) => operation(&mut repository),
                Err(error) => {
                    self.metrics
                        .session_policy_failures
                        .fetch_add(1, Ordering::Relaxed);
                    Err(error)
                }
            };
        let cancellation_reason = deadline.finish();
        let elapsed = started.elapsed();
        if cancellation_reason != CANCEL_NONE || elapsed >= total_budget {
            repository.client.retire();
        }
        match cancellation_reason {
            CANCEL_DEADLINE => Err(operation_deadline_exceeded()),
            CANCEL_SHUTDOWN => Err(operation_shutdown_cancelled()),
            _ if elapsed >= total_budget => Err(operation_deadline_exceeded()),
            _ => result,
        }
    }

    /// One lease, one native account call, no generic retry. The native account
    /// engine alone owns its bounded attempts. Keep late commit/cleanup/error
    /// observations while the outer deadline prevents a successful wire reply.
    pub fn run_account_with_deadline<T>(
        &self,
        total_budget: Duration,
        operation: impl FnOnce(&mut PgRepository) -> Result<T, crate::NakamaAccountError>,
    ) -> PgAccountLeaseOutcome<T> {
        if total_budget < MINIMUM_OPERATION_BUDGET {
            return PgAccountLeaseOutcome::not_observed(operation_deadline_exceeded());
        }
        let started = Instant::now();
        let acquire_budget = total_budget.min(self.policy.acquire_timeout);
        let mut repository = match self.acquire_unconfigured(acquire_budget) {
            Ok(repository) => repository,
            Err(error) => {
                return account_lease_outcome(
                    None,
                    Some(error),
                    CANCEL_NONE,
                    started.elapsed() >= total_budget,
                    false,
                );
            }
        };
        let remaining = total_budget.saturating_sub(started.elapsed());
        if remaining < MINIMUM_OPERATION_BUDGET {
            return PgAccountLeaseOutcome::not_observed(operation_deadline_exceeded());
        }
        let action = self.cancellation_action(
            repository.client.cancel_token(),
            repository.client.retirement_flag(),
        );
        let deadline =
            match DeadlineGuard::start(Arc::clone(&self.cancellations), remaining, action) {
                Ok(deadline) => deadline,
                Err(error) => return PgAccountLeaseOutcome::not_observed(error),
            };
        let (observed, setup_error) =
            match configure_session(&mut repository.client, self.profile, self.policy, remaining) {
                Ok(()) => (Some(operation(&mut repository)), None),
                Err(error) => {
                    self.metrics
                        .session_policy_failures
                        .fetch_add(1, Ordering::Relaxed);
                    // The native session policy is not confirmed on this lease.
                    repository.client.retire();
                    (None, Some(error))
                }
            };
        let cancellation_reason = deadline.finish();
        let expired = started.elapsed() >= total_budget;
        if cancellation_reason != CANCEL_NONE || expired {
            repository.client.retire();
        }
        if observed.as_ref().is_some_and(|result| matches!(result,
            Err(crate::NakamaAccountError::Internal(failure) | crate::NakamaAccountError::UsernameAlreadyExists(failure))
                if failure.unknown_commit || failure.cleanup_failure.is_some())) {
            repository.client.retire();
        }
        let lease_retired = repository
            .client
            .retirement_flag()
            .is_some_and(|flag| flag.load(Ordering::Acquire));
        account_lease_outcome(
            observed,
            setup_error,
            cancellation_reason,
            expired,
            lease_retired,
        )
    }

    #[must_use]
    pub fn cancel_inflight(&self) -> u64 {
        self.cancellations.cancel_all_for_shutdown()
    }

    #[must_use]
    pub fn snapshot(&self) -> PgPoolSnapshot {
        let (max_size, state) = match &self.inner {
            PoolInner::Plain(pool) => (pool.max_size(), pool.state()),
            PoolInner::Tls { pool, .. } => (pool.max_size(), pool.state()),
        };
        PgPoolSnapshot {
            max_size,
            connections: state.connections,
            idle_connections: state.idle_connections,
            acquire_attempts: self.metrics.acquire_attempts.load(Ordering::Relaxed),
            acquire_failures: self.metrics.acquire_failures.load(Ordering::Relaxed),
            session_policy_failures: self.metrics.session_policy_failures.load(Ordering::Relaxed),
            inflight_operations: self.metrics.inflight_operations.load(Ordering::Relaxed),
            deadline_cancellations: self.metrics.deadline_cancellations.load(Ordering::Relaxed),
            shutdown_cancellations: self.metrics.shutdown_cancellations.load(Ordering::Relaxed),
            cancellation_deliveries: self.metrics.cancellation_deliveries.load(Ordering::Relaxed),
            cancellation_failures: self.metrics.cancellation_failures.load(Ordering::Relaxed),
        }
    }

    #[must_use]
    pub const fn profile(&self) -> DatabaseProfile {
        self.profile
    }

    #[must_use]
    pub const fn serving_schema_target(&self) -> crate::AuthoritativeSchemaTarget {
        self.serving_schema_target
    }

    #[must_use]
    pub const fn policy(&self) -> PgPoolConfig {
        self.policy
    }

    fn acquire_unconfigured(&self, timeout: Duration) -> Result<PgRepository, DomainError> {
        self.metrics
            .acquire_attempts
            .fetch_add(1, Ordering::Relaxed);
        let handle = match &self.inner {
            PoolInner::Plain(pool) => pool.get_timeout(timeout).map(ClientHandle::Plain),
            PoolInner::Tls { pool, .. } => pool.get_timeout(timeout).map(ClientHandle::Tls),
        }
        .map_err(|_| {
            self.metrics
                .acquire_failures
                .fetch_add(1, Ordering::Relaxed);
            operational_error("database_pool_acquire_timeout")
        })?;
        Ok(PgRepository {
            profile: self.profile,
            serving_schema_target: self.serving_schema_target,
            client: handle,
        })
    }

    fn cancellation_action(
        &self,
        token: CancelToken,
        retirement: Option<Arc<AtomicBool>>,
    ) -> CancelAction {
        match &self.inner {
            PoolInner::Plain(_) => Arc::new(move || {
                if let Some(retired) = &retirement {
                    retired.store(true, Ordering::Release);
                }
                std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    token.cancel_query(NoTls).is_ok()
                }))
                .unwrap_or(false)
            }),
            PoolInner::Tls {
                cancellation_connector,
                ..
            } => {
                let connector = cancellation_connector.clone();
                Arc::new(move || {
                    if let Some(retired) = &retirement {
                        retired.store(true, Ordering::Release);
                    }
                    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                        token.cancel_query(connector.clone()).is_ok()
                    }))
                    .unwrap_or(false)
                })
            }
        }
    }
}

impl fmt::Debug for PgPool {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        let transport = match &self.inner {
            PoolInner::Plain(_) => "plaintext-candidate",
            PoolInner::Tls { .. } => "tls-verify-full",
        };
        formatter
            .debug_struct("PgPool")
            .field("profile", &self.profile)
            .field("transport", &transport)
            .field("policy", &self.policy)
            .field("snapshot", &self.snapshot())
            .finish()
    }
}

fn pool_builder<M>(policy: &PgPoolConfig) -> r2d2::Builder<M>
where
    M: r2d2::ManageConnection,
{
    Pool::builder()
        .max_size(policy.max_size)
        .min_idle(Some(policy.min_idle))
        .connection_timeout(policy.acquire_timeout)
        .idle_timeout(Some(policy.idle_timeout))
        .max_lifetime(Some(policy.max_lifetime))
        .test_on_check_out(true)
}

fn configure_session(
    client: &mut Client,
    profile: DatabaseProfile,
    policy: PgPoolConfig,
    operation_budget: Duration,
) -> Result<(), DomainError> {
    let statement_budget = policy
        .statement_timeout
        .min(operation_budget)
        .max(MINIMUM_OPERATION_BUDGET);
    let statement_timeout = duration_millis(statement_budget)?;
    client
        .batch_execute(&format!(
            "SET application_name = 'trillionnium-game'; SET statement_timeout = '{statement_timeout}ms';"
        ))
        .map_err(super::map_postgres_error)?;
    if profile == DatabaseProfile::PostgreSql {
        let lock_timeout = duration_millis(policy.lock_timeout.min(statement_budget))?;
        let idle_transaction_timeout =
            duration_millis(policy.idle_transaction_timeout.min(statement_budget))?;
        client
            .batch_execute(&format!(
                "SET lock_timeout = '{lock_timeout}ms'; \
                 SET idle_in_transaction_session_timeout = '{idle_transaction_timeout}ms';"
            ))
            .map_err(super::map_postgres_error)?;
    }
    Ok(())
}

fn duration_millis(value: Duration) -> Result<u64, DomainError> {
    u64::try_from(value.as_millis().max(1))
        .map_err(|_| configuration_error("database_timeout_millis_overflow"))
}

fn configuration_error(reason: &'static str) -> DomainError {
    DomainError::new(StableCode::InvalidArgument, reason, RetryClass::Never)
}

fn operational_error(reason: &'static str) -> DomainError {
    DomainError::new(StableCode::Unavailable, reason, RetryClass::SafeBackoff)
}

const fn operation_deadline_exceeded() -> DomainError {
    DomainError::new(
        StableCode::Unavailable,
        "database_operation_deadline_exceeded",
        RetryClass::SafeBackoff,
    )
}

const fn operation_shutdown_cancelled() -> DomainError {
    DomainError::new(
        StableCode::Unavailable,
        "database_operation_shutdown_cancelled",
        RetryClass::SafeBackoff,
    )
}
