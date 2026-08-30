#!/usr/bin/env python3
"""Validate the exact denominator review bundle or prove SG1 remains open."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE = "manifests/upstream/denominator-review-bundle.json"
EXPECTED_IDS = {
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
NAKAMA_COMMIT = "d4d92f93f78bbbe62c7fc50a3f85c772ec121a09"
NAKAMA_TREE = "f3c9cfc2726d5543da1564629170f35b98e3797d"
COMMON_COMMIT = "449b77ecc8789aa466c36b67f6e498033dfcd9c5"
COMMON_TREE = "c6a7b9796b9c2a6b5118c74e5f213963a5001f14"


class BundleError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise BundleError(message)


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BundleError(f"{path.relative_to(ROOT)}: invalid JSON: {error}") from error
    require(isinstance(value, dict), "bundle top level must be an object")
    return value


def valid_hex(value: Any, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def validate_registry() -> dict[str, Any]:
    path = ROOT / "docs/development/PARITY_DENOMINATORS.json"
    registry = load(path)
    rows = registry.get("denominators")
    require(isinstance(rows, list), "denominator registry rows missing")
    identifiers = {row.get("id") for row in rows if isinstance(row, dict)}
    require(identifiers == EXPECTED_IDS, "denominator registry set is not the required fourteen")
    for row in rows:
        require(row.get("output"), f"{row.get('id')}: output path missing")
        require(row.get("extractor_task"), f"{row.get('id')}: extractor task missing")
        require(row.get("unclassified_allowed") is False, f"{row.get('id')}: final unclassified leaves must be forbidden")
    return registry


def validate_artifact(value: Any, label: str) -> dict[str, Any]:
    require(isinstance(value, dict), f"{label}: artifact must be an object")
    relative = value.get("path")
    require(isinstance(relative, str) and relative, f"{label}: artifact path missing")
    require(valid_hex(value.get("sha256"), 64), f"{label}: invalid SHA-256")
    require(isinstance(value.get("size_bytes"), int) and value["size_bytes"] > 0, f"{label}: invalid size")
    path = ROOT / relative
    require(path.is_file(), f"{label}: artifact file missing: {relative}")
    data = path.read_bytes()
    require(len(data) == value["size_bytes"], f"{label}: artifact size mismatch")
    require(hashlib.sha256(data).hexdigest() == value["sha256"], f"{label}: artifact digest mismatch")
    return value


def validate_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    require(bundle.get("schema") == "trillionnium.denominator-review-bundle.v1", "wrong bundle schema")
    require(bundle.get("project_id") == "trillionnium-game", "wrong project ID")
    bundle_id = bundle.get("bundle_id")
    require(isinstance(bundle_id, str) and bundle_id.startswith("TG-DEN-REVIEW-"), "invalid bundle ID")

    baseline = bundle.get("baseline")
    require(isinstance(baseline, dict), "baseline missing")
    require(baseline.get("nakama_commit") == NAKAMA_COMMIT, "Nakama commit mismatch")
    require(baseline.get("nakama_tree") == NAKAMA_TREE, "Nakama tree mismatch")
    require(baseline.get("nakama_common_commit") == COMMON_COMMIT, "nakama-common commit mismatch")
    require(baseline.get("nakama_common_tree") == COMMON_TREE, "nakama-common tree mismatch")

    candidate = bundle.get("candidate")
    require(isinstance(candidate, dict), "candidate identity missing")
    require(candidate.get("repository") == "TrillionniumFoundation/TrillionniumGame", "candidate repository mismatch")
    require(valid_hex(candidate.get("commit"), 40), "candidate commit invalid")
    require(valid_hex(candidate.get("tree"), 40), "candidate tree invalid")
    require(valid_hex(candidate.get("candidate_manifest_sha256"), 64), "candidate manifest digest invalid")

    rows = bundle.get("denominators")
    require(isinstance(rows, list) and len(rows) == 14, "bundle must contain exactly fourteen denominators")
    identifiers = [row.get("id") for row in rows if isinstance(row, dict)]
    require(len(identifiers) == 14 and set(identifiers) == EXPECTED_IDS, "bundle denominator set mismatch")
    require(len(set(identifiers)) == 14, "duplicate denominator in bundle")

    totals = {
        "leaf_count_total": 0,
        "classified_count": 0,
        "unclassified_count": 0,
        "owner_bound_count": 0,
        "task_bound_count": 0,
        "test_bound_count": 0,
    }
    accepted = True
    for row in rows:
        identifier = row["id"]
        require(isinstance(row.get("layer"), str) and row["layer"].startswith("D"), f"{identifier}: invalid layer")
        validate_artifact(row.get("manifest"), f"{identifier} manifest")
        leaf_count = row.get("leaf_count")
        require(isinstance(leaf_count, int) and leaf_count > 0, f"{identifier}: empty leaf count")
        for key in (
            "classified_count",
            "unclassified_count",
            "owner_bound_count",
            "task_bound_count",
            "test_bound_count",
        ):
            require(isinstance(row.get(key), int) and row[key] >= 0, f"{identifier}: invalid {key}")
        require(row["classified_count"] + row["unclassified_count"] == leaf_count, f"{identifier}: classification arithmetic mismatch")
        require(row["classified_count"] <= leaf_count, f"{identifier}: classified count exceeds leaves")
        require(row["owner_bound_count"] <= leaf_count, f"{identifier}: owner count exceeds leaves")
        require(row["task_bound_count"] <= leaf_count, f"{identifier}: task count exceeds leaves")
        require(row["test_bound_count"] <= leaf_count, f"{identifier}: test count exceeds leaves")
        accepted &= row.get("review_status") == "accepted"
        totals["leaf_count_total"] += leaf_count
        for key in totals.keys() - {"leaf_count_total"}:
            totals[key] += row[key]

    aggregate = bundle.get("aggregate")
    require(isinstance(aggregate, dict), "aggregate missing")
    require(aggregate.get("denominator_count") == 14, "aggregate denominator count mismatch")
    for key, value in totals.items():
        require(aggregate.get(key) == value, f"aggregate {key} mismatch")
    require(valid_hex(aggregate.get("manifest_sha256"), 64), "aggregate manifest digest invalid")

    review = bundle.get("review")
    require(isinstance(review, dict), "review missing")
    independent_accepted = (
        review.get("decision") == "accepted"
        and review.get("independent") is True
        and review.get("reviewer_role") == "compatibility-architecture"
        and isinstance(review.get("reviewer_identity"), str)
        and bool(review["reviewer_identity"])
        and isinstance(review.get("reviewed_at"), str)
        and bool(review["reviewed_at"])
    )

    complete = (
        totals["leaf_count_total"] > 0
        and totals["classified_count"] == totals["leaf_count_total"]
        and totals["unclassified_count"] == 0
        and totals["owner_bound_count"] == totals["leaf_count_total"]
        and totals["task_bound_count"] == totals["leaf_count_total"]
        and totals["test_bound_count"] == totals["leaf_count_total"]
        and accepted
        and independent_accepted
    )

    claims = bundle.get("claims")
    require(isinstance(claims, dict), "claims missing")
    require(claims.get("compatibility_credit") is False, "SG1 bundle may not grant compatibility credit")
    require(claims.get("production_ready") is False, "SG1 bundle may not grant production credit")
    require(claims.get("sg1_eligible") is complete, "sg1_eligible does not match bundle completeness")

    return {
        "bundle_present": True,
        "bundle_id": bundle_id,
        "leaf_count_total": totals["leaf_count_total"],
        "unclassified_count": totals["unclassified_count"],
        "all_denominators_accepted": accepted,
        "independent_review_accepted": independent_accepted,
        "sg1_eligible": complete,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", default=DEFAULT_BUNDLE)
    parser.add_argument("--require-sg1", action="store_true")
    arguments = parser.parse_args()
    try:
        registry = validate_registry()
        bundle_path = ROOT / arguments.bundle
        if bundle_path.is_file():
            result = validate_bundle(load(bundle_path))
        else:
            result = {
                "bundle_present": False,
                "bundle_id": None,
                "leaf_count_total": registry.get("leaf_count_total"),
                "unclassified_count": registry.get("unclassified_count"),
                "all_denominators_accepted": False,
                "independent_review_accepted": False,
                "sg1_eligible": False,
            }
        if arguments.require_sg1 and not result["sg1_eligible"]:
            raise BundleError("SG1 denominator bundle is absent or incomplete")
        print(
            json.dumps(
                {
                    "schema": "trillionnium.denominator-review-check.v1",
                    "status": "passed" if result["sg1_eligible"] else "candidate-incomplete",
                    **result,
                    "claims": {
                        "sg1_eligible": result["sg1_eligible"],
                        "compatibility_credit": False,
                        "production_ready": False,
                    },
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except (OSError, ValueError, BundleError) as error:
        print(f"denominator review check failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
