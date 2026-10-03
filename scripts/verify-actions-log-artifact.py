#!/usr/bin/env python3
"""Verify exact completed outbox evidence reconstructed from GitHub job logs."""
from __future__ import annotations

import argparse
import base64
import gzip
import functools
import hashlib
import http.client
import importlib.util
import io
import json
import math
import os
import re
import sys
import tarfile
import time
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import schema_evidence_binding as BINDING
import schema_source_http as SOURCE_HTTP
EMITTER_PATH = ROOT / "scripts/emit-actions-log-artifact.py"
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 512
MAX_RETAINED_BYTES = 32 * 1024 * 1024
# Bound gzip expansion before tarfile can consume PAX/long-name headers. The
# framing allowance covers bounded member headers, padding and end records;
# regular file payloads still share the stricter MAX_RETAINED_BYTES budget.
MAX_EXPANDED_TAR_BYTES = MAX_RETAINED_BYTES + (MAX_ARCHIVE_ENTRIES + 1) * tarfile.RECORDSIZE
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
# These budgets apply to immutable source custody only. The older Actions
# metadata/log download helpers below retain their separate resource gaps.
MAX_SOURCE_FILE_JSON_BYTES = SOURCE_HTTP.FILE_JSON_BYTES
MAX_SOURCE_TREE_JSON_BYTES = SOURCE_HTTP.TREE_JSON_BYTES
MAX_SOURCE_ERROR_BYTES = SOURCE_HTTP.ERROR_BODY_BYTES
MAX_SOURCE_TREE_ENTRIES = 50000
MAX_SOURCE_JSON_DEPTH = 64
SOURCE_HTTP_TIMEOUT_SECONDS = SOURCE_HTTP.ABSOLUTE_SECONDS


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


def load_migration_checker() -> Any:
    spec = importlib.util.spec_from_file_location(
        "actions_log_migration_lock", ROOT / "scripts/check-migration-lock.py"
    )
    if spec is None or spec.loader is None:
        raise VerificationError("cannot load authoritative migration-lock checker")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATIONS = load_migration_checker()
SCHEMA_VERSION = 4
STORAGE_WRITER_EPOCH = 4


def strict_object(data: bytes, label: str) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise VerificationError(f"{label}: duplicate JSON key {key}")
            result[key] = value
        return result

    try:
        value = json.loads(data, object_pairs_hook=unique)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"{label}: invalid JSON") from error
    if not isinstance(value, dict):
        raise VerificationError(f"{label}: JSON object required")
    return value


def profile_object(document: dict[str, Any], profile: str, label: str) -> dict[str, Any]:
    profiles = document.get("profiles")
    if not isinstance(profiles, dict) or not isinstance(profiles.get(profile), dict):
        raise VerificationError(f"{label}: malformed profile mapping")
    return profiles[profile]


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


def _check_source_deadline(deadline) -> None:
    try:
        SOURCE_HTTP.remaining_deadline(deadline)
    except SOURCE_HTTP.SourceHttpError as error:
        raise VerificationError(str(error)) from None


def _source_deadline(existing=None):
    try:
        SOURCE_HTTP.remaining_deadline(existing)
        return existing
    except SOURCE_HTTP.SourceHttpError as error:
        raise VerificationError(str(error)) from None


def _source_budget(purpose):
    """Own one fixed-purpose token and check after the entire call completes."""
    def decorate(function):
        @functools.wraps(function)
        def execute(*args, **kwargs):
            deadline = None
            owned = False
            try:
                incoming = kwargs.pop('_deadline', None)
                if purpose == 'collection':
                    if incoming is not None:
                        raise SOURCE_HTTP.SourceHttpError('input')
                    deadline = SOURCE_HTTP.start_deadline(purpose='collection')
                    owned = True
                else:
                    deadline, owned = SOURCE_HTTP.acquire_request_deadline(incoming)
                result = function(*args, **kwargs, _deadline=deadline)
                SOURCE_HTTP.finish_deadline(deadline, release_owned=owned)
            except BaseException as error:
                if owned:
                    SOURCE_HTTP.release_deadline(deadline)
                if isinstance(error, SOURCE_HTTP.SourceHttpError):
                    raise VerificationError(str(error)) from None
                raise
            return result
        return execute
    return decorate


