#!/usr/bin/env python3
"""Converge retained Plan v3.2 source branches onto the current admitted stack.

Each candidate is merged with an ordinary two-parent merge, rejects imported
workflow definitions, runs the complete repository verification corpus, and is
published only as a Draft.  No acceptance or production claim is promoted.
"""
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
WORK = Path(tempfile.mkdtemp(prefix="plan-v32-convergence-"))
REVIEWERS = ["Franksudoman", "Tomasrgbsf"]
CONTROLLER_WORKFLOW = "plan-v32-full-closure-controller-20260911-v2.yml"
REPORT: dict[str, Any] = {
    "schema": "trillionnium.plan-v32-existing-candidate-convergence.v1",
    "repository": REPOSITORY,
    "run_id": os.environ.get("GITHUB_RUN_ID"),
    "history_rewritten": False,
    "administrator_bypass": False,
    "accepted_evidence_promoted": False,
    "production_claim_promoted": False,
    "candidates": [],
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
            raise RuntimeError(
                f"gh api failed ({result.returncode}): {endpoint}\n{result.stderr[-8000:]}"
            )
    return json.loads(result.stdout) if result.stdout.strip() else None


def wait_for_controller() -> None:
    deadline = time.monotonic() + 3 * 60 * 60
    while time.monotonic() < deadline:
        response = gh(
            f"repos/{REPOSITORY}/actions/workflows/{CONTROLLER_WORKFLOW}/runs"
            "?branch=ops/game-exact-source-snapshot-20260910&per_page=5"
        ) or {}
        runs = response.get("workflow_runs") or []
        relevant = [item for item in runs if str(item.get("id")) != os.environ.get("GITHUB_RUN_ID")]
        if not relevant:
            time.sleep(30)
            continue
        latest = relevant[0]
        REPORT["controller_run"] = {
            "id": latest.get("id"),
            "status": latest.get("status"),
            "conclusion": latest.get("conclusion"),
            "head_sha": latest.get("head_sha"),
        }
        if latest.get("status") == "completed":
            return
        time.sleep(30)
    REPORT["errors"].append("controller wait reached its bounded deadline")


def rev(repo: Path, expression: str = "HEAD") -> str:
    return run(["git", "rev-parse", expression], cwd=repo).stdout.strip()


def branch_exists(branch: str) -> bool:
    return (
        run(
            ["git", "ls-remote", "--exit-code", "--heads", REMOTE, f"refs/heads/{branch}"],
            check=False,
            timeout=120,
        ).returncode
        == 0
    )


def current_base() -> tuple[str, str]:
    pull = gh(f"repos/{REPOSITORY}/pulls/185")
    if pull.get("state") == "open":
        return pull["head"]["ref"], pull["head"]["sha"]
    main = gh(f"repos/{REPOSITORY}/git/ref/heads/main")
    return "main", main["object"]["sha"]


def clone_stack(base_branch: str, source_branch: str, destination: Path) -> Path:
    run(["git", "init", str(destination)])
    run(["git", "-C", str(destination), "remote", "add", "origin", REMOTE])
    run(
        [
            "git",
            "-C",
            str(destination),
            "fetch",
            "--no-tags",
            "origin",
            f"refs/heads/{base_branch}",
            f"refs/heads/{source_branch}",
        ],
        timeout=600,
    )
    run(["git", "-C", str(destination), "checkout", "--detach", "FETCH_HEAD"])
    # FETCH_HEAD has two entries; explicitly bind the base and source refs locally.
    base_sha = run(
        ["git", "-C", str(destination), "rev-parse", f"origin/{base_branch}"],
        check=False,
    )
    if base_sha.returncode != 0:
        run(
            [
                "git",
                "-C",
                str(destination),
                "fetch",
                "--no-tags",
                "origin",
                f"refs/heads/{base_branch}:refs/remotes/origin/{base_branch}",
            ],
            timeout=300,
        )
    source_sha = run(
        ["git", "-C", str(destination), "rev-parse", f"origin/{source_branch}"],
        check=False,
    )
    if source_sha.returncode != 0:
        run(
            [
                "git",
                "-C",
                str(destination),
                "fetch",
                "--no-tags",
                "origin",
                f"refs/heads/{source_branch}:refs/remotes/origin/{source_branch}",
            ],
            timeout=300,
        )
    return destination


def configure(repo: Path) -> None:
    run(["git", "config", "user.name", "Plan v3.2 candidate convergence"], cwd=repo)
    run(
        ["git", "config", "user.email", "102159240+ProfHepta@users.noreply.github.com"],
        cwd=repo,
    )


def verify(repo: Path) -> None:
    run(["git", "diff", "--check"], cwd=repo, timeout=120)
    run(["python3", "-m", "compileall", "-q", "scripts", "tests"], cwd=repo, timeout=600)
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
            run(["python3", script], cwd=repo, timeout=600)
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


def open_draft(
    *,
    base_branch: str,
    head_branch: str,
    title: str,
    body: str,
) -> int | None:
    body_file = WORK / (head_branch.rsplit("/", 1)[-1] + ".md")
    body_file.write_text(body, encoding="utf-8")
    created = run(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            REPOSITORY,
            "--base",
            base_branch,
            "--head",
            head_branch,
            "--draft",
            "--title",
            title,
            "--body-file",
            str(body_file),
        ],
        timeout=180,
    )
    match = re.search(r"/(\d+)\s*$", created.stdout.strip())
    number = int(match.group(1)) if match else None
    if number is not None:
        request_reviewers(number)
    return number


