#!/usr/bin/env python3
"""Derive fail-closed gap progress without mutating the authoritative register."""
from __future__ import annotations

import json
import importlib.util
import sys
from collections import Counter
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
REGISTER_PATH = ROOT / "docs/status/GAP_REGISTER.json"
EVIDENCE_PATH = ROOT / "docs/evidence/index.json"


class GapDerivationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GapDerivationError(message)


def load(path: Path) -> dict[str, Any]:
    try:
        return EVIDENCE.load_object(path)
    except (OSError, ValueError, RecursionError) as error:
        raise GapDerivationError(f"invalid control JSON: {error}") from error


def text(path: str) -> str:
    target = ROOT / path
    require(target.is_file(), f"missing source path: {path}")
    return target.read_text(encoding="utf-8")


def source_candidates() -> dict[str, dict[str, object]]:
    probes: dict[str, tuple[str, tuple[str, ...]]] = {
        "GAP-P0-PLAN-001": (
            "scripts/status_transition_policy.py",
            ("allowed_transitions", "validate_transition", "compare_roots"),
        ),
        "GAP-P0-EVIDENCE-001": (
            "docs/evidence/index.json",
            ("trillionnium.evidence-index.v1", "compatibility_credit"),
        ),
        "GAP-P0-DATA-001": (
            "scripts/check-schema-authority.py",
            ("SCHEMA_AUTHORITY.json", "FORBIDDEN_LITERAL"),
        ),
        "GAP-P0-SERVER-001": (
            "crates/trnm-persistence-pg/src/bin/trnm_server/app.rs",
            (
                '"/v1/authority/commit"',
                "CommitOutcome::Duplicate",
                "acknowledgement-after-commit fence",
            ),
        ),
        "GAP-P0-CRYPTO-001": (
            ".github/workflows/trillionnium-game-merge-gate.yml",
            (
                "token-jwt-adapter",
                "token-crypto-provider",
                "token-jwt-provider-adapter",
            ),
        ),
        "GAP-P1-CRYPTO-002": (
            "crates/trnm-token-jwt-adapter/src/sha256.rs",
            ("let mut difference = left.len() ^ right.len()", "let long = vec![0u8; 288]"),
        ),
        "GAP-P1-OUTBOX-001": (
            "crates/trnm-persistence-core/src/lib.rs",
            ("OUTBOX_ATTEMPT_LIMIT_REASON", "OutboxState::DeadLetter"),
        ),
        "GAP-P1-IDENTITY-001": (
            "runtime/go.mod",
            ("module github.com/TrillionniumFoundation/TrillionniumGame/runtime",),
        ),
        "GAP-P1-TEST-001": (
            "crates/trnm-persistence-pg/tests/runtime.rs",
            ("TRNM_REQUIRE_LIVE_DATABASE", "no evidence credit"),
        ),
    }
    result: dict[str, dict[str, object]] = {}
    for gap_id, (path, tokens) in probes.items():
        value = text(path)
        missing = [token for token in tokens if token not in value]
        result[gap_id] = {
            "path": path,
            "tokens_total": len(tokens),
            "tokens_present": len(tokens) - len(missing),
            "missing_tokens": missing,
            "source_candidate": not missing,
        }
    return result


def evidence_by_id(index: dict[str, Any]) -> dict[str, dict[str, Any]]:
    try:
        return {row["evidence_id"]: row for row in EVIDENCE.index_rows(index)}
    except EVIDENCE.AdmissionError as error:
        raise GapDerivationError(str(error)) from error


def accepted_evidence(entry: dict[str, Any]) -> bool:
    return EVIDENCE.entry_eligible(entry, root=ROOT)


def derive() -> dict[str, Any]:
    register = load(REGISTER_PATH)
    evidence_index = load(EVIDENCE_PATH)
    evidence = evidence_by_id(evidence_index)
    probes = source_candidates()

    gaps = register.get("gaps")
    require(isinstance(gaps, list) and gaps, "gap register must contain gaps")
    allowed = set(register.get("status_values", []))
    require(allowed, "gap register status values are empty")

    ids: set[str] = set()
    derived: list[dict[str, Any]] = []
    for gap in gaps:
        require(isinstance(gap, dict), "gap entry must be an object")
        gap_id = gap.get("id")
        require(isinstance(gap_id, str) and gap_id, "gap ID missing")
        require(gap_id not in ids, f"duplicate gap ID: {gap_id}")
        ids.add(gap_id)
        declared = gap.get("status")
        require(declared in allowed, f"{gap_id}: invalid declared status {declared!r}")

        evidence_ids = gap.get("evidence_ids", [])
        require(isinstance(evidence_ids, list), f"{gap_id}: evidence_ids must be a list")
        unknown = [value for value in evidence_ids if value not in evidence]
        require(not unknown, f"{gap_id}: unknown evidence IDs {unknown}")
        accepted = [value for value in evidence_ids if accepted_evidence(evidence[value])]

        severity = gap.get("severity")
        independent_required = severity in {"P0", "P1"}
        probe = probes.get(gap_id)
        source_candidate = bool(probe and probe["source_candidate"])

        if declared == "closed":
            try:
                EVIDENCE.validate_gap_evidence(gap, evidence, root=ROOT)
            except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
                raise GapDerivationError(f"{gap_id}: {error}") from error
            require(evidence_ids, f"{gap_id}: closed gap has no evidence")
            require(len(accepted) == len(evidence_ids), f"{gap_id}: closed gap has unaccepted evidence")
            require(not gap.get("external_dependency"), f"{gap_id}: closed gap retains external dependency")
            if independent_required:
                require(accepted, f"{gap_id}: P0/P1 closure requires accepted independent evidence")

        if declared == "blocked-external-admin":
            require(bool(gap.get("external_dependency")), f"{gap_id}: external-admin gap lacks dependency")

        if declared == "closed":
            suggested = "closed"
        elif accepted and not gap.get("external_dependency"):
            suggested = "independently-reviewed" if independent_required else "remote-verified"
        elif source_candidate:
            suggested = "source-candidate"
        else:
            suggested = declared

        derived.append(
            {
                "id": gap_id,
                "severity": severity,
                "declared_status": declared,
                "suggested_status": suggested,
                "source_probe": probe,
                "evidence_total": len(evidence_ids),
                "accepted_evidence": accepted,
                "external_dependency": gap.get("external_dependency"),
                "closed": declared == "closed",
            }
        )

    counts = Counter(row["suggested_status"] for row in derived)
    return {
        "schema": "trillionnium.gap-derivation.v1",
        "project_id": register.get("project_id"),
        "gaps_total": len(derived),
        "suggested_status_counts": dict(sorted(counts.items())),
        "source_candidate_probes": len(probes),
        "gaps": derived,
        "claim_boundary": {
            "mutates_register": False,
            "source_candidate_closes_gap": False,
            "local_execution_closes_gap": False,
            "p0_p1_independent_review_required": True,
            "external_admin_readback_required": True,
        },
    }


def main() -> int:
    try:
        result = derive()
    except GapDerivationError as error:
        print(f"gap derivation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
