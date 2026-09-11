#!/usr/bin/env python3
"""Settle current Plan v3.2 pull requests without bypassing protection."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

REPOSITORY = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GH_TOKEN"]
OWNER, _ = REPOSITORY.split("/", 1)
REVIEWERS = ["Franksudoman", "Tomasrgbsf"]
WORK = Path(tempfile.mkdtemp(prefix="plan-v32-settlement-"))
REPORT: dict[str, Any] = {
    "schema": "trillionnium.plan-v32-pr-settlement.v1",
    "repository": REPOSITORY,
    "run_id": os.environ.get("GITHUB_RUN_ID"),
    "history_rewritten": False,
    "administrator_bypass": False,
    "self_approval": False,
    "review_dismissal": False,
    "prs": [],
    "errors": [],
}


def run(
    args: list[str],
    *,
    check: bool = True,
    timeout: int | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        text=True,
        input=input_text,
        capture_output=True,
        check=False,
        timeout=timeout,
        env=os.environ.copy(),
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(args)}\n"
            f"stdout:\n{result.stdout[-10000:]}\n"
            f"stderr:\n{result.stderr[-10000:]}"
        )
    return result


def gh(endpoint: str, *, method: str = "GET", payload: Any | None = None) -> Any:
    args = ["gh", "api", endpoint, "-X", method]
    if payload is None:
        result = run(args, timeout=180)
    else:
        result = run(
            args + ["--input", "-"],
            timeout=180,
            input_text=json.dumps(payload),
        )
    return json.loads(result.stdout) if result.stdout.strip() else None


def pages(endpoint: str) -> list[Any]:
    result = run(["gh", "api", "--paginate", "--slurp", endpoint], timeout=300)
    output: list[Any] = []
    for page in json.loads(result.stdout or "[]"):
        if isinstance(page, list):
            output.extend(page)
        else:
            output.append(page)
    return output


def plan_pulls() -> list[dict[str, Any]]:
    pulls = pages(f"repos/{REPOSITORY}/pulls?state=open&per_page=100")
    return [item for item in pulls if "Plan v3.2" in (item.get("title") or "")]


def current_reviews(number: int) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for review in pages(f"repos/{REPOSITORY}/pulls/{number}/reviews?per_page=100"):
        login = ((review.get("user") or {}).get("login") or "").lower()
        if not login:
            continue
        incumbent = latest.get(login)
        if incumbent is None or (review.get("submitted_at") or "") >= (
            incumbent.get("submitted_at") or ""
        ):
            latest[login] = review
    return latest


def check_runs(head: str) -> list[dict[str, Any]]:
    response = gh(f"repos/{REPOSITORY}/commits/{head}/check-runs?per_page=100") or {}
    return list(response.get("check_runs") or [])


def workflow_runs(head: str) -> list[dict[str, Any]]:
    response = gh(
        f"repos/{REPOSITORY}/actions/runs?head_sha={head}&per_page=100"
    ) or {}
    return list(response.get("workflow_runs") or [])


def job_log(job_id: int) -> str:
    archive = WORK / f"job-{job_id}.zip"
    response = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/actions/jobs/{job_id}/logs",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(response, timeout=180) as source:
            archive.write_bytes(source.read())
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as bundle:
                return "\n".join(
                    bundle.read(name).decode("utf-8", "replace")
                    for name in bundle.namelist()
                    if not name.endswith("/")
                )
        return archive.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def classify_run_failure(run_data: dict[str, Any]) -> tuple[str, list[int]]:
    conclusion = run_data.get("conclusion")
    if conclusion in {"timed_out", "startup_failure", "action_required"}:
        return "infrastructure", []
    jobs = pages(
        f"repos/{REPOSITORY}/actions/runs/{run_data['id']}/jobs?per_page=100"
    )
    failed_ids = [int(job["id"]) for job in jobs if job.get("conclusion") == "failure"]
    combined = "\n".join(job_log(job_id) for job_id in failed_ids)
    known_stale = (
        "captured branch moved after inventory" in combined
        or "captured branch removed after inventory" in combined
        or "live remote ref set changed during inventory" in combined
    )
    if known_stale:
        return "stale-inventory", failed_ids
    return "deterministic", failed_ids


def rerun_bounded(head: str) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    seen_workflows: set[int] = set()
    for item in workflow_runs(head):
        workflow_id = int(item.get("workflow_id") or 0)
        if workflow_id in seen_workflows:
            continue
        seen_workflows.add(workflow_id)
        if item.get("status") != "completed" or item.get("conclusion") == "success":
            continue
        classification, jobs = classify_run_failure(item)
        decision = {
            "run_id": int(item["id"]),
            "name": item.get("name"),
            "conclusion": item.get("conclusion"),
            "classification": classification,
            "failed_job_ids": jobs,
            "rerun_requested": False,
        }
        if classification in {"infrastructure", "stale-inventory"}:
            result = run(
                ["gh", "run", "rerun", str(item["id"]), "--repo", REPOSITORY, "--failed"],
                check=False,
                timeout=180,
            )
            decision["rerun_requested"] = result.returncode == 0
            if result.returncode != 0:
                decision["rerun_error"] = result.stderr[-3000:]
        decisions.append(decision)
    return decisions


def wait_for_terminal(head: str, maximum: int = 3600) -> list[dict[str, Any]]:
    deadline = time.monotonic() + maximum
    latest: list[dict[str, Any]] = []
    while time.monotonic() <= deadline:
        latest = check_runs(head)
        if latest and all(item.get("status") == "completed" for item in latest):
            return latest
        time.sleep(30)
    return latest


def request_reviewers(number: int) -> None:
    try:
        gh(
            f"repos/{REPOSITORY}/pulls/{number}/requested_reviewers",
            method="POST",
            payload={"reviewers": REVIEWERS},
        )
    except Exception as error:
        REPORT["errors"].append(f"review request for PR #{number}: {error}")


def update_gate_marker(number: int, data: dict[str, Any]) -> None:
    pull = gh(f"repos/{REPOSITORY}/pulls/{number}")
    marker_start = "<!-- plan-v32-settlement:start -->"
    marker_end = "<!-- plan-v32-settlement:end -->"
    block = (
        marker_start
        + "\n## Current exact-head settlement\n\n```json\n"
        + json.dumps(data, indent=2, sort_keys=True)
        + "\n```\n\nNo administrator bypass, self-approval, review dismissal,"
        " evidence fabrication or claim promotion was used.\n"
        + marker_end
    )
    body = pull.get("body") or ""
    pattern = re.compile(
        re.escape(marker_start) + r".*?" + re.escape(marker_end), re.DOTALL
    )
    body = pattern.sub(block, body, count=1) if pattern.search(body) else block + "\n\n" + body
    gh(
        f"repos/{REPOSITORY}/pulls/{number}",
        method="PATCH",
        payload={"body": body},
    )


def settle_one(pull: dict[str, Any]) -> dict[str, Any]:
    number = int(pull["number"])
    head = pull["head"]["sha"]
    request_reviewers(number)
    initial = check_runs(head)
    if initial and all(item.get("status") == "completed" for item in initial):
        reruns = rerun_bounded(head)
        if any(item.get("rerun_requested") for item in reruns):
            checks = wait_for_terminal(head)
        else:
            checks = initial
    else:
        reruns = []
        checks = wait_for_terminal(head)

    # Re-read the pull request in case its head changed while checks ran.
    current = gh(f"repos/{REPOSITORY}/pulls/{number}")
    if current.get("state") != "open":
        return {
            "number": number,
            "state": current.get("state"),
            "merged": bool(current.get("merged_at")),
        }
    if current["head"]["sha"] != head:
        return {
            "number": number,
            "state": "head-moved-during-settlement",
            "initial_head": head,
            "current_head": current["head"]["sha"],
        }

    conclusions = Counter((item.get("conclusion") or "pending") for item in checks)
    non_success = sorted(
        str(item.get("name"))
        for item in checks
        if item.get("status") != "completed" or item.get("conclusion") != "success"
    )
    all_success = bool(checks) and not non_success
    latest = current_reviews(number)
    author = ((current.get("user") or {}).get("login") or "").lower()
    approvals = sorted(
        login
        for login, review in latest.items()
        if login != author
        and review.get("state") == "APPROVED"
        and review.get("commit_id") == head
    )
    data = {
        "number": number,
        "title": current.get("title"),
        "head": head,
        "base": current["base"]["sha"],
        "draft": bool(current.get("draft")),
        "check_count": len(checks),
        "conclusion_counts": dict(conclusions),
        "non_success_checks": non_success,
        "current_exact_head_approvals": approvals,
        "mergeable": current.get("mergeable"),
        "mergeable_state": current.get("mergeable_state"),
        "bounded_reruns": reruns,
        "ordinary_merge_attempted": False,
        "ordinary_merge_completed": False,
    }
    update_gate_marker(number, data)

    if all_success and approvals and current.get("mergeable") is True:
        if current.get("draft"):
            ready = run(
                ["gh", "pr", "ready", str(number), "--repo", REPOSITORY],
                check=False,
                timeout=180,
            )
            if ready.returncode != 0:
                data["ordinary_merge_error"] = ready.stderr[-3000:]
                update_gate_marker(number, data)
                return data
        data["ordinary_merge_attempted"] = True
        merged = run(
            [
                "gh",
                "pr",
                "merge",
                str(number),
                "--repo",
                REPOSITORY,
                "--squash",
                "--delete-branch=false",
            ],
            check=False,
            timeout=300,
        )
        reread = gh(f"repos/{REPOSITORY}/pulls/{number}")
        data["ordinary_merge_completed"] = bool(reread.get("merged_at"))
        if merged.returncode != 0:
            data["ordinary_merge_error"] = merged.stderr[-3000:]
        update_gate_marker(number, data)
    return data


def update_issue() -> None:
    marker = "<!-- plan-v32-pr-settlement -->"
    body = (
        marker
        + "\n## Plan v3.2 pull-request settlement readback\n\n```json\n"
        + json.dumps(REPORT, indent=2, sort_keys=True)
        + "\n```\n"
    )
    comments = gh(f"repos/{REPOSITORY}/issues/129/comments?per_page=100") or []
    me = (gh("user") or {}).get("login", "").lower()
    existing = next(
        (
            item
            for item in comments
            if marker in (item.get("body") or "")
            and ((item.get("user") or {}).get("login") or "").lower() == me
        ),
        None,
    )
    if existing:
        gh(
            f"repos/{REPOSITORY}/issues/comments/{existing['id']}",
            method="PATCH",
            payload={"body": body},
        )
    else:
        gh(
            f"repos/{REPOSITORY}/issues/129/comments",
            method="POST",
            payload={"body": body},
        )


def emit() -> None:
    REPORT["finished_at_epoch"] = int(time.time())
    rendered = json.dumps(REPORT, indent=2, sort_keys=True)
    print(rendered)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("## Plan v3.2 PR settlement\n\n```json\n")
            handle.write(rendered)
            handle.write("\n```\n")


def main() -> int:
    if not TOKEN:
        raise RuntimeError("GH_TOKEN is required")
    run(["gh", "auth", "setup-git"], timeout=120)
    # Let source-producing controllers establish their exact heads first.
    time.sleep(15 * 60)
    for pull in plan_pulls():
        try:
            REPORT["prs"].append(settle_one(pull))
        except Exception as error:
            REPORT["prs"].append(
                {"number": pull.get("number"), "status": "failed", "error": str(error)}
            )
            REPORT["errors"].append(f"PR #{pull.get('number')}: {error}")
    try:
        update_issue()
    except Exception as error:
        REPORT["errors"].append(f"issue readback: {error}")
    emit()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        REPORT["errors"].append(str(exc))
        emit()
        raise
