#!/usr/bin/env python3
"""Fail-closed verifier for the PostgreSQL and CockroachDB v2 profiles."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "database/schema/v2/database-profile-contract.v2.json"


class VerificationError(RuntimeError):
    """Raised when a database profile violates the canonical contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def read_text(relative: str) -> str:
    path = ROOT / relative
    require(path.is_file(), f"missing required file: {relative}")
    value = path.read_text(encoding="utf-8")
    require(value.endswith("\n"), f"file must end with newline: {relative}")
    require("\r" not in value, f"CRLF is forbidden: {relative}")
    return value


def normalized_sql(value: str) -> str:
    without_line_comments = re.sub(r"--[^\n]*", "", value)
    without_block_comments = re.sub(r"/\*.*?\*/", "", without_line_comments, flags=re.S)
    return re.sub(r"\s+", " ", without_block_comments).strip().lower()


def create_table_body(sql: str, table: str) -> str:
    pattern = re.compile(
        rf"create\s+table\s+if\s+not\s+exists\s+{re.escape(table)}\s*\((.*?)\)\s*;",
        flags=re.I | re.S,
    )
    match = pattern.search(sql)
    require(match is not None, f"missing CREATE TABLE for {table}")
    return match.group(1)


def require_tokens(value: str, tokens: Iterable[str], context: str) -> None:
    lowered = value.lower()
    for token in tokens:
        require(token.lower() in lowered, f"{context} missing token: {token}")


def verify_migration(profile: str, relative: str, tables: list[str]) -> dict[str, int]:
    raw = read_text(relative)
    sql = normalized_sql(raw)

    forbidden_patterns = {
        "database generated UUID": r"\b(gen_random_uuid|uuid_generate_v[0-9]+|unique_rowid)\s*\(",
        "serial identifier": r"\b(bigserial|serial)\b",
        "trigger side effect": r"\bcreate\s+trigger\b",
        "listen/notify side effect": r"\b(listen|notify)\b",
    }
    for label, pattern in forbidden_patterns.items():
        require(re.search(pattern, sql, flags=re.I) is None, f"{profile}: forbidden {label}")

    for table in tables:
        create_table_body(sql, table)

    head = create_table_body(sql, "trnm_entity_heads").lower()
    receipt = create_table_body(sql, "trnm_command_receipts").lower()
    event = create_table_body(sql, "trnm_events").lower()
    outbox = create_table_body(sql, "trnm_outbox").lower()

    require_tokens(
        head,
        [
            "tenant_id",
            "entity_id",
            "revision",
            "last_sequence",
            "authority_generation",
            "state_digest",
            "primary key (tenant_id, entity_id)",
        ],
        f"{profile}: entity head",
    )
    require_tokens(
        receipt,
        [
            "command_id",
            "fingerprint",
            "committed_revision",
            "committed_state_digest",
            "first_sequence",
            "last_sequence",
            "event_count",
            "outbox_count",
            "receipt_bytes",
            "receipt_digest",
            "primary key (tenant_id, entity_id, command_id)",
        ],
        f"{profile}: command receipt",
    )
    require_tokens(
        event,
        [
            "sequence",
            "event_id",
            "command_id",
            "payload_digest",
            "payload_bytes",
            "primary key (tenant_id, entity_id, sequence)",
            "foreign key (tenant_id, entity_id, command_id)",
        ],
        f"{profile}: event",
    )
    require_tokens(
        outbox,
        [
            "intent_id",
            "command_id",
            "kind",
            "payload_digest",
            "payload_bytes",
            "attempt",
            "lease_generation",
            "state",
            "lease_owner",
            "lease_expires_at",
            "applied_receipt_digest",
            "dead_letter_reason_digest",
            "primary key (tenant_id, intent_id)",
            "foreign key (tenant_id, entity_id, command_id)",
        ],
        f"{profile}: outbox",
    )

    digest_columns = [
        "contract_digest",
        "state_digest",
        "fingerprint",
        "committed_state_digest",
        "receipt_digest",
        "payload_digest",
        "applied_receipt_digest",
        "dead_letter_reason_digest",
    ]
    for column in digest_columns:
        require(
            re.search(rf"octet_length\s*\(\s*{column}\s*\)\s*=\s*32", sql) is not None,
            f"{profile}: {column} lacks a 32-byte check",
        )

    require("event_count between 0 and 64" in sql, f"{profile}: event fanout bound missing")
    require("outbox_count between 0 and 64" in sql, f"{profile}: outbox fanout bound missing")
    require("attempt between 0 and 32" in sql, f"{profile}: outbox attempt bound missing")
    for state in ("pending", "leased", "applied", "dead_letter"):
        require(f"'{state}'" in outbox, f"{profile}: outbox state missing: {state}")

    return {
        "tables": len(tables),
        "digest_columns": len(digest_columns),
        "bounded_fanouts": 3,
    }


