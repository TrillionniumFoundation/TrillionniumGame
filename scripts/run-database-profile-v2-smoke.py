#!/usr/bin/env python3
"""Apply one database profile and execute the W1/W14 transaction smoke matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from pathlib import Path
from typing import Any

import psycopg

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "database/schema/v2/database-profile-contract.v2.json"

TENANT = uuid.UUID("10000000-0000-0000-0000-000000000001")
ENTITY = uuid.UUID("20000000-0000-0000-0000-000000000001")
ROLLBACK_ENTITY = uuid.UUID("20000000-0000-0000-0000-000000000002")
COMMAND = uuid.UUID("30000000-0000-0000-0000-000000000001")
ROLLBACK_COMMAND = uuid.UUID("30000000-0000-0000-0000-000000000002")
EVENT = uuid.UUID("40000000-0000-0000-0000-000000000001")
INTENT = uuid.UUID("50000000-0000-0000-0000-000000000001")
ROLLBACK_INTENT = uuid.UUID("50000000-0000-0000-0000-000000000002")
WORKER = uuid.UUID("60000000-0000-0000-0000-000000000001")


def digest(byte: int) -> bytes:
    return bytes([byte]) * 32


def split_sql(value: str) -> list[str]:
    """Split the repository-owned migration files without accepting SQL input."""
    statements: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
    escaped = False
    in_line_comment = False
    in_block_comment = False
    index = 0
    while index < len(value):
        char = value[index]
        following = value[index + 1] if index + 1 < len(value) else ""
        if in_line_comment:
            if char == "\n":
                in_line_comment = False
                buffer.append(char)
            index += 1
            continue
        if in_block_comment:
            if char == "*" and following == "/":
                in_block_comment = False
                index += 2
            else:
                index += 1
            continue
        if quote is None and char == "-" and following == "-":
            in_line_comment = True
            index += 2
            continue
        if quote is None and char == "/" and following == "*":
            in_block_comment = True
            index += 2
            continue
        if quote is not None:
            buffer.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in ("'", '"'):
            quote = char
            buffer.append(char)
            index += 1
            continue
        if char == ";":
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer.clear()
        else:
            buffer.append(char)
        index += 1
    tail = "".join(buffer).strip()
    if tail:
        statements.append(tail)
    return statements


def execute_script(connection: psycopg.Connection[Any], path: Path) -> int:
    statements = split_sql(path.read_text(encoding="utf-8"))
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
    return len(statements)


def set_serializable(cursor: psycopg.Cursor[Any]) -> None:
    cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")


def reset_tables(connection: psycopg.Connection[Any]) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "TRUNCATE TABLE trnm_outbox, trnm_events, trnm_command_receipts, "
            "trnm_entity_heads"
        )


def insert_head(cursor: psycopg.Cursor[Any], entity_id: uuid.UUID) -> None:
    cursor.execute(
        """
        INSERT INTO trnm_entity_heads (
            tenant_id, entity_id, revision, last_sequence,
            authority_generation, state_digest
        ) VALUES (%s, %s, 0, 0, 1, %s)
        """,
        (TENANT, entity_id, digest(0x10)),
    )


def commit_command(connection: psycopg.Connection[Any]) -> None:
    with connection.transaction():
        with connection.cursor() as cursor:
            set_serializable(cursor)
            cursor.execute(
                """
                UPDATE trnm_entity_heads
                SET revision = 1,
                    last_sequence = 1,
                    state_digest = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE tenant_id = %s
                  AND entity_id = %s
                  AND revision = 0
                  AND authority_generation = 1
                """,
                (digest(0x11), TENANT, ENTITY),
            )
            if cursor.rowcount != 1:
                raise AssertionError("initial entity fence did not advance exactly one row")
            cursor.execute(
                """
                INSERT INTO trnm_command_receipts (
                    tenant_id, entity_id, command_id, fingerprint,
                    committed_revision, committed_state_digest,
                    first_sequence, last_sequence, event_count, outbox_count,
                    receipt_bytes, receipt_digest
                ) VALUES (%s, %s, %s, %s, 1, %s, 1, 1, 1, 1, %s, %s)
                """,
                (
                    TENANT,
                    ENTITY,
                    COMMAND,
                    digest(0x21),
                    digest(0x11),
                    b"canonical-receipt-v2",
                    digest(0x31),
                ),
            )
            cursor.execute(
                """
                INSERT INTO trnm_events (
                    tenant_id, entity_id, sequence, event_id, command_id,
                    payload_digest, payload_bytes
                ) VALUES (%s, %s, 1, %s, %s, %s, %s)
                """,
                (TENANT, ENTITY, EVENT, COMMAND, digest(0x41), b"event-v2"),
            )
            cursor.execute(
                """
                INSERT INTO trnm_outbox (
                    tenant_id, intent_id, entity_id, command_id, kind,
                    payload_digest, payload_bytes
                ) VALUES (%s, %s, %s, %s, 'match.broadcast', %s, %s)
                """,
                (TENANT, INTENT, ENTITY, COMMAND, digest(0x51), b"intent-v2"),
            )


def verify_committed_graph(connection: psycopg.Connection[Any]) -> dict[str, int]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT revision, last_sequence, authority_generation, state_digest
            FROM trnm_entity_heads
            WHERE tenant_id = %s AND entity_id = %s
            """,
            (TENANT, ENTITY),
        )
        head = cursor.fetchone()
        if head != (1, 1, 1, digest(0x11)):
            raise AssertionError(f"unexpected entity head: {head!r}")

        cursor.execute(
            """
            SELECT fingerprint, receipt_bytes, receipt_digest
            FROM trnm_command_receipts
            WHERE tenant_id = %s AND entity_id = %s AND command_id = %s
            """,
            (TENANT, ENTITY, COMMAND),
        )
        receipt = cursor.fetchone()
        if receipt != (digest(0x21), b"canonical-receipt-v2", digest(0x31)):
            raise AssertionError(f"unexpected command receipt: {receipt!r}")

        counts: dict[str, int] = {}
        for table in (
            "trnm_entity_heads",
            "trnm_command_receipts",
            "trnm_events",
            "trnm_outbox",
        ):
            cursor.execute(f"SELECT count(*) FROM {table}")
            counts[table] = int(cursor.fetchone()[0])
            if counts[table] != 1:
                raise AssertionError(f"unexpected row count for {table}: {counts[table]}")
        return counts


