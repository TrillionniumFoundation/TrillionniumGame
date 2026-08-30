#!/usr/bin/env python3
"""Validate the complete denominator review input without granting SG1 credit.

The repository has two supported representations:

* a compact materialization status plus draft packet; or
* the original immutable worklist, fourteen candidate manifests and fourteen
  per-denominator review requests.

The second representation is authoritative when the compact packet is absent.
It proves that every leaf has a conservative review proposal and is ready to be
reviewed. It never converts those proposals into accepted classifications.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / "docs/status/DENOMINATOR_MATERIALIZATION.json"
DRAFT = ROOT / "manifests/upstream/denominator-review-packet.draft.json"
WORKLIST = ROOT / "manifests/upstream/denominator-review-worklist.json"
EXPECTED = {
    "DEN-SOURCE",
    "DEN-API",
    "DEN-RTAPI",
    "DEN-CONSOLE",
    "DEN-RUNTIME",
    "DEN-CONFIG",
    "DEN-CLI",
    "DEN-DB",
    "DEN-DATA",
    "DEN-METRICS",
    "DEN-OPS",
    "DEN-PROVIDERS",
    "DEN-IAP",
    "DEN-SDK",
}
FORBIDDEN_TRUE_CLAIMS = {
    "sg1_eligible",
    "compatibility_credit",
    "production_ready",
    "public_online",
    "nakama_retired",
    "cutover_authorized",
}


class ContractError(RuntimeError):
    """Raised when the materialized review input violates its contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        try:
            label = path.relative_to(ROOT)
        except ValueError:
            label = path
        raise ContractError(f"{label}: invalid JSON: {error}") from error
    require(isinstance(value, dict), f"{path}: top level must be an object")
    return value


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_value(value: Any, label: str) -> str:
    require(isinstance(value, str), f"{label}: SHA-256 must be a string")
    digest = value.removeprefix("sha256:")
    require(
        len(digest) == 64 and all(character in "0123456789abcdef" for character in digest),
        f"{label}: invalid SHA-256",
    )
    return digest


def reject_true_claims(value: Any, label: str) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in FORBIDDEN_TRUE_CLAIMS and nested is True:
                raise ContractError(f"{label}: forbidden true claim {key}")
            reject_true_claims(nested, label)
    elif isinstance(value, list):
        for nested in value:
            reject_true_claims(nested, label)


def artifact_descriptor(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "path": str(path.relative_to(ROOT)),
        "sha256": sha256(data),
        "size_bytes": len(data),
    }


def validate_artifact(value: Any, label: str) -> dict[str, Any]:
    require(isinstance(value, dict), f"{label}: artifact descriptor missing")
    relative = value.get("path")
    require(isinstance(relative, str) and relative, f"{label}: path missing")
    expected_digest = digest_value(value.get("sha256"), f"{label} digest")
    expected_size = value.get("size_bytes")
    require(isinstance(expected_size, int) and expected_size > 0, f"{label}: invalid size")
    path = ROOT / relative
    require(path.is_file(), f"{label}: file missing: {relative}")
    data = path.read_bytes()
    require(len(data) == expected_size, f"{label}: size mismatch")
    require(sha256(data) == expected_digest, f"{label}: digest mismatch")
    payload = load_json(path)
    reject_true_claims(payload, label)
    return payload


