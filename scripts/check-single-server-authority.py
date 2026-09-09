#!/usr/bin/env python3
from __future__ import annotations
import json
import subprocess
import tomllib
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
def require(value: bool, message: str) -> None:
    if not value:
        raise SystemExit(message)
metadata = json.loads(subprocess.check_output(["cargo", "metadata", "--format-version", "1", "--no-deps"], cwd=ROOT, text=True))
owners = []
compat = []
for package in metadata["packages"]:
    for target in package["targets"]:
        if "bin" not in target["kind"]:
            continue
        if target["name"] == "trnm-server":
            owners.append(package["name"])
        if target["name"] == "trnm-pg-compat-server":
            compat.append((package["name"], target.get("required-features", [])))
require(owners == ["trnm-server"], f"default server owners must be exactly one: {owners}")
require(compat == [("trnm-persistence-pg", ["diagnostic-compat-server"])], f"compat target is invalid: {compat}")
persistence = tomllib.loads((ROOT / "crates/trnm-persistence-pg/Cargo.toml").read_text(encoding="utf-8"))
require(persistence["package"].get("autobins") is False, "persistence autobins must be false")
print("single server authority validation passed")
