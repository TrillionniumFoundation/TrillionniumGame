from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check-independent-review-domain-contract.py"
MATRIX = ROOT / "docs/review/INDEPENDENT_REVIEW_MATRIX.json"


def module():
    spec = importlib.util.spec_from_file_location("review_domain_contract", SCRIPT)
    assert spec is not None and spec.loader is not None
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class IndependentReviewDomainContractTests(unittest.TestCase):
    def fixture(self, directory: str) -> Path:
        path = Path(directory) / "matrix.json"
        path.write_bytes(MATRIX.read_bytes())
        return path

    def invalid(self, mutation, pattern: str) -> None:
        checker = module()
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory)
            value = json.loads(path.read_text(encoding="utf-8"))
            mutation(value)
            path.write_text(json.dumps(value) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(checker.DomainContractError, pattern):
                checker.validate(path)

    def test_repository_contract_passes_and_cli_executes(self) -> None:
        result = module().validate(MATRIX)
        self.assertTrue(result["domain_contract_valid"])
        self.assertEqual(result["domain_count"], 6)
        self.assertEqual(result["required_role_count"], 12)
        completed = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(json.loads(completed.stdout)["domain_contract_valid"])

    def test_domain_cannot_be_removed(self) -> None:
        self.invalid(lambda value: value["domains"].pop(), "review domain set mismatch")

    def test_domain_cannot_be_renamed(self) -> None:
        self.invalid(
            lambda value: value["domains"][0].update(id="governance-lite"),
            "review domain set mismatch",
        )

    def test_required_role_cannot_shrink(self) -> None:
        self.invalid(
            lambda value: value["domains"][2]["required_roles"].pop(),
            "required_roles drift",
        )

    def test_required_role_cannot_be_exchanged(self) -> None:
        self.invalid(
            lambda value: value["domains"][2]["required_roles"].__setitem__(
                0, "independent-generic-reviewer"
            ),
            "required_roles drift",
        )

    def test_protected_path_cannot_shrink(self) -> None:
        self.invalid(
            lambda value: value["domains"][1]["protected_paths"].pop(),
            "protected_paths drift",
        )

    def test_protected_path_cannot_be_moved_between_domains(self) -> None:
        def mutate(value):
            source = value["domains"][1]["protected_paths"]
            moved = source.pop()
            value["domains"][2]["protected_paths"].append(moved)

        self.invalid(mutate, "protected_paths drift")

    def test_blocking_gap_cannot_shrink(self) -> None:
        self.invalid(
            lambda value: value["domains"][1]["blocking_gaps"].pop(),
            "blocking_gaps drift",
        )

    def test_blocking_gap_cannot_be_exchanged(self) -> None:
        self.invalid(
            lambda value: value["domains"][3]["blocking_gaps"].__setitem__(
                0, "GAP-P2-NONBLOCKING"
            ),
            "blocking_gaps drift",
        )

    def test_summary_cannot_understate_domains(self) -> None:
        self.invalid(
            lambda value: value["summary"].update(domain_count=5),
            "summary domain count drift",
        )

    def test_duplicate_json_key_is_rejected(self) -> None:
        checker = module()
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory)
            text = path.read_text(encoding="utf-8")
            text = text.replace(
                '"schema":"trillionnium.independent-review-matrix.v2"',
                '"schema":"trillionnium.independent-review-matrix.v2",'
                '"schema":"trillionnium.independent-review-matrix.v2"',
                1,
            )
            path.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(
                checker.DomainContractError, "duplicate JSON key"
            ):
                checker.validate(path)


if __name__ == "__main__":
    unittest.main()
