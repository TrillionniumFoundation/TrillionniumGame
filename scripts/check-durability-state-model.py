#!/usr/bin/env python3
"""Source contract for the durability state model."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "scripts/durability-state-model.py"
CONTRACT = ROOT / "contracts/database/durability-state-model.v1.json"

REQUIRED = (
    "commit_current:A",
    "commit_stale:A",
    "replay_same",
    "replay_conflict",
    "takeover",
    "claim:1",
    "lease_expire",
    "publish_idempotent",
    "ack_exact",
    "ack_stale_owner_or_generation",
    "crash_before_or_after_publish",
    "reap_expired_at_limit",
    "duplicate externally visible effect",
    "stale delivery acknowledgement",
    "accepted command lost its outbox intent",
    "dead_with_visible_effect_states_seen",
    "idempotent_retry_states_seen",
)


class ValidationError(RuntimeError):
    pass


def require(value: bool, message: str) -> None:
    if not value:
        raise ValidationError(message)


def validate(source: str, contract: dict) -> None:
    for marker in REQUIRED:
        require(marker in source, f"durability model missing {marker}")
    require(
        contract.get("schema") == "trillionnium.durability-state-model.v1",
        "contract schema",
    )
    require(contract.get("exploration_depth") >= 10, "exploration depth")
    require(contract.get("maximum_attempts") == 2, "bounded maximum attempts")
    require(len(contract.get("actions", [])) >= 12, "action inventory")
    require(len(contract.get("invariants", [])) >= 12, "invariant inventory")
    boundaries = contract.get("known_ambiguity_boundaries")
    require(
        isinstance(boundaries, list)
        and len(boundaries) >= 2
        and any("dead" in value and "visible" in value for value in boundaries),
        "crash-after-publish ambiguity boundary",
    )
    mutants = contract.get("hostile_mutants")
    require(
        isinstance(mutants, list)
        and any("duplicate visible effect" in value for value in mutants)
        and any("stale owner or generation" in value for value in mutants),
        "hostile mutant inventory",
    )
    require(
        contract.get("implementation_differential_required") is True,
        "implementation differential boundary",
    )
    require(
        not any(contract.get("claim_boundary", {}).values()),
        "positive acceptance claim",
    )


def main() -> int:
    try:
        validate(MODEL.read_text(), json.loads(CONTRACT.read_text()))
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        print(f"durability state model validation failed: {error}", file=sys.stderr)
        return 1
    print("durability state model source contract: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
