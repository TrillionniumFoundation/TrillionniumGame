#!/usr/bin/env python3
"""Harden manifest-bound workflow admission against mutable metadata and rerun races."""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any

CORE_PATH = Path(__file__).with_name("check-required-workflow-runs-core.py")
CORE_MODULE_NAME = "trnm_required_workflow_runs_core"
_spec = importlib.util.spec_from_file_location(CORE_MODULE_NAME, CORE_PATH)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"cannot load workflow gate core: {CORE_PATH}")
_core = importlib.util.module_from_spec(_spec)
sys.modules[CORE_MODULE_NAME] = _core
_spec.loader.exec_module(_core)

CATALOG_PATH = Path(__file__).with_name("workflow_catalog_identity.py")
_catalog_spec = importlib.util.spec_from_file_location("trnm_workflow_catalog_identity", CATALOG_PATH)
if _catalog_spec is None or _catalog_spec.loader is None:
    raise RuntimeError(f"cannot load workflow catalog identity contract: {CATALOG_PATH}")
_catalog = importlib.util.module_from_spec(_catalog_spec)
sys.modules[_catalog_spec.name] = _catalog
_catalog_spec.loader.exec_module(_catalog)

API = _core.API
SCHEMA = _core.SCHEMA
DEFAULT_MANIFEST = _core.DEFAULT_MANIFEST
Requirement = _core.Requirement
Manifest = _core.Manifest
Run = _core.Run
valid_repo = _core.valid_repo
valid_path = _core.valid_path
blob_sha = _core.blob_sha
verify_files = _core.verify_files
latest_runs = _core.latest_runs
select_runs = _core.select_runs
classify = _core.classify
arguments = _core.arguments

FRAMEWORK_STEPS = {
    "Set up job",
    "Complete job",
    "Prepare all required actions",
    "Initialize containers",
    "Start containers",
    "Stop containers",
}
BLOCKING_STEP_CONCLUSIONS = {
    "failure",
    "cancelled",
    "timed_out",
    "action_required",
    "startup_failure",
    "stale",
}


def is_framework_step(name: str) -> bool:
    return name in FRAMEWORK_STEPS or name.startswith("Post ")


def job_failures(
    jobs: list[dict[str, Any]], minimum: int = 1
) -> list[str]:
    """Require successful jobs with real steps and reject masked step failures."""

    if not jobs:
        return ["workflow has zero jobs"]
    failures: list[str] = []
    valid = 0
    for job in jobs:
        if not isinstance(job, dict):
            failures.append("workflow job entry is not an object")
            continue
        name = str(job.get("name", ""))
        status = str(job.get("status", ""))
        conclusion = (
            None if job.get("conclusion") is None else str(job.get("conclusion"))
        )
        if conclusion == "skipped":
            continue
        if status != "completed" or conclusion != "success":
            failures.append(
                f"job {name!r} is not terminal-success: "
                f"status={status!r} conclusion={conclusion!r}"
            )
            continue

        steps = job.get("steps")
        if not isinstance(steps, list) or not steps:
            failures.append(f"job {name!r} has zero execution steps")
            continue

        effective = [
            step
            for step in steps
            if isinstance(step, dict)
            and not is_framework_step(str(step.get("name", "")))
        ]
        if not effective:
            failures.append(
                f"job {name!r} has zero non-framework execution steps"
            )
            continue

        job_failure_count = len(failures)
        successful = 0
        for step in effective:
            step_name = str(step.get("name", ""))
            step_status = str(step.get("status", ""))
            step_conclusion = (
                None
                if step.get("conclusion") is None
                else str(step.get("conclusion"))
            )
            if step_conclusion == "skipped":
                continue
            if (
                step_status != "completed"
                or step_conclusion in BLOCKING_STEP_CONCLUSIONS
                or step_conclusion != "success"
            ):
                failures.append(
                    f"job {name!r} execution step {step_name!r} is not "
                    f"terminal-success: status={step_status!r} "
                    f"conclusion={step_conclusion!r}"
                )
                continue
            successful += 1
        if successful == 0:
            failures.append(
                f"job {name!r} has no successful non-framework execution step"
            )
        if len(failures) == job_failure_count and successful > 0:
            valid += 1

    if valid < minimum:
        failures.append(
            "workflow has too few successful execution jobs: "
            f"observed={valid} required>={minimum}"
        )
    return failures


