#!/usr/bin/env python3
"""Verify exact completed outbox evidence reconstructed from GitHub job logs."""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import io
import json
import os
import re
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EMITTER_PATH = ROOT / "scripts/emit-actions-log-artifact.py"
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024
SHA_LINE = re.compile(r"^(?P<sha>[0-9a-f]{64})  (?P<path>\./[^\r\n]+)$")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
PROFILES = ("postgresql", "cockroachdb")
WORKFLOW_NAME = "outbox-final-attempt-reaper"
WORKFLOW_PATH = ".github/workflows/outbox-final-attempt-reaper.yml"
SOURCE_JOB = "source-contract"
FINAL_JOB = "outbox-final-attempt-reaper"
EXPECTED_JOB_NAMES = {
    SOURCE_JOB,
    FINAL_JOB,
    *(f"live-profile ({profile})" for profile in PROFILES),
}


class VerificationError(ValueError):
    """Raised when remote identity or reconstructed evidence fails closed."""


def load_emitter() -> Any:
    spec = importlib.util.spec_from_file_location("emit_actions_log_artifact", EMITTER_PATH)
    if spec is None or spec.loader is None:
        raise VerificationError("cannot load actions-log artifact module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EMITTER = load_emitter()


def require_nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VerificationError(f"{label} must be a non-empty string")
    return value


def request_json(token: str, url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "trillionnium-outbox-log-verifier/2",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except (urllib.error.URLError, json.JSONDecodeError) as error:
        raise VerificationError(f"GitHub JSON request failed: {url}: {error}") from error
    if not isinstance(payload, dict):
        raise VerificationError(f"GitHub JSON response is not an object: {url}")
    return payload


def request_bytes(token: str, url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "trillionnium-outbox-log-verifier/2",
        },
    )
    last_error: Exception | None = None
    for attempt in range(20):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
            if not data:
                raise VerificationError(f"GitHub log response is empty: {url}")
            return data
        except urllib.error.HTTPError as error:
            last_error = error
            if error.code not in {404, 409} or attempt == 19:
                detail = error.read().decode("utf-8", "replace")
                raise VerificationError(
                    f"GitHub log request failed: {url}: HTTP {error.code}: {detail}"
                ) from error
        except urllib.error.URLError as error:
            last_error = error
            if attempt == 19:
                raise VerificationError(f"GitHub log request failed: {url}: {error}") from error
        time.sleep(2)
    raise VerificationError(f"GitHub log request failed: {url}: {last_error}")


def api_base(repository: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise VerificationError("repository is not canonical owner/name")
    return f"https://api.github.com/repos/{urllib.parse.quote(repository, safe='/')}"


def decode_contents(payload: dict[str, Any], label: str) -> bytes:
    if payload.get("type") != "file" or payload.get("encoding") != "base64":
        raise VerificationError(f"{label} is not a base64 repository file")
    content = payload.get("content")
    if not isinstance(content, str) or not content:
        raise VerificationError(f"{label} has no content")
    try:
        return base64.b64decode(content, validate=True)
    except ValueError as error:
        raise VerificationError(f"{label} contains invalid base64") from error


def git_blob_sha1(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data, usedforsecurity=False).hexdigest()


def fetch_exact_file(
    token: str, repository: str, head_sha: str, path: str
) -> tuple[bytes, str]:
    encoded_path = urllib.parse.quote(path, safe="/")
    payload = request_json(
        token,
        f"{api_base(repository)}/contents/{encoded_path}?ref={head_sha}",
    )
    data = decode_contents(payload, path)
    declared = payload.get("sha")
    actual = git_blob_sha1(data)
    if not isinstance(declared, str) or declared != actual:
        raise VerificationError(
            f"{path} Git blob mismatch: declared={declared!r} actual={actual}"
        )
    return data, actual


def fetch_commit_tree(token: str, repository: str, head_sha: str) -> str:
    if SHA40.fullmatch(head_sha) is None:
        raise VerificationError("head SHA is not 40 lowercase hex")
    commit = request_json(token, f"{api_base(repository)}/git/commits/{head_sha}")
    if commit.get("sha") != head_sha:
        raise VerificationError("Git commit response is not bound to the requested head")
    tree = commit.get("tree")
    if not isinstance(tree, dict) or SHA40.fullmatch(str(tree.get("sha", ""))) is None:
        raise VerificationError("Git commit has no canonical tree SHA")
    return str(tree["sha"])


def fetch_workflow(token: str, repository: str) -> dict[str, Any]:
    workflow = request_json(
        token,
        f"{api_base(repository)}/actions/workflows/{urllib.parse.quote(WORKFLOW_PATH, safe='')}",
    )
    validate_workflow(workflow)
    return workflow


def validate_workflow(workflow: dict[str, Any]) -> None:
    workflow_id = workflow.get("id")
    if not isinstance(workflow_id, int) or workflow_id <= 0:
        raise VerificationError("workflow has no positive numeric ID")
    if workflow.get("name") != WORKFLOW_NAME:
        raise VerificationError(
            f"workflow name mismatch: {workflow.get('name')!r} != {WORKFLOW_NAME!r}"
        )
    if workflow.get("path") != WORKFLOW_PATH:
        raise VerificationError(
            f"workflow path mismatch: {workflow.get('path')!r} != {WORKFLOW_PATH!r}"
        )
    if workflow.get("state") != "active":
        raise VerificationError(f"workflow is not active: {workflow.get('state')!r}")


def fetch_profile_bindings(
    token: str, repository: str, head_sha: str
) -> dict[str, dict[str, str]]:
    lock_bytes, _ = fetch_exact_file(
        token, repository, head_sha, "migrations/MIGRATION_CHAIN.lock.json"
    )
    image_bytes, _ = fetch_exact_file(
        token, repository, head_sha, "config/database-test-images.json"
    )
    try:
        lock = json.loads(lock_bytes)
        images = json.loads(image_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VerificationError("migration lock or database image lock is invalid JSON") from error
    if lock.get("schema") != "trillionnium.migration-chain-lock.v1":
        raise VerificationError("migration lock schema mismatch")
    if images.get("schema") != "trillionnium.database-test-images.v1":
        raise VerificationError("database image lock schema mismatch")
    profiles = lock.get("profiles")
    image_profiles = images.get("profiles")
    if not isinstance(profiles, dict) or not isinstance(image_profiles, dict):
        raise VerificationError("profile lock mappings are malformed")

    result: dict[str, dict[str, str]] = {}
    for profile in PROFILES:
        row = profiles.get(profile)
        if not isinstance(row, dict):
            raise VerificationError(f"migration profile is absent: {profile}")
        ordered = row.get("ordered_files")
        if not isinstance(ordered, list) or len(ordered) != 1:
            raise VerificationError(
                f"{profile} must have exactly one locked foundation migration"
            )
        entry = ordered[0]
        if not isinstance(entry, dict):
            raise VerificationError(f"{profile} migration entry is malformed")
        path = require_nonempty_string(entry.get("path"), f"{profile} migration path")
        expected_directory = f"migrations/{profile}"
        if row.get("directory") != expected_directory:
            raise VerificationError(f"{profile} migration directory mismatch")
        if PurePosixPath(path).parent.as_posix() != expected_directory:
            raise VerificationError(f"{profile} migration path escapes its locked directory")
        locked_blob = require_nonempty_string(
            entry.get("git_blob_sha1"), f"{profile} migration blob"
        )
        if SHA40.fullmatch(locked_blob) is None:
            raise VerificationError(f"{profile} migration blob is not 40 lowercase hex")
        _, actual_blob = fetch_exact_file(token, repository, head_sha, path)
        if actual_blob != locked_blob:
            raise VerificationError(
                f"{profile} migration lock mismatch: {locked_blob} != {actual_blob}"
            )
        image_row = image_profiles.get(profile)
        if not isinstance(image_row, dict):
            raise VerificationError(f"database image profile is absent: {profile}")
        image = require_nonempty_string(image_row.get("image"), f"{profile} image")
        if "@sha256:" not in image:
            raise VerificationError(f"{profile} database image is not digest pinned")
        result[profile] = {
            "migration": path,
            "migration_blob_sha1": actual_blob,
            "image": image,
        }
    return result


def parse_env(data: bytes, label: str) -> dict[str, str]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise VerificationError(f"{label} is not UTF-8") from error
    values: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), 1):
        if not line or "=" not in line:
            raise VerificationError(f"{label}:{number}: malformed environment line")
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key):
            raise VerificationError(f"{label}:{number}: invalid key {key!r}")
        if key in values:
            raise VerificationError(f"{label}:{number}: duplicate key {key!r}")
        values[key] = value
    if not values:
        raise VerificationError(f"{label} is empty")
    return values


