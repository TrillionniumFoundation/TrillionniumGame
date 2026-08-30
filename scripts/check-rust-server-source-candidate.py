#!/usr/bin/env python3
"""Validate the bounded trnm-server source-candidate contract."""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ValidationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def main() -> int:
    try:
        manifest_path = ROOT / "crates/trnm-server/Cargo.toml"
        lock_path = ROOT / "crates/trnm-server/Cargo.lock"
        lib_path = ROOT / "crates/trnm-server/src/lib.rs"
        main_path = ROOT / "crates/trnm-server/src/main.rs"
        contract_path = ROOT / "contracts/server/vertical-slice-v1.json"
        documentation_path = ROOT / "docs/development/RUST_SERVER_VERTICAL_SLICE_V1.md"
        for path in (
            manifest_path,
            lock_path,
            lib_path,
            main_path,
            contract_path,
            documentation_path,
        ):
            require(path.is_file(), f"missing required server source: {path.relative_to(ROOT)}")

        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        package = manifest.get("package", {})
        require(package.get("name") == "trnm-server", "wrong server package name")
        require(package.get("publish") is False, "server source candidate must not publish")
        require(manifest.get("workspace") == {}, "server must remain an explicit standalone workspace candidate")
        dependencies = manifest.get("dependencies", {})
        require(
            set(dependencies) == {"trnm-contracts", "trnm-persistence-core"},
            "server source candidate dependency boundary changed",
        )
        for name, value in dependencies.items():
            require(isinstance(value, dict) and "path" in value, f"{name} must be a path dependency")
            require("version" not in value and "git" not in value, f"{name} introduced an external source")

        lock = lock_path.read_text(encoding="utf-8")
        for package_name in ("trnm-server", "trnm-contracts", "trnm-persistence-core"):
            require(f'name = "{package_name}"' in lock, f"lock omits {package_name}")
        require("registry+" not in lock and "git+" not in lock, "source candidate lock gained an external dependency")

        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        require(contract.get("schema") == "trillionnium.server-vertical-slice.v1", "wrong server contract schema")
        require(contract.get("status") == "source-candidate", "server status must remain source-candidate")
        claims = contract.get("claims", {})
        require(claims.get("rust_binary_exists") is True, "Rust binary claim missing")
        require(claims.get("source_candidate") is True, "source-candidate claim missing")
        for forbidden in (
            "live_database_bound",
            "wire_compatible",
            "behavior_compatible",
            "sg4_complete",
            "production_ready",
            "public_online",
            "nakama_replaced",
        ):
            require(claims.get(forbidden) is False, f"premature server claim: {forbidden}")

        source = lib_path.read_text(encoding="utf-8")
        for marker in (
            "MAX_REQUEST_BYTES",
            "MAX_BODY_BYTES",
            "TransferEncodingForbidden",
            "entity_revision_mismatch",
            "PrepareOutcome::Duplicate",
            "IntentKind::Broadcast",
            "/healthz",
            "/readyz",
            "/v2/rpc/trnm_vertical_slice",
        ):
            require(marker in source, f"server source missing marker: {marker}")
        require("unsafe" not in source.replace("#![forbid(unsafe_code)]", ""), "unsafe token entered server source")

        binary = main_path.read_text(encoding="utf-8")
        for marker in ("serve", "migrate", "--max-requests", "claim=no-compatibility-credit"):
            require(marker in binary, f"server binary missing marker: {marker}")

        print(
            json.dumps(
                {
                    "schema": "trillionnium.server-source-check.v1",
                    "status": "passed",
                    "package": package.get("name"),
                    "claims": {
                        "source_contract_passed": True,
                        "compiled": False,
                        "live_database_bound": False,
                        "wire_compatible": False,
                        "production_ready": False,
                    },
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except (OSError, ValueError, tomllib.TOMLDecodeError, ValidationError) as error:
        print(f"trnm-server source check failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
