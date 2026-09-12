#!/usr/bin/env python3
"""Validate an untrusted denominator-family decision as a proposal only.

Local JSON, reviewer login strings, attestations, digests and opaque evidence
identifiers cannot create an independently accepted decision. Output retains
only allowlisted reviewer fields; a future authority adapter must authenticate
immutable, replay-protected receipts and admitted evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

ALLOWED = {
    "compatible",
    "implemented-compatible",
    "intentional-divergence",
    "restricted-material-blocker",
    "not-applicable-with-rationale",
    "unimplemented-blocker",
}
EVIDENCE_REQUIRED = {
    "compatible",
    "implemented-compatible",
    "intentional-divergence",
}
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
BINDING_KEYS = (
    "repository",
    "source_head",
    "source_tree",
    "prospective_merge",
    "prospective_merge_tree",
)
UNTRUSTED_AUTHORITY_REASON = (
    "local decision JSON cannot authenticate reviewer identity, role, "
    "independence, evidence admission, expiry, nonce or replay state"
)


class DecisionError(RuntimeError):
    pass


def require(value: bool, message: str) -> None:
    if not value:
        raise DecisionError(message)


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode()


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def canonical_text(value: Any, label: str, maximum: int = 4096) -> str:
    require(
        isinstance(value, str)
        and value.strip() == value
        and bool(value)
        and len(value.encode("utf-8")) <= maximum
        and not any(ord(character) < 32 or ord(character) == 127 for character in value),
        label,
    )
    return value


def validate_binding(value: Any) -> dict[str, str]:
    require(isinstance(value, dict), "candidate binding")
    binding = {key: value.get(key) for key in BINDING_KEYS}
    require(
        binding["repository"] == "TrillionniumFoundation/TrillionniumGame",
        "repository binding",
    )
    for key in BINDING_KEYS[1:]:
        require(
            isinstance(binding[key], str) and HEX40.fullmatch(binding[key]) is not None,
            f"candidate binding {key}",
        )
    return binding  # type: ignore[return-value]


def validate_evidence_ids(value: Any, leaf_id: str, *, required: bool) -> list[str]:
    """Validate proposal references without treating them as admitted evidence."""
    require(isinstance(value, list), f"{leaf_id}: evidence IDs")
    require(
        all(
            isinstance(item, str)
            and item.strip() == item
            and bool(item)
            and len(item.encode("utf-8")) <= 256
            and not any(ord(character) < 32 or ord(character) == 127 for character in item)
            for item in value
        ),
        f"{leaf_id}: invalid evidence ID",
    )
    require(len(value) == len(set(value)), f"{leaf_id}: duplicate evidence ID")
    require(not required or bool(value), f"{leaf_id}: evidence reference required")
    return list(value)


def finalize(packet_path: Path, decision_path: Path, output: Path) -> dict[str, Any]:
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    require(
        packet.get("schema") == "trillionnium.denominator-family-review-packet.v1",
        "packet schema",
    )
    require(
        decision.get("schema") == "trillionnium.denominator-family-human-decision.v1",
        "decision schema",
    )
    require(decision.get("family_id") == packet.get("family_id"), "family identity")

    packet_sha256 = sha(packet_path)
    require(decision.get("packet_sha256") == packet_sha256, "packet digest binding")
    require(
        decision.get("source_manifest") == packet.get("source_manifest"),
        "source manifest binding",
    )
    require(
        decision.get("source_manifest_sha256") == packet.get("source_manifest_sha256"),
        "source manifest digest binding",
    )
    require(
        isinstance(packet.get("source_manifest_sha256"), str)
        and HEX64.fullmatch(packet["source_manifest_sha256"]) is not None,
        "packet source manifest digest",
    )

    binding = validate_binding(decision.get("candidate_binding"))
    binding_sha256 = digest_bytes(canonical(binding))
    candidate_author = canonical_text(
        decision.get("candidate_author"), "candidate author", 256
    )
    reviewer = decision.get("reviewer", {})
    require(isinstance(reviewer, dict), "claimed reviewer")
    reviewer_login = canonical_text(reviewer.get("login"), "reviewer login", 256)
    reviewer_role = canonical_text(reviewer.get("role"), "reviewer role", 256)
    require(
        reviewer.get("conflict_free_attestation") is True,
        "conflict-free attestation",
    )
    require(
        reviewer_login != candidate_author,
        "candidate author cannot self-attest",
    )
    require(
        reviewer.get("reviewed_binding_sha256") == binding_sha256,
        "reviewer candidate binding digest",
    )
    claimed_reviewer = {
        "login": reviewer_login,
        "role": reviewer_role,
        "conflict_free_attestation": True,
        "reviewed_binding_sha256": binding_sha256,
    }

    supplied = decision.get("leaves")
    require(isinstance(supplied, list), "leaf decisions")
    by_id = {row.get("leaf_id"): row for row in supplied if isinstance(row, dict)}
    require(len(by_id) == len(supplied), "duplicate or invalid leaf decisions")
    packet_leaves = packet.get("leaves")
    require(isinstance(packet_leaves, list), "packet leaves")
    expected_ids = {row["leaf_id"] for row in packet_leaves}
    require(set(by_id) == expected_ids, "leaf denominator changed or incomplete")

    blockers = 0
    proposal_rows = []
    referenced_evidence: set[str] = set()
    for leaf in packet_leaves:
        leaf_id = canonical_text(leaf.get("leaf_id"), "packet leaf ID", 512)
        source_leaf_sha256 = leaf.get("source_leaf_sha256")
        require(
            isinstance(source_leaf_sha256, str)
            and HEX64.fullmatch(source_leaf_sha256) is not None,
            f"{leaf_id}: source leaf hash",
        )
        row = by_id[leaf_id]
        require(
            row.get("source_leaf_sha256") == source_leaf_sha256,
            f"{leaf_id}: source hash",
        )
        classification = row.get("classification")
        require(classification in ALLOWED, f"{leaf_id}: classification")
        rationale = canonical_text(row.get("rationale"), f"{leaf_id}: rationale")
        evidence = validate_evidence_ids(
            row.get("evidence_ids"),
            leaf_id,
            required=classification in EVIDENCE_REQUIRED,
        )
        referenced_evidence.update(evidence)
        if classification in {
            "restricted-material-blocker",
            "unimplemented-blocker",
        }:
            blockers += 1
        proposal_rows.append(
            {
                "leaf_id": leaf_id,
                "source_leaf_sha256": source_leaf_sha256,
                "classification": classification,
                "rationale": rationale,
                "evidence_ids": evidence,
            }
        )

    requested_decision = decision.get("family_decision")
    require(requested_decision in {"accept", "reject"}, "family decision")
    result = {
        "schema": "trillionnium.denominator-family-decision-proposal.v1",
        "family_id": packet["family_id"],
        "packet_sha256": packet_sha256,
        "source_manifest": packet["source_manifest"],
        "source_manifest_sha256": packet["source_manifest_sha256"],
        "candidate_author": candidate_author,
        "candidate_binding": binding,
        "candidate_binding_sha256": binding_sha256,
        "claimed_reviewer": claimed_reviewer,
        "untrusted_decision_source_sha256": sha(decision_path),
        "leaf_count": len(proposal_rows),
        "blocker_count": blockers,
        "requested_family_decision": requested_decision,
        "proposal_complete": True,
        "referenced_evidence_ids": sorted(referenced_evidence),
        "authority": {
            "materialization_supported": False,
            "reviewer_identity_verified": False,
            "reviewer_role_verified": False,
            "reviewer_independence_verified": False,
            "evidence_admission_verified": False,
            "expiry_and_nonce_verified": False,
            "replay_protection_verified": False,
            "accepted": False,
            "reason": UNTRUSTED_AUTHORITY_REASON,
        },
        "accepted": False,
        "leaves": proposal_rows,
        "claim_boundary": {
            "family_acceptance": False,
            "global_sg1_accepted": False,
            "complete_nakama_compatibility": False,
            "production_ready": False,
        },
    }
    output.write_bytes(canonical(result))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--decision", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = finalize(args.packet, args.decision, args.output)
    print(
        json.dumps(
            {
                "family": report["family_id"],
                "proposal_complete": report["proposal_complete"],
                "accepted": report["accepted"],
                "blockers": report["blocker_count"],
                "authority_materialization_supported": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
