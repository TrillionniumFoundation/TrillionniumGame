//! Pure codec/ownership negatives. These are not native DB or oracle evidence.
use super::*;
use prost::Message;
use std::time::{Duration, Instant};

#[test]
fn metadata_primary_precedence_and_repetition_are_exact() {
    let mut metadata = MetadataMap::new();
    assert_eq!(authorization(&metadata, false).unwrap(), None);
    metadata.insert(
        "grpcgateway-authorization",
        "Basic ZGVmYXVsdGtleTo=".parse().unwrap(),
    );
    assert_eq!(
        authorization(&metadata, false).unwrap(),
        Some("Basic ZGVmYXVsdGtleTo=")
    );
    metadata.insert("authorization", "".parse().unwrap());
    assert_eq!(authorization(&metadata, false).unwrap(), Some(""));
    metadata.append("authorization", "Basic ignored".parse().unwrap());
    let e = authorization(&metadata, false).unwrap_err();
    assert_eq!(
        (e.code(), e.message()),
        (tonic::Code::Unauthenticated, "Server key required")
    );
    assert_eq!(
        authorization(&metadata, true).unwrap_err().message(),
        "Auth token invalid"
    );
}

#[test]
fn basic_key_errors_and_password_semantics_reuse_native_validation() {
    let key = wire::LegacyHttpServerKey::new(b"key".to_vec()).unwrap();
    let limits = LegacyHttpLimits::default();
    let missing = status(wire::require_basic_server_key(&key, None, limits).unwrap_err());
    assert_eq!(
        (missing.code(), missing.message()),
        (tonic::Code::Unauthenticated, "Server key required")
    );
    // key:any password, including an empty password, is valid upstream.
    assert!(wire::require_basic_server_key(&key, Some("Basic a2V5Og=="), limits).is_ok());
    assert!(wire::require_basic_server_key(&key, Some("Basic a2V5Ong="), limits).is_ok());
    let invalid =
        status(wire::require_basic_server_key(&key, Some("basic a2V5Og=="), limits).unwrap_err());
    assert_eq!(invalid.message(), "Server key invalid");
}

#[test]
fn protobuf_presence_field_numbers_unknown_fields_and_utf8() {
    let absent = AuthenticateDeviceRequest::decode(&[][..]).unwrap();
    assert!(absent.account.is_none() && absent.create.is_none());
    // Account tag 1 with empty message; wrapper tag 2 with empty message = false;
    // username tag 3; unknown varint tag 10 is ignored by official proto rules.
    let encoded = [0x0a, 0, 0x12, 0, 0x1a, 1, b'u', 0x50, 1];
    let present = AuthenticateDeviceRequest::decode(&encoded[..]).unwrap();
    assert_eq!(present.account.unwrap().id, "");
    assert!(!present.create.unwrap().value);
    assert_eq!(present.username, "u");
    assert!(AuthenticateDeviceRequest::decode(&[0x1a, 1, 0xff][..]).is_err());
    let custom = AuthenticateCustomRequest::decode(&encoded[..]).unwrap();
    assert!(!custom.create.unwrap().value);
    assert_eq!(
        Session {
            created: true,
            token: "t".into(),
            refresh_token: "r".into()
        }
        .encode_to_vec(),
        [8, 1, 18, 1, b't', 26, 1, b'r']
    );
}

#[test]
fn empty_protobuf_vars_retain_refresh_claims_and_limits_are_explicit() {
    let mut vars = BTreeMap::new();
    assert!(present_vars(&vars).is_none());
    vars.insert("".into(), "".into());
    assert!(present_vars(&vars).is_some());
    for i in 0..256 {
        vars.insert(i.to_string(), "x".into());
    }
    assert_eq!(
        check_vars(&vars).unwrap_err().code(),
        tonic::Code::ResourceExhausted
    );
}

#[test]
fn job_admission_is_global_bounded_and_drop_restores_exactly_one_slot() {
    let jobs = Arc::new(AtomicUsize::new(0));
    let permits: Vec<_> = (0..MAX_AUTH_JOBS)
        .map(|_| JobPermit::acquire(&jobs).unwrap())
        .collect();
    assert_eq!(jobs.load(Ordering::Acquire), MAX_AUTH_JOBS);
    assert!(JobPermit::acquire(&jobs).is_err());
    drop(permits);
    assert_eq!(jobs.load(Ordering::Acquire), 0);
}

async fn until(predicate: impl Fn() -> bool) {
    let end = Instant::now() + Duration::from_secs(3);
    while !predicate() {
        assert!(
            Instant::now() < end,
            "bounded lifecycle observation timed out"
        );
        tokio::time::sleep(Duration::from_millis(1)).await;
    }
}

