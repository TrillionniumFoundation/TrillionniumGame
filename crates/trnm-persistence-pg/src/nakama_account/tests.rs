use super::*;
use std::cell::RefCell;
use std::rc::Rc;
use trnm_contracts::RetryClass;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Step {
    Guard,
    Device,
    User,
    Begin,
    InsertUser,
    InsertDevice,
    Savepoint,
    Release,
    Restart,
    Commit,
    Rollback,
}
#[derive(Clone, Copy)]
struct Fault {
    step: Step,
    occurrence: usize,
    failure: AccountSqlFailure,
}
#[derive(Default)]
struct State {
    steps: Vec<Step>,
    faults: Vec<Fault>,
    inserted_ids: Vec<UserId>,
    durable_users: usize,
    durable_devices: usize,
    zero_user_rows: bool,
    zero_device_rows: bool,
}
impl State {
    fn step(&mut self, step: Step) -> Result<(), AccountSqlFailure> {
        self.steps.push(step);
        let occurrence = self.count(step);
        self.faults
            .iter()
            .find(|f| f.step == step && f.occurrence == occurrence)
            .map_or(Ok(()), |f| Err(f.failure))
    }
    fn count(&self, step: Step) -> usize {
        self.steps.iter().filter(|s| **s == step).count()
    }
}
struct Database {
    state: Rc<RefCell<State>>,
    guard: Option<DomainError>,
    existing: Option<UserId>,
    stored: Option<NakamaLegacyUser>,
}
impl Database {
    fn missing() -> Self {
        Self {
            state: Rc::default(),
            guard: None,
            existing: None,
            stored: None,
        }
    }
    fn fault(&self, step: Step, occurrence: usize, failure: AccountSqlFailure) {
        self.state.borrow_mut().faults.push(Fault {
            step,
            occurrence,
            failure,
        });
    }
}
impl DeviceDatabase for Database {
    type Transaction<'a> = Tx;
    fn require_ready(&mut self) -> Result<(), DomainError> {
        self.state.borrow_mut().step(Step::Guard).unwrap();
        self.guard.map_or(Ok(()), Err)
    }
    fn find_device(&mut self, _: &str) -> Result<Option<UserId>, AccountSqlFailure> {
        self.state.borrow_mut().step(Step::Device)?;
        Ok(self.existing)
    }
    fn read_user(&mut self, _: UserId) -> Result<Option<NakamaLegacyUser>, AccountSqlFailure> {
        self.state.borrow_mut().step(Step::User)?;
        Ok(self.stored.clone())
    }
    fn begin(&mut self) -> Result<Self::Transaction<'_>, AccountSqlFailure> {
        self.state.borrow_mut().step(Step::Begin)?;
        Ok(Tx {
            state: Rc::clone(&self.state),
            staged_users: 0,
            staged_devices: 0,
            closed: false,
            released: false,
        })
    }
}
struct Tx {
    state: Rc<RefCell<State>>,
    staged_users: usize,
    staged_devices: usize,
    closed: bool,
    released: bool,
}
impl Tx {
    fn publish(&mut self) {
        let mut state = self.state.borrow_mut();
        state.durable_users += self.staged_users;
        state.durable_devices += self.staged_devices;
        self.staged_users = 0;
        self.staged_devices = 0;
    }
}
impl DeviceTransaction for Tx {
    fn insert_user(
        &mut self,
        _: AuthenticateDevice<'_>,
        id: UserId,
    ) -> Result<u64, AccountSqlFailure> {
        let mut state = self.state.borrow_mut();
        state.inserted_ids.push(id);
        state.step(Step::InsertUser)?;
        if state.zero_user_rows {
            return Ok(0);
        }
        self.staged_users += 1;
        Ok(1)
    }
    fn insert_device(&mut self, _: &str, _: UserId) -> Result<u64, AccountSqlFailure> {
        let mut state = self.state.borrow_mut();
        state.step(Step::InsertDevice)?;
        if state.zero_device_rows {
            return Ok(0);
        }
        self.staged_devices += 1;
        Ok(1)
    }
    fn savepoint(&mut self) -> Result<(), AccountSqlFailure> {
        self.state.borrow_mut().step(Step::Savepoint)
    }
    fn release(&mut self) -> Result<(), AccountSqlFailure> {
        self.state.borrow_mut().step(Step::Release)?;
        self.publish();
        self.released = true;
        Ok(())
    }
    fn restart(&mut self) -> Result<(), AccountSqlFailure> {
        self.state.borrow_mut().step(Step::Restart)?;
        self.staged_users = 0;
        self.staged_devices = 0;
        Ok(())
    }
    fn commit(&mut self) -> Result<(), AccountSqlFailure> {
        self.closed = true;
        self.state.borrow_mut().step(Step::Commit)?;
        if !self.released {
            self.publish();
        }
        Ok(())
    }
    fn rollback(&mut self) -> Result<(), AccountSqlFailure> {
        self.state.borrow_mut().step(Step::Rollback)?;
        if self.closed {
            return Err(AccountSqlFailure::closed());
        }
        self.closed = true;
        self.staged_users = 0;
        self.staged_devices = 0;
        Ok(())
    }
}
fn id() -> UserId {
    let mut bytes = [0x31; 16];
    bytes[6] = 0x41;
    bytes[8] = 0x81;
    UserId::new(bytes)
}
fn request(create: bool) -> AuthenticateDevice<'static> {
    AuthenticateDevice::new("device12345", "new_username", create).unwrap()
}
fn failure(code: &[u8; 5]) -> AccountSqlFailure {
    AccountSqlFailure {
        sqlstate: Some(*code),
        ..AccountSqlFailure::local("synthetic_sql_failure")
    }
}
fn authenticate(
    db: &mut Database,
    profile: DatabaseProfile,
) -> Result<AuthenticateDeviceOutcome, NakamaAccountError> {
    authenticate_device(db, profile, request(true), Some(id()))
}
fn detailed(error: NakamaAccountError) -> AccountFailure {
    match error {
        NakamaAccountError::Internal(value) | NakamaAccountError::UsernameAlreadyExists(value) => {
            value
        }
        _ => panic!("expected detailed error"),
    }
}

