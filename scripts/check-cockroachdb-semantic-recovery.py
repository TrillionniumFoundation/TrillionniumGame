#!/usr/bin/env python3
"""Fail-closed source contract for CockroachDB semantic recovery evidence."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "scripts/cockroachdb-semantic-snapshot.sql"
HARNESS = ROOT / "scripts/ci-cockroachdb-semantic-recovery.sh"
REQUIRED_TABLES = {
    "trnm_schema_metadata",
    "trnm_entity_heads",
    "trnm_command_receipts",
    "trnm_events",
    "trnm_outbox",
    "trnm_command_outbox",
    "trnm_authority_leases",
    "trnm_session_families",
    "trnm_refresh_tokens",
    "trnm_storage_objects",
}

class ValidationError(RuntimeError):
    pass

def require(value: bool, message: str) -> None:
    if not value:
        raise ValidationError(message)

def validate_texts(data: str, harness: str) -> None:
    for table in sorted(REQUIRED_TABLES):
        require(table in data, f"semantic snapshot missing {table}")
    for marker in (
        "migrations/cockroachdb",
        "BACKUP DATABASE",
        "RESTORE DATABASE",
        "FROM LATEST IN",
        "new_db_name",
        "SHOW CREATE ALL TABLES",
        "repeat-migration",
        "negative-constraint",
        "cockroachdb-semantic-snapshot.sql",
        "cmp --silent",
        "backup_directory_sha256",
        '"semantic_data_equal": True',
        '"semantic_catalog_equal": True',
        '"accepted_evidence": False',
        '"production_ready": False',
    ):
        require(marker in harness, f"recovery harness missing {marker}")
    combined = data + harness
    require("migrations/postgresql" not in combined, "PostgreSQL profile inherited")
    authority_path = ROOT / "docs/development/SCHEMA_AUTHORITY.json"
    authority = __import__("json").loads(authority_path.read_text(encoding="utf-8"))
    quarantines = authority.get("non_authoritative")
    require(isinstance(quarantines, list) and quarantines, "schema quarantine registry missing")
    for quarantine in quarantines:
        require(isinstance(quarantine, dict), "schema quarantine row invalid")
        quarantine_path = quarantine.get("path")
        require(isinstance(quarantine_path, str) and quarantine_path, "schema quarantine path invalid")
        require(quarantine_path not in combined, "non-authoritative schema referenced")
    require("DROP TABLE" not in combined.upper(), "destructive rollback introduced")
    require("|| true" not in harness, "failure suppression introduced")

def main() -> int:
    try:
        validate_texts(
            DATA.read_text(encoding="utf-8"),
            HARNESS.read_text(encoding="utf-8"),
        )
    except (OSError, ValidationError) as error:
        print(f"CockroachDB semantic recovery contract failed: {error}", file=sys.stderr)
        return 1
    print("CockroachDB semantic recovery source contract: OK")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
