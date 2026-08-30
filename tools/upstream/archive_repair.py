# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import base64
import binascii
import json
import os
import stat
import tempfile
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any

from tools.upstream.pinned_archive import (
    LOCK_FILE,
    SourceArchiveError,
    canonical_bytes,
    git_blob_sha1_bytes,
    git_tree_sha1,
    verify_source_lock,
)

DEFAULT_MAX_BLOB_BYTES = 64 * 1024 * 1024


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SourceArchiveError(message)


def require_sha(value: Any, label: str) -> str:
    require(
        isinstance(value, str)
        and len(value) == 40
        and value != "0" * 40
        and all(character in "0123456789abcdef" for character in value),
        f"{label} must be a non-zero lowercase 40-character SHA",
    )
    return value


def canonical_path(value: Any) -> str:
    require(isinstance(value, str) and value, "archive repair path must be a string")
    require("\\" not in value and "\x00" not in value, f"unsafe archive repair path: {value!r}")
    path = PurePosixPath(value)
    require(
        not path.is_absolute()
        and path.parts
        and all(part not in {"", ".", "..", ".git", LOCK_FILE} for part in path.parts)
        and path.as_posix() == value,
        f"archive repair path must be canonical: {value!r}",
    )
    return value


def load_profile(registry: Path, profile_id: str) -> dict[str, Any]:
    try:
        value = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SourceArchiveError(f"invalid SDK source registry: {error}") from error
    profiles = value.get("profiles") if isinstance(value, dict) else None
    require(isinstance(profiles, list), "SDK source registry profiles must be an array")
    matching = [
        profile
        for profile in profiles
        if isinstance(profile, dict) and profile.get("id") == profile_id
    ]
    require(len(matching) == 1, f"expected exactly one SDK profile {profile_id!r}")
    return matching[0]


def normalized_repairs(profile: dict[str, Any]) -> list[dict[str, str]]:
    rows = profile.get("archive_repairs", [])
    require(isinstance(rows, list) and rows, "profile archive_repairs must be a non-empty array")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        require(isinstance(row, dict), "archive repair row must be an object")
        path = canonical_path(row.get("path"))
        require(path not in seen, f"duplicate archive repair path: {path}")
        seen.add(path)
        archive_blob = require_sha(row.get("archive_blob"), f"archive blob for {path}")
        canonical_blob = require_sha(row.get("canonical_blob"), f"canonical blob for {path}")
        require(archive_blob != canonical_blob, f"archive repair is unnecessary for {path}")
        reason = row.get("reason")
        require(isinstance(reason, str) and reason.strip(), f"archive repair reason missing for {path}")
        result.append(
            {
                "path": path,
                "archive_blob": archive_blob,
                "canonical_blob": canonical_blob,
                "reason": reason.strip(),
            }
        )
    result.sort(key=lambda row: row["path"])
    return result


