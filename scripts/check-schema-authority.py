#!/usr/bin/env python3
"""Enforce the single production-authoritative database schema chain."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
AUTHORITY_PATH = ROOT / "docs/development/SCHEMA_AUTHORITY.json"
QUARANTINE_STATUS_PATH = ROOT / "database/schema/v2/STATUS.json"
FORBIDDEN_LITERAL = "database/schema/v2"
HISTORICAL_DESIGN_TOOLS = {
    Path("scripts/check-database-v2.py"),
    Path("scripts/verify-database-profile-v2.py"),
    Path("scripts/verify-command-transaction-v2.py"),
}
ALLOWED_CONTROL_REFERENCES = {
    Path("scripts/check-plan.py"),
    Path("scripts/check-schema-authority.py"),
} | HISTORICAL_DESIGN_TOOLS
TEXT_SUFFIXES = {
    ".py",
    ".sh",
    ".rs",
    ".toml",
    ".yml",
    ".yaml",
    ".json",
    ".md",
    ".sql",
    ".go",
    ".mod",
    ".sum",
}
CONSUMER_ROOTS = [
    Path(".github/workflows"),
    Path("scripts"),
    Path("crates"),
    Path("runtime"),
    Path("deploy"),
    Path("compose.yaml"),
    Path("Makefile"),
]
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


def fail(message: str) -> None:
    raise ValidationError(message)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"{path.relative_to(ROOT)}: invalid JSON: {exc}")
    if not isinstance(value, dict):
        fail(f"{path.relative_to(ROOT)}: top-level value must be an object")
    return value


def files_under(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.exists():
        return []
    return sorted(item for item in path.rglob("*") if item.is_file())


def migration_digest(relative_root: str) -> tuple[str, list[Path]]:
    root = ROOT / relative_root
    files = [path for path in files_under(root) if path.suffix == ".sql"]
    require(files, f"{relative_root}: no SQL migrations")
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(ROOT).as_posix().encode("utf-8")
        content = path.read_bytes()
        require(content.strip(), f"{path.relative_to(ROOT)}: empty migration")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest(), files


def validate_authority() -> dict[str, str]:
    document = load_json(AUTHORITY_PATH)
    require(document.get("schema") == "trillionnium.schema-authority.v1", "schema authority schema")
    authority = document.get("authority", {})
    require(authority.get("migration_root") == "migrations", "authoritative migration root")
    profiles = authority.get("profiles", [])
    require([row.get("id") for row in profiles] == ["postgresql", "cockroachdb"], "database profiles")
    digests: dict[str, str] = {}
    for profile in profiles:
        profile_id = profile["id"]
        relative_root = profile.get("path")
        require(relative_root == f"migrations/{profile_id}", f"{profile_id}: migration path")
        require(profile.get("runtime_adapter") == "crates/trnm-persistence-pg", f"{profile_id}: adapter")
        digest, files = migration_digest(relative_root)
        require(all(path.name.endswith(".sql") for path in files), f"{profile_id}: migration naming")
        digests[profile_id] = digest

    non_authoritative = document.get("non_authoritative", [])
    require(len(non_authoritative) == 1, "expected one quarantined schema family")
    quarantine = non_authoritative[0]
    require(quarantine.get("path") == FORBIDDEN_LITERAL, "quarantined schema path")
    require(quarantine.get("compatibility_credit") is False, "quarantined schema credit")
    forbidden_consumers = set(quarantine.get("forbidden_consumers", []))
    require(
        {"runtime", "ci-live-database", "backup", "restore", "release"} <= forbidden_consumers,
        "quarantined schema forbidden consumers",
    )

    abi_tables = set(document.get("adapter_abi", {}).get("required_tables", []))
    require(abi_tables == REQUIRED_TABLES, "adapter ABI table list")
    return digests


def validate_quarantine() -> None:
    status = load_json(QUARANTINE_STATUS_PATH)
    require(status.get("schema") == "trillionnium.schema-family-status.v1", "quarantine status schema")
    require(status.get("path") == FORBIDDEN_LITERAL, "quarantine status path")
    for field in (
        "production_authority",
        "runtime_consumption_allowed",
        "ci_live_database_consumption_allowed",
        "backup_restore_consumption_allowed",
        "release_consumption_allowed",
        "compatibility_credit",
    ):
        require(status.get(field) is False, f"quarantine status {field}")
    require(status.get("authoritative_migration_root") == "migrations", "quarantine authority pointer")
    require((ROOT / "database/schema/v2/README.md").is_file(), "quarantine README missing")


def scan_forbidden_consumers() -> None:
    literal_violations: list[str] = []
    historical_tool_callers: list[str] = []
    historical_names = {path.name for path in HISTORICAL_DESIGN_TOOLS}
    for consumer_root in CONSUMER_ROOTS:
        absolute = ROOT / consumer_root
        for path in files_under(absolute):
            relative = path.relative_to(ROOT)
            if path.suffix and path.suffix not in TEXT_SUFFIXES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if relative not in ALLOWED_CONTROL_REFERENCES and FORBIDDEN_LITERAL in text:
                literal_violations.append(relative.as_posix())
            if relative not in ALLOWED_CONTROL_REFERENCES and any(name in text for name in historical_names):
                historical_tool_callers.append(relative.as_posix())
    require(
        not literal_violations,
        "non-authoritative schema referenced by production/CI consumers: "
        + ", ".join(sorted(literal_violations)),
    )
    require(
        not historical_tool_callers,
        "historical alternate-schema verifier invoked by production/CI consumers: "
        + ", ".join(sorted(historical_tool_callers)),
    )


def validate_sql_abi() -> None:
    adapter = (ROOT / "crates/trnm-persistence-pg/src/lib.rs").read_text(encoding="utf-8")
    missing_adapter = sorted(table for table in REQUIRED_TABLES if table not in adapter)
    adapter_owned = {
        "trnm_schema_metadata",
        "trnm_entity_heads",
        "trnm_command_receipts",
        "trnm_events",
        "trnm_outbox",
        "trnm_command_outbox",
    }
    missing_owned = sorted(table for table in adapter_owned if table not in adapter)
    require(not missing_owned, f"adapter missing core table references {missing_owned}")

    for profile in ("postgresql", "cockroachdb"):
        sql = "\n".join(
            path.read_text(encoding="utf-8")
            for path in files_under(ROOT / f"migrations/{profile}")
            if path.suffix == ".sql"
        )
        missing = sorted(table for table in REQUIRED_TABLES if table not in sql)
        require(not missing, f"{profile}: missing authoritative tables {missing}")
    if missing_adapter:
        print(
            "schema authority note: adapter does not yet access all authoritative tables: "
            + ", ".join(missing_adapter)
        )


def main() -> int:
    try:
        digests = validate_authority()
        validate_quarantine()
        scan_forbidden_consumers()
        validate_sql_abi()
    except (ValidationError, OSError) as exc:
        print(f"schema authority validation failed: {exc}", file=sys.stderr)
        return 1
    for profile, digest in digests.items():
        print(f"{profile} migration-chain sha256={digest}")
    print("TrillionniumGame schema authority: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
