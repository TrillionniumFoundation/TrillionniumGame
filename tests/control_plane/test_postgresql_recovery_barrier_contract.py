from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/check-postgresql-recovery-barrier.py"

def load_checker():
    spec = importlib.util.spec_from_file_location("postgresql_recovery_barrier_contract", CHECKER)
    if spec is None or spec.loader is None:
        raise RuntimeError("checker loader unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class PostgreSqlRecoveryBarrierContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checker = load_checker()
        cls.sql = cls.checker.SQL.read_text(encoding="utf-8")
        cls.harness = cls.checker.HARNESS.read_text(encoding="utf-8")

    def test_real_contract_passes(self):
        self.checker.validate_texts(self.sql, self.harness)

    def test_missing_lease_rejection_rejected(self):
        with self.assertRaisesRegex(self.checker.ValidationError, "active outbox lease"):
            self.checker.validate_texts(
                self.sql.replace("active outbox lease blocks recovery quarantine", "removed", 1),
                self.harness,
            )

    def test_deletion_and_failure_suppression_rejected(self):
        with self.assertRaisesRegex(self.checker.ValidationError, "pending outbox deletion"):
            self.checker.validate_texts(self.sql, self.harness + "\nDELETE FROM trnm_outbox;\n")
        with self.assertRaisesRegex(self.checker.ValidationError, "failure suppression"):
            self.checker.validate_texts(self.sql, self.harness + "\nfalse || true\n")

    def test_command_line_checker_passes(self):
        result = subprocess.run(
            [sys.executable, str(CHECKER)], cwd=ROOT, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("recovery barrier source contract: OK", result.stdout)

    def test_readonly_error_and_full_chain_identity_are_required(self):
        for old, new in (("--sqlstate 25006", "--sqlstate 42501"),
                         ("apply-authoritative-schema.sh", "removed-runner"),
                         ("schema-identity-check.json", "removed-identity-check")):
            with self.subTest(old=old):
                with self.assertRaises(self.checker.ValidationError):
                    self.checker.validate_texts(self.sql, self.harness.replace(old, new))
        with self.assertRaises(self.checker.ValidationError):
            self.checker.validate_texts(self.sql, self.harness + "\nINSERT INTO trnm_schema_metadata VALUES (1);\n")

if __name__ == "__main__":
    unittest.main()