#[test]
fn closed_accounts_guard_runs_before_any_business_or_begin() {
    let mut db = Database::missing();
    let gate = DomainError::new(
        StableCode::FailedPrecondition,
        "schema5_native_catalog_capture_pending",
        RetryClass::Never,
    );
    db.guard = Some(gate);
    assert_eq!(
        authenticate(&mut db, DatabaseProfile::PostgreSql).unwrap_err(),
        NakamaAccountError::SchemaNotReady(gate)
    );
    assert_eq!(
        read_user(&mut db, UserId::new([0; 16]))
            .unwrap_err()
            .reason(),
        gate.reason()
    );
    assert_eq!(db.state.borrow().steps, [Step::Guard, Step::Guard]);
}

#[test]
fn existing_account_uses_two_reads_and_stored_unicode_username_without_writes() {
    for disabled in [None, Some(0)] {
        let mut db = Database::missing();
        let system_id = UserId::new([0; 16]);
        db.existing = Some(system_id);
        db.stored = Some(checked_stored_user(system_id, "界".repeat(128), disabled).unwrap());
        let result =
            authenticate_device(&mut db, DatabaseProfile::PostgreSql, request(true), None).unwrap();
        assert!(!result.created);
        assert_eq!(result.user.id, system_id);
        assert_eq!(result.user.stored_username.len(), 384);
        assert_eq!(
            db.state.borrow().steps,
            [Step::Guard, Step::Device, Step::User]
        );
    }
}

#[test]
fn disable_seconds_nonzero_including_negative_are_banned() {
    for seconds in [-1, 1, i64::MIN, i64::MAX] {
        let mut db = Database::missing();
        db.existing = Some(id());
        db.stored = Some(checked_stored_user(id(), "stored".into(), Some(seconds)).unwrap());
        assert_eq!(
            authenticate(&mut db, DatabaseProfile::CockroachDb)
                .unwrap_err()
                .code(),
            StableCode::PermissionDenied
        );
        assert_eq!(db.state.borrow().count(Step::Begin), 0);
    }
}

#[test]
fn missing_create_false_and_missing_linked_user_keep_distinct_errors() {
    let mut db = Database::missing();
    assert_eq!(
        authenticate_device(&mut db, DatabaseProfile::PostgreSql, request(false), None)
            .unwrap_err(),
        NakamaAccountError::NotFound
    );
    db.existing = Some(id());
    assert_eq!(
        authenticate(&mut db, DatabaseProfile::PostgreSql)
            .unwrap_err()
            .code(),
        StableCode::Internal
    );
    assert_eq!(db.state.borrow().count(Step::Begin), 0);
    assert_eq!(read_user(&mut Database::missing(), id()).unwrap(), None);
}

