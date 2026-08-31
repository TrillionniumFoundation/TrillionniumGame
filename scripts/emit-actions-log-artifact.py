#!/usr/bin/env python3
"""Emit a bounded binary archive as a reconstructable GitHub Actions log envelope."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
from pathlib import Path

SCHEMA = "trillionnium.actions-log-artifact.v1"
TRANSPORT = "github-actions-job-log-base64-v1"
DEFAULT_MAX_BYTES = 2 * 1024 * 1024
LINE_WIDTH = 76
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
BEGIN = re.compile(
    r"^TRNM_LOG_ARTIFACT_BEGIN name=(?P<name>\S+) sha256=(?P<sha>[0-9a-f]{64}) "
    r"size=(?P<size>[0-9]+) encoding=base64$"
)
END = re.compile(
    r"^TRNM_LOG_ARTIFACT_END name=(?P<name>\S+) sha256=(?P<sha>[0-9a-f]{64}) "
    r"size=(?P<size>[0-9]+) lines=(?P<lines>[0-9]+)$"
)
PREFIX = "TRNM_LOG_ARTIFACT_B64 "


class EnvelopeError(ValueError):
    """Raised when an archive or log envelope violates the bounded contract."""


def validate_name(value: str) -> str:
    if NAME.fullmatch(value) is None:
        raise EnvelopeError("artifact name must be 1-200 safe ASCII characters")
    return value


def envelope(data: bytes, name: str, max_bytes: int) -> tuple[str, dict[str, object]]:
    validate_name(name)
    if max_bytes <= 0:
        raise EnvelopeError("max_bytes must be positive")
    size = len(data)
    if size == 0:
        raise EnvelopeError("archive is empty")
    if size > max_bytes:
        raise EnvelopeError(f"archive exceeds bound: size={size} max_bytes={max_bytes}")
    digest = hashlib.sha256(data).hexdigest()
    encoded = base64.b64encode(data).decode("ascii")
    chunks = [encoded[index : index + LINE_WIDTH] for index in range(0, len(encoded), LINE_WIDTH)]
    lines = [
        f"TRNM_LOG_ARTIFACT_BEGIN name={name} sha256={digest} size={size} encoding=base64",
        *(PREFIX + chunk for chunk in chunks),
        f"TRNM_LOG_ARTIFACT_END name={name} sha256={digest} size={size} lines={len(chunks)}",
    ]
    metadata: dict[str, object] = {
        "schema": SCHEMA,
        "transport": TRANSPORT,
        "name": name,
        "sha256": digest,
        "size": size,
        "encoding": "base64",
        "data_line_count": len(chunks),
        "max_bytes": max_bytes,
        "repository": os.environ.get("GITHUB_REPOSITORY", ""),
        "commit": os.environ.get("CANDIDATE_SHA") or os.environ.get("GITHUB_SHA", ""),
        "run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "job": os.environ.get("GITHUB_JOB", ""),
        "claims": {
            "compatibility_credit": False,
            "production_ready": False,
        },
    }
    return "\n".join(lines) + "\n", metadata


def parse(text: str) -> tuple[bytes, dict[str, object]]:
    lines = text.splitlines()
    begin_indexes = [index for index, line in enumerate(lines) if line.startswith("TRNM_LOG_ARTIFACT_BEGIN ")]
    end_indexes = [index for index, line in enumerate(lines) if line.startswith("TRNM_LOG_ARTIFACT_END ")]
    if len(begin_indexes) != 1 or len(end_indexes) != 1:
        raise EnvelopeError("expected exactly one begin and one end marker")
    begin_index, end_index = begin_indexes[0], end_indexes[0]
    if begin_index >= end_index:
        raise EnvelopeError("end marker precedes begin marker")
    begin = BEGIN.fullmatch(lines[begin_index])
    end = END.fullmatch(lines[end_index])
    if begin is None or end is None:
        raise EnvelopeError("malformed begin or end marker")
    if begin.group("name", "sha", "size") != end.group("name", "sha", "size"):
        raise EnvelopeError("begin/end identity mismatch")
    payload_lines = lines[begin_index + 1 : end_index]
    if len(payload_lines) != int(end.group("lines")):
        raise EnvelopeError("payload line count mismatch")
    if not payload_lines or any(not line.startswith(PREFIX) for line in payload_lines):
        raise EnvelopeError("malformed payload line")
    try:
        data = base64.b64decode(
            "".join(line[len(PREFIX) :] for line in payload_lines),
            validate=True,
        )
    except ValueError as error:
        raise EnvelopeError("invalid base64 payload") from error
    expected_size = int(begin.group("size"))
    expected_sha = begin.group("sha")
    if len(data) != expected_size:
        raise EnvelopeError("decoded size mismatch")
    actual_sha = hashlib.sha256(data).hexdigest()
    if actual_sha != expected_sha:
        raise EnvelopeError("decoded SHA-256 mismatch")
    return data, {
        "schema": SCHEMA,
        "transport": TRANSPORT,
        "name": begin.group("name"),
        "sha256": actual_sha,
        "size": len(data),
        "encoding": "base64",
        "data_line_count": len(payload_lines),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--metadata-json", type=Path)
    arguments = parser.parse_args()
    try:
        if not arguments.archive.is_file():
            raise EnvelopeError(f"archive is not a regular file: {arguments.archive}")
        text, metadata = envelope(
            arguments.archive.read_bytes(), arguments.name, arguments.max_bytes
        )
        # The fixed prefix prevents binary data from being interpreted as workflow
        # commands; base64 contains no colon and never begins with "::".
        sys.stdout.write(text)
        sys.stdout.flush()
        if arguments.metadata_json is not None:
            arguments.metadata_json.parent.mkdir(parents=True, exist_ok=True)
            arguments.metadata_json.write_text(
                json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
        return 0
    except (OSError, EnvelopeError) as error:
        print(f"actions log artifact failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
