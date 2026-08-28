#!/usr/bin/env python3
"""Execute the W1/W14 concurrent transaction and outbox fence matrix.

This runner is intentionally profile-aware. PostgreSQL uses SKIP LOCKED for
outbox leasing; CockroachDB uses an optimistic candidate/CAS transaction and
restarts the whole SERIALIZABLE transaction on SQLSTATE 40001.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import json
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, TypeVar

import psycopg

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "database/schema/v2/database-profile-contract.v2.json"

TENANT = uuid.UUID("71000000-0000-0000-0000-000000000001")
ENTITY = uuid.UUID("72000000-0000-0000-0000-000000000001")
WORKER_A = uuid.UUID("73000000-0000-0000-0000-000000000001")
WORKER_B = uuid.UUID("73000000-0000-0000-0000-000000000002")

T = TypeVar("T")


class DomainConflict(RuntimeError):
    """A non-retryable authority or idempotency conflict."""


class SimulatedPostCommitAckLoss(RuntimeError):
    """The database committed, but the application response was discarded."""


@dataclasses.dataclass(frozen=True)
class CommandMaterial:
    command_id: uuid.UUID
    event_id: uuid.UUID
    intent_id: uuid.UUID
    fingerprint: bytes
    state_digest: bytes
    event_digest: bytes
    intent_digest: bytes
    receipt_digest: bytes
    receipt_bytes: bytes
    expected_revision: int


@dataclasses.dataclass(frozen=True)
class CommandResult:
    disposition: str
    receipt_bytes: bytes
    receipt_digest: bytes
    retries: int


def digest(byte: int) -> bytes:
    return bytes([byte]) * 32


def split_sql(value: str) -> list[str]:
    statements: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
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
            if char == quote:
                if following == quote:
                    buffer.append(following)
                    index += 2
                    continue
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


def apply_migration(connection: psycopg.Connection[Any], path: Path) -> int:
    statements = split_sql(path.read_text(encoding="utf-8"))
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


def seed_entity(connection: psycopg.Connection[Any]) -> None:
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            cursor.execute(
                """
                INSERT INTO trnm_entity_heads (
                    tenant_id, entity_id, revision, last_sequence,
                    authority_generation, state_digest
                ) VALUES (%s, %s, 0, 0, 1, %s)
                """,
                (TENANT, ENTITY, digest(0x01)),
            )


def normalize_bytes(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, memoryview):
        return value.tobytes()
    raise AssertionError(f"expected bytes-compatible value, received {type(value)!r}")


def fetch_receipt(
    connection: psycopg.Connection[Any], material: CommandMaterial
) -> tuple[bytes, bytes, bytes] | None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT fingerprint, receipt_bytes, receipt_digest
            FROM trnm_command_receipts
            WHERE tenant_id = %s AND entity_id = %s AND command_id = %s
            """,
            (TENANT, ENTITY, material.command_id),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return (
        normalize_bytes(row[0]),
        normalize_bytes(row[1]),
        normalize_bytes(row[2]),
    )


def classify_duplicate(
    connection: psycopg.Connection[Any], material: CommandMaterial, retries: int
) -> CommandResult | None:
    stored = fetch_receipt(connection, material)
    if stored is None:
        return None
    fingerprint, receipt_bytes, receipt_digest = stored
    if fingerprint != material.fingerprint:
        raise DomainConflict("idempotency_fingerprint_conflict")
    if receipt_bytes != material.receipt_bytes or receipt_digest != material.receipt_digest:
        raise AssertionError("stored exact duplicate receipt drifted from canonical material")
    return CommandResult("replayed", receipt_bytes, receipt_digest, retries)


def retryable_states(profile_contract: dict[str, object]) -> set[str]:
    value = profile_contract.get("retry_sqlstates")
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("invalid retry SQLSTATE contract")
    return set(value)


