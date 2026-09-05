from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check-gap-register.py"
SPEC = importlib.util.spec_from_file_location("trillionnium_check_gap_register", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
CHECKER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CHECKER
SPEC.loader.exec_module(CHECKER)


class GapRegisterContractTests(unittest.TestCase):
    def review(self, **overrides: object) -> dict[str, object]:
        value: dict[str, object] = {
            "decision": "accepted",
            "reviewer_identity": "independent-reviewer",
            "independent": True,
            "self_review": False,
        }
        value.update(overrides)
        return {"independent_review": value}

    def test_only_consistent_independent_non_self_review_is_accepted(self) -> None:
        self.assertIsNotNone(CHECKER.accepted_review(self.review()))
        for row in [
            self.review(self_review=True),
            self.review(independent=False),
            self.review(independent=False, self_review=True),
            self.review(reviewer_identity=""),
            self.review(reviewer_identity=7),
            self.review(decision="needs-work"),
        ]:
            self.assertIsNone(CHECKER.accepted_review(row))

    def test_required_evidence_types_reject_malformed_values(self) -> None:
        malformed = [
            [None],
            [7],
            [""],
            [" unit"],
            ["unit "],
            ["unknown"],
            ["unit", None],
            ["unit", ""],
            ["unit", "unit"],
        ]
        for values in malformed:
            with self.subTest(values=values):
                with self.assertRaises(CHECKER.ValidationError):
                    CHECKER.validate_required_evidence_types("GAP-TEST", values)

    def test_required_evidence_types_accept_complete_canonical_list(self) -> None:
        self.assertEqual(
            CHECKER.validate_required_evidence_types(
                "GAP-TEST",
                ["unit", "wire-differential", "fault-injection"],
            ),
            ["unit", "wire-differential", "fault-injection"],
        )

    def test_closed_gap_requires_every_declared_evidence_type(self) -> None:
        evidence = {
            "TG-EV-UNIT": {
                "evidence_type": "unit",
                "status": "accepted",
                "schema_valid": True,
                "target_identity_verified_by_current_repo": True,
            }
        }
        with self.assertRaisesRegex(
            CHECKER.ValidationError,
            "missing required evidence types",
        ):
            CHECKER.validate_closed_evidence(
                "GAP-TEST",
                "P2",
                ["unit", "fault-injection"],
                ["TG-EV-UNIT"],
                evidence,
            )


if __name__ == "__main__":
    unittest.main()
