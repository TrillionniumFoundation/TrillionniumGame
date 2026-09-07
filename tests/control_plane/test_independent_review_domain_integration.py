from __future__ import annotations

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
PLAN_CHECKER = ROOT / "scripts/check-plan.py"


class IndependentReviewDomainIntegrationTests(unittest.TestCase):
    def run_cli(self, matrix_mutation=None, codeowners_mutation=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            matrix = root / "matrix.json"
            gaps = root / "gaps.json"
            owners = root / "CODEOWNERS"
            matrix.write_bytes(MATRIX.read_bytes())
            gaps.write_bytes(GAPS.read_bytes())
            owners.write_bytes(CODEOWNERS.read_bytes())
            if matrix_mutation is not None:
                value = json.loads(matrix.read_text(encoding="utf-8"))
                matrix_mutation(value)
                matrix.write_text(json.dumps(value) + "\n", encoding="utf-8")
            if codeowners_mutation is not None:
                text = owners.read_text(encoding="utf-8")
                owners.write_text(codeowners_mutation(text), encoding="utf-8")
            return subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--matrix",
                    str(matrix),
                    "--gaps",
                    str(gaps),
                    "--codeowners",
                    str(owners),
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

    def assert_cli_rejects(self, matrix_mutation, pattern: str) -> None:
        completed = self.run_cli(matrix_mutation=matrix_mutation)
        self.assertNotEqual(completed.returncode, 0, completed.stdout)
        self.assertIn(pattern, completed.stderr)

    def test_canonical_cli_executes_shared_contract(self) -> None:
        completed = self.run_cli()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertTrue(result["domain_contract_bound"])
        self.assertTrue(result["codeowners_last_match_bound"])
        self.assertEqual(result["status"], "blocked-reviewer-capacity")

    def test_canonical_cli_rejects_domain_removal(self) -> None:
        self.assert_cli_rejects(
            lambda value: value["domains"].pop(),
            "review domain set mismatch",
        )

    def test_canonical_cli_rejects_role_shrink(self) -> None:
        self.assert_cli_rejects(
            lambda value: value["domains"][2]["required_roles"].pop(),
            "required_roles drift",
        )

    def test_canonical_cli_rejects_path_shrink(self) -> None:
        self.assert_cli_rejects(
            lambda value: value["domains"][1]["protected_paths"].pop(),
            "protected_paths drift",
        )

    def test_canonical_cli_rejects_gap_shrink(self) -> None:
        self.assert_cli_rejects(
            lambda value: value["domains"][1]["blocking_gaps"].pop(),
            "blocking_gaps drift",
        )

    def test_canonical_cli_rejects_path_moved_between_domains(self) -> None:
        def mutation(value):
            moved = value["domains"][1]["protected_paths"].pop()
            value["domains"][2]["protected_paths"].append(moved)

        self.assert_cli_rejects(mutation, "protected_paths drift")

    def test_uncovered_path_fails_effective_mapping(self) -> None:
        def mutation(text: str) -> str:
            return "\n".join(
                line
                for line in text.splitlines()
                if not line.startswith(
                    "/docs/development/SCHEMA_AUTHORITY.json "
                )
            ) + "\n"

        completed = self.run_cli(codeowners_mutation=mutation)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("effective CODEOWNERS pattern drift", completed.stderr)

    def test_later_override_fails_last_match_binding(self) -> None:
        def mutation(text: str) -> str:
            return (
                text.rstrip()
                + "\n/docs/development/** @ProfHepta @Franksudoman @Tomasrgbsf\n"
            )

        completed = self.run_cli(codeowners_mutation=mutation)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("effective CODEOWNERS pattern drift", completed.stderr)

    def test_plan_checker_requires_and_executes_canonical_entrypoint(self) -> None:
        source = PLAN_CHECKER.read_text(encoding="utf-8")
        self.assertIn(
            '"scripts/check-independent-review-domain-contract.py"',
            source,
        )
        self.assertIn(
            'run_child("scripts/check-independent-review-matrix.py")',
            source,
        )
        completed = subprocess.run(
            [sys.executable, str(PLAN_CHECKER)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
