#!/usr/bin/env python3
"""Generate a non-destructive exact branch/ref disposition inventory."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTIVE_PLAN_BRANCH = "feat/plan-v3-gap-closure-2026-08-29"


class InventoryError(RuntimeError):
    pass


def git(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
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


def reachable(ancestor: str, descendant: str) -> bool:
    return git("merge-base", "--is-ancestor", ancestor, descendant, check=False).returncode == 0


def classify(name: str, commit: str, tree: str, main_commit: str, duplicate_tip: bool) -> tuple[str, str]:
    if name == "main":
        return "keep", "canonical default branch"
    if name == ACTIVE_PLAN_BRANCH:
        return "keep-active", "active plan-v3 pull-request line"
    if name.startswith("archive/"):
        return "keep-archive", "explicit immutable archive namespace"
    if name == "integration/all-branches-main-v1":
        return "preserve-pending-review", "closed PR #41 retains unique source-freeze files"
    if not reachable(commit, main_commit):
        return "preserve-nonancestor", "tip is not reachable from current main"
    if duplicate_tip:
        return "delete-candidate-after-review", "tip is reachable and shared by multiple branch names"
    return "archive-or-delete-after-review", "tip is reachable from main but has a unique branch name"


def build_inventory() -> dict[str, object]:
    main_commit = git("rev-parse", "refs/remotes/origin/main").stdout.strip()
    main_tree = git("rev-parse", f"{main_commit}^{{tree}}").stdout.strip()
    rows = []
    output = git(
        "for-each-ref",
        "--format=%(refname:strip=3)\t%(objectname)",
        "refs/remotes/origin",
    ).stdout
    raw_refs = []
    for line in output.splitlines():
        if not line or line.startswith("HEAD\t"):
            continue
        name, commit = line.split("\t", 1)
        if name == "HEAD":
            continue
        tree = git("rev-parse", f"{commit}^{{tree}}").stdout.strip()
        raw_refs.append((name, commit, tree))

    names_by_tip: dict[str, list[str]] = defaultdict(list)
    names_by_tree: dict[str, list[str]] = defaultdict(list)
    for name, commit, tree in raw_refs:
        names_by_tip[commit].append(name)
        names_by_tree[tree].append(name)

    for name, commit, tree in sorted(raw_refs):
        disposition, reason = classify(
            name,
            commit,
            tree,
            main_commit,
            len(names_by_tip[commit]) > 1,
        )
        rows.append(
            {
                "name": name,
                "commit": commit,
                "tree": tree,
                "commit_reachable_from_main": reachable(commit, main_commit),
                "main_reachable_from_commit": reachable(main_commit, commit),
                "same_tip_branches": sorted(names_by_tip[commit]),
                "same_tree_branches": sorted(names_by_tree[tree]),
                "disposition": disposition,
                "reason": reason,
            }
        )

    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "schema": "trillionnium.branch-inventory.v1",
        "project_id": "trillionnium-game",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "main": {"commit": main_commit, "tree": main_tree},
        "branch_count": len(rows),
        "unique_tip_count": len(names_by_tip),
        "unique_tree_count": len(names_by_tree),
        "rows_sha256": hashlib.sha256(canonical).hexdigest(),
        "branches": rows,
        "policy": {
            "deletion_executed": False,
            "history_rewritten": False,
            "independent_review_required": True,
            "before_after_manifests_required": True,
            "nonancestor_may_be_deleted": False,
        },
        "claims": {
            "inventory_generated": True,
            "disposition_reviewed": False,
            "cleanup_complete": False,
            "sg0_complete": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="run/governance/branch-inventory.json")
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
                    "branch_count": inventory["branch_count"],
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
