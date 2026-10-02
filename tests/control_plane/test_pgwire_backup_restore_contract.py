from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import tempfile
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

    def shell_function(self, name):
        function = re.search(rf"^{name}\(\) \{{\n.*?^\}}$", self.script, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(function, f"missing shell function: {name}")
        return function.group(0)

    def restored_url(self, source):
        return subprocess.run(
            ["bash", "-c", "set -euo pipefail\n" + self.shell_function("restored_database_url")
             + '\nrestored_database_url "$1"', "restore-url-test", source],
            text=True, capture_output=True, check=False,
        )

    def test_restore_url_changes_database_path_without_changing_postgres_username(self):
        source = "postgres://trnm:fixture-password@127.0.0.1:55434/trnm"
        result = self.restored_url(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "postgres://trnm:fixture-password@127.0.0.1:55434/trnm_restore")
        self.assertEqual(self.script.count('restored_url=$(restored_database_url "$database_url")'), 2)
        self.assertNotIn('${database_url/\\/trnm/\\/trnm_restore}', self.script)

    def test_restore_url_preserves_escaped_credentials_query_and_fragment(self):
        authority = "postgresql://trnm:p%40ss%2Ftrnm%3Aword@[::1]:55434"
        suffix = "?sslmode=disable&application_name=%2Ftrnm&options=-c%20search_path%3Dpublic#fixture"
        result = self.restored_url(authority + "/trnm" + suffix)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), authority + "/trnm_restore" + suffix)

    def test_restore_url_preserves_cockroach_root_and_options(self):
        result = self.restored_url("postgres://root@127.0.0.1:26257/trnm?sslmode=disable")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "postgres://root@127.0.0.1:26257/trnm_restore?sslmode=disable")

    def test_restore_url_rejects_unexpected_path_without_echoing_credentials(self):
        for source in ("postgres://trnm:private-fixture@localhost/another",
                       "postgres://trnm:private-fixture@[malformed/trnm"):
            with self.subTest(source=source.split(":", 1)[0]):
                result = self.restored_url(source)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr.strip(), "invalid backup source database URL")
                self.assertNotIn("private-fixture", result.stderr)

    def test_failed_stage_prints_bounded_redacted_log_and_preserves_exit_status(self):
        helpers = "\n".join(self.shell_function(name) for name in ("begin_stage", "report_failure", "cleanup"))
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "restored-schema-build.log"
            log.write_text("postgres://trnm:" + "partial-secret" * 7000 + "@localhost/trnm\n"
                           + "\n".join(f"old-line-{index}" for index in range(90)) + "\n"
                           + "schema_connection_failed postgres://trnm:private-fixture@localhost/trnm\n"
                           + "connection postgres://trnm:p'raw-db-fixture@localhost/trnm\n"
                           + "password='quoted fixture secret' PGPASSWORD=second-fixture\n")
            script = ("set -euo pipefail\n" + helpers + "\n"
                      + 'profile=postgresql; stage=initialize; diagnostic_logs=(); container=fixture; evidence="$1"\n'
                      + 'docker() { return 0; }\ntrap cleanup EXIT\n'
                      + 'begin_stage verify-restored-schema "$2"\nexit 37\n')
            result = subprocess.run(["bash", "-c", script, "failure-test", directory, str(log)],
                                    text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 37)
        self.assertIn("profile=postgresql stage=verify-restored-schema exit=37", result.stderr)
        self.assertIn("schema_connection_failed <redacted-database-url>", result.stderr)
        self.assertIn("password=<redacted> PGPASSWORD=<redacted>", result.stderr)
        for secret in ("private-fixture", "raw-db-fixture", "quoted fixture secret", "second-fixture", "partial-secret"):
            self.assertNotIn(secret, result.stderr)
        self.assertNotIn("old-line-0\n", result.stderr)
        self.assertLessEqual(len(result.stderr.splitlines()), 82)

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