def converge(
    *,
    slug: str,
    source_branch: str,
    title: str,
    base_branch: str,
    base_sha: str,
) -> tuple[dict[str, Any], str, str]:
    result: dict[str, Any] = {
        "slug": slug,
        "source_branch": source_branch,
        "base_branch": base_branch,
        "base_sha": base_sha,
    }
    if not branch_exists(source_branch):
        result["status"] = "source-branch-absent"
        return result, base_branch, base_sha

    head_branch = f"feature/plan-v32-{slug}-converged-{base_sha[:12]}"
    result["head_branch"] = head_branch
    if branch_exists(head_branch):
        pulls = gh(
            f"repos/{REPOSITORY}/pulls?state=open&head={OWNER}:{head_branch}&per_page=100"
        ) or []
        result["status"] = "existing-converged-branch"
        if pulls:
            result["pull_request"] = int(pulls[0]["number"])
            request_reviewers(int(pulls[0]["number"]))
            result["head_sha"] = pulls[0]["head"]["sha"]
            return result, head_branch, pulls[0]["head"]["sha"]
        return result, base_branch, base_sha

    repo = clone_stack(base_branch, source_branch, WORK / slug)
    base_ref = f"origin/{base_branch}"
    source_ref = f"origin/{source_branch}"
    exact_base = rev(repo, base_ref)
    exact_source = rev(repo, source_ref)
    if exact_base != base_sha:
        result["status"] = "base-moved-before-convergence"
        result["observed_base"] = exact_base
        return result, base_branch, base_sha
    if run(["git", "merge-base", "--is-ancestor", exact_source, exact_base], cwd=repo, check=False).returncode == 0:
        result["status"] = "source-already-contained"
        return result, base_branch, base_sha

    run(["git", "checkout", "-B", head_branch, exact_base], cwd=repo)
    configure(repo)
    merge = run(
        [
            "git",
            "merge",
            "--no-ff",
            "--no-commit",
            source_ref,
        ],
        cwd=repo,
        check=False,
        timeout=600,
    )
    if merge.returncode != 0:
        run(["git", "merge", "--abort"], cwd=repo, check=False)
        result["status"] = "merge-conflict"
        result["source_sha"] = exact_source
        result["diagnostic"] = (merge.stdout + merge.stderr)[-6000:]
        return result, base_branch, base_sha

    staged = run(["git", "diff", "--cached", "--name-only"], cwd=repo).stdout.splitlines()
    workflow_changes = [name for name in staged if name.startswith(".github/workflows/")]
    if workflow_changes:
        run(["git", "merge", "--abort"], cwd=repo, check=False)
        result["status"] = "rejected-imported-workflows"
        result["workflow_changes"] = workflow_changes
        return result, base_branch, base_sha

    verify(repo)
    # Formatting performed by verification is part of the bounded merge tree.
    run(["git", "add", "--all"], cwd=repo)
    run(["git", "diff", "--cached", "--check"], cwd=repo)
    run(
        [
            "git",
            "commit",
            "-m",
            f"plan-v3.2: converge {slug} candidate on current stack",
            "-m",
            f"Merge retained source branch {source_branch} onto exact base {base_branch}@{base_sha} using an ordinary two-parent commit. Reject workflow-definition import and preserve all evidence, compatibility, production, public-online, cutover, replacement and retirement claims as false pending exact qualification and independent acceptance.",
        ],
        cwd=repo,
    )
    head = rev(repo)
    tree = rev(repo, "HEAD^{tree}")
    parents = run(["git", "rev-list", "--parents", "-n", "1", "HEAD"], cwd=repo).stdout.split()
    if len(parents) != 3:
        raise RuntimeError(f"{slug} convergence commit is not an ordinary two-parent merge")
    run(["git", "push", "origin", f"HEAD:refs/heads/{head_branch}"], cwd=repo, timeout=600)
    body = f'''## Plan v3.2 stacked bounded candidate

```text
repository           {REPOSITORY}
base branch          {base_branch}
base commit          {base_sha}
retained source      {source_branch}@{exact_source}
head branch          {head_branch}
head commit          {head}
head tree            {tree}
ordinary merge       true
history rewritten    false
administrator bypass false
```

The complete repository Rust workspace, strict Clippy, control-plane suite and
plan/schema/status checks passed before publication.  This remains a Draft and
provides bounded source/execution credit only.

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
'''
    number = open_draft(
        base_branch=base_branch,
        head_branch=head_branch,
        title=title,
        body=body,
    )
    result.update(
        {
            "status": "published-stacked-draft",
            "source_sha": exact_source,
            "head_sha": head,
            "head_tree": tree,
            "pull_request": number,
        }
    )
    return result, head_branch, head