#[test]
fn lookup_failures_are_not_retried() {
    for step in [Step::Device, Step::User] {
        let mut db = Database::missing();
        if step == Step::User {
            db.existing = Some(id());
        }
        db.fault(step, 1, failure(b"40001"));
        assert_eq!(
            authenticate(&mut db, DatabaseProfile::PostgreSql)
                .unwrap_err()
                .code(),
            StableCode::Internal
        );
        assert_eq!(db.state.borrow().count(step), 1);
        assert_eq!(db.state.borrow().count(Step::Begin), 0);
    }
}

#[test]
fn creation_uuid_requires_trusted_v4_variant_only_when_missing_create() {
    let mut version = *id().as_bytes();
    version[6] = 0x51;
    let mut variant = *id().as_bytes();
    variant[8] = 0xC1;
    for invalid in [
        None,
        Some(UserId::new([0; 16])),
        Some(UserId::new(version)),
        Some(UserId::new(variant)),
    ] {
        let mut db = Database::missing();
        assert_eq!(
            authenticate_device(&mut db, DatabaseProfile::PostgreSql, request(true), invalid)
                .unwrap_err()
                .code(),
            StableCode::Internal
        );
        assert_eq!(db.state.borrow().count(Step::Begin), 0);
    }
}

#[test]
fn creation_publishes_only_after_both_inserts_and_commit() {
    for profile in [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb] {
        let mut db = Database::missing();
        let result = authenticate(&mut db, profile).unwrap();
        assert!(result.created);
        assert_eq!(result.user.id, id());
        assert_eq!(result.user.disable_unix_seconds, Some(0));
        let s = db.state.borrow();
        assert_eq!((s.durable_users, s.durable_devices), (1, 1));
        assert_eq!(s.count(Step::Commit), 1);
        assert_eq!(s.count(Step::Rollback), 0);
        assert!(
            s.steps.iter().position(|s| *s == Step::InsertUser).unwrap()
                < s.steps
                    .iter()
                    .position(|s| *s == Step::InsertDevice)
                    .unwrap()
        );
        assert!(
            s.steps
                .iter()
                .position(|s| *s == Step::InsertDevice)
                .unwrap()
                < s.steps.iter().position(|s| *s == Step::Commit).unwrap()
        );
    }
}

#[test]
fn zero_affected_rows_rollback_and_never_ack_partial_creation() {
    for user_zero in [true, false] {
        let mut db = Database::missing();
        db.state.borrow_mut().zero_user_rows = user_zero;
        db.state.borrow_mut().zero_device_rows = !user_zero;
        assert_eq!(
            authenticate(&mut db, DatabaseProfile::PostgreSql)
                .unwrap_err()
                .code(),
            StableCode::Internal
        );
        let s = db.state.borrow();
        assert_eq!((s.durable_users, s.durable_devices), (0, 0));
        assert_eq!(s.count(Step::Commit), 0);
        assert_eq!(s.count(Step::Rollback), 1);
    }
}

#[test]
fn username_collision_only_maps_insert_users_to_already_exists() {
    for step in [Step::InsertUser, Step::InsertDevice, Step::Commit] {
        let mut db = Database::missing();
        let mut collision = failure(b"23505");
        collision.username_collision = true;
        db.fault(step, 1, collision);
        let error = authenticate(&mut db, DatabaseProfile::PostgreSql).unwrap_err();
        assert_eq!(
            error.code(),
            if step == Step::InsertUser {
                StableCode::AlreadyExists
            } else {
                StableCode::Internal
            }
        );
        assert_eq!(db.state.borrow().count(Step::Begin), 1);
    }
}

#[test]
fn plain_device_unique_race_is_internal_rollback_without_convergence() {
    let mut db = Database::missing();
    db.fault(Step::InsertDevice, 1, failure(b"23505"));
    let error = authenticate(&mut db, DatabaseProfile::PostgreSql).unwrap_err();
    assert_eq!(error.code(), StableCode::Internal);
    let s = db.state.borrow();
    assert_eq!((s.durable_users, s.durable_devices), (0, 0));
    assert_eq!(s.count(Step::Device), 1);
    assert_eq!(s.count(Step::Begin), 1);
    assert_eq!(s.count(Step::Rollback), 1);
}

