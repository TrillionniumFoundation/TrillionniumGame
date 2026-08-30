use std::path::Path;

const GAME_SERVER_ENTRYPOINT: &str = include_str!("../src/lib.rs");
const SETTLEMENT_WORKER_ENTRYPOINT: &str = include_str!("../src/settlement_worker.rs");
const SETTLEMENT_WORKER_LEGACY: &str = include_str!("../src/settlement_worker_legacy.rs");
const SETTLEMENT_WORKER_RUNTIME_V2: &str = include_str!("../src/settlement_worker_runtime_v2.rs");

#[test]
fn game_server_is_direct_source_and_does_not_execute_terminal_economy_settlement() {
    let crate_root = Path::new(env!("CARGO_MANIFEST_DIR"));
    assert!(!crate_root.join("build.rs").exists());
    assert!(!crate_root.join("src/lib.rs.in").exists());
    assert!(!GAME_SERVER_ENTRYPOINT.contains("OUT_DIR"));
    assert!(!GAME_SERVER_ENTRYPOINT.contains("trnm_game_server_lib_generated.rs"));
    assert!(!GAME_SERVER_ENTRYPOINT.contains("reconcile_economy(&state.cex"));
    assert!(!GAME_SERVER_ENTRYPOINT.contains("settle_pending_matches(&settlement_state"));
    assert!(GAME_SERVER_ENTRYPOINT.contains(
        "terminal settlement is owned by trnm-settlement-worker; in-process settlement is prohibited"
    ));
}

#[test]
fn direct_runtime_entrypoints_register_the_complete_settlement_migration_chain() {
    assert!(!SETTLEMENT_WORKER_ENTRYPOINT.contains("OUT_DIR"));
    assert!(!SETTLEMENT_WORKER_ENTRYPOINT.contains("trnm_settlement_worker_generated.rs"));
    let worker = format!("{SETTLEMENT_WORKER_LEGACY}\n{SETTLEMENT_WORKER_RUNTIME_V2}");
    for marker in [
        "0016_online_settlement_outbox_v1",
        "0017_online_settlement_worker_runtime_v1",
        "0018_online_settlement_operator_controls_v1",
        "0019_online_settlement_quarantine_v1",
    ] {
        assert!(
            GAME_SERVER_ENTRYPOINT.contains(marker),
            "direct game server lost {marker}"
        );
        assert!(
            worker.contains(marker),
            "direct settlement worker lost {marker}"
        );
    }
}

#[test]
fn directly_compiled_migration_includes_are_source_relative() {
    for source in [
        GAME_SERVER_ENTRYPOINT,
        SETTLEMENT_WORKER_LEGACY,
        SETTLEMENT_WORKER_RUNTIME_V2,
    ] {
        assert!(source.contains("include_str!(\"../migrations/"));
        assert!(!source.contains("CARGO_MANIFEST_DIR"));
    }
}
