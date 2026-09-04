#!/usr/bin/env python3
"""Fail closed unless a manifest-bound exact-head PR workflow set succeeds."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

API = "https://api.github.com"
SCHEMA = "trnm_required_workflow_manifest_v1"
DEFAULT_MANIFEST = "docs/governance/REQUIRED_WORKFLOWS_V1.json"
FRAMEWORK_STEPS = {"Set up job", "Complete job"}


def valid_repo(value: str) -> bool:
    parts = value.split("/")
    return len(parts) == 2 and all(parts) and all(
        all(c.isalnum() or c in "._-" for c in part) for part in parts
    )


def valid_path(value: str) -> None:
    path = PurePosixPath(value)
    if (path.is_absolute() or ".." in path.parts or len(path.parts) != 3
            or path.parts[:2] != (".github", "workflows")
            or path.suffix not in {".yml", ".yaml"}):
        raise ValueError(f"invalid workflow path: {value!r}")


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


@dataclass(frozen=True)
class Requirement:
    workflow_id: int
    name: str
    path: str
    git_blob_sha1: str
    allowed_events: tuple[str, ...]
    minimum_successful_execution_jobs: int = 1

    @classmethod
    def parse(cls, value: dict[str, Any]) -> "Requirement":
        events = value.get("allowed_events")
        if not isinstance(events, list) or not events:
            raise ValueError("allowed_events must be a non-empty list")
        item = cls(
            workflow_id=int(value["workflow_id"]),
            name=str(value["name"]),
            path=str(value["path"]),
            git_blob_sha1=str(value["git_blob_sha1"]),
            allowed_events=tuple(str(x) for x in events),
            minimum_successful_execution_jobs=int(
                value.get("minimum_successful_execution_jobs", 1)
            ),
        )
        item.validate()
        return item

    def validate(self) -> None:
        if self.workflow_id <= 0 or not self.name:
            raise ValueError(f"invalid workflow identity: {self!r}")
        valid_path(self.path)
        if not re.fullmatch(r"[0-9a-f]{40}", self.git_blob_sha1):
            raise ValueError(f"invalid workflow blob SHA-1: {self.path}")
        if self.minimum_successful_execution_jobs <= 0:
            raise ValueError(f"invalid minimum execution jobs: {self.path}")


@dataclass(frozen=True)
class Manifest:
    repository: str
    event: str
    aggregate: Requirement
    reject_unlisted: bool
    workflows: tuple[Requirement, ...]

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"cannot load manifest {path}: {error}") from error
        if not isinstance(value, dict) or value.get("schema") != SCHEMA:
            raise ValueError("unsupported required-workflow manifest schema")
        aggregate_value = value.get("aggregate_workflow")
        workflows_value = value.get("workflows")
        requirements = value.get("requirements")
        if not isinstance(aggregate_value, dict):
            raise ValueError("aggregate_workflow must be an object")
        if not isinstance(workflows_value, list) or not workflows_value:
            raise ValueError("workflows must be a non-empty list")
        if not isinstance(requirements, dict):
            raise ValueError("requirements must be an object")
        aggregate = Requirement.parse({
            "workflow_id": aggregate_value["workflow_id"],
            "name": aggregate_value.get("name", "trillionnium-game-merge-gate"),
            "path": aggregate_value["path"],
            "git_blob_sha1": aggregate_value["git_blob_sha1"],
            "allowed_events": aggregate_value.get("allowed_events", []),
            "minimum_successful_execution_jobs": 1,
        })
        result = cls(
            repository=str(value.get("repository", "")),
            event=str(value.get("event", "")),
            aggregate=aggregate,
            reject_unlisted=bool(
                requirements.get("reject_unlisted_exact_head_workflows", False)
            ),
            workflows=tuple(Requirement.parse(x) for x in workflows_value),
        )
        result.validate()
        return result

    def validate(self) -> None:
        if not valid_repo(self.repository):
            raise ValueError("manifest repository must be owner/name")
        if self.event != "pull_request":
            raise ValueError("manifest event must be pull_request")
        ids = [x.workflow_id for x in self.workflows]
        names = [x.name for x in self.workflows]
        paths = [x.path for x in self.workflows]
        if self.aggregate.workflow_id in ids:
            raise ValueError("aggregate workflow appears in external set")
        for label, values in (("workflow_id", ids), ("name", names), ("path", paths)):
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {label} in workflow manifest")
        for item in (self.aggregate, *self.workflows):
            if self.event not in item.allowed_events:
                raise ValueError(f"manifest event not allowed for {item.path}")


@dataclass(frozen=True)
class Run:
    id: int
    attempt: int
    workflow_id: int
    name: str
    path: str
    status: str
    conclusion: str | None
    head_sha: str
    event: str
    url: str

    @classmethod
    def parse(cls, value: dict[str, Any]) -> "Run":
        return cls(
            id=int(value["id"]), attempt=int(value.get("run_attempt", 1)),
            workflow_id=int(value["workflow_id"]), name=str(value["name"]),
            path=str(value.get("path", "")), status=str(value["status"]),
            conclusion=None if value.get("conclusion") is None else str(value["conclusion"]),
            head_sha=str(value["head_sha"]), event=str(value["event"]),
            url=str(value.get("html_url", "")),
        )


def verify_files(root: Path, manifest: Manifest) -> list[str]:
    failures: list[str] = []
    expected = {x.path: x.git_blob_sha1 for x in (manifest.aggregate, *manifest.workflows)}
    directory = root / ".github/workflows"
    if not directory.is_dir():
        return ["workflow directory is missing"]
    actual = {
        p.relative_to(root).as_posix()
        for p in directory.iterdir()
        if p.is_file() and p.suffix in {".yml", ".yaml"}
    }
    missing = sorted(set(expected) - actual)
    unlisted = sorted(actual - set(expected))
    if missing:
        failures.append(f"manifest workflow files are missing: {missing}")
    if manifest.reject_unlisted and unlisted:
        failures.append(f"workflow definitions are unlisted: {unlisted}")
    for relative, expected_sha in expected.items():
        target = root / relative
        if not target.is_file() or target.is_symlink():
            failures.append(f"required workflow is missing or not regular: {relative}")
        else:
            observed = blob_sha(target.read_bytes())
            if observed != expected_sha:
                failures.append(
                    f"workflow definition drift for {relative}: "
                    f"observed={observed} expected={expected_sha}"
                )
    return failures


def latest_runs(values: list[dict[str, Any]], *, head_sha: str, event: str,
                excluded_workflow_id: int | None) -> list[Run]:
    selected: dict[int, Run] = {}
    for value in values:
        run = Run.parse(value)
        if run.head_sha != head_sha or run.event != event:
            continue
        if excluded_workflow_id is not None and run.workflow_id == excluded_workflow_id:
            continue
        previous = selected.get(run.workflow_id)
        if previous is None or (run.id, run.attempt) > (previous.id, previous.attempt):
            selected[run.workflow_id] = run
    return sorted(selected.values(), key=lambda x: x.workflow_id)


def select_runs(values: list[dict[str, Any]], manifest: Manifest, head_sha: str) -> tuple[list[Run], list[str]]:
    runs = latest_runs(values, head_sha=head_sha, event=manifest.event,
                       excluded_workflow_id=manifest.aggregate.workflow_id)
    by_id = {x.workflow_id: x for x in runs}
    required_ids = {x.workflow_id for x in manifest.workflows}
    failures: list[str] = []
    if manifest.reject_unlisted:
        for run in runs:
            if run.workflow_id not in required_ids:
                failures.append(
                    f"unlisted exact-head workflow: id={run.workflow_id} "
                    f"name={run.name!r} path={run.path!r}"
                )
    selected: list[Run] = []
    for requirement in manifest.workflows:
        run = by_id.get(requirement.workflow_id)
        if run is None:
            failures.append(
                f"required workflow is missing: id={requirement.workflow_id} "
                f"name={requirement.name!r} path={requirement.path}"
            )
            continue
        selected.append(run)
        if run.name != requirement.name:
            failures.append(
                f"workflow name mismatch id={run.workflow_id}: "
                f"observed={run.name!r} expected={requirement.name!r}"
            )
        if run.path != requirement.path:
            failures.append(
                f"workflow path mismatch id={run.workflow_id}: "
                f"observed={run.path!r} expected={requirement.path!r}"
            )
        if run.event not in requirement.allowed_events:
            failures.append(f"workflow event mismatch for {requirement.path}")
    return selected, failures


def classify(runs: list[Run], minimum_runs: int) -> tuple[list[Run], list[Run], list[str]]:
    pending = [x for x in runs if x.status != "completed"]
    failed = [x for x in runs if x.status == "completed" and x.conclusion != "success"]
    failures = [] if len(runs) >= minimum_runs else [
        f"exact-head workflow collection is too small: observed={len(runs)} required>={minimum_runs}"
    ]
    failures += [f"workflow {x.name!r} completed with conclusion {x.conclusion!r}: {x.url}" for x in failed]
    return pending, failed, failures


def job_failures(jobs: list[dict[str, Any]], minimum: int = 1) -> list[str]:
    if not jobs:
        return ["workflow has zero jobs"]
    failures: list[str] = []
    valid = 0
    for job in jobs:
        if not isinstance(job, dict):
            failures.append("workflow job entry is not an object")
            continue
        name = str(job.get("name", ""))
        conclusion = job.get("conclusion")
        if conclusion == "skipped":
            continue
        if job.get("status") != "completed" or conclusion != "success":
            failures.append(
                f"job {name!r} is not terminal-success: "
                f"status={job.get('status')!r} conclusion={conclusion!r}"
            )
            continue
        steps = job.get("steps")
        if not isinstance(steps, list) or not steps:
            failures.append(f"job {name!r} has zero execution steps")
            continue
        meaningful = [
            step for step in steps if isinstance(step, dict)
            and str(step.get("name", "")) not in FRAMEWORK_STEPS
            and not str(step.get("name", "")).startswith("Post ")
            and step.get("status") == "completed"
            and step.get("conclusion") == "success"
        ]
        if not meaningful:
            failures.append(f"job {name!r} has no successful non-framework execution step")
            continue
        valid += 1
    if valid < minimum:
        failures.append(f"workflow has too few successful execution jobs: observed={valid} required>={minimum}")
    return failures


class GitHubApi:
    def __init__(self, token: str) -> None:
        if not token:
            raise ValueError("GITHUB_TOKEN is required")
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "trillionnium-closed-workflow-gate",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def get(self, url: str) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=self.headers), timeout=30
            ) as response:
                value = json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:1000]
            raise RuntimeError(f"GitHub API HTTP {error.code}: {detail}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"GitHub API request failed: {error.reason}") from error
        if not isinstance(value, dict):
            raise RuntimeError("GitHub API response is not an object")
        return value

    def paged(self, url: str, key: str, query: dict[str, Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for page in range(1, 21):
            qs = urllib.parse.urlencode({**query, "per_page": 100, "page": page})
            values = self.get(f"{url}?{qs}").get(key)
            if not isinstance(values, list):
                raise RuntimeError(f"{key} response is absent or invalid")
            if any(not isinstance(x, dict) for x in values):
                raise RuntimeError(f"{key} contains a non-object")
            result.extend(values)
            if len(values) < 100:
                return result
        raise RuntimeError(f"{key} pagination exceeded 2000 entries")

    def current_workflow_id(self, repo: str, run_id: int) -> int:
        value = self.get(f"{API}/repos/{repo}/actions/runs/{run_id}").get("workflow_id")
        if not isinstance(value, int) or value <= 0:
            raise RuntimeError("current workflow_id is absent or invalid")
        return value

    def runs(self, repo: str, sha: str, event: str) -> list[dict[str, Any]]:
        return self.paged(f"{API}/repos/{repo}/actions/runs", "workflow_runs",
                          {"head_sha": sha, "event": event})

    def jobs(self, repo: str, run_id: int) -> list[dict[str, Any]]:
        return self.paged(f"{API}/repos/{repo}/actions/runs/{run_id}/jobs", "jobs",
                          {"filter": "latest"})


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--current-run-id", required=True, type=int)
    parser.add_argument("--manifest", type=Path, default=Path(DEFAULT_MANIFEST))
    parser.add_argument("--timeout-seconds", type=int, default=2700)
    parser.add_argument("--poll-seconds", type=float, default=5)
    parser.add_argument("--stable-polls", type=int, default=3)
    value = parser.parse_args(argv)
    if not valid_repo(value.repository):
        parser.error("repository must be owner/name")
    if not re.fullmatch(r"[0-9a-f]{40}", value.head_sha):
        parser.error("head SHA must be forty lowercase hex characters")
    if min(value.current_run_id, value.timeout_seconds, value.stable_polls) <= 0 or value.poll_seconds <= 0:
        parser.error("run id, timeouts, polling and stable polls must be positive")
    return value


def main(argv: list[str] | None = None) -> int:
    options = arguments(argv)
    try:
        manifest = Manifest.load(options.manifest)
        if manifest.repository != options.repository:
            raise ValueError("repository does not match manifest")
        failures = verify_files(Path.cwd(), manifest)
        if failures:
            raise ValueError("; ".join(failures))
        api = GitHubApi(os.environ.get("GITHUB_TOKEN", ""))
        current_id = api.current_workflow_id(options.repository, options.current_run_id)
        if current_id != manifest.aggregate.workflow_id:
            raise ValueError("current workflow does not match manifest aggregate")
    except (ValueError, RuntimeError) as error:
        print(f"required workflow gate failed: {error}", file=sys.stderr)
        return 1

    deadline = time.monotonic() + options.timeout_seconds
    previous: tuple[tuple[int, int, int], ...] | None = None
    stable = 0
    verified_jobs: set[tuple[int, int]] = set()
    last: list[str] = []
    while True:
        try:
            runs, failures = select_runs(
                api.runs(options.repository, options.head_sha, manifest.event),
                manifest, options.head_sha,
            )
        except RuntimeError as error:
            print(f"required workflow gate failed: {error}", file=sys.stderr)
            return 1
        by_id = {x.workflow_id: x for x in runs}
        terminal = [
            f"{x.path}: conclusion={x.conclusion!r} url={x.url}"
            for x in runs if x.status == "completed" and x.conclusion != "success"
        ]
        invariant = [x for x in failures if not x.startswith("required workflow is missing:")]
        missing = [x for x in failures if x.startswith("required workflow is missing:")]
        if terminal or invariant:
            for failure in invariant + terminal:
                print(f"required workflow gate failed: {failure}", file=sys.stderr)
            return 1
        pending = [x for x in runs if x.status != "completed"]
        if len(runs) == len(manifest.workflows) and not missing and not pending:
            evidence: list[str] = []
            for requirement in manifest.workflows:
                run = by_id[requirement.workflow_id]
                key = (run.id, run.attempt)
                if key not in verified_jobs:
                    try:
                        failures = job_failures(
                            api.jobs(options.repository, run.id),
                            requirement.minimum_successful_execution_jobs,
                        )
                    except RuntimeError as error:
                        failures = [f"cannot read jobs: {error}"]
                    evidence += [f"{requirement.path} run={run.id}: {x}" for x in failures]
                    if not failures:
                        verified_jobs.add(key)
            if evidence:
                for failure in evidence:
                    print(f"required workflow gate failed: {failure}", file=sys.stderr)
                return 1
            identity = tuple((x.workflow_id, x.id, x.attempt) for x in runs)
            stable = stable + 1 if identity == previous else 1
            previous = identity
            if stable >= options.stable_polls:
                print(
                    f"required workflow gate: OK ({len(runs)}/{len(manifest.workflows)} "
                    "manifest-bound exact-head workflows terminal-success with non-empty jobs/steps)"
                )
                return 0
        else:
            stable, previous = 0, None
        last = missing + [f"pending {x.name}: run={x.id} status={x.status}" for x in pending]
        if time.monotonic() >= deadline:
            print(
                f"required workflow gate timed out: observed={len(runs)}/{len(manifest.workflows)}",
                file=sys.stderr,
            )
            for failure in last:
                print(f"- {failure}", file=sys.stderr)
            return 1
        time.sleep(options.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
