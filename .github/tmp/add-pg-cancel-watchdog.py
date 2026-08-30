from pathlib import Path

repo = Path('.')
pool = repo / 'crates/trnm-persistence-pg/src/pool.rs'
lib = repo / 'crates/trnm-persistence-pg/src/lib.rs'
server_pool = repo / 'crates/trnm-persistence-pg/src/bin/trnm_server/pool.rs'
app = repo / 'crates/trnm-persistence-pg/src/bin/trnm_server/app.rs'
for path in (pool, lib, server_pool, app):
    if not path.is_file():
        raise SystemExit(f'missing required source file: {path}')

source = pool.read_text(encoding='utf-8')
if 'struct CancellationWatchdog' not in source:
    source = source.replace(
        'use std::sync::atomic::{AtomicU64, Ordering};\nuse std::sync::Arc;\nuse std::time::Duration;\n',
        'use std::sync::atomic::{AtomicU64, Ordering};\nuse std::sync::{mpsc, Arc};\nuse std::thread::{self, JoinHandle};\nuse std::time::Duration;\n',
    )
    source = source.replace(
        '    Tls(PooledConnection<TlsManager>),\n',
        '    Tls {\n        client: PooledConnection<TlsManager>,\n        connector: MakeTlsConnector,\n    },\n',
    )
    source = source.replace('            Self::Tls(_) => "pooled-tls",\n', '            Self::Tls { .. } => "pooled-tls",\n')
    source = source.replace('            Self::Tls(client) => client,\n', '            Self::Tls { client, .. } => client,\n')
    source = source.replace('            Self::Tls(client) => client,\n', '            Self::Tls { client, .. } => client,\n')
    marker = '''impl DerefMut for ClientHandle {
    fn deref_mut(&mut self) -> &mut Self::Target {
        match self {
            Self::Direct(client) => client,
            Self::Plain(client) => client,
            Self::Tls { client, .. } => client,
        }
    }
}
'''
    insert = marker + '''
#[derive(Clone)]
pub(crate) enum CancelTransport {
    Plain,
    Tls(MakeTlsConnector),
}

impl fmt::Debug for CancelTransport {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::Plain => "plaintext",
            Self::Tls(_) => "tls",
        })
    }
}

#[derive(Debug)]
pub(crate) struct CancellationWatchdog {
    completion: Option<mpsc::SyncSender<()>>,
    worker: Option<JoinHandle<()>>,
}

impl CancellationWatchdog {
    pub(crate) fn start(
        client: &ClientHandle,
        timeout: Duration,
        metrics: Option<Arc<PgPoolMetrics>>,
    ) -> Result<Self, DomainError> {
        if timeout.is_zero() {
            return Err(configuration_error("database_operation_timeout_invalid"));
        }
        let token = client.cancel_token();
        let transport = match client {
            ClientHandle::Direct(_) | ClientHandle::Plain(_) => CancelTransport::Plain,
            ClientHandle::Tls { connector, .. } => CancelTransport::Tls(connector.clone()),
        };
        let (completion, wait) = mpsc::sync_channel(1);
        let worker = thread::Builder::new()
            .name("trnm-pg-cancel-watchdog".to_owned())
            .spawn(move || {
                if wait.recv_timeout(timeout) == Err(mpsc::RecvTimeoutError::Timeout) {
                    if let Some(metrics) = &metrics {
                        metrics.cancellation_requests.fetch_add(1, Ordering::Relaxed);
                    }
                    let result = match transport {
                        CancelTransport::Plain => token.cancel_query(NoTls),
                        CancelTransport::Tls(connector) => token.cancel_query(connector),
                    };
                    if result.is_err() {
                        if let Some(metrics) = &metrics {
                            metrics.cancellation_failures.fetch_add(1, Ordering::Relaxed);
                        }
                    }
                }
            })
            .map_err(|_| operational_error("database_cancellation_watchdog_spawn_failed"))?;
        Ok(Self {
            completion: Some(completion),
            worker: Some(worker),
        })
    }
}

impl Drop for CancellationWatchdog {
    fn drop(&mut self) {
        if let Some(completion) = self.completion.take() {
            let _ = completion.try_send(());
        }
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}
'''
    if marker not in source:
        raise SystemExit('ClientHandle DerefMut anchor missing')
    source = source.replace(marker, insert, 1)
    source = source.replace(
        '    pub session_policy_failures: u64,\n',
        '    pub session_policy_failures: u64,\n    pub cancellation_requests: u64,\n    pub cancellation_failures: u64,\n',
    )
    source = source.replace(
        '    session_policy_failures: AtomicU64,\n',
        '    session_policy_failures: AtomicU64,\n    cancellation_requests: AtomicU64,\n    cancellation_failures: AtomicU64,\n',
    )
    source = source.replace(
        '    Tls(Pool<TlsManager>),\n',
        '    Tls {\n        pool: Pool<TlsManager>,\n        connector: MakeTlsConnector,\n    },\n',
    )
    source = source.replace(
        '        let manager = PostgresConnectionManager::new(database, tls.connector()?);\n',
        '        let connector = tls.connector()?;\n        let manager = PostgresConnectionManager::new(database, connector.clone());\n',
    )
    source = source.replace(
        '            inner: PoolInner::Tls(pool),\n',
        '            inner: PoolInner::Tls { pool, connector },\n',
    )
    source = source.replace(
        '''            PoolInner::Tls(pool) => pool
                .get_timeout(self.policy.acquire_timeout)
                .map(ClientHandle::Tls),
''',
        '''            PoolInner::Tls { pool, connector } => pool
                .get_timeout(self.policy.acquire_timeout)
                .map(|client| ClientHandle::Tls {
                    client,
                    connector: connector.clone(),
                }),
''',
    )
    source = source.replace(
        '            PoolInner::Tls(pool) => (pool.max_size(), pool.state()),\n',
        '            PoolInner::Tls { pool, .. } => (pool.max_size(), pool.state()),\n',
    )
    source = source.replace(
        '''            session_policy_failures: self
                .metrics
                .session_policy_failures
                .load(Ordering::Relaxed),
''',
        '''            session_policy_failures: self
                .metrics
                .session_policy_failures
                .load(Ordering::Relaxed),
            cancellation_requests: self
                .metrics
                .cancellation_requests
                .load(Ordering::Relaxed),
            cancellation_failures: self
                .metrics
                .cancellation_failures
                .load(Ordering::Relaxed),
''',
    )
    source = source.replace('            PoolInner::Tls(_) => "tls-verify-full",\n', '            PoolInner::Tls { .. } => "tls-verify-full",\n')
    source = source.replace(
        '''        let mut repository = PgRepository {
            profile: self.profile,
            client: handle,
        };
''',
        '''        let mut repository = PgRepository {
            profile: self.profile,
            client: handle,
            operation_timeout: self.policy.statement_timeout,
            cancellation_metrics: Some(Arc::clone(&self.metrics)),
        };
''',
    )
