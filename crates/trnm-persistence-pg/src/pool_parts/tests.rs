#[cfg(test)]
mod tests {
    use super::*;

    fn action(counter: Arc<AtomicU64>, succeeds: bool) -> CancelAction {
        Arc::new(move || {
            counter.fetch_add(1, Ordering::Relaxed);
            succeeds
        })
    }

    #[test]
    fn default_pool_policy_is_bounded_and_valid() {
        let policy = PgPoolConfig::default().validate().unwrap();
        assert_eq!(policy.max_size, 8);
        assert_eq!(policy.min_idle, 1);
        assert!(policy.acquire_timeout < policy.statement_timeout);
        assert!(policy.lock_timeout <= policy.statement_timeout);
        assert!(policy.idle_timeout < policy.max_lifetime);
    }

    #[test]
    fn invalid_pool_policy_fails_closed() {
        let policy = PgPoolConfig {
            max_size: 0,
            ..PgPoolConfig::default()
        };
        assert_eq!(
            policy.validate().unwrap_err().reason(),
            "database_pool_policy_invalid"
        );

        let default = PgPoolConfig::default();
        let policy = PgPoolConfig {
            min_idle: default.max_size + 1,
            ..default
        };
        assert_eq!(
            policy.validate().unwrap_err().reason(),
            "database_pool_policy_invalid"
        );

        let policy = PgPoolConfig {
            lock_timeout: Duration::from_secs(6),
            statement_timeout: Duration::from_secs(5),
            ..PgPoolConfig::default()
        };
        assert_eq!(
            policy.validate().unwrap_err().reason(),
            "database_pool_policy_invalid"
        );
    }

    #[test]
    fn tls_identity_requires_cert_and_key_pair() {
        assert_eq!(
            PgTlsConfig::new(None, Some(b"cert".to_vec()), None)
                .unwrap_err()
                .reason(),
            "database_tls_identity_cert_key_pair_required"
        );
        assert!(PgTlsConfig::new(None, None, None).is_ok());
    }

    #[test]
    fn tls_debug_never_exposes_private_key_material() {
        let config = PgTlsConfig::new(
            None,
            Some(b"certificate".to_vec()),
            Some(b"super-secret-private-key".to_vec()),
        )
        .unwrap();
        let debug = format!("{config:?}");
        assert!(debug.contains("<redacted>"));
        assert!(!debug.contains("super-secret"));
    }

    #[test]
    fn completed_deadline_guard_does_not_cancel() {
        let metrics = Arc::new(PgPoolMetrics::default());
        let state = Arc::new(CancelState::new(metrics));
        let calls = Arc::new(AtomicU64::new(0));
        let deadline = DeadlineGuard::start(
            Arc::clone(&state),
            Duration::from_secs(1),
            action(Arc::clone(&calls), true),
        )
        .unwrap();
        assert_eq!(deadline.finish(), CANCEL_NONE);
        assert_eq!(calls.load(Ordering::Relaxed), 0);
        assert_eq!(state.metrics.inflight_operations.load(Ordering::Relaxed), 0);
    }

    #[test]
    fn deadline_and_shutdown_requests_are_single_delivery() {
        let metrics = Arc::new(PgPoolMetrics::default());
        let state = Arc::new(CancelState::new(Arc::clone(&metrics)));
        let calls = Arc::new(AtomicU64::new(0));
        let deadline = DeadlineGuard::start(
            Arc::clone(&state),
            Duration::from_millis(2),
            action(Arc::clone(&calls), true),
        )
        .unwrap();
        thread::sleep(Duration::from_millis(20));
        assert_eq!(deadline.finish(), CANCEL_DEADLINE);
        assert_eq!(calls.load(Ordering::Relaxed), 1);
        assert_eq!(metrics.deadline_cancellations.load(Ordering::Relaxed), 1);
        assert_eq!(metrics.cancellation_deliveries.load(Ordering::Relaxed), 1);

        let reason = Arc::new(AtomicU8::new(CANCEL_NONE));
        let id = state.register(action(Arc::clone(&calls), true), reason);
        assert_eq!(state.cancel_all_for_shutdown(), 1);
        assert_eq!(state.cancel_all_for_shutdown(), 0);
        state.complete(id);
        assert_eq!(metrics.shutdown_cancellations.load(Ordering::Relaxed), 1);
        assert_eq!(calls.load(Ordering::Relaxed), 2);
    }

