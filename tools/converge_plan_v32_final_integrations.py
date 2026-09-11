#!/usr/bin/env python3
"""Guardedly converge retained broad Plan v3.2 integrations on the live stack."""
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
WORK = Path(tempfile.mkdtemp(prefix="plan-v32-final-integration-"))
REVIEWERS = ["Franksudoman", "Tomasrgbsf"]
REPORT: dict[str, Any] = {
    "schema": "trillionnium.plan-v32-final-integration-convergence.v1",
    "repository": REPOSITORY,
    "run_id": os.environ.get("GITHUB_RUN_ID"),
    "history_rewritten": False,
    "administrator_bypass": False,
    "claim_promotion_allowed": False,
    "attempts": [],
    "errors": [],
}


def run(
    args: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    timeout: int | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=cwd,
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
            f"stdout:\n{result.stdout[-12000:]}\n"
            f"stderr:\n{result.stderr[-12000:]}"
        )
    return result


def gh(endpoint: str, *, method: str = "GET", payload: Any | None = None) -> Any:
    args = ["gh", "api", endpoint, "-X", method]
    result = run(
        args + (["--input", "-"] if payload is not None else []),
        timeout=180,
        input_text=json.dumps(payload) if payload is not None else None,
    )
    return json.loads(result.stdout) if result.stdout.strip() else None


def branch_exists(branch: str) -> bool:
    return (
        run(
            ["git", "ls-remote", "--exit-code", "--heads", REMOTE, f"refs/heads/{branch}"],
            check=False,
            timeout=120,
        ).returncode
        == 0
    )


def rev(repo: Path, expression: str = "HEAD") -> str:
    return run(["git", "rev-parse", expression], cwd=repo).stdout.strip()


def resolve_base() -> tuple[str, str]:
    deadline = time.monotonic() + 3 * 60 * 60
    prefixes = (
        "feature/plan-v32-competition-core-converged-",
        "feature/plan-v32-storage-public-api-converged-",
    )
    while time.monotonic() < deadline:
        pulls = gh(f"repos/{REPOSITORY}/pulls?state=open&per_page=100") or []
        for prefix in prefixes:
            candidates = [
                item for item in pulls if (item.get("head") or {}).get("ref", "").startswith(prefix)
            ]
            if candidates:
                selected = sorted(candidates, key=lambda item: item.get("created_at") or "")[-1]
                return selected["head"]["ref"], selected["head"]["sha"]
        surface = next((item for item in pulls if int(item.get("number", 0)) == 185), None)
        if surface:
            # Give the focused convergence workflow time to publish a later stack.
            time.sleep(30)
            continue
        break
    pull = gh(f"repos/{REPOSITORY}/pulls/185")
    if pull.get("state") == "open":
        return pull["head"]["ref"], pull["head"]["sha"]
    main = gh(f"repos/{REPOSITORY}/git/ref/heads/main")
    return "main", main["object"]["sha"]


def clone_pair(base_branch: str, source_branch: str, destination: Path) -> Path:
    run(["git", "init", str(destination)])
    run(["git", "-C", str(destination), "remote", "add", "origin", REMOTE])
    for branch in (base_branch, source_branch):
        run(
            [
                "git",
                "-C",
                str(destination),
                "fetch",
                "--no-tags",
                "origin",
                f"refs/heads/{branch}:refs/remotes/origin/{branch}",
            ],
            timeout=600,
        )
    return destination


def verify_claim_boundary(repo: Path, base_ref: str) -> None:
    # Existing repository checkers remain authoritative.  Add a narrow textual
    # guard against broad candidate branches silently promoting global claims.
    diff = run(["git", "diff", "--unified=0", base_ref, "--", "*.json", "*.md"], cwd=repo).stdout
    forbidden_additions = (
        r"^\+.*\bproduction[-_ ]?ready\b[^\n]*(?:true|yes|complete)",
        r"^\+.*\bpublic[-_ ]?online\b[^\n]*(?:true|yes|complete)",
        r"^\+.*\bcutover[-_ ]?authorized\b[^\n]*(?:true|yes|complete)",
        r"^\+.*\bnakama[-_ ]?retired\b[^\n]*(?:true|yes|complete)",
        r"^\+.*\baccepted[-_ ]?evidence\b[^\n]*(?:true|yes|complete)",
        r"^\+.*\ball[-_ ]?gaps[-_ ]?closed\b[^\n]*(?:true|yes|complete)",
    )
    hits = []
    for pattern in forbidden_additions:
        hits.extend(re.findall(pattern, diff, flags=re.IGNORECASE | re.MULTILINE))
    if hits:
        raise RuntimeError("broad candidate attempts unsupported global claim promotion")


def verify(repo: Path, base_ref: str) -> None:
    verify_claim_boundary(repo, base_ref)
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


