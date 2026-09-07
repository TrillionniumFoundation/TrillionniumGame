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
CONFLICTS = {
    "candidate-author",
    "evidence-producer",
    "administrator-mutator-under-review",
}
PRINCIPALS = {
    102159240: "ProfHepta",
    273670192: "Franksudoman",
    273673612: "Tomasrgbsf",
}


def module():
    spec = importlib.util.spec_from_file_location("review_matrix", SCRIPT)
    assert spec is not None and spec.loader is not None
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class IndependentReviewMatrixTests(unittest.TestCase):
    def fixture(self, directory: str):
        root = Path(directory)
        paths = root / "matrix.json", root / "gaps.json", root / "CODEOWNERS"
        for source, target in zip((MATRIX, GAPS, CODEOWNERS), paths):
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        return paths

    def mutate(self, path: Path, callback) -> None:
        value = json.loads(path.read_text(encoding="utf-8"))
        callback(value)
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def invalid(self, callback, pattern: str) -> None:
        checker = module()
        with tempfile.TemporaryDirectory() as directory:
            matrix, gaps, owners = self.fixture(directory)
            self.mutate(matrix, callback)
            with self.assertRaisesRegex(checker.ReviewMatrixError, pattern):
                checker.validate(matrix, gaps, owners)

    def test_current_matrix_reports_capacity_block(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "blocked-reviewer-capacity")
        self.assertEqual(result["available_domains"], 0)
        self.assertEqual(result["blocked_domains"], 6)
        self.assertEqual(result["eligible_reviewers"], [])
        self.assertEqual(result["candidate_conflict_user_ids"], sorted(PRINCIPALS))
        self.assertTrue(result["candidate_conflict_evidence_bound"])
        self.assertFalse(result["all_required_reviews_available"])
        self.assertFalse(result["conflict_survivable"])

    def test_matrix_binds_all_three_conflicts_to_stable_ids(self) -> None:
        value = json.loads(MATRIX.read_text(encoding="utf-8"))
        reviewers = {
            row["github_user_id"]: row["login"] for row in value["reviewers"]
        }
        self.assertEqual(reviewers, PRINCIPALS)
        principals = {
            row["github_user_id"]: row
            for row in value["candidate_scope"]["conflict_principals"]
        }
        self.assertEqual(
            {item: row["login"] for item, row in principals.items()},
            PRINCIPALS,
        )
        for row in principals.values():
            self.assertEqual(
                {item["type"] for item in row["conflicts"]},
                CONFLICTS,
            )
            self.assertTrue(all(item["evidence"] for item in row["conflicts"]))
        for domain in value["domains"]:
            self.assertEqual(
                set(domain["assigned_reviewer_ids"]), set(PRINCIPALS)
            )
            self.assertEqual(domain["status"], "blocked-reviewer-capacity")

    def test_login_case_variation_is_same_principal(self) -> None:
        checker = module()
        with tempfile.TemporaryDirectory() as directory:
            matrix, gaps, owners = self.fixture(directory)

            def change(value):
                for reviewer in value["reviewers"]:
                    if reviewer["github_user_id"] == 102159240:
                        reviewer["login"] = "pROFhEPTA"
                for principal in value["candidate_scope"]["conflict_principals"]:
                    if principal["github_user_id"] == 102159240:
                        principal["login"] = "PROFHEPTA"

            self.mutate(matrix, change)
            result = checker.validate(matrix, gaps, owners)
            self.assertFalse(result["all_required_reviews_available"])

    def test_known_login_with_wrong_id_is_rejected(self) -> None:
        self.invalid(
            lambda value: value["reviewers"][0].update(github_user_id=999999999),
            "known login with wrong GitHub user id",
        )

    def test_stable_id_with_unrelated_login_is_rejected(self) -> None:
        self.invalid(
            lambda value: value["reviewers"][0].update(login="UnrelatedReviewer"),
            "stable GitHub user id/login binding mismatch",
        )

    def test_duplicate_case_alias_is_rejected(self) -> None:
        self.invalid(
            lambda value: value["reviewers"][1].update(
                login=value["reviewers"][0]["login"].swapcase()
            ),
            "duplicate case-insensitive login",
        )

    def test_duplicate_stable_id_is_rejected(self) -> None:
        self.invalid(
            lambda value: value["reviewers"][1].update(
                github_user_id=value["reviewers"][0]["github_user_id"]
            ),
            "duplicate stable GitHub user id",
        )

    def test_each_conflict_type_is_mandatory(self) -> None:
        for conflict in sorted(CONFLICTS):
            with self.subTest(conflict=conflict):

                def change(value, conflict=conflict):
                    row = value["candidate_scope"]["conflict_principals"][0]
                    row["conflicts"] = [
                        item for item in row["conflicts"] if item["type"] != conflict
                    ]

                self.invalid(change, "candidate principal conflicts incomplete")

    def test_conflict_evidence_exact_commit_is_mandatory(self) -> None:
        self.invalid(
            lambda value: value["candidate_scope"]["conflict_principals"][0][
                "conflicts"
            ][0]["evidence"][0].update(commit="0" * 40),
            "principal conflict binding mismatch",
        )

    def test_conflict_evidence_exact_scope_is_mandatory(self) -> None:
        self.invalid(
            lambda value: value["candidate_scope"]["conflict_principals"][0][
                "conflicts"
            ][0]["evidence"][0].update(scope="other/**"),
            "principal conflict binding mismatch",
        )

    def test_candidate_principal_cannot_be_removed(self) -> None:
        self.invalid(
            lambda value: value["candidate_scope"]["conflict_principals"].pop(),
            "candidate conflict principals removed",
        )

    def test_well_formed_but_unrelated_artifact_is_rejected(self) -> None:
        self.invalid(
            lambda value: value["candidate_scope"]["evidence"].update(
                artifact_id=999999,
                artifact_sha256="0" * 64,
            ),
            "candidate conflict evidence exact binding mismatch",
        )

    def test_well_formed_but_different_candidate_head_is_rejected(self) -> None:
        self.invalid(
            lambda value: value["candidate_scope"].update(
                authorship_observed_through_head="0" * 40
            ),
            "candidate authorship_observed_through_head exact binding mismatch",
        )

    def test_duplicate_json_key_is_rejected(self) -> None:
        checker = module()
        with tempfile.TemporaryDirectory() as directory:
            matrix, gaps, owners = self.fixture(directory)
            text = matrix.read_text(encoding="utf-8").replace(
                '"schema":"trillionnium.independent-review-matrix.v2"',
                '"schema":"trillionnium.independent-review-matrix.v2",'
                '"schema":"trillionnium.independent-review-matrix.v2"',
                1,
            )
            matrix.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(
                checker.ReviewMatrixError, "duplicate JSON key"
            ):
                checker.validate(matrix, gaps, owners)

    def test_non_finite_json_is_rejected(self) -> None:
        checker = module()
        with tempfile.TemporaryDirectory() as directory:
            matrix, gaps, owners = self.fixture(directory)
            text = matrix.read_text(encoding="utf-8").replace(
                '"candidate_commit_count":249',
                '"candidate_commit_count":NaN',
                1,
            )
            matrix.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(
                checker.ReviewMatrixError, "non-finite JSON value"
            ):
                checker.validate(matrix, gaps, owners)

    def test_unknown_conflict_is_rejected(self) -> None:
        self.invalid(
            lambda value: value["candidate_scope"]["conflict_principals"][0][
                "conflicts"
            ][0].update(type="unknown"),
            "unknown conflict type",
        )

    def test_single_reviewer_domain_is_rejected(self) -> None:
        self.invalid(
            lambda value: value["domains"][0].update(
                assigned_reviewer_ids=value["domains"][0][
                    "assigned_reviewer_ids"
                ][:1]
            ),
            "at least 2 reviewers required",
        )

    def test_two_reviewers_cannot_delete_a_known_conflicted_route(self) -> None:
        self.invalid(
            lambda value: value["domains"][0].update(
                assigned_reviewer_ids=value["domains"][0][
                    "assigned_reviewer_ids"
                ][:2]
            ),
            "candidate-conflicted routing principals removed",
        )

    def test_active_status_cannot_hide_capacity_shortage(self) -> None:
        self.invalid(
            lambda value: value["domains"][0].update(status="active"),
            "insufficient conflict-free reviewers must block",
        )

    def test_false_summary_is_rejected(self) -> None:
        self.invalid(
            lambda value: value["summary"].update(
                all_required_reviews_available=True
            ),
            "review summary mismatch",
        )

    def test_insufficient_permission_is_rejected(self) -> None:
        self.invalid(
            lambda value: value["reviewers"][0]["permission_readback"].update(
                permission="read"
            ),
            "insufficient repository permission",
        )

    def test_expired_assignment_is_rejected(self) -> None:
        self.invalid(
            lambda value: value["reviewers"][0].update(
                expires_at="2026-09-03T04:02:00Z"
            ),
            "inactive or expired",
        )

    def test_codeowners_login_case_is_normalized(self) -> None:
        checker = module()
        with tempfile.TemporaryDirectory() as directory:
            matrix, gaps, owners = self.fixture(directory)
            text = owners.read_text(encoding="utf-8")
            for old, new in (
                ("@ProfHepta", "@profhepta"),
                ("@Franksudoman", "@FRANKSUDOMAN"),
                ("@Tomasrgbsf", "@tOMASRGBSF"),
            ):
                text = text.replace(old, new)
            owners.write_text(text, encoding="utf-8")
            self.assertTrue(
                checker.validate(matrix, gaps, owners)["codeowners_redundant"]
            )

    def test_codeowners_single_route_is_rejected(self) -> None:
        checker = module()
        with tempfile.TemporaryDirectory() as directory:
            matrix, gaps, owners = self.fixture(directory)
            text = owners.read_text(encoding="utf-8").replace(
                "* @ProfHepta @Franksudoman @Tomasrgbsf",
                "* @ProfHepta",
                1,
            )
            owners.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(
                checker.ReviewMatrixError, "at least two owners required"
            ):
                checker.validate(matrix, gaps, owners)

    def test_codeowners_missing_third_route_is_rejected(self) -> None:
        checker = module()
        with tempfile.TemporaryDirectory() as directory:
            matrix, gaps, owners = self.fixture(directory)
            text = owners.read_text(encoding="utf-8").replace(
                "* @ProfHepta @Franksudoman @Tomasrgbsf",
                "* @ProfHepta @Franksudoman",
                1,
            )
            owners.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(
                checker.ReviewMatrixError,
                "lacks conflict-surviving review routes",
            ):
                checker.validate(matrix, gaps, owners)


if __name__ == "__main__":
    unittest.main()
