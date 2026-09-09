#!/usr/bin/env python3
from __future__ import annotations
import json
import tomllib
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
LONG_LIVED = ['trnm-contracts', 'trnm-authority-core', 'trnm-session-core', 'trnm-storage-core', 'trnm-persistence-core', 'trnm-persistence-pg', 'trnm-canonical-core', 'trnm-transport-core', 'trnm-token-core', 'trnm-presence-core', 'trnm-query-core', 'trnm-token-jwt-adapter', 'trnm-presence-router-v2', 'trnm-server', 'trnm-persistence-runtime-policy', 'trnm-realtime-wire', 'trnm-storage-nakama-version', 'trnm-token-crypto-provider', 'trnm-token-jwt-provider-adapter']
GATES = ['trnm-token-jwt-adapter-gate', 'trnm-token-jwt-adapter-gate-v2']
def require(value: bool, message: str) -> None:
    if not value:
        raise SystemExit(message)
manifest = tomllib.loads((ROOT / "Cargo.toml").read_text(encoding="utf-8"))
workspace = manifest["workspace"]
require(set(workspace["members"]) == {f"crates/{name}" for name in LONG_LIVED}, "root members are stale")
require(set(workspace["exclude"]) == {f"crates/{name}" for name in GATES}, "root exclusions are stale")
for name in LONG_LIVED:
    crate = ROOT / "crates" / name
    require("[workspace]" not in (crate / "Cargo.toml").read_text(encoding="utf-8"), f"{name} remains nested")
    require(not (crate / "Cargo.lock").exists(), f"{name} retains a nested lockfile")
registry = json.loads((ROOT / "docs/status/MODULE_DOCUMENTATION.json").read_text(encoding="utf-8"))
rows = {row["id"]: row for row in registry["modules"]}
require(all(rows[name]["workspace"] == "root" for name in LONG_LIVED), "registry root metadata is stale")
require(all(rows[name]["workspace"] == "isolated" for name in GATES), "registry gate metadata is stale")
print("workspace convergence validation passed")
