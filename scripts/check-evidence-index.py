#!/usr/bin/env python3
"""Validate the plan-v3 evidence index without granting unearned credit."""
from __future__ import annotations

import json
import importlib.util
import re
import sys
from datetime import datetime, timezone
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
INDEX_PATH = ROOT / "docs/evidence/index.json"
HEX40 = re.compile(r"^[a-f0-9]{40}$")
HEX64 = re.compile(r"^(?:sha256:)?[a-f0-9]{64}$")


class ValidationError(RuntimeError):
    """Raised when indexed evidence is ambiguous, stale or over-credited."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def load_object(path: Path) -> dict[str, Any]:
    try:
        return EVIDENCE.load_object(path)
    except (OSError, ValueError, RecursionError) as error:
        raise ValidationError(f"invalid control JSON: {error}") from error


def rows(index: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        return EVIDENCE.index_rows(index)
    except EVIDENCE.AdmissionError as error:
        raise ValidationError(str(error)) from error


def first(row: dict[str, Any], *paths: str) -> Any:
    try:
        return EVIDENCE.exact_alias(row, *paths)
    except (ValueError, TypeError) as error:
        raise ValidationError(str(error)) from error


def credit_enabled(row: dict[str, Any]) -> bool:
    value = first(
        row,
        "compatibility_credit",
        "claim_credit",
        "validity.compatibility_credit",
        "validity.claim_credit",
    )
    return value is True


def parse_time(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    require(parsed.tzinfo is not None, "evidence timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def accepted_review(
    row: dict[str, Any], *, target_commit: str | None = None,
    target_tree: str | None = None,
) -> dict[str, Any] | None:
    return EVIDENCE.accepted_review(row, target_commit=target_commit, target_tree=target_tree)


def validate(
    index_path: Path = INDEX_PATH,
    root: Path = ROOT,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    index = load_object(index_path)
    schema = index.get("schema")
    require(
        isinstance(schema, str) and schema.startswith("trillionnium.evidence-index."),
        "unexpected evidence index schema",
    )
    require(index.get("project_id") == "trillionnium-game", "unexpected project_id")

    policy = index.get("policy", index.get("policies", {}))
    require(isinstance(policy, dict), "evidence policy must be an object")
    required_policy = {
        "exact_target_commit_and_tree_required": True,
        "artifact_sha256_required": True,
        "independent_review_required": True,
        "self_approval_allowed": False,
        "expired_evidence_counts": False,
        "relay_evidence_requires_target_identity_validation": True,
        "logs_or_screenshots_without_archived_digest_count": False,
        "empty_or_skipped_execution_counts": False,
    }
    for key, expected in required_policy.items():
        require(policy.get(key) is expected, f"evidence policy {key} must be {expected}")

    seen: set[str] = set()
    credited = 0
    diagnostic = 0
    accepted_status_rows = 0
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    for row in rows(index):
        evidence_id = row.get("evidence_id")
        require(
            isinstance(evidence_id, str) and evidence_id.startswith("TG-EV-"),
            "invalid evidence_id",
        )
        require(evidence_id not in seen, f"duplicate evidence_id: {evidence_id}")
        seen.add(evidence_id)

        path_value = first(row, "path", "manifest_path", "source.path")
        if path_value is not None:
            require(
                isinstance(path_value, str) and path_value,
                f"{evidence_id}: invalid path",
            )
            try:
                candidate_path = EVIDENCE.repository_path(root, path_value)
            except (OSError, ValueError) as error:
                raise ValidationError(f"{evidence_id}: {error}") from error
            require(
                candidate_path.is_file(),
                f"{evidence_id}: indexed file is missing: {path_value}",
            )

        artifact_rows = first(row, "artifacts", "source.artifacts")
        if artifact_rows is not None:
            require(
                isinstance(artifact_rows, list),
                f"{evidence_id}: artifacts must be a list",
            )
            artifact_keys: set[tuple[str, str]] = set()
            for artifact_index, artifact in enumerate(artifact_rows):
                require(
                    isinstance(artifact, dict),
                    f"{evidence_id}: artifact must be an object",
                )
                digest = artifact.get("sha256", artifact.get("digest"))
                if digest is not None:
                    require(
                        isinstance(digest, str)
                        and HEX64.fullmatch(digest) is not None,
                        f"{evidence_id}: invalid artifact digest",
                    )
                size = artifact.get("size_bytes", artifact.get("size"))
                if size is not None:
                    require(
                        isinstance(size, int) and not isinstance(size, bool) and size > 0,
                        f"{evidence_id}: artifact {artifact_index} must be non-empty",
                    )
                name = artifact.get(
                    "name",
                    artifact.get("profile", artifact.get("artifact_id", artifact_index)),
                )
                key = (str(name), str(digest))
                require(
                    key not in artifact_keys,
                    f"{evidence_id}: duplicate artifact identity {key}",
                )
                artifact_keys.add(key)

        expires_at = first(row, "expires_at", "validity.expires_at")
        expired = False
        if expires_at is not None:
            require(
                isinstance(expires_at, str),
                f"{evidence_id}: expires_at must be a string",
            )
            expired = parse_time(expires_at) <= current_time

        status = row.get("status")
        if status == "accepted":
            accepted_status_rows += 1

        if status == "accepted" or credit_enabled(row):
            try:
                EVIDENCE.validate_entry(row, root=root, now=current_time)
            except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
                raise ValidationError(f"{evidence_id}: {error}") from error
        if credit_enabled(row):
            credited += 1
            repository = first(
                row, "candidate.repository", "target.repository", "target_repository"
            )
            commit = first(row, "candidate.commit", "target.commit", "target_commit")
            tree = first(row, "candidate.tree", "target.tree", "target_tree")
            schema_valid = first(row, "validity.schema_valid", "schema_valid")
            exact_target = first(
                row,
                "validity.exact_target_identity",
                "exact_target_identity",
                "target_identity_verified_by_current_repo",
            )
            require(
                repository == "TrillionniumFoundation/TrillionniumGame",
                f"{evidence_id}: wrong target repository",
            )
            require(
                isinstance(commit, str) and HEX40.fullmatch(commit) is not None,
                f"{evidence_id}: exact target commit required",
            )
            require(
                isinstance(tree, str) and HEX40.fullmatch(tree) is not None,
                f"{evidence_id}: exact target tree required",
            )
            require(schema_valid is True, f"{evidence_id}: schema validation required")
            require(
                exact_target is True,
                f"{evidence_id}: target identity validation required",
            )
            require(not expired, f"{evidence_id}: expired evidence cannot receive credit")
            require(
                isinstance(artifact_rows, list) and artifact_rows,
                f"{evidence_id}: credited evidence requires artifacts",
            )
            require(
                accepted_review(
                    row,
                    target_commit=commit,
                    target_tree=tree,
                )
                is not None,
                f"{evidence_id}: exact independent accepted review required",
            )
            require(
                status == "accepted",
                f"{evidence_id}: credited evidence status must be accepted",
            )
        else:
            diagnostic += 1

    declared_accepted = index.get("accepted_entry_count")
    if declared_accepted is not None:
        require(
            isinstance(declared_accepted, int)
            and not isinstance(declared_accepted, bool)
            and declared_accepted == credited,
            "accepted_entry_count must equal credited evidence count",
        )
    require(
        accepted_status_rows == credited,
        "credited evidence cannot exceed accepted-status rows",
    )

    return {
        "schema": "trillionnium.evidence-index-validation.v1",
        "evidence_count": len(seen),
        "credited": credited,
        "diagnostic_only": diagnostic,
        "accepted_status_rows": accepted_status_rows,
        "claim_boundary": (
            "Only entries satisfying every exact-identity, artifact, freshness "
            "and independent-review check may receive claim credit."
        ),
    }


def main() -> int:
    try:
        result = validate()
    except (OSError, ValueError, json.JSONDecodeError, ValidationError) as error:
        print(f"evidence index validation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
