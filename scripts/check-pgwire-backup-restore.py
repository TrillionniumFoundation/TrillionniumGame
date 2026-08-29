#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

POSTGRES_IMAGE = (
    "postgres:17.6-alpine3.22@sha256:"
    "ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94"
)
COCKROACH_IMAGE = (
    "cockroachdb/cockroach:v24.1.2@sha256:"
    "105b9d1e10e4845c9c59266bef3c27ff8b82eeaeb1b464c75423408c3a2968ba"
)
EXPECTED_TABLES = {
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


def check(root: Path) -> None:
    script_path = root / "scripts/ci-pgwire-backup-restore.sh"
    script = script_path.read_text(encoding="utf-8")
    for image in (POSTGRES_IMAGE, COCKROACH_IMAGE):
        if script.count(image) != 1:
            raise SystemExit(f"exact image identity missing or duplicated: {image}")
    for forbidden in (":latest", "production_pitr\":true", "multi_node_restore\":true"):
        if forbidden in script:
            raise SystemExit(f"forbidden overclaim or floating input: {forbidden}")
    for required in (
        "pg_dump -Fc",
        "pg_restore --no-owner --no-privileges",
        "BACKUP DATABASE trnm INTO 'nodelocal://1/trnm-backup'",
        "RESTORE DATABASE trnm FROM LATEST",
        'cmp "$evidence/source.csv" "$evidence/restored.csv"',
        'test -s "$evidence/backup.dump"',
        'test -s "$evidence/backup-manifest.csv"',
    ):
        if required not in script:
            raise SystemExit(f"required backup/restore assertion missing: {required}")
    missing_tables = sorted(table for table in EXPECTED_TABLES if table not in script)
    if missing_tables:
        raise SystemExit(f"semantic snapshot omits tables: {missing_tables}")
    if script.count("semantic_snapshot_equal\":true") != 1:
        raise SystemExit("semantic equality claim must be emitted exactly once after cmp")
    print(
        "backup/restore source contract passed: "
        f"tables={len(EXPECTED_TABLES)} profiles=2 production_pitr=false multi_node=false"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()
    check(args.root.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