#[test]
fn cancelled_started_job_retains_permit_and_does_not_cancel_other_job() {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .max_blocking_threads(2)
        .build()
        .unwrap();
    runtime.block_on(async {
        let jobs = Arc::new(AtomicUsize::new(0));
        let fence = test_fence();
        let started = Arc::new(AtomicBool::new(false));
        let signal = Arc::clone(&started);
        let (release, wait) = std::sync::mpsc::channel();
        let first = tokio::spawn(run_owned(Arc::clone(&jobs), fence.clone(), move || {
            signal.store(true, Ordering::Release);
            wait.recv_timeout(Duration::from_secs(3)).unwrap();
            Ok(())
        }));
        until(|| started.load(Ordering::Acquire)).await;
        first.abort();
        assert!(first.await.unwrap_err().is_cancelled());
        assert_eq!(
            jobs.load(Ordering::Acquire),
            1,
            "aborting RPC must not free running job's permit"
        );
        assert_eq!(
            run_owned(Arc::clone(&jobs), fence.clone(), || Ok(42))
                .await
                .unwrap(),
            42
        );
        assert_eq!(jobs.load(Ordering::Acquire), 1);
        assert!(!fence.worker_failed.load(Ordering::Acquire));
        assert!(!fence.draining.is_draining());
        release.send(()).unwrap();
        until(|| jobs.load(Ordering::Acquire) == 0).await;
    });
}

#[test]
fn cancelled_queued_job_skips_operation_and_releases_only_after_dequeue() {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .max_blocking_threads(1)
        .build()
        .unwrap();
    runtime.block_on(async {
        let jobs = Arc::new(AtomicUsize::new(0));
        let started = Arc::new(AtomicBool::new(false));
        let signal = Arc::clone(&started);
        let (release, wait) = std::sync::mpsc::channel();
        let first = tokio::spawn(run_owned(Arc::clone(&jobs), test_fence(), move || {
            signal.store(true, Ordering::Release);
            wait.recv_timeout(Duration::from_secs(3)).unwrap();
            Ok(())
        }));
        until(|| started.load(Ordering::Acquire)).await;
        let called = Arc::new(AtomicBool::new(false));
        let operation_called = Arc::clone(&called);
        let queued = tokio::spawn(run_owned(Arc::clone(&jobs), test_fence(), move || {
            operation_called.store(true, Ordering::Release);
            Ok(())
        }));
        until(|| jobs.load(Ordering::Acquire) == 2).await;
        queued.abort();
        assert!(queued.await.unwrap_err().is_cancelled());
        assert_eq!(jobs.load(Ordering::Acquire), 2);
        release.send(()).unwrap();
        first.await.unwrap().unwrap();
        until(|| jobs.load(Ordering::Acquire) == 0).await;
        assert!(!called.load(Ordering::Acquire));
    });
}

#[test]
fn drain_prevents_new_native_effect() {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    let jobs = Arc::new(AtomicUsize::new(0));
    let drain = SharedDrain::default();
    drain.begin();
    let result = runtime.block_on(run_owned(
        Arc::clone(&jobs),
        FailureFence {
            draining: drain,
            worker_failed: Arc::new(AtomicBool::new(false)),
        },
        || -> Result<(), Status> { panic!("drained operation started") },
    ));
    assert_eq!(result.unwrap_err().code(), tonic::Code::Unavailable);
    assert_eq!(jobs.load(Ordering::Acquire), 0);
}

fn test_fence() -> FailureFence {
    FailureFence {
        draining: SharedDrain::default(),
        worker_failed: Arc::new(AtomicBool::new(false)),
    }
}

#[test]
fn preparation_panic_sets_shared_process_failure_and_drain() {
    let fence = test_fence();
    let result = fence.protect(|| -> Result<(), Status> { panic!("preparation panic") });
    assert_eq!(result.unwrap_err().code(), tonic::Code::Internal);
    assert!(fence.worker_failed.load(Ordering::Acquire));
    assert!(fence.draining.is_draining());
}

