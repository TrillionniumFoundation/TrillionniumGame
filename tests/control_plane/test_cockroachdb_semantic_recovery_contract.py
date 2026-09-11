from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/check-cockroachdb-semantic-recovery.py"

def load_checker():
    spec = importlib.util.spec_from_file_location("cockroachdb_semantic_recovery_contract", CHECKER)
    if spec is None or spec.loader is None:
        raise RuntimeError("checker loader unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class CockroachDbSemanticRecoveryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checker = load_checker()
        cls.data = cls.checker.DATA.read_text(encoding="utf-8")
        cls.harness = cls.checker.HARNESS.read_text(encoding="utf-8")

    def test_real_contract_passes(self):
        self.checker.validate_texts(self.data, self.harness)

    def test_postgresql_inheritance_rejected(self):
        with self.assertRaisesRegex(self.checker.ValidationError, "PostgreSQL profile inherited"):
            self.checker.validate_texts(
                self.data,
                self.harness + "\nmigrations/postgresql\n",
            )

    def test_missing_native_restore_rejected(self):
        with self.assertRaisesRegex(self.checker.ValidationError, "RESTORE DATABASE"):
            self.checker.validate_texts(
                self.data,
                self.harness.replace("RESTORE DATABASE", "removed restore", 1),
            )

    def test_command_line_checker_passes(self):
        result = subprocess.run(
            [sys.executable, str(CHECKER)], cwd=ROOT, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("semantic recovery source contract: OK", result.stdout)

if __name__ == "__main__":
    unittest.main()