def validate_compact_packet() -> dict[str, Any]:
    status = load_json(STATUS)
    draft = load_json(DRAFT)
    reject_true_claims(status, "materialization status")
    reject_true_claims(draft, "draft review packet")
    require(
        status.get("schema") == "trillionnium.denominator-materialization.v1",
        "wrong materialization status schema",
    )
    require(status.get("project_id") == "trillionnium-game", "wrong project ID")
    require(
        draft.get("schema") == "trillionnium.denominator-review-packet.draft.v1",
        "wrong draft packet schema",
    )
    require(draft.get("project_id") == "trillionnium-game", "wrong draft project ID")

    rows = draft.get("denominators")
    require(isinstance(rows, list), "draft denominators must be a list")
    identifiers = [row.get("id") for row in rows if isinstance(row, dict)]
    require(len(identifiers) == len(set(identifiers)), "duplicate denominator rows")
    require(set(identifiers) <= EXPECTED, "unknown denominator row")

    totals = {
        "leaf_count_total": 0,
        "classified_count": 0,
        "unclassified_count": 0,
        "owner_bound_count": 0,
        "task_bound_count": 0,
        "test_bound_count": 0,
    }
    pending = True
    for row in rows:
        require(isinstance(row, dict), "denominator row must be an object")
        identifier = row["id"]
        manifest = validate_artifact(row.get("manifest"), f"{identifier} manifest")
        require(
            manifest.get("denominator") == identifier,
            f"{identifier}: embedded denominator mismatch",
        )
        leaf_count = row.get("leaf_count")
        require(isinstance(leaf_count, int) and leaf_count > 0, f"{identifier}: empty leaves")
        embedded_count = manifest.get("leaf_count")
        if embedded_count is not None:
            require(embedded_count == leaf_count, f"{identifier}: embedded leaf count mismatch")
        leaves = manifest.get("leaves")
        if isinstance(leaves, list):
            require(len(leaves) == leaf_count, f"{identifier}: leaf array mismatch")
        for key in totals.keys() - {"leaf_count_total"}:
            value = row.get(key)
            require(isinstance(value, int) and value >= 0, f"{identifier}: invalid {key}")
            require(value <= leaf_count, f"{identifier}: {key} exceeds leaves")
            totals[key] += value
        require(
            row["classified_count"] + row["unclassified_count"] == leaf_count,
            f"{identifier}: classification arithmetic mismatch",
        )
        totals["leaf_count_total"] += leaf_count
        pending &= row.get("review_status") == "pending-independent-review"

    missing = sorted(EXPECTED - set(identifiers))
    declared_missing = draft.get("missing_denominators")
    require(isinstance(declared_missing, list), "draft missing_denominators must be a list")
    require(sorted(declared_missing) == missing, "draft missing denominator list mismatch")

    aggregate = draft.get("aggregate")
    require(isinstance(aggregate, dict), "draft aggregate missing")
    require(aggregate.get("denominator_count") == len(rows), "aggregate denominator count mismatch")
    for key, value in totals.items():
        require(aggregate.get(key) == value, f"aggregate {key} mismatch")
    digest_value(aggregate.get("manifest_sha256"), "aggregate manifest digest")

    materialized = status.get("materialized_denominators")
    require(isinstance(materialized, list), "status materialized_denominators must be a list")
    require(set(materialized) == set(identifiers), "status materialized denominator mismatch")
    require(
        status.get("materialized_denominator_count") == len(identifiers),
        "status denominator count mismatch",
    )
    require(
        sorted(status.get("missing_denominators", [])) == missing,
        "status missing denominator mismatch",
    )

    review_ready = (
        set(identifiers) == EXPECTED
        and not missing
        and totals["leaf_count_total"] > 0
        and totals["classified_count"] == totals["leaf_count_total"]
        and totals["unclassified_count"] == 0
        and totals["owner_bound_count"] == totals["leaf_count_total"]
        and totals["task_bound_count"] == totals["leaf_count_total"]
        and totals["test_bound_count"] == totals["leaf_count_total"]
        and pending
    )
    require(
        status.get("status") == ("review-ready" if review_ready else "incomplete"),
        "materialization status does not match packet completeness",
    )
    return {
        "source": "compact-draft-packet",
        "present": True,
        "review_ready": review_ready,
        "materialized_denominator_count": len(identifiers),
        "materialized_denominators": sorted(identifiers),
        "missing_denominators": missing,
        "leaf_count_total": totals["leaf_count_total"],
        "proposal_classified_count": totals["classified_count"],
        "candidate_unclassified_count": totals["unclassified_count"],
        "manual_blocker_count": draft.get("manual_blocker_count", 0),
        "independent_review_completed": False,
        "claims": {
            "sg1_eligible": False,
            "compatibility_credit": False,
            "production_ready": False,
        },
    }


