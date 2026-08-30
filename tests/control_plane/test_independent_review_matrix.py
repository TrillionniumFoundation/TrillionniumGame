from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check-independent-review-matrix.py"
MATRIX = ROOT / "docs/review/INDEPENDENT_REVIEW_MATRIX.json"


class IndependentReviewMatrixTests(unittest.TestCase):
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
        self.assertEqual(result["status"], "passed")
        self.assertFalse(result["claim_boundary"]["matrix_presence_is_review"])
        self.assertTrue(result["claim_boundary"]["unassigned_domains_block_closure"])

    def test_current_matrix_is_explicitly_unassigned(self) -> None:
        matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
        self.assertFalse(matrix["summary"]["all_required_reviews_available"])
        self.assertEqual(matrix["summary"]["assigned_domain_count"], 0)
        for domain in matrix["domains"]:
            self.assertEqual(domain["status"], "unassigned")
            self.assertEqual(domain["assigned_reviewers"], [])


if __name__ == "__main__":
    unittest.main()
