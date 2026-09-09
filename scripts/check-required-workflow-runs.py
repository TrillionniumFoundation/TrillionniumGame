#!/usr/bin/env python3
"""Compose an exact child-workflow overlay, then run the hardened admission gate."""
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
    """Normalize only GitHub's stale status with independently terminal evidence.

    GitHub may return a workflow run as completed/success while an exact-attempt
    job still reports status=in_progress, conclusion=success, even though every
    execution step, including the runner completion step, is completed/success.
    This function preserves fail-closed behavior: normalization is allowed only
    when the parent run is terminal-success, the job conclusion is success, and
    every non-framework, non-skipped step is completed/success.
    """

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


# The hardened core resolves this global at execution time.
_hardened.GitHubApi = GitHubApi


def canonical_overlay_digest(value: dict[str, Any]) -> str:
    payload = dict(value)
    payload.pop("overlay_sha256", None)
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parse_requirement_list(
    value: Any, name: str
) -> tuple[Requirement, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, dict) for item in value
    ):
        raise ValueError(f"{name} must be an object array")
    return tuple(Requirement.parse(item) for item in value)


def load_composed_manifest(path: Path) -> Manifest:
    base = Manifest.load(path)
    overlay_path = path.with_name(OVERLAY_FILENAME)
    if not overlay_path.exists():
        return base

    try:
        raw = json.loads(overlay_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read workflow overlay: {error}") from error
    if not isinstance(raw, dict):
        raise ValueError("workflow overlay root must be an object")
    if raw.get("schema") != OVERLAY_SCHEMA:
        raise ValueError("unsupported workflow overlay schema")
    expected_relative = path.as_posix()
    declared_relative = str(raw.get("base_manifest_path", ""))
    if declared_relative != expected_relative:
        raise ValueError(
            "workflow overlay base_manifest_path mismatch: "
            f"declared={declared_relative!r} expected={expected_relative!r}"
        )
    expected_base_sha = str(raw.get("base_manifest_blob_sha1", ""))
    observed_base_sha = blob_sha(path)
    if expected_base_sha != observed_base_sha:
        raise ValueError(
            "workflow overlay base manifest drift: "
            f"observed={observed_base_sha} expected={expected_base_sha}"
        )
    if raw.get("repository") != base.repository:
        raise ValueError("workflow overlay repository mismatch")
    if raw.get("event") != base.event:
        raise ValueError("workflow overlay event mismatch")
    digest = str(raw.get("overlay_sha256", ""))
    if digest != canonical_overlay_digest(raw):
        raise ValueError("workflow overlay SHA-256 does not match content")

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
    return Manifest(
        repository=base.repository,
        event=base.event,
        aggregate=base.aggregate,
        reject_unlisted=base.reject_unlisted,
        workflows=workflows,
    )


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
