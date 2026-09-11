#!/usr/bin/env python3
"""Validate the PostgreSQL recovery barrier source contract."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "scripts/postgresql-recovery-quarantine.sql"
HARNESS = ROOT / "scripts/ci-postgresql-recovery-barrier.sh"
REQUIRED_SQL = (
    "BEGIN;",
    "LOCK TABLE trnm_outbox IN ACCESS EXCLUSIVE MODE",
    "active outbox lease blocks recovery quarantine",
    "COPY (",
    "WHERE state = 0",
    "UPDATE trnm_outbox",
    "SET state = 3",
    "dead_reason_digest",
    "ALTER ROLE trnm_runtime IN DATABASE trnm_source",
    "default_transaction_read_only = on",
    "COMMIT;",
)
REQUIRED_HARNESS = (
    "active-lease-rejection",
    "postgresql-recovery-quarantine.sql",
    "quarantine-export.csv",
    "read-only runtime write unexpectedly succeeded",
    "runtime-read-after-fence",
    "pending_or_leased_after_quarantine",
    "quarantine_export_sha256",
    '"write_fence_effective_for_new_runtime_connections": True',
    '"accepted_evidence": False',
    '"routing_fence_proven": False',
    '"production_ready": False',
)

class ValidationError(RuntimeError):
    pass

def require(value: bool, message: str) -> None:
    if not value:
        raise ValidationError(message)

def validate_texts(sql: str, harness: str) -> None:
    for marker in REQUIRED_SQL:
        require(marker in sql, f"quarantine SQL missing {marker}")
    for marker in REQUIRED_HARNESS:
        require(marker in harness, f"barrier harness missing {marker}")
    combined = sql + harness
    authority_path = ROOT / "docs/development/SCHEMA_AUTHORITY.json"
    authority = __import__("json").loads(authority_path.read_text(encoding="utf-8"))
    quarantines = authority.get("non_authoritative")
    require(isinstance(quarantines, list) and quarantines, "schema quarantine registry missing")
    for quarantine in quarantines:
        require(isinstance(quarantine, dict), "schema quarantine row invalid")
        quarantine_path = quarantine.get("path")
        require(isinstance(quarantine_path, str) and quarantine_path, "schema quarantine path invalid")
        require(quarantine_path not in combined, "non-authoritative schema referenced")
    require("DROP TABLE" not in combined.upper(), "destructive table rollback introduced")
    require("DELETE FROM trnm_outbox" not in combined, "pending outbox deletion introduced")
    require("|| true" not in harness, "failure suppression introduced")

def main() -> int:
    try:
        validate_texts(
            SQL.read_text(encoding="utf-8"),
            HARNESS.read_text(encoding="utf-8"),
        )
    except (OSError, ValidationError) as error:
        print(f"PostgreSQL recovery barrier contract failed: {error}", file=sys.stderr)
        return 1
    print("PostgreSQL recovery barrier source contract: OK")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
