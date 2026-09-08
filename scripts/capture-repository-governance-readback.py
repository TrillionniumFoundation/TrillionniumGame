#!/usr/bin/env python3
"""Create a read-only, content-addressed GitHub governance read-back packet."""
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
from pathlib import Path
from typing import Any

ORIGIN = "https://api.github.com"
REPO = "TrillionniumFoundation/TrillionniumGame"
REPO_ID = 1323087470
BRANCH = "main"
REQUIRED_CHECK = "trillionnium-game-merge-gate"
TOKEN_ENV = "TRNM_GITHUB_ADMIN_AUDIT_TOKEN"
MAX_BYTES = 2 * 1024 * 1024
SHA = re.compile(r"^[0-9a-f]{40}$")
SAFE_HEADERS = {
    "content-type", "date", "etag", "last-modified", "link",
    "x-github-request-id", "x-github-api-version-selected",
    "x-oauth-scopes", "x-accepted-oauth-scopes",
    "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset",
}


class ReadbackError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        raise ReadbackError(f"redirect rejected: HTTP {code}")


class GitHubApi:
    def __init__(self, token: str, opener: Any | None = None, timeout: float = 30.0):
        if len(token) < 20 or any(char.isspace() for char in token):
            raise ReadbackError(f"{TOKEN_ENV} is absent or malformed")
        self.token = token
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self.timeout = timeout

    def get(self, path: str) -> tuple[int, dict[str, str], Any]:
        allowed = path == "/user" or path == f"/repos/{REPO}" or path.startswith(f"/repos/{REPO}/")
        if not allowed:
            raise ReadbackError(f"unapproved API path: {path}")
        url = ORIGIN + path
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != "api.github.com":
            raise ReadbackError("API origin drift")
        request = urllib.request.Request(
            url,
            method="GET",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "User-Agent": "TrillionniumGame-governance-readback/1",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            response = self.opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            response = error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ReadbackError(f"GitHub API transport failed for {path}: {error}") from error
        try:
            status = int(response.getcode())
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ReadbackError(f"response exceeds {MAX_BYTES} bytes")
            try:
                value = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ReadbackError(f"non-JSON response for {path}") from error
            safe = {
                str(name).lower(): str(value)
                for name, value in response.headers.items()
                if str(name).lower() in SAFE_HEADERS
            }
            return status, safe, value
        finally:
            response.close()


def enabled(value: Any, key: str, default: bool = False) -> bool:
    if not isinstance(value, dict) or key not in value:
        return default
    item = value[key]
    if isinstance(item, dict):
        item = item.get("enabled")
    return item is True


def nested(value: Any, *keys: str, default: Any = None) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def has_next_page(headers: dict[str, str]) -> bool:
    return 'rel="next"' in headers.get("link", "").lower()


def write(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


def prepare(path: Path) -> Path:
    path = path.expanduser().absolute()
    if path.exists():
        if path.is_symlink() or not path.is_dir() or any(path.iterdir()):
            raise ReadbackError("output must be a real empty directory")
    else:
        path.mkdir(mode=0o700, parents=True)
    return path


def retain(output: Path, key: str, path: str, result: tuple[int, dict[str, str], Any]) -> Any:
    status, headers, value = result
    body = json.dumps(value, sort_keys=True, indent=2).encode() + b"\n"
    write(output / f"{key}.json", body)
    metadata = {
        "schema": "trillionnium.github-api-read.v1",
        "method": "GET", "origin": ORIGIN, "path": path, "status": status,
        "headers": headers, "body_file": f"{key}.json",
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "body_size_bytes": len(body), "authorization_retained": False,
    }
    write(output / f"{key}.http.json", json.dumps(metadata, sort_keys=True, indent=2).encode() + b"\n")
    return value


def capture(api: Any, output_path: Path, expected_main: str, environments: list[str]) -> dict[str, Any]:
    if not SHA.fullmatch(expected_main):
        raise ReadbackError("expected main must be a lowercase 40-character SHA")
    if not environments:
        raise ReadbackError("at least one protected environment must be named")
    if any(not name or "/" in name or name in {".", ".."} for name in environments):
        raise ReadbackError("environment names must be canonical single components")
    environments = list(dict.fromkeys(environments))
    output = prepare(output_path)
    reads: dict[str, tuple[int, dict[str, str], Any]] = {}

    paths = {
        "actor": "/user",
        "repository": f"/repos/{REPO}",
        "branch": f"/repos/{REPO}/branches/{BRANCH}",
        "main_checks": f"/repos/{REPO}/commits/{expected_main}/check-runs?per_page=100",
        "protection": f"/repos/{REPO}/branches/{BRANCH}/protection",
        "rulesets": f"/repos/{REPO}/rulesets?includes_parents=true&per_page=100",
        "actions": f"/repos/{REPO}/actions/permissions",
        "workflow_permissions": f"/repos/{REPO}/actions/permissions/workflow",
        "environments": f"/repos/{REPO}/environments?per_page=100",
    }
    values: dict[str, Any] = {}
    for key, path in paths.items():
        reads[key] = api.get(path)
        values[key] = retain(output, key, path, reads[key])

    actor = values["actor"]
    login = actor.get("login") if isinstance(actor, dict) else None
    if not isinstance(login, str) or not login:
        raise ReadbackError("authenticated actor has no stable login")
    permission_path = f"/repos/{REPO}/collaborators/{urllib.parse.quote(login, safe='')}/permission"
    reads["actor_permission"] = api.get(permission_path)
    values["actor_permission"] = retain(output, "actor_permission", permission_path, reads["actor_permission"])

    ruleset_keys: dict[int, str] = {}
    rulesets = values["rulesets"] if isinstance(values["rulesets"], list) else []
    for row in rulesets:
        if not isinstance(row, dict) or not isinstance(row.get("id"), int):
            continue
        ruleset_id = row["id"]
        key = f"ruleset_{ruleset_id}"
        path = f"/repos/{REPO}/rulesets/{ruleset_id}"
        reads[key] = api.get(path)
        values[key] = retain(output, key, path, reads[key])
        ruleset_keys[ruleset_id] = key

    environment_keys: dict[str, str] = {}
    for name in environments:
        key = "environment_" + hashlib.sha256(name.encode()).hexdigest()[:16]
        path = f"/repos/{REPO}/environments/{urllib.parse.quote(name, safe='')}"
        reads[key] = api.get(path)
        values[key] = retain(output, key, path, reads[key])
        environment_keys[name] = key

    repository, branch, checks = values["repository"], values["branch"], values["main_checks"]
    protection, actions, workflow = values["protection"], values["actions"], values["workflow_permissions"]
    actor_permission = values["actor_permission"] if isinstance(values["actor_permission"], dict) else {}
    review = nested(protection, "required_pull_request_reviews", default={})
    required = nested(protection, "required_status_checks", default={})
    context_present = REQUIRED_CHECK in (required.get("contexts", []) if isinstance(required, dict) else [])
    context_present |= any(
        isinstance(item, dict) and item.get("context") == REQUIRED_CHECK
        for item in (required.get("checks", []) if isinstance(required, dict) else [])
    )
    check_rows = checks.get("check_runs", []) if isinstance(checks, dict) else []
    environment_names = {
        row.get("name") for row in nested(values["environments"], "environments", default=[])
        if isinstance(row, dict) and isinstance(row.get("name"), str)
    }
    bypass = review.get("bypass_pull_request_allowances", {}) if isinstance(review, dict) else None
    branch_bypass_empty = isinstance(bypass, dict) and all(value in (None, [], {}) for value in bypass.values())
    ruleset_details_valid = all(
        isinstance(values[key], dict) and values[key].get("id") == ruleset_id
        for ruleset_id, key in ruleset_keys.items()
    )
    ruleset_bypass_empty = all(
        isinstance(values[key], dict) and values[key].get("bypass_actors") in (None, [])
        for key in ruleset_keys.values()
    )
    protected_environments = all(
        isinstance(values[key], dict)
        and values[key].get("name") == name
        and any(
            isinstance(rule, dict) and rule.get("type") == "required_reviewers" and rule.get("reviewers")
            for rule in values[key].get("protection_rules", [])
        )
        for name, key in environment_keys.items()
    )

    assertions = {
        "all_reads_http_200": all(status == 200 for status, _, _ in reads.values()),
        "authenticated_human_admin": (
            isinstance(actor, dict) and actor.get("type") == "User"
            and isinstance(actor.get("id"), int)
            and actor_permission.get("permission") == "admin"
        ),
        "repository_identity": (
            isinstance(repository, dict) and repository.get("id") == REPO_ID
            and repository.get("full_name") == REPO and repository.get("default_branch") == BRANCH
            and repository.get("archived") is False
        ),
        "main_identity": nested(branch, "commit", "sha") == expected_main,
        "main_protected": isinstance(branch, dict) and branch.get("protected") is True,
        "main_check_collection_complete": (
            isinstance(checks, dict) and isinstance(checks.get("total_count"), int)
            and checks.get("total_count") == len(check_rows)
            and not has_next_page(reads["main_checks"][1])
        ),
        "successful_exact_main_merge_gate": any(
            isinstance(row, dict) and row.get("name") == REQUIRED_CHECK
            and row.get("status") == "completed" and row.get("conclusion") == "success"
            for row in check_rows
        ),
        "strict_required_check": isinstance(required, dict) and required.get("strict") is True and context_present,
        "admins_enforced": enabled(protection, "enforce_admins"),
        "stale_reviews_dismissed": isinstance(review, dict) and review.get("dismiss_stale_reviews") is True,
        "code_owner_review_required": isinstance(review, dict) and review.get("require_code_owner_reviews") is True,
        "latest_push_approval_required": isinstance(review, dict) and review.get("require_last_push_approval") is True,
        "approval_required": isinstance(review, dict) and review.get("required_approving_review_count", 0) >= 1,
        "conversation_resolution_required": enabled(protection, "required_conversation_resolution"),
        "linear_history_required": enabled(protection, "required_linear_history"),
        "force_push_forbidden": not enabled(protection, "allow_force_pushes", True),
        "deletion_forbidden": not enabled(protection, "allow_deletions", True),
        "branch_bypass_empty": branch_bypass_empty,
        "actions_enabled": isinstance(actions, dict) and actions.get("enabled") is True,
        "workflow_permissions_read_only": isinstance(workflow, dict) and workflow.get("default_workflow_permissions") == "read",
        "actions_cannot_approve": isinstance(workflow, dict) and workflow.get("can_approve_pull_request_reviews") is False,
        "rulesets_read_back": (
            reads["rulesets"][0] == 200
            and isinstance(values["rulesets"], list)
            and not has_next_page(reads["rulesets"][1])
            and all(reads[key][0] == 200 for key in ruleset_keys.values())
            and ruleset_details_valid
        ),
        "ruleset_bypass_empty": ruleset_bypass_empty,
        "environments_read_back": (
            reads["environments"][0] == 200
            and isinstance(values["environments"], dict)
            and isinstance(values["environments"].get("environments"), list)
            and not has_next_page(reads["environments"][1])
            and all(reads[key][0] == 200 for key in environment_keys.values())
        ),
        "required_environments_present": set(environments).issubset(environment_names),
        "required_environments_have_reviewers": protected_environments,
    }
    complete = all(assertions.values())
    result = {
        "schema": "trillionnium.repository-governance-readback.v1",
        "repository": REPO, "repository_id": REPO_ID, "branch": BRANCH,
        "expected_main": expected_main, "required_check": REQUIRED_CHECK,
        "authenticated_actor": {"id": actor.get("id"), "login": login, "type": actor.get("type"),
                                "repository_permission": actor_permission.get("permission")},
        "read_only": True, "mutation_methods_used": [],
        "required_environments": environments,
        "observed_environment_names": sorted(environment_names),
        "ruleset_ids": sorted(ruleset_keys),
        "http_status": {key: status for key, (status, _, _) in reads.items()},
        "assertions": assertions, "all_required_assertions": complete,
        "claims": {"governance_readback_complete": complete, "negative_rehearsal_accepted": False,
                   "independent_acceptance": False, "accepted_evidence": False,
                   "gap_closed": False, "production_ready": False},
        "limitations": [
            "Read-only observation; it does not perform or accept the negative no-bypass rehearsal.",
            "The policy administrator cannot independently accept their own evidence.",
        ],
    }
    write(output / "result.json", json.dumps(result, sort_keys=True, indent=2).encode() + b"\n")
    members = sorted(path for path in output.iterdir() if path.name != "SHA256SUMS")
    write(output / "SHA256SUMS", "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in members
    ).encode())
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-main", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--required-environment", action="append", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    args = parser.parse_args(argv)
    try:
        api = GitHubApi(os.environ.get(TOKEN_ENV, ""), timeout=args.timeout_seconds)
        result = capture(api, args.output, args.expected_main, args.required_environment)
    except ReadbackError as error:
        print(f"governance readback failed closed: {error}", file=sys.stderr)
        return 2
    if not result["all_required_assertions"]:
        print("governance readback retained but required assertions are false", file=sys.stderr)
        return 3
    print(f"governance readback complete for {REPO}@{args.expected_main}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())