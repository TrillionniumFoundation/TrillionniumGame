#!/usr/bin/env python3
"""Reconstruct and independently validate the complete branch inventory log artifact."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EMITTER_PATH = ROOT / "scripts/emit-actions-log-artifact.py"
ACTIVE_BRANCHES_PATH = ROOT / "docs/governance/ACTIVE_BRANCHES.json"
WORKFLOW_NAME = "branch-inventory"
WORKFLOW_PATH = ".github/workflows/branch-inventory.yml"
PRODUCER_JOB_NAME = "exact-ref-inventory"
EXPECTED_ARCHIVE_MEMBERS = {"branch-inventory.json", "SHA256SUMS"}
SHA40 = re.compile(r"^[a-f0-9]{40}$")
RUN_ID = re.compile(r"^[1-9][0-9]*$")
REDIRECT_CODES = {301, 302, 303, 307, 308}


class VerificationError(RuntimeError):
    """Raised when a retained inventory cannot be proven exact."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise VerificationError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


EMITTER = load_module(EMITTER_PATH, "trillionnium_branch_inventory_emitter")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def git(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if check and completed.returncode != 0:
        raise VerificationError(
            f"git {' '.join(arguments)} failed: {completed.stderr.strip()}"
        )
    return completed


def reachable(ancestor: str, descendant: str) -> bool:
    return (
        git(
            "merge-base",
            "--is-ancestor",
            ancestor,
            descendant,
            check=False,
        ).returncode
        == 0
    )


def api_base(repository: str) -> str:
    require(
        repository == "TrillionniumFoundation/TrillionniumGame",
        "unexpected repository",
    )
    owner, name = repository.split("/", 1)
    return f"https://api.github.com/repos/{owner}/{name}"


def github_request(
    token: str,
    url: str,
    *,
    accept: str = "application/vnd.github+json",
) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "Accept": accept,
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "trillionnium-branch-inventory-verifier/1",
        },
    )


def request_json(token: str, url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(github_request(token, url), timeout=60) as response:
            data = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:1000]
        raise VerificationError(
            f"GitHub API request failed: HTTP {error.code}: {detail}"
        ) from error
    except urllib.error.URLError as error:
        raise VerificationError(f"GitHub API request failed: {error}") from error
    try:
        value = json.loads(data)
    except json.JSONDecodeError as error:
        raise VerificationError("GitHub API response is not JSON") from error
    require(isinstance(value, dict), "GitHub API response must be an object")
    return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> None:
        del request, file_pointer, code, message, headers, new_url
        return None


def validate_signed_location(location: str) -> urllib.request.Request:
    parsed = urllib.parse.urlsplit(location)
    require(
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment,
        "GitHub job-log redirect is not a safe HTTPS URL",
    )
    return urllib.request.Request(
        location,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": "trillionnium-branch-inventory-verifier/1",
        },
    )


def request_job_log(token: str, repository: str, job_id: int) -> bytes:
    url = f"{api_base(repository)}/actions/jobs/{job_id}/logs"
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(github_request(token, url), timeout=60) as response:
            data = response.read()
            require(data, "GitHub job log is empty")
            return data
    except urllib.error.HTTPError as error:
        if error.code not in REDIRECT_CODES:
            detail = error.read().decode("utf-8", "replace")[:1000]
            raise VerificationError(
                f"GitHub job-log request failed: HTTP {error.code}: {detail}"
            ) from error
        location = error.headers.get("Location")
        require(
            isinstance(location, str) and location,
            "GitHub job-log redirect has no Location header",
        )
        request = validate_signed_location(location)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
        except urllib.error.HTTPError as download_error:
            detail = download_error.read().decode("utf-8", "replace")[:1000]
            raise VerificationError(
                f"GitHub signed job-log download failed: HTTP "
                f"{download_error.code}: {detail}"
            ) from download_error
        except urllib.error.URLError as download_error:
            raise VerificationError(
                f"GitHub signed job-log download failed: {download_error}"
            ) from download_error
        require(data, "GitHub signed job log is empty")
        return data
    except urllib.error.URLError as error:
        raise VerificationError(f"GitHub job-log request failed: {error}") from error


