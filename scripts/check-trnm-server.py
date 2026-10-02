#!/usr/bin/env python3
"""Validate the first-party Rust server vertical-slice source candidate."""
from __future__ import annotations

import ast
import argparse
import hashlib
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
        "06_native_projection.rs",
    )
)
STORAGE_LIVE_HARNESS = ROOT / "scripts/ci-trnm-server-live.sh"
LIVE_FAILURE_HELPER = ROOT / "scripts/print-server-live-failure.py"
STORAGE_IMPORT_PARTS = tuple(
    ROOT / "crates/trnm-persistence-pg/tests/storage_import_v4_parts" / name
    for name in ("environment.rs", "packet.rs", "lifecycle.rs", "tamper.rs", "security.rs")
)
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
    ROOT / "crates/trnm-server/src/runtime/storage_api_projection_tests.rs",
    ROOT / "crates/trnm-server/src/runtime/storage_api_v3_live.rs",
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
    PERSISTENCE_ROOT / "schema_parts/jsonb_backfill.rs",
    PERSISTENCE_ROOT / "storage_metadata.rs",
    SERVER_ROOT / "trnm-schema.rs",
    ROOT / "crates/trnm-persistence-pg/tests/storage_timestamps.rs",
    ROOT / "crates/trnm-persistence-pg/tests/storage_jsonb.rs",
    PERSISTENCE_ROOT / "storage_import.rs",
    PERSISTENCE_ROOT / "storage_import_parts/packet.rs",
    PERSISTENCE_ROOT / "storage_import_parts/repository.rs",
    PERSISTENCE_ROOT / "storage_import_parts/exporter.rs",
    SERVER_ROOT / "storage_transfer.rs",
    SERVER_ROOT / "trnm-storage-export.rs",
    SERVER_ROOT / "trnm-storage-import.rs",
    ROOT / "crates/trnm-persistence-pg/tests/storage_import_v4.rs",
    *STORAGE_IMPORT_PARTS,
    ROOT / "scripts/materialize-pinned-storage-upstream.py",
    ROOT / "crates/trnm-persistence-pg/tests/storage_permissions_v4.rs",
    ROOT / "crates/trnm-persistence-pg/tests/schema_upgrade.rs",
    ROOT / "crates/trnm-persistence-pg/tests/schema_upgrade_parts/v3.rs",
}
SCHEMA_V3_CASE_FAMILIES = {
    "shapes": 8, "illegal_legacy": 9, "catalog_drift": 6,
    "partial_resume": 3, "metadata_validation": 9, "opaque_history": 6,
}
STORAGE_IMPORT_LOG = "storage-v4-import.log"
STORAGE_IMPORT_SELECTOR = "storage_v4_source_export_custody_resume_finish_and_tamper_are_native"
REQUIRED_TESTS = {
    STORAGE_IMPORT_SELECTOR,
    "migration_diagnostic_unknown_reasons_and_other_variants_do_not_leak_secrets",
    "migration_diagnostic_never_reconstructs_sqlstate_from_domain_reason",
    "migration_sqlstate_projection_rejects_non_code_or_unsafe_driver_values",
    "migration_diagnostic_profile_phase_and_output_are_bounded",
    "migration_diagnostic_success_preserves_value_without_output",
    "migration_diagnostic_sink_failure_preserves_original_error",
    "canonical_storage_api_live_database",
    "storage_read_preserves_native_projection_and_independent_public_tokens",
    "storage_native_projection_validates_known_witness_without_inventing_history",
    "storage_ack_uses_request_digest_and_keeps_empty_previous_version_present",
    "storage_native_projection_budget_is_separate_from_request_value_budget",
    "storage_response_budget_counts_escaped_bytes_and_rejects_the_whole_batch",
    "storage_repository_resource_errors_preserve_code_eight_and_redact_reasons",
    "storage_list_preserves_native_history_shapes_and_opaque_public_versions",
    "storage_list_encoded_response_budget_rejects_partial_objects_with_code_eight",
    "storage_list_repository_resource_exhaustion_is_redacted_without_integrity_code",
    "opaque_write_conditions_reach_storage_occ_and_acl_after_authentication",
    "opaque_delete_conditions_including_star_are_literal_and_reach_storage",
    "absent_null_and_empty_conditions_keep_unconditional_write_and_delete_semantics",
    "storage_timestamp_json_matches_protobuf_range_precision_and_pre_epoch",
    "new_storage_ack_requires_both_times_and_historical_unknown_is_not_fabricated",
    "storage_ack_rejects_missing_effective_update_time_and_unequal_insert_pair",
    "read_projects_valid_fraction_to_seconds_omits_unknown_and_rejects_invalid_times",
    "storage_list_projects_fraction_to_seconds_omits_unknown_and_rejects_invalid_times",
    "storage_timestamps_database_clock_no_op_and_atomicity",
    "storage_native_jsonb_versions_provenance_and_atomicity",
    "storage_v4_raw_acl_domains_keys_and_operation_predicates_are_native",
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
    "both_authoritative_profiles_embed_the_twelve_table_chain",
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
    absolute_directory = once('evidence_absolute=$(cd "$evidence" && pwd -P)')
    migration = once('"$binary" migrate > "$evidence/migrate.log" 2>&1')
    if not absolute_directory < migration:
        fail("storage import directories must be absolute before native execution")
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
        (
            'cargo test -p trnm-persistence-pg --locked --test storage_jsonb '
            'storage_native_jsonb_versions_provenance_and_atomicity',
            "storage-native-jsonb.log", "storage_native_jsonb_test_count",
            "storage_native_jsonb_live_executed", "storage_native_jsonb_live_skipped",
        ),
        (
            'cargo test -p trnm-persistence-pg --locked --test storage_permissions_v4 '
            'storage_v4_raw_acl_domains_keys_and_operation_predicates_are_native',
            "storage-v4-acl.log", "storage_v4_acl_test_count",
            "storage_v4_acl_live_executed", "storage_v4_acl_live_skipped",
        ),
        (
            'cargo test -p trnm-persistence-pg --locked --test storage_import_v4 '
            + STORAGE_IMPORT_SELECTOR,
            STORAGE_IMPORT_LOG, "storage_v4_import_test_count",
            "storage_v4_import_live_executed", "storage_v4_import_live_skipped",
        ),
    )
    previous_end = migration
    for cargo, logfile, counter, marker, skip in lanes:
        environment = prefix
        if logfile == STORAGE_IMPORT_LOG:
            annex = once('python3 scripts/materialize-pinned-storage-upstream.py '
                         '--destination "$evidence/storage-source-upstream" '
                         '> "$evidence/storage-v4-source-materialization.json"')
            archive = once('python3 scripts/check-trnm-server.py '
                           '--storage-import-source-archive "$evidence" --profile "$profile" '
                           '--commit "$candidate_sha" --tree "$candidate_tree" '
                           '> "$evidence/storage-v4-source-archive.log"')
            if not previous_end < annex < archive:
                fail("storage import immutable upstream acquisition must precede source sealing and execution")
            previous_end = archive
            environment += (
                'TRNM_SCHEMA_UPGRADE_ADMIN_DATABASE_URL="$database_url" '
                'TRNM_STORAGE_PINNED_UPSTREAM_DIRECTORY="$evidence_absolute/storage-source-upstream" '
                'TRNM_STORAGE_TEST_PRODUCER_COMMIT="$candidate_sha" '
                'TRNM_STORAGE_TEST_PRODUCER_TREE="$candidate_tree" '
                'TRNM_STORAGE_IMPORT_EVIDENCE_ROOT="$evidence_absolute/storage-v4-import-packets" '
            )
        start = once(
            environment + cargo + ' -- --exact --nocapture --test-threads=1 2>&1 | '
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
            jsonb = once(
                'grep -Fxq "storage_jsonb_v3_live_executed profile=${profile} '
                'history_cases=6 opaque_success_cases=4 noop_cases=2 resource_cases=1 native_input_cases=3" '
                '"$evidence/canonical-storage-app.log"'
            )
            native_inputs = once(
                'test "$(grep -Ec "^storage_jsonb_v3_native_inputs profile=${profile} '
                'condition_sql=(accepted|rejected) payload_sql=(accepted|rejected) compatibility_credit=false$" '
                '"$evidence/canonical-storage-app.log")" -eq 1'
            )
            if not opaque < jsonb < native_inputs < reject_skip:
                fail("storage JSONB v3 markers must bind actual cases and SQL profile results to the canonical fixture")
        if logfile in ("storage-native-jsonb.log", "storage-v4-acl.log", STORAGE_IMPORT_LOG):
            unique_marker = once(
                f'test "$(grep -Fxc "{marker} profile=${{profile}}" '
                f'"$evidence/{logfile}")" -eq 1'
            )
            if not executed < unique_marker < reject_skip:
                fail("native storage JSONB fixture marker must occur once in the executed fixture log")
        if logfile == STORAGE_IMPORT_LOG:
            terminal = once('test "$(grep -Ec \'^test result:\' '
                            '"$evidence/storage-v4-import.log")" -eq 1')
            if not exact < terminal < executed:
                fail("storage import fixture must have one complete terminal result before its marker")
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
    schema_previous = schema_exact
    for family, count in SCHEMA_V3_CASE_FAMILIES.items():
        marker = once(f'grep -Fxq "schema_v3_{family}_executed profile=${{profile}} extra_cases={count}" '
                      '"$evidence/schema-upgrade.log"')
        unique = once(f'test "$(grep -Ec \'^schema_v3_{family}_executed \' '
                      '"$evidence/schema-upgrade.log")" -eq 1')
        if not schema_previous < marker < unique:
            fail("schema v3 scenario families must follow the original exact-six result and execute once")
        schema_previous = unique
    schema_family_skip = once("if grep -Eq 'schema_v3_[a-z_]+_skipped' \"$evidence/schema-upgrade.log\"; then")
    try:
        schema_family_end = commands.index("fi", schema_family_skip + 1)
    except ValueError:
        fail("schema v3 family skip guard has no terminal block")
    if not schema_previous < schema_family_skip < schema_family_end or (
        "exit 1" not in commands[schema_family_skip + 1:schema_family_end]
        or "exit 0" in commands[schema_family_skip + 1:schema_family_end]
    ):
        fail("schema v3 family skips must terminate before any success summary")
    schema_previous = schema_family_end
    schema_skip = once("if grep -Fq 'developer-only live test skip' \"$evidence/schema-upgrade.log\"; then")
    try:
        schema_end = commands.index("fi", schema_skip + 1)
    except ValueError:
        fail("schema live harness skip guard has no terminal block")
    if "exit 1" not in commands[schema_skip + 1:schema_end] or "exit 0" in commands[schema_skip + 1:schema_end]:
        fail("schema live harness must reject a developer-only skip")
    if not previous_end < schema_start < schema_assignment < schema_count < schema_numeric < schema_exact < schema_previous < schema_skip < schema_end:
        fail("schema live harness required environment/count/skip order drifted")
    previous_end = schema_end
    schema_guards = (
        "schema_version=$(db_scalar 'SELECT schema_version FROM trnm_schema_metadata WHERE singleton = 1')",
        'test "$schema_version" = 4',
        "storage_writer_epoch=$(db_scalar 'SELECT storage_writer_epoch FROM trnm_schema_metadata WHERE singleton = 1')",
        'test "$storage_writer_epoch" = 4',
        "v2_apply_source_commit=$(db_scalar 'SELECT v2_apply_source_commit FROM trnm_schema_metadata WHERE singleton = 1')",
        'test "$v2_apply_source_commit" = "$candidate_sha"',
        "v3_apply_source_commit=$(db_scalar 'SELECT v3_apply_source_commit FROM trnm_schema_metadata WHERE singleton = 1')",
        'test "$v3_apply_source_commit" = "$candidate_sha"',
        "python3 - \"$evidence\" \"$profile\" \"$candidate_sha\" <<'PY_STORAGE_SCHEMA_V3'",
        'authoritative_migrations_count=$(python3 -c \'import json,sys; print(len(json.load(open(sys.argv[1]))["ordered_files"]))\' "$evidence/authoritative-migrations.json")',
        'test "$authoritative_migrations_count" -eq 4',
    )
    for guard in schema_guards:
        position = once(guard)
        if position <= previous_end:
            fail("server schema v3 assertions must follow isolated lifecycle execution")
        previous_end = position
    require_markers("server schema v3 archived chain", source, (
        "identity['schema_version']==4 and identity['storage_writer_epoch']==4",
        "identity['source_commit']==identity['upgrade_source_commit']==identity['v2_apply_source_commit']==identity['v3_apply_source_commit']==candidate",
        "assert lock['schema_version']==4",
        "names=('0001_foundation_up.sql','0002_storage_timestamps_up.sql','0003_storage_jsonb_up.sql','0004_storage_source_import_up.sql')",
        "assert [entry['path'] for entry in files]==[f'migrations/{profile}/{name}' for name in names]",
        "zip(files,names,strict=True)", "data=Path(entry['path']).read_bytes()",
        "entry['git_blob_sha1']", "target.write_bytes(data)", "assert target.read_bytes()==data",
        "'sha256':hashlib.sha256(data).hexdigest()", "'size_bytes':len(data)",
        "'compatibility_credit':False", "evidence/'authoritative-migrations.json'",
    ))
    summary = [index for index, command in enumerate(commands)
               if '"nakama_client_list_projection":true' in command and command.startswith("{")]
    if len(summary) != 1 or summary[0] <= previous_end:
        fail("storage live summary must follow the checked client-list execution")
    if '"storage_occ_precedence":true' not in commands[summary[0]]:
        fail("storage live summary must bind the checked ACL/OCC execution")
    if '"raw_version_conditions":true' not in commands[summary[0]]:
        fail("storage live summary must bind the checked original condition inputs")
    require_markers("storage v3 fixture summary", commands[summary[0]], (
        '"storage_jsonb_v3_projection":true',
        '"storage_native_jsonb":true', '"storage_v4_acl":true', '"schema_v3_extra_cases":41',
        '"storage_v4_import":true',
        '"schema_v3_case_families":{"shapes":8,"illegal_legacy":9,"catalog_drift":6,"partial_resume":3,"metadata_validation":9,"opaque_history":6}',
        '"storage_jsonb_v3_cases":{"history":6,"opaque_success":4,"no_op":2,"resource":1,"native_input":3}',
        '"schema_version":${schema_version}', '"storage_writer_epoch":${storage_writer_epoch}',
        '"authoritative_migrations_count":${authoritative_migrations_count}',
        '"compatibility_credit":false', '"accepted":false', '"wire_compatible":false',
        '"production_ready":false',
    ))
    seal = once(
        'find "$evidence" -type f ! -name SHA256SUMS -print0 '
        '| sort -z | xargs -0 sha256sum > "$evidence/SHA256SUMS"'
    )
    if seal <= summary[0]:
        fail("storage live fixture logs must be included in the final checksum seal")


def validate_storage_projection_source(source: str, fixture: str) -> None:
    """Bind HTTP projection and fixture source, without execution credit."""
    require_markers("storage HTTP projection source", source, (
        'include!("storage_api_projection_tests.rs");',
        "pub(super) enum StorageEncodingError", "ResourceExhausted",
        "const MAX_ENCODED_STORAGE_RESPONSE_BYTES: usize = 32 * 1024 * 1024;",
        "impl io::Write for StorageJsonEncoder", "serde_json::to_writer(&mut *self, value)",
        "checked_add(bytes.len())", "try_reserve_exact", "storage_resource_error(",
        "version != ContentVersion::from_value(&write.value)",
        "receipt.previous_version.is_none()",
    ))
    try:
        encoder = source.split("pub(super) fn encode_storage_object(", 1)[1].split("\nstruct ParsedWrite", 1)[0]
    except IndexError:
        fail("storage HTTP object encoder is absent")
    require_markers("storage HTTP object projection", encoder, (
        "Result<String, StorageEncodingError>", "object.verify_integrity()",
        "StableCode::ResourceExhausted", "StorageEncodingError::DataLoss",
        "std::str::from_utf8(&object.value)", "output.string(value)?",
        "!object.version.as_str().is_empty()", "object.version.as_str()",
    ))
    if any(marker in encoder for marker in (
        "ContentVersion::from_value", "MAX_REQUEST_VALUE_BYTES", "serde_json::from_",
    )):
        fail("storage HTTP read projection reinterprets native payload as a request or generated version")
    require_markers("canonical storage v3 native fixture", fixture, (
        'include!("storage_api_v3_live.rs");', "prove_storage_jsonb_v3_app(",
        "SELECT (($1::TEXT)::JSONB)::TEXT", "SELECT $1::TEXT",
        "value_jsonb::TEXT, value_projection_digest", "source_manifest_digest",
        "storage_jsonb_v3_native_inputs profile={}", "compatibility_credit=false",
        "storage_jsonb_v3_live_executed profile={} history_cases=6 opaque_success_cases=4 noop_cases=2 resource_cases=1 native_input_cases=3",
        "storage_opaque_conditions_live_executed profile={} write_cases=15 delete_cases=18 batch_cases=2",
    ))


def validate_storage_live_workflow(source: str, *, prospective: bool) -> None:
    """Keep each retained packet bound to its independently executed Git object."""
    live = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    compact = re.sub(r"\\\s*\n\s*", " ", live)
    compact = re.sub(r"[ \t]+", " ", compact)
    root = "$server" if prospective else "$evidence"
    commit = "$PROSPECTIVE_MERGE_SHA" if prospective else "$CANDIDATE_SHA"
    command = (f'python3 scripts/check-trnm-server.py --live-packet "{root}" '
               f'--profile "$PROFILE" --commit "{commit}" --tree "$(git rev-parse HEAD^{{tree}})"')
    if compact.count(command) != 1:
        fail("retained server packet must be validated once against its source or prospective object")
    require_markers("retained server schema v3 proof", live, (
        "'storage_native_jsonb'", "'storage_v4_acl'", "'storage_v4_import'", "'schema_upgrade'", "'storage_jsonb_v3_projection'",
        "assert type(summary['schema_v3_extra_cases']) is int and summary['schema_v3_extra_cases'] == 41",
        "assert summary['schema_v3_case_families'] == {",
        "'shapes': 8, 'illegal_legacy': 9, 'catalog_drift': 6,",
        "'partial_resume': 3, 'metadata_validation': 9, 'opaque_history': 6,",
        "assert all(type(value) is int for value in summary['schema_v3_case_families'].values())",
        "for field in ('schema_version', 'storage_writer_epoch', 'authoritative_migrations_count'):",
        "assert type(summary[field]) is int and summary[field] == 4",
    ))


def storage_import_source_authority() -> tuple[object, bytes, bytes]:
    """Read the current exporter/query and the sole pinned upstream authority."""
    path = ROOT / "scripts/materialize-pinned-storage-upstream.py"
    spec = importlib.util.spec_from_file_location("trnm_storage_source_annex", path)
    if spec is None or spec.loader is None:
        fail("storage import upstream source authority is unavailable")
    authority = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(authority)
    if len(authority.MEMBERS) != 3:
        fail("storage import upstream source inventory must remain the closed three members")
    exporter = (PERSISTENCE_ROOT / "storage_import_parts/exporter.rs").read_bytes()
    packet = (PERSISTENCE_ROOT / "storage_import_parts/packet.rs").read_text(encoding="utf-8")
    literals = re.findall(r'^const STORAGE_EXPORT_QUERY: &str = ("[^\n]*");$', packet, re.MULTILINE)
    if len(literals) != 1:
        fail("storage import native query has no single current source authority")
    query = json.loads(literals[0]).encode("utf-8")
    return authority, exporter, query


def storage_import_source_file(root: Path, relative: str) -> bytes:
    path = root
    for component in Path(relative).parts:
        if component in ("", ".", ".."):
            fail("storage import source archive path is not closed")
        path = path / component
        if path.is_symlink():
            fail("storage import source archive contains indirect bytes")
    if root.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 2 * 1024 * 1024:
        fail("storage import source archive file is absent, empty or oversized")
    return path.read_bytes()


def storage_import_source_entries(root: Path, *, write_current: bool = False) -> tuple[object, list[dict[str, object]]]:
    authority, exporter, query = storage_import_source_authority()
    archive = root / "storage-v4-import-source"
    if write_current:
        archive.mkdir(mode=0o700)
        for name, data in (("exporter.rs", exporter), ("source-query.sql", query)):
            with (archive / name).open("xb") as target:
                target.write(data)
    annex = root / "storage-source-upstream"
    if archive.is_symlink() or annex.is_symlink() or not archive.is_dir() or not annex.is_dir() or (
        {path.name for path in archive.iterdir()} != {"exporter.rs", "source-query.sql"}
        or {path.name for path in annex.iterdir()} != {member[0] for member in authority.MEMBERS}
    ):
        fail("storage import source archive must contain exactly the current exporter/query and three pinned upstream files")
    entries = []
    expected = [
        ("storage-v4-import-source/exporter.rs", "crates/trnm-persistence-pg/src/storage_import_parts/exporter.rs", exporter),
        ("storage-v4-import-source/source-query.sql", "crates/trnm-persistence-pg/src/storage_import_parts/packet.rs#STORAGE_EXPORT_QUERY", query),
    ]
    for member in authority.MEMBERS:
        relative = "storage-source-upstream/" + member[0]
        data = storage_import_source_file(root, relative)
        try:
            authority.validate_bytes(data, member)
        except ValueError:
            fail("storage import pinned upstream source bytes differ")
        expected.append((relative, "heroiclabs/nakama/" + member[1], data))
    for relative, source, expected_bytes in expected:
        actual = storage_import_source_file(root, relative)
        if actual != expected_bytes:
            fail("storage import source archive bytes differ from their actual authority")
        entries.append({"archive_path": relative, "source_path": source, "size_bytes": len(actual),
                        "sha256": hashlib.sha256(actual).hexdigest(),
                        "git_blob_sha1": hashlib.sha1(f"blob {len(actual)}\0".encode() + actual).hexdigest()})
    return authority, entries


def write_storage_import_source_archive(root: Path, *, profile: str, commit: str, tree: str) -> dict[str, object]:
    if profile not in {"postgresql", "cockroachdb"} or any(
        re.fullmatch(r"[0-9a-f]{40}", value) is None for value in (commit, tree)
    ):
        fail("storage import source archive target identity is invalid")
    authority, entries = storage_import_source_entries(root, write_current=True)
    report = {"schema": "trillionnium.storage-import-source-archive.v1", "scope": "source-annex-only",
              "repository": "TrillionniumFoundation/TrillionniumGame", "commit": commit, "tree": tree,
              "profile": profile, "upstream_repository": "heroiclabs/nakama",
              "upstream_commit": authority.COMMIT, "upstream_tree": authority.TREE, "members": entries,
              "server_execution_credit": False, "compatibility_credit": False,
              "accepted": False, "production_ready": False, "full_nakama_replacement": False}
    with (root / "storage-v4-import-source.json").open("x", encoding="utf-8") as output:
        json.dump(report, output, sort_keys=True, separators=(",", ":"))
        output.write("\n")
    return report


def validate_storage_import_native_archive(root: Path, *, profile: str, commit: str, tree: str) -> None:
    """Check retained native observations against actual packet/source bytes.

    The execution log is checked independently. Custodian/source/binary hashes
    are observed fixture identities, not signatures or accepted oracle proof.
    """
    native = root / "storage-v4-import-packets"
    if native.is_symlink() or not native.is_dir():
        fail("storage import native observations were not retained")
    files: dict[str, bytes] = {}
    entry_count = total_bytes = 0
    for path in native.rglob("*"):
        entry_count += 1
        if entry_count > 512 or path.is_symlink() or (not path.is_dir() and not path.is_file()):
            fail("storage import native archive inventory is indirect or exceeds its bound")
        if path.is_file():
            if not 0 <= path.stat().st_size <= 2 * 1024 * 1024:
                fail("storage import native archive member exceeds its byte bound")
            data = path.read_bytes()
            total_bytes += len(data)
            if total_bytes > 32 * 1024 * 1024:
                fail("storage import native archive exceeds its total byte bound")
            files[str(path.relative_to(native))] = data

    def parsed(data: bytes) -> object:
        def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
            value: dict[str, object] = {}
            for key, item in pairs:
                if key in value:
                    fail("storage import native JSON has duplicate fields")
                value[key] = item
            return value

        try:
            return json.loads(data, object_pairs_hook=unique,
                              parse_constant=lambda _: fail("storage import native JSON has a nonfinite number"))
        except (ValueError, RecursionError):
            fail("storage import native JSON could not be decoded")

    def decoded(name: str) -> object:
        if name not in files:
            fail("storage import native observation member is missing")
        return parsed(files[name])

    def obj(name: str, keys: set[str]) -> dict[str, object]:
        value = decoded(name)
        if not isinstance(value, dict) or set(value) != keys:
            fail("storage import native observation fields are not closed")
        return value

    def hex_digest(value: object) -> str:
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None or value == "0" * 64:
            fail("storage import native observation digest is invalid")
        return value

    def no_credit(value: dict[str, object]) -> None:
        if any(value.get(field) is not False for field in (
            "compatibility_credit", "production_ready", "full_nakama_replacement"
        )):
            fail("storage import native observations must retain no-credit claims")

    credit = {"compatibility_credit", "production_ready", "full_nakama_replacement"}
    anchor = obj("custody-anchors.json", {"schema", "profile", "producer_commit", "producer_tree",
                 "producer_source_sha256", "producer_binary_sha256", "execution_id", "manifest_sha256",
                 "receipt_sha256"} | credit)
    no_credit(anchor)
    if anchor["schema"] != "trillionnium.storage-import-native-custody-anchors.v1" or (
        anchor["profile"] != profile or anchor["producer_commit"] != commit or anchor["producer_tree"] != tree
    ):
        fail("storage import native custody differs from the actual candidate/profile")
    for field in ("producer_source_sha256", "producer_binary_sha256", "manifest_sha256", "receipt_sha256"):
        hex_digest(anchor[field])
    execution = anchor["execution_id"]
    if not isinstance(execution, str) or not 0 < len(execution.encode()) <= 256 or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in execution):
        fail("storage import native producer execution identity is invalid")
    authority, exporter, query = storage_import_source_authority()
    if anchor["producer_source_sha256"] != hashlib.sha256(exporter).hexdigest():
        fail("storage import native producer source differs from the actual exporter")
    for name, field in (("packet/manifest.json", "manifest_sha256"), ("packet/source-receipt.json", "receipt_sha256")):
        if name not in files or hashlib.sha256(files[name]).hexdigest() != anchor[field]:
            fail("storage import native packet differs from independent custody hashes")
    manifest = obj("packet/manifest.json", {"schema", "project_id", "completed", "source", "producer",
                   "total_rows", "page_rows", "members"})
    receipt = obj("packet/source-receipt.json", {"schema", "manifest_sha256", "producer", "source",
                  "row_count", "completed"} | credit)
    no_credit(receipt)
    producer = {"repository": "TrillionniumFoundation/TrillionniumGame", "commit": commit, "tree": tree,
                "source_sha256": anchor["producer_source_sha256"], "binary_sha256": anchor["producer_binary_sha256"],
                "execution_id": execution}
    if manifest["schema"] != "trillionnium.nakama-storage-native-export.v1" or manifest["project_id"] != "trillionnium-game" or (
        manifest["completed"] is not True or manifest["producer"] != producer
        or receipt["schema"] != "trillionnium.nakama-storage-source-receipt.v1"
        or receipt["completed"] is not True or receipt["producer"] != producer
        or receipt["manifest_sha256"] != anchor["manifest_sha256"] or receipt["source"] != manifest["source"]
    ):
        fail("storage import native producer/manifest/receipt identity differs")
    source = manifest["source"]
    source_keys = {"repository", "commit", "tree", "profile", "server_version", "database_identity",
                   "snapshot_identity", "isolation", "execution_class", "whole_table", "owner_references_valid"}
    if not isinstance(source, dict) or set(source) != source_keys or any(source.get(field) != expected for field, expected in {
        "repository": "heroiclabs/nakama", "commit": authority.COMMIT, "tree": authority.TREE, "profile": profile,
        "isolation": "repeatable-read-read-only" if profile == "postgresql" else "serializable-read-only",
        "execution_class": "native-source-ddl-fixture",
    }.items()) or any(source.get(field) is not True for field in ("whole_table", "owner_references_valid")):
        fail("storage import native source scope is not the closed required fixture")
    for field in ("server_version", "database_identity", "snapshot_identity"):
        value = source[field]
        if not isinstance(value, str) or not 0 < len(value) <= 256 or "://" in value or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value):
            fail("storage import native source identity is empty, oversized or unsafe")
    total, page_rows = manifest["total_rows"], manifest["page_rows"]
    # This exact CI selector retains its original main eight-row fixture.
    # Generic exporter limits and temporary one-row pages belong to the Rust
    # library/CLI; neither may silently shrink or replace this evidence lane.
    if type(total) is not int or total != 8 or type(page_rows) is not int or page_rows != 2:
        fail("storage import main native fixture requires exactly eight rows in four two-row pages")
    if type(receipt["row_count"]) is not int or receipt["row_count"] != total:
        fail("storage import native fixture row/page inventory is invalid")
    packet_paths = {"upstream/" + member[0] for member in authority.MEMBERS} | {
        "producer-source/exporter.rs", "source-catalog.json", "source-query.sql", "rows.ndjson"
    } | {f"values/{index:08}.json" for index in range(total)}
    members = manifest["members"]
    if not isinstance(members, list) or len(members) != len(packet_paths):
        fail("storage import native packet has an incomplete or duplicate inventory")
    seen = set()
    for member in members:
        if not isinstance(member, dict) or set(member) != {"path", "bytes", "sha256"} or (
            member.get("path") not in packet_paths or member["path"] in seen
        ):
            fail("storage import native packet member path is not closed")
        seen.add(member["path"])
        data = files.get("packet/" + member["path"])
        if data is None or type(member["bytes"]) is not int or member["bytes"] != len(data) or (
            hex_digest(member["sha256"]) != hashlib.sha256(data).hexdigest()
        ):
            fail("storage import native packet member bytes differ")
    if seen != packet_paths:
        fail("storage import native packet inventory is not complete")
    for member in authority.MEMBERS:
        try:
            authority.validate_bytes(files["packet/upstream/" + member[0]], member)
        except ValueError:
            fail("storage import native packet upstream bytes differ")
    if files["packet/producer-source/exporter.rs"] != exporter or files["packet/source-query.sql"] != query:
        fail("storage import native packet source/query differs from the actual candidate")
    catalog = obj("packet/source-catalog.json", {"schema", "profile", "namespace", "table", "table_type", "table_kind",
                  "collation_binding", "snapshot_identity", "columns", "constraints", "primary_key_columns",
                  "owner_foreign_key_columns", "owner_foreign_key_table", "owner_foreign_key_parent_schema",
                  "owner_foreign_key_parent_columns", "owner_foreign_key_delete_cascade", "owner_foreign_key_validated",
                  "read_nonnegative_check_validated", "write_nonnegative_check_validated"})
    if any(catalog.get(field) != expected for field, expected in {
        "schema": "trillionnium.nakama-storage-source-catalog.v1", "profile": profile,
        "namespace": "public", "table": "storage", "table_type": "BASE TABLE", "table_kind": "r",
        "snapshot_identity": source["snapshot_identity"], "primary_key_columns": ["collection", "key", "user_id"],
        "owner_foreign_key_columns": ["user_id"], "owner_foreign_key_table": "users",
        "owner_foreign_key_parent_schema": "public", "owner_foreign_key_parent_columns": ["id"],
    }.items()):
        fail("storage import native source catalog is disconnected from its actual snapshot")
    if any(catalog[field] is not True for field in ("owner_foreign_key_delete_cascade", "owner_foreign_key_validated",
                                                   "read_nonnegative_check_validated", "write_nonnegative_check_validated")):
        fail("storage import native source catalog omits its captured source constraints")
    binding = catalog["collation_binding"]
    if profile == "cockroachdb":
        valid_collation = binding == ["cockroachdb", "UTF8", "uncollated"]
    else:
        valid_collation = isinstance(binding, list) and len(binding) == 8 and binding[:3] == ["postgresql", "UTF8", "c"] and (
            binding[5:] == ["default", "d", "true"] and all(isinstance(value, str) and 0 < len(value.encode()) <= 1024
                and not any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value) for value in binding[3:5]))
    if not valid_collation:
        fail("storage import native source collation is outside the supported captured policy")
    for field, count, keys in (("columns", 9, {"name", "udt", "nullable", "character_maximum_length", "default_expression"}),
                               ("constraints", 4, {"name", "kind", "validated", "definition", "columns", "parent_columns",
                                    "parent_schema", "parent_table", "delete_action"})):
        entries = catalog[field]
        if not isinstance(entries, list) or len(entries) != count or any(not isinstance(entry, dict) or set(entry) != keys for entry in entries):
            fail("storage import native source catalog inventory is not closed")

    source_rows = decoded("source-rows.json")
    target_rows = decoded("target-rows.json")
    rows_report = obj("native-rows.json", {"schema", "profile", "source_path", "target_path",
                      "source_sha256", "target_sha256", "exact_source_projection_and_metadata_preserved",
                      "unknown_request_witnesses"} | credit)
    no_credit(rows_report)
    if rows_report["schema"] != "trillionnium.storage-import-native-rows.v1" or rows_report["profile"] != profile or (
        rows_report["source_path"] != "source-rows.json" or rows_report["target_path"] != "target-rows.json"
        or rows_report["exact_source_projection_and_metadata_preserved"] is not True
        or rows_report["unknown_request_witnesses"] is not True
    ):
        fail("storage import native row observations have the wrong scope")
    for name, field in (("source-rows.json", "source_sha256"), ("target-rows.json", "target_sha256")):
        if hex_digest(rows_report[field]) != hashlib.sha256(files[name]).hexdigest():
            fail("storage import native row preimage bytes differ from their actual digest")
    if not isinstance(source_rows, list) or not isinstance(target_rows, list) or len(source_rows) != total or len(target_rows) != total:
        fail("storage import native source/target row inventory is incomplete")
    shared = {"collection", "key", "user_id", "native_text", "public_version", "read", "write", "create_time", "update_time"}
    extras = {"projection_sha256", "value_origin", "source_manifest_sha256", "raw_value_is_null", "raw_digest_is_null", "updated_at_ms"}
    def row_key(row: object, target: bool) -> tuple[str, str, str]:
        if not isinstance(row, dict) or set(row) != shared | (extras if target else set()):
            fail("storage import native row fields are not closed")
        for field, maximum in (("collection", 128), ("key", 128), ("public_version", 32)):
            if not isinstance(row[field], str) or len(row[field]) > maximum:
                fail("storage import native key/token observation is invalid")
        if not isinstance(row["user_id"], str) or re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", row["user_id"]) is None or not isinstance(row["native_text"], str):
            fail("storage import native owner/value observation is invalid")
        for field in ("read", "write"):
            if type(row[field]) is not int or not 0 <= row[field] <= 32767:
                fail("storage import native raw permission observation is invalid")
        for field in ("create_time", "update_time"):
            time = row[field]
            if not isinstance(time, dict) or set(time) != {"seconds", "nanos"} or type(time["seconds"]) is not int or (
                not -62135596800 <= time["seconds"] <= 253402300799 or type(time["nanos"]) is not int
                or not 0 <= time["nanos"] < 1000000000 or time["nanos"] % 1000 != 0
            ):
                fail("storage import native exact timestamp observation is invalid")
        return row["collection"], row["key"], row["user_id"]
    source_by_key = {}
    for row in source_rows:
        key = row_key(row, False)
        if key in source_by_key:
            fail("storage import native source inventory duplicates a key")
        source_by_key[key] = row
    targets = set()
    for row in target_rows:
        key = row_key(row, True)
        if key not in source_by_key or key in targets or {field: row[field] for field in shared} != source_by_key[key]:
            fail("storage import native final tuple differs from its exact source observation")
        targets.add(key)
        if hex_digest(row["projection_sha256"]) != hashlib.sha256(row["native_text"].encode()).hexdigest() or (
            row["value_origin"] != "nakama-export-unknown-request" or row["source_manifest_sha256"] != anchor["manifest_sha256"]
            or row["raw_value_is_null"] is not True or row["raw_digest_is_null"] is not True
            or type(row["updated_at_ms"]) is not int or row["updated_at_ms"] != 2468
        ):
            fail("storage import native witness/provenance/audit observation differs")
    raw_rows = files["packet/rows.ndjson"]
    if not raw_rows.endswith(b"\n") or len(raw_rows.splitlines()) != total:
        fail("storage import native packet row framing is incomplete")
    seen_rows = set()
    records = []
    for ordinal, raw in enumerate(raw_rows.splitlines()):
        # Row metadata contains exact native text hashes, never JSONB content.
        record = parsed(raw)
        if not isinstance(record, dict) or set(record) != {"ordinal", "collection", "key", "user_id", "public_version",
                    "read", "write", "create_time", "update_time", "value_path", "value_sha256", "value_bytes"} or (
            type(record["ordinal"]) is not int or record["ordinal"] != ordinal
            or record["value_path"] != f"values/{ordinal:08}.json"
        ):
            fail("storage import native packet row identity is invalid")
        key = row_key({**{field: record[field] for field in shared - {"native_text"}},
                       "native_text": ""}, False)
        if key not in source_by_key or key in seen_rows:
            fail("storage import native packet/source keys are not identical")
        seen_rows.add(key)
        native_row = source_by_key[key]
        row_key({**{field: record[field] for field in shared - {"native_text"}},
                 "native_text": native_row["native_text"]}, False)
        value = files["packet/" + record["value_path"]]
        if value != native_row["native_text"].encode() or type(record["value_bytes"]) is not int or record["value_bytes"] != len(value) or (
            hex_digest(record["value_sha256"]) != hashlib.sha256(value).hexdigest()
            or any(record[field] != native_row[field] for field in shared - {"native_text"})
        ):
            fail("storage import native packet value/token/ACL/time differs from actual source bytes")
        records.append(record)

    page_count = (total + page_rows - 1) // page_rows
    summary = obj("exporter-summary.json", {"schema", "profile", "row_count", "page_count", "manifest_sha256",
                  "receipt_sha256", "producer_source_sha256", "producer_binary_sha256", "execution_class"} | credit)
    no_credit(summary)
    expected_summary = {"schema": "trillionnium.storage-export-summary.v1", "profile": profile,
                        "row_count": total, "page_count": page_count, "execution_class": "native-source-ddl-fixture",
                        **{field: anchor[field] for field in ("manifest_sha256", "receipt_sha256",
                           "producer_source_sha256", "producer_binary_sha256")},
                        **{field: False for field in credit}}
    if summary != expected_summary or any(type(summary[field]) is not int for field in ("row_count", "page_count")):
        fail("storage import native exporter summary is not its actual source packet")
    journal = obj("import-journal.json", {"schema", "profile", "jobs", "pages"} | credit)
    no_credit(journal)
    if journal["schema"] != "trillionnium.storage-import-native-journal.v1" or journal["profile"] != profile or (
        not isinstance(journal["jobs"], list) or len(journal["jobs"]) != 1
        or not isinstance(journal["pages"], list) or len(journal["pages"]) != page_count
    ):
        fail("storage import native custody journal has an incomplete inventory")
    job = journal["jobs"][0]
    job_keys = {"singleton", "manifest_sha256", "custody_sha256", "source_inventory_sha256", "target_schema_guard_sha256",
                "prefix_sha256", "source_profile", "source_snapshot", "audit_at_ms", "total_rows", "total_pages",
                "next_page", "committed_rows", "status"}
    if not isinstance(job, dict) or set(job) != job_keys:
        fail("storage import native job fields are not closed")
    guard = bytes.fromhex(hex_digest(job["target_schema_guard_sha256"]))
    def framed(raw: bytes) -> bytes:
        return len(raw).to_bytes(8, "big") + raw
    row_digests = []
    for record in records:
        preimage = b"trillionnium.storage-import-row.v1\0" + record["ordinal"].to_bytes(8, "big")
        for field in ("collection", "key", "user_id", "public_version", "value_sha256"):
            preimage += framed(record[field].encode())
        for field in ("read", "write"):
            preimage += record[field].to_bytes(2, "big", signed=True)
        for field in ("create_time", "update_time"):
            preimage += record[field]["seconds"].to_bytes(8, "big", signed=True) + record[field]["nanos"].to_bytes(4, "big")
        row_digests.append(hashlib.sha256(preimage).digest())
    inventory = hashlib.sha256(b"trillionnium.storage-import-inventory.v1\0" + b"".join(row_digests)).digest()
    manifest_digest = bytes.fromhex(anchor["manifest_sha256"])
    prefix = hashlib.sha256(
        b"trillionnium.storage-import-empty-prefix.v1\0" + manifest_digest + bytes.fromhex(anchor["receipt_sha256"])
        + inventory + guard + framed(profile.encode()) + framed(source["snapshot_identity"].encode())
        + (2468).to_bytes(8, "big", signed=True) + total.to_bytes(8, "big") + page_count.to_bytes(8, "big")
    ).digest()
    pages = []
    for index in range(page_count):
        first = index * page_rows
        count = min(page_rows, total - first)
        preimage = b"trillionnium.storage-import-page.v1\0" + manifest_digest + index.to_bytes(8, "big") + count.to_bytes(8, "big")
        for ordinal in range(first, first + count):
            preimage += ordinal.to_bytes(8, "big") + row_digests[ordinal]
        digest = hashlib.sha256(preimage).digest()
        prefix = hashlib.sha256(b"trillionnium.storage-import-page-prefix.v1\0" + prefix + digest
                                + index.to_bytes(8, "big") + first.to_bytes(8, "big") + count.to_bytes(8, "big")).digest()
        page = {"manifest_sha256": anchor["manifest_sha256"], "page_index": index, "first_ordinal": first,
                "row_count": count, "page_sha256": digest.hex(), "prefix_sha256": prefix.hex(), "audit_at_ms": 2468}
        actual = journal["pages"][index]
        if not isinstance(actual, dict) or actual != page or any(type(actual[field]) is not int for field in (
            "page_index", "first_ordinal", "row_count", "audit_at_ms"
        )):
            fail("storage import native page receipt differs from the full-source prefix calculation")
        pages.append(page)
    expected_job = {"singleton": 1, "manifest_sha256": anchor["manifest_sha256"], "custody_sha256": anchor["receipt_sha256"],
                    "source_inventory_sha256": inventory.hex(), "target_schema_guard_sha256": guard.hex(),
                    "prefix_sha256": prefix.hex(), "source_profile": profile, "source_snapshot": source["snapshot_identity"],
                    "audit_at_ms": 2468, "total_rows": total, "total_pages": page_count, "next_page": page_count,
                    "committed_rows": total, "status": 1}
    if job != expected_job or any(type(job[field]) is not int for field in (
        "singleton", "audit_at_ms", "total_rows", "total_pages", "next_page", "committed_rows", "status"
    )):
        fail("storage import native completed job differs from the full packet/custody/inventory")
    lifecycle = obj("lifecycle.json", {"schema", "profile", "begin", "first_page", "resumed", "remaining_pages",
                    "finished", "verified"} | credit)
    no_credit(lifecycle)
    if lifecycle["schema"] != "trillionnium.storage-import-native-lifecycle.v1" or lifecycle["profile"] != profile:
        fail("storage import native lifecycle scope differs")
    def progress(value: object, next_page: int, completed: bool) -> None:
        expected = {"schema": "trillionnium.storage-import-progress.v1", "manifest_sha256": anchor["manifest_sha256"],
                    "next_page": next_page, "total_pages": page_count, "total_rows": total,
                    "committed_rows": min(total, next_page * page_rows), "completed": completed,
                    "target_identity_classification": "native-system-database-and-external-scope" if profile == "postgresql"
                    else "namespace-and-external-scope-only", **{field: False for field in credit}}
        if not isinstance(value, dict) or value != expected or value["completed"] is not completed or any(
            type(value[field]) is not int for field in ("next_page", "total_pages", "total_rows", "committed_rows")
        ):
            fail("storage import native lifecycle progress differs from its actual committed prefix")
        no_credit(value)
    def page_receipt(value: object, index: int) -> None:
        expected = {field: pages[index][field] for field in (
            "page_index", "first_ordinal", "row_count", "page_sha256", "prefix_sha256"
        )}
        if not isinstance(value, dict) or set(value) != set(expected) | {"schema", "progress"} or any(
            value[field] != expected[field] for field in expected
        ) or value["schema"] != "trillionnium.storage-import-page-receipt.v1" or any(
            type(value[field]) is not int for field in ("page_index", "first_ordinal", "row_count")
        ):
            fail("storage import native committed page acknowledgement differs from actual journal bytes")
        progress(value["progress"], index + 1, False)
    progress(lifecycle["begin"], 0, False)
    page_receipt(lifecycle["first_page"], 0)
    progress(lifecycle["resumed"], 1, False)
    if not isinstance(lifecycle["remaining_pages"], list) or len(lifecycle["remaining_pages"]) != page_count - 1:
        fail("storage import native lifecycle lost or duplicated a page acknowledgement")
    for index, value in enumerate(lifecycle["remaining_pages"], 1):
        page_receipt(value, index)
    progress(lifecycle["finished"], page_count, True)
    progress(lifecycle["verified"], page_count, True)
    # These reports are actual observations of the dedicated import target,
    # independently checked against the candidate's complete immutable chain.
    lock = json.loads((ROOT / "migrations/MIGRATION_CHAIN.lock.json").read_bytes())
    chain = hashlib.sha256()
    for position, entry in enumerate(lock["profiles"][profile]["ordered_files"]):
        chain.update(position.to_bytes(8, "big"))
        chain.update(entry["path"].encode() + b"\0")
        chain.update(bytes.fromhex(entry["git_blob_sha1"]))
    identity = {"schema": "trillionnium.authoritative-schema-report.v1", "profile": profile,
                "schema_version": 4, "storage_writer_epoch": 4, "table_count": 12,
                "digest_algorithm": "ordered-path-git-blob-sha256.v1", "chain_digest": chain.hexdigest(),
                "source_commit": commit, "upgrade_source_commit": commit, "v2_apply_source_commit": commit,
                "v3_apply_source_commit": commit, "compatibility_credit": False}
    for name, applied, steps in (("target-schema-migrate.json", True, 4), ("target-schema-verify.json", False, 0)):
        report = obj(name, set(identity) | {"migration_applied", "applied_steps"})
        if report != {**identity, "migration_applied": applied, "applied_steps": steps} or (
            report["migration_applied"] is not applied or report["compatibility_credit"] is not False
            or any(type(report[field]) is not int for field in ("schema_version", "storage_writer_epoch", "table_count", "applied_steps"))
        ):
            fail("storage import target schema differs from the actual fresh migration/readonly observation")
    expected_paths = {"packet/" + path for path in packet_paths} | {
        "packet/manifest.json", "packet/source-receipt.json", "exporter-summary.json", "custody-anchors.json",
        "lifecycle.json", "source-rows.json", "target-rows.json", "native-rows.json", "import-journal.json",
        "target-schema-migrate.json", "target-schema-verify.json"
    }
    directories = {str(path.relative_to(native)) for path in native.rglob("*") if path.is_dir()}
    if set(files) != expected_paths or directories != {"packet", "packet/upstream", "packet/producer-source", "packet/values"}:
        fail("storage import native observation archive is not the complete closed inventory")


