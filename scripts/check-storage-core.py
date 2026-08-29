#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_TESTS = {
    "owner_write_and_read_respects_occ",
    "stale_version_rejects_without_mutation",
    "multi_operation_batch_rolls_back_on_any_failure",
    "duplicate_key_in_batch_is_rejected",
    "server_owned_object_cannot_be_mutated_by_user",
    "delete_requires_exact_version_when_supplied",
}
FORBIDDEN = ("unsafe {", "std::net", "std::time", "tokio", "sqlx", "postgres", "rand::")


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
    names = set(re.findall(r"fn\s+([a-z0-9_]+)\s*\(\)\s*\{", source))
    missing = sorted(REQUIRED_TESTS - names)
    if missing:
        fail(f"missing Rust tests: {missing}")
    if source.count("#[test]") < 9:
        fail("expected at least 9 Rust storage tests")
    vectors = json.loads((ROOT / "contracts/storage/storage-vectors.json").read_text())
    if any(vectors["claims"].values()) or len(vectors["cases"]) < 4:
        fail("storage vectors overclaim or are empty")
    status = json.loads((ROOT / "docs/status/STORAGE_CORE_STATUS.json").read_text())
    if any(status["claims"].values()):
        fail("storage status overclaims")
    print(json.dumps({"status":"storage-core-static-contract-passed","rust_tests":source.count("#[test]"),"vector_cases":len(vectors["cases"]),"cargo_executed_locally":False,"compatibility_credit":False},sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
