use std::collections::BTreeSet;
use std::env;
use std::sync::{Arc, Barrier};
use std::thread;

use trnm_contracts::{Digest32, RefreshTokenId, SessionFamilyId, StableCode, UserId};
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
    assert_eq!(digests.len(), 35);
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
