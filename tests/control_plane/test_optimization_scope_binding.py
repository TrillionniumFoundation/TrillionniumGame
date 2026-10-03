"""Pin the candidate-reference repair without granting status or evidence credit."""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_policy(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    if spec is None or spec.loader is None:
        raise RuntimeError("scope policy is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


GAP = load_policy("trnm_optimization_gap_scope", "scripts/gap_register_scope_policy.py")
TRANSITION = load_policy("trnm_optimization_transitions", "scripts/status_transition_policy.py")


class OptimizationScopeBindingTests(unittest.TestCase):
    def setUp(self):
        self.baseline, self.payload = GAP.load_object(ROOT / GAP.BASELINE_RELATIVE, "baseline")
        self.current, _ = GAP.load_object(ROOT / GAP.CURRENT_RELATIVE, "current")

    def test_current_gap_scope_is_pinned_and_has_no_credit(self):
        result = GAP.validate_files(ROOT)
        self.assertEqual(result["gap_count"], 18)
        self.assertEqual(result["close_criteria_total"], 92)
        self.assertFalse(any(result["claim_boundary"].values()))
        integration = next(row for row in self.current["gaps"] if row["id"] == "GAP-P0-PR-001")
        self.assertEqual(integration["candidate_authority_ref"],
                         "docs/status/CURRENT_STATE.json#/authority")

    def test_changed_gap_baseline_bytes_are_rejected(self):
        with self.assertRaisesRegex(GAP.ScopeError, "Git blob identity"):
            GAP.validate_document(self.current, self.baseline,
                                  baseline_payload=self.payload + b" ")

    def test_removed_gap_close_criterion_is_rejected(self):
        self.current["gaps"][0]["close_criteria"].pop()
        with self.assertRaisesRegex(GAP.ScopeError, "semantic scope"):
            GAP.validate_document(self.current, self.baseline, baseline_payload=self.payload)

    def test_replaced_candidate_selector_is_rejected(self):
        integration = next(row for row in self.current["gaps"] if row["id"] == "GAP-P0-PR-001")
        integration["candidate_authority_ref"] = "other.json"
        with self.assertRaisesRegex(GAP.ScopeError, "semantic scope"):
            GAP.validate_document(self.current, self.baseline, baseline_payload=self.payload)

    def roadmap_pair(self):
        roadmap = json.loads((ROOT / "docs/roadmap/NEXT_MILESTONE.json").read_text())
        rows = {row["id"]: row for row in roadmap["items"]}
        current = {
            "plan_version": roadmap["plan_version"],
            "milestone_id": roadmap["milestone_id"],
            "milestone_status": roadmap["status"],
            "item_count": len(rows),
            "item_ids_sha256": TRANSITION._sha256(sorted(rows)),
            "scope_sha256": TRANSITION._sha256(TRANSITION._roadmap_scope(roadmap, rows)),
            "items": {key: row["status"] for key, row in rows.items()},
        }
        previous = copy.deepcopy(current)
        previous["scope_sha256"] = "dc7af646e78d3beb976b78e2a7a8787b8f4a5139d65c996b296dfef0e9060678"
        return previous, current

    def test_current_reference_revision_is_an_explicit_no_credit_transition(self):
        TRANSITION.validate_policy()
        previous, current = self.roadmap_pair()
        events = TRANSITION._compare_roadmap(previous, current, [])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["credit_policy"], "reset-no-verified-or-accepted-state-transfer")
        self.assertEqual(events[0]["previous_item_count"], events[0]["current_item_count"])

    def test_unregistered_new_scope_is_rejected(self):
        previous, current = self.roadmap_pair()
        current["scope_sha256"] = "f" * 64
        with self.assertRaisesRegex(TRANSITION.TransitionError, "exact approved transition"):
            TRANSITION._compare_roadmap(previous, current, [])

    def test_verified_or_accepted_state_cannot_transfer(self):
        for side in ("previous", "current"):
            for status in ("locally-verified", "remote-verified", "independently-reviewed", "accepted"):
                previous, current = self.roadmap_pair()
                selected = previous if side == "previous" else current
                selected["items"]["TG-V3-001"] = status
                with self.subTest(side=side, status=status):
                    with self.assertRaisesRegex(TRANSITION.TransitionError, "verified or accepted"):
                        TRANSITION._compare_roadmap(previous, current, [])

    def test_historical_scope_replacement_is_retained(self):
        historical = TRANSITION.APPROVED_ROADMAP_SCOPE_REPLACEMENTS[0]
        self.assertEqual(historical["previous_item_count"], 25)
        self.assertEqual(historical["current_scope_sha256"],
                         "dc7af646e78d3beb976b78e2a7a8787b8f4a5139d65c996b296dfef0e9060678")


if __name__ == "__main__":
    unittest.main()
