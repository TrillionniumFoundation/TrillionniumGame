#!/usr/bin/env python3
"""Cross-engine entrypoint for the durable crash/restart probe.

The implementation module owns recovery and inspection. This entrypoint keeps
prepare-time diagnostics on portable SQL (`CURRENT_TIMESTAMP`) so a successful
commit is never misclassified by a non-critical engine-specific timestamp
query.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import psycopg

ROOT = Path(__file__).resolve().parents[1]
IMPLEMENTATION = ROOT / "scripts/run-database-profile-v2-crash-recovery.py"


def load_implementation() -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "trnm_database_crash_recovery_v2",
        IMPLEMENTATION,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("unable to load crash recovery implementation")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def prepare(
    module: ModuleType,
    profile_name: str,
    dsn: str,
    lease_seconds: int,
) -> dict[str, object]:
    profile = module.profile_contract(profile_name)
    with psycopg.connect(dsn, autocommit=True) as connection:
        statements = module.apply_migration(connection, profile)
        module.clear_graph(connection)
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
                    (module.TENANT, module.ENTITY, module.digest(0x11)),
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
                        module.TENANT,
                        module.ENTITY,
                        module.COMMAND,
                        module.digest(0x21),
                        module.digest(0x11),
                        b"durable-receipt-v2",
                        module.digest(0x31),
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO trnm_events (
                        tenant_id, entity_id, sequence, event_id, command_id,
                        payload_digest, payload_bytes
                    ) VALUES (%s, %s, 1, %s, %s, %s, %s)
                    """,
                    (
                        module.TENANT,
                        module.ENTITY,
                        module.EVENT,
                        module.COMMAND,
                        module.digest(0x41),
                        b"durable-event-v2",
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO trnm_outbox (
                        tenant_id, intent_id, entity_id, command_id, kind,
                        payload_digest, payload_bytes
                    ) VALUES (%s, %s, %s, %s, 'durable.crash.probe', %s, %s)
                    """,
                    (
                        module.TENANT,
                        module.INTENT,
                        module.ENTITY,
                        module.COMMAND,
                        module.digest(0x51),
                        b"durable-intent-v2",
                    ),
                )

        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                cursor.execute(
                    module.lease_sql(profile_name, lease_seconds),
                    (module.TENANT, module.INTENT, module.WORKER_A),
                )
                row = cursor.fetchone()
                if row != (1,):
                    raise AssertionError(
                        f"first durable lease did not bind generation 1: {row!r}"
                    )

        with connection.cursor() as cursor:
            cursor.execute("SELECT CURRENT_TIMESTAMP")
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
            "receipt_digest_hex": module.digest(0x31).hex(),
        },
        "claims": {
            "commit_observed_before_process_kill": True,
            "restart_recovery_verified": False,
            "production_ready": False,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "recover", "inspect"))
    parser.add_argument(
        "--profile",
        choices=("postgresql", "cockroachdb"),
        required=True,
    )
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--lease-seconds", type=int, default=5)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        module = load_implementation()
        if args.mode == "prepare":
            if not 1 <= args.lease_seconds <= 300:
                raise ValueError("lease seconds must be between 1 and 300")
            report = prepare(
                module,
                args.profile,
                args.dsn,
                args.lease_seconds,
            )
        elif args.mode == "recover":
            report = module.recover(args.profile, args.dsn)
        else:
            report = module.inspect(args.profile, args.dsn)
    except (AssertionError, OSError, RuntimeError, ValueError, psycopg.Error) as error:
        print(f"database crash recovery entrypoint failed: {error}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n"
    args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
