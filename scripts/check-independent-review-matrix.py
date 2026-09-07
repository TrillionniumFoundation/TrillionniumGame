#!/usr/bin/env python3
"""Validate candidate-aware reviewer routing without treating routing as acceptance."""
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
LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
CONFLICT_TYPES = {
    "candidate-author",
    "evidence-producer",
    "administrator-mutator-under-review",
}
REQUIRED_CODEOWNER_PATTERNS = {
    "*", "/.github/", "/CURRENT_PLAN.md", "/docs/governance/",
    "/docs/evidence/", "/docs/review/", "/docs/status/", "/migrations/",
    "/database/", "/crates/trnm-persistence-core/",
    "/crates/trnm-persistence-pg/", "/SECURITY.md",
    "/docs/SECURITY_AND_PRIVACY.md", "/crates/trnm-token-core/",
    "/crates/trnm-token-jwt-adapter/", "/crates/trnm-token-crypto-provider/",
    "/crates/trnm-token-jwt-provider-adapter/", "/crates/trnm-session-core/",
    "/contracts/", "/crates/trnm-canonical-core/",
    "/crates/trnm-transport-core/", "/crates/trnm-realtime-wire/",
    "/crates/trnm-presence-core/", "/crates/trnm-presence-router-v2/",
    "/runtime/", "/deploy/", "/compose.yaml",
    "/docs/OPERATIONS_AND_RELEASE.md",
}
EXPECTED_SCOPE = {
    "repository": REPOSITORY,
    "pull_request": 63,
    "base_commit": "d66c1b5b614a2a7b682c233fe2e7a19939b6976b",
    "authorship_observed_through_head": "88c02a417c7b0c64f27dc9f49e0b5e6317150964",
    "head_tree": "9d3ae2fadb2684f095855ecf16307376b55a440f",
    "prospective_merge": "6965f95d03031156edc92cc862bd979ff87e9edb",
    "candidate_commit_count": 249,
}
EXPECTED_EVIDENCE = {
    "workflow_run_id": 34091807478,
    "job_id": 101646679602,
    "artifact_id": 10007085164,
    "artifact_sha256": "aabfd9621b1f13ca480546434a787171236d72349a27fe17925c0b98dc76a9f9",
    "observed_at": "2026-09-07T06:39:35Z",
}
EXPECTED_CONFLICTS: dict[int, tuple[str, dict[str, tuple[tuple[str, str], ...]]]] = {
    273670192: ("Franksudoman", {
        "candidate-author": (("44d0a4274d3c665aa58d17fbde9f9dc0ded08281", "candidate-history"),),
        "evidence-producer": (("68a7401aea06b7dd398a2deb802098c2f99d0779", "docs/evidence/**"),),
        "administrator-mutator-under-review": (("c87f3f25e7b859a7b32d51236f7c458967d7a350", "docs/governance/**"),),
    }),
    102159240: ("ProfHepta", {
        "candidate-author": (("a77a8a79f3258668f5e56fa0354cf0e754ceab3f", "candidate-history"),),
        "evidence-producer": (("a77a8a79f3258668f5e56fa0354cf0e754ceab3f", "docs/evidence/**"),),
        "administrator-mutator-under-review": (("7deee4377cff401854647151842c582f038feef1", "docs/governance/**"),),
    }),
    273673612: ("Tomasrgbsf", {
        "candidate-author": (("3d55f1dd10201bf97f661f9c3f458a05931b091e", "candidate-history"),),
        "evidence-producer": (("d952f322c4614d63cd7c514adb264c3bfd6a1dfd", "docs/evidence/**"),),
        "administrator-mutator-under-review": (("d66c1b5b614a2a7b682c233fe2e7a19939b6976b", "protected-main-negative-rehearsal"),),
    }),
}


class ReviewMatrixError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReviewMatrixError(message)


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def reject_constant(value: str) -> Any:
    raise ReviewMatrixError(f"non-finite JSON value: {value}")


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except ReviewMatrixError:
        raise
    except (OSError, json.JSONDecodeError) as error:
        raise ReviewMatrixError(f"{path}: {error}") from error
    require(isinstance(value, dict), f"{path}: root must be an object")
    return value


