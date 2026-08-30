use std::path::PathBuf;

const OUTBOX_MIGRATION: &str = include_str!("../migrations/0016_online_settlement_outbox_v1.sql");
const WORKER_MIGRATION: &str =
    include_str!("../migrations/0017_online_settlement_worker_runtime_v1.sql");
const WORKER_WRAPPER: &str = include_str!("../src/settlement_worker.rs");
const WORKER_LEGACY_SOURCE: &str = include_str!("../src/settlement_worker_legacy.rs");
const WORKER_RUNTIME_V2_SOURCE: &str = include_str!("../src/settlement_worker_runtime_v2.rs");
const CEX_SOURCE: &str = include_str!("../src/cex.rs");
const SIGNER_PROTOCOL: &str = include_str!("../src/signer_protocol.rs");
const SIGNER_BINARY: &str = include_str!("../src/bin/trnm-entitlement-signer.rs");
const WORKER_BINARY: &str = include_str!("../src/bin/trnm-settlement-worker.rs");

fn normalized(source: &str) -> String {
    source.split_whitespace().collect::<Vec<_>>().join(" ")
}

#[test]
fn settlement_worker_is_directly_compiled_from_reviewed_modules() {
    assert!(WORKER_WRAPPER.contains("settlement_worker_legacy.rs"));
    assert!(WORKER_WRAPPER.contains("settlement_worker_runtime_v2.rs"));
    assert!(WORKER_WRAPPER.contains("run_v2 as run"));
    assert!(!WORKER_WRAPPER.contains("OUT_DIR"));
    assert!(!WORKER_WRAPPER.contains("trnm_settlement_worker_generated.rs"));

    let crate_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    assert!(!crate_root.join("build.rs").exists());
    assert!(!crate_root.join("src/lib.rs.in").exists());
    assert!(!crate_root.join("src/settlement_worker.rs.in").exists());
}

#[test]
fn capture_claim_and_apply_are_persisted_as_separate_contracts() {
    let sql = normalized(&format!("{OUTBOX_MIGRATION}\n{WORKER_MIGRATION}"));
    for required in [
        "create table if not exists public.trnm_online_settlement_captures",
        "expected_campaign_revision bigint not null",
        "expected_campaign_state_hash text",
        "terminal_identity_hash text not null",
        "campaign_fences_json jsonb not null",
        "head_intent_ids_json jsonb not null",
        "create or replace function public.trnm_online_remote_request_id_v1",
        "create or replace function public.trnm_online_settlement_serialization_key_v1",
        "create or replace function public.trnm_online_claim_settlement_job_v2",
        "pg_catalog.pg_try_advisory_xact_lock",
        "for update of job skip locked",
        "create or replace function public.trnm_online_complete_settlement_job_v1",
        "campaign_applied_at timestamptz",
        "create or replace view public.trnm_online_settlement_job_status_v1",
        "create or replace view public.trnm_online_settlement_metrics_v1",
    ] {
        assert!(
            sql.contains(required),
            "missing settlement contract: {required}"
        );
    }
}

#[test]
fn runtime_v2_registers_operator_and_quarantine_migrations_directly() {
    for required in [
        "0018_online_settlement_operator_controls_v1.sql",
        "0019_online_settlement_quarantine_v1.sql",
        "apply_worker_migrations_v2",
        "apply_worker_migrations_v2_locked",
        "18_i32",
        "19_i32",
    ] {
        assert!(
            WORKER_RUNTIME_V2_SOURCE.contains(required),
            "missing direct runtime migration control: {required}"
        );
    }
}

#[test]
fn remote_retries_reuse_stable_authorization_material() {
    let sql = normalized(WORKER_MIGRATION);
    for required in [
        "authorization_request_id = coalesce( job.authorization_request_id, job.remote_request_id )",
        "entitlement_issued_at_epoch = coalesce",
        "entitlement_expires_at_epoch = coalesce",
        "entitlement_nonce = coalesce(job.entitlement_nonce, job.remote_request_id)",
        "p_authorization_request_id = remote_request_id",
        "authorization_request_id is null or authorization_request_id = remote_request_id",
        "entitlement_nonce is null or entitlement_nonce = remote_request_id",
        "authorized_intent_json = p_authorized_intent_json",
        "signer_receipt_hash = p_signer_receipt_hash",
        "remote_attempts = remote_attempts + 1",
    ] {
        assert!(sql.contains(required), "missing stable retry material: {required}");
    }
    assert!(!sql
        .contains("authorization_request_id = coalesce(job.authorization_request_id, job.job_id)"));
    assert!(!sql.contains("entitlement_nonce = coalesce(job.entitlement_nonce, job.job_id)"));
    assert!(CEX_SOURCE.contains("stable_entitlement_id(authorization_request_id)"));
    assert!(CEX_SOURCE.contains("signed.request_id != authorization_request_id"));
    assert!(CEX_SOURCE.contains("request_hash != signed.request_hash"));
}

