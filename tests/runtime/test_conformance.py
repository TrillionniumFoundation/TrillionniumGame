from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from tools.runtime.conformance import (
    ConformanceError,
    canonical_bytes,
    compare_observations,
    evaluate_engine_selection,
)

ROOT = Path(__file__).resolve().parents[2]
POLICY = json.loads((ROOT / "config/runtime-engine-conformance-policy.json").read_text())
CORPUS = json.loads((ROOT / "corpus/runtime/runtime-engine-corpus.v1.json").read_text())


def observations() -> list[dict]:
    result = []
    for case in CORPUS["cases"]:
        for lane in POLICY["lanes"]:
            for attempt in range(1, POLICY["attempts_per_lane"] + 1):
                result.append({
                    "schema": "trillionnium.runtime-engine-observation.v1",
                    "engine": case["engine"],
                    "lane": lane,
                    "case_id": case["id"],
                    "category": case["category"],
                    "attempt": attempt,
                    "input_sha256": case["input_sha256"],
                    "return_value": {"case": case["id"], "ok": True},
                    "error": None,
                    "stdout": "",
                    "host_calls": [],
                    "resources": {"instruction_count": 100, "memory_bytes": 1024, "stdout_bytes": 0, "host_call_count": 0},
                })
    return result


class RuntimeConformanceTests(unittest.TestCase):
    def test_full_seed_corpus_covers_required_categories(self):
        for engine in POLICY["required_engines"]:
            categories = {case["category"] for case in CORPUS["cases"] if case["engine"] == engine}
            self.assertTrue(set(POLICY["required_categories"]) <= categories)

    def test_semantic_match_requires_ten_stable_attempts(self):
        evidence = compare_observations(observations(), POLICY, CORPUS)
        self.assertEqual(evidence["status"], "semantic-candidate")
        self.assertEqual(evidence["divergence_counts"]["P0"], 0)
        self.assertEqual(evidence["divergence_counts"]["P1"], 0)
        self.assertFalse(evidence["claims"]["runtime_semantic_equivalence"])

    def test_missing_attempt_is_rejected(self):
        values = observations()
        values.pop()
        with self.assertRaises(ConformanceError):
            compare_observations(values, POLICY, CORPUS)

    def test_lane_nondeterminism_is_blocking(self):
        values = observations()
        target = next(item for item in values if item["lane"] == "rust-candidate" and item["attempt"] == 10)
        target["return_value"] = "different"
        evidence = compare_observations(values, POLICY, CORPUS)
        self.assertGreater(evidence["divergence_counts"]["P1"], 0)
        self.assertEqual(evidence["status"], "blocked")

    def test_return_value_mismatch_is_p1(self):
        values = observations()
        for item in values:
            if item["lane"] == "rust-candidate" and item["case_id"] == CORPUS["cases"][0]["id"]:
                item["return_value"] = "different"
        evidence = compare_observations(values, POLICY, CORPUS)
        self.assertTrue(any(item["severity"] == "P1" and item["path"].startswith("$.return_value") for item in evidence["divergences"]))

    def test_host_call_mismatch_is_p0(self):
        values = observations()
        for item in values:
            if item["lane"] == "rust-candidate" and item["case_id"] == CORPUS["cases"][0]["id"]:
                item["host_calls"] = [{"capability": "storage", "operation": "read"}]
        evidence = compare_observations(values, POLICY, CORPUS)
        self.assertGreater(evidence["divergence_counts"]["P0"], 0)

    def test_forbidden_host_capability_is_rejected(self):
        values = observations()
        values[0]["host_calls"] = [{"capability": "network", "operation": "connect"}]
        with self.assertRaises(ConformanceError):
            compare_observations(values, POLICY, CORPUS)

    def test_resource_budget_is_p2_not_hidden(self):
        values = observations()
        for item in values:
            if item["lane"] == "rust-candidate" and item["case_id"] == CORPUS["cases"][0]["id"]:
                item["resources"]["memory_bytes"] = POLICY["resource_budgets"]["memory_bytes"] + 1
        evidence = compare_observations(values, POLICY, CORPUS)
        self.assertGreater(evidence["divergence_counts"]["P2"], 0)
        self.assertFalse(evidence["claims"]["resource_profile_candidate"])

    def test_evidence_is_deterministic(self):
        values = observations()
        first = compare_observations(copy.deepcopy(values), POLICY, CORPUS)
        second = compare_observations(copy.deepcopy(values), POLICY, CORPUS)
        self.assertEqual(canonical_bytes(first), canonical_bytes(second))

    def test_engine_selection_requires_independent_review_and_still_does_not_close_gate(self):
        evidence = compare_observations(observations(), POLICY, CORPUS)
        review = {
            "author_identity": "author",
            "self_approval": False,
            "adr_ref": "docs/adr/ADR-RUNTIME-ENGINE.md",
            "reviewers": [
                {"identity": "runtime-reviewer", "role": "runtime"},
                {"identity": "security-reviewer", "role": "security"},
            ],
        }
        candidate = evaluate_engine_selection(evidence, review, POLICY)
        self.assertTrue(candidate["claims"]["engine_selection_candidate"])
        self.assertFalse(candidate["claims"]["javascript_engine_selected"])
        self.assertFalse(candidate["claims"]["sg3_complete"])
        review["reviewers"][0]["identity"] = "author"
        with self.assertRaises(ConformanceError):
            evaluate_engine_selection(evidence, review, POLICY)


if __name__ == "__main__":
    unittest.main()
