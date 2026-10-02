"""Exercise retry workflow guards; synthetic packets grant no live SQL credit."""
from __future__ import annotations

from contextlib import contextmanager, redirect_stdout
import gzip
import hashlib
import importlib.util
import io
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
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
COMMIT = "a" * 40
TREE = "b" * 40
WORKFLOW_SHA = "c" * 40


class CockroachRetryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = (ROOT / ".github/workflows/cockroach-serialization-retry.yml").read_text()
        spec = importlib.util.spec_from_file_location(
            "retry_contract_source_schema", ROOT / "scripts/check-authoritative-schema-identity.py"
        )
        schemas = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(schemas)
        cls.chains, cls.version, cls.tables = schemas.validated_source()
        cls.lock = json.loads((ROOT / "migrations/MIGRATION_CHAIN.lock.json").read_bytes())
        cls.image = json.loads((ROOT / "config/database-test-images.json").read_bytes())["profiles"]["cockroachdb"]["image"]

    def workflow_python(self, label):
        match = re.search(rf"python3 - <<'{label}'\n(.*?)^\s*{label}$",
                          self.workflow, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(match, f"missing retry workflow block: {label}")
        return textwrap.dedent(match.group(1))

    @contextmanager
    def packet_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in ("scripts/verify-actions-log-artifact.py", "scripts/emit-actions-log-artifact.py",
                             "scripts/check-migration-lock.py", "scripts/check-authoritative-schema-identity.py",
                             "scripts/upload-actions-artifact.py", "docs/development/SCHEMA_AUTHORITY.json"):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, target)
            shutil.copytree(ROOT / "migrations", root / "migrations")
            (root / "run").mkdir()
            env = {"CANDIDATE_REPOSITORY": "Fixture/Retry", "CANDIDATE_SHA": COMMIT,
                   "GITHUB_REPOSITORY": "Fixture/Workflow", "GITHUB_RUN_ID": "123456789",
                   "GITHUB_RUN_ATTEMPT": "2", "GITHUB_JOB": "live-cockroach-serialization-retry",
                   "GITHUB_WORKFLOW_REF": "Fixture/Workflow/.github/workflows/cockroach-serialization-retry.yml@refs/pull/151/merge",
                   "GITHUB_WORKFLOW_SHA": WORKFLOW_SHA, "COCKROACH_IMAGE": self.image}
            applied = {"schema": "trillionnium.authoritative-schema-report.v1", "profile": "cockroachdb",
                       "schema_version": self.version, "storage_writer_epoch": 4,
                       "chain_digest": self.chains["cockroachdb"]["chain_sha256"],
                       "digest_algorithm": "ordered-path-git-blob-sha256.v1", "table_count": self.tables,
                       "source_commit": COMMIT, "upgrade_source_commit": COMMIT, "v2_apply_source_commit": COMMIT, "v3_apply_source_commit": COMMIT,
                       "migration_applied": True, "applied_steps": 4, "compatibility_credit": False}
            verified = {**applied, "migration_applied": False, "applied_steps": 0}
            identity = {"repository": "Fixture/Retry", "commit": COMMIT, "tree": TREE,
                        "run_id": "123456789", "run_attempt": "2", "workflow": "cockroach-serialization-retry",
                        "job_key": "live-cockroach-serialization-retry", "job_name": "live-cockroach-serialization-retry",
                        "workflow_repository": "Fixture/Workflow", "workflow_ref": env["GITHUB_WORKFLOW_REF"],
                        "workflow_sha": WORKFLOW_SHA, "profile": "cockroachdb", "image": self.image,
                        "fault_mode": "cockroach_session_commit_error_injection", "network_response_loss_injected": False,
                        "compatibility_credit": False, "accepted_evidence": False, "production_ready": False,
                        "schema_version": self.version, "storage_writer_epoch": 4,
                        "chain_digest": applied["chain_digest"], "digest_algorithm": applied["digest_algorithm"],
                        "source_commit": COMMIT, "upgrade_source_commit": COMMIT, "v2_apply_source_commit": COMMIT, "v3_apply_source_commit": COMMIT,
                        "authoritative_migration_file_count": 4}
            files = {"migration-chain.lock.json": (ROOT / "migrations/MIGRATION_CHAIN.lock.json").read_bytes(),
                     "execution.log": b"synthetic retry execution; no live SQL credit\n"}
            for entry in self.lock["profiles"]["cockroachdb"]["ordered_files"]:
                files[entry["path"]] = (ROOT / entry["path"]).read_bytes()
            for name, report in (("identity.json", identity), ("schema-apply.json", applied), ("schema-verify.json", verified)):
                files[name] = json.dumps(report, sort_keys=True).encode()
            self.write_archive(root, files)
            yield root, env, files

    def write_archive(self, root, files):
        # Recompute the real checksum manifest so producer attacks cannot fail only on a stale digest.
        retained = {name: data for name, data in files.items() if name != "files.sha256"}
        retained["files.sha256"] = "".join(
            hashlib.sha256(data).hexdigest() + "  ./" + name + "\n"
            for name, data in sorted(retained.items())
        ).encode()
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for name, data in sorted(retained.items()):
                member = tarfile.TarInfo("./" + name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        data = gzip.compress(stream.getvalue(), mtime=0)
        (root / "run/cockroach-retry.tar.gz").write_bytes(data)
        return data

    def mutate_report(self, files, name, changes):
        report = json.loads(files[name])
        report.update(changes)
        files[name] = json.dumps(report, sort_keys=True).encode()

    def execute_guard(self, label, root, env, *, commit=COMMIT, tree=TREE):
        calls = []

        def checkout_read(command, **kwargs):
            self.assertEqual(kwargs, {"text": True})
            values = {("git", "rev-parse", "HEAD"): commit,
                      ("git", "rev-parse", "HEAD^{tree}"): tree}
            self.assertIn(tuple(command), values, "unexpected external command in retry guard")
            calls.append(tuple(command))
            return values[tuple(command)] + "\n"

        previous = Path.cwd()
        output = io.StringIO()
        try:
            os.chdir(root)
            with patch.dict(os.environ, env, clear=True), redirect_stdout(output), \
                    patch.object(subprocess, "check_output", side_effect=checkout_read), \
                    patch.object(subprocess, "run", side_effect=AssertionError("unexpected subprocess")):
                exec(compile(self.workflow_python(label), f"retry-workflow:{label}", "exec"), {})
        finally:
            os.chdir(previous)
        return output.getvalue(), calls

    def receipt_env(self, root, env):
        archive = (root / "run/cockroach-retry.tar.gz").read_bytes()
        return {**env, "RETAINED_ARTIFACT_ID": "987654321",
                "RETAINED_SHA256": hashlib.sha256(archive).hexdigest(), "RETAINED_SIZE": str(len(archive))}

    def test_current_candidate_and_producer_pass_with_independent_workflow_sha(self):
        with self.packet_fixture() as (root, env, _):
            output, calls = self.execute_guard("PY_RETRY_ARCHIVE", root, env)
            self.assertEqual(output, "retry_archive_bytes_verified=true\n")
            self.assertEqual(calls, [("git", "rev-parse", "HEAD"), ("git", "rev-parse", "HEAD^{tree}")])
            checked = json.loads((root / "run/cockroach-retry-archive-check.json").read_bytes())
            self.assertEqual(checked["producer"]["tree"], TREE)
            self.assertNotEqual(checked["producer"]["workflow_sha"], checked["producer"]["commit"])
            self.assertFalse(checked["producer"]["accepted_evidence"])
            self.execute_guard("PY_RETRY_RECEIPT", root, self.receipt_env(root, env))

    def test_manifest_valid_producer_substitution_is_rejected(self):
        replacements = {"repository": "Other/Retry", "commit": "d" * 40, "tree": "d" * 40,
                        "run_id": "123456788", "run_attempt": "1", "workflow": "other-workflow",
                        "job_key": "source-and-unit", "job_name": "other-job",
                        "workflow_repository": "Other/Workflow", "workflow_ref": "other/ref",
                        "workflow_sha": "d" * 40, "profile": "postgresql", "image": "other@sha256:" + "d" * 64,
                        "fault_mode": "other-injection", "network_response_loss_injected": 0,
                        "compatibility_credit": True, "accepted_evidence": True, "production_ready": True}
        for field, replacement in replacements.items():
            with self.subTest(field=field), self.packet_fixture() as (root, env, files):
                self.mutate_report(files, "identity.json", {field: replacement})
                self.write_archive(root, files)
                with self.assertRaisesRegex(AssertionError, "retry producer mismatch"):
                    self.execute_guard("PY_RETRY_ARCHIVE", root, env)
                self.assertFalse((root / "run/cockroach-retry-archive-check.json").exists())

    def test_consistent_old_packet_cannot_self_authorize_fresh_publishers(self):
        with self.packet_fixture() as (root, env, files):
            provenance = dict.fromkeys(("source_commit", "upgrade_source_commit", "v2_apply_source_commit", "v3_apply_source_commit"), "d" * 40)
            for name in ("schema-apply.json", "schema-verify.json", "identity.json"):
                self.mutate_report(files, name, provenance)
            self.mutate_report(files, "identity.json", {"commit": "d" * 40, "tree": "e" * 40, "run_attempt": "1"})
            self.write_archive(root, files)
            with self.assertRaisesRegex(AssertionError, "retry producer mismatch"):
                self.execute_guard("PY_RETRY_ARCHIVE", root, env)

    def test_archive_provenance_cannot_replace_actual_fresh_apply_commit(self):
        with self.packet_fixture() as (root, env, files):
            provenance = dict.fromkeys(("source_commit", "upgrade_source_commit", "v2_apply_source_commit", "v3_apply_source_commit"), "d" * 40)
            for name in ("schema-apply.json", "schema-verify.json", "identity.json"):
                self.mutate_report(files, name, provenance)
            self.write_archive(root, files)
            with self.assertRaisesRegex(RuntimeError, "fresh apply provenance mismatch"):
                self.execute_guard("PY_RETRY_ARCHIVE", root, env)

    def test_checkout_commit_and_tree_are_independent_authority(self):
        for field in ("commit", "tree"):
            with self.subTest(field=field), self.packet_fixture() as (root, env, _):
                with self.assertRaises(AssertionError):
                    self.execute_guard("PY_RETRY_ARCHIVE", root, env, **{field: "d" * 40})

    def test_current_producer_environment_is_required_and_canonical(self):
        changes = {"GITHUB_RUN_ID": "0", "GITHUB_RUN_ATTEMPT": "", "GITHUB_JOB": "source-and-unit",
                   "GITHUB_WORKFLOW_SHA": "x" * 40, "GITHUB_WORKFLOW_REF": "Fixture/Other/ref\n",
                   "GITHUB_REPOSITORY": "invalid", "CANDIDATE_REPOSITORY": "invalid"}
        for field, value in changes.items():
            with self.subTest(field=field), self.packet_fixture() as (root, env, _):
                with self.assertRaises(AssertionError):
                    self.execute_guard("PY_RETRY_ARCHIVE", root, {**env, field: value})
        with self.packet_fixture() as (root, env, _):
            del env["GITHUB_RUN_ATTEMPT"]
            with self.assertRaises(KeyError):
                self.execute_guard("PY_RETRY_ARCHIVE", root, env)

    def test_all_three_exact_sql_files_remain_mandatory(self):
        path = self.lock["profiles"]["cockroachdb"]["ordered_files"][2]["path"]
        for mutation in ("missing", "changed"):
            with self.subTest(mutation=mutation), self.packet_fixture() as (root, env, files):
                if mutation == "missing":
                    del files[path]
                else:
                    files[path] += b"\n-- synthetic changed source\n"
                self.write_archive(root, files)
                with self.assertRaisesRegex(ValueError, "archived migration"):
                    self.execute_guard("PY_RETRY_ARCHIVE", root, env)

    def test_readonly_schema_report_cannot_claim_mutation_or_changed_history(self):
        for changes in ({"migration_applied": True, "applied_steps": 4}, {"v2_apply_source_commit": "d" * 40}):
            with self.subTest(changes=changes), self.packet_fixture() as (root, env, files):
                self.mutate_report(files, "schema-verify.json", changes)
                self.write_archive(root, files)
                with self.assertRaises((RuntimeError, AssertionError)):
                    self.execute_guard("PY_RETRY_ARCHIVE", root, env)

    def test_matching_upload_receipt_cannot_promote_changed_prechecked_bytes(self):
        with self.packet_fixture() as (root, env, files):
            self.execute_guard("PY_RETRY_ARCHIVE", root, env)
            files["execution.log"] += b"synthetic post-check mutation\n"
            self.write_archive(root, files)
            with self.assertRaises(AssertionError):
                self.execute_guard("PY_RETRY_RECEIPT", root, self.receipt_env(root, env))

    def test_retained_receipt_requires_same_current_producer_and_typed_size(self):
        for field, value in (("GITHUB_RUN_ATTEMPT", "3"), ("GITHUB_WORKFLOW_SHA", "d" * 40),
                             ("RETAINED_ARTIFACT_ID", "0"), ("RETAINED_SIZE", "01")):
            with self.subTest(field=field), self.packet_fixture() as (root, env, _):
                self.execute_guard("PY_RETRY_ARCHIVE", root, env)
                with self.assertRaises((RuntimeError, AssertionError)):
                    self.execute_guard("PY_RETRY_RECEIPT", root, {**self.receipt_env(root, env), field: value})

    def test_missing_or_malformed_precheck_cannot_authorize_matching_upload_receipt(self):
        for mutation in ("missing", "boolean-size", "duplicate-field"):
            with self.subTest(mutation=mutation), self.packet_fixture() as (root, env, _):
                self.execute_guard("PY_RETRY_ARCHIVE", root, env)
                path = root / "run/cockroach-retry-archive-check.json"
                if mutation == "missing":
                    path.unlink()
                elif mutation == "boolean-size":
                    checked = json.loads(path.read_bytes())
                    checked["archive_size"] = True
                    path.write_text(json.dumps(checked))
                else:
                    path.write_bytes(path.read_bytes().replace(b'{', b'{"archive_size":1,', 1))
                with self.assertRaises((OSError, RuntimeError, AssertionError)):
                    self.execute_guard("PY_RETRY_RECEIPT", root, self.receipt_env(root, env))


if __name__ == "__main__":
    unittest.main()