#[test]
fn postgres_retries_entire_default_transaction_with_same_uuid() {
    for step in [Step::InsertUser, Step::InsertDevice, Step::Commit] {
        let mut db = Database::missing();
        db.fault(step, 1, failure(b"40P01"));
        assert!(
            authenticate(&mut db, DatabaseProfile::PostgreSql)
                .unwrap()
                .created
        );
        let s = db.state.borrow();
        assert_eq!(s.count(Step::Begin), 2);
        assert_eq!(s.inserted_ids, [id(), id()]);
        assert_eq!((s.durable_users, s.durable_devices), (1, 1));
    }
}

#[test]
fn postgres_five_failures_retain_original_error_despite_successful_rollbacks() {
    let mut db = Database::missing();
    for n in 1..=5 {
        db.fault(Step::InsertUser, n, failure(b"40001"));
    }
    let error = detailed(authenticate(&mut db, DatabaseProfile::PostgreSql).unwrap_err());
    assert!(error.exhausted);
    assert_eq!(error.attempts, 5);
    assert_eq!(error.last_failure.sqlstate, Some(*b"40001"));
    assert_eq!(error.cleanup_failure, None);
    let s = db.state.borrow();
    assert_eq!(s.count(Step::Begin), 5);
    assert_eq!(s.count(Step::Rollback), 5);
    assert_eq!(s.count(Step::Commit), 0);
    assert_eq!((s.durable_users, s.durable_devices), (0, 0));
}

#[test]
fn postgres_commit_exhaustion_retains_sql_failure_instead_of_tx_done() {
    let mut db = Database::missing();
    for n in 1..=5 {
        db.fault(Step::Commit, n, failure(b"40001"));
    }
    let error = detailed(authenticate(&mut db, DatabaseProfile::PostgreSql).unwrap_err());
    assert!(error.exhausted);
    assert_eq!(error.last_failure.sqlstate, Some(*b"40001"));
    assert_eq!(error.cleanup_failure, None);
    assert_eq!(db.state.borrow().count(Step::Begin), 5);
    assert_eq!(db.state.borrow().count(Step::Rollback), 5);
}

#[test]
fn postgres_statement_class40_retries_after_known_rollback_and_request_debug_redacts() {
    let mut db = Database::missing();
    db.fault(Step::InsertUser, 1, failure(b"40003"));
    assert!(
        authenticate(&mut db, DatabaseProfile::PostgreSql)
            .unwrap()
            .created
    );
    assert_eq!(db.state.borrow().count(Step::Begin), 2);
    let debug = format!("{:?}", request(true));
    assert!(!debug.contains("device12345"));
    assert!(!debug.contains("new_username"));
}

#[test]
fn postgres_begin_and_rollback_failure_end_retry_and_retain_both_causes() {
    let mut db = Database::missing();
    db.fault(Step::Begin, 1, failure(b"40001"));
    assert!(authenticate(&mut db, DatabaseProfile::PostgreSql).is_err());
    assert_eq!(db.state.borrow().count(Step::Begin), 1);
    let mut db = Database::missing();
    db.fault(Step::InsertUser, 1, failure(b"40001"));
    db.fault(Step::Rollback, 1, failure(b"08006"));
    let error = detailed(authenticate(&mut db, DatabaseProfile::PostgreSql).unwrap_err());
    assert_eq!(error.last_failure.sqlstate, Some(*b"40001"));
    assert_eq!(error.cleanup_failure.unwrap().sqlstate, Some(*b"08006"));
    assert_eq!(db.state.borrow().count(Step::Begin), 1);
}

#[test]
fn postgres_unknown_completion_is_never_retried_or_acknowledged() {
    for failure in [
        failure(b"40003"),
        AccountSqlFailure::local("transport_lost"),
    ] {
        let mut db = Database::missing();
        db.fault(Step::Commit, 1, failure);
        let error = detailed(authenticate(&mut db, DatabaseProfile::PostgreSql).unwrap_err());
        assert!(error.unknown_commit);
        assert_eq!(db.state.borrow().count(Step::Begin), 1);
    }
}

