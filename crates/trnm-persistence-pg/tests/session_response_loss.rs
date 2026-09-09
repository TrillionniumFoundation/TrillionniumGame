use std::collections::BTreeSet;
use std::env;
#[cfg(feature = "session-test-hooks")]
use std::sync::mpsc;
use std::sync::{Arc, Barrier};
use std::thread;
#[cfg(feature = "session-test-hooks")]
use std::time::Duration;

#[cfg(feature = "session-test-hooks")]
use postgres::{Client, NoTls};
use trnm_contracts::{Digest32, RefreshTokenId, SessionFamilyId, StableCode, UserId};
#[cfg(feature = "session-test-hooks")]
use trnm_persistence_pg::SessionMutationPoint;
use trnm_persistence_pg::{
    CreateSessionFamily, DatabaseProfile, PgRepository, RefreshRotationOutcome,
    RefreshTokenCredential, RotateRefreshToken,
};
use trnm_session_core::RevocationReason;

fn profile(value: &str) -> DatabaseProfile {
    match value {
        "postgresql" => DatabaseProfile::PostgreSql,
        "cockroachdb" => DatabaseProfile::CockroachDb,
        other => panic!("unsupported TRNM_DATABASE_PROFILE={other}"),
    }
}

fn live_database_environment(label: &str) -> Option<(String, DatabaseProfile)> {
    let required = match env::var("TRNM_REQUIRE_LIVE_DATABASE") {
        Err(env::VarError::NotPresent) => false,
        Err(error) => panic!("cannot read TRNM_REQUIRE_LIVE_DATABASE: {error}"),
        Ok(value) if matches!(value.as_str(), "1" | "true" | "TRUE" | "yes" | "YES") => true,
        Ok(value) if matches!(value.as_str(), "0" | "false" | "FALSE" | "no" | "NO") => false,
        Ok(value) => {
            panic!("invalid TRNM_REQUIRE_LIVE_DATABASE={value:?}; expected 0/1 or false/true")
        }
    };

    let database_url = match env::var("TRNM_DATABASE_URL") {
        Ok(value) if !value.is_empty() => value,
        Ok(_) if required => panic!("{label}: TRNM_DATABASE_URL is required and must not be empty"),
        Ok(_) => {
            eprintln!(
                "{label}: empty TRNM_DATABASE_URL; developer-only live test skip (no evidence credit)"
            );
            return None;
        }
        Err(env::VarError::NotPresent) if required => {
            panic!("{label}: TRNM_REQUIRE_LIVE_DATABASE=1 but TRNM_DATABASE_URL is absent")
        }
        Err(env::VarError::NotPresent) => {
            eprintln!(
                "{label}: TRNM_DATABASE_URL absent; developer-only live test skip (no evidence credit)"
            );
            return None;
        }
        Err(error) => panic!("{label}: cannot read TRNM_DATABASE_URL: {error}"),
    };

    let profile_value = env::var("TRNM_DATABASE_PROFILE").unwrap_or_else(|_| {
        panic!("{label}: TRNM_DATABASE_PROFILE is required with TRNM_DATABASE_URL")
    });
    Some((database_url, profile(&profile_value)))
}

fn credential(id: u8, digest: u8) -> RefreshTokenCredential {
    RefreshTokenCredential {
        id: RefreshTokenId::new([id; 16]),
        digest: Digest32::new([digest; 32]),
    }
}

fn concurrency_credentials(iteration: u8) -> (RefreshTokenCredential, RefreshTokenCredential) {
    let base = 0x90_u8.checked_add(iteration).unwrap();
    (
        credential(base + 0x20, base),
        credential(base + 0x40, base ^ 0x80),
    )
}

fn interleaving_credentials() -> [RefreshTokenCredential; 14] {
    [
        credential(0x42, 0x43),
        credential(0x44, 0x45),
        credential(0x48, 0x49),
        credential(0x4a, 0x4b),
        credential(0x4c, 0x4d),
        credential(0x50, 0x51),
        credential(0x52, 0x53),
        credential(0x54, 0x55),
        credential(0x58, 0x59),
        credential(0x5a, 0x5b),
        credential(0x5c, 0x5d),
        credential(0x60, 0x61),
        credential(0x62, 0x63),
        credential(0x64, 0x65),
    ]
}

