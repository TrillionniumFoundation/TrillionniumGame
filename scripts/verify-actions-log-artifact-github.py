#!/usr/bin/env python3
"""Run the retained-log verifier against GitHub transport conventions.

The repository Contents API line-wraps RFC 4648 base64.  The Actions job-log
endpoint returns an authenticated GitHub redirect to an expiring external
object URL.  This adapter normalizes only the Contents whitespace and follows
that redirect without forwarding the repository bearer token.  Every identity,
Git blob, workflow/job, archive and evidence assertion remains delegated to the
core verifier.
"""
from __future__ import annotations

import importlib.util
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CORE_PATH = ROOT / "scripts/verify-actions-log-artifact.py"
REDIRECT_CODES = {301, 302, 303, 307, 308}


def load_core() -> Any:
    spec = importlib.util.spec_from_file_location(
        "verify_actions_log_artifact_core", CORE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load retained-log verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CORE = load_core()
_CORE_DECODE_CONTENTS = CORE.decode_contents


def decode_contents(payload: dict[str, Any], label: str) -> bytes:
    normalized = dict(payload)
    content = normalized.get("content")
    if isinstance(content, str):
        normalized["content"] = "".join(content.split())
    return _CORE_DECODE_CONTENTS(normalized, label)


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


def _github_log_request(token: str, url: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "trillionnium-outbox-log-verifier/3",
        },
    )


def _unsigned_redirect_request(location: str) -> urllib.request.Request:
    parsed = urllib.parse.urlsplit(location)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise CORE.VerificationError("GitHub log redirect is not a safe HTTPS URL")
    return urllib.request.Request(
        location,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": "trillionnium-outbox-log-verifier/3",
        },
    )


def _redacted_location(location: str) -> str:
    parsed = urllib.parse.urlsplit(location)
    host = parsed.hostname or "invalid-host"
    return urllib.parse.urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def _read_nonempty(response: Any, label: str) -> bytes:
    data = response.read()
    if not data:
        raise CORE.VerificationError(f"GitHub log response is empty: {label}")
    return data


def request_bytes(token: str, url: str) -> bytes:
    if not token:
        raise CORE.VerificationError("GITHUB_TOKEN is required for job-log discovery")
    opener = urllib.request.build_opener(_NoRedirect())
    last_error: Exception | None = None
    for attempt in range(20):
        request = _github_log_request(token, url)
        try:
            with opener.open(request, timeout=60) as response:
                return _read_nonempty(response, url)
        except urllib.error.HTTPError as error:
            last_error = error
            if error.code in REDIRECT_CODES:
                location = error.headers.get("Location")
                if not isinstance(location, str) or not location:
                    raise CORE.VerificationError(
                        f"GitHub log redirect has no Location header: {url}"
                    ) from error
                unsigned_request = _unsigned_redirect_request(location)
                try:
                    with urllib.request.urlopen(unsigned_request, timeout=60) as response:
                        return _read_nonempty(
                            response, _redacted_location(location)
                        )
                except urllib.error.HTTPError as download_error:
                    last_error = download_error
                    if attempt == 19:
                        detail = download_error.read().decode("utf-8", "replace")[:1000]
                        raise CORE.VerificationError(
                            "GitHub signed log download failed: "
                            f"{_redacted_location(location)}: "
                            f"HTTP {download_error.code}: {detail}"
                        ) from download_error
                except urllib.error.URLError as download_error:
                    last_error = download_error
                    if attempt == 19:
                        raise CORE.VerificationError(
                            "GitHub signed log download failed: "
                            f"{_redacted_location(location)}: {download_error}"
                        ) from download_error
            elif error.code not in {404, 409} or attempt == 19:
                detail = error.read().decode("utf-8", "replace")[:1000]
                raise CORE.VerificationError(
                    f"GitHub log request failed: {url}: HTTP {error.code}: {detail}"
                ) from error
        except urllib.error.URLError as error:
            last_error = error
            if attempt == 19:
                raise CORE.VerificationError(
                    f"GitHub log request failed: {url}: {error}"
                ) from error
        time.sleep(2)
    raise CORE.VerificationError(f"GitHub log request failed: {url}: {last_error}")


def main() -> int:
    CORE.decode_contents = decode_contents
    CORE.request_bytes = request_bytes
    return int(CORE.main())


if __name__ == "__main__":
    raise SystemExit(main())
