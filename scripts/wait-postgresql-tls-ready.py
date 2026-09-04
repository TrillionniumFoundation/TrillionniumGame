#!/usr/bin/env python3
"""Wait for a test container's final TCP/TLS SQL endpoint, never initdb's socket.

This is a bounded CI prerequisite, not TLS-rotation or production evidence.
The password is inherited through Docker's named environment forwarding and
never placed in an argument value, a URI, a log, or the readiness receipt.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from typing import Any

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
SQL = (
    "SELECT CASE WHEN current_setting('ssl') = 'on' "
    "AND NOT pg_is_in_recovery() "
    "AND EXISTS (SELECT 1 FROM pg_stat_ssl "
    "WHERE pid = pg_backend_pid() AND ssl) "
    "THEN 'ready' ELSE 'not-ready' END"
)


class ReadinessError(RuntimeError):
    """A bounded, non-secret readiness failure."""


def checked_name(value: str, label: str) -> str:
    if not isinstance(value, str) or NAME.fullmatch(value) is None:
        raise ReadinessError(f"invalid {label}")
    return value


def wait_ready(
    container: str,
    database: str,
    *,
    timeout_seconds: float = 60.0,
    run: Callable[..., Any] = subprocess.run,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    checked_name(container, "container name")
    checked_name(database, "database name")
    if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 300:
        raise ReadinessError("timeout must be finite and in (0, 300] seconds")
    start = clock()
    deadline = start + timeout_seconds
    attempts = 0
    inspect = ["docker", "inspect", "--format", "{{.State.Running}}", container]
    query = [
        "docker", "exec",
        "--env", "PGPASSWORD",
        "--env", "PGCONNECT_TIMEOUT=2",
        "--env", "PGSSLMODE=verify-full",
        "--env", "PGSSLROOTCERT=/var/lib/postgresql/root.crt",
        "--env", "PGOPTIONS=-c statement_timeout=2000",
        container, "psql", "--no-psqlrc", "--no-password",
        "--host=127.0.0.1", "--username=postgres", f"--dbname={database}",
        "--tuples-only", "--no-align", "--set=ON_ERROR_STOP=1", "--command", SQL,
    ]

    def execute(command: Sequence[str]) -> Any:
        remaining = deadline - clock()
        if remaining <= 0:
            raise ReadinessError("PostgreSQL TLS readiness deadline exceeded")
        return run(
            list(command), check=False, capture_output=True, text=True,
            timeout=min(3.0, remaining),
        )

    while clock() < deadline:
        attempts += 1
        try:
            state = execute(inspect)
            if state.returncode != 0 or state.stdout.strip() != "true":
                raise ReadinessError("PostgreSQL test container is unavailable or stopped")
            result = execute(query)
            if result.returncode == 0 and result.stdout.strip() == "ready":
                finished = clock()
                if finished >= deadline:
                    raise ReadinessError("PostgreSQL TLS readiness completed after deadline")
                return {
                    "schema": "trillionnium.pg-tls-endpoint-readiness.v1",
                    "status": "ready",
                    "transport": "tcp-verify-full",
                    "session_tls_verified": True,
                    "attempts": attempts,
                    "elapsed_seconds": round(finished - start, 6),
                    "tls_rotation_credit": False,
                    "compatibility_credit": False,
                    "production_ready": False,
                }
        except subprocess.TimeoutExpired:
            # One stuck Docker/psql call may consume only the remaining budget.
            # libpq connect_timeout and statement_timeout also bound its SQL.
            pass
        except OSError as error:
            # Do not render subprocess arguments or captured diagnostic output.
            raise ReadinessError("PostgreSQL readiness process could not execute") from error
        remaining = deadline - clock()
        if remaining <= 0:
            break
        sleep(min(1.0, remaining))
    raise ReadinessError("PostgreSQL TLS readiness deadline exceeded")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    args = parser.parse_args(argv)
    try:
        if not os.environ.get("PGPASSWORD"):
            raise ReadinessError("the ephemeral test PGPASSWORD is required")
        result = wait_ready(
            args.container, args.database, timeout_seconds=args.timeout_seconds,
        )
    except ReadinessError as error:
        print(f"PostgreSQL TLS endpoint not ready: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
