#!/usr/bin/env python3
"""Validate the bounded Rust server source slice without granting product credit.

The repository has one production-candidate server binary in
``trnm-persistence-pg`` and one explicitly standalone in-memory foundation
prototype in ``crates/trnm-server``.  This checker validates the latter's
fail-closed source contract.  It must not resurrect the retired alternate
``trnm-server`` binary that previously lived in ``trnm-persistence-core``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "crates/trnm-server/Cargo.toml"
LIBRARY = ROOT / "crates/trnm-server/src/lib.rs"
BINARY = ROOT / "crates/trnm-server/src/main.rs"
README = ROOT / "crates/trnm-server/README.md"
STATUS = ROOT / "docs/status/RUST_SERVER_VERTICAL_SLICE_STATUS.json"
CANONICAL_SERVER = ROOT / "crates/trnm-persistence-pg/src/bin/trnm-server.rs"
RETIRED_ALTERNATE = ROOT / "crates/trnm-persistence-core/src/bin/trnm-server.rs"


class ContractError(RuntimeError):
    """Raised when the source slice violates its fail-closed contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def read(path: Path) -> str:
    require(path.is_file(), f"missing required file: {path.relative_to(ROOT)}")
    value = path.read_text(encoding="utf-8")
    require(value.endswith("\n"), f"file lacks trailing newline: {path.relative_to(ROOT)}")
    require("\r" not in value, f"CRLF is forbidden: {path.relative_to(ROOT)}")
    return value


def load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(read(path))
    except json.JSONDecodeError as error:
        raise ContractError(f"{path.relative_to(ROOT)}: invalid JSON: {error}") from error
    require(isinstance(value, dict), f"{path.relative_to(ROOT)}: root must be an object")
    return value


def validate() -> dict[str, object]:
    require(CANONICAL_SERVER.is_file(), "canonical PostgreSQL server binary is missing")
    require(
        not RETIRED_ALTERNATE.exists(),
        f"retired alternate server binary still exists: {RETIRED_ALTERNATE.relative_to(ROOT)}",
    )

    manifest = read(MANIFEST)
    source = read(LIBRARY) + "\n" + read(BINARY)
    readme = read(README)
    status = load_json(STATUS)

    require('name = "trnm-server"' in manifest, "standalone foundation package name missing")
    require("[workspace]" in manifest, "foundation prototype must remain an explicit standalone workspace")

    required_source_tokens = [
        "#![forbid(unsafe_code)]",
        "sync_channel",
        "queue_capacity",
        "worker_count",
        "set_read_timeout",
        "set_write_timeout",
        "AtomicBool",
        '"/healthz"',
        '"/readyz"',
        '"/v1/bootstrap"',
        '"/v1/command"',
        "PrepareOutcome::Duplicate",
        "durable.commit",
        "request_queue_full",
        "shutdown.try_recv",
        "ServerConfig::from_env",
    ]
    for token in required_source_tokens:
        require(token in source, f"server source missing token: {token}")

    for token in (
        "unsafe {",
        "postgres::Client",
        "compatibility_credit=true",
        "production_ready=true",
    ):
        require(token not in source, f"server source contains forbidden token: {token}")

    require(
        status.get("schema") == "trillionnium.rust-server-vertical-slice-status.v1",
        "wrong status schema",
    )
    require(status.get("status") == "source-candidate", "server status must remain source-candidate")
    require(status.get("implementation") == "crates/trnm-server", "status implementation authority drift")

    claims = status.get("claims")
    require(isinstance(claims, dict), "status claims must be an object")
    require(claims.get("source_vertical_slice_exists") is True, "source-presence fact must remain explicit")
    product_claims = (
        "nakama_wire_compatible",
        "database_durable",
        "sg4_complete",
        "compatibility_credit",
        "production_ready",
        "public_online",
        "nakama_replaced",
    )
    for name in product_claims:
        require(claims.get(name) is False, f"product claim must remain false: {name}")

    not_implemented = status.get("not_implemented")
    require(isinstance(not_implemented, list), "not_implemented must be a list")
    for required_gap in (
        "PostgreSQL repository binding",
        "gRPC and grpc-gateway",
        "WebSocket JSON and protobuf",
        "immutable oracle differential",
    ):
        require(required_gap in not_implemented, f"status omits limitation: {required_gap}")

    for marker in (
        "compatibility_credit=false",
        "database_durability_credit=false",
        "sg4_credit=false",
        "production_ready=false",
    ):
        require(marker in readme, f"foundation README missing marker: {marker}")

    return {
        "schema": "trillionnium.rust-server-slice-contract.v2",
        "source": str(LIBRARY.relative_to(ROOT)),
        "canonical_server": str(CANONICAL_SERVER.relative_to(ROOT)),
        "source_tokens": len(required_source_tokens),
        "claims_all_false": True,
        "status": "passed",
        "compatibility_credit": False,
    }


def main() -> int:
    try:
        result = validate()
    except (OSError, ContractError) as error:
        print(f"Rust server slice contract failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
