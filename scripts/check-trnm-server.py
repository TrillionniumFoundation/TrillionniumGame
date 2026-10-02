#!/usr/bin/env python3
"""Validate the first-party Rust server vertical-slice source candidate."""
from __future__ import annotations

import ast
import importlib.util
import json
from copy import deepcopy
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PERSISTENCE_ROOT = ROOT / "crates/trnm-persistence-pg/src"
POOL_ROOT = PERSISTENCE_ROOT / "pool.rs"
POOL_PARTS = tuple(
    PERSISTENCE_ROOT / "pool_parts" / name
    for name in ("base.rs", "cancellation.rs", "pool.rs", "tests.rs")
)
SERVER_ROOT = PERSISTENCE_ROOT / "bin"
MODULE_ROOT = SERVER_ROOT / "trnm_server"
AUTHORITY_STORAGE_ROOT = ROOT / "crates/trnm-persistence-pg/tests/authority_storage.rs"
AUTHORITY_STORAGE_PARTS = tuple(
    AUTHORITY_STORAGE_ROOT.parent / "authority_storage_parts" / name
    for name in ("00_helpers.rs", "01_authority.rs", "02_storage_batch.rs", "03_storage_list.rs")
)
STORAGE_PARTS = tuple(
    PERSISTENCE_ROOT / "storage_parts" / name
    for name in (
        "00_prelude.rs", "01_repository.rs", "02_list_helpers.rs",
        "03_write.rs", "04_delete_authorize.rs", "05_decode_errors.rs",
    )
)
STORAGE_LIVE_HARNESS = ROOT / "scripts/ci-trnm-server-live.sh"
LIVE_FAILURE_HELPER = ROOT / "scripts/print-server-live-failure.py"
REQUIRED_FILES = {
    POOL_ROOT,
    *POOL_PARTS,
    PERSISTENCE_ROOT / "session.rs",
    PERSISTENCE_ROOT / "storage.rs",
    *STORAGE_PARTS,
    AUTHORITY_STORAGE_ROOT,
    *AUTHORITY_STORAGE_PARTS,
    PERSISTENCE_ROOT / "auth.rs",
    PERSISTENCE_ROOT / "authority.rs",
    SERVER_ROOT / "trnm-server.rs",
    MODULE_ROOT / "mod.rs",
    MODULE_ROOT / "app.rs",
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
    ROOT / "crates/trnm-server/src/runtime/storage_api.rs",
    ROOT / "crates/trnm-server/src/runtime/storage_api_tests.rs",
    ROOT / "crates/trnm-server/src/runtime/storage_list_api.rs",
    ROOT / "crates/trnm-server/src/runtime/storage_list_query.rs",
    ROOT / "crates/trnm-server/src/runtime/storage_list_api_tests.rs",
    ROOT / "crates/trnm-server/src/runtime/storage_cursor.rs",
    ROOT / "crates/trnm-server/src/runtime/storage_cursor_tests.rs",
    ROOT / "crates/trnm-server/src/runtime/error.rs",
    ROOT / "crates/trnm-server/src/runtime/schema.rs",
    ROOT / "crates/trnm-storage-core/src/lib.rs",
    STORAGE_LIVE_HARNESS,
    LIVE_FAILURE_HELPER,
    PERSISTENCE_ROOT / "schema.rs",
    PERSISTENCE_ROOT / "storage_metadata.rs",
    SERVER_ROOT / "trnm-schema.rs",
    ROOT / "crates/trnm-persistence-pg/tests/storage_timestamps.rs",
    ROOT / "crates/trnm-persistence-pg/tests/schema_upgrade.rs",
}
REQUIRED_TESTS = {
    "migration_diagnostic_unknown_reasons_and_other_variants_do_not_leak_secrets",
    "migration_diagnostic_never_reconstructs_sqlstate_from_domain_reason",
    "migration_sqlstate_projection_rejects_non_code_or_unsafe_driver_values",
    "migration_diagnostic_profile_phase_and_output_are_bounded",
    "migration_diagnostic_success_preserves_value_without_output",
    "migration_diagnostic_sink_failure_preserves_original_error",
    "canonical_storage_api_live_database",
    "opaque_write_conditions_reach_storage_occ_and_acl_after_authentication",
    "opaque_delete_conditions_including_star_are_literal_and_reach_storage",
    "absent_null_and_empty_conditions_keep_unconditional_write_and_delete_semantics",
    "storage_timestamp_json_matches_protobuf_range_precision_and_pre_epoch",
    "new_storage_ack_requires_both_times_and_historical_unknown_is_not_fabricated",
    "storage_ack_rejects_missing_effective_update_time_and_unequal_insert_pair",
    "read_projects_valid_fraction_to_seconds_omits_unknown_and_rejects_invalid_times",
    "storage_list_projects_fraction_to_seconds_omits_unknown_and_rejects_invalid_times",
    "storage_timestamps_database_clock_no_op_and_atomicity",
    "authoritative_fresh_repeat_and_readonly_verification",
    "authoritative_v1_preserves_history_and_observes_actual_legacy_writer_revocation",
    "authoritative_populated_unbound_and_catalog_drift_fail_closed",
    "authoritative_declared_partial_prefix_resumes_and_malformed_prefixes_reject",
    "authoritative_existing_empty_v1_requires_a_real_unprivileged_writer_barrier",
    "authoritative_inherited_storage_privileges_are_not_a_writer_barrier",
    "nakama_client_listing_modes_cursors_and_integrity_are_database_projected",
    "nakama_row_identifier_projection_keeps_unicode_schema_bounds_separate",
    "storage_list_default_page_and_original_gob_continuation_are_bounded",
    "storage_list_offset_never_supplies_principal_owner_or_collection_authority",
    "storage_list_validation_rejects_before_repository_and_redacts_failures",
    "storage_list_response_defends_against_acl_scope_and_integrity_violations",
    "storage_list_visibility_and_equal_literal_cursor_guard_match_projection",
    "storage_list_dynamic_route_templates_are_used_by_live_matcher",
    "list_query_defaults_wrappers_aliases_and_path_binding",
    "list_query_path_and_query_escaping_follow_legacy_gateway",
    "list_query_rejects_known_duplicates_and_invalid_decimal_limits",
    "unknown_wrapper_fields_only_allocate_and_preserve_a_known_limit",
    "linebreak_query_names_do_not_match_the_gateway_bracket_regexp",
    "routing_shape_unescapes_static_segments_and_slashes_once",
    "frozen_go_query_observations_and_named_candidate_residuals",
    "go_vectors_decode_and_encoder_matches_fresh_process",
    "all_framed_prefix_truncations_fail_without_panicking",
    "unknown_interface_is_an_explicit_subset_limit_even_when_omitted",
    "bounded_keys_and_encoded_cursor_text_fail_closed",
    "type_message_and_descriptor_field_budgets_fail_closed",
    "nested_unknown_type_and_container_work_budgets_fail_closed",
    "invalid_gob_integer_widths_and_partial_bytes_fail_closed",
    "mutation_corpus_is_bounded_and_never_panics",
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
    "cancellation_metrics_are_exported_without_query_or_credential_labels",
    "safe_immediate_failure_is_retried_within_attempt_budget",
    "never_and_resync_errors_are_not_retried",
    "exhausted_retry_returns_stable_unavailable_error",
    "elapsed_budget_prevents_an_additional_attempt",
    "successful_result_after_budget_is_rejected",
    "each_attempt_receives_only_the_remaining_total_budget",
    "jitter_remains_inside_half_to_full_backoff",
    "authority_takeover_fences_stale_generation",
    "storage_occ_acl_and_batch_rollback_are_transactional",
    "blind_storage_no_op_preserves_timestamp_after_acl_occ_and_integrity_checks",
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
    "completed_deadline_guard_does_not_cancel",
    "deadline_and_shutdown_requests_are_single_delivery",
    "cancellation_id_exhaustion_is_atomic_and_fail_closed",
    "sub_millisecond_budget_fails_closed",
    "wrapper_is_cloneable_and_supports_budget_and_shutdown_contracts",
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


def require_markers(label: str, text: str, markers: tuple[str, ...]) -> None:
    for marker in markers:
        if marker not in text:
            fail(f"{label}: missing marker {marker!r}")


def validate_live_failure_diagnostics(harness: str, helper: str) -> None:
    """Bind the bounded failure path without treating diagnostics as success."""
    def bound_value(node: ast.expr) -> int:
        if isinstance(node, ast.Constant) and type(node.value) is int:
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            return bound_value(node.left) * bound_value(node.right)
        raise ValueError("resource bound is not a constant integer product")

    try:
        syntax = ast.parse(helper)
        constants = {
            node.targets[0].id: bound_value(node.value)
            for node in syntax.body
            if isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id.startswith("MAX_")
        }
    except (SyntaxError, ValueError, TypeError):
        fail("server live failure helper has invalid resource declarations")
    if constants != {
        "MAX_LOGS": 2, "MAX_READ_BYTES": 64 * 1024,
        "MAX_LINES": 80, "MAX_OUTPUT_BYTES": 1024 * 1024,
    }:
        fail("server live failure diagnostics changed their resource bounds")
    require_markers("server live failure helper", helper, (
        "secret_literals(dict(os.environ))", "urlsplit(value).password",
        "re.escape(literal) for literal in literals", "pattern.sub(REDACTED, text)",
        "URI.sub(", "CREDENTIAL.sub(",
        "os.O_NONBLOCK", 'getattr(os, "O_NOFOLLOW", 0)',
        "stat.S_ISREG(metadata.st_mode)", "os.lseek(fd, offset, os.SEEK_SET)",
        "os.read(fd, MAX_READ_BYTES)", 'data.partition(b"\\n")[2]',
        'data.rpartition(b"\\n")[0]', '"partial_last_line_discarded"',
        'data.decode("utf-8", errors="replace")', "lines[-MAX_LINES:]",
        "len(args.logs) > MAX_LOGS", 'report["logs"] =',
        '"trillionnium.server-live-failure.v1"', "ensure_ascii=True",
        '.replace("::", "\\\\u003a\\\\u003a")', "print(output, file=sys.stderr)",
    ))
    cleanup = re.search(r"^cleanup\(\) \{\n(.*?)^\}", harness, re.MULTILINE | re.DOTALL)
    if cleanup is None:
        fail("server live harness has no failure-preserving cleanup")
    body = cleanup.group(1)
    guards = (
        "local status=$?", "trap - EXIT INT TERM", "if (( status != 0 )); then",
        'report_failure "$status" || true', 'exit "$status"',
    )
    positions = [body.find(guard) for guard in guards]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        fail("server live cleanup must preserve the original failure even if diagnostics fail")
    if body.strip().splitlines()[0].strip() != "local status=$?":
        fail("server live cleanup must capture the failure before executing another command")
    require_markers("server live harness diagnostic traps", harness, (
        "trap cleanup EXIT", "trap 'exit 130' INT", "trap 'exit 143' TERM",
        'test "${#diagnostic_logs[@]}" -le 2',
    ))
    reporter = re.search(r"^report_failure\(\) \{\n(.*?)^\}", harness, re.MULTILINE | re.DOTALL)
    if reporter is None:
        fail("server live harness has no bounded failure reporter")
    require_markers("server live failure reporter", reporter.group(1), (
        'TRNM_SERVER_ADMIN_TOKEN="$admin_token"',
        'TRNM_SERVER_DATABASE_URL="${database_url:-}"',
        'python3 "$root/scripts/print-server-live-failure.py"',
        '--profile "$profile" --stage "$stage" --status "$status" -- "${diagnostic_logs[@]}"',
    ))
    commands = [line.strip() for line in harness.splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
    ordered = (
        'begin_stage check-config "$evidence/check-config.log"',
        '"$binary" check-config > "$evidence/check-config.log" 2>&1',
        'begin_stage check-config-output "$evidence/check-config.log"',
        "grep -qx 'trnm-server configuration valid' \"$evidence/check-config.log\"",
        'begin_stage credential-check "$evidence/check-config.log"',
        "if grep -Fq 'trnm_live_password' \"$evidence/check-config.log\"; then",
        'if grep -Fq "$admin_token" "$evidence/check-config.log"; then',
        'begin_stage migrate "$evidence/check-config.log" "$evidence/migrate.log"',
        '"$binary" migrate > "$evidence/migrate.log" 2>&1',
        'begin_stage migrate-output "$evidence/migrate.log"',
        "grep -qx 'trnm-server migration completed' \"$evidence/migrate.log\"",
    )
    if any(commands.count(command) != 1 for command in ordered):
        fail("server live early diagnostics must identify the failing command and avoid echoing secrets")
    positions = [commands.index(command) for command in ordered]
    if positions != sorted(positions):
        fail("server live early diagnostic stages must precede the failing operation")


def validate_storage_live_harness(source: str) -> None:
    """Check storage and schema live lanes, without granting execution credit.

    This accepts the repository's bounded shell layout rather than evaluating
    shell code. Required environment, exact selectors, count/marker/skip guards
    and sealing remain separately subject to real native execution.
    """
    commands: list[str] = []
    pending = ""
    for raw in source.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            pending += line[:-1].rstrip() + " "
        else:
            commands.append(pending + line)
            pending = ""
    if pending:
        fail("storage live harness has an incomplete continued command")

    def once(command: str) -> int:
        if commands.count(command) != 1:
            fail("storage live harness missing or duplicate guard: " + command)
        return commands.index(command)

    once("set -euo pipefail")
    migration = once('"$binary" migrate > "$evidence/migrate.log" 2>&1')
    prefix = (
        'CARGO_TERM_COLOR=never TRNM_REQUIRE_LIVE_DATABASE=1 '
        'TRNM_DATABASE_URL="$database_url" TRNM_DATABASE_PROFILE="$profile" '
    )
    lanes = (
        (
            'cargo test -p trnm-server --locked --lib '
            'runtime::storage_api_tests::canonical_storage_api_live_database',
            "canonical-storage-app.log", "canonical_storage_test_count",
            "canonical_storage_api_live_executed", "canonical_storage_api_live_skipped",
        ),
        (
            'cargo test -p trnm-persistence-pg --locked --test authority_storage '
            'nakama_client_listing_modes_cursors_and_integrity_are_database_projected',
            "nakama-client-list-projection.log", "nakama_client_list_test_count",
            "nakama_client_list_projection_executed", "nakama_client_list_projection_skipped",
        ),
        (
            'cargo test -p trnm-persistence-pg --locked --test authority_storage '
            'blind_storage_no_op_preserves_timestamp_after_acl_occ_and_integrity_checks',
            "storage-occ-precedence.log", "storage_occ_test_count",
            "storage_blind_write_timestamps_executed", "storage_blind_write_timestamps_skipped",
        ),
        (
            'cargo test -p trnm-persistence-pg --locked --test storage_timestamps '
            'storage_timestamps_database_clock_no_op_and_atomicity',
            "storage-timestamps.log", "storage_timestamps_test_count",
            "storage_timestamps_live_executed", "storage_timestamps_live_skipped",
        ),
    )
    previous_end = migration
    for cargo, logfile, counter, marker, skip in lanes:
        start = once(
            prefix + cargo + ' -- --exact --nocapture --test-threads=1 2>&1 | '
            f'tee "$evidence/{logfile}"'
        )
        assignment = once(counter + "=$(")
        count = once(
            "sed -nE 's/^test result: ok[.] ([0-9]+) passed; 0 failed; 0 ignored;.*/\\1/p' "
            f'"$evidence/{logfile}"'
        )
        numeric = once(f'[[ "${counter}" =~ ^[0-9]+$ ]]')
        exact = once(f'test "${counter}" -eq 1')
        executed = once(f'grep -Fxq "{marker} profile=${{profile}}" "$evidence/{logfile}"')
        reject_skip = once(f"if grep -Fq '{skip}' \"$evidence/{logfile}\"; then")
        try:
            end = commands.index("fi", reject_skip + 1)
        except ValueError:
            fail("storage live harness skip guard has no terminal block")
        if "exit 1" not in commands[reject_skip + 1:end] or "exit 0" in commands[reject_skip + 1:end]:
            fail("storage live harness must reject an optional fixture skip")
        if not previous_end < start < assignment < count < numeric < exact < executed < reject_skip < end:
            fail("storage live harness fixture/guard order drifted")
        if logfile == "canonical-storage-app.log":
            opaque = once(
                'grep -Fxq "storage_opaque_conditions_live_executed profile=${profile} '
                'write_cases=15 delete_cases=18 batch_cases=2" '
                '"$evidence/canonical-storage-app.log"'
            )
            if not executed < opaque < reject_skip:
                fail("storage opaque condition marker must bind the checked canonical fixture")
        previous_end = end
    schema_start = once(
        'CARGO_TERM_COLOR=never TRNM_REQUIRE_LIVE_DATABASE=1 '
        'TRNM_SCHEMA_UPGRADE_ADMIN_DATABASE_URL="$database_url" TRNM_DATABASE_PROFILE="$profile" '
        'cargo test -p trnm-persistence-pg --locked --test schema_upgrade '
        '-- --nocapture --test-threads=1 2>&1 | tee "$evidence/schema-upgrade.log"'
    )
    schema_assignment = once("schema_upgrade_test_count=$(")
    schema_count = once(
        "sed -nE 's/^test result: ok[.] ([0-9]+) passed; 0 failed; 0 ignored;.*/\\1/p' "
        '"$evidence/schema-upgrade.log"'
    )
    schema_numeric = once('[[ "$schema_upgrade_test_count" =~ ^[0-9]+$ ]]')
    schema_exact = once('test "$schema_upgrade_test_count" -eq 6')
    schema_skip = once("if grep -Fq 'developer-only live test skip' \"$evidence/schema-upgrade.log\"; then")
    try:
        schema_end = commands.index("fi", schema_skip + 1)
    except ValueError:
        fail("schema live harness skip guard has no terminal block")
    if "exit 1" not in commands[schema_skip + 1:schema_end] or "exit 0" in commands[schema_skip + 1:schema_end]:
        fail("schema live harness must reject a developer-only skip")
    if not previous_end < schema_start < schema_assignment < schema_count < schema_numeric < schema_exact < schema_skip < schema_end:
        fail("schema live harness required environment/count/skip order drifted")
    previous_end = schema_end
    summary = [index for index, command in enumerate(commands)
               if '"nakama_client_list_projection":true' in command and command.startswith("{")]
    if len(summary) != 1 or summary[0] <= previous_end:
        fail("storage live summary must follow the checked client-list execution")
    if '"storage_occ_precedence":true' not in commands[summary[0]]:
        fail("storage live summary must bind the checked ACL/OCC execution")
    if '"raw_version_conditions":true' not in commands[summary[0]]:
        fail("storage live summary must bind the checked original condition inputs")
    seal = once(
        'find "$evidence" -type f ! -name SHA256SUMS -print0 '
        '| sort -z | xargs -0 sha256sum > "$evidence/SHA256SUMS"'
    )
    if seal <= summary[0]:
        fail("storage live fixture logs must be included in the final checksum seal")


def expected_persistence_dependencies() -> dict[str, object]:
    """Read the same closed dependency policy used by the foundation checker.

    Resolve only the sibling script, never a module selected by cwd or sys.path.
    Importing that script does not execute its command-line validation. No local
    duplicate or fallback policy may silently diverge from the foundation table.
    """
    path = Path(__file__).with_name("check-rust-foundation.py")
    spec = importlib.util.spec_from_file_location("trnm_server_foundation_policy", path)
    if spec is None or spec.loader is None:
        fail("foundation dependency policy loader is unavailable")
    foundation = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(foundation)
        dependencies = foundation.EXPECTED_DEPENDENCIES["crates/trnm-persistence-pg"]
    except (OSError, SyntaxError, AttributeError, KeyError, TypeError) as error:
        fail("foundation dependency policy could not be loaded: " + type(error).__name__)
    if not isinstance(dependencies, dict) or not dependencies:
        fail("foundation persistence dependency policy must be a nonempty mapping")
    return deepcopy(dependencies)


def validate_dependency_boundary(manifest: dict[str, object]) -> None:
    expected_dependencies = expected_persistence_dependencies()
    if manifest.get("dependencies") != expected_dependencies:
        fail("server candidate changed the reviewed persistence dependency boundary")
    expected_build_dependencies = {
        "openssl": "=0.10.81",
        "serde_json": "=1.0.145",
        "prost-build": "=0.14.3",
        "prost-types": "=0.14.3",
        "protoc-bin-vendored": "=3.2.0",
        "tonic-build": "=0.14.5",
        "tonic-prost-build": "=0.14.5",
    }
    if manifest.get("build-dependencies") != expected_build_dependencies:
        fail("server candidate changed the reviewed protobuf build dependency boundary")


def main() -> int:
    missing = sorted(str(path.relative_to(ROOT)) for path in REQUIRED_FILES if not path.is_file())
    if missing:
        fail("missing files: " + ", ".join(missing))

    manifest = tomllib.loads(
        (ROOT / "crates/trnm-persistence-pg/Cargo.toml").read_text(encoding="utf-8")
    )
    validate_dependency_boundary(manifest)

    sources = {
        path.relative_to(ROOT): path.read_text(encoding="utf-8")
        for path in sorted(REQUIRED_FILES)
    }
    validate_storage_live_harness(sources[STORAGE_LIVE_HARNESS.relative_to(ROOT)])
    validate_live_failure_diagnostics(
        sources[STORAGE_LIVE_HARNESS.relative_to(ROOT)],
        sources[LIVE_FAILURE_HELPER.relative_to(ROOT)],
    )
    pool_root_key = POOL_ROOT.relative_to(ROOT)
    expected_pool_root = "\n".join(
        f'include!("pool_parts/{part.name}");' for part in POOL_PARTS
    ) + "\n"
    if sources[pool_root_key] != expected_pool_root:
        fail("pool.rs must remain the exact four-part include authority")
    pool_source = "\n".join(
        [sources[pool_root_key], *(sources[part.relative_to(ROOT)] for part in POOL_PARTS)]
    )

    authority_storage_root_key = AUTHORITY_STORAGE_ROOT.relative_to(ROOT)
    expected_authority_storage_root = "\n".join(
        f'include!("authority_storage_parts/{part.name}");'
        for part in AUTHORITY_STORAGE_PARTS
    ) + "\n"
    if sources[authority_storage_root_key] != expected_authority_storage_root:
        fail("authority_storage.rs must remain the exact four-part include authority")
    authority_storage_source = "\n".join(
        [
            sources[authority_storage_root_key],
            *(sources[part.relative_to(ROOT)] for part in AUTHORITY_STORAGE_PARTS),
        ]
    )
    combined = "\n".join(sources.values())

    binary_key = Path("crates/trnm-persistence-pg/src/bin/trnm-server.rs")
    if "#![forbid(unsafe_code)]" not in sources[binary_key]:
        fail("binary root does not forbid unsafe code")
    for marker in FORBIDDEN_SOURCE:
        if marker in combined:
            fail(f"forbidden source marker: {marker}")

    require_markers(
        "closed pool source set",
        pool_source,
        (
            "pub struct PgPoolConfig",
            "pub struct PgTlsConfig",
            "pub struct PgPoolSnapshot",
            "pub struct PgPool",
            "get_timeout(timeout)",
            ".test_on_check_out(true)",
            "Certificate::from_pem",
            "Identity::from_pkcs8",
            "Protocol::Tlsv12",
            "SET statement_timeout",
            "SET lock_timeout",
            '"<redacted>"',
            "pub fn run_with_deadline<T>",
            "CancelState",
            "DeadlineGuard",
            "cancel_all_for_shutdown",
            "token.cancel_query(NoTls)",
            "token.cancel_query(connector.clone())",
            "database_operation_deadline_exceeded",
            "database_operation_shutdown_cancelled",
            "database_cancellation_id_exhausted",
            "fetch_update(Ordering::AcqRel",
        ),
    )

    marker_groups = {
        "crates/trnm-persistence-pg/src/storage_parts/01_repository.rs": (
            "pub fn list_storage_objects_nakama(",
            "validate_client_list_request",
            ".read_only(true)",
            ".take(limit)",
            "decode_nakama_listed_storage_object",
            "transaction.commit()",
        ),
        "crates/trnm-persistence-pg/src/storage_parts/02_list_helpers.rs": (
            "storage_client_list_public_query",
            "storage_client_list_own_query",
            "storage_client_list_foreign_query",
            "ORDER BY read_permission ASC, object_key ASC, user_id ASC",
            "ORDER BY read_permission ASC, object_key ASC",
            "ORDER BY object_key ASC",
        ),
        "crates/trnm-persistence-pg/src/storage_parts/05_decode_errors.rs": (
            "decode_nakama_listed_storage_object",
            "StorageObjectKey::new_nakama",
            "decode_storage_object_at(key, row, 2)",
        ),
        "crates/trnm-server/src/runtime/storage_list_api.rs": (
            "STORAGE_LIST_ROUTES",
            "pub(super) fn is_list_target",
            "list_storage_objects_nakama",
            "decode_cursor",
            "encode_cursor",
            "cursor != query.cursor",
        ),
        "crates/trnm-server/src/runtime/storage_list_query.rs": (
            "const MAX_COLLECTION_BYTES: usize = 4096;",
            "pub(super) fn parse",
            "1..=100",
            "parse_uuid",
        ),
        "crates/trnm-server/src/runtime/storage_cursor.rs": (
            "pub(super) fn decode_cursor",
            "pub(super) fn encode_cursor",
            "const MAX_KEY_BYTES: usize = 4096;",
            "const MAX_DEPTH: usize = 16;",
            "const MAX_ITEMS: usize = 1024;",
        ),
        "crates/trnm-persistence-pg/src/session.rs": (
            "pub struct CreateSessionFamily",
            "pub enum RefreshRotationOutcome",
            "IsolationLevel::Serializable",
            "FOR UPDATE",
            "refresh_compare_and_swap_failed",
            "revoked_reason = 2",
            "RevocationReason::RefreshReplay",
        ),
        "crates/trnm-persistence-pg/src/bin/trnm-server.rs": (
            "Command::CheckConfig",
            "Command::Migrate",
            "Command::Serve",
            "open_verified_repository",
        ),
        "crates/trnm-persistence-pg/src/auth.rs": (
            "pub struct AccessTokenVerifier",
            "allow_legacy_without_key_id: false",
            "MAX_ACCESS_TOKEN_LIFETIME_SECONDS",
            "if lifetime > MAX_ACCESS_TOKEN_LIFETIME_SECONDS",
            "claim_string(claims, \"sid\")",
            "claim_unsigned(claims, \"sgn\")",
            "sha256_digest(value.as_bytes())",
            "trnm_token_jwt_provider_adapter",
            "authenticate(",
            "from_provider(",
            "\"session_authentication_failed\"",
        ),
        "crates/trnm-persistence-pg/src/bin/trnm_server/mod.rs": (
            "pub(crate) mod auth;",
            "pub(crate) mod pool;",
            "pub(crate) mod session_api;",
            "pub(crate) mod websocket;",
        ),
        "crates/trnm-persistence-pg/src/bin/trnm_server/config.rs": (
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
        ),
        "crates/trnm-persistence-pg/src/bin/trnm_server/pool.rs": (
            "pub struct PooledRepository",
            "run_with_deadline",
            "impl BudgetedRepository for PooledRepository",
            "impl InflightCancellation for PooledRepository",
            "self.pool.cancel_inflight()",
            "database_inflight_operations: snapshot.inflight_operations",
            "database_cancellation_failures: snapshot.cancellation_failures",
        ),
        "crates/trnm-persistence-pg/src/bin/trnm_server/schema.rs": (
            "migrate_authoritative_schema",
            "verify_authoritative_schema",
            "TRNM_STORAGE_LEGACY_WRITER_ROLE",
            "PgPool::connect_plain",
            "PgPool::connect_tls",
            "PgTlsConfig::new",
        ),
        "crates/trnm-persistence-pg/src/bin/trnm_server/grpc.rs": (
            "/nakama.api.Nakama/Healthcheck",
            "NakamaServer::new",
            "serve_with_shutdown",
            "worker_failed.store(true",
            "catch_unwind",
            "draining.begin()",
        ),
        "crates/trnm-persistence-pg/src/bin/trnm_server/http.rs": (
            "http_transfer_encoding_not_supported",
            "http_pipelining_not_supported",
            "http_duplicate_header",
            "Connection: close",
        ),
        "crates/trnm-persistence-pg/src/bin/trnm_server/app.rs": (
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
            "trnm_server_database_inflight_operations",
            "trnm_server_database_deadline_cancellations_total",
            "trnm_server_database_shutdown_cancellations_total",
            "trnm_server_database_cancellation_deliveries_total",
            "trnm_server_database_cancellation_failures_total",
            "pub(crate) struct SharedDrain",
            "try_admit",
            "admit_realtime_dispatch",
            "handle_admitted",
            "is_mutating_request",
        ),
        "crates/trnm-persistence-pg/src/bin/trnm_server/session_api.rs": (
            "pub struct SessionApi",
            "verify_access_session",
            "rotate_refresh_token",
            "revoke_session_family",
            "RefreshRotationOutcome::ReplayRevoked",
            "session_authentication_not_configured",
        ),
        "crates/trnm-persistence-pg/src/bin/trnm_server/retry.rs": (
            "max_attempts: 3",
            "total_budget: DATABASE_OPERATION_BUDGET",
            "RetryClass::SafeImmediate",
            "RetryClass::SafeBackoff",
            "database_retry_budget_exhausted",
            "let remaining = policy.total_budget.saturating_sub(started.elapsed());",
            "operation(remaining)",
            "if started.elapsed() >= policy.total_budget",
            "jittered_backoff(backoff)",
            "base_nanos / 2",
        ),
        "crates/trnm-persistence-pg/src/bin/trnm_server/websocket.rs": (
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
        ),
        "crates/trnm-persistence-pg/src/bin/trnm_server/server.rs": (
            "RetryingRepository::new",
            "RetryPolicy::candidate_default",
            "config.session_auth",
            "with_access_token_verifier",
            "websocket::is_route",
            "websocket::serve_once",
            "grpc::spawn",
            "grpc::join",
            "with_shared_state",
            "let cancelled_operations = repository.cancel_inflight();",
            "drop(sender);",
            "join_workers(workers)",
        ),
    }
    for relative, markers in marker_groups.items():
        require_markers(relative, sources[Path(relative)], markers)

    for process_root in (MODULE_ROOT.relative_to(ROOT), Path("crates/trnm-server/src/runtime")):
        require_markers("migration operator source", sources[process_root / "error.rs"], (
            "enum MigrationPhase", "const MIGRATION_DIAGNOSTIC_MAX_BYTES: usize = 512;",
            "MIGRATION_REASONS", 'unwrap_or("unclassified")',
            "trillionnium.server-migration-failure.v1", "result.inspect_err(|error|",
            "let _ = writer.write_all(", "allowlisted_migration_reason(error.reason())",
            "migration_sqlstate(error.code())",
        ))
        require_markers("migration operator stage source", sources[process_root / "schema.rs"], (
            "MigrationPhase::BuildPool", "MigrationPhase::AcquireSession",
            "MigrationPhase::ApplyAuthoritativeChain", "diagnose_migration_result(",
        ))

    server_source = sources[Path("crates/trnm-persistence-pg/src/bin/trnm_server/server.rs")]
    cancel_position = server_source.find("let cancelled_operations = repository.cancel_inflight();")
    drop_position = server_source.find("drop(sender);")
    join_position = server_source.find("join_workers(workers)")
    if not (0 <= cancel_position < drop_position < join_position):
        fail("shutdown cancellation must precede queue close and worker join")

    test_names = set(
        re.findall(
            r"fn\s+([a-z0-9_]+)\s*\(\)\s*\{",
            combined + "\n" + authority_storage_source,
        )
    )
    missing_tests = sorted(REQUIRED_TESTS - test_names)
    if missing_tests:
        fail(f"missing tests: {missing_tests}")
    test_count = combined.count("#[test]")
    if test_count < 72:
        fail(f"expected at least 72 server/session/pool/websocket/grpc source tests, got {test_count}")

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
    if server.get("manifest") != "crates/trnm-server/Cargo.toml":
        fail("Rust package authority points to another server manifest")
    if server.get("source") != "crates/trnm-server/src/main.rs":
        fail("Rust package authority points to another server source")

    status = json.loads(
        (ROOT / "docs/status/TRNM_SERVER_STATUS.json").read_text(encoding="utf-8")
    )
    diagnostics = status.get("live_failure_diagnostics_source_candidate", {})
    if diagnostics.get("helper") != str(LIVE_FAILURE_HELPER.relative_to(ROOT)) or (
        diagnostics.get("limits") != {
            "max_logs": 2, "read_bytes_per_log": 65536,
            "lines_per_log": 80, "output_bytes": 1048576,
        }
    ) or diagnostics.get("preserves_original_exit_status") is not True or any(
        diagnostics.get(field) is not False for field in ("accepted", "gap_closed", "compatibility_credit")
    ):
        fail("server live failure diagnostic state must retain bounds, original failure and no-credit claims")
    operator = status.get("migration_operator_diagnostics_source_candidate", {})
    if operator.get("max_record_bytes") != 512 or operator.get("domain_sqlstate_retained") is not False or any(
        operator.get(field) is not False for field in ("accepted", "gap_closed", "compatibility_credit")
    ):
        fail("migration operator diagnostics must retain their bound and truthful no-credit state")
    storage_contract = json.loads(
        (ROOT / "contracts/storage/nakama-http-storage-v1.json").read_text(encoding="utf-8")
    )
    if storage_contract.get("composition") != "crates/trnm-server::trnm-server":
        fail("storage HTTP API must use the canonical process authority")
    expected_storage_routes = {
        ("POST", "/v2/storage"), ("PUT", "/v2/storage"), ("PUT", "/v2/storage/delete"),
        ("GET", "/v2/storage/{collection}"),
        ("GET", "/v2/storage/{collection}/{user_id}"),
    }
    if {
        (row.get("method"), row.get("path"))
        for row in storage_contract.get("routes", [])
    } != expected_storage_routes:
        fail("storage API contract routes differ from the pinned source subset")
    if not storage_contract.get("limitations") or any(storage_contract.get("claims", {}).values()):
        fail("storage API contract must retain limitations and no-credit claims")
    if storage_contract.get("resource_limits", {}).get("version_condition") != (
        "No separate format or length cap; bounded by the configured HTTP request budget."
    ):
        fail("storage exact input conditions must retain the complete request budget")
    condition_state = status.get("storage_http_mutations", {}).get("condition_version")
    if condition_state != (
        "Original-string ExpectedVersion: write empty is blind, write star is insert-only, "
        "other writes and all nonempty deletes are exact; schema v2 generated versions remain unchanged."
    ):
        fail("storage mutation state must distinguish raw conditions from generated versions")
    if status.get("claims", {}).get("storage_http_mutation_source_candidate") is not True:
        fail("storage mutation component state is missing")
    if status.get("claims", {}).get("storage_http_read_source_candidate") is not True:
        fail("storage read component state is missing")
    if status.get("claims", {}).get("storage_http_list_source_candidate") is not True:
        fail("storage list component state is missing")
    if status.get("stage") != "canonical-http-grpc-websocket-session-database-source-candidate":
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
                "pool_parts": [part.name for part in POOL_PARTS],
                "source_candidate": True,
                "bounded_retry_source_candidate": True,
                "request_cancellation_source_candidate": True,
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
