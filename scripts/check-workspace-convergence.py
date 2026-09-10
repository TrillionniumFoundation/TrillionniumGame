#!/usr/bin/env python3
"""Validate the converged Rust workspace and one canonical server authority."""
from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LONG_LIVED = [
    "trnm-contracts",
    "trnm-authority-core",
    "trnm-identity-core",
    "trnm-session-core",
    "trnm-storage-core",
    "trnm-persistence-core",
    "trnm-persistence-pg",
    "trnm-canonical-core",
    "trnm-transport-core",
    "trnm-token-core",
    "trnm-presence-core",
    "trnm-query-core",
    "trnm-token-jwt-adapter",
    "trnm-presence-router-v2",
    "trnm-server",
    "trnm-persistence-runtime-policy",
    "trnm-realtime-wire",
    "trnm-storage-nakama-version",
    "trnm-token-crypto-provider",
    "trnm-token-jwt-provider-adapter",
]
GATES = ["trnm-token-jwt-adapter-gate", "trnm-token-jwt-adapter-gate-v2"]
CANONICAL_STAGE = "canonical-http-grpc-websocket-session-database-source-candidate"


class ConvergenceError(RuntimeError):
    """Raised when source, machine state, and human authority diverge."""


def require(value: bool, message: str) -> None:
    if not value:
        raise ConvergenceError(message)


