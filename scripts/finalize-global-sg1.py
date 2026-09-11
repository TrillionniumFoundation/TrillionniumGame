#!/usr/bin/env python3
"""Require 14 exact, accepted family decisions before a separate global SG1 decision."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

class GlobalDecisionError(RuntimeError):
    pass

def require(value: bool, message: str) -> None:
    if not value:
        raise GlobalDecisionError(message)

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def finalize(index_path: Path, decisions: list[Path], global_input: Path, output: Path) -> dict:
    index = json.loads(index_path.read_text(encoding="utf-8"))
    global_decision = json.loads(global_input.read_text(encoding="utf-8"))
    require(index.get("family_count") == 14 and index.get("leaf_count") == 10173, "denominator authority")
    rows = [json.loads(path.read_text(encoding="utf-8")) for path in decisions]
    by_family = {row.get("family_id"): (path, row) for path, row in zip(decisions, rows)}
    expected = {row["family_id"] for row in index["families"]}
    require(set(by_family) == expected, "family decision set incomplete or changed")
    binding = None
    family_records = []
    reviewers = set()
    for family in sorted(expected):
        path, row = by_family[family]
        require(row.get("schema") == "trillionnium.denominator-family-decision.v1", f"{family}: schema")
        require(row.get("accepted") is True, f"{family}: not accepted")
        require(row.get("blocker_count") == 0, f"{family}: blockers remain")
        current = row.get("candidate_binding")
        require(binding is None or current == binding, f"{family}: candidate identity drift")
        binding = current
        login = row.get("reviewer", {}).get("login")
        require(isinstance(login, str) and login, f"{family}: reviewer")
        reviewers.add(login)
        family_records.append({"family_id": family, "decision_sha256": sha(path), "reviewer_login": login})
    reviewer = global_decision.get("reviewer", {})
    require(reviewer.get("conflict_free_attestation") is True, "global reviewer conflict attestation")
    require(isinstance(reviewer.get("login"), str) and reviewer["login"], "global reviewer login")
    require(reviewer["login"] not in reviewers, "global SG1 reviewer must be distinct from family reviewers")
    require(global_decision.get("candidate_binding") == binding, "global candidate binding")
    require(global_decision.get("decision") in {"accept", "reject"}, "global decision")
    accepted = global_decision["decision"] == "accept"
    result = {
        "schema": "trillionnium.global-sg1-decision.v1",
        "index_sha256": sha(index_path),
        "candidate_binding": binding,
        "family_count": 14,
        "leaf_count": 10173,
        "families": family_records,
        "global_reviewer": reviewer,
        "global_input_sha256": sha(global_input),
        "decision": global_decision["decision"],
        "global_sg1_accepted": accepted,
        "claim_boundary": {
            "complete_nakama_compatibility": False,
            "production_ready": False,
            "cutover_authorized": False,
            "nakama_retired": False,
        },
    }
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--global-decision", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("decisions", nargs="+", type=Path)
    args = parser.parse_args()
    result = finalize(args.index, args.decisions, args.global_decision, args.output)
    print(json.dumps({"global_sg1_accepted": result["global_sg1_accepted"], "families": result["family_count"]}, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
