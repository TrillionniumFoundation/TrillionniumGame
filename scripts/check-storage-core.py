#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_TESTS = {
    "nakama_row_identifier_projection_keeps_unicode_schema_bounds_separate",
    "content_version_matches_pinned_nakama_md5_hex",
    "integrity_digest_is_canonical_sha256_over_exact_value_bytes",
    "content_version_parser_is_strict_lowercase_hex",
    "owner_write_and_read_respects_occ",
    "public_and_private_read_permissions_are_distinct",
    "stale_version_rejects_without_mutation",
    "multi_operation_batch_rolls_back_on_any_failure",
    "duplicate_key_in_batch_is_rejected",
    "server_owned_object_cannot_be_mutated_by_user",
    "delete_requires_exact_version_when_supplied",
    "identical_version_cannot_name_different_value",
    "must_not_exist_rejects_existing_object",
    "create_only_occ_precedence_preserves_owner_authority_and_batch_state",
    "expected_version_preserves_strings_without_stored_version_constraints",
    "opaque_write_tokens_are_exact_and_keep_permission_precedence",
    "opaque_delete_tokens_are_literal_and_keep_permission_precedence",
    "opaque_conditions_do_not_override_owner_binding",
    "opaque_condition_rejections_roll_back_prior_writes_and_deletes",
}
PROJECTION_TESTS = {
    "public_version_preserves_opaque_history_and_unicode_character_bounds",
    "known_collision_witness_binds_request_version_and_projection_without_reconstruction",
    "request_and_projection_budgets_are_independent_and_legal_overflow_is_resource_exhausted",
    "supplied_projection_seam_is_executed_and_raw_model_stays_explicit_identity",
    "blind_known_no_op_preserves_projection_and_witness_but_exact_and_acl_changes_project",
    "unknown_matching_token_no_op_preserves_native_value_and_never_fabricates_witness",
    "opaque_and_empty_previous_tokens_remain_present_in_write_and_delete_receipts",
    "known_md5_collision_uses_request_fingerprint_and_unknown_history_keeps_upstream_no_op",
    "projected_batch_failures_roll_back_and_full_validation_precedes_projection",
    "projection_integrity_and_witness_corruption_fail_before_no_op_or_delete",
    "projection_seam_keeps_permission_and_create_only_occ_precedence",
}
STORED_DOMAIN_TESTS = {'stored_permission_wrappers_preserve_full_nonnegative_smallint_domain', 'blind_raw_acl_no_op_and_acl_change_preserve_or_replace_unknown_witness', 'stored_acl_predicates_keep_read_list_write_and_delete_distinct', 'historical_acl_batch_failure_preserves_values_versions_witnesses_and_all_rows', 'historical_raw_acl_write_delete_and_occ_precedence_are_distinct', 'historical_raw_acl_read_visibility_uses_authenticated_owner_and_exact_read_values', 'stored_nakama_identifiers_preserve_empty_unicode_control_and_owner_cells'}
NAKAMA_SORT_TESTS = {
    "source_predicted_thirteen_operations_change_last_duplicate",
    "insertion_boundary_keeps_occurrences_and_original_ordinals",
    "oversized_input_rejects_before_comparison_or_permutation",
    "binary_thirteen_permutations_preserve_occurrences_and_key_order",
}
NAKAMA_BATCH_TESTS = {
    "nakama_two_client_writes_keep_occurrence_receipts_and_last_step_state",
    "nakama_three_server_writes_bypass_step_acl_without_collapsing_acks",
    "nakama_exact_condition_observes_the_preceding_same_key_insert",
    "nakama_step_acl_occ_and_insert_only_failures_restore_the_whole_model",
    "nakama_duplicate_client_delete_rolls_back_and_server_missing_is_explicit_noop",
    "nakama_sorted_candidate_preserves_input_ack_ordinals_and_projected_witnesses",
    "nakama_policy_is_homogeneous_and_internal_mixed_policy_remains_distinct",
    "thirteen_occurrences_follow_observed_go_order_without_reordering_acks",
}
SOURCE_FILES = (
    "crates/trnm-storage-core/src/lib.rs",
    "crates/trnm-storage-core/src/projection.rs",
    "crates/trnm-storage-core/src/projection_tests.rs",
    "crates/trnm-storage-core/src/stored_domain.rs",
    "crates/trnm-storage-core/src/stored_domain_tests.rs",
    "crates/trnm-storage-core/src/nakama_sort.rs",
    "crates/trnm-storage-core/src/nakama_batch_tests.rs",
)
AUXILIARY_SOURCE_FILES = ("crates/trnm-storage-core/src/bin/trnm-storage-version.rs",)
SORT_LOCK_PATH = "contracts/storage/nakama-sort-source-lock-v1.json"
SORT_LICENSE_PATH = "third_party/go-sort/LICENSE"
REQUIRED_VECTOR_CASES = {
    "owner-create", "stale-batch-rollback", "delete-exact", "server-owned-denied",
    "create-only-existing-disabled-write-acl", "exact-stale-disabled-write-acl-permission-first",
    "last-write-wins", "opaque-exact-uppercase-rollback", "opaque-exact-nonhex-rollback",
    "opaque-exact-unicode-rollback", "opaque-exact-suffix-rollback", "opaque-exact-whitespace-rollback",
    "opaque-exact-long-rollback", "delete-star-is-literal-exact", "opaque-exact-disabled-acl-permission-first",
}
FORBIDDEN = ("unsafe {", "std::net", "std::time", "tokio", "sqlx", "postgres", "rand::")
FALSE_CLAIMS = ("storage_behavior_compatible", "database_durable", "production_ready")


