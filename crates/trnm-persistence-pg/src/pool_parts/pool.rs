#[derive(Clone)]
pub struct PgPool {
    profile: DatabaseProfile,
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
        let result = match configure_session(
            &mut repository.client,
            self.profile,
            self.policy,
            remaining,
        ) {
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
            session_policy_failures: self
                .metrics
                .session_policy_failures
                .load(Ordering::Relaxed),
            inflight_operations: self.metrics.inflight_operations.load(Ordering::Relaxed),
            deadline_cancellations: self
                .metrics
                .deadline_cancellations
                .load(Ordering::Relaxed),
            shutdown_cancellations: self
                .metrics
                .shutdown_cancellations
                .load(Ordering::Relaxed),
            cancellation_deliveries: self
                .metrics
                .cancellation_deliveries
                .load(Ordering::Relaxed),
            cancellation_failures: self
                .metrics
                .cancellation_failures
                .load(Ordering::Relaxed),
        }
    }

    #[must_use]
    pub const fn profile(&self) -> DatabaseProfile {
        self.profile
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
