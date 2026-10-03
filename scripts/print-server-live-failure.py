#!/usr/bin/env python3
"""Print bounded, redacted server-lane failure context without accepting evidence."""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import stat
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

MAX_LOGS = 2
MAX_READ_BYTES = 65536
MAX_LINES = 80
MAX_OUTPUT_BYTES = 1048576
REDACTED = "<redacted>"
SENSITIVE_KEY = re.compile(
    r"(?i)(?:password|passwd|pgpassword|token|secret|credential|authorization|api[_-]?key|dsn|database[_-]?url)"
)
URI = re.compile(r"(?i)[a-z][a-z0-9+.-]{0,31}://[^\r\n]*")
CREDENTIAL = re.compile(
    r'''(?i)(?:["']?\b[a-z_][a-z0-9_.-]*(?:password|passwd|token|secret|credential|authorization|api[_-]?key|dsn|database[_-]?url)[a-z0-9_.-]*["']?|["']?\b(?:password|passwd|token|secret|credential|authorization|api[_-]?key|dsn|database[_-]?url)["']?)\s*[=:][^\r\n]*'''
)


def secret_literals(environment: dict[str, str]) -> list[str]:
    values: set[str] = set()
    for key, value in environment.items():
        if value and (SENSITIVE_KEY.search(key) or key in {"TRNM_DATABASE_URL", "TRNM_SCHEMA_UPGRADE_ADMIN_DATABASE_URL"}):
            values.add(value)
        if value and ("DATABASE_URL" in key or "DSN" in key):
            try:
                password = urlsplit(value).password
            except ValueError:
                password = None
            if not password and "://" in value and "@" in value:
                # Failed config validation can leave malformed URL punctuation.
                # Conservatively hide its raw userinfo as well as parsed values.
                userinfo = value.split("://", 1)[1].rsplit("@", 1)[0]
                _, separator, password = userinfo.partition(":")
                if not separator:
                    password = None
            if password:
                values.update({password, unquote(password)})
    for value in list(values):
        values.update({json.dumps(value, ensure_ascii=True)[1:-1], shlex.quote(value), quote(value, safe="")})
        # An environment secret can contain line breaks. Its individual parts
        # must also disappear when a log renders it on separate lines.
        values.update(part for part in value.splitlines() if part)
    return sorted((v for v in values if v), key=len, reverse=True)


def redact(text: str, literals: list[str]) -> str:
    # Scrub generic syntax first: a literal secret may itself be "password" or
    # ":", and replacing it must not disable detection of another credential.
    text = URI.sub("<redacted-uri>", text)
    text = CREDENTIAL.sub("<redacted-credential-field>", text)
    if literals:
        # One pass never rescans generated markers. Repeated replacements for
        # short secrets could otherwise grow the bounded input at every pass.
        pattern = re.compile("|".join(re.escape(literal) for literal in literals))
        text = pattern.sub(REDACTED, text)
    return text


def read_log(name: str, literals: list[str]) -> dict[str, object]:
    result: dict[str, object] = {"log": redact(Path(name).name, literals)[:128]}
    fd = None
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError("not a regular diagnostic log")
        offset = max(0, metadata.st_size - MAX_READ_BYTES)
        os.lseek(fd, offset, os.SEEK_SET)
        data = os.read(fd, MAX_READ_BYTES)
        read_bytes = len(data)
        if offset:
            # The first bytes can begin inside an unknown credential fragment.
            data = data.partition(b"\n")[2]
        partial_last_line = bool(data) and not data.endswith(b"\n")
        if partial_last_line:
            # A still-running process or a short read can end inside a secret.
            # Only completed lines are eligible for diagnostic publication.
            data = data.rpartition(b"\n")[0]
        lines = redact(data.decode("utf-8", errors="replace"), literals).splitlines()
        result.update({"lines": lines[-MAX_LINES:], "read_bytes": read_bytes,
                       "partial_first_line_discarded": offset > 0,
                       "partial_last_line_discarded": partial_last_line,
                       "earlier_lines_omitted": len(lines) > MAX_LINES})
    except (OSError, ValueError):
        result["unavailable"] = True
    finally:
        if fd is not None:
            os.close(fd)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--status", type=int, required=True)
    parser.add_argument("logs", nargs="*")
    args = parser.parse_args()
    if not 1 <= args.status <= 255 or len(args.logs) > MAX_LOGS:
        parser.error("invalid failure status or diagnostic log count")
    literals = secret_literals(dict(os.environ))
    report = {"schema": "trillionnium.server-live-failure.v1",
              "profile": redact(args.profile, literals)[:128],
              "stage": redact(args.stage, literals)[:128], "exit_code": args.status,
              "limits": {"max_logs": MAX_LOGS, "read_bytes_per_log": MAX_READ_BYTES,
                         "lines_per_log": MAX_LINES, "output_bytes": MAX_OUTPUT_BYTES},
              "logs": [read_log(name, literals) for name in args.logs]}
    # JSON escaping protects control bytes; escaping the command delimiter also
    # prevents GitHub Actions from interpreting embedded workflow commands.
    output = json.dumps(report, ensure_ascii=True, separators=(",", ":")).replace("::", "\\u003a\\u003a")
    if len(output.encode()) + 1 > MAX_OUTPUT_BYTES:
        report["logs"] = [{"diagnostics_omitted": "output budget exceeded"}]
        output = json.dumps(report, ensure_ascii=True, separators=(",", ":")).replace("::", "\\u003a\\u003a")
    print(output, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
