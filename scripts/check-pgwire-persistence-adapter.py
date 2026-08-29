#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARGO = ROOT / "crates/trnm-persistence-pg/Cargo.toml"
LIB = ROOT / "crates/trnm-persistence-pg/src/lib.rs"
RUNTIME = ROOT / "crates/trnm-persistence-pg/tests/runtime.rs"
CONTRACT = ROOT / "contracts/database/pgwire-persistence-adapter.v1.json"


def fail(message: str) -> None:
    raise SystemExit(f"pgwire persistence adapter contract failed: {message}")


def main() -> None:
    for path in (CARGO, LIB, RUNTIME, CONTRACT):
        if not path.is_file():
            fail(f"missing {path.relative_to(ROOT)}")

    cargo = CARGO.read_text(encoding="utf-8")
    source = LIB.read_text(encoding="utf-8")
    runtime = RUNTIME.read_text(encoding="utf-8")
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))

    if 'postgres = "=0.19.14"' not in cargo:
        fail("rust-postgres dependency is not exactly pinned")
    for symbol in (
        "pub struct PgRepository",
        "pub fn connect",
        "pub fn bind_schema_metadata",
        "pub fn bootstrap_entity",
        "pub fn load_head",
        "pub fn commit_command",
        "pub fn classify_sqlstate",
    ):
        if symbol not in source:
            fail(f"missing {symbol}")
    for fragment in (
        "Client::connect",
        "IsolationLevel::Serializable",
        "FOR UPDATE",
        "entity_compare_and_swap_failed",
        "INSERT INTO trnm_command_receipts",
        "INSERT INTO trnm_events",
        "INSERT INTO trnm_outbox",
        "INSERT INTO trnm_command_outbox",
        "transaction.commit()",
        'classify_sqlstate("40001")',
    ):
        if fragment not in source:
            fail(f"missing adapter contract fragment {fragment}")

    for pattern in (
        r"\bunsafe\b(?!_code)",
        r"std::process",
        r"Command::new",
        r"\bpsql\b",
        r"\bdocker\b",
        r"tokio::process",
    ):
        if re.search(pattern, source, re.IGNORECASE):
            fail(f"adapter gained forbidden subprocess/capability pattern {pattern}")

    for fragment in (
        "TRNM_DATABASE_URL",
        "TRNM_DATABASE_PROFILE",
        "drop(repository)",
        "CommitOutcome::Duplicate",
        "command_id_conflict",
        "entity_revision_mismatch",
    ):
        if fragment not in runtime:
            fail(f"live contract missing {fragment}")

    if contract.get("schema") != "trillionnium.pgwire-persistence-adapter-contract.v1":
        fail("unexpected contract schema")
    if contract["driver"] != {
        "crate": "postgres",
        "version": "0.19.14",
        "mode": "synchronous-native-pgwire",
        "subprocess_wrapper_allowed": False,
    }:
        fail("driver contract drift")
    if contract["security"]["production_no_tls_allowed"] is not False:
        fail("production NoTls must remain forbidden")
    if contract["security"]["shell_or_psql_adapter_allowed"] is not False:
        fail("shell adapter must remain forbidden")
    if any(contract["claims"].values()):
        fail("source contract overclaims unexecuted live maturity")

    print(json.dumps({
        "status": "pgwire-persistence-adapter-static-contract-passed",
        "driver": "postgres-0.19.14",
        "profiles": contract["profiles"],
        "rust_test_contracts": source.count("#[test]") + runtime.count("#[test]"),
        "live_adapter_verified": False,
        "production_ready": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