def attempt_source(base_branch: str, base_sha: str, source_branch: str) -> dict[str, Any]:
    attempt: dict[str, Any] = {
        "base_branch": base_branch,
        "base_sha": base_sha,
        "source_branch": source_branch,
    }
    if not branch_exists(source_branch):
        attempt["status"] = "source-absent"
        return attempt
    slug = source_branch.replace("/", "-").replace("_", "-")
    head_branch = f"integration/plan-v32-guarded-{slug[-40:]}-{base_sha[:12]}"
    attempt["head_branch"] = head_branch
    if branch_exists(head_branch):
        pulls = gh(
            f"repos/{REPOSITORY}/pulls?state=open&head={OWNER}:{head_branch}&per_page=100"
        ) or []
        attempt["status"] = "existing-guarded-branch"
        if pulls:
            attempt["pull_request"] = int(pulls[0]["number"])
            attempt["head_sha"] = pulls[0]["head"]["sha"]
            request_reviewers(int(pulls[0]["number"]))
        return attempt

    repo = clone_pair(base_branch, source_branch, WORK / slug[-48:])
    base_ref = f"origin/{base_branch}"
    source_ref = f"origin/{source_branch}"
    exact_base = rev(repo, base_ref)
    source_sha = rev(repo, source_ref)
    attempt["source_sha"] = source_sha
    if exact_base != base_sha:
        attempt["status"] = "base-moved"
        attempt["observed_base"] = exact_base
        return attempt
    if run(["git", "merge-base", "--is-ancestor", source_sha, base_sha], cwd=repo, check=False).returncode == 0:
        attempt["status"] = "source-contained"
        return attempt

    run(["git", "checkout", "-B", head_branch, base_sha], cwd=repo)
    run(["git", "config", "user.name", "Plan v3.2 guarded integration"], cwd=repo)
    run(
        ["git", "config", "user.email", "102159240+ProfHepta@users.noreply.github.com"],
        cwd=repo,
    )
    merged = run(
        ["git", "merge", "--no-ff", "--no-commit", source_ref],
        cwd=repo,
        check=False,
        timeout=900,
    )
    if merged.returncode != 0:
        run(["git", "merge", "--abort"], cwd=repo, check=False)
        attempt["status"] = "merge-conflict"
        attempt["diagnostic"] = (merged.stdout + merged.stderr)[-8000:]
        return attempt

    staged = run(["git", "diff", "--cached", "--name-only"], cwd=repo).stdout.splitlines()
    workflow_changes = [name for name in staged if name.startswith(".github/workflows/")]
    if workflow_changes:
        run(["git", "merge", "--abort"], cwd=repo, check=False)
        attempt["status"] = "rejected-workflow-import"
        attempt["workflow_changes"] = workflow_changes
        return attempt

    verify(repo, base_ref)
    run(["git", "add", "--all"], cwd=repo)
    run(["git", "diff", "--cached", "--check"], cwd=repo)
    run(
        [
            "git",
            "commit",
            "-m",
            "plan-v3.2: guarded convergence of retained broad integration",
            "-m",
            f"Merge {source_branch}@{source_sha} onto exact current stack {base_branch}@{base_sha} through an ordinary two-parent commit. Reject workflow import and unsupported global claim promotion. Preserve evidence, compatibility, production, public-online, cutover, replacement and retirement claims as false pending their own acceptance contracts.",
        ],
        cwd=repo,
    )
    head = rev(repo)
    tree = rev(repo, "HEAD^{tree}")
    parents = run(["git", "rev-list", "--parents", "-n", "1", "HEAD"], cwd=repo).stdout.split()
    if len(parents) != 3:
        raise RuntimeError("guarded integration is not an ordinary two-parent commit")
    run(["git", "push", "origin", f"HEAD:refs/heads/{head_branch}"], cwd=repo, timeout=900)

    body_file = WORK / "body.md"
    body_file.write_text(
        f'''## Guarded broad Plan v3.2 integration candidate

```text
repository            {REPOSITORY}
base branch           {base_branch}
base commit           {base_sha}
retained source       {source_branch}@{source_sha}
head branch           {head_branch}
head commit           {head}
head tree             {tree}
ordinary merge        true
workflow import       false
history rewritten     false
administrator bypass  false
```

The complete repository verification corpus passed before publication. This is
still a Draft; broad source convergence is not gap closure or accepted evidence.

```text
all gaps closed = false
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
            base_branch,
            "--head",
            head_branch,
            "--draft",
            "--title",
            "[Plan v3.2][Guarded Integration] Converge retained broad candidate",
            "--body-file",
            str(body_file),
        ],
        timeout=180,
    )
    match = re.search(r"/(\d+)\s*$", created.stdout.strip())
    number = int(match.group(1)) if match else None
    if number is not None:
        request_reviewers(number)
    attempt.update(
        {
            "status": "published-guarded-draft",
            "head_sha": head,
            "head_tree": tree,
            "pull_request": number,
            "changed_file_count": len(staged),
        }
    )
    return attempt


def update_issue() -> None:
    marker = "<!-- plan-v32-final-integration-convergence -->"
    body = (
        marker
        + "\n## Plan v3.2 guarded final-integration readback\n\n```json\n"
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
            handle.write("## Plan v3.2 guarded final integration\n\n```json\n")
            handle.write(rendered)
            handle.write("\n```\n")


def main() -> int:
    if not TOKEN:
        raise RuntimeError("GH_TOKEN is required")
    run(["gh", "auth", "setup-git"], timeout=120)
    base_branch, base_sha = resolve_base()
    REPORT["base"] = {"branch": base_branch, "sha": base_sha}
    sources = (
        "integration/plan-v32-final-2026-09-09",
        "integration/plan-v32-truth-convergence-2026-09-09",
        "codex/v32-gap-implementation-2026-09-09",
    )
    for source in sources:
        try:
            attempt = attempt_source(base_branch, base_sha, source)
            REPORT["attempts"].append(attempt)
            if attempt.get("status") == "published-guarded-draft":
                break
        except Exception as error:
            REPORT["attempts"].append(
                {"source_branch": source, "status": "failed", "error": str(error)}
            )
            REPORT["errors"].append(f"{source}: {error}")
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
