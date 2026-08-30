#!/usr/bin/env python3
"""Read GitHub Actions/check/protection state without mutating the repository."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPOSITORY = "TrillionniumFoundation/TrillionniumGame"
REPOSITORY_ID = 1323087470
MAIN = "main"


class StateError(RuntimeError):
    pass


def gh(path: str) -> Any:
    completed = subprocess.run(
        [
            "gh",
            "api",
            "--hostname",
            "github.com",
            "-H",
            "Accept: application/vnd.github+json",
            path,
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        raise StateError(f"gh api {path} failed: {completed.stderr.strip()}")
    return json.loads(completed.stdout)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--head", required=True)
    parser.add_argument("--output", default="run/governance/github-state.json")
    arguments = parser.parse_args()
    try:
        if len(arguments.head) != 40 or any(c not in "0123456789abcdef" for c in arguments.head):
            raise StateError("--head must be a 40-character lowercase commit")

        repository = gh(f"/repos/{REPOSITORY}")
        if repository.get("id") != REPOSITORY_ID or repository.get("full_name") != REPOSITORY:
            raise StateError("repository identity mismatch")

        actions = gh(f"/repos/{REPOSITORY}/actions/permissions")
        branch = gh(f"/repos/{REPOSITORY}/branches/{MAIN}")
        runs = gh(f"/repos/{REPOSITORY}/actions/runs?head_sha={arguments.head}&per_page=100")
        checks = gh(f"/repos/{REPOSITORY}/commits/{arguments.head}/check-runs?per_page=100")
        try:
            protection = gh(f"/repos/{REPOSITORY}/branches/{MAIN}/protection")
        except StateError as error:
            protection = {"error": str(error)}

        completed_successful = [
            row
            for row in checks.get("check_runs", [])
            if row.get("status") == "completed" and row.get("conclusion") == "success"
        ]
        names = {row.get("name") for row in completed_successful}
        required_names = {
            "trillionnium-game-merge-gate",
            "v3-source-and-scope-gate",
        }
        exact_head_ready = required_names.issubset(names)
        main_protected = bool(branch.get("protected")) and "error" not in protection
        required_contexts = set(
            protection.get("required_status_checks", {}).get("contexts", [])
            if isinstance(protection, dict)
            else []
        )
        protection_ready = (
            main_protected
            and required_names.issubset(required_contexts)
            and protection.get("required_status_checks", {}).get("strict") is True
            and protection.get("enforce_admins", {}).get("enabled") is True
            and protection.get("required_pull_request_reviews", {}).get("dismiss_stale_reviews") is True
            and protection.get("required_pull_request_reviews", {}).get("require_code_owner_reviews") is True
            and protection.get("required_pull_request_reviews", {}).get("require_last_push_approval") is True
        )

        result = {
            "schema": "trillionnium.github-governance-state.v1",
            "repository": REPOSITORY,
            "repository_id": REPOSITORY_ID,
            "observed_head": arguments.head,
            "actions": {
                "enabled": actions.get("enabled") is True,
                "allowed_actions": actions.get("allowed_actions"),
                "workflow_run_count": runs.get("total_count", 0),
                "check_run_count": checks.get("total_count", 0),
                "completed_successful_names": sorted(name for name in names if isinstance(name, str)),
                "required_exact_head_checks_present": exact_head_ready,
            },
            "main": {
                "commit": branch.get("commit", {}).get("sha"),
                "protected": main_protected,
                "required_contexts": sorted(required_contexts),
                "strict_and_review_policy_ready": protection_ready,
            },
            "gaps": {
                "GAP-P0-CI-001": "closed" if actions.get("enabled") is True and exact_head_ready else "blocked-external-admin",
                "GAP-P0-GOV-001": "closed" if protection_ready else "blocked-external-admin",
            },
            "claims": {
                "repository_control_ready": actions.get("enabled") is True and exact_head_ready and protection_ready,
                "compatibility_credit": False,
                "production_ready": False,
                "public_online": False,
            },
        }
        output = Path(arguments.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (OSError, json.JSONDecodeError, StateError) as error:
        print(f"GitHub governance state check failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
