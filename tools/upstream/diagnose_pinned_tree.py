#!/usr/bin/env python3
"""Diagnose exact Git tree mismatches for GitHub archive snapshots.

The production archive verifier remains fail closed. This helper downloads the
same pinned archive, reconstructs every Git object (including virtual gitlinks),
fetches the expected recursive tree from the GitHub Git database API and emits
path-level mode/type/SHA differences. It never substitutes an observed archive
hash for the pinned repository tree.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.upstream.pinned_archive import (  # noqa: E402
    SourceArchiveError,
    extract_github_tarball,
    git_blob_sha1,
    http_bytes,
    normalize_gitlink_map,
)


class DiagnosticError(RuntimeError):
    """Raised when the mismatch itself cannot be diagnosed safely."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DiagnosticError(message)


def git_object(kind: str, payload: bytes) -> str:
    header = f"{kind} {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload).hexdigest()  # noqa: S324 - Git object ID


def tree_sort_key(name: bytes, is_tree: bool) -> bytes:
    return name + (b"/" if is_tree else b"")


def immediate_virtual_names(
    prefix: PurePosixPath,
    links: dict[PurePosixPath, bytes],
) -> set[str]:
    names: set[str] = set()
    prefix_parts = prefix.parts
    for path in links:
        parts = path.parts
        if len(parts) <= len(prefix_parts) or parts[: len(prefix_parts)] != prefix_parts:
            continue
        names.add(parts[len(prefix_parts)])
    return names


def has_descendant_link(
    path: PurePosixPath,
    links: dict[PurePosixPath, bytes],
) -> bool:
    prefix = path.parts
    return any(
        len(candidate.parts) > len(prefix)
        and candidate.parts[: len(prefix)] == prefix
        for candidate in links
    )


def local_inventory(
    root: Path,
    virtual_gitlinks: dict[str, str] | None,
) -> tuple[str, dict[str, dict[str, Any]]]:
    links = normalize_gitlink_map(virtual_gitlinks)
    inventory: dict[str, dict[str, Any]] = {}

    def walk(directory: Path | None, prefix: PurePosixPath) -> str:
        physical: dict[str, Path] = {}
        if directory is not None and directory.exists():
            require(directory.is_dir(), f"expected directory while hashing: {directory}")
            physical = {child.name: child for child in directory.iterdir()}
        names = set(physical) | immediate_virtual_names(prefix, links)
        encoded_entries: list[tuple[bytes, bool, bytes]] = []

        for name in names:
            child = physical.get(name)
            relative = prefix / name if prefix.parts else PurePosixPath(name)
            relative_text = relative.as_posix()
            link_sha = links.get(relative)

            if link_sha is not None:
                if child is not None and child.exists():
                    if child.is_dir() and not child.is_symlink():
                        require(
                            not any(child.iterdir()),
                            f"virtual gitlink path is not empty: {relative_text}",
                        )
                    else:
                        raise DiagnosticError(
                            f"virtual gitlink collides with archive file: {relative_text}"
                        )
                inventory[relative_text] = {
                    "mode": "160000",
                    "type": "commit",
                    "sha": link_sha.hex(),
                }
                encoded_entries.append(
                    (
                        os.fsencode(name),
                        False,
                        b"160000 " + os.fsencode(name) + b"\0" + link_sha,
                    )
                )
                continue

            if child is None:
                require(
                    has_descendant_link(relative, links),
                    f"synthetic tree has no gitlink descendants: {relative_text}",
                )
                object_sha = walk(None, relative)
                inventory[relative_text] = {
                    "mode": "040000",
                    "type": "tree",
                    "sha": object_sha,
                }
                encoded_entries.append(
                    (
                        os.fsencode(name),
                        True,
                        b"40000 "
                        + os.fsencode(name)
                        + b"\0"
                        + bytes.fromhex(object_sha),
                    )
                )
                continue

            mode = child.lstat().st_mode
            name_bytes = os.fsencode(name)
            if stat.S_ISDIR(mode):
                object_sha = walk(child, relative)
                inventory[relative_text] = {
                    "mode": "040000",
                    "type": "tree",
                    "sha": object_sha,
                }
                encoded_entries.append(
                    (
                        name_bytes,
                        True,
                        b"40000 " + name_bytes + b"\0" + bytes.fromhex(object_sha),
                    )
                )
            elif stat.S_ISLNK(mode):
                object_sha = git_blob_sha1(os.fsencode(os.readlink(child)))
                inventory[relative_text] = {
                    "mode": "120000",
                    "type": "blob",
                    "sha": object_sha,
                }
                encoded_entries.append(
                    (
                        name_bytes,
                        False,
                        b"120000 " + name_bytes + b"\0" + bytes.fromhex(object_sha),
                    )
                )
            elif stat.S_ISREG(mode):
                object_sha = git_blob_sha1(child.read_bytes())
                object_mode = "100755" if mode & 0o111 else "100644"
                inventory[relative_text] = {
                    "mode": object_mode,
                    "type": "blob",
                    "sha": object_sha,
                }
                encoded_entries.append(
                    (
                        name_bytes,
                        False,
                        object_mode.encode("ascii")
                        + b" "
                        + name_bytes
                        + b"\0"
                        + bytes.fromhex(object_sha),
                    )
                )
            else:
                raise DiagnosticError(
                    f"unsupported filesystem object while hashing: {relative_text}"
                )

        encoded_entries.sort(key=lambda row: tree_sort_key(row[0], row[1]))
        return git_object("tree", b"".join(row[2] for row in encoded_entries))

    return walk(root, PurePosixPath()), inventory