def load_json(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConvergenceError(f"cannot load {relative}: {error}") from error
    require(isinstance(value, dict), f"{relative} must contain an object")
    return value


def load_toml(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConvergenceError(f"cannot load {relative}: {error}") from error
    require(isinstance(value, dict), f"{relative} must contain a table")
    return value


def exact_bin(rows: Any, name: str) -> dict[str, Any]:
    require(isinstance(rows, list), "Cargo bin targets must be an array")
    matches = [row for row in rows if isinstance(row, dict) and row.get("name") == name]
    require(len(matches) == 1, f"expected one {name} binary target")
    return matches[0]


def validate(root: Path = ROOT) -> dict[str, Any]:
    root = root.resolve()
    manifest = load_toml(root, "Cargo.toml")
    workspace = manifest.get("workspace")
    require(isinstance(workspace, dict), "root workspace is missing")
    require(
        set(workspace.get("members", [])) == {f"crates/{name}" for name in LONG_LIVED},
        "root members are stale",
    )
    require(
        set(workspace.get("exclude", [])) == {f"crates/{name}" for name in GATES},
        "root exclusions are stale",
    )

    for name in LONG_LIVED:
        crate = root / "crates" / name
        text = (crate / "Cargo.toml").read_text(encoding="utf-8")
        require("[workspace]" not in text, f"{name} remains a nested workspace")
        require(not (crate / "Cargo.lock").exists(), f"{name} retains a nested lockfile")

    registry = load_json(root, "docs/status/MODULE_DOCUMENTATION.json")
    rows = {
        row.get("id"): row
        for row in registry.get("modules", [])
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    require(set(LONG_LIVED + GATES) <= set(rows), "module registry is incomplete")
    require(
        all(rows[name].get("workspace") == "root" for name in LONG_LIVED),
        "registry root metadata is stale",
    )
    require(
        all(rows[name].get("workspace") == "isolated" for name in GATES),
        "registry gate metadata is stale",
    )

    authority = load_json(root, "docs/development/RUST_PACKAGE_AUTHORITY.json")
    server_authority = authority.get("server_binary_authority", {})
    require(
        server_authority.get("manifest") == "crates/trnm-server/Cargo.toml",
        "package authority server manifest is stale",
    )
    require(
        server_authority.get("source") == "crates/trnm-server/src/main.rs",
        "package authority server source is stale",
    )
    compat_authority = authority.get("foundation_prototype", {})
    require(
        compat_authority.get("name") == "trnm-pg-compat-server",
        "diagnostic server name is stale",
    )
    require(
        compat_authority.get("status")
        == "feature-gated-diagnostic-compatibility-source-candidate",
        "diagnostic server status is stale",
    )

    server_manifest = load_toml(root, "crates/trnm-server/Cargo.toml")
    canonical_bin = exact_bin(server_manifest.get("bin"), "trnm-server")
    require(canonical_bin.get("path") == "src/main.rs", "canonical binary path is stale")

    persistence_manifest = load_toml(root, "crates/trnm-persistence-pg/Cargo.toml")
    require(
        persistence_manifest.get("package", {}).get("autobins") is False,
        "persistence package must disable automatic binaries",
    )
    require(
        "diagnostic-compat-server" in persistence_manifest.get("features", {}),
        "diagnostic server feature is missing",
    )
    diagnostic_bin = exact_bin(
        persistence_manifest.get("bin"), "trnm-pg-compat-server"
    )
    require(
        diagnostic_bin.get("path") == "src/bin/trnm-server.rs",
        "diagnostic server source path is stale",
    )
    require(
        diagnostic_bin.get("required-features") == ["diagnostic-compat-server"],
        "diagnostic server is not feature gated",
    )

    server_row = rows["trnm-server"]
    require(
        server_row.get("lifecycle") == "canonical-server-composition-root",
        "module registry server lifecycle is stale",
    )
    require(
        server_row.get("authority")
        == "only default first-party trnm-server composition authority source candidate",
        "module registry server authority is stale",
    )

    inventory = load_json(root, "docs/status/IMPLEMENTATION_INVENTORY.json")
    inventory_rows = {
        row.get("id"): row
        for row in inventory.get("components", [])
        if isinstance(row, dict)
    }
    server_inventory = inventory_rows.get("COMP-TRNM-SERVER", {})
    require(
        server_inventory.get("path") == "crates/trnm-server",
        "implementation inventory server path is stale",
    )
    require(
        server_inventory.get("status")
        == "http-grpc-websocket-session-database-source-candidate",
        "implementation inventory server stage is stale",
    )

    current = load_json(root, "docs/status/CURRENT_STATE.json")
    runtime = current.get("runtime_topology", {})
    require(
        runtime.get("rust_server_binary_authority")
        == "crates/trnm-server/Cargo.toml::trnm-server",
        "current state server authority is stale",
    )
    require(
        runtime.get("rust_server_stage") == CANONICAL_STAGE,
        "current state server stage is stale",
    )

    server_status = load_json(root, "docs/status/RUST_SERVER_STATUS.json")
    require(
        server_status.get("claims", {}).get("canonical_binary_authority") is True,
        "server status canonical source authority is missing",
    )
    require(
        server_status.get("canonical_server_binary", {}).get("manifest")
        == "crates/trnm-server/Cargo.toml",
        "server status canonical manifest is stale",
    )

    plan = (root / "CURRENT_PLAN.md").read_text(encoding="utf-8")
    readme = (root / "crates/trnm-server/README.md").read_text(encoding="utf-8")
    require(
        "`crates/trnm-server` 现在是唯一默认 `trnm-server` composition authority"
        in plan,
        "CURRENT_PLAN server authority is stale",
    )
    require(
        "only default `trnm-server` composition authority source candidate" in readme,
        "trnm-server README authority is stale",
    )
    require(
        "feature-gated `trnm-pg-compat-server`" in readme,
        "trnm-server README diagnostic boundary is stale",
    )
    combined = plan + "\n" + readme + "\n" + json.dumps(registry)
    for stale in (
        "当前 canonical database-backed server 仍位于 `crates/trnm-persistence-pg`",
        "currently admitted canonical process remains `crates/trnm-persistence-pg",
        "foundation process prototype; not the canonical production binary",
    ):
        require(stale not in combined, f"stale authority statement remains: {stale}")

    return {
        "schema": "trillionnium.workspace-convergence-validation.v2",
        "root_workspace_packages": len(LONG_LIVED),
        "isolated_gate_packages": len(GATES),
        "canonical_server": "crates/trnm-server::trnm-server",
        "diagnostic_server": "crates/trnm-persistence-pg::trnm-pg-compat-server",
        "status": "passed",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    arguments = parser.parse_args()
    try:
        result = validate(arguments.root)
    except (ConvergenceError, OSError, KeyError, TypeError) as error:
        print(f"workspace convergence validation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
