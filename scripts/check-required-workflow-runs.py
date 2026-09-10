#!/usr/bin/env python3
"""Compose exact workflow-definition overlays, then run hardened admission."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

HARDENED_PATH = Path(__file__).with_name(
    "check-required-workflow-runs-hardened-core.py"
)
HARDENED_MODULE_NAME = "trnm_required_workflow_runs_hardened_core"
_spec = importlib.util.spec_from_file_location(
    HARDENED_MODULE_NAME, HARDENED_PATH
)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"cannot load hardened workflow gate: {HARDENED_PATH}")
_hardened = importlib.util.module_from_spec(_spec)
sys.modules[HARDENED_MODULE_NAME] = _hardened
_spec.loader.exec_module(_hardened)

API = _hardened.API
SCHEMA = _hardened.SCHEMA
DEFAULT_MANIFEST = _hardened.DEFAULT_MANIFEST
Requirement = _hardened.Requirement
Manifest = _hardened.Manifest
Run = _hardened.Run
_HardenedGitHubApi = _hardened.GitHubApi
valid_repo = _hardened.valid_repo
valid_path = _hardened.valid_path
_core_blob_sha = _hardened.blob_sha
verify_files = _hardened.verify_files
latest_runs = _hardened.latest_runs
select_runs = _hardened.select_runs
classify = _hardened.classify
arguments = _hardened.arguments
job_failures = _hardened.job_failures
workflow_metadata_failures = _hardened.workflow_metadata_failures
current_run_failures = _hardened.current_run_failures
run_identity = _hardened.run_identity

OVERLAY_SCHEMA = "trnm_required_workflow_overlay_v1"
OVERLAY_FILENAME = "REQUIRED_WORKFLOWS_OVERLAY_V1.json"
AGGREGATE_OVERLAY_SCHEMA = "trnm_required_aggregate_overlay_v1"
AGGREGATE_OVERLAY_FILENAME = "REQUIRED_AGGREGATE_OVERLAY_V1.json"


def blob_sha(value: bytes | bytearray | Path) -> str:
    """Accept the core byte contract and bounded local Path inputs."""

    if isinstance(value, Path):
        value = value.read_bytes()
    if isinstance(value, bytearray):
        value = bytes(value)
    if not isinstance(value, bytes):
        raise TypeError("blob_sha requires bytes, bytearray, or Path")
    return _core_blob_sha(value)


def _successful_execution_steps_are_terminal(job: dict[str, Any]) -> bool:
    """Return true only for a non-empty, wholly successful execution step set."""

    steps = job.get("steps")
    if not isinstance(steps, list) or not steps:
        return False
    effective = [
        step
        for step in steps
        if isinstance(step, dict)
        and not _hardened.is_framework_step(str(step.get("name", "")))
    ]
    if not effective:
        return False

    successful = 0
    for step in effective:
        status = str(step.get("status", ""))
        conclusion = (
            None
            if step.get("conclusion") is None
            else str(step.get("conclusion"))
        )
        if conclusion == "skipped":
            continue
        if status != "completed" or conclusion != "success":
            return False
        successful += 1
    return successful > 0


def normalize_github_job_statuses(
    jobs: list[dict[str, Any]], parent: Run
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    """Normalize only GitHub's stale status with independently terminal evidence."""

    if parent.status != "completed" or parent.conclusion != "success":
        return jobs, ()

    normalized: list[dict[str, Any]] = []
    anomalies: list[str] = []
    for job in jobs:
        if not isinstance(job, dict):
            normalized.append(job)
            continue
        status = str(job.get("status", ""))
        conclusion = (
            None
            if job.get("conclusion") is None
            else str(job.get("conclusion"))
        )
        if (
            status != "completed"
            and conclusion == "success"
            and _successful_execution_steps_are_terminal(job)
        ):
            replacement = dict(job)
            replacement["status"] = "completed"
            replacement["trnm_observed_status"] = status
            replacement["trnm_status_normalized"] = True
            normalized.append(replacement)
            anomalies.append(
                f"{job.get('name', '<unnamed>')}: "
                f"observed status={status!r}, conclusion='success'"
            )
        else:
            normalized.append(job)
    return normalized, tuple(anomalies)


class GitHubApi(_HardenedGitHubApi):
    """Harden exact-attempt reads against one documented GitHub API anomaly."""

    def jobs_attempt(
        self, repo: str, run_id: int, attempt: int
    ) -> list[dict[str, Any]]:
        jobs = super().jobs_attempt(repo, run_id, attempt)
        if not hasattr(self, "headers"):
            return jobs
        parent = self.current_run(repo, run_id)
        normalized, anomalies = normalize_github_job_statuses(jobs, parent)
        for anomaly in anomalies:
            print(
                "required workflow gate: normalized stale GitHub job status "
                f"for terminal-success parent run={run_id} attempt={attempt}: "
                f"{anomaly}",
                file=sys.stderr,
            )
        return normalized


_hardened.GitHubApi = GitHubApi


def _canonical_digest(value: dict[str, Any]) -> str:
    payload = dict(value)
    payload.pop("overlay_sha256", None)
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_overlay_digest(value: dict[str, Any]) -> str:
    return _canonical_digest(value)


def canonical_aggregate_overlay_digest(value: dict[str, Any]) -> str:
    return _canonical_digest(value)


def _parse_requirement_list(
    value: Any, name: str
) -> tuple[Requirement, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, dict) for item in value
    ):
        raise ValueError(f"{name} must be an object array")
    return tuple(Requirement.parse(item) for item in value)