class ValidationError(RuntimeError):
    """The pure storage source inventory or claim boundary has drifted."""


def fail(message: str) -> None:
    raise ValidationError(message)


def validate(root: Path = ROOT) -> dict:
    workspace = tomllib.loads((root / "Cargo.toml").read_text())["workspace"]
    if "crates/trnm-storage-core" not in workspace["members"]:
        fail("storage crate missing from workspace")
    inventory = sorted(path.relative_to(root).as_posix() for path in
                       (root / "crates/trnm-storage-core/src").rglob("*.rs"))
    if inventory != sorted(SOURCE_FILES + AUXILIARY_SOURCE_FILES):
        fail("pure storage source inventory drifted")
    sources = {path: (root / path).read_text(encoding="utf-8") for path in SOURCE_FILES}
    source = sources["crates/trnm-storage-core/src/lib.rs"]
    projection = sources["crates/trnm-storage-core/src/projection.rs"]
    tests = sources["crates/trnm-storage-core/src/projection_tests.rs"]
    combined = "\n".join(sources.values())
    for path in AUXILIARY_SOURCE_FILES:
        auxiliary_source = (root / path).read_text(encoding="utf-8")
        if "#![forbid(unsafe_code)]" not in auxiliary_source:
            fail("auxiliary storage binary does not forbid unsafe code")
        combined += "\n" + auxiliary_source
    if "#![forbid(unsafe_code)]" not in source:
        fail("unsafe code is not forbidden")
    for marker in FORBIDDEN:
        if marker in combined:
            fail(f"forbidden pure storage capability {marker}")
    for marker in (
        "pub struct ContentVersion([u8; 32]);",
        "pub struct ExpectedVersion(String);",
        "pub struct IntegrityDigest(Digest32);",
        "mod projection;",
        "#[cfg(test)]\nmod projection_tests;",
        "CollisionWitness, PublicVersion, MAX_PROJECTION_VALUE_BYTES, MAX_REQUEST_VALUE_BYTES,",
        "pub version: PublicVersion,",
        "pub collision_witness: Option<CollisionWitness>,",
        "pub previous_version: Option<PublicVersion>,",
        "pub current_version: Option<ContentVersion>,",
        "pub fn apply_batch_projected<F>(",
        "F: FnMut(&[u8]) -> Result<Vec<u8>, DomainError>",
        "self.apply_batch_projected(actor, operations, |request| Ok(request.to_vec()))",
        "let value = projector(&operation.value)?;",
        "CollisionWitness::from_request(&operation.value, &value)?",
        "integrity_digest: collision_witness.projection_digest()",
        "!witness.matches_request(&operation.value)",
        "witness.validate_projection(&object.version, &object.value)?;",
        "ContentVersion::from_value(&operation.value)",
        "lowercase hexadecimal MD5",
    ):
        if marker not in source:
            fail(f"missing version contract marker: {marker}")
    for marker in (
        "pub struct PublicVersion(String);",
        "value.chars().take(33).count() > 32",
        "pub const MAX_REQUEST_VALUE_BYTES: usize = 1024 * 1024;",
        "pub const MAX_PROJECTION_VALUE_BYTES: usize = 16 * 1024 * 1024;",
        "pub struct CollisionWitness {",
        "request_digest: IntegrityDigest,",
        "request_len: usize,",
        "request_version: ContentVersion,",
        "projection_digest: IntegrityDigest,",
        "pub fn from_request(request: &[u8], projected_value: &[u8])",
        "request_digest: IntegrityDigest::from_value(request)",
        "request_version: ContentVersion::from_value(request)",
        "projection_digest: IntegrityDigest::from_value(projected_value)",
        "version.as_str() != self.request_version.as_str()",
        "!self.projection_digest.matches_value(projected_value)",
        "StableCode::ResourceExhausted",
        '"storage_projection_value_budget_exceeded"',
    ):
        if marker not in projection:
            fail(f"missing projection contract marker: {marker}")
    witness_fields = projection.split("pub struct CollisionWitness {", 1)[1].split("}", 1)[0]
    if re.search(r"\bpub(?:\s|\()", witness_fields):
        fail("collision witness fields must remain private")
    if "ContentVersion::from_value(&value)" in source:
        fail("native projection must not reconstruct its public version")
    names = set(re.findall(r"#\[test\]\s*fn\s+([a-z0-9_]+)\s*\(\)\s*\{", source))
    missing = sorted(REQUIRED_TESTS - names)
    if missing:
        fail(f"missing Rust tests: {missing}")
    projection_names = set(re.findall(r"#\[test\]\s*fn\s+([a-z0-9_]+)\s*\(\)\s*\{", tests))
    if PROJECTION_TESTS - projection_names:
        fail(f"missing Rust projection tests: {sorted(PROJECTION_TESTS - projection_names)}")

    stored_tests = sources["crates/trnm-storage-core/src/stored_domain_tests.rs"]
    stored_names = set(re.findall(r"#\[test\]\s*fn\s+([a-z0-9_]+)\s*\(\)\s*\{", stored_tests))
    if stored_names != STORED_DOMAIN_TESTS:
        fail("stored Nakama domains and operation-specific ACL regressions drifted")
    if "mod stored_domain;" not in source or "mod stored_domain_tests;" not in source:
        fail("stored Nakama domain implementation or tests are unwired")

    sort_source = sources["crates/trnm-storage-core/src/nakama_sort.rs"]
    batch_tests = sources["crates/trnm-storage-core/src/nakama_batch_tests.rs"]
    sort_names = set(re.findall(r"#\[test\]\s*fn\s+([a-z0-9_]+)\s*\(\)\s*\{", sort_source))
    batch_names = set(re.findall(r"#\[test\]\s*fn\s+([a-z0-9_]+)\s*\(\)\s*\{", batch_tests))
    if sort_names != NAKAMA_SORT_TESTS or batch_names != NAKAMA_BATCH_TESTS:
        fail("bounded Nakama homogeneous batch regression inventory drifted")
    for marker in (
        "mod nakama_sort;", "#[cfg(test)]\nmod nakama_batch_tests;",
        "pub enum NakamaBatchKind {", "pub fn plan_nakama_batch(",
        "operations.is_empty() || operations.len() > MAX_BATCH_OPERATIONS",
        '"mixed_nakama_storage_batch"',
        "let mut order: Vec<_> = (0..operations.len()).collect();",
        "nakama_sort::go1265_sort_ordinals(&mut order, |left, right| {",
        "operations[left].key() < operations[right].key()",
        "let order = plan_nakama_batch(operations, kind)?;",
        "let mut receipts = vec![None; operations.len()];",
        "for ordinal in order {", "receipts[ordinal] = Some(receipt);",
    ):
        if marker not in source:
            fail(f"missing homogeneous batch contract marker: {marker}")
    bound = "if data.len() > 100 {\n        return Err(SortBoundError::TooManyOperations);\n    }"
    if bound not in sort_source or sort_source.index(bound) > sort_source.index("let n = data.len();"):
        fail("Go ordinal sort bound must reject before permutation or comparison")
    lock = json.loads((root / SORT_LOCK_PATH).read_text())
    if lock.get("source_commit") != "c19862e5f8415b4f24b189d065ed739517c548ba" or lock.get("modified_rust_path") != SOURCE_FILES[5] or lock.get("license_path") != SORT_LICENSE_PATH:
        fail("bounded Go ordinal sorter source identity drifted")
    for field in ("runtime_owner_representation_parity", "nakama_native_differential_accepted"):
        if lock.get(field) is not False:
            fail(f"Go sorter source lock overclaims {field}")
    license_hash = "911f8f5782931320f5b8d1160a76365b83aea6447ee6c04fa6d5591467db9dad"
    if hashlib.sha256((root / SORT_LICENSE_PATH).read_bytes()).hexdigest() != license_hash:
        fail("modified Go sorter BSD license bytes drifted")

    vectors = json.loads((root / "contracts/storage/storage-vectors.json").read_text())
    if vectors.get("schema") != "trillionnium.storage-core-vectors.v2":
        fail("storage vector schema is not v2")
    case_ids = [case.get("id") for case in vectors.get("cases", [])]
    if len(case_ids) != len(set(case_ids)) or REQUIRED_VECTOR_CASES - set(case_ids):
        fail("storage vectors are incomplete or duplicate")
    baseline = vectors.get("baseline", {})
    if baseline.get("commit") != "d4d92f93f78bbbe62c7fc50a3f85c772ec121a09":
        fail("storage vectors are not pinned to the Nakama baseline")
    if baseline.get("public_version_rule") != "lowercase hexadecimal MD5 of the exact write-request value bytes":
        fail("public version rule mismatch")
    projection_contract = vectors.get("projection_source_candidate", {})
    if projection_contract.get("source_files") != list(SOURCE_FILES):
        fail("projection source files are not fully inventoried")
    projection_test_inventory = projection_contract.get("test_functions", [])
    if len(projection_test_inventory) != len(PROJECTION_TESTS) or set(projection_test_inventory) != PROJECTION_TESTS:
        fail("projection tests are not fully inventoried")
    if projection_contract.get("request_byte_limit") != 1024 * 1024 or projection_contract.get("projection_byte_limit") != 16 * 1024 * 1024:
        fail("projection and request budgets drifted")
    if projection_contract.get("native_renderer_implemented") is not False:
        fail("pure storage model must not claim a native JSONB renderer")
    for field in ("accepted", "compatibility_credit", "database_durable"):
        if projection_contract.get(field) is not False:
            fail(f"projection source claim {field} must remain false")
    batch_contract = vectors.get("nakama_homogeneous_batch_source_candidate", {})
    if batch_contract.get("source_files") != list(SOURCE_FILES[5:]) or batch_contract.get("sort_source_lock") != SORT_LOCK_PATH or batch_contract.get("license") != SORT_LICENSE_PATH:
        fail("homogeneous batch source identities are not inventoried")
    for field, expected in (("sort_test_functions", NAKAMA_SORT_TESTS), ("batch_test_functions", NAKAMA_BATCH_TESTS)):
        observed = batch_contract.get(field, [])
        if len(observed) != len(expected) or set(observed) != expected:
            fail("homogeneous batch test functions are not fully inventoried")
    if batch_contract.get("max_operations") != 100 or batch_contract.get("owner_order") != "canonical-user-id" or batch_contract.get("receipt_order") != "original-input-ordinal" or batch_contract.get("typed_mixed_duplicate_policy") != "reject":
        fail("homogeneous batch source scope drifted")
    for field in ("accepted", "compatibility_credit", "database_durable", "native_lock_schedule_compatible", "runtime_owner_representation_parity", "hooks_and_index_compatible", "full_nakama_replacement"):
        if batch_contract.get(field) is not False:
            fail(f"homogeneous batch source overclaims {field}")
    claims = vectors.get("claims", {})
    if claims.get("public_version_source_candidate") is not True:
        fail("public version source candidate is not recorded")
    for field in FALSE_CLAIMS:
        if claims.get(field) is not False:
            fail(f"storage vector claim {field} must remain false")

    status = json.loads((root / "docs/status/STORAGE_CORE_STATUS.json").read_text())
    for field in FALSE_CLAIMS:
        if status.get("claims", {}).get(field) is not False:
            fail(f"storage status overclaims {field}")
    if status.get("nakama_homogeneous_batch_source_candidate") != batch_contract:
        fail("storage status homogeneous batch source contract drifted")
    inventory = json.loads((root / "docs/status/IMPLEMENTATION_INVENTORY.json").read_text())
    components = [row for row in inventory.get("components", []) if row.get("id") == "COMP-STORAGE"]
    if len(components) != 1:
        fail("storage component inventory identity is missing or duplicate")
    component = components[0]
    if component.get("source_files") != list(SOURCE_FILES) or component.get("auxiliary_source_files") != list(AUXILIARY_SOURCE_FILES):
        fail("storage component source inventory is incomplete")
    if component.get("claim_credit") is not False:
        fail("storage component must not grant claim credit")
    if component.get("parity_ids") != ["TG-PAR-021", "TG-PAR-022", "TG-PAR-023", "TG-PAR-024", "TG-PAR-025"]:
        fail("storage component parity denominator drifted")
    candidates = json.loads((root / "docs/status/SOURCE_CANDIDATES.json").read_text())
    projection_candidates = [row for row in candidates.get("candidates", []) if row.get("id") == "SRC-STORAGE-PROJECTION-CORE-V3"]
    if len(projection_candidates) != 1:
        fail("storage projection candidate identity is missing or duplicate")
    candidate = projection_candidates[0]
    if candidate.get("status") != "source-candidate" or not set(SOURCE_FILES).issubset(candidate.get("paths", [])):
        fail("storage projection candidate source inventory is incomplete")
    for field in ("accepted", "gap_closed", "compatibility_credit"):
        if candidate.get(field) is not False:
            fail(f"storage projection candidate overclaims {field}")
    return {
        "status": "storage-core-static-contract-passed",
        "rust_tests": len(names) + len(projection_names) + len(stored_names) + len(sort_names) + len(batch_names),
        "nakama_sort_tests": len(sort_names),
        "nakama_batch_tests": len(batch_names),
        "stored_domain_tests": len(stored_names),
        "source_files": list(SOURCE_FILES),
        "auxiliary_source_files": list(AUXILIARY_SOURCE_FILES),
        "projection_tests": len(projection_names),
        "vector_cases": len(vectors["cases"]),
        "public_version_source_candidate": True,
        "cargo_executed_locally": False,
        "compatibility_credit": False,
    }


def main() -> int:
    try:
        result = validate()
    except (OSError, ValueError, KeyError, ValidationError) as error:
        print(f"storage core contract failed: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            result,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
