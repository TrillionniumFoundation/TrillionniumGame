from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/check-postgresql-connection-faults.py"

def load_checker():
    spec = importlib.util.spec_from_file_location("postgresql_connection_fault_contract", CHECKER)
    if spec is None or spec.loader is None:
        raise RuntimeError("checker loader unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class PostgreSqlConnectionFaultContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checker = load_checker()
        cls.harness = cls.checker.HARNESS.read_text(encoding="utf-8")

    def test_real_contract_passes(self):
        self.checker.validate_text(self.harness)

    def test_cancel_or_exhaustion_removal_rejected(self):
        for marker in ("pg_cancel_backend", "pool-exhaustion"):
            with self.subTest(marker=marker):
                with self.assertRaisesRegex(self.checker.ValidationError, "missing"):
                    self.checker.validate_text(self.harness.replace(marker, "removed"))

    def test_command_line_checker_passes(self):
        result = subprocess.run(
            [sys.executable, str(CHECKER)], cwd=ROOT, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("connection-fault source contract: OK", result.stdout)

if __name__ == "__main__":
    unittest.main()
