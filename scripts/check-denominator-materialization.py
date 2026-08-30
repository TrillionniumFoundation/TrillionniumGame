#!/usr/bin/env python3
"""Validate materialized parity-denominator candidates without granting SG1 credit."""
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
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"{path.relative_to(ROOT)}: invalid JSON: {error}") from error
    require(isinstance(value, dict), f"{path.relative_to(ROOT)}: top level must be an object")
    return value


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def reject_true_claims(value: Any, location: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}.{key}"
            if key in FORBIDDEN_TRUE_CLAIMS:
                require(child is False, f"{child_location}: materialization may not set this claim true")
            reject_true_claims(child, child_location)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_true_claims(child, f"{location}[{index}]")


def validate_artifact(row: dict[str, Any]) -> tuple[dict[str, Any], int]:
    identifier = row.get("id")
    require(identifier in EXPECTED, f"unknown denominator: {identifier!r}")
    artifact = row.get("manifest")
    require(isinstance(artifact, dict), f"{identifier}: manifest descriptor missing")
    relative = artifact.get("path")
    require(isinstance(relative, str) and relative, f"{identifier}: manifest path missing")
    path = ROOT / relative
    require(path.is_file(), f"{identifier}: manifest file missing: {relative}")
    data = path.read_bytes()
    require(artifact.get("size_bytes") == len(data), f"{identifier}: size mismatch")
    require(artifact.get("sha256") == sha256(data), f"{identifier}: SHA-256 mismatch")
    manifest = load(path)
    manifest_id = manifest.get("denominator") or manifest.get("denominator_id") or manifest.get("id")
    require(manifest_id == identifier, f"{identifier}: embedded denominator mismatch")
    reject_true_claims(manifest, f"manifest[{identifier}]")
    leaves = manifest.get("leaves")
    leaves = leaves if isinstance(leaves, list) else []
    leaf_count = row.get("leaf_count")
    require(isinstance(leaf_count, int) and leaf_count >= 0, f"{identifier}: invalid leaf count")
    embedded_count = manifest.get("leaf_count")
    if isinstance(embedded_count, int):
        require(embedded_count == leaf_count, f"{identifier}: embedded leaf count mismatch")
    elif leaves:
        require(len(leaves) == leaf_count, f"{identifier}: leaf-list count mismatch")
    for key in (
        "classified_count",
        "unclassified_count",
        "owner_bound_count",
        "task_bound_count",
        "test_bound_count",
    ):
        require(isinstance(row.get(key), int) and row[key] >= 0, f"{identifier}: invalid {key}")
        require(row[key] <= leaf_count, f"{identifier}: {key} exceeds leaf count")
    require(
        row["classified_count"] + row["unclassified_count"] == leaf_count,
        f"{identifier}: classification arithmetic mismatch",
    )
    require(
        row.get("review_status") == "pending-independent-review",
        f"{identifier}: materialization must remain pending independent review",
    )
    return manifest, leaf_count


def validate() -> dict[str, Any]:
    if not STATUS.is_file() or not DRAFT.is_file():
        return {
            "present": False,
            "review_ready": False,
            "materialized_denominator_count": 0,
            "missing_denominators": sorted(EXPECTED),
            "leaf_count_total": 0,
            "unclassified_count": None,
        }

    status = load(STATUS)
    draft = load(DRAFT)
    require(status.get("schema") == "trillionnium.denominator-materialization.v1", "wrong status schema")
    require(draft.get("schema") == "trillionnium.denominator-review-packet.draft.v1", "wrong draft schema")
    require(status.get("project_id") == "trillionnium-game", "wrong status project")
    require(draft.get("project_id") == "trillionnium-game", "wrong draft project")
    reject_true_claims(status, "status")
    reject_true_claims(draft, "draft")

    rows = draft.get("denominators")
    require(isinstance(rows, list), "draft denominator rows missing")
    identifiers = [row.get("id") for row in rows if isinstance(row, dict)]
    require(len(identifiers) == len(rows), "draft contains a non-object denominator row")
    require(len(set(identifiers)) == len(identifiers), "duplicate denominator row")
    require(set(identifiers) <= EXPECTED, "draft contains an unknown denominator")

    totals = {
        "leaf_count_total": 0,
        "classified_count": 0,
        "unclassified_count": 0,
        "owner_bound_count": 0,
        "task_bound_count": 0,
        "test_bound_count": 0,
    }
    for row in rows:
        _, leaf_count = validate_artifact(row)
        totals["leaf_count_total"] += leaf_count
        for key in totals.keys() - {"leaf_count_total"}:
            totals[key] += row[key]

    aggregate = draft.get("aggregate")
    require(isinstance(aggregate, dict), "draft aggregate missing")
    require(aggregate.get("denominator_count") == len(rows), "aggregate denominator count mismatch")
    for key, value in totals.items():
        require(aggregate.get(key) == value, f"aggregate {key} mismatch")
    require(
        isinstance(aggregate.get("manifest_sha256"), str)
        and len(aggregate["manifest_sha256"]) == 64,
        "aggregate manifest digest missing",
    )

    missing = sorted(EXPECTED - set(identifiers))
    require(draft.get("missing_denominators") == missing, "draft missing-denominator list mismatch")
    require(status.get("missing_denominators") == missing, "status missing-denominator list mismatch")
    require(status.get("materialized_denominator_count") == len(rows), "status denominator count mismatch")
    require(set(status.get("materialized_denominators", [])) == set(identifiers), "status denominator set mismatch")
    review = draft.get("review")
    require(isinstance(review, dict), "draft review block missing")
    require(review.get("decision") == "pending", "draft review decision must remain pending")
    require(review.get("independent") is False, "draft may not claim independent review")
    require(review.get("minimum_reviewers") == 2, "draft must require two reviewers")

    review_ready = (
        not missing
        and totals["leaf_count_total"] > 0
        and totals["unclassified_count"] == 0
        and totals["classified_count"] == totals["leaf_count_total"]
        and totals["owner_bound_count"] == totals["leaf_count_total"]
        and totals["task_bound_count"] == totals["leaf_count_total"]
        and totals["test_bound_count"] == totals["leaf_count_total"]
    )
    expected_status = "review-ready" if review_ready else "candidate-incomplete"
    require(status.get("status") == expected_status, "derived materialization status mismatch")
    return {
        "present": True,
        "review_ready": review_ready,
        "materialized_denominator_count": len(rows),
        "missing_denominators": missing,
        **totals,
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
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (ContractError, OSError, ValueError) as error:
        print(f"denominator materialization check failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