#[test]
fn cockroach_uses_one_begin_and_retry_savepoint_including_release_errors() {
    for step in [Step::InsertUser, Step::InsertDevice, Step::Release] {
        for code in [b"40001", b"CR000"] {
            let mut db = Database::missing();
            db.fault(step, 1, failure(code));
            assert!(
                authenticate(&mut db, DatabaseProfile::CockroachDb)
                    .unwrap()
                    .created
            );
            let s = db.state.borrow();
            assert_eq!(s.count(Step::Begin), 1);
            assert_eq!(s.count(Step::Savepoint), 1);
            assert_eq!(s.count(Step::Restart), 1);
            assert_eq!(s.inserted_ids, [id(), id()]);
            assert_eq!((s.durable_users, s.durable_devices), (1, 1));
        }
    }
}

#[test]
fn cockroach_exact_retry_codes_cap_and_restart_failure_preserve_failure() {
    let mut db = Database::missing();
    db.fault(Step::InsertUser, 1, failure(b"40P01"));
    assert!(authenticate(&mut db, DatabaseProfile::CockroachDb).is_err());
    assert_eq!(db.state.borrow().count(Step::Restart), 0);
    let mut db = Database::missing();
    for n in 1..=5 {
        db.fault(Step::Release, n, failure(b"40001"));
    }
    let error = detailed(authenticate(&mut db, DatabaseProfile::CockroachDb).unwrap_err());
    assert!(error.exhausted);
    assert_eq!(error.attempts, 5);
    assert_eq!(error.last_failure.sqlstate, Some(*b"40001"));
    let s = db.state.borrow();
    assert_eq!(s.count(Step::Begin), 1);
    assert_eq!(s.count(Step::Restart), 5);
    assert_eq!(s.count(Step::Rollback), 1);
    assert_eq!((s.durable_users, s.durable_devices), (0, 0));
    drop(s);
    let mut db = Database::missing();
    db.fault(Step::InsertDevice, 1, failure(b"CR000"));
    db.fault(Step::Restart, 1, failure(b"08006"));
    let error = detailed(authenticate(&mut db, DatabaseProfile::CockroachDb).unwrap_err());
    assert_eq!(error.last_failure.sqlstate, Some(*b"CR000"));
    assert_eq!(error.cleanup_failure.unwrap().sqlstate, Some(*b"08006"));
    assert_eq!(db.state.borrow().count(Step::Rollback), 1);
}

#[test]
fn cockroach_savepoint_and_ambiguous_release_fail_without_ack() {
    for step in [Step::Savepoint, Step::Release] {
        let mut db = Database::missing();
        db.fault(step, 1, AccountSqlFailure::local("transport_lost"));
        let error = detailed(authenticate(&mut db, DatabaseProfile::CockroachDb).unwrap_err());
        assert_eq!(error.unknown_commit, step == Step::Release);
        assert_eq!(db.state.borrow().count(Step::Begin), 1);
        assert_eq!(db.state.borrow().count(Step::Restart), 0);
        assert_eq!(db.state.borrow().count(Step::Rollback), 1);
    }
}

#[test]
fn cockroach_release_success_is_committed_despite_cleanup_error_without_replay() {
    let mut db = Database::missing();
    let cleanup = AccountSqlFailure::local("final_commit_transport_lost");
    db.fault(Step::Commit, 1, cleanup);
    let result = authenticate(&mut db, DatabaseProfile::CockroachDb).unwrap();
    assert!(result.created);
    assert_eq!(result.committed_cleanup_failure, Some(cleanup));
    let s = db.state.borrow();
    assert_eq!(s.count(Step::Begin), 1);
    assert_eq!(s.count(Step::InsertUser), 1);
    assert_eq!(s.count(Step::InsertDevice), 1);
    assert_eq!(s.count(Step::Release), 1);
    assert_eq!(s.count(Step::Restart), 0);
    assert_eq!((s.durable_users, s.durable_devices), (1, 1));
}

#[test]
fn native_uuid_decoder_is_exact_canonical_and_not_a_second_http_parser() {
    for user in [id(), UserId::new([0; 16]), UserId::new([255; 16])] {
        assert_eq!(decode_database_uuid(&database_uuid(user)).unwrap(), user);
    }
    for invalid in [
        "31313131313141318131313131313131",
        "{31313131-3131-4131-8131-313131313131}",
        "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF",
    ] {
        assert!(decode_database_uuid(invalid).is_err());
    }
    assert!(checked_stored_user(id(), "😀".repeat(128), Some(0)).is_ok());
    assert!(checked_stored_user(id(), "x".repeat(129), Some(0)).is_err());
    assert!(AuthenticateDevice::new(&"x".repeat(129), "name", true).is_err());
}

