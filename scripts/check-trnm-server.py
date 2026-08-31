#!/usr/bin/env python3
"""Validate the first-party Rust server vertical-slice source candidate."""
from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PERSISTENCE_ROOT = ROOT / "crates/trnm-persistence-pg/src"
SERVER_ROOT = PERSISTENCE_ROOT / "bin"
MODULE_ROOT = SERVER_ROOT / "trnm_server"
REQUIRED_FILES = {
    PERSISTENCE_ROOT / "pool.rs",
    PERSISTENCE_ROOT / "session.rs",
    SERVER_ROOT / "trnm-server.rs",
    MODULE_ROOT / "mod.rs",
    MODULE_ROOT / "app.rs",
    PERSISTENCE_ROOT / "auth.rs",
    MODULE_ROOT / "codec.rs",
    MODULE_ROOT / "config.rs",
    MODULE_ROOT / "error.rs",
    MODULE_ROOT / "grpc.rs",
    MODULE_ROOT / "http.rs",
    MODULE_ROOT / "json.rs",
    MODULE_ROOT / "pool.rs",
    MODULE_ROOT / "retry.rs",
    MODULE_ROOT / "schema.rs",
    MODULE_ROOT / "server.rs",
    MODULE_ROOT / "session_api.rs",
    MODULE_ROOT / "websocket.rs",
}
REQUIRED_TESTS = {
    "fixed_hex_round_trip_is_lowercase_and_exact_width",
    "duplicate_nested_escaped_and_noncanonical_numbers_fail_closed",
    "default_candidate_config_is_loopback_bounded_and_redacted",
    "accidental_public_bind_and_implicit_plaintext_database_fail_closed",
    "verify_full_tls_is_secure_by_default_and_material_is_paired",
    "pool_and_timeout_bounds_fail_closed",
    "duplicate_chunked_pipelined_and_noncanonical_lengths_fail_closed",
    "both_authoritative_profiles_embed_the_ten_table_chain",
    "health_ready_bootstrap_and_commit_form_one_in_process_vertical_slice",
    "internal_domain_reason_is_never_exposed",
    "authenticated_drain_stops_new_mutations",
    "shared_drain_fences_new_mutations_across_app_instances",
    "admitted_mutation_can_complete_after_drain_begins",
    "unauthenticated_mutations_fail_closed",
    "admin_token_comparison_rejects_a_256_byte_length_delta",
    "safe_immediate_failure_is_retried_within_attempt_budget",
    "never_and_resync_errors_are_not_retried",
    "exhausted_retry_returns_stable_unavailable_error",
    "elapsed_budget_prevents_an_additional_attempt",
    "jitter_remains_inside_half_to_full_backoff",
    "create_and_rotation_validation_fail_closed",
    "persisted_revocation_reason_mapping_is_exact",
    "generic_session_failure_does_not_disclose_identity_state",
    "strict_epoch_access_token_yields_session_principal",
    "malformed_tampered_and_incomplete_access_tokens_fail_closed",
    "refresh_credential_is_bounded_id_prefixed_and_hashed",
    "verifier_debug_redacts_key_material",
    "session_auth_is_explicit_bounded_and_redacted",
    "session_auth_rejects_partial_or_noncanonical_key_material",
    "configured_access_token_is_bound_to_persisted_family",
    "refresh_rotation_hashes_credentials_and_advances_generation",
    "refresh_replay_revokes_family_without_disclosing_state",
    "logout_revokes_persisted_family",
    "disabled_session_api_fails_closed_without_parsing_credentials",
    "default_pool_policy_is_bounded_and_valid",
    "invalid_pool_policy_fails_closed",
    "tls_identity_requires_cert_and_key_pair",
    "tls_debug_never_exposes_private_key_material",
    "wrapper_is_cloneable_without_exposing_database_url",
    "rfc6455_handshake_accept_matches_the_published_vector",
    "malformed_key_version_and_subprotocol_fail_closed",
    "masked_single_text_frame_is_unmasked_exactly",
    "protobuf_subprotocol_selects_binary_encoding",
    "persistent_reader_keeps_frame_boundaries_and_control_frames",
    "protobuf_response_envelope_preserves_status_and_json_body",
    "message_budget_is_nonzero_and_hard_bounded",
    "shared_codec_rejects_encoding_mismatch",
    "websocket_subprotocols_are_case_sensitive_and_echo_exact_offer",
    "duplicate_websocket_subprotocol_offers_fail_closed",
    "drain_ack_on_second_worker_fences_existing_websocket_mutation",
    "drain_ack_closes_control_only_websocket",
    "drain_ack_closes_idle_websocket_at_read_deadline",
    "grpc_bind_is_optional_distinct_and_public_bind_requires_opt_in",
    "official_healthcheck_method_path_is_exact",
    "generated_service_returns_an_empty_response",
    "generated_client_reaches_the_http2_healthcheck_path",
    "grpc_worker_returned_error_signals_shared_failure_fence",
    "grpc_worker_panic_signals_shared_failure_fence",
    "unmasked_fragmented_and_oversized_frames_are_rejected",
    "server_text_and_close_frames_are_unmasked_and_canonical",
    "sha1_and_base64_helpers_match_known_vectors",
}
FORBIDDEN_SOURCE = (
    "database/schema/v2",
    "todo!",
    "unimplemented!",
    "unsafe {",
)


