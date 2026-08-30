#!/usr/bin/env python3
"""Inventory the legacy Go Nakama plugin as migration input, not target runtime credit."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
REGISTER = re.compile(
    r"\b(?:initializer|init)\.Register(?P<kind>[A-Za-z0-9_]+)\s*\(\s*(?P<name>`[^`]*`|\"(?:\\.|[^\"\\])*\")?"
)
MODULE_CALL = re.compile(r"\bnk\.([A-Z][A-Za-z0-9_]*)\s*\(")
ENV_CALL = re.compile(r"\b(?:os\.)?Getenv\s*\(\s*\"([^\"]+)\"")
HTTP_CALL = re.compile(r"\b(?:http\.(?:Get|Post|Do)|client\.Do)\s*\(")
SQL_CALL = re.compile(r"\b(?:db\.(?:Exec|Query|QueryRow)|tx\.(?:Exec|Query|QueryRow))(?:Context)?\s*\(")


class InventoryError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise InventoryError(message)


def unquote(value: str | None) -> str | None:
    if value is None:
        return None
    if value.startswith("`") and value.endswith("`"):
        return value[1:-1]
    if value.startswith('"') and value.endswith('"'):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value[1:-1]
    return value


def line_number(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def inventory_file(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    try:
        source = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise InventoryError(f"{path.relative_to(ROOT)} is not UTF-8: {error}") from error
    registrations = [
        {
            "kind": match.group("kind"),
            "name": unquote(match.group("name")),
            "line": line_number(source, match.start()),
        }
        for match in REGISTER.finditer(source)
    ]
    module_calls = sorted(set(MODULE_CALL.findall(source)))
    environment_keys = sorted(set(ENV_CALL.findall(source)))
    return {
        "path": str(path.relative_to(ROOT)),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "registrations": registrations,
        "nakama_module_calls": module_calls,
        "environment_keys": environment_keys,
        "http_call_sites": len(HTTP_CALL.findall(source)),
        "sql_call_sites": len(SQL_CALL.findall(source)),
    }


def inventory() -> dict[str, Any]:
    require(RUNTIME.is_dir(), "runtime directory is missing")
    files = sorted(
        path
        for path in RUNTIME.rglob("*.go")
        if "vendor" not in path.parts and not any(part.startswith(".") for part in path.relative_to(RUNTIME).parts)
    )
    require(files, "no Go runtime source files found")
    records = [inventory_file(path) for path in files]
    registrations = [
        {"path": record["path"], **registration}
        for record in records
        for registration in record["registrations"]
    ]
    module_calls = sorted(
        {
            call
            for record in records
            for call in record["nakama_module_calls"]
        }
    )
    environment_keys = sorted(
        {
            key
            for record in records
            for key in record["environment_keys"]
        }
    )
    source_manifest_sha256 = hashlib.sha256(
        json.dumps(
            [{"path": row["path"], "sha256": row["sha256"]} for row in records],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schema": "trillionnium.go-runtime-migration-inventory.v1",
        "project_id": "trillionnium-game",
        "runtime_root": "runtime",
        "source_file_count": len(records),
        "source_manifest_sha256": source_manifest_sha256,
        "registration_count": len(registrations),
        "registrations": registrations,
        "nakama_module_call_count": len(module_calls),
        "nakama_module_calls": module_calls,
        "environment_key_count": len(environment_keys),
        "environment_keys": environment_keys,
        "http_call_site_count": sum(row["http_call_sites"] for row in records),
        "sql_call_site_count": sum(row["sql_call_sites"] for row in records),
        "files": records,
        "migration": {
            "owner_role": "runtime-migration",
            "task_id": "TG-W11-007",
            "parity_ids": ["TG-PAR-053", "TG-PAR-054", "TG-PAR-055"],
            "status": "inventory-candidate",
            "required_next": [
                "classify every registration and module call against DEN-RUNTIME",
                "assign a Rust/WASM/Lua/JS target and owner",
                "bind fixtures and exact Nakama behavior evidence",
                "remove production Go plugin authority only after cutover",
            ],
        },
        "claims": {
            "inventory_generated": True,
            "inventory_independently_reviewed": False,
            "go_sources_migrated": False,
            "runtime_compatible": False,
            "c2_earned": False,
            "c5_earned": False,
            "production_ready": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-registrations", action="store_true")
    args = parser.parse_args()
    try:
        result = inventory()
        if args.require_registrations:
            require(result["registration_count"] > 0, "no initializer registrations found")
        rendered = json.dumps(result, sort_keys=True, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
    except InventoryError as error:
        print(f"Go runtime inventory failed: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "source_file_count": result["source_file_count"],
                "registration_count": result["registration_count"],
                "nakama_module_call_count": result["nakama_module_call_count"],
                "source_manifest_sha256": result["source_manifest_sha256"],
                "go_sources_migrated": False,
                "compatibility_credit": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