#[test]
fn deferred_uuid_skips_closed_existing_banned_and_missing_no_create_paths() {
    let mut closed = Database::missing();
    closed.guard = Some(DomainError::new(
        StableCode::FailedPrecondition,
        "closed",
        RetryClass::Never,
    ));
    assert!(matches!(
        authenticate_device_with_id_source(
            &mut closed,
            DatabaseProfile::PostgreSql,
            request(true),
            || panic!("guard precedes RNG")
        ),
        Err(NakamaAccountError::SchemaNotReady(_))
    ));
    for disabled in [None, Some(-1)] {
        let mut existing = Database::missing();
        existing.existing = Some(UserId::new([0; 16]));
        existing.stored = Some(NakamaLegacyUser {
            id: UserId::new([0; 16]),
            stored_username: "stored-name".into(),
            disable_unix_seconds: disabled,
        });
        let result = authenticate_device_with_id_source(
            &mut existing,
            DatabaseProfile::PostgreSql,
            request(true),
            || panic!("existing account never requests new UUID"),
        );
        if disabled.is_some() {
            assert_eq!(result, Err(NakamaAccountError::Banned));
        } else {
            assert!(!result.unwrap().created);
        }
        assert_eq!(existing.state.borrow().count(Step::Device), 1);
        assert_eq!(existing.state.borrow().count(Step::Begin), 0);
    }
    let mut missing = Database::missing();
    assert_eq!(
        authenticate_device_with_id_source(
            &mut missing,
            DatabaseProfile::CockroachDb,
            request(false),
            || panic!("not create never requests UUID")
        ),
        Err(NakamaAccountError::NotFound)
    );
    assert_eq!(missing.state.borrow().count(Step::Device), 1);
    assert_eq!(missing.state.borrow().count(Step::Begin), 0);
}

#[test]
fn deferred_uuid_failure_is_internal_before_tx_without_lookup_recall() {
    let mut db = Database::missing();
    let mut calls = 0;
    let error = authenticate_device_with_id_source(
        &mut db,
        DatabaseProfile::PostgreSql,
        request(true),
        || {
            calls += 1;
            Err(NakamaAccountIdGenerationError::Unavailable)
        },
    )
    .unwrap_err();
    assert_eq!(error.code(), StableCode::Internal);
    assert_eq!(error.reason(), "new_device_user_uuid_generation_failed");
    assert_eq!(detailed(error).phase, AccountPhase::Input);
    assert_eq!(calls, 1);
    assert_eq!(db.state.borrow().steps, vec![Step::Guard, Step::Device]);
    assert_eq!(db.state.borrow().durable_users, 0);
}

#[test]
fn deferred_uuid_is_generated_once_and_reused_across_both_profile_attempts() {
    for profile in [DatabaseProfile::PostgreSql, DatabaseProfile::CockroachDb] {
        let mut db = Database::missing();
        db.fault(Step::InsertUser, 1, failure(b"40001"));
        let mut calls = 0;
        let shared = Rc::clone(&db.state);
        let outcome = authenticate_device_with_id_source(&mut db, profile, request(true), || {
            calls += 1;
            assert_eq!(shared.borrow().steps, vec![Step::Guard, Step::Device]);
            Ok(id())
        });
        assert!(outcome.unwrap().created);
        assert_eq!(calls, 1);
        assert_eq!(db.state.borrow().inserted_ids, vec![id(), id()]);
        assert_eq!(db.state.borrow().count(Step::Device), 1);
        assert_eq!(
            (
                db.state.borrow().durable_users,
                db.state.borrow().durable_devices
            ),
            (1, 1)
        );
    }
}

#[test]
fn deferred_uuid_invalid_version_or_variant_never_begins_transaction() {
    for raw in [[0; 16], [1; 16]] {
        let mut db = Database::missing();
        let result = authenticate_device_with_id_source(
            &mut db,
            DatabaseProfile::PostgreSql,
            request(true),
            || Ok(UserId::new(raw)),
        );
        assert_eq!(result.unwrap_err().reason(), "new_device_user_uuid_invalid");
        assert_eq!(db.state.borrow().count(Step::Begin), 0);
    }
}