def timestamp(value: Any, label: str) -> datetime:
    require(isinstance(value, str) and value, f"{label}: timestamp missing")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise ReviewMatrixError(f"{label}: invalid timestamp") from error
    require(parsed.tzinfo is not None, f"{label}: timezone required")
    return parsed.astimezone(timezone.utc)


def positive(value: Any, label: str) -> int:
    require(
        isinstance(value, int) and not isinstance(value, bool) and value > 0,
        f"{label}: positive integer required",
    )
    return value


def login_key(value: str) -> str:
    return value.casefold()


def string_list(value: Any, label: str, *, nonempty: bool = True) -> list[str]:
    require(isinstance(value, list), f"{label}: list required")
    require(not nonempty or value, f"{label}: non-empty list required")
    require(
        all(isinstance(item, str) and item for item in value),
        f"{label}: non-empty strings required",
    )
    require(len(value) == len(set(value)), f"{label}: duplicates forbidden")
    return value


def parse_codeowners(path: Path) -> dict[str, set[str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ReviewMatrixError(f"{path}: {error}") from error
    rows: dict[str, set[str]] = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        require(
            len(parts) >= 3,
            f"CODEOWNERS line {number}: at least two owners required",
        )
        pattern, owners = parts[0], parts[1:]
        require(pattern not in rows, f"CODEOWNERS line {number}: duplicate pattern")
        require(
            all(owner.startswith("@") for owner in owners),
            f"CODEOWNERS line {number}: invalid owner",
        )
        normalized = {owner[1:].casefold() for owner in owners}
        require(
            len(normalized) == len(owners),
            f"CODEOWNERS line {number}: duplicate owner alias",
        )
        rows[pattern] = normalized
    return rows


def evidence_rows(value: Any, label: str) -> tuple[tuple[str, str], ...]:
    require(isinstance(value, list) and value, f"{label}: evidence required")
    result: list[tuple[str, str]] = []
    for index, row in enumerate(value):
        require(
            isinstance(row, dict) and set(row) == {"commit", "scope"},
            f"{label}[{index}]: evidence fields drift",
        )
        commit, scope = row.get("commit"), row.get("scope")
        require(
            isinstance(commit, str) and SHA.fullmatch(commit) is not None,
            f"{label}[{index}]: full Git SHA required",
        )
        require(
            isinstance(scope, str) and scope.strip() == scope and bool(scope),
            f"{label}[{index}]: scope required",
        )
        result.append((commit, scope))
    require(len(result) == len(set(result)), f"{label}: duplicate evidence")
    return tuple(result)


def validate_scope(scope: Any) -> dict[int, frozenset[str]]:
    require(isinstance(scope, dict), "candidate scope is required")
    expected_fields = {*EXPECTED_SCOPE, "evidence", "conflict_principals"}
    require(set(scope) == expected_fields, "candidate scope fields drift")
    for field, expected in EXPECTED_SCOPE.items():
        require(scope.get(field) == expected, f"candidate {field} exact binding mismatch")
    for field in (
        "base_commit",
        "authorship_observed_through_head",
        "head_tree",
        "prospective_merge",
    ):
        require(
            SHA.fullmatch(scope[field]) is not None,
            f"candidate {field}: full Git SHA required",
        )

    evidence = scope.get("evidence")
    require(
        evidence == EXPECTED_EVIDENCE,
        "candidate conflict evidence exact binding mismatch",
    )
    require(
        SHA256.fullmatch(evidence["artifact_sha256"]) is not None,
        "candidate artifact digest invalid",
    )
    timestamp(evidence["observed_at"], "candidate evidence observed_at")
    for field in ("workflow_run_id", "job_id", "artifact_id"):
        positive(evidence[field], f"candidate evidence {field}")

    principals = scope.get("conflict_principals")
    require(
        isinstance(principals, list) and principals,
        "candidate conflict principals are required",
    )
    result: dict[int, frozenset[str]] = {}
    logins: set[str] = set()
    for index, principal in enumerate(principals):
        label = f"candidate principal[{index}]"
        require(
            isinstance(principal, dict)
            and set(principal) == {"github_user_id", "login", "conflicts"},
            f"{label}: fields drift",
        )
        user_id = positive(principal["github_user_id"], f"{label}.github_user_id")
        login = principal["login"]
        require(
            isinstance(login, str) and LOGIN.fullmatch(login) is not None,
            f"{label}: invalid login",
        )
        key = login_key(login)
        require(user_id not in result, f"{label}: duplicate stable GitHub user id")
        require(key not in logins, f"{label}: duplicate case-insensitive login")
        logins.add(key)
        conflicts = principal["conflicts"]
        require(isinstance(conflicts, list) and conflicts, f"{label}: conflicts required")
        conflict_map: dict[str, tuple[tuple[str, str], ...]] = {}
        for offset, conflict in enumerate(conflicts):
            item = f"{label}.conflicts[{offset}]"
            require(
                isinstance(conflict, dict) and set(conflict) == {"type", "evidence"},
                f"{item}: fields drift",
            )
            kind = conflict["type"]
            require(kind in CONFLICT_TYPES, f"{item}: unknown conflict type")
            require(kind not in conflict_map, f"{label}: duplicate conflict type")
            conflict_map[kind] = evidence_rows(
                conflict["evidence"], f"{item}.evidence"
            )
        require(
            set(conflict_map) == CONFLICT_TYPES,
            f"{label}: candidate principal conflicts incomplete",
        )
        expected = EXPECTED_CONFLICTS.get(user_id)
        if expected is not None:
            expected_login, expected_map = expected
            require(
                key == login_key(expected_login),
                f"{label}: stable GitHub user id/login binding mismatch",
            )
            require(
                conflict_map == expected_map,
                f"{label}: principal conflict binding mismatch",
            )
        result[user_id] = frozenset(conflict_map)
    missing = sorted(set(EXPECTED_CONFLICTS) - set(result))
    require(not missing, f"candidate conflict principals removed: {missing}")
    return result


def validate(
    matrix_path: Path = MATRIX_PATH,
    gaps_path: Path = GAPS_PATH,
    codeowners_path: Path = CODEOWNERS_PATH,
) -> dict[str, Any]:
    matrix, gaps = load(matrix_path), load(gaps_path)
    require(
        set(matrix)
        == {
            "schema",
            "project_id",
            "plan_version",
            "generated_at",
            "policy",
            "candidate_scope",
            "reviewers",
            "domains",
            "summary",
            "claim_boundary",
        },
        "review matrix top-level fields drift",
    )
    require(
        matrix["schema"] == "trillionnium.independent-review-matrix.v2",
        "wrong review matrix schema",
    )
    require(
        matrix["project_id"] == "trillionnium-game" and matrix["plan_version"] == 3,
        "wrong review matrix identity",
    )
    generated_at = timestamp(matrix["generated_at"], "generated_at")

    policy = matrix.get("policy")
    false_keys = {
        "implementation_author_may_accept_own_evidence",
        "administrator_mutator_may_accept_own_governance_evidence",
        "evidence_producer_may_accept_own_evidence",
    }
    true_keys = {
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
        "stable_principal_id_required",
        "github_login_case_insensitive",
        "candidate_conflict_evidence_binding_required",
    }
    require(
        isinstance(policy, dict)
        and set(policy)
        == false_keys | true_keys | {"minimum_redundant_reviewers_per_domain"},
        "review policy fields drift",
    )
    for key in false_keys:
        require(policy[key] is False, f"{key} must be false")
    for key in true_keys:
        require(policy[key] is True, f"{key} must be true")
    minimum = positive(
        policy["minimum_redundant_reviewers_per_domain"], "minimum reviewers"
    )
    require(minimum >= 2, "minimum reviewers must be at least two")

    candidate_conflicts = validate_scope(matrix["candidate_scope"])
    reviewers = matrix.get("reviewers")
    require(isinstance(reviewers, list) and reviewers, "reviewer registry required")
    registry: dict[int, dict[str, Any]] = {}
    logins: dict[str, int] = {}
    expected_login_keys = {login_key(item[0]) for item in EXPECTED_CONFLICTS.values()}
    for index, reviewer in enumerate(reviewers):
        label = f"reviewers[{index}]"
        required = {
            "github_user_id",
            "login",
            "kind",
            "organization",
            "effective_at",
            "expires_at",
            "permission_readback",
            "qualification_basis",
        }
        require(
            isinstance(reviewer, dict) and set(reviewer) == required,
            f"{label}: fields drift",
        )
        user_id = positive(reviewer["github_user_id"], f"{label}.github_user_id")
        login = reviewer["login"]
        require(
            isinstance(login, str) and LOGIN.fullmatch(login) is not None,
            f"{label}: invalid login",
        )
        key = login_key(login)
        require(user_id not in registry, f"{label}: duplicate stable GitHub user id")
        require(key not in logins, f"{label}: duplicate case-insensitive login")
        expected = EXPECTED_CONFLICTS.get(user_id)
        if expected is not None:
            require(
                key == login_key(expected[0]),
                f"{label}: stable GitHub user id/login binding mismatch",
            )
        elif key in expected_login_keys:
            raise ReviewMatrixError(f"{label}: known login with wrong GitHub user id")
        require(
            reviewer["kind"] == "github-user"
            and reviewer["organization"] == "TrillionniumFoundation",
            f"{label}: reviewer identity class mismatch",
        )
        effective = timestamp(reviewer["effective_at"], f"{label}.effective_at")
        expires = timestamp(reviewer["expires_at"], f"{label}.expires_at")
        require(effective <= generated_at < expires, f"{label}: inactive or expired")
        permission = reviewer["permission_readback"]
        require(
            isinstance(permission, dict)
            and set(permission) == {"repository", "permission", "observed_at"},
            f"{label}: permission fields drift",
        )
        require(
            permission["repository"] == REPOSITORY
            and permission["permission"] in {"write", "maintain", "admin"},
            f"{label}: insufficient repository permission",
        )
        observed = timestamp(
            permission["observed_at"], f"{label}.permission.observed_at"
        )
        age = generated_at - observed
        require(
            0 <= age.total_seconds() <= 7 * 86400,
            f"{label}: permission readback stale or from future",
        )
        string_list(reviewer["qualification_basis"], f"{label}.qualification_basis")
        registry[user_id] = reviewer
        logins[key] = user_id

    missing_reviewers = sorted(set(EXPECTED_CONFLICTS) - set(registry))
    require(
        not missing_reviewers,
        f"conflicted reviewer routes removed: {missing_reviewers}",
    )
    gap_ids = {
        row.get("id")
        for row in gaps.get("gaps", [])
        if isinstance(row, dict)
    }
    domains = matrix.get("domains")
    require(isinstance(domains, list) and domains, "review domains required")
    domain_ids: set[str] = set()
    available = blocked = redundant = 0
    assigned_global: set[int] = set()
    eligible_global: set[int] = set()
    required_roles = 0
    for index, domain in enumerate(domains):
        label = f"domains[{index}]"
        require(
            isinstance(domain, dict)
            and set(domain)
            == {
                "id",
                "protected_paths",
                "required_roles",
                "assigned_reviewer_ids",
                "status",
                "blocking_gaps",
            },
            f"{label}: fields drift",
        )
        domain_id = domain["id"]
        require(
            isinstance(domain_id, str)
            and domain_id
            and domain_id not in domain_ids,
            f"{label}: invalid or duplicate domain id",
        )
        domain_ids.add(domain_id)
        string_list(domain["protected_paths"], f"{domain_id}.protected_paths")
        roles = string_list(domain["required_roles"], f"{domain_id}.required_roles")
        required_roles += len(roles)
        blocking_gaps = string_list(
            domain["blocking_gaps"], f"{domain_id}.blocking_gaps"
        )
        unknown = sorted(set(blocking_gaps) - gap_ids)
        require(not unknown, f"{domain_id}: unknown blocking gaps {unknown}")
        assigned = domain["assigned_reviewer_ids"]
        require(
            isinstance(assigned, list)
            and all(isinstance(item, int) and item > 0 for item in assigned),
            f"{domain_id}: reviewer ids invalid",
        )
        require(
            len(assigned) == len(set(assigned)),
            f"{domain_id}: duplicate reviewer id",
        )
        require(
            len(assigned) >= minimum,
            f"{domain_id}: at least {minimum} reviewers required",
        )
        missing = sorted(set(assigned) - set(registry))
        require(not missing, f"{domain_id}: unknown reviewer ids {missing}")
        required_routes = set(EXPECTED_CONFLICTS)
        require(
            required_routes <= set(assigned),
            f"{domain_id}: candidate-conflicted routing principals removed",
        )
        for excluded in assigned:
            require(
                len([item for item in assigned if item != excluded]) >= minimum,
                f"{domain_id}: losing reviewer {excluded} breaks routing redundancy",
            )
        redundant += 1
        assigned_global.update(assigned)
        eligible = [item for item in assigned if item not in candidate_conflicts]
        eligible_global.update(eligible)
        domain_available = len(eligible) >= minimum
        if domain_available:
            require(
                domain["status"] == "active",
                f"{domain_id}: available domain must be active",
            )
            available += 1
        else:
            require(
                domain["status"] == "blocked-reviewer-capacity",
                f"{domain_id}: insufficient conflict-free reviewers must block",
            )
            blocked += 1

    codeowners = parse_codeowners(codeowners_path)
    missing_patterns = sorted(REQUIRED_CODEOWNER_PATTERNS - set(codeowners))
    require(
        not missing_patterns,
        f"CODEOWNERS missing critical patterns {missing_patterns}",
    )
    expected_owners = {login_key(item[0]) for item in EXPECTED_CONFLICTS.values()}
    for pattern in REQUIRED_CODEOWNER_PATTERNS:
        require(
            expected_owners <= codeowners[pattern],
            f"CODEOWNERS pattern {pattern} lacks conflict-surviving review routes",
        )

    all_available = available == len(domains)
    summary = matrix.get("summary")
    expected_summary = {
        "domain_count": len(domains),
        "assigned_domain_count": len(domains),
        "redundant_domain_count": redundant,
        "available_domain_count": available,
        "blocked_domain_count": blocked,
        "named_reviewer_count": len(assigned_global),
        "eligible_named_reviewer_count": len(eligible_global),
        "candidate_conflict_identity_count": len(candidate_conflicts),
        "all_required_reviews_available": all_available,
    }
    require(summary == expected_summary, "review summary mismatch")
    claims = matrix.get("claim_boundary")
    expected_claims = {
        "matrix_presence_is_review": False,
        "assignment_is_acceptance": False,
        "reviewer_routing_available": all_available,
        "candidate_specific_availability": all_available,
        "branch_policy_enforced": False,
        "latest_head_review_observed": False,
        "production_review_complete": False,
    }
    require(claims == expected_claims, "review claim boundary mismatch")

    conflict_logins = sorted(
        (EXPECTED_CONFLICTS[item][0] for item in candidate_conflicts),
        key=str.casefold,
    )
    eligible_logins = sorted(
        (registry[item]["login"] for item in eligible_global),
        key=str.casefold,
    )
    return {
        "schema": "trillionnium.independent-review-matrix-validation.v2",
        "domains": len(domains),
        "assigned_domains": len(domains),
        "redundant_domains": redundant,
        "required_roles": required_roles,
        "named_reviewers": sorted(
            (registry[item]["login"] for item in assigned_global),
            key=str.casefold,
        ),
        "available_domains": available,
        "blocked_domains": blocked,
        "eligible_reviewers": eligible_logins,
        "candidate_conflicts": conflict_logins,
        "candidate_conflict_user_ids": sorted(candidate_conflicts),
        "authorship_observed_through_head": matrix["candidate_scope"][
            "authorship_observed_through_head"
        ],
        "candidate_conflict_evidence_bound": True,
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
