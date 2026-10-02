from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "pgwire_backup_restore_contract", ROOT / "scripts/check-pgwire-backup-restore.py"
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("backup checker unavailable")
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class PgwireBackupRestoreContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = (ROOT / "scripts/ci-pgwire-backup-restore.sh").read_text()
        cls.images = json.loads((ROOT / "config/database-test-images.json").read_text())

    def test_current_profiles_use_complete_schema_chain(self):
        CHECKER.validate_text(self.script, self.images)

    def test_old_image_cannot_inherit_current_profile_credit(self):
        old = "cockroachdb/cockroach:v24.1.2@sha256:" + "1" * 64
        with self.assertRaisesRegex(SystemExit, "differs from current configuration"):
            CHECKER.validate_text(self.script + "\nimage='" + old + "'\n", self.images)
        with self.assertRaisesRegex(SystemExit, "read their image identity"):
            CHECKER.validate_text(self.script.replace("config/database-test-images.json", "old-images.json"), self.images)
        images = copy.deepcopy(self.images)
        images["profiles"]["cockroachdb"]["image"] = "cockroachdb/cockroach:latest"
        with self.assertRaisesRegex(SystemExit, "immutable OCI digest"):
            CHECKER.validate_text(self.script, images)

    def test_restored_schema_and_exact_known_time_fixture_are_required(self):
        for marker in ("apply-authoritative-schema.sh verify", "--mode verify",
                       "schema-identity-check.json", "migration-chain-validation.json",
                       "1969-12-31 23:59:59.999999+00", "2024-02-29 00:00:00.123456+00"):
            with self.subTest(marker=marker):
                with self.assertRaises(SystemExit):
                    CHECKER.validate_text(self.script.replace(marker, "removed"), self.images)

    def test_legacy_rows_and_metadata_cannot_use_positional_inserts(self):
        for statement in ("INSERT INTO trnm_storage_objects VALUES ('bad');",
                          "INSERT INTO trnm_schema_metadata VALUES (1);"):
            with self.subTest(statement=statement):
                with self.assertRaises(SystemExit):
                    CHECKER.validate_text(self.script + "\n" + statement + "\n", self.images)


if __name__ == "__main__":
    unittest.main()
