from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("sql_error_evidence", ROOT / "scripts/check-sql-error.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SqlErrorEvidenceTests(unittest.TestCase):
    def test_postgresql_and_cockroach_constraint_rejections(self) -> None:
        for text in [
            'ERROR: 23514: new row violates check constraint "collection_check"',
            'ERROR: failed to satisfy CHECK constraint (collection != \'\')\nSQLSTATE: 23514\nCONSTRAINT: collection_check\nFailed running "sql"',
        ]:
            MODULE.verify_error(text, "23514", "collection_check")

    def test_unrelated_failure_or_identifier_mention_is_rejected(self) -> None:
        for text in [
            'ERROR: permission denied\nSQLSTATE: 42501',
            'ERROR: relation does not exist\nSQLSTATE: 42P01',
            'ERROR: syntax error in collection_check\nSQLSTATE: 42601',
            'ERROR: wrong constraint\nSQLSTATE: 23514',
            'ERROR: violates check constraint "collection_check_other"\nSQLSTATE: 23514',
            'ERROR: violates check constraint "other_check"\nDETAIL: collection_check\nSQLSTATE: 23514',
            'ERROR: violates check constraint "collection_check"',
            'ERROR: violates check constraint "collection_check"\nSQLSTATE: 23514\nSQLSTATE: 42501',
            'ERROR: failed to satisfy CHECK constraint (collection != \'\')\nSQLSTATE: 23514\nCONSTRAINT: collection_check_other',
            'ERROR: failed to satisfy CHECK constraint (collection != \'\')\nSQLSTATE: 23514\nDETAIL: CONSTRAINT: collection_check',
        ]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                MODULE.verify_error(text, "23514", "collection_check")

    def test_foreign_key_and_readonly_codes_are_distinct(self) -> None:
        MODULE.verify_error('ERROR: 23503: violates foreign key constraint "event_fk"', "23503", "event_fk")
        MODULE.verify_error('ERROR: 25006: cannot execute INSERT in a read-only transaction', "25006")
        with self.assertRaises(ValueError):
            MODULE.verify_error('ERROR: 23514: violates check constraint "event_fk"', "23503", "event_fk")


if __name__ == "__main__":
    unittest.main()