def fetch_all_jobs(
    token: str,
    repository: str,
    run_id: str,
    run_attempt: str,
) -> list[dict[str, Any]]:
    base = (
        f"{api_base(repository)}/actions/runs/{run_id}/attempts/"
        f"{run_attempt}/jobs?per_page=100"
    )
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = request_json(token, f"{base}&page={page}")
        page_rows = payload.get("jobs")
        require(isinstance(page_rows, list), "workflow jobs response is malformed")
        require(
            all(isinstance(row, dict) for row in page_rows),
            "workflow jobs contain a non-object",
        )
        rows.extend(page_rows)
        if len(page_rows) < 100:
            break
        page += 1
        require(page <= 10, "workflow jobs pagination exceeded bound")
    require(rows, "workflow run has zero jobs")
    return rows


def validate_run(
    run: dict[str, Any],
    *,
    repository: str,
    head_sha: str,
    run_id: str,
    run_attempt: str,
) -> None:
    require(str(run.get("id")) == run_id, "workflow run ID mismatch")
    repository_value = run.get("repository")
    require(isinstance(repository_value, dict), "workflow repository identity missing")
    require(
        repository_value.get("full_name") == repository,
        "workflow repository mismatch",
    )
    require(run.get("head_sha") == head_sha, "workflow head SHA mismatch")
    require(str(run.get("run_attempt")) == run_attempt, "workflow run attempt mismatch")
    require(run.get("event") == "pull_request", "workflow event must be pull_request")
    require(run.get("name") == WORKFLOW_NAME, "workflow name mismatch")
    require(run.get("path") == WORKFLOW_PATH, "workflow path mismatch")
    require(run.get("status") in {"in_progress", "completed"}, "workflow status invalid")
    require(run.get("conclusion") in {None, "success"}, "workflow run is not successful")


