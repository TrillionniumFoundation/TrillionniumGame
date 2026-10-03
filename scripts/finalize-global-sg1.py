#!/usr/bin/env python3
"""Assemble fourteen untrusted family proposals into a non-authoritative SG1 proposal.

No local file can materialize family or global SG1 acceptance. Inputs reject
duplicate keys/non-finite numbers and outputs retain only allowlisted reviewer
fields. A future authority service must verify immutable receipts, evidence,
principal qualification, expiry, nonce and durable anti-replay state.
"""
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
MAX_JSON_BYTES = 8 * 1024 * 1024


class GlobalDecisionError(RuntimeError):
    pass


def require(value: bool, message: str) -> None:
    if not value:
        raise GlobalDecisionError(message)


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def reject_non_finite(value: str) -> None:
    raise GlobalDecisionError(f"non-finite JSON number: {value}")


def load_object(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    require(len(data) <= MAX_JSON_BYTES, f"JSON document too large: {path}")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise GlobalDecisionError(f"JSON document is not UTF-8: {path}") from error
    try:
        value = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_non_finite,
        )
    except json.JSONDecodeError as error:
        raise GlobalDecisionError(f"invalid JSON: {path}") from error
    require(isinstance(value, dict), f"top-level JSON object required: {path}")
    return value


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


def canonical_text(value: Any, label: str, maximum: int = 256) -> str:
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


def sanitized_reviewer(
    value: Any,
    *,
    binding_sha256: str,
    label: str,
) -> dict[str, Any]:
    require(isinstance(value, dict), label)
    login = canonical_text(value.get("login"), f"{label} login")
    role = canonical_text(value.get("role"), f"{label} role")
    require(
        value.get("conflict_free_attestation") is True,
        f"{label} conflict-free attestation",
    )
    require(
        value.get("reviewed_binding_sha256") == binding_sha256,
        f"{label} candidate binding digest",
    )
    return {
        "login": login,
        "role": role,
        "conflict_free_attestation": True,
        "reviewed_binding_sha256": binding_sha256,
    }


