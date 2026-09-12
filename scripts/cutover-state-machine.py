#!/usr/bin/env python3
"""Validate a cutover request and emit a non-authoritative proposal only.

The repository does not contain a trusted cutover authority verifier or durable
anti-replay store.  Consequently local request, blocker, reviewer, gate,
rollback or admission JSON can never advance the authoritative state or set an
online, cutover or retirement claim.  A future materializer must consume
externally authenticated receipts and platform-native protected-admission
readback outside candidate-controlled writes.
"""
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
FALSE_CLAIMS = {
    "public_online": False,
    "cutover_authorized": False,
    "nakama_retired": False,
}
AUTHORITY_REASON = "trusted authority receipt verifier is not implemented"


class TransitionError(RuntimeError):
    pass


def require(value: bool, message: str) -> None:
    if not value:
        raise TransitionError(message)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_digest(value: Any) -> str:
    encoded = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def validate_binding(binding: dict[str, Any]) -> None:
    require(
        binding.get("repository") == "TrillionniumFoundation/TrillionniumGame",
        "repository binding",
    )
    for key in (
        "source_head",
        "source_tree",
        "prospective_merge",
        "prospective_merge_tree",
    ):
        value = binding.get(key)
        require(
            isinstance(value, str)
            and len(value) == 40
            and all(character in "0123456789abcdef" for character in value),
            f"candidate binding {key}",
        )


def validate_proposal_inputs(
    current: dict[str, Any],
    request: dict[str, Any],
    blocker_packet: dict[str, Any],
) -> tuple[str, str, dict[str, Any], set[str], dict[str, Any], dict[str, Any]]:
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

    require(
        blocker_packet.get("schema") == "trillionnium.cutover-blocker-packet.v1",
        "blocker packet schema",
    )
    require(
        blocker_packet.get("candidate_binding") == binding,
        "blocker packet candidate mismatch",
    )
    require(blocker_packet.get("open_p0_p1_count") == 0, "open P0/P1 blockers remain")
    require(
        blocker_packet.get("all_required_evidence_accepted") is True,
        "required evidence not accepted",
    )
    require(
        blocker_packet.get("independent_review_complete") is True,
        "independent review incomplete",
    )
    require(
        blocker_packet.get("governance_readback_complete") is True,
        "governance readback incomplete",
    )

    accepted_gates = set(request.get("accepted_gates") or [])
    missing = REQUIRED_GATES[target] - accepted_gates
    require(not missing, "required gates missing: " + ",".join(sorted(missing)))
    reviewer = request.get("reviewer") or {}
    require(
        reviewer.get("conflict_free_attestation") is True,
        "reviewer conflict attestation",
    )
    require(
        isinstance(reviewer.get("login"), str) and reviewer["login"],
        "reviewer login",
    )
    require(
        reviewer["login"] != request.get("candidate_author"),
        "candidate author cannot approve transition",
    )
    require(
        reviewer["login"] not in set(request.get("evidence_producers") or []),
        "evidence producer cannot approve own transition",
    )
    rollback = request.get("rollback_packet") or {}
    require(rollback.get("accepted") is True, "accepted rollback packet required")
    require(
        isinstance(rollback.get("sha256"), str)
        and len(rollback["sha256"]) == 64
        and all(character in "0123456789abcdef" for character in rollback["sha256"]),
        "rollback packet digest",
    )
    require(
        request.get("ordinary_protected_admission") is True,
        "ordinary protected admission required",
    )

    if target == "exclusive-canary":
        require(
            request.get("shadow_observation_accepted") is True,
            "shadow observation not accepted",
        )
    if target == "production":
        require(
            request.get("exclusive_canary_accepted") is True,
            "exclusive canary not accepted",
        )
        require(
            request.get("endurance_24h_accepted") is True,
            "24h endurance not accepted",
        )
        require(
            request.get("endurance_72h_accepted") is True,
            "72h endurance not accepted",
        )
        require(
            request.get("endurance_7d_accepted") is True,
            "7d endurance not accepted",
        )
        require(request.get("approved_rpo_rto") is True, "RPO/RTO not approved")
    if target in {"retirement-pending", "retired"}:
        require(
            request.get("complete_nakama_compatibility") is True,
            "complete Nakama compatibility not accepted",
        )
        require(
            request.get("global_sg1_accepted") is True,
            "global SG1 not accepted",
        )
    if target == "retired":
        retirement = request.get("retirement_decision") or {}
        require(retirement.get("explicit") is True, "explicit retirement decision required")
        require(
            retirement.get("reviewer_login") != reviewer["login"],
            "retirement reviewer must be distinct",
        )
        require(
            retirement.get("rollback_window_expired") is True,
            "rollback window not complete",
        )

    return source, target, binding, accepted_gates, reviewer, rollback


def transition(
    current: dict[str, Any],
    request: dict[str, Any],
    blocker_packet: dict[str, Any],
) -> dict[str, Any]:
    """Return a proposal while preserving the authoritative current state."""
    source, target, binding, accepted_gates, reviewer, rollback = validate_proposal_inputs(
        current, request, blocker_packet
    )
    request_digest = request.get("request_sha256")
    require(
        isinstance(request_digest, str)
        and len(request_digest) == 64
        and all(character in "0123456789abcdef" for character in request_digest),
        "request digest",
    )
    blocker_digest = request.get("blocker_packet_sha256")
    if blocker_digest is None:
        blocker_digest = canonical_digest(blocker_packet)

    pending = {
        "from": source,
        "to": target,
        "candidate_binding": binding,
        "request_sha256": request_digest,
        "blocker_packet_sha256": blocker_digest,
        "claimed_reviewer_login": reviewer["login"],
        "claimed_accepted_gates": sorted(accepted_gates),
        "claimed_rollback_packet_sha256": rollback["sha256"],
        "local_inputs_authenticated": False,
        "authority_materialized": False,
    }
    return {
        "schema": "trillionnium.cutover-state.v1",
        "state": source,
        "candidate_binding": current.get("candidate_binding"),
        "history": list(current.get("history") or []),
        "pending_proposal": pending,
        "authority": {
            "materialization_supported": False,
            "trusted_receipt_verified": False,
            "platform_admission_verified": False,
            "reviewer_identity_verified": False,
            "reviewer_role_verified": False,
            "reviewer_independence_verified": False,
            "evidence_admission_verified": False,
            "expiry_and_nonce_verified": False,
            "replay_protection_verified": False,
            "reason": AUTHORITY_REASON,
        },
        "claims": dict(FALSE_CLAIMS),
    }


def materialize_transition(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    """Reject local authority materialization until a trusted verifier exists."""
    raise TransitionError(AUTHORITY_REASON)


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
    request["blocker_packet_sha256"] = sha(args.blockers)
    blockers = json.loads(args.blockers.read_text(encoding="utf-8"))
    result = transition(current, request, blockers)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "current_state": current["state"],
                "proposed_state": result["pending_proposal"]["to"],
                "authority_materialized": False,
                "claims": result["claims"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
