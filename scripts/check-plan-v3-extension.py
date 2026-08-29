#!/usr/bin/env python3
"""Validate plan-v3 extension artifacts added after the audited v2 baseline."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "CURRENT_PLAN.md",
    "SECURITY.md",
    ".github/CODEOWNERS",
    ".github/workflows/trillionnium-game-merge-gate.yml",
    "crates/trnm-server/Cargo.toml",
    "crates/trnm-server/Cargo.lock",
    "crates/trnm-server/src/lib.rs",
    "crates/trnm-server/src/main.rs",
    "database/schema/v2/STATUS.json",
    "database/schema/v2/README.md",
    "docs/architecture/CURRENT_AND_TARGET_RUNTIME.md",
    "docs/architecture/RUST_SERVER_REFERENCE_ARCHITECTURE.md",
    "docs/development/COMPATIBILITY_DIVERGENCES.json",
    "docs/development/RUST_SERVER_VERTICAL_SLICE.md",
    "docs/development/SCHEMA_AUTHORITY.json",
    "docs/evidence/index.json",
    "docs/governance/BRANCH_AND_MERGE_POLICY.md",
    "docs/roadmap/NEXT_MILESTONE.json",
    "docs/security/CRYPTOGRAPHY_AND_KEYS.md",
    "docs/status/CURRENT_STATE.json",
    "docs/status/EXECUTION_STATUS.json",
    "docs/status/GAP_REGISTER.json",
    "docs/status/IMPLEMENTATION_INVENTORY.json",
    "docs/status/RUST_SERVER_STATUS.json",
    "docs/testing/TEST_POLICY.md",
    "migrations/MIGRATION_CHAIN.lock.json",
    "scripts/check-evidence-index.py",
    "scripts/check-gap-register.py",
    "scripts/check-migration-lock.py",
    "scripts/check-schema-authority.py",
    "scripts/check-status-transitions.py",
    "scripts/derive-gates.py",
]


class ValidationError(RuntimeError):
    """Raised when plan-v3 artifacts or claim boundaries drift."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def load_object(relative: str) -> dict[str, Any]:
    path = ROOT / relative
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"{relative}: top-level value must be an object")
    return value


def validate() -> dict[str, Any]:
    missing = [relative for relative in REQUIRED_FILES if not (ROOT / relative).is_file()]
    require(not missing, f"missing plan-v3 files: {missing}")

    plan = (ROOT / "CURRENT_PLAN.md").read_text(encoding="utf-8")
    for marker in (
        "开发计划 v3",
        "规划与状态分离",
        "门禁从证据派生",
        "Gap 可关闭",
        "Rust server 纵向切片",
        "trillionnium-game-merge-gate",
        "blocked-external-admin",
    ):
        require(marker in plan, f"CURRENT_PLAN.md missing v3 marker: {marker}")

    gap = load_object("docs/status/GAP_REGISTER.json")
    require(gap.get("plan_version") == 3, "gap register is not plan v3")
    require(gap.get("closure_policy", {}).get("implementation_only_closes_gap") is False, "implementation-only gap closure is forbidden")

    current = load_object("docs/status/CURRENT_STATE.json")
    require(current.get("plan_version") == 3, "current state is not plan v3")
    claims = current.get("claims", current.get("claim_boundary", {}))
    require(isinstance(claims, dict), "current-state claims must be an object")
    for key in ("production_ready", "public_online", "nakama_replaced"):
        if key in claims:
            require(claims[key] is False, f"current state must keep {key}=false")

    server = load_object("docs/status/RUST_SERVER_STATUS.json")
    require(server.get("status") == "source-candidate-unverified", "Rust server status must remain unverified")
    require(server.get("claims", {}).get("source_exists") is True, "Rust server source must be recorded")
    for key in ("remote_verified", "http_compatible", "grpc_compatible", "realtime_compatible", "database_durable", "production_ready"):
        require(server.get("claims", {}).get(key) is False, f"Rust server must keep {key}=false")

    schema = load_object("docs/development/SCHEMA_AUTHORITY.json")
    require(schema.get("migration_lock", {}).get("path") == "migrations/MIGRATION_CHAIN.lock.json", "schema authority must bind the migration lock")
    require(schema.get("non_authoritative", [{}])[0].get("path") == "database/schema/v2", "alternate schema quarantine is missing")
    require(schema.get("non_authoritative", [{}])[0].get("compatibility_credit") is False, "alternate schema must not receive credit")

    workflow = (ROOT / ".github/workflows/trillionnium-game-merge-gate.yml").read_text(encoding="utf-8")
    for marker in (
        "name: trillionnium-game-merge-gate",
        "scripts/check-gap-register.py",
        "crates/trnm-server/Cargo.toml",
        "test \"$CONTROL_PLANE\" = success",
    ):
        require(marker in workflow, f"aggregate workflow missing: {marker}")

    return {
        "schema": "trillionnium.plan-v3-extension-validation.v1",
        "required_file_count": len(REQUIRED_FILES),
        "rust_server_source_candidate": True,
        "migration_lock_bound": True,
        "claim_credit": False,
    }


def main() -> int:
    try:
        result = validate()
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        print(f"plan-v3 extension validation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
