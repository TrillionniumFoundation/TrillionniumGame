#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct PgPoolSnapshot {
    pub max_size: u32,
    pub connections: u32,
    pub idle_connections: u32,
    pub acquire_attempts: u64,
    pub acquire_failures: u64,
    pub session_policy_failures: u64,
    pub inflight_operations: u64,
    pub deadline_cancellations: u64,
    pub shutdown_cancellations: u64,
    pub cancellation_deliveries: u64,
    pub cancellation_failures: u64,
}

#[derive(Debug, Default)]
struct PgPoolMetrics {
    acquire_attempts: AtomicU64,
    acquire_failures: AtomicU64,
    session_policy_failures: AtomicU64,
    inflight_operations: AtomicU64,
    deadline_cancellations: AtomicU64,
    shutdown_cancellations: AtomicU64,
    cancellation_deliveries: AtomicU64,
    cancellation_failures: AtomicU64,
}

#[derive(Clone)]
enum PoolInner {
    Plain(Pool<PlainManager>),
    Tls {
        pool: Pool<TlsManager>,
        cancellation_connector: MakeTlsConnector,
    },
}

#[derive(Clone)]
struct CancelEntry {
    action: CancelAction,
    reason: Arc<AtomicU8>,
}

struct CancelState {
    next_id: AtomicU64,
    entries: Mutex<BTreeMap<u64, CancelEntry>>,
    metrics: Arc<PgPoolMetrics>,
}

impl CancelState {
    fn new(metrics: Arc<PgPoolMetrics>) -> Self {
        Self {
            next_id: AtomicU64::new(0),
            entries: Mutex::new(BTreeMap::new()),
            metrics,
        }
    }

    fn register(
        &self,
        action: CancelAction,
        reason: Arc<AtomicU8>,
    ) -> Result<u64, DomainError> {
        let previous = self
            .next_id
            .fetch_update(Ordering::AcqRel, Ordering::Acquire, |current| {
                current.checked_add(1)
            })
            .map_err(|_| operational_error("database_cancellation_id_exhausted"))?;
        let id = previous + 1;
        let mut entries = self
            .entries
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        match entries.entry(id) {
            std::collections::btree_map::Entry::Vacant(slot) => {
                slot.insert(CancelEntry { action, reason });
            }
            std::collections::btree_map::Entry::Occupied(_) => {
                return Err(operational_error("database_cancellation_registry_collision"));
            }
        }
        drop(entries);
        self.metrics
            .inflight_operations
            .fetch_add(1, Ordering::Relaxed);
        Ok(id)
    }

    fn request(&self, id: u64, reason: u8) -> Option<bool> {
        if !matches!(reason, CANCEL_DEADLINE | CANCEL_SHUTDOWN) {
            return None;
        }
        let entry = self
            .entries
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .get(&id)
            .cloned()?;
        if entry
            .reason
            .compare_exchange(CANCEL_NONE, reason, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            return None;
        }
        match reason {
            CANCEL_DEADLINE => {
                self.metrics
                    .deadline_cancellations
                    .fetch_add(1, Ordering::Relaxed);
            }
            CANCEL_SHUTDOWN => {
                self.metrics
                    .shutdown_cancellations
                    .fetch_add(1, Ordering::Relaxed);
            }
            _ => unreachable!("reason validated before registry lookup"),
        }
        let delivered = (entry.action)();
        let metric = if delivered {
            &self.metrics.cancellation_deliveries
        } else {
            &self.metrics.cancellation_failures
        };
        metric.fetch_add(1, Ordering::Relaxed);
        Some(delivered)
    }

    fn complete(&self, id: u64) {
        if self
            .entries
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .remove(&id)
            .is_some()
        {
            self.metrics
                .inflight_operations
                .fetch_sub(1, Ordering::Relaxed);
        }
    }

    fn cancel_all_for_shutdown(self: &Arc<Self>) -> u64 {
        let ids = self
            .entries
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .keys()
            .copied()
            .collect::<Vec<_>>();
        let requested = thread::scope(|scope| {
            let handles = ids
                .into_iter()
                .map(|id| {
                    let state = Arc::clone(self);
                    scope.spawn(move || state.request(id, CANCEL_SHUTDOWN).is_some())
                })
                .collect::<Vec<_>>();
            handles
                .into_iter()
                .map(|handle| handle.join().unwrap_or(false))
                .filter(|requested| *requested)
                .count()
        });
        u64::try_from(requested).unwrap_or(u64::MAX)
    }
}

impl fmt::Debug for CancelState {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("CancelState")
            .field(
                "inflight_operations",
                &self.metrics.inflight_operations.load(Ordering::Relaxed),
            )
            .finish_non_exhaustive()
    }
}

#[derive(Debug)]
struct DeadlineGuard {
    state: Arc<CancelState>,
    id: u64,
    reason: Arc<AtomicU8>,
    done: Option<Sender<()>>,
    watcher: Option<JoinHandle<()>>,
}

impl DeadlineGuard {
    fn start(
        state: Arc<CancelState>,
        timeout: Duration,
        action: CancelAction,
    ) -> Result<Self, DomainError> {
        if timeout < MINIMUM_OPERATION_BUDGET {
            return Err(operation_deadline_exceeded());
        }
        let reason = Arc::new(AtomicU8::new(CANCEL_NONE));
        let id = state.register(action, Arc::clone(&reason))?;
        let (done, receiver) = mpsc::channel();
        let watcher_state = Arc::clone(&state);
        let watcher = match thread::Builder::new()
            .name(format!("trnm-pg-deadline-{id}"))
            .spawn(move || match receiver.recv_timeout(timeout) {
                Err(RecvTimeoutError::Timeout) => {
                    let _ = watcher_state.request(id, CANCEL_DEADLINE);
                }
                Ok(()) | Err(RecvTimeoutError::Disconnected) => {}
            }) {
            Ok(watcher) => watcher,
            Err(_) => {
                state.complete(id);
                return Err(operational_error(
                    "database_deadline_supervisor_unavailable",
                ));
            }
        };
        Ok(Self {
            state,
            id,
            reason,
            done: Some(done),
            watcher: Some(watcher),
        })
    }

    fn finish(mut self) -> u8 {
        self.cleanup();
        self.reason.load(Ordering::Acquire)
    }

    fn cleanup(&mut self) {
        if let Some(done) = self.done.take() {
            let _ = done.send(());
        }
        if let Some(watcher) = self.watcher.take() {
            let _ = watcher.join();
        }
        self.state.complete(self.id);
    }
}

impl Drop for DeadlineGuard {
    fn drop(&mut self) {
        self.cleanup();
    }
}