def validate_storage_live_packet(root: Path, *, profile: str, commit: str, tree: str) -> dict[str, object]:
    """Verify retained fixture bytes; this is not acceptance or Nakama parity."""
    if profile not in {"postgresql", "cockroachdb"} or any(
        re.fullmatch(r"[0-9a-f]{40}", value) is None for value in (commit, tree)
    ):
        fail("server packet target identity is invalid")

    def document(name: str) -> dict[str, object]:
        path = root / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
            fail("server packet JSON document is missing or oversized")
        def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
            value = {}
            for key, item in pairs:
                if key in value:
                    fail("server packet JSON document has duplicate keys")
                value[key] = item
            return value

        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
        if not isinstance(value, dict):
            fail("server packet JSON document must be an object")
        return value

    summary = document("summary.json")
    if any(summary.get(field) != value for field, value in {
        "schema": "trillionnium.server-live-evidence.v1",
        "repository": "TrillionniumFoundation/TrillionniumGame",
        "profile": profile, "commit": commit, "tree": tree,
    }.items()):
        fail("server packet summary target identity differs from the actual object")
    for field in (
        "check_config", "fresh_migration", "nakama_client_list_projection", "storage_occ_precedence",
        "raw_version_conditions", "storage_timestamps", "schema_upgrade", "storage_jsonb_v3_projection",
        "storage_native_jsonb", "storage_v4_acl", "storage_v4_import",
        "health_ready", "unauthenticated_mutation_rejected", "http_bootstrap_commit_duplicate_conflict",
        "websocket_json_commit", "response_loss_exact_receipt_replay", "authenticated_drain",
        "process_restart_exact_receipt_replay",
    ):
        if summary.get(field) is not True:
            fail("server packet required execution field is absent or false: " + field)
    for field in ("schema_version", "storage_writer_epoch", "authoritative_migrations_count",
                  "entity_revision", "event_sequence", "command_receipts", "events", "outbox_intents"):
        if type(summary.get(field)) is not int or summary[field] != (4 if field in ("schema_version", "storage_writer_epoch", "authoritative_migrations_count") else 3):
            fail("server packet count or schema ABI differs: " + field)
    cases = {"history": 6, "opaque_success": 4, "no_op": 2, "resource": 1, "native_input": 3}
    observed = summary.get("storage_jsonb_v3_cases")
    if not isinstance(observed, dict) or observed != cases or any(type(value) is not int for value in observed.values()):
        fail("server packet JSONB fixture case inventory differs")
    families = summary.get("schema_v3_case_families")
    if type(summary.get("schema_v3_extra_cases")) is not int or summary["schema_v3_extra_cases"] != 41 or (
        not isinstance(families, dict) or families != SCHEMA_V3_CASE_FAMILIES
        or any(type(value) is not int for value in families.values())
    ):
        fail("server packet schema v3 native scenario inventory must be the complete forty-one cases")
    for field in ("production_pitr", "multi_node", "wire_compatible", "compatibility_credit", "accepted", "production_ready"):
        if summary.get(field) is not False:
            fail("server packet must retain no-credit claim: " + field)
    # Reuse the same closed validator as native migration/restore consumers.
    # This packet retains a read-only verify report from the fresh main DB;
    # applying all four revisions happened earlier in the actual harness.
    script = Path(__file__).with_name("check-authoritative-schema-identity.py")
    spec = importlib.util.spec_from_file_location("trnm_server_schema_identity", script)
    if spec is None or spec.loader is None:
        fail("shared authoritative schema identity validator is unavailable")
    schema_validator = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(schema_validator)
        identity_path = root / "schema-identity.json"
        if identity_path.is_symlink() or not identity_path.is_file() or (
            identity_path.stat().st_size > schema_validator.MAX_IDENTITY_BYTES
        ):
            fail("server schema identity is absent, indirect or oversized")
        identity = schema_validator.decode_identity_document(identity_path.read_bytes())
        chains, version, tables = schema_validator.validated_source()
        schema_validator.validate_identity(
            identity, profile=profile, chains=chains, schema_version=version,
            table_count=tables, mode="verify",
        )
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, SyntaxError, AttributeError) as error:
        fail("server schema verify report failed shared authoritative validation: " + type(error).__name__)
    # The shared verify policy permits historical apply provenance. The main
    # database of this fresh packet has the stronger actual-candidate context.
    if any(identity[field] != commit for field in (
        "source_commit", "upgrade_source_commit", "v2_apply_source_commit", "v3_apply_source_commit"
    )):
        fail("fresh server packet schema apply provenance differs from the actual candidate")

    lock = document("migration-lock.json")
    if lock != json.loads((ROOT / "migrations/MIGRATION_CHAIN.lock.json").read_text(encoding="utf-8")):
        fail("server packet migration lock differs from the actual source")
    manifest = document("authoritative-migrations.json")
    if manifest.get("schema") != "trillionnium.server-live-schema-source.v1" or manifest.get("profile") != profile or any(
        type(manifest.get(field)) is not int or manifest[field] != 4
        for field in ("schema_version", "storage_writer_epoch")
    ) or manifest.get("compatibility_credit") is not False:
        fail("server packet SQL manifest identity or no-credit scope differs")
    names = ("0001_foundation_up.sql", "0002_storage_timestamps_up.sql", "0003_storage_jsonb_up.sql", "0004_storage_source_import_up.sql")
    archive = root / "authoritative-migrations"
    if not archive.is_dir() or {path.name for path in archive.iterdir()} != set(names):
        fail("server packet SQL archive must contain exactly the four authoritative files")
    locked = lock["profiles"][profile]["ordered_files"]
    files = manifest.get("ordered_files")
    if not isinstance(files, list) or len(files) != 4 or [entry["path"] for entry in locked] != [f"migrations/{profile}/{name}" for name in names]:
        fail("server packet must retain the complete four-file profile chain")
    for entry, authority, name in zip(files, locked, names, strict=True):
        archive_path = f"authoritative-migrations/{name}"
        if not isinstance(entry, dict) or entry.get("path") != authority["path"] or entry.get("archive_path") != archive_path:
            fail("server packet SQL archive path or profile differs")
        path = root / archive_path
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 256 * 1024:
            fail("server packet SQL file is absent, empty or oversized")
        data = path.read_bytes()
        if data != (ROOT / authority["path"]).read_bytes() or (
            entry.get("git_blob_sha1") != authority["git_blob_sha1"]
            or hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest() != authority["git_blob_sha1"]
            or entry.get("sha256") != hashlib.sha256(data).hexdigest()
            or type(entry.get("size_bytes")) is not int or entry["size_bytes"] != len(data)
        ):
            fail("server packet SQL bytes, digest or size differ from the actual source")
    log = root / "canonical-storage-app.log"
    if not log.is_file() or log.stat().st_size > 16 * 1024 * 1024:
        fail("canonical storage fixture log is absent or oversized")
    lines = log.read_text(encoding="utf-8").splitlines()
    for marker in (
        f"canonical_storage_api_live_executed profile={profile}",
        f"storage_opaque_conditions_live_executed profile={profile} write_cases=15 delete_cases=18 batch_cases=2",
        f"storage_jsonb_v3_live_executed profile={profile} history_cases=6 opaque_success_cases=4 noop_cases=2 resource_cases=1 native_input_cases=3",
    ):
        if lines.count(marker) != 1:
            fail("canonical storage fixture has a missing or duplicate execution marker")
    branch = rf"storage_jsonb_v3_native_inputs profile={profile} condition_sql=(accepted|rejected) payload_sql=(accepted|rejected) compatibility_credit=false"
    branches = [line for line in lines if line.startswith("storage_jsonb_v3_native_inputs ")]
    if len(branches) != 1 or re.fullmatch(branch, branches[0]) is None or any(
        "canonical_storage_api_live_skipped" in line for line in lines
    ):
        fail("canonical storage native SQL observations are absent or fixture skipped")
    canonical_terminal = [line for line in lines if line.startswith("test result:")]
    if len(canonical_terminal) != 1 or re.fullmatch(
        r"test result: ok\. 1 passed; 0 failed; 0 ignored;.*", canonical_terminal[0]
    ) is None:
        fail("canonical storage fixture lacks one real terminal test result")

    def execution_log(name: str, count: int, skipped: str) -> list[str]:
        path = root / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
            fail("server native fixture log is absent, indirect or oversized: " + name)
        recorded = path.read_text(encoding="utf-8").splitlines()
        if any(skipped in line for line in recorded):
            fail("server native fixture skipped instead of executing: " + name)
        terminal = [line for line in recorded if line.startswith("test result:")]
        if len(terminal) != 1 or re.fullmatch(
            rf"test result: ok\. {count} passed; 0 failed; 0 ignored;.*", terminal[0]
        ) is None:
            fail("server native fixture lacks one exact successful terminal result: " + name)
        return recorded

    schema_lines = execution_log("schema-upgrade.log", 6, "developer-only live test skip")
    if any(re.search(r"schema_v3_[a-z_]+_skipped", line) for line in schema_lines):
        fail("server schema v3 family skipped instead of executing")
    for family, count in SCHEMA_V3_CASE_FAMILIES.items():
        prefix = f"schema_v3_{family}_executed "
        recorded = [line for line in schema_lines if prefix in line]
        if recorded != [f"{prefix}profile={profile} extra_cases={count}"] or any(
            f"schema_v3_{family}_skipped" in line for line in schema_lines
        ):
            fail("server schema native scenario family has a missing, duplicate or incorrect marker: " + family)
    native_lines = execution_log("storage-native-jsonb.log", 1, "storage_native_jsonb_live_skipped")
    native_markers = [line for line in native_lines if "storage_native_jsonb_live_executed " in line]
    if native_markers != [f"storage_native_jsonb_live_executed profile={profile}"]:
        fail("native storage JSONB fixture did not execute once for the packet profile")
    acl_lines = execution_log("storage-v4-acl.log", 1, "storage_v4_acl_live_skipped")
    acl_markers = [line for line in acl_lines if "storage_v4_acl_live_executed " in line]
    if acl_markers != [f"storage_v4_acl_live_executed profile={profile}"]:
        fail("native storage v4 ACL fixture did not execute once for the packet profile")
    import_lines = execution_log(STORAGE_IMPORT_LOG, 1, "storage_v4_import_live_skipped")
    import_markers = [line for line in import_lines if "storage_v4_import_live_executed " in line]
    if import_markers != [f"storage_v4_import_live_executed profile={profile}"]:
        fail("native storage v4 import fixture did not execute once for the packet profile")
    authority, entries = storage_import_source_entries(root)
    source_manifest = document("storage-v4-import-source.json")
    expected_source_manifest = {
        "schema": "trillionnium.storage-import-source-archive.v1", "scope": "source-annex-only",
        "repository": "TrillionniumFoundation/TrillionniumGame", "commit": commit, "tree": tree,
        "profile": profile, "upstream_repository": "heroiclabs/nakama",
        "upstream_commit": authority.COMMIT, "upstream_tree": authority.TREE, "members": entries,
        "server_execution_credit": False, "compatibility_credit": False,
        "accepted": False, "production_ready": False, "full_nakama_replacement": False,
    }
    if source_manifest != expected_source_manifest or any(
        type(member.get("size_bytes")) is not int for member in source_manifest.get("members", [])
    ) or any(type(source_manifest.get(field)) is not bool for field in (
        "server_execution_credit", "compatibility_credit", "accepted", "production_ready", "full_nakama_replacement"
    )):
        fail("storage import source archive identity, inventory or no-credit scope differs")
    validate_storage_import_native_archive(root, profile=profile, commit=commit, tree=tree)
    return {"status": "trnm-server-live-packet-validated", "profile": profile,
            "schema_version": 4, "storage_writer_epoch": 4, "authoritative_migrations_count": 4,
            "storage_jsonb_v3_cases": cases, "storage_native_jsonb": True, "storage_v4_acl": True,
            "storage_v4_import": True,
            "schema_v3_extra_cases": 41, "schema_v3_case_families": SCHEMA_V3_CASE_FAMILIES.copy(),
            "compatibility_credit": False, "accepted": False,
            "production_ready": False}


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


