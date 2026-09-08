#!/usr/bin/env python3
"""Dispatch and verify the closed required-workflow set for one frozen SHA.

This tool creates execution evidence only. It cannot independently accept its
own packet, change gap status, or substitute for the prospective-merge object.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class Workflow:
    workflow_id: int
    path: str
    name: str


class Api:
    def __init__(self, repository: str, token: str) -> None:
        self.base = f"https://api.github.com/repos/{repository}"
        self.token = token

    def call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "trillionnium-exact-head-qualification",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
        return None if not raw else json.loads(raw)

    def paged(self, path: str) -> list[Any]:
        result: list[Any] = []
        page = 1
        while True:
            separator = "&" if "?" in path else "?"
            value = self.call("GET", f"{path}{separator}per_page=100&page={page}")
            if isinstance(value, list):
                rows = value
            elif isinstance(value, dict):
                rows = next(
                    (
                        item
                        for item in value.values()
                        if isinstance(item, list)
                    ),
                    [],
                )
            else:
                rows = []
            result.extend(rows)
            if len(rows) < 100:
                return result
            page += 1


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)


def required_paths(root: Path) -> list[str]:
    manifest = json.loads(
        (root / "docs/governance/REQUIRED_WORKFLOWS_V1.json").read_text(
            encoding="utf-8"
        )
    )
    paths = {
        value.removeprefix("./")
        for value in strings(manifest)
        if value.removeprefix("./").startswith(".github/workflows/")
        and value.endswith((".yml", ".yaml"))
    }
    if not paths:
        # The manifest may register names rather than paths. In that case use
        # the complete repository workflow set but exclude explicit one-shot
        # and controller workflows.
        paths = {
            path.relative_to(root).as_posix()
            for path in (root / ".github/workflows").glob("*.y*ml")
            if "one-shot" not in path.name
            and "transport" not in path.name
            and "controller" not in path.name
        }
    return sorted(paths)


def workflow_inventory(api: Api) -> dict[str, Workflow]:
    rows = api.paged("/actions/workflows")
    result: dict[str, Workflow] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        path = row.get("path")
        workflow_id = row.get("id")
        name = row.get("name")
        if isinstance(path, str) and isinstance(workflow_id, int) and isinstance(name, str):
            result[path] = Workflow(workflow_id=workflow_id, path=path, name=name)
    return result


def exact_runs(api: Api, workflow: Workflow, head: str) -> list[dict[str, Any]]:
    value = api.call(
        "GET",
        f"/actions/workflows/{workflow.workflow_id}/runs?per_page=100",
    )
    rows = value.get("workflow_runs", []) if isinstance(value, dict) else []
    return [
        row
        for row in rows
        if isinstance(row, dict) and row.get("head_sha") == head
    ]


def jobs(api: Api, run_id: int) -> list[dict[str, Any]]:
    value = api.call("GET", f"/actions/runs/{run_id}/jobs?per_page=100")
    return value.get("jobs", []) if isinstance(value, dict) else []


def qualify_run(api: Api, row: dict[str, Any]) -> tuple[bool, str, int]:
    status = row.get("status")
    conclusion = row.get("conclusion")
    run_id = row.get("id")
    if status != "completed":
        return False, "active", 0
    if conclusion != "success" or not isinstance(run_id, int):
        return False, str(conclusion or "unknown"), 0
    run_jobs = jobs(api, run_id)
    if not run_jobs:
        return False, "zero-jobs", 0
    bad = [
        job
        for job in run_jobs
        if job.get("status") != "completed" or job.get("conclusion") != "success"
    ]
    if bad:
        classifications = sorted(
            {str(job.get("conclusion") or job.get("status")) for job in bad}
        )
        return False, "jobs:" + ",".join(classifications), len(run_jobs)
    return True, "success", len(run_jobs)


def best_state(api: Api, workflow: Workflow, head: str) -> dict[str, Any]:
    rows = exact_runs(api, workflow, head)
    evaluated: list[dict[str, Any]] = []
    for row in rows:
        qualified, reason, job_count = qualify_run(api, row)
        evaluated.append(
            {
                "run_id": row.get("id"),
                "event": row.get("event"),
                "run_attempt": row.get("run_attempt"),
                "status": row.get("status"),
                "conclusion": row.get("conclusion"),
                "qualified": qualified,
                "reason": reason,
                "job_count": job_count,
                "html_url": row.get("html_url"),
            }
        )
    successful = [row for row in evaluated if row["qualified"]]
    if successful:
        successful.sort(key=lambda row: (int(row["run_attempt"] or 0), int(row["run_id"] or 0)), reverse=True)
        return {"state": "success", "selected": successful[0], "runs": evaluated}
    if any(row["status"] != "completed" for row in evaluated):
        return {"state": "active", "selected": None, "runs": evaluated}
    if evaluated:
        return {"state": "failed", "selected": None, "runs": evaluated}
    return {"state": "missing", "selected": None, "runs": []}


def dispatch(api: Api, workflow: Workflow, ref: str) -> tuple[bool, str]:
    try:
        api.call(
            "POST",
            f"/actions/workflows/{workflow.workflow_id}/dispatches",
            {"ref": ref},
        )
        return True, "accepted"
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")[:2000]
        return False, f"HTTP {error.code}: {body}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=4500)
    parser.add_argument("--poll-seconds", type=int, default=20)
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    require(bool(token), "GITHUB_TOKEN is required")
    require(len(args.head) == 40, "head must be a full commit SHA")
    api = Api(args.repository, token or "")

    pull = api.call("GET", f"/pulls/{args.pr}")
    require(pull.get("head", {}).get("sha") == args.head, "PR head is not the frozen target SHA")
    require(pull.get("head", {}).get("ref") == args.ref, "PR head branch does not match")

    required = required_paths(args.root)
    inventory = workflow_inventory(api)
    missing_from_api = sorted(set(required) - set(inventory))
    require(not missing_from_api, f"required workflows absent from API inventory: {missing_from_api}")

    dispatches: dict[str, dict[str, Any]] = {}
    initial: dict[str, dict[str, Any]] = {}
    for path in required:
        workflow = inventory[path]
        state = best_state(api, workflow, args.head)
        initial[path] = state
        if state["state"] in {"missing", "failed"}:
            accepted, detail = dispatch(api, workflow, args.ref)
            dispatches[path] = {"accepted": accepted, "detail": detail}
        else:
            dispatches[path] = {"accepted": False, "detail": "not-required"}

    deadline = time.monotonic() + args.timeout_seconds
    final: dict[str, dict[str, Any]] = {}
    while True:
        pull = api.call("GET", f"/pulls/{args.pr}")
        require(pull.get("head", {}).get("sha") == args.head, "PR head moved during qualification")
        final = {
            path: best_state(api, inventory[path], args.head)
            for path in required
        }
        unresolved = {
            path: row["state"]
            for path, row in final.items()
            if row["state"] != "success"
        }
        if not unresolved:
            break
        if time.monotonic() >= deadline:
            break
        # Failed dispatched runs are re-run at most once through the supported
        # rerun endpoint; no new source or synthetic check is created.
        for path, state in final.items():
            if state["state"] != "failed":
                continue
            rows = state["runs"]
            if not rows:
                continue
            latest = max(
                (row for row in rows if isinstance(row.get("run_id"), int)),
                key=lambda row: int(row["run_id"]),
                default=None,
            )
            if latest is None or latest.get("rerun_requested"):
                continue
            try:
                api.call("POST", f"/actions/runs/{latest['run_id']}/rerun-failed-jobs")
                latest["rerun_requested"] = True
            except urllib.error.HTTPError:
                pass
        time.sleep(args.poll_seconds)

    selected = {
        path: row["selected"]
        for path, row in final.items()
        if row["state"] == "success"
    }
    unresolved = {
        path: row["state"]
        for path, row in final.items()
        if row["state"] != "success"
    }
    packet = {
        "schema": "trillionnium.required-workflow-exact-head-candidate.v1",
        "repository": args.repository,
        "pull_request": args.pr,
        "head_ref": args.ref,
        "head_sha": args.head,
        "required_workflow_count": len(required),
        "selected_success_count": len(selected),
        "dispatches": dispatches,
        "selected_runs": selected,
        "unresolved": unresolved,
        "claims": {
            "exact_head_execution_complete": not unresolved,
            "prospective_merge_execution_complete": False,
            "independent_acceptance": False,
            "accepted_evidence": False,
            "gap_closed": False,
            "production_ready": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(packet, indent=2, sort_keys=True) + "\n").encode("utf-8")
    args.output.write_bytes(raw)
    args.output.with_suffix(args.output.suffix + ".sha256").write_text(
        hashlib.sha256(raw).hexdigest() + "  " + args.output.name + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"success": not unresolved, "selected": len(selected), "required": len(required), "unresolved": unresolved}, sort_keys=True))
    return 0 if not unresolved else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"qualification failed closed: {error}", file=sys.stderr)
        raise