def normalized_member_name(value: str) -> str | None:
    path = PurePosixPath(value)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise VerificationError(f"unsafe tar member path: {value!r}")
    parts = [part for part in path.parts if part not in {"", "."}]
    if not parts:
        return None
    return "/".join(parts)


def archive_files(data: bytes) -> dict[str, bytes]:
    if not data or len(data) > MAX_ARCHIVE_BYTES:
        raise VerificationError(f"archive size is outside the bound: {len(data)} bytes")
    files: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            for member in archive.getmembers():
                name = normalized_member_name(member.name)
                if name is None or member.isdir():
                    continue
                if not member.isfile():
                    raise VerificationError(
                        f"non-regular tar member is forbidden: {member.name!r}"
                    )
                if name in files:
                    raise VerificationError(f"duplicate tar member: {name}")
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise VerificationError(f"cannot read tar member: {name}")
                payload = extracted.read(MAX_ARCHIVE_BYTES + 1)
                if len(payload) > MAX_ARCHIVE_BYTES:
                    raise VerificationError(f"tar member exceeds the archive bound: {name}")
                files[name] = payload
    except (tarfile.TarError, OSError) as error:
        raise VerificationError(f"invalid gzip tar archive: {error}") from error
    if not files:
        raise VerificationError("archive contains no regular files")
    return files


