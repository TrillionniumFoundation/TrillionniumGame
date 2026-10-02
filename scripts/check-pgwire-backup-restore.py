#!/usr/bin/env python3
from __future__ import annotations

import importlib.util

import argparse
import json
import re
from pathlib import Path
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



SCHEMA_SPEC = importlib.util.spec_from_file_location(
    "schema_consumer_source_contract", Path(__file__).with_name("check-schema-authority.py")
)
if SCHEMA_SPEC is None or SCHEMA_SPEC.loader is None:
    raise RuntimeError("schema consumer checker unavailable")
SCHEMA = importlib.util.module_from_spec(SCHEMA_SPEC)
SCHEMA_SPEC.loader.exec_module(SCHEMA)

def validate_text(script: str, image_config: dict) -> None:
    profiles = image_config.get("profiles")
    if not isinstance(profiles, dict) or set(profiles) != {"postgresql", "cockroachdb"}:
        raise SystemExit("current database image profiles are invalid")
    images = set()
    for profile in ("postgresql", "cockroachdb"):
        row = profiles[profile]
        image = row.get("image") if isinstance(row, dict) else None
        if not isinstance(image, str) or re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", image) is None:
            raise SystemExit(f"current {profile} image lacks an immutable OCI digest")
        images.add(image)
    if "config/database-test-images.json" not in script or not re.search(r"\[['\"]image['\"]\]", script):
        raise SystemExit("backup profiles must read their image identity from the current configuration")
    for image in re.findall(r"(?:postgres|cockroachdb/cockroach)(?::[^\s'\"@]+)?@sha256:[0-9a-f]{64}", script):
        if image not in images:
            raise SystemExit(f"backup image differs from current configuration: {image}")
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
    try:
        SCHEMA.validate_schema_consumer(script, None)
        SCHEMA.validate_known_timestamp_fixtures(script)
    except SCHEMA.ValidationError as error:
        raise SystemExit(str(error)) from error
    if script.count("apply-authoritative-schema.sh") < 2:
        raise SystemExit("both backup profiles must consume the complete Rust schema chain")
    if script.count("apply-authoritative-schema.sh verify") != 2 or script.count("--mode verify") != 2:
        raise SystemExit("both restored profiles must verify the retained complete schema identity")


def check(root: Path) -> None:
    script = (root / "scripts/ci-pgwire-backup-restore.sh").read_text(encoding="utf-8")
    config = json.loads((root / "config/database-test-images.json").read_text(encoding="utf-8"))
    validate_text(script, config)
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