def main(arguments: list[str] | None = None) -> int:
    if arguments:
        parser = argparse.ArgumentParser(description=__doc__)
        modes = parser.add_mutually_exclusive_group(required=True)
        modes.add_argument("--live-packet", type=Path)
        modes.add_argument("--storage-import-source-archive", type=Path)
        parser.add_argument("--profile", choices=("postgresql", "cockroachdb"), required=True)
        parser.add_argument("--commit", required=True)
        parser.add_argument("--tree", required=True)
        args = parser.parse_args(arguments)
        try:
            if args.storage_import_source_archive is not None:
                report = write_storage_import_source_archive(args.storage_import_source_archive,
                                                            profile=args.profile, commit=args.commit, tree=args.tree)
            else:
                report = validate_storage_live_packet(args.live_packet, profile=args.profile, commit=args.commit, tree=args.tree)
        except (OSError, ValueError, KeyError, TypeError):
            fail("server live packet could not be decoded or validated")
        print(json.dumps(report, sort_keys=True))
        return 0
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
    server_runtime = Path("crates/trnm-server/src/runtime")
    validate_storage_projection_source(
        sources[server_runtime / "storage_api.rs"],
        sources[server_runtime / "storage_api_tests.rs"] + "\n" + sources[server_runtime / "storage_api_v3_live.rs"],
    )
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
        "crates/trnm-persistence-pg/src/storage_parts/00_prelude.rs": (
            "postgres::fallible_iterator::FallibleIterator",
            "const MAX_VALUE_BYTES: usize = 1024 * 1024;",
            "const MAX_NATIVE_VALUE_BYTES: usize = 16 * 1024 * 1024;",
            "const MAX_RESULT_VALUE_BYTES: usize = 32 * 1024 * 1024;",
        ),
        "crates/trnm-persistence-pg/src/storage_parts/01_repository.rs": (
            "pub fn list_storage_objects_nakama(",
            "validate_client_list_request",
            ".read_only(true)",
            ".query_raw(&query, parameters)",
            "if objects.len() == limit",
            "consume_storage_result_budget(&mut result_bytes, object.object.value.len())?",
            "decode_nakama_listed_storage_object",
            "transaction.commit()",
        ),
        "crates/trnm-persistence-pg/src/storage_parts/02_list_helpers.rs": (
            "storage_client_list_public_query",
            "storage_client_list_own_query",
            "storage_client_list_foreign_query",
            '"read_permission ASC, object_key ASC, user_id ASC"',
            '"read_permission ASC, object_key ASC"',
            '"object_key ASC"',
        ),
        "crates/trnm-persistence-pg/src/storage_parts/05_decode_errors.rs": (
            "decode_nakama_listed_storage_object",
            "StorageObjectKey::new_nakama",
            "decode_storage_object_at(key, row, 2)",
        ),
        "crates/trnm-persistence-pg/src/storage_parts/06_native_projection.rs": (
            "value_jsonb::TEXT", "value_projection_digest", "public_version",
            "fn consume_storage_result_budget", "storage_resource_exhausted(",
            "storage_ordinal < ${fetch_parameter}", "ROW_NUMBER() OVER",
            "WHERE {predicate} ORDER BY {ordering} LIMIT ${fetch_parameter}",
            "fn validate_native_condition", 'query_one("SELECT $1::TEXT"',
            "FROM (SELECT $1::TEXT::JSONB::TEXT AS value) AS native_projection",
            "if length > MAX_NATIVE_VALUE_BYTES", ".checked_add(value_bytes)",
            ".filter(|value| *value <= MAX_RESULT_VALUE_BYTES)",
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
    live_workflow = (ROOT / ".github/workflows/trnm-server-live.yml").read_text(encoding="utf-8")
    prospective_workflow = (ROOT / ".github/workflows/prospective-merge-gate.yml").read_text(encoding="utf-8")
    validate_storage_live_workflow(live_workflow, prospective=False)
    validate_storage_live_workflow(prospective_workflow, prospective=True)
    require_markers("source live packet retention", live_workflow, (
        'python3 scripts/check-trnm-server.py --live-packet "$evidence"',
        '--profile "$PROFILE" --commit "$CANDIDATE_SHA" --tree "$(git rev-parse HEAD^{tree})"',
    ))
    require_markers("prospective server packet retention", prospective_workflow, (
        'python3 scripts/check-trnm-server.py --live-packet "$server"',
        '--profile "$PROFILE" --commit "$PROSPECTIVE_MERGE_SHA" --tree "$(git rev-parse HEAD^{tree})"',
    ))

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
    limits = storage_contract.get("resource_limits", {})
    if any(type(limits.get(field)) is not int or limits[field] != maximum for field, maximum in {
        "maximum_value_bytes": 1048576, "maximum_native_value_bytes": 16777216,
        "maximum_encoded_response_bytes": 33554432,
    }.items()):
        fail("storage HTTP request/native/encoded response budgets differ")
    projection = storage_contract.get("jsonb_v3_projection_source_candidate", {})
    if projection.get("schema_version") != 4 or projection.get("storage_writer_epoch") != 4 or any(
        projection.get(field) is not False for field in ("accepted", "compatibility_credit", "gap_closed", "production_ready")
    ):
        fail("storage HTTP JSONB projection must retain current v4 ABI and no-credit claims")
    condition_state = status.get("storage_http_mutations", {}).get("condition_version")
    if condition_state != (
        "Original-string ExpectedVersion: write empty is blind, write star is insert-only, "
        "other writes and all nonempty deletes are exact; schema v3 preserves independent PublicVersion tokens and ACKs use request MD5."
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
    sys.exit(main(sys.argv[1:]))
