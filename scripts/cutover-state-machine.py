#!/usr/bin/env python3
"""Evaluate one cutover transition from accepted, exact evidence only."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

TRANSITIONS = {
    "planning": "shadow",
    "shadow": "exclusive-canary",
    "exclusive-canary": "production",
    "production": "retirement-pending",
    "retirement-pending": "retired",
}
REQUIRED_GATES = {
    "shadow": {"SG0", "SG1", "SG2", "SG3", "SG4"},
    "exclusive-canary": {"SG0", "SG1", "SG2", "SG3", "SG4", "SG5"},
    "production": {"SG0", "SG1", "SG2", "SG3", "SG4", "SG5", "SG8"},
    "retirement-pending": {"SG0", "SG1", "SG2", "SG3", "SG4", "SG5", "SG8"},
    "retired": {"SG0", "SG1", "SG2", "SG3", "SG4", "SG5", "SG8"},
}

class TransitionError(RuntimeError):
    pass

def require(value: bool, message: str) -> None:
    if not value:
        raise TransitionError(message)

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def validate_binding(binding: dict[str, Any]) -> None:
    require(binding.get("repository") == "TrillionniumFoundation/TrillionniumGame", "repository binding")
    for key in ("source_head", "source_tree", "prospective_merge", "prospective_merge_tree"):
        value = binding.get(key)
        require(isinstance(value, str) and len(value) == 40 and all(ch in "0123456789abcdef" for ch in value), f"candidate binding {key}")

def transition(current: dict[str, Any], request: dict[str, Any], blocker_packet: dict[str, Any]) -> dict[str, Any]:
    require(current.get("schema") == "trillionnium.cutover-state.v1", "current schema")
    require(request.get("schema") == "trillionnium.cutover-transition-request.v1", "request schema")
    source = current.get("state")
    target = request.get("target_state")
    require(TRANSITIONS.get(source) == target, "illegal or skipped transition")
    binding = request.get("candidate_binding")
    require(isinstance(binding, dict), "candidate binding")
    validate_binding(binding)
    existing_binding = current.get("candidate_binding")
    require(existing_binding in (None, binding), "candidate changed during promotion")
    require(blocker_packet.get("schema") == "trillionnium.cutover-blocker-packet.v1", "blocker packet schema")
    require(blocker_packet.get("candidate_binding") == binding, "blocker packet candidate mismatch")
    require(blocker_packet.get("open_p0_p1_count") == 0, "open P0/P1 blockers remain")
    require(blocker_packet.get("all_required_evidence_accepted") is True, "required evidence not accepted")
    require(blocker_packet.get("independent_review_complete") is True, "independent review incomplete")
    require(blocker_packet.get("governance_readback_complete") is True, "governance readback incomplete")

    accepted_gates = set(request.get("accepted_gates") or [])
    missing = REQUIRED_GATES[target] - accepted_gates
    require(not missing, "required gates missing: " + ",".join(sorted(missing)))
    reviewer = request.get("reviewer") or {}
    require(reviewer.get("conflict_free_attestation") is True, "reviewer conflict attestation")
    require(isinstance(reviewer.get("login"), str) and reviewer["login"], "reviewer login")
    require(reviewer["login"] != request.get("candidate_author"), "candidate author cannot approve transition")
    require(reviewer["login"] not in set(request.get("evidence_producers") or []), "evidence producer cannot approve own transition")
    rollback = request.get("rollback_packet") or {}
    require(rollback.get("accepted") is True, "accepted rollback packet required")
    require(isinstance(rollback.get("sha256"), str) and len(rollback["sha256"]) == 64, "rollback packet digest")
    require(request.get("ordinary_protected_admission") is True, "ordinary protected admission required")

    if target == "exclusive-canary":
        require(request.get("shadow_observation_accepted") is True, "shadow observation not accepted")
    if target == "production":
        require(request.get("exclusive_canary_accepted") is True, "exclusive canary not accepted")
        require(request.get("endurance_24h_accepted") is True, "24h endurance not accepted")
        require(request.get("endurance_72h_accepted") is True, "72h endurance not accepted")
        require(request.get("endurance_7d_accepted") is True, "7d endurance not accepted")
        require(request.get("approved_rpo_rto") is True, "RPO/RTO not approved")
    if target in {"retirement-pending", "retired"}:
        require(request.get("complete_nakama_compatibility") is True, "complete Nakama compatibility not accepted")
        require(request.get("global_sg1_accepted") is True, "global SG1 not accepted")
    if target == "retired":
        retirement = request.get("retirement_decision") or {}
        require(retirement.get("explicit") is True, "explicit retirement decision required")
        require(retirement.get("reviewer_login") != reviewer["login"], "retirement reviewer must be distinct")
        require(retirement.get("rollback_window_expired") is True, "rollback window not complete")

    history = list(current.get("history") or [])
    history.append({
        "from": source,
        "to": target,
        "request_sha256": request.get("request_sha256"),
        "reviewer_login": reviewer["login"],
        "accepted_gates": sorted(accepted_gates),
        "rollback_packet_sha256": rollback["sha256"],
    })
    return {
        "schema": "trillionnium.cutover-state.v1",
        "state": target,
        "candidate_binding": binding,
        "history": history,
        "claims": {
            "public_online": target in {"production", "retirement-pending", "retired"},
            "cutover_authorized": target in {"production", "retirement-pending", "retired"},
            "nakama_retired": target == "retired",
        },
    }

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--blockers", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    current = json.loads(args.current.read_text(encoding="utf-8"))
    request = json.loads(args.request.read_text(encoding="utf-8"))
    request["request_sha256"] = sha(args.request)
    blockers = json.loads(args.blockers.read_text(encoding="utf-8"))
    result = transition(current, request, blockers)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"from": current["state"], "to": result["state"], "claims": result["claims"]}, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
