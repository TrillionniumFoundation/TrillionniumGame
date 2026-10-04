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
sys.path.insert(0, str(ROOT / "scripts"))
import schema_evidence_binding as BINDING
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
    PERSISTENCE_ROOT / "storage_parts/91_test_batch.rs",
    PERSISTENCE_ROOT / "storage_parts/94_test_integrity.rs",
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
    ROOT / "crates/trnm-storage-core/src/nakama_sort.rs",
    ROOT / "crates/trnm-storage-core/src/nakama_batch_tests.rs",
    ROOT / "crates/trnm-server/src/runtime/app.rs",
    ROOT / "crates/trnm-server/src/runtime/cors.rs",
    ROOT / "crates/trnm-server/src/runtime/healthcheck_form.rs",
    ROOT / "contracts/http/nakama-healthcheck-form-v1.json",
    ROOT / "contracts/http/nakama-healthcheck-form-fixtures-v1.json",
    ROOT / "crates/trnm-server/src/runtime/cors_transport_tests.rs",
    ROOT / "contracts/http/nakama-cors-v1.json",
    ROOT / "contracts/http/nakama-cors-fixtures-v1.json",
    ROOT / "third_party/gorilla-handlers/LICENSE",
    ROOT / "crates/trnm-server/src/runtime/auth_runtime.rs",
    ROOT / "crates/trnm-server/src/runtime/auth_app_tests.rs",
    ROOT / "crates/trnm-server/src/runtime/pool.rs",
    ROOT / "crates/trnm-server/src/runtime/retry.rs",
    ROOT / "crates/trnm-persistence-pg/tests/storage_duplicate_batches.rs",
    ROOT / "contracts/storage/nakama-sort-source-lock-v1.json",
    ROOT / "third_party/go-sort/LICENSE",
    ROOT / "NOTICE",
    STORAGE_LIVE_HARNESS,
    LIVE_FAILURE_HELPER,
    PERSISTENCE_ROOT / "schema.rs",
    PERSISTENCE_ROOT / "schema_parts/account_catalog.rs",
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
STORAGE_DUPLICATE_LOG = "storage-duplicate-batches.log"
STORAGE_DUPLICATE_SELECTOR = "nakama_duplicate_batches_preserve_step_receipts_and_native_atomicity"
STORAGE_DUPLICATE_MARKERS = (
    (
        "nakama_duplicate_batches_executed",
        "",
    ),
    (
        "nakama_duplicate_late_json_rejection",
        " independent_probe_sqlstate=22P02 actual_batch_domain_code=InvalidArgument",
    ),
    (
        "nakama_duplicate_success_full_tuple_executed",
        " fields=15",
    ),
    (
        "nakama_duplicate_go13_executed",
        " occurrences=13 final_ordinals=a12_b0 ack_positions=original fields=15",
    ),
    (
        "nakama_duplicate_imported_history_executed",
        " source_rows=3 pages=3 witness_null=true full_tuple_fields=15 source_execution_class=native-source-ddl-fixture",
    ),
    (
        "nakama_duplicate_occurrence_locks_executed",
        " missing_delete_rejected_before_late_lock=true fields=15",
    ),
    (
        "nakama_write_tail_drain_executed",
        " held_wait_cases=2 early_reject_cases=3 fields=15",
    ),
    (
        'nakama_native_insert_only_matrix_executed',
        ' cases=3',
    ),
    (
        'nakama_native_jsonb_exact_matrix_executed',
        ' cases=11 late_exact_excluded=2 matched_wait_commit=2 missing_exact_drain_wait=1 literal_native_text=1 escaped_nul=1 both_bad_input=1 both_bad_input_vectors=2 surrogate_bind=1 duplicate_exact=1 typed_policy=2 fields=15',
    ),
    (
        'nakama_native_jsonb_exact_subvector_executed',
        ' case=both_bad_input_legal_surrogate main_case=both_bad_payload_token_native_priority input=legal_object_escaped_unpaired_surrogate fields=15 actual_domain=InvalidArgument actual_reason=database_constraint_violation retry=Never no_receipts=true same_lease_readable=true hidden_batch_sqlstate=null',
    ),
    ('nakama_native_any_matrix_executed', ' cases=4 permission_wait=1 creator_commit=1 unknown_noop=1 surrogate_priority=1 known_duplicate=3 unknown_duplicate=3 aba=2 own_delete_reinsert=1 fields=15 pg_worker_isolation=unobserved'),
    ('nakama_native_any_steps_executed', ' known_duplicate=3 unknown_duplicate=3 aba=2 own_delete_reinsert=1 fields=15'),
)
STORAGE_LATE_EXACT_PROFILE_COUNTERS = {"postgresql": (0, 2), "cockroachdb": (2, 0)}
STORAGE_LATE_EXACT_SHELL_SUFFIX = ' late_exact_wait=${storage_late_exact_wait_cases} late_exact_no_wait=${storage_late_exact_no_wait_cases}'
STORAGE_INSERT_ONLY_PROFILE_COUNTERS = {"postgresql": (0, 1), "cockroachdb": (1, 0)}
STORAGE_INSERT_ONLY_SHELL_SUFFIX = ' committed_existing_wait=${storage_insert_only_committed_wait_cases} committed_existing_no_wait=${storage_insert_only_committed_no_wait_cases} uncommitted_delete_wait=1 fields=15'


def storage_duplicate_markers(profile: str) -> tuple[tuple[str, str], ...]:
    if profile not in STORAGE_LATE_EXACT_PROFILE_COUNTERS:
        fail("unsupported native Exact profile")
    wait, no_wait = STORAGE_LATE_EXACT_PROFILE_COUNTERS[profile]
    insert_wait, insert_no_wait = STORAGE_INSERT_ONLY_PROFILE_COUNTERS[profile]
    def bound_suffix(marker: str, suffix: str) -> str:
        if marker == "nakama_native_jsonb_exact_matrix_executed":
            return suffix + f" late_exact_wait={wait} late_exact_no_wait={no_wait}"
        if marker == "nakama_native_insert_only_matrix_executed":
            return suffix + (f" committed_existing_wait={insert_wait} committed_existing_no_wait={insert_no_wait}"
                             " uncommitted_delete_wait=1 fields=15")
        return suffix
    return tuple((marker, bound_suffix(marker, suffix)) for marker, suffix in STORAGE_DUPLICATE_MARKERS)


def validate_late_exact_policy_counters(policy: dict[str, object]) -> None:
    for field in ("late_exact_wait_cases", "late_exact_no_wait_cases"):
        observed = policy.get(field)
        if not isinstance(observed, dict) or set(observed) != {"postgresql", "cockroachdb"} or any(
            type(value) is not int for value in observed.values()
        ):
            fail("native Exact policy profile counters must be integers")


def validate_insert_only_policy_counters(policy: dict[str, object]) -> None:
    for field in ("insert_only_committed_existing_wait_cases",
                  "insert_only_committed_existing_no_wait_cases",
                  "insert_only_uncommitted_delete_wait_cases"):
        observed = policy.get(field)
        if not isinstance(observed, dict) or set(observed) != {"postgresql", "cockroachdb"} or any(
            type(value) is not int for value in observed.values()
        ):
            fail("native insert-only policy profile counters must be integers")


def storage_duplicate_shell_markers() -> tuple[tuple[str, str], ...]:
    return tuple((marker, suffix + (STORAGE_LATE_EXACT_SHELL_SUFFIX
                  if marker == "nakama_native_jsonb_exact_matrix_executed" else
                  STORAGE_INSERT_ONLY_SHELL_SUFFIX if marker == "nakama_native_insert_only_matrix_executed" else ""))
                 for marker, suffix in STORAGE_DUPLICATE_MARKERS)


STORAGE_HOMOGENEOUS_POLICY = {
    "status": "source-candidate",
    "maximum_occurrences": 100,
    "sort_source_lock": "contracts/storage/nakama-sort-source-lock-v1.json",
    "sort_profile": "go1.26.5-sort.Sort-canonical-owner-tuples",
    "public_owner": "canonical-session-uuid",
    "duplicate_key_policy": "preserve-every-write-or-delete-occurrence",
    "write_ack_order": "original-input-ordinal",
    "step_validation": "fresh-per-occurrence-acl-occ-native-current-row",
    "atomicity": "whole-batch-commit-or-rollback",
    "typed_mixed_duplicate_keys": "reject",
    "batch_read_duplicate_keys": "reject",
    "automatic_mutation_retry": False,
    "runtime_raw_owner_representation_parity": False,
    "hook_index_qualification": False,
    "native_lock_schedule_qualified": False,
    "accepted": False,
    "compatibility_credit": False,
    "production_ready": False,
    "full_nakama_replacement": False,
    "row_lock_policy": "nakama-per-occurrence-only-typed-unique-locks-unchanged",
    'write_tail_policy': 'some-write-first-acl-or-exact-rejection-real-tail-until-hard-error',
    'write_tail_semantic_conditions': ['write-acl-rejection-after-native-jsonb-bind', 'exact-mismatch-after-native-jsonb-and-text-bind'],
    'write_tail_hard_errors': 'must-not-exist-existing-native-data-loss-resource-stop',
    'write_tail_primary_error': 'first-inner-semantic-rejection-existing-outer-pool-budget-may-override',
    'write_tail_cleanup': 'explicit-rollback-before-error-no-confirmation-on-rollback-failure',
    'write_tail_rollback_failure': 'retire-pooled-lease-before-return-recycling-direct-client-unchanged',
    'native_write_prequeue_qualified': False,
    'native_preparation_query_group_qualified': False,
    'native_failing_occurrence_jsonb_bind_priority_qualified': False,
    'conditional_exact_lock_footprint_qualified': False,
    'native_isolation_retry_qualified': False,
    'native_jsonb_binding': 'formattext-original-request-bytes-before-host-acl-occ',
    'native_condition_binding': 'formattext-raw-text-before-host-acl-occ-no-varchar32-input-cast',
    'exact_acquisition_policy': 'token-and-server-or-write1-forupdate-with-nonlocking-rejected-fallback',
    'exact_update_policy': 'token-and-server-or-write1-native-jsonb-parameter4',
    'native_write_constraint_facade': 'write-invalidargument-database-constraint-violation-http500-code13',
    'native_jsonb_exact_main_cases': 11,
    'native_jsonb_exact_legal_surrogate_subvectors': 1,
    'native_write_failure_app_cases': 1,
    'late_exact_excluded_semantics': 'conditional-eligibility-exclusion-not-absence-of-native-wait',
    'late_exact_wait_cases': {'postgresql': 0, 'cockroachdb': 2},
    'late_exact_no_wait_cases': {'postgresql': 2, 'cockroachdb': 0},
    'literal_nul_condition_tail_policy': {'postgresql': 'native-text-hard-rejection-no-tail', 'cockroachdb': 'native-text-accepted-first-acl-rejection-real-later-any-wait'},
    'insert_only_acquisition_policy': 'no-existing-row-select-native-jsonb-projection-then-plain-insert',
    'insert_only_unique_error_policy': 'plain-insert-native-unique-hard-stop-keeps-existing-version-rejection',
    'insert_only_native_main_cases': 3,
    'insert_only_committed_existing_wait_cases': {'postgresql': 0, 'cockroachdb': 1},
    'insert_only_committed_existing_no_wait_cases': {'postgresql': 1, 'cockroachdb': 0},
    'insert_only_uncommitted_delete_wait_cases': {'postgresql': 1, 'cockroachdb': 1},
    'native_insert_only_statement_schedule_qualified': False,
    'any_acquisition_policy': {'postgresql': 'honest-insert15-reservation-or-retained-conflict-lock-real-prior', 'cockroachdb': 'honest-insert15-reservation-then-skinny-acl-and-real-prior-forupdate'},
    'any_native_digest_policy': 'native-jsonb-text-utf8-sha25632-with-profile-byte-adapter',
    'any_previous_receipt_policy': 'returning-one-insert-none-otherwise-actual-native-prior',
    'any_update_policy': 'locked-prior-bound-insert-select-onconflict-authorized-substantive-update',
    'any_unknown_noop_policy': 'matching-token-read-write-preserves-complete-native-fifteen-tuple',
    'any_batch_isolation_policy': {'postgresql': 'read-committed-only-validated-nonempty-all-any-some-write', 'cockroachdb': 'serializable', 'other_batches': 'serializable'},
    'any_native_main_cases': 4,
    'any_known_duplicate_occurrences': 3,
    'any_unknown_duplicate_occurrences': 3,
    'any_aba_occurrences': 2,
    'any_delete_reinsert_cases': 1,
    'native_any_statement_schedule_qualified': False,
    'native_any_isolation_retry_qualified': False,
}

