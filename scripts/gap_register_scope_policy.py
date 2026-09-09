#!/usr/bin/env python3
"""Lock the immutable semantic scope of the Plan-v3 gap register.

Status, retained evidence references, generated summaries and the resolution of a
previously declared external dependency may change. Gap identity, severity,
ownership, blocking claims, affected paths, close criteria, evidence classes and
all other semantic fields must continue to match the reviewed baseline.

The baseline bytes are themselves pinned by their Git blob identity. Updating the
approved gap scope therefore requires an explicit source change to this policy,
not a coordinated edit of the live register and its baseline file.
"""
from __future__ import annotations

import hashlib
import json
import re
import stat
import sys
from pathlib import Path
from typing import Any

CURRENT_RELATIVE = "docs/status/GAP_REGISTER.json"
BASELINE_RELATIVE = "scripts/control_baselines/gap-register.v1.json"
BASELINE_GIT_BLOB_SHA1 = "577cfb3a97b6b6b98b8ecd3182991910f3645296"
MAX_JSON_BYTES = 2 * 1024 * 1024
MUTABLE_ROOT_FIELDS = frozenset({"generated_at", "summary"})
MUTABLE_GAP_FIELDS = frozenset({"status", "evidence_ids", "external_dependency"})
EXPECTED_GAP_COUNT = 18
EXPECTED_CLOSE_CRITERIA_TOTAL = 92
GAP_ID = re.compile(r"GAP-P[0-2]-[A-Z0-9-]+\Z")


