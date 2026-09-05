#!/usr/bin/env python3
"""Validate the plan-v3 gap register and its fail-closed closure contract."""
from __future__ import annotations

import json
import importlib.util
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

_SPEC = importlib.util.spec_from_file_location(
    "trnm_evidence_admission_" + Path(__file__).stem.replace("-", "_"),
    Path(__file__).with_name("evidence_admission.py"),
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("cannot load shared evidence admission contract")
EVIDENCE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = EVIDENCE
_SPEC.loader.exec_module(EVIDENCE)
REGISTER = ROOT / "docs/status/GAP_REGISTER.json"
EVIDENCE_INDEX = ROOT / "docs/evidence/index.json"

ALLOWED_SEVERITIES = {"P0", "P1", "P2", "informational"}
ALLOWED_EVIDENCE_TYPES = {
    "manifest",
    "unit",
    "property",
    "fuzz",
    "wire-differential",
    "database-differential",
    "runtime-differential",
    "sdk-blackbox",
    "migration-rehearsal",
    "fault-injection",
    "performance",
    "endurance",
    "security-review",
    "penetration-test",
    "backup-restore",
    "canary",
    "cutover",
    "retirement",
}
TERMINAL_STATUSES = {"closed", "rejected", "superseded"}
VERIFIED_STATUSES = {"remote-verified", "independently-reviewed", "closed"}
SOURCE_ONLY_STATUSES = {"source-candidate", "locally-verified"}


class ValidationError(RuntimeError):
    """Raised when the gap control plane is internally inconsistent."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def load_object(path: Path) -> dict[str, Any]:
    try:
        return EVIDENCE.load_object(path)
    except (OSError, ValueError, RecursionError) as error:
        raise ValidationError(f"invalid control JSON: {error}") from error


def indexed_evidence_rows(index: dict[str, Any]) -> dict[str, dict[str, Any]]:
    try:
        return {row["evidence_id"]: row for row in EVIDENCE.index_rows(index)}
    except EVIDENCE.AdmissionError as error:
        raise ValidationError(str(error)) from error


def indexed_evidence_ids(index: dict[str, Any]) -> set[str]:
    """Return indexed IDs while preserving the original checker module API."""

    return set(indexed_evidence_rows(index))


def accepted_review(row: dict[str, Any]) -> dict[str, Any] | None:
    try:
        _, commit, tree = EVIDENCE.target_identity(row)
    except (ValueError, TypeError):
        return None
    return EVIDENCE.accepted_review(row, target_commit=commit, target_tree=tree)


def validate_required_evidence_types(gap_id: str, values: Any) -> list[str]:
    require(
        isinstance(values, list) and values,
        f"{gap_id}: evidence types required",
    )
    result: list[str] = []
    for index, value in enumerate(values):
        require(
            isinstance(value, str),
            f"{gap_id}: required_evidence_types[{index}] must be a string",
        )
        require(
            bool(value) and value.strip() == value,
            f"{gap_id}: required_evidence_types[{index}] must be non-empty canonical text",
        )
        require(
            value in ALLOWED_EVIDENCE_TYPES,
            f"{gap_id}: unsupported evidence type {value!r}",
        )
        result.append(value)
    require(
        len(result) == len(set(result)),
        f"{gap_id}: duplicate required evidence types",
    )
    return result


def validate_closed_evidence(
    gap_id: str,
    severity: str,
    required_types: list[Any],
    evidence_ids: list[str],
    evidence: dict[str, dict[str, Any]],
) -> None:
    try:
        EVIDENCE.validate_gap_evidence({
            "id": gap_id, "evidence_ids": evidence_ids,
            "required_evidence_types": required_types, "external_dependency": None,
        }, evidence, root=ROOT)
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
        raise ValidationError(f"{gap_id}: {error}") from error
    require(evidence_ids, f"{gap_id}: closed gap requires indexed evidence")
    present_types: set[str] = set()
    for evidence_id in evidence_ids:
        row = evidence[evidence_id]
        evidence_type = row.get("evidence_type")
        require(
            isinstance(evidence_type, str) and evidence_type,
            f"{gap_id}: {evidence_id} has no evidence_type",
        )
        present_types.add(evidence_type)
        require(
            row.get("status") == "accepted",
            f"{gap_id}: {evidence_id} is not accepted",
        )
        require(
            row.get("schema_valid") is True,
            f"{gap_id}: {evidence_id} is not schema-valid",
        )
        require(
            row.get("target_identity_verified_by_current_repo") is True,
            f"{gap_id}: {evidence_id} lacks exact-target verification",
        )
        if severity in {"P0", "P1"}:
            require(
                accepted_review(row) is not None,
                f"{gap_id}: {evidence_id} lacks independent accepted review",
            )
    missing_types = set(required_types) - present_types
    require(
        not missing_types,
        f"{gap_id}: missing required evidence types: {sorted(missing_types)}",
    )


def validate() -> dict[str, Any]:
    register = load_object(REGISTER)
    index = load_object(EVIDENCE_INDEX)
    evidence = indexed_evidence_rows(index)
    known_evidence = set(evidence)

    require(
        register.get("schema") == "trillionnium.gap-register.v1",
        "unexpected gap schema",
    )
    require(register.get("project_id") == "trillionnium-game", "unexpected project_id")
    require(register.get("plan_version") == 3, "gap register must target plan v3")
    try:
        gap_scope_sha256 = EVIDENCE.validate_gap_scope(register)
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        raise ValidationError(str(error)) from error

    status_values = register.get("status_values")
    require(
        isinstance(status_values, list) and status_values,
        "status_values must be non-empty",
    )
    statuses = set(status_values)
    require(len(statuses) == len(status_values), "duplicate status_values")
    require(TERMINAL_STATUSES <= statuses, "terminal statuses are incomplete")
    require(VERIFIED_STATUSES <= statuses, "verified statuses are incomplete")

    policy = register.get("closure_policy")
    require(isinstance(policy, dict), "closure_policy must be an object")
    require(
        policy.get("implementation_only_closes_gap") is False,
        "implementation-only closure must be forbidden",
    )
    require(
        policy.get("documentation_only_closes_gap") is False,
        "documentation-only closure must be forbidden",
    )
    require(
        policy.get("empty_or_skipped_checks_count") is False,
        "empty/skipped checks must not count",
    )
    require(
        policy.get("exact_candidate_identity_required") is True,
        "exact candidate identity is required",
    )
    require(
        policy.get("independent_review_required_for_p0_p1") is True,
        "P0/P1 independent review is required",
    )
    require(
        policy.get("external_admin_state_must_be_read_back") is True,
        "external state must be read back",
    )

    rows = register.get("gaps")
    require(isinstance(rows, list) and rows, "gaps must be a non-empty list")
    seen: set[str] = set()
    counts: Counter[str] = Counter()
    external_blocked = 0
    source_only = 0

    for row in rows:
        require(isinstance(row, dict), "gap row must be an object")
        gap_id = row.get("id")
        require(
            isinstance(gap_id, str) and gap_id.startswith("GAP-"),
            "invalid gap id",
        )
        require(gap_id not in seen, f"duplicate gap id: {gap_id}")
        seen.add(gap_id)

        severity = row.get("severity")
        status = row.get("status")
        require(
            severity in ALLOWED_SEVERITIES,
            f"{gap_id}: invalid severity {severity!r}",
        )
        require(status in statuses, f"{gap_id}: invalid status {status!r}")
        require(
            isinstance(row.get("category"), str) and row["category"],
            f"{gap_id}: category required",
        )
        require(
            isinstance(row.get("title"), str) and row["title"],
            f"{gap_id}: title required",
        )
        require(
            isinstance(row.get("owner_role"), str) and row["owner_role"],
            f"{gap_id}: owner_role required",
        )
        require(
            isinstance(row.get("blocking_claims"), list),
            f"{gap_id}: blocking_claims must be a list",
        )
        require(
            isinstance(row.get("affected_paths"), list),
            f"{gap_id}: affected_paths must be a list",
        )
        require(
            isinstance(row.get("close_criteria"), list) and row["close_criteria"],
            f"{gap_id}: close criteria required",
        )
        required_types = validate_required_evidence_types(
            gap_id,
            row.get("required_evidence_types"),
        )
        evidence_ids = row.get("evidence_ids")
        require(isinstance(evidence_ids, list), f"{gap_id}: evidence_ids must be a list")
        require(
            len(evidence_ids) == len(set(evidence_ids)),
            f"{gap_id}: duplicate evidence_ids",
        )
        unknown = set(evidence_ids) - known_evidence
        require(not unknown, f"{gap_id}: unknown evidence ids: {sorted(unknown)}")

        external_dependency = row.get("external_dependency")
        if status == "blocked-external-admin":
            external_blocked += 1
            require(
                isinstance(external_dependency, str) and external_dependency,
                f"{gap_id}: external dependency required",
            )
        if status in SOURCE_ONLY_STATUSES:
            source_only += 1

        if status == "closed":
            require(
                external_dependency in (None, ""),
                f"{gap_id}: closed gap retains an external dependency",
            )
            validate_closed_evidence(
                gap_id,
                str(severity),
                required_types,
                evidence_ids,
                evidence,
            )

        counts[str(status)] += 1

    declared_summary = register.get("summary")
    if declared_summary is not None:
        require(isinstance(declared_summary, dict), "summary must be an object")
        require(declared_summary.get("total") == len(rows), "summary.total is stale")
        for status, count in counts.items():
            if status in declared_summary:
                require(
                    declared_summary[status] == count,
                    f"summary.{status} is stale",
                )

    return {
        "schema": "trillionnium.gap-register-validation.v1",
        "gap_scope_sha256": gap_scope_sha256,
        "gap_count": len(rows),
        "status_counts": dict(sorted(counts.items())),
        "external_admin_blocked": external_blocked,
        "source_only": source_only,
        "closed": counts.get("closed", 0),
        "claim_boundary": (
            "Validation proves control-plane consistency only; it grants no "
            "compatibility or production credit."
        ),
    }


def main() -> int:
    try:
        result = validate()
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        print(f"gap register validation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
