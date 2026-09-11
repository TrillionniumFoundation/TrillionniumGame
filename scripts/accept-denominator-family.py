#!/usr/bin/env python3
"""Bind a human-supplied leaf-complete family decision without self-approval."""
from __future__ import annotations

import argparse
import hashlib
import json
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

class DecisionError(RuntimeError):
    pass

def require(value: bool, message: str) -> None:
    if not value:
        raise DecisionError(message)

def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def finalize(packet_path: Path, decision_path: Path, output: Path) -> dict[str, Any]:
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    require(packet.get("schema") == "trillionnium.denominator-family-review-packet.v1", "packet schema")
    require(decision.get("schema") == "trillionnium.denominator-family-human-decision.v1", "decision schema")
    require(decision.get("family_id") == packet.get("family_id"), "family identity")
    reviewer = decision.get("reviewer", {})
    require(isinstance(reviewer.get("login"), str) and reviewer["login"], "reviewer login")
    require(isinstance(reviewer.get("role"), str) and reviewer["role"], "reviewer role")
    require(reviewer.get("conflict_free_attestation") is True, "conflict-free attestation")
    require(reviewer.get("login") != decision.get("candidate_author"), "candidate author cannot self-approve")
    binding = decision.get("candidate_binding", {})
    for key in ("repository", "source_head", "source_tree", "prospective_merge", "prospective_merge_tree"):
        require(isinstance(binding.get(key), str) and binding[key], f"candidate binding {key}")
    require(binding["repository"] == "TrillionniumFoundation/TrillionniumGame", "repository binding")
    supplied = decision.get("leaves")
    require(isinstance(supplied, list), "leaf decisions")
    by_id = {row.get("leaf_id"): row for row in supplied if isinstance(row, dict)}
    require(len(by_id) == len(supplied), "duplicate or invalid leaf decisions")
    expected_ids = {row["leaf_id"] for row in packet["leaves"]}
    require(set(by_id) == expected_ids, "leaf denominator changed or incomplete")
    blockers = 0
    accepted_rows = []
    for leaf in packet["leaves"]:
        row = by_id[leaf["leaf_id"]]
        require(row.get("source_leaf_sha256") == leaf["source_leaf_sha256"], f"{leaf['leaf_id']}: source hash")
        classification = row.get("classification")
        require(classification in ALLOWED, f"{leaf['leaf_id']}: classification")
        rationale = row.get("rationale")
        require(isinstance(rationale, str) and rationale.strip(), f"{leaf['leaf_id']}: rationale")
        evidence = row.get("evidence_ids")
        require(isinstance(evidence, list), f"{leaf['leaf_id']}: evidence IDs")
        if classification in {"restricted-material-blocker", "unimplemented-blocker"}:
            blockers += 1
        accepted_rows.append({
            "leaf_id": leaf["leaf_id"],
            "source_leaf_sha256": leaf["source_leaf_sha256"],
            "classification": classification,
            "rationale": rationale.strip(),
            "evidence_ids": evidence,
        })
    require(decision.get("family_decision") in {"accept", "reject"}, "family decision")
    accepted = decision["family_decision"] == "accept" and blockers == 0
    result = {
        "schema": "trillionnium.denominator-family-decision.v1",
        "family_id": packet["family_id"],
        "packet_sha256": sha(packet_path),
        "source_manifest": packet["source_manifest"],
        "source_manifest_sha256": packet["source_manifest_sha256"],
        "candidate_binding": binding,
        "reviewer": reviewer,
        "decision_source_sha256": sha(decision_path),
        "leaf_count": len(accepted_rows),
        "blocker_count": blockers,
        "family_decision": decision["family_decision"],
        "accepted": accepted,
        "leaves": accepted_rows,
        "claim_boundary": {
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
    print(json.dumps({"family": report["family_id"], "accepted": report["accepted"], "blockers": report["blocker_count"]}, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
