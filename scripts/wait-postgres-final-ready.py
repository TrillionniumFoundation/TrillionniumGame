#!/usr/bin/env python3
"""Wait for the final Dockerized PostgreSQL server to be stably queryable.

The official PostgreSQL container starts a temporary initialization server on a
Unix socket before it hands over to the final TCP-listening postmaster. A single
``pg_isready`` inside the container can therefore observe a transient server.
This helper probes the container's TCP listener and requires a bounded run of
successful SQL transactions before callers may proceed.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable

_SAFE_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\Z")


class ReadinessError(RuntimeError):
    """Raised when stable final-server readiness is not established."""


@dataclass(frozen=True)
class ProbeResult:
    container_running: bool
    sql_ok: bool
    detail: str


def _validate_identifier(value: str, label: str) -> str:
    if not _SAFE_ID.fullmatch(value):
        raise ValueError(f"{label} contains unsupported characters")
    return value


def _bounded_detail(value: str, *, limit: int = 240) -> str:
    compact = " ".join(value.split())
    return compact[:limit]


def docker_postgres_probe(
    container: str,
    user: str,
    database: str,
    *,
    docker: str = "docker",
    command_timeout_seconds: float = 5.0,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> ProbeResult:
    """Probe one container without exposing its PostgreSQL password.

    The SQL probe explicitly connects over 127.0.0.1:5432 inside the container.
    PostgreSQL's temporary initialization server does not listen on TCP, so this
    distinguishes it from the final postmaster instead of merely observing the
    temporary Unix socket.
    """

    container = _validate_identifier(container, "container")
    user = _validate_identifier(user, "user")
    database = _validate_identifier(database, "database")
    if command_timeout_seconds <= 0:
        raise ValueError("command_timeout_seconds must be positive")

    common = {
        "check": False,
        "capture_output": True,
        "text": True,
        "timeout": command_timeout_seconds,
    }
    try:
        inspect = runner(
            [docker, "inspect", "--format", "{{.State.Running}}", container],
            **common,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return ProbeResult(False, False, f"docker-inspect-error:{type(error).__name__}")

    running = inspect.returncode == 0 and inspect.stdout.strip() == "true"
    if not running:
        detail = _bounded_detail(inspect.stderr or inspect.stdout)
        return ProbeResult(False, False, f"container-not-running:{detail}")

    shell = (
        'PGPASSWORD="$POSTGRES_PASSWORD" exec psql '
        '-X -qAt -h 127.0.0.1 -p 5432 '
        '-U "$TRNM_READY_USER" -d "$TRNM_READY_DATABASE" '
        '-v ON_ERROR_STOP=1 -c "SELECT 1"'
    )
    try:
        query = runner(
            [
                docker,
                "exec",
                "-e",
                f"TRNM_READY_USER={user}",
                "-e",
                f"TRNM_READY_DATABASE={database}",
                container,
                "sh",
                "-ec",
                shell,
            ],
            **common,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return ProbeResult(True, False, f"sql-probe-error:{type(error).__name__}")

    output = [line.strip() for line in query.stdout.splitlines() if line.strip()]
    sql_ok = query.returncode == 0 and output == ["1"]
    if sql_ok:
        return ProbeResult(True, True, "tcp-sql-transaction-ok")
    detail = _bounded_detail(query.stderr or query.stdout)
    return ProbeResult(True, False, f"tcp-sql-transaction-failed:{query.returncode}:{detail}")


def wait_for_stable_readiness(
    probe: Callable[[], ProbeResult],
    *,
    attempts: int,
    consecutive_successes: int,
    interval_seconds: float,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, int | str]:
    """Require a bounded consecutive-success window and fail closed otherwise."""

    if attempts <= 0:
        raise ValueError("attempts must be positive")
    if consecutive_successes <= 0:
        raise ValueError("consecutive_successes must be positive")
    if consecutive_successes > attempts:
        raise ValueError("consecutive_successes cannot exceed attempts")
    if interval_seconds < 0:
        raise ValueError("interval_seconds cannot be negative")

    streak = 0
    streak_started_at = 0
    last = ProbeResult(False, False, "not-probed")
    for attempt in range(1, attempts + 1):
        last = probe()
        if last.container_running and last.sql_ok:
            if streak == 0:
                streak_started_at = attempt
            streak += 1
            if streak >= consecutive_successes:
                return {
                    "attempts_used": attempt,
                    "stable_window_started_at_attempt": streak_started_at,
                    "consecutive_successes": streak,
                    "last_probe": last.detail,
                }
        else:
            streak = 0
            streak_started_at = 0

        if attempt < attempts:
            sleeper(interval_seconds)

    raise ReadinessError(
        "stable PostgreSQL readiness was not established "
        f"after {attempts} attempts; last_probe={last.detail}"
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", required=True)
    parser.add_argument("--user", default="trnm")
    parser.add_argument("--database", default="trnm")
    parser.add_argument("--attempts", type=int, default=160)
    parser.add_argument("--consecutive-successes", type=int, default=6)
    parser.add_argument("--interval-seconds", type=float, default=0.25)
    parser.add_argument("--command-timeout-seconds", type=float, default=5.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        result = wait_for_stable_readiness(
            lambda: docker_postgres_probe(
                args.container,
                args.user,
                args.database,
                command_timeout_seconds=args.command_timeout_seconds,
            ),
            attempts=args.attempts,
            consecutive_successes=args.consecutive_successes,
            interval_seconds=args.interval_seconds,
        )
    except (ReadinessError, ValueError) as error:
        print(f"PostgreSQL final readiness failed: {error}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "schema": "trillionnium.postgres-final-readiness.v1",
                "status": "stable",
                "container": args.container,
                "database": args.database,
                "user": args.user,
                "tcp_host": "127.0.0.1",
                "tcp_port": 5432,
                "required_consecutive_successes": args.consecutive_successes,
                **result,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
