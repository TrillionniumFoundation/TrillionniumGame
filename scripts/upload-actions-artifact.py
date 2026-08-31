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


def validate_artifact(name: str, path: Path) -> bytes:
    if NAME_PATTERN.fullmatch(name) is None:
        raise ArtifactUploadError("artifact name is not canonical")
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ArtifactUploadError("artifact file cannot be inspected") from error
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise ArtifactUploadError("artifact path must be a regular non-symlink file")
    if metadata.st_size <= 0 or metadata.st_size > MAX_ARTIFACT_BYTES:
        raise ArtifactUploadError("artifact size is outside the bounded non-empty range")
    try:
        data = path.read_bytes()
    except OSError as error:
        raise ArtifactUploadError("artifact file cannot be read") from error
    if len(data) != metadata.st_size:
        raise ArtifactUploadError("artifact file changed while being read")
    return data


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
                body = response.read(MAX_ARTIFACT_BYTES + 1)
            if status_code in TRANSIENT_HTTP_STATUSES:
                raise urllib.error.HTTPError(
                    request.full_url,
                    status_code,
                    "transient artifact service response",
                    hdrs=None,
                    fp=None,
                )
            if status_code < 200 or status_code >= 300:
                raise ArtifactUploadError(f"artifact service returned HTTP {status_code}")
            return status_code, body
        except urllib.error.HTTPError as error:
            if error.code not in TRANSIENT_HTTP_STATUSES or attempt == MAX_ATTEMPTS:
                raise ArtifactUploadError(f"artifact service returned HTTP {error.code}") from error
        except urllib.error.URLError as error:
            if attempt == MAX_ATTEMPTS:
                raise ArtifactUploadError("artifact service transport failed") from error
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
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "trillionnium-native-artifact-uploader/1",
        },
    )
    _, body = _request_bytes(request, opener=opener, sleeper=sleeper)
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ArtifactUploadError(f"{method} response is not valid JSON") from error
    if not isinstance(value, dict):
        raise ArtifactUploadError(f"{method} response must be an object")
    return value


def _field(value: dict[str, Any], snake: str, camel: str) -> Any:
    return value.get(snake, value.get(camel))


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
    open_request = urllib.request.urlopen if opener is None else opener

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
            "metadata": {"wrapper": {"mime_type": mime_type}},
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
    artifact_id = _field(finalized, "artifact_id", "artifactId")
    if isinstance(artifact_id, int):
        artifact_id = str(artifact_id)
    if not isinstance(artifact_id, str) or not artifact_id or artifact_id == "0":
        raise ArtifactUploadError("FinalizeArtifact returned no non-zero artifact ID")

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
