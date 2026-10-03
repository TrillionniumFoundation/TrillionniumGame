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
