#!/usr/bin/env python3
"""Materialize the bounded Plan v3.2 operations/security wave on current main."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

REPOSITORY = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GH_TOKEN"]
OWNER, _ = REPOSITORY.split("/", 1)
REMOTE = f"https://github.com/{REPOSITORY}.git"
CONTROLLER_COMMIT = "c78f20a02a475c2ddc1a376fd24b79930d3c7e29"
OPS_BRANCH = "ops/game-exact-source-snapshot-20260910"
REVIEWERS = ["Franksudoman", "Tomasrgbsf"]
WORK = Path(tempfile.mkdtemp(prefix="plan-v32-current-ops-"))
REPORT: dict[str, Any] = {
    "schema": "trillionnium.plan-v32-current-ops-security-materialization.v1",
    "repository": REPOSITORY,
    "run_id": os.environ.get("GITHUB_RUN_ID"),
    "history_rewritten": False,
    "administrator_bypass": False,
    "accepted_evidence": False,
    "production_ready": False,
    "public_online": False,
    "cutover_authorized": False,
    "nakama_retired": False,
    "errors": [],
}


def run(
    args: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
        env=os.environ.copy(),
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(args)}\n"
            f"stdout:\n{result.stdout[-12000:]}\n"
            f"stderr:\n{result.stderr[-12000:]}"
        )
    return result


def gh(endpoint: str, *, method: str = "GET", payload: Any | None = None) -> Any:
    args = ["gh", "api", endpoint, "-X", method]
    if payload is None:
        result = run(args, timeout=180)
    else:
        result = subprocess.run(
            args + ["--input", "-"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
            timeout=180,
            env=os.environ.copy(),
        )
        if result.returncode != 0:
            raise RuntimeError(f"gh api failed: {endpoint}\n{result.stderr[-8000:]}")
    return json.loads(result.stdout) if result.stdout.strip() else None


def clone(ref: str, destination: Path, *, branch: str | None = None) -> Path:
    run(["git", "init", str(destination)])
    run(["git", "-C", str(destination), "remote", "add", "origin", REMOTE])
    run(["git", "-C", str(destination), "fetch", "--no-tags", "origin", ref], timeout=600)
    if branch:
        run(["git", "-C", str(destination), "checkout", "-B", branch, "FETCH_HEAD"])
    else:
        run(["git", "-C", str(destination), "checkout", "--detach", "FETCH_HEAD"])
    return destination


def rev(repo: Path, expression: str = "HEAD") -> str:
    return run(["git", "rev-parse", expression], cwd=repo).stdout.strip()


def verify(repo: Path) -> None:
    run(["git", "diff", "--check"], cwd=repo, timeout=120)
    run(["python3", "-m", "compileall", "-q", "scripts", "tests"], cwd=repo, timeout=900)
    run(["cargo", "fmt", "--all"], cwd=repo, timeout=600)
    run(["cargo", "fmt", "--all", "--", "--check"], cwd=repo, timeout=600)
    run(
        ["cargo", "test", "--workspace", "--all-targets", "--locked"],
        cwd=repo,
        timeout=5400,
    )
    run(
        [
            "cargo",
            "clippy",
            "--workspace",
            "--all-targets",
            "--locked",
            "--",
            "-D",
            "warnings",
        ],
        cwd=repo,
        timeout=5400,
    )
    run(
        [
            "python3",
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests/control_plane",
            "-p",
            "test_*.py",
            "-v",
        ],
        cwd=repo,
        timeout=3600,
    )
    for script in (
        "scripts/check-plan.py",
        "scripts/derive-gates.py",
        "scripts/check-status-transitions.py",
        "scripts/check-schema-authority.py",
    ):
        if (repo / script).is_file():
            run(["python3", script], cwd=repo, timeout=900)
    run(["git", "diff", "--check"], cwd=repo, timeout=120)


def request_reviewers(number: int) -> None:
    try:
        gh(
            f"repos/{REPOSITORY}/pulls/{number}/requested_reviewers",
            method="POST",
            payload={"reviewers": REVIEWERS},
        )
    except Exception as error:
        REPORT["errors"].append(f"review request for PR #{number}: {error}")


def update_issue() -> None:
    marker = "<!-- plan-v32-current-ops-security-materialization -->"
    body = (
        marker
        + "\n## Current-main operations/security materialization\n\n```json\n"
        + json.dumps(REPORT, indent=2, sort_keys=True)
        + "\n```\n\nSource publication is not accepted evidence and closes no gap by itself."
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
            handle.write("## Current-main operations/security materialization\n\n```json\n")
            handle.write(rendered)
            handle.write("\n```\n")


def main() -> int:
    if not TOKEN:
        raise RuntimeError("GH_TOKEN is required")
    run(["gh", "auth", "setup-git"], timeout=120)

    main_repo = clone("refs/heads/main", WORK / "main")
    main_sha = rev(main_repo)
    main_tree = rev(main_repo, "HEAD^{tree}")
    branch = f"feature/plan-v32-ops-security-current-{main_sha[:12]}"
    REPORT["base"] = {"commit": main_sha, "tree": main_tree}
    REPORT["branch"] = branch

    exists = run(
        ["git", "ls-remote", "--exit-code", "--heads", REMOTE, f"refs/heads/{branch}"],
        check=False,
        timeout=120,
    ).returncode == 0
    if exists:
        pulls = gh(
            f"repos/{REPOSITORY}/pulls?state=open&head={OWNER}:{branch}&per_page=100"
        ) or []
        REPORT["status"] = "existing-current-main-candidate"
        if pulls:
            REPORT["pull_request"] = int(pulls[0]["number"])
            request_reviewers(int(pulls[0]["number"]))
        update_issue()
        emit()
        return 0

    driver = clone(f"refs/heads/{OPS_BRANCH}", WORK / "driver")
    controller = clone(CONTROLLER_COMMIT, WORK / "controller")
    target = clone(main_sha, WORK / "target", branch=branch)
    materializer = driver / "tools/materialize_plan_v32_closure.py"
    if not materializer.is_file():
        raise RuntimeError("current ops branch lacks materialize_plan_v32_closure.py")

    run(
        [
            "python3",
            str(materializer),
            "--root",
            str(target),
            "--controller",
            str(controller),
        ],
        cwd=target,
        timeout=2400,
    )
    if not run(["git", "status", "--porcelain"], cwd=target).stdout.strip():
        REPORT["status"] = "no-delta-from-current-main"
        update_issue()
        emit()
        return 0

    verify(target)
    run(["git", "config", "user.name", "Plan v3.2 current-main materializer"], cwd=target)
    run(
        ["git", "config", "user.email", "102159240+ProfHepta@users.noreply.github.com"],
        cwd=target,
    )
    run(["git", "add", "--all"], cwd=target)
    changed = run(["git", "diff", "--cached", "--name-only"], cwd=target).stdout.splitlines()
    workflows = [name for name in changed if name.startswith(".github/workflows/")]
    if workflows:
        raise RuntimeError("materializer attempted to alter workflow definitions: " + repr(workflows))
    run(["git", "diff", "--cached", "--check"], cwd=target)
    run(
        [
            "git",
            "commit",
            "-m",
            "plan-v3.2: materialize current-main operations and security candidates",
            "-m",
            "Materialize fail-closed remote MAC, Unix transport, durability model, denominator review, PostgreSQL and CockroachDB fault/recovery, capacity/endurance and cutover state-machine source contracts on the exact current main. Preserve every acceptance, compatibility, production, public-online, cutover, replacement and retirement claim as false.",
        ],
        cwd=target,
    )
    head = rev(target)
    tree = rev(target, "HEAD^{tree}")
    run(["git", "push", "origin", f"HEAD:refs/heads/{branch}"], cwd=target, timeout=900)

    body_file = WORK / "body.md"
    body_file.write_text(
        f'''## Plan v3.2 current-main bounded operations/security candidate

```text
repository           {REPOSITORY}
base                  main@{main_sha}
base tree             {main_tree}
head branch           {branch}
head commit           {head}
head tree             {tree}
history rewritten     false
administrator bypass  false
```

The complete Rust workspace, strict Clippy, control-plane suite and plan,
schema and status checks passed before publication. This remains Draft.

```text
accepted evidence = false
independently accepted = false
complete Nakama compatibility = false
production-ready = false
public-online = false
cutover authorized = false
replacement authorized = false
Nakama retired = false
```
''',
        encoding="utf-8",
    )
    created = run(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            REPOSITORY,
            "--base",
            "main",
            "--head",
            branch,
            "--draft",
            "--title",
            "[Plan v3.2][Ops/Security] Current-main durability and recovery wave",
            "--body-file",
            str(body_file),
        ],
        timeout=180,
    )
    match = re.search(r"/(\d+)\s*$", created.stdout.strip())
    number = int(match.group(1)) if match else None
    if number is not None:
        request_reviewers(number)
    REPORT.update(
        {
            "status": "published-draft-candidate",
            "head": head,
            "tree": tree,
            "pull_request": number,
            "changed_file_count": len(changed),
        }
    )
    update_issue()
    emit()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        REPORT["status"] = "failed"
        REPORT["errors"].append(str(exc))
        try:
            update_issue()
        except Exception as nested:
            REPORT["errors"].append(f"issue update: {nested}")
        emit()
        raise