def validate_review_request(
    identifier: str,
    candidate_path: Path,
    candidate: dict[str, Any],
    review_path: Path,
    expected_digest: str,
    manual_blocker_count: int,
) -> dict[str, Any]:
    candidate_data = candidate_path.read_bytes()
    require(sha256(candidate_data) == expected_digest, f"{identifier}: worklist candidate digest drift")
    request = load_json(review_path)
    reject_true_claims(request, f"{identifier} review request")
    require(
        request.get("schema") == "trillionnium.denominator-review-request.v1",
        f"{identifier}: wrong review-request schema",
    )
    require(request.get("project_id") == "trillionnium-game", f"{identifier}: wrong project ID")
    require(request.get("denominator") == identifier, f"{identifier}: review denominator mismatch")
    require(
        request.get("candidate_path") == str(candidate_path.relative_to(ROOT)),
        f"{identifier}: candidate path drift",
    )
    require(
        digest_value(request.get("candidate_sha256"), f"{identifier} review candidate digest")
        == expected_digest,
        f"{identifier}: review candidate digest drift",
    )
    require(request.get("self_approval") is False, f"{identifier}: self approval must be false")
    require(
        request.get("status") == "awaiting-independent-review",
        f"{identifier}: review request status drift",
    )
    require(
        request.get("proposal_policy")
        == "Every extracted leaf is conservatively proposed mandatory; only independent review may change classification.",
        f"{identifier}: proposal policy drift",
    )
    required_reviewer_count = request.get("required_reviewer_count")
    require(
        isinstance(required_reviewer_count, int) and required_reviewer_count >= 2,
        f"{identifier}: two independent reviewers are required",
    )
    roles = request.get("required_reviewer_roles")
    require(isinstance(roles, list) and len(roles) >= 2, f"{identifier}: reviewer roles missing")

    template = request.get("review_bundle_template")
    require(isinstance(template, dict), f"{identifier}: review template missing")
    require(template.get("denominator") == identifier, f"{identifier}: template denominator drift")
    require(template.get("self_approval") is False, f"{identifier}: template self approval")
    reviewers = template.get("reviewers")
    require(isinstance(reviewers, list) and not reviewers, f"{identifier}: reviewers must remain empty")

    leaves = candidate.get("leaves")
    leaf_count = candidate.get("leaf_count")
    require(isinstance(leaves, list), f"{identifier}: candidate leaves missing")
    require(isinstance(leaf_count, int) and leaf_count > 0, f"{identifier}: invalid leaf count")
    require(len(leaves) == leaf_count, f"{identifier}: candidate leaf count mismatch")
    candidate_leaves: dict[str, dict[str, Any]] = {}
    for leaf in leaves:
        require(isinstance(leaf, dict), f"{identifier}: leaf must be an object")
        leaf_id = leaf.get("id")
        require(isinstance(leaf_id, str) and leaf_id, f"{identifier}: leaf ID missing")
        require(leaf_id not in candidate_leaves, f"{identifier}: duplicate leaf {leaf_id}")
        candidate_leaves[leaf_id] = leaf

    decisions = template.get("leaf_decisions")
    require(isinstance(decisions, list), f"{identifier}: leaf decisions missing")
    require(len(decisions) == leaf_count, f"{identifier}: proposal count mismatch")
    seen: set[str] = set()
    owner_bound = 0
    task_bound = 0
    test_bound = 0
    for decision in decisions:
        require(isinstance(decision, dict), f"{identifier}: decision must be an object")
        leaf_id = decision.get("leaf_id")
        require(leaf_id in candidate_leaves, f"{identifier}: unknown decision leaf {leaf_id}")
        require(leaf_id not in seen, f"{identifier}: duplicate decision {leaf_id}")
        seen.add(leaf_id)
        leaf = candidate_leaves[leaf_id]
        require(decision.get("classification") == "mandatory", f"{identifier}/{leaf_id}: proposal must be mandatory")
        require(decision.get("proposal_only") is True, f"{identifier}/{leaf_id}: proposal_only must be true")
        reviewer_ids = decision.get("reviewer_ids")
        require(isinstance(reviewer_ids, list) and not reviewer_ids, f"{identifier}/{leaf_id}: reviewer IDs must be empty")
        require(
            decision.get("signature_hash") == leaf.get("signature_hash"),
            f"{identifier}/{leaf_id}: signature drift",
        )
        owner_bound += bool(decision.get("owner_role"))
        task_bound += bool(decision.get("task_id"))
        test_bound += bool(decision.get("test_id"))
        require(bool(decision.get("gate_id")), f"{identifier}/{leaf_id}: gate missing")
        require(bool(decision.get("evidence_path")), f"{identifier}/{leaf_id}: evidence path missing")
    require(seen == set(candidate_leaves), f"{identifier}: proposal leaf coverage mismatch")

    manual_contracts = template.get("manual_contracts")
    require(isinstance(manual_contracts, list), f"{identifier}: manual contracts missing")
    require(
        len(manual_contracts) == manual_blocker_count,
        f"{identifier}: manual blocker count mismatch",
    )
    for index, contract in enumerate(manual_contracts):
        require(isinstance(contract, dict), f"{identifier}: manual contract {index} invalid")
        require(contract.get("proposal_only") is True, f"{identifier}: manual contract must be proposal-only")
        reviewer_ids = contract.get("reviewer_ids")
        require(isinstance(reviewer_ids, list) and not reviewer_ids, f"{identifier}: manual contract reviewers must be empty")
        require(contract.get("disposition") == "owned-blocker", f"{identifier}: manual contract must remain an owned blocker")
        require(bool(contract.get("owner_role")), f"{identifier}: manual contract owner missing")
        require(bool(contract.get("gate_ids")), f"{identifier}: manual contract gate missing")

    return {
        "id": identifier,
        "layer": candidate.get("layer"),
        "candidate": artifact_descriptor(candidate_path),
        "review_request": artifact_descriptor(review_path),
        "leaf_count": leaf_count,
        "proposal_classified_count": leaf_count,
        "candidate_unclassified_count": sum(
            leaf.get("classification") == "unclassified" for leaf in leaves
        ),
        "owner_bound_count": owner_bound,
        "task_bound_count": task_bound,
        "test_bound_count": test_bound,
        "manual_blocker_count": manual_blocker_count,
        "required_reviewer_count": required_reviewer_count,
        "review_status": "pending-independent-review",
    }


