use super::*;
use trnm_contracts::{DomainError, RetryClass, StableCode};
use trnm_persistence_pg::{AccountFailure, AccountPhase};
fn failure() -> AccountFailure {
    AccountFailure {
        phase: AccountPhase::Commit,
        last_failure: AccountSqlFailure {
            sqlstate: Some(*b"08006"),
            username_collision: false,
            transaction_closed: false,
            reason: "bounded-failure",
        },
        cleanup_failure: None,
        attempts: 1,
        exhausted: false,
        unknown_commit: true,
    }
}

#[test]
fn retry_wrapper_observes_native_failure_once_without_retry_or_reinterpretation() {
    use std::cell::Cell;
    use std::rc::Rc;

    struct Observation {
        calls: Rc<Cell<usize>>,
        outcome: Option<NakamaAccountError>,
    }
    impl LegacyNativeFailureObservation for Observation {
        fn last_legacy_native_failure(&self) -> Option<NakamaAccountError> {
            self.calls.set(self.calls.get() + 1);
            self.outcome
        }
    }

    for outcome in [None, Some(NakamaAccountError::Internal(failure()))] {
        let calls = Rc::new(Cell::new(0));
        let repository = super::super::retry::RetryingRepository::new(
            Observation {
                calls: Rc::clone(&calls),
                outcome,
            },
            super::super::retry::RetryPolicy::candidate_default(),
        )
        .unwrap();
        let observer: &dyn LegacyNativeFailureObservation = &repository;
        assert_eq!(observer.last_legacy_native_failure(), outcome);
        assert_eq!(calls.get(), 1);
    }
}

#[test]
fn native_repository_errors_preserve_unavailable_5_7_6_and_internal_13() {
    for (native, expected) in [
        (
            NakamaAccountError::SchemaNotReady(DomainError::new(
                StableCode::FailedPrecondition,
                "closed",
                RetryClass::Never,
            )),
            StableCode::Unavailable,
        ),
        (NakamaAccountError::NotFound, StableCode::NotFound),
        (NakamaAccountError::Banned, StableCode::PermissionDenied),
        (
            NakamaAccountError::UsernameAlreadyExists(failure()),
            StableCode::AlreadyExists,
        ),
        (
            NakamaAccountError::Internal(failure()),
            StableCode::Internal,
        ),
    ] {
        assert_eq!(map_native_error(native).code(), expected);
    }
    assert_ne!(
        map_native_error(NakamaAccountError::Internal(failure())),
        LegacyRepositoryError::DataLoss
    );
}
#[test]
fn stored_record_mapping_keeps_nil_negative_disable_and_multibyte_history() {
    let uid = UserId::new([0; 16]);
    let history = "界".repeat(128);
    let mapped = map_stored_user(NakamaLegacyUser {
        id: uid,
        stored_username: history.clone(),
        disable_unix_seconds: Some(-1),
    });
    assert!(mapped.user_id.is_zero());
    assert_eq!(mapped.username, history);
    assert_eq!(mapped.disable_time_unix_seconds, Some(-1));
    assert!(!format!("{mapped:?}").contains(&history));
}
#[test]
fn committed_cleanup_diagnostic_survives_mapping_with_no_business_failure() {
    let diagnostic = AccountSqlFailure {
        sqlstate: Some(*b"08006"),
        username_collision: false,
        transaction_closed: true,
        reason: "post-release-cleanup",
    };
    let mapped = map_device_outcome(AuthenticateDeviceOutcome {
        user: NakamaLegacyUser {
            id: UserId::new([1; 16]),
            stored_username: "stored".into(),
            disable_unix_seconds: Some(0),
        },
        created: true,
        committed_cleanup_failure: Some(diagnostic),
    });
    assert!(mapped.created);
    let retained = mapped.committed_cleanup_failure.unwrap();
    assert_eq!(retained.sqlstate, diagnostic.sqlstate);
    assert_eq!(retained.transaction_closed, diagnostic.transaction_closed);
    assert_eq!(retained.username_collision, diagnostic.username_collision);
    assert_eq!(retained.reason, diagnostic.reason);
}

