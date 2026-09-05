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
    // Entry-local serialization, never the registry lock, spans dispatch.
    // A cloned entry cannot dispatch after retirement; retirement waits for
    // any locally running sender before the associated lease can be returned.
    retired: Arc<Mutex<bool>>,
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

    fn register(&self, action: CancelAction, reason: Arc<AtomicU8>) -> Result<u64, DomainError> {
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
                slot.insert(CancelEntry {
                    action,
                    reason,
                    retired: Arc::new(Mutex::new(false)),
                });
            }
            std::collections::btree_map::Entry::Occupied(_) => {
                return Err(operational_error("database_cancellation_registry_collision"));
            }
        }
        // Publish the gauge before another thread can remove this entry.
        self.metrics
            .inflight_operations
            .fetch_add(1, Ordering::Relaxed);
        drop(entries);
        Ok(id)
    }

    fn request(&self, id: u64, reason: u8) -> Option<bool> {
        let entry = self
            .entries
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .get(&id)
            .cloned()?;
        self.request_entry(&entry, reason)
    }

    fn request_entry(&self, entry: &CancelEntry, reason: u8) -> Option<bool> {
        if !matches!(reason, CANCEL_DEADLINE | CANCEL_SHUTDOWN) {
            return None;
        }
        let retired = entry
            .retired
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if *retired {
            return None;
        }
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
        let delivered = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| (entry.action)()))
            .unwrap_or(false);
        let metric = if delivered {
            &self.metrics.cancellation_deliveries
        } else {
            &self.metrics.cancellation_failures
        };
        metric.fetch_add(1, Ordering::Relaxed);
        drop(retired);
        Some(delivered)
    }

    fn complete(&self, id: u64) {
        // Release the registry mutex before waiting for entry-local dispatch.
        let entry = self
            .entries
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .remove(&id);
        if let Some(entry) = entry {
            let mut retired = entry
                .retired
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            *retired = true;
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
                return Err(operational_error("database_deadline_supervisor_unavailable"));
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

#[cfg(test)]
mod cancellation_lifecycle_tests {
    use super::*;

    fn state_and_entry(action: CancelAction) -> (Arc<CancelState>, u64, CancelEntry) {
        let state = Arc::new(CancelState::new(Arc::new(PgPoolMetrics::default())));
        let id = state
            .register(action, Arc::new(AtomicU8::new(CANCEL_NONE)))
            .unwrap();
        let entry = state.entries.lock().unwrap().get(&id).unwrap().clone();
        (state, id, entry)
    }

    #[test]
    fn retired_snapshot_never_dispatches_a_late_cancel() {
        let calls = Arc::new(AtomicU64::new(0));
        let recorded = Arc::clone(&calls);
        let (state, id, snapshot) = state_and_entry(Arc::new(move || {
            recorded.fetch_add(1, Ordering::Relaxed);
            true
        }));
        state.complete(id);
        assert_eq!(state.request_entry(&snapshot, CANCEL_SHUTDOWN), None);
        assert_eq!(state.request_entry(&snapshot, CANCEL_DEADLINE), None);
        assert_eq!(snapshot.reason.load(Ordering::Acquire), CANCEL_NONE);
        assert_eq!(calls.load(Ordering::Relaxed), 0);
        assert_eq!(state.metrics.inflight_operations.load(Ordering::Relaxed), 0);
    }

    #[test]
    fn completion_waits_for_sender_without_holding_registry_lock() {
        let (entered, receiving) = mpsc::channel();
        let (release, released) = mpsc::channel();
        let released = Mutex::new(released);
        let (state, id, snapshot) = state_and_entry(Arc::new(move || {
            entered.send(()).unwrap();
            released
                .lock()
                .unwrap()
                .recv_timeout(Duration::from_secs(5))
                .unwrap();
            true
        }));
        let sender_state = Arc::clone(&state);
        let sender = thread::spawn(move || sender_state.request(id, CANCEL_SHUTDOWN));
        receiving.recv_timeout(Duration::from_secs(5)).unwrap();
        assert!(snapshot.retired.try_lock().is_err());
        let completion_state = Arc::clone(&state);
        let (completed, completion) = mpsc::channel();
        let worker = thread::spawn(move || {
            completion_state.complete(id);
            completed.send(()).unwrap();
        });
        let started = Instant::now();
        loop {
            if let Ok(entries) = state.entries.try_lock() {
                if !entries.contains_key(&id) {
                    break;
                }
            }
            assert!(started.elapsed() < Duration::from_secs(5));
            thread::yield_now();
        }
        assert!(matches!(completion.try_recv(), Err(mpsc::TryRecvError::Empty)));
        assert_eq!(state.metrics.inflight_operations.load(Ordering::Relaxed), 1);
        let other = state
            .register(Arc::new(|| true), Arc::new(AtomicU8::new(CANCEL_NONE)))
            .unwrap();
        assert_eq!(state.request(other, CANCEL_DEADLINE), Some(true));
        state.complete(other);
        release.send(()).unwrap();
        assert_eq!(sender.join().unwrap(), Some(true));
        completion.recv_timeout(Duration::from_secs(5)).unwrap();
        worker.join().unwrap();
        assert!(*snapshot.retired.lock().unwrap());
        assert_eq!(state.metrics.inflight_operations.load(Ordering::Relaxed), 0);
    }

    #[test]
    fn panicking_sender_records_failure_and_can_be_retired() {
        let (state, id, snapshot) = state_and_entry(Arc::new(|| panic!("test sender panic")));
        assert_eq!(state.request(id, CANCEL_DEADLINE), Some(false));
        assert_eq!(state.request(id, CANCEL_SHUTDOWN), None);
        state.complete(id);
        assert!(*snapshot.retired.lock().unwrap());
        assert_eq!(state.metrics.cancellation_failures.load(Ordering::Relaxed), 1);
        assert_eq!(state.metrics.inflight_operations.load(Ordering::Relaxed), 0);
    }

    #[test]
    fn duplicate_completion_and_invalid_reason_do_not_change_metrics() {
        let (state, id, snapshot) = state_and_entry(Arc::new(|| true));
        assert_eq!(state.request(id, CANCEL_NONE), None);
        assert_eq!(state.request(id, u8::MAX), None);
        assert_eq!(snapshot.reason.load(Ordering::Acquire), CANCEL_NONE);
        state.complete(id);
        state.complete(id);
        assert_eq!(state.request(id, CANCEL_SHUTDOWN), None);
        assert_eq!(state.metrics.inflight_operations.load(Ordering::Relaxed), 0);
        assert_eq!(state.metrics.shutdown_cancellations.load(Ordering::Relaxed), 0);
    }
}