#[test]
fn ambiguous_remote_outcomes_use_lookup_before_submit() {
    for required in [
        "async fn lookup_signer_receipt",
        "ENTITLEMENT_SIGNER_RECEIPT_PATH",
        "async fn lookup_authorized_settlement_receipt",
        "CEX_SETTLEMENT_RECEIPT_LOOKUP_PATH",
        "CEX_SETTLEMENT_RECEIPT_LOOKUP_CONTRACT",
        "signer_response_loss_recovers_by_lookup_without_a_second_sign",
        "cex_response_loss_recovers_by_lookup_without_a_second_submit",
        "cex_lookup_with_a_mismatched_hash_fails_closed",
    ] {
        assert!(
            CEX_SOURCE.contains(required),
            "missing recovery contract: {required}"
        );
    }
    assert!(SIGNER_PROTOCOL
        .contains("pub const ENTITLEMENT_SIGNER_RECEIPT_PATH: &str = \"/v1/signer/receipts\""));
    assert!(SIGNER_BINARY.contains("/v1/signer/receipts/:request_id"));
    assert!(SIGNER_BINARY.contains("get(get_signing_receipt)"));
    assert!(SIGNER_BINARY.contains("entitlement.nonce != request.request_id"));
    assert!(!SIGNER_BINARY.contains("entitlement.intent_id != request.request_id"));
}

#[test]
fn stale_or_expired_workers_cannot_mutate_another_lease() {
    let sql = normalized(WORKER_MIGRATION);
    let lease_fence = "state = 'leased' and lease_owner = p_owner and lease_generation = p_lease_generation and lease_expires_at > pg_catalog.clock_timestamp()";
    assert!(
        sql.matches(lease_fence).count() >= 5,
        "authorization/attempt/completion/retry/dead-letter must all share the live lease fence"
    );
}

#[test]
fn account_serialization_preserves_unrelated_progress() {
    let sql = normalized(WORKER_MIGRATION);
    for required in [
        "nullif(p_intent_json #>> '{actors,0,account_id}', '')",
        "'campaign:' || p_campaign_id",
        "pg_catalog.pg_try_advisory_xact_lock",
        "pg_catalog.hashtextextended",
        "blocker.state = 'succeeded'",
        "blocker.lease_expires_at > pg_catalog.clock_timestamp()",
        "limit 16",
    ] {
        assert!(
            sql.contains(required),
            "missing serialization control: {required}"
        );
    }
}

#[test]
fn durable_identity_fields_cannot_be_rebound() {
    let sql = normalized(WORKER_MIGRATION);
    for required in [
        "settlement match, campaign and intent identity fields are immutable",
        "remote_request_id does not match durable settlement identity",
        "before update of match_id, campaign_id, intent_id, remote_request_id",
        "errcode = '23514'",
    ] {
        assert!(
            sql.contains(required),
            "missing immutable identity fence: {required}"
        );
    }
}

#[test]
fn both_campaign_fences_are_revalidated_before_any_apply_commit() {
    for required in [
        "campaign_fences_json(&campaigns) != expected_campaign_fences",
        "terminal identity hash changed after capture",
        "campaign_revision = $6",
        "state_hash = $7",
        "failed exact revision/state-hash CAS",
        "finalize_match_in_transaction(&mut transaction, match_id).await",
    ] {
        assert!(
            WORKER_LEGACY_SOURCE.contains(required),
            "worker lost exact apply invariant: {required}"
        );
    }
}

#[test]
fn external_requests_are_only_in_the_execute_phase() {
    let capture_start = WORKER_LEGACY_SOURCE.find("async fn capture_match").unwrap();
    let capture_end = WORKER_LEGACY_SOURCE[capture_start..]
        .find("async fn load_terminal_identity")
        .map(|offset| capture_start + offset)
        .unwrap();
    let capture = &WORKER_LEGACY_SOURCE[capture_start..capture_end];
    assert!(!capture.contains("authorize_settlement_intent"));
    assert!(!capture.contains("submit_authorized_settlement_intent"));

    let apply_start = WORKER_LEGACY_SOURCE.find("async fn apply_capture").unwrap();
    let apply_end = WORKER_LEGACY_SOURCE[apply_start..]
        .find("struct CaptureJobRow")
        .map(|offset| apply_start + offset)
        .unwrap();
    let apply = &WORKER_LEGACY_SOURCE[apply_start..apply_end];
    assert!(!apply.contains("authorize_settlement_intent"));
    assert!(!apply.contains("submit_authorized_settlement_intent"));

    let execute_start = WORKER_LEGACY_SOURCE
        .find("async fn process_claimed_job")
        .unwrap();
    let execute_end = WORKER_LEGACY_SOURCE[execute_start..]
        .find("async fn handle_external_failure")
        .map(|offset| execute_start + offset)
        .unwrap();
    let execute = &WORKER_LEGACY_SOURCE[execute_start..execute_end];
    assert!(!execute.contains(".begin()"));
    assert!(execute.contains("authorize_settlement_intent"));
    assert!(execute.contains("submit_authorized_settlement_intent"));
}

#[test]
fn synchronous_game_server_backend_remains_fail_closed() {
    assert!(CEX_SOURCE.contains("SETTLEMENT_OUTBOX_REQUIRED"));
    assert!(CEX_SOURCE.contains("Err(SETTLEMENT_OUTBOX_REQUIRED.to_string())"));
    assert!(!CEX_SOURCE.contains("blocking_client"));
    assert!(!CEX_SOURCE.contains("reqwest::blocking"));
    assert!(WORKER_BINARY.contains("settlement_worker::run(config).await"));
}
