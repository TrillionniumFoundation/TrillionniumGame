#!/usr/bin/env python3
"""Validate the composed Rust server source slice while preserving no-credit boundaries."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "contracts/server/rust-server-vertical-slice.v1.json"
STATUS_PATH = ROOT / "docs/status/RUST_SERVER_VERTICAL_SLICE_STATUS.json"
CRATE = ROOT / "crates/trnm-server"
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


def load(path: Path) -> dict[str, object]:
    value = json.loads(read(path))
    require(isinstance(value, dict), f"object required: {path.relative_to(ROOT)}")
    return value


def main() -> int:
    try:
        contract = load(CONTRACT_PATH)
        status = load(STATUS_PATH)
        manifest = read(CRATE / "Cargo.toml")
        lock = read(CRATE / "Cargo.lock")
        readme = read(CRATE / "README.md")
        source_paths = [CRATE / "src/lib.rs", CRATE / "src/main.rs"] + sorted(
            (CRATE / "src/runtime").glob("*.rs")
        )
        source = "\n".join(read(path) for path in source_paths)

        require(contract.get("schema") == "trillionnium.rust-server-vertical-slice.v1", "wrong server contract schema")
        require(contract.get("contract_version") == 2, "server contract version drift")
        require(contract.get("status") == "source-candidate", "contract status must fail closed")
        require(contract.get("binary") == "trnm-server-composition-candidate", "contract binary drift")
        require(status.get("status") == "source-candidate", "status must fail closed")
        require(status.get("gap_id") == "GAP-P0-SERVER-001", "wrong gap binding")
        require(status.get("contract") == str(CONTRACT_PATH.relative_to(ROOT)), "contract path drift")

        for dependency in (
            "trnm-contracts",
            "trnm-persistence-pg",
            "trnm-realtime-wire",
            "trnm-session-core",
            "trnm-token-jwt-adapter",
        ):
            require(dependency in manifest, f"manifest omits {dependency}")
            require(f'name = "{dependency}"' in lock, f"lock omits {dependency}")
        require('name = "trnm-server"' in manifest, "server package missing")
        require('name = "trnm-server"' in lock, "server lock entry missing")
        require("[workspace]" in manifest, "isolated workspace boundary missing")

        required_source = (
            "#![forbid(unsafe_code)]",
            "sync_channel(queue_capacity)",
            "set_read_timeout(Some(config.read_timeout))",
            "set_write_timeout(Some(config.write_timeout))",
            "PgRepository",
            "CommitOutcome::Duplicate",
            "acknowledgement-after-commit fence",
            "RetryingRepository",
            "cancel_inflight()",
            "tonic::transport::Server",
            "websocket::serve_once",
            "AccessTokenVerifier",
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
        for marker in required_source:
            require(marker in source, f"server source missing marker: {marker}")
        for marker in ("unsafe {", "compatibility_credit=true", "production_ready=true"):
            require(marker not in source.replace("#![forbid(unsafe_code)]", ""), f"forbidden source marker: {marker}")

        claims = contract.get("claims")
        status_claims = status.get("claims")
        require(isinstance(claims, dict), "contract claims must be an object")
        require(isinstance(status_claims, dict), "status claims must be an object")
        for name in PRODUCT_CLAIMS:
            require(claims.get(name) is False, f"contract claim must remain false: {name}")
            require(status_claims.get(name) is False, f"status claim must remain false: {name}")
        verification = status.get("verification")
        require(isinstance(verification, dict), "verification must be an object")
        require(verification.get("target_native_actions_run") is False, "target execution cannot be preclaimed")
        require(verification.get("independent_review") is False, "review cannot be preclaimed")

        for marker in (
            "compatibility_credit=false",
            "database_durability_credit=false",
            "sg4_credit=false",
            "production_ready=false",
            "accepted_evidence=false",
        ):
            require(marker in readme, f"README missing no-credit marker: {marker}")

        routes = contract.get("implemented_routes")
        require(isinstance(routes, list) and len(routes) == 9, "exact nine HTTP source routes required")
        print(json.dumps({
            "schema": "trillionnium.rust-server-vertical-slice-check.v2",
            "status": "passed-source-contract",
            "routes": len(routes),
            "composition_source": True,
            "compatibility_credit": False,
            "database_durable": False,
            "sg4_complete": False,
        }, sort_keys=True, separators=(",", ":")))
        return 0
    except (OSError, json.JSONDecodeError, ContractError) as error:
        print(f"Rust server vertical-slice contract failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
