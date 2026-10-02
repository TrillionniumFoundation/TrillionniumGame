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

    def test_constraint_probe_must_target_expected_error(self):
        mutations = (
            self.harness.replace("event-foreign-key 23503", "event-foreign-key 23514"),
            self.harness.replace('storage-collection 23514 "trnm_storage_objects_collection_check"',
                                 'storage-collection 23514 "trnm_outbox_check"'),
            self.harness.replace('--constraint "$expected_constraint"', ""),
            self.harness.replace("--sqlstate 42P07", "--sqlstate 23514"),
        )
        for harness in mutations:
            with self.subTest(harness=harness[-80:]):
                with self.assertRaises(self.checker.ValidationError):
                    self.checker.validate_texts(self.data, self.catalog, harness)

    def test_full_chain_identity_noop_and_known_timestamp_restore_are_required(self):
        for marker in ("apply-authoritative-schema.sh", "schema-identity-check.json",
                       "migration-chain-validation.json", "1969-12-31 23:59:59.999999+00"):
            with self.subTest(marker=marker):
                with self.assertRaises(self.checker.ValidationError):
                    self.checker.validate_texts(self.data, self.catalog, self.harness.replace(marker, "removed"))
        for name in ("create_time", "update_time", "chain_digest", "storage_writer_epoch"):
            with self.subTest(name=name):
                with self.assertRaises(self.checker.ValidationError):
                    self.checker.validate_texts(self.data.replace(name, "removed"), self.catalog, self.harness)
        with self.assertRaises(self.checker.ValidationError):
            self.checker.validate_texts(self.data, self.catalog, self.harness.replace("['applied_steps']==0", "['applied_steps']==1"))
        with self.assertRaises(self.checker.ValidationError):
            self.checker.validate_texts(self.data, self.catalog, self.harness + "\nINSERT INTO trnm_storage_objects VALUES ('bad');\n")

if __name__ == "__main__":
    unittest.main()