def fail(message: str) -> None:
    raise SystemExit(f"trnm-server contract failed: {message}")


def main() -> int:
    missing = sorted(str(path.relative_to(ROOT)) for path in REQUIRED_FILES if not path.is_file())
    if missing:
        fail("missing files: " + ", ".join(missing))

    manifest = tomllib.loads(
        (ROOT / "crates/trnm-persistence-pg/Cargo.toml").read_text(encoding="utf-8")
    )
    expected_dependencies = {
        "native-tls": "=0.2.18",
        "postgres": "=0.19.14",
        "postgres-native-tls": "=0.5.3",
        "prost": "=0.14.3",
        "r2d2": "=0.8.10",
        "r2d2_postgres": "=0.18.2",
        "tokio": {"version": "=1.53.1", "features": ["rt", "time"]},
        "tonic": {"version": "=0.14.5", "features": ["transport"]},
        "tonic-prost": "=0.14.5",
        "trnm-contracts": {"path": "../trnm-contracts"},
        "trnm-realtime-wire": {"path": "../trnm-realtime-wire"},
        "trnm-session-core": {"path": "../trnm-session-core"},
        "trnm-token-jwt-adapter": {"path": "../trnm-token-jwt-adapter"},
    }
    if manifest.get("dependencies") != expected_dependencies:
        fail("server candidate changed the reviewed persistence dependency boundary")
    expected_build_dependencies = {
        "prost-build": "=0.14.3",
        "prost-types": "=0.14.3",
        "protoc-bin-vendored": "=3.2.0",
        "tonic-build": "=0.14.5",
        "tonic-prost-build": "=0.14.5",
    }
    if manifest.get("build-dependencies") != expected_build_dependencies:
        fail("server candidate changed the reviewed protobuf build dependency boundary")

    sources = {
        path.relative_to(ROOT): path.read_text(encoding="utf-8")
        for path in sorted(REQUIRED_FILES)
    }
    combined = "\n".join(sources.values())
    if "#![forbid(unsafe_code)]" not in sources[Path("crates/trnm-persistence-pg/src/bin/trnm-server.rs")]:
        fail("binary root does not forbid unsafe code")
    for marker in FORBIDDEN_SOURCE:
        if marker in combined:
            fail(f"forbidden source marker: {marker}")

    required_markers = {
        "crates/trnm-persistence-pg/src/pool.rs": [
            "pub struct PgPoolConfig",
            "pub struct PgTlsConfig",
            "pub struct PgPoolSnapshot",
            "pub struct PgPool",
            "get_timeout(self.policy.acquire_timeout)",
            ".test_on_check_out(true)",
            "Certificate::from_pem",
            "Identity::from_pkcs8",
            "Protocol::Tlsv12",
            "SET statement_timeout",
            "SET lock_timeout",
            '"<redacted>"',
        ],
        "crates/trnm-persistence-pg/src/session.rs": [
            "pub struct CreateSessionFamily",
            "pub enum RefreshRotationOutcome",
            "IsolationLevel::Serializable",
            "FOR UPDATE",
            "refresh_compare_and_swap_failed",
            "revoked_reason = 2",
            "RevocationReason::RefreshReplay",
        ],
        "crates/trnm-persistence-pg/src/bin/trnm-server.rs": [
            "Command::CheckConfig",
            "Command::Migrate",
            "Command::Serve",
            "open_verified_repository",
        ],
        "crates/trnm-persistence-pg/src/auth.rs": [
            "pub struct AccessTokenVerifier",
            "allow_legacy_without_key_id: false",
            "max_lifetime_seconds: Some(15 * 60)",
            "claim_string(claims, \"sid\")",
            "claim_unsigned(claims, \"sgn\")",
            "sha256_digest(value.as_bytes())",
            "\"session_authentication_failed\"",
        ],
        "crates/trnm-persistence-pg/src/bin/trnm_server/mod.rs": [
            "pub(crate) mod auth;",
            "pub(crate) mod pool;",
            "pub(crate) mod session_api;",
            "pub(crate) mod websocket;",
        ],
        "crates/trnm-persistence-pg/src/bin/trnm_server/config.rs": [
            "127.0.0.1:7350",
            "TRNM_SERVER_ALLOW_NON_LOOPBACK",
            "TRNM_SERVER_GRPC_BIND",
            "TRNM_SERVER_ALLOW_PLAINTEXT_DATABASE",
            "TRNM_SERVER_DATABASE_TLS_MODE",
            'Some("verify-full")',
            "TRNM_SERVER_DATABASE_POOL_MAX_SIZE",
            "TRNM_SERVER_DATABASE_POOL_ACQUIRE_TIMEOUT_MS",
            "TRNM_SERVER_DATABASE_STATEMENT_TIMEOUT_MS",
            "TRNM_SERVER_DATABASE_LOCK_TIMEOUT_MS",
            "pub struct SessionAuthConfig",
            "TRNM_SERVER_SESSION_AUTH_ENABLED",
            "TRNM_SERVER_SESSION_AUTH_KEY_HEX",
            '"<redacted>"',
        ],
        "crates/trnm-persistence-pg/src/bin/trnm_server/pool.rs": [
            "pub struct PooledRepository",
            "self.pool.acquire()?",
            "pool_acquire_failures",
        ],
        "crates/trnm-persistence-pg/src/bin/trnm_server/schema.rs": [
            "migrations/postgresql/0001_foundation_up.sql",
            "migrations/cockroachdb/0001_foundation_up.sql",
            "trnm_schema_metadata",
            "REQUIRED_TABLES: [&str; 10]",
            "PgPool::connect_plain",
            "PgPool::connect_tls",
            "PgTlsConfig::new",
        ],
        "crates/trnm-persistence-pg/src/bin/trnm_server/grpc.rs": [
            "/nakama.api.Nakama/Healthcheck",
            "NakamaServer::new",
            "serve_with_shutdown",
            "worker_failed.store(true",
            "catch_unwind",
            "draining.begin()",
        ],
        "crates/trnm-persistence-pg/src/bin/trnm_server/http.rs": [
            "http_transfer_encoding_not_supported",
            "http_pipelining_not_supported",
            "http_duplicate_header",
            "Connection: close",
        ],
        "crates/trnm-persistence-pg/src/bin/trnm_server/app.rs": [
            "/healthz",
            "/readyz",
            "/metrics",
            "/-/drain",
            "/v1/authority/bootstrap",
            "/v1/authority/commit",
            "/v1/session/me",
            "/v1/session/refresh",
            "/v1/session/logout",
            "with_access_token_verifier",
            "acknowledgement-after-commit fence",
            "CommitOutcome::Duplicate",
            "if !self.authorized(request)",
            "trnm_server_database_pool_acquire_failures_total",
            "trnm_server_database_retry_exhausted_total",
            "pub(crate) struct SharedDrain",
            "try_admit",
            "admit_realtime_dispatch",
            "handle_admitted",
            "is_mutating_request",
        ],
        "crates/trnm-persistence-pg/src/bin/trnm_server/session_api.rs": [
            "pub struct SessionApi",
            "verify_access_session",
            "rotate_refresh_token",
            "revoke_session_family",
            "RefreshRotationOutcome::ReplayRevoked",
            "session_authentication_not_configured",
        ],
        "crates/trnm-persistence-pg/src/bin/trnm_server/retry.rs": [
            "max_attempts: 3",
            "total_budget: Duration::from_secs(2)",
            "RetryClass::SafeImmediate",
            "RetryClass::SafeBackoff",
            "database_retry_budget_exhausted",
            "if attempt > 0 && started.elapsed() >= policy.total_budget",
            "jittered_backoff(backoff)",
            "base_nanos / 2",
        ],
        "crates/trnm-persistence-pg/src/bin/trnm_server/websocket.rs": [
            "/v1/realtime",
            "trnm.json.v1",
            "trnm.protobuf.v1",
            "Sec-WebSocket-Accept",
            "MAX_MESSAGES_PER_CONNECTION",
            "read_client_frame_exact",
            "decode_authority_command",
            "encode_authority_response",
            "Opcode::Ping",
            "Opcode::Pong",
            'Request::new("POST", "/v1/authority/commit"',
            "BTreeSet",
            "app.should_stop()",
        ],
        "crates/trnm-persistence-pg/src/bin/trnm_server/server.rs": [
            "RetryingRepository::new",
            "RetryPolicy::candidate_default",
            "config.session_auth",
            "with_access_token_verifier",
            "websocket::is_route",
            "websocket::serve_once",
            "grpc::spawn",
            "grpc::join",
            "with_shared_state",
        ],
    }
    for relative, markers in required_markers.items():
        text = sources[Path(relative)]
        for marker in markers:
            if marker not in text:
                fail(f"{relative}: missing marker {marker!r}")

    test_names = set(re.findall(r"fn\s+([a-z0-9_]+)\s*\(\)\s*\{", combined))
    missing_tests = sorted(REQUIRED_TESTS - test_names)
    if missing_tests:
        fail(f"missing tests: {missing_tests}")
    test_count = combined.count("#[test]")
    if test_count < 66:
        fail(f"expected at least 66 server/session/pool/websocket/grpc source tests, got {test_count}")

    workflow = (
        ROOT / ".github/workflows/trillionnium-game-merge-gate.yml"
    ).read_text(encoding="utf-8")
    if "cargo test --workspace --all-targets --locked" not in workflow:
        fail("aggregate gate does not compile/test the binary target")
    if "cargo clippy --workspace --all-targets --locked -- -D warnings" not in workflow:
        fail("aggregate gate does not strictly lint the binary target")
    if "python3 scripts/check-trnm-server.py" not in workflow:
        fail("aggregate gate does not execute the server source contract")

    authority = json.loads(
        (ROOT / "docs/development/RUST_PACKAGE_AUTHORITY.json").read_text(encoding="utf-8")
    )
    server = authority.get("server_binary_authority", {})
    if server.get("name") != "trnm-server":
        fail("Rust package authority does not name trnm-server")
    if server.get("manifest") != "crates/trnm-persistence-pg/Cargo.toml":
        fail("Rust package authority points to another server manifest")
    if server.get("source") != "crates/trnm-persistence-pg/src/bin/trnm-server.rs":
        fail("Rust package authority points to another server source")

    status = json.loads(
        (ROOT / "docs/status/TRNM_SERVER_STATUS.json").read_text(encoding="utf-8")
    )
    if status.get("stage") != "http-websocket-session-database-vertical-source-candidate":
        fail("unexpected server status stage")
    claims = status.get("claims", {})
    forbidden_positive_claims = [
        "remote_verified",
        "live_database_verified",
        "http_wire_compatible",
        "websocket_wire_compatible",
        "grpc_implemented",
        "websocket_protobuf_implemented",
        "session_integrated",
        "request_cancellation_implemented",
        "certificate_rotation_verified",
        "outbox_delivery_verified",
        "sg4_complete",
        "production_ready",
        "public_online",
        "nakama_replaced",
    ]
    if any(claims.get(field) for field in forbidden_positive_claims):
        fail("server status overclaims execution, compatibility or production")
    required_source_claims = [
        "source_candidate",
        "bounded_retry_source_candidate",
        "websocket_json_source_candidate",
        "websocket_persistent_source_candidate",
        "websocket_protobuf_envelope_source_candidate",
        "bounded_pool_source_candidate",
        "tls_verify_full_source_candidate",
        "statement_timeout_source_candidate",
        "retry_jitter_source_candidate",
        "access_token_verifier_source_candidate",
        "refresh_family_repository_source_candidate",
        "session_http_source_candidate",
        "grpc_healthcheck_source_candidate",
    ]
    if any(claims.get(field) is not True for field in required_source_claims):
        fail("server operational source-candidate claim missing")

    print(
        json.dumps(
            {
                "status": "trnm-server-source-contract-passed",
                "source_files": len(sources),
                "source_tests": test_count,
                "source_candidate": True,
                "bounded_retry_source_candidate": True,
                "websocket_json_source_candidate": True,
                "websocket_persistent_source_candidate": True,
                "websocket_protobuf_envelope_source_candidate": True,
                "bounded_pool_source_candidate": True,
                "tls_verify_full_source_candidate": True,
                "statement_timeout_source_candidate": True,
                "retry_jitter_source_candidate": True,
                "access_token_verifier_source_candidate": True,
                "refresh_family_repository_source_candidate": True,
                "session_http_source_candidate": True,
                "cargo_executed_here": False,
                "live_database_executed_here": False,
                "compatibility_credit": False,
                "sg4_complete": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