def validate_producer_job(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    matches = [row for row in jobs if row.get("name") == PRODUCER_JOB_NAME]
    require(len(matches) == 1, "expected exactly one inventory producer job")
    job = matches[0]
    require(job.get("status") == "completed", "inventory producer is not completed")
    require(job.get("conclusion") == "success", "inventory producer did not succeed")
    runner_id = job.get("runner_id")
    runner_name = job.get("runner_name")
    require(
        isinstance(runner_id, int) and not isinstance(runner_id, bool) and runner_id > 0,
        "inventory producer has no assigned runner",
    )
    require(
        isinstance(runner_name, str) and runner_name,
        "inventory producer runner name missing",
    )
    steps = job.get("steps")
    require(isinstance(steps, list) and steps, "inventory producer has zero steps")
    expected = {
        "Fetch exact candidate without external actions",
        "Fetch all branch heads without mutation",
        "Generate non-destructive inventory",
        "Seal complete branch inventory in retained job log",
    }
    by_name = {
        step.get("name"): step
        for step in steps
        if isinstance(step, dict) and isinstance(step.get("name"), str)
    }
    require(expected <= set(by_name), "inventory producer required step is missing")
    for name in expected:
        step = by_name[name]
        require(step.get("status") == "completed", f"producer step not completed: {name}")
        require(step.get("conclusion") == "success", f"producer step failed: {name}")
    return job


def read_archive(archive: bytes) -> dict[str, bytes]:
    require(archive.startswith(b"\x1f\x8b"), "inventory artifact is not gzip")
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            members = tar.getmembers()
            names = {member.name for member in members}
            require(
                names == EXPECTED_ARCHIVE_MEMBERS,
                f"inventory archive member set mismatch: {sorted(names)}",
            )
            result: dict[str, bytes] = {}
            for member in members:
                require(member.isfile(), f"non-regular archive member: {member.name}")
                require(
                    not member.name.startswith("/")
                    and ".." not in Path(member.name).parts
                    and "\\" not in member.name,
                    f"unsafe archive path: {member.name}",
                )
                extracted = tar.extractfile(member)
                require(extracted is not None, f"cannot extract archive member: {member.name}")
                data = extracted.read()
                require(data, f"archive member is empty: {member.name}")
                result[member.name] = data
            return result
    except (tarfile.TarError, OSError, EOFError) as error:
        raise VerificationError(f"invalid inventory archive: {error}") from error


def load_active_registry() -> tuple[dict[str, dict[str, Any]], str]:
    raw = ACTIVE_BRANCHES_PATH.read_bytes()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise VerificationError("active branch registry is invalid JSON") from error
    require(isinstance(value, dict), "active branch registry must be an object")
    require(
        value.get("schema") == "trillionnium.active-branches.v1",
        "active branch registry schema mismatch",
    )
    rows = value.get("active_branches")
    require(isinstance(rows, list) and rows, "active branch registry is empty")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        require(isinstance(row, dict), "active branch row must be an object")
        name = row.get("name")
        require(isinstance(name, str) and name, "active branch name missing")
        require(name not in result, f"duplicate active branch: {name}")
        result[name] = row
    require("main" in result, "main is absent from active branch registry")
    return result, hashlib.sha256(raw).hexdigest()


def expected_disposition(
    *,
    name: str,
    is_active: bool,
    commit_reachable_from_main: bool,
    duplicate_tip: bool,
) -> tuple[str, str]:
    if name == "main":
        return "keep", "canonical protected default branch"
    if is_active:
        return "keep-active", "declared current integration or gap-closure line"
    if name.startswith("archive/"):
        return "keep-archive", "explicit immutable archive namespace"
    if name == "integration/all-branches-main-v1":
        return (
            "preserve-pending-review",
            "historical consolidation authority retains unique audit value",
        )
    if not commit_reachable_from_main:
        return "preserve-nonancestor", "tip is not reachable from current main"
    if duplicate_tip:
        return (
            "delete-candidate-after-review",
            "tip is reachable from main and shared by multiple branch names",
        )
    return (
        "archive-or-delete-after-review",
        "tip is reachable from main but has a unique branch name",
    )


def remote_refs() -> list[tuple[str, str, str]]:
    output = git(
        "for-each-ref",
        "--format=%(refname:strip=3)\t%(objectname)",
        "refs/remotes/origin",
    ).stdout
    rows: list[tuple[str, str, str]] = []
    for line in output.splitlines():
        if not line:
            continue
        name, commit = line.split("\t", 1)
        if name == "HEAD":
            continue
        require(SHA40.fullmatch(commit) is not None, f"invalid commit for branch {name}")
        tree = git("rev-parse", f"{commit}^{{tree}}").stdout.strip()
        require(SHA40.fullmatch(tree) is not None, f"invalid tree for branch {name}")
        rows.append((name, commit, tree))
    require(rows, "local remote-ref inventory is empty")
    return sorted(rows)


def validate_inventory(
    inventory_bytes: bytes,
    *,
    repository: str,
    head_sha: str,
) -> dict[str, Any]:
    try:
        inventory = json.loads(inventory_bytes)
    except json.JSONDecodeError as error:
        raise VerificationError("branch inventory is invalid JSON") from error
    require(isinstance(inventory, dict), "branch inventory must be an object")
    require(
        inventory.get("schema") == "trillionnium.branch-inventory.v2",
        "branch inventory schema mismatch",
    )
    require(inventory.get("project_id") == "trillionnium-game", "project mismatch")
    require(inventory.get("repository") == repository, "repository mismatch")

    head_tree = git("rev-parse", f"{head_sha}^{{tree}}").stdout.strip()
    candidate = inventory.get("candidate")
    require(isinstance(candidate, dict), "candidate identity missing")
    require(candidate.get("commit") == head_sha, "inventory candidate commit mismatch")
    require(candidate.get("tree") == head_tree, "inventory candidate tree mismatch")

    main_commit = git("rev-parse", "refs/remotes/origin/main").stdout.strip()
    main_tree = git("rev-parse", f"{main_commit}^{{tree}}").stdout.strip()
    main = inventory.get("main")
    require(isinstance(main, dict), "main identity missing")
    require(main.get("commit") == main_commit, "inventory main commit mismatch")
    require(main.get("tree") == main_tree, "inventory main tree mismatch")

    active, active_sha256 = load_active_registry()
    active_record = inventory.get("active_branch_registry")
    require(isinstance(active_record, dict), "active registry identity missing")
    require(
        active_record.get("path") == "docs/governance/ACTIVE_BRANCHES.json",
        "active registry path mismatch",
    )
    require(active_record.get("sha256") == active_sha256, "active registry digest mismatch")
    require(active_record.get("branch_count") == len(active), "active branch count mismatch")
    require(active_record.get("branches") == sorted(active), "active branch names mismatch")

    refs = remote_refs()
    observed = inventory.get("branches")
    require(isinstance(observed, list) and observed, "inventory branch rows are empty")
    require(
        inventory.get("branch_count") == len(refs) == len(observed),
        "branch count mismatch",
    )
    by_name = {
        row.get("name"): row
        for row in observed
        if isinstance(row, dict) and isinstance(row.get("name"), str)
    }
    require(len(by_name) == len(observed), "duplicate or malformed inventory branch row")
    ref_names = {name for name, _, _ in refs}
    require(set(by_name) == ref_names, "inventory branch-name set mismatch")
    require(set(active) <= ref_names, "one or more active branches are missing")

    names_by_tip: dict[str, list[str]] = defaultdict(list)
    names_by_tree: dict[str, list[str]] = defaultdict(list)
    for name, commit, tree in refs:
        names_by_tip[commit].append(name)
        names_by_tree[tree].append(name)

    expected_rows: list[dict[str, Any]] = []
    disposition_counts: dict[str, int] = defaultdict(int)
    for name, commit, tree in refs:
        commit_reachable = reachable(commit, main_commit)
        main_reachable = reachable(main_commit, commit)
        disposition, reason = expected_disposition(
            name=name,
            is_active=name in active,
            commit_reachable_from_main=commit_reachable,
            duplicate_tip=len(names_by_tip[commit]) > 1,
        )
        expected = {
            "name": name,
            "commit": commit,
            "tree": tree,
            "active_role": active[name]["role"] if name in active else None,
            "pull_request": active[name]["pull_request"] if name in active else None,
            "commit_reachable_from_main": commit_reachable,
            "main_reachable_from_commit": main_reachable,
            "same_tip_branches": sorted(names_by_tip[commit]),
            "same_tree_branches": sorted(names_by_tree[tree]),
            "disposition": disposition,
            "reason": reason,
        }
        require(by_name[name] == expected, f"inventory row mismatch: {name}")
        expected_rows.append(expected)
        disposition_counts[disposition] += 1

    require(
        observed == expected_rows,
        "inventory rows are not canonical name-sorted exact observations",
    )
    require(
        inventory.get("rows_sha256")
        == hashlib.sha256(canonical_bytes(expected_rows)).hexdigest(),
        "inventory rows digest mismatch",
    )
    require(
        inventory.get("unique_tip_count") == len(names_by_tip),
        "unique tip count mismatch",
    )
    require(
        inventory.get("unique_tree_count") == len(names_by_tree),
        "unique tree count mismatch",
    )
    require(
        inventory.get("disposition_counts")
        == dict(sorted(disposition_counts.items())),
        "disposition counts mismatch",
    )

    policy = inventory.get("policy")
    require(isinstance(policy, dict), "inventory policy missing")
    require(policy.get("deletion_executed") is False, "inventory claims deletion")
    require(policy.get("history_rewritten") is False, "inventory claims history rewrite")
    require(
        policy.get("independent_review_required") is True,
        "inventory omits independent review",
    )
    require(
        policy.get("before_after_manifests_required") is True,
        "inventory omits before/after manifests",
    )
    require(policy.get("nonancestor_may_be_deleted") is False, "nonancestor deletion enabled")
    require(policy.get("active_branch_may_be_deleted") is False, "active deletion enabled")

    claims = inventory.get("claims")
    require(isinstance(claims, dict), "inventory claims missing")
    require(claims.get("inventory_generated") is True, "inventory generation claim missing")
    for key in (
        "inventory_retained_and_verified",
        "disposition_reviewed",
        "cleanup_complete",
        "sg0_complete",
    ):
        require(claims.get(key) is False, f"premature inventory claim: {key}")

    return {
        "head_tree": head_tree,
        "main_commit": main_commit,
        "main_tree": main_tree,
        "branch_count": len(refs),
        "active_branch_count": len(active),
        "unique_tip_count": len(names_by_tip),
        "unique_tree_count": len(names_by_tree),
        "rows_sha256": inventory["rows_sha256"],
        "disposition_counts": dict(sorted(disposition_counts.items())),
    }


def verify(
    *,
    token: str,
    repository: str,
    head_sha: str,
    run_id: str,
    run_attempt: str,
    output: Path,
) -> dict[str, Any]:
    require(SHA40.fullmatch(head_sha) is not None, "head SHA must be 40 lowercase hex")
    require(RUN_ID.fullmatch(run_id) is not None, "run ID must be positive decimal")
    require(RUN_ID.fullmatch(run_attempt) is not None, "run attempt must be positive decimal")

    checked_out = git("rev-parse", "HEAD").stdout.strip()
    require(checked_out == head_sha, "verifier checkout is not the exact head")
    run = request_json(token, f"{api_base(repository)}/actions/runs/{run_id}")
    validate_run(
        run,
        repository=repository,
        head_sha=head_sha,
        run_id=run_id,
        run_attempt=run_attempt,
    )
    jobs = fetch_all_jobs(token, repository, run_id, run_attempt)
    producer = validate_producer_job(jobs)
    job_id = producer.get("id")
    require(
        isinstance(job_id, int) and not isinstance(job_id, bool) and job_id > 0,
        "inventory producer job ID missing",
    )
    log_bytes = request_job_log(token, repository, job_id)
    try:
        log_text = log_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise VerificationError("inventory producer log is not UTF-8") from error
    archive, envelope = EMITTER.parse(log_text)
    expected_name = f"branch-inventory-{head_sha}-{run_id}-{run_attempt}"
    require(envelope.get("name") == expected_name, "inventory envelope name mismatch")
    require(
        envelope.get("log_style") == EMITTER.GITHUB_LOG_STYLE,
        "inventory was not reconstructed from a retained GitHub job log",
    )

    members = read_archive(archive)
    inventory_bytes = members["branch-inventory.json"]
    expected_line = (
        hashlib.sha256(inventory_bytes).hexdigest()
        + "  branch-inventory.json\n"
    ).encode("ascii")
    require(
        members["SHA256SUMS"] == expected_line,
        "inventory SHA256SUMS does not match exact inventory bytes",
    )
    observation = validate_inventory(
        inventory_bytes,
        repository=repository,
        head_sha=head_sha,
    )
    archive_sha256 = hashlib.sha256(archive).hexdigest()
    require(
        envelope.get("sha256") == archive_sha256,
        "envelope/archive digest mismatch",
    )
    summary = {
        "schema": "trillionnium.branch-inventory-retained-log-verification.v1",
        "repository": repository,
        "head_sha": head_sha,
        "head_tree": observation["head_tree"],
        "run_id": run_id,
        "run_attempt": run_attempt,
        "workflow_name": WORKFLOW_NAME,
        "workflow_path": WORKFLOW_PATH,
        "producer_job_id": job_id,
        "producer_runner_id": producer["runner_id"],
        "producer_runner_name": producer["runner_name"],
        "archive_name": expected_name,
        "archive_sha256": archive_sha256,
        "archive_size_bytes": len(archive),
        "inventory_sha256": hashlib.sha256(inventory_bytes).hexdigest(),
        "source_log_style": envelope["log_style"],
        "source_begin_line": envelope["source_begin_line"],
        "source_end_line": envelope["source_end_line"],
        **observation,
        "complete_before_state_retained": True,
        "live_remote_ref_set_reverified": True,
        "deletion_executed": False,
        "history_rewritten": False,
        "disposition_reviewed": False,
        "cleanup_complete": False,
        "sg0_complete": False,
        "claim_boundary": (
            "This verifies one exact retained before-state only. Independent "
            "disposition review and a verified after-state remain required "
            "before branch cleanup can close."
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("branch inventory verification failed: missing GITHUB_TOKEN", file=sys.stderr)
        return 1
    try:
        summary = verify(
            token=token,
            repository=arguments.repository,
            head_sha=arguments.head_sha,
            run_id=arguments.run_id,
            run_attempt=arguments.run_attempt,
            output=arguments.output,
        )
        print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
        return 0
    except (OSError, json.JSONDecodeError, VerificationError, EMITTER.EnvelopeError) as error:
        print(f"branch inventory verification failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