    #[test]
    fn sub_millisecond_budget_fails_closed() {
        let metrics = Arc::new(PgPoolMetrics::default());
        assert_eq!(
            DeadlineGuard::start(
                Arc::new(CancelState::new(metrics)),
                Duration::from_nanos(1),
                action(Arc::new(AtomicU64::new(0)), true),
            )
            .unwrap_err()
            .reason(),
            "database_operation_deadline_exceeded"
        );
        assert_eq!(duration_millis(Duration::from_nanos(1)).unwrap(), 1);
    }

    fn live_database_url() -> Option<String> {
        match std::env::var("TRNM_TEST_DATABASE_URL") {
            Ok(value) if !value.is_empty() => Some(value),
            Ok(_) | Err(_)
                if std::env::var("TRNM_REQUIRE_LIVE_PG_DEADLINE").as_deref() == Ok("1") =>
            {
                panic!("TRNM_TEST_DATABASE_URL is required for the live deadline lane");
            }
            Ok(_) | Err(_) => None,
        }
    }

    fn live_policy() -> PgPoolConfig {
        PgPoolConfig {
            max_size: 4,
            min_idle: 1,
            acquire_timeout: Duration::from_secs(2),
            idle_timeout: Duration::from_secs(30),
            max_lifetime: Duration::from_secs(5 * 60),
            statement_timeout: Duration::from_secs(30),
            lock_timeout: Duration::from_secs(1),
            idle_transaction_timeout: Duration::from_secs(5),
        }
    }

    #[test]
    fn live_deadline_cancels_blocking_query_and_keeps_pool_usable() {
        let Some(database_url) = live_database_url() else {
            return;
        };
        let pool =
            PgPool::connect_plain(&database_url, DatabaseProfile::PostgreSql, live_policy())
                .unwrap();
        let started = Instant::now();
        let returned = pool
            .run_with_deadline(Duration::from_millis(150), |repository| {
                repository
                    .client
                    .batch_execute("SELECT pg_sleep(10)")
                    .map_err(super::super::map_postgres_error)
            })
            .unwrap_err();
        assert_eq!(returned.reason(), "database_operation_deadline_exceeded");
        assert!(started.elapsed() < Duration::from_secs(5));

        let snapshot = pool.snapshot();
        assert_eq!(snapshot.inflight_operations, 0);
        assert_eq!(snapshot.deadline_cancellations, 1);
        assert_eq!(snapshot.cancellation_deliveries, 1);
        assert_eq!(snapshot.cancellation_failures, 0);

        pool.run_with_deadline(Duration::from_secs(2), |repository| {
            repository
                .client
                .batch_execute("SELECT 1")
                .map_err(super::super::map_postgres_error)
        })
        .unwrap();
    }

    #[test]
    fn live_shutdown_cancels_inflight_query_and_preserves_connection_pool() {
        let Some(database_url) = live_database_url() else {
            return;
        };
        let pool =
            PgPool::connect_plain(&database_url, DatabaseProfile::PostgreSql, live_policy())
                .unwrap();
        let worker_pool = pool.clone();
        let worker = thread::spawn(move || {
            worker_pool.run_with_deadline(Duration::from_secs(30), |repository| {
                repository
                    .client
                    .batch_execute("SELECT pg_sleep(10)")
                    .map_err(super::super::map_postgres_error)
            })
        });

        let wait_started = Instant::now();
        while pool.snapshot().inflight_operations == 0 {
            assert!(
                wait_started.elapsed() < Duration::from_secs(5),
                "blocking query never entered the cancellation registry"
            );
            thread::sleep(Duration::from_millis(10));
        }

        let cancel_started = Instant::now();
        assert_eq!(pool.cancel_inflight(), 1);
        let returned = worker.join().unwrap().unwrap_err();
        assert_eq!(
            returned.reason(),
            "database_operation_shutdown_cancelled"
        );
        assert!(cancel_started.elapsed() < Duration::from_secs(5));

        let snapshot = pool.snapshot();
        assert_eq!(snapshot.inflight_operations, 0);
        assert_eq!(snapshot.shutdown_cancellations, 1);
        assert_eq!(snapshot.cancellation_deliveries, 1);
        assert_eq!(snapshot.cancellation_failures, 0);

        pool.run_with_deadline(Duration::from_secs(2), |repository| {
            repository
                .client
                .batch_execute("SELECT 1")
                .map_err(super::super::map_postgres_error)
        })
        .unwrap();
    }
}
