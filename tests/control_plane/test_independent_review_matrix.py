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
HEAD = "88c02a417c7b0c64f27dc9f49e0b5e6317150964"
TREE = "9d3ae2fadb2684f095855ecf16307376b55a440f"
OBSERVED = "2026-09-07T06:00:00Z"


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

    @staticmethod
    def reviewer(user_id: int, login: str) -> dict:
        return {
            "github_user_id": user_id,
            "login": login,
            "kind": "github-user",
            "organization": "TrillionniumFoundation",
            "effective_at": "2026-09-01T00:00:00Z",
            "expires_at": "2026-12-01T00:00:00Z",
            "permission_readback": {
                "repository": "TrillionniumFoundation/TrillionniumGame",
                "permission": "write",
                "observed_at": "2026-09-07T06:00:00Z",
            },
            "routing_basis": [
                "Synthetic independent qualification fixture.",
                "Test-only principal; no repository claim.",
            ],
        }

    @staticmethod
    def qualification(
        user_id: int,
        login: str,
        domain_roles: dict[str, list[str]],
        *,
        marker: str,
    ) -> tuple[dict, tuple]:
        evidence = {
            "evidence_id": f"TG-QUAL-{marker}",
            "artifact_sha256": marker[0] * 64,
            "candidate_head": HEAD,
            "candidate_tree": TREE,
            "observed_at": OBSERVED,
            "decision": "accepted",
            "independent": True,
            "self_review": False,
        }
        rows = [
            {"domain": domain, "roles": roles, "evidence": [dict(evidence)]}
            for domain, roles in domain_roles.items()
        ]
        records = tuple(
            sorted(
                (
                    domain,
                    role,
                    evidence["evidence_id"],
                    evidence["artifact_sha256"],
                    HEAD,
                    TREE,
                    OBSERVED,
                    "accepted",
                    True,
                    False,
                )
                for domain, roles in domain_roles.items()
                for role in roles
            )
        )
        return {
            "github_user_id": user_id,
            "login": login,
            "qualifications": rows,
        }, (login, records)

    @staticmethod
    def add_codeowners(owners: Path, *logins: str) -> None:
        suffix = "".join(f" @{login}" for login in logins)
        rows = []
        for raw in owners.read_text(encoding="utf-8").splitlines():
            rows.append(raw if not raw or raw.lstrip().startswith("#") else raw + suffix)
        owners.write_text("\n".join(rows) + "\n", encoding="utf-8")

    def install_principals(
        self,
        checker,
        matrix: Path,
        principals: list[tuple[int, str, dict[str, list[str]], str]],
    ) -> dict:
        value = json.loads(matrix.read_text(encoding="utf-8"))
        expected = {}
        for user_id, login, domain_roles, marker in principals:
            value["reviewers"].append(self.reviewer(user_id, login))
            row, expected_row = self.qualification(
                user_id, login, domain_roles, marker=marker
            )
            value["candidate_scope"]["qualification_principals"].append(row)
            expected[user_id] = expected_row
        checker.EXPECTED_QUALIFICATIONS = expected
        matrix.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        return value

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
        self.assertEqual(result["required_roles"], 12)
        self.assertEqual(result["covered_required_roles"], 0)
        self.assertEqual(result["eligible_reviewers"], [])
        self.assertEqual(result["qualification_principals"], [])
        self.assertEqual(result["candidate_conflict_user_ids"], sorted(PRINCIPALS))
        self.assertTrue(result["candidate_conflict_evidence_bound"])
        self.assertTrue(result["role_qualification_evidence_bound"])
        self.assertFalse(result["all_required_reviews_available"])

    def test_matrix_binds_all_three_conflicts_to_stable_ids(self) -> None:
        value = json.loads(MATRIX.read_text(encoding="utf-8"))
        reviewers = {
            row["github_user_id"]: row["login"] for row in value["reviewers"]
        }
        self.assertEqual(reviewers, PRINCIPALS)
        self.assertTrue(
            all(
                "routing_basis" in row and "qualification_basis" not in row
                for row in value["reviewers"]
            )
        )
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
        self.assertEqual(value["candidate_scope"]["qualification_principals"], [])
        for domain in value["domains"]:
            self.assertEqual(set(domain["assigned_reviewer_ids"]), set(PRINCIPALS))
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
                        item
                        for item in row["conflicts"]
                        if item["type"] != conflict
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
                assigned_reviewer_ids=value["domains"][0]["assigned_reviewer_ids"][:1]
            ),
            "at least 2 reviewers required",
        )

    def test_two_reviewers_cannot_delete_a_known_conflicted_route(self) -> None:
        self.invalid(
            lambda value: value["domains"][0].update(
                assigned_reviewer_ids=value["domains"][0]["assigned_reviewer_ids"][:2]
            ),
            "candidate-conflicted routing principals removed",
        )

    def test_active_status_cannot_hide_capacity_shortage(self) -> None:
        self.invalid(
            lambda value: value["domains"][0].update(status="active"),
            "insufficient qualified role coverage must block",
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

    def test_two_unqualified_principals_cannot_create_capacity(self) -> None:
        def change(value):
            for user_id, login in ((9001, "ExternalOne"), (9002, "ExternalTwo")):
                value["reviewers"].append(self.reviewer(user_id, login))
            for domain in value["domains"]:
                domain["assigned_reviewer_ids"].extend([9001, 9002])
                domain["status"] = "active"
            value["summary"].update(
                available_domain_count=6,
                blocked_domain_count=0,
                named_reviewer_count=5,
                eligible_named_reviewer_count=2,
                all_required_reviews_available=True,
            )
            value["claim_boundary"].update(
                reviewer_routing_available=True,
                candidate_specific_availability=True,
            )

        self.invalid(change, "insufficient qualified role coverage must block")

    def test_well_formed_unbound_qualification_is_rejected(self) -> None:
        def change(value):
            value["reviewers"].append(self.reviewer(9001, "ExternalOne"))
            row, _ = self.qualification(
                9001,
                "ExternalOne",
                {"governance": ["repository-administrator"]},
                marker="1",
            )
            value["candidate_scope"]["qualification_principals"].append(row)

        self.invalid(
            change,
            "qualification principal lacks exact accepted evidence binding",
        )

    def test_wrong_domain_only_qualifications_do_not_create_capacity(self) -> None:
        checker = module()
        with tempfile.TemporaryDirectory() as directory:
            matrix, gaps, owners = self.fixture(directory)
            self.install_principals(
                checker,
                matrix,
                [
                    (
                        9001,
                        "ExternalOne",
                        {"governance": ["repository-administrator"]},
                        "1",
                    ),
                    (
                        9002,
                        "ExternalTwo",
                        {"governance": ["independent-program-governance-reviewer"]},
                        "2",
                    ),
                ],
            )

            def change(value):
                target = next(
                    row
                    for row in value["domains"]
                    if row["id"] == "database-data-integrity"
                )
                target["assigned_reviewer_ids"].extend([9001, 9002])
                target["status"] = "active"

            self.mutate(matrix, change)
            with self.assertRaisesRegex(
                checker.ReviewMatrixError,
                "insufficient qualified role coverage must block",
            ):
                checker.validate(matrix, gaps, owners)

    def test_missing_required_role_keeps_domain_blocked(self) -> None:
        checker = module()
        with tempfile.TemporaryDirectory() as directory:
            matrix, gaps, owners = self.fixture(directory)
            self.install_principals(
                checker,
                matrix,
                [
                    (
                        9001,
                        "ExternalOne",
                        {"governance": ["repository-administrator"]},
                        "1",
                    ),
                    (
                        9002,
                        "ExternalTwo",
                        {"governance": ["repository-administrator"]},
                        "2",
                    ),
                ],
            )
            self.add_codeowners(owners, "ExternalOne", "ExternalTwo")

            def change(value):
                target = next(
                    row for row in value["domains"] if row["id"] == "governance"
                )
                target["assigned_reviewer_ids"].extend([9001, 9002])
                target["status"] = "active"

            self.mutate(matrix, change)
            with self.assertRaisesRegex(
                checker.ReviewMatrixError,
                "insufficient qualified role coverage must block",
            ):
                checker.validate(matrix, gaps, owners)

    def test_role_qualified_principals_must_be_codeowners(self) -> None:
        checker = module()
        with tempfile.TemporaryDirectory() as directory:
            matrix, gaps, owners = self.fixture(directory)
            all_roles = {
                row["id"]: list(row["required_roles"])
                for row in json.loads(matrix.read_text(encoding="utf-8"))["domains"]
            }
            self.install_principals(
                checker,
                matrix,
                [
                    (9001, "ExternalOne", all_roles, "1"),
                    (9002, "ExternalTwo", all_roles, "2"),
                ],
            )

            def change(value):
                for domain in value["domains"]:
                    domain["assigned_reviewer_ids"].extend([9001, 9002])
                    domain["status"] = "active"

            self.mutate(matrix, change)
            with self.assertRaisesRegex(
                checker.ReviewMatrixError,
                "is not a CODEOWNER route",
            ):
                checker.validate(matrix, gaps, owners)

    def test_genuinely_role_qualified_fixture_can_make_domains_available(self) -> None:
        checker = module()
        with tempfile.TemporaryDirectory() as directory:
            matrix, gaps, owners = self.fixture(directory)
            base = json.loads(matrix.read_text(encoding="utf-8"))
            all_roles = {
                row["id"]: list(row["required_roles"]) for row in base["domains"]
            }
            self.install_principals(
                checker,
                matrix,
                [
                    (9001, "ExternalOne", all_roles, "1"),
                    (9002, "ExternalTwo", all_roles, "2"),
                ],
            )
            self.add_codeowners(owners, "ExternalOne", "ExternalTwo")

            def change(value):
                for domain in value["domains"]:
                    domain["assigned_reviewer_ids"].extend([9001, 9002])
                    domain["status"] = "active"
                value["summary"].update(
                    available_domain_count=6,
                    blocked_domain_count=0,
                    required_role_count=12,
                    covered_required_role_count=12,
                    named_reviewer_count=5,
                    eligible_named_reviewer_count=2,
                    qualification_principal_count=2,
                    all_required_reviews_available=True,
                )
                value["claim_boundary"].update(
                    reviewer_routing_available=True,
                    candidate_specific_availability=True,
                    required_role_coverage_available=True,
                )

            self.mutate(matrix, change)
            result = checker.validate(matrix, gaps, owners)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["available_domains"], 6)
            self.assertEqual(result["covered_required_roles"], 12)
            self.assertEqual(
                result["eligible_reviewers"], ["ExternalOne", "ExternalTwo"]
            )

    def test_qualification_candidate_head_mismatch_is_rejected(self) -> None:
        checker = module()
        with tempfile.TemporaryDirectory() as directory:
            matrix, gaps, owners = self.fixture(directory)
            row, expected = self.qualification(
                9001,
                "ExternalOne",
                {"governance": ["repository-administrator"]},
                marker="1",
            )
            checker.EXPECTED_QUALIFICATIONS = {9001: expected}
            value = json.loads(matrix.read_text(encoding="utf-8"))
            value["reviewers"].append(self.reviewer(9001, "ExternalOne"))
            row["qualifications"][0]["evidence"][0]["candidate_head"] = "0" * 40
            value["candidate_scope"]["qualification_principals"].append(row)
            matrix.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(
                checker.ReviewMatrixError, "candidate head mismatch"
            ):
                checker.validate(matrix, gaps, owners)

    def test_qualification_self_review_is_rejected(self) -> None:
        checker = module()
        with tempfile.TemporaryDirectory() as directory:
            matrix, gaps, owners = self.fixture(directory)
            row, expected = self.qualification(
                9001,
                "ExternalOne",
                {"governance": ["repository-administrator"]},
                marker="1",
            )
            checker.EXPECTED_QUALIFICATIONS = {9001: expected}
            value = json.loads(matrix.read_text(encoding="utf-8"))
            value["reviewers"].append(self.reviewer(9001, "ExternalOne"))
            row["qualifications"][0]["evidence"][0]["self_review"] = True
            value["candidate_scope"]["qualification_principals"].append(row)
            matrix.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(
                checker.ReviewMatrixError, "self_review=false required"
            ):
                checker.validate(matrix, gaps, owners)


if __name__ == "__main__":
    unittest.main()
