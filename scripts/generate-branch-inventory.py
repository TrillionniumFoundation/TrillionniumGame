#!/usr/bin/env python3
"""Generate a non-destructive exact branch/ref disposition inventory."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ACTIVE_BRANCHES_PATH = ROOT / "docs/governance/ACTIVE_BRANCHES.json"
BRANCH_NAME = re.compile(r"^(?!/)(?!.*//)(?!.*\.\.)(?!.*@\{)(?!.*\\)[^\x00-\x20~^:?*\[]+(?<![/.])$")


class InventoryError(RuntimeError):
    """Raised when ref inventory or disposition policy is ambiguous."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise InventoryError(message)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def git(
    *arguments: str,
    check: bool = True,
    root: Path = ROOT,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if check and completed.returncode != 0:
        raise InventoryError(
            f"git {' '.join(arguments)} failed: {completed.stderr.strip()}"
        )
    return completed


def reachable(
    ancestor: str,
    descendant: str,
    *,
    root: Path = ROOT,
) -> bool:
    return (
        git(
            "merge-base",
            "--is-ancestor",
            ancestor,
            descendant,
            check=False,
            root=root,
        ).returncode
        == 0
    )


def parse_time(value: Any, label: str) -> datetime:
    require(isinstance(value, str) and value, f"{label}: timestamp required")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise InventoryError(f"{label}: invalid timestamp") from error
    require(parsed.tzinfo is not None, f"{label}: timezone required")
    return parsed.astimezone(timezone.utc)


def validate_branch_name(value: Any, label: str) -> str:
    require(
        isinstance(value, str)
        and value.strip() == value
        and BRANCH_NAME.fullmatch(value) is not None,
        f"{label}: invalid branch name",
    )
    return value


