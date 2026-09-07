#!/usr/bin/env python3
"""Capture exact GitHub governance state as a bounded, non-creditable packet.

The observer is read-only. Missing administration credentials or incomplete
server-side policy are recorded and retained, then the workflow's final gate
fails closed. This script never converts observation into acceptance.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SHA = re.compile(r"^[0-9a-f]{40}$")
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class ReadbackError(RuntimeError):
    """Raised for malformed inputs or unbounded/invalid API responses."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReadbackError(message)


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def decode_json(raw: bytes, label: str) -> Any:
    require(len(raw) <= MAX_RESPONSE_BYTES, f"{label}: response exceeds byte limit")
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReadbackError(f"{label}: invalid UTF-8 JSON") from error


def canonical_env(name: str) -> str:
    value = os.environ.get(name, "")
    require(value and value.strip() == value, f"{name}: canonical non-empty value required")
    return value


def request_json(
    url: str,
    headers: dict[str, str],
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = (
        None
        if body is None
        else json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            return {
                "status": int(response.status),
                "body": decode_json(raw, url),
            }
    except urllib.error.HTTPError as error:
        raw = error.read(MAX_RESPONSE_BYTES + 1)
        try:
            value = decode_json(raw, url)
        except ReadbackError:
            value = {"message": "non-JSON error response"}
        return {"status": int(error.code), "body": value}
    except urllib.error.URLError:
        return {"status": 0, "body": {"message": "transport failure"}}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def capture(output: Path) -> dict[str, Any]:
    repository = canonical_env("TARGET_REPOSITORY")
    require(repository == "TrillionniumFoundation/TrillionniumGame", "repository mismatch")
    owner, name = repository.split("/", 1)
    pr_number_text = canonical_env("TARGET_PR")
    require(pr_number_text.isdigit() and int(pr_number_text) > 0, "TARGET_PR invalid")
    pr_number = int(pr_number_text)
    expected_head = canonical_env("TARGET_HEAD")
    expected_tree = canonical_env("TARGET_TREE")
    expected_base = canonical_env("TARGET_BASE")
    for label, value in (
        ("TARGET_HEAD", expected_head),
        ("TARGET_TREE", expected_tree),
        ("TARGET_BASE", expected_base),
    ):
        require(SHA.fullmatch(value) is not None, f"{label}: full lowercase SHA required")

    token = os.environ.get("ADMIN_TOKEN", "")
    api = f"https://api.github.com/repos/{repository}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "trillionnium-governance-readback-v2",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    endpoints: dict[str, Any] = {
        "repository": request_json(api, headers),
        "main_branch": request_json(api + "/branches/main", headers),
        "main_protection": request_json(api + "/branches/main/protection", headers),
        "rulesets": request_json(
            api + "/rulesets?includes_parents=true&per_page=100", headers
        ),
        "pull_request": request_json(api + f"/pulls/{pr_number}", headers),
        "reviews": request_json(
            api + f"/pulls/{pr_number}/reviews?per_page=100", headers
        ),
        "comments": request_json(
            api + f"/issues/{pr_number}/comments?per_page=100", headers
        ),
        "codeowners": request_json(
            api + "/contents/.github/CODEOWNERS?ref=" + expected_head, headers
        ),
        "target_commit": request_json(api + "/git/commits/" + expected_head, headers),
    }

    ruleset_rows = endpoints["rulesets"].get("body")
    detailed_rulesets = []
    if endpoints["rulesets"]["status"] == 200 and isinstance(ruleset_rows, list):
        require(len(ruleset_rows) <= 100, "ruleset response exceeds page contract")
        for row in ruleset_rows:
            identifier = row.get("id") if isinstance(row, dict) else None
            if isinstance(identifier, int) and identifier > 0:
                detailed_rulesets.append(
                    request_json(api + f"/rulesets/{identifier}", headers)
                )
    endpoints["ruleset_details"] = detailed_rulesets

    graphql: dict[str, Any] = {
        "status": 0,
        "body": {"message": "administration token unavailable"},
    }
    if token:
        query = """query($owner:String!,$name:String!,$pr:Int!){
          repository(owner:$owner,name:$name){
            branchProtectionRules(first:100){nodes{
              id pattern isAdminEnforced allowsForcePushes allowsDeletions
              requiresApprovingReviews requiredApprovingReviewCount
              dismissesStaleReviews requiresCodeOwnerReviews
              requiresConversationResolution requiresStrictStatusChecks
              requiredStatusCheckContexts
              bypassPullRequestAllowances(first:100){nodes{
                actor{__typename ... on User{login} ... on Team{slug} ... on App{slug}}
              }}
              bypassForcePushAllowances(first:100){nodes{
                actor{__typename ... on User{login} ... on Team{slug} ... on App{slug}}
              }}
            }}
            pullRequest(number:$pr){
              headRefOid baseRefOid reviewDecision mergeStateStatus isDraft
              reviewThreads(first:100){nodes{isResolved isOutdated}}
            }
          }
        }"""
        graphql = request_json(
            "https://api.github.com/graphql",
            headers,
            method="POST",
            body={
                "query": query,
                "variables": {"owner": owner, "name": name, "pr": pr_number},
            },
        )
    endpoints["graphql"] = graphql

    pr = (
        endpoints["pull_request"].get("body")
        if endpoints["pull_request"]["status"] == 200
        else {}
    )
    pr = pr if isinstance(pr, dict) else {}
    head = (pr.get("head") or {}).get("sha")
    base = (pr.get("base") or {}).get("sha")
    target_commit = endpoints["target_commit"].get("body")
    target_commit = target_commit if isinstance(target_commit, dict) else {}
    tree = (target_commit.get("tree") or {}).get("sha")

    protection = (
        endpoints["main_protection"].get("body")
        if endpoints["main_protection"]["status"] == 200
        else {}
    )
    protection = protection if isinstance(protection, dict) else {}
    reviews = protection.get("required_pull_request_reviews") or {}
    checks = protection.get("required_status_checks") or {}
    contexts = list(checks.get("contexts") or [])
    contexts.extend(
        item.get("context")
        for item in checks.get("checks") or []
        if isinstance(item, dict) and isinstance(item.get("context"), str)
    )
    contexts = sorted(set(contexts))

    ruleset_bypass = []
    for result in detailed_rulesets:
        body = result.get("body")
        if isinstance(body, dict):
            ruleset_bypass.extend(body.get("bypass_actors") or [])

    evaluation = {
        "administration_token_present": bool(token),
        "target_identity_matches": (
            head == expected_head and tree == expected_tree and base == expected_base
        ),
        "main_protection_readable": endpoints["main_protection"]["status"] == 200,
        "main_is_protected": bool(
            (endpoints["main_branch"].get("body") or {}).get("protected")
        ),
        "strict_status_checks": checks.get("strict") is True,
        "aggregate_gate_required": "trillionnium-game-merge-gate" in contexts,
        "administrator_enforcement": bool(
            (protection.get("enforce_admins") or {}).get("enabled")
        ),
        "force_push_forbidden": not bool(
            (protection.get("allow_force_pushes") or {}).get("enabled")
        ),
        "deletion_forbidden": not bool(
            (protection.get("allow_deletions") or {}).get("enabled")
        ),
        "approval_required": int(
            reviews.get("required_approving_review_count") or 0
        )
        >= 1,
        "stale_approvals_dismissed": reviews.get("dismiss_stale_reviews") is True,
        "codeowner_review_required": reviews.get("require_code_owner_reviews") is True,
        "latest_push_approval_required": reviews.get("require_last_push_approval") is True,
        "conversation_resolution_required": bool(
            (protection.get("required_conversation_resolution") or {}).get("enabled")
        ),
        "rulesets_readable": endpoints["rulesets"]["status"] == 200,
        "ruleset_bypass_actor_count": len(ruleset_bypass),
    }
    required = (
        "administration_token_present",
        "target_identity_matches",
        "main_protection_readable",
        "main_is_protected",
        "strict_status_checks",
        "aggregate_gate_required",
        "administrator_enforcement",
        "force_push_forbidden",
        "deletion_forbidden",
        "approval_required",
        "stale_approvals_dismissed",
        "codeowner_review_required",
        "latest_push_approval_required",
        "conversation_resolution_required",
        "rulesets_readable",
    )
    complete = all(evaluation.get(key) is True for key in required)
    accepted = complete and len(ruleset_bypass) == 0

    packet = {
        "schema": "trillionnium.github-governance-readback.v2",
        "repository": repository,
        "target": {
            "pull_request": pr_number,
            "base": expected_base,
            "head": expected_head,
            "tree": expected_tree,
        },
        "producer": {
            "workflow_repository": os.environ.get("GITHUB_REPOSITORY"),
            "workflow_sha": os.environ.get("GITHUB_SHA"),
            "run_id": os.environ.get("GITHUB_RUN_ID"),
            "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
            "job": os.environ.get("GITHUB_JOB"),
        },
        "evaluation": evaluation,
        "endpoints": endpoints,
        "claims": {
            "readback_complete": complete,
            "configuration_matches_required_minimum": accepted,
            "independently_accepted": False,
            "gap_closed": False,
            "production_ready": False,
        },
        "limitations": [
            "This workflow is a read-only observer and does not independently accept its own result.",
            "A harmless enforcement rehearsal and conflict-free governance review remain separately required.",
        ],
    }

    output.mkdir(parents=True, exist_ok=True)
    (output / "governance-readback.json").write_text(
        json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output / "result.env").write_text(
        f"readback_complete={str(complete).lower()}\n"
        f"configuration_matches_required_minimum={str(accepted).lower()}\n",
        encoding="utf-8",
    )
    return {"evaluation": evaluation, "complete": complete, "accepted": accepted}


def main() -> int:
    args = parse_args()
    try:
        result = capture(args.output)
    except ReadbackError as error:
        print(f"governance readback failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
