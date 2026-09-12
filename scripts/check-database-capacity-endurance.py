#!/usr/bin/env python3
"""Source contract for database capacity and endurance evidence."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "contracts/operations/database-endurance-plan.v1.json"
WORKLOAD = ROOT / "scripts/database-capacity-workload.sql"
SMOKE = ROOT / "scripts/ci-database-capacity-smoke.sh"
SEGMENT = ROOT / "scripts/ci-database-endurance-segment.sh"
FINALIZE = ROOT / "scripts/finalize-database-endurance-ledger.py"
REQUIRED = (
    "pgbench",
    "failed_transactions",
    "latency_average_ms",
    "transactions_per_second",
    "TRNM_DATABASE_LOGICAL_ID",
    "CANDIDATE_COMMIT is required",
    "CANDIDATE_TREE is required",
    "@sha256:",
    "started_epoch_seconds",
    "finished_epoch_seconds",
    "capacity_manifest_sha256",
    "PREVIOUS_SEGMENT_SHA256",
    "GENESIS",
    "segment gap at",
    "previous digest",
    "commit changed",
    "tree changed",
    "workload changed",
    "database identity changed",
    "client image changed",
    "requested endurance duration",
    "observed endurance duration",
    "segment gap too large",
)


class ValidationError(RuntimeError):
    pass


def require(value: bool, message: str) -> None:
    if not value:
        raise ValidationError(message)


def validate(
    plan: dict, workload: str, smoke: str, segment: str, finalize: str
) -> None:
    combined = smoke + segment + finalize
    for marker in REQUIRED:
        require(marker in combined, f"capacity/endurance source missing {marker}")
    require("trnm_storage_objects" in workload, "workload misses authoritative storage")
    require(
        plan.get("schema") == "trillionnium.database-endurance-plan.v1",
        "plan schema",
    )
    require(
        plan.get("profiles") == ["postgresql", "cockroachdb"], "profile order"
    )
    require(plan.get("targets_hours") == [24, 72, 168], "duration targets")
    require(plan.get("maximum_segment_seconds") == 21600, "segment bound")
    require(
        plan.get("maximum_segment_overhead_seconds") == 300,
        "segment overhead bound",
    )
    require(plan.get("maximum_segment_gap_seconds") == 900, "segment gap bound")
    properties = plan.get("required_segment_properties")
    require(
        isinstance(properties, list)
        and len(properties) >= 12
        and any("requested workload time" in item for item in properties)
        and any("digest-pinned" in item for item in properties),
        "segment property inventory",
    )
    require(
        plan.get("thresholds_must_be_independently_approved") is True,
        "approval boundary",
    )
    require(
        not any(plan.get("claim_boundary", {}).values()),
        "positive acceptance claim",
    )
    require("|| true" not in smoke + segment, "failure suppression introduced")
    require(
        "CANDIDATE_COMMIT=${CANDIDATE_COMMIT:-unknown}" not in smoke,
        "unknown candidate fallback",
    )
    require(
        "CANDIDATE_TREE=${CANDIDATE_TREE:-unknown}" not in smoke,
        "unknown tree fallback",
    )


def main() -> int:
    try:
        validate(
            json.loads(PLAN.read_text(encoding="utf-8")),
            WORKLOAD.read_text(encoding="utf-8"),
            SMOKE.read_text(encoding="utf-8"),
            SEGMENT.read_text(encoding="utf-8"),
            FINALIZE.read_text(encoding="utf-8"),
        )
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        print(f"database capacity/endurance validation failed: {error}", file=sys.stderr)
        return 1
    print("database capacity/endurance source contract: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