def finalize(
    index_path: Path,
    decisions: list[Path],
    global_input: Path,
    output: Path,
) -> dict[str, Any]:
    index = load_object(index_path)
    global_proposal = load_object(global_input)
    require(
        index.get("schema") == "trillionnium.denominator-family-review-index.v1",
        "index schema",
    )
    require(
        index.get("family_count") == 14 and index.get("leaf_count") == 10173,
        "denominator authority",
    )
    index_family_rows = index.get("families")
    require(isinstance(index_family_rows, list), "index family rows")
    index_rows = {
        row.get("family_id"): row
        for row in index_family_rows
        if isinstance(row, dict) and isinstance(row.get("family_id"), str)
    }
    require(
        len(index_rows) == len(index_family_rows) == 14,
        "duplicate or invalid index family",
    )
    require(
        sum(
            row.get("leaf_count", -1)
            for row in index_family_rows
            if isinstance(row.get("leaf_count"), int)
            and not isinstance(row.get("leaf_count"), bool)
        )
        == 10173,
        "index family leaf total",
    )
    require(len(decisions) == 14, "exactly 14 family proposal files required")

    rows = [load_object(path) for path in decisions]
    family_ids = [row.get("family_id") for row in rows]
    require(len(family_ids) == len(set(family_ids)), "duplicate family proposal")
    by_family = {
        row.get("family_id"): (path, row) for path, row in zip(decisions, rows)
    }
    expected = set(index_rows)
    require(set(by_family) == expected, "family proposal set incomplete or changed")

    binding: dict[str, str] | None = None
    binding_sha256: str | None = None
    candidate_author: str | None = None
    family_records = []
    total_blockers = 0
    total_leaves = 0
    claimed_reviewers = []
    for family in sorted(expected):
        path, row = by_family[family]
        authority = index_rows[family]
        require(
            row.get("schema")
            == "trillionnium.denominator-family-decision-proposal.v1",
            f"{family}: proposal schema",
        )
        require(row.get("proposal_complete") is True, f"{family}: incomplete proposal")
        require(row.get("accepted") is False, f"{family}: forged accepted flag")
        authority_state = row.get("authority")
        require(isinstance(authority_state, dict), f"{family}: authority boundary")
        require(
            authority_state.get("materialization_supported") is False
            and authority_state.get("accepted") is False,
            f"{family}: local authority materialization is forbidden",
        )
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
        leaf_count = row.get("leaf_count")
        require(
            isinstance(leaf_count, int)
            and not isinstance(leaf_count, bool)
            and leaf_count >= 0
            and leaf_count == authority.get("leaf_count"),
            f"{family}: leaf count drift",
        )
        total_leaves += leaf_count
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

        current_author = canonical_text(
            row.get("candidate_author"), f"{family}: candidate author"
        )
        require(
            candidate_author is None or current_author == candidate_author,
            f"{family}: candidate author drift",
        )
        candidate_author = current_author

        reviewer = sanitized_reviewer(
            row.get("claimed_reviewer"),
            binding_sha256=current_sha256,
            label=f"{family}: claimed reviewer",
        )
        claimed_reviewers.append(reviewer["login"])
        blockers = row.get("blocker_count")
        require(
            isinstance(blockers, int)
            and not isinstance(blockers, bool)
            and blockers >= 0,
            f"{family}: blockers",
        )
        total_blockers += blockers
        family_records.append(
            {
                "family_id": family,
                "proposal_sha256": sha(path),
                "packet_sha256": row["packet_sha256"],
                "claimed_reviewer": reviewer,
                "blocker_count": blockers,
                "requested_family_decision": row.get("requested_family_decision"),
                "accepted": False,
            }
        )

    require(total_leaves == 10173, "family proposal leaf total")
    assert binding is not None
    assert binding_sha256 is not None
    assert candidate_author is not None
    require(
        global_proposal.get("schema")
        == "trillionnium.global-sg1-human-decision.v1",
        "global proposal schema",
    )
    require(global_proposal.get("index_sha256") == sha(index_path), "global index digest")
    supplied_family_digests = global_proposal.get("family_decisions")
    require(isinstance(supplied_family_digests, list), "global family proposal digests")
    supplied_by_family = {
        row.get("family_id"): row.get("decision_sha256")
        for row in supplied_family_digests
        if isinstance(row, dict)
    }
    require(
        len(supplied_by_family) == len(supplied_family_digests) == 14,
        "duplicate or invalid global family proposal digest",
    )
    require(
        supplied_by_family
        == {row["family_id"]: row["proposal_sha256"] for row in family_records},
        "global family proposal digest drift",
    )
    require(global_proposal.get("candidate_binding") == binding, "global candidate binding")
    require(
        global_proposal.get("candidate_binding_sha256") == binding_sha256,
        "global candidate binding digest",
    )
    require(
        global_proposal.get("candidate_author") == candidate_author,
        "global candidate author",
    )
    claimed_global_reviewer = sanitized_reviewer(
        global_proposal.get("reviewer"),
        binding_sha256=binding_sha256,
        label="claimed global reviewer",
    )
    require(
        claimed_global_reviewer["login"] != candidate_author,
        "candidate author cannot self-attest global proposal",
    )
    require(
        global_proposal.get("decision") in {"accept", "reject"},
        "global requested decision",
    )

    result = {
        "schema": "trillionnium.global-sg1-proposal.v1",
        "index_sha256": sha(index_path),
        "candidate_author": candidate_author,
        "candidate_binding": binding,
        "candidate_binding_sha256": binding_sha256,
        "family_count": 14,
        "leaf_count": 10173,
        "families": family_records,
        "claimed_family_reviewer_logins": claimed_reviewers,
        "claimed_global_reviewer": claimed_global_reviewer,
        "untrusted_global_input_sha256": sha(global_input),
        "requested_decision": global_proposal["decision"],
        "proposal_bundle_complete": True,
        "blocker_count": total_blockers,
        "authority": {
            "materialization_supported": False,
            "reviewer_identity_verified": False,
            "reviewer_role_verified": False,
            "principal_separation_verified": False,
            "family_receipts_verified": False,
            "evidence_admission_verified": False,
            "expiry_and_nonce_verified": False,
            "replay_protection_verified": False,
            "global_sg1_accepted": False,
            "reason": "local proposal files cannot create family or global SG1 authority",
        },
        "global_sg1_accepted": False,
        "claim_boundary": {
            "complete_nakama_compatibility": False,
            "production_ready": False,
            "cutover_authorized": False,
            "nakama_retired": False,
        },
    }
    output.write_bytes(canonical(result))
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
                "proposal_bundle_complete": result["proposal_bundle_complete"],
                "global_sg1_accepted": False,
                "families": result["family_count"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
