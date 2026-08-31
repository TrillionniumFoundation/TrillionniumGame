#!/usr/bin/env python3
"""Download, reconstruct and validate exact-run outbox job-log archives."""
from __future__ import annotations

import argparse
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


def request_json(token: str, url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "trillionnium-outbox-log-verifier"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except (urllib.error.URLError, json.JSONDecodeError) as error:
        raise VerificationError(f"GitHub JSON request failed: {url}: {error}") from error
    if not isinstance(payload, dict):
        raise VerificationError(f"GitHub JSON response is not an object: {url}")
    return payload


def request_bytes(token: str, url: str) -> bytes:
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "trillionnium-outbox-log-verifier"})
    last_error: Exception | None = None
    for attempt in range(10):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
            if not data:
                raise VerificationError(f"GitHub log response is empty: {url}")
            return data
        except urllib.error.HTTPError as error:
            last_error = error
            if error.code not in {404, 409} or attempt == 9:
                detail = error.read().decode("utf-8", "replace")
                raise VerificationError(f"GitHub log request failed: {url}: HTTP {error.code}: {detail}") from error
        except urllib.error.URLError as error:
            last_error = error
            if attempt == 9:
                raise VerificationError(f"GitHub log request failed: {url}: {error}") from error
        time.sleep(2)
    raise VerificationError(f"GitHub log request failed: {url}: {last_error}")


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
                    raise VerificationError(f"non-regular tar member is forbidden: {member.name!r}")
                if name in files:
                    raise VerificationError(f"duplicate tar member: {name}")
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise VerificationError(f"cannot read tar member: {name}")
                files[name] = extracted.read()
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
        raise VerificationError(f"files.sha256 path set mismatch: missing={sorted(actual_names - set(expected))} extra={sorted(set(expected) - actual_names)}")
    for name, digest in expected.items():
        actual = hashlib.sha256(files[name]).hexdigest()
        if actual != digest:
            raise VerificationError(f"files.sha256 digest mismatch for {name}: expected={digest} actual={actual}")


def require_env(values: dict[str, str], expected: dict[str, str], label: str) -> None:
    for key, value in expected.items():
        if values.get(key) != value:
            raise VerificationError(f"{label}: {key} mismatch: expected={value!r} actual={values.get(key)!r}")


