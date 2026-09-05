"""Hostile regression contract for the immutable Plan-v3 gap denominator."""
from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "scripts/gap_register_scope_policy.py"
CHECKER = ROOT / "scripts/check-gap-register.py"
AGGREGATE = ROOT / ".github/workflows/trillionnium-game-merge-gate.yml"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "trnm_gap_register_scope_policy_tests", POLICY_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("gap register scope policy unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


POLICY = load_module()


class GapRegisterScopeLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.current, _ = POLICY.load_object(
            ROOT / POLICY.CURRENT_RELATIVE, "current gap register"
        )
        cls.baseline, cls.baseline_payload = POLICY.load_object(
            ROOT / POLICY.BASELINE_RELATIVE, "gap scope baseline"
        )

    def validate(self, current=None, *, payload=None):
        return POLICY.validate_document(
            self.current if current is None else current,
            self.baseline,
            baseline_payload=self.baseline_payload if payload is None else payload,
        )

    def rejected(self, mutation, pattern=None) -> None:
        current = copy.deepcopy(self.current)
        mutation(current)
        context = (
            self.assertRaisesRegex(POLICY.ScopeError, pattern)
            if pattern
            else self.assertRaises(POLICY.ScopeError)
        )
        with context:
            self.validate(current)

    def row(self, document, gap_id):
        return next(row for row in document["gaps"] if row["id"] == gap_id)

    def test_repository_gap_scope_is_exact_and_non_shrinkable(self):
        result = POLICY.validate_files(ROOT)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["gap_count"], 18)
        self.assertEqual(result["close_criteria_total"], 92)
        self.assertFalse(result["claim_boundary"]["gap_closed"])
        self.assertEqual(
            POLICY.git_blob_sha1(self.baseline_payload),
            POLICY.BASELINE_GIT_BLOB_SHA1,
        )

    def test_mutable_status_and_evidence_references_do_not_repin_scope(self):
        current = copy.deepcopy(self.current)
        current["gaps"][0]["status"] = "ready"
        current["gaps"][0]["evidence_ids"] = ["TG-EV-FUTURE-001"]
        result = self.validate(current)
        self.assertEqual(
            result["projection_sha256"],
            POLICY.projection_sha256(self.current),
        )

    def test_exact_external_dependency_may_clear_only_after_closure(self):
        current = copy.deepcopy(self.current)
        gap = self.row(current, "GAP-P0-GOV-001")
        gap["status"] = "closed"
        gap["external_dependency"] = None
        self.validate(current)

        self.rejected(
            lambda value: self.row(value, "GAP-P0-GOV-001").update(
                status="open", external_dependency=None
            ),
            "external dependency",
        )
        self.rejected(
            lambda value: self.row(value, "GAP-P0-GOV-001").update(
                external_dependency="weaker replacement text"
            ),
            "dependency changed",
        )
        self.rejected(
            lambda value: self.row(value, "GAP-P0-CI-001").update(
                external_dependency="invented external blocker"
            ),
            "internal gap",
        )

    def test_gap_removal_addition_reordering_and_substitution_are_rejected(self):
        self.rejected(lambda value: value["gaps"].pop(), "semantic scope")
        self.rejected(
            lambda value: value["gaps"].append(copy.deepcopy(value["gaps"][0])),
            "semantic scope",
        )
        self.rejected(lambda value: value["gaps"].reverse(), "semantic scope")
        self.rejected(
            lambda value: value["gaps"][0].update(id="GAP-P0-FAKE-001"),
            "semantic scope",
        )

    def test_equal_cardinality_semantic_rewrites_are_rejected(self):
        mutations = (
            lambda value: value["gaps"][0].update(severity="P1"),
            lambda value: value["gaps"][0].update(category="other"),
            lambda value: value["gaps"][0].update(title="weaker title"),
            lambda value: value["gaps"][0].update(owner_role="nobody"),
            lambda value: value["gaps"][0]["blocking_claims"].pop(),
            lambda value: value["gaps"][0]["affected_paths"].pop(),
            lambda value: value["gaps"][0]["close_criteria"].__setitem__(
                0, "replacement criterion"
            ),
            lambda value: value["gaps"][0]["required_evidence_types"].__setitem__(
                0, "performance"
            ),
            lambda value: value["gaps"][0]["issue_refs"].clear(),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.rejected(mutation, "semantic scope")

    def test_source_candidate_metadata_and_root_policy_are_immutable(self):
        self.rejected(
            lambda value: self.row(value, "GAP-P0-PLAN-001")[
                "admission_source_candidate"
            ].update(accepted=True),
            "semantic scope",
        )
        self.rejected(
            lambda value: value["closure_policy"].update(
                independent_review_required_for_p0_p1=False
            ),
            "semantic scope",
        )
        self.rejected(
            lambda value: value["status_values"].remove("independently-reviewed"),
            "semantic scope",
        )
        self.rejected(
            lambda value: value.update(plan_version=4),
            "unexpected plan",
        )

    def test_duplicate_or_noncanonical_mutable_references_are_rejected(self):
        self.rejected(
            lambda value: value["gaps"][0].update(
                evidence_ids=["TG-EV-X", "TG-EV-X"]
            ),
            "evidence IDs",
        )
        self.rejected(
            lambda value: value["gaps"][0].update(evidence_ids=[" bad "]),
            "evidence IDs",
        )
        self.rejected(
            lambda value: value["gaps"][0].update(status="accepted"),
            "invalid mutable status",
        )

    def test_baseline_byte_identity_is_not_editable_with_the_register(self):
        changed = self.baseline_payload.replace(
            b'"generated_at":"2026-09-02"',
            b'"generated_at":"2026-09-03"',
            1,
        )
        self.assertNotEqual(changed, self.baseline_payload)
        with self.assertRaisesRegex(POLICY.ScopeError, "Git blob identity"):
            self.validate(payload=changed)

    def test_duplicate_json_keys_and_nonfinite_numbers_fail_closed(self):
        for raw in (
            b'{"schema":"a","schema":"b"}',
            b'{"value":NaN}',
            b'{"value":Infinity}',
        ):
            with self.subTest(raw=raw), self.assertRaises(POLICY.ScopeError):
                POLICY.parse_object(raw, "fixture")

    def test_production_checker_executes_shared_scope_policy(self):
        source = CHECKER.read_text(encoding="utf-8")
        self.assertIn("gap_register_scope_policy.py", source)
        self.assertIn("SCOPE.validate_files", source)
        result = subprocess.run(
            [sys.executable, str(CHECKER)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=40,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["immutable_scope"]["status"], "verified")
        self.assertEqual(payload["immutable_scope"]["gap_count"], 18)

    def test_policy_cli_is_discovered_and_passes_current_repository(self):
        result = subprocess.run(
            [sys.executable, str(POLICY_PATH)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "verified")

    def test_required_aggregate_discovers_complete_python_and_gap_checker(self):
        source = AGGREGATE.read_text(encoding="utf-8")
        self.assertIn("unittest discover", source)
        self.assertIn("test_*.py", source)
        self.assertIn("check-gap-register.py", source)


if __name__ == "__main__":
    unittest.main()