// Pure Custom seam tests deliberately do not qualify SQL/catalog/HTTP.
struct CustomMock {
    ready: Result<(), DomainError>,
    found: Result<Option<NakamaLegacyUser>, AccountSqlFailure>,
    inserted: Result<u64, AccountSqlFailure>,
    write_allowed: bool,
    calls: Vec<&'static str>,
}
impl CustomMock {
    fn missing() -> Self {
        Self {
            ready: Ok(()),
            found: Ok(None),
            inserted: Ok(1),
            write_allowed: true,
            calls: Vec::new(),
        }
    }
}
impl CustomDatabase for CustomMock {
    fn require_custom_ready(&mut self) -> Result<(), DomainError> {
        self.calls.push("ready");
        self.ready
    }
    fn find_custom(&mut self, _: &str) -> Result<Option<NakamaLegacyUser>, AccountSqlFailure> {
        self.calls.push("find");
        self.found.clone()
    }
    fn check_custom_write_allowed(&mut self) -> Result<(), AccountSqlFailure> {
        if self.write_allowed {
            Ok(())
        } else {
            Err(AccountSqlFailure::local("custom_write_lease_retired"))
        }
    }
    fn insert_custom(
        &mut self,
        _: AuthenticateCustom<'_>,
        _: UserId,
    ) -> Result<u64, AccountSqlFailure> {
        self.calls.push("insert");
        self.inserted
    }
}
fn custom_request(create: bool) -> AuthenticateCustom<'static> {
    AuthenticateCustom::new("custom123", "Requested", create).unwrap()
}
#[test]
fn custom_closed_guard_skips_lookup_uuid_and_autocommit() {
    let mut db = CustomMock::missing();
    db.ready = Err(DomainError::new(
        StableCode::Unavailable,
        "schema5_native_catalog_capture_pending",
        trnm_contracts::RetryClass::Never,
    ));
    assert!(matches!(
        authenticate_custom_with_id_source(&mut db, custom_request(true), || panic!(
            "guard must skip UUID"
        )),
        Err(NakamaAccountError::SchemaNotReady(_))
    ));
    assert_eq!(db.calls, ["ready"]);
}
#[test]
fn custom_existing_returns_stored_name_and_never_generates_or_inserts() {
    for create in [false, true] {
        let mut db = CustomMock::missing();
        db.found = Ok(Some(NakamaLegacyUser {
            id: id(),
            stored_username: "界".repeat(90),
            disable_unix_seconds: Some(0),
        }));
        let outcome = authenticate_custom_with_id_source(&mut db, custom_request(create), || {
            panic!("existing must skip UUID")
        })
        .unwrap();
        assert!(!outcome.created);
        assert_eq!(outcome.user.stored_username, "界".repeat(90));
        assert_eq!(db.calls, ["ready", "find"]);
    }
}
#[test]
fn custom_banned_checks_nonzero_unix_floor_before_uuid_or_create() {
    for seconds in [-1, 1] {
        let mut db = CustomMock::missing();
        db.found = Ok(Some(NakamaLegacyUser {
            id: id(),
            stored_username: "old".into(),
            disable_unix_seconds: Some(seconds),
        }));
        assert_eq!(
            authenticate_custom_with_id_source(&mut db, custom_request(true), || panic!(
                "banned UUID"
            )),
            Err(NakamaAccountError::Banned)
        );
        assert_eq!(db.calls, ["ready", "find"]);
    }
}
#[test]
fn custom_missing_false_does_not_generate_or_insert() {
    let mut db = CustomMock::missing();
    assert_eq!(
        authenticate_custom_with_id_source(&mut db, custom_request(false), || panic!(
            "createfalse UUID"
        )),
        Err(NakamaAccountError::NotFound)
    );
    assert_eq!(db.calls, ["ready", "find"]);
}
#[test]
fn custom_lookup_error_keeps_typed_finding_phase_and_original_sqlstate() {
    let mut db = CustomMock::missing();
    db.found = Err(failure(b"08006"));
    let error = authenticate_custom_with_id_source(&mut db, custom_request(true), || {
        panic!("lookup error UUID")
    })
    .unwrap_err();
    let NakamaAccountError::CustomLookupFailed(detail) = error else {
        panic!("lookup distinction");
    };
    assert_eq!(detail.phase, AccountPhase::CustomLookup);
    assert_eq!(detail.last_failure.sqlstate, Some(*b"08006"));
    assert_eq!(detail.attempts, 0);
    assert!(!detail.unknown_commit);
    assert_eq!(db.calls, ["ready", "find"]);
}
#[test]
fn custom_create_generates_once_then_one_autocommit_and_returns_created() {
    let mut db = CustomMock::missing();
    let mut generated = 0;
    let outcome = authenticate_custom_with_id_source(&mut db, custom_request(true), || {
        generated += 1;
        Ok(id())
    })
    .unwrap();
    assert_eq!(generated, 1);
    assert!(outcome.created);
    assert_eq!(outcome.user.id, id());
    assert_eq!(outcome.user.stored_username, "Requested");
    assert_eq!(db.calls, ["ready", "find", "insert"]);
}
#[test]
fn custom_uuid_failure_or_invalid_v4_never_inserts() {
    let mut db = CustomMock::missing();
    assert!(
        authenticate_custom_with_id_source(&mut db, custom_request(true), || Err(
            NakamaAccountIdGenerationError::Unavailable
        ))
        .is_err()
    );
    assert_eq!(db.calls, ["ready", "find"]);
    let mut db = CustomMock::missing();
    assert!(
        authenticate_custom_with_id_source(&mut db, custom_request(true), || Ok(UserId::new(
            [0; 16]
        )))
        .is_err()
    );
    assert_eq!(db.calls, ["ready", "find"]);
}
#[test]
fn custom_identity_race_internal_username_23505_conflict_and_no_retry() {
    for (state, username, expected) in [
        (b"23505", false, StableCode::Internal),
        (b"23505", true, StableCode::AlreadyExists),
        (b"40001", true, StableCode::Internal),
    ] {
        let mut db = CustomMock::missing();
        db.inserted = Err(AccountSqlFailure {
            username_collision: username,
            ..failure(state)
        });
        let error = authenticate_custom_with_id_source(&mut db, custom_request(true), || Ok(id()))
            .unwrap_err();
        let detail = detailed(error);
        assert_eq!(error.code(), expected);
        assert_eq!(detail.phase, AccountPhase::CustomInsert);
        assert_eq!(detail.last_failure.sqlstate, Some(*state));
        assert_eq!(detail.attempts, 1);
        assert!(!detail.exhausted);
        assert_eq!(db.calls, ["ready", "find", "insert"]);
    }
}
#[test]
fn custom_unknown_autocommit_is_preserved_without_winner_lookup_or_retry() {
    for state in [None, Some(*b"40003"), Some(*b"08006")] {
        let mut db = CustomMock::missing();
        db.inserted = Err(AccountSqlFailure {
            sqlstate: state,
            ..AccountSqlFailure::local("synthetic_autocommit_failure")
        });
        let detail = detailed(
            authenticate_custom_with_id_source(&mut db, custom_request(true), || Ok(id()))
                .unwrap_err(),
        );
        assert!(detail.unknown_commit);
        assert_eq!(detail.attempts, 1);
        assert_eq!(db.calls, ["ready", "find", "insert"]);
    }
}
#[test]
fn custom_only_one_affected_row_can_ack_creation() {
    for count in [0, 2] {
        let mut db = CustomMock::missing();
        db.inserted = Ok(count);
        let detail = detailed(
            authenticate_custom_with_id_source(&mut db, custom_request(true), || Ok(id()))
                .unwrap_err(),
        );
        assert_eq!(detail.phase, AccountPhase::CustomInsert);
        assert_eq!(
            detail.last_failure.reason,
            "custom_insert_rows_affected_unexpected"
        );
        assert_eq!(detail.unknown_commit, count > 0);
        assert_eq!(db.calls, ["ready", "find", "insert"]);
    }
    let debug = format!(
        "{:?}",
        AuthenticateCustom::new("secret-id", "secret-name", true).unwrap()
    );
    assert!(!debug.contains("secret-id") && !debug.contains("secret-name"));
}

#[test]
fn custom_observed_retired_lease_skips_write_without_claiming_unknown_commit() {
    let mut db = CustomMock::missing();
    db.write_allowed = false;
    let detail = detailed(
        authenticate_custom_with_id_source(&mut db, custom_request(true), || Ok(id())).unwrap_err(),
    );
    assert_eq!(detail.phase, AccountPhase::Input);
    assert_eq!(detail.attempts, 0);
    assert!(!detail.unknown_commit);
    assert_eq!(detail.last_failure.reason, "custom_write_lease_retired");
    assert_eq!(db.calls, ["ready", "find"]);
}
