#!/usr/bin/env python3
"""Validate redundant reviewer routing without confusing assignment with acceptance."""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "docs/review/INDEPENDENT_REVIEW_MATRIX.json"
GAPS_PATH = ROOT / "docs/status/GAP_REGISTER.json"
CODEOWNERS_PATH = ROOT / ".github/CODEOWNERS"
REPOSITORY = "TrillionniumFoundation/TrillionniumGame"
IDENTITY = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_CODEOWNER_PATTERNS = {
    "*",
    "/.github/",
    "/CURRENT_PLAN.md",
    "/docs/governance/",
    "/docs/evidence/",
    "/docs/review/",
    "/docs/status/",
    "/migrations/",
    "/database/",
    "/crates/trnm-persistence-core/",
    "/crates/trnm-persistence-pg/",
    "/SECURITY.md",
    "/docs/SECURITY_AND_PRIVACY.md",
    "/crates/trnm-token-core/",
    "/crates/trnm-token-jwt-adapter/",
    "/crates/trnm-token-crypto-provider/",
    "/crates/trnm-token-jwt-provider-adapter/",
    "/crates/trnm-session-core/",
    "/contracts/",
    "/crates/trnm-canonical-core/",
    "/crates/trnm-transport-core/",
    "/crates/trnm-realtime-wire/",
    "/crates/trnm-presence-core/",
    "/crates/trnm-presence-router-v2/",
    "/runtime/",
    "/deploy/",
    "/compose.yaml",
    "/docs/OPERATIONS_AND_RELEASE.md",
}
REQUIRED_DYNAMIC_CONFLICTS = {
    "candidate-author",
    "evidence-producer",
    "administrator-mutator-under-review",
}
# These identities were observed in the immutable PR #63 commit collection through
# the bound candidate head. Descendant candidates may add conflicts but cannot
# erase an already observed author/committer conflict.
REQUIRED_CANDIDATE_CONFLICT_IDENTITIES = {
    "ProfHepta",
    "Franksudoman",
    "Tomasrgbsf",
}


class ReviewMatrixError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReviewMatrixError(message)


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReviewMatrixError(f"{path}: {error}") from error
    require(isinstance(value, dict), f"{path}: root must be an object")
    return value


def parse_time(value: Any, label: str) -> datetime:
    require(isinstance(value, str) and value, f"{label}: timestamp missing")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ReviewMatrixError(f"{label}: invalid timestamp") from error
    require(parsed.tzinfo is not None, f"{label}: timezone required")
    return parsed.astimezone(timezone.utc)


