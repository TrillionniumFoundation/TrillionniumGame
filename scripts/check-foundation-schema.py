#!/usr/bin/env python3
from __future__ import annotations

import json
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/database/foundation-schema.v1.json"
CREATE_TABLE = re.compile(r"CREATE\s+TABLE\s+([a-z0-9_]+)", re.IGNORECASE)
CREATE_INDEX = re.compile(r"CREATE\s+INDEX\s+([a-z0-9_]+)", re.IGNORECASE)
DIGEST_ALGORITHM = "ordered-path-git-blob-sha256.v1"
UPGRADE_COLUMNS = (
    ("metadata_chain_digest", "trnm_schema_metadata", "chain_digest", "string"),
    ("metadata_digest_algorithm", "trnm_schema_metadata", "digest_algorithm", "string"),
    ("metadata_storage_writer_epoch", "trnm_schema_metadata", "storage_writer_epoch", "integer"),
    ("metadata_upgrade_source_commit", "trnm_schema_metadata", "upgrade_source_commit", "string"),
    ("storage_create_time", "trnm_storage_objects", "create_time", "timestamp"),
    ("storage_update_time", "trnm_storage_objects", "update_time", "timestamp"),
)


class SchemaError(RuntimeError):
    pass


def locked_chain(root: Path) -> dict[str, object]:
    script = Path(__file__).with_name("check-migration-lock.py")
    spec = importlib.util.spec_from_file_location("foundation_migration_lock", script)
    if spec is None or spec.loader is None:
        raise SchemaError("migration-lock checker unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        return module.validate(root)
    except (module.ValidationError, OSError, ValueError) as error:
        raise SchemaError(f"authoritative migration lock: {error}") from error


def normalize(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip()).lower()


def inspect_profile(
    profile: str,
    path: Path,
    contract: dict[str, object],
    root: Path,
) -> dict[str, object]:
    sql = path.read_text(encoding="utf-8")
    normalized = normalize(sql)
    tables = CREATE_TABLE.findall(sql)
    indexes = CREATE_INDEX.findall(sql)
    required = contract["required_tables"]
    if set(tables) != set(required):
        missing = sorted(set(required) - set(tables))
        extra = sorted(set(tables) - set(required))
        raise SchemaError(
            f"{profile}: table mismatch missing={missing} extra={extra}"
        )
    if len(tables) != len(set(tables)):
        raise SchemaError(f"{profile}: duplicate CREATE TABLE")

    expected_binary = contract["profiles"][profile]["binary_type"].lower()
    if expected_binary not in normalized:
        raise SchemaError(f"{profile}: expected binary type {expected_binary}")
    wrong_binary = "bytes" if expected_binary == "bytea" else "bytea"
    if re.search(rf"\b{wrong_binary}\b", normalized):
        raise SchemaError(
            f"{profile}: contains other profile binary type {wrong_binary}"
        )

    for pattern in (
        r"\bserial\b",
        r"\bbigserial\b",
        r"\buuid\b\s+default",
        r"default\s+now\s*\(",
        r"default\s+clock_timestamp\s*\(",
        r"\btoken\s+(text|string|bytea|bytes)\b",
        r"\baccess_token\b",
        r"\brefresh_token\b(?!s)",
        r"drop\s+table",
        r"cascade",
    ):
        if re.search(pattern, normalized):
            raise SchemaError(f"{profile}: forbidden SQL pattern {pattern}")

    for fragment in (
        "primary key (entity_id, command_id)",
        "unique (entity_id, revision)",
        "primary key (entity_id, sequence)",
        "intent_id",
        "token_digest",
        "unique check (octet_length(token_digest) = 32)",
        "primary key (collection, object_key, user_id)",
        "state between 0 and 3",
        "lease_generation",
        "authority_generation",
        "on delete restrict",
        "event_count <= 64",
        "attempt <= 32",
    ):
        if fragment not in normalized:
            raise SchemaError(f"{profile}: missing contract fragment {fragment}")

    if normalized.count("begin;") != 1 or normalized.count("commit;") != 1:
        raise SchemaError(f"{profile}: migration must use one explicit transaction")
    if "trnm_outbox_ready_idx" not in indexes:
        raise SchemaError(f"{profile}: missing outbox readiness index")

    return {
        "profile": profile,
        "path": str(path.relative_to(root)),
        "table_count": len(tables),
        "index_count": len(indexes),
        "tables": tables,
    }


def inspect_upgrade(profile: str, path: Path) -> dict[str, object]:
    sql = path.read_text(encoding="utf-8")
    markers = re.findall(r"^-- trnm:action ([a-z_]+)\s*$", sql, flags=re.MULTILINE)
    expected_markers = [row[0] for row in UPGRADE_COLUMNS]
    if markers != expected_markers:
        raise SchemaError(f"{profile}: upgrade action inventory/order differs")
    clean = re.sub(r"--[^\n]*", "", sql)
    statements = [normalize(part) for part in clean.split(";") if part.strip()]
    if profile == "postgresql":
        if not statements or statements[0] != "begin" or statements[-1] != "commit":
            raise SchemaError("postgresql: timestamp upgrade must own one transaction")
        statements = statements[1:-1]
    kinds = {
        "string": "text" if profile == "postgresql" else "string",
        "integer": "bigint" if profile == "postgresql" else "int8",
        "timestamp": "timestamptz",
    }
    expected = [f"alter table {table} add column {name} {kinds[kind]}"
                for _, table, name, kind in UPGRADE_COLUMNS]
    if statements != expected:
        raise SchemaError(f"{profile}: timestamp upgrade must append exactly six nullable columns without defaults/backfill")
    return {"action_count": len(statements), "historical_timestamps_remain_unknown": True}


def validate(root: Path = ROOT) -> dict[str, object]:
    contract = json.loads(
        (root / CONTRACT.relative_to(ROOT)).read_text(encoding="utf-8")
    )
    if contract.get("schema") != "trillionnium.foundation-schema-contract.v1":
        raise SchemaError("unexpected contract schema")
    if contract.get("schema_version") != 2 or contract.get("storage_writer_epoch") != 2:
        raise SchemaError("current schema version and storage writer epoch must be 2")
    if contract.get("migration_lock") != "migrations/MIGRATION_CHAIN.lock.json":
        raise SchemaError("foundation contract must consume the authoritative migration lock")
    if contract.get("digest_algorithm") != DIGEST_ALGORITHM:
        raise SchemaError("foundation migration digest algorithm differs from Rust runner")
    if any(contract.get("claims", {}).values()):
        raise SchemaError("foundation schema contract overclaims maturity")
    if contract.get("security", {}).get("raw_session_token_storage_allowed") is not False:
        raise SchemaError("raw session token storage must be false")
    if contract.get("security", {}).get("raw_refresh_token_storage_allowed") is not False:
        raise SchemaError("raw refresh token storage must be false")
    if (
        contract.get("rollback", {}).get("automatic_destructive_down_migration")
        is not False
    ):
        raise SchemaError("automatic destructive rollback must be false")

    chain = locked_chain(root)
    profiles = []
    for profile in ("postgresql", "cockroachdb"):
        row = chain["profiles"][profile]
        relative = Path(contract["profiles"][profile]["foundation_path"])
        if relative.as_posix() != row["ordered_paths"][0]:
            raise SchemaError(f"{profile}: immutable foundation fixture differs from chain prefix")
        result = inspect_profile(profile, root / relative, contract, root)
        result.update(inspect_upgrade(profile, root / row["ordered_paths"][1]))
        result.update({"ordered_paths": row["ordered_paths"], "chain_sha256": row["chain_sha256"],
                       "digest_algorithm": row["digest_algorithm"]})
        profiles.append(result)
    if profiles[0]["tables"] != profiles[1]["tables"]:
        raise SchemaError("logical table order differs between profiles")

    operations = (root / "docs/OPERATIONS_AND_RELEASE.md").read_text(
        encoding="utf-8"
    )
    for marker in (
        "`migrations/postgresql/` and `migrations/cockroachdb/` are the only production DDL chains",
        "Rollback must account for sessions, parties, tickets, matches, schedulers, IAP transactions, outbox effects and Rust-only schema state.",
        "Crossing an irreversible barrier requires explicit approval and evidence.",
    ):
        if marker not in operations:
            raise SchemaError(f"current operations documentation missing: {marker}")

    return {
        "status": "foundation-schema-static-contract-passed",
        "profiles": profiles,
        "rollback_authority": "docs/OPERATIONS_AND_RELEASE.md",
        "runtime_execution_verified": False,
        "database_durable": False,
        "migration_compatible": False,
        "production_ready": False,
    }


def main() -> None:
    print(json.dumps(validate(), sort_keys=True))


if __name__ == "__main__":
    main()
