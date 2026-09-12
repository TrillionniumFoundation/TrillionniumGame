#!/usr/bin/env python3
"""Validate PostgreSQL connection-fault evidence source."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "scripts/ci-postgresql-connection-faults.sh"
REQUIRED = (
    "connection-churn",
    "CONNECTION LIMIT 4",
    "pool-exhaustion",
    "pg_cancel_backend",
    "cancel-transaction",
    "pg_terminate_backend",
    "terminate-transaction",
    "statement_timeout",
    "fresh-connection-after-faults",
    "cancel_rollback_verified",
    "terminate_rollback_verified",
    "pool_exhaustion_rejected",
    '"accepted_evidence": False',
    '"production_ready": False',
)

class ValidationError(RuntimeError):
    pass

def require(value: bool, message: str) -> None:
    if not value:
        raise ValidationError(message)

def validate_text(text: str) -> None:
    for marker in REQUIRED:
        require(marker in text, f"connection-fault harness missing {marker}")
    authority_path = ROOT / "docs/development/SCHEMA_AUTHORITY.json"
    authority = __import__("json").loads(authority_path.read_text(encoding="utf-8"))
    quarantines = authority.get("non_authoritative")
    require(isinstance(quarantines, list) and quarantines, "schema quarantine registry missing")
    for quarantine in quarantines:
        require(isinstance(quarantine, dict), "schema quarantine row invalid")
        quarantine_path = quarantine.get("path")
        require(isinstance(quarantine_path, str) and quarantine_path, "schema quarantine path invalid")
        require(quarantine_path not in text, "non-authoritative schema referenced")
    require("DROP TABLE" not in text.upper(), "destructive schema operation introduced")
    require("|| true" not in text, "failure suppression introduced")
    require("TRNM_REQUIRE_LIVE_DATABASE" in text, "mandatory live-profile marker absent")

def main() -> int:
    try:
        validate_text(HARNESS.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as error:
        print(f"PostgreSQL connection-fault contract failed: {error}", file=sys.stderr)
        return 1
    print("PostgreSQL connection-fault source contract: OK")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
