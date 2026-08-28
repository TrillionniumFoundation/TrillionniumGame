#!/usr/bin/env python3
"""Run the complete static W1/W14 database v2 gate."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = [
    [sys.executable, "scripts/verify-database-profile-v2.py"],
    [sys.executable, "scripts/verify-command-transaction-v2.py"],
    [sys.executable, "tests/database/test_database_profile_v2.py"],
    [sys.executable, "tests/database/test_command_transaction_v2.py"],
]


def run(command: list[str]) -> dict[str, object]:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        sys.stderr.write(completed.stdout)
        sys.stderr.write(completed.stderr)
        raise RuntimeError(f"database v2 gate failed: {' '.join(command)}")
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout_sha256": __import__("hashlib").sha256(
            completed.stdout.encode("utf-8")
        ).hexdigest(),
        "stderr_sha256": __import__("hashlib").sha256(
            completed.stderr.encode("utf-8")
        ).hexdigest(),
    }


def main() -> int:
    results: list[dict[str, object]] = []
    try:
        for command in COMMANDS:
            results.append(run(command))
        subprocess.run(
            [sys.executable, "-m", "compileall", "-q", "scripts", "tests/database"],
            cwd=ROOT,
            check=True,
        )
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"database v2 gate failed: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "schema": "trillionnium.game.database-v2-static-gate.v1",
                "results": results,
                "claims": {
                    "static_gate_passed": True,
                    "runtime_apply_passed": False,
                    "fault_matrix_complete": False,
                    "production_ready": False,
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
