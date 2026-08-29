#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "crates/trnm-persistence-core/src/lib.rs"
WORKSPACE = ROOT / "Cargo.toml"
VECTORS = ROOT / "contracts/persistence/persistence-vectors.v1.json"


def fail(message: str) -> None:
    raise SystemExit(f"persistence core contract failed: {message}")


def main() -> None:
    for path in (LIB, WORKSPACE, VECTORS):
        if not path.is_file():
            fail(f"missing {path.relative_to(ROOT)}")

    source = LIB.read_text(encoding="utf-8")
    workspace = WORKSPACE.read_text(encoding="utf-8")
    vectors = json.loads(VECTORS.read_text(encoding="utf-8"))

    for symbol in (
        "pub struct DurableState",
        "pub struct CommandIntent",
        "pub struct PreparedCommit",
        "pub struct Receipt",
        "pub enum OutboxState",
        "pub fn prepare",
        "pub fn commit",
        "pub fn takeover",
        "pub fn lease",
        "pub fn apply",
        "pub fn retry",
        "pub fn dead_letter",
    ):
        if symbol not in source:
            fail(f"missing symbol {symbol}")

    for pattern in (
        r"\bunsafe\b(?!_code)",
        r"std::net",
        r"tokio",
        r"async_std",
        r"sqlx",
        r"tokio_postgres",
        r"std::time",
        r"SystemTime",
        r"rand::",
        r"ring::",
        r"openssl",
        r"reqwest",
        r"hyper::",
    ):
        if re.search(pattern, source):
            fail(f"forbidden capability pattern {pattern}")

    if '"crates/trnm-persistence-core"' not in workspace:
        fail("workspace does not include persistence core")
    if len(vectors.get("cases", [])) < 6:
        fail("insufficient vector cases")
    if any(vectors.get("claims", {}).values()):
        fail("vector contract overclaims maturity")

    status = json.loads(
        (ROOT / "docs/status/PERSISTENCE_FOUNDATION_STATUS.json").read_text(encoding="utf-8")
    )
    for field in (
        "database_durable",
        "postgresql_verified",
        "cockroachdb_verified",
        "sg4_complete",
        "compatibility_credit",
        "production_ready",
    ):
        if status.get("claims", {}).get(field) is not False:
            fail(f"status claim {field} must remain false")

    print(
        json.dumps(
            {
                "status": "persistence-core-static-contract-passed",
                "rust_test_contracts": source.count("#[test]"),
                "vector_cases": len(vectors["cases"]),
                "database_durable": False,
                "production_ready": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
