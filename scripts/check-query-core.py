#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "nakama_commit": "d4d92f93f78bbbe62c7fc50a3f85c772ec121a09",
    "nakama_tree": "f3c9cfc2726d5543da1564629170f35b98e3797d",
    "nakama_wrapper_blob": "6b17452a50e323c0c4e06d130639bd1119bf0491",
    "nakama_go_mod_blob": "05584b9e0e80787e424428701281207b7cd3d881",
    "query_commit": "e2a05d85a1f2d8e34ce9b9863ce1f867c7d00288",
    "query_tree": "2de5178bf27b6418eafd15cac74c910868f14b34",
}
REQUIRED_TESTS = {
    "empty_and_star_match_nakama_wrapper_special_cases",
    "numeric_and_date_ranges_preserve_inclusive_boundaries",
    "wildcard_regexp_fuzzy_and_boost_are_distinct",
    "invalid_quotes_dates_suffixes_and_field_gaps_fail_closed",
    "query_token_and_clause_limits_are_enforced",
}
FORBIDDEN_CAPABILITIES = (
    "unsafe {",
    "std::net",
    "std::time",
    "tokio",
    "sqlx",
    "postgres",
)


def fail(message: str) -> None:
    raise SystemExit(f"query core contract failed: {message}")


def main() -> int:
    cargo = tomllib.loads((ROOT / "crates/trnm-query-core/Cargo.toml").read_text())
    dependencies = cargo.get("dependencies", {})
    if set(dependencies) != {"trnm-contracts"}:
        fail(f"unexpected query dependencies: {sorted(dependencies)}")

    source_paths = sorted((ROOT / "crates/trnm-query-core/src").glob("*.rs"))
    if [path.name for path in source_paths] != ["lexer.rs", "lib.rs", "parser.rs", "tests.rs"]:
        fail("unexpected query source layout")
    combined = "\n".join(path.read_text() for path in source_paths)
    if combined.count("#[test]") < 15:
        fail("expected at least 15 Rust query tests")
    names = set(re.findall(r"fn\s+([a-z0-9_]+)\s*\(\)\s*\{", combined))
    missing = sorted(REQUIRED_TESTS - names)
    if missing:
        fail(f"required query tests missing: {missing}")
    for marker in FORBIDDEN_CAPABILITIES:
        if marker in combined:
            fail(f"forbidden query-core capability: {marker}")

    lock = json.loads((ROOT / "contracts/query/upstream-query-lock.json").read_text())
    observed = {
        "nakama_commit": lock["nakama"]["commit"],
        "nakama_tree": lock["nakama"]["tree"],
        "nakama_wrapper_blob": lock["nakama"]["wrapper"]["blob"],
        "nakama_go_mod_blob": lock["nakama"]["dependency_manifest"]["blob"],
        "query_commit": lock["query_string"]["commit"],
        "query_tree": lock["query_string"]["tree"],
    }
    if observed != EXPECTED:
        fail(f"upstream query identity mismatch: {observed}")
    if any(lock["claims"].values()):
        fail("upstream query lock overclaims")

    vectors = json.loads(
        (ROOT / "contracts/query/query-compatibility-vectors.json").read_text()
    )
    if len(vectors["accepted"]) < 16 or len(vectors["rejected"]) < 9:
        fail("query vector corpus is incomplete")
    if any(vectors["claims"].values()):
        fail("query vectors overclaim")

    status = json.loads((ROOT / "docs/status/QUERY_CORE_STATUS.json").read_text())
    if any(status["claims"].values()):
        fail("query status overclaims")
    notice = (ROOT / "NOTICE").read_text()
    if "blugelabs/query_string" not in notice or "v0.3.0" not in notice:
        fail("NOTICE does not include query grammar attribution")

    print(
        json.dumps(
            {
                "status": "query-core-static-contract-passed",
                "rust_tests": combined.count("#[test]"),
                "accepted_vectors": len(vectors["accepted"]),
                "rejected_vectors": len(vectors["rejected"]),
                "cargo_executed_locally": False,
                "oracle_verified": False,
                "sg3_complete": False,
                "compatibility_credit": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
