#!/usr/bin/env python3
"""Require 14 exact, accepted family decisions before a separate global SG1 decision."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
BINDING_KEYS = (
    "repository",
    "source_head",
    "source_tree",
    "prospective_merge",
    "prospective_merge_tree",
)


class GlobalDecisionError(RuntimeError):
    pass


def require(value: bool, message: str) -> None:
    if not value:
        raise GlobalDecisionError(message)


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


def finalize(
    index_path: Path,
    decisions: list[Path],
    global_input: Path,
    output: Path,
) -> dict[str, Any]:
    index = json.loads(index_path.read_text(encoding="utf-8"))
    global_decision = json.loads(global_input.read_text(encoding="utf-8"))
    require(
        index.get("schema") == "trillionnium.denominator-family-review-index.v1",
        "index schema",
    )
    require(
        index.get("family_count") == 14 and index.get("leaf_count") == 10173,
        "denominator authority",
    )
    require(len(decisions) == 14, "exactly 14 family decision files required")

    rows = [json.loads(path.read_text(encoding="utf-8")) for path in decisions]
    family_ids = [row.get("family_id") for row in rows]
    require(len(family_ids) == len(set(family_ids)), "duplicate family decision")
    by_family = {
        row.get("family_id"): (path, row) for path, row in zip(decisions, rows)
    }
    index_rows = {row["family_id"]: row for row in index["families"]}
    expected = set(index_rows)
    require(set(by_family) == expected, "family decision set incomplete or changed")

    binding: dict[str, str] | None = None
    binding_sha256: str | None = None
    candidate_author: str | None = None
    family_records = []
    reviewers = set()
    for family in sorted(expected):
        path, row = by_family[family]
        authority = index_rows[family]
        require(
            row.get("schema") == "trillionnium.denominator-family-decision.v1",
            f"{family}: schema",
        )
        require(row.get("accepted") is True, f"{family}: not accepted")
        require(row.get("blocker_count") == 0, f"{family}: blockers remain")
        require(
            row.get("packet_sha256") == authority.get("packet_sha256"),
            f"{family}: packet digest drift",
        )
        require(
            row.get("source_manifest") == authority.get("source_manifest"),
            f"{family}: source manifest drift",
        )
        require(
            row.get("source_manifest_sha256")
            == authority.get("source_manifest_sha256"),
            f"{family}: source manifest digest drift",
        )
        require(
            row.get("leaf_count") == authority.get("leaf_count"),
            f"{family}: leaf count drift",
        )
        require(
            isinstance(row.get("packet_sha256"), str)
            and HEX64.fullmatch(row["packet_sha256"]) is not None,
            f"{family}: packet digest",
        )

        current = validate_binding(row.get("candidate_binding"))
        current_sha256 = digest_bytes(canonical(current))
        require(
            row.get("candidate_binding_sha256") == current_sha256,
            f"{family}: candidate binding digest",
        )
        require(binding is None or current == binding, f"{family}: candidate identity drift")
        require(
            binding_sha256 is None or current_sha256 == binding_sha256,
            f"{family}: candidate binding digest drift",
        )
        binding = current
        binding_sha256 = current_sha256

        current_author = row.get("candidate_author")
        require(
            isinstance(current_author, str) and current_author,
            f"{family}: candidate author",
        )
        require(
            candidate_author is None or current_author == candidate_author,
            f"{family}: candidate author drift",
        )
        candidate_author = current_author

        reviewer = row.get("reviewer", {})
        login = reviewer.get("login")
        require(isinstance(login, str) and login, f"{family}: reviewer")
        require(
            reviewer.get("conflict_free_attestation") is True,
            f"{family}: reviewer conflict attestation",
        )
        require(login != current_author, f"{family}: author reviewer conflict")
        require(
            reviewer.get("reviewed_binding_sha256") == current_sha256,
            f"{family}: reviewer binding drift",
        )
        reviewers.add(login)
        family_records.append(
            {
                "family_id": family,
                "decision_sha256": sha(path),
                "packet_sha256": row["packet_sha256"],
                "reviewer_login": login,
            }
        )

    assert binding is not None
    assert binding_sha256 is not None
    assert candidate_author is not None
    require(
        global_decision.get("schema") == "trillionnium.global-sg1-human-decision.v1",
        "global decision schema",
    )
    require(global_decision.get("index_sha256") == sha(index_path), "global index digest")
    supplied_family_digests = global_decision.get("family_decisions")
    require(isinstance(supplied_family_digests, list), "global family decision digests")
    supplied_by_family = {
        row.get("family_id"): row.get("decision_sha256")
        for row in supplied_family_digests
        if isinstance(row, dict)
    }
    require(
        len(supplied_by_family) == len(supplied_family_digests) == 14,
        "duplicate or invalid global family decision digest",
    )
    require(
        supplied_by_family
        == {row["family_id"]: row["decision_sha256"] for row in family_records},
        "global family decision digest drift",
    )
    require(
        global_decision.get("candidate_binding") == binding,
        "global candidate binding",
    )
    require(
        global_decision.get("candidate_binding_sha256") == binding_sha256,
        "global candidate binding digest",
    )
    require(
        global_decision.get("candidate_author") == candidate_author,
        "global candidate author",
    )

    reviewer = global_decision.get("reviewer", {})
    require(
        reviewer.get("conflict_free_attestation") is True,
        "global reviewer conflict attestation",
    )
    require(
        isinstance(reviewer.get("login"), str) and reviewer["login"],
        "global reviewer login",
    )
    require(
        reviewer.get("reviewed_binding_sha256") == binding_sha256,
        "global reviewer binding digest",
    )
    require(
        reviewer["login"] not in reviewers,
        "global SG1 reviewer must be distinct from family reviewers",
    )
    require(
        reviewer["login"] != candidate_author,
        "candidate author cannot accept global SG1",
    )
    require(
        global_decision.get("decision") in {"accept", "reject"},
        "global decision",
    )
    accepted = global_decision["decision"] == "accept"
    result = {
        "schema": "trillionnium.global-sg1-decision.v1",
        "index_sha256": sha(index_path),
        "candidate_author": candidate_author,
        "candidate_binding": binding,
        "candidate_binding_sha256": binding_sha256,
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
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--global-decision", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("decisions", nargs="+", type=Path)
    args = parser.parse_args()
    result = finalize(args.index, args.decisions, args.global_decision, args.output)
    print(
        json.dumps(
            {
                "global_sg1_accepted": result["global_sg1_accepted"],
                "families": result["family_count"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