def parse_codeowners(path: Path) -> dict[str, list[str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ReviewMatrixError(f"{path}: {error}") from error
    rows: dict[str, list[str]] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        require(len(parts) >= 3, f"CODEOWNERS line {number}: at least two owners required")
        pattern, owners = parts[0], parts[1:]
        require(pattern not in rows, f"CODEOWNERS line {number}: duplicate pattern {pattern}")
        require(len(owners) == len(set(owners)), f"CODEOWNERS line {number}: duplicate owner")
        require(all(owner.startswith("@") for owner in owners), f"CODEOWNERS line {number}: invalid owner")
        rows[pattern] = owners
    return rows


def validate_candidate_scope(matrix: dict[str, Any]) -> tuple[set[str], dict[str, Any]]:
    scope = matrix.get("candidate_scope")
    require(isinstance(scope, dict), "candidate scope is required")
    required = {
        "repository",
        "pull_request",
        "base_commit",
        "authorship_observed_through_head",
        "head_tree",
        "prospective_merge",
        "candidate_commit_count",
        "conflict_identities",
        "evidence",
    }
    require(set(scope) == required, "candidate scope fields drift")
    require(scope.get("repository") == REPOSITORY, "candidate repository mismatch")
    require(scope.get("pull_request") == 63, "candidate pull request mismatch")
    for field in (
        "base_commit",
        "authorship_observed_through_head",
        "head_tree",
        "prospective_merge",
    ):
        value = scope.get(field)
        require(isinstance(value, str) and GIT_SHA.fullmatch(value) is not None, f"candidate {field} must be a full Git SHA")
    count = scope.get("candidate_commit_count")
    require(isinstance(count, int) and not isinstance(count, bool) and count > 0, "candidate commit count must be positive")
    identities = scope.get("conflict_identities")
    require(
        isinstance(identities, list)
        and identities
        and len(identities) == len(set(identities))
        and all(isinstance(value, str) and IDENTITY.fullmatch(value) for value in identities),
        "candidate conflict identities are invalid",
    )
    identity_set = set(identities)
    missing = sorted(REQUIRED_CANDIDATE_CONFLICT_IDENTITIES - identity_set)
    require(not missing, f"candidate conflict identities removed: {missing}")

    evidence = scope.get("evidence")
    require(isinstance(evidence, dict), "candidate authorship evidence missing")
    require(
        set(evidence)
        == {"workflow_run_id", "job_id", "artifact_id", "artifact_sha256", "observed_at"},
        "candidate authorship evidence fields drift",
    )
    for field in ("workflow_run_id", "job_id", "artifact_id"):
        value = evidence.get(field)
        require(isinstance(value, int) and not isinstance(value, bool) and value > 0, f"candidate evidence {field} must be positive")
    digest = evidence.get("artifact_sha256")
    require(isinstance(digest, str) and SHA256.fullmatch(digest) is not None, "candidate evidence artifact digest is invalid")
    parse_time(evidence.get("observed_at"), "candidate evidence observed_at")
    return identity_set, scope


def validate(
    matrix_path: Path = MATRIX_PATH,
    gaps_path: Path = GAPS_PATH,
    codeowners_path: Path = CODEOWNERS_PATH,
) -> dict[str, Any]:
    matrix = load(matrix_path)
    gaps = load(gaps_path)
    require(matrix.get("schema") == "trillionnium.independent-review-matrix.v1", "wrong review matrix schema")
    require(matrix.get("project_id") == "trillionnium-game", "wrong review matrix project")
    require(matrix.get("plan_version") == 3, "review matrix must target plan v3")
    generated_at = parse_time(matrix.get("generated_at"), "generated_at")

    policy = matrix.get("policy")
    require(isinstance(policy, dict), "review policy must be an object")
    for key in (
        "implementation_author_may_accept_own_evidence",
        "administrator_mutator_may_accept_own_governance_evidence",
        "evidence_producer_may_accept_own_evidence",
    ):
        require(policy.get(key) is False, f"{key} must be false")
    for key in (
        "candidate_author_is_conflict",
        "p0_requires_independent_domain_reviewer",
        "p1_requires_independent_domain_or_cross_domain_reviewer",
        "review_identity_must_be_named_user_or_team",
        "unassigned_review_blocks_gap_closure",
        "latest_head_and_tree_binding_required",
        "permission_readback_required",
        "branch_policy_enforcement_required_for_closure",
        "candidate_conflicts_must_be_explicit",
        "conflicted_assignment_cannot_satisfy_availability",
    ):
        require(policy.get(key) is True, f"{key} must be true")
    minimum_reviewers = policy.get("minimum_redundant_reviewers_per_domain")
    require(
        isinstance(minimum_reviewers, int)
        and not isinstance(minimum_reviewers, bool)
        and minimum_reviewers >= 2,
        "minimum_redundant_reviewers_per_domain must be at least 2",
    )

    assignment_contract = matrix.get("assignment_contract")
    require(isinstance(assignment_contract, dict), "assignment contract is required")
    required_fields = assignment_contract.get("required_fields")
    require(isinstance(required_fields, list) and required_fields, "assignment required_fields missing")
    expected_fields = {
        "identity",
        "kind",
        "organization",
        "domain",
        "roles",
        "conflicts",
        "candidate_ineligibility",
        "effective_at",
        "expires_at",
        "permission_readback",
        "qualification_basis",
    }
    require(set(required_fields) == expected_fields, "assignment required_fields drift")
    allowed_kinds = set(assignment_contract.get("allowed_kinds", []))
    allowed_permissions = set(assignment_contract.get("allowed_repository_permissions", []))
    allowed_conflicts = set(assignment_contract.get("allowed_conflicts", []))
    require(allowed_kinds == {"github-user", "github-team", "external-review-provider"}, "allowed reviewer kinds drift")
    require(allowed_permissions == {"write", "maintain", "admin"}, "allowed repository permissions drift")
    require(allowed_conflicts == REQUIRED_DYNAMIC_CONFLICTS, "allowed reviewer conflicts drift")
    require(
        set(assignment_contract.get("required_candidate_ineligibility", [])) == REQUIRED_DYNAMIC_CONFLICTS,
        "dynamic conflict contract drift",
    )

    candidate_conflicts, candidate_scope = validate_candidate_scope(matrix)
    gap_by_id = {
        row.get("id"): row
        for row in gaps.get("gaps", [])
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    domains = matrix.get("domains")
    require(isinstance(domains, list) and domains, "review domains must be a non-empty list")
    ids: set[str] = set()
    assigned_domains = 0
    redundant_domains = 0
    available_domains = 0
    blocked_domains = 0
    required_roles = 0
    global_identities: set[str] = set()
    global_eligible_identities: set[str] = set()
    observed_candidate_conflicts: set[str] = set()

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
        require(all(isinstance(role, str) and role for role in roles), f"{domain_id}: invalid required role")
        require(len(set(roles)) == len(roles), f"{domain_id}: duplicate required role")
        require(isinstance(reviewers, list), f"{domain_id}: assigned_reviewers must be a list")
        require(isinstance(paths, list) and paths, f"{domain_id}: protected paths missing")
        require(isinstance(blocking, list) and blocking, f"{domain_id}: blocking gaps missing")
        unknown = [gap_id for gap_id in blocking if gap_id not in gap_by_id]
        require(not unknown, f"{domain_id}: unknown blocking gaps {unknown}")
        required_roles += len(roles)

        require(len(reviewers) >= minimum_reviewers, f"{domain_id}: at least {minimum_reviewers} redundant reviewers required")
        assigned_domains += 1
        identities: set[str] = set()
        reviewer_roles: dict[str, set[str]] = {}
        eligible_roles_by_identity: dict[str, set[str]] = {}

        for reviewer in reviewers:
            require(isinstance(reviewer, dict), f"{domain_id}: reviewer must be an object")
            require(set(reviewer) == expected_fields, f"{domain_id}: reviewer fields drift")
            identity = reviewer.get("identity")
            require(isinstance(identity, str) and IDENTITY.fullmatch(identity) is not None, f"{domain_id}: invalid reviewer identity")
            require(identity not in identities, f"{domain_id}: duplicate reviewer {identity}")
            identities.add(identity)
            global_identities.add(identity)
            require(reviewer.get("kind") in allowed_kinds, f"{domain_id}: invalid reviewer kind")
            require(reviewer.get("organization") == "TrillionniumFoundation", f"{domain_id}: organization mismatch")
            require(reviewer.get("domain") == domain_id, f"{domain_id}: reviewer domain mismatch")
            role_values = reviewer.get("roles")
            require(isinstance(role_values, list) and role_values, f"{domain_id}: reviewer roles missing")
            require(len(role_values) == len(set(role_values)), f"{domain_id}: duplicate reviewer role")
            role_set = set(role_values)
            require(role_set == set(roles), f"{domain_id}: each reviewer must cover every required role")
            reviewer_roles[identity] = role_set

            conflicts = reviewer.get("conflicts")
            require(
                isinstance(conflicts, list)
                and len(conflicts) == len(set(conflicts))
                and set(conflicts) <= allowed_conflicts,
                f"{domain_id}: reviewer {identity} has invalid conflicts",
            )
            if identity in candidate_conflicts:
                require(
                    "candidate-author" in conflicts,
                    f"{domain_id}: candidate author {identity} must declare candidate-author conflict",
                )
            if "candidate-author" in conflicts:
                observed_candidate_conflicts.add(identity)
            if not conflicts:
                eligible_roles_by_identity[identity] = role_set
                global_eligible_identities.add(identity)

            require(
                set(reviewer.get("candidate_ineligibility", [])) == REQUIRED_DYNAMIC_CONFLICTS,
                f"{domain_id}: candidate conflict rules drift for {identity}",
            )
            effective = parse_time(reviewer.get("effective_at"), f"{domain_id}.{identity}.effective_at")
            expires = parse_time(reviewer.get("expires_at"), f"{domain_id}.{identity}.expires_at")
            require(effective <= generated_at < expires, f"{domain_id}: reviewer assignment is inactive or expired")
            permission = reviewer.get("permission_readback")
            require(isinstance(permission, dict), f"{domain_id}: permission readback missing")
            require(set(permission) == {"repository", "permission", "observed_at"}, f"{domain_id}: permission readback fields drift")
            require(permission.get("repository") == REPOSITORY, f"{domain_id}: permission repository mismatch")
            require(permission.get("permission") in allowed_permissions, f"{domain_id}: insufficient repository permission")
            observed = parse_time(permission.get("observed_at"), f"{domain_id}.{identity}.permission.observed_at")
            age = generated_at - observed
            require(age.total_seconds() >= 0, f"{domain_id}: permission readback is from the future")
            require(age.total_seconds() <= 7 * 24 * 3600, f"{domain_id}: permission readback is stale")
            basis = reviewer.get("qualification_basis")
            require(
                isinstance(basis, list)
                and basis
                and all(isinstance(value, str) and value.strip() == value and value for value in basis),
                f"{domain_id}: qualification basis is required",
            )

        # Routing remains redundant even when every route is currently conflicted.
        for excluded in identities:
            survivors = {identity: role_set for identity, role_set in reviewer_roles.items() if identity != excluded}
            require(
                len(survivors) >= minimum_reviewers,
                f"{domain_id}: losing assigned reviewer {excluded} leaves fewer than {minimum_reviewers} routing assignments",
            )
            surviving_roles: set[str] = set()
            for role_set in survivors.values():
                surviving_roles.update(role_set)
            require(surviving_roles == set(roles), f"{domain_id}: losing assigned reviewer {excluded} removes required role coverage")
        redundant_domains += 1

        eligible_roles: set[str] = set()
        for role_set in eligible_roles_by_identity.values():
            eligible_roles.update(role_set)
        available = len(eligible_roles_by_identity) >= minimum_reviewers and eligible_roles == set(roles)
        if available:
            require(domain.get("status") == "active", f"{domain_id}: available domain must be active")
            available_domains += 1
        else:
            require(
                domain.get("status") == "blocked-reviewer-capacity",
                f"{domain_id}: insufficient conflict-free reviewers must block the domain",
            )
            blocked_domains += 1

    require(
        candidate_conflicts <= observed_candidate_conflicts,
        "candidate conflict identities are not declared across reviewer assignments",
    )

    codeowners = parse_codeowners(codeowners_path)
    missing_patterns = sorted(REQUIRED_CODEOWNER_PATTERNS - set(codeowners))
    require(not missing_patterns, f"CODEOWNERS missing critical patterns {missing_patterns}")
    required_named_owners = {"@ProfHepta", "@Franksudoman", "@Tomasrgbsf"}
    for pattern in REQUIRED_CODEOWNER_PATTERNS:
        owners = set(codeowners[pattern])
        require(required_named_owners <= owners, f"CODEOWNERS pattern {pattern} lacks conflict-surviving review routes")

    all_available = available_domains == len(domains)
    summary = matrix.get("summary")
    require(isinstance(summary, dict), "review matrix summary missing")
    require(summary.get("domain_count") == len(domains), "review domain count summary mismatch")
    require(summary.get("assigned_domain_count") == assigned_domains, "assigned domain count summary mismatch")
    require(summary.get("redundant_domain_count") == redundant_domains, "redundant domain count summary mismatch")
    require(summary.get("available_domain_count") == available_domains, "available domain count summary mismatch")
    require(summary.get("blocked_domain_count") == blocked_domains, "blocked domain count summary mismatch")
    require(summary.get("named_reviewer_count") == len(global_identities), "named reviewer count summary mismatch")
    require(summary.get("eligible_named_reviewer_count") == len(global_eligible_identities), "eligible named reviewer count summary mismatch")
    require(summary.get("candidate_conflict_identity_count") == len(candidate_conflicts), "candidate conflict identity count summary mismatch")
    require(summary.get("all_required_reviews_available") is all_available, "review availability summary mismatch")

    claim_boundary = matrix.get("claim_boundary")
    require(isinstance(claim_boundary, dict), "review claim boundary missing")
    require(claim_boundary.get("matrix_presence_is_review") is False, "matrix cannot be review")
    require(claim_boundary.get("assignment_is_acceptance") is False, "assignment cannot be acceptance")
    require(claim_boundary.get("reviewer_routing_available") is all_available, "reviewer routing availability mismatch")
    require(claim_boundary.get("candidate_specific_availability") is all_available, "candidate-specific availability mismatch")
    require(claim_boundary.get("branch_policy_enforced") is False, "branch policy enforcement remains external")
    require(claim_boundary.get("latest_head_review_observed") is False, "latest-head review is candidate-specific")
    require(claim_boundary.get("production_review_complete") is False, "production review cannot be claimed")

    return {
        "schema": "trillionnium.independent-review-matrix-validation.v1",
        "domains": len(domains),
        "assigned_domains": assigned_domains,
        "redundant_domains": redundant_domains,
        "required_roles": required_roles,
        "named_reviewers": sorted(global_identities),
        "available_domains": available_domains,
        "blocked_domains": blocked_domains,
        "eligible_reviewers": sorted(global_eligible_identities),
        "candidate_conflicts": sorted(candidate_conflicts),
        "authorship_observed_through_head": candidate_scope["authorship_observed_through_head"],
        "all_required_reviews_available": all_available,
        "codeowners_redundant": True,
        "conflict_survivable": all_available,
        "branch_policy_enforced": False,
        "status": "passed" if all_available else "blocked-reviewer-capacity",
        "claim_boundary": {
            "matrix_presence_is_review": False,
            "assignment_is_acceptance": False,
            "administrative_enforcement_still_required": True,
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
