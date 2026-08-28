#!/usr/bin/env python3
"""Verify the deterministic Rust presence-router lifecycle vector."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/realtime/presence-router-v2.json"


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def parse_vector(path: Path) -> dict[str, str]:
    require(path.is_file(), f"missing Rust vector: {path}")
    values: dict[str, str] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        require("=" in line, f"line {line_number} lacks '='")
        key, value = line.split("=", 1)
        require(key != "", f"line {line_number} has an empty key")
        require(key not in values, f"duplicate vector key: {key}")
        values[key] = value
    return values


def expected_vector() -> dict[str, str]:
    return {
        "join_visible": "applied:1:1:0:0:0",
        "join_hidden": "applied:2:0:0:0:1",
        "update": "applied:3:0:1:0:0",
        "leave": "applied:4:0:0:1:0",
        "rejoin": "applied:5:1:0:0:0",
        "replacement": "applied:6:1:0:1:0",
        "stale": "stale_generation",
        "revision": "6",
        "entry_count": "2",
        "connection_count": "2",
        "public": "session-c@node-a/connection-a:new",
        "all": (
            "session-b@node-b/connection-b:hidden:hidden|"
            "session-c@node-a/connection-a:new:visible"
        ),
    }


def verify(path: Path) -> dict[str, object]:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    require(
        contract.get("schema") == "trillionnium.game.presence-router.v2",
        "presence contract schema drift",
    )
    require(contract.get("version") == 2, "presence contract version drift")
    identity = contract.get("identity")
    require(isinstance(identity, dict), "identity contract missing")
    require(
        identity.get("generation_high_water_persists_without_active_streams") is True,
        "generation tombstone contract drift",
    )
    require(
        identity.get("identity_replacement_requires_strictly_higher_generation") is True,
        "identity generation binding contract drift",
    )

    actual = parse_vector(path)
    expected = expected_vector()
    require(set(actual) == set(expected), f"vector key drift: {sorted(actual)}")
    for key, value in expected.items():
        require(actual[key] == value, f"vector mismatch for {key}: {actual[key]!r}")

    revisions: list[int] = []
    for key in (
        "join_visible",
        "join_hidden",
        "update",
        "leave",
        "rejoin",
        "replacement",
    ):
        parts = actual[key].split(":")
        require(len(parts) == 6 and parts[0] == "applied", f"bad delta encoding: {key}")
        revisions.append(int(parts[1]))
    require(revisions == [1, 2, 3, 4, 5, 6], "applied revisions are not contiguous")
    require(actual["stale"] == "stale_generation", "stale mutation did not fail closed")
    require(int(actual["revision"]) == revisions[-1], "error path advanced revision")

    public_rows = actual["public"].split("|") if actual["public"] else []
    all_rows = actual["all"].split("|") if actual["all"] else []
    require(len(public_rows) == 1, "hidden presence leaked into public snapshot")
    require(len(all_rows) == 2, "include-hidden snapshot lost a record")
    require(all_rows == sorted(all_rows), "snapshot vector is not deterministic")

    return {
        "schema": "trillionnium.game.presence-router-reference.v2",
        "checks": {
            "delta_sequence_exact": True,
            "revision_monotonic": True,
            "stale_error_atomic": True,
            "hidden_filter_exact": True,
            "snapshot_order_exact": True,
            "identity_replacement_generation_bound": True,
        },
        "claims": {
            "cross_implementation_vector_passed": True,
            "nakama_wire_compatibility_verified": False,
            "distributed_fault_matrix_complete": False,
            "production_ready": False,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = verify(args.input)
    except (OSError, KeyError, TypeError, ValueError, VerificationError) as error:
        print(f"presence-router-v2 reference verification failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
