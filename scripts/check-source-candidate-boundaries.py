#!/usr/bin/env python3
"""Fail closed when a source candidate overstates execution or compatibility."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


class BoundaryError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise BoundaryError(message)


def load(relative: str) -> dict[str, Any]:
    path = ROOT / relative
    require(path.is_file(), f"missing structured source contract: {relative}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BoundaryError(f"{relative}: invalid JSON: {error}") from error
    require(isinstance(value, dict), f"{relative}: top level must be an object")
    return value


def require_false(claims: dict[str, Any], keys: tuple[str, ...], relative: str) -> None:
    for key in keys:
        require(key in claims, f"{relative}: missing fail-closed claim {key}")
        require(claims[key] is False, f"{relative}: premature true claim {key}")


def validate_inventory() -> list[str]:
    relative = "docs/status/SOURCE_CANDIDATES.json"
    inventory = load(relative)
    require(inventory.get("schema") == "trillionnium.source-candidates.v1", "wrong source inventory schema")
    policy = inventory.get("policy")
    require(isinstance(policy, dict), "source inventory policy missing")
    require(policy.get("source_candidate_closes_gap") is False, "source candidate may not close a gap")
    require(policy.get("source_candidate_grants_compatibility_credit") is False, "source candidate may not grant compatibility")
    require(policy.get("exact_head_execution_required") is True, "exact-head execution must be required")
    require(policy.get("independent_review_required_for_p0_p1") is True, "P0/P1 independent review must be required")

    candidates = inventory.get("candidates")
    require(isinstance(candidates, list) and candidates, "source candidate inventory is empty")
    identifiers: list[str] = []
    allowed_statuses = {"source-candidate", "fixed-source"}
    for candidate in candidates:
        require(isinstance(candidate, dict), "candidate entry must be an object")
        identifier = candidate.get("id")
        require(isinstance(identifier, str) and identifier.startswith("SRC-"), "invalid source candidate ID")
        require(identifier not in identifiers, f"duplicate source candidate ID: {identifier}")
        identifiers.append(identifier)
        gaps = candidate.get("gap_ids")
        paths = candidate.get("paths")
        next_evidence = candidate.get("required_next")
        require(isinstance(gaps, list) and gaps, f"{identifier}: gap_ids missing")
        require(all(isinstance(value, str) and value.startswith("GAP-") for value in gaps), f"{identifier}: invalid gap ID")
        require(isinstance(paths, list) and paths, f"{identifier}: paths missing")
        require(isinstance(next_evidence, list) and next_evidence, f"{identifier}: required_next missing")
        require(candidate.get("status") in allowed_statuses, f"{identifier}: invalid source status")
        for raw_path in paths:
            path = ROOT / raw_path
            require(path.exists(), f"{identifier}: declared path does not exist: {raw_path}")

    claims = inventory.get("claims")
    require(isinstance(claims, dict), "source inventory claims missing")
    require(claims.get("source_progress_recorded") is True, "source progress must be recorded")
    require_false(
        claims,
        ("remote_verified", "independently_reviewed", "compatibility_credit", "production_ready"),
        relative,
    )
    return identifiers


def validate_server() -> None:
    relative = "contracts/server/vertical-slice-v1.json"
    contract = load(relative)
    require(contract.get("status") == "source-candidate", f"{relative}: status must be source-candidate")
    claims = contract.get("claims")
    require(isinstance(claims, dict), f"{relative}: claims missing")
    require(claims.get("rust_binary_exists") is True, f"{relative}: Rust binary source claim missing")
    require(claims.get("source_candidate") is True, f"{relative}: source candidate claim missing")
    require_false(
        claims,
        (
            "live_database_bound",
            "wire_compatible",
            "behavior_compatible",
            "sg4_complete",
            "production_ready",
            "public_online",
            "nakama_replaced",
        ),
        relative,
    )
    require(contract.get("not_implemented"), f"{relative}: limitations missing")


def validate_storage() -> None:
    relative = "contracts/storage/nakama-public-version-v1.json"
    contract = load(relative)
    require(contract.get("status") == "source-candidate", f"{relative}: status must be source-candidate")
    public_version = contract.get("public_version")
    require(isinstance(public_version, dict), f"{relative}: public version contract missing")
    require(public_version.get("security_use") is False, f"{relative}: MD5 may not receive security credit")
    claims = contract.get("claims")
    require(isinstance(claims, dict), f"{relative}: claims missing")
    require(claims.get("source_candidate") is True, f"{relative}: source candidate claim missing")
    require(claims.get("public_version_vectors_present") is True, f"{relative}: vector source claim missing")
    require_false(
        claims,
        ("storage_core_integrated", "storage_behavior_compatible", "database_durable", "production_ready"),
        relative,
    )
    require(contract.get("not_implemented"), f"{relative}: limitations missing")


def validate_crypto_provider() -> None:
    relative = "contracts/security/jwt-crypto-provider-v1.json"
    contract = load(relative)
    require(contract.get("status") == "interface-source-candidate", f"{relative}: invalid status")
    claims = contract.get("claims")
    require(isinstance(claims, dict), f"{relative}: claims missing")
    require(claims.get("provider_interface_exists") is True, f"{relative}: interface source claim missing")
    require_false(
        claims,
        ("production_provider_exists", "jwt_adapter_integrated", "security_review_accepted", "production_ready"),
        relative,
    )
    require(contract.get("not_implemented"), f"{relative}: limitations missing")


def validate_gap_states() -> None:
    relative = "docs/status/GAP_REGISTER.json"
    register = load(relative)
    gaps = register.get("gaps")
    require(isinstance(gaps, list), f"{relative}: gaps missing")
    indexed = {row.get("id"): row for row in gaps if isinstance(row, dict)}
    for gap_id in (
        "GAP-P0-SERVER-001",
        "GAP-P0-CRYPTO-001",
        "GAP-P1-CRYPTO-002",
        "GAP-P1-OUTBOX-001",
        "GAP-P1-STORAGE-001",
        "GAP-P0-EVIDENCE-001",
    ):
        require(gap_id in indexed, f"{relative}: expected source-candidate gap missing: {gap_id}")
        status = indexed[gap_id].get("status")
        require(status != "closed", f"{relative}: source-only work prematurely closed {gap_id}")


def main() -> int:
    try:
        candidate_ids = validate_inventory()
        validate_server()
        validate_storage()
        validate_crypto_provider()
        validate_gap_states()
        print(
            json.dumps(
                {
                    "schema": "trillionnium.source-candidate-boundary-check.v1",
                    "status": "passed",
                    "candidate_count": len(candidate_ids),
                    "claims": {
                        "structured_boundaries_valid": True,
                        "remote_verified": False,
                        "compatibility_credit": False,
                        "production_ready": False,
                    },
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except (OSError, ValueError, BoundaryError) as error:
        print(f"source-candidate boundary check failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
