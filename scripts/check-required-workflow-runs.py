#!/usr/bin/env python3
"""Fail closed unless every exact-head pull-request workflow run succeeds."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

API_ROOT = "https://api.github.com"
TERMINAL_STATUS = "completed"
SUCCESS_CONCLUSION = "success"


@dataclass(frozen=True)
class Run:
    id: int
    workflow_id: int
    name: str
    status: str
    conclusion: str | None
    head_sha: str
    event: str
    html_url: str

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> "Run":
        return cls(
            id=int(value["id"]),
            workflow_id=int(value["workflow_id"]),
            name=str(value["name"]),
            status=str(value["status"]),
            conclusion=(
                None if value.get("conclusion") is None else str(value["conclusion"])
            ),
            head_sha=str(value["head_sha"]),
            event=str(value["event"]),
            html_url=str(value.get("html_url", "")),
        )


def latest_runs(
    values: list[dict[str, Any]],
    *,
    head_sha: str,
    event: str,
    excluded_workflow_id: int | None,
) -> list[Run]:
    """Return the newest exact-target run for each workflow definition."""

    selected: dict[int, Run] = {}
    for value in values:
        run = Run.from_json(value)
        if run.head_sha != head_sha or run.event != event:
            continue
        if excluded_workflow_id is not None and run.workflow_id == excluded_workflow_id:
            continue
        previous = selected.get(run.workflow_id)
        if previous is None or run.id > previous.id:
            selected[run.workflow_id] = run
    return sorted(selected.values(), key=lambda item: (item.name, item.workflow_id))


def classify(runs: list[Run], minimum_runs: int) -> tuple[list[Run], list[Run], list[str]]:
    pending = [run for run in runs if run.status != TERMINAL_STATUS]
    failed = [
        run
        for run in runs
        if run.status == TERMINAL_STATUS and run.conclusion != SUCCESS_CONCLUSION
    ]
    failures: list[str] = []
    if len(runs) < minimum_runs:
        failures.append(
            f"exact-head workflow collection is too small: "
            f"observed={len(runs)} required>={minimum_runs}"
        )
    for run in failed:
        failures.append(
            f"workflow {run.name!r} completed with conclusion "
            f"{run.conclusion!r}: {run.html_url}"
        )
    return pending, failed, failures


class GitHubApi:
    def __init__(self, token: str) -> None:
        if not token:
            raise ValueError("GITHUB_TOKEN is required")
        self._headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "trillionnium-required-workflow-gate",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def get_json(self, url: str) -> dict[str, Any]:
        request = urllib.request.Request(url, headers=self._headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.status != 200:
                    raise RuntimeError(f"GitHub API returned HTTP {response.status}")
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:1000]
            raise RuntimeError(
                f"GitHub API returned HTTP {error.code}: {detail}"
            ) from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"GitHub API request failed: {error.reason}") from error
        if not isinstance(payload, dict):
            raise RuntimeError("GitHub API response is not an object")
        return payload

    def current_workflow_id(self, repository: str, run_id: int) -> int:
        payload = self.get_json(
            f"{API_ROOT}/repos/{repository}/actions/runs/{run_id}"
        )
        workflow_id = payload.get("workflow_id")
        if not isinstance(workflow_id, int) or workflow_id <= 0:
            raise RuntimeError("current workflow_id is absent or invalid")
        return workflow_id

    def workflow_runs(
        self, repository: str, head_sha: str, *, event: str
    ) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        page = 1
        while True:
            query = urllib.parse.urlencode(
                {
                    "head_sha": head_sha,
                    "event": event,
                    "per_page": 100,
                    "page": page,
                }
            )
            payload = self.get_json(
                f"{API_ROOT}/repos/{repository}/actions/runs?{query}"
            )
            page_values = payload.get("workflow_runs")
            if not isinstance(page_values, list):
                raise RuntimeError("workflow_runs response is absent or invalid")
            for value in page_values:
                if not isinstance(value, dict):
                    raise RuntimeError("workflow run entry is not an object")
                values.append(value)
            if len(page_values) < 100:
                break
            page += 1
            if page > 20:
                raise RuntimeError("workflow run pagination exceeded 2000 entries")
        return values


def valid_repository(value: str) -> str:
    parts = value.split("/")
    if (
        len(parts) != 2
        or not all(parts)
        or any(
            not all(character.isalnum() or character in "._-" for character in part)
            for part in parts
        )
    ):
        raise argparse.ArgumentTypeError("repository must be owner/name")
    return value


def valid_sha(value: str) -> str:
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise argparse.ArgumentTypeError(
            "head SHA must be forty lowercase hexadecimal characters"
        )
    return value


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--repository", required=True, type=valid_repository)
    result.add_argument("--head-sha", required=True, type=valid_sha)
    result.add_argument("--current-run-id", required=True, type=int)
    result.add_argument("--minimum-runs", type=int, default=1)
    result.add_argument("--timeout-seconds", type=int, default=2700)
    result.add_argument("--poll-seconds", type=float, default=5.0)
    result.add_argument("--stable-polls", type=int, default=3)
    return result


def main(arguments: list[str] | None = None) -> int:
    options = parser().parse_args(arguments)
    if options.current_run_id <= 0:
        raise SystemExit("--current-run-id must be positive")
    if options.minimum_runs <= 0:
        raise SystemExit("--minimum-runs must be positive")
    if options.timeout_seconds <= 0:
        raise SystemExit("--timeout-seconds must be positive")
    if options.poll_seconds <= 0:
        raise SystemExit("--poll-seconds must be positive")
    if options.stable_polls <= 0:
        raise SystemExit("--stable-polls must be positive")

    try:
        api = GitHubApi(os.environ.get("GITHUB_TOKEN", ""))
        excluded_workflow_id = api.current_workflow_id(
            options.repository, options.current_run_id
        )
    except (RuntimeError, ValueError) as error:
        print(f"required workflow gate failed: {error}", file=sys.stderr)
        return 1

    deadline = time.monotonic() + options.timeout_seconds
    previous_identity: tuple[tuple[int, int], ...] | None = None
    stable_success_polls = 0
    last_pending: list[Run] = []
    last_count = 0

    while True:
        try:
            raw_runs = api.workflow_runs(
                options.repository, options.head_sha, event="pull_request"
            )
            runs = latest_runs(
                raw_runs,
                head_sha=options.head_sha,
                event="pull_request",
                excluded_workflow_id=excluded_workflow_id,
            )
        except RuntimeError as error:
            print(f"required workflow gate failed: {error}", file=sys.stderr)
            return 1

        pending, failed, failures = classify(runs, options.minimum_runs)
        last_pending = pending
        last_count = len(runs)
        if failed:
            print("required workflow gate failed:", file=sys.stderr)
            for failure in failures:
                print(f"- {failure}", file=sys.stderr)
            return 1

        identity = tuple((run.workflow_id, run.id) for run in runs)
        collection_large_enough = len(runs) >= options.minimum_runs
        if collection_large_enough and not pending:
            if identity == previous_identity:
                stable_success_polls += 1
            else:
                previous_identity = identity
                stable_success_polls = 1
            if stable_success_polls >= options.stable_polls:
                print(
                    "required workflow gate: OK "
                    f"({len(runs)} external exact-head pull-request workflows; "
                    "all completed/success; collection stable)"
                )
                for run in runs:
                    print(
                        f"- {run.name}: id={run.id} "
                        f"status={run.status} conclusion={run.conclusion}"
                    )
                return 0
        else:
            previous_identity = identity
            stable_success_polls = 0

        if time.monotonic() >= deadline:
            print("required workflow gate timed out:", file=sys.stderr)
            print(
                f"- observed {last_count} exact-head external workflows; "
                f"required at least {options.minimum_runs}",
                file=sys.stderr,
            )
            for run in last_pending:
                print(
                    f"- pending {run.name}: id={run.id} status={run.status}",
                    file=sys.stderr,
                )
            return 1
        time.sleep(options.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