def expected_inventory(
    repository: str,
    tree_sha: str,
    *,
    token: str | None,
) -> dict[str, dict[str, Any]]:
    url = f"https://api.github.com/repos/{repository}/git/trees/{tree_sha}?recursive=1"
    payload = json.loads(http_bytes(url, token=token).decode("utf-8"))
    require(isinstance(payload, dict), "GitHub tree response must be an object")
    require(payload.get("sha") == tree_sha, "GitHub tree response SHA mismatch")
    require(payload.get("truncated") is False, "GitHub recursive tree response is truncated")
    rows = payload.get("tree")
    require(isinstance(rows, list), "GitHub tree response has no tree array")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        require(isinstance(row, dict), "GitHub tree entry must be an object")
        path = row.get("path")
        mode = row.get("mode")
        kind = row.get("type")
        sha = row.get("sha")
        require(
            all(isinstance(value, str) and value for value in (path, mode, kind, sha)),
            f"invalid GitHub tree entry: {row!r}",
        )
        result[path] = {"mode": mode, "type": kind, "sha": sha}
    return result


def compare(
    expected: dict[str, dict[str, Any]],
    observed: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []
    for path in sorted(set(expected) | set(observed)):
        left = expected.get(path)
        right = observed.get(path)
        if left == right:
            continue
        if left is None:
            classification = "extra"
        elif right is None:
            classification = "missing"
        else:
            changed = [
                field
                for field in ("mode", "type", "sha")
                if left[field] != right[field]
            ]
            classification = "+".join(changed)
        differences.append(
            {
                "path": path,
                "classification": classification,
                "expected": left,
                "observed": right,
            }
        )
    return differences


def load_profile(registry: Path, profile_id: str) -> dict[str, Any]:
    value = json.loads(registry.read_text(encoding="utf-8"))
    profiles = value.get("profiles") if isinstance(value, dict) else None
    require(isinstance(profiles, list), "SDK registry profiles must be an array")
    matching = [
        row
        for row in profiles
        if isinstance(row, dict) and row.get("id") == profile_id
    ]
    require(len(matching) == 1, f"expected exactly one SDK profile {profile_id!r}")
    return matching[0]


def gitlinks_from_profile(profile: dict[str, Any]) -> dict[str, str]:
    rows = profile.get("gitlinks", [])
    require(isinstance(rows, list), "profile gitlinks must be an array")
    result: dict[str, str] = {}
    for row in rows:
        require(isinstance(row, dict), "gitlink row must be an object")
        path = row.get("path")
        commit = row.get("commit")
        require(isinstance(path, str) and path, "gitlink path is invalid")
        require(isinstance(commit, str) and len(commit) == 40, "gitlink commit is invalid")
        require(path not in result, f"duplicate gitlink path: {path}")
        result[path] = commit
    return result


def diagnostic(registry: Path, profile_id: str, limit: int) -> dict[str, Any]:
    profile = load_profile(registry, profile_id)
    repository = profile.get("repository")
    revision = profile.get("commit")
    expected_tree = profile.get("tree")
    require(isinstance(repository, str) and "/" in repository, "invalid repository")
    require(isinstance(revision, str) and len(revision) == 40, "invalid revision")
    require(isinstance(expected_tree, str) and len(expected_tree) == 40, "invalid tree")
    token = os.environ.get("GITHUB_TOKEN") or None
    archive_url = f"https://api.github.com/repos/{repository}/tarball/{revision}"
    archive = http_bytes(archive_url, token=token)
    archive_sha256 = hashlib.sha256(archive).hexdigest()
    links = gitlinks_from_profile(profile)

    with tempfile.TemporaryDirectory(prefix="trnm-tree-diagnostic-") as temporary:
        destination = Path(temporary) / "source"
        extracted_root = extract_github_tarball(archive, destination)
        observed_tree, observed = local_inventory(extracted_root, links)
        expected = expected_inventory(repository, expected_tree, token=token)
        differences = compare(expected, observed)

    summary = {
        "schema": "trillionnium.pinned-tree-diagnostic.v1",
        "profile": profile_id,
        "repository": repository,
        "revision": revision,
        "expected_tree": expected_tree,
        "observed_tree": observed_tree,
        "archive_sha256": archive_sha256,
        "expected_entry_count": len(expected),
        "observed_entry_count": len(observed),
        "difference_count": len(differences),
        "differences": differences[:limit],
        "difference_output_truncated": len(differences) > limit,
        "tree_matches": observed_tree == expected_tree and not differences,
        "claim_boundary": {
            "diagnostic_only": True,
            "pinned_tree_relaxed": False,
            "compatibility_credit": False,
        },
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--require-match", action="store_true")
    args = parser.parse_args()
    try:
        require(1 <= args.limit <= 1000, "--limit must be in 1..=1000")
        result = diagnostic(args.registry.resolve(), args.profile, args.limit)
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        SourceArchiveError,
        DiagnosticError,
        ValueError,
    ) as error:
        print(f"pinned tree diagnostic failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.require_match and not result["tree_matches"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
