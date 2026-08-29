#!/usr/bin/env python3
"""Validate desired GitHub governance and optionally perform authenticated read-back."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs/governance/GITHUB_ADMIN_ACCEPTANCE.json"
HEX40 = re.compile(r"^[a-f0-9]{40}$")
API_ROOT = "https://api.github.com"


class ValidationError(RuntimeError):
    """Raised when desired or observed repository governance is incomplete."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"{path}: top-level value must be an object")
    return value


def validate_contract(contract: dict[str, Any]) -> None:
    require(contract.get("schema") == "trillionnium.github-admin-acceptance.v1", "wrong governance schema")
    repository = contract.get("repository")
    require(isinstance(repository, dict), "repository contract is required")
    require(repository.get("id") == 1323087470, "repository ID drift")
    require(repository.get("full_name") == "TrillionniumFoundation/TrillionniumGame", "repository name drift")
    require(repository.get("default_branch") == "main", "default branch drift")

    actions = contract.get("actions")
    require(isinstance(actions, dict), "actions contract is required")
    require(actions.get("enabled") is True, "Actions must be enabled")
    require(actions.get("required_aggregate_check") == "trillionnium-game-merge-gate", "aggregate check drift")
    require(actions.get("empty_run_collection_is_success") is False, "empty runs must fail closed")
    require(actions.get("skipped_cancelled_or_older_head_is_success") is False, "non-current/non-success runs must fail closed")
    pins = actions.get("allowed_action_pins")
    require(isinstance(pins, list) and pins, "immutable action pins are required")
    for pin in pins:
        require(isinstance(pin, str) and re.search(r"@[a-f0-9]{40}$", pin) is not None, f"action is not commit-pinned: {pin!r}")

    rules = contract.get("main_rules")
    require(isinstance(rules, dict), "main rules are required")
    for key in (
        "direct_push_allowed",
        "force_push_allowed",
        "deletion_allowed",
    ):
        require(rules.get(key) is False, f"{key} must be false")
    for key in (
        "required_linear_history",
        "required_conversation_resolution",
        "required_latest_head",
        "dismiss_stale_approvals",
        "require_code_owner_review",
        "require_last_push_approval",
    ):
        require(rules.get(key) is True, f"{key} must be true")
    require(rules.get("required_approving_reviews", 0) >= 1, "at least one approval is required")
    require("trillionnium-game-merge-gate" in rules.get("required_status_checks", []), "aggregate check must be required")
    require(rules.get("bypass_roles") == [], "unreviewed bypass roles are forbidden")

    review = contract.get("independent_review")
    require(isinstance(review, dict), "independent review contract is required")
    require(review.get("implementer_may_self_approve") is False, "self approval must be forbidden")
    require(set(review.get("required_domains", [])) == {"database", "security", "protocol", "realtime"}, "review domains drift")
    require(review.get("named_user_or_team_required") is True, "named reviewer mapping required")