def fetch_exact_blob(
    repository: str,
    blob_sha: str,
    *,
    token: str | None,
    timeout_seconds: int = 120,
    max_blob_bytes: int = DEFAULT_MAX_BLOB_BYTES,
) -> bytes:
    require(repository.count("/") == 1, "repository must be owner/name")
    blob_sha = require_sha(blob_sha, "blob SHA")
    require(timeout_seconds > 0, "blob timeout must be positive")
    require(max_blob_bytes > 0, "blob size limit must be positive")
    url = f"https://api.github.com/repos/{repository}/git/blobs/{blob_sha}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "TrillionniumGame-pinned-archive-repair/1",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        response = urllib.request.urlopen(request, timeout=timeout_seconds)
    except (urllib.error.URLError, TimeoutError) as error:
        raise SourceArchiveError(f"could not fetch canonical Git blob: {error}") from error

    max_json_bytes = ((max_blob_bytes + 2) // 3) * 4 + 1024 * 1024
    with response:
        resolved_url = response.geturl()
        require(
            resolved_url.startswith("https://"),
            f"Git blob redirect resolved to non-HTTPS URL: {resolved_url}",
        )
        encoded_response = response.read(max_json_bytes + 1)
    require(len(encoded_response) <= max_json_bytes, "Git blob response exceeds bounded JSON size")
    try:
        value = json.loads(encoded_response)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SourceArchiveError(f"Git blob response is invalid JSON: {error}") from error
    require(isinstance(value, dict), "Git blob response must be an object")
    require(value.get("sha") == blob_sha, "Git blob response SHA does not match the pin")
    require(value.get("encoding") == "base64", "Git blob response must use base64 encoding")
    content = value.get("content")
    require(isinstance(content, str), "Git blob response content must be a string")
    compact = "".join(content.split())
    try:
        payload = base64.b64decode(compact, validate=True)
    except (ValueError, binascii.Error) as error:
        raise SourceArchiveError(f"Git blob response has invalid base64: {error}") from error
    require(len(payload) <= max_blob_bytes, "canonical Git blob exceeds size limit")
    size = value.get("size")
    require(isinstance(size, int) and size == len(payload), "Git blob response size is inconsistent")
    require(git_blob_sha1_bytes(payload) == blob_sha, "canonical Git blob payload SHA mismatch")
    return payload


def replace_regular_blob(target: Path, expected_archive_blob: str, payload: bytes) -> None:
    require(target.exists(), f"archive repair target is missing: {target}")
    metadata = target.lstat()
    require(
        stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode),
        f"archive repair target is not a regular file: {target}",
    )
    observed = git_blob_sha1_bytes(target.read_bytes())
    require(
        observed == expected_archive_blob,
        f"archive repair source blob drift for {target}: expected {expected_archive_blob}, got {observed}",
    )
    temporary_fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.repair-", dir=target.parent)
    try:
        with os.fdopen(temporary_fd, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        temporary = Path(temporary_name)
        temporary.chmod(0o755 if metadata.st_mode & 0o111 else 0o644)
        os.replace(temporary, target)
    finally:
        try:
            Path(temporary_name).unlink()
        except FileNotFoundError:
            pass


def repair_profile(
    *,
    registry: Path,
    profile_id: str,
    root: Path,
    token: str | None,
    timeout_seconds: int = 120,
    max_blob_bytes: int = DEFAULT_MAX_BLOB_BYTES,
) -> dict[str, Any]:
    profile = load_profile(registry, profile_id)
    repository = profile.get("repository")
    revision = require_sha(profile.get("commit"), "profile commit")
    canonical_tree = require_sha(profile.get("tree"), "profile canonical tree")
    archive_tree = require_sha(profile.get("archive_tree"), "profile archive tree")
    require(isinstance(repository, str) and repository.count("/") == 1, "invalid profile repository")
    require(archive_tree != canonical_tree, "archive repair requires distinct archive and canonical trees")
    repairs = normalized_repairs(profile)

    root = root.resolve(strict=True)
    marker = verify_source_lock(
        root,
        repository=repository,
        revision=revision,
        tree=archive_tree,
    )
    gitlinks = marker.get("gitlinks", [])

    for repair in repairs:
        payload = fetch_exact_blob(
            repository,
            repair["canonical_blob"],
            token=token,
            timeout_seconds=timeout_seconds,
            max_blob_bytes=max_blob_bytes,
        )
        target = root.joinpath(*PurePosixPath(repair["path"]).parts)
        try:
            target.resolve(strict=False).relative_to(root)
        except ValueError as error:
            raise SourceArchiveError(f"archive repair target escapes root: {repair['path']}") from error
        replace_regular_blob(target, repair["archive_blob"], payload)

    observed_tree = git_tree_sha1(root, gitlinks=gitlinks)
    require(
        observed_tree == canonical_tree,
        f"repaired source tree mismatch: expected {canonical_tree}, got {observed_tree}",
    )

    marker["tree"] = canonical_tree
    marker["archive_tree"] = archive_tree
    marker["archive_repairs"] = repairs
    marker["transport_verification"] = "archive-tree-verified-before-pinned-canonical-blob-repair"
    (root / LOCK_FILE).write_bytes(canonical_bytes(marker))
    verify_source_lock(root, repository=repository, revision=revision, tree=canonical_tree)

    return {
        "schema": "trillionnium.pinned-archive-repair.v1",
        "profile": profile_id,
        "repository": repository,
        "revision": revision,
        "archive_tree": archive_tree,
        "canonical_tree": canonical_tree,
        "repairs": repairs,
        "status": "passed",
        "compatibility_credit": False,
    }
