#!/usr/bin/env python3
"""Validate the Plan-v3 gap register, including immutable scope and closure proof.

The existing closure/evidence validator is kept in ``gap_register_validation_core``.
This composition root first verifies the reviewed semantic baseline and then runs
all existing evidence, review, status and summary checks. Tests importing the
legacy checker API continue to receive the same functions.

``ROOT`` remains the mutable validation root used by existing fixture tests. The
immutable baseline is always loaded relative to this policy source file, so a test
fixture cannot accidentally become its own proof authority and does not need to
copy the pinned baseline into every temporary repository.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASELINE_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CORE = _load(
    "trnm_gap_register_validation_core",
    Path(__file__).with_name("gap_register_validation_core.py"),
)
SCOPE = _load(
    "trnm_gap_register_scope_policy",
    Path(__file__).with_name("gap_register_scope_policy.py"),
)

EVIDENCE = CORE.EVIDENCE
ValidationError = CORE.ValidationError
ALLOWED_SEVERITIES = CORE.ALLOWED_SEVERITIES
ALLOWED_EVIDENCE_TYPES = CORE.ALLOWED_EVIDENCE_TYPES
TERMINAL_STATUSES = CORE.TERMINAL_STATUSES
VERIFIED_STATUSES = CORE.VERIFIED_STATUSES
SOURCE_ONLY_STATUSES = CORE.SOURCE_ONLY_STATUSES


def _sync_root() -> None:
    CORE.ROOT = ROOT
    CORE.REGISTER = ROOT / "docs/status/GAP_REGISTER.json"
    CORE.EVIDENCE_INDEX = ROOT / "docs/evidence/index.json"


def _immutable_scope(register: dict[str, Any]) -> dict[str, Any]:
    baseline, baseline_payload = SCOPE.load_object(
        BASELINE_ROOT / SCOPE.BASELINE_RELATIVE,
        "gap scope baseline",
    )
    return SCOPE.validate_document(
        register,
        baseline,
        baseline_payload=baseline_payload,
    )


def load_object(path: Path) -> dict[str, Any]:
    _sync_root()
    return CORE.load_object(path)


def indexed_evidence_rows(index: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return CORE.indexed_evidence_rows(index)


def indexed_evidence_ids(index: dict[str, Any]) -> set[str]:
    return CORE.indexed_evidence_ids(index)


def accepted_review(row: dict[str, Any]) -> dict[str, Any] | None:
    return CORE.accepted_review(row)


def validate_required_evidence_types(gap_id: str, values: Any) -> list[str]:
    return CORE.validate_required_evidence_types(gap_id, values)


def validate_closed_evidence(
    gap_id: str,
    severity: str,
    required_types: list[Any],
    evidence_ids: list[str],
    evidence: dict[str, dict[str, Any]],
) -> None:
    _sync_root()
    CORE.validate_closed_evidence(
        gap_id,
        severity,
        required_types,
        evidence_ids,
        evidence,
    )


def validate() -> dict[str, Any]:
    _sync_root()
    register = CORE.load_object(CORE.REGISTER)
    try:
        immutable_scope = _immutable_scope(register)
    except (OSError, TypeError, ValueError) as error:
        raise ValidationError(f"immutable gap scope: {error}") from error
    result = CORE.validate()
    result["immutable_scope"] = immutable_scope
    return result


def __getattr__(name: str):
    """Expose non-root-sensitive helpers retained by the prior checker module."""
    return getattr(CORE, name)


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
