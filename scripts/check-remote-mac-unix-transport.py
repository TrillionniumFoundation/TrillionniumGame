#!/usr/bin/env python3
"""Validate the Unix remote MAC transport boundary.

The validator checks executable production structure rather than identifier
presence. Test-only code, comments and string literals cannot pay for deadline
or peer-identity obligations. The transport remains a local-service source
candidate: filesystem UID/GID and stable inode checks narrow endpoint
substitution but do not replace a production KMS/HSM trust decision.
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


def source_parts(source: str) -> tuple[str, str]:
    marker = "#[cfg(test)]"
    parts = source.split(marker, 1)
    require(len(parts) == 2, "Unix MAC transport missing focused test module")
    return parts[0], parts[1]


def strip_comments_and_literals(source: str) -> str:
    """Return Rust-like executable text with comments/string/char literals blanked."""
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
                    escaped = c == "\\" and not escaped
                    if c != "\\":
                        escaped = False
            continue
        if ch == "'" and i + 2 < len(source):
            end = i + 1
            escaped = False
            while end < len(source):
                c = source[end]
                if c == "'" and not escaped:
                    end += 1
                    break
                escaped = c == "\\" and not escaped
                if c != "\\":
                    escaped = False
                end += 1
            if end <= len(source) and source[end - 1 : end] == "'":
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
    production, tests = source_parts(source)
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
    require(
        contract.get("unix_transport_profile") == "local-service-source-candidate",
        "Unix transport profile",
    )
    peer = contract.get("unix_peer_authentication")
    require(isinstance(peer, dict), "Unix peer-authentication contract")
    for key in (
        "expected_uid_required",
        "expected_gid_required",
        "socket_inode_rechecked_after_connect",
        "symlink_directory_components_rejected",
        "direct_parent_world_writable_rejected",
        "socket_world_writable_rejected",
        "socket_path_redacted_from_debug",
        "same_uid_or_trusted_group_compromise_remains_out_of_scope",
    ):
        require(peer.get(key) is True, f"Unix peer-authentication contract {key}")
    claims = contract.get("claim_boundary")
    require(
        isinstance(claims, dict) and claims and not any(claims.values()),
        "positive Unix transport claim",
    )

    code = compact(strip_comments_and_literals(production))
    test_code = compact(strip_comments_and_literals(tests))

    require_once(
        code,
        "letdeadline=Instant::now().checked_add(timeout).ok_or(RemoteMacError::InvalidRequest)?;",
        "exchange deadline creation",
    )
    require_once(
        code,
        "letexpected_endpoint=inspect_socket_endpoint(&self.socket_path,self.expected_peer_uid,self.expected_peer_gid)?;",
        "pre-connect endpoint identity",
    )
    require_once(
        code,
        "letmutstream=connect_before_deadline(&self.socket_path,self.expected_peer_uid,self.expected_peer_gid,expected_endpoint,deadline)?;",
        "deadline and identity-bound connect edge",
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

    require_once(
        code,
        "letobserved_endpoint=inspect_socket_endpoint(path,expected_peer_uid,expected_peer_gid)?;",
        "post-connect endpoint recheck",
    )
    require_once(
        code,
        "ifobserved_endpoint!=expected_endpoint{returnErr(RemoteMacError::ProtocolViolation);}",
        "stable socket inode comparison",
    )
    require_once(
        code,
        "socket_metadata.uid()!=expected_peer_uid",
        "peer UID comparison",
    )
    require_once(
        code,
        "socket_metadata.gid()!=expected_peer_gid",
        "peer GID comparison",
    )
    require_once(code, "socket_metadata.nlink()!=1", "socket link-count check")
    require_once(
        code,
        "socket_metadata.mode()&0o002!=0",
        "socket world-write rejection",
    )
    require_once(
        code,
        "mode&0o002==0",
        "direct-parent world-write rejection",
    )
    require_once(
        code,
        "metadata.file_type().is_symlink()||!metadata.is_dir()",
        "symlink directory rejection",
    )
    require(
        code.count("fs::symlink_metadata(") >= 3,
        "descriptor path identity must use symlink_metadata",
    )
    require_once(
        code,
        "implfmt::DebugforUnixSocketRemoteMacTransport",
        "redacted transport Debug",
    )
    require(
        "<redacted-socket-path>" in production,
        "socket path redaction marker",
    )

    require_once(
        test_code,
        "fnslow_drip_response_cannot_extend_total_deadline(){",
        "slow-drip total-deadline regression",
    )
    require_once(
        test_code,
        "fnsign_round_trip_uses_bounded_opaque_frame(){",
        "bounded opaque-frame regression",
    )
    require_once(
        test_code,
        "fnwrong_peer_identity_and_insecure_parent_fail_closed(){",
        "peer-identity hostile regression",
    )
    require_once(
        test_code,
        "fntransport_debug_redacts_socket_path(){",
        "transport path redaction regression",
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
