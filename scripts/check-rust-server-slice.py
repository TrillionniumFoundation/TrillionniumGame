#!/usr/bin/env python3
"""Validate the first Rust server vertical slice without granting compatibility credit."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "crates/trnm-persistence-core/src/bin/trnm-server.rs"
STATUS = ROOT / "docs/status/RUST_SERVER_VERTICAL_SLICE_STATUS.json"
DOCUMENT = ROOT / "docs/development/RUST_SERVER_VERTICAL_SLICE_ALPHA.md"
SCHEMA_AUTHORITY = ROOT / "docs/development/SCHEMA_AUTHORITY.json"


class ContractError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"{path.relative_to(ROOT)}: invalid JSON: {error}") from error
    require(isinstance(value, dict), f"{path.relative_to(ROOT)}: root must be an object")
    return value


def validate() -> dict[str, object]:
    for path in (SOURCE, STATUS, DOCUMENT, SCHEMA_AUTHORITY):
        require(path.is_file(), f"missing required file: {path.relative_to(ROOT)}")

    source = SOURCE.read_text(encoding="utf-8")
    document = DOCUMENT.read_text(encoding="utf-8")
    status = load_json(STATUS)
    authority = load_json(SCHEMA_AUTHORITY)

    required_source_tokens = [
        "#![forbid(unsafe_code)]",
        "const MAX_REQUEST_BYTES: usize = 64 * 1024;",
        '"serve"',
        '"healthcheck"',
        '"migrate-contract"',
        '"/healthz"',
        '"/readyz"',
        '"/version"',
        '"/v1/command"',
        '"/v1/realtime"',
        "PrepareOutcome::Prepared",
        "PrepareOutcome::Duplicate",
        "authority_generation",
        "expected_revision",
        "IntentKind::Broadcast",
        "websocket_adapter_not_implemented",
        "compatibility_credit=false",
    ]
    for token in required_source_tokens:
        require(token in source, f"server source missing token: {token}")

    forbidden_source_tokens = [
        "TcpListener::bind(\"0.0.0.0",
        "compatibility_credit=true",
        "production_ready=true",
        "unsafe {",
    ]
    for token in forbidden_source_tokens:
        require(token not in source, f"server source contains forbidden token: {token}")

    require(status.get("schema") == "trillionnium.rust-server-vertical-slice-status.v1", "wrong status schema")
    require(status.get("status") == "source-candidate", "server status must remain source-candidate")
    binary = status.get("binary")
    require(isinstance(binary, dict), "status binary must be an object")
    require(binary.get("path") == str(SOURCE.relative_to(ROOT)), "status binary path mismatch")

    claims = status.get("claims")
    require(isinstance(claims, dict) and claims, "status claims must be a non-empty object")
    require(not any(bool(value) for value in claims.values()), "no Rust server claim may be true before evidence")

    not_implemented = status.get("not_implemented")
    require(isinstance(not_implemented, list), "not_implemented must be a list")
    for required_gap in (
        "live PostgreSQL repository binding",
        "HTTP/2 gRPC and grpc-gateway",
        "WebSocket JSON and protobuf adapters",
        "immutable Nakama differential",
    ):
        require(required_gap in not_implemented, f"status omits limitation: {required_gap}")

    authority_value = authority.get("authority")
    require(isinstance(authority_value, dict), "schema authority object missing")
    profiles = authority_value.get("profiles")
    require(isinstance(profiles, list), "schema authority profiles missing")
    profile_paths = {row.get("id"): row.get("path") for row in profiles if isinstance(row, dict)}
    require(profile_paths == {
        "postgresql": "migrations/postgresql",
        "cockroachdb": "migrations/cockroachdb",
    }, "Rust server migration profiles do not match schema authority")

    for marker in (
        "Status: source candidate only",
        "compatibility_credit=false",
        "live PostgreSQL or CockroachDB transaction repository binding",
        "WebSocket handshake/frame processing",
        "immutable Nakama oracle differential",
    ):
        require(marker in document, f"server document missing marker: {marker}")

    return {
        "schema": "trillionnium.rust-server-vertical-slice-contract.v1",
        "source": str(SOURCE.relative_to(ROOT)),
        "source_tokens": len(required_source_tokens),
        "profiles": profile_paths,
        "claims_all_false": True,
        "status": "passed",
        "compatibility_credit": False,
    }


def main() -> int:
    try:
        result = validate()
    except ContractError as error:
        print(f"Rust server vertical-slice contract failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