class ScopeError(ValueError):
    """Raised when the register or immutable baseline is ambiguous or changed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ScopeError(message)


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ScopeError(f"non-finite JSON number: {value}")


def canonical_text(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value == value.strip()
        and 0 < len(value) <= 4096
        and not any(ord(character) < 32 for character in value)
    )


def git_blob_sha1(payload: bytes) -> str:
    header = b"blob " + str(len(payload)).encode("ascii") + b"\0"
    return hashlib.sha1(header + payload).hexdigest()


def read_regular(path: Path) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ScopeError("gap scope file is unavailable") from error
    require(stat.S_ISREG(metadata.st_mode), "gap scope input must be a regular file")
    require(0 < metadata.st_size <= MAX_JSON_BYTES, "gap scope input size is invalid")
    try:
        with path.open("rb") as handle:
            payload = handle.read(MAX_JSON_BYTES + 1)
    except OSError as error:
        raise ScopeError("gap scope file cannot be read") from error
    require(len(payload) <= MAX_JSON_BYTES, "gap scope input exceeds byte limit")
    require(len(payload) == metadata.st_size, "gap scope input changed during read")
    return payload


def parse_object(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ScopeError(f"{label}: invalid JSON") from error
    require(isinstance(value, dict), f"{label}: object root required")
    return value


def load_object(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    payload = read_regular(path)
    return parse_object(payload, label), payload


def immutable_projection(document: dict[str, Any]) -> dict[str, Any]:
    gaps = document.get("gaps")
    require(isinstance(gaps, list), "gap register gaps must be an array")
    root = {
        key: value
        for key, value in document.items()
        if key not in MUTABLE_ROOT_FIELDS and key != "gaps"
    }
    projected_gaps: list[dict[str, Any]] = []
    for index, row in enumerate(gaps):
        require(isinstance(row, dict), f"gaps[{index}]: object required")
        projected_gaps.append(
            {
                key: value
                for key, value in row.items()
                if key not in MUTABLE_GAP_FIELDS
            }
        )
    root["gaps"] = projected_gaps
    return root


def projection_sha256(document: dict[str, Any]) -> str:
    encoded = json.dumps(
        immutable_projection(document),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_mutable_fields(
    current: dict[str, Any], baseline: dict[str, Any]
) -> dict[str, Any]:
    statuses = baseline.get("status_values")
    require(
        isinstance(statuses, list)
        and statuses
        and len(statuses) == len(set(statuses))
        and all(canonical_text(value) for value in statuses),
        "baseline status vocabulary is invalid",
    )
    current_rows = current.get("gaps")
    baseline_rows = baseline.get("gaps")
    require(
        isinstance(current_rows, list) and isinstance(baseline_rows, list),
        "gap rows must be arrays",
    )
    require(
        len(current_rows) == len(baseline_rows) == EXPECTED_GAP_COUNT,
        "gap count differs from the approved denominator",
    )
    seen: set[str] = set()
    external_required: list[str] = []
    for index, (row, approved) in enumerate(zip(current_rows, baseline_rows)):
        require(
            isinstance(row, dict) and isinstance(approved, dict),
            f"gaps[{index}]: object required",
        )
        gap_id = row.get("id")
        require(
            isinstance(gap_id, str)
            and GAP_ID.fullmatch(gap_id) is not None
            and gap_id not in seen,
            f"gaps[{index}]: invalid or duplicate gap ID",
        )
        require(gap_id == approved.get("id"), f"{gap_id}: baseline order or ID changed")
        seen.add(gap_id)
        status_value = row.get("status")
        require(status_value in statuses, f"{gap_id}: invalid mutable status")
        evidence_ids = row.get("evidence_ids")
        require(
            isinstance(evidence_ids, list)
            and len(evidence_ids) == len(set(evidence_ids))
            and all(canonical_text(value) for value in evidence_ids),
            f"{gap_id}: invalid mutable evidence IDs",
        )
        approved_dependency = approved.get("external_dependency")
        current_dependency = row.get("external_dependency")
        if approved_dependency is None:
            require(
                current_dependency is None,
                f"{gap_id}: internal gap cannot gain an external dependency",
            )
        else:
            require(
                canonical_text(approved_dependency),
                f"{gap_id}: baseline external dependency is invalid",
            )
            external_required.append(gap_id)
            if status_value == "closed":
                require(
                    current_dependency in (None, ""),
                    f"{gap_id}: closed gap must clear its external dependency",
                )
            else:
                require(
                    current_dependency == approved_dependency,
                    f"{gap_id}: unresolved external dependency changed or was removed",
                )
    criteria_total = sum(
        len(row.get("close_criteria", [])) for row in current_rows
    )
    require(
        criteria_total == EXPECTED_CLOSE_CRITERIA_TOTAL,
        "close-criteria denominator differs from the approved baseline",
    )
    return {
        "gap_count": len(current_rows),
        "close_criteria_total": criteria_total,
        "external_dependency_gap_ids": external_required,
    }


def validate_document(
    current: dict[str, Any],
    baseline: dict[str, Any],
    *,
    baseline_payload: bytes | None = None,
) -> dict[str, Any]:
    if baseline_payload is not None:
        require(
            git_blob_sha1(baseline_payload) == BASELINE_GIT_BLOB_SHA1,
            "gap scope baseline Git blob identity changed",
        )
    for label, document in (("current", current), ("baseline", baseline)):
        require(
            document.get("schema") == "trillionnium.gap-register.v1",
            f"{label}: unexpected gap-register schema",
        )
        require(
            document.get("project_id") == "trillionnium-game",
            f"{label}: unexpected project identity",
        )
        require(
            document.get("plan_version") == 3,
            f"{label}: unexpected plan version",
        )
    baseline_projection = immutable_projection(baseline)
    current_projection = immutable_projection(current)
    require(
        current_projection == baseline_projection,
        "immutable gap-register semantic scope changed",
    )
    mutable = _validate_mutable_fields(current, baseline)
    return {
        "schema": "trillionnium.gap-register-scope-validation.v1",
        "status": "verified",
        "baseline_path": BASELINE_RELATIVE,
        "baseline_git_blob_sha1": BASELINE_GIT_BLOB_SHA1,
        "projection_sha256": projection_sha256(current),
        **mutable,
        "claim_boundary": {
            "status_promoted": False,
            "evidence_accepted": False,
            "gap_closed": False,
            "production_ready": False,
        },
    }


def validate_files(
    root: Path, *, current_document: dict[str, Any] | None = None
) -> dict[str, Any]:
    root = root.resolve()
    baseline, baseline_payload = load_object(
        root / BASELINE_RELATIVE, "gap scope baseline"
    )
    if current_document is None:
        current, _ = load_object(root / CURRENT_RELATIVE, "gap register")
    else:
        require(isinstance(current_document, dict), "current gap register object required")
        current = current_document
    return validate_document(
        current,
        baseline,
        baseline_payload=baseline_payload,
    )


def main() -> int:
    try:
        result = validate_files(Path(__file__).resolve().parents[1])
    except (OSError, ScopeError, TypeError, ValueError) as error:
        print(f"gap scope validation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
