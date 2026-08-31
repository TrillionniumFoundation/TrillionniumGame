#!/usr/bin/env python3
"""Emit and reconstruct a bounded archive carried by GitHub Actions job logs."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

SCHEMA = "trillionnium.actions-log-artifact.v1"
TRANSPORT = "github-actions-job-log-base64-v1"
BARE_LOG_STYLE = "bare-envelope-v1"
GITHUB_LOG_STYLE = "github-actions-timestamped-v1"
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
RESERVED = "TRNM_LOG_ARTIFACT_"
GITHUB_PREFIX = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z) "
    r"(?P<body>TRNM_LOG_ARTIFACT_(?:BEGIN|B64|END) .*)$"
)


class EnvelopeError(ValueError):
    """Raised when an archive or retained-log envelope violates the contract."""


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
    chunks = [
        encoded[index : index + LINE_WIDTH]
        for index in range(0, len(encoded), LINE_WIDTH)
    ]
    lines = [
        f"TRNM_LOG_ARTIFACT_BEGIN name={name} sha256={digest} size={size} encoding=base64",
        *(PREFIX + chunk for chunk in chunks),
        f"TRNM_LOG_ARTIFACT_END name={name} sha256={digest} size={size} lines={len(chunks)}",
    ]
    metadata: dict[str, object] = {
        "schema": SCHEMA,
        "transport": TRANSPORT,
        "log_style": BARE_LOG_STYLE,
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


def _valid_timestamp(value: str) -> None:
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise EnvelopeError(f"invalid GitHub Actions log timestamp: {value!r}") from error
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise EnvelopeError(f"GitHub Actions log timestamp is not UTC: {value!r}")


def _canonical_marker_lines(
    text: str,
) -> tuple[list[str], list[int], str, list[str]]:
    source_lines = text.splitlines()
    normalized: list[str] = []
    indexes: list[int] = []
    styles: list[str] = []
    timestamps: list[str] = []

    for index, raw_line in enumerate(source_lines):
        line = raw_line.lstrip("\ufeff") if index == 0 else raw_line
        if RESERVED not in line:
            continue
        if line.startswith(RESERVED):
            body = line
            style = BARE_LOG_STYLE
            timestamp = ""
        else:
            match = GITHUB_PREFIX.fullmatch(line)
            if match is None:
                raise EnvelopeError(
                    f"reserved artifact token is not a canonical log line at source line {index + 1}"
                )
            timestamp = match.group("timestamp")
            _valid_timestamp(timestamp)
            body = match.group("body")
            style = GITHUB_LOG_STYLE
        if body.count(RESERVED) != 1:
            raise EnvelopeError(
                f"artifact log line contains multiple reserved tokens at source line {index + 1}"
            )
        normalized.append(body)
        indexes.append(index)
        styles.append(style)
        if timestamp:
            timestamps.append(timestamp)

    if not normalized:
        raise EnvelopeError("retained log contains no artifact envelope")
    if len(set(styles)) != 1:
        raise EnvelopeError("mixed bare and GitHub-timestamped artifact lines are forbidden")
    style = styles[0]
    if style == GITHUB_LOG_STYLE:
        if len(timestamps) != len(normalized):
            raise EnvelopeError("timestamped artifact envelope has an unprefixed line")
        if timestamps != sorted(timestamps):
            raise EnvelopeError("GitHub Actions artifact timestamps are not monotonic")
    return normalized, indexes, style, timestamps


def parse(text: str) -> tuple[bytes, dict[str, object]]:
    lines, source_indexes, log_style, timestamps = _canonical_marker_lines(text)
    begin_indexes = [
        index
        for index, line in enumerate(lines)
        if line.startswith("TRNM_LOG_ARTIFACT_BEGIN ")
    ]
    end_indexes = [
        index
        for index, line in enumerate(lines)
        if line.startswith("TRNM_LOG_ARTIFACT_END ")
    ]
    if len(begin_indexes) != 1 or len(end_indexes) != 1:
        raise EnvelopeError("expected exactly one begin and one end marker")
    begin_index, end_index = begin_indexes[0], end_indexes[0]
    if begin_index != 0 or end_index != len(lines) - 1:
        raise EnvelopeError("artifact marker lines outside the unique envelope are forbidden")
    if begin_index >= end_index:
        raise EnvelopeError("end marker precedes begin marker")
    if source_indexes != list(range(source_indexes[0], source_indexes[-1] + 1)):
        raise EnvelopeError("non-artifact log lines interrupt the retained envelope")

    begin = BEGIN.fullmatch(lines[begin_index])
    end = END.fullmatch(lines[end_index])
    if begin is None or end is None:
        raise EnvelopeError("malformed begin or end marker")
    if begin.group("name", "sha", "size") != end.group("name", "sha", "size"):
        raise EnvelopeError("begin/end identity mismatch")
    validate_name(begin.group("name"))

    payload_lines = lines[begin_index + 1 : end_index]
    if len(payload_lines) != int(end.group("lines")):
        raise EnvelopeError("payload line count mismatch")
    if not payload_lines or any(not line.startswith(PREFIX) for line in payload_lines):
        raise EnvelopeError("malformed payload line")
    payload_chunks = [line[len(PREFIX) :] for line in payload_lines]
    if any(not chunk or len(chunk) > LINE_WIDTH for chunk in payload_chunks):
        raise EnvelopeError("payload chunk is empty or exceeds the canonical line width")
    if any(len(chunk) != LINE_WIDTH for chunk in payload_chunks[:-1]):
        raise EnvelopeError("non-final payload chunk has a non-canonical width")
    try:
        data = base64.b64decode("".join(payload_chunks), validate=True)
    except ValueError as error:
        raise EnvelopeError("invalid base64 payload") from error

    expected_size = int(begin.group("size"))
    expected_sha = begin.group("sha")
    if expected_size <= 0 or expected_size > DEFAULT_MAX_BYTES:
        raise EnvelopeError("declared archive size is outside the global bound")
    if len(data) != expected_size:
        raise EnvelopeError("decoded size mismatch")
    actual_sha = hashlib.sha256(data).hexdigest()
    if actual_sha != expected_sha:
        raise EnvelopeError("decoded SHA-256 mismatch")

    metadata: dict[str, object] = {
        "schema": SCHEMA,
        "transport": TRANSPORT,
        "log_style": log_style,
        "name": begin.group("name"),
        "sha256": actual_sha,
        "size": len(data),
        "encoding": "base64",
        "data_line_count": len(payload_lines),
        "source_begin_line": source_indexes[0] + 1,
        "source_end_line": source_indexes[-1] + 1,
    }
    if timestamps:
        metadata["first_log_timestamp"] = timestamps[0]
        metadata["last_log_timestamp"] = timestamps[-1]
    return data, metadata


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