def verify_file_manifest(files: dict[str, bytes]) -> None:
    manifest_name = "files.sha256"
    if manifest_name not in files:
        raise VerificationError("archive is missing files.sha256")
    try:
        lines = files[manifest_name].decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise VerificationError("files.sha256 is not UTF-8") from error
    if not lines:
        raise VerificationError("files.sha256 is empty")
    expected: dict[str, str] = {}
    for number, line in enumerate(lines, 1):
        match = SHA_LINE.fullmatch(line)
        if match is None:
            raise VerificationError(f"files.sha256:{number}: malformed line")
        name = normalized_member_name(match.group("path"))
        if name is None or name == manifest_name or name in expected:
            raise VerificationError(f"files.sha256:{number}: invalid duplicate path")
        expected[name] = match.group("sha")
    actual_names = set(files) - {manifest_name}
    if set(expected) != actual_names:
        raise VerificationError(
            "files.sha256 path set mismatch: "
            f"missing={sorted(actual_names - set(expected))} "
            f"extra={sorted(set(expected) - actual_names)}"
        )
    for name, digest in expected.items():
        actual = hashlib.sha256(files[name]).hexdigest()
        if actual != digest:
            raise VerificationError(
                f"files.sha256 digest mismatch for {name}: expected={digest} actual={actual}"
            )


def require_env(values: dict[str, str], expected: dict[str, str], label: str) -> None:
    for key, value in expected.items():
        if values.get(key) != value:
            raise VerificationError(
                f"{label}: {key} mismatch: expected={value!r} actual={values.get(key)!r}"
            )


