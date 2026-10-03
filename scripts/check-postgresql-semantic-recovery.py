#!/usr/bin/env python3
"""Fail-closed source contract for PostgreSQL semantic recovery evidence."""
from __future__ import annotations

import importlib.util

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "scripts/postgresql-semantic-snapshot.sql"
CATALOG = ROOT / "scripts/postgresql-catalog-snapshot.sql"
HARNESS = ROOT / "scripts/ci-postgresql-semantic-recovery.sh"
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


SCHEMA_SPEC = importlib.util.spec_from_file_location(
    "schema_consumer_source_contract", Path(__file__).with_name("check-schema-authority.py")
)
if SCHEMA_SPEC is None or SCHEMA_SPEC.loader is None:
    raise RuntimeError("schema consumer checker unavailable")
SCHEMA = importlib.util.module_from_spec(SCHEMA_SPEC)
SCHEMA_SPEC.loader.exec_module(SCHEMA)
V3_SPEC = importlib.util.spec_from_file_location(
    "storage_v3_recovery_contract", Path(__file__).with_name("check-pgwire-backup-restore.py")
)
if V3_SPEC is None or V3_SPEC.loader is None:
    raise RuntimeError("storage v3 recovery checker unavailable")
V3 = importlib.util.module_from_spec(V3_SPEC)
V3_SPEC.loader.exec_module(V3)


def require(value: bool, message: str) -> None:
    if not value:
        raise ValidationError(message)

def validate_texts(data: str, catalog: str, harness: str) -> None:
    for table in sorted(REQUIRED_TABLES):
        require(table in data, f"semantic snapshot missing {table}")
    for marker in (
        "information_schema.columns",
        "pg_constraint",
        "pg_get_constraintdef",
        "pg_indexes",
    ):
        require(marker in catalog, f"catalog snapshot missing {marker}")
    for marker in (
        "migrations/postgresql",
        "pg_dump",
        "pg_restore",
        "--exit-on-error",
        "repeat-migration",
        "negative-constraint",
        "postgresql-semantic-snapshot.sql",
        "postgresql-catalog-snapshot.sql",
        "cmp --silent",
        "sha256",
        '"semantic_data_equal": True',
        '"semantic_catalog_equal": True',
        '"accepted_evidence": False',
        '"production_ready": False',
    ):
        require(marker in harness, f"recovery harness missing {marker}")
    combined = data + catalog + harness
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
    require("|| true" not in harness, "failure suppression introduced")
    try:
        SCHEMA.validate_schema_consumer(harness, "postgresql", metadata_negative=True)
        SCHEMA.validate_pinned_database_image(harness, 'postgresql')
        SCHEMA.validate_storage_snapshot(data)
        SCHEMA.validate_known_timestamp_fixtures(harness)
        SCHEMA.validate_semantic_negative_probes(harness, "postgresql")
        V3.validate_v3_snapshot_source(data)
        V3.validate_v3_semantic_harness(harness)
    except (SCHEMA.ValidationError, SystemExit) as error:
        raise ValidationError(str(error)) from error

def main() -> int:
    try:
        validate_texts(
            DATA.read_text(encoding="utf-8"),
            CATALOG.read_text(encoding="utf-8"),
            HARNESS.read_text(encoding="utf-8"),
        )
    except (OSError, ValidationError) as error:
        print(f"PostgreSQL semantic recovery contract failed: {error}", file=sys.stderr)
        return 1
    print("PostgreSQL semantic recovery source contract: OK")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
