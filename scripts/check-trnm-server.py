#!/usr/bin/env python3
"""Validate the first-party Rust server vertical-slice source candidate."""
from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER_ROOT = ROOT / "crates/trnm-persistence-pg/src/bin"
MODULE_ROOT = SERVER_ROOT / "trnm_server"
REQUIRED_FILES = {
    SERVER_ROOT / "trnm-server.rs",
    MODULE_ROOT / "mod.rs",
    MODULE_ROOT / "app.rs",
    MODULE_ROOT / "codec.rs",
    MODULE_ROOT / "config.rs",
    MODULE_ROOT / "error.rs",
    MODULE_ROOT / "http.rs",
    MODULE_ROOT / "json.rs",
    MODULE_ROOT / "retry.rs",
    MODULE_ROOT / "schema.rs",
    MODULE_ROOT / "server.rs",
    MODULE_ROOT / "websocket.rs",
}
REQUIRED_TESTS = {
    "fixed_hex_round_trip_is_lowercase_and_exact_width",
    "duplicate_nested_escaped_and_noncanonical_numbers_fail_closed",
    "default_candidate_config_is_loopback_bounded_and_redacted",
    "accidental_public_bind_and_implicit_plaintext_database_fail_closed",
    "duplicate_chunked_pipelined_and_noncanonical_lengths_fail_closed",
    "both_authoritative_profiles_embed_the_ten_table_chain",
    "health_ready_bootstrap_and_commit_form_one_in_process_vertical_slice",
    "internal_domain_reason_is_never_exposed",
    "authenticated_drain_stops_new_mutations",
    "unauthenticated_mutations_fail_closed",
    "admin_token_comparison_rejects_a_256_byte_length_delta",
    "safe_immediate_failure_is_retried_within_attempt_budget",
    "never_and_resync_errors_are_not_retried",
    "exhausted_retry_returns_stable_unavailable_error",
    "elapsed_budget_prevents_an_additional_attempt",
    "rfc6455_handshake_accept_matches_the_published_vector",
    "malformed_key_version_and_subprotocol_fail_closed",
    "masked_single_text_frame_is_unmasked_exactly",
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
        "postgres": "=0.19.14",
        "trnm-contracts": {"path": "../trnm-contracts"},
    }
    if manifest.get("dependencies") != expected_dependencies:
        fail("server candidate changed the reviewed persistence dependency boundary")

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
        "crates/trnm-persistence-pg/src/bin/trnm-server.rs": [
            "Command::CheckConfig",
            "Command::Migrate",
            "Command::Serve",
            "open_verified_repository",
        ],
        "crates/trnm-persistence-pg/src/bin/trnm_server/mod.rs": [
            "pub(crate) mod websocket;",
        ],
        "crates/trnm-persistence-pg/src/bin/trnm_server/config.rs": [
            "127.0.0.1:7350",
            "TRNM_SERVER_ALLOW_NON_LOOPBACK",
            "TRNM_SERVER_ALLOW_PLAINTEXT_DATABASE",
            '"<redacted>"',
        ],
        "crates/trnm-persistence-pg/src/bin/trnm_server/schema.rs": [
            "migrations/postgresql/0001_foundation_up.sql",
            "migrations/cockroachdb/0001_foundation_up.sql",
            "trnm_schema_metadata",
            "REQUIRED_TABLES: [&str; 10]",
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
            "acknowledgement-after-commit fence",
            "CommitOutcome::Duplicate",
            "if !self.authorized(request)",
        ],
        "crates/trnm-persistence-pg/src/bin/trnm_server/retry.rs": [
            "max_attempts: 3",
            "total_budget: Duration::from_secs(2)",
            "RetryClass::SafeImmediate",
            "RetryClass::SafeBackoff",
            "database_retry_budget_exhausted",
            "if attempt > 0 && started.elapsed() >= policy.total_budget",
        ],
        "crates/trnm-persistence-pg/src/bin/trnm_server/websocket.rs": [
            "/v1/realtime",
            "trnm.json.v1",
            "Sec-WebSocket-Accept",
            "websocket_client_frame_unmasked",
            "websocket_length_not_canonical",
            '"POST",\n        "/v1/authority/commit"',
        ],
        "crates/trnm-persistence-pg/src/bin/trnm_server/server.rs": [
            "RetryingRepository::new",
            "RetryPolicy::candidate_default",
            "websocket::is_route",
            "websocket::serve_once",
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
    if test_count < 31:
        fail(f"expected at least 31 server source tests, got {test_count}")

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
    if status.get("stage") != "http-websocket-database-vertical-source-candidate":
        fail("unexpected server status stage")
    claims = status.get("claims", {})
    forbidden_positive_claims = [
        "remote_verified",
        "live_database_verified",
        "http_wire_compatible",
        "websocket_wire_compatible",
        "grpc_implemented",
        "websocket_protobuf_implemented",
        "sg4_complete",
        "production_ready",
        "public_online",
        "nakama_replaced",
    ]
    if any(claims.get(field) for field in forbidden_positive_claims):
        fail("server status overclaims execution, compatibility or production")
    if claims.get("source_candidate") is not True:
        fail("server source candidate claim missing")
    if claims.get("bounded_retry_source_candidate") is not True:
        fail("bounded retry source candidate claim missing")
    if claims.get("websocket_json_source_candidate") is not True:
        fail("WebSocket JSON source candidate claim missing")

    print(
        json.dumps(
            {
                "status": "trnm-server-source-contract-passed",
                "source_files": len(sources),
                "source_tests": test_count,
                "source_candidate": True,
                "bounded_retry_source_candidate": True,
                "websocket_json_source_candidate": True,
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
