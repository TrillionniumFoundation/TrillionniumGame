#!/usr/bin/env python3
"""Derive product-gate status from gap, evidence and dependency state."""
from __future__ import annotations

import argparse
import json
import importlib.util
import sys
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAX_GATE_FRESHNESS_DAYS = 36_500

_SPEC = importlib.util.spec_from_file_location(
    "trnm_evidence_admission_" + Path(__file__).stem.replace("-", "_"),
    Path(__file__).with_name("evidence_admission.py"),
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("cannot load shared evidence admission contract")
EVIDENCE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = EVIDENCE
_SPEC.loader.exec_module(EVIDENCE)


class DerivationError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise DerivationError(message)


def load_json(path: str) -> dict[str, Any]:
    try:
        return EVIDENCE.load_object(ROOT / path)
    except (OSError, ValueError, RecursionError) as error:
        raise DerivationError(f"invalid control JSON: {error}") from error


def parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        fail(f"invalid datetime value {value!r}")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        fail(f"invalid datetime {value!r}: {exc}")
    if parsed.tzinfo is None:
        fail(f"datetime must include timezone: {value!r}")
    return parsed.astimezone(timezone.utc)


def accepted_evidence_records(
    entries: list[dict[str, Any]], now: datetime
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Return index rows paired with the manifest validated in the same pass."""
    accepted: list[tuple[dict[str, Any], dict[str, Any]]] = []
    try:
        for row in EVIDENCE.index_rows({"entries": entries}):
            credit = EVIDENCE.exact_alias(
                row,
                "compatibility_credit",
                "claim_credit",
                "validity.compatibility_credit",
                "validity.claim_credit",
            )
            if row.get("status") == "accepted" or credit is True:
                manifest = EVIDENCE.validate_entry(row, root=ROOT, now=now)
                accepted.append((row, manifest))
        if len({EVIDENCE.target_identity(row) for row, _ in accepted}) > 1:
            fail("accepted gate evidence mixes candidate identities")
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
        raise DerivationError(str(error)) from error
    return accepted


def accepted_evidence(entries: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """Compatibility wrapper for consumers that need only admitted index rows."""
    return [row for row, _ in accepted_evidence_records(entries, now)]


def freshness_days(gate_id: str, row: dict[str, Any]) -> int | None:
    """Require an explicit unlimited or positive integral gate freshness policy."""
    if "freshness_days" not in row:
        fail(f"{gate_id}: freshness_days is required")
    value = row["freshness_days"]
    if value is None:
        return None
    if type(value) is not int or not 0 < value <= MAX_GATE_FRESHNESS_DAYS:
        fail(
            f"{gate_id}: freshness_days must be null or an integer from 1 "
            f"through {MAX_GATE_FRESHNESS_DAYS}"
        )
    return value


def topological_order(gates: dict[str, dict[str, Any]]) -> list[str]:
    indegree = {gate_id: 0 for gate_id in gates}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for gate_id, row in gates.items():
        dependencies = row.get("depends_on", [])
        if not isinstance(dependencies, list):
            fail(f"{gate_id}: depends_on must be an array")
        unknown = set(dependencies) - set(gates)
        if unknown:
            fail(f"{gate_id}: unknown dependencies {sorted(unknown)}")
        for dependency in dependencies:
            indegree[gate_id] += 1
            outgoing[dependency].append(gate_id)
    queue = deque(sorted(gate_id for gate_id, degree in indegree.items() if degree == 0))
    ordered: list[str] = []
    while queue:
        gate_id = queue.popleft()
        ordered.append(gate_id)
        for follower in outgoing[gate_id]:
            indegree[follower] -= 1
            if indegree[follower] == 0:
                queue.append(follower)
    if len(ordered) != len(gates):
        fail("product gate dependency cycle")
    return ordered


def derive(now: datetime | None = None) -> dict[str, Any]:
    gaps_document = load_json("docs/status/GAP_REGISTER.json")
    gates_document = load_json("docs/status/PRODUCT_GATES.json")
    evidence_document = load_json("docs/evidence/index.json")

    gaps: dict[str, dict[str, Any]] = {}
    for row in gaps_document.get("gaps", []):
        gap_id = row.get("id")
        if not isinstance(gap_id, str) or not gap_id:
            fail("gap missing ID")
        if gap_id in gaps:
            fail(f"duplicate gap {gap_id}")
        gaps[gap_id] = row

    gates: dict[str, dict[str, Any]] = {}
    for row in gates_document.get("gates", []):
        gate_id = row.get("id")
        if not isinstance(gate_id, str) or not gate_id:
            fail("gate missing ID")
        if gate_id in gates:
            fail(f"duplicate gate {gate_id}")
        gates[gate_id] = row

    try:
        current = EVIDENCE.clock(now)
        entries = EVIDENCE.index_rows(evidence_document)
        evidence = {entry["evidence_id"]: entry for entry in entries}
        for gap in gaps.values():
            if gap.get("status") == "closed":
                EVIDENCE.validate_gap_evidence(gap, evidence, root=ROOT, now=current)
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
        raise DerivationError(str(error)) from error
    accepted_records = accepted_evidence_records(entries, current)
    accepted = [row for row, _ in accepted_records]
    accepted_ids = {row["evidence_id"] for row in accepted}

    status: dict[str, str] = {}
    detail: dict[str, dict[str, Any]] = {}
    for gate_id in topological_order(gates):
        row = gates[gate_id]
        dependencies = row.get("depends_on", [])
        dependency_blockers = [dependency for dependency in dependencies if status.get(dependency) != "passed"]

        gap_ids = row.get("blocking_gap_ids", [])
        unknown_gaps = set(gap_ids) - set(gaps)
        if unknown_gaps:
            fail(f"{gate_id}: unknown gaps {sorted(unknown_gaps)}")
        open_gaps = [gap_id for gap_id in gap_ids if gaps[gap_id].get("status") != "closed"]

        required_types = set(row.get("evidence_types", []))
        if not required_types or not required_types <= EVIDENCE.EVIDENCE_TYPES:
            fail(f"{gate_id}: nonempty supported evidence types required")
        age_limit = freshness_days(gate_id, row)
        cutoff = current - timedelta(days=age_limit) if age_limit is not None else None
        matching_records = [
            (entry, manifest)
            for entry, manifest in accepted_records
            if gate_id in entry.get("gate_ids", [])
        ]
        fresh_records = [
            (entry, manifest)
            for entry, manifest in matching_records
            if cutoff is None or EVIDENCE.parse_time(manifest["completed_at"]) >= cutoff
        ]
        stale_records = [
            (entry, manifest)
            for entry, manifest in matching_records
            if cutoff is not None and EVIDENCE.parse_time(manifest["completed_at"]) < cutoff
        ]
        present_types = {entry.get("evidence_type") for entry, _ in fresh_records}
        missing_types = sorted(required_types - present_types)

        if dependency_blockers or open_gaps:
            derived_status = "blocked"
        elif missing_types:
            derived_status = "open"
        else:
            derived_status = "passed"
        status[gate_id] = derived_status
        detail[gate_id] = {
            "status": derived_status,
            "dependency_blockers": dependency_blockers,
            "open_gaps": open_gaps,
            "missing_evidence_types": missing_types,
            "freshness_days": age_limit,
            "freshness_cutoff": cutoff.isoformat() if cutoff is not None else None,
            "accepted_evidence_ids": sorted(
                entry["evidence_id"]
                for entry, _ in fresh_records
                if entry["evidence_id"] in accepted_ids
            ),
            "stale_evidence_ids": sorted(
                entry["evidence_id"] for entry, _ in stale_records
            ),
        }

    summary = {
        state: sum(value == state for value in status.values())
        for state in ("passed", "open", "blocked")
    }
    return {
        "schema": "trillionnium.derived-gates.v1",
        "generated_at": current.isoformat(),
        "accepted_evidence_count": len(accepted),
        "gates": detail,
        "summary": summary,
    }


def check_snapshot(derived: dict[str, Any]) -> None:
    snapshot = load_json("docs/status/PRODUCT_GATES.json")
    derived_gates = derived["gates"]
    for row in snapshot.get("gates", []):
        gate_id = row["id"]
        expected = derived_gates[gate_id]["status"]
        if row.get("status") != expected:
            fail(
                f"{gate_id}: snapshot status {row.get('status')!r} does not match derived {expected!r}"
            )
    if snapshot.get("summary") is None:
        fail("product gate summary missing")
    for state, count in derived["summary"].items():
        if snapshot["summary"].get(state) != count:
            fail(f"product gate summary {state}: expected {count}")
    if derived["summary"]["passed"] == 0:
        if snapshot.get("release_claim") != "planning-only":
            fail("release claim must remain planning-only")
        if snapshot.get("compatibility_level") != "C0-not-earned":
            fail("compatibility level must remain C0-not-earned")
        for field in ("public_online", "drop_in_replacement", "production_ready", "nakama_retired"):
            if snapshot.get(field) is not False:
                fail(f"{field} must remain false")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print-json", action="store_true", help="print the full derived result")
    args = parser.parse_args()
    try:
        derived = derive()
        check_snapshot(derived)
    except DerivationError as exc:
        print(f"gate derivation failed: {exc}", file=sys.stderr)
        return 1
    if args.print_json:
        print(json.dumps(derived, indent=2, sort_keys=True))
    else:
        summary = derived["summary"]
        print(
            "TrillionniumGame product gates: "
            f"{summary['passed']} passed, {summary['open']} open, {summary['blocked']} blocked"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
