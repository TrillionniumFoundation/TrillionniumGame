#!/usr/bin/env python3
"""Hostile regression contract for non-shrinkable Plan v3 gap semantics.

This suite deliberately lives in complete Python discovery: the required aggregate
must not be able to accept a gap register that removes a gap, external dependency,
blocking claim, close criterion or required evidence class before claiming closure.
It validates source shape only; it does not close a gap or fabricate evidence.
"""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REGISTER = ROOT / "docs/status/GAP_REGISTER.json"
AGGREGATE = ROOT / ".github/workflows/trillionnium-game-merge-gate.yml"

EXPECTED_STATUS_VALUES = {
    "open", "ready", "in-progress", "source-candidate", "locally-verified",
    "remote-verified", "independently-reviewed", "closed",
    "blocked-external-admin", "rejected", "superseded",
}
EXPECTED_CLOSURE_POLICY = {
    "implementation_only_closes_gap": False,
    "documentation_only_closes_gap": False,
    "empty_or_skipped_checks_count": False,
    "exact_candidate_identity_required": True,
    "independent_review_required_for_p0_p1": True,
    "external_admin_state_must_be_read_back": True,
}

# Values are immutable minima. Additional evidence classes or blocking claims may
# be introduced only without removing the baseline obligation.
EXPECTED: dict[str, dict[str, Any]] = {
    "GAP-P0-CI-001": {"severity": "P0", "external": False, "evidence": {"manifest", "unit"}, "claims": {"C0", "C1", "C2", "C3", "C4", "C5", "SG0", "SG1", "SG2", "SG3", "SG4"}},
    "GAP-P0-GOV-001": {"severity": "P0", "external": True, "evidence": {"manifest"}, "claims": {"C0", "C1", "C2", "C3", "C4", "C5", "SG0"}},
    "GAP-P0-PR-001": {"severity": "P0", "external": False, "evidence": {"manifest"}, "claims": {"SG0"}},
    "GAP-P0-PLAN-001": {"severity": "P0", "external": False, "evidence": {"unit", "manifest"}, "claims": {"SG0", "SG1"}},
    "GAP-P0-EVIDENCE-001": {"severity": "P0", "external": False, "evidence": {"manifest"}, "claims": {"C1", "C2", "C3", "C4", "C5", "SG1", "SG2"}},
    "GAP-P0-DATA-001": {"severity": "P0", "external": False, "evidence": {"manifest", "database-differential", "backup-restore"}, "claims": {"C2", "C3", "C4", "C5", "SG4", "SG5", "SG8"}},
    "GAP-P0-SERVER-001": {"severity": "P0", "external": False, "evidence": {"unit", "wire-differential", "database-differential", "fault-injection"}, "claims": {"C1", "C2", "C3", "C4", "C5", "SG4"}},
    "GAP-P0-CRYPTO-001": {"severity": "P0", "external": True, "evidence": {"unit", "fuzz", "security-review"}, "claims": {"C2", "C4", "C5", "SG5", "SG8"}},
    "GAP-P1-CRYPTO-002": {"severity": "P1", "external": False, "evidence": {"unit", "security-review"}, "claims": {"C2", "C4", "C5"}},
    "GAP-P1-OUTBOX-001": {"severity": "P1", "external": False, "evidence": {"unit", "property", "database-differential", "fault-injection"}, "claims": {"C2", "C4", "C5", "SG4"}},
    "GAP-P1-IDENTITY-001": {"severity": "P1", "external": False, "evidence": {"unit", "manifest"}, "claims": {"SG0"}},
    "GAP-P1-TEST-001": {"severity": "P1", "external": False, "evidence": {"unit", "database-differential"}, "claims": {"C2", "C3", "C4", "C5"}},
    "GAP-P1-STORAGE-001": {"severity": "P1", "external": False, "evidence": {"unit", "wire-differential", "database-differential"}, "claims": {"C2", "C3", "C5", "SG5"}},
    "GAP-P1-PG-001": {"severity": "P1", "external": False, "evidence": {"database-differential", "fault-injection", "performance", "security-review"}, "claims": {"C2", "C4", "C5", "SG4", "SG8"}},
    "GAP-P0-SCOPE-001": {"severity": "P0", "external": True, "evidence": {"manifest"}, "claims": {"C1", "C2", "C3", "C4", "C5", "SG1"}},
    "GAP-P1-DOCS-001": {"severity": "P1", "external": False, "evidence": {"unit", "manifest"}, "claims": set()},
    "GAP-P1-BRANCH-001": {"severity": "P1", "external": True, "evidence": {"manifest"}, "claims": set()},
    "GAP-P1-REVIEW-001": {"severity": "P1", "external": True, "evidence": set(), "claims": set()},
}
MINIMUM_TOTAL_CLOSE_CRITERIA = 92
MINIMUM_CRITERIA_PER_GAP = 4


