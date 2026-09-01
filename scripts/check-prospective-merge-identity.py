#!/usr/bin/env python3
"""Validate the exact GitHub prospective-merge object for a pull request.

The source-head packet and the prospective-merge packet are distinct evidence
objects. This checker runs from a detached checkout of ``refs/pull/N/merge``
and proves that HEAD is the event's exact merge commit with the expected,
ordered base-first/head-second parents. It deliberately makes no product or
compatibility claim.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


class IdentityError(RuntimeError):
    """Raised when the checked-out merge object does not match the contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise IdentityError(message)


def canonical_sha(value: str, label: str) -> str:
    require(
        isinstance(value, str) and _SHA.fullmatch(value) is not None,
        f"{label} must be 40 lowercase hexadecimal characters",
    )
    return value


def canonical_repository(value: str) -> str:
    require(
        isinstance(value, str) and _REPOSITORY.fullmatch(value) is not None,
        "repository must be owner/name",
    )
    return value


def run_git(root: Path, arguments: Sequence[str]) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise IdentityError(
            "git command failed to execute: "
            f"{' '.join(arguments)}: {type(error).__name__}"
        ) from error
    if result.returncode != 0:
        detail = " ".join((result.stderr or result.stdout).split())[:300]
        raise IdentityError(f"git {' '.join(arguments)} failed: {detail}")
    return result.stdout.strip()


def repository_from_remote(value: str) -> str:
    value = value.strip()
    prefixes = (
        "https://github.com/",
        "http://github.com/",
        "ssh://git@github.com/",
        "git://github.com/",
    )
    for prefix in prefixes:
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    else:
        ssh_prefix = "git@github.com:"
        require(
            value.startswith(ssh_prefix),
            "origin is not a canonical github.com repository URL",
        )
        value = value[len(ssh_prefix) :]
    if value.endswith(".git"):
        value = value[:-4]
    return canonical_repository(value)


def validate_identity(
    root: Path,
    *,
    repository: str,
    expected_base: str,
    expected_head: str,
    expected_merge: str,
) -> dict[str, Any]:
    root = root.resolve()
    require(root.is_dir(), f"repository root is not a directory: {root}")
    repository = canonical_repository(repository)
    expected_base = canonical_sha(expected_base, "expected base")
    expected_head = canonical_sha(expected_head, "expected head")
    expected_merge = canonical_sha(expected_merge, "expected merge")
    require(
        len({expected_base, expected_head, expected_merge}) == 3,
        "base, head and merge identities must be distinct",
    )

    require(
        run_git(root, ["rev-parse", "--is-inside-work-tree"]) == "true",
        "root is not a Git work tree",
    )
    origin = repository_from_remote(
        run_git(root, ["remote", "get-url", "origin"])
    )
    require(
        origin == repository,
        f"origin repository mismatch: expected {repository}, got {origin}",
    )

    actual_merge = canonical_sha(
        run_git(root, ["rev-parse", "HEAD"]),
        "actual merge",
    )
    require(
        actual_merge == expected_merge,
        f"HEAD mismatch: expected {expected_merge}, got {actual_merge}",
    )
    require(
        run_git(root, ["cat-file", "-t", actual_merge]) == "commit",
        "HEAD is not a commit object",
    )

    parent_text = run_git(root, ["show", "-s", "--format=%P", actual_merge])
    parents = parent_text.split()
    require(
        len(parents) == 2,
        f"prospective merge must have exactly two parents, got {len(parents)}",
    )
    parents = [
        canonical_sha(parent, f"parent {index}")
        for index, parent in enumerate(parents, 1)
    ]
    require(
        parents[0] == expected_base,
        f"first parent is not the expected base: {parents[0]}",
    )
    require(
        parents[1] == expected_head,
        f"second parent is not the expected head: {parents[1]}",
    )

    for label, commit in (("base", expected_base), ("head", expected_head)):
        require(
            run_git(root, ["cat-file", "-t", commit]) == "commit",
            f"{label} parent is not a commit object",
        )
        run_git(root, ["merge-base", "--is-ancestor", commit, actual_merge])

    merge_tree = canonical_sha(
        run_git(root, ["rev-parse", f"{actual_merge}^{{tree}}"]),
        "merge tree",
    )
    base_tree = canonical_sha(
        run_git(root, ["rev-parse", f"{expected_base}^{{tree}}"]),
        "base tree",
    )
    head_tree = canonical_sha(
        run_git(root, ["rev-parse", f"{expected_head}^{{tree}}"]),
        "head tree",
    )

    # A checkout used for evidence must not contain staged or tracked changes.
    # Untracked run/evidence output is intentionally allowed after this checker
    # executes, so only tracked status is considered.
    tracked_status = run_git(
        root,
        ["status", "--porcelain=v1", "--untracked-files=no"],
    )
    require(
        tracked_status == "",
        "prospective merge checkout has staged or tracked changes",
    )

    return {
        "schema": "trillionnium.prospective-merge-identity.v1",
        "status": "verified",
        "repository": repository,
        "merge_commit": actual_merge,
        "merge_tree": merge_tree,
        "base_commit": expected_base,
        "base_tree": base_tree,
        "head_commit": expected_head,
        "head_tree": head_tree,
        "ordered_parents": parents,
        "parent_count": 2,
        "base_first_head_second": True,
        "tracked_worktree_clean": True,
        "compatibility_credit": False,
        "production_ready": False,
    }


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument("--repository", required=True)
    parser.add_argument("--expected-base", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--expected-merge", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        result = validate_identity(
            args.repository_root,
            repository=args.repository,
            expected_base=args.expected_base,
            expected_head=args.expected_head,
            expected_merge=args.expected_merge,
        )
        write_json_atomic(args.output, result)
    except (IdentityError, OSError, ValueError) as error:
        print(
            f"prospective merge identity validation failed: {error}",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
