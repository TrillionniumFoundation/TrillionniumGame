#!/usr/bin/env python3
"""Behavioral source contract for the durability state model."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "scripts/durability-state-model.py"
CONTRACT = ROOT / "contracts/database/durability-state-model.v1.json"


class ValidationError(RuntimeError):
    pass


def require(value: bool, message: str) -> None:
    if not value:
        raise ValidationError(message)


def load_model() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "trnm_durability_state_model_contract", MODEL
    )
    require(spec is not None and spec.loader is not None, "model loader unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def assert_state_except_rejections(before, after, expected_rejections: int) -> None:
    comparable_before = before.__class__(
        **{
            field: getattr(before, field)
            for field in before.__dataclass_fields__
            if field != "stale_rejections"
        },
        stale_rejections=expected_rejections,
    )
    require(after == comparable_before, "stale token changed durable state")


def validate_behavior(model: ModuleType) -> None:
    initial = model.State()
    stale_commit = model.commit_command(
        initial,
        caller_authority_generation=0,
        fingerprint="A",
    )
    assert_state_except_rejections(initial, stale_commit, 1)

    committed = model.commit_command(
        initial,
        caller_authority_generation=1,
        fingerprint="A",
    )
    require(committed.command_authority_generation == 1, "command authority binding")
    require(committed.outbox_state == model.READY, "command outbox transition")

    claimed = next(model.claim_edges(committed)).state
    require(claimed.lease_authority_generation == 1, "lease authority binding")
    token = model.current_lease_token(claimed)

    taken_over = model.replace(claimed, authority_generation=2)
    stale_publish = model.publish_with_token(taken_over, token)
    assert_state_except_rejections(taken_over, stale_publish, 1)

    published_before_takeover = model.publish_with_token(claimed, token)
    require(
        published_before_takeover.visible_effect_count == 1,
        "fresh publish path unavailable",
    )
    taken_over_after_publish = model.replace(
        published_before_takeover, authority_generation=2
    )
    stale_ack = model.acknowledge_with_token(taken_over_after_publish, token)
    assert_state_except_rejections(taken_over_after_publish, stale_ack, 1)

    expired = model.replace(taken_over, lease_expired=True)
    reclaimed = next(model.claim_edges(expired)).state
    require(
        reclaimed.lease_authority_generation == 2,
        "reclaim did not bind fresh authority",
    )
    fresh_token = model.current_lease_token(reclaimed)
    published = model.publish_with_token(reclaimed, fresh_token)
    delivered = model.acknowledge_with_token(published, fresh_token)
    model.assert_invariants(delivered)
    require(delivered.outbox_state == model.DELIVERED, "fresh delivery unavailable")
    require(
        delivered.delivery_ack_authority_generation == 2,
        "ack authority binding",
    )

    report = model.explore(10)
    require(report["delivered_states_seen"] > 0, "delivery path unreachable")
    require(report["dead_states_seen"] > 0, "reaper path unreachable")
    require(
        report["dead_with_visible_effect_states_seen"] > 0,
        "crash-after-publish ambiguity path unreachable",
    )
    require(
        report["stale_authority_rejection_states_seen"] > 0,
        "stale authority rejection path unreachable",
    )
    require(
        report["reclaimed_after_takeover_states_seen"] > 0,
        "fresh takeover reclaim path unreachable",
    )

    hostile = (
        ({"duplicate_visible_effect": True}, "duplicate externally visible effect"),
        ({"allow_stale_ack": True}, "stale delivery acknowledgement"),
        ({"allow_stale_authority_publish": True}, "stale authority accepted mutation"),
        ({"allow_stale_authority_ack": True}, "stale authority accepted mutation"),
    )
    for options, expected in hostile:
        try:
            model.explore(10, **options)
        except AssertionError as error:
            require(expected in str(error), f"wrong hostile-mutant failure: {error}")
        else:
            raise ValidationError(f"hostile mutant survived: {options}")


def validate_contract(contract: dict) -> None:
    require(
        contract.get("schema") == "trillionnium.durability-state-model.v1",
        "contract schema",
    )
    require(contract.get("exploration_depth") >= 10, "exploration depth")
    require(contract.get("maximum_attempts") == 2, "bounded maximum attempts")
    actions = contract.get("actions")
    require(isinstance(actions, list) and len(actions) >= 13, "action inventory")
    invariants = contract.get("invariants")
    require(
        isinstance(invariants, list) and len(invariants) >= 17,
        "invariant inventory",
    )
    require(
        any("same transition" in value for value in actions),
        "stale/current command paths must share one transition",
    )
    require(
        any("authority generation" in value and "lease" in value for value in invariants),
        "authority-bound lease invariant",
    )
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
        and any("stale authority generation publishes" in value for value in mutants)
        and any("stale authority generation acknowledges" in value for value in mutants),
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
        model = load_model()
        validate_behavior(model)
        validate_contract(json.loads(CONTRACT.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValidationError, AssertionError) as error:
        print(f"durability state model validation failed: {error}", file=sys.stderr)
        return 1
    print("durability state model behavioral contract: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