source = source.replace('struct PgPoolMetrics {', 'pub(crate) struct PgPoolMetrics {')
pool.write_text(source, encoding='utf-8')

source = lib.read_text(encoding='utf-8')
if 'operation_timeout:' not in source:
    source = source.replace(
        'use std::collections::BTreeSet;\n',
        'use std::collections::BTreeSet;\nuse std::sync::Arc;\nuse std::time::Duration;\n',
    )
    source = source.replace(
        '''pub struct PgRepository {
    profile: DatabaseProfile,
    client: pool::ClientHandle,
}''',
        '''pub struct PgRepository {
    profile: DatabaseProfile,
    client: pool::ClientHandle,
    operation_timeout: Duration,
    cancellation_metrics: Option<Arc<pool::PgPoolMetrics>>,
}''',
    )
    source = source.replace(
        '''        Ok(Self {
            profile,
            client: pool::ClientHandle::direct(client),
        })
''',
        '''        Ok(Self {
            profile,
            client: pool::ClientHandle::direct(client),
            operation_timeout: Duration::from_secs(5),
            cancellation_metrics: None,
        })
''',
    )
    anchor = '''    pub const fn profile(&self) -> DatabaseProfile {
        self.profile
    }
'''
    if anchor not in source:
        raise SystemExit('PgRepository profile anchor missing')
    source = source.replace(
        anchor,
        anchor + '''
    pub(crate) fn cancellation_watchdog(
        &self,
    ) -> Result<pool::CancellationWatchdog, DomainError> {
        pool::CancellationWatchdog::start(
            &self.client,
            self.operation_timeout,
            self.cancellation_metrics.clone(),
        )
    }
''',
        1,
    )
lib.write_text(source, encoding='utf-8')

source = server_pool.read_text(encoding='utf-8')
if 'cancellation_watchdog' not in source:
    source = source.replace(
        '''        self.pool
            .acquire()?
            .bootstrap_entity(entity, authority_generation, state, updated_at_ms)
''',
        '''        let mut repository = self.pool.acquire()?;
        let _cancellation = repository.cancellation_watchdog()?;
        repository.bootstrap_entity(entity, authority_generation, state, updated_at_ms)
''',
    )
    source = source.replace(
        '        self.pool.acquire()?.commit_command(request)\n',
        '''        let mut repository = self.pool.acquire()?;
        let _cancellation = repository.cancellation_watchdog()?;
        repository.commit_command(request)
''',
    )
    source = source.replace(
        '            pool_session_policy_failures: snapshot.session_policy_failures,\n',
        '            pool_session_policy_failures: snapshot.session_policy_failures,\n            cancellation_requests: snapshot.cancellation_requests,\n            cancellation_failures: snapshot.cancellation_failures,\n',
    )
server_pool.write_text(source, encoding='utf-8')

source = app.read_text(encoding='utf-8')
if 'cancellation_requests:' not in source:
    source = source.replace(
        '''    pub pool_session_policy_failures: u64,
    pub retry_attempts: u64,
''',
        '''    pub pool_session_policy_failures: u64,
    pub cancellation_requests: u64,
    pub cancellation_failures: u64,
    pub retry_attempts: u64,
''',
    )
    metric_anchor = '''# TYPE trnm_server_database_retry_attempts_total counter\\n\\
trnm_server_database_retry_attempts_total {}\\n\\
'''
    if metric_anchor in source:
        source = source.replace(
            metric_anchor,
            '''# TYPE trnm_server_database_cancellation_requests_total counter\\n\\
trnm_server_database_cancellation_requests_total {}\\n\\
# TYPE trnm_server_database_cancellation_failures_total counter\\n\\
trnm_server_database_cancellation_failures_total {}\\n\\
''' + metric_anchor,
            1,
        )
        source = source.replace(
            '            repository.retry_attempts,\n',
            '            repository.cancellation_requests,\n            repository.cancellation_failures,\n            repository.retry_attempts,\n',
            1,
        )
app.write_text(source, encoding='utf-8')
