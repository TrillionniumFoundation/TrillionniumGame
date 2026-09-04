"""Verify a catalog path-as-display-name against exact source and execution.

GitHub's repository workflow catalog may expose a file path as its display
name while a candidate repairing an invalid default-branch YAML already runs
under the declared name. That display form is not an alternate workflow ID.
Only the exact registered repository-relative path can be recognized, and
only for a successful current-head pull-request run of the exact definition.
Arbitrary renamed, disabled, missing, stale or substituted workflows still
fail. This helper performs no API request, metadata mutation or gate promotion.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

MAX_DEFINITION_BYTES = 1024 * 1024
SHA = re.compile(r"[0-9a-f]{40}\Z")
PLAIN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,127}\Z")


class RequirementIdentity(Protocol):
    workflow_id: int
    name: str
    path: str
    git_blob_sha1: str
    allowed_events: tuple[str, ...]


class RunIdentity(Protocol):
    id: int
    attempt: int
    workflow_id: int
    name: str
    path: str
    head_sha: str
    event: str
    status: str
    conclusion: str | None


def verified_catalog_path_alias(
    requirement: RequirementIdentity,
    catalog: dict[str, Any],
    *,
    source_root: Path | None,
    run: RunIdentity | None,
    expected_head: str | None,
) -> bool:
    """Return True only with all path, blob, name, head and run predicates.

    Callers retain the original catalog name in the execution receipt. The
    source root must be the gate's exact candidate checkout, not a caller-
    supplied replacement definition. The aggregate's own in-progress run is
    deliberately ineligible. Job/assertion and final run-attempt freshness
    checks remain separate mandatory gates, never inferred from this result.
    """
    if source_root is None or run is None or not isinstance(expected_head, str):
        return False
    if SHA.fullmatch(expected_head) is None:
        return False
    if not isinstance(requirement.name, str) or PLAIN_NAME.fullmatch(requirement.name) is None:
        return False
    if not isinstance(requirement.path, str) or "\\" in requirement.path:
        return False
    relative = PurePosixPath(requirement.path)
    if (relative.is_absolute() or len(relative.parts) != 3
            or relative.parts[:2] != (".github", "workflows")
            or relative.suffix not in {".yml", ".yaml"}
            or ".." in relative.parts or relative.as_posix() != requirement.path):
        return False
    if not isinstance(requirement.git_blob_sha1, str) or SHA.fullmatch(requirement.git_blob_sha1) is None:
        return False
    if (type(catalog.get("id")) is not int
            or catalog["id"] != requirement.workflow_id
            or catalog.get("name") != requirement.path
            or catalog.get("path") != requirement.path
            or catalog.get("state") != "active"):
        return False
    if (type(run.id) is not int or run.id <= 0
            or type(run.attempt) is not int or run.attempt <= 0
            or run.workflow_id != requirement.workflow_id
            or run.name != requirement.name or run.path != requirement.path
            or run.head_sha != expected_head
            or run.event != "pull_request" or run.event not in requirement.allowed_events
            or run.status != "completed" or run.conclusion != "success"):
        return False
    try:
        root = source_root.resolve(strict=True)
        target = root
        for part in relative.parts:
            target = target / part
            if target.is_symlink():
                return False
        target.resolve(strict=True).relative_to(root)
        if not target.is_file():
            return False
        with target.open("rb") as handle:
            payload = handle.read(MAX_DEFINITION_BYTES + 1)
        if len(payload) > MAX_DEFINITION_BYTES:
            return False
        observed = hashlib.sha1(f"blob {len(payload)}\0".encode() + payload).hexdigest()
        if observed != requirement.git_blob_sha1:
            return False
        text = payload.decode("utf-8")
    except (OSError, ValueError):
        return False
    expected_name = f"name: {requirement.name}"
    lines = text.splitlines()
    # Deliberately support only the canonical plain top-level name emitted by
    # this repository. Ambiguous, duplicate or complex YAML name forms fail.
    names = [line for line in lines if re.match(r"^name[ \t]*:", line)]
    return bool(lines) and lines[0] == expected_name and names == [expected_name]
