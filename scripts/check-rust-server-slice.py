#!/usr/bin/env python3
"""Validate server composition convergence without prematurely transferring authority."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRATE = ROOT / "crates/trnm-server"
STATUS = ROOT / "docs/status/RUST_SERVER_VERTICAL_SLICE_STATUS.json"
CANONICAL_SERVER = CRATE / "src/main.rs"
CANDIDATE_SERVER = ROOT / "crates/trnm-persistence-pg/src/bin/trnm-server.rs"
RETIRED_ALTERNATE = ROOT / "crates/trnm-persistence-core/src/bin/trnm-server.rs"
PRODUCT_CLAIMS = (
    "nakama_wire_compatible",
    "database_durable",
    "sg4_complete",
    "compatibility_credit",
    "production_ready",
    "public_online",
    "nakama_replaced",
)


class ContractError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def read(path: Path) -> str:
    require(path.is_file(), f"missing required file: {path.relative_to(ROOT)}")
    value = path.read_text(encoding="utf-8")
    require(value.endswith("\n"), f"file lacks trailing newline: {path.relative_to(ROOT)}")
    require("\r" not in value, f"CRLF is forbidden: {path.relative_to(ROOT)}")
    return value


def validate() -> dict[str, object]:
    require(CANONICAL_SERVER.is_file(), "canonical trnm-server composition root is missing")
    require(CANDIDATE_SERVER.is_file(), "diagnostic persistence compatibility server is missing")
    require(not RETIRED_ALTERNATE.exists(), "retired persistence-core alternate server returned")

    manifest = read(CRATE / "Cargo.toml")
    readme = read(CRATE / "README.md")
    source_paths = [CRATE / "src/lib.rs", CANONICAL_SERVER] + sorted(
        (CRATE / "src/runtime").glob("*.rs")
    )
    source = "\n".join(read(path) for path in source_paths)
    status = json.loads(read(STATUS))
    require(isinstance(status, dict), "status object required")

    require('name = "trnm-server"' in manifest, "candidate package name missing")
    require('name = "trnm-server"\npath = "src/main.rs"' in manifest, "candidate binary binding missing")
    require("[workspace]" not in manifest, "nested canonical server workspace returned")
    required_source_tokens = (
        "#![forbid(unsafe_code)]",
        "trnm_server::run_from_environment()",
        "ServerConfig::from_environment(arguments)",
        "Command::Migrate",
        "PgRepository",
        "sync_channel::<QueuedConnection>(queue_capacity)",
        "set_read_timeout(Some(config.read_timeout))",
        "set_write_timeout(Some(config.write_timeout))",
        "RetryingRepository",
        "cancel_inflight()",
        "tonic::transport::Server",
        "websocket::serve_once",
        "AccessTokenVerifier",
        "CommitOutcome::Duplicate",
        '"/healthz"',
        '"/readyz"',
        '"/metrics"',
        '"/-/drain"',
        '"/v1/authority/bootstrap"',
        '"/v1/authority/commit"',
        '"/v1/session/me"',
        '"/v1/session/refresh"',
        '"/v1/session/logout"',
    )
    for token in required_source_tokens:
        require(token in source, f"server candidate missing token: {token}")
    require("unsafe {" not in source.replace("#![forbid(unsafe_code)]", ""), "unsafe block entered candidate")

    require(status.get("schema") == "trillionnium.rust-server-vertical-slice-status.v1", "wrong status schema")
    require(status.get("status") == "source-candidate", "server status must remain source-candidate")
    require(status.get("implementation") == "crates/trnm-server", "status authority drift")
    claims = status.get("claims")
    require(isinstance(claims, dict), "status claims object required")
    require(claims.get("source_vertical_slice_exists") is True, "source presence fact missing")
    require(claims.get("composition_source_exists") is True, "composition source fact missing")
    for name in PRODUCT_CLAIMS:
        require(claims.get(name) is False, f"product claim must remain false: {name}")
    gaps = status.get("not_implemented")
    require(isinstance(gaps, list), "not_implemented list required")
    for required_gap in (
        "accepted exact-head authority-transfer evidence",
        "complete Nakama HTTP gRPC gateway and RTAPI parity",
        "accepted PostgreSQL and CockroachDB durability and ambiguity evidence",
        "conflict-free database protocol security SRE and whole-candidate review",
    ):
        require(required_gap in gaps, f"status omits limitation: {required_gap}")
    for marker in (
        "crates/trnm-persistence-pg/src/bin/trnm-server.rs",
        "accepted_evidence=false",
        "production_ready=false",
    ):
        require(marker in readme, f"README convergence boundary missing: {marker}")

    return {
        "schema": "trillionnium.rust-server-slice-contract.v3",
        "source": str((CRATE / "src/lib.rs").relative_to(ROOT)),
        "diagnostic_server": str(CANDIDATE_SERVER.relative_to(ROOT)),
        "canonical_server": str(CANONICAL_SERVER.relative_to(ROOT)),
        "source_tokens": len(required_source_tokens),
        "authority_transferred_source_candidate": True,
        "claims_all_false": True,
        "status": "passed",
        "compatibility_credit": False,
    }


def main() -> int:
    try:
        result = validate()
    except (OSError, json.JSONDecodeError, ContractError) as error:
        print(f"Rust server slice contract failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
