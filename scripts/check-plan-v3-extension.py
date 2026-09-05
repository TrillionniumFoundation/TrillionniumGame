#!/usr/bin/env python3
"""Validate the current plan-v3.1 extension surface."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CURRENT_DOCS = [
    "docs/README.md",
    "docs/ARCHITECTURE.md",
    "docs/DEVELOPMENT.md",
    "docs/COMPATIBILITY.md",
    "docs/TESTING_AND_EVIDENCE.md",
    "docs/SECURITY_AND_PRIVACY.md",
    "docs/OPERATIONS_AND_RELEASE.md",
    "docs/GOVERNANCE.md",
    "docs/ROADMAP.md",
]
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
    "docs/DOCUMENTATION_AUTHORITY.json",
    *CURRENT_DOCS,
    "docs/development/COMPATIBILITY_DIVERGENCES.json",
    "docs/development/SCHEMA_AUTHORITY.json",
    "docs/evidence/index.json",
    "docs/roadmap/NEXT_MILESTONE.json",
    "docs/status/CURRENT_STATE.json",
    "docs/status/EXECUTION_STATUS.json",
    "docs/status/GAP_REGISTER.json",
    "docs/status/IMPLEMENTATION_INVENTORY.json",
    "docs/status/RUST_SERVER_STATUS.json",
    "migrations/MIGRATION_CHAIN.lock.json",
    "scripts/check-documentation-authority.py",
    "scripts/check-evidence-index.py",
    "scripts/check-gap-register.py",
    "scripts/check-migration-lock.py",
    "scripts/check-schema-authority.py",
    "scripts/check-status-transitions.py",
    "scripts/derive-gates.py",
]


class ValidationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def load_object(relative: str) -> dict[str, Any]:
    value = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"{relative}: object required")
    return value


def validate() -> dict[str, Any]:
    missing = [path for path in REQUIRED_FILES if not (ROOT / path).is_file()]
    require(not missing, f"missing current plan files: {missing}")

    plan = (ROOT / "CURRENT_PLAN.md").read_text(encoding="utf-8")
    for marker in (
        "开发计划 v3.1",
        "文档与控制面模型",
        "唯一黄金纵向切片",
        "trillionnium-game-merge-gate",
        "Gap closure definition",
        "docs/DOCUMENTATION_AUTHORITY.json",
    ):
        require(marker in plan, f"CURRENT_PLAN.md missing marker: {marker}")

    authority = load_object("docs/DOCUMENTATION_AUTHORITY.json")
    require(
        authority.get("schema") == "trillionnium.documentation-authority.v1",
        "documentation authority schema",
    )
    require(authority.get("revision") == "2026-09-01", "documentation revision")
    require(
        authority.get("current_human_documents") == CURRENT_DOCS,
        "current document list drifted",
    )
    require(
        authority.get("claims", {}).get("documentation_consolidated") is True,
        "documentation consolidation claim",
    )
    for key in (
        "compatibility_credit",
        "production_ready",
        "public_online",
        "nakama_retired",
    ):
        require(
            authority.get("claims", {}).get(key) is False,
            f"documentation must keep {key}=false",
        )

    gap = load_object("docs/status/GAP_REGISTER.json")
    require(gap.get("plan_version") == 3, "gap register plan version")
    require(
        gap.get("closure_policy", {}).get("implementation_only_closes_gap")
        is False,
        "implementation-only closure is forbidden",
    )

    current = load_object("docs/status/CURRENT_STATE.json")
    require(current.get("plan_version") == 3, "current state plan version")
    for key in (
        "production_ready",
        "public_online",
        "nakama_retired",
    ):
        require(current.get("claims", {}).get(key) is False, f"current claim {key}")

    server = load_object("docs/status/RUST_SERVER_STATUS.json")
    require(
        server.get("status") == "source-candidate-unverified",
        "foundation server status",
    )
    require(server.get("claims", {}).get("source_exists") is True, "server source")
    for key in (
        "remote_verified",
        "http_compatible",
        "grpc_compatible",
        "realtime_compatible",
        "database_durable",
        "production_ready",
    ):
        require(server.get("claims", {}).get(key) is False, f"server claim {key}")

    schema = load_object("docs/development/SCHEMA_AUTHORITY.json")
    require(
        schema.get("migration_lock", {}).get("path")
        == "migrations/MIGRATION_CHAIN.lock.json",
        "migration lock binding",
    )
    require(
        schema.get("non_authoritative", [{}])[0].get("path")
        == "database/schema/v2",
        "alternate schema quarantine",
    )

    workflow = (
        ROOT / ".github/workflows/trillionnium-game-merge-gate.yml"
    ).read_text(encoding="utf-8")
    for marker in (
        "name: trillionnium-game-merge-gate",
        "scripts/check-gap-register.py",
        "crates/trnm-server/Cargo.toml",
        'test "$CONTROL_PLANE" = success',
    ):
        require(marker in workflow, f"aggregate workflow missing: {marker}")

    return {
        "schema": "trillionnium.plan-v3-extension-validation.v2",
        "required_file_count": len(REQUIRED_FILES),
        "current_human_document_count": len(CURRENT_DOCS),
        "historical_human_markdown_count": 0,
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