def validate_archive(
    data: bytes,
    *,
    repository: str,
    head_sha: str,
    head_tree: str,
    run_id: str,
    run_attempt: str,
    profile: str,
    binding: dict[str, str],
) -> dict[str, object]:
    if profile not in PROFILES:
        raise VerificationError(f"unsupported profile: {profile}")
    files = archive_files(data)
    verify_file_manifest(files)
    identity = parse_env(files.get("identity.env", b""), "identity.env")
    require_env(
        identity,
        {
            "repository": repository,
            "commit": head_sha,
            "tree": head_tree,
            "profile": profile,
            "image": binding["image"],
            "run_id": run_id,
            "run_attempt": run_attempt,
            "evidence_run_id": f"{run_id}-{run_attempt}-{profile}",
            "workflow": WORKFLOW_NAME,
            "workflow_path": WORKFLOW_PATH,
            "job_key": "live-profile",
            "job_name": f"live-profile ({profile})",
            "migration": binding["migration"],
            "migration_blob_sha1": binding["migration_blob_sha1"],
        },
        "identity.env",
    )
    result = parse_env(files.get("result.env", b""), "result.env")
    require_env(
        result,
        {
            "status": "passed",
            "profile": profile,
            "commit": head_sha,
            "tree": head_tree,
        },
        "result.env",
    )
    before = parse_env(
        files.get("crash-before-publish/result.env", b""),
        "crash-before-publish/result.env",
    )
    require_env(
        before,
        {
            "possible_lost_effect_declared": "true",
            "spool_effect_count": "0",
            "outbox_row_count": "1",
            "dead_letter_count": "1",
        },
        "crash-before-publish/result.env",
    )
    after = parse_env(
        files.get("crash-after-publish/result.env", b""),
        "crash-after-publish/result.env",
    )
    require_env(
        after,
        {
            "possible_lost_effect_declared": "false",
            "spool_effect_count": "1",
            "outbox_row_count": "1",
            "dead_letter_count": "1",
        },
        "crash-after-publish/result.env",
    )
    for boundary in ("crash-before-publish", "crash-after-publish"):
        stdout_name = f"{boundary}/reaper.stdout"
        try:
            stdout = files[stdout_name].decode("utf-8")
        except (KeyError, UnicodeDecodeError) as error:
            raise VerificationError(f"missing or invalid {stdout_name}") from error
        if "claimed=0 completed=0 retried=0 dead_lettered=1" not in stdout:
            raise VerificationError(f"{stdout_name}: reaper-only count is absent")
    before_spool = [
        name
        for name in files
        if name.startswith("crash-before-publish/spool/") and name.endswith(".json")
    ]
    after_spool = [
        name
        for name in files
        if name.startswith("crash-after-publish/spool/") and name.endswith(".json")
    ]
    if before_spool:
        raise VerificationError(
            f"crash-before-publish unexpectedly retained spool effects: {before_spool}"
        )
    if len(after_spool) != 1:
        raise VerificationError(
            f"crash-after-publish expected one spool effect, got {after_spool}"
        )
    return {
        "schema": "trillionnium.outbox-final-attempt-log-verification.v2",
        "repository": repository,
        "head_sha": head_sha,
        "head_tree": head_tree,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "profile": profile,
        "archive_sha256": hashlib.sha256(data).hexdigest(),
        "archive_size": len(data),
        "migration": binding["migration"],
        "migration_blob_sha1": binding["migration_blob_sha1"],
        "database_image": binding["image"],
        "file_count": len(files),
        "crash_before_publish": "passed-with-declared-possible-lost-effect",
        "crash_after_publish": "passed-with-one-stable-spool-effect",
        "production_ready": False,
        "compatibility_credit": False,
    }