def execute_command(
    dsn: str,
    profile_contract: dict[str, object],
    material: CommandMaterial,
    barrier: threading.Barrier | None = None,
    simulate_post_commit_ack_loss: bool = False,
    max_attempts: int = 12,
) -> CommandResult:
    retryable = retryable_states(profile_contract)
    retries = 0
    first_attempt = True
    with psycopg.connect(dsn, autocommit=True) as connection:
        while retries < max_attempts:
            duplicate = classify_duplicate(connection, material, retries)
            if duplicate is not None:
                return duplicate
            try:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                        cursor.execute(
                            """
                            SELECT fingerprint, receipt_bytes, receipt_digest
                            FROM trnm_command_receipts
                            WHERE tenant_id = %s AND entity_id = %s
                              AND command_id = %s
                            """,
                            (TENANT, ENTITY, material.command_id),
                        )
                        stored = cursor.fetchone()
                        if stored is not None:
                            fingerprint = normalize_bytes(stored[0])
                            if fingerprint != material.fingerprint:
                                raise DomainConflict("idempotency_fingerprint_conflict")
                            receipt_bytes = normalize_bytes(stored[1])
                            receipt_digest = normalize_bytes(stored[2])
                            if (
                                receipt_bytes != material.receipt_bytes
                                or receipt_digest != material.receipt_digest
                            ):
                                raise AssertionError("transactional duplicate receipt drift")
                            return CommandResult(
                                "replayed", receipt_bytes, receipt_digest, retries
                            )

                        cursor.execute(
                            """
                            SELECT revision, authority_generation
                            FROM trnm_entity_heads
                            WHERE tenant_id = %s AND entity_id = %s
                            """,
                            (TENANT, ENTITY),
                        )
                        head = cursor.fetchone()
                        if head is None:
                            raise DomainConflict("entity_missing")
                        if int(head[0]) != material.expected_revision:
                            raise DomainConflict("revision_conflict")
                        if int(head[1]) != 1:
                            raise DomainConflict("authority_generation_conflict")

                        if barrier is not None and first_attempt:
                            barrier.wait(timeout=20)
                        first_attempt = False

                        next_revision = material.expected_revision + 1
                        next_sequence = material.expected_revision + 1
                        cursor.execute(
                            """
                            UPDATE trnm_entity_heads
                            SET revision = %s,
                                last_sequence = %s,
                                state_digest = %s,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE tenant_id = %s AND entity_id = %s
                              AND revision = %s AND authority_generation = 1
                            """,
                            (
                                next_revision,
                                next_sequence,
                                material.state_digest,
                                TENANT,
                                ENTITY,
                                material.expected_revision,
                            ),
                        )
                        if cursor.rowcount != 1:
                            raise DomainConflict("revision_conflict")

                        cursor.execute(
                            """
                            INSERT INTO trnm_command_receipts (
                                tenant_id, entity_id, command_id, fingerprint,
                                committed_revision, committed_state_digest,
                                first_sequence, last_sequence, event_count,
                                outbox_count, receipt_bytes, receipt_digest
                            ) VALUES (
                                %s, %s, %s, %s, %s, %s,
                                %s, %s, 1, 1, %s, %s
                            )
                            """,
                            (
                                TENANT,
                                ENTITY,
                                material.command_id,
                                material.fingerprint,
                                next_revision,
                                material.state_digest,
                                next_sequence,
                                next_sequence,
                                material.receipt_bytes,
                                material.receipt_digest,
                            ),
                        )
                        cursor.execute(
                            """
                            INSERT INTO trnm_events (
                                tenant_id, entity_id, sequence, event_id,
                                command_id, payload_digest, payload_bytes
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                TENANT,
                                ENTITY,
                                next_sequence,
                                material.event_id,
                                material.command_id,
                                material.event_digest,
                                b"concurrent-event-v2",
                            ),
                        )
                        cursor.execute(
                            """
                            INSERT INTO trnm_outbox (
                                tenant_id, intent_id, entity_id, command_id,
                                kind, payload_digest, payload_bytes
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                TENANT,
                                material.intent_id,
                                ENTITY,
                                material.command_id,
                                "match.concurrent.broadcast",
                                material.intent_digest,
                                b"concurrent-intent-v2",
                            ),
                        )
                if simulate_post_commit_ack_loss:
                    raise SimulatedPostCommitAckLoss("commit succeeded; response discarded")
                return CommandResult(
                    "committed",
                    material.receipt_bytes,
                    material.receipt_digest,
                    retries,
                )
            except DomainConflict:
                duplicate = classify_duplicate(connection, material, retries)
                if duplicate is not None:
                    return duplicate
                raise
            except threading.BrokenBarrierError as error:
                raise AssertionError("concurrency barrier broke") from error
            except psycopg.Error as error:
                sqlstate = error.sqlstate or ""
                if sqlstate == "23505":
                    duplicate = classify_duplicate(connection, material, retries)
                    if duplicate is not None:
                        return duplicate
                    raise DomainConflict("unique_constraint_conflict") from error
                if sqlstate in retryable:
                    retries += 1
                    continue
                raise
        raise AssertionError("serializable transaction retry budget exhausted")