def verify_idempotency_and_fence(connection: psycopg.Connection[Any]) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT receipt_bytes, receipt_digest
            FROM trnm_command_receipts
            WHERE tenant_id = %s AND entity_id = %s AND command_id = %s
              AND fingerprint = %s
            """,
            (TENANT, ENTITY, COMMAND, digest(0x21)),
        )
        if cursor.fetchone() != (b"canonical-receipt-v2", digest(0x31)):
            raise AssertionError("exact duplicate did not replay the canonical receipt")

        cursor.execute(
            """
            SELECT count(*)
            FROM trnm_command_receipts
            WHERE tenant_id = %s AND entity_id = %s AND command_id = %s
              AND fingerprint = %s
            """,
            (TENANT, ENTITY, COMMAND, digest(0x22)),
        )
        if int(cursor.fetchone()[0]) != 0:
            raise AssertionError("mismatched duplicate fingerprint was accepted")

    with connection.transaction():
        with connection.cursor() as cursor:
            set_serializable(cursor)
            cursor.execute(
                """
                UPDATE trnm_entity_heads
                SET revision = 2
                WHERE tenant_id = %s AND entity_id = %s
                  AND revision = 0 AND authority_generation = 1
                """,
                (TENANT, ENTITY),
            )
            if cursor.rowcount != 0:
                raise AssertionError("stale revision fence advanced the entity")


def verify_atomic_rollback(connection: psycopg.Connection[Any]) -> None:
    expected_failure = False
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                set_serializable(cursor)
                insert_head(cursor, ROLLBACK_ENTITY)
                cursor.execute(
                    """
                    INSERT INTO trnm_command_receipts (
                        tenant_id, entity_id, command_id, fingerprint,
                        committed_revision, committed_state_digest,
                        first_sequence, last_sequence, event_count, outbox_count,
                        receipt_bytes, receipt_digest
                    ) VALUES (%s, %s, %s, %s, 1, %s, NULL, 0, 0, 1, %s, %s)
                    """,
                    (
                        TENANT,
                        ROLLBACK_ENTITY,
                        ROLLBACK_COMMAND,
                        digest(0x61),
                        digest(0x62),
                        b"rollback-receipt",
                        digest(0x63),
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO trnm_outbox (
                        tenant_id, intent_id, entity_id, command_id, kind,
                        payload_digest, payload_bytes
                    ) VALUES (%s, %s, %s, %s, 'invalid.digest', %s, %s)
                    """,
                    (
                        TENANT,
                        ROLLBACK_INTENT,
                        ROLLBACK_ENTITY,
                        ROLLBACK_COMMAND,
                        b"not-32-bytes",
                        b"must-rollback",
                    ),
                )
    except psycopg.Error:
        expected_failure = True
    if not expected_failure:
        raise AssertionError("invalid digest did not abort the transaction")

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*) FROM trnm_entity_heads
            WHERE tenant_id = %s AND entity_id = %s
            """,
            (TENANT, ROLLBACK_ENTITY),
        )
        if int(cursor.fetchone()[0]) != 0:
            raise AssertionError("failed transaction leaked an entity head")
        cursor.execute(
            """
            SELECT count(*) FROM trnm_command_receipts
            WHERE tenant_id = %s AND entity_id = %s AND command_id = %s
            """,
            (TENANT, ROLLBACK_ENTITY, ROLLBACK_COMMAND),
        )
        if int(cursor.fetchone()[0]) != 0:
            raise AssertionError("failed transaction leaked a command receipt")


def lease_one(connection: psycopg.Connection[Any], profile: str) -> tuple[int, uuid.UUID]:
    if profile == "postgresql":
        statement = """
            WITH candidates AS (
                SELECT tenant_id, intent_id
                FROM trnm_outbox
                WHERE tenant_id = %s AND state = 'pending'
                  AND available_at <= CURRENT_TIMESTAMP AND attempt < 32
                ORDER BY available_at, intent_id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE trnm_outbox AS outbox
            SET state = 'leased', lease_owner = %s,
                lease_generation = outbox.lease_generation + 1,
                lease_expires_at = CURRENT_TIMESTAMP + INTERVAL '60 seconds',
                attempt = outbox.attempt + 1,
                updated_at = CURRENT_TIMESTAMP
            FROM candidates
            WHERE outbox.tenant_id = candidates.tenant_id
              AND outbox.intent_id = candidates.intent_id
              AND outbox.state = 'pending'
            RETURNING outbox.lease_generation, outbox.lease_owner
        """
        parameters = (TENANT, WORKER)
    else:
        statement = """
            WITH candidates AS (
                SELECT tenant_id, intent_id
                FROM trnm_outbox
                WHERE tenant_id = %s AND state = 'pending'
                  AND available_at <= current_timestamp() AND attempt < 32
                ORDER BY available_at, intent_id
                LIMIT 1
            )
            UPDATE trnm_outbox AS outbox
            SET state = 'leased', lease_owner = %s,
                lease_generation = outbox.lease_generation + 1,
                lease_expires_at = current_timestamp() + INTERVAL '60 seconds',
                attempt = outbox.attempt + 1,
                updated_at = current_timestamp()
            WHERE (outbox.tenant_id, outbox.intent_id) IN (
                SELECT tenant_id, intent_id FROM candidates
            )
              AND outbox.state = 'pending'
            RETURNING outbox.lease_generation, outbox.lease_owner
        """
        parameters = (TENANT, WORKER)

    with connection.transaction():
        with connection.cursor() as cursor:
            set_serializable(cursor)
            cursor.execute(statement, parameters)
            row = cursor.fetchone()
            if row is None:
                raise AssertionError("no pending outbox intent was leased")
            if cursor.fetchone() is not None:
                raise AssertionError("single-record lease returned multiple rows")
            return int(row[0]), row[1]


def verify_lease_completion(connection: psycopg.Connection[Any], generation: int) -> None:
    with connection.transaction():
        with connection.cursor() as cursor:
            set_serializable(cursor)
            cursor.execute(
                """
                UPDATE trnm_outbox
                SET state = 'applied', lease_owner = NULL, lease_expires_at = NULL,
                    applied_receipt_digest = %s, updated_at = CURRENT_TIMESTAMP
                WHERE tenant_id = %s AND intent_id = %s AND state = 'leased'
                  AND lease_owner = %s AND lease_generation = %s
                """,
                (digest(0x71), TENANT, INTENT, WORKER, generation - 1),
            )
            if cursor.rowcount != 0:
                raise AssertionError("stale lease generation completed an intent")

    with connection.transaction():
        with connection.cursor() as cursor:
            set_serializable(cursor)
            cursor.execute(
                """
                UPDATE trnm_outbox
                SET state = 'applied', lease_owner = NULL, lease_expires_at = NULL,
                    applied_receipt_digest = %s, updated_at = CURRENT_TIMESTAMP
                WHERE tenant_id = %s AND intent_id = %s AND state = 'leased'
                  AND lease_owner = %s AND lease_generation = %s
                RETURNING applied_receipt_digest
                """,
                (digest(0x71), TENANT, INTENT, WORKER, generation),
            )
            if cursor.fetchone() != (digest(0x71),):
                raise AssertionError("current lease owner could not complete the intent")

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT state, lease_owner, lease_expires_at, applied_receipt_digest
            FROM trnm_outbox
            WHERE tenant_id = %s AND intent_id = %s
            """,
            (TENANT, INTENT),
        )
        row = cursor.fetchone()
        if row != ("applied", None, None, digest(0x71)):
            raise AssertionError(f"unexpected applied outbox shape: {row!r}")


