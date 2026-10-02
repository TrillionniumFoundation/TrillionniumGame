#!/usr/bin/env python3
from __future__ import annotations

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
SOURCE_FILES = (
    "crates/trnm-storage-core/src/lib.rs",
    "crates/trnm-storage-core/src/projection.rs",
    "crates/trnm-storage-core/src/projection_tests.rs",
)
AUXILIARY_SOURCE_FILES = ("crates/trnm-storage-core/src/bin/trnm-storage-version.rs",)
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
    source, projection, tests = (sources[path] for path in SOURCE_FILES)
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
        "rust_tests": len(names) + len(projection_names),
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
