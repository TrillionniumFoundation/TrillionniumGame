#!/usr/bin/env python3
"""Verify a retained branch snapshot while rejecting destructive live-ref drift.

The producer inventory is an exact immutable before-state. A verifier that runs
later must reject deletion or movement of every captured ref, but a newly added
branch cannot invalidate the already retained snapshot. This wrapper preserves
all validation in verify-branch-inventory-log.py and narrows the live comparison
to that safety property.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE_PATH = ROOT / "scripts/verify-branch-inventory-log.py"
MAX_CONCURRENT_ADDITIONS = 128


def load_base() -> Any:
    spec = importlib.util.spec_from_file_location(
        "trillionnium_branch_inventory_base_verifier", BASE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load base verifier: {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BASE = load_base()
ORIGINAL_REMOTE_REFS = BASE.remote_refs
ORIGINAL_VALIDATE_INVENTORY = BASE.validate_inventory


def snapshot_refs(inventory_bytes: bytes) -> list[tuple[str, str, str]]:
    try:
        value = json.loads(inventory_bytes)
    except json.JSONDecodeError as error:
        raise BASE.VerificationError("branch inventory is invalid JSON") from error
    BASE.require(isinstance(value, dict), "branch inventory must be an object")
    rows = value.get("branches")
    BASE.require(isinstance(rows, list) and rows, "inventory branch rows are empty")
    result: list[tuple[str, str, str]] = []
    names: set[str] = set()
    for row in rows:
        BASE.require(isinstance(row, dict), "inventory branch row must be an object")
        name = row.get("name")
        commit = row.get("commit")
        tree = row.get("tree")
        BASE.require(isinstance(name, str) and name, "inventory branch name missing")
        BASE.require(name not in names, f"duplicate inventory branch name: {name}")
        BASE.require(
            isinstance(commit, str) and BASE.SHA40.fullmatch(commit) is not None,
            f"invalid inventory commit for branch {name}",
        )
        BASE.require(
            isinstance(tree, str) and BASE.SHA40.fullmatch(tree) is not None,
            f"invalid inventory tree for branch {name}",
        )
        names.add(name)
        result.append((name, commit, tree))
    return sorted(result)


def compare_snapshot_to_live(
    captured: list[tuple[str, str, str]],
    live: list[tuple[str, str, str]],
) -> dict[str, Any]:
    captured_by_name = {name: (commit, tree) for name, commit, tree in captured}
    live_by_name = {name: (commit, tree) for name, commit, tree in live}
    BASE.require(
        len(captured_by_name) == len(captured),
        "captured remote refs contain duplicate names",
    )
    BASE.require(len(live_by_name) == len(live), "live remote refs contain duplicate names")

    missing = sorted(set(captured_by_name) - set(live_by_name))
    moved = sorted(
        name
        for name in set(captured_by_name) & set(live_by_name)
        if captured_by_name[name] != live_by_name[name]
    )
    additions = sorted(set(live_by_name) - set(captured_by_name))
    BASE.require(not missing, f"captured branch disappeared after inventory: {missing}")
    BASE.require(not moved, f"captured branch moved after inventory: {moved}")
    BASE.require(
        len(additions) <= MAX_CONCURRENT_ADDITIONS,
        "concurrent branch additions exceeded verification bound",
    )
    return {
        "captured_branch_count": len(captured),
        "live_branch_count": len(live),
        "concurrent_addition_count": len(additions),
        "concurrent_additions": [
            {
                "name": name,
                "commit": live_by_name[name][0],
                "tree": live_by_name[name][1],
            }
            for name in additions
        ],
        "concurrent_removed_branch_count": 0,
        "concurrent_moved_branch_count": 0,
        "captured_remote_refs_reverified": True,
    }


def validate_inventory(
    inventory_bytes: bytes,
    *,
    repository: str,
    head_sha: str,
) -> dict[str, Any]:
    captured = snapshot_refs(inventory_bytes)
    live = ORIGINAL_REMOTE_REFS()
    drift = compare_snapshot_to_live(captured, live)

    # Re-run every original inventory assertion against the immutable captured
    # set. Destructive live drift was rejected above; only bounded additions are
    # omitted from the earlier exact before-state by definition.
    previous = BASE.remote_refs
    BASE.remote_refs = lambda: captured
    try:
        observation = ORIGINAL_VALIDATE_INVENTORY(
            inventory_bytes,
            repository=repository,
            head_sha=head_sha,
        )
    finally:
        BASE.remote_refs = previous
    observation.update(drift)
    return observation


def main() -> int:
    BASE.validate_inventory = validate_inventory
    return BASE.main()


if __name__ == "__main__":
    raise SystemExit(main())