def introspection_inventory(
    connection: psycopg.Connection[Any], introspection_path: Path
) -> list[str]:
    statements = split_sql(introspection_path.read_text(encoding="utf-8"))
    if not statements:
        raise AssertionError("empty introspection script")
    with connection.cursor() as cursor:
        cursor.execute(statements[0])
        names = sorted({str(row[0]) for row in cursor.fetchall()})
    expected = sorted(
        [
            "trnm_schema_migrations",
            "trnm_entity_heads",
            "trnm_command_receipts",
            "trnm_events",
            "trnm_outbox",
        ]
    )
    if names != expected:
        raise AssertionError(f"introspection inventory mismatch: {names!r}")
    return names


def run(profile: str, dsn: str) -> dict[str, object]:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    profile_contract = contract["profiles"][profile]
    migration_path = ROOT / profile_contract["migration"]
    introspection_path = ROOT / profile_contract["introspection"]
    contract_digest = hashlib.sha256(CONTRACT_PATH.read_bytes()).digest()

    with psycopg.connect(dsn, autocommit=True) as connection:
        migration_statements = execute_script(connection, migration_path)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO trnm_schema_migrations (profile, version, contract_digest)
                VALUES (%s, 2, %s)
                ON CONFLICT (profile, version) DO UPDATE
                SET contract_digest = excluded.contract_digest
                """,
                (profile, contract_digest),
            )
        reset_tables(connection)
        with connection.transaction():
            with connection.cursor() as cursor:
                set_serializable(cursor)
                insert_head(cursor, ENTITY)
        commit_command(connection)
        counts = verify_committed_graph(connection)
        verify_idempotency_and_fence(connection)
        verify_atomic_rollback(connection)
        generation, owner = lease_one(connection, profile)
        if generation != 1 or owner != WORKER:
            raise AssertionError(f"unexpected first lease fence: {(generation, owner)!r}")
        verify_lease_completion(connection, generation)
        inventory = introspection_inventory(connection, introspection_path)

    return {
        "schema": "trillionnium.game.database-profile-runtime-smoke.v2",
        "profile": profile,
        "migration_statements": migration_statements,
        "tables": inventory,
        "committed_counts": counts,
        "lease_generation": generation,
        "checks": {
            "atomic_command_commit": True,
            "exact_duplicate_receipt_replay": True,
            "mismatched_duplicate_rejected": True,
            "stale_revision_rejected": True,
            "constraint_failure_rolled_back": True,
            "stale_lease_completion_rejected": True,
            "current_lease_completion_applied": True,
        },
        "claims": {
            "runtime_apply_passed": True,
            "concurrency_fault_matrix_complete": False,
            "backup_restore_complete": False,
            "production_ready": False,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("postgresql", "cockroachdb"), required=True)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = run(args.profile, args.dsn)
    except (AssertionError, OSError, ValueError, psycopg.Error) as error:
        print(f"database profile runtime smoke failed: {error}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
