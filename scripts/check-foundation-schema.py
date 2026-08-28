#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/database/foundation-schema.v1.json"
CREATE_TABLE = re.compile(r"CREATE\s+TABLE\s+([a-z0-9_]+)", re.IGNORECASE)
CREATE_INDEX = re.compile(r"CREATE\s+INDEX\s+([a-z0-9_]+)", re.IGNORECASE)


class SchemaError(RuntimeError):
    pass


def normalize(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip()).lower()


def inspect_profile(profile: str, path: Path, contract: dict[str, object], root: Path) -> dict[str, object]:
    sql = path.read_text(encoding="utf-8")
    normalized = normalize(sql)
    tables = CREATE_TABLE.findall(sql)
    indexes = CREATE_INDEX.findall(sql)
    required = contract["required_tables"]
    if set(tables) != set(required):
        missing = sorted(set(required) - set(tables))
        extra = sorted(set(tables) - set(required))
        raise SchemaError(f"{profile}: table mismatch missing={missing} extra={extra}")
    if len(tables) != len(set(tables)):
        raise SchemaError(f"{profile}: duplicate CREATE TABLE")

    expected_binary = contract["profiles"][profile]["binary_type"].lower()
    if expected_binary not in normalized:
        raise SchemaError(f"{profile}: expected binary type {expected_binary}")
    wrong_binary = "bytes" if expected_binary == "bytea" else "bytea"
    if re.search(rf"\b{wrong_binary}\b", normalized):
        raise SchemaError(f"{profile}: contains other profile binary type {wrong_binary}")

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


def validate(root: Path = ROOT) -> dict[str, object]:
    contract = json.loads((root / CONTRACT.relative_to(ROOT)).read_text(encoding="utf-8"))
    if contract.get("schema") != "trillionnium.foundation-schema-contract.v1":
        raise SchemaError("unexpected contract schema")
    if any(contract.get("claims", {}).values()):
        raise SchemaError("foundation schema contract overclaims maturity")
    if contract.get("security", {}).get("raw_session_token_storage_allowed") is not False:
        raise SchemaError("raw session token storage must be false")
    if contract.get("security", {}).get("raw_refresh_token_storage_allowed") is not False:
        raise SchemaError("raw refresh token storage must be false")
    if contract.get("rollback", {}).get("automatic_destructive_down_migration") is not False:
        raise SchemaError("automatic destructive rollback must be false")

    profiles = []
    for profile in ("postgresql", "cockroachdb"):
        relative = Path(contract["profiles"][profile]["path"])
        profiles.append(inspect_profile(profile, root / relative, contract, root))
    if profiles[0]["tables"] != profiles[1]["tables"]:
        raise SchemaError("logical table order differs between profiles")

    rollback = (root / "docs/development/FOUNDATION_SCHEMA_ROLLBACK.md").read_text(encoding="utf-8")
    if "drop table` is not an accepted" not in rollback.lower():
        raise SchemaError("rollback barrier is missing")

    return {
        "status": "foundation-schema-static-contract-passed",
        "profiles": profiles,
        "runtime_execution_verified": False,
        "database_durable": False,
        "migration_compatible": False,
        "production_ready": False,
    }


def main() -> None:
    print(json.dumps(validate(), sort_keys=True))


if __name__ == "__main__":
    main()