#[test]
fn abandoned_native_panic_fences_process_and_skips_queued_effect() {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .max_blocking_threads(1)
        .build()
        .unwrap();
    runtime.block_on(async {
        let jobs = Arc::new(AtomicUsize::new(0));
        let fence = test_fence();
        let started = Arc::new(AtomicBool::new(false));
        let signal = Arc::clone(&started);
        let (release, wait) = std::sync::mpsc::channel();
        let first = tokio::spawn(run_owned(
            Arc::clone(&jobs),
            fence.clone(),
            move || -> Result<(), Status> {
                signal.store(true, Ordering::Release);
                wait.recv_timeout(Duration::from_secs(3)).unwrap();
                panic!("native panic after RPC abandonment")
            },
        ));
        until(|| started.load(Ordering::Acquire)).await;
        let called = Arc::new(AtomicBool::new(false));
        let queued_called = Arc::clone(&called);
        let queued = tokio::spawn(run_owned(Arc::clone(&jobs), fence.clone(), move || {
            queued_called.store(true, Ordering::Release);
            Ok(())
        }));
        until(|| jobs.load(Ordering::Acquire) == 2).await;
        first.abort();
        assert!(first.await.unwrap_err().is_cancelled());
        assert_eq!(jobs.load(Ordering::Acquire), 2);
        release.send(()).unwrap();
        let rejection = queued.await.unwrap().unwrap_err();
        assert_eq!(rejection.code(), tonic::Code::Unavailable);
        assert!(fence.worker_failed.load(Ordering::Acquire));
        assert!(fence.draining.is_draining());
        assert!(!called.load(Ordering::Acquire));
        until(|| jobs.load(Ordering::Acquire) == 0).await;
    });
}

#[test]
fn panic_cleanup_runs_before_native_permit_release() {
    struct ObserveCleanup {
        jobs: Arc<AtomicUsize>,
        observed: Arc<AtomicBool>,
    }
    impl Drop for ObserveCleanup {
        fn drop(&mut self) {
            self.observed
                .store(self.jobs.load(Ordering::Acquire) == 1, Ordering::Release);
        }
    }
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    let jobs = Arc::new(AtomicUsize::new(0));
    let observed = Arc::new(AtomicBool::new(false));
    let cleanup = ObserveCleanup {
        jobs: Arc::clone(&jobs),
        observed: Arc::clone(&observed),
    };
    let fence = test_fence();
    let result = runtime.block_on(run_owned(
        Arc::clone(&jobs),
        fence.clone(),
        move || -> Result<(), Status> {
            let _cleanup = cleanup;
            panic!("panic while owning native resources")
        },
    ));
    assert_eq!(result.unwrap_err().code(), tonic::Code::Internal);
    assert!(observed.load(Ordering::Acquire));
    assert_eq!(jobs.load(Ordering::Acquire), 0);
    assert!(fence.worker_failed.load(Ordering::Acquire));
}

#[test]
fn dropping_dedicated_runtime_joins_started_native_job() {
    struct ShutdownWitness(std::sync::mpsc::Sender<()>);
    impl Drop for ShutdownWitness {
        fn drop(&mut self) {
            let _ = self.0.send(());
        }
    }
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    let jobs = Arc::new(AtomicUsize::new(0));
    let started = Arc::new(AtomicBool::new(false));
    let completed = Arc::new(AtomicBool::new(false));
    let (release, wait) = std::sync::mpsc::channel();
    let (shutdown_entered, entered) = std::sync::mpsc::channel();
    let (drop_finished, finished) = std::sync::mpsc::channel();
    runtime.block_on(async {
        let witness = ShutdownWitness(shutdown_entered);
        tokio::spawn(async move {
            let _witness = witness;
            std::future::pending::<()>().await;
        });
        let signal = Arc::clone(&started);
        let finished = Arc::clone(&completed);
        tokio::spawn(run_owned(Arc::clone(&jobs), test_fence(), move || {
            signal.store(true, Ordering::Release);
            wait.recv_timeout(Duration::from_secs(3)).unwrap();
            finished.store(true, Ordering::Release);
            Ok(())
        }));
        until(|| started.load(Ordering::Acquire)).await;
    });
    let dropper = std::thread::spawn(move || {
        drop(runtime);
        drop_finished.send(()).unwrap();
    });
    // Witness destruction proves Runtime::drop has entered scheduler shutdown;
    // the native operation is still blocked because its release is withheld.
    entered.recv_timeout(Duration::from_secs(1)).unwrap();
    assert!(matches!(
        finished.recv_timeout(Duration::from_millis(50)),
        Err(std::sync::mpsc::RecvTimeoutError::Timeout)
    ));
    assert!(!completed.load(Ordering::Acquire));
    assert_eq!(jobs.load(Ordering::Acquire), 1);
    release.send(()).unwrap();
    finished.recv_timeout(Duration::from_secs(1)).unwrap();
    dropper.join().unwrap();
    assert!(completed.load(Ordering::Acquire));
    assert_eq!(jobs.load(Ordering::Acquire), 0);
}
