#!/usr/bin/env python3
"""Upload one small immutable diagnostic file to GitHub Actions artifact storage.

The repository intentionally forbids third-party workflow actions. This module
implements the minimal GitHub Actions Results/Twirp and Azure Put Blob protocol
needed for bounded evidence archives, using only the Python standard library.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import re
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
# Small protocol acknowledgements must not consume the archive's byte budget.
MAX_SERVICE_RESPONSE_BYTES = 1024 * 1024
MAX_RESPONSE_JSON_DEPTH = 64
MAX_RESPONSE_NUMBER_CHARS = 128
MAX_ARTIFACT_ID = (1 << 63) - 1
ARTIFACT_ID_PATTERN = re.compile(r"[1-9][0-9]{0,18}")
READ_CHUNK_BYTES = 1024 * 1024
MAX_PATH_COMPONENTS = 256
_SECURE_FILE_IO_SUPPORTED = (
    os.name == "posix"
    and all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_DIRECTORY", "O_CLOEXEC", "O_NONBLOCK"))
    and os.open in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.stat in os.supports_follow_symlinks
)
MAX_ATTEMPTS = 5
INITIAL_BACKOFF_SECONDS = 0.25
ARTIFACT_VERSION = 7
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RESULT_SCOPE_PATTERN = re.compile(r"(?<!\S)Actions\.Results:([^:\s]+):([^:\s]+)(?=\s|$)")
TRANSIENT_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}


class ArtifactUploadError(RuntimeError):
    """Raised when artifact creation, upload or finalization fails closed."""


def _base64url_json(segment: str) -> dict[str, Any]:
    padding = "=" * (-len(segment) % 4)
    try:
        value = json.loads(base64.urlsafe_b64decode(segment + padding))
    except (ValueError, json.JSONDecodeError) as error:
        raise ArtifactUploadError("runtime token payload is not valid base64url JSON") from error
    if not isinstance(value, dict):
        raise ArtifactUploadError("runtime token payload must be an object")
    return value


def backend_ids_from_runtime_token(token: str) -> tuple[str, str]:
    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        raise ArtifactUploadError("ACTIONS_RUNTIME_TOKEN is not a canonical JWT")
    payload = _base64url_json(parts[1])
    scopes = payload.get("scp")
    if not isinstance(scopes, str):
        raise ArtifactUploadError("runtime token scp claim must be a string")
    matches = RESULT_SCOPE_PATTERN.findall(scopes)
    if len(matches) != 1:
        raise ArtifactUploadError("runtime token must contain exactly one Actions.Results scope")
    run_backend_id, job_backend_id = matches[0]
    if not run_backend_id or not job_backend_id:
        raise ArtifactUploadError("Actions.Results scope has empty backend identity")
    return run_backend_id, job_backend_id


def results_origin(results_url: str) -> str:
    parsed = urllib.parse.urlsplit(results_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ArtifactUploadError("ACTIONS_RESULTS_URL must be an HTTPS origin")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _file_identity(value: os.stat_result) -> tuple[int, ...]:
    return (value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
            value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def validate_artifact(name: str, path: Path) -> bytes:
    """Read the inspected regular inode through one pinned descriptor.

    The no-follow directory walk prevents ancestor symlink substitution. Leaf
    lstat-equivalent metadata must match fstat before any bytes are read; both
    the descriptor and anchored directory entry must still match afterwards.
    This detects observable mutation, not an immutable filesystem snapshot or
    a wall-clock bound on an unresponsive filesystem.
    """
    if not isinstance(name, str) or NAME_PATTERN.fullmatch(name) is None:
        raise ArtifactUploadError("artifact name is not canonical")
    if not _SECURE_FILE_IO_SUPPORTED:
        raise ArtifactUploadError("descriptor-relative no-follow artifact I/O is required")
    absolute = path.absolute()
    if (absolute.anchor != os.sep or not 1 < len(absolute.parts) <= MAX_PATH_COMPONENTS
            or any(part in (".", "..") or "\0" in part for part in absolute.parts[1:])):
        raise ArtifactUploadError("artifact path is not canonical")
    directory = None
    descriptor = None
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    try:
        directory = os.open(os.sep, directory_flags)
        for part in absolute.parts[1:-1]:
            next_directory = os.open(part, directory_flags, dir_fd=directory)
            previous, directory = directory, next_directory
            os.close(previous)
        inspected = os.stat(absolute.name, dir_fd=directory, follow_symlinks=False)
        if not stat.S_ISREG(inspected.st_mode):
            raise ArtifactUploadError("artifact path must be a regular non-symlink file")
        if not 0 < inspected.st_size <= MAX_ARTIFACT_BYTES:
            raise ArtifactUploadError("artifact size is outside the bounded non-empty range")
        descriptor = os.open(absolute.name, file_flags, dir_fd=directory)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _file_identity(opened) != _file_identity(inspected):
            raise ArtifactUploadError("artifact identity changed before reading")
        chunks = []
        remaining = opened.st_size
        while remaining:
            block = os.read(descriptor, min(READ_CHUNK_BYTES, remaining))
            if not block:
                raise ArtifactUploadError("artifact file was truncated during reading")
            chunks.append(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise ArtifactUploadError("artifact file grew during reading")
        after = os.fstat(descriptor)
        leaf_after = os.stat(absolute.name, dir_fd=directory, follow_symlinks=False)
        if (_file_identity(after) != _file_identity(opened)
                or _file_identity(leaf_after) != _file_identity(opened)):
            raise ArtifactUploadError("artifact file changed while being read")
        return b"".join(chunks)
    except OSError:
        # Paths, filesystem diagnostics and retained contents are not public errors.
        raise ArtifactUploadError("secure artifact file I/O failed") from None
    finally:
        try:
            if descriptor is not None:
                os.close(descriptor)
        finally:
            if directory is not None:
                os.close(directory)


class _RejectArtifactRedirects(urllib.request.HTTPRedirectHandler):
    """No automatic redirect may replay credentials or signed artifact bytes."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ArtifactUploadError("artifact service redirects are forbidden")

    def http_error_302(self, req, fp, code, msg, headers):
        # Do not consume an unbounded redirect body or log its Location/URL.
        fp.close()
        raise ArtifactUploadError("artifact service redirects are forbidden")

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def _no_redirect_opener() -> Callable[..., Any]:
    # Private per-upload opener; do not inherit an installed global urlopen opener.
    return urllib.request.build_opener(_RejectArtifactRedirects()).open


