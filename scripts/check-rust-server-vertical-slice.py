#!/usr/bin/env python3
"""Validate the first Rust server vertical slice without granting product credit."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "contracts/server/rust-server-vertical-slice.v1.json"
STATUS_PATH = ROOT / "docs/status/RUST_SERVER_VERTICAL_SLICE_STATUS.json"
MANIFEST_PATH = ROOT / "crates/trnm-server/Cargo.toml"
LOCK_PATH = ROOT / "crates/trnm-server/Cargo.lock"
LIB_PATH = ROOT / "crates/trnm-server/src/lib.rs"
MAIN_PATH = ROOT / "crates/trnm-server/src/main.rs"
README_PATH = ROOT / "crates/trnm-server/README.md"


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


def load(path: Path) -> dict[str, object]:
    value = json.loads(read(path))
    require(isinstance(value, dict), f"top-level object required: {path.relative_to(ROOT)}")
    return value


def main() -> int:
    try:
        contract = load(CONTRACT_PATH)
        status = load(STATUS_PATH)
        manifest = read(MANIFEST_PATH)
        lock = read(LOCK_PATH)
        source = read(LIB_PATH)
        binary = read(MAIN_PATH)
        readme = read(README_PATH)

        require(
            contract.get("schema") == "trillionnium.rust-server-vertical-slice.v1",
            "wrong server contract schema",
        )
        require(contract.get("status") == "source-candidate", "contract status must fail closed")
        require(status.get("status") == "source-candidate", "status must be source-candidate")
        require(status.get("gap_id") == "GAP-P0-SERVER-001", "wrong gap binding")
        require(status.get("contract") == str(CONTRACT_PATH.relative_to(ROOT)), "contract path drift")

        require('name = "trnm-server"' in manifest, "server package name missing")
        require("[workspace]" in manifest, "server must be explicitly verified as a standalone workspace")
        require("trnm-contracts" in manifest, "server does not consume canonical contracts")
        require("trnm-persistence-core" in manifest, "server does not consume persistence invariants")
        require('name = "trnm-server"' in lock, "standalone lockfile omits server package")

        required_source = [
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
        ]
        for marker in required_source:
            require(marker in source, f"server source missing marker: {marker}")

        forbidden_source = [
            "unsafe {",
            "tokio::spawn",
            "thread::spawn(move || loop { thread::spawn",
            "postgres::Client",
            "compatibility_credit=true",
            "production_ready=true",
        ]
        for marker in forbidden_source:
            require(marker not in source, f"server source contains forbidden marker: {marker}")

        for marker in [
            "ServerConfig::from_env",
            "TcpListener::bind",
            "Server::new",
            "server.serve",
            "profile=source-vertical-slice",
            "compatibility_credit=false",
        ]:
            require(marker in binary, f"server binary missing marker: {marker}")

        for marker in [
            "compatibility_credit=false",
            "database_durability_credit=false",
            "sg4_credit=false",
            "production_ready=false",
        ]:
            require(marker in readme, f"README missing no-credit marker: {marker}")

        contract_claims = contract.get("claims")
        status_claims = status.get("claims")
        require(isinstance(contract_claims, dict), "contract claims must be an object")
        require(isinstance(status_claims, dict), "status claims must be an object")
        for name in [
            "nakama_wire_compatible",
            "database_durable",
            "sg4_complete",
            "compatibility_credit",
            "production_ready",
            "public_online",
            "nakama_replaced",
        ]:
            require(contract_claims.get(name) is False, f"contract claim must be false: {name}")
            require(status_claims.get(name) is False, f"status claim must be false: {name}")

        verification = status.get("verification")
        require(isinstance(verification, dict), "verification must be an object")
        require(verification.get("target_native_actions_run") is False, "remote run cannot be claimed")
        require(verification.get("independent_review") is False, "independent review cannot be claimed")

        print(
            json.dumps(
                {
                    "schema": "trillionnium.rust-server-vertical-slice-check.v1",
                    "status": "passed-source-contract",
                    "routes": 4,
                    "compatibility_credit": False,
                    "database_durable": False,
                    "sg4_complete": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except (OSError, json.JSONDecodeError, ContractError) as error:
        print(f"Rust server vertical-slice contract failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
