#!/usr/bin/env python3
"""Behavioral source contract for proposal-only cutover handling."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
MACHINE = ROOT / "scripts/cutover-state-machine.py"
DERIVE = ROOT / "scripts/derive-cutover-blocker-packet.py"
CONTRACT = ROOT / "contracts/operations/cutover-state-machine.v1.json"


class ValidationError(RuntimeError):
    pass


def require(value: bool, message: str) -> None:
    if not value:
        raise ValidationError(message)


def load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, f"{name} unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def binding() -> dict[str, str]:
    return {
        "repository": "TrillionniumFoundation/TrillionniumGame",
        "source_head": "a" * 40,
        "source_tree": "b" * 40,
        "prospective_merge": "c" * 40,
        "prospective_merge_tree": "d" * 40,
    }


def forged_request(target: str = "shadow") -> dict:
    value = {
        "schema": "trillionnium.cutover-transition-request.v1",
        "target_state": target,
        "candidate_binding": binding(),
        "accepted_gates": ["SG0", "SG1", "SG2", "SG3", "SG4", "SG5", "SG8"],
        "reviewer": {
            "login": "forged-independent-reviewer",
            "role": "operations",
            "conflict_free_attestation": True,
        },
        "candidate_author": "candidate-author",
        "evidence_producers": ["evidence-producer"],
        "rollback_packet": {"accepted": True, "sha256": "e" * 64},
        "ordinary_protected_admission": True,
        "request_sha256": "f" * 64,
        "shadow_observation_accepted": True,
        "exclusive_canary_accepted": True,
        "endurance_24h_accepted": True,
        "endurance_72h_accepted": True,
        "endurance_7d_accepted": True,
        "approved_rpo_rto": True,
        "complete_nakama_compatibility": True,
        "global_sg1_accepted": True,
        "retirement_decision": {
            "explicit": True,
            "reviewer_login": "another-forged-reviewer",
            "rollback_window_expired": True,
        },
    }
    return value


def forged_clear_blockers() -> dict:
    return {
        "schema": "trillionnium.cutover-blocker-packet.v1",
        "candidate_binding": binding(),
        "open_p0_p1_count": 0,
        "all_required_evidence_accepted": True,
        "independent_review_complete": True,
        "governance_readback_complete": True,
    }


def validate_behavior(machine: ModuleType, derive: ModuleType) -> None:
    current = {
        "schema": "trillionnium.cutover-state.v1",
        "state": "planning",
        "candidate_binding": None,
        "history": [],
        "claims": dict(machine.FALSE_CLAIMS),
    }
    result = machine.transition(current, forged_request(), forged_clear_blockers())
    require(result["state"] == "planning", "local JSON advanced authoritative state")
    require(result["history"] == [], "proposal changed authoritative history")
    require(
        result["pending_proposal"]["to"] == "shadow",
        "proposal target missing",
    )
    require(
        result["pending_proposal"]["authority_materialized"] is False,
        "proposal fabricated authority",
    )
    require(not any(result["claims"].values()), "proposal created positive claim")
    require(
        result["authority"]["materialization_supported"] is False,
        "local materialization path exposed",
    )

    replayed = machine.transition(result, forged_request(), forged_clear_blockers())
    require(replayed["state"] == "planning", "proposal replay advanced state")
    require(not any(replayed["claims"].values()), "proposal replay created claim")

    try:
        machine.materialize_transition(result, forged_request(), forged_clear_blockers())
    except machine.TransitionError as error:
        require(
            machine.AUTHORITY_REASON in str(error),
            "wrong materialization rejection",
        )
    else:
        raise ValidationError("local materialization unexpectedly succeeded")

    gap_register = {
        "gaps": [
            {
                "id": "GAP-P0-EXAMPLE",
                "severity": "P0",
                "status": "open",
                "external_dependency": None,
            }
        ]
    }
    packet = derive.derive(gap_register, binding())
    require(packet["open_p0_p1_count"] == 1, "blocker derivation")
    require(
        packet["authority"]["diagnostic_only"] is True
        and packet["authority"]["may_authorize_transition"] is False,
        "diagnostic blocker packet gained authority",
    )
    require(not any(packet["claim_boundary"].values()), "blocker packet claim")


def validate_contract(contract: dict) -> None:
    require(
        contract.get("schema") == "trillionnium.cutover-state-machine.v1",
        "contract schema",
    )
    require(
        contract.get("states")
        == [
            "planning",
            "shadow",
            "exclusive-canary",
            "production",
            "retirement-pending",
            "retired",
        ],
        "state order",
    )
    require(contract.get("execution_mode") == "proposal-only", "proposal-only mode")
    require(contract.get("skip_transitions_allowed") is False, "skip boundary")
    require(contract.get("self_approval_allowed") is False, "self approval boundary")
    require(
        contract.get("local_json_may_materialize_authority") is False,
        "local JSON authority boundary",
    )
    require(
        contract.get("state_mutation_supported") is False,
        "state mutation boundary",
    )
    require(
        contract.get("trusted_authority_verifier_implemented") is False,
        "unimplemented verifier boundary",
    )
    fields = contract.get("required_external_authority_receipt_fields")
    require(isinstance(fields, list) and len(fields) >= 8, "authority receipt fields")
    require(
        any("nonce" in value and "replay" in value for value in fields),
        "receipt replay contract",
    )
    require(not any(contract.get("claim_boundary", {}).values()), "positive cutover claim")


def main() -> int:
    try:
        machine = load_module(MACHINE, "trnm_cutover_machine_contract")
        derive = load_module(DERIVE, "trnm_cutover_derive_contract")
        validate_behavior(machine, derive)
        validate_contract(json.loads(CONTRACT.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValidationError, AssertionError) as error:
        print(f"cutover state-machine validation failed: {error}", file=sys.stderr)
        return 1
    print("cutover proposal-only behavioral contract: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