def _response_status(response: Any) -> int:
    value = getattr(response, "status", None)
    if value is None:
        value = response.getcode()
    return int(value)


def _request_bytes(
    request: urllib.request.Request,
    *,
    opener: Callable[..., Any],
    sleeper: Callable[[float], None],
) -> tuple[int, bytes]:
    backoff = INITIAL_BACKOFF_SECONDS
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with opener(request, timeout=60) as response:
                status_code = _response_status(response)
                # Error bodies are irrelevant to retry classification and can contain
                # credentials. Do not read them, even for a transient HTTP response.
                if status_code in TRANSIENT_HTTP_STATUSES:
                    raise urllib.error.HTTPError(
                        request.full_url, status_code,
                        "transient artifact service response", hdrs=None, fp=None,
                    )
                if status_code < 200 or status_code >= 300:
                    raise ArtifactUploadError(f"artifact service returned HTTP {status_code}")
                body = response.read(MAX_SERVICE_RESPONSE_BYTES + 1)
                if not isinstance(body, bytes) or len(body) > MAX_SERVICE_RESPONSE_BYTES:
                    raise ArtifactUploadError("artifact service response exceeds the byte limit")
            return status_code, body
        except urllib.error.HTTPError as error:
            # urlopen can raise before entering a context manager. Close that
            # response on both permanent and retryable errors without reading it.
            code = error.code
            error.close()
            if code not in TRANSIENT_HTTP_STATUSES or attempt == MAX_ATTEMPTS:
                raise ArtifactUploadError(f"artifact service returned HTTP {code}") from None
        except urllib.error.URLError:
            if attempt == MAX_ATTEMPTS:
                raise ArtifactUploadError("artifact service transport failed") from None
        sleeper(backoff)
        backoff = min(backoff * 2, 4.0)
    raise AssertionError("bounded retry loop must return or raise")


def _twirp_json(
    origin: str,
    method: str,
    payload: dict[str, Any],
    token: str,
    *,
    opener: Callable[..., Any],
    sleeper: Callable[[float], None],
) -> dict[str, Any]:
    url = f"{origin}/twirp/github.actions.results.api.v1.ArtifactService/{method}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "trillionnium-native-artifact-uploader/1",
        },
    )
    # Defense in depth: urllib must not copy this header onto a redirected request,
    # even when a trusted caller supplies its own test/transport adapter.
    request.add_unredirected_header("Authorization", f"Bearer {token}")
    _, body = _request_bytes(request, opener=opener, sleeper=sleeper)
    return _decode_service_response(body, method)


def _decode_service_response(body: bytes, method: str) -> dict[str, Any]:
    """Bounded strict JSON, not a general-purpose ProtoJSON implementation.

    Unknown finite extension fields remain readable within the same budgets.
    Duplicate/aliased identities are rejected, not resolved by last-key wins.
    """
    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate field")
            value[key] = item
        return value

    def integer(text: str) -> int:
        if len(text) > MAX_RESPONSE_NUMBER_CHARS:
            raise ValueError("number too long")
        return int(text)

    def real(text: str) -> float:
        if len(text) > MAX_RESPONSE_NUMBER_CHARS:
            raise ValueError("number too long")
        result = float(text)
        if not math.isfinite(result):
            raise ValueError("nonfinite number")
        return result

    def constant(_text: str) -> None:
        raise ValueError("nonfinite constant")

    try:
        if not isinstance(body, bytes) or not 0 < len(body) <= MAX_SERVICE_RESPONSE_BYTES:
            raise ValueError("response outside byte bound")
        text = body.decode("utf-8")
        # Check nesting before recursive JSON decoding; braces in escaped strings
        # do not count. The JSON decoder remains the authority for all syntax.
        depth, quoted, escaped = 0, False, False
        for char in text:
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in "[{":
                depth += 1
                if depth > MAX_RESPONSE_JSON_DEPTH:
                    raise ValueError("response nesting exceeded")
            elif char in "]}":
                depth -= 1
        value = json.loads(text, object_pairs_hook=unique_pairs,
                           parse_int=integer, parse_float=real, parse_constant=constant)
        if not isinstance(value, dict):
            raise ValueError("response must be an object")
        return value
    except (ValueError, RecursionError):
        # Never include a response value or chained JSON/source diagnostics.
        raise ArtifactUploadError(f"{method} response is not valid bounded JSON") from None