class ScopeError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ScopeError(message)


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load(path: Path = REGISTER) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    require(isinstance(value, dict), "gap register root must be an object")
    return value


def validate(value: dict[str, Any]) -> None:
    require(value.get("schema") == "trillionnium.gap-register.v1", "gap schema changed")
    require(value.get("project_id") == "trillionnium-game", "gap project changed")
    require(value.get("plan_version") == 3, "gap plan changed")
    statuses = value.get("status_values")
    require(isinstance(statuses, list), "status_values must be a list")
    require(len(statuses) == len(set(statuses)), "duplicate status value")
    require(set(statuses) == EXPECTED_STATUS_VALUES, "status vocabulary changed")
    policy = value.get("closure_policy")
    require(isinstance(policy, dict), "closure_policy must be an object")
    require(policy == EXPECTED_CLOSURE_POLICY, "closure policy weakened or changed")

    rows = value.get("gaps")
    require(isinstance(rows, list), "gaps must be a list")
    require(len(rows) == len(EXPECTED), "gap cardinality changed")
    by_id: dict[str, dict[str, Any]] = {}
    total_criteria = 0
    for index, row in enumerate(rows):
        require(isinstance(row, dict), f"gaps[{index}] must be an object")
        gap_id = row.get("id")
        require(isinstance(gap_id, str) and gap_id not in by_id, "invalid or duplicate gap id")
        by_id[gap_id] = row
    require(set(by_id) == set(EXPECTED), "registered gap ID set changed")

    for gap_id, expected in EXPECTED.items():
        row = by_id[gap_id]
        require(row.get("severity") == expected["severity"], f"{gap_id}: severity changed")
        for field in ("category", "title", "owner_role"):
            item = row.get(field)
            require(isinstance(item, str) and item.strip() == item and bool(item), f"{gap_id}: invalid {field}")
        status = row.get("status")
        require(status in EXPECTED_STATUS_VALUES, f"{gap_id}: invalid status")
        dependency = row.get("external_dependency")
        if expected["external"]:
            require(isinstance(dependency, str) and bool(dependency.strip()), f"{gap_id}: external dependency removed")
        else:
            require(dependency is None, f"{gap_id}: unexpected external dependency")

        evidence = row.get("required_evidence_types")
        require(isinstance(evidence, list) and len(evidence) == len(set(evidence)), f"{gap_id}: invalid evidence types")
        require(expected["evidence"].issubset(set(evidence)), f"{gap_id}: required evidence class removed")
        claims = row.get("blocking_claims")
        require(isinstance(claims, list) and len(claims) == len(set(claims)), f"{gap_id}: invalid blocking claims")
        require(expected["claims"].issubset(set(claims)), f"{gap_id}: blocking claim removed")

        criteria = row.get("close_criteria")
        require(isinstance(criteria, list) and len(criteria) >= MINIMUM_CRITERIA_PER_GAP, f"{gap_id}: close criteria shrank")
        normalized: list[str] = []
        for criterion in criteria:
            require(isinstance(criterion, str) and criterion.strip() == criterion and bool(criterion), f"{gap_id}: invalid close criterion")
            normalized.append(" ".join(criterion.lower().split()))
        require(len(normalized) == len(set(normalized)), f"{gap_id}: duplicate close criterion")
        total_criteria += len(criteria)

        ids = row.get("evidence_ids")
        require(isinstance(ids, list) and len(ids) == len(set(ids)), f"{gap_id}: invalid evidence IDs")
        require(all(isinstance(item, str) and item for item in ids), f"{gap_id}: empty evidence ID")
        affected = row.get("affected_paths")
        require(isinstance(affected, list) and len(affected) == len(set(affected)), f"{gap_id}: invalid affected paths")
        require(all(isinstance(item, str) and item for item in affected), f"{gap_id}: empty affected path")

    require(total_criteria >= MINIMUM_TOTAL_CLOSE_CRITERIA, "aggregate close-criteria denominator shrank")
    require(value.get("fail_closed") is True, "gap register must remain fail closed")


class GapRegisterScopeLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.current = load()

    def rejected(self, mutation) -> None:
        value = copy.deepcopy(self.current)
        mutation(value)
        with self.assertRaises(ScopeError):
            validate(value)

    def test_repository_gap_scope_is_non_shrinkable(self):
        validate(self.current)

    def test_gap_removal_or_substitution_is_rejected(self):
        self.rejected(lambda v: v["gaps"].pop())
        self.rejected(lambda v: v["gaps"].append({"id": "GAP-P0-FAKE-001"}))

    def test_severity_external_dependency_and_policy_cannot_be_weakened(self):
        self.rejected(lambda v: v["gaps"][0].__setitem__("severity", "P1"))
        self.rejected(lambda v: next(x for x in v["gaps"] if x["id"] == "GAP-P0-GOV-001").__setitem__("external_dependency", None))
        self.rejected(lambda v: v["closure_policy"].__setitem__("independent_review_required_for_p0_p1", False))

    def test_required_evidence_and_blocking_claims_cannot_shrink(self):
        self.rejected(lambda v: next(x for x in v["gaps"] if x["id"] == "GAP-P0-DATA-001")["required_evidence_types"].remove("backup-restore"))
        self.rejected(lambda v: next(x for x in v["gaps"] if x["id"] == "GAP-P0-SERVER-001")["blocking_claims"].remove("C5"))

    def test_close_criteria_denominator_and_per_gap_minimum_cannot_shrink(self):
        self.rejected(lambda v: next(x for x in v["gaps"] if x["id"] == "GAP-P0-CI-001").__setitem__("close_criteria", ["one", "two", "three"]))
        def reduce_total(v):
            for row in v["gaps"]:
                row["close_criteria"] = row["close_criteria"][:MINIMUM_CRITERIA_PER_GAP]
        self.rejected(reduce_total)

    def test_duplicate_keys_ids_criteria_and_evidence_are_rejected(self):
        with self.assertRaises(ScopeError):
            json.loads('{"schema":"a","schema":"b"}', object_pairs_hook=unique_object)
        self.rejected(lambda v: v["gaps"].append(copy.deepcopy(v["gaps"][0])))
        self.rejected(lambda v: next(x for x in v["gaps"] if x["id"] == "GAP-P0-CI-001")["close_criteria"].append(next(x for x in v["gaps"] if x["id"] == "GAP-P0-CI-001")["close_criteria"][0]))
        self.rejected(lambda v: next(x for x in v["gaps"] if x["id"] == "GAP-P0-CI-001")["evidence_ids"].extend(["TG-EV-X", "TG-EV-X"]))

    def test_required_aggregate_still_discovers_complete_python_suite(self):
        source = AGGREGATE.read_text(encoding="utf-8")
        self.assertIn("unittest discover", source)
        self.assertIn("test_*.py", source)
        self.assertIn("python", source.lower())


if __name__ == "__main__":
    unittest.main()