def validate_run(
    run: dict[str, Any],
    *,
    repository: str,
    head_sha: str,
    head_tree: str,
    run_attempt: str,
    workflow_id: int,
    current: bool,
) -> None:
    if run.get("id") is None or not isinstance(run.get("id"), int):
        raise VerificationError("workflow run has no integer ID")
    if run.get("workflow_id") != workflow_id:
        raise VerificationError("workflow run numeric workflow ID mismatch")
    if run.get("name") != WORKFLOW_NAME or run.get("path") != WORKFLOW_PATH:
        raise VerificationError("workflow run name/path mismatch")
    if run.get("event") != "pull_request":
        raise VerificationError(f"workflow run event is not pull_request: {run.get('event')!r}")
    if run.get("head_sha") != head_sha:
        raise VerificationError(f"workflow run head mismatch: {run.get('head_sha')!r}")
    if str(run.get("run_attempt")) != run_attempt:
        raise VerificationError("workflow run attempt mismatch")
    repository_row = run.get("repository")
    head_repository = run.get("head_repository")
    if not isinstance(repository_row, dict) or repository_row.get("full_name") != repository:
        raise VerificationError("workflow run repository mismatch")
    if not isinstance(head_repository, dict) or head_repository.get("full_name") != repository:
        raise VerificationError("workflow run head repository mismatch")
    head_commit = run.get("head_commit")
    if not isinstance(head_commit, dict):
        raise VerificationError("workflow run has no head_commit")
    if head_commit.get("id") != head_sha or head_commit.get("tree_id") != head_tree:
        raise VerificationError("workflow run head commit/tree mismatch")
    require_nonempty_string(run.get("run_started_at"), "workflow run start time")
    if current:
        if run.get("status") != "in_progress" or run.get("conclusion") is not None:
            raise VerificationError("current producer run is not in progress")
    else:
        if run.get("status") != "completed" or run.get("conclusion") != "success":
            raise VerificationError("producer workflow run is not completed-success")


def validate_job_steps(job: dict[str, Any], *, current: bool) -> None:
    runner_id = job.get("runner_id")
    if not isinstance(runner_id, int) or runner_id <= 0:
        raise VerificationError(f"{job.get('name')}: runner_id is not positive")
    require_nonempty_string(job.get("runner_name"), f"{job.get('name')} runner_name")
    steps = job.get("steps")
    if not isinstance(steps, list) or not steps:
        raise VerificationError(f"{job.get('name')}: steps are empty")
    seen_numbers: set[int] = set()
    successful = 0
    for step in steps:
        if not isinstance(step, dict):
            raise VerificationError(f"{job.get('name')}: malformed step")
        number = step.get("number")
        if not isinstance(number, int) or number <= 0 or number in seen_numbers:
            raise VerificationError(f"{job.get('name')}: invalid duplicate step number")
        seen_numbers.add(number)
        require_nonempty_string(step.get("name"), f"{job.get('name')} step name")
        status = step.get("status")
        conclusion = step.get("conclusion")
        if status == "completed":
            if conclusion not in {"success", "skipped"}:
                raise VerificationError(
                    f"{job.get('name')}: completed step is not success/skipped"
                )
            if conclusion == "success":
                successful += 1
        elif not current or status not in {"queued", "in_progress", "pending"}:
            raise VerificationError(f"{job.get('name')}: non-terminal step is forbidden")
    if successful == 0:
        raise VerificationError(f"{job.get('name')}: no successful executed step")


