from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/check-postgresql-semantic-recovery.py"

def load_checker():
    spec = importlib.util.spec_from_file_location("postgresql_semantic_recovery_contract", CHECKER)
    if spec is None or spec.loader is None:
        raise RuntimeError("checker loader unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class PostgreSqlSemanticRecoveryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checker = load_checker()
        cls.data = cls.checker.DATA.read_text(encoding="utf-8")
        cls.catalog = cls.checker.CATALOG.read_text(encoding="utf-8")
        cls.harness = cls.checker.HARNESS.read_text(encoding="utf-8")

    def test_real_contract_passes(self):
        self.checker.validate_texts(self.data, self.catalog, self.harness)

    def test_missing_table_rejected(self):
        with self.assertRaisesRegex(self.checker.ValidationError, "semantic snapshot missing"):
            self.checker.validate_texts(
                self.data.replace("trnm_storage_objects", "removed_storage_table"),
                self.catalog,
                self.harness,
            )

    def test_missing_restore_or_failure_suppression_rejected(self):
        with self.assertRaisesRegex(self.checker.ValidationError, "pg_restore"):
            self.checker.validate_texts(
                self.data,
                self.catalog,
                self.harness.replace("pg_restore", "removed_restore", 1),
            )
        with self.assertRaisesRegex(self.checker.ValidationError, "failure suppression"):
            self.checker.validate_texts(
                self.data,
                self.catalog,
                self.harness + "\nfalse || true\n",
            )

    def test_command_line_checker_passes(self):
        result = subprocess.run(
            [sys.executable, str(CHECKER)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("semantic recovery source contract: OK", result.stdout)

if __name__ == "__main__":
    unittest.main()