fn deadline_lease<T>(observed: Option<Result<T, NakamaAccountError>>) -> PgAccountLeaseOutcome<T> {
    PgAccountLeaseOutcome {
        observed,
        boundary_error: Some(DomainError::new(
            StableCode::Unavailable,
            "database_operation_deadline_exceeded",
            RetryClass::SafeBackoff,
        )),
        setup_error: None,
        cancellation: PgAccountLeaseCancellation::Deadline,
        lease_retired: true,
    }
}
#[test]
fn pooled_late_creation_preserves_confirmed_commit_and_cleanup_without_success() {
    let cleanup = AccountSqlFailure {
        sqlstate: Some(*b"08006"),
        username_collision: false,
        transaction_closed: true,
        reason: "private-cleanup-sentinel",
    };
    let result = resolve_device_lease(deadline_lease(Some(Ok(AuthenticateDeviceOutcome {
        user: NakamaLegacyUser {
            id: UserId::new([9; 16]),
            stored_username: "private-username".into(),
            disable_unix_seconds: Some(0),
        },
        created: true,
        committed_cleanup_failure: Some(cleanup),
    }))));
    let error = result.unwrap_err();
    let LegacyRepositoryError::Lease(ref failure) = error else {
        panic!("must retain late boundary")
    };
    let LegacyLeaseCompletion::ConfirmedCreation {
        committed_cleanup_failure,
    } = failure.completion
    else {
        panic!("must retain commit")
    };
    assert_eq!(committed_cleanup_failure, Some(map_cleanup(cleanup)));
    assert!(failure.lease_retired);
    let debug = format!("{error:?}");
    assert!(!debug.contains("private-username") && !debug.contains("private-cleanup-sentinel"));
}
#[test]
fn pooled_existing_read_unknown_commit_and_unobserved_are_distinct() {
    let existing = resolve_device_lease(deadline_lease(Some(Ok(AuthenticateDeviceOutcome {
        user: NakamaLegacyUser {
            id: UserId::new([9; 16]),
            stored_username: "existing".into(),
            disable_unix_seconds: Some(0),
        },
        created: false,
        committed_cleanup_failure: None,
    }))))
    .unwrap_err();
    let unknown = resolve_device_lease(deadline_lease(Some(Err(NakamaAccountError::Internal(
        failure(),
    )))))
    .unwrap_err();
    let unobserved = resolve_device_lease(deadline_lease(None)).unwrap_err();
    assert!(matches!(
        existing,
        LegacyRepositoryError::Lease(ref record)
            if matches!(record.completion, LegacyLeaseCompletion::DeviceExisting)
    ));
    assert!(matches!(
        unknown,
        LegacyRepositoryError::Lease(ref record)
            if matches!(record.completion, LegacyLeaseCompletion::NativeFailure(LegacyNativeAccountFailure { unknown_commit:Some(true), .. }))
    ));
    assert!(matches!(
        unobserved,
        LegacyRepositoryError::Lease(ref record)
            if matches!(record.completion, LegacyLeaseCompletion::NotObserved)
    ));
}
#[test]
fn pooled_timely_unknown_commit_keeps_typed_native_failure() {
    let native = NakamaAccountError::Internal(failure());
    let result = resolve_device_lease(PgAccountLeaseOutcome {
        observed: Some(Err(native)),
        boundary_error: None,
        setup_error: None,
        cancellation: PgAccountLeaseCancellation::None,
        lease_retired: true,
    })
    .unwrap_err();
    let LegacyRepositoryError::NativeFailure(record) = result else {
        panic!("must retain native failure")
    };
    assert_eq!(record.unknown_commit, Some(true));
    assert_eq!(record.attempts, Some(1));
    assert_eq!(record.phase, Some("commit"));
    assert_eq!(record.last_failure.unwrap().sqlstate, Some(*b"08006"));
    assert_eq!(result.code(), StableCode::Internal);
}
#[test]
fn pooled_missing_operation_result_is_failclosed_and_not_a_success() {
    let error = resolve_user_lease(PgAccountLeaseOutcome {
        observed: None,
        boundary_error: None,
        setup_error: None,
        cancellation: PgAccountLeaseCancellation::None,
        lease_retired: false,
    })
    .unwrap_err();
    assert_eq!(error.code(), StableCode::DataLoss);
    assert!(matches!(
        error,
        LegacyRepositoryError::Lease(ref record)
            if matches!(record.completion, LegacyLeaseCompletion::NotObserved)
    ));
}
#[test]
fn pooled_timely_user_projection_preserves_historical_username() {
    let historical = "界".repeat(128);
    let result = resolve_user_lease(PgAccountLeaseOutcome {
        observed: Some(Ok(Some(NakamaLegacyUser {
            id: UserId::new([0; 16]),
            stored_username: historical.clone(),
            disable_unix_seconds: Some(-1),
        }))),
        boundary_error: None,
        setup_error: None,
        cancellation: PgAccountLeaseCancellation::None,
        lease_retired: false,
    })
    .unwrap()
    .unwrap();
    assert_eq!(result.username, historical);
    assert_eq!(result.disable_time_unix_seconds, Some(-1));
    assert!(result.user_id.is_zero());
}

#[test]
fn pooled_late_false_creation_retains_unconfirmed_cleanup() {
    let diagnostic = AccountSqlFailure {
        sqlstate: Some(*b"08006"),
        username_collision: false,
        transaction_closed: true,
        reason: "bounded-contradictory-cleanup",
    };
    let error = resolve_device_lease(deadline_lease(Some(Ok(AuthenticateDeviceOutcome {
        user: NakamaLegacyUser {
            id: UserId::new([2; 16]),
            stored_username: "existing".into(),
            disable_unix_seconds: Some(0),
        },
        created: false,
        committed_cleanup_failure: Some(diagnostic),
    }))))
    .unwrap_err();
    assert!(
        matches!(error,LegacyRepositoryError::Lease(ref record) if matches!(record.completion,LegacyLeaseCompletion::UnconfirmedCleanup { committed_cleanup_failure } if committed_cleanup_failure==map_cleanup(diagnostic)))
    );
}

#[test]
fn custom_one_lease_keeps_late_confirmed_autocommit_and_lookup_failure_facts() {
    let user = NakamaLegacyUser {
        id: UserId::new([7; 16]),
        stored_username: "stored".to_owned(),
        disable_unix_seconds: Some(0),
    };
    let error = resolve_custom_lease(deadline_lease(Some(Ok(AuthenticateCustomOutcome {
        user,
        created: true,
    }))))
    .unwrap_err();
    let LegacyRepositoryError::Lease(detail) = error else {
        panic!("lease facts");
    };
    assert!(matches!(
        detail.completion,
        LegacyLeaseCompletion::ConfirmedCreation {
            committed_cleanup_failure: None
        }
    ));
    assert_eq!(detail.cancellation, LegacyLeaseCancellation::Deadline);
    let error = map_native_error(NakamaAccountError::CustomLookupFailed(failure()));
    assert!(matches!(
        error,
        LegacyRepositoryError::CustomLookupFailed(_)
    ));
    assert_eq!(error.code(), StableCode::Internal);
}
