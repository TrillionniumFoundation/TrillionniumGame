#!/usr/bin/env python3
"""Prepare, recover and inspect the database v2 durable crash scenario."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

import psycopg

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "database/schema/v2/database-profile-contract.v2.json"

TENANT = uuid.UUID("81000000-0000-0000-0000-000000000001")
ENTITY = uuid.UUID("82000000-0000-0000-0000-000000000001")
COMMAND = uuid.UUID("83000000-0000-0000-0000-000000000001")
EVENT = uuid.UUID("84000000-0000-0000-0000-000000000001")
INTENT = uuid.UUID("85000000-0000-0000-0000-000000000001")
WORKER_A = uuid.UUID("86000000-0000-0000-0000-000000000001")
WORKER_B = uuid.UUID("86000000-0000-0000-0000-000000000002")


def digest(byte: int) -> bytes:
    return bytes([byte]) * 32


def normalize_bytes(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, memoryview):
        return value.tobytes()
    raise AssertionError(f"expected bytes-compatible value, received {type(value)!r}")


def split_sql(value: str) -> list[str]:
    statements: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
    in_line_comment = False
    in_block_comment = False
    index = 0
    while index < len(value):
        byte = value[index]
        following = value[index + 1] if index + 1 < len(value) else ""
        if in_line_comment:
            if byte == "\n":
                in_line_comment = False
                buffer.append(byte)
            index += 1
            continue
        if in_block_comment:
            if byte == "*" and following == "/":
                in_block_comment = False
                index += 2
            else:
                index += 1
            continue
        if quote is None and byte == "-" and following == "-":
            in_line_comment = True
            index += 2
            continue
        if quote is None and byte == "/" and following == "*":
            in_block_comment = True
            index += 2
            continue
        if quote is not None:
            buffer.append(byte)
            if byte == quote:
                if following == quote:
                    buffer.append(following)
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if byte in ("'", '"'):
            quote = byte
            buffer.append(byte)
            index += 1
            continue
        if byte == ";":
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer.clear()
        else:
            buffer.append(byte)
        index += 1
    tail = "".join(buffer).strip()
    if tail:
        statements.append(tail)
    return statements


def profile_contract(profile: str) -> dict[str, object]:
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))["profiles"][profile]
    if not isinstance(value, dict):
        raise ValueError("profile contract is not an object")
    return value


def apply_migration(
    connection: psycopg.Connection[Any], profile: dict[str, object]
) -> int:
    migration = ROOT / str(profile["migration"])
    statements = split_sql(migration.read_text(encoding="utf-8"))
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
    return len(statements)


def clear_graph(connection: psycopg.Connection[Any]) -> None:
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM trnm_outbox")
            cursor.execute("DELETE FROM trnm_events")
            cursor.execute("DELETE FROM trnm_command_receipts")
            cursor.execute("DELETE FROM trnm_entity_heads")


def lease_sql(profile: str, duration_seconds: int) -> str:
    if profile == "postgresql":
        return f"""
            WITH candidate AS (
                SELECT tenant_id, intent_id
                FROM trnm_outbox
                WHERE tenant_id = %s AND intent_id = %s
                  AND state = 'pending' AND attempt < 32
                FOR UPDATE SKIP LOCKED
            )
            UPDATE trnm_outbox AS outbox
            SET state = 'leased', lease_owner = %s,
                lease_generation = outbox.lease_generation + 1,
                lease_expires_at = CURRENT_TIMESTAMP + INTERVAL '{duration_seconds} seconds',
                attempt = outbox.attempt + 1,
                updated_at = CURRENT_TIMESTAMP
            FROM candidate
            WHERE outbox.tenant_id = candidate.tenant_id
              AND outbox.intent_id = candidate.intent_id
              AND outbox.state = 'pending'
            RETURNING outbox.lease_generation
        """
    return f"""
        WITH candidate AS (
            SELECT tenant_id, intent_id
            FROM trnm_outbox
            WHERE tenant_id = %s AND intent_id = %s
              AND state = 'pending' AND attempt < 32
        )
        UPDATE trnm_outbox AS outbox
        SET state = 'leased', lease_owner = %s,
            lease_generation = outbox.lease_generation + 1,
            lease_expires_at = CURRENT_TIMESTAMP + INTERVAL '{duration_seconds} seconds',
            attempt = outbox.attempt + 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE (outbox.tenant_id, outbox.intent_id) IN (
            SELECT tenant_id, intent_id FROM candidate
        )
          AND outbox.state = 'pending'
        RETURNING outbox.lease_generation
    """


def prepare(profile_name: str, dsn: str, lease_seconds: int) -> dict[str, object]:
    profile = profile_contract(profile_name)
    with psycopg.connect(dsn, autocommit=True) as connection:
        statements = apply_migration(connection, profile)
        clear_graph(connection)
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                cursor.execute(
                    """
                    INSERT INTO trnm_entity_heads (
                        tenant_id, entity_id, revision, last_sequence,
                        authority_generation, state_digest
                    ) VALUES (%s, %s, 1, 1, 1, %s)
                    """,
                    (TENANT, ENTITY, digest(0x11)),
                )
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
                        b"durable-receipt-v2",
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
                    (TENANT, ENTITY, EVENT, COMMAND, digest(0x41), b"durable-event-v2"),
                )
                cursor.execute(
                    """
                    INSERT INTO trnm_outbox (
                        tenant_id, intent_id, entity_id, command_id, kind,
                        payload_digest, payload_bytes
                    ) VALUES (%s, %s, %s, %s, 'durable.crash.probe', %s, %s)
                    """,
                    (TENANT, INTENT, ENTITY, COMMAND, digest(0x51), b"durable-intent-v2"),
                )

        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                cursor.execute(
                    lease_sql(profile_name, lease_seconds),
                    (TENANT, INTENT, WORKER_A),
                )
                row = cursor.fetchone()
                if row != (1,):
                    raise AssertionError(f"first durable lease did not bind generation 1: {row!r}")

        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_catalog.current_timestamp")
            database_time = str(cursor.fetchone()[0])

    return {
        "schema": "trillionnium.game.database-crash-prepare.v2",
        "profile": profile_name,
        "migration_statements": statements,
        "lease_seconds": lease_seconds,
        "database_time": database_time,
        "expected": {
            "revision": 1,
            "last_sequence": 1,
            "lease_generation": 1,
            "receipt_digest_hex": digest(0x31).hex(),
        },
        "claims": {
            "commit_observed_before_process_kill": True,
            "restart_recovery_verified": False,
            "production_ready": False,
        },
    }


def verify_durable_graph(cursor: psycopg.Cursor[Any]) -> None:
    cursor.execute(
        """
        SELECT revision, last_sequence, authority_generation, state_digest
        FROM trnm_entity_heads
        WHERE tenant_id = %s AND entity_id = %s
        """,
        (TENANT, ENTITY),
    )
    head = cursor.fetchone()
    if head is None or head[:3] != (1, 1, 1) or normalize_bytes(head[3]) != digest(0x11):
        raise AssertionError(f"durable entity head mismatch: {head!r}")

    cursor.execute(
        """
        SELECT fingerprint, receipt_bytes, receipt_digest
        FROM trnm_command_receipts
        WHERE tenant_id = %s AND entity_id = %s AND command_id = %s
        """,
        (TENANT, ENTITY, COMMAND),
    )
    receipt = cursor.fetchone()
    if receipt is None:
        raise AssertionError("durable command receipt is missing")
    if (
        normalize_bytes(receipt[0]) != digest(0x21)
        or normalize_bytes(receipt[1]) != b"durable-receipt-v2"
        or normalize_bytes(receipt[2]) != digest(0x31)
    ):
        raise AssertionError(f"durable command receipt mismatch: {receipt!r}")

    cursor.execute(
        """
        SELECT count(*) FROM trnm_events
        WHERE tenant_id = %s AND entity_id = %s AND command_id = %s
        """,
        (TENANT, ENTITY, COMMAND),
    )
    if int(cursor.fetchone()[0]) != 1:
        raise AssertionError("durable event count is not one")
    cursor.execute(
        """
        SELECT count(*) FROM trnm_outbox
        WHERE tenant_id = %s AND intent_id = %s AND command_id = %s
        """,
        (TENANT, INTENT, COMMAND),
    )
    if int(cursor.fetchone()[0]) != 1:
        raise AssertionError("durable outbox count is not one")


def recover(profile_name: str, dsn: str) -> dict[str, object]:
    with psycopg.connect(dsn, autocommit=True) as connection:
        with connection.cursor() as cursor:
            verify_durable_graph(cursor)
            cursor.execute(
                """
                SELECT receipt_bytes, receipt_digest
                FROM trnm_command_receipts
                WHERE tenant_id = %s AND entity_id = %s AND command_id = %s
                  AND fingerprint = %s
                """,
                (TENANT, ENTITY, COMMAND, digest(0x21)),
            )
            replay = cursor.fetchone()
            if replay is None or normalize_bytes(replay[0]) != b"durable-receipt-v2":
                raise AssertionError("exact duplicate receipt replay failed after restart")
            if normalize_bytes(replay[1]) != digest(0x31):
                raise AssertionError("exact duplicate receipt digest drifted after restart")
            cursor.execute(
                """
                SELECT count(*) FROM trnm_command_receipts
                WHERE tenant_id = %s AND entity_id = %s AND command_id = %s
                  AND fingerprint = %s
                """,
                (TENANT, ENTITY, COMMAND, digest(0x22)),
            )
            if int(cursor.fetchone()[0]) != 0:
                raise AssertionError("mismatched duplicate fingerprint matched after restart")

        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                cursor.execute(
                    """
                    UPDATE trnm_outbox
                    SET state = 'pending', lease_owner = NULL,
                        lease_expires_at = NULL,
                        available_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE tenant_id = %s AND intent_id = %s
                      AND state = 'leased'
                      AND lease_expires_at <= CURRENT_TIMESTAMP
                    """,
                    (TENANT, INTENT),
                )
                if cursor.rowcount != 1:
                    raise AssertionError("expired lease was not recovered after restart")

        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                cursor.execute(
                    lease_sql(profile_name, 60),
                    (TENANT, INTENT, WORKER_B),
                )
                row = cursor.fetchone()
                if row != (2,):
                    raise AssertionError(f"recovered lease did not advance generation: {row!r}")

        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                cursor.execute(
                    """
                    UPDATE trnm_outbox
                    SET state = 'applied', lease_owner = NULL,
                        lease_expires_at = NULL,
                        applied_receipt_digest = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE tenant_id = %s AND intent_id = %s
                      AND state = 'leased' AND lease_owner = %s
                      AND lease_generation = 1
                    """,
                    (digest(0x61), TENANT, INTENT, WORKER_A),
                )
                if cursor.rowcount != 0:
                    raise AssertionError("pre-crash worker completed a generation-2 lease")

        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                cursor.execute(
                    """
                    UPDATE trnm_outbox
                    SET state = 'applied', lease_owner = NULL,
                        lease_expires_at = NULL,
                        applied_receipt_digest = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE tenant_id = %s AND intent_id = %s
                      AND state = 'leased' AND lease_owner = %s
                      AND lease_generation = 2
                    RETURNING applied_receipt_digest
                    """,
                    (digest(0x62), TENANT, INTENT, WORKER_B),
                )
                row = cursor.fetchone()
                if row is None or normalize_bytes(row[0]) != digest(0x62):
                    raise AssertionError("post-restart worker could not complete generation 2")

        with connection.cursor() as cursor:
            verify_durable_graph(cursor)
            cursor.execute(
                """
                SELECT state, lease_generation, attempt, applied_receipt_digest
                FROM trnm_outbox
                WHERE tenant_id = %s AND intent_id = %s
                """,
                (TENANT, INTENT),
            )
            row = cursor.fetchone()
            if (
                row is None
                or row[0] != "applied"
                or int(row[1]) != 2
                or int(row[2]) != 2
                or normalize_bytes(row[3]) != digest(0x62)
            ):
                raise AssertionError(f"final recovered outbox shape mismatch: {row!r}")

    return {
        "schema": "trillionnium.game.database-crash-recovery.v2",
        "profile": profile_name,
        "checks": {
            "entity_head_durable": True,
            "command_receipt_durable": True,
            "event_durable": True,
            "outbox_durable": True,
            "exact_duplicate_replay_after_restart": True,
            "mismatched_duplicate_rejected_after_restart": True,
            "expired_lease_recovered": True,
            "lease_generation_advanced": True,
            "pre_crash_worker_rejected": True,
            "post_restart_worker_applied": True,
        },
        "invariants": {
            "acknowledged_command_loss": 0,
            "duplicate_visible_effect": 0,
            "stale_authority_write": 0,
            "partial_command_event_outbox_commit": 0,
            "stale_outbox_completion": 0,
        },
        "claims": {
            "single_node_process_crash_restart_complete": True,
            "primary_or_leaseholder_failover_complete": False,
            "network_partition_matrix_complete": False,
            "production_ready": False,
        },
    }


def inspect(profile_name: str, dsn: str) -> dict[str, object]:
    with psycopg.connect(dsn, autocommit=True) as connection:
        with connection.cursor() as cursor:
            verify_durable_graph(cursor)
            cursor.execute(
                """
                SELECT state, lease_generation, attempt, applied_receipt_digest
                FROM trnm_outbox
                WHERE tenant_id = %s AND intent_id = %s
                """,
                (TENANT, INTENT),
            )
            row = cursor.fetchone()
            if (
                row is None
                or row[0] != "applied"
                or int(row[1]) != 2
                or int(row[2]) != 2
                or normalize_bytes(row[3]) != digest(0x62)
            ):
                raise AssertionError(f"restored outbox state mismatch: {row!r}")
    return {
        "schema": "trillionnium.game.database-restored-inspection.v2",
        "profile": profile_name,
        "checks": {
            "entity_head_present": True,
            "receipt_present": True,
            "event_present": True,
            "applied_outbox_present": True,
            "lease_generation_preserved": True,
        },
        "claims": {
            "logical_snapshot_restore_readback": True,
            "point_in_time_recovery_complete": False,
            "production_ready": False,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "recover", "inspect"))
    parser.add_argument("--profile", choices=("postgresql", "cockroachdb"), required=True)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--lease-seconds", type=int, default=5)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.mode == "prepare":
            if not 1 <= args.lease_seconds <= 300:
                raise ValueError("lease seconds must be between 1 and 300")
            report = prepare(args.profile, args.dsn, args.lease_seconds)
        elif args.mode == "recover":
            report = recover(args.profile, args.dsn)
        else:
            report = inspect(args.profile, args.dsn)
    except (AssertionError, OSError, ValueError, psycopg.Error) as error:
        print(f"database crash recovery probe failed: {error}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n"
    args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