def workflow_metadata_failures(
    requirement: Requirement, value: dict[str, Any],
    *, source_root: Path | None = None, run: Run | None = None,
    expected_head: str | None = None,
) -> list[str]:
    failures: list[str] = []
    if int(value.get("id", 0)) != requirement.workflow_id:
        failures.append(
            f"workflow metadata ID mismatch for {requirement.path}"
        )
    if (str(value.get("name", "")) != requirement.name
            and not _catalog.verified_catalog_path_alias(
                requirement, value, source_root=source_root,
                run=run, expected_head=expected_head,
            )):
        failures.append(
            f"workflow metadata name mismatch for id={requirement.workflow_id}: "
            f"observed={value.get('name')!r} expected={requirement.name!r}"
        )
    if str(value.get("path", "")) != requirement.path:
        failures.append(
            f"workflow metadata path mismatch for id={requirement.workflow_id}: "
            f"observed={value.get('path')!r} expected={requirement.path!r}"
        )
    if str(value.get("state", "")) != "active":
        failures.append(
            f"workflow {requirement.path!r} is not active: "
            f"{value.get('state')!r}"
        )
    return failures


def current_run_failures(
    current: Run, manifest: Manifest, head_sha: str
) -> list[str]:
    failures: list[str] = []
    aggregate = manifest.aggregate
    if current.workflow_id != aggregate.workflow_id:
        failures.append("current workflow ID does not match manifest aggregate")
    if current.name != aggregate.name:
        failures.append("current workflow name does not match manifest aggregate")
    if current.path != aggregate.path:
        failures.append("current workflow path does not match manifest aggregate")
    if current.head_sha != head_sha:
        failures.append("current workflow head SHA does not match requested head")
    if current.event != manifest.event:
        failures.append("current workflow event does not match manifest event")
    return failures


def run_identity(runs: list[Run]) -> tuple[tuple[int, int, int], ...]:
    return tuple(
        (run.workflow_id, run.id, run.attempt)
        for run in sorted(runs, key=lambda item: item.workflow_id)
    )


class GitHubApi(_core.GitHubApi):
    def current_run(self, repo: str, run_id: int) -> Run:
        return Run.parse(self.get(f"{API}/repos/{repo}/actions/runs/{run_id}"))

    def workflow(self, repo: str, workflow_id: int) -> dict[str, Any]:
        return self.get(
            f"{API}/repos/{repo}/actions/workflows/{workflow_id}"
        )

    def jobs_attempt(
        self, repo: str, run_id: int, attempt: int
    ) -> list[dict[str, Any]]:
        if run_id <= 0 or attempt <= 0:
            raise RuntimeError("run id and attempt must be positive")
        return self.paged(
            f"{API}/repos/{repo}/actions/runs/{run_id}/"
            f"attempts/{attempt}/jobs",
            "jobs",
            {},
        )


