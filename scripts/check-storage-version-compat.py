#!/usr/bin/env python3
"""Validate the Nakama public storage-version source-candidate boundary."""
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
        root = ROOT / "crates/trnm-storage-nakama-version"
        manifest_path = root / "Cargo.toml"
        lock_path = root / "Cargo.lock"
        source_path = root / "src/lib.rs"
        contract_path = ROOT / "contracts/storage/nakama-public-version-v1.json"
        documentation_path = ROOT / "docs/COMPATIBILITY.md"
        for path in (
            manifest_path,
            lock_path,
            source_path,
            contract_path,
            documentation_path,
        ):
            require(
                path.is_file(),
                f"missing storage-version source: {path.relative_to(ROOT)}",
            )

        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        package = manifest.get("package", {})
        require(
            package.get("name") == "trnm-storage-nakama-version",
            "wrong package name",
        )
        require(
            package.get("publish") is False,
            "compatibility adapter must not publish",
        )
        require(
            manifest.get("dependencies") in (None, {}),
            "adapter gained a dependency",
        )
        require(
            manifest.get("workspace") == {},
            "adapter must remain an explicit standalone workspace candidate",
        )

        lock = lock_path.read_text(encoding="utf-8")
        require(
            'name = "trnm-storage-nakama-version"' in lock,
            "lock omits adapter package",
        )
        require(
            "registry+" not in lock and "git+" not in lock,
            "lock gained an external source",
        )

        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        require(
            contract.get("schema")
            == "trillionnium.storage-nakama-public-version.v1",
            "wrong contract schema",
        )
        require(
            contract.get("status") == "source-candidate",
            "contract must remain source-candidate",
        )
        public = contract.get("public_version", {})
        require(public.get("algorithm") == "MD5", "public version algorithm drifted")
        require(
            public.get("wire_encoding")
            == "32-character lowercase hexadecimal",
            "wire encoding drifted",
        )
        require(
            public.get("security_use") is False,
            "MD5 must never receive security credit",
        )
        claims = contract.get("claims", {})
        require(claims.get("source_candidate") is True, "source candidate claim missing")
        for forbidden in (
            "storage_core_integrated",
            "storage_behavior_compatible",
            "database_durable",
            "production_ready",
        ):
            require(
                claims.get(forbidden) is False,
                f"premature storage claim: {forbidden}",
            )

        source = source_path.read_text(encoding="utf-8")
        for marker in (
            "PublicStorageVersion",
            "ContentIntegrityDigest",
            "WriteCondition",
            "Blind",
            "CreateOnly",
            "Exact",
            "ROUND_CONSTANTS",
            "ROTATIONS",
            "rfc1321_vectors_are_exact_lowercase_hex",
            "client_occ_conditions_distinguish_blind_create_and_exact",
        ):
            require(marker in source, f"storage adapter missing marker: {marker}")
        require(
            "unsafe"
            not in source.replace("#![forbid(unsafe_code)]", ""),
            "unsafe token entered storage adapter",
        )

        documentation = documentation_path.read_text(encoding="utf-8")
        for marker in (
            "## 10. Storage public version and OCC",
            "MD5 over the exact stored value bytes",
            "32-character lowercase hexadecimal",
            "MD5 receives no security or integrity-authentication credit",
            "blind write",
            "create-only",
            "exact expected version",
        ):
            require(
                marker in documentation,
                f"current compatibility documentation missing: {marker}",
            )

        print(
            json.dumps(
                {
                    "schema": "trillionnium.storage-version-source-check.v2",
                    "status": "passed",
                    "documentation": "docs/COMPATIBILITY.md",
                    "claims": {
                        "source_contract_passed": True,
                        "compiled": False,
                        "integrated": False,
                        "compatible": False,
                    },
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except (OSError, ValueError, tomllib.TOMLDecodeError, ValidationError) as error:
        print(f"storage-version source check failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