def _read_overlay(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read {label}: {error}") from error
    if not isinstance(raw, dict):
        raise ValueError(f"{label} root must be an object")
    return raw


def _validate_common_overlay(
    *,
    raw: dict[str, Any],
    path: Path,
    base: Manifest,
    schema: str,
    label: str,
) -> None:
    if raw.get("schema") != schema:
        raise ValueError(f"unsupported {label} schema")
    expected_relative = path.as_posix()
    declared_relative = str(raw.get("base_manifest_path", ""))
    if declared_relative != expected_relative:
        raise ValueError(
            f"{label} base_manifest_path mismatch: "
            f"declared={declared_relative!r} expected={expected_relative!r}"
        )
    expected_base_sha = str(raw.get("base_manifest_blob_sha1", ""))
    observed_base_sha = blob_sha(path)
    if expected_base_sha != observed_base_sha:
        raise ValueError(
            f"{label} base manifest drift: "
            f"observed={observed_base_sha} expected={expected_base_sha}"
        )
    if raw.get("repository") != base.repository:
        raise ValueError(f"{label} repository mismatch")
    if raw.get("event") != base.event:
        raise ValueError(f"{label} event mismatch")
    digest = str(raw.get("overlay_sha256", ""))
    if digest != _canonical_digest(raw):
        raise ValueError(f"{label} SHA-256 does not match content")


def _load_aggregate_overlay(path: Path, base: Manifest) -> Requirement:
    overlay_path = path.with_name(AGGREGATE_OVERLAY_FILENAME)
    if not overlay_path.exists():
        return base.aggregate

    raw = _read_overlay(overlay_path, "aggregate workflow overlay")
    _validate_common_overlay(
        raw=raw,
        path=path,
        base=base,
        schema=AGGREGATE_OVERLAY_SCHEMA,
        label="aggregate workflow overlay",
    )
    expected_previous = str(raw.get("base_aggregate_blob_sha1", ""))
    if expected_previous != base.aggregate.git_blob_sha1:
        raise ValueError(
            "aggregate workflow overlay base aggregate drift: "
            f"observed={base.aggregate.git_blob_sha1} "
            f"expected={expected_previous}"
        )
    value = raw.get("aggregate_workflow")
    if not isinstance(value, dict):
        raise ValueError("aggregate workflow overlay replacement must be an object")
    replacement = Requirement.parse(value)
    if (
        replacement.workflow_id != base.aggregate.workflow_id
        or replacement.path != base.aggregate.path
        or replacement.name != base.aggregate.name
    ):
        raise ValueError(
            "aggregate workflow overlay may update definition policy "
            "but not workflow identity"
        )
    return replacement


def _compose_external_workflows(path: Path, base: Manifest) -> tuple[Requirement, ...]:
    overlay_path = path.with_name(OVERLAY_FILENAME)
    if not overlay_path.exists():
        return base.workflows

    raw = _read_overlay(overlay_path, "workflow overlay")
    _validate_common_overlay(
        raw=raw,
        path=path,
        base=base,
        schema=OVERLAY_SCHEMA,
        label="workflow overlay",
    )

    replacements = _parse_requirement_list(
        raw.get("replace_workflows", []), "replace_workflows"
    )
    additions = _parse_requirement_list(
        raw.get("add_workflows", []), "add_workflows"
    )
    removals_raw = raw.get("remove_workflow_ids", [])
    if not isinstance(removals_raw, list) or not all(
        isinstance(item, int) and item > 0 for item in removals_raw
    ):
        raise ValueError("remove_workflow_ids must be a positive integer array")
    if len(set(removals_raw)) != len(removals_raw):
        raise ValueError("remove_workflow_ids contains duplicates")

    by_id = {item.workflow_id: item for item in base.workflows}
    for workflow_id in removals_raw:
        if workflow_id not in by_id:
            raise ValueError(
                f"workflow overlay removal is absent: {workflow_id}"
            )
        del by_id[workflow_id]

    for replacement in replacements:
        previous = by_id.get(replacement.workflow_id)
        if previous is None:
            raise ValueError(
                "workflow overlay replacement is absent: "
                f"{replacement.workflow_id}"
            )
        if replacement.path != previous.path or replacement.name != previous.name:
            raise ValueError(
                "workflow overlay replacement may update definition policy "
                "but not workflow identity"
            )
        by_id[replacement.workflow_id] = replacement

    for addition in additions:
        if addition.workflow_id in by_id:
            raise ValueError(
                f"workflow overlay addition ID already exists: "
                f"{addition.workflow_id}"
            )
        if any(
            item.path == addition.path or item.name == addition.name
            for item in by_id.values()
        ):
            raise ValueError(
                "workflow overlay addition path/name already exists: "
                f"{addition.path}"
            )
        by_id[addition.workflow_id] = addition

    workflows = tuple(
        sorted(by_id.values(), key=lambda item: (item.path, item.workflow_id))
    )
    declared_count = int(raw.get("composed_external_workflow_count", 0))
    if declared_count != len(workflows):
        raise ValueError(
            "workflow overlay composed count mismatch: "
            f"declared={declared_count} actual={len(workflows)}"
        )
    return workflows


def load_composed_manifest(path: Path) -> Manifest:
    base = Manifest.load(path)
    result = Manifest(
        repository=base.repository,
        event=base.event,
        aggregate=_load_aggregate_overlay(path, base),
        reject_unlisted=base.reject_unlisted,
        workflows=_compose_external_workflows(path, base),
    )
    result.validate()
    return result


class _OverlayManifestLoader:
    @classmethod
    def load(cls, path: Path) -> Manifest:
        return load_composed_manifest(path)


def main(argv: list[str] | None = None) -> int:
    original = _hardened.Manifest
    _hardened.Manifest = _OverlayManifestLoader
    try:
        return _hardened.main(argv)
    finally:
        _hardened.Manifest = original


if __name__ == "__main__":
    raise SystemExit(main())