def print_failures(title: str, failures: list[str]) -> None:
    print(title, file=sys.stderr)
    for failure in failures:
        print(f"- {failure}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    options = arguments(argv)
    try:
        manifest = Manifest.load(options.manifest)
        if manifest.repository != options.repository:
            raise ValueError("repository does not match manifest")
        local_failures = verify_files(Path.cwd(), manifest)
        if local_failures:
            raise ValueError("; ".join(local_failures))
        api = GitHubApi(__import__("os").environ.get("GITHUB_TOKEN", ""))
        current = api.current_run(
            options.repository, options.current_run_id
        )
        tuple_failures = current_run_failures(
            current, manifest, options.head_sha
        )
        tuple_failures.extend(
            workflow_metadata_failures(
                manifest.aggregate,
                api.workflow(
                    options.repository, manifest.aggregate.workflow_id
                ),
            )
        )
        if tuple_failures:
            raise ValueError("; ".join(tuple_failures))
    except (KeyError, TypeError, ValueError, RuntimeError) as error:
        print(f"required workflow gate failed: {error}", file=sys.stderr)
        return 1

    deadline = time.monotonic() + options.timeout_seconds
    previous: tuple[tuple[int, int, int], ...] | None = None
    stable = 0
    last_pending: list[str] = []

    while True:
        try:
            raw = api.runs(
                options.repository, options.head_sha, manifest.event
            )
            runs, selection_failures = select_runs(
                raw, manifest, options.head_sha
            )
        except (KeyError, TypeError, ValueError, RuntimeError) as error:
            print(f"required workflow gate failed: {error}", file=sys.stderr)
            return 1

        invariant = [
            item
            for item in selection_failures
            if not item.startswith("required workflow is missing:")
        ]
        missing = [
            item
            for item in selection_failures
            if item.startswith("required workflow is missing:")
        ]
        terminal = [
            f"{run.path}: conclusion={run.conclusion!r} url={run.url}"
            for run in runs
            if run.status == "completed" and run.conclusion != "success"
        ]
        if invariant or terminal:
            print_failures(
                "required workflow gate failed:", invariant + terminal
            )
            return 1

        pending = [
            run for run in runs if run.status != "completed"
        ]
        last_pending = missing + [
            f"pending {run.name}: run={run.id} "
            f"attempt={run.attempt} status={run.status}"
            for run in pending
        ]
        complete = (
            len(runs) == len(manifest.workflows)
            and not missing
            and not pending
        )
        if complete:
            by_id = {run.workflow_id: run for run in runs}
            evidence_failures: list[str] = []
            receipt: list[dict[str, Any]] = []
            try:
                for requirement in manifest.workflows:
                    run = by_id[requirement.workflow_id]
                    metadata = api.workflow(
                        options.repository, requirement.workflow_id
                    )
                    errors = workflow_metadata_failures(
                        requirement, metadata, source_root=Path.cwd(),
                        run=run, expected_head=options.head_sha,
                    )
                    jobs = api.jobs_attempt(
                        options.repository, run.id, run.attempt
                    )
                    errors.extend(
                        job_failures(
                            jobs,
                            requirement.minimum_successful_execution_jobs,
                        )
                    )
                    evidence_failures.extend(
                        f"{requirement.path} run={run.id} "
                        f"attempt={run.attempt}: {error}"
                        for error in errors
                    )
                    receipt.append(
                        {
                            "workflow_id": requirement.workflow_id,
                            "path": requirement.path,
                            "run_id": run.id,
                            "run_attempt": run.attempt,
                            "jobs": len(jobs),
                            "catalog_name_observed": metadata.get("name"),
                            "run_name": run.name,
                            "definition_blob_sha1": requirement.git_blob_sha1,
                            "catalog_path_alias_verified": metadata.get("name") == requirement.path,
                        }
                    )
            except (KeyError, TypeError, ValueError, RuntimeError) as error:
                evidence_failures.append(str(error))

            if evidence_failures:
                print_failures(
                    "required workflow execution contract failed:",
                    evidence_failures,
                )
                return 1

            try:
                final_raw = api.runs(
                    options.repository,
                    options.head_sha,
                    manifest.event,
                )
                final_runs, final_failures = select_runs(
                    final_raw, manifest, options.head_sha
                )
            except (KeyError, TypeError, ValueError, RuntimeError) as error:
                print(f"required workflow gate failed: {error}", file=sys.stderr)
                return 1

            final_invariant = [
                item
                for item in final_failures
                if not item.startswith("required workflow is missing:")
            ]
            final_missing = [
                item
                for item in final_failures
                if item.startswith("required workflow is missing:")
            ]
            final_pending = [
                run for run in final_runs if run.status != "completed"
            ]
            final_terminal = [
                f"{run.path}: conclusion={run.conclusion!r} url={run.url}"
                for run in final_runs
                if run.status == "completed"
                and run.conclusion != "success"
            ]
            if final_invariant or final_terminal:
                print_failures(
                    "required workflow gate changed during verification:",
                    final_invariant + final_terminal,
                )
                return 1

            identity = run_identity(runs)
            final_identity = run_identity(final_runs)
            if (
                final_missing
                or final_pending
                or len(final_runs) != len(manifest.workflows)
                or final_identity != identity
            ):
                stable = 0
                previous = None
                last_pending = final_missing + [
                    f"pending {run.name}: run={run.id} "
                    f"attempt={run.attempt} status={run.status}"
                    for run in final_pending
                ]
                if final_identity != identity:
                    last_pending.append(
                        "canonical run/attempt identity changed "
                        "during exact-attempt job verification"
                    )
            else:
                stable = stable + 1 if identity == previous else 1
                previous = identity
                if stable >= options.stable_polls:
                    print(
                        "required workflow gate: OK "
                        f"({len(runs)}/{len(manifest.workflows)} "
                        "manifest-bound exact-head workflows active, "
                        "terminal-success, exact-attempt verified, and "
                        "non-empty without masked step failures)"
                    )
                    print(
                        json.dumps(
                            receipt,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                    )
                    return 0
        else:
            stable = 0
            previous = None

        if time.monotonic() >= deadline:
            print(
                "required workflow gate timed out: "
                f"observed={len(runs)}/{len(manifest.workflows)}",
                file=sys.stderr,
            )
            for failure in last_pending:
                print(f"- {failure}", file=sys.stderr)
            return 1
        time.sleep(options.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