def graph_counts(connection: psycopg.Connection[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    with connection.cursor() as cursor:
        for table in (
            "trnm_entity_heads",
            "trnm_command_receipts",
            "trnm_events",
            "trnm_outbox",
        ):
            cursor.execute(f"SELECT count(*) FROM {table}")
            counts[table] = int(cursor.fetchone()[0])
    return counts


def assert_single_graph(connection: psycopg.Connection[Any], revision: int = 1) -> None:
    counts = graph_counts(connection)
    expected = {
        "trnm_entity_heads": 1,
        "trnm_command_receipts": 1,
        "trnm_events": 1,
        "trnm_outbox": 1,
    }
    if counts != expected:
        raise AssertionError(f"unexpected graph counts: {counts!r}")
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT revision, last_sequence
            FROM trnm_entity_heads
            WHERE tenant_id = %s AND entity_id = %s
            """,
            (TENANT, ENTITY),
        )
        row = cursor.fetchone()
    if row != (revision, revision):
        raise AssertionError(f"unexpected entity fence after concurrency: {row!r}")


def material(slot: int, expected_revision: int = 0) -> CommandMaterial:
    suffix = f"{slot:012d}"
    return CommandMaterial(
        command_id=uuid.UUID(f"74000000-0000-0000-0000-{suffix}"),
        event_id=uuid.UUID(f"75000000-0000-0000-0000-{suffix}"),
        intent_id=uuid.UUID(f"76000000-0000-0000-0000-{suffix}"),
        fingerprint=digest(0x20 + slot),
        state_digest=digest(0x30 + slot),
        event_digest=digest(0x40 + slot),
        intent_digest=digest(0x50 + slot),
        receipt_digest=digest(0x60 + slot),
        receipt_bytes=f"canonical-receipt-{slot}".encode("ascii"),
        expected_revision=expected_revision,
    )


def run_same_command_race(
    dsn: str, profile_contract: dict[str, object], workers: int = 8
) -> dict[str, object]:
    command = material(1)
    barrier = threading.Barrier(workers)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                execute_command,
                dsn,
                profile_contract,
                command,
                barrier,
            )
            for _ in range(workers)
        ]
        results = [future.result(timeout=90) for future in futures]
    receipts = {(result.receipt_bytes, result.receipt_digest) for result in results}
    if receipts != {(command.receipt_bytes, command.receipt_digest)}:
        raise AssertionError("same command race returned divergent receipts")
    if sum(result.disposition == "committed" for result in results) != 1:
        raise AssertionError(f"same command race did not have exactly one commit: {results!r}")
    return {
        "workers": workers,
        "committed": sum(result.disposition == "committed" for result in results),
        "replayed": sum(result.disposition == "replayed" for result in results),
        "total_retries": sum(result.retries for result in results),
    }


def run_distinct_command_race(
    dsn: str, profile_contract: dict[str, object]
) -> dict[str, object]:
    commands = [material(2), material(3)]
    barrier = threading.Barrier(2)

    def invoke(value: CommandMaterial) -> tuple[str, int]:
        try:
            result = execute_command(
                dsn,
                profile_contract,
                value,
                barrier,
            )
            return result.disposition, result.retries
        except DomainConflict as error:
            if str(error) != "revision_conflict":
                raise
            return "revision_conflict", 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(invoke, commands, timeout=90))
    dispositions = sorted(result[0] for result in results)
    if dispositions != ["committed", "revision_conflict"]:
        raise AssertionError(f"distinct command race outcome drift: {results!r}")
    return {
        "committed": dispositions.count("committed"),
        "revision_conflict": dispositions.count("revision_conflict"),
        "total_retries": sum(result[1] for result in results),
    }


def lease_statement(profile: str) -> str:
    candidate = """
        SELECT tenant_id, intent_id
        FROM trnm_outbox
        WHERE tenant_id = %s AND state = 'pending'
          AND available_at <= CURRENT_TIMESTAMP AND attempt < 32
        ORDER BY available_at, intent_id
        LIMIT 1
    """
    if profile == "postgresql":
        candidate = candidate.rstrip() + " FOR UPDATE SKIP LOCKED"
        return f"""
            WITH candidates AS ({candidate})
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
            RETURNING outbox.intent_id, outbox.lease_generation
        """
    return f"""
        WITH candidates AS ({candidate})
        UPDATE trnm_outbox AS outbox
        SET state = 'leased', lease_owner = %s,
            lease_generation = outbox.lease_generation + 1,
            lease_expires_at = CURRENT_TIMESTAMP + INTERVAL '60 seconds',
            attempt = outbox.attempt + 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE (outbox.tenant_id, outbox.intent_id) IN (
            SELECT tenant_id, intent_id FROM candidates
        )
          AND outbox.state = 'pending'
        RETURNING outbox.intent_id, outbox.lease_generation
    """


def lease_once(
    dsn: str,
    profile: str,
    profile_contract: dict[str, object],
    worker: uuid.UUID,
    barrier: threading.Barrier | None = None,
    max_attempts: int = 12,
) -> tuple[uuid.UUID, int] | None:
    retryable = retryable_states(profile_contract)
    first_attempt = True
    with psycopg.connect(dsn, autocommit=True) as connection:
        for _ in range(max_attempts):
            try:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                        if barrier is not None and first_attempt:
                            barrier.wait(timeout=20)
                        first_attempt = False
                        cursor.execute(lease_statement(profile), (TENANT, worker))
                        row = cursor.fetchone()
                        if row is None:
                            return None
                        if cursor.fetchone() is not None:
                            raise AssertionError("single-intent lease returned multiple rows")
                        return row[0], int(row[1])
            except threading.BrokenBarrierError as error:
                raise AssertionError("lease contention barrier broke") from error
            except psycopg.Error as error:
                if (error.sqlstate or "") in retryable:
                    continue
                raise
    raise AssertionError("lease retry budget exhausted")


def run_lease_contention(
    dsn: str,
    profile: str,
    profile_contract: dict[str, object],
    workers: int = 8,
) -> dict[str, object]:
    barrier = threading.Barrier(workers)
    worker_ids = [
        uuid.UUID(f"77000000-0000-0000-0000-{index:012d}")
        for index in range(1, workers + 1)
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                lease_once,
                dsn,
                profile,
                profile_contract,
                worker,
                barrier,
            )
            for worker in worker_ids
        ]
        results = [future.result(timeout=90) for future in futures]
    winners = [result for result in results if result is not None]
    if len(winners) != 1 or winners[0][1] != 1:
        raise AssertionError(f"outbox contention produced invalid winners: {winners!r}")
    return {"workers": workers, "winners": 1, "lease_generation": winners[0][1]}


def run_lease_rollover(
    dsn: str,
    profile: str,
    profile_contract: dict[str, object],
) -> dict[str, object]:
    with psycopg.connect(dsn, autocommit=True) as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                cursor.execute(
                    """
                    UPDATE trnm_outbox
                    SET lease_expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second'
                    WHERE tenant_id = %s AND state = 'leased'
                    """,
                    (TENANT,),
                )
                if cursor.rowcount != 1:
                    raise AssertionError("expected one lease to expire")
                cursor.execute(
                    """
                    UPDATE trnm_outbox
                    SET state = 'pending', lease_owner = NULL,
                        lease_expires_at = NULL, available_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE tenant_id = %s AND state = 'leased'
                      AND lease_expires_at <= CURRENT_TIMESTAMP
                    """,
                    (TENANT,),
                )
                if cursor.rowcount != 1:
                    raise AssertionError("expired lease recovery did not reclaim one row")

    second = lease_once(dsn, profile, profile_contract, WORKER_B)
    if second is None or second[1] != 2:
        raise AssertionError(f"lease generation did not advance on rollover: {second!r}")
    intent_id, generation = second

    with psycopg.connect(dsn, autocommit=True) as connection:
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
                    (digest(0x7A), TENANT, intent_id, WORKER_A),
                )
                if cursor.rowcount != 0:
                    raise AssertionError("stale worker completed a generation-2 lease")

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
                      AND lease_generation = %s
                    RETURNING applied_receipt_digest
                    """,
                    (digest(0x7B), TENANT, intent_id, WORKER_B, generation),
                )
                row = cursor.fetchone()
                if row is None or normalize_bytes(row[0]) != digest(0x7B):
                    raise AssertionError("current generation could not complete outbox")
    return {
        "recovered_generation": generation,
        "stale_worker_rejected": True,
        "current_worker_applied": True,
    }


def run_post_commit_ack_loss(
    dsn: str, profile_contract: dict[str, object]
) -> dict[str, object]:
    command = material(4)
    try:
        execute_command(
            dsn,
            profile_contract,
            command,
            simulate_post_commit_ack_loss=True,
        )
    except SimulatedPostCommitAckLoss:
        pass
    else:
        raise AssertionError("post-commit acknowledgement loss was not simulated")
    recovered = execute_command(dsn, profile_contract, command)
    if recovered.disposition != "replayed":
        raise AssertionError(f"lost acknowledgement did not recover by replay: {recovered!r}")
    return {
        "simulated_post_commit_ack_loss": True,
        "recovery_disposition": recovered.disposition,
        "receipt_digest_exact": recovered.receipt_digest == command.receipt_digest,
    }


def prepare(dsn: str, profile: str, profile_contract: dict[str, object]) -> int:
    migration = ROOT / str(profile_contract["migration"])
    with psycopg.connect(dsn, autocommit=True) as connection:
        statements = apply_migration(connection, migration)
        clear_graph(connection)
        seed_entity(connection)
    return statements


def reset(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as connection:
        clear_graph(connection)
        seed_entity(connection)


def run(profile: str, dsn: str) -> dict[str, object]:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    profile_contract = contract["profiles"][profile]
    migration_statements = prepare(dsn, profile, profile_contract)

    same_command = run_same_command_race(dsn, profile_contract)
    with psycopg.connect(dsn, autocommit=True) as connection:
        assert_single_graph(connection)

    reset(dsn)
    distinct_command = run_distinct_command_race(dsn, profile_contract)
    with psycopg.connect(dsn, autocommit=True) as connection:
        assert_single_graph(connection)

    reset(dsn)
    initial = execute_command(dsn, profile_contract, material(5))
    if initial.disposition != "committed":
        raise AssertionError("lease scenario seed command did not commit")
    lease_contention = run_lease_contention(dsn, profile, profile_contract)
    lease_rollover = run_lease_rollover(dsn, profile, profile_contract)

    reset(dsn)
    post_commit_ack_loss = run_post_commit_ack_loss(dsn, profile_contract)
    with psycopg.connect(dsn, autocommit=True) as connection:
        assert_single_graph(connection)

    return {
        "schema": "trillionnium.game.database-concurrency-fault-matrix.v2",
        "profile": profile,
        "migration_statements": migration_statements,
        "scenarios": {
            "same_command_race": same_command,
            "distinct_command_revision_race": distinct_command,
            "outbox_lease_contention": lease_contention,
            "outbox_lease_rollover": lease_rollover,
            "post_commit_ack_loss": post_commit_ack_loss,
        },
        "invariants": {
            "acknowledged_command_loss": 0,
            "duplicate_visible_effect": 0,
            "stale_authority_write": 0,
            "partial_command_event_outbox_commit": 0,
            "multiple_outbox_lease_winners": 0,
            "stale_outbox_completion": 0,
        },
        "claims": {
            "single_node_concurrency_matrix_complete": True,
            "network_partition_matrix_complete": False,
            "primary_or_leaseholder_failover_complete": False,
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
    except (
        AssertionError,
        DomainConflict,
        OSError,
        ValueError,
        psycopg.Error,
    ) as error:
        print(f"database concurrency matrix failed: {error}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n"
    args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