def validate_job_set(jobs: list[dict[str, Any]], *, current: bool) -> dict[str, dict[str, Any]]:
    if len(jobs) != len(EXPECTED_JOB_NAMES):
        raise VerificationError(
            f"closed-world job count mismatch: {len(jobs)} != {len(EXPECTED_JOB_NAMES)}"
        )
    by_name: dict[str, dict[str, Any]] = {}
    ids: set[int] = set()
    for job in jobs:
        if not isinstance(job, dict):
            raise VerificationError("workflow job entry is malformed")
        name = require_nonempty_string(job.get("name"), "workflow job name")
        job_id = job.get("id")
        if name in by_name or not isinstance(job_id, int) or job_id <= 0 or job_id in ids:
            raise VerificationError("duplicate or invalid workflow job identity")
        by_name[name] = job
        ids.add(job_id)
    if set(by_name) != EXPECTED_JOB_NAMES:
        raise VerificationError(
            f"closed-world job name mismatch: {sorted(by_name)} != {sorted(EXPECTED_JOB_NAMES)}"
        )
    for name, job in by_name.items():
        is_current = current and name == FINAL_JOB
        if is_current:
            if job.get("status") != "in_progress" or job.get("conclusion") is not None:
                raise VerificationError("current verifier job is not in progress")
        else:
            if job.get("status") != "completed" or job.get("conclusion") != "success":
                raise VerificationError(f"{name}: job is not completed-success")
        validate_job_steps(job, current=is_current)
    return by_name


def fetch_jobs(token: str, repository: str, run_id: str) -> list[dict[str, Any]]:
    payload = request_json(
        token,
        f"{api_base(repository)}/actions/runs/{run_id}/jobs?filter=latest&per_page=100",
    )
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise VerificationError("run jobs response is missing jobs")
    if payload.get("total_count") != len(jobs):
        raise VerificationError("run jobs response is paginated or count-inconsistent")
    return jobs


