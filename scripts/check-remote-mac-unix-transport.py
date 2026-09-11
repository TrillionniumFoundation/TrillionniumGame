#!/usr/bin/env python3
"""Validate the Unix remote MAC transport boundary."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "crates/trnm-token-crypto-provider/src/remote_unix.rs"
LIB = ROOT / "crates/trnm-token-crypto-provider/src/lib.rs"
CONTRACT = ROOT / "contracts/security/remote-mac-provider.v1.json"

REQUIRED = (
    "UnixSocketRemoteMacTransport",
    "UnixStream::connect",
    "recv_timeout",
    "MAX_PENDING_CONNECTS",
    "remaining_timeout",
    "write_all_before_deadline",
    "read_exact_before_deadline",
    "set_read_timeout",
    "set_write_timeout",
    "MAX_REQUEST_FRAME_BYTES",
    "MAX_RESPONSE_FRAME_BYTES",
    "REQUEST_MAGIC",
    "RESPONSE_MAGIC",
    "ProtocolViolation",
    "TransportUnavailable",
    "sign_round_trip_uses_bounded_opaque_frame",
    "slow_drip_response_cannot_extend_total_deadline",
    "oversized_truncated_and_relative_paths_are_rejected",
)
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


def validate(source: str, lib: str, contract: dict) -> None:
    for marker in REQUIRED:
        require(marker in source, f"Unix MAC transport missing {marker}")
    for marker in FORBIDDEN:
        require(
            marker not in source,
            f"Unix MAC transport contains forbidden marker {marker}",
        )
    require("#[cfg(unix)]\nmod remote_unix;" in lib, "Unix module not gated")
    require(
        "#[cfg(unix)]\npub use remote_unix::UnixSocketRemoteMacTransport;" in lib,
        "Unix export not gated",
    )
    require("fallback" not in source.lower(), "software fallback marker")
    require(
        contract.get("timeout_semantics")
        == "one monotonic total deadline covers connect, request write, response length and response body",
        "total timeout contract",
    )
    require(contract.get("maximum_pending_connects") == 8, "connect budget contract")


def main() -> int:
    try:
        import json

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