@_source_budget('request')
def request_source_json(token: str, url: str, *, maximum: int, _deadline=None) -> dict[str, Any]:
    """Strict source JSON after a cancellable absolute-deadline HTTP request."""
    deadline = _source_deadline(_deadline)
    if type(maximum) is not int or maximum not in (
        MAX_SOURCE_FILE_JSON_BYTES, MAX_SOURCE_TREE_JSON_BYTES
    ):
        raise VerificationError("unreviewed source HTTP byte budget")
    try:
        raw = SOURCE_HTTP.fetch(token, url, budget="tree" if maximum == MAX_SOURCE_TREE_JSON_BYTES else "file", deadline=deadline)
    except SOURCE_HTTP.SourceHttpError as error:
        raise VerificationError(str(error)) from None

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise VerificationError("GitHub source JSON contains duplicate keys")
            result[key] = value
        return result

    def finite(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("nonfinite JSON number")
        return number

    def reject_constant(_value: str) -> Any:
        raise ValueError("nonfinite JSON number")

    try:
        # Python's process-wide recursion limit is mutable. Enforce this
        # transport's own parse-depth budget before allocating nested values.
        depth = 0
        quoted = False
        escaped = False
        for byte in raw:
            if quoted:
                if escaped:
                    escaped = False
                elif byte == 92:
                    escaped = True
                elif byte == 34:
                    quoted = False
            elif byte == 34:
                quoted = True
            elif byte in (91, 123):
                depth += 1
                if depth > MAX_SOURCE_JSON_DEPTH:
                    raise ValueError("source JSON nesting budget exceeded")
            elif byte in (93, 125):
                depth -= 1
        payload = json.loads(
            raw.decode("utf-8"), object_pairs_hook=unique,
            parse_float=finite, parse_constant=reject_constant,
        )
    except (UnicodeError, ValueError, RecursionError) as error:
        raise VerificationError("GitHub source response contains invalid JSON") from error
    if type(payload) is not dict:
        raise VerificationError("GitHub source JSON object required")
    _check_source_deadline(deadline)
    return payload


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


def decode_source_contents(
    payload: dict[str, Any], path: str, *, maximum: int
) -> bytes:
    if type(maximum) is not int or not 0 <= maximum <= BINDING.MAX_FILE_BYTES:
        raise VerificationError("unreviewed decoded source byte budget")
    declared = payload.get("size")
    if type(declared) is not int or not 0 <= declared <= maximum:
        raise VerificationError(f"{path}: declared source byte budget or type mismatch")
    if payload.get("path") != path or payload.get("type") != "file" or payload.get("encoding") != "base64":
        raise VerificationError(f"{path}: exact base64 source file required")
    content = payload.get("content")
    # GitHub Contents wraps canonical base64 at 60 columns with LF. Bound the
    # encoded string before removing those transport line breaks or decoding.
    encoded_limit = 4 * ((declared + 2) // 3)
    wrapped_limit = encoded_limit + (encoded_limit + 59) // 60
    if type(content) is not str or not content or len(content) > wrapped_limit:
        raise VerificationError(f"{path}: encoded source byte budget exceeded")
    encoded = content.replace("\n", "")
    if len(encoded) > encoded_limit:
        raise VerificationError(f"{path}: encoded source byte budget exceeded")
    padding = len(encoded) - len(encoded.rstrip("="))
    if len(encoded) % 4 or padding > 2 or 3 * (len(encoded) // 4) - padding != declared:
        raise VerificationError(f"{path}: source size or base64 padding mismatch")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (UnicodeError, ValueError) as error:
        raise VerificationError(f"{path}: invalid source base64") from error
    if len(data) != declared or base64.b64encode(data).decode("ascii") != encoded:
        raise VerificationError(f"{path}: source size or canonical base64 mismatch")
    return data


@_source_budget('request')
def fetch_source_exact_file(
    token: str, repository: str, head_sha: str, path: str, *, maximum: int, _deadline=None
) -> tuple[bytes, str]:
    deadline = _source_deadline(_deadline)
    encoded_path = urllib.parse.quote(path, safe="/")
    payload = request_source_json(
        token, f"{api_base(repository)}/contents/{encoded_path}?ref={head_sha}",
        maximum=MAX_SOURCE_FILE_JSON_BYTES, _deadline=deadline,
    )
    data = decode_source_contents(payload, path, maximum=maximum)
    actual = git_blob_sha1(data)
    if payload.get("sha") != actual:
        raise VerificationError(f"{path}: source Git blob mismatch")
    _check_source_deadline(deadline)
    return data, actual


@_source_budget('request')
def fetch_commit_tree(token: str, repository: str, head_sha: str, *, _deadline=None) -> str:
    deadline = _source_deadline(_deadline)
    if type(head_sha) is not str or SHA40.fullmatch(head_sha) is None:
        raise VerificationError("head SHA is not 40 lowercase hex")
    commit = request_source_json(
        token, f"{api_base(repository)}/git/commits/{head_sha}",
        maximum=MAX_SOURCE_FILE_JSON_BYTES, _deadline=deadline,
    )
    if commit.get("sha") != head_sha:
        raise VerificationError("Git commit response is not bound to the requested head")
    tree = commit.get("tree")
    if type(tree) is not dict or type(tree.get("sha")) is not str or SHA40.fullmatch(tree["sha"]) is None:
        raise VerificationError("Git commit has no canonical tree SHA")
    _check_source_deadline(deadline)
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


@_source_budget('collection')
def fetch_profile_bindings(
    token: str, repository: str, head_sha: str, *, head_tree: str | None = None, _deadline=None
) -> dict[str, Any]:
    """Fetch complete exact-head authority, then issue opaque profile tokens."""
    deadline = _source_deadline(_deadline)
    if type(head_sha) is not str or SHA40.fullmatch(head_sha) is None:
        raise VerificationError("head SHA is not 40 lowercase hex")
    tree_sha = head_tree if head_tree is not None else fetch_commit_tree(token, repository, head_sha, _deadline=deadline)
    if type(tree_sha) is not str or SHA40.fullmatch(tree_sha) is None:
        raise VerificationError("source tree SHA is not 40 lowercase hex")
    tree = request_source_json(
        token, f"{api_base(repository)}/git/trees/{tree_sha}?recursive=1",
        maximum=MAX_SOURCE_TREE_JSON_BYTES, _deadline=deadline,
    )
    if tree.get("sha") != tree_sha or tree.get("truncated") is not False or type(tree.get("tree")) is not list or len(tree["tree"]) > MAX_SOURCE_TREE_ENTRIES:
        raise VerificationError("exact-head full source tree is mismatched, truncated or oversized")
    entries = {}
    inventory = {profile:[] for profile in PROFILES}
    for entry in tree["tree"]:
        if type(entry) is not dict or type(entry.get("path")) is not str or entry["path"] in entries:
            raise VerificationError("exact-head source tree entry malformed or duplicate")
        path=entry["path"];entries[path]=entry
        for profile in PROFILES:
            if path.startswith(f"migrations/{profile}/") and path.endswith(".sql"):
                inventory[profile].append(path)
    paths = sorted(BINDING.CONTROL_PATHS + tuple(path for profile in PROFILES for path in inventory[profile]))
    if any(len(inventory[p]) != 5 for p in PROFILES) or len(paths) != 18:
        raise VerificationError("exact-head all10 source migration denominator required")
    # Validate the complete selected inventory and aggregate declaration before
    # downloading any file. Each independently decoded body must also match it.
    declared_total = 0
    for path in paths:
        entry = entries.get(path, {})
        if entry.get("type") != "blob" or entry.get("mode") not in ("100644", "100755"):
            raise VerificationError("exact-head full source regular blob required")
        if re.fullmatch(r'migrations/(postgresql|cockroachdb)/[0-9]{4}_[a-z0-9_]+[.]sql', path) is None and path not in BINDING.CONTROL_PATHS:
            raise VerificationError("exact-head source path must be a reviewed regular file")
        size = entry.get("size")
        if type(size) is not int or not 0 <= size <= BINDING.MAX_FILE_BYTES:
            raise VerificationError("exact-head source declared byte bound or type mismatch")
        declared_total += size
        if declared_total > BINDING.MAX_ANNEX_BYTES:
            raise VerificationError("exact-head source declared byte bound or type mismatch")
        if type(entry.get("sha")) is not str or SHA40.fullmatch(entry["sha"]) is None:
            raise VerificationError("exact-head canonical source blob required")
    # Only immutable, independently tree-bound regular bytes enter the issuer.
    # No caller-supplied read callback or synthetic shortened source report.
    _check_source_deadline(deadline)
    with tempfile.TemporaryDirectory(prefix="trnm-full-schema-source-") as temporary:
        snapshot=Path(temporary);total=0
        for path in paths:
            entry=entries[path];size=entry["size"]
            raw,_=fetch_source_exact_file(token,repository,head_sha,path,maximum=min(BINDING.MAX_FILE_BYTES,BINDING.MAX_ANNEX_BYTES-total),_deadline=deadline)
            total+=len(raw)
            if len(raw)!=size or len(raw)>BINDING.MAX_FILE_BYTES or total>BINDING.MAX_ANNEX_BYTES or git_blob_sha1(raw)!=entry.get("sha"):
                raise VerificationError("exact-head source blob preimage or byte bound mismatch")
            target=snapshot/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        _check_source_deadline(deadline)
        try:
            bindings = {}
            for profile in PROFILES:
                _check_source_deadline(deadline)
                bindings[profile] = BINDING.verify_binding(snapshot,profile=profile)
            _check_source_deadline(deadline)
        except (BINDING.BindingError, BINDING.SOURCE.SelectionError, KeyError, TypeError, ValueError) as error:
            raise VerificationError("exact-head complete source validation failed") from error
    _check_source_deadline(deadline)
    return bindings


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
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
            expanded = compressed.read(MAX_EXPANDED_TAR_BYTES + 1)
        if len(expanded) > MAX_EXPANDED_TAR_BYTES:
            raise VerificationError("archive exceeds the expanded tar bound")
        entry_count = 0
        retained_size = 0
        root_directory_seen = False
        with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as archive:
            for member in archive:
                name = normalized_member_name(member.name)
                # `tar -C root .` emits one structural root directory which is
                # absent from the producer's rglob entry inventory.
                if name is None and member.isdir() and not root_directory_seen:
                    if member.size != 0:
                        raise VerificationError("tar root directory has a payload")
                    root_directory_seen = True
                    continue
                entry_count += 1
                if entry_count > MAX_ARCHIVE_ENTRIES:
                    raise VerificationError("archive exceeds the entry budget")
                if member.isdir():
                    if member.size != 0:
                        raise VerificationError("tar directory has a payload")
                    continue
                if name is None:
                    raise VerificationError("tar root member must be a directory")
                if not member.isfile():
                    raise VerificationError(
                        f"non-regular tar member is forbidden: {member.name!r}"
                    )
                if name in files:
                    raise VerificationError(f"duplicate tar member: {name}")
                if not 0 <= member.size <= MAX_ARCHIVE_BYTES:
                    raise VerificationError(f"tar member exceeds the archive bound: {name}")
                retained_size += member.size
                if retained_size > MAX_RETAINED_BYTES:
                    raise VerificationError("archive exceeds the expanded file byte budget")
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise VerificationError(f"cannot read tar member: {name}")
                payload = extracted.read(MAX_ARCHIVE_BYTES + 1)
                if len(payload) != member.size:
                    raise VerificationError(f"tar member payload is truncated: {name}")
                files[name] = payload
    except (tarfile.TarError, OSError, EOFError, zlib.error) as error:
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


def verify_migration_files(files: dict[str, bytes], binding) -> None:
    try:
        ordered_files = BINDING.operational_binding(binding)["ordered_files"]
    except (BINDING.BindingError, BINDING.SOURCE.SelectionError) as error:
        raise VerificationError("issued source token required for executed SQL inventory") from error
    expected_paths = {entry["path"] for entry in ordered_files}
    actual_paths = {name for name in files if name.startswith("migrations/")}
    if actual_paths != expected_paths:
        raise VerificationError(
            "archived migration inventory differs from exact-head source: "
            f"missing={sorted(expected_paths - actual_paths)} "
            f"unlisted={sorted(actual_paths - expected_paths)}"
        )
    for entry in ordered_files:
        name = entry["path"]
        if git_blob_sha1(files[name]) != entry["git_blob_sha1"]:
            raise VerificationError(f"archived migration blob differs from exact-head source: {name}")


def producer_identity(profile: str, workflow_context: str = "outbox") -> dict[str, str]:
    if profile not in PROFILES:
        raise VerificationError(f"unsupported profile: {profile}")
    if workflow_context == "outbox":
        return {"workflow": WORKFLOW_NAME, "workflow_path": WORKFLOW_PATH,
                "job_key": "live-profile", "job_name": f"live-profile ({profile})"}
    if workflow_context == "prospective":
        return {"workflow": "prospective-merge-gate",
                "workflow_path": ".github/workflows/prospective-merge-gate.yml",
                "job_key": "live-profiles", "job_name": f"prospective-live ({profile})"}
    raise VerificationError("unsupported evidence producer context")


def validate_archive(
    data: bytes,
    *,
    repository: str,
    head_sha: str,
    head_tree: str,
    run_id: str,
    run_attempt: str,
    profile: str,
    binding,
    workflow_context: str = "outbox",
) -> dict[str, object]:
    if profile not in PROFILES:
        raise VerificationError(f"unsupported profile: {profile}")
    verified_binding = binding
    try:
        binding = BINDING.operational_binding(verified_binding)
        proof = BINDING.binding_document(verified_binding)
    except (BINDING.BindingError, BINDING.SOURCE.SelectionError) as error:
        raise VerificationError("issued full-source5 evidence binding required") from error
    if proof["profile"] != profile:
        raise VerificationError("source binding profile mismatch")
    files = archive_files(data)
    verify_file_manifest(files)
    try:
        source_fields = BINDING.validate_annex_files(verified_binding, files, commit=head_sha, tree=head_tree)
    except (BINDING.BindingError, BINDING.SOURCE.SelectionError) as error:
        raise VerificationError("closed full-source5 sidecar/annex differs") from error
    verify_migration_files(files, verified_binding)
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
            **producer_identity(profile, workflow_context),
            **source_fields,
            **{key: binding[key] for key in (
                "migration_lock", "schema_version", "storage_writer_epoch", "chain_digest", "digest_algorithm"
            )},
        },
        "identity.env",
    )
    expected_identity_keys = {"repository", "commit", "tree", "profile", "image", "run_id", "run_attempt",
                              "evidence_run_id", *producer_identity(profile, workflow_context),
                              "migration_lock", "schema_version", "storage_writer_epoch", "chain_digest", "digest_algorithm", *source_fields}
    if set(identity) != expected_identity_keys:
        raise VerificationError("identity.env closed source/execution fields differ")
    retained_lock = files.get("migration-chain.lock.json", b"")
    if hashlib.sha256(retained_lock).hexdigest() != binding["migration_lock_sha256"]:
        raise VerificationError("retained migration lock differs from exact-head source")
    lock = strict_object(retained_lock, "retained migration lock")
    if type(lock.get("schema_version")) is not int or lock["schema_version"] != 5 or retained_lock != files[BINDING.ANNEX + "/" + BINDING.SOURCE.LOCK_PATH]:
        raise VerificationError("retained migration chain is incomplete or mismatched")
    schema = strict_object(files.get("schema-identity.json", b""), "schema identity")
    expected_schema = {
        "schema": "trillionnium.authoritative-schema-report.v1", "profile": profile,
        "schema_version": SCHEMA_VERSION, "storage_writer_epoch": STORAGE_WRITER_EPOCH,
        "chain_digest": binding["chain_digest"], "digest_algorithm": binding["digest_algorithm"],
        "source_commit": head_sha, "upgrade_source_commit": head_sha, "v2_apply_source_commit": head_sha, "v3_apply_source_commit": head_sha,
        "table_count": 12, "applied_steps": len(binding["ordered_files"]),
        "migration_applied": True, "compatibility_credit": False,
    }
    if set(schema) != set(expected_schema):
        raise VerificationError("schema identity closed fields mismatch: missing=" + repr(sorted(set(expected_schema) - set(schema))) + " unknown=" + repr(sorted(set(schema) - set(expected_schema))))
    for key, value in expected_schema.items():
        if type(schema.get(key)) is not type(value) or schema.get(key) != value:
            raise VerificationError(f"schema identity: {key} mismatch")
    validation = strict_object(files.get("migration-chain-validation.json", b""), "migration chain validation")
    if not BINDING.same(validation, proof["source_selection"]["source"]["complete_validation"]):
        raise VerificationError("retained complete all10 source validation differs from issued binding")
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
    if set(result) != {"status", "profile", "commit", "tree"}:
        raise VerificationError("result.env closed fields mismatch")
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
        "schema": "trillionnium.outbox-final-attempt-log-verification.v3",
        "repository": repository,
        "head_sha": head_sha,
        "head_tree": head_tree,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "profile": profile,
        "archive_sha256": hashlib.sha256(data).hexdigest(),
        "archive_size": len(data),
        "migration_lock": binding["migration_lock"],
        "migration_lock_sha256": binding["migration_lock_sha256"],
        "schema_version": SCHEMA_VERSION,
        "storage_writer_epoch": STORAGE_WRITER_EPOCH,
        "v2_apply_source_commit": schema["v2_apply_source_commit"],
        "v3_apply_source_commit": schema["v3_apply_source_commit"],
        "chain_digest": binding["chain_digest"],
        "digest_algorithm": binding["digest_algorithm"],
        "ordered_files": binding["ordered_files"],
        "database_image": binding["image"],
        "source_selection": source_fields,
        "full_source_inventory_count": 18,
        "source_frontier_schema_version": 5,
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
    bindings = fetch_profile_bindings(token, repository, head_sha, head_tree=head_tree)

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