def verify_lease(profile: str, relative: str, strategy: str) -> dict[str, object]:
    raw = read_text(relative)
    sql = normalized_sql(raw)
    require_tokens(
        sql,
        [
            "state = 'pending'",
            "state = 'leased'",
            "lease_owner",
            "lease_generation",
            "lease_expires_at",
            "attempt",
            "returning",
        ],
        f"{profile}: lease query",
    )
    require("lease_generation + 1" in sql, f"{profile}: lease generation is not monotonic")
    require("attempt + 1" in sql, f"{profile}: attempt is not monotonic")

    if profile == "postgresql":
        require("for update skip locked" in sql, "postgresql: expected SKIP LOCKED strategy")
        require(strategy == "select_for_update_skip_locked", "postgresql: contract strategy mismatch")
    elif profile == "cockroachdb":
        require("for update skip locked" not in sql, "cockroachdb: PostgreSQL lock strategy leaked")
        require("where (outbox.tenant_id, outbox.intent_id) in" in sql, "cockroachdb: CAS candidate fence missing")
        require(
            strategy == "optimistic_compare_and_swap_with_transaction_retry",
            "cockroachdb: contract strategy mismatch",
        )
    else:
        raise VerificationError(f"unexpected profile: {profile}")

    return {"strategy": strategy, "fence_columns": 3}


def verify_introspection(profile: str, relative: str, tables: list[str]) -> int:
    sql = normalized_sql(read_text(relative))
    for table in tables:
        require(table in sql, f"{profile}: introspection omits {table}")
    require("information_schema.columns" in sql, f"{profile}: column introspection missing")
    return len(tables)


def verify() -> dict[str, object]:
    contract = json.loads(read_text(str(CONTRACT_PATH.relative_to(ROOT))))
    require(contract.get("schema") == "trillionnium.game.database-profile.v2", "wrong contract schema")
    require(contract.get("version") == 2, "wrong contract version")
    require(contract.get("digest_bytes") == 32, "digest size must be 32")
    require(contract.get("max_events_per_command") == 64, "event fanout bound mismatch")
    require(contract.get("max_outbox_intents_per_command") == 64, "outbox fanout bound mismatch")
    require(contract.get("max_outbox_attempts") == 32, "outbox attempt bound mismatch")

    tables = contract.get("tables")
    require(isinstance(tables, list) and len(tables) == 5, "canonical table inventory mismatch")
    require(len(set(tables)) == len(tables), "duplicate canonical table name")

    profiles = contract.get("profiles")
    require(isinstance(profiles, dict), "profiles must be an object")
    require(set(profiles) == {"postgresql", "cockroachdb"}, "profile set mismatch")

    report: dict[str, object] = {
        "schema": "trillionnium.game.database-profile-verification.v2",
        "contract": str(CONTRACT_PATH.relative_to(ROOT)),
        "profiles": {},
        "claims": {
            "static_contract_passed": True,
            "runtime_apply_passed": False,
            "fault_matrix_complete": False,
            "production_ready": False,
        },
    }
    profile_report = report["profiles"]
    assert isinstance(profile_report, dict)
    for name in sorted(profiles):
        value = profiles[name]
        require(value.get("transaction_isolation") == "serializable", f"{name}: isolation mismatch")
        migration = verify_migration(name, value["migration"], tables)
        lease = verify_lease(name, value["lease_query"], value["lease_strategy"])
        introspection_tables = verify_introspection(name, value["introspection"], tables)
        profile_report[name] = {
            "migration": migration,
            "lease": lease,
            "introspection_tables": introspection_tables,
            "retry_sqlstates": value["retry_sqlstates"],
        }

    return report


def main() -> int:
    try:
        report = verify()
    except (OSError, ValueError, VerificationError) as error:
        print(f"database-profile-v2 verification failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
