#!/usr/bin/env python3
"""Bind a human-supplied leaf-complete family decision without self-approval."""
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


class DecisionError(RuntimeError):
    pass


def require(value: bool, message: str) -> None:
    if not value:
        raise DecisionError(message)


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha(path: Path) -> str:
    return digest_bytes(path.read_bytes())


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
    require(isinstance(value, list), f"{leaf_id}: evidence IDs")
    require(
        all(
            isinstance(item, str)
            and item.strip() == item
            and bool(item)
            and len(item) <= 256
            for item in value
        ),
        f"{leaf_id}: invalid evidence ID",
    )
    require(len(value) == len(set(value)), f"{leaf_id}: duplicate evidence ID")
    require(not required or bool(value), f"{leaf_id}: evidence required")
    return value


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
    candidate_author = decision.get("candidate_author")
    require(
        isinstance(candidate_author, str)
        and candidate_author.strip() == candidate_author
        and bool(candidate_author),
        "candidate author",
    )
    reviewer = decision.get("reviewer", {})
    require(
        isinstance(reviewer.get("login"), str) and reviewer["login"],
        "reviewer login",
    )
    require(
        isinstance(reviewer.get("role"), str) and reviewer["role"],
        "reviewer role",
    )
    require(
        reviewer.get("conflict_free_attestation") is True,
        "conflict-free attestation",
    )
    require(
        reviewer.get("login") != candidate_author,
        "candidate author cannot self-approve",
    )
    require(
        reviewer.get("reviewed_binding_sha256") == binding_sha256,
        "reviewer candidate binding digest",
    )

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
        require(
            row.get("source_leaf_sha256") == leaf["source_leaf_sha256"],
            f"{leaf['leaf_id']}: source hash",
        )
        classification = row.get("classification")
        require(classification in ALLOWED, f"{leaf['leaf_id']}: classification")
        rationale = row.get("rationale")
        require(
            isinstance(rationale, str) and rationale.strip(),
            f"{leaf['leaf_id']}: rationale",
        )
        evidence = validate_evidence_ids(
            row.get("evidence_ids"),
            leaf["leaf_id"],
            required=classification in EVIDENCE_REQUIRED,
        )
        if classification in {
            "restricted-material-blocker",
            "unimplemented-blocker",
        }:
            blockers += 1
        accepted_rows.append(
            {
                "leaf_id": leaf["leaf_id"],
                "source_leaf_sha256": leaf["source_leaf_sha256"],
                "classification": classification,
                "rationale": rationale.strip(),
                "evidence_ids": evidence,
            }
        )

    require(
        decision.get("family_decision") in {"accept", "reject"},
        "family decision",
    )
    accepted = decision["family_decision"] == "accept" and blockers == 0
    result = {
        "schema": "trillionnium.denominator-family-decision.v1",
        "family_id": packet["family_id"],
        "packet_sha256": packet_sha256,
        "source_manifest": packet["source_manifest"],
        "source_manifest_sha256": packet["source_manifest_sha256"],
        "candidate_author": candidate_author,
        "candidate_binding": binding,
        "candidate_binding_sha256": binding_sha256,
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
    print(
        json.dumps(
            {
                "family": report["family_id"],
                "accepted": report["accepted"],
                "blockers": report["blocker_count"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
