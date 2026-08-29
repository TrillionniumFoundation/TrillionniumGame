from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check-gap-register.py"


def load_module():
    spec = importlib.util.spec_from_file_location("check_gap_register", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GapRegisterContractTests(unittest.TestCase):
    def test_repository_gap_register_is_consistent(self) -> None:
        result = load_module().validate()
        self.assertEqual(result["schema"], "trillionnium.gap-register-validation.v1")
        self.assertGreater(result["gap_count"], 0)
        self.assertIn("closed", result)
        self.assertIn("external_admin_blocked", result)

    def test_p0_p1_closed_rows_require_indexed_evidence(self) -> None:
        module = load_module()
        register = module.load_object(module.REGISTER)
        index = module.load_object(module.EVIDENCE_INDEX)
        known = module.indexed_evidence_ids(index)
        for row in register["gaps"]:
            if row["status"] == "closed" and row["severity"] in {"P0", "P1"}:
                self.assertTrue(row["evidence_ids"], row["id"])
                self.assertTrue(set(row["evidence_ids"]) <= known, row["id"])

    def test_external_admin_rows_name_the_dependency(self) -> None:
        module = load_module()
        register = module.load_object(module.REGISTER)
        blocked = [row for row in register["gaps"] if row["status"] == "blocked-external-admin"]
        self.assertTrue(blocked)
        for row in blocked:
            self.assertIsInstance(row["external_dependency"], str)
            self.assertTrue(row["external_dependency"].strip(), row["id"])


if __name__ == "__main__":
    unittest.main()
