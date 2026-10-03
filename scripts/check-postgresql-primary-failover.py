#!/usr/bin/env python3
"""Validate the PostgreSQL synchronous failover source contract."""
from __future__ import annotations

import importlib.util

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "scripts/ci-postgresql-primary-failover.sh"
REQUIRED = (
    "pg_basebackup",
    "synchronous_standby_names",
    "synchronous_commit",
    "remote_apply",
    "pg_stat_replication",
    "pg_last_wal_replay_lsn",
    "pg_ctl",
    "promote",
    "postgresql-semantic-snapshot.sql",
    "acknowledged-snapshot.txt",
    "promoted-snapshot.txt",
    "cmp --silent",
    "post-failover-write",
    "measured_failover_ms",
    '"zero_acknowledged_loss_observed": True',
    '"approved_rpo_rto": False',
    '"accepted_evidence": False',
    '"production_ready": False',
)

class ValidationError(RuntimeError):
    pass


SCHEMA_SPEC = importlib.util.spec_from_file_location(
    "schema_consumer_source_contract", Path(__file__).with_name("check-schema-authority.py")
)
if SCHEMA_SPEC is None or SCHEMA_SPEC.loader is None:
    raise RuntimeError("schema consumer checker unavailable")
SCHEMA = importlib.util.module_from_spec(SCHEMA_SPEC)
SCHEMA_SPEC.loader.exec_module(SCHEMA)

def require(value: bool, message: str) -> None:
    if not value:
        raise ValidationError(message)

def validate_text(text: str) -> None:
    for marker in REQUIRED:
        require(marker in text, f"primary-failover harness missing {marker}")
    authority_path = ROOT / "docs/development/SCHEMA_AUTHORITY.json"
    authority = __import__("json").loads(authority_path.read_text(encoding="utf-8"))
    quarantines = authority.get("non_authoritative")
    require(isinstance(quarantines, list) and quarantines, "schema quarantine registry missing")
    for quarantine in quarantines:
        require(isinstance(quarantine, dict), "schema quarantine row invalid")
        quarantine_path = quarantine.get("path")
        require(isinstance(quarantine_path, str) and quarantine_path, "schema quarantine path invalid")
        require(quarantine_path not in text, "non-authoritative schema referenced")
    require("DROP TABLE" not in text.upper(), "destructive schema rollback introduced")
    require("|| true" not in text, "failure suppression introduced")
    require("docker start trnm-pg-primary" not in text, "old primary automatically reintroduced")
    try:
        SCHEMA.validate_schema_consumer(text, "postgresql")
        SCHEMA.validate_pinned_database_image(text, 'postgresql')
    except SCHEMA.ValidationError as error:
        raise ValidationError(str(error)) from error

def main() -> int:
    try:
        validate_text(HARNESS.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as error:
        print(f"PostgreSQL primary failover contract failed: {error}", file=sys.stderr)
        return 1
    print("PostgreSQL primary failover source contract: OK")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
