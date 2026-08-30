#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_TESTS = {
    "content_version_matches_pinned_nakama_md5_hex",
    "content_version_parser_is_strict_lowercase_hex",
    "owner_write_and_read_respects_occ",
    "stale_version_rejects_without_mutation",
    "multi_operation_batch_rolls_back_on_any_failure",
    "duplicate_key_in_batch_is_rejected",
    "server_owned_object_cannot_be_mutated_by_user",
    "delete_requires_exact_version_when_supplied",
    "identical_version_cannot_name_different_value",
}
FORBIDDEN = ("unsafe {", "std::net", "std::time", "tokio", "sqlx", "postgres", "rand::")
FALSE_CLAIMS = ("storage_behavior_compatible", "database_durable", "production_ready")


def fail(message: str) -> None:
    raise SystemExit(f"storage core contract failed: {message}")


def main() -> int:
    workspace = tomllib.loads((ROOT / "Cargo.toml").read_text())["workspace"]
    if "crates/trnm-storage-core" not in workspace["members"]:
        fail("storage crate missing from workspace")
    source = (ROOT / "crates/trnm-storage-core/src/lib.rs").read_text()
    if "#![forbid(unsafe_code)]" not in source:
        fail("unsafe code is not forbidden")
    for marker in FORBIDDEN:
        if marker in source:
            fail(f"forbidden pure storage capability {marker}")
    for marker in (
        "pub struct ContentVersion([u8; 32]);",
        "pub struct IntegrityDigest(Digest32);",
        "ContentVersion::from_value(&operation.value)",
        "lowercase hexadecimal MD5",
    ):
        if marker not in source:
            fail(f"missing version contract marker: {marker}")
    names = set(re.findall(r"fn\s+([a-z0-9_]+)\s*\(\)\s*\{", source))
    missing = sorted(REQUIRED_TESTS - names)
    if missing:
        fail(f"missing Rust tests: {missing}")
    if source.count("#[test]") < 11:
        fail("expected at least 11 Rust storage tests")

    vectors = json.loads((ROOT / "contracts/storage/storage-vectors.json").read_text())
    if vectors.get("schema") != "trillionnium.storage-core-vectors.v2":
        fail("storage vector schema is not v2")
    if len(vectors.get("cases", [])) < 5:
        fail("storage vectors are incomplete")
    baseline = vectors.get("baseline", {})
    if baseline.get("commit") != "d4d92f93f78bbbe62c7fc50a3f85c772ec121a09":
        fail("storage vectors are not pinned to the Nakama baseline")
    if baseline.get("public_version_rule") != "lowercase hexadecimal MD5 of the exact value bytes":
        fail("public version rule mismatch")
    claims = vectors.get("claims", {})
    if claims.get("public_version_source_candidate") is not True:
        fail("public version source candidate is not recorded")
    for field in FALSE_CLAIMS:
        if claims.get(field) is not False:
            fail(f"storage vector claim {field} must remain false")

    status = json.loads((ROOT / "docs/status/STORAGE_CORE_STATUS.json").read_text())
    for field in FALSE_CLAIMS:
        if status.get("claims", {}).get(field) is not False:
            fail(f"storage status overclaims {field}")
    print(
        json.dumps(
            {
                "status": "storage-core-static-contract-passed",
                "rust_tests": source.count("#[test]"),
                "vector_cases": len(vectors["cases"]),
                "public_version_source_candidate": True,
                "cargo_executed_locally": False,
                "compatibility_credit": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
