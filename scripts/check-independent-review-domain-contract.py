#!/usr/bin/env python3
"""Validate the immutable candidate-specific independent-review domain contract."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "docs/review/INDEPENDENT_REVIEW_MATRIX.json"

EXPECTED_DOMAINS: dict[str, dict[str, frozenset[str]]] = {
    "governance": {
        "required_roles": frozenset({
            "repository-administrator",
            "independent-program-governance-reviewer",
        }),
        "protected_paths": frozenset({
            ".github/**",
            "CURRENT_PLAN.md",
            "docs/DOCUMENTATION_AUTHORITY.json",
            "docs/GOVERNANCE.md",
            "docs/TESTING_AND_EVIDENCE.md",
            "docs/governance/**",
            "docs/review/**",
            "docs/status/PRODUCT_GATES.json",
            "docs/status/GAP_REGISTER.json",
            "docs/evidence/index.json",
        }),
        "blocking_gaps": frozenset({
            "GAP-P0-CI-001",
            "GAP-P0-GOV-001",
            "GAP-P0-PLAN-001",
        }),
    },
    "database-data-integrity": {
        "required_roles": frozenset({
            "independent-database-reviewer",
            "independent-data-integrity-reviewer",
        }),
        "protected_paths": frozenset({
            "migrations/**",
            "database/**",
            "crates/trnm-persistence-core/**",
            "crates/trnm-persistence-pg/**",
            "docs/development/SCHEMA_AUTHORITY.json",
            "docs/ARCHITECTURE.md",
            "docs/OPERATIONS_AND_RELEASE.md",
        }),
        "blocking_gaps": frozenset({
            "GAP-P0-DATA-001",
            "GAP-P1-OUTBOX-001",
            "GAP-P1-PG-001",
        }),
    },
    "security-cryptography": {
        "required_roles": frozenset({
            "independent-security-reviewer",
            "independent-cryptography-reviewer",
        }),
        "protected_paths": frozenset({
            "SECURITY.md",
            "docs/SECURITY_AND_PRIVACY.md",
            "crates/trnm-token-core/**",
            "crates/trnm-token-jwt-adapter/**",
            "crates/trnm-token-crypto-provider/**",
            "crates/trnm-token-jwt-provider-adapter/**",
            "crates/trnm-session-core/**",
        }),
        "blocking_gaps": frozenset({
            "GAP-P0-CRYPTO-001",
            "GAP-P1-CRYPTO-002",
        }),
    },
    "protocol-compatibility": {
        "required_roles": frozenset({
            "independent-protocol-reviewer",
            "independent-compatibility-qa-reviewer",
        }),
        "protected_paths": frozenset({
            "contracts/**",
            "crates/trnm-canonical-core/**",
            "crates/trnm-transport-core/**",
            "crates/trnm-realtime-wire/**",
            "crates/trnm-persistence-pg/src/bin/trnm-server.rs",
            "crates/trnm-persistence-pg/src/bin/trnm_server/**",
            "docs/ARCHITECTURE.md",
            "docs/COMPATIBILITY.md",
            "docs/TESTING_AND_EVIDENCE.md",
        }),
        "blocking_gaps": frozenset({
            "GAP-P0-SERVER-001",
            "GAP-P0-SCOPE-001",
        }),
    },
    "realtime-distributed-systems": {
        "required_roles": frozenset({
            "independent-realtime-reviewer",
            "independent-distributed-systems-reviewer",
        }),
        "protected_paths": frozenset({
            "crates/trnm-presence-core/**",
            "crates/trnm-presence-router-v2/**",
            "crates/trnm-realtime-wire/**",
            "runtime/internal/worldcommand/**",
            "runtime/internal/worldtransition/**",
            "docs/ARCHITECTURE.md",
            "docs/COMPATIBILITY.md",
        }),
        "blocking_gaps": frozenset({
            "GAP-P0-SERVER-001",
        }),
    },
    "operations-sre": {
        "required_roles": frozenset({
            "independent-sre-reviewer",
            "independent-recovery-reviewer",
        }),
        "protected_paths": frozenset({
            "deploy/**",
            "compose.yaml",
            ".github/workflows/database-backup-restore.yml",
            ".github/workflows/prospective-merge-gate.yml",
            "scripts/ci-pgwire-backup-restore.sh",
            "scripts/ci-trnm-server-live.sh",
            "docs/OPERATIONS_AND_RELEASE.md",
            "docs/TESTING_AND_EVIDENCE.md",
        }),
        "blocking_gaps": frozenset({
            "GAP-P1-PG-001",
        }),
    },
}


class DomainContractError(RuntimeError):
    """The review matrix changed a canonical review-domain obligation."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DomainContractError(message)


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        require(key not in value, f"duplicate JSON key: {key}")
        value[key] = item
    return value


def reject_constant(value: str) -> Any:
    raise DomainContractError(f"non-finite JSON value: {value}")


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except DomainContractError:
        raise
    except (OSError, json.JSONDecodeError) as error:
        raise DomainContractError(f"{path}: {error}") from error
    require(isinstance(value, dict), "review matrix root must be an object")
    return value


def string_set(value: Any, label: str) -> frozenset[str]:
    require(isinstance(value, list), f"{label}: list required")
    require(
        all(isinstance(item, str) and item and item.strip() == item for item in value),
        f"{label}: non-empty normalized strings required",
    )
    require(len(value) == len(set(value)), f"{label}: duplicates forbidden")
    return frozenset(value)


def validate(path: Path = MATRIX_PATH) -> dict[str, int | bool]:
    matrix = load(path)
    require(
        matrix.get("schema") == "trillionnium.independent-review-matrix.v2",
        "review matrix schema mismatch",
    )
    raw = matrix.get("domains")
    require(isinstance(raw, list) and raw, "review domains required")
    domains: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(raw):
        require(isinstance(row, dict), f"domains[{index}]: object required")
        domain_id = row.get("id")
        require(
            isinstance(domain_id, str) and domain_id and domain_id not in domains,
            f"domains[{index}]: invalid or duplicate id",
        )
        domains[domain_id] = row

    missing = sorted(set(EXPECTED_DOMAINS) - set(domains))
    extra = sorted(set(domains) - set(EXPECTED_DOMAINS))
    require(
        not missing and not extra,
        f"review domain set mismatch: missing={missing}, extra={extra}",
    )

    role_count = 0
    path_count = 0
    gap_count = 0
    for domain_id, expected in EXPECTED_DOMAINS.items():
        observed = domains[domain_id]
        for field in ("required_roles", "protected_paths", "blocking_gaps"):
            actual = string_set(observed.get(field), f"{domain_id}.{field}")
            require(
                actual == expected[field],
                f"{domain_id}: {field} drift; "
                f"missing={sorted(expected[field] - actual)}, "
                f"extra={sorted(actual - expected[field])}",
            )
        role_count += len(expected["required_roles"])
        path_count += len(expected["protected_paths"])
        gap_count += len(expected["blocking_gaps"])

    summary = matrix.get("summary")
    require(isinstance(summary, dict), "review matrix summary required")
    require(
        summary.get("domain_count") == len(EXPECTED_DOMAINS),
        "summary domain count drift",
    )
    require(
        summary.get("required_role_count") == role_count,
        "summary required-role count drift",
    )
    return {
        "domain_contract_valid": True,
        "domain_count": len(EXPECTED_DOMAINS),
        "required_role_count": role_count,
        "protected_path_assignments": path_count,
        "blocking_gap_assignments": gap_count,
    }


def main() -> int:
    try:
        result = validate()
    except DomainContractError as error:
        print(f"independent review domain contract invalid: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