#[test]
fn session_live_fixture_digests_are_globally_unique() {
    let mut digests = BTreeSet::new();
    for value in [
        credential(0x73, 0x74),
        credential(0x75, 0x76),
        credential(0x77, 0x78),
    ] {
        assert!(digests.insert(*value.digest.as_bytes()));
    }
    for iteration in 0_u8..16 {
        let (predecessor, successor) = concurrency_credentials(iteration);
        assert!(digests.insert(*predecessor.digest.as_bytes()));
        assert!(digests.insert(*successor.digest.as_bytes()));
    }
    for value in interleaving_credentials() {
        assert!(digests.insert(*value.digest.as_bytes()));
    }
    assert_eq!(digests.len(), 49);
}

#[test]
fn committed_refresh_response_loss_is_idempotent_and_changed_successor_revokes() {
    let Some((database_url, profile)) = live_database_environment("refresh response-loss contract")
    else {
        return;
    };

    let family = SessionFamilyId::new([0x71; 16]);
    let user = UserId::new([0x72; 16]);
    let predecessor = credential(0x73, 0x74);
    let successor = credential(0x75, 0x76);
    let changed_successor = credential(0x77, 0x78);

    let mut repository = PgRepository::connect(&database_url, profile).unwrap();
    let created = repository
        .create_session_family(&CreateSessionFamily {
            family,
            user,
            refresh: predecessor,
            issued_at_ms: 100,
        })
        .unwrap();
    assert_eq!(created.generation, 0);
    assert_eq!(created.active_token, Some(predecessor.id));

    let first_request = RotateRefreshToken {
        presented: predecessor,
        replacement: successor,
        rotated_at_ms: 200,
    };
    let first = match repository.rotate_refresh_token(&first_request).unwrap() {
        RefreshRotationOutcome::Rotated(record) => record,
        RefreshRotationOutcome::ReplayRevoked(_) => {
            panic!("fresh refresh rotation was classified as replay")
        }
    };
    assert_eq!(first.generation, 1);
    assert_eq!(first.active_token, Some(successor.id));
    assert_eq!(first.updated_at_ms, 200);
    drop(repository);

    // Reopen the repository to model a committed rotation whose HTTP response
    // was lost. The client repeats the exact credential transition at a later
    // wall-clock time; the durable result, not the new timestamp, is returned.
    let mut repository = PgRepository::connect(&database_url, profile).unwrap();
    let retried = match repository
        .rotate_refresh_token(&RotateRefreshToken {
            rotated_at_ms: 201,
            ..first_request
        })
        .unwrap()
    {
        RefreshRotationOutcome::Rotated(record) => record,
        RefreshRotationOutcome::ReplayRevoked(_) => {
            panic!("exact committed successor was not replayed idempotently")
        }
    };
    assert_eq!(retried, first);
    assert!(repository.verify_access_session(family, user, 1).is_ok());

    // Reusing the predecessor with any other successor is still an attack-like
    // replay and revokes the complete family atomically.
    let revoked = match repository
        .rotate_refresh_token(&RotateRefreshToken {
            presented: predecessor,
            replacement: changed_successor,
            rotated_at_ms: 202,
        })
        .unwrap()
    {
        RefreshRotationOutcome::ReplayRevoked(record) => record,
        RefreshRotationOutcome::Rotated(_) => {
            panic!("changed successor bypassed refresh replay revocation")
        }
    };
    assert_eq!(revoked.active_token, None);
    assert_eq!(
        revoked.revoked_reason,
        Some(RevocationReason::RefreshReplay)
    );
    assert_eq!(revoked.updated_at_ms, 202);
    let failure = repository
        .verify_access_session(family, user, 1)
        .unwrap_err();
    assert_eq!(failure.code(), StableCode::Unauthenticated);
    assert_eq!(failure.reason(), "session_not_active");
}