def _field(value: dict[str, Any], snake: str, camel: str) -> Any:
    if snake in value and camel in value:
        raise ArtifactUploadError("artifact service returned duplicate field aliases")
    return value.get(snake, value.get(camel))


def _artifact_id(value: Any) -> str:
    """A positive signed-int64 ID, encoded as canonical decimal text or an int.

    bool is intentionally not an integer here. Noncanonical text must never
    become raw GITHUB_OUTPUT content. This keeps the uploader's narrow identity
    profile rather than pretending to accept every ProtoJSON numeric spelling.
    """
    if type(value) is int:
        if 0 < value <= MAX_ARTIFACT_ID:
            return str(value)
    elif isinstance(value, str) and ARTIFACT_ID_PATTERN.fullmatch(value):
        if int(value) <= MAX_ARTIFACT_ID:
            return value
    raise ArtifactUploadError("FinalizeArtifact returned no canonical positive int64 artifact ID")


def _mask_signed_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    for key in ("sig", "skoid", "sktid", "skt", "ske"):
        for value in query.get(key, []):
            if value:
                print(f"::add-mask::{value}")


def upload_artifact(
    name: str,
    path: Path,
    mime_type: str,
    *,
    runtime_token: str,
    actions_results_url: str,
    opener: Callable[..., Any] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if not isinstance(mime_type, str) or not mime_type or any(ch.isspace() for ch in mime_type):
        raise ArtifactUploadError("MIME type is not canonical")
    data = validate_artifact(name, path)
    digest = hashlib.sha256(data).hexdigest()
    run_backend_id, job_backend_id = backend_ids_from_runtime_token(runtime_token)
    origin = results_origin(actions_results_url)
    open_request = _no_redirect_opener() if opener is None else opener

    identity = {
        "workflow_run_backend_id": run_backend_id,
        "workflow_job_run_backend_id": job_backend_id,
        "name": name,
    }
    created = _twirp_json(
        origin,
        "CreateArtifact",
        {
            **identity,
            "version": ARTIFACT_VERSION,
            "mime_type": mime_type,
        },
        runtime_token,
        opener=open_request,
        sleeper=sleeper,
    )
    if created.get("ok") is not True:
        raise ArtifactUploadError("CreateArtifact did not return ok=true")
    signed_upload_url = _field(created, "signed_upload_url", "signedUploadUrl")
    if not isinstance(signed_upload_url, str) or not signed_upload_url.startswith("https://"):
        raise ArtifactUploadError("CreateArtifact returned no HTTPS signed upload URL")
    _mask_signed_url(signed_upload_url)

    blob_request = urllib.request.Request(
        signed_upload_url,
        data=data,
        method="PUT",
        headers={
            "Content-Length": str(len(data)),
            "Content-Type": mime_type,
            "User-Agent": "trillionnium-native-artifact-uploader/1",
            "x-ms-blob-type": "BlockBlob",
            "x-ms-version": "2023-11-03",
        },
    )
    _request_bytes(blob_request, opener=open_request, sleeper=sleeper)

    finalized = _twirp_json(
        origin,
        "FinalizeArtifact",
        {
            **identity,
            "size": str(len(data)),
            "hash": f"sha256:{digest}",
        },
        runtime_token,
        opener=open_request,
        sleeper=sleeper,
    )
    if finalized.get("ok") is not True:
        raise ArtifactUploadError("FinalizeArtifact did not return ok=true")
    artifact_id = _artifact_id(_field(finalized, "artifact_id", "artifactId"))

    return {
        "schema": "trillionnium.actions-artifact-upload.v1",
        "artifact_id": artifact_id,
        "name": name,
        "path": str(path),
        "sha256": digest,
        "size_bytes": len(data),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("name")
    parser.add_argument("path", type=Path)
    parser.add_argument("--mime-type", default="application/gzip")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        runtime_token = os.environ["ACTIONS_RUNTIME_TOKEN"]
        results_url = os.environ["ACTIONS_RESULTS_URL"]
    except KeyError as error:
        print(f"artifact upload failed: missing {error.args[0]}", file=sys.stderr)
        return 1
    try:
        result = upload_artifact(
            args.name,
            args.path,
            args.mime_type,
            runtime_token=runtime_token,
            actions_results_url=results_url,
        )
    except ArtifactUploadError as error:
        print(f"artifact upload failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"artifact_id={result['artifact_id']}\n")
            stream.write(f"sha256={result['sha256']}\n")
            stream.write(f"size_bytes={result['size_bytes']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