def validate_archive(data: bytes, *, repository: str, head_sha: str, run_id: str, run_attempt: str, profile: str) -> dict[str, object]:
    if profile not in PROFILES:
        raise VerificationError(f"unsupported profile: {profile}")
    files = archive_files(data)
    verify_file_manifest(files)
    identity = parse_env(files.get("identity.env", b""), "identity.env")
    require_env(identity, {"repository": repository, "commit": head_sha, "profile": profile, "run_id": f"{run_id}-{run_attempt}-{profile}"}, "identity.env")
    migration = identity.get("migration", "")
    expected_migration = f"migrations/{profile}/0001_foundation_up.sql"
    if migration != expected_migration:
        raise VerificationError(f"identity.env: migration mismatch: {migration!r} != {expected_migration!r}")
    if SHA40.fullmatch(identity.get("migration_blob_sha1", "")) is None:
        raise VerificationError("identity.env: migration blob is not 40-hex")
    result = parse_env(files.get("result.env", b""), "result.env")
    require_env(result, {"status": "passed", "profile": profile, "commit": head_sha}, "result.env")
    before = parse_env(files.get("crash-before-publish/result.env", b""), "crash-before-publish/result.env")
    require_env(before, {"possible_lost_effect_declared": "true", "spool_effect_count": "0", "outbox_row_count": "1", "dead_letter_count": "1"}, "crash-before-publish/result.env")
    after = parse_env(files.get("crash-after-publish/result.env", b""), "crash-after-publish/result.env")
    require_env(after, {"possible_lost_effect_declared": "false", "spool_effect_count": "1", "outbox_row_count": "1", "dead_letter_count": "1"}, "crash-after-publish/result.env")
    for boundary in ("crash-before-publish", "crash-after-publish"):
        stdout_name = f"{boundary}/reaper.stdout"
        try:
            stdout = files[stdout_name].decode("utf-8")
        except (KeyError, UnicodeDecodeError) as error:
            raise VerificationError(f"missing or invalid {stdout_name}") from error
        if "claimed=0 completed=0 retried=0 dead_lettered=1" not in stdout:
            raise VerificationError(f"{stdout_name}: reaper-only count is absent")
    before_spool = [name for name in files if name.startswith("crash-before-publish/spool/") and name.endswith(".json")]
    after_spool = [name for name in files if name.startswith("crash-after-publish/spool/") and name.endswith(".json")]
    if before_spool:
        raise VerificationError(f"crash-before-publish unexpectedly retained spool effects: {before_spool}")
    if len(after_spool) != 1:
        raise VerificationError(f"crash-after-publish expected one spool effect, got {after_spool}")
    return {"schema": "trillionnium.outbox-final-attempt-log-verification.v1", "repository": repository, "head_sha": head_sha, "run_id": run_id, "run_attempt": run_attempt, "profile": profile, "archive_sha256": hashlib.sha256(data).hexdigest(), "archive_size": len(data), "migration": migration, "migration_blob_sha1": identity["migration_blob_sha1"], "file_count": len(files), "crash_before_publish": "passed-with-declared-possible-lost-effect", "crash_after_publish": "passed-with-one-stable-spool-effect", "production_ready": False, "compatibility_credit": False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    arguments = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("outbox log verification failed: missing GITHUB_TOKEN", file=sys.stderr)
        return 1
    if SHA40.fullmatch(arguments.head_sha) is None:
        print("outbox log verification failed: head SHA is not 40-hex", file=sys.stderr)
        return 1
    try:
        owner_repo = urllib.parse.quote(arguments.repository, safe="/")
        base = f"https://api.github.com/repos/{owner_repo}"
        run = request_json(token, f"{base}/actions/runs/{arguments.run_id}")
        if run.get("head_sha") != arguments.head_sha:
            raise VerificationError(f"run head mismatch: {run.get('head_sha')} != {arguments.head_sha}")
        if str(run.get("run_attempt")) != arguments.run_attempt:
            raise VerificationError(f"run attempt mismatch: {run.get('run_attempt')} != {arguments.run_attempt}")
        jobs_payload = request_json(token, f"{base}/actions/runs/{arguments.run_id}/jobs?per_page=100")
        jobs = jobs_payload.get("jobs")
        if not isinstance(jobs, list):
            raise VerificationError("run jobs response is missing jobs")
        arguments.output_directory.mkdir(parents=True, exist_ok=True)
        records: list[dict[str, object]] = []
        for profile in PROFILES:
            expected_job_name = f"live-profile ({profile})"
            matches = [job for job in jobs if job.get("name") == expected_job_name]
            if len(matches) != 1:
                raise VerificationError(f"expected one {expected_job_name!r} job, got {len(matches)}")
            job = matches[0]
            if job.get("status") != "completed" or job.get("conclusion") != "success":
                raise VerificationError(f"{expected_job_name} is not successful: status={job.get('status')} conclusion={job.get('conclusion')}")
            job_id = job.get("id")
            if not isinstance(job_id, int):
                raise VerificationError(f"{expected_job_name} has no integer job ID")
            log_bytes = request_bytes(token, f"{base}/actions/jobs/{job_id}/logs")
            try:
                log_text = log_bytes.decode("utf-8-sig")
            except UnicodeDecodeError as error:
                raise VerificationError(f"{expected_job_name} log is not UTF-8") from error
            archive, envelope_metadata = EMITTER.parse(log_text)
            expected_name = f"outbox-final-attempt-reaper-{profile}-{arguments.head_sha}-{arguments.run_id}-{arguments.run_attempt}"
            if envelope_metadata.get("name") != expected_name:
                raise VerificationError(f"{expected_job_name} envelope name mismatch: {envelope_metadata.get('name')!r} != {expected_name!r}")
            if envelope_metadata.get("log_style") != "github-actions-timestamped-v1":
                raise VerificationError(f"{expected_job_name} was not reconstructed from a retained GitHub log")
            record = validate_archive(archive, repository=arguments.repository, head_sha=arguments.head_sha, run_id=arguments.run_id, run_attempt=arguments.run_attempt, profile=profile)
            if record["archive_sha256"] != envelope_metadata.get("sha256"):
                raise VerificationError(f"{expected_job_name} envelope/archive digest mismatch")
            (arguments.output_directory / f"{profile}.tar.gz").write_bytes(archive)
            (arguments.output_directory / f"{profile}.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            records.append(record)
        summary = {"schema": "trillionnium.outbox-final-attempt-log-set.v1", "repository": arguments.repository, "head_sha": arguments.head_sha, "run_id": arguments.run_id, "run_attempt": arguments.run_attempt, "profiles": records, "all_profiles_verified": len(records) == len(PROFILES), "production_ready": False, "compatibility_credit": False}
        (arguments.output_directory / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(summary, sort_keys=True))
        return 0
    except (OSError, VerificationError, EMITTER.EnvelopeError) as error:
        print(f"outbox log verification failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
