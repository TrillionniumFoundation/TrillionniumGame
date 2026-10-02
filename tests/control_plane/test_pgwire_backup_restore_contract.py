from __future__ import annotations

import copy
import csv
import io
from contextlib import contextmanager
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import textwrap
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
        cls.workflow = (ROOT / ".github/workflows/database-backup-restore.yml").read_text()

    def shell_function(self, name):
        start = re.search(rf"^{name}\(\) \{{\n", self.script, re.MULTILINE)
        self.assertIsNotNone(start, f"missing shell function: {name}")
        lines, delimiter = [], None
        for line in self.script[start.start():].splitlines():
            lines.append(line)
            if delimiter is not None:
                if line == delimiter:
                    delimiter = None
                continue
            here_document = re.search(r"<<'([A-Z0-9_]+)'", line)
            if here_document:
                delimiter = here_document[1]
            elif line == "}":
                return "\n".join(lines)
        self.fail(f"unterminated shell function: {name}")

    def restored_url(self, source):
        return subprocess.run(
            ["bash", "-c", "set -euo pipefail\n" + self.shell_function("restored_database_url")
             + '\nrestored_database_url "$1"', "restore-url-test", source],
            text=True, capture_output=True, check=False,
        )

    def workflow_python(self, label):
        source = re.search(rf"python3 - <<'{label}'\n(.*?)^\s*{label}$",
                           self.workflow, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(source, f"missing workflow Python block: {label}")
        return textwrap.dedent(source.group(1))

    def snapshot_rows(self, identity, collection="restore"):
        # Literal synthetic native output tests the reader, never substitutes for live SQL.
        request = b' { "b": 2, "a": 1.00 } '
        native = '{"a": 1.00, "b": 2}'
        known = {"collection": collection, "object_key": "fixture", "user_id": "a5" * 16,
                 "value_bytes": request.hex(), "version_digest": hashlib.sha256(request).hexdigest(),
                 "value_jsonb_text": native, "public_version": hashlib.md5(request).hexdigest(),
                 "value_projection_digest": hashlib.sha256(native.encode()).hexdigest(),
                 "value_origin": "write-request-bytes", "source_manifest_digest": None,
                 "request_native_text": native, "read_permission": 2, "write_permission": 1,
                 "updated_at_ms": 10, "create_epoch": None, "update_epoch": None,
                 "create_time_text": None, "update_time_text": None}
        timestamped = {**known, "object_key": "known-time", "create_epoch": -0.000001,
                       "update_epoch": 1709164800.123456,
                       "create_time_text": "1969-12-31 23:59:59.999999+00",
                       "update_time_text": "2024-02-29 00:00:00.123456+00"}
        unknown = {**known, "object_key": "unknown-empty", "value_bytes": None, "version_digest": None,
                   "value_jsonb_text": "null", "public_version": "", "request_native_text": None,
                   "value_projection_digest": hashlib.sha256(b"null").hexdigest(),
                   "value_origin": "nakama-export-unknown-request",
                   "source_manifest_digest": hashlib.sha256(b"synthetic storage fixture manifest").hexdigest()}
        array = {**unknown, "object_key": "unknown-unicode", "value_jsonb_text": "[3, true, null]",
                 "public_version": "版本A*", "value_projection_digest": hashlib.sha256(b"[3, true, null]").hexdigest()}
        rows = {table: [{"synthetic": True}] for table in CHECKER.EXPECTED_TABLES}
        rows["trnm_schema_metadata"] = [{key: identity[key] for key in
            ("profile", "schema_version", "storage_writer_epoch", "chain_digest", "digest_algorithm",
             "source_commit", "upgrade_source_commit", "v2_apply_source_commit")}]
        rows["trnm_storage_objects"] = [known, timestamped, unknown, array]
        return rows

    def snapshot_bytes(self, rows, profile):
        lines = [table + "|" + json.dumps(row, ensure_ascii=False, separators=(",", ":"))
                 for table in sorted(rows) for row in rows[table]]
        if profile == "postgresql":
            return ("\n".join(lines) + "\n").encode()
        stream = io.StringIO()
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(["snapshot_row"])
        writer.writerows([[line] for line in lines])
        return stream.getvalue().encode()

    def snapshot_identity(self, profile):
        return {"profile": profile, "schema_version": 3, "storage_writer_epoch": 3,
                "chain_digest": "d" * 64, "digest_algorithm": "ordered-path-git-blob-sha256.v1",
                "source_commit": "a" * 40, "upgrade_source_commit": "b" * 40,
                "v2_apply_source_commit": "c" * 40}

    @contextmanager
    def evidence_fixture(self, profile="postgresql"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            root.mkdir()
            for relative in ("scripts/check-migration-lock.py", "scripts/check-authoritative-schema-identity.py",
                             "scripts/check-pgwire-backup-restore.py", "scripts/check-schema-authority.py",
                             "scripts/upload-actions-artifact.py", "scripts/seal-outbox-final-attempt.py",
                             "scripts/verify-actions-log-artifact.py", "scripts/emit-actions-log-artifact.py",
                             "docs/development/SCHEMA_AUTHORITY.json"):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, target)
            shutil.copytree(ROOT / "migrations", root / "migrations")
            for command in (["git", "init", "-q"], ["git", "add", "."],
                            ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                             "commit", "-qm", "synthetic backup source fixture"]):
                subprocess.run(command, cwd=root, check=True, capture_output=True)
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=root, text=True).strip()
            spec = importlib.util.spec_from_file_location("backup_fixture_schemas", root / "scripts/check-authoritative-schema-identity.py")
            schemas = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(schemas)
            chains, version, tables = schemas.validated_source()
            retained = root / "run/backup-restore" / profile
            retained.mkdir(parents=True)
            shutil.copyfile(root / "migrations/MIGRATION_CHAIN.lock.json", retained / "migration-lock.json")
            validation = subprocess.check_output(["python3", "scripts/check-migration-lock.py"], cwd=root)
            (retained / "migration-chain-validation.json").write_bytes(validation)
            fresh = {"schema": "trillionnium.authoritative-schema-report.v1", "profile": profile,
                     "schema_version": version, "storage_writer_epoch": 3,
                     "chain_digest": chains[profile]["chain_sha256"],
                     "digest_algorithm": "ordered-path-git-blob-sha256.v1", "table_count": tables,
                     "source_commit": commit, "upgrade_source_commit": commit, "v2_apply_source_commit": commit,
                     "compatibility_credit": False, "migration_applied": True, "applied_steps": 3}
            (retained / "schema-identity.json").write_text(json.dumps(fresh))
            restored = {**fresh, "migration_applied": False, "applied_steps": 0}
            (retained / "restored-schema-identity.json").write_text(json.dumps(restored))
            check = {"schema": "trillionnium.authoritative-schema-identity-check.v1", "profile": profile,
                     "schema_version": version, "chain_digest": fresh["chain_digest"],
                     "identity_verified": True, "compatibility_credit": False}
            for name in ("schema-identity-check.json", "restored-schema-identity-check.json"):
                (retained / name).write_text(json.dumps(check))
            (retained / "summary.json").write_text(json.dumps({"schema": "trillionnium.backup-restore.v1",
                "profile": profile, "backup_created": True, "empty_restore": True,
                "semantic_snapshot_equal": True, "production_pitr": False, "multi_node_restore": False,
                "schema_version": 3, "storage_writer_epoch": 3, "authoritative_migration_file_count": 3,
                "storage_v3_fixture_count": 4}))
            for name in ("source.csv", "restored.csv"):
                (retained / name).write_text("synthetic semantic snapshot\n")
            for name in ("source-storage-v3.txt", "restored-storage-v3.txt"):
                (retained / name).write_bytes(self.snapshot_bytes(self.snapshot_rows(fresh), profile))
            (retained / "container.log").write_text("synthetic frozen database log\n")
            (retained / "restore.log").write_text("")
            env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "CANDIDATE_REPOSITORY": "Fixture/Backup",
                   "CANDIDATE_SHA": commit, "GITHUB_REPOSITORY": "Fixture/Backup",
                   "GITHUB_RUN_ID": "12345", "GITHUB_RUN_ATTEMPT": "2", "GITHUB_JOB": "restore",
                   "GITHUB_WORKFLOW_REF": "Fixture/Backup/.github/workflows/database-backup-restore.yml@refs/heads/fixture",
                   "GITHUB_WORKFLOW_SHA": commit, "PROFILE": profile}
            yield root, retained, commit, tree, env

    def seal_fixture(self, fixture):
        root, retained, commit, tree, env = fixture
        source = ("set -euo pipefail\n" + self.shell_function("seal_backup_evidence") + "\n"
                  + 'root="$1"; evidence="$2"; profile="$3"; candidate_head="$4"; candidate_tree="$5"\n'
                  + "seal_backup_evidence\n")
        return subprocess.run(["bash", "-c", source, "backup-seal-fixture", str(root), str(retained),
                               env["PROFILE"], commit, tree], cwd=root, env=env,
                              text=True, capture_output=True, check=False)

    def archive_fixture(self, fixture, extra_member=None):
        root, retained, _, _, env = fixture
        target = root / f"run/backup-restore-{env['PROFILE']}.tar.gz"
        with tarfile.open(target, "w:gz", format=tarfile.GNU_FORMAT) as archive:
            archive.add(retained, arcname=".")
            if extra_member is not None:
                archive.addfile(extra_member)
        return target

    def check_archive_fixture(self, fixture):
        root, _, _, _, env = fixture
        return subprocess.run(["python3", "-c", self.workflow_python("PY_BACKUP_ARCHIVE")],
                              cwd=root, env=env, text=True, capture_output=True, check=False)

    def rewrite_manifest(self, retained):
        paths = sorted(path for path in retained.rglob("*") if path.is_file() and path.name != "SHA256SUMS")
        (retained / "SHA256SUMS").write_text("".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  ./{path.relative_to(retained).as_posix()}\n"
            for path in paths))

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

    def test_native_v3_snapshot_preserves_opaque_history_and_independent_publishers(self):
        for profile in ("postgresql", "cockroachdb"):
            identity = self.snapshot_identity(profile)
            data = self.snapshot_bytes(self.snapshot_rows(identity), profile)
            with self.subTest(profile=profile):
                summary = CHECKER.validate_storage_snapshot_bytes(data, profile, identity, "restore")
                self.assertEqual(summary["storage_fixture_count"], 4)
                self.assertEqual(summary["unknown_request_witness_count"], 2)
                self.assertEqual(summary["null_timestamp_fixture_count"], 3)
                self.assertFalse(summary["accepted_evidence"])
                self.assertFalse(summary["compatibility_credit"])

    def test_native_v3_snapshot_rejects_hash_rederivation_and_missing_witness_bits(self):
        for profile in ("postgresql", "cockroachdb"):
            identity = self.snapshot_identity(profile)
            for fault in ("projection", "public-from-render", "request-native", "unknown-request",
                          "manifest-zero", "known-null-request", "missing-column", "invented-time",
                          "lost-microseconds", "lost-native-time", "lost-empty-token", "prior-v2", "stale-epoch"):
                rows = self.snapshot_rows(identity)
                storage = rows["trnm_storage_objects"]
                if fault == "projection":
                    storage[0]["value_projection_digest"] = "0" * 64
                elif fault == "public-from-render":
                    storage[0]["public_version"] = hashlib.md5(storage[0]["value_jsonb_text"].encode()).hexdigest()
                elif fault == "request-native":
                    storage[0]["request_native_text"] = "null"
                elif fault == "unknown-request":
                    storage[2]["value_bytes"] = "01"
                elif fault == "manifest-zero":
                    storage[2]["source_manifest_digest"] = "0" * 64
                elif fault == "known-null-request":
                    storage[0]["value_bytes"] = None
                elif fault == "missing-column":
                    del storage[2]["public_version"]
                elif fault == "invented-time":
                    storage[2]["create_epoch"] = 10
                elif fault == "lost-microseconds":
                    storage[1]["update_epoch"] = 1709164800
                    storage[1]["update_time_text"] = "2024-02-29 00:00:00+00"
                elif fault == "lost-native-time":
                    storage[1]["update_time_text"] = "2024-02-29 00:00:00+00"
                elif fault == "lost-empty-token":
                    storage[2]["public_version"] = "opaque"
                elif fault == "prior-v2":
                    rows["trnm_schema_metadata"][0]["v2_apply_source_commit"] = "f" * 40
                else:
                    rows["trnm_schema_metadata"][0]["storage_writer_epoch"] = 2
                with self.subTest(profile=profile, fault=fault), self.assertRaises(ValueError):
                    CHECKER.validate_storage_snapshot_bytes(self.snapshot_bytes(rows, profile), profile, identity, "restore")

    def test_native_v3_snapshot_accepts_large_native_history_without_request_limit_or_renderer(self):
        for profile in ("postgresql", "cockroachdb"):
            identity = self.snapshot_identity(profile)
            rows = self.snapshot_rows(identity)
            native = '["' + "x" * (1024 * 1024 + 1) + '"]'
            rows["trnm_storage_objects"][3]["value_jsonb_text"] = native
            rows["trnm_storage_objects"][3]["value_projection_digest"] = hashlib.sha256(native.encode()).hexdigest()
            with self.subTest(profile=profile):
                summary = CHECKER.validate_storage_snapshot_bytes(self.snapshot_bytes(rows, profile), profile, identity, "restore")
                self.assertTrue(summary["native_projection_verified"])
                self.assertFalse(summary["compatibility_credit"])

    def test_cockroach_float_epoch_cannot_replace_native_microsecond_timestamp_witness(self):
        identity = self.snapshot_identity("cockroachdb")
        rows = self.snapshot_rows(identity)
        rows["trnm_storage_objects"][1]["create_epoch"] = -0.0000010000000000287557
        summary = CHECKER.validate_storage_snapshot_bytes(self.snapshot_bytes(rows, "cockroachdb"), "cockroachdb", identity, "restore")
        self.assertEqual(summary["known_microsecond_fixture_count"], 1)
        rows["trnm_storage_objects"][1]["create_time_text"] = "1970-01-01 00:00:00+00"
        with self.assertRaisesRegex(ValueError, "native timestamp precision"):
            CHECKER.validate_storage_snapshot_bytes(self.snapshot_bytes(rows, "cockroachdb"), "cockroachdb", identity, "restore")

    def test_native_v3_snapshot_validates_resource_and_structure_bounds_without_secret_echo(self):
        identity = self.snapshot_identity("postgresql")
        rows = self.snapshot_rows(identity)
        private = "PRIVATE_NATIVE_PAYLOAD_FIXTURE"
        native = '["' + private + "x" * (16 * 1024 * 1024) + '"]'
        rows["trnm_storage_objects"][3]["value_jsonb_text"] = native
        with self.assertRaisesRegex(ValueError, "unbounded") as raised:
            CHECKER.validate_storage_snapshot_bytes(self.snapshot_bytes(rows, "postgresql"), "postgresql", identity, "restore")
        self.assertNotIn(private, str(raised.exception))
        with self.assertRaisesRegex(ValueError, "unbounded"):
            CHECKER.validate_storage_snapshot_bytes(b"x" * (32 * 1024 * 1024 + 1), "postgresql", identity, "restore")
        data = self.snapshot_bytes(self.snapshot_rows(identity), "postgresql")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            CHECKER.validate_storage_snapshot_bytes(data.replace(b'"public_version":', b'"public_version":"injected","public_version":', 1), "postgresql", identity, "restore")
        with self.assertRaises(ValueError):
            CHECKER.validate_storage_snapshot_bytes(data, "unknown-profile", identity, "restore")
        with self.assertRaisesRegex(ValueError, "header"):
            CHECKER.validate_storage_snapshot_bytes(data, "cockroachdb", self.snapshot_identity("cockroachdb"), "restore")

    def test_native_v3_packet_guards_require_new_columns_actual_snapshot_and_complete_chain(self):
        for marker in ("validate_storage_snapshot_bytes", "source-storage-v3.txt", "value_jsonb",
                       "public_version", "value_projection_digest", "source_manifest_digest",
                       "unknown-empty", "unknown-unicode", "len(ordered) == 3"):
            with self.subTest(marker=marker), self.assertRaises(SystemExit):
                CHECKER.validate_text(self.script.replace(marker, "removed"), self.images)
        for marker in ("validate_storage_snapshot_bytes", "source-storage-v3.txt", "v2_apply_source_commit",
                       "storage_v3_fixture_count", "ordered_files']) == 3"):
            self.assertIn(marker, self.workflow_python("PY_BACKUP_ARCHIVE"))

    def test_semantic_manifest_builder_retains_actual_three_sql_bytes_and_restored_metadata(self):
        for profile in ("postgresql", "cockroachdb"):
            harness = (ROOT / f"scripts/ci-{profile}-semantic-recovery.sh").read_text()
            matches = re.findall(r"<<'PY'\n(.*?)^PY$", harness, re.MULTILINE | re.DOTALL)
            self.assertEqual(len(matches), 1)
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as directory:
                evidence = Path(directory)
                identity = self.snapshot_identity(profile)
                identity["upgrade_source_commit"] = identity["v2_apply_source_commit"] = identity["source_commit"]
                for name in ("schema-identity.json", "repeat-schema-identity.json", "restored-schema-identity.json"):
                    (evidence / name).write_text(json.dumps(identity))
                for name in ("source-data.txt", "restored-data.txt"):
                    (evidence / name).write_bytes(self.snapshot_bytes(self.snapshot_rows(identity, "recovery"), profile))
                for name in ("source-catalog.txt", "restored-catalog.txt", "image-id.txt"):
                    (evidence / name).write_text("synthetic control fixture\n")
                (evidence / "migration-chain-validation.json").write_text("{}")
                shutil.copyfile(ROOT / "migrations/MIGRATION_CHAIN.lock.json", evidence / "migration-lock.json")
                if profile == "postgresql":
                    (evidence / "source.dump").write_bytes(b"synthetic backup fixture")
                else:
                    (evidence / "backup-files").mkdir()
                    (evidence / "backup-files/fixture.bin").write_bytes(b"synthetic backup fixture")
                result = subprocess.run(["python3", "-c", matches[0], str(ROOT), str(evidence), "synthetic-image"],
                                        text=True, capture_output=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                manifest = json.loads((evidence / "manifest.json").read_text())
                self.assertEqual(manifest["negative_constraint_probe_count"], 10)
                self.assertEqual(manifest["storage_v3_constraint_probe_count"], 9)
                self.assertEqual(manifest["authoritative_migration_file_count"], 3)
                self.assertEqual(manifest["restored_schema_identity"]["v2_apply_source_commit"], identity["v2_apply_source_commit"])
                for entry in manifest["authoritative_migrations"]:
                    content = (ROOT / entry["path"]).read_bytes()
                    self.assertEqual((evidence / entry["path"]).read_bytes(), content)
                    self.assertEqual(entry["size_bytes"], len(content))
                    self.assertEqual(entry["sha256"], hashlib.sha256(content).hexdigest())
                self.assertFalse(manifest["claim_boundary"]["accepted_evidence"])
                self.assertFalse(manifest["claim_boundary"]["production_ready"])

    def test_profile_packets_bind_provenance_and_retain_complete_relative_inventory(self):
        self.assertEqual(self.script.count('--mode fresh --source-commit "$candidate_head"'), 2)
        self.assertEqual(self.script.count('TRNM_SCHEMA_SOURCE_COMMIT="$candidate_head"'), 2)
        for profile in ("postgresql", "cockroachdb"):
            with self.subTest(profile=profile), self.evidence_fixture(profile) as fixture:
                root, retained, commit, tree, env = fixture
                result = self.seal_fixture(fixture)
                self.assertEqual(result.returncode, 0, result.stderr)
                identity = json.loads((retained / "identity.json").read_text())
                self.assertEqual((identity["commit"], identity["tree"]), (commit, tree))
                self.assertEqual((identity["run_id"], identity["run_attempt"], identity["job"]), ("12345", "2", "restore"))
                self.assertEqual(identity["source_commit"], commit)
                self.assertEqual(identity["upgrade_source_commit"], commit)
                self.assertEqual(identity["v2_apply_source_commit"], commit)
                self.assertEqual(identity["schema_version"], 3)
                self.assertFalse(identity["accepted_evidence"])
                sql = sorted(path.relative_to(retained).as_posix() for path in (retained / "migrations").rglob("*.sql"))
                self.assertEqual(sql, [f"migrations/{profile}/0001_foundation_up.sql", f"migrations/{profile}/0002_storage_timestamps_up.sql",
                                       f"migrations/{profile}/0003_storage_jsonb_up.sql"])
                manifest = (retained / "SHA256SUMS").read_text()
                self.assertNotIn(str(root), manifest)
                self.assertIn("  ./container.log\n", manifest)
                self.assertIn("  ./restore.log\n", manifest)  # An empty successful PG restore log is valid.
                archived = self.archive_fixture(fixture)
                checked = self.check_archive_fixture(fixture)
                self.assertEqual(checked.returncode, 0, checked.stderr)
                self.assertTrue(json.loads(checked.stdout)["archive_verified"])
                env.update(ARTIFACT_ID="56789", RETAINED_SHA256=hashlib.sha256(archived.read_bytes()).hexdigest(),
                           RETAINED_SIZE_BYTES=str(archived.stat().st_size))
                receipt = subprocess.run(["python3", "-c", self.workflow_python("PY_BACKUP_RECEIPT")],
                                         cwd=root, env=env, text=True, capture_output=True)
                self.assertEqual(receipt.returncode, 0, receipt.stderr)
                document = json.loads(receipt.stdout)
                self.assertEqual(document["artifact_id"], "56789")
                self.assertEqual(document["sha256"], env["RETAINED_SHA256"])
                self.assertEqual(document["size_bytes"], archived.stat().st_size)
                self.assertFalse(document["compatibility_credit"])
                self.assertTrue((root / f"run/backup-restore-{profile}-retention-receipt.json").is_file())

    def test_sealing_rejects_wrong_source_provenance_run_and_migration_identity(self):
        for fault in ("fresh-provenance", "restored-provenance", "prior-v2-provenance", "run-attempt", "candidate-head", "migration-source"):
            with self.subTest(fault=fault), self.evidence_fixture() as fixture:
                root, retained, _, _, env = fixture
                if fault in ("fresh-provenance", "restored-provenance", "prior-v2-provenance"):
                    path = retained / ("schema-identity.json" if fault == "fresh-provenance" else "restored-schema-identity.json")
                    document = json.loads(path.read_text())
                    document["v2_apply_source_commit" if fault == "prior-v2-provenance" else "upgrade_source_commit"] = "f" * 40
                    path.write_text(json.dumps(document))
                elif fault == "run-attempt":
                    env["GITHUB_RUN_ATTEMPT"] = "0"
                elif fault == "candidate-head":
                    env["CANDIDATE_SHA"] = "f" * 40
                else:
                    path = root / "migrations/postgresql/0002_storage_timestamps_up.sql"
                    path.write_bytes(path.read_bytes() + b"\n-- identity drift\n")
                result = self.seal_fixture(fixture)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("syntax error", result.stderr)
                expected = {"fresh-provenance": "fresh foundation/v2/current apply provenance mismatch",
                            "restored-provenance": "restored backup schema provenance differs",
                            "prior-v2-provenance": "restored backup schema provenance differs",
                            "run-attempt": "backup run context is missing or invalid",
                            "candidate-head": "backup candidate commit mismatch",
                            "migration-source": "blob identity drift"}[fault]
                self.assertIn(expected, result.stderr)
                self.assertFalse((retained / "SHA256SUMS").exists())

    def test_archive_rejects_missing_checksum_bad_payload_and_forged_producer_or_sql(self):
        for fault in ("missing-checksum", "bad-payload", "forged-producer", "forged-sql",
                      "forged-native", "forged-prior-v2", "missing-v3-sql", "traversal", "symlink"):
            with self.subTest(fault=fault), self.evidence_fixture() as fixture:
                _, retained, _, _, _ = fixture
                sealed = self.seal_fixture(fixture)
                self.assertEqual(sealed.returncode, 0, sealed.stderr)
                extra = None
                if fault == "missing-checksum":
                    (retained / "unlisted.log").write_text("unlisted payload\n")
                elif fault == "bad-payload":
                    (retained / "source.csv").write_text("changed payload\n")
                elif fault == "forged-producer":
                    path = retained / "identity.json"
                    document = json.loads(path.read_text())
                    document["run_attempt"] = "1"
                    path.write_text(json.dumps(document))
                    self.rewrite_manifest(retained)
                elif fault == "forged-sql":
                    path = retained / "migrations/postgresql/0002_storage_timestamps_up.sql"
                    path.write_bytes(path.read_bytes() + b"\n-- forged SQL\n")
                    self.rewrite_manifest(retained)
                elif fault == "forged-native":
                    path = retained / "source-storage-v3.txt"
                    data = path.read_bytes().replace(b'"public_version":', b'"public_version":"forged","unused":', 1)
                    path.write_bytes(data)
                    (retained / "restored-storage-v3.txt").write_bytes(data)
                    self.rewrite_manifest(retained)
                elif fault == "forged-prior-v2":
                    path = retained / "restored-schema-identity.json"
                    document = json.loads(path.read_text())
                    document["v2_apply_source_commit"] = "f" * 40
                    path.write_text(json.dumps(document))
                    self.rewrite_manifest(retained)
                elif fault == "missing-v3-sql":
                    (retained / "migrations/postgresql/0003_storage_jsonb_up.sql").unlink()
                    self.rewrite_manifest(retained)
                else:
                    extra = tarfile.TarInfo("../escape" if fault == "traversal" else "link")
                    if fault == "symlink":
                        extra.type, extra.linkname = tarfile.SYMTYPE, "source.csv"
                self.archive_fixture(fixture, extra)
                checked = self.check_archive_fixture(fixture)
                self.assertNotEqual(checked.returncode, 0)

    def test_retention_receipt_rejects_empty_noncanonical_id_or_wrong_uploaded_bytes(self):
        with self.evidence_fixture() as fixture:
            root, _, _, _, env = fixture
            result = self.seal_fixture(fixture)
            self.assertEqual(result.returncode, 0, result.stderr)
            archived = self.archive_fixture(fixture)
            checked = self.check_archive_fixture(fixture)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            digest, size = hashlib.sha256(archived.read_bytes()).hexdigest(), str(archived.stat().st_size)
            for artifact_id, retained_digest, retained_size in (("", digest, size), ("0", digest, size),
                    ("1\nforged=1", digest, size), ("123", "0" * 64, size), ("123", digest, "0"),
                    ("123", digest, "1")):
                with self.subTest(artifact_id=artifact_id[:1], size=retained_size):
                    env.update(ARTIFACT_ID=artifact_id, RETAINED_SHA256=retained_digest, RETAINED_SIZE_BYTES=retained_size)
                    checked = subprocess.run(["python3", "-c", self.workflow_python("PY_BACKUP_RECEIPT")],
                                             cwd=root, env=env, text=True, capture_output=True)
                    self.assertNotEqual(checked.returncode, 0)
                    self.assertEqual(checked.stdout, "")
            archived.write_bytes(archived.read_bytes() + b"changed after validation")
            env.update(ARTIFACT_ID="123", RETAINED_SHA256=hashlib.sha256(archived.read_bytes()).hexdigest(),
                       RETAINED_SIZE_BYTES=str(archived.stat().st_size))
            checked = subprocess.run(["python3", "-c", self.workflow_python("PY_BACKUP_RECEIPT")],
                                     cwd=root, env=env, text=True, capture_output=True)
            self.assertNotEqual(checked.returncode, 0)
            self.assertIn("backup archive changed after validation", checked.stderr)
            archived.write_bytes(b"")
            env.update(ARTIFACT_ID="123", RETAINED_SHA256=hashlib.sha256(b"").hexdigest(), RETAINED_SIZE_BYTES="0")
            checked = subprocess.run(["python3", "-c", self.workflow_python("PY_BACKUP_RECEIPT")],
                                     cwd=root, env=env, text=True, capture_output=True)
            self.assertNotEqual(checked.returncode, 0)

    def test_success_cleanup_preserves_the_already_checksummed_container_log(self):
        helpers = "\n".join(self.shell_function(name) for name in ("report_failure", "cleanup"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "container.log"
            path.write_text("frozen log\n")
            source = ("set -euo pipefail\n" + helpers + "\n"
                      + 'profile=postgresql; stage=seal-evidence; diagnostic_logs=(); container=fixture; evidence="$1"\n'
                      + 'docker() { if [[ "$1" == logs ]]; then printf "late changed log\\n"; fi; }\ncleanup\n')
            result = subprocess.run(["bash", "-c", source, "cleanup-fixture", directory],
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(path.read_text(), "frozen log\n")

    def test_archive_rejects_upload_and_gzip_expansion_byte_limits_before_parsing(self):
        spec = importlib.util.spec_from_file_location("backup_test_uploader", ROOT / "scripts/upload-actions-artifact.py")
        uploader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(uploader)
        spec = importlib.util.spec_from_file_location("backup_test_verifier", ROOT / "scripts/verify-actions-log-artifact.py")
        verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        with self.evidence_fixture() as fixture:
            root, _, _, _, env = fixture
            target = root / f"run/backup-restore-{env['PROFILE']}.tar.gz"
            with target.open("wb") as output:
                output.truncate(uploader.MAX_ARTIFACT_BYTES + 1)
            result = self.check_archive_fixture(fixture)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("artifact size is outside the bounded non-empty range", result.stderr)
            chunk = b"\0" * (1024 * 1024)
            with gzip.open(target, "wb") as output:
                for _ in range(verifier.MAX_EXPANDED_TAR_BYTES // len(chunk)):
                    output.write(chunk)
                output.write(b"\0" * (verifier.MAX_EXPANDED_TAR_BYTES % len(chunk) + 1))
            result = self.check_archive_fixture(fixture)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("archive exceeds the expanded tar bound", result.stderr)

    def test_workflow_uses_local_bounded_upload_and_validates_archive_before_retention(self):
        self.assertIn("uses: ./.github/actions/upload-evidence", self.workflow)
        for field in ("artifact_id", "sha256", "size_bytes"):
            self.assertIn(f"steps.retained-backup-profile.outputs.{field}", self.workflow)
        self.assertLess(self.workflow.index("PY_BACKUP_ARCHIVE"), self.workflow.index("id: retained-backup-profile"))
        self.assertIn("uploader.validate_artifact", self.workflow_python("PY_BACKUP_ARCHIVE"))
        self.assertIn("uploader.validate_artifact", self.workflow_python("PY_BACKUP_RECEIPT"))
        self.assertIn("github.run_attempt", self.workflow)


if __name__ == "__main__":
    unittest.main()
