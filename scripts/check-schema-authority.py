#!/usr/bin/env python3
"""Enforce the single production-authoritative database schema chain."""
from __future__ import annotations

import importlib.util
import json
import re
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
# This exact, Git-blob-pinned baseline is specification data, not a runtime,
# CI-live-database, backup, restore or release consumer. It must retain the
# quarantined path verbatim because that path is part of an immutable gap
# closure criterion. Keep this exemption file-specific: sibling or future
# control files do not inherit authority to consume the alternate schema.
IMMUTABLE_SPECIFICATION_REFERENCES = {
    Path("scripts/control_baselines/gap-register.v1.json"),
}
ALLOWED_CONTROL_REFERENCES = {
    Path("scripts/check-plan.py"),
    Path("scripts/check-plan-v3-extension.py"),
    Path("scripts/check-schema-authority.py"),
    Path("scripts/check-trnm-server.py"),
} | HISTORICAL_DESIGN_TOOLS | IMMUTABLE_SPECIFICATION_REFERENCES
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


def validated_migration_chain() -> dict[str, Any]:
    # One source validator supplies order, complete inventory and the same
    # algorithm as the Rust runner. Historical raw-content digests are records,
    # never an alternative current migration identity.
    script = Path(__file__).with_name("check-migration-lock.py")
    spec = importlib.util.spec_from_file_location("schema_authority_migration_lock", script)
    require(spec is not None and spec.loader is not None, "migration-lock checker unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        return module.validate(ROOT)
    except (module.ValidationError, OSError, ValueError) as error:
        fail(f"authoritative migration lock: {error}")


def migration_digest(relative_root: str, report: dict[str, Any] | None = None) -> tuple[str, list[Path]]:
    require(relative_root in ("migrations/postgresql", "migrations/cockroachdb"), "authoritative profile path")
    report = validated_migration_chain() if report is None else report
    row = report["profiles"][relative_root.rsplit("/", 1)[1]]
    return row["chain_sha256"], [ROOT / path for path in row["ordered_paths"]]


def validate_schema_consumer(harness: str, profile: str | None, *, mode: str = "migrate",
                             metadata_negative: bool = False) -> None:
    """Static wiring checks; only native execution can prove these effects."""
    require("apply-authoritative-schema.sh" in harness and
            re.search(r'apply-authoritative-schema\.sh["\']?\s+' + re.escape(mode) + r'\b', harness) is not None,
            f"shared Rust schema {mode} consumer missing")
    require("schema-identity.json" in harness, "actual schema identity is not retained")
    for marker in ("MIGRATION_CHAIN.lock.json", "migration-lock.json", "check-migration-lock.py",
                   "migration-chain-validation.json", "check-authoritative-schema-identity.py",
                   "schema-identity-check.json"):
        require(marker in harness, f"complete schema chain evidence missing {marker}")
    if profile is not None:
        require(re.search(r'TRNM_DATABASE_PROFILE=["\']?' + re.escape(profile) + r'\b', harness) is not None,
                "schema consumer profile binding missing")
    for obsolete in ('cat "${migrations[@]}"', 'mapfile -t migrations', '< "$migration"', '<"$migration"'):
        require(obsolete not in harness, "direct unvalidated migration executor introduced")
    require(re.search(r'INSERT\s+INTO\s+trnm_storage_objects\s+VALUES\b', harness, re.I) is None,
            "positional storage INSERT introduced")
    metadata = list(re.finditer(r'INSERT\s+INTO\s+trnm_schema_metadata\b', harness, re.I))
    if metadata_negative:
        require(len(metadata) == 1 and re.search(
            r'INSERT\s+INTO\s+trnm_schema_metadata\s*\([^)]*\)\s*VALUES\s*\(2\s*,', harness, re.I),
            "only the explicit-column singleton negative may insert schema metadata")
    else:
        require(not metadata, "consumer bypasses Rust schema metadata publication")


def validate_storage_snapshot(data: str) -> None:
    for name in ("chain_digest", "digest_algorithm", "storage_writer_epoch", "upgrade_source_commit"):
        require(re.search(r"['\"]" + name + r"['\"]\s*,\s*" + name + r"\b", data) is not None,
                f"semantic snapshot omits schema identity field {name}")
    for name in ("create_time", "update_time"):
        require(re.search(r'extract\s*\(\s*epoch\s+FROM\s+' + name + r'\s*\)', data, re.I) is not None,
                f"semantic snapshot omits exact epoch projection {name}")
    require("updated_at_ms" in data, "legacy timestamp provenance disappeared")


def validate_pinned_database_image(harness: str, profile: str) -> None:
    variable = {"postgresql": "POSTGRES_IMAGE", "cockroachdb": "COCKROACH_IMAGE"}[profile]
    require("config/database-test-images.json" in harness and re.search(
        r"\[['\"]profiles['\"]\]\[['\"]" + profile + r"['\"]\]\[['\"]image['\"]\]", harness) is not None,
        "native database image must come from the current pinned profile")
    require(re.search(r'\[\[\s+["\']?\$' + variable +
                      r'["\']?\s+!=\s+["\']?\$expected_image["\']?\s+\]\]', harness) is not None and
            "exit 64" in harness,
            "native database image override is not rejected when the pinned profile differs")


def validate_known_timestamp_fixtures(harness: str) -> None:
    for marker in ("known-time", "1969-12-31 23:59:59.999999+00", "2024-02-29 00:00:00.123456+00",
                   "create_time", "update_time"):
        require(marker in harness, f"restore fixture omits {marker}")
    require(re.search(r'updated_at_ms\s*\)\s*VALUES\b', harness, re.I) is not None,
            "unknown-history fixture must omit nullable real timestamps explicitly")


def validate_semantic_negative_probes(harness: str, profile: str) -> None:
    require("check-sql-error.py" in harness, "SQL errors are not checked by the shared verifier")
    require('--sqlstate "$expected_state" --constraint "$expected_constraint"' in harness,
            "constraint probes do not verify intended SQLSTATE and constraint")
    probes = re.findall(r'^negative_constraint\s+([a-z-]+)\s+([0-9A-Z]{5})\s+"([a-z0-9_]+)"', harness, re.M)
    expected = {
        "metadata-singleton": ("23514", "trnm_schema_metadata_singleton_check", "check_singleton"),
        "entity-id-width": ("23514", "trnm_entity_heads_entity_id_check", "check_entity_id"),
        "receipt-event-range": ("23514", "trnm_command_receipts_check", "check_event_count_first_event_sequence_event_count_first_event_sequence_first_event_sequence_last_event_sequence_first_event_sequence_event_count"),
        "event-foreign-key": ("23503", "trnm_events_entity_id_command_id_fkey", "trnm_events_entity_id_command_id_fkey"),
        "outbox-state-shape": ("23514", "trnm_outbox_check", "check_state_owner_node_receipt_digest_dead_reason_digest_state_owner_node_receipt_digest_dead_reason_digest_state_owner_node_receipt_digest_dead_reason_digest_state_owner_node_receipt_digest_dead_reason_digest"),
        "command-outbox-position": ("23514", "trnm_command_outbox_position_check", "check_position_position"),
        "lease-generation": ("23514", "trnm_authority_leases_lease_generation_check", "check_lease_generation"),
        "session-state-shape": ("23514", "trnm_session_families_check1", "check_revoked_reason_active_token_id_revoked_reason_active_token_id"),
        "refresh-consumed-shape": ("23514", "trnm_refresh_tokens_check", "check_state_consumed_at_ms_state_consumed_at_ms_consumed_at_ms_issued_at_ms"),
        "storage-collection": ("23514", "trnm_storage_objects_collection_check", "check_collection"),
    }
    require(len(probes) == len(expected) and len({row[0] for row in probes}) == len(expected),
            "ten distinct intended constraint probes are required")
    for label, state, constraint in probes:
        require(label in expected, "unexpected constraint probe")
        row = expected[label]
        require((state, constraint) == (row[0], row[1 if profile == "postgresql" else 2]),
                f"{label}: SQLSTATE/constraint target differs")
    require("--sqlstate 42P07" in harness, "raw immutable foundation replay must assert duplicate-table SQLSTATE")
    require("repeat-schema-identity.json" in harness and
            re.search(r"\[['\"]migration_applied['\"]\]\s+is\s+False", harness) is not None and
            re.search(r"\[['\"]applied_steps['\"]\]\s*==\s*0", harness) is not None,
            "shared migrator repeated no-op is not checked")


def validate_authority() -> dict[str, str]:
    document = load_json(AUTHORITY_PATH)
    require(document.get("schema") == "trillionnium.schema-authority.v1", "schema authority schema")
    authority = document.get("authority", {})
    require(authority.get("migration_root") == "migrations", "authoritative migration root")
    chain = validated_migration_chain()
    require(authority.get("schema_version") == chain["schema_version"], "authority schema version differs from lock")
    profiles = authority.get("profiles", [])
    require([row.get("id") for row in profiles] == ["postgresql", "cockroachdb"], "database profiles")
    digests: dict[str, str] = {}
    for profile in profiles:
        profile_id = profile["id"]
        relative_root = profile.get("path")
        require(relative_root == f"migrations/{profile_id}", f"{profile_id}: migration path")
        require(profile.get("runtime_adapter") == "crates/trnm-persistence-pg", f"{profile_id}: adapter")
        digest, files = migration_digest(relative_root, chain)
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
    adapter_root = ROOT / "crates/trnm-persistence-pg/src"
    adapter = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(adapter_root.rglob("*.rs"))
    )
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

    chain = validated_migration_chain()
    for profile in ("postgresql", "cockroachdb"):
        sql = "\n".join(
            path.read_text(encoding="utf-8")
            for path in migration_digest(f"migrations/{profile}", chain)[1]
        )
        missing = sorted(table for table in REQUIRED_TABLES if table not in sql)
        require(not missing, f"{profile}: missing authoritative tables {missing}")
    require(
        not missing_adapter,
        "adapter missing authoritative table references " + ", ".join(missing_adapter),
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
        print(f"{profile} migration-chain sha256={digest} algorithm=ordered-path-git-blob-sha256.v1")
    print("TrillionniumGame schema authority: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