def validate_committed_worklist() -> dict[str, Any]:
    worklist = load_json(WORKLIST)
    reject_true_claims(worklist, "denominator review worklist")
    require(worklist.get("candidate_count") == 14, "worklist candidate count must be fourteen")
    claims = worklist.get("claims")
    require(isinstance(claims, dict), "worklist claims missing")
    require(claims.get("all_candidates_materialized") is True, "candidate materialization is incomplete")
    require(
        claims.get("all_leaves_have_conservative_proposals") is True,
        "conservative proposal coverage is incomplete",
    )
    require(claims.get("all_denominators_reviewed_locked") is False, "worklist must not claim reviewed locks")
    require(claims.get("independent_review_completed") is False, "worklist must not claim independent review")
    require(claims.get("sg1_complete") is False, "worklist must not claim SG1")
    require(claims.get("compatibility_credit") is False, "worklist must not grant compatibility credit")

    source_rows = worklist.get("denominators")
    require(isinstance(source_rows, list) and len(source_rows) == 14, "worklist must have fourteen rows")
    identifiers = [row.get("denominator") for row in source_rows if isinstance(row, dict)]
    require(len(identifiers) == 14 and set(identifiers) == EXPECTED, "worklist denominator set mismatch")
    require(len(set(identifiers)) == 14, "worklist contains duplicate denominators")

    rows = []
    for source in source_rows:
        require(isinstance(source, dict), "worklist row must be an object")
        identifier = source["denominator"]
        candidate_relative = source.get("candidate_path")
        review_relative = source.get("review_request_path")
        require(isinstance(candidate_relative, str), f"{identifier}: candidate path missing")
        require(isinstance(review_relative, str), f"{identifier}: review path missing")
        candidate_path = ROOT / candidate_relative
        review_path = ROOT / review_relative
        require(candidate_path.is_file(), f"{identifier}: candidate missing")
        require(review_path.is_file(), f"{identifier}: review request missing")
        candidate = load_json(candidate_path)
        reject_true_claims(candidate, f"{identifier} candidate")
        require(candidate.get("denominator") == identifier, f"{identifier}: candidate denominator drift")
        manual_blocker_count = source.get("manual_blocker_count")
        require(
            isinstance(manual_blocker_count, int) and manual_blocker_count >= 0,
            f"{identifier}: invalid manual blocker count",
        )
        expected_digest = digest_value(
            source.get("candidate_sha256"), f"{identifier} worklist candidate digest"
        )
        row = validate_review_request(
            identifier,
            candidate_path,
            candidate,
            review_path,
            expected_digest,
            manual_blocker_count,
        )
        require(row["leaf_count"] == source.get("leaf_count"), f"{identifier}: worklist leaf count drift")
        rows.append(row)

    leaf_count_total = sum(row["leaf_count"] for row in rows)
    proposal_count = sum(row["proposal_classified_count"] for row in rows)
    unclassified_count = sum(row["candidate_unclassified_count"] for row in rows)
    owner_count = sum(row["owner_bound_count"] for row in rows)
    task_count = sum(row["task_bound_count"] for row in rows)
    test_count = sum(row["test_bound_count"] for row in rows)
    manual_count = sum(row["manual_blocker_count"] for row in rows)
    review_ready = (
        leaf_count_total > 0
        and proposal_count == leaf_count_total
        and owner_count == leaf_count_total
        and task_count == leaf_count_total
        and test_count == leaf_count_total
        and all(row["required_reviewer_count"] >= 2 for row in rows)
    )
    require(review_ready, "committed review input is not ready for independent review")

    return {
        "source": "committed-review-worklist",
        "present": True,
        "review_ready": True,
        "candidate_head": worklist.get("candidate_head"),
        "materialized_denominator_count": len(rows),
        "materialized_denominators": sorted(EXPECTED),
        "missing_denominators": [],
        "leaf_count_total": leaf_count_total,
        "proposal_classified_count": proposal_count,
        "candidate_unclassified_count": unclassified_count,
        "owner_bound_count": owner_count,
        "task_bound_count": task_count,
        "test_bound_count": test_count,
        "manual_blocker_count": manual_count,
        "denominators": sorted(rows, key=lambda row: row["id"]),
        "independent_review_completed": False,
        "claims": {
            "sg1_eligible": False,
            "compatibility_credit": False,
            "production_ready": False,
        },
    }