def update_issue() -> None:
    marker = "<!-- plan-v32-existing-candidate-convergence -->"
    body = (
        marker
        + "\n## Existing Plan v3.2 candidate convergence\n\n```json\n"
        + json.dumps(REPORT, indent=2, sort_keys=True)
        + "\n```\n\nNo gap or product claim is promoted by this readback."
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
            handle.write("## Plan v3.2 existing-candidate convergence\n\n```json\n")
            handle.write(rendered)
            handle.write("\n```\n")


def main() -> int:
    if not TOKEN:
        raise RuntimeError("GH_TOKEN is required")
    run(["gh", "auth", "setup-git"], timeout=120)
    wait_for_controller()
    base_branch, base_sha = current_base()
    REPORT["initial_base"] = {"branch": base_branch, "sha": base_sha}

    specs = (
        (
            "storage-public-api",
            "feature/plan-v32-storage-public-api-20260909",
            "[Plan v3.2][Stack] Converge storage public API on current Surface 3",
        ),
        (
            "competition-core",
            "feature/plan-v32-competition-core-20260909",
            "[Plan v3.2][Stack] Converge competition core on current admitted stack",
        ),
    )
    for slug, source, title in specs:
        try:
            result, next_branch, next_sha = converge(
                slug=slug,
                source_branch=source,
                title=title,
                base_branch=base_branch,
                base_sha=base_sha,
            )
            REPORT["candidates"].append(result)
            if result.get("status") in {
                "published-stacked-draft",
                "existing-converged-branch",
            } and result.get("head_sha"):
                base_branch, base_sha = next_branch, next_sha
        except Exception as error:
            REPORT["candidates"].append(
                {"slug": slug, "source_branch": source, "status": "failed", "error": str(error)}
            )
            REPORT["errors"].append(f"{slug}: {error}")

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
