from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/derive-gap-status.py"
REGISTER = ROOT / "docs/status/GAP_REGISTER.json"


class GapDerivationTests(unittest.TestCase):
    def test_derivation_passes_and_never_promotes_source_to_closed(self) -> None:
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
        self.assertEqual(result["schema"], "trillionnium.gap-derivation.v1")
        self.assertFalse(result["claim_boundary"]["source_candidate_closes_gap"])
        self.assertFalse(result["claim_boundary"]["local_execution_closes_gap"])
        for gap in result["gaps"]:
            if gap["suggested_status"] == "source-candidate":
                self.assertFalse(gap["closed"])

    def test_closed_p0_p1_gaps_cannot_be_evidence_empty(self) -> None:
        register = json.loads(REGISTER.read_text(encoding="utf-8"))
        for gap in register["gaps"]:
            if gap["status"] == "closed" and gap["severity"] in {"P0", "P1"}:
                self.assertTrue(gap["evidence_ids"], gap["id"])
                self.assertFalse(gap.get("external_dependency"), gap["id"])

    def test_module_derivation_matches_register_cardinality(self) -> None:
        spec = importlib.util.spec_from_file_location("derive_gap_status", SCRIPT)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.derive()
        register = json.loads(REGISTER.read_text(encoding="utf-8"))
        self.assertEqual(result["gaps_total"], len(register["gaps"]))
        self.assertEqual(len({gap["id"] for gap in result["gaps"]}), result["gaps_total"])


if __name__ == "__main__":
    unittest.main()
