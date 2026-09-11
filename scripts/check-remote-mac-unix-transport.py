#!/usr/bin/env python3
"""Validate the Unix remote MAC transport boundary.

The validator intentionally checks executable production structure rather than
identifier presence.  Test-only code, comments and string literals cannot pay
for the deadline contract.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "crates/trnm-token-crypto-provider/src/remote_unix.rs"
LIB = ROOT / "crates/trnm-token-crypto-provider/src/lib.rs"
CONTRACT = ROOT / "contracts/security/remote-mac-provider.v1.json"

FORBIDDEN = (
    "raw_key",
    "key_material",
    "private_key",
    "SoftwareHs256Provider",
    "new_from_slice",
    "Hmac<",
    "sha256_digest",
)


class ValidationError(RuntimeError):
    pass


def require(value: bool, message: str) -> None:
    if not value:
        raise ValidationError(message)


def production_source(source: str) -> str:
    marker = "#[cfg(test)]"
    return source.split(marker, 1)[0]


def strip_comments_and_literals(source: str) -> str:
    """Return Rust-like executable text with comments/string/char literals blanked.

    This is deliberately a small lexer, not a Rust parser. It preserves braces,
    identifiers and punctuation used by the structural checks below while making
    dead markers in comments or literals ineligible.
    """
    out: list[str] = []
    i = 0
    block_depth = 0
    while i < len(source):
        if block_depth:
            if source.startswith("/*", i):
                block_depth += 1
                out.extend("  ")
                i += 2
            elif source.startswith("*/", i):
                block_depth -= 1
                out.extend("  ")
                i += 2
            else:
                out.append("\n" if source[i] == "\n" else " ")
                i += 1
            continue
        if source.startswith("//", i):
            end = source.find("\n", i)
            if end == -1:
                out.extend(" " * (len(source) - i))
                break
            out.extend(" " * (end - i))
            i = end
            continue
        if source.startswith("/*", i):
            block_depth = 1
            out.extend("  ")
            i += 2
            continue
        ch = source[i]
        # Byte/raw/ordinary strings are irrelevant to the call graph. Handle the
        # forms used by this source and fail closed on unterminated literals.
        prefix = None
        if source.startswith('b"', i):
            prefix = 'b"'
        elif source.startswith('r"', i):
            prefix = 'r"'
        elif source.startswith('br"', i):
            prefix = 'br"'
        elif ch == '"':
            prefix = '"'
        if prefix is not None:
            out.extend(" " * len(prefix))
            i += len(prefix)
            escaped = False
            while i < len(source):
                c = source[i]
                out.append("\n" if c == "\n" else " ")
                i += 1
                if prefix.startswith("r") or prefix.startswith("br"):
                    if c == '"':
                        break
                else:
                    if c == '"' and not escaped:
                        break
                    escaped = (c == "\\" and not escaped)
                    if c != "\\":
                        escaped = False
            continue
        # Rust character literals are not needed by the structural contract.
        if ch == "'" and i + 2 < len(source):
            end = i + 1
            escaped = False
            while end < len(source):
                c = source[end]
                if c == "'" and not escaped:
                    end += 1
                    break
                escaped = (c == "\\" and not escaped)
                if c != "\\":
                    escaped = False
                end += 1
            if end <= len(source) and source[end - 1:end] == "'":
                out.extend(" " * (end - i))
                i = end
                continue
        out.append(ch)
        i += 1
    require(block_depth == 0, "unterminated block comment")
    return "".join(out)


def compact(code: str) -> str:
    return re.sub(r"\s+", "", code)


def require_once(code: str, needle: str, label: str) -> None:
    count = code.count(needle)
    require(count == 1, f"Unix MAC transport missing or ambiguous {label}: count={count}")


def validate(source: str, lib: str, contract: dict) -> None:
    production = production_source(source)
    for marker in FORBIDDEN:
        require(
            marker not in production,
            f"Unix MAC transport contains forbidden marker {marker}",
        )
    require("#[cfg(unix)]\nmod remote_unix;" in lib, "Unix module not gated")
    require(
        "#[cfg(unix)]\npub use remote_unix::UnixSocketRemoteMacTransport;" in lib,
        "Unix export not gated",
    )
    require("fallback" not in production.lower(), "software fallback marker")
    require(
        contract.get("timeout_semantics")
        == "one monotonic total deadline covers connect, request write, response length and response body",
        "total timeout contract",
    )
    require(contract.get("maximum_pending_connects") == 8, "connect budget contract")

    code = compact(strip_comments_and_literals(production))

    # One deadline is created in exchange and the same variable is passed to
    # every I/O stage. These exact call edges are the authority for the source
    # contract; a dead helper or marker elsewhere cannot satisfy them.
    require_once(
        code,
        "letdeadline=Instant::now().checked_add(timeout).ok_or(RemoteMacError::InvalidRequest)?;",
        "exchange deadline creation",
    )
    require_once(
        code,
        "letmutstream=connect_before_deadline(&self.socket_path,deadline)?;",
        "deadline-bound connect edge",
    )
    require_once(
        code,
        "write_all_before_deadline(&mutstream,&length,deadline)?;",
        "deadline-bound request length write",
    )
    require_once(
        code,
        "write_all_before_deadline(&mutstream,&frame,deadline)?;",
        "deadline-bound request body write",
    )
    require_once(
        code,
        "flush_before_deadline(&mutstream,deadline)?;",
        "deadline-bound flush",
    )
    require_once(
        code,
        "read_exact_before_deadline(&mutstream,&mutresponse_length,deadline)?;",
        "deadline-bound response length read",
    )
    require_once(
        code,
        "read_exact_before_deadline(&mutstream,&mutresponse,deadline)?;",
        "deadline-bound response body read",
    )

    # Helpers themselves must derive each blocking wait from remaining time on
    # that deadline. Direct production read_exact/write_all calls are forbidden
    # because they bypass the recomputation loop.
    require_once(
        code,
        "receiver.recv_timeout(remaining_timeout(deadline)?)",
        "connect remaining-time wait",
    )
    require(
        code.count("set_write_timeout(Some(remaining_timeout(deadline)?))") == 2,
        "missing deadline recomputation on write or flush",
    )
    require_once(
        code,
        "set_read_timeout(Some(remaining_timeout(deadline)?))",
        "read remaining-time recomputation",
    )
    require("stream.read_exact(" not in code, "unbounded direct read_exact bypass")
    require("stream.write_all(" not in code, "unbounded direct write_all bypass")
    require_once(
        code,
        "deadline.checked_duration_since(Instant::now()).filter(|remaining|!remaining.is_zero()).ok_or(RemoteMacError::Timeout)",
        "monotonic remaining-time calculation",
    )


def main() -> int:
    try:
        validate(
            SOURCE.read_text(encoding="utf-8"),
            LIB.read_text(encoding="utf-8"),
            json.loads(CONTRACT.read_text(encoding="utf-8")),
        )
    except (OSError, ValueError, ValidationError) as error:
        print(f"remote MAC Unix transport validation failed: {error}", file=sys.stderr)
        return 1
    print("remote MAC Unix transport boundary: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