#[cfg(feature = "session-test-hooks")]
type RotationResult = Result<RefreshRotationOutcome, (StableCode, &'static str)>;

#[cfg(feature = "session-test-hooks")]
#[derive(Debug)]
struct PersistedRefreshToken {
    id: Vec<u8>,
    generation: i64,
    state: i16,
    issued_at_ms: i64,
    consumed_at_ms: Option<i64>,
}

#[cfg(feature = "session-test-hooks")]
#[derive(Debug)]
struct PersistedSessionFamily {
    generation: i64,
    active_token: Option<Vec<u8>>,
    revoked_reason: Option<i16>,
    tokens: Vec<PersistedRefreshToken>,
}

#[cfg(feature = "session-test-hooks")]
fn mapped_rotation(
    result: Result<RefreshRotationOutcome, trnm_contracts::DomainError>,
) -> RotationResult {
    result.map_err(|error| (error.code(), error.reason()))
}

#[cfg(feature = "session-test-hooks")]
fn establish_committed_successor(
    database_url: &str,
    profile: DatabaseProfile,
    family: SessionFamilyId,
    user: UserId,
    predecessor: RefreshTokenCredential,
    successor: RefreshTokenCredential,
) -> trnm_persistence_pg::SessionFamilyRecord {
    let mut repository = PgRepository::connect(database_url, profile).unwrap();
    repository
        .create_session_family(&CreateSessionFamily {
            family,
            user,
            refresh: predecessor,
            issued_at_ms: 100,
        })
        .unwrap();
    let request = RotateRefreshToken {
        presented: predecessor,
        replacement: successor,
        rotated_at_ms: 200,
    };
    for attempt in 0..4 {
        match repository.rotate_refresh_token(&request) {
            Ok(RefreshRotationOutcome::Rotated(record)) => return record,
            Ok(RefreshRotationOutcome::ReplayRevoked(_)) => {
                panic!("fresh setup rotation was classified as replay")
            }
            Err(error)
                if error.code() == StableCode::Aborted
                    && error.reason() == "database_serialization_failure"
                    && attempt < 3 =>
            {
                repository = PgRepository::connect(database_url, profile).unwrap();
            }
            Err(error) => panic!("fresh setup rotation failed: {error}"),
        }
    }
    unreachable!("bounded fresh setup rotation retries exhausted")
}

#[cfg(feature = "session-test-hooks")]
fn ordered_rotations(
    database_url: &str,
    profile: DatabaseProfile,
    first_request: RotateRefreshToken,
    second_request: RotateRefreshToken,
) -> (RotationResult, RotationResult) {
    let (first_ready_tx, first_ready_rx) = mpsc::channel();
    let (release_first_tx, release_first_rx) = mpsc::channel();
    let first_url = database_url.to_owned();
    let first = thread::spawn(move || {
        let mut repository = PgRepository::connect(&first_url, profile).unwrap();
        let mut announced = false;
        mapped_rotation(
            repository.rotate_refresh_token_with_test_hook(&first_request, |point| {
                if point == SessionMutationPoint::BeforeCommit && !announced {
                    announced = true;
                    first_ready_tx.send(()).unwrap();
                    release_first_rx
                        .recv_timeout(Duration::from_secs(20))
                        .expect("first rotation release was not delivered");
                }
            }),
        )
    });

    first_ready_rx
        .recv_timeout(Duration::from_secs(20))
        .expect("first rotation did not reach its deterministic commit fence");

    let (second_ready_tx, second_ready_rx) = mpsc::channel();
    let second_url = database_url.to_owned();
    let second = thread::spawn(move || {
        let mut repository = PgRepository::connect(&second_url, profile).unwrap();
        let mut announced = false;
        mapped_rotation(
            repository.rotate_refresh_token_with_test_hook(&second_request, |point| {
                if point == SessionMutationPoint::CredentialResolved && !announced {
                    announced = true;
                    second_ready_tx.send(()).unwrap();
                }
            }),
        )
    });

    second_ready_rx
        .recv_timeout(Duration::from_secs(20))
        .expect("second rotation did not resolve the exact credential");
    release_first_tx.send(()).unwrap();

    let first = first.join().expect("first rotation thread panicked");
    let second = second.join().expect("second rotation thread panicked");
    for result in [&first, &second] {
        if let Err((_code, reason)) = result {
            assert_ne!(*reason, "database_deadlock");
        }
    }
    (first, second)
}

#[cfg(feature = "session-test-hooks")]
fn settle_serialization(
    database_url: &str,
    profile: DatabaseProfile,
    request: RotateRefreshToken,
    mut result: RotationResult,
) -> RotationResult {
    for _ in 0..4 {
        let retry = matches!(
            &result,
            Err((StableCode::Aborted, "database_serialization_failure"))
        );
        if !retry {
            return result;
        }
        let mut repository = PgRepository::connect(database_url, profile).unwrap();
        result = mapped_rotation(repository.rotate_refresh_token(&request));
    }
    result
}

#[cfg(feature = "session-test-hooks")]
fn expect_rotated(result: RotationResult) -> trnm_persistence_pg::SessionFamilyRecord {
    match result {
        Ok(RefreshRotationOutcome::Rotated(record)) => record,
        other => panic!("expected rotated outcome, got {other:?}"),
    }
}

#[cfg(feature = "session-test-hooks")]
fn expect_replay_revoked(result: RotationResult) -> trnm_persistence_pg::SessionFamilyRecord {
    match result {
        Ok(RefreshRotationOutcome::ReplayRevoked(record)) => record,
        other => panic!("expected replay-revoked outcome, got {other:?}"),
    }
}

#[cfg(feature = "session-test-hooks")]
fn load_persisted_family(database_url: &str, family: SessionFamilyId) -> PersistedSessionFamily {
    let mut client = Client::connect(database_url, NoTls).unwrap();
    let row = client
        .query_one(
            "SELECT generation, active_token_id, revoked_reason \
             FROM trnm_session_families WHERE family_id = $1",
            &[&family.as_bytes().as_slice()],
        )
        .unwrap();
    let tokens = client
        .query(
            "SELECT token_id, generation, state, issued_at_ms, consumed_at_ms \
             FROM trnm_refresh_tokens WHERE family_id = $1 \
             ORDER BY generation, token_id",
            &[&family.as_bytes().as_slice()],
        )
        .unwrap()
        .into_iter()
        .map(|row| PersistedRefreshToken {
            id: row.get(0),
            generation: row.get(1),
            state: row.get(2),
            issued_at_ms: row.get(3),
            consumed_at_ms: row.get(4),
        })
        .collect();
    PersistedSessionFamily {
        generation: row.get(0),
        active_token: row.get(1),
        revoked_reason: row.get(2),
        tokens,
    }
}

#[cfg(feature = "session-test-hooks")]
fn assert_family_invariants(state: &PersistedSessionFamily) {
    let active: Vec<_> = state
        .tokens
        .iter()
        .filter(|token| token.state == 0)
        .collect();
    assert_eq!(active.len(), usize::from(state.active_token.is_some()));
    if let Some(active_token) = &state.active_token {
        assert_eq!(active[0].id, *active_token);
    }
    let mut generations = BTreeSet::new();
    for token in &state.tokens {
        assert!(generations.insert(token.generation));
        match token.state {
            0 => assert_eq!(token.consumed_at_ms, None),
            1 => assert!(
                token.consumed_at_ms.unwrap() >= token.issued_at_ms,
                "consumed refresh token predates issue: {token:?}"
            ),
            value => panic!("unexpected refresh token state {value}"),
        }
    }
}

#[cfg(feature = "session-test-hooks")]
fn assert_family_state(
    state: &PersistedSessionFamily,
    generation: i64,
    active: Option<RefreshTokenCredential>,
    revoked_reason: Option<i16>,
) {
    assert_eq!(state.generation, generation);
    assert_eq!(state.revoked_reason, revoked_reason);
    assert_eq!(
        state.active_token.as_deref(),
        active
            .map(|value| value.id)
            .as_ref()
            .map(|id| id.as_bytes().as_slice())
    );
    assert_family_invariants(state);
}

#[cfg(feature = "session-test-hooks")]
fn assert_token_state(
    state: &PersistedSessionFamily,
    credential: RefreshTokenCredential,
    generation: i64,
    token_state: i16,
    issued_at_ms: i64,
    consumed_at_ms: Option<i64>,
) {
    let token = state
        .tokens
        .iter()
        .find(|token| token.id.as_slice() == credential.id.as_bytes().as_slice())
        .unwrap_or_else(|| panic!("missing persisted token generation {generation}"));
    assert_eq!(token.generation, generation);
    assert_eq!(token.state, token_state);
    assert_eq!(token.issued_at_ms, issued_at_ms);
    assert_eq!(token.consumed_at_ms, consumed_at_ms);
}

#[cfg(feature = "session-test-hooks")]
#[test]
fn concurrent_exact_committed_retries_return_same_durable_result() {
    let Some((database_url, profile)) =
        live_database_environment("concurrent exact committed refresh retries")
    else {
        return;
    };
    let credentials = interleaving_credentials();
    let predecessor = credentials[0];
    let successor = credentials[1];
    let family = SessionFamilyId::new([0x40; 16]);
    let user = UserId::new([0x41; 16]);
    let durable =
        establish_committed_successor(&database_url, profile, family, user, predecessor, successor);
    let first_request = RotateRefreshToken {
        presented: predecessor,
        replacement: successor,
        rotated_at_ms: 201,
    };
    let second_request = RotateRefreshToken {
        rotated_at_ms: 202,
        ..first_request
    };
    let (first, second) = ordered_rotations(&database_url, profile, first_request, second_request);
    assert_eq!(expect_rotated(first), durable);
    assert_eq!(
        expect_rotated(settle_serialization(
            &database_url,
            profile,
            second_request,
            second,
        )),
        durable
    );
    let state = load_persisted_family(&database_url, family);
    assert_family_state(&state, 1, Some(successor), None);
    assert_eq!(state.tokens.len(), 2);
    assert_token_state(&state, predecessor, 0, 1, 100, Some(200));
    assert_token_state(&state, successor, 1, 0, 200, None);
}

#[cfg(feature = "session-test-hooks")]
#[test]
fn exact_retry_then_successor_rotation_is_one_linear_history() {
    let Some((database_url, profile)) =
        live_database_environment("exact retry before successor rotation")
    else {
        return;
    };
    let credentials = interleaving_credentials();
    let predecessor = credentials[2];
    let successor = credentials[3];
    let third = credentials[4];
    let family = SessionFamilyId::new([0x46; 16]);
    let user = UserId::new([0x47; 16]);
    let durable =
        establish_committed_successor(&database_url, profile, family, user, predecessor, successor);
    let retry_request = RotateRefreshToken {
        presented: predecessor,
        replacement: successor,
        rotated_at_ms: 201,
    };
    let next_request = RotateRefreshToken {
        presented: successor,
        replacement: third,
        rotated_at_ms: 300,
    };
    let (retry, next) = ordered_rotations(&database_url, profile, retry_request, next_request);
    assert_eq!(expect_rotated(retry), durable);
    let next = expect_rotated(settle_serialization(
        &database_url,
        profile,
        next_request,
        next,
    ));
    assert_eq!(next.generation, 2);
    assert_eq!(next.active_token, Some(third.id));
    let state = load_persisted_family(&database_url, family);
    assert_family_state(&state, 2, Some(third), None);
    assert_eq!(state.tokens.len(), 3);
    assert_token_state(&state, predecessor, 0, 1, 100, Some(200));
    assert_token_state(&state, successor, 1, 1, 200, Some(300));
    assert_token_state(&state, third, 2, 0, 300, None);
}

#[cfg(feature = "session-test-hooks")]
#[test]
fn successor_rotation_then_old_retry_revokes_one_linear_history() {
    let Some((database_url, profile)) =
        live_database_environment("successor rotation before old exact retry")
    else {
        return;
    };
    let credentials = interleaving_credentials();
    let predecessor = credentials[5];
    let successor = credentials[6];
    let third = credentials[7];
    let family = SessionFamilyId::new([0x4e; 16]);
    let user = UserId::new([0x4f; 16]);
    establish_committed_successor(&database_url, profile, family, user, predecessor, successor);
    let next_request = RotateRefreshToken {
        presented: successor,
        replacement: third,
        rotated_at_ms: 300,
    };
    let old_retry = RotateRefreshToken {
        presented: predecessor,
        replacement: successor,
        rotated_at_ms: 301,
    };
    let (next, retry) = ordered_rotations(&database_url, profile, next_request, old_retry);
    let next = expect_rotated(next);
    assert_eq!(next.generation, 2);
    assert_eq!(next.active_token, Some(third.id));
    let revoked = expect_replay_revoked(settle_serialization(
        &database_url,
        profile,
        old_retry,
        retry,
    ));
    assert_eq!(revoked.generation, 2);
    assert_eq!(revoked.active_token, None);
    assert_eq!(
        revoked.revoked_reason,
        Some(RevocationReason::RefreshReplay)
    );
    let state = load_persisted_family(&database_url, family);
    assert_family_state(&state, 2, None, Some(2));
    assert_eq!(state.tokens.len(), 3);
    assert_token_state(&state, predecessor, 0, 1, 100, Some(200));
    assert_token_state(&state, successor, 1, 1, 200, Some(300));
    assert_token_state(&state, third, 2, 1, 300, Some(301));
}

#[cfg(feature = "session-test-hooks")]
#[test]
fn exact_retry_then_changed_replay_revokes_one_linear_history() {
    let Some((database_url, profile)) =
        live_database_environment("exact retry before changed successor replay")
    else {
        return;
    };
    let credentials = interleaving_credentials();
    let predecessor = credentials[8];
    let successor = credentials[9];
    let changed = credentials[10];
    let family = SessionFamilyId::new([0x56; 16]);
    let user = UserId::new([0x57; 16]);
    let durable =
        establish_committed_successor(&database_url, profile, family, user, predecessor, successor);
    let exact = RotateRefreshToken {
        presented: predecessor,
        replacement: successor,
        rotated_at_ms: 201,
    };
    let changed_request = RotateRefreshToken {
        presented: predecessor,
        replacement: changed,
        rotated_at_ms: 202,
    };
    let (exact_result, changed_result) =
        ordered_rotations(&database_url, profile, exact, changed_request);
    assert_eq!(expect_rotated(exact_result), durable);
    let revoked = expect_replay_revoked(settle_serialization(
        &database_url,
        profile,
        changed_request,
        changed_result,
    ));
    assert_eq!(revoked.generation, 1);
    assert_eq!(
        revoked.revoked_reason,
        Some(RevocationReason::RefreshReplay)
    );
    let state = load_persisted_family(&database_url, family);
    assert_family_state(&state, 1, None, Some(2));
    assert_eq!(state.tokens.len(), 2);
    assert_token_state(&state, predecessor, 0, 1, 100, Some(200));
    assert_token_state(&state, successor, 1, 1, 200, Some(202));
}

#[cfg(feature = "session-test-hooks")]
#[test]
fn changed_replay_then_exact_retry_is_one_linear_history() {
    let Some((database_url, profile)) =
        live_database_environment("changed successor replay before exact retry")
    else {
        return;
    };
    let credentials = interleaving_credentials();
    let predecessor = credentials[11];
    let successor = credentials[12];
    let changed = credentials[13];
    let family = SessionFamilyId::new([0x5e; 16]);
    let user = UserId::new([0x5f; 16]);
    establish_committed_successor(&database_url, profile, family, user, predecessor, successor);
    let changed_request = RotateRefreshToken {
        presented: predecessor,
        replacement: changed,
        rotated_at_ms: 202,
    };
    let exact = RotateRefreshToken {
        presented: predecessor,
        replacement: successor,
        rotated_at_ms: 203,
    };
    let (changed_result, exact_result) =
        ordered_rotations(&database_url, profile, changed_request, exact);
    let revoked = expect_replay_revoked(changed_result);
    assert_eq!(revoked.generation, 1);
    let exact_result = settle_serialization(&database_url, profile, exact, exact_result);
    assert!(matches!(
        exact_result,
        Err((StableCode::Unauthenticated, "session_not_active"))
    ));
    let state = load_persisted_family(&database_url, family);
    assert_family_state(&state, 1, None, Some(2));
    assert_eq!(state.tokens.len(), 2);
    assert_token_state(&state, predecessor, 0, 1, 100, Some(200));
    assert_token_state(&state, successor, 1, 1, 200, Some(202));
}

#[test]
fn concurrent_refresh_and_logout_never_surface_a_database_deadlock() {
    let Some((database_url, profile)) =
        live_database_environment("refresh/logout lock-order contract")
    else {
        return;
    };

    for iteration in 0_u8..16 {
        let base = 0x90_u8.checked_add(iteration).unwrap();
        let family = SessionFamilyId::new([base; 16]);
        let user = UserId::new([base + 0x10; 16]);
        let (predecessor, successor) = concurrency_credentials(iteration);

        let mut repository = PgRepository::connect(&database_url, profile).unwrap();
        repository
            .create_session_family(&CreateSessionFamily {
                family,
                user,
                refresh: predecessor,
                issued_at_ms: 100,
            })
            .unwrap();
        drop(repository);

        let barrier = Arc::new(Barrier::new(3));
        let rotation_barrier = Arc::clone(&barrier);
        let rotation_url = database_url.clone();
        let rotation = thread::spawn(move || {
            let mut repository = PgRepository::connect(&rotation_url, profile).unwrap();
            rotation_barrier.wait();
            repository
                .rotate_refresh_token(&RotateRefreshToken {
                    presented: predecessor,
                    replacement: successor,
                    rotated_at_ms: 200,
                })
                .map_err(|error| (error.code(), error.reason()))
        });

        let revoke_barrier = Arc::clone(&barrier);
        let revoke_url = database_url.clone();
        let revoke = thread::spawn(move || {
            let mut repository = PgRepository::connect(&revoke_url, profile).unwrap();
            revoke_barrier.wait();
            repository
                .revoke_session_family(family, user, RevocationReason::Logout, 201)
                .map_err(|error| (error.code(), error.reason()))
        });

        barrier.wait();
        let rotation = rotation.join().expect("rotation thread panicked");
        let revoke = revoke.join().expect("revoke thread panicked");

        for result in [
            rotation.as_ref().map(|_| ()).map_err(|value| *value),
            revoke.as_ref().map(|_| ()).map_err(|value| *value),
        ] {
            if let Err((_code, reason)) = result {
                assert_ne!(reason, "database_deadlock", "iteration {iteration}");
            }
        }

        match rotation {
            Ok(RefreshRotationOutcome::Rotated(record)) => {
                assert_eq!(record.generation, 1);
                assert_eq!(record.active_token, Some(successor.id));
            }
            Ok(RefreshRotationOutcome::ReplayRevoked(_)) => {
                panic!("fresh concurrent rotation was classified as replay")
            }
            Err((StableCode::Unauthenticated, "session_not_active"))
            | Err((StableCode::Aborted, "database_serialization_failure")) => {}
            Err((code, reason)) => {
                panic!("unexpected concurrent rotation failure {code:?}:{reason}")
            }
        }

        let mut repository = PgRepository::connect(&database_url, profile).unwrap();
        let final_record = match revoke {
            Ok(record) => record,
            Err((StableCode::Aborted, "database_serialization_failure")) => repository
                .revoke_session_family(family, user, RevocationReason::Logout, 202)
                .unwrap(),
            Err((code, reason)) => panic!("unexpected revoke failure {code:?}:{reason}"),
        };
        assert_eq!(final_record.active_token, None);
        assert_eq!(final_record.revoked_reason, Some(RevocationReason::Logout));
        assert_eq!(
            repository
                .load_session_family(family)
                .unwrap()
                .unwrap()
                .revoked_reason,
            Some(RevocationReason::Logout)
        );
    }
}