def verify_run(
    token: str,
    *,
    repository: str,
    head_sha: str,
    run_id: str,
    run_attempt: str,
    current: bool,
    output_directory: Path,
) -> dict[str, object]:
    workflow = fetch_workflow(token, repository)
    workflow_id = int(workflow["id"])
    head_tree = fetch_commit_tree(token, repository, head_sha)
    run = request_json(token, f"{api_base(repository)}/actions/runs/{run_id}")
    validate_run(
        run,
        repository=repository,
        head_sha=head_sha,
        head_tree=head_tree,
        run_attempt=run_attempt,
        workflow_id=workflow_id,
        current=current,
    )
    jobs = fetch_jobs(token, repository, run_id)
    by_name = validate_job_set(jobs, current=current)
    bindings = fetch_profile_bindings(token, repository, head_sha)

    output_directory.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    for profile in PROFILES:
        expected_job_name = f"live-profile ({profile})"
        job = by_name[expected_job_name]
        job_id = int(job["id"])
        log_bytes = request_bytes(
            token,
            f"{api_base(repository)}/actions/jobs/{job_id}/logs",
        )
        try:
            log_text = log_bytes.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise VerificationError(f"{expected_job_name} log is not UTF-8") from error
        archive, envelope_metadata = EMITTER.parse(log_text)
        expected_name = (
            f"outbox-final-attempt-reaper-{profile}-{head_sha}-{run_id}-{run_attempt}"
        )
        if envelope_metadata.get("name") != expected_name:
            raise VerificationError(
                f"{expected_job_name} envelope name mismatch: "
                f"{envelope_metadata.get('name')!r} != {expected_name!r}"
            )
        if envelope_metadata.get("log_style") != EMITTER.GITHUB_LOG_STYLE:
            raise VerificationError(
                f"{expected_job_name} was not reconstructed from a retained GitHub log"
            )
        record = validate_archive(
            archive,
            repository=repository,
            head_sha=head_sha,
            head_tree=head_tree,
            run_id=run_id,
            run_attempt=run_attempt,
            profile=profile,
            binding=bindings[profile],
        )
        if record["archive_sha256"] != envelope_metadata.get("sha256"):
            raise VerificationError(f"{expected_job_name} envelope/archive digest mismatch")
        record["job_id"] = job_id
        record["runner_id"] = job["runner_id"]
        record["runner_name"] = job["runner_name"]
        record["source_log_style"] = envelope_metadata["log_style"]
        record["source_begin_line"] = envelope_metadata["source_begin_line"]
        record["source_end_line"] = envelope_metadata["source_end_line"]
        (output_directory / f"{profile}.tar.gz").write_bytes(archive)
        (output_directory / f"{profile}.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        records.append(record)
    summary: dict[str, object] = {
        "schema": "trillionnium.outbox-final-attempt-completed-log-set.v2",
        "repository": repository,
        "head_sha": head_sha,
        "head_tree": head_tree,
        "workflow_id": workflow_id,
        "workflow_name": WORKFLOW_NAME,
        "workflow_path": WORKFLOW_PATH,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "run_mode": "current-producer" if current else "terminal-producer",
        "profiles": records,
        "all_profiles_verified": len(records) == len(PROFILES),
        "closed_world_job_set_verified": True,
        "runner_and_step_identity_verified": True,
        "migration_and_image_identity_verified": True,
        "production_ready": False,
        "compatibility_credit": False,
    }
    (output_directory / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def discover_completed_run(
    token: str,
    *,
    repository: str,
    head_sha: str,
    wait_seconds: int,
) -> tuple[str, str]:
    workflow = fetch_workflow(token, repository)
    workflow_id = int(workflow["id"])
    deadline = time.monotonic() + wait_seconds
    query = urllib.parse.urlencode(
        {
            "event": "pull_request",
            "status": "success",
            "head_sha": head_sha,
            "per_page": "100",
        }
    )
    url = f"{api_base(repository)}/actions/workflows/{workflow_id}/runs?{query}"
    while True:
        payload = request_json(token, url)
        runs = payload.get("workflow_runs")
        if not isinstance(runs, list):
            raise VerificationError("workflow runs response is malformed")
        candidates = [
            run
            for run in runs
            if isinstance(run, dict)
            and run.get("workflow_id") == workflow_id
            and run.get("name") == WORKFLOW_NAME
            and run.get("path") == WORKFLOW_PATH
            and run.get("head_sha") == head_sha
            and run.get("event") == "pull_request"
            and run.get("status") == "completed"
            and run.get("conclusion") == "success"
        ]
        if candidates:
            run = max(candidates, key=lambda row: int(row.get("id", 0)))
            run_id = str(run["id"])
            run_attempt = str(run["run_attempt"])
            return run_id, run_attempt
        if time.monotonic() >= deadline:
            raise VerificationError(
                f"no terminal-success {WORKFLOW_NAME} run found for exact head {head_sha}"
            )
        time.sleep(10)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--current-run", action="store_true")
    modes.add_argument("--discover-completed-run", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--run-attempt")
    parser.add_argument("--wait-seconds", type=int, default=1200)
    arguments = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("outbox log verification failed: missing GITHUB_TOKEN", file=sys.stderr)
        return 1
    if SHA40.fullmatch(arguments.head_sha) is None:
        print("outbox log verification failed: head SHA is not 40 lowercase hex", file=sys.stderr)
        return 1
    if arguments.wait_seconds < 0 or arguments.wait_seconds > 1800:
        print("outbox log verification failed: wait-seconds is outside 0..1800", file=sys.stderr)
        return 1
    try:
        if arguments.current_run:
            run_id = require_nonempty_string(arguments.run_id, "run-id")
            run_attempt = require_nonempty_string(arguments.run_attempt, "run-attempt")
            current = True
        else:
            if arguments.run_id is not None or arguments.run_attempt is not None:
                raise VerificationError(
                    "discover-completed-run forbids explicit run-id/run-attempt"
                )
            run_id, run_attempt = discover_completed_run(
                token,
                repository=arguments.repository,
                head_sha=arguments.head_sha,
                wait_seconds=arguments.wait_seconds,
            )
            current = False
        summary = verify_run(
            token,
            repository=arguments.repository,
            head_sha=arguments.head_sha,
            run_id=run_id,
            run_attempt=run_attempt,
            current=current,
            output_directory=arguments.output_directory,
        )
        print(json.dumps(summary, sort_keys=True))
        return 0
    except (OSError, VerificationError, EMITTER.EnvelopeError) as error:
        print(f"outbox log verification failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
