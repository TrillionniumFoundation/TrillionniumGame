from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check-independent-review-matrix.py"
MATRIX = ROOT / "docs/review/INDEPENDENT_REVIEW_MATRIX.json"
GAPS = ROOT / "docs/status/GAP_REGISTER.json"
CODEOWNERS = ROOT / ".github/CODEOWNERS"


def load_module():
    spec = importlib.util.spec_from_file_location("check_independent_review_matrix", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class IndependentReviewMatrixTests(unittest.TestCase):
    def fixture(self, directory: str):
        root = Path(directory)
        matrix = root / "matrix.json"
        gaps = root / "gaps.json"
        codeowners = root / "CODEOWNERS"
        matrix.write_text(MATRIX.read_text(encoding="utf-8"), encoding="utf-8")
        gaps.write_text(GAPS.read_text(encoding="utf-8"), encoding="utf-8")
        codeowners.write_text(CODEOWNERS.read_text(encoding="utf-8"), encoding="utf-8")
        return matrix, gaps, codeowners

    def mutate_matrix(self, path: Path, callback) -> None:
        value = json.loads(path.read_text(encoding="utf-8"))
        callback(value)
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def test_matrix_validator_passes_but_does_not_claim_review(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "blocked-reviewer-capacity")
        self.assertEqual(result["assigned_domains"], 6)
        self.assertEqual(result["redundant_domains"], 6)
        self.assertEqual(
            result["named_reviewers"],
            ["Franksudoman", "ProfHepta", "Tomasrgbsf"],
        )
        self.assertFalse(result["all_required_reviews_available"])
        self.assertEqual(result["available_domains"], 0)
        self.assertEqual(result["blocked_domains"], 6)
        self.assertEqual(result["eligible_reviewers"], [])
        self.assertEqual(
            result["candidate_conflicts"],
            ["Franksudoman", "ProfHepta", "Tomasrgbsf"],
        )
        self.assertTrue(result["codeowners_redundant"])
        self.assertFalse(result["conflict_survivable"])
        self.assertFalse(result["branch_policy_enforced"])
        self.assertFalse(result["claim_boundary"]["matrix_presence_is_review"])
        self.assertFalse(result["claim_boundary"]["assignment_is_acceptance"])
        self.assertTrue(
            result["claim_boundary"]["administrative_enforcement_still_required"]
        )

    def test_current_matrix_fails_closed_when_every_named_reviewer_is_conflicted(self) -> None:
        matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
        minimum = matrix["policy"]["minimum_redundant_reviewers_per_domain"]
        self.assertEqual(minimum, 2)
        self.assertFalse(matrix["summary"]["all_required_reviews_available"])
        self.assertEqual(matrix["summary"]["assigned_domain_count"], 6)
        self.assertEqual(matrix["summary"]["redundant_domain_count"], 6)
        self.assertEqual(matrix["summary"]["available_domain_count"], 0)
        self.assertEqual(matrix["summary"]["blocked_domain_count"], 6)
        self.assertEqual(matrix["summary"]["eligible_named_reviewer_count"], 0)
        self.assertEqual(matrix["summary"]["named_reviewer_count"], 3)
        self.assertEqual(
            set(matrix["candidate_scope"]["conflict_identities"]),
            {"ProfHepta", "Franksudoman", "Tomasrgbsf"},
        )
        for domain in matrix["domains"]:
            self.assertEqual(domain["status"], "blocked-reviewer-capacity")
            self.assertEqual(len(domain["assigned_reviewers"]), 3)
            required = set(domain["required_roles"])
            identities = {
                reviewer["identity"] for reviewer in domain["assigned_reviewers"]
            }
            self.assertEqual(
                identities,
                {"ProfHepta", "Franksudoman", "Tomasrgbsf"},
            )
            for excluded in identities:
                survivors = [
                    reviewer
                    for reviewer in domain["assigned_reviewers"]
                    if reviewer["identity"] != excluded
                ]
                self.assertGreaterEqual(len(survivors), minimum)
                covered = set().union(*(set(row["roles"]) for row in survivors))
                self.assertEqual(covered, required)
            for reviewer in domain["assigned_reviewers"]:
                self.assertEqual(set(reviewer["roles"]), required)
                self.assertIn("candidate-author", reviewer["conflicts"])

    def test_single_reviewer_domain_is_rejected(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            matrix, gaps, codeowners = self.fixture(directory)
            self.mutate_matrix(
                matrix,
                lambda value: value["domains"][0].update(
                    assigned_reviewers=value["domains"][0]["assigned_reviewers"][:1]
                ),
            )
            with self.assertRaisesRegex(
                module.ReviewMatrixError, "at least 2 redundant reviewers"
            ):
                module.validate(matrix, gaps, codeowners)

    def test_two_reviewers_fail_conflict_survivability(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            matrix, gaps, codeowners = self.fixture(directory)

            def reduce_to_two(value):
                value["domains"][0]["assigned_reviewers"] = value["domains"][0][
                    "assigned_reviewers"
                ][:2]

            self.mutate_matrix(matrix, reduce_to_two)
            with self.assertRaisesRegex(
                module.ReviewMatrixError,
                "losing assigned reviewer .* leaves fewer than 2 routing assignments",
            ):
                module.validate(matrix, gaps, codeowners)

    def test_unknown_conflict_type_is_rejected(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            matrix, gaps, codeowners = self.fixture(directory)
            self.mutate_matrix(
                matrix,
                lambda value: value["domains"][0]["assigned_reviewers"][0].update(
                    conflicts=["candidate-author", "unregistered-conflict"]
                ),
            )
            with self.assertRaisesRegex(
                module.ReviewMatrixError, "invalid conflicts"
            ):
                module.validate(matrix, gaps, codeowners)

    def test_candidate_author_cannot_be_hidden_as_conflict_free(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            matrix, gaps, codeowners = self.fixture(directory)
            self.mutate_matrix(
                matrix,
                lambda value: value["domains"][0]["assigned_reviewers"][0].update(
                    conflicts=[]
                ),
            )
            with self.assertRaisesRegex(
                module.ReviewMatrixError, "must be explicitly conflicted"
            ):
                module.validate(matrix, gaps, codeowners)

    def test_insufficient_permission_is_rejected(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            matrix, gaps, codeowners = self.fixture(directory)
            self.mutate_matrix(
                matrix,
                lambda value: value["domains"][0]["assigned_reviewers"][0][
                    "permission_readback"
                ].update(permission="read"),
            )
            with self.assertRaisesRegex(
                module.ReviewMatrixError, "insufficient repository permission"
            ):
                module.validate(matrix, gaps, codeowners)

    def test_expired_assignment_is_rejected(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            matrix, gaps, codeowners = self.fixture(directory)
            self.mutate_matrix(
                matrix,
                lambda value: value["domains"][0]["assigned_reviewers"][0].update(
                    expires_at="2026-09-03T04:02:00Z"
                ),
            )
            with self.assertRaisesRegex(
                module.ReviewMatrixError, "inactive or expired"
            ):
                module.validate(matrix, gaps, codeowners)

    def test_candidate_conflict_rule_drift_is_rejected(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            matrix, gaps, codeowners = self.fixture(directory)
            self.mutate_matrix(
                matrix,
                lambda value: value["domains"][0]["assigned_reviewers"][0].update(
                    candidate_ineligibility=["candidate-author"]
                ),
            )
            with self.assertRaisesRegex(
                module.ReviewMatrixError, "candidate conflict rules drift"
            ):
                module.validate(matrix, gaps, codeowners)

    def test_false_available_summary_is_rejected(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            matrix, gaps, codeowners = self.fixture(directory)
            self.mutate_matrix(
                matrix,
                lambda value: value["summary"].update(
                    all_required_reviews_available=True
                ),
            )
            with self.assertRaisesRegex(
                module.ReviewMatrixError, "review availability summary mismatch"
            ):
                module.validate(matrix, gaps, codeowners)

    def test_codeowners_single_route_is_rejected(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            matrix, gaps, codeowners = self.fixture(directory)
            text = codeowners.read_text(encoding="utf-8").replace(
                "* @ProfHepta @Franksudoman @Tomasrgbsf",
                "* @ProfHepta",
                1,
            )
            codeowners.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(
                module.ReviewMatrixError, "at least two owners required"
            ):
                module.validate(matrix, gaps, codeowners)

    def test_critical_codeowner_without_third_route_is_rejected(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            matrix, gaps, codeowners = self.fixture(directory)
            text = codeowners.read_text(encoding="utf-8").replace(
                "* @ProfHepta @Franksudoman @Tomasrgbsf",
                "* @ProfHepta @Franksudoman",
                1,
            )
            codeowners.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(
                module.ReviewMatrixError, "lacks conflict-surviving review routes"
            ):
                module.validate(matrix, gaps, codeowners)

    def test_missing_critical_codeowner_pattern_is_rejected(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            matrix, gaps, codeowners = self.fixture(directory)
            text = codeowners.read_text(encoding="utf-8").replace(
                "/docs/review/ @ProfHepta @Franksudoman @Tomasrgbsf\n",
                "",
            )
            codeowners.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(
                module.ReviewMatrixError, "missing critical patterns"
            ):
                module.validate(matrix, gaps, codeowners)


if __name__ == "__main__":
    unittest.main()