def load_active_branches(
    path: Path = ACTIVE_BRANCHES_PATH,
) -> tuple[dict[str, dict[str, Any]], str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise InventoryError(f"{path}: {error}") from error
    require(isinstance(value, dict), "active branch registry must be an object")
    require(
        value.get("schema") == "trillionnium.active-branches.v1",
        "wrong active branch registry schema",
    )
    require(value.get("project_id") == "trillionnium-game", "wrong project_id")
    require(value.get("plan_version") == 3, "active branches must target plan v3")
    parse_time(value.get("generated_at"), "generated_at")

    policy = value.get("policy")
    require(isinstance(policy, dict), "active branch policy must be an object")
    required_true = (
        "exact_name_required",
        "active_branch_may_be_nonancestor",
        "active_branch_never_auto_deleted",
        "main_must_be_active",
        "cleanup_requires_independent_review",
        "cleanup_requires_before_and_after_manifests",
    )
    for key in required_true:
        require(policy.get(key) is True, f"active branch policy {key} must be true")
    require(
        policy.get("nonancestor_branch_deletion_allowed") is False,
        "nonancestor branch deletion must be false",
    )
    maximum = policy.get("maximum_active_branch_count")
    require(
        isinstance(maximum, int)
        and not isinstance(maximum, bool)
        and 1 <= maximum <= 20,
        "maximum_active_branch_count must be in 1..20",
    )

    rows = value.get("active_branches")
    require(isinstance(rows, list) and rows, "active_branches must be non-empty")
    require(len(rows) <= maximum, "active branch count exceeds policy maximum")
    result: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        require(isinstance(row, dict), f"active_branches[{index}] must be an object")
        require(
            set(row) == {"name", "role", "pull_request"},
            f"active_branches[{index}] fields drift",
        )
        name = validate_branch_name(row.get("name"), f"active_branches[{index}].name")
        require(name not in result, f"duplicate active branch: {name}")
        role = row.get("role")
        require(
            isinstance(role, str) and role.strip() == role and bool(role),
            f"{name}: role must be canonical non-empty text",
        )
        pull_request = row.get("pull_request")
        require(
            pull_request is None
            or (
                isinstance(pull_request, int)
                and not isinstance(pull_request, bool)
                and pull_request > 0
            ),
            f"{name}: pull_request must be null or positive integer",
        )
        if name == "main":
            require(
                role == "protected-integration-authority",
                "main role must be protected-integration-authority",
            )
            require(pull_request is None, "main cannot have a pull request")
        else:
            require(
                name.startswith(("codex/", "integration/", "archive/")),
                f"{name}: active branch namespace is not approved",
            )
        result[name] = row
    require("main" in result, "main must be declared active")

    claims = value.get("claims")
    require(isinstance(claims, dict), "active branch claims must be an object")
    require(claims.get("active_line_declared") is True, "active line claim missing")
    for key in (
        "branch_cleanup_reviewed",
        "branch_cleanup_executed",
        "cleanup_complete",
        "sg0_complete",
    ):
        require(claims.get(key) is False, f"premature active branch claim: {key}")
    return result, hashlib.sha256(raw).hexdigest()


def classify(
    *,
    name: str,
    is_active: bool,
    commit_reachable_from_main: bool,
    duplicate_tip: bool,
) -> tuple[str, str]:
    if name == "main":
        return "keep", "canonical protected default branch"
    if is_active:
        return "keep-active", "declared current integration or gap-closure line"
    if name.startswith("archive/"):
        return "keep-archive", "explicit immutable archive namespace"
    if name == "integration/all-branches-main-v1":
        return (
            "preserve-pending-review",
            "historical consolidation authority retains unique audit value",
        )
    if not commit_reachable_from_main:
        return "preserve-nonancestor", "tip is not reachable from current main"
    if duplicate_tip:
        return (
            "delete-candidate-after-review",
            "tip is reachable from main and shared by multiple branch names",
        )
    return (
        "archive-or-delete-after-review",
        "tip is reachable from main but has a unique branch name",
    )


def build_inventory(
    *,
    root: Path = ROOT,
    active_path: Path = ACTIVE_BRANCHES_PATH,
) -> dict[str, object]:
    active, active_sha256 = load_active_branches(active_path)
    candidate_commit = git("rev-parse", "HEAD", root=root).stdout.strip()
    candidate_tree = git(
        "rev-parse",
        f"{candidate_commit}^{{tree}}",
        root=root,
    ).stdout.strip()
    main_commit = git(
        "rev-parse",
        "refs/remotes/origin/main",
        root=root,
    ).stdout.strip()
    main_tree = git(
        "rev-parse",
        f"{main_commit}^{{tree}}",
        root=root,
    ).stdout.strip()

    output = git(
        "for-each-ref",
        "--format=%(refname:strip=3)\t%(objectname)",
        "refs/remotes/origin",
        root=root,
    ).stdout
    raw_refs: list[tuple[str, str, str]] = []
    for line in output.splitlines():
        if not line:
            continue
        name, commit = line.split("\t", 1)
        if name == "HEAD":
            continue
        validate_branch_name(name, "remote branch")
        tree = git(
            "rev-parse",
            f"{commit}^{{tree}}",
            root=root,
        ).stdout.strip()
        raw_refs.append((name, commit, tree))
    require(raw_refs, "remote branch collection is empty")

    observed_names = {name for name, _, _ in raw_refs}
    missing_active = sorted(set(active) - observed_names)
    require(
        not missing_active,
        f"declared active branches are missing from remote inventory: {missing_active}",
    )
    require("main" in observed_names, "remote main branch is missing")

    names_by_tip: dict[str, list[str]] = defaultdict(list)
    names_by_tree: dict[str, list[str]] = defaultdict(list)
    for name, commit, tree in raw_refs:
        names_by_tip[commit].append(name)
        names_by_tree[tree].append(name)

    rows: list[dict[str, object]] = []
    disposition_counts: Counter[str] = Counter()
    for name, commit, tree in sorted(raw_refs):
        commit_reachable = reachable(
            commit,
            main_commit,
            root=root,
        )
        main_reachable = reachable(
            main_commit,
            commit,
            root=root,
        )
        disposition, reason = classify(
            name=name,
            is_active=name in active,
            commit_reachable_from_main=commit_reachable,
            duplicate_tip=len(names_by_tip[commit]) > 1,
        )
        disposition_counts[disposition] += 1
        rows.append(
            {
                "name": name,
                "commit": commit,
                "tree": tree,
                "active_role": active[name]["role"] if name in active else None,
                "pull_request": active[name]["pull_request"] if name in active else None,
                "commit_reachable_from_main": commit_reachable,
                "main_reachable_from_commit": main_reachable,
                "same_tip_branches": sorted(names_by_tip[commit]),
                "same_tree_branches": sorted(names_by_tree[tree]),
                "disposition": disposition,
                "reason": reason,
            }
        )

    return {
        "schema": "trillionnium.branch-inventory.v2",
        "project_id": "trillionnium-game",
        "repository": "TrillionniumFoundation/TrillionniumGame",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "candidate": {
            "commit": candidate_commit,
            "tree": candidate_tree,
        },
        "main": {
            "commit": main_commit,
            "tree": main_tree,
        },
        "active_branch_registry": {
            "path": active_path.relative_to(root).as_posix(),
            "sha256": active_sha256,
            "branch_count": len(active),
            "branches": sorted(active),
        },
        "branch_count": len(rows),
        "unique_tip_count": len(names_by_tip),
        "unique_tree_count": len(names_by_tree),
        "rows_sha256": hashlib.sha256(canonical_bytes(rows)).hexdigest(),
        "disposition_counts": dict(sorted(disposition_counts.items())),
        "branches": rows,
        "policy": {
            "deletion_executed": False,
            "history_rewritten": False,
            "independent_review_required": True,
            "before_after_manifests_required": True,
            "nonancestor_may_be_deleted": False,
            "active_branch_may_be_deleted": False,
        },
        "claims": {
            "inventory_generated": True,
            "inventory_retained_and_verified": False,
            "disposition_reviewed": False,
            "cleanup_complete": False,
            "sg0_complete": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="run/governance/branch-inventory.json",
    )
    arguments = parser.parse_args()
    try:
        inventory = build_inventory()
        output = ROOT / arguments.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(inventory, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "status": "generated",
                    "output": str(output.relative_to(ROOT)),
                    "candidate_commit": inventory["candidate"]["commit"],
                    "candidate_tree": inventory["candidate"]["tree"],
                    "branch_count": inventory["branch_count"],
                    "active_branch_count": inventory["active_branch_registry"][
                        "branch_count"
                    ],
                    "unique_tip_count": inventory["unique_tip_count"],
                    "cleanup_complete": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except (OSError, ValueError, InventoryError) as error:
        print(f"branch inventory generation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