REQUIRED_TESTS = {
    'any_batch_isolation_is_explicitly_pg_all_any_and_bounded',
    'any_batch_mixed_literal_exact_star_and_delete_never_select_rc',
    'any_occurrence_route_is_only_canonical_write_any',
    'any_unknown_noop_keeps_different_native_payload_and_private_custody',
    'any_conflict_verifies_known_projection_and_raw_request_identity',
    'any_returning_requires_actual_raw_origin_manifest_clock_and_acl',
    'any_returning_preserves_nullable_prior_create_and_rejects_fake_insert_time',
    'any_private_full_row_equality_does_not_drop_custody_or_legacy_time',
    'any_conflict_acquisition_preserves_acl_boundary_and_native_profile_lock',
    "nakama_insert_only_route_excludes_typed_any_exact_and_delete_policies",
    "native_insert_only_unique_rejection_is_scoped_to_plain_nakama_insert",
    'raw_jsonb_text_binding_preserves_bytes_and_rejects_wrong_type_or_budget',
    'raw_condition_text_binding_preserves_long_unicode_star_empty_and_nul',
    'storage_write_native_constraint_errors_are_internal_at_http_boundary',
    'storage_write_native_constraint_guard_requires_exact_reason_and_code',
    'storage_native_constraint_guard_preserves_delete_read_and_host_validation',
    'storage_write_lone_surrogate_object_reaches_repository_without_normalization',
    STORAGE_DUPLICATE_SELECTOR,
    "nakama_two_client_writes_keep_occurrence_receipts_and_last_step_state",
    "nakama_three_server_writes_bypass_step_acl_without_collapsing_acks",
    "nakama_exact_condition_observes_the_preceding_same_key_insert",
    "nakama_step_acl_occ_and_insert_only_failures_restore_the_whole_model",
    "nakama_duplicate_client_delete_rolls_back_and_server_missing_is_explicit_noop",
    "nakama_policy_is_homogeneous_and_internal_mixed_policy_remains_distinct",
    "thirteen_occurrences_follow_observed_go_order_without_reordering_acks",
    "duplicate_write_occurrences_return_independent_acks_in_original_positions",
    "duplicate_client_delete_reports_combined_rejection_and_restores_original_model",
    "duplicate_chained_exact_and_later_acl_failure_use_occurrence_state",
    "nakama_homogeneous_storage_batches_are_not_implicitly_retried",
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

REQUIRED_TESTS.update({'durable_mode_preserves_old_enablement_and_never_falls_back', 'accidental_public_bind_and_implicit_plaintext_database_fail_closed', 'legacy_key_limits_reuse_actual_provider_without_durable_minimum', 'authority_profiles_reject_every_mixed_material_field', 'grpc_bind_is_optional_distinct_and_public_bind_requires_opt_in', 'legacy_authentication_and_complete_decode_precede_native_and_cache_mutation', 'legacy_check_config_is_pure_and_serve_rejects_before_repository_setup', 'legacy_explicit_twenty_and_twenty_seven_byte_keys_are_redacted', 'legacy_material_requires_explicit_authority_mode', 'shared_legacy_runtime_revocation_crosses_app_and_never_uses_family_calls', 'wrong_authority_never_falls_back_or_calls_native_accounts', 'request_response_debug_and_static_challenge_cannot_disclose_secrets', 'legacy_mode_requires_explicit_accounts_five_target', 'legacy_mode_requires_all_five_operator_key_and_ttl_values', 'secrets_and_source_identity_are_strictly_validated', 'authority_modes_are_closed_and_default_is_disabled', 'session_auth_is_explicit_bounded_and_redacted', 'legacy_single_session_is_bounded_boolean_with_explicit_false_default', 'verify_full_tls_is_secure_by_default_and_material_is_paired', 'legacy_refresh_reads_once_and_invalid_logout_second_token_changes_no_cache', 'durable_mode_rejects_each_missing_profile_field', 'default_candidate_config_is_loopback_bounded_and_redacted', 'legacy_ttls_reject_zero_parse_overflow_and_actual_duration_overflow', 'session_auth_rejects_partial_or_noncanonical_key_material', 'selected_legacy_guard_is_static_and_never_uses_durable_verifier', 'schema_target_syntax_is_closed_and_defaults_to_storage_four', 'operator_messages_are_static_and_secret_free', 'pool_and_timeout_bounds_fail_closed', 'every_legacy_post_query_variant_is_rejected_by_drain_before_parsing', 'selected_legacy_drain_rejects_before_native_and_blacklist_mutation'})
REQUIRED_TESTS.add('legacy_transport_drain_preserves_gateway_code_for_every_post_query_variant')

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
        (
            'cargo test -p trnm-persistence-pg --locked --test storage_duplicate_batches '
            + STORAGE_DUPLICATE_SELECTOR,
            STORAGE_DUPLICATE_LOG, "storage_duplicate_batches_test_count",
            "nakama_duplicate_batches_executed", "nakama_duplicate_batches_skipped",
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
        if logfile == STORAGE_DUPLICATE_LOG:
            environment += (
                'TRNM_SCHEMA_UPGRADE_ADMIN_DATABASE_URL="$database_url" '
                'TRNM_STORAGE_PINNED_UPSTREAM_DIRECTORY="$evidence_absolute/storage-source-upstream" '
                'TRNM_STORAGE_TEST_PRODUCER_COMMIT="$candidate_sha" '
                'TRNM_STORAGE_TEST_PRODUCER_TREE="$candidate_tree" '
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
            homogeneous = once(
                'grep -Fxq "storage_homogeneous_app_executed profile=${profile} '
                'write_occurrences=13 rollback_cases=3" "$evidence/canonical-storage-app.log"'
            )
            homogeneous_unique = once(
                'test "$(grep -Ec \'^storage_homogeneous_app_executed \' '
                '"$evidence/canonical-storage-app.log")" -eq 1'
            )
            native_failure = once(
                'grep -Fxq "storage_jsonb_native_write_failure_executed profile=${profile} '
                'cases=1 fields=15 sqlstate=22P02" "$evidence/canonical-storage-app.log"'
            )
            native_failure_unique = once(
                'test "$(grep -Ec \'^storage_jsonb_native_write_failure_executed \' '
                '"$evidence/canonical-storage-app.log")" -eq 1'
            )
            if not opaque < jsonb < native_inputs < homogeneous < homogeneous_unique < native_failure < native_failure_unique < reject_skip:
                fail("storage JSONB and homogeneous App markers must bind actual cases to the canonical fixture")
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
        if logfile == STORAGE_DUPLICATE_LOG:
            profile_guard = (
                'case "$profile" in',
                'postgresql) storage_late_exact_wait_cases=0; storage_late_exact_no_wait_cases=2 ;;',
                'cockroachdb) storage_late_exact_wait_cases=2; storage_late_exact_no_wait_cases=0 ;;',
                "*) echo 'unsupported storage native Exact profile' >&2; exit 1 ;;",
                'esac',
            )
            start_guard = once('begin_stage storage-duplicate-batches "$evidence/storage-duplicate-batches.log"')
            if commands[start_guard + 1:start_guard + 1 + len(profile_guard)] != list(profile_guard):
                fail("storage duplicate native Exact profile counters must remain closed and before execution")
            insert_profile_guard = (
                'case "$profile" in',
                'postgresql) storage_insert_only_committed_wait_cases=0; storage_insert_only_committed_no_wait_cases=1 ;;',
                'cockroachdb) storage_insert_only_committed_wait_cases=1; storage_insert_only_committed_no_wait_cases=0 ;;',
                "*) echo 'unsupported storage native insert-only profile' >&2; exit 1 ;;",
                'esac',
            )
            insert_guard_start = start_guard + 1 + len(profile_guard)
            if commands[insert_guard_start:insert_guard_start + len(insert_profile_guard)] != list(insert_profile_guard):
                fail("storage duplicate insert-only profile counters must remain closed and before execution")
            terminal = once('test "$(grep -Ec \'^test result:\' '
                            '"$evidence/storage-duplicate-batches.log")" -eq 1')
            if not exact < terminal < executed:
                fail("storage duplicate fixture must have one successful terminal result")
            previous_marker = terminal
            for expected_marker, suffix in storage_duplicate_shell_markers():
                marker_position = once(
                    f'grep -Fxq "{expected_marker} profile=${{profile}}{suffix}" '
                    f'"$evidence/{logfile}"'
                )
                unique_position = once(
                    f'test "$(grep -Ec \'^{expected_marker} \' '
                    f'"$evidence/{logfile}")" -eq 1'
                )
                if not previous_marker < marker_position < unique_position < reject_skip:
                    fail("storage duplicate fixture requires all finite unique whole-line profile markers")
                previous_marker = unique_position
            insert_skip = once("if grep -Fq 'nakama_native_insert_only_skipped' \"$evidence/storage-duplicate-batches.log\"; then")
            insert_skip_body = ["echo 'storage insert-only database lane skipped instead of executing' >&2", "exit 1", "fi"]
            if not previous_marker < insert_skip < reject_skip or commands[insert_skip + 1:insert_skip + 4] != insert_skip_body:
                fail("storage insert-only skip must terminate the owned fixture before summary credit")
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
    require_markers("server fullsource5 proof", source, ("scripts/capture-schema-source-selection.py", "schema-source-capture.json", "binding.verify_binding(Path('.'),profile=profile)"))
    require_markers("server schema v3 archived chain", source, (
        "identity['schema_version']==4 and identity['storage_writer_epoch']==4",
        "identity['source_commit']==identity['upgrade_source_commit']==identity['v2_apply_source_commit']==identity['v3_apply_source_commit']==candidate",
        "assert type(lock['schema_version']) is int and lock['schema_version']==5",
        "files=binding.operational_binding(token)['ordered_files']",
        "assert len(lock['profiles'][profile]['ordered_files'])==5",
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
        '"storage_v4_import":true', '"storage_homogeneous_batches":true', '"storage_homogeneous_app":true', '"storage_write_tail_drain":true', '"storage_native_jsonb_exact":true', '"storage_jsonb_native_write_failure":true',
        '"late_exact_wait_cases":${storage_late_exact_wait_cases}',
        '"late_exact_no_wait_cases":${storage_late_exact_no_wait_cases}',
        '"storage_native_insert_only":true',
        '"storage_native_any":true', '"any_native_main_cases":4',
        '"any_known_duplicate_occurrences":3', '"any_unknown_duplicate_occurrences":3',
        '"any_aba_occurrences":2', '"any_delete_reinsert_cases":1',
        '"insert_only_committed_wait_cases":${storage_insert_only_committed_wait_cases}',
        '"insert_only_committed_no_wait_cases":${storage_insert_only_committed_no_wait_cases}',
        '"insert_only_uncommitted_delete_wait_cases":1',
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
    token = BINDING.verify_binding(ROOT, profile=profile)
    BINDING.validate_annex_directory(token, root, commit=commit, tree=tree)
    # This annex proves source frontier only. Native storage transfer remains
    # its original schema4 packet and admits no account rows or target5 state.
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
    for position, entry in enumerate(BINDING.operational_binding(token)["ordered_files"]):
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
        "storage_homogeneous_batches", "storage_homogeneous_app", "storage_write_tail_drain",
        "storage_native_jsonb_exact", "storage_jsonb_native_write_failure", "storage_native_insert_only", "storage_native_any",
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
    wait, no_wait = STORAGE_LATE_EXACT_PROFILE_COUNTERS[profile]
    for field, expected in (("late_exact_wait_cases", wait), ("late_exact_no_wait_cases", no_wait)):
        if type(summary.get(field)) is not int or summary[field] != expected:
            fail("server packet native Exact profile counter differs: " + field)
    insert_wait, insert_no_wait = STORAGE_INSERT_ONLY_PROFILE_COUNTERS[profile]
    for field, expected in (("insert_only_committed_wait_cases", insert_wait),
                            ("insert_only_committed_no_wait_cases", insert_no_wait),
                            ("insert_only_uncommitted_delete_wait_cases", 1)):
        if type(summary.get(field)) is not int or summary[field] != expected:
            fail("server packet native insert-only profile counter differs: " + field)
    for field, expected in (("any_native_main_cases", 4), ("any_known_duplicate_occurrences", 3),
                            ("any_unknown_duplicate_occurrences", 3), ("any_aba_occurrences", 2),
                            ("any_delete_reinsert_cases", 1)):
        if type(summary.get(field)) is not int or summary[field] != expected:
            fail("server packet native Any case counter differs: " + field)
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
        verified_binding = BINDING.verify_binding(ROOT, profile=profile)
        BINDING.validate_annex_directory(verified_binding, root, commit=commit, tree=tree)
        selection = BINDING.selection_token(verified_binding)
        schema_validator.validate_identity(
            identity, profile=profile, selection=selection, mode="verify",
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
    proof = BINDING.binding_document(verified_binding)
    full_validation = document("migration-chain-validation.json")
    if not BINDING.same(full_validation, proof["source_selection"]["source"]["complete_validation"]):
        fail("server fullsource5 all10 SQL validation differs from issued source proof")
    locked = BINDING.operational_binding(verified_binding)["ordered_files"]
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
        f"storage_homogeneous_app_executed profile={profile} write_occurrences=13 rollback_cases=3",
        f"storage_jsonb_native_write_failure_executed profile={profile} cases=1 fields=15 sqlstate=22P02",
    ):
        if lines.count(marker) != 1:
            fail("canonical storage fixture has a missing or duplicate execution marker")
    homogeneous_app = [line for line in lines if "storage_homogeneous_app_executed " in line]
    if homogeneous_app != [f"storage_homogeneous_app_executed profile={profile} write_occurrences=13 rollback_cases=3"]:
        fail("canonical homogeneous App marker must occur once for the packet profile")
    native_failure = [line for line in lines if "storage_jsonb_native_write_failure_executed " in line]
    if native_failure != [f"storage_jsonb_native_write_failure_executed profile={profile} cases=1 fields=15 sqlstate=22P02"]:
        fail("canonical native write failure marker must occur once for the packet profile")
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
    duplicate_lines = execution_log(STORAGE_DUPLICATE_LOG, 1, "nakama_duplicate_batches_skipped")
    if any("nakama_native_any_skipped" in line for line in duplicate_lines):
        fail("native Any fixture skipped instead of executing")
    if any("nakama_native_insert_only_skipped" in line for line in duplicate_lines):
        fail("native insert-only fixture skipped instead of executing")
    for marker, suffix in storage_duplicate_markers(profile):
        recorded = [line for line in duplicate_lines if marker + " " in line]
        if recorded != [f"{marker} profile={profile}{suffix}"]:
            fail("homogeneous storage fixture has a missing, duplicate or incorrect marker: " + marker)
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
            "storage_v4_import": True, "storage_homogeneous_batches": True, "storage_homogeneous_app": True, "storage_write_tail_drain": True,
            "storage_native_jsonb_exact": True, "storage_jsonb_native_write_failure": True,
            "storage_native_insert_only": True,
            "storage_native_any": True, "any_native_main_cases": 4,
            "any_known_duplicate_occurrences": 3, "any_unknown_duplicate_occurrences": 3,
            "any_aba_occurrences": 2, "any_delete_reinsert_cases": 1,
            "schema_v3_extra_cases": 41, "schema_v3_case_families": SCHEMA_V3_CASE_FAMILIES.copy(),
            "compatibility_credit": False, "accepted": False,
            "production_ready": False}


def validate_homogeneous_storage_contract(contract: dict[str, object], source_lock: dict[str, object],
                                          license_data: bytes, notice: str, sorter: str) -> None:
    """Closed bounded canonical subset and BSD custody, not compatibility evidence."""
    policy = contract.get("homogeneous_mutation_batches")
    if not isinstance(policy, dict) or policy != STORAGE_HOMOGENEOUS_POLICY or any(
        type(policy.get(field)) is not type(value)
        for field, value in STORAGE_HOMOGENEOUS_POLICY.items()
    ):
        fail("homogeneous storage policy must retain its exact canonical scope, bounds and no-credit flags")
    validate_late_exact_policy_counters(policy)
    validate_insert_only_policy_counters(policy)
    commit = "c19862e5f8415b4f24b189d065ed739517c548ba"
    expected_files = [
        (
            "VERSION", 35,
            "11b4fb14680701f98ca60fd8464a836ca4374f17896a125f4510ab6ae8cecc9b",
            "9b642ac23a6b1ef47713901720fb8d2b41139eda",
        ),
        (
            "LICENSE", 1453,
            "911f8f5782931320f5b8d1160a76365b83aea6447ee6c04fa6d5591467db9dad",
            "2a7cf70da6e498df9c11ab6a5eaa2ddd7af34da4",
        ),
        (
            "src/sort/sort.go", 10503,
            "100e49103d23dd6b8335a50b17ae6e9db2b056cb1217d964dcf58852c3c6f8a1",
            "087e7d033dd2356beda5c38f4d42ea4029496baa",
        ),
        (
            "src/sort/zsortinterface.go", 11485,
            "8978a49cc4174b0c35a84a2ddf73175d41254eb340ebeda29f21d3e546088a3b",
            "51fa5032e9912a8d7db6005dd1d11291b422a312",
        ),
    ]
    expected_entries = [
        {"path": path, "url": f"https://raw.githubusercontent.com/golang/go/{commit}/{path}",
         "commit": commit, "size": size, "sha256": sha, "git_blob_sha1": blob, "downloaded": True}
        for path, size, sha, blob in expected_files
    ]
    expected = {
        "source_commit": commit, "files": expected_entries,
        "schema": "trillionnium.nakama-storage-sort-source-lock.v1",
        "purpose": "Modified bounded Rust ordinal sorter for canonical-owner homogeneous storage batches",
        "runtime_owner_representation_parity": False, "nakama_native_differential_accepted": False,
        "source_limits": [
            "Canonical principal UUID ordering only; runtime raw owner representation remains open.",
            "Maximum 100 occurrences; larger upstream batches remain outside this candidate bound.",
            "Go stdlib observations and Rust execution do not constitute whole Nakama/SDK/native compatibility acceptance.",
        ],
        "modified_rust_path": "crates/trnm-storage-core/src/nakama_sort.rs",
        "license_path": "third_party/go-sort/LICENSE",
    }
    if source_lock != expected or any(
        type(source_lock.get(field)) is not bool
        for field in ("runtime_owner_representation_parity", "nakama_native_differential_accepted")
    ) or any(type(entry.get("size")) is not int or type(entry.get("downloaded")) is not bool
             for entry in source_lock.get("files", [])):
        fail("bounded Go sorter source lock differs from the pinned source or overclaims its scope")
    if len(license_data) != 1453 or hashlib.sha256(license_data).hexdigest() != (
        "911f8f5782931320f5b8d1160a76365b83aea6447ee6c04fa6d5591467db9dad"
    ):
        fail("Go sorter BSD license must retain the complete pinned original bytes")
    require_markers("Go sorter source attribution", sorter, (
        "// Copyright 2009 The Go Authors. All rights reserved.",
        "// Copyright 2022 The Go Authors. All rights reserved.", "// Modified:",
        commit, "src/sort/sort.go", "src/sort/zsortinterface.go", "third_party/go-sort/LICENSE",
        "contracts/storage/nakama-sort-source-lock-v1.json", "if data.len() > 100",
    ))
    require_markers("Go sorter NOTICE", notice, (
        "Copyright 2009 and 2022", "The Go Authors", commit,
        "third_party/go-sort/LICENSE", "contracts/storage/nakama-sort-source-lock-v1.json",
    ))


def validate_homogeneous_storage_source(core: str, repository: str, wire: str,
                                        pool: str, retry: str) -> None:
    """Bind the occurrence seam without asserting upstream lock timing."""
    require_markers("homogeneous storage core", core, (
        "mod nakama_sort;", "mod nakama_batch_tests;", "const MAX_BATCH_OPERATIONS: usize = 100;",
        "pub enum NakamaBatchKind", "pub fn plan_nakama_batch(",
        '"mixed_nakama_storage_batch"', "nakama_sort::go1265_sort_ordinals",
        "operations[left].key() < operations[right].key()", "pub fn apply_nakama_batch_projected<F>",
        "for ordinal in order", "receipts[ordinal] = Some(receipt)",
    ))
    require_markers("homogeneous storage repository", repository, (
        "pub fn apply_storage_batch_nakama_with_metadata(", "Some(kind)",
        "plan_nakama_batch(operations, kind)?", "for ordinal in order", "if kind.is_none() {",
        "lock_storage_access(&mut transaction, operation.key())?",
        "load_for_update(&mut transaction, operation.key(), self.profile)?",
        "receipts[ordinal] = Some(receipt)", "transaction.commit()",
    ))
    if "lock_nakama_storage_key(" in repository:
        fail("homogeneous Nakama policy must not reintroduce the all-unique prelock pass")
    require_markers("homogeneous storage wire", wire, (
        "repository.apply_storage_batch_nakama(", "NakamaBatchKind::Write", "NakamaBatchKind::Delete",
    ))
    require_markers("homogeneous storage pool", pool, (
        "fn apply_storage_batch_nakama(", "self.run(|repository|",
        "repository.apply_storage_batch_nakama_with_metadata(",
    ))
    require_markers("homogeneous storage retry", retry, (
        "fn apply_storage_batch_nakama(", "self.inner", "apply_storage_batch_nakama(actor, operations, updated_at_ms, kind)",
        "fn nakama_homogeneous_storage_batches_are_not_implicitly_retried()",
    ))


def validate_nakama_write_tail_source(repository: str, pool_base: str, fixture: str) -> None:
    """Bind the narrow real-tail seam; no preparation/lock-schedule acceptance."""
    require_markers("Nakama write tail repository", repository, (
        "let mut first_write_rejection = None;",
        "if kind == Some(NakamaBatchKind::Write)",
        "acquire_nakama_write_access(&mut transaction, actor, write)?",
        "match validate_nakama_write_step(actor, write, access)?",
        "WriteStepValidation::Semantic(rejection)",
        "first_write_rejection.get_or_insert(rejection)",
        "Err(hard_error) => {", "let _tail_stop_error = hard_error;",
        "rollback_nakama_write_rejection(transaction, primary)",
        "if rollback_error.is_some() {", "self.client.retire();",
        "enum WriteStepValidation {", "Semantic(DomainError)",
        "fn validate_nakama_write_step(", "fn rollback_nakama_write_rejection(",
        "transaction.rollback().err().map(map_postgres_error)",
    ))
    step = repository.split("fn validate_nakama_write_step(", 1)[1].split(
        "fn rollback_nakama_write_rejection(", 1)[0]
    require_markers("Nakama write semantic classification", step, (
        "Err(rejection) if rejection == write_permission_error()",
        "Err(hard_error) => return Err(hard_error)",
        "VersionCheck::Any => Ok(WriteStepValidation::Ready)",
        "VersionCheck::MustNotExist => Err(error(",
        "VersionCheck::Exact(_) => Ok(WriteStepValidation::Semantic(version_error()))",
    ))
    if "validate_native_condition(" in step or "transaction:" in step:
        fail("SomeWrite host rejection must not rebind TEXT after ACL")
    if repository.index("acquire_nakama_write_access(&mut transaction, actor, write)?") > repository.index(
        "match validate_nakama_write_step(actor, write, access)?"
    ):
        fail("SomeWrite native acquisition must precede host semantic classification")
    if repository.index("let receipts = receipts") < repository.index(
        "if let Some(primary) = first_write_rejection {\n"
    ):
        fail("write tail semantic rejection must roll back before receipt assembly")
    require_markers("Nakama write tail native fixture", fixture, (
        "write_tail_drain::exercise(&url, profile, &collection);",
        "mod write_tail_drain {", "Case::AclWait,", "Case::ExactWait,",
        "Case::CreateOnlyStops,", "Case::AclNativeStops,", "Case::NativeStops,",
        "exercise_case(url, profile, collection, case);",
        "nakama_write_tail_drain_executed profile={} held_wait_cases=2 early_reject_cases=3 fields=15",
    ))
    require_markers("Nakama write rollback pooled retirement", pool_base, (
        "pub(crate) fn retire(&self)", "if let Some(retired) = self.retirement_flag()",
        "retired.store(true, Ordering::Release)",
        "connection.retired.load(Ordering::Acquire) || self.inner.has_broken(&mut connection.client)",
        "Self::Direct(_) => None",
    ))



def validate_native_exact_fixture_local_policy(fixture: str) -> None:
    """Close the finite fixture's local wait/query branches, without execution credit."""
    if len(fixture.encode("utf-8")) > 1024 * 1024:
        fail("native Exact fixture source exceeds its local policy parsing budget")
    waits = re.findall(
        r"(?m)^[ \t]*const fn waits\(self, profile: DatabaseProfile\) -> bool \{\n"
        r"([\s\S]*?)^[ \t]{12}\}\n[ \t]{8}\}\n[ \t]{8}enum Expected",
        fixture,
    )
    expected_waits = """match self {
        Self::ClientExactMatched | Self::ServerExactMatchedWriteZero | Self::ExactMissing => true,
        Self::AclLateExactStale | Self::AclLateExactWriteZero | Self::ExactNulAclZero => {
            matches!(profile, DatabaseProfile::CockroachDb)
        }
        _ => false,
    }"""
    # Only formatter whitespace in this Rust policy is ignored. Original
    # fixture/SQL/query/observation bytes remain unchanged and hash-bound.
    compact = lambda value: re.sub(r"\s+", "", value)
    if len(waits) != 1 or compact(waits[0]) != compact(expected_waits):
        fail("native Exact fixture local waits body differs from the finite profile policy")
    cases = re.findall(
        r"(?m)^[ \t]{8}fn exercise_case\(\n([\s\S]*?)^[ \t]{8}fn duplicate_exact\(",
        fixture,
    )
    if len(cases) != 1:
        fail("native Exact fixture exercise-case source boundary missing or duplicated")
    case = cases[0]
    # This reviewed finite exercise body has no block comments or raw/multiline
    # literals. Reject those structures before counting executable headers so
    # they cannot supply a fake call site. This is a source seam restriction,
    # not a general Rust lexer or an execution/oracle claim.
    if "/*" in case or "*/" in case:
        fail("native Exact exercise-case block comments may not supply policy sites")
    if re.search(r'\b(?:br|cr|r)#{0,255}"', case):
        fail("native Exact exercise-case raw literals are outside the finite policy")
    literals = re.finditer(r'"(?:\\[\s\S]|[^"\\])*"', case)
    if any("\n" in literal.group() for literal in literals):
        fail("native Exact exercise-case multiline literals may not supply policy sites")
    selectors = re.findall(
        r"(?m)^[ \t]*let expected_query = ([\s\S]*?)^[ \t]*let before = snapshot\(&mut control, collection\);",
        case,
    )
    expected_selector = """if matches!(
        case, MatrixCase::ClientExactMatched | MatrixCase::ServerExactMatchedWriteZero
    ) || (matches!(profile, DatabaseProfile::CockroachDb)
        && matches!(case, MatrixCase::AclLateExactStale | MatrixCase::AclLateExactWriteZero)) {
        EXACT_ACCESS_SQL
    } else if case.typed() {
        ACCESS_SQL
    } else {
        native_any_upsert::query_kind()
    };"""
    if len(selectors) != 1 or compact(selectors[0]) != compact(expected_selector):
        fail("native Exact fixture local expected-query selector differs from the finite profile policy")
    if case.count("case.waits(profile)") != 3:
        fail("native Exact fixture requires exactly three real profile-aware wait call sites")
    wait_sites = re.findall(
        r"(?m)^[ \t]*if case\.waits\(profile\) \{\n[ \t]*let proof = wait_for_lock\(", case,
    )
    no_wait_sites = re.findall(
        r"(?m)^[ \t]*if !case\.waits\(profile\) \{\n"
        r"[ \t]*holder_still_open\(&mut control, &held_key, &holder\);\n[ \t]*\}", case,
    )
    marker_sites = re.findall(
        r'(?m)^[ \t]*println!\("nakama_native_jsonb_exact_case_executed profile=\{\} case=\{label\} fields=15 native_wait=\{\} committed=\{\} same_lease_readable=true payload_serde_roundtrip=false",profile\.metadata_value\(\),case\.waits\(profile\),matches!\(expected,Expected::Committed\)\);',
        case,
    )
    if tuple(map(len, (wait_sites, no_wait_sites, marker_sites))) != (1, 1, 1):
        fail("native Exact fixture profile wait calls must drive proof, holder readback and case marker")

    # Bind publication to the real matrix execution function. Other modules
    # repeat counters/marker text, so global token presence cannot protect it.
    modules = list(re.finditer(
        r"(?m)^    mod native_exact_input \{\n([\s\S]*?)^    \}\n    // Native Any observations",
        fixture,
    ))
    literal_pattern = re.compile(
        r'\b(?:br|cr|r)(?P<hashes>#{0,255})"[\s\S]*?"(?P=hashes)|"(?:\\[\s\S]|[^"\\])*"'
    )
    literal_spans = list(literal_pattern.finditer(fixture))
    comment_spans = list(re.finditer(r"/\*[\s\S]*?\*/", fixture))
    if len(modules) != 1 or any(
        span.start() <= modules[0].start() < span.end()
        for span in literal_spans + comment_spans
    ):
        fail("native Exact matrix module is missing, duplicated or supplied by a literal/comment")
    module = modules[0]
    executions = list(re.finditer(
        r"(?m)^        pub\(super\) fn exercise\(url: &str, profile: DatabaseProfile, collection: &str\) \{\n"
        r"([\s\S]*?)^        \}\n", module.group(1),
    ))
    if len(executions) != 1 or any(
        span.start() <= module.start(1) + executions[0].start() < span.end()
        for span in literal_spans + comment_spans
    ):
        fail("native Exact matrix execution function is missing, duplicated or not code")
    execution = executions[0].group(1)
    if "/*" in execution or "*/" in execution or re.search(r'\b(?:br|cr|r)#{0,255}"', execution):
        fail("native Exact matrix execution has structures outside its finite source policy")
    pieces = []
    cursor = 0
    for literal in re.finditer(r'"(?:\\[\s\S]|[^"\\])*"', execution):
        if "\n" in literal.group():
            fail("native Exact matrix execution multiline literals are outside the finite policy")
        code = re.sub(r"(?m)^[ \t]*//[^\n]*", "", execution[cursor:literal.start()])
        pieces.extend((re.sub(r"\s+", "", code), literal.group()))
        cursor = literal.end()
    code = re.sub(r"(?m)^[ \t]*//[^\n]*", "", execution[cursor:])
    pieces.append(re.sub(r"\s+", "", code))
    # Reviewed11case execution+surrogate+duplicate and PG0/2,CR2/0 publication;
    # literal bytes are exact. This is a finite seam, not a Rust parser/proof.
    if hashlib.sha256("".join(pieces).encode("utf-8")).hexdigest() != (
        "054efa7efcf8f20d1da01801e673eb8c46fbff1892541dbc16468d8878e67fe8"
    ):
        fail("native Exact matrix execution/publication differs from its finite reviewed policy")



def validate_nakama_insert_only_source(repository: str, write: str, projection: str, fixture: str) -> None:
    """Reviewed finite source regions only; this grants no native or oracle credit.

    Formatter whitespace and complete line comments are the only ignored source
    spelling. Regions with block comments or raw literals are outside this finite
    policy. Whole region equality prevents an unused literal/comment from supplying
    the executable route, native error scope or query selector.
    """
    inputs = {"repository": repository, "write": write, "projection": projection, "fixture": fixture}
    literal_pattern = re.compile(r'\b(?:br|cr|r)(?P<hashes>#{0,255})"[\s\S]*?"(?P=hashes)|"(?:\\[\s\S]|[^"\\])*"')
    def actual_regions(pattern: str, source: str) -> list[str]:
        matches = list(re.finditer(pattern, source))
        literals = list(literal_pattern.finditer(source))
        if any(literal.start() <= match.start() < literal.end()
               for match in matches for literal in literals):
            fail("insert-only source headers cannot come from a string literal")
        return [match.group(1) for match in matches]
    # These two reviewed production files have no block comments, raw strings or
    # multiline string literals. Keep function headers outside those structures.
    for source in (repository, write):
        if "/*" in source or "*/" in source or re.search(r'\b(?:br|cr|r)#{0,255}"', source):
            fail("insert-only production headers cannot derive from comments or raw literals")
    if "/*" in fixture or "*/" in fixture:
        fail("insert-only native headers cannot come from block comments")
    modules = actual_regions(r"(?m)^    mod native_insert_only \{\n([\s\S]*?)^    // Future-production-candidate-only", fixture)
    if len(modules) != 1 or "/*" in modules[0] or "*/" in modules[0] or re.search(r'\b(?:br|cr|r)#{0,255}"', modules[0]):
        fail("insert-only native module headers must remain in the reviewed finite source")
    def compact(value: str) -> str:
        # Preserve every literal byte. Only formatter whitespace and complete
        # code line comments outside those literals may be ignored.
        pieces = []
        cursor = 0
        for literal in literal_pattern.finditer(value):
            code = re.sub(r"(?m)^[ \t]*//[^\n]*", "", value[cursor:literal.start()])
            pieces.append(re.sub(r"\s+", "", code))
            pieces.append(literal.group())
            cursor = literal.end()
        code = re.sub(r"(?m)^[ \t]*//[^\n]*", "", value[cursor:])
        pieces.append(re.sub(r"\s+", "", code))
        return "".join(pieces)
    policies = (('insert-only occurrence route', 'repository', '(?m)^                if let Some\\(write\\) = nakama_insert_only_write\\(kind, operation\\) \\{\\n([\\s\\S]*?)^                \\}\\n                // The canonical occurrence plan', '                    // Owner authority was checked for the whole request before\n                    // this transaction. Star uses a real ordinary INSERT, without\n                    // reading or locking the previous row or consulting its ACL.\n                    // None is explicit insert-mode input, not proof of absence;\n                    // native uniqueness determines whether this occurrence exists.\n                    staged.insert(write.key.clone(), None);\n                    verify_storage_staged_budget(&staged)?;\n                    let receipt = apply_nakama_write(\n                        &mut transaction,\n                        &mut staged,\n                        actor,\n                        write,\n                        updated_at_i64,\n                        self.profile,\n                    )?;\n                    verify_storage_staged_budget(&staged)?;\n                    return Ok(Some(receipt));\n'), ('insert-only kind and version scope', 'repository', '(?m)^fn nakama_insert_only_write\\(\\n([\\s\\S]*?)^\\}\\n\\nenum WriteStepValidation', '    kind: Option<NakamaBatchKind>,\n    operation: &BatchOperation,\n) -> Option<&WriteOperation> {\n    match (kind, operation) {\n        (Some(NakamaBatchKind::Write), BatchOperation::Write(write))\n            if write.expected == VersionCheck::MustNotExist =>\n        {\n            Some(write)\n        }\n        _ => None,\n    }\n'), ('plain INSERT unique error scope', 'write', '(?m)^fn nakama_insert_only_unique_rejection\\(\\n([\\s\\S]*?)^\\}\\n\\n// Private actual native row custody\\.', '    binding: StorageWriteBinding,\n    expected: &VersionCheck,\n    previous_exists: bool,\n    sqlstate: Option<&str>,\n) -> Option<DomainError> {\n    if binding == StorageWriteBinding::Nakama\n        && *expected == VersionCheck::MustNotExist\n        && !previous_exists\n        && sqlstate == Some("23505")\n    {\n        Some(error(\n            StableCode::AlreadyExists,\n            "storage_object_already_exists",\n            RetryClass::Never,\n        ))\n    } else {\n        None\n    }\n'), ('native DML error call site', 'write', '(?m)^    let returned = transaction\\n([\\s\\S]*?)^    let next = decode_stored_storage_object', '        .query_opt(&query, &parameters)\n        .map_err(|source| {\n            // Only this actual ordinary INSERT\'s native unique violation is an\n            // insert-only version rejection. Projection, typed, UPDATE and every\n            // other SQLSTATE retain their existing classification.\n            let rejection = nakama_insert_only_unique_rejection(\n                binding,\n                &operation.expected,\n                previous.is_some(),\n                source.code().map(|code| code.code()),\n            );\n            rejection.unwrap_or_else(|| map_postgres_error(source))\n        })?\n        .ok_or_else(|| data_loss("storage_write_row_count_mismatch"))?;\n'), ('Nakama substantive projection selector', 'write', '(?m)^    let projected = if binding == StorageWriteBinding::Nakama \\{\\n([\\s\\S]*?)^    let projection_digest = ', '        // Native JSONB text input also applies to insert-only, which deliberately\n        // skipped the previous-row acquisition query. Typed projection retains\n        // its separate TEXT input policy.\n        project_nakama_storage_request(transaction, &operation.value)?\n    } else {\n        project_storage_request(transaction, &operation.value)?\n    };\n'), ('insert-only profile wait policy', 'fixture', '(?m)^            const fn expects_wait\\(self, profile: DatabaseProfile\\) -> bool \\{\\n([\\s\\S]*?)^            \\}\\n            const fn expected_error', '                match self {\n                    Self::AnyPositive | Self::UncommittedDelete => true,\n                    Self::CommittedExisting => matches!(profile, DatabaseProfile::CockroachDb),\n                }\n'), ('native ordinary INSERT query selector', 'fixture', '(?m)^        fn plain_insert_query\\(query: &str\\) -> bool \\{\\n([\\s\\S]*?)^        \\}\\n        fn observe_insert_wait', '            // Inspect the native statement without modifying its bytes. PG\n            // exposes the wire literal; CR exposes its own substituted display.\n            let Some(after_table) = query\n                .trim_start()\n                .strip_prefix("INSERT INTO public.trnm_storage_objects")\n            else {\n                return false;\n            };\n            query.len() <= 8192\n                && after_table.trim_start().starts_with(\'(\')\n                && query.contains(INSERT_COLUMNS)\n                && query.contains("VALUES (")\n                && query.contains("\'write-request-bytes\'")\n                && query.contains("RETURNING ")\n                && query.contains("value_projection_digest")\n                && query.contains("create_time")\n                && query.contains("update_time")\n                && !query.contains("ON CONFLICT")\n                && !query.contains("FOR UPDATE")\n                && !query.contains("WITH upd")\n'), ('insert-only three-case invocation and marker', 'fixture', '(?m)^        pub\\(super\\) fn exercise\\(url: &str, profile: DatabaseProfile, collection: &str\\) \\{\\n([\\s\\S]*?)^        \\}\\n    \\}\\n\\n    // Future-production-candidate-only', '            for case in [\n                InsertCase::AnyPositive,\n                InsertCase::CommittedExisting,\n                InsertCase::UncommittedDelete,\n            ] {\n                exercise_insert_case(url, profile, collection, case);\n            }\n            let (committed_existing_wait, committed_existing_no_wait) = match profile {\n                DatabaseProfile::PostgreSql => (0_u8, 1_u8),\n                DatabaseProfile::CockroachDb => (1_u8, 0_u8),\n            };\n            println!("nakama_native_insert_only_matrix_executed profile={} cases=3 committed_existing_wait={committed_existing_wait} committed_existing_no_wait={committed_existing_no_wait} uncommitted_delete_wait=1 fields=15",profile.metadata_value());\n'), ('native JSONB projection query parameters and decode bound', 'projection', '(?m)^fn project_nakama_storage_request\\(\\n([\\s\\S]*?)^\\}\\n\\nfn validate_storage_request_native', '    transaction: &mut Transaction<\'_>,\n    request: &[u8],\n) -> Result<Vec<u8>, DomainError> {\n    let payload = RawStorageJsonb::new(request)?;\n    let row = transaction\n        .query_one(\n            "SELECT CASE WHEN pg_catalog.octet_length(value) <= $2::INT8 THEN value END, \\\n                    pg_catalog.octet_length(value)::INT8 \\\n             FROM (SELECT $1::JSONB::TEXT AS value) AS native_projection",\n            &[\n                &payload,\n                &i64::try_from(MAX_NATIVE_VALUE_BYTES).expect("constant fits INT8"),\n            ],\n        )\n        .map_err(map_postgres_error)?;\n    decode_native_value(&row, 0)\n'), ('native full-tuple actual outcome claims', 'fixture', '(?m)^            let check_state = if rollback_ok && joined.as_ref\\(\\).is_some_and\\(Result::is_ok\\) \\{\\n([\\s\\S]*?)^            if let Err\\(payload\\) = body \\{', '                Some(catch_unwind(AssertUnwindSafe(|| {\n                    let after = snapshot(&mut control, collection);\n                    assert_eq!(\n                        after, before,\n                        "insert-only failed batch changed complete full15field native rows"\n                    );\n                    let own =\n                        |r: &&RawRow| r.key.starts_with(&format!("native-star-{}-", case.name()));\n                    let b: Vec<_> = before.iter().filter(own).map(tuple_json).collect();\n                    let a: Vec<_> = after.iter().filter(own).map(tuple_json).collect();\n                    assert_eq!(b.len(), 4);\n                    assert_eq!(a, b);\n                    let no_receipts = outcome.as_ref().map(|actual| actual.batch.is_err());\n                    let same_lease_readable =\n                        outcome.as_ref().map(|actual| actual.reused == Ok(true));\n                    let causal_body_passed = body.is_ok();\n                    let packet = serde_json::json!({"schema":"local.storage-native-insert-only-full-tuple.v1","profile":profile.metadata_value(),"case":case.name(),"fields":15,"before":b,"after":a,"holder_primary_catalog":[holder.table_id,holder.index_id,holder.primary_name],"holder_granted_before":holder.granted_rows,"hidden_batch_sqlstate":null,"no_receipts":no_receipts,"same_lease_readable":same_lease_readable,"causal_body_passed":causal_body_passed,"accepted":false,"full_protocol_parity":false});\n                    let bytes = serde_json::to_vec(&packet).unwrap();\n                    assert!(\n                        bytes.len() <= 65536,\n                        "insert-only native proof output exceeds64KiB"\n                    );\n                    println!("{}", String::from_utf8(bytes).unwrap());\n                })))\n            } else {\n                None\n            };\n'))
    for label, source, pattern, expected in policies:
        if len(inputs[source].encode("utf-8")) > 1024 * 1024:
            fail("insert-only source exceeds its finite parsing budget")
        regions = actual_regions(pattern, inputs[source])
        if len(regions) != 1:
            fail(label + " source region is missing or duplicated")
        region = regions[0]
        if "/*" in region or "*/" in region or re.search(r'\b(?:br|cr|r)#{0,255}"', region):
            fail(label + " cannot derive executable policy from comments or raw literals")
        if compact(region) != compact(expected):
            fail(label + " differs from the reviewed finite source policy")
    if repository.index("if let Some(write) = nakama_insert_only_write(kind, operation)") > repository.index("let refreshed_access = "):
        fail("insert-only must return before previous-row acquisition")
    projected = actual_regions(r"(?m)^fn project_nakama_storage_request\(\n([\s\S]*?)^\}\n\nfn validate_storage_request_native", projection)
    if len(projected) != 1:
        fail("Nakama native projection region must be unique")
    region = re.sub(r"(?m)^[ \t]*//[^\n]*", "", projected[0])
    if "/*" in region or "*/" in region or re.search(r'\b(?:br|cr|r)#{0,255}"', region):
        fail("Nakama native projection must use its actual reviewed query")
    require_markers("Nakama native projection", region, (
        "RawStorageJsonb::new(request)?", "FROM (SELECT $1::JSONB::TEXT AS value) AS native_projection",
        "&payload,", "MAX_NATIVE_VALUE_BYTES", "decode_native_value(&row, 0)",
    ))
    if "::TEXT::JSONB" in region or "serde_json" in region or "Format::Binary" in region:
        fail("Nakama native projection cannot reconstruct original JSONB input")
    cases = actual_regions(r"(?m)^        fn exercise_insert_case\(\n([\s\S]*?)^        pub\(super\) fn exercise\(", fixture)
    if len(cases) != 1:
        fail("insert-only actual case body must be unique")
    case = cases[0]
    if "/*" in case or "*/" in case or re.search(r'\b(?:br|cr|r)#{0,255}"', case):
        fail("insert-only case proof cannot come from comments or raw literals")
    literals = re.finditer(r'"(?:\\[\s\S]|[^"\\])*"', case)
    if any("\n" in literal.group() for literal in literals):
        fail("insert-only case proof cannot come from multiline literals")
    if case.count("case.expects_wait(profile)") != 3:
        fail("insert-only needs exactly three actual profile-aware wait sites")
    wait_sites = re.findall(r"(?m)^                if case\.expects_wait\(profile\) \{\n[ \t]*let proof = loop \{", case)
    completion_sites = re.findall(r"(?m)^                if !case\.expects_wait\(profile\) \{\n[ \t]*let fresh = fresh_granted\(&mut control, &later, &holder\);", case)
    marker_sites = re.findall(r'(?m)^            println!\("nakama_native_insert_only_case_executed profile=\{\} case=\{\} native_wait=\{\} fields=15 no_receipts=true same_lease_readable=true hidden_batch_sqlstate=null",profile\.metadata_value\(\),case\.name\(\),case\.expects_wait\(profile\)\);', case)
    if tuple(map(len, (wait_sites, completion_sites, marker_sites))) != (1, 1, 1):
        fail("insert-only wait policy must drive actual proof, fresh holder completion and case marker")
    require_markers("insert-only actual fixture execution", fixture, (
        "native_insert_only::exercise(url, profile, collection);", "mod native_insert_only {",
        "local.storage-native-insert-only-full-tuple.v1", "local.storage-native-insert-only-early-completion.v1",
        "before.iter().filter(own).map(tuple_json)", "after.iter().filter(own).map(tuple_json)",
        "assert_eq!(a, b)", "assert_eq!(b.len(), 4)", "bytes.len() <= 65536",
        "plain_insert_query(&query)", "holder.index_id", "holder.table_id", "primary_catalog",
    ))


def validate_nakama_any_source(repository: str, write: str, projection: str) -> None:
    """Finite reviewed Any source regions; this grants no execution credit.

    Each digest binds a complete executable function and native SQL literal
    bytes. Only formatter whitespace and whole code line comments are ignored.
    This bounded spelling policy is not a general Rust lexer.
    """
    inputs = {"repository": repository, "write": write, "projection": projection}
    literal = re.compile('\\b(?:br|cr|r)(?P<hashes>#{0,255})"[\\s\\S]*?"(?P=hashes)|"(?:\\\\[\\s\\S]|[^"\\\\])*"|\\\'(?:\\\\(?:u\\{[0-9A-Fa-f]+\\}|x[0-9A-Fa-f]{2}|[^\\n])|[^\\\'\\\\\\n])\\\'')
    def compact(value: str) -> str:
        pieces = []
        cursor = 0
        for item in literal.finditer(value):
            code = re.sub(r"(?m)^[ \t]*//[^\n]*", "", value[cursor:item.start()])
            pieces.append(re.sub(r"\s+", "", code))
            pieces.append(item.group())
            cursor = item.end()
        code = re.sub(r"(?m)^[ \t]*//[^\n]*", "", value[cursor:])
        pieces.append(re.sub(r"\s+", "", code))
        return "".join(pieces)
    policies = (('storage_batch_isolation', 'repository', '(?m)^fn storage_batch_isolation\\([\\s\\S]*?^\\}\\n', 'ccf503bd75dbe2a36673ef8a533341cf32684f80f34fc53e4d49a61b38c0dd38'), ('nakama_any_write', 'repository', '(?m)^fn nakama_any_write\\([\\s\\S]*?^\\}\\n', '3712f71a6ee676e5e57c7d2df02bad95e486240ff5d04bbf427798e46af4bd9c'), ('decode_any_native_row', 'write', '(?m)^fn decode_any_native_row\\([\\s\\S]*?^\\}\\n', 'cb6ca1176e47e92001679adaf20b0323b791938b27c45cf9ffee8f17248f99be'), ('read_nakama_any_prior', 'write', '(?m)^fn read_nakama_any_prior\\([\\s\\S]*?^\\}\\n', '8de884356b5d3f2cfe8470bca22f7b243d28741f4fb32054dff851e9e9ac1608'), ('any_conflict_noop', 'write', '(?m)^fn any_conflict_noop\\([\\s\\S]*?^\\}\\n', '00902b47451b28969744da793f8a448edf5849fa19e88027ed0fa3657d80adbd'), ('verify_any_written_row', 'write', '(?m)^fn verify_any_written_row\\([\\s\\S]*?^\\}\\n', 'd78821ea1f4155a1742d0bc84e744564cbc52511f84d1d7f158e4824f79ef3ad'), ('apply_nakama_any_write', 'write', '(?m)^fn apply_nakama_any_write\\([\\s\\S]*?^\\}\\n', 'e0a52d34eec89e2ca21b59140373e23cf85db70ca44938332dde8bc7e0f0e0fb'), ('storage_any_row_columns', 'projection', '(?m)^fn storage_any_row_columns\\([\\s\\S]*?^\\}\\n', '320573a85f7fd5658df6e3933830044fe853ca13e7f2a8e5e55fc3294d315446'), ('storage_any_conflict_lock_clause', 'projection', '(?m)^fn storage_any_conflict_lock_clause\\([\\s\\S]*?^\\}\\n', '186b0468429ee6792f3bb262629277e7f378f83f82909b6e2397b1e255a60b0f'), ('storage_any_access_query', 'projection', '(?m)^fn storage_any_access_query\\([\\s\\S]*?^\\}\\n', '176395cc7a2b8ad5e7b279da71f47cc0cac110ec852942ffcf1768d8e00392dc'), ('storage_any_prior_query', 'projection', '(?m)^fn storage_any_prior_query\\([\\s\\S]*?^\\}\\n', '481aa32b04812b42012e7033d1ffe93752d269be6b2035fd74d145b402021a42'), ('storage_any_native_digest', 'projection', '(?m)^fn storage_any_native_digest\\([\\s\\S]*?^\\}\\n', '4cbe5394170d056e85c12d03799b28d25d9852e5e418811e4bc2c9d69a54998d'), ('storage_any_upsert_query', 'projection', '(?m)^fn storage_any_upsert_query\\([\\s\\S]*?^\\}\\n', 'f43eb3c6dfd8e35e9d57f13ceef5914af07249d968331f72d1509b0d717841fc'), ('actual Any batch route and transaction admission', 'repository', '(?m)^    fn apply_storage_batch_with_policy\\([\\s\\S]*?^    \\}\\n', '807d90536372264d9540db21b5152c245f9b71713958147b085553659f639a93'), ('complete private native prior row equality', 'write', '(?m)^#\\[derive\\(Eq, PartialEq\\)\\]\\nstruct AnyNativeRow \\{[\\s\\S]*?^\\}\\n', 'd7a59067c9368e9956c07b9c9252ed1b4e0fb2fa212ee402641762ae2ff3bc62'), ('Any occurrence semantic result scope', 'write', '(?m)^enum AnyWriteStep \\{[\\s\\S]*?^\\}\\n', 'a11098adccce74168965f99e1beff79f7612f2f4f09fa2e80c720cfb0c088f81'))
    for label, name, pattern, expected in policies:
        source = inputs[name]
        if "/*" in source or "*/" in source:
            fail("Any source headers cannot derive from block comments")
        matches = list(re.finditer(pattern, source))
        spans = list(literal.finditer(source))
        if len(matches) != 1 or any(
            item.start() <= matches[0].start() < item.end()
            or item.start() < matches[0].end() <= item.end()
            for item in spans
        ):
            fail("Any source region is missing, duplicated or inside a literal: " + label)
        actual = hashlib.sha256(compact(matches[0].group()).encode("utf-8")).hexdigest()
        if actual != expected:
            fail("Any source region differs from its reviewed finite policy: " + label)


def expand_storage_any_reservation_template(projection: str, profile: str) -> str:
    """Expand the reviewed finite formatter subset, without Rust or SQL execution.

    The production function guards and observation guard bind this subset's
    source shape. This is not an interpreter for arbitrary Rust formatters.
    The existing source boundary fixes request/native budgets to1MiB/16MiB.
    """
    quoted = r'"(?:\\[\s\S]|[^"\\])*"'

    def body(name: str) -> str:
        matches = re.findall(r'(?m)^fn ' + name + r'\([\s\S]*?^\}\n', projection)
        if len(matches) != 1:
            fail("Any template expansion needs one reviewed production function: " + name)
        return matches[0]

    def capture(pattern: str, source: str) -> str:
        matches = re.findall(pattern, source, re.MULTILINE)
        if len(matches) != 1:
            fail("Any template formatter source differs from its reviewed subset")
        return matches[0]

    def decode(token: str) -> str:
        # Rust string continuations remove the newline and following indent.
        return json.loads(re.sub(r'\\\r?\n\s*', '', token))

    row = body('storage_row_columns')
    environment = {'MAX_NATIVE_VALUE_BYTES': 16777216, 'MAX_VALUE_BYTES': 1048576}
    for name, value in re.findall(r'let (\w+) = (' + quoted + r');', row):
        environment[name] = decode(value)
    fields = capture(r'let fields = \[\n([\s\S]*?)^    \];', row)
    field_values = []
    for item in re.finditer(r'format!\(\s*(' + quoted + r')\s*\)|(' + quoted + r')\.to_owned\(\)|(\w+)\.to_owned\(\)', fields):
        formatted, literal, variable = item.groups()
        field_values.append(decode(formatted).format_map(environment) if formatted else
                            decode(literal) if literal else environment[variable])
    if len(field_values) != 14:
        fail("Any template expansion must retain all14 computed native fields")
    wrapper = decode(capture(r'\.map\(\|field\| format!\((' + quoted + r')\)\)', row))
    native_columns = ', '.join(wrapper.format(visible='TRUE', field=field) for field in field_values)
    additional = body('storage_any_row_columns')
    suffix = decode(capture(r'format!\(\s*(' + quoted + r')\s*,', additional))
    columns = suffix.format(native_columns)
    digest_function = body('storage_any_native_digest')
    variant = {'postgresql': 'PostgreSql', 'cockroachdb': 'CockroachDb'}.get(profile)
    if variant is None:
        fail("Any template expansion requires a closed database profile")
    digest = decode(capture(r'DatabaseProfile::' + variant + r' => (' + quoted + r')', digest_function))
    upsert = body('storage_any_upsert_query')
    incoming = decode(capture(r'let incoming = format!\(\s*(' + quoted + r')\s*\);', upsert)).format(digest=digest)
    source = decode(capture(r'let source = if reserve \{\s*format!\((' + quoted + r')\)', upsert)).format(incoming=incoming)
    predicate = decode(capture(r'let predicate = if reserve \{\s*(' + quoted + r')', upsert))
    assignments = decode(capture(r'let assignments = if reserve \{(?:\s*//[^\n]*\n)*\s*(' + quoted + r')\.to_owned', upsert))
    template = decode(capture(r'let columns = storage_any_row_columns\(profile\);\s*format!\(\s*(' + quoted + r')\s*\)', upsert))
    return template.format(source=source, assignments=assignments, predicate=predicate, columns=columns)


def validate_storage_any_observation_source(projection: str, fixture: str) -> None:
    """Bind finite observation spelling and source templates; no native credit."""
    if max(len(projection.encode()), len(fixture.encode())) > 1024 * 1024:
        fail("Any observation source exceeds its finite parsing budget")
    literal = re.compile(r'\b(?:br|cr|r)(?P<hashes>#{0,255})"[\s\S]*?"(?P=hashes)|"(?:\\[\s\S]|[^"\\])*"|\'(?:\\[^\n]|[^\'\\\n])\'')

    def compact(value: str) -> str:
        pieces, cursor = [], 0
        for item in literal.finditer(value):
            pieces.append(re.sub(r'\s+', '', re.sub(r'(?m)^[ \t]*//[^\n]*', '', value[cursor:item.start()])))
            pieces.append(item.group())
            cursor = item.end()
        pieces.append(re.sub(r'\s+', '', re.sub(r'(?m)^[ \t]*//[^\n]*', '', value[cursor:])))
        return ''.join(pieces)

    inputs = {'projection': projection, 'fixture': fixture}
    # Filled from the frozen release donor; each region includes its actual
    # executable sites. These hashes do not replace the16 production guards.
    regions = (('actual computed native row-column formatter', 'projection', '(?m)^fn storage_row_columns\\([\\s\\S]*?^\\}\\n', '5e14b5171e46fa584ca3af86973d0341189f4d27c540408b5f41ed721eb860e9'), ('unchanged complete legacy Any classifiers', 'fixture', '(?m)^        pub\\(super\\) fn reservation_query\\([\\s\\S]*?(?=^        // PG17\\.6 with)', 'd6261f25775dad7e6fd1e7cefaeec43a1288d634b2edaa98def0a8bebf652b1b'), ('profile exact templates scopes and packet publication', 'fixture', '(?m)^        const PG_ANY_RESERVATION_WIRE:[\\s\\S]*?(?=^        fn application\\()', '97f62335065e3788e0857ea4181ad9e1c5621fca5e444d0d189c60a24e6d3174'), ('actual shared tail PG CR acquisition routing', 'fixture', '(?m)^    fn observe_wait\\([\\s\\S]*?(?=^    fn log_outcome\\()', 'daefd41652b5dd35ccbf89cb3110016b60ca9945b19f4659a683b05e14ba223e'), ('actual four-case PG CR acquisition and kind routing', 'fixture', '(?m)^        fn observe_wait\\([\\s\\S]*?(?=^        fn wait_for_reservation\\()', 'b2edcac6762b661930b1c5b5a9a0faa5f119419a022904efc3d3e79c809a752d'))
    for label, name, pattern, expected in regions:
        source = inputs[name]
        if '/*' in source or '*/' in source:
            fail("Any observation headers cannot come from block comments")
        matches = list(re.finditer(pattern, source))
        spans = list(literal.finditer(source))
        if len(matches) != 1 or any(item.start() <= matches[0].start() < item.end() or
                                   item.start() < matches[0].end() <= item.end() for item in spans):
            fail("Any observation region is missing, duplicated or inside a literal: " + label)
        if hashlib.sha256(compact(matches[0].group()).encode()).hexdigest() != expected:
            fail("Any observation region differs from its finite reviewed spelling: " + label)
    for profile, constant, expected in (
        ('postgresql', 'PG_ANY_RESERVATION_WIRE', '8c9261d375b21ae321a41c65d8f2f2b580ef0f8cd21d478fdc6cd559c19a6eaa'),
        ('cockroachdb', 'CR_ANY_RESERVATION_WIRE', '58f0907078ad1eefa84c2e0623e1251a76ad4659a287f801e5b180d7e14eaa54'),
    ):
        expanded = expand_storage_any_reservation_template(projection, profile)
        matches = re.findall(r'(?m)^        const ' + constant + r': &str = r#"([\s\S]*?)"#;', fixture)
        if len(matches) != 1 or expanded != matches[0] or hashlib.sha256(expanded.encode()).hexdigest() != expected:
            fail("Any complete observation template must match actual production expansion: " + profile)

def validate_nakama_native_write_source(repository: str, write: str, projection: str,
                                       wire: str, fixture: str, app_fixture: str) -> None:
    """Closed source seam only: source checks do not grant native/oracle acceptance."""
    validate_native_exact_fixture_local_policy(fixture)
    validate_nakama_insert_only_source(repository, write, projection, fixture)
    validate_nakama_any_source(repository, write, projection)
    validate_storage_any_observation_source(projection, fixture)
    jsonb = projection.split("struct RawStorageJsonb", 1)[-1].split("struct RawStorageCondition", 1)[0]
    require_markers("raw JSONB text binding", jsonb, (
        "if value.len() > MAX_VALUE_BYTES", "if *kind != postgres::types::Type::JSONB",
        "output.extend_from_slice(self.0);", "postgres::types::IsNull::No",
        "*kind == postgres::types::Type::JSONB", "postgres::types::Format::Text",
        "postgres::types::to_sql_checked!();", '.field("bytes", &self.0.len())',
    ))
    condition = projection.split("struct RawStorageCondition", 1)[-1].split("fn decode_locked_storage_access", 1)[0]
    require_markers("raw TEXT condition binding", condition, (
        "(&'a str)", "if *kind != postgres::types::Type::TEXT",
        "output.extend_from_slice(self.0.as_bytes());", "postgres::types::IsNull::No",
        "*kind == postgres::types::Type::TEXT", "postgres::types::Format::Text",
        "postgres::types::to_sql_checked!();", '.field("bytes", &self.0.len())',
    ))
    if "Format::Binary" in jsonb + condition or "serde_json" in jsonb + condition:
        fail("native input must not use binary/serde reconstruction")
    acquisition = projection.split("fn acquire_nakama_write_access(", 1)[-1].split("fn validate_locked_operation(", 1)[0]
    require_markers("Nakama native input and Exact acquisition", acquisition, (
        "RawStorageJsonb::new(&operation.value)?", "let VersionCheck::Exact(token)",
        "AND $4::JSONB IS NOT NULL FOR UPDATE", "RawStorageCondition(token.as_str())",
        "AND $4::JSONB IS NOT NULL AND public_version::TEXT=$5::TEXT",
        "AND ($6::BOOL OR write_permission=1) FOR UPDATE", "let authoritative = actor == Actor::Server;",
        "&payload,", "&condition,", "&authoritative,", "let fallback = transaction",
        "row.version.as_str() == token.as_str()", "row.write.allows_client_write()",
        'data_loss("storage_exact_access_predicate_mismatch")', "Ok(fallback)",
    ))
    fallback = acquisition.split("let fallback = transaction", 1)[-1].split("if fallback.as_ref()", 1)[0]
    if "FOR UPDATE" in fallback:
        fail("excluded Exact fallback must remain nonlocking")
    eligible = acquisition.split("let eligible = transaction", 1)[-1].split("if eligible.is_some()", 1)[0]
    if not eligible.index("&payload,") < eligible.index("&condition,") < eligible.index("&authoritative,"):
        fail("eligible acquisition must bind JSONB before raw TEXT and authority")
    require_markers("Nakama native UPDATE binding", write, (
        "fn apply_nakama_write(", "StorageWriteBinding::Typed", "StorageWriteBinding::Nakama",
        "apply_write_with_binding(", "RawStorageJsonb::new(&operation.value)?",
        '"$4::JSONB"', '"$4::TEXT::JSONB"', "RawStorageCondition(token.as_str())",
        '" AND public_version::TEXT=$12::TEXT AND ($13::BOOL OR write_permission=1)"',
        "{exact_predicate}", "&native_payload", "parameters.push(condition);", "parameters.push(&authoritative);",
        'data_loss("storage_write_row_count_mismatch")', 'data_loss("storage_write_native_returning_mismatch")',
    ))
    error = wire.split("fn storage_error(", 1)[-1].split("pub(crate) fn authentication_error", 1)[0]
    require_markers("native Write facade", error, (
        "(OperationKind::Write, StableCode::InvalidArgument)",
        'if error.reason() == "database_constraint_violation"',
        'gateway_error(500, 13, "Error writing storage objects.")',
        '(_, StableCode::InvalidArgument) => gateway_error(400, 3, "Invalid storage request.")',
    ))
    if error.index('if error.reason() == "database_constraint_violation"') > error.index("(_, StableCode::InvalidArgument)"):
        fail("native Write facade must precede generic host validation mapping")
    require_markers("native JSONB/Exact matrix fixture", fixture, (
        "native_exact_input::exercise(url, profile, collection);", "mod native_exact_input {",
        "MatrixCase::AclLateExactStale,", "MatrixCase::AclLateExactWriteZero,",
        "MatrixCase::ClientExactMatched,", "MatrixCase::ServerExactMatchedWriteZero,",
        "MatrixCase::ExactMissing,", "MatrixCase::ExactNulAclZero,", "MatrixCase::EscapedNul,",
        "MatrixCase::BothBadPayloadAndToken,", "MatrixCase::TypedNulAclZero,", "MatrixCase::TypedMalformedAclZero,",
        "duplicate_exact(url, profile, collection);", "statement.params()", "Type::oid",
        "local.storage-native-jsonb-lock-query.v1", "local.storage-native-jsonb-input-observation.v1",
        "local.storage-native-jsonb-exact-fixture.v1", "LEGAL_SURROGATE", "hidden_batch_sqlstate=null",
        "const fn waits(self, profile: DatabaseProfile) -> bool", "case.waits(profile)",
        "Self::AclLateExactStale", "Self::AclLateExactWriteZero", "Self::ExactNulAclZero",
        "matches!(profile, DatabaseProfile::CockroachDb)",
        "DatabaseProfile::PostgreSql => (0_u8, 2_u8)", "DatabaseProfile::CockroachDb => (2_u8, 0_u8)",
        '"late_exact_wait_cases":late_exact_wait_cases', '"late_exact_no_wait_cases":late_exact_no_wait_cases',
    ))
    for marker, suffix in STORAGE_DUPLICATE_MARKERS:
        if marker not in {"nakama_native_jsonb_exact_matrix_executed", "nakama_native_jsonb_exact_subvector_executed"}:
            continue
        source_suffix = suffix.replace("case=both_bad_input_legal_surrogate", "case={label}")
        if marker == "nakama_native_jsonb_exact_matrix_executed":
            source_suffix += " late_exact_wait={late_exact_wait_cases} late_exact_no_wait={late_exact_no_wait_cases}"
        require_markers("native JSONB/Exact matrix exact marker", fixture, (marker + " profile={}" + source_suffix,))
    require_markers("bound surrogate source invocation", fixture, ('Some("both_bad_input_legal_surrogate")',))
    require_markers("authenticated native Write facade fixture", app_fixture, (
        "storage_jsonb_native_write_failure_executed profile={} cases=1 fields=15 sqlstate=22P02",
        'native_surrogate.code().map(|code| code.code())', '"Error writing storage objects."', '"22P02"',
        'assert_eq!(rejected.status, 500)', 'before_surrogate', 'assert_eq!(healthy.status, 200)',
    ))


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



ACCOUNTS_FULL_SOURCE_SHA256 = {'crates/trnm-persistence-pg/src/schema_parts/account_catalog.rs': 'b020b404a676cfc8b118c7e342c90e40a2cccc5e9bf2c9b5f938f08d5e0522dc',
 'crates/trnm-persistence-pg/src/schema_parts/migrate.rs': '8324f47213f1ea9172e01d6524cd1280649b6c8ffdd07e3fd1b348ae2e2dec16',
 'crates/trnm-persistence-pg/src/schema_parts/metadata.rs': 'ececa19afd84d92ba152c9c4abde420dbfef9d691178cb7cfd349bb623974d26'}

LEGACY_AUTH_FULL_SOURCE_SHA256 = {'crates/trnm-token-crypto-provider/src/nakama_legacy.rs': 'b0ead9137eb2a1c7a353a0ba489f287e1dc3de67cbfb4617f06bdf2c8031cb21',
 'crates/trnm-token-jwt-adapter/src/nakama_legacy_verify.rs': 'b36fef2b6bf05856a85fed712b2a673069608ebf3387af94729dd906fd43a0fe',
 'crates/trnm-server/src/runtime/legacy_auth.rs': '20830ff85bbb0d608a525cd2a99dcb1645188a1af57dd9a695f60f24e2596fb1',
 'crates/trnm-server/src/runtime/legacy_repository.rs': 'df8ac88d58aa19b57cf28fe341669a49355d1c348be279c81cd70d2d3995e141',
 'crates/trnm-persistence-pg/src/nakama_account.rs': '7e045e8b45e3aa35009be6b47f04e4962ba10c4f8ef4b85f7a5c82337e48b430'}

def validate_reviewed_complete_production_files(bindings: dict[str, str], sources: dict[Path, str] | None = None) -> None:
    """Bind raw complete files before finite region checks; no Rust parsing claim."""
    for path, expected in bindings.items():
        raw = (ROOT / path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            fail(f"reviewed complete production file drift: {path}")
        if sources is not None and sources.get(Path(path), "").encode("utf-8") != raw:
            fail(f"reviewed complete production text differs from raw bytes: {path}")


def validate_accounts_schema_source() -> None:
    """An embedded source frontier never authorizes a new runtime profile."""
    validate_reviewed_complete_production_files(ACCOUNTS_FULL_SOURCE_SHA256)
    schema = (PERSISTENCE_ROOT / "schema.rs").read_text()
    gate = (PERSISTENCE_ROOT / "schema_parts/account_catalog.rs").read_text()
    migrate = (PERSISTENCE_ROOT / "schema_parts/migrate.rs").read_text()
    metadata = (PERSISTENCE_ROOT / "schema_parts/metadata.rs").read_text()
    lock = json.loads((ROOT / "migrations/MIGRATION_CHAIN.lock.json").read_text())
    status = json.loads((ROOT / "docs/status/TRNM_SERVER_STATUS.json").read_text())["nakama_accounts_schema5_source_candidate"]
    if 'pub const AUTHORITATIVE_SCHEMA_VERSION: u64 = 4;' not in schema or 'pub const AUTHORITATIVE_STORAGE_WRITER_EPOCH: u64 = 4;' not in schema or lock.get("schema_version") != 5 or lock.get("default_runtime_schema_version") != 4:
        fail("schema5 source cannot silently promote the current storage ABI")
    if 'const ACCOUNT_CATALOG_CAPTURE_READY: bool = false;' not in gate or 'schema5_native_catalog_capture_pending' not in gate:
        fail("schema5 native catalog observations must remain explicitly pending")
    expected_gate = (
        "fn require_account_catalog_capture(target: AuthoritativeSchemaTarget) -> Result<(), DomainError> {\n"
        "    if target == AuthoritativeSchemaTarget::NakamaAccountsV5 && !ACCOUNT_CATALOG_CAPTURE_READY {\n"
        "        return Err(failed_precondition(\n"
        "            \"schema5_native_catalog_capture_pending\",\n"
        "        ));\n"
        "    }\n"
        "    Ok(())\n"
        "}"
    )
    if gate.count(expected_gate) != 1:
        fail("schema5 account gate must retain its exact closed pre-I/O body")
    for name in ('verify_authoritative_schema_target', 'migrate_authoritative_schema_target'):
        match = re.search(r'pub fn ' + name + r'\([\s\S]*?\) -> Result<[^\n]+> \{([\s\S]*?)\n    \}', migrate)
        if match is None or match.group(1).lstrip().splitlines()[0].strip() != 'require_account_catalog_capture(target)?;':
            fail("schema5 target capture gate must precede repository or DDL work")
    if 'descriptor.version < 5 && recorded.v4_apply_source_commit.is_some()' not in metadata or 'v4_apply_source_commit: if target.version == 5' not in metadata:
        fail("schema5 must preserve prior4 provenance and reject prepublication")
    for field in ('native_catalog_observations_bound', 'native_migration_executed', 'default_runtime_promoted', 'account_repository_implemented', 'account_service_HTTP_gRPC_implemented', 'account_transfer_implemented', 'schema5_storage_transfer_admitted', 'schema5_backup_restore_qualified', 'accepted', 'compatibility_credit'):
        if status.get(field) is not False:
            fail("schema5 account source frontier overclaims " + field)


# Finite source candidate bindings; identity checks grant no runtime evidence.
LEGACY_AUTH_REQUIRED_FILES = tuple(Path(value) for value in ('crates/trnm-server/src/lib.rs', 'crates/trnm-server/src/runtime/mod.rs', 'crates/trnm-server/src/runtime/legacy_auth.rs', 'crates/trnm-server/src/runtime/legacy_auth_tests.rs', 'crates/trnm-server/src/runtime/legacy_repository.rs', 'crates/trnm-server/src/runtime/legacy_repository_tests.rs', 'crates/trnm-server/src/runtime/legacy_device_predicates.rs', 'crates/trnm-server/src/runtime/legacy_uuid.rs', 'crates/trnm-server/src/runtime/config.rs', 'crates/trnm-token-crypto-provider/src/lib.rs', 'crates/trnm-token-crypto-provider/src/nakama_legacy.rs', 'crates/trnm-token-jwt-adapter/src/lib.rs', 'crates/trnm-token-jwt-adapter/src/nakama_legacy.rs', 'crates/trnm-token-jwt-adapter/src/nakama_legacy_payload.rs', 'crates/trnm-token-jwt-adapter/src/nakama_legacy_decode.rs', 'crates/trnm-token-jwt-adapter/src/nakama_legacy_header.rs', 'crates/trnm-token-jwt-adapter/src/nakama_legacy_verify.rs', 'crates/trnm-session-core/src/lib.rs', 'crates/trnm-session-core/src/nakama_legacy_blacklist.rs', 'crates/trnm-persistence-pg/src/nakama_account.rs', 'crates/trnm-persistence-pg/src/nakama_account/native.rs', 'crates/trnm-persistence-pg/src/nakama_account/tests.rs', 'crates/trnm-persistence-pg/src/schema_parts/account_native_attributes.rs', 'crates/trnm-persistence-pg/src/schema_parts/account_native_columns.rs', 'crates/trnm-persistence-pg/src/schema_parts/account_native_objects.rs', 'crates/trnm-persistence-pg/src/schema_parts/account_native_triggers.rs', 'crates/trnm-persistence-pg/src/schema_parts/account_relation_semantics.rs', 'contracts/session/nakama-v340-token-source-lock.json', 'crates/trnm-token-crypto-provider/src/software.rs', 'crates/trnm-server/src/runtime/legacy_http_api.rs', 'crates/trnm-server/src/runtime/legacy_http_api_tests.rs', 'crates/trnm-server/src/runtime/pool.rs', 'crates/trnm-server/src/runtime/retry.rs', 'crates/trnm-persistence-pg/src/lib.rs', 'crates/trnm-persistence-pg/src/pool.rs', 'crates/trnm-persistence-pg/src/pool_parts/base.rs', 'crates/trnm-persistence-pg/src/pool_parts/pool.rs', 'crates/trnm-persistence-pg/src/schema_parts/migrate.rs', 'crates/trnm-persistence-pg/src/storage_parts/01_repository.rs', 'crates/trnm-persistence-pg/src/storage_parts/04_delete_authorize.rs', 'crates/trnm-persistence-pg/src/storage_import.rs', 'crates/trnm-server/src/runtime/auth.rs', 'crates/trnm-server/src/runtime/legacy_config.rs', 'crates/trnm-server/src/runtime/schema.rs', 'crates/trnm-server/src/runtime/server.rs', 'crates/trnm-server/src/runtime/app.rs', 'crates/trnm-server/src/runtime/http.rs', 'crates/trnm-server/src/runtime/session_api.rs', 'crates/trnm-server/src/runtime/grpc.rs', 'crates/trnm-server/src/runtime/auth_runtime.rs', 'crates/trnm-server/src/runtime/auth_app_tests.rs'))
REQUIRED_FILES.update(ROOT / path for path in LEGACY_AUTH_REQUIRED_FILES)
REQUIRED_TESTS.update({
    'ban_samples_clock_before_bounds_preparation_and_preserves_atomic_quota',
    'cockroach_release_success_is_committed_despite_cleanup_error_without_replay',
    'committed_device_issuer_failure_has_no_pair_and_keeps_native_cleanup',
    'committed_device_random_and_each_clock_stage_keep_cause_and_cleanup',
    'committed_device_single_session_clock_quota_and_poison_keep_diagnostic',
    'deferred_uuid_failure_is_internal_before_tx_without_lookup_recall',
    'deferred_uuid_invalid_version_or_variant_never_begins_transaction',
    'deferred_uuid_is_generated_once_and_reused_across_both_profile_attempts',
    'deferred_uuid_skips_closed_existing_banned_and_missing_no_create_paths',
    'device_success_preserves_exact_normal_token_pair_and_clock_order',
    'endpoint_purpose_never_falls_back',
    'existing_device_failure_does_not_claim_current_commit_or_allow_retry',
    'legacy_hs256_defaults_are_distinct_and_do_not_weaken_software_keys',
    'legacy_hs256_wrong_domain_handle_and_epoch_have_no_fallback',
    'original_crlf_segments_are_signed_without_reencoding',
    'postgres_unknown_completion_is_never_retried_or_acknowledged',
    'public_device_call_keeps_one_repository_outcome_and_commit_truth_on_issue_failure',
    'wrong_mac_precedes_untrusted_claim_semantics',
})

REQUIRED_TESTS.update({'boxed_lease_clone_preserves_confirmed_creation_cleanup_and_unknown_facts', 'device_error_mapping_preserves_semantics_and_old_private_branches', 'legacy_error_results_remain_below_large_error_threshold_with_boxed_lease', 'device_native_and_lease_errors_keep_private_message_and_specific_code', 'registered_options_extensions_reject_before_unknown_and_null_handling'})

LEGACY_AUTH_REGION_BINDINGS = [('legacy-fixed-key-construction', 'crates/trnm-token-crypto-provider/src/nakama_legacy.rs', '    pub fn new(\n', '    fn tag(\n', 'e4995240d94cefa5ad8767e0889d8b8e23a32499b5d2c7a8d9ae5b87347c2910'), ('legacy-fixed-key-selection', 'crates/trnm-token-crypto-provider/src/nakama_legacy.rs', '    fn tag(\n', '\n}\n\nimpl fmt::Debug', 'e87556b29e78a740b473c3ab163cea3e0feea906fb0c605772de8971384379b8'), ('legacy-exact-encoded-MAC-before-claims', 'crates/trnm-token-jwt-adapter/src/nakama_legacy_verify.rs', '    pub fn verify(\n', '\n}\n\n// Both values', '032ff36cefb15db4ddfcd11eb7f7b61389260ac6fc9d75b82fdd7f1924469a6e'), ('legacy-service-owned-construction', 'crates/trnm-server/src/runtime/legacy_auth.rs', '    pub fn from_config(', '    fn state(', '10994515928ef2fa72034bb5f74e52c1eb7aae71f7660b461242720651b1fa22'), ('legacy-UUID-after-MAC', 'crates/trnm-server/src/runtime/legacy_auth.rs', '    fn parse_at(\n', '    fn verify_access_at(\n', '1a5293276762e363ed94cc7a2fba1fcb659539bcf40245d438e2992a2b32462c'), ('legacy-cache-before-principal', 'crates/trnm-server/src/runtime/legacy_auth.rs', '    fn verify_access_at(\n', '    pub fn refresh<', '030ad246030a7c171c1aea65e70e04d19b0fcd0b997dbf783504ac5e407d0d0f'), ('legacy-refresh-durable-user', 'crates/trnm-server/src/runtime/legacy_auth.rs', '    fn refresh_at<', '    fn issue_session(\n', 'c27e3ec06426b9383feef3c65771e89ff5a121c9bc9f69ba6a4a82539e913423'), ('legacy-logout-purpose-owner-quota', 'crates/trnm-server/src/runtime/legacy_auth.rs', '    fn logout_at(\n', '    pub fn authenticate_device<', '65e3f4bf99dcf33723b7178934d681aaffc6b4e2dc4aa626ad5de2fdaf083b65'), ('legacy-device-error-confirmation', 'crates/trnm-server/src/runtime/legacy_auth.rs', 'impl LegacyDeviceAuthError {', 'impl fmt::Debug for LegacyDeviceAuthError', 'cfd119a7e6ecfd0d4ba53adfeef56c293718ff035efdf07927ff61d8bd3b2c83'), ('legacy-created-only-postcommit', 'crates/trnm-server/src/runtime/legacy_auth.rs', '    fn finish_device_account(\n', '    pub fn remove_all(', 'c4f1d914fc103974a1212f139092be5f18650b3986808f52f379319974ad44ee'), ('legacy-Ban-clock-first', 'crates/trnm-server/src/runtime/legacy_auth.rs', '    fn ban_at(\n', '    pub fn unban(', '90d4b445f9e95760b6d2f3b57b6f95c4ef256d2e0b32252807768a2a8fe75c6c'), ('legacy-native-error-mapping', 'crates/trnm-server/src/runtime/legacy_repository.rs', 'fn map_native_error(', 'fn map_cleanup(', '158e87294037ce67740e854bdebc420f2957f294137ee4567d194e56d429482c'), ('legacy-single-native-device-call', 'crates/trnm-server/src/runtime/legacy_repository.rs', "impl LegacyDeviceRepository for PgLegacyAuthRepository<'_>", 'fn map_stored_user(', 'acdb4874831b335b990844341a713f0039f88285737451f8439ccb400bf39e07'), ('legacy-deferred-account-UUID', 'crates/trnm-persistence-pg/src/nakama_account.rs', 'fn authenticate_device_with_id_source(\n', 'fn insert_both(\n', 'ca044de9376dcbdd5e118f81f83ed00af7795a293b7284f200d093cb56ffdb1e'), ('legacy-fixed-size-postcommit-failure', 'crates/trnm-server/src/runtime/legacy_auth.rs', 'pub struct LegacyDevicePostCommitFailure {', 'pub enum LegacyDeviceAuthError {', 'be6fb711b1a8966f1e13293c5e6cc9f9ec9d82ae7156c8dfe9ed6e3bd401d3b5'), ('legacy-native-outcome-cleanup-preserved', 'crates/trnm-server/src/runtime/legacy_repository.rs', 'fn map_cleanup(', '\n#[cfg(test)]', '4634e5f32fbb5ecc52aea03660850ab60f86021bb2efcb4dd1ff9845f2cfa593')]

LEGACY_AUTH_UPSTREAM_FILES = [{'path': 'server/api.go',
  'blob': '61d1b8763f7b7fcf6ed0bc5cd720ff2314c7dacb',
  'bytes': 30888,
  'sha256': '583942e1fa47902a1b6cd231a08af3c938314cbe665e6a9765c3dc1959b6cae9',
  'verification_scope': 'complete-file-identity-from-retained-official-API-bytes',
  'primary_url': 'https://github.com/heroiclabs/nakama/blob/d4d92f93f78bbbe62c7fc50a3f85c772ec121a09/server/api.go'},
 {'path': 'server/api_authenticate.go',
  'blob': '1f938603160ef1dc7f6546926de5481622139dd2',
  'bytes': 33798,
  'sha256': '89c0659aa6994c353e9d9dc32ccc412cbdb5dfc04537a1c93fa026dd32a06995',
  'verification_scope': 'complete-file-identity-from-retained-official-API-bytes',
  'primary_url': 'https://github.com/heroiclabs/nakama/blob/d4d92f93f78bbbe62c7fc50a3f85c772ec121a09/server/api_authenticate.go'},
 {'path': 'server/api_session.go',
  'blob': '1cef7b9d967e93745b19bdd048ff203fc212acea',
  'bytes': 5848,
  'sha256': '8bd5ad085d3ebf2a6accc3e8133f605a1f74fa899068caaca95514d321bdf090',
  'verification_scope': 'complete-file-identity-from-retained-official-API-bytes',
  'primary_url': 'https://github.com/heroiclabs/nakama/blob/d4d92f93f78bbbe62c7fc50a3f85c772ec121a09/server/api_session.go'},
 {'path': 'server/config.go',
  'blob': 'd9cd2b5c1bca3ae13a2560513a8fd99575ec4fe6',
  'bytes': 72466,
  'sha256': 'd437669975abe45ddaa8db556260e5c80faac69a1a4e9c2627967bdbfca24823',
  'verification_scope': 'complete-file-identity-from-retained-official-API-bytes',
  'primary_url': 'https://github.com/heroiclabs/nakama/blob/d4d92f93f78bbbe62c7fc50a3f85c772ec121a09/server/config.go'},
 {'path': 'server/core_authenticate.go',
  'blob': '5ec2f5c00ef875d11fc79865a30059d1147ac7e1',
  'bytes': 50169,
  'sha256': '1995cf76a7e2f35185e5a9eef161da2cb5851be7a8bdeddb2609c1a0b7b4dc96',
  'verification_scope': 'complete-file-identity-from-retained-official-API-bytes',
  'primary_url': 'https://github.com/heroiclabs/nakama/blob/d4d92f93f78bbbe62c7fc50a3f85c772ec121a09/server/core_authenticate.go'},
 {'path': 'server/core_session.go',
  'blob': 'beee140dd6d402434811721f858e64c1c3c79e09',
  'bytes': 3668,
  'sha256': '068b8846dc32321dc874c6cd0693e304042c08e8f75f9e50738254afa227546f',
  'verification_scope': 'complete-file-identity-from-retained-official-API-bytes',
  'primary_url': 'https://github.com/heroiclabs/nakama/blob/d4d92f93f78bbbe62c7fc50a3f85c772ec121a09/server/core_session.go'},
 {'path': 'server/db.go',
  'blob': '80bff94cfa224bc953fd674af673944ebc50e875',
  'bytes': 18795,
  'sha256': '5ea00517ee4b9df752cd40da58e75bf18340bf587a9dd7ca4aaf9cef6c0f472d',
  'verification_scope': 'complete-file-identity-from-retained-official-API-bytes',
  'primary_url': 'https://github.com/heroiclabs/nakama/blob/d4d92f93f78bbbe62c7fc50a3f85c772ec121a09/server/db.go'},
 {'path': 'server/jwt.go',
  'blob': 'ab0c53aef5152429370ffe1d3ec9d273007132be',
  'bytes': 1241,
  'sha256': 'f50b8ced87d8b457714ee189a6dfdcee37e78fb57dfbd0b93ea9aade789f8cd2',
  'verification_scope': 'complete-file-identity-from-retained-official-API-bytes',
  'primary_url': 'https://github.com/heroiclabs/nakama/blob/d4d92f93f78bbbe62c7fc50a3f85c772ec121a09/server/jwt.go'},
 {'path': 'server/session_cache.go',
  'blob': '2f3d4153bfadd6ad398d24173fe4413c12bf9b04',
  'bytes': 6227,
  'sha256': 'c7cc40783688ae48b677f3cca72f5afd7eefb8ab4ba0c6e81e3a6ee0ca2964e6',
  'verification_scope': 'complete-file-identity-from-retained-official-API-bytes',
  'primary_url': 'https://github.com/heroiclabs/nakama/blob/d4d92f93f78bbbe62c7fc50a3f85c772ec121a09/server/session_cache.go'}]

LEGACY_AUTH_OLD_SOURCE_IDENTITIES = [{'path': 'server/api_authenticate.go',
  'blob': '1f938603160ef1dc7f6546926de5481622139dd2',
  'observed_contract': ['tid', 'uid', 'usn', 'vrs', 'exp', 'iat']},
 {'path': 'server/jwt.go',
  'blob': 'ab0c53aef5152429370ffe1d3ec9d273007132be',
  'observed_contract': ['HS256', 'expiration-required', 'valid-method-restriction']},
 {'path': 'server/api_session.go',
  'blob': '1cef7b9d967e93745b19bdd048ff203fc212acea',
  'observed_contract': ['refresh-required', 'same-token-id-refresh', 'session-cache-validation']}]

LEGACY_AUTH_PRODUCTION_PREFIXES = {'crates/trnm-token-jwt-adapter/src/nakama_legacy.rs': 'd98d5fbdf5e633950e25ba8be8dad16a5ee4a6594ff99d0ce9c5dc2892057cf4',
 'crates/trnm-token-jwt-adapter/src/nakama_legacy_payload.rs': '8110eb4e36c41f491037fb0ac898b5817b8ccff394ab6934327cafa53094241c',
 'crates/trnm-token-jwt-adapter/src/nakama_legacy_decode.rs': '33ebbc1cad842fe552f54ee0f2c62c58dd03498b5d64b2554dd3d3cf5482f516',
 'crates/trnm-token-jwt-adapter/src/nakama_legacy_header.rs': 'edb5f3ecfa88e19b01c01ada49fc5d055411f1ab62520b5abb01197144a28dd3',
 'crates/trnm-session-core/src/nakama_legacy_blacklist.rs': '1d89c5d29ceabee032d0cacce0356c0f0fa0383e42251b5b378d1cc9ba8bdd8f',
 'crates/trnm-server/src/runtime/legacy_device_predicates.rs': '6e788e77a2db36d9d2dc64ad65374a1bae99e91f68b73822543520810b3398a7',
 'crates/trnm-server/src/runtime/legacy_uuid.rs': 'b344139d0d1c956a273b8a889521b11569e722fd7f95d039031cdc09dc558c4e',
 'crates/trnm-token-crypto-provider/src/software.rs': '043e0d21d7d19382ff8cd46affe7c64fb572af65d83cc80770dcc6f70d6f29e7'}

LEGACY_AUTH_ALL_SOURCE_IDENTITIES = [{'path': 'server/api_authenticate.go',
  'blob': '1f938603160ef1dc7f6546926de5481622139dd2',
  'observed_contract': ['tid', 'uid', 'usn', 'vrs', 'exp', 'iat']},
 {'path': 'server/jwt.go',
  'blob': 'ab0c53aef5152429370ffe1d3ec9d273007132be',
  'observed_contract': ['HS256', 'expiration-required', 'valid-method-restriction']},
 {'path': 'server/api_session.go',
  'blob': '1cef7b9d967e93745b19bdd048ff203fc212acea',
  'observed_contract': ['refresh-required', 'same-token-id-refresh', 'session-cache-validation']},
 {'path': 'server/api.go',
  'blob': '61d1b8763f7b7fcf6ed0bc5cd720ff2314c7dacb',
  'observed_contract': ['Bearer-purpose-key', 'UUID-parse', 'session-cache-before-context']},
 {'path': 'server/core_authenticate.go',
  'blob': '5ec2f5c00ef875d11fc79865a30059d1147ac7e1',
  'observed_contract': ['Device-lookup-before-create',
                        'deferred-account-UUID',
                        'native-transaction']},
 {'path': 'server/core_session.go',
  'blob': 'beee140dd6d402434811721f858e64c1c3c79e09',
  'observed_contract': ['stored-username-refresh',
                        'disable-Unix-seconds',
                        'ordered-purpose-owner-logout']},
 {'path': 'server/session_cache.go',
  'blob': '2f3d4153bfadd6ad398d24173fe4413c12bf9b04',
  'observed_contract': ['blacklist-only', 'clock-before-lock', 'Add-Unban-no-op']},
 {'path': 'server/config.go',
  'blob': 'd9cd2b5c1bca3ae13a2560513a8fd99575ec4fe6',
  'observed_contract': ['nonempty-distinct-session-keys',
                        'positive-TTLs',
                        'explicit-default20-and27']},
 {'path': 'server/db.go',
  'blob': '80bff94cfa224bc953fd674af673944ebc50e875',
  'observed_contract': ['PostgreSQL-class40-bounded-attempts',
                        'Cockroach-savepoint-retry',
                        'completion-error-boundary']}]

LEGACY_AUTH_DERIVED_ATTRIBUTION = {'interpretation_scope': 'independent-first-party-Rust-derived-from-selected-expressions; '
                         'complete-file identity is not complete behavior equivalence',
 'Go_code_compiled_into_Rust_target': False,
 'regions': [{'path': 'server/api_authenticate.go',
              'blob': '1f938603160ef1dc7f6546926de5481622139dd2',
              'first_line': 33,
              'last_line': 64,
              'region_sha256': '00e4476411b175e6433fcad10d1634bea17b48f6edefb1622333e46945562330',
              'scope': 'derived-narrow-expression-or-function',
              'purpose': 'six-tagged-claims-and-input-POSIX-expressions'},
             {'path': 'server/api_authenticate.go',
              'blob': '1f938603160ef1dc7f6546926de5481622139dd2',
              'first_line': 216,
              'last_line': 289,
              'region_sha256': '76a2e88fa6b4c3a6ddb71a281a408e6b807aed77951be272c3e9166235448d4b',
              'scope': 'derived-narrow-expression-or-function',
              'purpose': 'Device-input-order-and-token-issuance'},
             {'path': 'server/api.go',
              'blob': '61d1b8763f7b7fcf6ed0bc5cd720ff2314c7dacb',
              'first_line': 518,
              'last_line': 546,
              'region_sha256': 'a3c91ad05734c6f77c9656aba6bf45a484e4a63c717204cada56dbc99377e7d2',
              'scope': 'derived-narrow-expression-or-function',
              'purpose': 'Bearer-and-token-UUID-parser'},
             {'path': 'server/jwt.go',
              'blob': 'ab0c53aef5152429370ffe1d3ec9d273007132be',
              'first_line': 22,
              'last_line': 37,
              'region_sha256': '3b8bc443761a54b9843bf42ab3ab46c676a1d97f324cdc8fc4599c4bc31c5f63',
              'scope': 'derived-narrow-expression-or-function',
              'purpose': 'HS256-parser-policy'},
             {'path': 'server/core_session.go',
              'blob': 'beee140dd6d402434811721f858e64c1c3c79e09',
              'first_line': 34,
              'last_line': 97,
              'region_sha256': '2ff18d98f5ad6f8258ec671d121d3a5a625f6abc6c4ed97f57d4e2e6805b7c83',
              'scope': 'derived-narrow-expression-or-function',
              'purpose': 'refresh-durable-user-and-logout-purpose-order'},
             {'path': 'server/api_session.go',
              'blob': '1cef7b9d967e93745b19bdd048ff203fc212acea',
              'first_line': 29,
              'last_line': 94,
              'region_sha256': 'edaaff6e47705f5f358313778a1a15fe62da308abffc500dd5f1c4a099979430',
              'scope': 'derived-narrow-expression-or-function',
              'purpose': 'refresh-same-token-ID-and-issued-at'},
             {'path': 'server/session_cache.go',
              'blob': '2f3d4153bfadd6ad398d24173fe4413c12bf9b04',
              'first_line': 62,
              'last_line': 222,
              'region_sha256': 'b2ff5d21e0111a5992a6d668eb8c1e5d20376149e6fab59101923c312702e9f0',
              'scope': 'derived-narrow-expression-or-function',
              'purpose': 'blacklist-validity-Remove-RemoveAll-Ban-and-ticker'},
             {'path': 'server/core_authenticate.go',
              'blob': '5ec2f5c00ef875d11fc79865a30059d1147ac7e1',
              'first_line': 185,
              'last_line': 285,
              'region_sha256': 'ee6b3afac7ca1419e75fb5c496725ad40cdaa5b8bc54506f7e4678e8c5b34454',
              'scope': 'derived-narrow-expression-or-function',
              'purpose': 'Device-native-account-lookup-and-creation'},
             {'path': 'server/config.go',
              'blob': 'd9cd2b5c1bca3ae13a2560513a8fd99575ec4fe6',
              'first_line': 147,
              'last_line': 161,
              'region_sha256': '3f84f3a999ef29ea78ef907bb59cbfbc2caa84a874277ab81c9a64aa401d3c60',
              'scope': 'derived-narrow-expression-or-function',
              'purpose': 'positive-TTL-and-nonempty-distinct-key-validation'},
             {'path': 'server/config.go',
              'blob': 'd9cd2b5c1bca3ae13a2560513a8fd99575ec4fe6',
              'first_line': 807,
              'last_line': 818,
              'region_sha256': '265547ad21c2b595fbeea9262577486c0d9c12646541cc204270bdc10bc59886',
              'scope': 'derived-narrow-expression-or-function',
              'purpose': 'explicit-public-session-defaults'},
             {'path': 'server/db.go',
              'blob': '80bff94cfa224bc953fd674af673944ebc50e875',
              'first_line': 308,
              'last_line': 408,
              'region_sha256': '80e14333ca221744a2bd243c1e028e4cb3e02269aaefe688642b79e0160aebed',
              'scope': 'derived-narrow-expression-or-function',
              'purpose': 'SQL-transaction-profile-attempt-and-completion-rules'}],
 'accepted': False,
 'compatibility_credit': False}

LEGACY_AUTH_OLD_LIMITATIONS = ['No key ID or key epoch is carried in the legacy token.',
 'Access and refresh tokens use distinct configured signing keys.',
 'The same token ID and original issued-at value are reused during refresh in the pinned '
 'implementation.',
 'Legacy overlap rotation is ambiguous unless verification tries external key policy; this '
 'candidate fails closed instead of guessing.']

def validate_legacy_auth_source(sources: dict[Path, str], status: dict, source_lock: dict) -> None:
    """Close finite candidate source regions without granting runtime evidence."""
    validate_reviewed_complete_production_files(LEGACY_AUTH_FULL_SOURCE_SHA256, sources)
    for path in LEGACY_AUTH_REQUIRED_FILES:
        if path not in sources:
            fail(f"legacy auth required source absent: {path}")
    candidate = status.get("nakama_legacy_auth_source_candidate", {})
    if candidate.get("status") != "source-candidate-http-app-connected-accounts5-gated":
        fail("legacy auth source state drift")
    expected = {
        "source_composition_present": True, "native_repository_adapter_present": True,
        "legacy_principal_distinct_from_durable_session": True,
        "fixed_purpose_keys_without_epoch_or_fallback": True,
        "unconfirmed_means_no_commit": False, "caller_replay_permitted": False,
        "caller_compensation_permitted": False, "token_ID_before_issued_at": True,
        "Ban_cutoff_before_key_preparation_and_cache_lock": True,
    }
    if any(candidate.get(key) is not value for key, value in expected.items()):
        fail("legacy auth source truth/commit/replay policy drift")
    for key, value in (("key_budget_bytes",4096),("default_runtime_schema_version",4),
                       ("default_storage_writer_epoch",4),("default_runtime_table_count",12),
                       ("supported_source_schema_version",5)):
        if type(candidate.get(key)) is not int or candidate[key] != value:
            fail(f"legacy auth bounded schema/profile policy drift: {key}")
    if candidate.get("account_gate_reason") != "schema5_native_catalog_capture_pending" or (
        candidate.get("active_authentication_profile") != "explicitly-selected-disabled-durable-family-nakama-legacy-source"
    ) or candidate.get("device_error_variants") != ["Unconfirmed","CommittedCreation","UnconfirmedCleanup"]:
        fail("legacy auth gate, active authority or error-envelope drift")
    flags = candidate.get("acceptance_flags", {})
    expected_flags = {"HTTP_connected","gRPC_connected","startup_config_enabled",
        "native_device_transaction_qualified","schema5_migration_qualified",
        "signature_compatible","refresh_behavior_compatible","accepted","production_ready",
        "full_replacement","compatibility_credit","current_HEAD_CI_qualified"}
    if set(flags) != expected_flags or any(value is not False for value in flags.values()):
        fail("legacy auth source cannot grant runtime or acceptance credit")
    relation_scope = candidate.get("relation_observation_scope", {})
    fetch_scope = candidate.get("source_fetch_policy", {})
    if (relation_scope.get("qualifies_account_migration_or_device_transaction") is not False
        or fetch_scope.get("checks_before_IO_and_after_delivery") is not True
        or fetch_scope.get("global_nonreturning_IO_bound_qualified") is not False
        or any(type(relation_scope.get(key)) is not int for key in (
            "postgresql_finite_negative_cases", "cockroachdb_negative_cases"))
        or any(type(fetch_scope.get(key)) is not int for key in (
            "request_seconds", "collection_seconds"))):
        fail("legacy auth observation/budget booleans and integers cannot be coerced")
    if candidate.get("source_paths") != [str(path) for path in LEGACY_AUTH_REQUIRED_FILES
                                          if path.name != "software.rs"]:
        fail("legacy auth machine source inventory drift")
    if candidate.get("relation_observation_scope") != {
        "readonly_profiles":["postgresql","cockroachdb"],
        "postgresql_finite_negative_cases":5,"cockroachdb_negative_cases":0,
        "qualifies_account_migration_or_device_transaction":False,
    } or candidate.get("source_fetch_policy") != {
        "request_seconds":30,"collection_seconds":605,
        "checks_before_IO_and_after_delivery":True,"global_nonreturning_IO_bound_qualified":False,
    }:
        fail("legacy auth observations/resource scope drift")
    if source_lock.get("sources", [])[:3] != LEGACY_AUTH_OLD_SOURCE_IDENTITIES or (
        source_lock.get("verified_files") != LEGACY_AUTH_UPSTREAM_FILES
    ):
        fail("legacy auth exact upstream file identities drift")
    if source_lock.get("upstream", {}).get("commit") != "d4d92f93f78bbbe62c7fc50a3f85c772ec121a09":
        fail("legacy auth upstream commit drift")
    if source_lock.get("sources") != LEGACY_AUTH_ALL_SOURCE_IDENTITIES or (
        source_lock.get("legacy_limitations") != LEGACY_AUTH_OLD_LIMITATIONS
    ) or set(source_lock.get("claims", {})) != {"token_serialization_compatible",
        "signature_compatible","refresh_behavior_compatible","c1_earned","production_ready"} or any(
        source_lock.get("claims",{}).get(key) is not False for key in (
            "token_serialization_compatible","signature_compatible","refresh_behavior_compatible",
            "c1_earned","production_ready")):
        fail("legacy auth source lock cannot grant compatibility")
    attribution = source_lock.get("implementation_attribution", {})
    if attribution != LEGACY_AUTH_DERIVED_ATTRIBUTION:
        fail("legacy auth complete-file identity and narrow-derived-region scope cannot be conflated")
    if attribution.get("Go_code_compiled_into_Rust_target") is not False or (
        attribution.get("accepted") is not False or attribution.get("compatibility_credit") is not False
    ) or not attribution.get("regions"):
        fail("legacy auth selected-region attribution drift")
    for label, path, start, end, digest in LEGACY_AUTH_REGION_BINDINGS:
        source = sources[Path(path)]
        if source.count(start) != 1:
            fail(f"legacy auth local region missing/ambiguous: {label}")
        first = source.index(start)
        last = source.find(end, first + len(start))
        if last < 0 or hashlib.sha256(source[first:last].encode()).hexdigest() != digest:
            fail(f"legacy auth local ordering/error/authority region drift: {label}")
    for path, digest in LEGACY_AUTH_PRODUCTION_PREFIXES.items():
        prefix = sources[Path(path)].split("\n#[cfg(test)]", 1)[0]
        if hashlib.sha256(prefix.encode()).hexdigest() != digest:
            fail(f"legacy auth codec/cache/input or old Software production prefix drift: {path}")
    registrations = {
        "crates/trnm-server/src/lib.rs":("pub use runtime::legacy_service_exports::*;",),
        "crates/trnm-server/src/runtime/mod.rs":("mod legacy_auth;","mod legacy_repository;",
            "mod legacy_uuid;","mod legacy_device_predicates;","pub use super::legacy_auth::*;",
            "pub use super::legacy_repository::PgLegacyAuthRepository;"),
        "crates/trnm-token-crypto-provider/src/lib.rs":("mod nakama_legacy;",),
        "crates/trnm-token-jwt-adapter/src/lib.rs":("mod nakama_legacy_decode;",
            "mod nakama_legacy_verify;","NakamaLegacyVerifier","NakamaLegacyIssuer"),
        "crates/trnm-session-core/src/lib.rs":("mod nakama_legacy_blacklist;",),
    }
    for path, markers in registrations.items():
        require_markers("legacy auth local module registration", sources[Path(path)], markers)
    validate_legacy_adapter_composition(sources, status, source_lock)
    validate_selected_auth_app_source(sources, status)



# Complete raw files close the finite target/pool/HTTP source seam. These
# checks do not parse arbitrary Rust or prove native, route or resource parity.
LEGACY_ADAPTER_FULL_SOURCE_SHA256 = {'crates/trnm-server/src/runtime/legacy_http_api.rs': 'f25620caf671f7faa425a53867caa705c39a3d52cf0f90ca5dcc9a3d8aa3da76', 'crates/trnm-server/src/runtime/pool.rs': 'c7058fe9c77228bc2551fd6c02c2f2bd5132f9706d45fbdf40d5b3969be67ba0', 'crates/trnm-server/src/runtime/retry.rs': '08344b4f592d193cb16b9392242be2d0770067ba9e6e1a9495593a3376cadb49', 'crates/trnm-persistence-pg/src/lib.rs': 'd604caf57010b71d6d3cebb58455f6a616519412dca2997807bfbc9bea851e96', 'crates/trnm-persistence-pg/src/pool.rs': 'd4d7f3cff7e922c215ab345ed7e167a6a1c21dbb56b61c0a11ed799902381883', 'crates/trnm-persistence-pg/src/pool_parts/base.rs': '1bc565243740e4b639cd69ea49f4ee8974299b2cc101bd6d3054eeae7783ec6c', 'crates/trnm-persistence-pg/src/pool_parts/pool.rs': 'b5ef7f83218785290366ba21490389bca23f182c3ec9e5e6dec879c5a171792d', 'crates/trnm-persistence-pg/src/schema_parts/migrate.rs': '8324f47213f1ea9172e01d6524cd1280649b6c8ffdd07e3fd1b348ae2e2dec16', 'crates/trnm-persistence-pg/src/storage_parts/01_repository.rs': '34abe49096eb1f9df15b0ed8a45295de050616971839a3df354c37b3f32fb301', 'crates/trnm-persistence-pg/src/storage_parts/04_delete_authorize.rs': '15d15ef36ef214ed518ef15fdf8acb3c4856faa1b9d063987089b1e03e67ae92', 'crates/trnm-persistence-pg/src/storage_import.rs': 'd54162a7f65c9923098edfa39642a3010f124c0f7d9515fb3de3e27b514a11d0', 'crates/trnm-server/src/runtime/legacy_repository.rs': 'df8ac88d58aa19b57cf28fe341669a49355d1c348be279c81cd70d2d3995e141', 'crates/trnm-server/src/runtime/legacy_auth.rs': '20830ff85bbb0d608a525cd2a99dcb1645188a1af57dd9a695f60f24e2596fb1', 'crates/trnm-server/src/runtime/mod.rs': '57fd8e18930ef6477a3e4c8332dadf823917c056c8e22846ce52064ab7353c93', 'crates/trnm-server/src/lib.rs': 'cc28e29f62382b56c719171efed7e95f32ee2ed214db22d15886471491047749'}
LEGACY_ADAPTER_POLICY = {'immutable_serving_schema_target': True, 'legacy_constructors_default': 'StorageV4', 'default_schema_version': 4, 'default_storage_writer_epoch': 4, 'default_table_count': 12, 'account_source_schema_version': 5, 'account_target_table_count': 14, 'account_gate_enabled': False, 'typed_single_lease': True, 'observed_native_result_retained': True, 'late_result_is_successful_ack': False, 'unobserved_means_no_effect': False, 'generic_business_retry': False, 'caller_replay': False, 'caller_compensation': False, 'canceled_or_unknown_lease_recycled': False, 'HTTP_route_or_authority_installed': False, 'HTTP_codec_source_present': True, 'registered_options_extensions': 7, 'registration_scope': 'captured gateway imports; not whole-process GlobalTypes census', 'registered_extension_check_before_unknown_and_null': True, 'registered_extension_rule_applies_to_vars_map_keys': False, 'first_object_only': True, 'Go_invalid_UTF8_and_overlap_create_qualified': False, 'auth_before_business_policy': 'AGENTS rule 11; differs from pinned gateway decode-first', 'Basic_base64_dependency': '=0.22.1', 'bounds': {'body_bytes': 524288, 'json_depth': 64, 'json_nodes': 8192, 'json_members': 2048, 'decoded_string_bytes': 524288, 'scalar_bytes': 131072, 'vars_entries': 256, 'query_bytes': 8192, 'query_pairs': 64, 'authorization_bytes': 65536, 'response_bytes': 2097152}, 'accepted': False, 'compatibility_credit': False, 'production_ready': False, 'full_replacement': False, 'HTTP_App_source_routes_connected': True, 'selected_authority_config_source_present': True, 'HTTP_route_or_authority_installed_scope': 'actual gated runtime qualification; source wiring is separately registered'}
LEGACY_HTTP_SOURCE_BINDING = {'upstream_commit': 'd4d92f93f78bbbe62c7fc50a3f85c772ec121a09',
 'verified_files': [{'path': 'apigrpc/apigrpc.proto',
                     'bytes': 24346,
                     'sha256': '8e2ebf5d8569b2847dec95d3132d3a2537e28d588f55d301decdd1775c906ca4',
                     'git_blob_sha1': '1cc63aae1aaa5dc56ede9c9d0b6f9a95ff91361c',
                     'primary_url': 'https://github.com/heroiclabs/nakama/blob/d4d92f93f78bbbe62c7fc50a3f85c772ec121a09/apigrpc/apigrpc.proto'},
                    {'path': 'apigrpc/apigrpc.pb.gw.go',
                     'bytes': 367338,
                     'sha256': '590d0d37be149e158fe514e64ecef06955e5baa5b2b64c7cb4d5bb2b0e1c670d',
                     'git_blob_sha1': '0e741e8ef596cb7c3ca2a636e3b3403a22105a39',
                     'primary_url': 'https://github.com/heroiclabs/nakama/blob/d4d92f93f78bbbe62c7fc50a3f85c772ec121a09/apigrpc/apigrpc.pb.gw.go'},
                    {'path': 'vendor/github.com/grpc-ecosystem/grpc-gateway/v2/runtime/query.go',
                     'bytes': 12228,
                     'sha256': 'cdb3c973f81862d47dacf2056cf0ac58c66c98f9c0b609efc9bc6f10f69510c6',
                     'git_blob_sha1': '8549dfb97afb0c9e68aa1349d05c87a40fdca117',
                     'primary_url': 'https://github.com/heroiclabs/nakama/blob/d4d92f93f78bbbe62c7fc50a3f85c772ec121a09/vendor/github.com/grpc-ecosystem/grpc-gateway/v2/runtime/query.go'},
                    {'path': 'vendor/github.com/grpc-ecosystem/grpc-gateway/v2/runtime/marshal_jsonpb.go',
                     'bytes': 8900,
                     'sha256': '64a8847a795ee97267d48d6a8904ea0e32e6b480e218fbe731ba7472e012962d',
                     'git_blob_sha1': '3d07063007d5d6f097ae90d9f230f2a4e4beb9ac',
                     'primary_url': 'https://github.com/heroiclabs/nakama/blob/d4d92f93f78bbbe62c7fc50a3f85c772ec121a09/vendor/github.com/grpc-ecosystem/grpc-gateway/v2/runtime/marshal_jsonpb.go'},
                    {'path': 'vendor/github.com/grpc-ecosystem/grpc-gateway/v2/runtime/errors.go',
                     'bytes': 7193,
                     'sha256': '5dc0dd6af8471c0922164791d4a628338305a30ebbcd3198a22f22b6599e7a8b',
                     'git_blob_sha1': 'bbe7decf09bc61c5b2166c9ef3be4754d5df8913',
                     'primary_url': 'https://github.com/heroiclabs/nakama/blob/d4d92f93f78bbbe62c7fc50a3f85c772ec121a09/vendor/github.com/grpc-ecosystem/grpc-gateway/v2/runtime/errors.go'},
                    {'path': 'vendor/google.golang.org/protobuf/encoding/protojson/decode.go',
                     'bytes': 18134,
                     'sha256': '41d2b009c5648973715d476e3668cd736a7c815c31a7b392ea4d7594ad3e81e5',
                     'git_blob_sha1': '737d6876d5efd10c82d1cc2b35782a27579034ba',
                     'primary_url': 'https://github.com/heroiclabs/nakama/blob/d4d92f93f78bbbe62c7fc50a3f85c772ec121a09/vendor/google.golang.org/protobuf/encoding/protojson/decode.go'},
                    {'path': 'vendor/google.golang.org/protobuf/encoding/protojson/well_known_types.go',
                     'bytes': 26280,
                     'sha256': 'b0425d7be0da31c38914588bfdea03ce3452a9d547b2dc649fcd06982695912e',
                     'git_blob_sha1': 'e9fe1039437a91f09253addbcd6668f56b08225f',
                     'primary_url': 'https://github.com/heroiclabs/nakama/blob/d4d92f93f78bbbe62c7fc50a3f85c772ec121a09/vendor/google.golang.org/protobuf/encoding/protojson/well_known_types.go'},
                    {'path': 'vendor/google.golang.org/protobuf/internal/encoding/json/decode.go',
                     'bytes': 8821,
                     'sha256': '149097bfd4ae2cd52f8f8d5b36c850c3972124670c75aaabfaa32edb29512aa4',
                     'git_blob_sha1': 'ea1d3e65a5752ff3fdd621635709b6d3dcfae4be',
                     'primary_url': 'https://github.com/heroiclabs/nakama/blob/d4d92f93f78bbbe62c7fc50a3f85c772ec121a09/vendor/google.golang.org/protobuf/internal/encoding/json/decode.go'},
                    {'path': 'vendor/modules.txt',
                     'bytes': 16427,
                     'sha256': '88f58c86148495545a9c8496c8cb5438919317c65ea53ac44f51d31bea4f7d94',
                     'git_blob_sha1': '3ce9841e5d55abb9918240cfdaae2fbf831699f9',
                     'primary_url': 'https://github.com/heroiclabs/nakama/blob/d4d92f93f78bbbe62c7fc50a3f85c772ec121a09/vendor/modules.txt'},
                    {'path': 'apigrpc/apigrpc.pb.go',
                     'bytes': 43220,
                     'sha256': '1d0575929da7cf4020d12155b2ee0cc551f95343ac33ddfa0e2603d3add83ffc',
                     'git_blob_sha1': 'b14e5fd82e934fee10b7a0b7d619eeaa488f9366',
                     'primary_url': 'https://github.com/heroiclabs/nakama/blob/d4d92f93f78bbbe62c7fc50a3f85c772ec121a09/apigrpc/apigrpc.pb.go'},
                    {'path': 'vendor/github.com/grpc-ecosystem/grpc-gateway/v2/runtime/convert.go',
                     'bytes': 8788,
                     'sha256': '9555d52adab6a92bdccee6654287a28f329762c2b61c8516dad7ddeff78a667a',
                     'git_blob_sha1': '2e50082ad1163331d97a070121d2abb0f95f563b',
                     'primary_url': 'https://github.com/heroiclabs/nakama/blob/d4d92f93f78bbbe62c7fc50a3f85c772ec121a09/vendor/github.com/grpc-ecosystem/grpc-gateway/v2/runtime/convert.go'},
                    {'path': 'vendor/github.com/grpc-ecosystem/grpc-gateway/v2/runtime/marshaler.go',
                     'bytes': 1961,
                     'sha256': 'c63d20153f702c1238e8cb2fdff72f627d64b1b934c7b49d079b78986c72c047',
                     'git_blob_sha1': 'b1dfc37af9b9f61125d6b3544f614bd48984ef15',
                     'primary_url': 'https://github.com/heroiclabs/nakama/blob/d4d92f93f78bbbe62c7fc50a3f85c772ec121a09/vendor/github.com/grpc-ecosystem/grpc-gateway/v2/runtime/marshaler.go'},
                    {'path': 'vendor/google.golang.org/genproto/googleapis/api/annotations/annotations.pb.go',
                     'bytes': 5199,
                     'sha256': '2bcace728699fc0e05c18da5653fba32b61802f13cd2c4fde534e006adcb0454',
                     'git_blob_sha1': '0b789e2c5e9e7effd6416cc0ff87955c6aa1c50c',
                     'primary_url': 'https://github.com/heroiclabs/nakama/blob/d4d92f93f78bbbe62c7fc50a3f85c772ec121a09/vendor/google.golang.org/genproto/googleapis/api/annotations/annotations.pb.go'},
                    {'path': 'vendor/google.golang.org/genproto/googleapis/api/annotations/http.pb.go',
                     'bytes': 28416,
                     'sha256': 'a1ca2f72182fa28782a70c44c52c2bcde28d2e4c73ab67fd24faba92f5f27149',
                     'git_blob_sha1': '998205e180847b3138fa534f0c14c65791e1febb',
                     'primary_url': 'https://github.com/heroiclabs/nakama/blob/d4d92f93f78bbbe62c7fc50a3f85c772ec121a09/vendor/google.golang.org/genproto/googleapis/api/annotations/http.pb.go'},
                    {'path': 'vendor/github.com/grpc-ecosystem/grpc-gateway/v2/protoc-gen-openapiv2/options/annotations.pb.go',
                     'bytes': 15763,
                     'sha256': '0860a6f60d589027889073806e941232ebd074f71da3f2bf84dc10a37e9fe29c',
                     'git_blob_sha1': '738c9754a61561adbce8d3543545a5ede98b3985',
                     'primary_url': 'https://github.com/heroiclabs/nakama/blob/d4d92f93f78bbbe62c7fc50a3f85c772ec121a09/vendor/github.com/grpc-ecosystem/grpc-gateway/v2/protoc-gen-openapiv2/options/annotations.pb.go'},
                    {'path': 'vendor/github.com/grpc-ecosystem/grpc-gateway/v2/protoc-gen-openapiv2/options/openapiv2.pb.go',
                     'bytes': 171943,
                     'sha256': '882d72f9f4d605073889cff684ad7d9b2e1dffe457d360dc530d72e89c4abdf9',
                     'git_blob_sha1': '5121dce386cc08f96cfc82f96abea9e2070dd4b4',
                     'primary_url': 'https://github.com/heroiclabs/nakama/blob/d4d92f93f78bbbe62c7fc50a3f85c772ec121a09/vendor/github.com/grpc-ecosystem/grpc-gateway/v2/protoc-gen-openapiv2/options/openapiv2.pb.go'},
                    {'path': 'vendor/github.com/heroiclabs/nakama-common/api/api.pb.go',
                     'bytes': 320372,
                     'sha256': '0f227d58b1d3c8077693ca3a5677c0d66bc6a60d53f5f556e7dcb785c3b1a2fd',
                     'git_blob_sha1': '7335fa2a608c8fb7ea58c39ab935ac2c9589d249',
                     'primary_url': 'https://github.com/heroiclabs/nakama/blob/d4d92f93f78bbbe62c7fc50a3f85c772ec121a09/vendor/github.com/heroiclabs/nakama-common/api/api.pb.go'}],
 'registered_extensions': ['google.api.http',
                           'grpc.gateway.protoc_gen_openapiv2.options.openapiv2_swagger',
                           'grpc.gateway.protoc_gen_openapiv2.options.openapiv2_operation',
                           'grpc.gateway.protoc_gen_openapiv2.options.openapiv2_schema',
                           'grpc.gateway.protoc_gen_openapiv2.options.openapiv2_enum',
                           'grpc.gateway.protoc_gen_openapiv2.options.openapiv2_tag',
                           'grpc.gateway.protoc_gen_openapiv2.options.openapiv2_field'],
 'scope': 'exact cached primary files and captured gateway transitive registration closure; no full-process '
          'registry proof',
 'HTTP_App_connected': False,
 'HTTP_compatible': False,
 'native_execution_qualified': False,
 'accepted': False}

def same_typed_value(actual: object, expected: object) -> bool:
    """Reject bool-as-int or absent/extra nested policy keys."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(actual) == set(expected) and all(
            same_typed_value(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            same_typed_value(a, b) for a, b in zip(actual, expected))
    return actual == expected

LEGACY_HTTP_REFRESH_SOURCE_REGION = (
    "crates/trnm-server/src/runtime/legacy_http_api.rs",
    "pub fn decode_refresh_http_request(",
    "/// The mandatory private verified principal",
    "4c3b3a3435e1058600164436f58963fe7e35b19043e2cc42b79ed07d21efd15a",
)


def validate_legacy_http_refresh_source(sources: dict[Path, str]) -> None:
    """Bind only the reviewed HTTP empty-map inheritance change, not typed core."""
    path, start, end, digest = LEGACY_HTTP_REFRESH_SOURCE_REGION
    source = sources.get(Path(path), "")
    if source.count(start) != 1:
        fail("legacy HTTP Refresh decoder region missing or ambiguous")
    first = source.index(start)
    last = source.find(end, first + len(start))
    if last < 0:
        fail("legacy HTTP Refresh decoder region end missing")
    region = source[first:last]
    if hashlib.sha256(region.encode()).hexdigest() != digest:
        fail("legacy HTTP Refresh validated empty-map inheritance region drift")
    validated = 'variables: variables(object.field(&["vars"])?, limits)?.filter(|vars| !vars.is_empty()),'
    if region.count(validated) != 1:
        fail("legacy HTTP Refresh empty-map collapse must follow map validation")


def validate_legacy_adapter_composition(sources: dict[Path, str], status: dict,
                                        source_lock: dict) -> None:
    validate_reviewed_complete_production_files(LEGACY_ADAPTER_FULL_SOURCE_SHA256, sources)
    validate_legacy_http_refresh_source(sources)
    if not same_typed_value(status.get("nakama_legacy_auth_source_candidate", {}).get(
            "target_pool_HTTP_source_policy"), LEGACY_ADAPTER_POLICY):
        fail("gated legacy target/pool/HTTP source policy drift")
    if not same_typed_value(source_lock.get("HTTP_adapter_source_binding"), LEGACY_HTTP_SOURCE_BINDING):
        fail("legacy HTTP primary source/registry scope drift")
    # These exact file hashes bind the actual call paths, including single FnOnce
    # invocation, delayed native result facts, default4 construction, gate-before-I/O,
    # borrowed boxed lease errors, map-key exception and HTTP call boundaries.
    # No comment or unused literal can stand in for a changed executable body.


# Finite complete bytes of the reviewed config/App source slice. This is not a
# Rust parser or runtime acceptance; subsequent functional changes require rebind.
SELECTED_AUTH_APP_FULL_SOURCE_SHA256 = {'crates/trnm-server/src/runtime/auth.rs': '0299ab171af1be55ba8b2f939df680f23af2fd58a31c4b438796cb70cb0fd718', 'crates/trnm-server/src/runtime/config.rs': '35948aa76100439bb5293de39083226edc32610d7d4e02cabefb91f7bdc94f3a', 'crates/trnm-server/src/runtime/legacy_config.rs': '6f219b7fc1f336dd8ecd1a4491d92b59c2dd1dcb9a6d0db653429255a906f77a', 'crates/trnm-server/src/runtime/mod.rs': '57fd8e18930ef6477a3e4c8332dadf823917c056c8e22846ce52064ab7353c93', 'crates/trnm-server/src/runtime/schema.rs': '47254715b43146cc09e6fb5dbe62580d9e2a9b2c9e82b08a6f468ba3f6201982', 'crates/trnm-server/src/runtime/server.rs': 'a3c5d40c29aaab8bc9489b418962f0222820626f6435005ea154a5a666473b3a', 'crates/trnm-server/src/runtime/app.rs': '959b971dad77929b911526864c4a980ad10a48bca453fb8bb3c1af9d4d6260a1', 'crates/trnm-server/src/runtime/http.rs': '036059033d73ef66ff5f1f244be77a99284e7cf0cdf5981e74fa1115bf9ceb11', 'crates/trnm-server/src/runtime/pool.rs': 'c7058fe9c77228bc2551fd6c02c2f2bd5132f9706d45fbdf40d5b3969be67ba0', 'crates/trnm-server/src/runtime/retry.rs': '08344b4f592d193cb16b9392242be2d0770067ba9e6e1a9495593a3376cadb49', 'crates/trnm-server/src/runtime/session_api.rs': '6797009fa6af9cf21614386fcef57ab4d87e037fba372023598382a77c166bbc', 'crates/trnm-server/src/runtime/grpc.rs': '9c505f70d22adfc40631f820c751d0ba4adb7a977fee59a4b029a98ee24f3d92', 'crates/trnm-server/src/runtime/auth_runtime.rs': '4363223b9be6f40f9794e3799a02a0a0e18c1211efc32c42c2945c4fc681260a', 'crates/trnm-server/src/runtime/auth_app_tests.rs': '743a9f87e62cb4cca90bf2170d622e0998f192f2c3394391a4c04e815aa6ddd8', 'crates/trnm-server/src/runtime/legacy_http_api.rs': 'f25620caf671f7faa425a53867caa705c39a3d52cf0f90ca5dcc9a3d8aa3da76'}
SELECTED_AUTH_APP_POLICY = {'modes': ['Disabled', 'DurableFamily', 'NakamaLegacy'], 'environment_modes': ['disabled', 'durable-family', 'nakama-legacy'], 'default_mode': 'Disabled', 'absent_mode_preserves_explicit_legacy_durable_enablement': True, 'mixed_or_partial_profile_rejected': True, 'canonical_environment': ['TRNM_SERVER_ADMIN_TOKEN', 'TRNM_SERVER_ALLOW_NON_LOOPBACK', 'TRNM_SERVER_ALLOW_PLAINTEXT_DATABASE', 'TRNM_SERVER_AUTH_MODE', 'TRNM_SERVER_BIND', 'TRNM_SERVER_DATABASE_IDLE_TRANSACTION_TIMEOUT_MS', 'TRNM_SERVER_DATABASE_LOCK_TIMEOUT_MS', 'TRNM_SERVER_DATABASE_POOL_ACQUIRE_TIMEOUT_MS', 'TRNM_SERVER_DATABASE_POOL_IDLE_TIMEOUT_MS', 'TRNM_SERVER_DATABASE_POOL_MAX_LIFETIME_MS', 'TRNM_SERVER_DATABASE_POOL_MAX_SIZE', 'TRNM_SERVER_DATABASE_POOL_MIN_IDLE', 'TRNM_SERVER_DATABASE_PROFILE', 'TRNM_SERVER_DATABASE_STATEMENT_TIMEOUT_MS', 'TRNM_SERVER_DATABASE_TLS_IDENTITY_CERT_PEM', 'TRNM_SERVER_DATABASE_TLS_IDENTITY_KEY_PKCS8_PEM', 'TRNM_SERVER_DATABASE_TLS_MODE', 'TRNM_SERVER_DATABASE_TLS_ROOT_CERT_PEM', 'TRNM_SERVER_DATABASE_URL', 'TRNM_SERVER_GRPC_BIND', 'TRNM_SERVER_LEGACY_ACCESS_KEY', 'TRNM_SERVER_LEGACY_ACCESS_TTL_SECONDS', 'TRNM_SERVER_LEGACY_REFRESH_KEY', 'TRNM_SERVER_LEGACY_REFRESH_TTL_SECONDS', 'TRNM_SERVER_LEGACY_SERVER_KEY', 'TRNM_SERVER_LEGACY_SINGLE_SESSION', 'TRNM_SERVER_MAX_REQUEST_BYTES', 'TRNM_SERVER_READ_TIMEOUT_MS', 'TRNM_SERVER_SCHEMA_SOURCE_COMMIT', 'TRNM_SERVER_SCHEMA_TARGET', 'TRNM_SERVER_SESSION_AUTH_AUDIENCE', 'TRNM_SERVER_SESSION_AUTH_ENABLED', 'TRNM_SERVER_SESSION_AUTH_EPOCH', 'TRNM_SERVER_SESSION_AUTH_ISSUER', 'TRNM_SERVER_SESSION_AUTH_KEY_HEX', 'TRNM_SERVER_WRITE_TIMEOUT_MS'], 'legacy_required_environment': ['TRNM_SERVER_LEGACY_SERVER_KEY', 'TRNM_SERVER_LEGACY_ACCESS_KEY', 'TRNM_SERVER_LEGACY_REFRESH_KEY', 'TRNM_SERVER_LEGACY_ACCESS_TTL_SECONDS', 'TRNM_SERVER_LEGACY_REFRESH_TTL_SECONDS'], 'legacy_optional_environment': 'TRNM_SERVER_LEGACY_SINGLE_SESSION', 'legacy_single_session_default': False, 'legacy_explicit_schema_target': 'nakama-accounts-v5', 'legacy_key_bytes_min': 1, 'legacy_key_bytes_max': 4096, 'legacy_implicit_keys_or_TTLs': False, 'legacy_equal_access_refresh_keys_rejected_inherited_local_policy': True, 'legacy_access_TTL_max_seconds': 4611686018, 'legacy_refresh_TTL_max_seconds': 9223372036, 'shared_legacy_service_per_process': True, 'shared_legacy_cache_scope': 'same-process workers; no restart or multi-node qualification', 'legacy_http_routes_source': ['POST /v2/account/authenticate/custom', 'POST /v2/account/authenticate/device', 'POST /v2/account/session/refresh', 'POST /v2/session/logout'], 'query_variants_use_the_same_closed_routes': True, 'private_storage_access_context': ['durable', 'legacy'], 'administrator_is_not_player': True, 'durable_v1_session_routes_only': True, 'credential_or_key_fallback': False, 'legacy_calls_durable_family_operations': False, 'typed_native_account_single_lease': True, 'generic_business_retry': False, 'confirmed_late_commit_allows_tokens': False, 'authentication_and_complete_decode_before_account_or_cache_mutation': True, 'all_legacy_POSTs_rejected_by_drain_before_parsing_or_mutation': True, 'request_response_debug_redacted': True, 'WWW_Authenticate_static_bounded_source': True, 'startup_failure_owned_cleanup_source': True, 'AccountsV5_gate': False, 'AccountsV5_gate_reason': 'schema5_native_catalog_capture_pending', 'default_schema_version': 4, 'default_storage_writer_epoch': 4, 'default_table_count': 12, 'accounts_target_table_count': 14, 'check_config_starts_runtime': False, 'source_only': True, 'native_HTTP_qualified': False, 'paired_oracle_qualified': False, 'production_ready': False, 'compatibility_credit': False, 'source_sha256': {'crates/trnm-server/src/runtime/auth.rs': '0299ab171af1be55ba8b2f939df680f23af2fd58a31c4b438796cb70cb0fd718', 'crates/trnm-server/src/runtime/config.rs': '35948aa76100439bb5293de39083226edc32610d7d4e02cabefb91f7bdc94f3a', 'crates/trnm-server/src/runtime/legacy_config.rs': '6f219b7fc1f336dd8ecd1a4491d92b59c2dd1dcb9a6d0db653429255a906f77a', 'crates/trnm-server/src/runtime/mod.rs': '57fd8e18930ef6477a3e4c8332dadf823917c056c8e22846ce52064ab7353c93', 'crates/trnm-server/src/runtime/schema.rs': '47254715b43146cc09e6fb5dbe62580d9e2a9b2c9e82b08a6f468ba3f6201982', 'crates/trnm-server/src/runtime/server.rs': 'a3c5d40c29aaab8bc9489b418962f0222820626f6435005ea154a5a666473b3a', 'crates/trnm-server/src/runtime/app.rs': '959b971dad77929b911526864c4a980ad10a48bca453fb8bb3c1af9d4d6260a1', 'crates/trnm-server/src/runtime/http.rs': '036059033d73ef66ff5f1f244be77a99284e7cf0cdf5981e74fa1115bf9ceb11', 'crates/trnm-server/src/runtime/pool.rs': 'c7058fe9c77228bc2551fd6c02c2f2bd5132f9706d45fbdf40d5b3969be67ba0', 'crates/trnm-server/src/runtime/retry.rs': '08344b4f592d193cb16b9392242be2d0770067ba9e6e1a9495593a3376cadb49', 'crates/trnm-server/src/runtime/session_api.rs': '6797009fa6af9cf21614386fcef57ab4d87e037fba372023598382a77c166bbc', 'crates/trnm-server/src/runtime/grpc.rs': '9c505f70d22adfc40631f820c751d0ba4adb7a977fee59a4b029a98ee24f3d92', 'crates/trnm-server/src/runtime/auth_runtime.rs': '4363223b9be6f40f9794e3799a02a0a0e18c1211efc32c42c2945c4fc681260a', 'crates/trnm-server/src/runtime/auth_app_tests.rs': '743a9f87e62cb4cca90bf2170d622e0998f192f2c3394391a4c04e815aa6ddd8', 'crates/trnm-server/src/runtime/legacy_http_api.rs': 'f25620caf671f7faa425a53867caa705c39a3d52cf0f90ca5dcc9a3d8aa3da76'}, 'Legacy_transport_drain_uses_numeric_gateway_envelope_source': True}

def validate_selected_auth_app_source(sources: dict[Path, str], status: dict,
                                      contract: dict | None = None,
                                      vertical_status: dict | None = None) -> None:
    validate_reviewed_complete_production_files(SELECTED_AUTH_APP_FULL_SOURCE_SHA256, sources)
    if not same_typed_value(status.get("selected_auth_authority_source"), SELECTED_AUTH_APP_POLICY):
        fail("selected authority source status, identity or no-credit boundary drift")
    if contract is None:
        contract = json.loads((ROOT / "contracts/server/rust-server-vertical-slice.v1.json").read_text())
    if vertical_status is None:
        vertical_status = json.loads((ROOT / "docs/status/RUST_SERVER_VERTICAL_SLICE_STATUS.json").read_text())
    for label, document in (("contract", contract), ("vertical status", vertical_status)):
        if not same_typed_value(document.get("selected_auth_authority_source"), SELECTED_AUTH_APP_POLICY):
            fail(f"selected authority {label} binding drift")
    environment = sorted(set(re.findall(r'"(TRNM_SERVER_[A-Z0-9_]+)"',
        sources[Path("crates/trnm-server/src/runtime/config.rs")].split("\n#[cfg(test)]", 1)[0])))
    if not same_typed_value(contract.get("configuration", {}).get("environment"), environment):
        fail("canonical configuration environment differs from actual pure lookup source")
    if environment != SELECTED_AUTH_APP_POLICY["canonical_environment"]:
        fail("selected authority configuration lookup inventory drift")


def validate_http_healthcheck_source(sources: dict[Path, str], status: dict) -> None:
    """Check bounded source/fixture scope; this does not grant oracle acceptance."""
    path = "contracts/http/nakama-healthcheck-v1.json"
    contract = json.loads((ROOT / path).read_text())
    if contract.get("upstream", {}).get("commit") != "d4d92f93f78bbbe62c7fc50a3f85c772ec121a09":
        fail("HTTP healthcheck must retain the pinned Nakama baseline")
    candidate = contract.get("candidate", {})
    if any(candidate.get(key) != value for key, value in {
        "method": "GET", "path": "/healthcheck", "authentication": "public",
        "query": "ignored", "database_access": False, "readiness_dependency": False,
        "available_before_startup_admission": False, "app_health_during_drain": True,
    }.items()):
        fail("HTTP healthcheck source scope drift")
    if any(value is not False for value in contract.get("claims", {}).values()) or not contract.get("remaining_divergences"):
        fail("HTTP healthcheck must retain explicit unaccepted residuals")
    component = status.get("http_healthcheck_source_candidate", {})
    if component.get("contract") != path or any(component.get(key) is not False for key in (
        "accepted", "gap_closed", "compatibility_credit", "production_ready",
    )):
        fail("HTTP healthcheck component boundary drift")
    runtime = Path("crates/trnm-server/src/runtime")
    tests = contract.get("tests", [])
    source = sources[runtime / "app.rs"] + sources[runtime / "server.rs"]
    if len(tests) != 7 or len(set(tests)) != 7 or any(f"fn {name}()" not in source for name in tests):
        fail("HTTP healthcheck native fixture inventory drift")
    require_markers("HTTP healthcheck dispatcher", sources[runtime / "app.rs"], (
        '("GET", "/healthcheck")', "if healthcheck_path(target)", "Response::nakama_healthcheck",
    ))
    require_markers("HTTP healthcheck response", sources[runtime / "http.rs"], (
        'response.content_type = "application/json"', "no-store, no-cache, must-revalidate",
        "Vary: Accept-Encoding", "Grpc-Metadata-Content-Type: application/grpc", "if !self.suppress_body",
    ))


def validate_healthcheck_form_source(sources: dict[Path, str], status: dict) -> None:
    """Keep the form adapter scoped, byte-exact and explicitly unaccepted."""
    path = "contracts/http/nakama-healthcheck-form-v1.json"
    contract = json.loads((ROOT / path).read_text())
    expected_scope = {
        "target": "/healthcheck", "method": "POST",
        "content_type": "application/x-www-form-urlencoded", "media_type_exact_match": True,
        "body_error_before_query_error": True, "semicolon_overrides_escape_error_within_form": True,
        "override_after_form_parse": True, "authentication": "public", "database_access": False,
        "operator_or_storage_overrides_enabled": False, "ingress_limits_unchanged": True,
        "transport_drain_denial_unchanged": True,
    }
    if not same_typed_value(contract.get("scope"), expected_scope):
        fail("healthcheck form scope or ordering drift")
    if contract.get("upstream", {}).get("commit") != "d4d92f93f78bbbe62c7fc50a3f85c772ec121a09" or contract["upstream"].get("go_version") != "go1.26.5":
        fail("healthcheck form source or Go baseline drift")
    expected_claims = {key: False for key in ("accepted", "gap_closed", "compatibility_credit", "production_ready", "full_replacement")}
    if not same_typed_value(contract.get("claims"), expected_claims) or not contract.get("limitations"):
        fail("healthcheck form must retain unaccepted residuals")
    fixture = json.loads((ROOT / contract["fixtures"]).read_text())
    if len(fixture.get("fixtures", [])) != 48 or fixture.get("compatibility_credit") is not False or hashlib.sha256((ROOT / contract["fixtures"]).read_bytes()).hexdigest() != contract.get("fixtures_sha256"):
        fail("healthcheck form reference fixture drift")
    quote = contract.get("go_quote_corpus", {})
    if quote.get("cases") != 65536 or quote.get("bytes") != 1180960 or not re.fullmatch(r"[a-f0-9]{64}", quote.get("sha256", "")):
        fail("healthcheck form byte-quote denominator drift")
    if contract.get("nonascii_method_mappings") != [{"from": "ſ", "to": "S"}]:
        fail("healthcheck form simple method mapping drift")
    component = status.get("healthcheck_form_source_candidate", {})
    if component.get("contract") != path or component.get("reference_cases") != 48 or component.get("quote_cases") != 65536 or any(component.get(key) is not False for key in expected_claims):
        fail("healthcheck form component boundary drift")
    bindings = contract.get("source_sha256", {})
    if set(bindings) != {
        "crates/trnm-server/src/runtime/healthcheck_form.rs", "crates/trnm-server/src/runtime/app.rs",
        "crates/trnm-server/src/runtime/mod.rs", "crates/trnm-server/src/runtime/cors_transport_tests.rs",
    }:
        fail("healthcheck form complete-source binding drift")
    validate_reviewed_complete_production_files(bindings, sources)
    for source, expected in contract.get("reference_source_sha256", {}).items():
        if hashlib.sha256((ROOT / source).read_bytes()).hexdigest() != expected:
            fail("healthcheck form reference runner drift")
    tests = contract.get("native_tests", [])
    native = "\n".join(sources[Path(key)] for key in bindings)
    if len(tests) != 5 or len(set(tests)) != 5 or any(f"fn {name}()" not in native for name in tests):
        fail("healthcheck form native regression inventory drift")


def validate_client_routing_source(sources: dict[Path, str], status: dict) -> None:
    """Bound installed-route method diagnostics without granting acceptance."""
    path = "contracts/http/nakama-routing-v1.json"
    contract = json.loads((ROOT / path).read_text())
    if contract.get("upstream", {}).get("commit") != "d4d92f93f78bbbe62c7fc50a3f85c772ec121a09":
        fail("client routing must retain the pinned Nakama baseline")
    expected = {key: False for key in ("accepted", "gap_closed", "compatibility_credit", "production_ready", "full_replacement")}
    if not same_typed_value(contract.get("claims"), expected) or not contract.get("limitations"):
        fail("client routing must retain its unaccepted claim boundary")
    fixtures = contract.get("fixtures", [])
    if len(fixtures) != 27 or len({(row["method"], row["target"]) for row in fixtures}) != 27:
        fail("client routing fixture inventory drift")
    if any(row.get("status") != 501 or row.get("code") != 12 or row.get("message") != "Method Not Allowed" for row in fixtures):
        fail("client routing fixture status or error drift")
    component = status.get("client_routing_source_candidate", {})
    if component.get("contract") != path or component.get("reference_cases") != 27 or any(component.get(key) is not False for key in expected):
        fail("client routing component boundary drift")
    tests = contract.get("native_tests", [])
    runtime = Path("crates/trnm-server/src/runtime")
    native = sources[runtime / "app.rs"] + sources[runtime / "cors_transport_tests.rs"]
    if len(tests) != 3 or len(set(tests)) != 3 or any(f"fn {name}()" not in native for name in tests):
        fail("client routing native regression inventory drift")
    require_markers("client routing", sources[runtime / "app.rs"], (
        "if nakama_client_path(target)", "storage_list_api::is_list_target(target)",
        'request.method == "HEAD"',
    ))


def validate_client_cors_source(sources: dict[Path, str], status: dict) -> None:
    """Bind the limited source profile without treating it as accepted parity."""
    path = "contracts/http/nakama-cors-v1.json"
    contract = json.loads((ROOT / path).read_text())
    fixture = json.loads((ROOT / contract["fixtures"]).read_text())
    policy = contract.get("policy", {})
    if any(not same_typed_value(policy.get(key), value) for key, value in {
        "origins": ["*"], "credentials": False,
        "allowed_methods": ["GET", "HEAD", "POST", "PUT", "DELETE"],
        "max_request_headers_bytes": 32768,
        "ordinary_method_or_auth_dispatch_changed": False,
        "operator_routes_included": False, "unknown_routes_included": False,
        "request_parser_bypassed": False, "preflight_invokes_application_or_repository": False,
    }.items()):
        fail("client CORS must preserve bounded noncredentialed scope and auth isolation")
    claims = {key: False for key in ("accepted", "gap_closed", "compatibility_credit", "production_ready", "full_replacement")}
    if not same_typed_value(contract.get("claims"), claims) or not contract.get("limitations"):
        fail("client CORS must retain its unaccepted claim boundary")
    if fixture.get("upstream_commit") != "d4d92f93f78bbbe62c7fc50a3f85c772ec121a09" or len(fixture.get("fixtures", [])) != 22 or fixture.get("compatibility_credit") is not False:
        fail("client CORS original-source fixture inventory drift")
    component = status.get("client_cors_source_candidate", {})
    if component.get("contract") != path or any(component.get(key) is not False for key in ("credentials_enabled", "accepted", "gap_closed", "compatibility_credit", "production_ready")):
        fail("client CORS component boundary drift")
    bindings = contract.get("source_sha256", {})
    if set(bindings) != {"crates/trnm-server/src/runtime/cors.rs", "crates/trnm-server/src/runtime/cors_transport_tests.rs"}:
        fail("client CORS complete-source bindings differ")
    validate_reviewed_complete_production_files(bindings, sources)
    if hashlib.sha256((ROOT / contract["fixtures"]).read_bytes()).hexdigest() != contract.get("fixtures_sha256"):
        fail("client CORS fixture bytes differ")
    tests = contract.get("native_tests", [])
    native = "\n".join(sources[Path(key)] for key in bindings)
    if len(tests) != 7 or len(set(tests)) != 7 or any(f"fn {name}()" not in native for name in tests):
        fail("client CORS native regression inventory drift")


def expected_server_dependencies() -> dict[str, object]:
    path = Path(__file__).with_name("check-rust-foundation.py")
    spec = importlib.util.spec_from_file_location("trnm_server_direct_dependency_policy", path)
    if spec is None or spec.loader is None:
        fail("foundation server dependency policy loader is unavailable")
    foundation = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(foundation)
        value = foundation.EXPECTED_DEPENDENCIES["crates/trnm-server"]
    except (OSError, SyntaxError, AttributeError, KeyError, TypeError) as error:
        fail("foundation server dependency policy could not be loaded: " + type(error).__name__)
    if not isinstance(value, dict) or not value:
        fail("foundation server dependency policy must be a nonempty mapping")
    return deepcopy(value)


def validate_server_dependency_boundary(manifest: dict[str, object]) -> None:
    if manifest.get("dependencies") != expected_server_dependencies():
        fail("server candidate changed the reviewed direct dependency boundary")
    expected_build_dependencies = {
        "prost-build": "=0.14.3", "prost-types": "=0.14.3",
        "protoc-bin-vendored": "=3.2.0", "tonic-build": "=0.14.5",
        "tonic-prost-build": "=0.14.5",
    }
    if manifest.get("build-dependencies") != expected_build_dependencies:
        fail("server candidate changed the reviewed server protobuf build boundary")


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
    validate_accounts_schema_source()
    missing = sorted(str(path.relative_to(ROOT)) for path in REQUIRED_FILES if not path.is_file())
    if missing:
        fail("missing files: " + ", ".join(missing))

    manifest = tomllib.loads(
        (ROOT / "crates/trnm-persistence-pg/Cargo.toml").read_text(encoding="utf-8")
    )
    validate_dependency_boundary(manifest)
    validate_server_dependency_boundary(tomllib.loads(
        (ROOT / "crates/trnm-server/Cargo.toml").read_text(encoding="utf-8")
    ))

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
    validate_legacy_auth_source(sources, status, json.loads(
        (ROOT / "contracts/session/nakama-v340-token-source-lock.json").read_text()
    ))
    validate_custom_auth_source(sources, status)
    validate_http_healthcheck_source(sources, status)
    validate_client_cors_source(sources, status)
    validate_client_routing_source(sources, status)
    validate_healthcheck_form_source(sources, status)
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
    validate_homogeneous_storage_contract(
        storage_contract,
        json.loads((ROOT / "contracts/storage/nakama-sort-source-lock-v1.json").read_text()),
        (ROOT / "third_party/go-sort/LICENSE").read_bytes(), (ROOT / "NOTICE").read_text(),
        sources[Path("crates/trnm-storage-core/src/nakama_sort.rs")],
    )
    validate_homogeneous_storage_source(
        sources[Path("crates/trnm-storage-core/src/lib.rs")],
        sources[Path("crates/trnm-persistence-pg/src/storage_parts/01_repository.rs")],
        sources[Path("crates/trnm-server/src/runtime/storage_api.rs")],
        sources[Path("crates/trnm-server/src/runtime/pool.rs")],
        sources[Path("crates/trnm-server/src/runtime/retry.rs")],
    )
    validate_nakama_write_tail_source(
        sources[Path("crates/trnm-persistence-pg/src/storage_parts/01_repository.rs")],
        (ROOT / "crates/trnm-persistence-pg/src/pool_parts/base.rs").read_text(encoding="utf-8"),
        sources[Path("crates/trnm-persistence-pg/tests/storage_duplicate_batches.rs")],
    )
    validate_nakama_native_write_source(
        sources[Path("crates/trnm-persistence-pg/src/storage_parts/01_repository.rs")],
        sources[Path("crates/trnm-persistence-pg/src/storage_parts/03_write.rs")],
        sources[Path("crates/trnm-persistence-pg/src/storage_parts/06_native_projection.rs")],
        sources[Path("crates/trnm-server/src/runtime/storage_api.rs")],
        sources[Path("crates/trnm-persistence-pg/tests/storage_duplicate_batches.rs")],
        sources[Path("crates/trnm-server/src/runtime/storage_api_v3_live.rs")],
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
    homogeneous_status = status.get("storage_homogeneous_mutation_source_candidate", {})
    if not isinstance(homogeneous_status, dict) or any(
        homogeneous_status.get(field) != value or type(homogeneous_status.get(field)) is not type(value)
        for field, value in STORAGE_HOMOGENEOUS_POLICY.items()
    ) or homogeneous_status.get("gap_closed") is not False:
        fail("homogeneous storage status must retain exact source scope and false acceptance")
    validate_late_exact_policy_counters(homogeneous_status)
    validate_insert_only_policy_counters(homogeneous_status)
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



CUSTOM_AUTH_SOURCE_POLICY = {'status': 'source-candidate-custom-autocommit-accounts5-gated', 'route': 'POST /v2/account/authenticate/custom', 'default_runtime_schema_version': 4, 'default_storage_writer_epoch': 4, 'AccountsV5_gate': False, 'one_selected_service_cache_issuer': True, 'one_native_lease': True, 'source_single_autocommit': True, 'Device_transaction_retry_used': False, 'identity_race_fallback': False, 'username_unique_code': 6, 'custom_unique_code': 13, 'lookup_error_message': 'Error finding user account.', 'create_error_message': 'Error finding or creating user account.', 'retired_lease_prewrite_check': True, 'cancellation_dispatch_race_closed': False, 'late_confirmed_allows_token': False, 'unknown_completion_proves_no_effect': False, 'caller_replay': False, 'caller_compensation': False, 'Email_implemented': False, 'source_sha256': {'crates/trnm-persistence-pg/src/lib.rs': 'd604caf57010b71d6d3cebb58455f6a616519412dca2997807bfbc9bea851e96', 'crates/trnm-persistence-pg/src/nakama_account.rs': '7e045e8b45e3aa35009be6b47f04e4962ba10c4f8ef4b85f7a5c82337e48b430', 'crates/trnm-persistence-pg/src/nakama_account/native.rs': '9d1f794d3960963e457f936dac1d03bdbab5428a4fe892e68d4e5d4ce5016cf3', 'crates/trnm-persistence-pg/src/nakama_account/tests.rs': '1277429231aa78c0418a2e793c1edc11dae0a18c0257d08ce9f2132c29a2744a', 'crates/trnm-persistence-pg/src/pool_parts/base.rs': '1bc565243740e4b639cd69ea49f4ee8974299b2cc101bd6d3054eeae7783ec6c', 'crates/trnm-server/src/runtime/app.rs': '959b971dad77929b911526864c4a980ad10a48bca453fb8bb3c1af9d4d6260a1', 'crates/trnm-server/src/runtime/auth_app_tests.rs': '743a9f87e62cb4cca90bf2170d622e0998f192f2c3394391a4c04e815aa6ddd8', 'crates/trnm-server/src/runtime/auth_runtime.rs': '4363223b9be6f40f9794e3799a02a0a0e18c1211efc32c42c2945c4fc681260a', 'crates/trnm-server/src/runtime/legacy_auth.rs': '20830ff85bbb0d608a525cd2a99dcb1645188a1af57dd9a695f60f24e2596fb1', 'crates/trnm-server/src/runtime/legacy_auth_tests.rs': 'cb091a288f1f7613d93980de3be2cb90ae44ce7e0d100260717cfb83fc9ad558', 'crates/trnm-server/src/runtime/legacy_device_predicates.rs': 'a236efab95250544085c1a1f8cbb137e9da0e4313d639ef1f8a15ba6036d30d3', 'crates/trnm-server/src/runtime/legacy_http_api.rs': 'f25620caf671f7faa425a53867caa705c39a3d52cf0f90ca5dcc9a3d8aa3da76', 'crates/trnm-server/src/runtime/legacy_http_api_tests.rs': '35a4e0f5af2f4b58929246a4da64d979ba74ec3b70c772e7ee12f9b4a951d356', 'crates/trnm-server/src/runtime/legacy_repository.rs': 'df8ac88d58aa19b57cf28fe341669a49355d1c348be279c81cd70d2d3995e141', 'crates/trnm-server/src/runtime/legacy_repository_tests.rs': '80190ebec55afe9d91b8f4f0f246ee6c4d653c71c529aa34b9709c98905b1c81', 'crates/trnm-server/src/runtime/pool.rs': 'c7058fe9c77228bc2551fd6c02c2f2bd5132f9706d45fbdf40d5b3969be67ba0'}, 'upstream': {'commit': 'd4d92f93f78bbbe62c7fc50a3f85c772ec121a09', 'api_authenticate_sha256': '89c0659aa6994c353e9d9dc32ccc412cbdb5dfc04537a1c93fa026dd32a06995', 'core_authenticate_sha256': '1995cf76a7e2f35185e5a9eef161da2cb5851be7a8bdeddb2609c1a0b7b4dc96', 'gateway_sha256': '590d0d37be149e158fe514e64ecef06955e5baa5b2b64c7cb4d5bb2b0e1c670d'}, 'claims': {'native_qualified': False, 'HTTP_qualified': False, 'paired_oracle': False, 'accepted': False, 'production_ready': False, 'compatibility_credit': False, 'full_replacement': False}}

def validate_custom_auth_source(sources: dict[Path,str], status: dict) -> None:
    validate_reviewed_complete_production_files(CUSTOM_AUTH_SOURCE_POLICY['source_sha256'], sources)
    for name in ('docs/status/TRNM_SERVER_STATUS.json','docs/status/RUST_SERVER_VERTICAL_SLICE_STATUS.json','docs/status/CURRENT_STATE.json','contracts/server/rust-server-vertical-slice.v1.json'):
        document=json.loads((ROOT/name).read_bytes())
        if not same_typed_value(document.get('nakama_legacy_custom_auth_source_candidate'),CUSTOM_AUTH_SOURCE_POLICY):
            fail('Custom source byte/status/no-credit boundary drift: '+name)

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