def request_json(path: str, token: str) -> tuple[dict[str, Any] | list[Any], str]:
    request = urllib.request.Request(
        API_ROOT + path,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "trillionnium-game-governance-audit",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise ValidationError(f"GitHub API {path} returned {error.code}: {body[:500]}") from error
    except urllib.error.URLError as error:
        raise ValidationError(f"GitHub API {path} failed: {error}") from error
    value = json.loads(data)
    require(isinstance(value, (dict, list)), f"GitHub API {path} returned an unexpected value")
    return value, hashlib.sha256(data).hexdigest()


def contexts(protection: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    checks = protection.get("required_status_checks")
    if isinstance(checks, dict):
        for value in checks.get("contexts", []):
            if isinstance(value, str):
                result.add(value)
        for row in checks.get("checks", []):
            if isinstance(row, dict) and isinstance(row.get("context"), str):
                result.add(row["context"])
    return result


def enabled(value: Any) -> bool:
    return isinstance(value, dict) and value.get("enabled") is True


def build_live_observation(contract: dict[str, Any], sha: str, token: str) -> dict[str, Any]:
    require(HEX40.fullmatch(sha) is not None, "--sha must be an exact 40-character commit")
    repository_name = contract["repository"]["full_name"]
    owner, repository = repository_name.split("/", 1)
    escaped_sha = urllib.parse.quote(sha, safe="")

    endpoints = {
        "repository": f"/repos/{owner}/{repository}",
        "actions_permissions": f"/repos/{owner}/{repository}/actions/permissions",
        "branch_protection": f"/repos/{owner}/{repository}/branches/main/protection",
        "rulesets": f"/repos/{owner}/{repository}/rulesets?includes_parents=true",
        "workflow_runs": f"/repos/{owner}/{repository}/actions/runs?head_sha={escaped_sha}&per_page=100",
        "check_runs": f"/repos/{owner}/{repository}/commits/{escaped_sha}/check-runs?per_page=100",
    }
    values: dict[str, Any] = {}
    digests: dict[str, str] = {}
    for name, path in endpoints.items():
        values[name], digests[name] = request_json(path, token)

    repo = values["repository"]
    actions = values["actions_permissions"]
    protection = values["branch_protection"]
    rulesets = values["rulesets"]
    runs = values["workflow_runs"]
    checks = values["check_runs"]
    require(isinstance(repo, dict), "repository response must be an object")
    require(isinstance(actions, dict), "Actions response must be an object")
    require(isinstance(protection, dict), "protection response must be an object")
    require(isinstance(rulesets, list), "rulesets response must be a list")
    require(isinstance(runs, dict), "workflow runs response must be an object")
    require(isinstance(checks, dict), "check runs response must be an object")

    run_rows = runs.get("workflow_runs", [])
    check_rows = checks.get("check_runs", [])
    require(isinstance(run_rows, list), "workflow_runs must be a list")
    require(isinstance(check_rows, list), "check_runs must be a list")
    exact_success_runs = [
        row
        for row in run_rows
        if isinstance(row, dict)
        and row.get("head_sha") == sha
        and row.get("status") == "completed"
        and row.get("conclusion") == "success"
    ]
    aggregate_checks = [
        row
        for row in check_rows
        if isinstance(row, dict)
        and row.get("name") == contract["actions"]["required_aggregate_check"]
        and row.get("status") == "completed"
        and row.get("conclusion") == "success"
    ]

    review = protection.get("required_pull_request_reviews")
    review = review if isinstance(review, dict) else {}
    observed_contexts = contexts(protection)
    facts = {
        "repository_identity": repo.get("id") == 1323087470
        and repo.get("full_name") == repository_name
        and repo.get("default_branch") == "main",
        "actions_enabled": actions.get("enabled") is True,
        "main_protection_readable": True,
        "aggregate_check_required": contract["actions"]["required_aggregate_check"] in observed_contexts,
        "strict_latest_head": protection.get("required_status_checks", {}).get("strict") is True,
        "force_push_blocked": not enabled(protection.get("allow_force_pushes")),
        "deletion_blocked": not enabled(protection.get("allow_deletions")),
        "linear_history_required": enabled(protection.get("required_linear_history")),
        "conversation_resolution_required": enabled(protection.get("required_conversation_resolution")),
        "approval_count_sufficient": review.get("required_approving_review_count", 0)
        >= contract["main_rules"]["required_approving_reviews"],
        "stale_approvals_dismissed": review.get("dismiss_stale_reviews") is True,
        "code_owner_review_required": review.get("require_code_owner_reviews") is True,
        "last_push_approval_required": review.get("require_last_push_approval") is True,
        "ruleset_collection_nonempty": len(rulesets) > 0,
        "exact_head_success_run": len(exact_success_runs) > 0,
        "aggregate_success_check": len(aggregate_checks) > 0,
    }
    return {
        "schema": "trillionnium.github-governance-observation.v1",
        "repository": repository_name,
        "candidate_commit": sha,
        "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "observer_identity": os.environ.get("GITHUB_ACTOR", "authenticated-api-observer"),
        "facts": facts,
        "workflow_run_ids": [row.get("id") for row in exact_success_runs],
        "aggregate_check_ids": [row.get("id") for row in aggregate_checks],
        "response_sha256": digests,
        "accepted": all(facts.values()),
        "claim_boundary": "Acceptance closes repository-governance prerequisites only; it grants no protocol, data or production compatibility credit.",
    }


def validate_observation(contract: dict[str, Any], observation: dict[str, Any]) -> None:
    require(observation.get("schema") == "trillionnium.github-governance-observation.v1", "wrong observation schema")
    require(observation.get("repository") == contract["repository"]["full_name"], "observation repository mismatch")
    require(isinstance(observation.get("candidate_commit"), str) and HEX40.fullmatch(observation["candidate_commit"]) is not None, "observation commit missing")
    require(isinstance(observation.get("recorded_at"), str), "recorded_at missing")
    require(isinstance(observation.get("observer_identity"), str) and observation["observer_identity"], "observer identity missing")
    facts = observation.get("facts")
    require(isinstance(facts, dict) and facts, "observation facts missing")
    require(all(value is True for value in facts.values()), "one or more governance facts are false")
    require(observation.get("accepted") is True, "observation is not accepted")
    digests = observation.get("response_sha256")
    require(isinstance(digests, dict) and digests, "API response digests missing")
    require(all(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) for value in digests.values()), "invalid response digest")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="perform authenticated GitHub API read-back")
    parser.add_argument("--sha", help="exact candidate commit for --live")
    parser.add_argument("--observation", type=Path, help="validate a previously generated observation")
    parser.add_argument("--output", type=Path, help="write live observation to this path")
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    try:
        contract = load_object(CONTRACT_PATH)
        validate_contract(contract)
        if arguments.observation is not None:
            validate_observation(contract, load_object(arguments.observation))
            result = {"schema": "trillionnium.github-admin-contract-validation.v1", "contract_valid": True, "observation_valid": True}
        elif arguments.live:
            token = os.environ.get("GITHUB_TOKEN")
            require(isinstance(token, str) and token, "GITHUB_TOKEN is required for --live")
            require(isinstance(arguments.sha, str), "--sha is required for --live")
            observation = build_live_observation(contract, arguments.sha, token)
            if arguments.output is not None:
                arguments.output.parent.mkdir(parents=True, exist_ok=True)
                arguments.output.write_text(json.dumps(observation, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            require(observation["accepted"] is True, "live governance observation did not meet acceptance")
            result = observation
        else:
            result = {
                "schema": "trillionnium.github-admin-contract-validation.v1",
                "contract_valid": True,
                "live_observation_performed": False,
                "external_gaps_closed": False,
            }
    except (OSError, ValueError, json.JSONDecodeError, ValidationError) as error:
        print(f"repository governance validation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