def validate() -> dict[str, Any]:
    status_present = STATUS.is_file()
    draft_present = DRAFT.is_file()
    require(status_present == draft_present, "status and draft packet must appear together")
    if status_present:
        return validate_compact_packet()
    if WORKLIST.is_file():
        return validate_committed_worklist()
    return {
        "source": "absent",
        "present": False,
        "review_ready": False,
        "materialized_denominator_count": 0,
        "materialized_denominators": [],
        "missing_denominators": sorted(EXPECTED),
        "leaf_count_total": 0,
        "proposal_classified_count": 0,
        "candidate_unclassified_count": 0,
        "manual_blocker_count": 0,
        "independent_review_completed": False,
        "claims": {
            "sg1_eligible": False,
            "compatibility_credit": False,
            "production_ready": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-present", action="store_true")
    parser.add_argument("--require-review-ready", action="store_true")
    arguments = parser.parse_args()
    try:
        result = validate()
        if arguments.require_present:
            require(result["present"], "denominator materialization is absent")
        if arguments.require_review_ready:
            require(result["review_ready"], "denominator materialization is not review-ready")
        print(
            json.dumps(
                {
                    "schema": "trillionnium.denominator-materialization-check.v2",
                    "status": (
                        "review-ready"
                        if result["review_ready"]
                        else "incomplete" if result["present"] else "absent"
                    ),
                    **result,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except (OSError, ValueError, ContractError) as error:
        print(f"denominator materialization check failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
