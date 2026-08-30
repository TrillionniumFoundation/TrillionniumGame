#!/usr/bin/env python3
"""Validate the independent review matrix and keep unassigned domains fail closed."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "docs/review/INDEPENDENT_REVIEW_MATRIX.json"
GAPS_PATH = ROOT / "docs/status/GAP_REGISTER.json"


class ReviewMatrixError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReviewMatrixError(message)


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReviewMatrixError(f"{path.relative_to(ROOT)}: {error}") from error
    require(isinstance(value, dict), f"{path.relative_to(ROOT)}: root must be an object")
    return value


def validate() -> dict[str, Any]:
    matrix = load(MATRIX_PATH)
    gaps = load(GAPS_PATH)
    require(matrix.get("schema") == "trillionnium.independent-review-matrix.v1", "wrong review matrix schema")
    require(matrix.get("project_id") == "trillionnium-game", "wrong review matrix project")
    policy = matrix.get("policy")
    require(isinstance(policy, dict), "review policy must be an object")
    for key in (
        "implementation_author_may_accept_own_evidence",
        "administrator_mutator_may_accept_own_governance_evidence",
    ):
        require(policy.get(key) is False, f"{key} must be false")
    for key in (
        "p0_requires_independent_domain_reviewer",
        "p1_requires_independent_domain_or_cross_domain_reviewer",
        "review_identity_must_be_named_user_or_team",
        "unassigned_review_blocks_gap_closure",
    ):
        require(policy.get(key) is True, f"{key} must be true")

    gap_by_id = {
        row.get("id"): row
        for row in gaps.get("gaps", [])
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    domains = matrix.get("domains")
    require(isinstance(domains, list) and domains, "review domains must be a non-empty list")
    ids: set[str] = set()
    assigned_domains = 0
    required_roles = 0
    for domain in domains:
        require(isinstance(domain, dict), "review domain must be an object")
        domain_id = domain.get("id")
        require(isinstance(domain_id, str) and domain_id, "review domain ID missing")
        require(domain_id not in ids, f"duplicate review domain: {domain_id}")
        ids.add(domain_id)
        roles = domain.get("required_roles")
        reviewers = domain.get("assigned_reviewers")
        paths = domain.get("protected_paths")
        blocking = domain.get("blocking_gaps")
        require(isinstance(roles, list) and roles, f"{domain_id}: required_roles missing")
        require(len(set(roles)) == len(roles), f"{domain_id}: duplicate required role")
        require(isinstance(reviewers, list), f"{domain_id}: assigned_reviewers must be a list")
        require(isinstance(paths, list) and paths, f"{domain_id}: protected paths missing")
        require(isinstance(blocking, list) and blocking, f"{domain_id}: blocking gaps missing")
        unknown = [gap_id for gap_id in blocking if gap_id not in gap_by_id]
        require(not unknown, f"{domain_id}: unknown blocking gaps {unknown}")
        required_roles += len(roles)

        status = domain.get("status")
        if reviewers:
            assigned_domains += 1
            require(status in {"assigned", "active"}, f"{domain_id}: assigned reviewers require assigned/active status")
            identities: set[str] = set()
            covered_roles: set[str] = set()
            for reviewer in reviewers:
                require(isinstance(reviewer, dict), f"{domain_id}: reviewer must be an object")
                identity = reviewer.get("identity")
                kind = reviewer.get("kind")
                role_values = reviewer.get("roles", [])
                conflicts = reviewer.get("conflicts", [])
                require(isinstance(identity, str) and identity, f"{domain_id}: reviewer identity missing")
                require(identity not in identities, f"{domain_id}: duplicate reviewer {identity}")
                identities.add(identity)
                require(kind in {"github-user", "github-team", "external-review-provider"}, f"{domain_id}: invalid reviewer kind")
                require(isinstance(role_values, list) and role_values, f"{domain_id}: reviewer roles missing")
                require(isinstance(conflicts, list), f"{domain_id}: conflicts must be a list")
                require(not conflicts, f"{domain_id}: reviewer {identity} has unresolved conflicts")
                covered_roles.update(value for value in role_values if isinstance(value, str))
            missing_roles = sorted(set(roles) - covered_roles)
            require(not missing_roles, f"{domain_id}: required roles not assigned {missing_roles}")
        else:
            require(status == "unassigned", f"{domain_id}: empty reviewer list must be unassigned")
            for gap_id in blocking:
                require(gap_by_id[gap_id].get("status") != "closed", f"{domain_id}: unassigned review cannot close {gap_id}")

    summary = matrix.get("summary")
    require(isinstance(summary, dict), "review matrix summary missing")
    require(summary.get("domain_count") == len(domains), "review domain count summary mismatch")
    require(summary.get("assigned_domain_count") == assigned_domains, "assigned domain count summary mismatch")
    require(summary.get("all_required_reviews_available") is (assigned_domains == len(domains)), "review availability summary mismatch")

    return {
        "schema": "trillionnium.independent-review-matrix-validation.v1",
        "domains": len(domains),
        "assigned_domains": assigned_domains,
        "required_roles": required_roles,
        "all_required_reviews_available": assigned_domains == len(domains),
        "status": "passed",
        "claim_boundary": {
            "matrix_presence_is_review": False,
            "unassigned_domains_block_closure": True,
        },
    }


def main() -> int:
    try:
        result = validate()
    except ReviewMatrixError as error:
        print(f"independent review matrix validation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
