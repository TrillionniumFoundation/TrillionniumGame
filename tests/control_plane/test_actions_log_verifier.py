from __future__ import annotations

import copy
import base64
import hashlib
import importlib.util
import io
import json
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import sys
import urllib.parse
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/control_plane"))
from source_http_mock import mock_source_process
SCRIPT = ROOT / "scripts/verify-actions-log-artifact.py"
REPOSITORY = "TrillionniumFoundation/TrillionniumGame"
HEAD = "a" * 40
TREE = "b" * 40
RUN_ID = "123456"
RUN_ATTEMPT = "2"


class ActionsLogVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location("verify_actions_log_artifact", SCRIPT)
        assert spec is not None and spec.loader is not None
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        seal_spec = importlib.util.spec_from_file_location("seal_outbox_final_attempt", ROOT / "scripts/seal-outbox-final-attempt.py")
        assert seal_spec is not None and seal_spec.loader is not None
        cls.sealer = importlib.util.module_from_spec(seal_spec)
        seal_spec.loader.exec_module(cls.sealer)

    @classmethod
    def binding(cls, profile: str):
        return cls.module.BINDING.verify_binding(ROOT, profile=profile)

    @classmethod
    def binding_view(cls, profile: str):
        return cls.module.BINDING.operational_binding(cls.binding(profile))

    @classmethod
    def archive(
        cls,
        profile: str,
        *,
        identity_overrides: dict[str, str] | None = None,
        result_overrides: dict[str, str] | None = None,
        manifest_self_reference: bool = False,
        schema_overrides: dict[str, Any] | None = None,
        file_overrides: dict[str, bytes | None] | None = None,
        legacy_identity: bool = False,
    ) -> bytes:
        token = cls.binding(profile)
        binding = cls.module.BINDING.operational_binding(token)
        identity = {
            "repository": REPOSITORY,
            "commit": HEAD,
            "tree": TREE,
            "profile": profile,
            "image": binding["image"],
            "run_id": RUN_ID,
            "run_attempt": RUN_ATTEMPT,
            "evidence_run_id": f"{RUN_ID}-{RUN_ATTEMPT}-{profile}",
            "workflow": cls.module.WORKFLOW_NAME,
            "workflow_path": cls.module.WORKFLOW_PATH,
            "job_key": "live-profile",
            "job_name": f"live-profile ({profile})",
            **cls.module.BINDING.identity_fields(token),
            **{key: binding[key] for key in (
                "migration_lock", "schema_version", "storage_writer_epoch", "chain_digest", "digest_algorithm"
            )},
        }
        identity.update(identity_overrides or {})
        if legacy_identity:
            for key in ("migration_lock", "schema_version", "storage_writer_epoch", "chain_digest", "digest_algorithm"):
                identity.pop(key, None)
            identity["migration"] = f"migrations/{profile}/0001_foundation_up.sql"
            identity["migration_blob_sha1"] = "c" * 40
        result = {
            "status": "passed",
            "profile": profile,
            "commit": HEAD,
            "tree": TREE,
        }
        result.update(result_overrides or {})
        schema = {
            "schema": "trillionnium.authoritative-schema-report.v1", "profile": profile,
            "schema_version": 4, "storage_writer_epoch": 4,
            "chain_digest": binding["chain_digest"], "digest_algorithm": binding["digest_algorithm"],
            "source_commit": HEAD, "upgrade_source_commit": HEAD, "v2_apply_source_commit": HEAD, "v3_apply_source_commit": HEAD,
            "migration_applied": True, "table_count": 12, "applied_steps": 4,
            "compatibility_credit": False,
        }
        schema.update(schema_overrides or {})
        files = {
            "identity.env": cls.env_bytes(identity),
            "result.env": cls.env_bytes(result),
            "crash-before-publish/result.env": cls.env_bytes(
                {
                    "possible_lost_effect_declared": "true",
                    "spool_effect_count": "0",
                    "outbox_row_count": "1",
                    "dead_letter_count": "1",
                }
            ),
            "crash-before-publish/reaper.stdout": (
                b"claimed=0 completed=0 retried=0 dead_lettered=1\n"
            ),
            "crash-after-publish/result.env": cls.env_bytes(
                {
                    "possible_lost_effect_declared": "false",
                    "spool_effect_count": "1",
                    "outbox_row_count": "1",
                    "dead_letter_count": "1",
                }
            ),
            "crash-after-publish/reaper.stdout": (
                b"claimed=0 completed=0 retried=0 dead_lettered=1\n"
            ),
            "crash-after-publish/spool/abcd.json": b'{"effect":"stable"}\n',
            "logs/database.log": b"database evidence\n",
            "migration-chain.lock.json": (ROOT / "migrations/MIGRATION_CHAIN.lock.json").read_bytes(),
            "migration-chain-validation.json": json.dumps(cls.module.MIGRATIONS.validate(ROOT)).encode(),
            "schema-identity.json": json.dumps(schema).encode(),
        }
        proof = cls.module.BINDING.binding_document(token)
        files[cls.module.BINDING.SIDECAR] = cls.module.BINDING.canonical(proof)
        files[cls.module.BINDING.HEAD_PROOF] = cls.module.BINDING.canonical(cls.module.BINDING.head_document(token,commit=HEAD,tree=TREE))
        files.update({row["archive_path"]:(ROOT/row["path"]).read_bytes() for row in proof["full_source_inventory"]})
        files.update({entry["path"]: (ROOT / entry["path"]).read_bytes()
                      for entry in binding["ordered_files"]})
        for name, payload in (file_overrides or {}).items():
            if payload is None:
                files.pop(name, None)
            else:
                files[name] = payload
        manifest_lines = [
            f"{hashlib.sha256(payload).hexdigest()}  ./{name}"
            for name, payload in sorted(files.items())
        ]
        if manifest_self_reference:
            manifest_lines.append(f"{'e' * 64}  ./files.sha256")
        files["files.sha256"] = ("\n".join(manifest_lines) + "\n").encode()
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            for name, payload in sorted(files.items()):
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                info.mtime = 0
                info.uid = 0
                info.gid = 0
                archive.addfile(info, io.BytesIO(payload))
        return output.getvalue()

    @staticmethod
    def env_bytes(values: dict[str, str]) -> bytes:
        return "".join(f"{key}={value}\n" for key, value in values.items()).encode()

    @staticmethod
    def step(
        number: int,
        *,
        status: str = "completed",
        conclusion: Any = "success",
    ) -> dict[str, Any]:
        return {
            "name": f"step-{number}",
            "number": number,
            "status": status,
            "conclusion": conclusion,
        }

    @classmethod
    def job(
        cls,
        name: str,
        job_id: int,
        *,
        status: str = "completed",
        conclusion: Any = "success",
        runner_id: int = 9001,
        steps: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "id": job_id,
            "name": name,
            "status": status,
            "conclusion": conclusion,
            "runner_id": runner_id,
            "runner_name": "GitHub Actions runner",
            "steps": steps if steps is not None else [cls.step(1)],
        }

    @classmethod
    def completed_jobs(cls) -> list[dict[str, Any]]:
        return [
            cls.job(cls.module.SOURCE_JOB, 1),
            cls.job("live-profile (postgresql)", 2),
            cls.job("live-profile (cockroachdb)", 3),
            cls.job(cls.module.FINAL_JOB, 4),
        ]

    @classmethod
    def current_jobs(cls) -> list[dict[str, Any]]:
        jobs = cls.completed_jobs()
        jobs[-1] = cls.job(
            cls.module.FINAL_JOB,
            4,
            status="in_progress",
            conclusion=None,
            steps=[
                cls.step(1),
                cls.step(2, status="in_progress", conclusion=None),
            ],
        )
        return jobs

    @classmethod
    def workflow_run(cls, workflow_id: int, *, current: bool) -> dict[str, Any]:
        return {
            "id": 123456,
            "workflow_id": workflow_id,
            "name": cls.module.WORKFLOW_NAME,
            "path": cls.module.WORKFLOW_PATH,
            "event": "pull_request",
            "head_sha": HEAD,
            "run_attempt": int(RUN_ATTEMPT),
            "repository": {"full_name": REPOSITORY},
            "head_repository": {"full_name": REPOSITORY},
            "head_commit": {"id": HEAD, "tree_id": TREE},
            "run_started_at": "2026-08-31T00:00:00Z",
            "status": "in_progress" if current else "completed",
            "conclusion": None if current else "success",
        }

    def test_archive_accepts_exact_identity_and_both_boundaries(self) -> None:
        for profile in self.module.PROFILES:
            record = self.module.validate_archive(
                self.archive(profile),
                repository=REPOSITORY,
                head_sha=HEAD,
                head_tree=TREE,
                run_id=RUN_ID,
                run_attempt=RUN_ATTEMPT,
                profile=profile,
                binding=self.binding(profile),
            )
            self.assertEqual(record["profile"], profile)
            self.assertEqual(record["head_tree"], TREE)
            self.assertEqual(record["schema_version"], 4)
            self.assertEqual(record["storage_writer_epoch"], 4)
            self.assertEqual(record["v2_apply_source_commit"], HEAD)
            self.assertEqual(len(record["ordered_files"]), 4)
            self.assertFalse(record["production_ready"])
            self.assertFalse(record["compatibility_credit"])

    def seal_fixture(self, profile: str) -> tuple[Path, dict[str, str]]:
        temporary = tempfile.TemporaryDirectory(prefix="outbox-seal-", dir=ROOT.parent)
        self.addCleanup(temporary.cleanup)
        # Commit only this test's copied source in an isolated repository. The
        # real sealer still checks Git HEAD/tree/lock/SQL blobs, even while the
        # working repository contains an uncommitted complete schema cutover.
        source_root = Path(temporary.name) / "source"
        source_root.mkdir()
        shutil.copytree(ROOT / "migrations", source_root / "migrations")
        for relative in self.module.BINDING.CONTROL_PATHS:
            target=source_root/relative;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/relative,target)
        for arguments in (
            ["init", "--quiet"],
            ["add", "--", "."],
            ["-c", "user.name=Storage Control Fixture", "-c", "user.email=storage-fixture@example.invalid",
             "-c", "commit.gpgSign=false", "-c", "core.hooksPath=/dev/null",
             "commit", "--quiet", "-m", "Isolated source binding fixture"],
        ):
            subprocess.run(["git", *arguments], cwd=source_root, check=True, capture_output=True)
        source_patch = mock.patch.object(self.sealer, "SOURCE_ROOT", source_root)
        source_patch.start()
        self.addCleanup(source_patch.stop)
        root = Path(temporary.name) / profile
        root.mkdir()
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source_root, text=True).strip()
        tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=source_root, text=True).strip()
        files = self.module.archive_files(self.archive(
            profile, schema_overrides={"source_commit": commit, "upgrade_source_commit": commit, "v2_apply_source_commit": commit, "v3_apply_source_commit": commit},
            result_overrides={"commit": commit, "tree": tree},
        ))
        files={name:payload for name,payload in files.items() if name not in (self.module.BINDING.SIDECAR,self.module.BINDING.HEAD_PROOF) and not name.startswith(self.module.BINDING.ANNEX+'/')}
        files["result.env"] = self.env_bytes({"status": "passed", "profile": profile, "commit": commit})
        for name, payload in files.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        return root, dict(profile=profile, commit=commit, tree=tree, repository=REPOSITORY,
                          run_id=RUN_ID, run_attempt=RUN_ATTEMPT)

    def sealed_tar(self, root: Path, changes: dict[str, bytes | None] | None = None) -> Path:
        files = {path.relative_to(root).as_posix(): path.read_bytes()
                 for path in root.rglob("*") if path.is_file()}
        for name, payload in (changes or {}).items():
            if payload is None:
                files.pop(name, None)
            else:
                files[name] = payload
        # Rehash deliberately modified archives so semantic lock checks must
        # catch altered SQL even when their ordinary file manifest is valid.
        files.pop("files.sha256", None)
        files["files.sha256"] = ("\n".join(f"{hashlib.sha256(payload).hexdigest()}  ./{name}"
                                              for name, payload in sorted(files.items())) + "\n").encode()
        archive = root.parent / "retained.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            for name, payload in sorted(files.items()):
                member = tarfile.TarInfo(name)
                member.size = len(payload)
                output.addfile(member, io.BytesIO(payload))
        return archive

    def test_actual_sealer_copies_all_four_locked_migrations_and_verifies_each_profile_archive(self) -> None:
        for profile in self.module.PROFILES:
            with self.subTest(profile=profile):
                root, identity = self.seal_fixture(profile)
                result = self.sealer.seal_profile(root, **identity)
                self.assertFalse(result["compatibility_credit"])
                self.assertFalse(result["production_ready"])
                binding = self.binding_view(profile)
                self.assertEqual(len(binding["ordered_files"]), 4)
                for entry in binding["ordered_files"]:
                    self.assertEqual(self.module.git_blob_sha1((root / entry["path"]).read_bytes()), entry["git_blob_sha1"])
                sealed = self.module.parse_env((root / "identity.env").read_bytes(), "sealed identity")
                self.assertNotIn("migration_blob_sha1", sealed)
                self.assertEqual(sealed["chain_digest"], binding["chain_digest"])
                checked = self.sealer.verify_archive(self.sealed_tar(root), **identity)
                self.assertTrue(checked["complete_chain_archive_verified"])
                self.assertFalse(checked["compatibility_credit"])

    def test_actual_sealer_rejects_stale_incomplete_ambiguous_and_failed_evidence(self) -> None:
        for mutation in ("old_lock", "short_validation", "short_schema", "wrong_schema_profile",
                         "wrong_apply_commit", "wrong_v2_apply_commit", "missing_v2_apply_commit", "wrong_v3_apply_commit", "missing_v3_apply_commit", "duplicate_schema", "duplicate_result", "failed_result",
                         "extra_migration", "symlink", "oversized_file"):
            with self.subTest(mutation=mutation):
                root, identity = self.seal_fixture("postgresql")
                schema_path = root / "schema-identity.json"
                schema = json.loads(schema_path.read_bytes())
                if mutation == "old_lock":
                    (root / "migration-chain.lock.json").write_text("{}")
                elif mutation == "short_validation":
                    path = root / "migration-chain-validation.json"
                    value = json.loads(path.read_bytes())
                    value["profiles"]["postgresql"]["file_count"] = 1
                    path.write_text(json.dumps(value))
                elif mutation in {"short_schema", "wrong_schema_profile", "wrong_apply_commit", "wrong_v2_apply_commit", "missing_v2_apply_commit", "wrong_v3_apply_commit", "missing_v3_apply_commit"}:
                    schema.update({"short_schema": {"applied_steps": 1},
                                   "wrong_schema_profile": {"profile": "cockroachdb"},
                                   "wrong_apply_commit": {"source_commit": "f" * 40},
                                   "wrong_v2_apply_commit": {"v2_apply_source_commit": "f" * 40},
                                   "missing_v2_apply_commit": {},
                                   "wrong_v3_apply_commit": {"v3_apply_source_commit": "f" * 40},
                                   "missing_v3_apply_commit": {}}[mutation])
                    if mutation in ("missing_v2_apply_commit", "missing_v3_apply_commit"):
                        del schema["v2_apply_source_commit" if mutation == "missing_v2_apply_commit" else "v3_apply_source_commit"]
                    schema_path.write_text(json.dumps(schema))
                elif mutation == "duplicate_schema":
                    schema_path.write_text('{"schema_version":1,' + json.dumps(schema)[1:])
                elif mutation == "duplicate_result":
                    path = root / "result.env"
                    path.write_text("status=failed\n" + path.read_text())
                elif mutation == "failed_result":
                    path = root / "result.env"
                    path.write_text(path.read_text().replace("status=passed", "status=failed"))
                elif mutation == "extra_migration":
                    path = root / "migrations/postgresql/0003_unlisted_up.sql"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("SELECT 1;")
                elif mutation == "symlink":
                    (root / "linked-schema.json").symlink_to(schema_path)
                else:
                    (root / "oversized.log").write_bytes(b"x" * (self.module.MAX_ARCHIVE_BYTES + 1))
                with self.assertRaises((RuntimeError, OSError, ValueError)):
                    self.sealer.seal_profile(root, **identity)

    def test_actual_prospective_sealer_binds_real_producer_and_rejects_context_mix(self) -> None:
        for profile in self.module.PROFILES:
            with self.subTest(profile=profile):
                root, identity = self.seal_fixture(profile)
                self.sealer.seal_profile(root, **identity, workflow_context="prospective")
                sealed = self.module.parse_env((root / "identity.env").read_bytes(), "sealed identity")
                producer = self.module.producer_identity(profile, "prospective")
                for field, value in producer.items():
                    self.assertEqual(sealed[field], value)
                archive = self.sealed_tar(root)
                self.assertTrue(self.sealer.verify_archive(
                    archive, **identity, workflow_context="prospective"
                )["complete_chain_archive_verified"])
                with self.assertRaisesRegex(self.sealer.VERIFIER.VerificationError, "workflow mismatch"):
                    self.sealer.verify_archive(archive, **identity)
                for field in producer:
                    changed = dict(sealed)
                    changed[field] = self.module.producer_identity(profile)[field]
                    with self.subTest(field=field), self.assertRaisesRegex(
                        self.sealer.VERIFIER.VerificationError, f"{field} mismatch"
                    ):
                        self.sealer.verify_archive(
                            self.sealed_tar(root, {"identity.env": self.env_bytes(changed)}),
                            **identity, workflow_context="prospective",
                        )
        with self.assertRaisesRegex(self.module.VerificationError, "producer context"):
            self.module.producer_identity("postgresql", "unlisted")

    def test_actual_archive_sealer_rejects_each_missing_or_rehashed_sql_blob(self) -> None:
        root, identity = self.seal_fixture("postgresql")
        self.sealer.seal_profile(root, **identity)
        for entry in self.binding_view("postgresql")["ordered_files"]:
            for payload in (None, b"SELECT 1;\n"):
                with self.subTest(path=entry["path"], missing=payload is None):
                    archive = self.sealed_tar(root, {entry["path"]: payload})
                    with self.assertRaisesRegex(self.sealer.VERIFIER.VerificationError, "archived migration"):
                        self.sealer.verify_archive(archive, **identity)

    def test_actual_sealer_accounts_for_manifest_entry_and_payload_in_shared_budgets(self) -> None:
        root, identity = self.seal_fixture("postgresql")
        self.sealer.seal_profile(root, **identity)
        (root / "result.env").write_bytes(self.env_bytes({"status": "passed", "profile": identity["profile"], "commit": identity["commit"]}))
        (root / "files.sha256").unlink()
        entries_without_manifest = sum(1 for _ in root.rglob("*"))
        with mock.patch.object(self.sealer.VERIFIER, "MAX_ARCHIVE_ENTRIES", entries_without_manifest):
            with self.assertRaisesRegex(self.sealer.SealingError, "entry budget"):
                self.sealer.seal_profile(root, **identity)
        self.assertTrue((root / "files.sha256").is_file())

        root, identity = self.seal_fixture("postgresql")
        self.sealer.seal_profile(root, **identity)
        final_size = sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
        manifest_bytes = (root / "files.sha256").stat().st_size
        producer_result = self.env_bytes({"status": "passed", "profile": identity["profile"], "commit": identity["commit"]})
        sealing_result_delta = (root / "result.env").stat().st_size - len(producer_result)
        (root / "result.env").write_bytes(producer_result)
        (root / "files.sha256").unlink()
        initial_size = sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
        self.assertEqual(initial_size + sealing_result_delta, final_size - manifest_bytes)
        self.assertLess(initial_size, final_size - 1)
        with mock.patch.object(self.sealer.VERIFIER, "MAX_RETAINED_BYTES", final_size - 1):
            with self.assertRaisesRegex(self.sealer.SealingError, "expanded byte budget"):
                self.sealer.seal_profile(root, **identity)

    def test_shared_archive_requires_each_exact_locked_sql_blob_despite_valid_manifest(self) -> None:
        for profile in self.module.PROFILES:
            binding = self.binding_view(profile)
            for entry in binding["ordered_files"]:
                for payload in (None, b"SELECT 1;\n"):
                    with self.subTest(profile=profile, path=entry["path"], missing=payload is None):
                        archive = self.archive(profile, file_overrides={entry["path"]: payload})
                        # The fixture hashes its changed contents, so ordinary
                        # manifest verification succeeds before the source check.
                        self.module.verify_file_manifest(self.module.archive_files(archive))
                        with self.assertRaisesRegex(self.module.VerificationError, "archived migration"):
                            self.module.validate_archive(
                                archive, repository=REPOSITORY, head_sha=HEAD, head_tree=TREE,
                                run_id=RUN_ID, run_attempt=RUN_ATTEMPT,
                                profile=profile, binding=self.binding(profile),
                            )

    def test_shared_archive_rejects_unlisted_or_other_profile_migration_payloads(self) -> None:
        for profile in self.module.PROFILES:
            other_profile = next(value for value in self.module.PROFILES if value != profile)
            for name in (f"migrations/{profile}/0003_unlisted_up.sql",
                         f"migrations/{other_profile}/0001_foundation_up.sql",
                         f"migrations/{profile}/unlisted.txt"):
                with self.subTest(profile=profile, path=name):
                    archive = self.archive(profile, file_overrides={name: b"SELECT 1;\n"})
                    self.module.verify_file_manifest(self.module.archive_files(archive))
                    with self.assertRaisesRegex(self.module.VerificationError, "migration inventory.*unlisted"):
                        self.module.validate_archive(
                            archive, repository=REPOSITORY, head_sha=HEAD, head_tree=TREE,
                            run_id=RUN_ID, run_attempt=RUN_ATTEMPT,
                            profile=profile, binding=self.binding(profile),
                        )

    @staticmethod
    def bounded_tar(files: dict[str, bytes], *, directories: list[str] | None = None,
                    pax_headers: dict[str, str] | None = None) -> bytes:
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            for name in directories or []:
                member = tarfile.TarInfo(name)
                member.type = tarfile.DIRTYPE
                archive.addfile(member)
            for name, payload in files.items():
                member = tarfile.TarInfo(name)
                member.size = len(payload)
                member.pax_headers = pax_headers or {}
                archive.addfile(member, io.BytesIO(payload))
        return output.getvalue()

    def test_archive_parser_counts_directories_and_preserves_one_structural_root(self) -> None:
        maximum = self.module.MAX_ARCHIVE_ENTRIES
        accepted = self.bounded_tar({"retained.txt": b"retained"},
                                    directories=["."] + [f"d{index}" for index in range(maximum - 1)])
        self.assertEqual(self.module.archive_files(accepted), {"retained.txt": b"retained"})
        for directories in ([f"d{index}" for index in range(maximum)],
                            ["repeated"] * maximum,
                            ["."] + [f"d{index}" for index in range(maximum)]):
            with self.subTest(first=directories[0], length=len(directories)):
                archive = self.bounded_tar({"retained.txt": b"retained"}, directories=directories)
                with self.assertRaisesRegex(self.module.VerificationError, "entry budget"):
                    self.module.archive_files(archive)

    def test_archive_parser_enforces_file_and_total_expanded_payload_bounds(self) -> None:
        payload = b"x" * self.module.MAX_ARCHIVE_BYTES
        count = self.module.MAX_RETAINED_BYTES // len(payload)
        files = {f"f{index}": payload for index in range(count)}
        accepted = self.bounded_tar(files)
        self.assertLess(len(accepted), self.module.MAX_ARCHIVE_BYTES)
        self.assertEqual(sum(map(len, self.module.archive_files(accepted).values())),
                         self.module.MAX_RETAINED_BYTES)
        files["over-budget"] = b"x"
        with self.assertRaisesRegex(self.module.VerificationError, "expanded file byte budget"):
            self.module.archive_files(self.bounded_tar(files))
        oversized = self.bounded_tar({"oversized": payload + b"x"})
        with self.assertRaisesRegex(self.module.VerificationError, "tar member exceeds"):
            self.module.archive_files(oversized)

    def test_archive_parser_bounds_gzip_expansion_before_large_pax_header(self) -> None:
        archive = self.bounded_tar({"retained.txt": b"retained"},
                                   pax_headers={"comment": "x" * (self.module.MAX_EXPANDED_TAR_BYTES + 1)})
        self.assertLess(len(archive), self.module.MAX_ARCHIVE_BYTES)
        with self.assertRaisesRegex(self.module.VerificationError, "expanded tar bound"):
            self.module.archive_files(archive)

    def test_outbox_workflow_uses_shared_sealer_before_and_after_archive(self) -> None:
        workflow = (ROOT / ".github/workflows/outbox-final-attempt-reaper.yml").read_text()
        self.assertEqual(workflow.count("python3 scripts/seal-outbox-final-attempt.py"), 2)
        self.assertIn('--tree "$candidate_tree" --archive "$archive"', workflow)
        self.assertNotIn("profile migration lock is not singular", workflow)
        self.assertNotIn("ordered[0]", workflow)

    def test_prospective_workflow_verifies_archive_with_its_actual_producer(self) -> None:
        workflow = (ROOT / ".github/workflows/prospective-merge-gate.yml").read_text()
        self.assertEqual(workflow.count("python3 scripts/seal-outbox-final-attempt.py"), 2)
        self.assertEqual(workflow.count("--workflow-context prospective"), 2)
        self.assertIn('--workflow-context prospective --archive "$outbox_archive"', workflow)
        self.assertLess(workflow.index('--archive "$outbox_archive"'),
                        workflow.index('archive="run/prospective-merge-${PROFILE}'))

    def test_archive_rejects_wrong_tree_blob_path_and_self_reference(self) -> None:
        profile = "postgresql"
        binding = self.binding(profile)
        cases = [
            (
                self.archive(profile, identity_overrides={"tree": "e" * 40}),
                binding,
            ),
            (
                self.archive(
                    profile,
                    identity_overrides={"chain_digest": "e" * 64},
                ),
                binding,
            ),
            (
                self.archive(
                    profile,
                    identity_overrides={
                        "migration_lock": "migrations/cockroachdb/0001_foundation_up.sql"
                    },
                ),
                binding,
            ),
            (self.archive(profile, manifest_self_reference=True), binding),
        ]
        for archive, case_binding in cases:
            with self.subTest():
                with self.assertRaises(self.module.VerificationError):
                    self.module.validate_archive(
                        archive,
                        repository=REPOSITORY,
                        head_sha=HEAD,
                        head_tree=TREE,
                        run_id=RUN_ID,
                        run_attempt=RUN_ATTEMPT,
                        profile=profile,
                        binding=case_binding,
                    )

    def assert_schema_archive_rejected(self, archive: bytes) -> None:
        with self.assertRaises(self.module.VerificationError):
            self.module.validate_archive(
                archive, repository=REPOSITORY, head_sha=HEAD, head_tree=TREE,
                run_id=RUN_ID, run_attempt=RUN_ATTEMPT, profile="postgresql",
                binding=self.binding("postgresql"),
            )

    def test_archive_rejects_v1_identity_incomplete_schema_and_forged_execution(self) -> None:
        self.assert_schema_archive_rejected(self.archive("postgresql", legacy_identity=True))
        for key, value in (
            ("schema_version", 1), ("schema_version", 2), ("storage_writer_epoch", 1), ("storage_writer_epoch", 2),
            ("applied_steps", 1), ("applied_steps", 2),
            ("table_count", 9), ("chain_digest", "e" * 64),
            ("digest_algorithm", "raw-content-historical.v1"),
            ("source_commit", "f" * 40), ("upgrade_source_commit", "f" * 40),
            ("v2_apply_source_commit", "f" * 40), ("v2_apply_source_commit", None),
            ("migration_applied", False), ("migration_applied", 1),
            ("compatibility_credit", True),
        ):
            with self.subTest(key=key, value=value):
                self.assert_schema_archive_rejected(
                    self.archive("postgresql", schema_overrides={key: value})
                )

    def test_archive_rejects_missing_or_tampered_retained_chain_documents(self) -> None:
        for name in ("migration-chain.lock.json", "migration-chain-validation.json", "schema-identity.json"):
            with self.subTest(name=name):
                self.assert_schema_archive_rejected(self.archive("postgresql", file_overrides={name: None}))
        self.assert_schema_archive_rejected(self.archive("postgresql", file_overrides={"migration-chain.lock.json": b"{}"}))
        validation = self.module.MIGRATIONS.validate(ROOT)
        validation["profiles"]["postgresql"]["file_count"] = 1
        self.assert_schema_archive_rejected(self.archive("postgresql", file_overrides={
            "migration-chain-validation.json": json.dumps(validation).encode(),
        }))
        self.assert_schema_archive_rejected(self.archive("postgresql", file_overrides={
            "schema-identity.json": b'{"schema_version":1,"schema_version":3}',
        }))

    def test_v3_archive_prior_publisher_cannot_be_missing_stale_or_forged_even_when_rehashed(self) -> None:
        for profile in self.module.PROFILES:
            original = self.module.strict_object(self.module.archive_files(self.archive(profile))["schema-identity.json"], "fixture schema")
            for value in (None, "f" * 40, "not-a-commit", 7):
                schema = original | {"v2_apply_source_commit": value}
                if value is None:
                    del schema["v2_apply_source_commit"]
                archive = self.archive(profile, file_overrides={"schema-identity.json": json.dumps(schema).encode()})
                self.module.verify_file_manifest(self.module.archive_files(archive))
                with self.subTest(profile=profile, prior=value), self.assertRaisesRegex(self.module.VerificationError, "v2_apply_source_commit"):
                    self.module.validate_archive(
                        archive, repository=REPOSITORY, head_sha=HEAD, head_tree=TREE,
                        run_id=RUN_ID, run_attempt=RUN_ATTEMPT, profile=profile, binding=self.binding(profile),
                    )
            duplicate = b'{"v2_apply_source_commit":"' + b"f" * 40 + b'",' + json.dumps(original).encode()[1:]
            with self.subTest(profile=profile, prior="duplicate"), self.assertRaisesRegex(self.module.VerificationError, "duplicate JSON key"):
                self.module.validate_archive(
                    self.archive(profile, file_overrides={"schema-identity.json": duplicate}),
                    repository=REPOSITORY, head_sha=HEAD, head_tree=TREE,
                    run_id=RUN_ID, run_attempt=RUN_ATTEMPT, profile=profile, binding=self.binding(profile),
                )

    def test_current_chain_binding_cannot_revert_to_v2_or_an_incomplete_suffix(self) -> None:
        for profile in self.module.PROFILES:
            for field in ("schema_version", "storage_writer_epoch", "ordered_files"):
                binding = self.binding_view(profile)
                binding[field] = binding[field][:2] if field == "ordered_files" else "2"
                with self.subTest(profile=profile, field=field), self.assertRaisesRegex(self.module.VerificationError, "issued full-source5 evidence binding"):
                    self.module.validate_archive(
                        self.archive(profile), repository=REPOSITORY, head_sha=HEAD, head_tree=TREE,
                        run_id=RUN_ID, run_attempt=RUN_ATTEMPT, profile=profile, binding=binding,
                    )

    def test_retained_whole_chain_action_counts_and_claims_remain_exact(self) -> None:
        for profile in self.module.PROFILES:
            for scope, field, value in (
                ("global", "schema_version", 2), ("global", "source_identity_verified", False),
                ("global", "runtime_execution_verified", True), ("global", "compatibility_credit", True),
                ("profile", "declared_action_count", 21), ("profile", "revision_action_counts", {"2": 6, "3": 15}),
                ("profile", "file_count", 2),
            ):
                validation = self.module.MIGRATIONS.validate(ROOT)
                target = validation if scope == "global" else validation["profiles"][profile]
                target[field] = value
                archive = self.archive(profile, file_overrides={"migration-chain-validation.json": json.dumps(validation).encode()})
                self.module.verify_file_manifest(self.module.archive_files(archive))
                with self.subTest(profile=profile, field=field), self.assertRaisesRegex(self.module.VerificationError, "complete all10 source validation"):
                    self.module.validate_archive(
                        archive, repository=REPOSITORY, head_sha=HEAD, head_tree=TREE,
                        run_id=RUN_ID, run_attempt=RUN_ATTEMPT, profile=profile, binding=self.binding(profile),
                    )

    def test_actual_sealer_keeps_committed_source_binding_after_isolated_fixture_setup(self) -> None:
        root, identity = self.seal_fixture("postgresql")
        self.sealer.seal_profile(root, **identity)
        source_root = self.sealer.SOURCE_ROOT
        lock_path = source_root / "migrations/MIGRATION_CHAIN.lock.json"
        lock = json.loads(lock_path.read_text())
        original_lock = lock_path.read_bytes()
        lock_path.write_text(json.dumps(lock) + "\n")
        self.assertNotEqual(lock_path.read_bytes(), original_lock)
        # Semantically valid lock reformatting changes real source bytes. It
        # cannot bypass the committed source check, while immutable SQL stays exact.
        self.module.MIGRATIONS.validate(source_root)
        with self.assertRaisesRegex(self.sealer.SealingError, "complete authoritative source differs from committed candidate"):
            self.sealer.seal_profile(root, **identity)

    def test_remote_binding_uses_complete_exact_head_tree_and_shared_lock_validator(self) -> None:
        lock_path = "migrations/MIGRATION_CHAIN.lock.json"
        files = {lock_path: (ROOT / lock_path).read_bytes(),
                 "config/database-test-images.json": (ROOT / "config/database-test-images.json").read_bytes()}
        lock = json.loads(files[lock_path])
        files.update({path:(ROOT/path).read_bytes() for path in self.module.BINDING.CONTROL_PATHS})
        entries = []
        for profile in self.module.PROFILES:
            for item in lock["profiles"][profile]["ordered_files"]:
                files[item["path"]] = (ROOT / item["path"]).read_bytes()
                entries.append({"path": item["path"], "mode": "100644", "type": "blob"})
        entries=[{"path":path,"mode":"100644","type":"blob","sha":self.module.git_blob_sha1(payload),"size":len(payload)} for path,payload in files.items()]
        tree = {"sha": TREE, "truncated": False, "tree": entries}

        responses = []
        class Response(io.BytesIO):
            def __init__(self, body):
                super().__init__(body)
                self.headers = {"Content-Length": str(len(body))}
                self.read_sizes = []
            def read(self, size=-1):
                self.read_sizes.append(size)
                return super().read(size)

        def transport(document):
            def open_mock(request, timeout):
                self.assertEqual(timeout, self.module.SOURCE_HTTP_TIMEOUT_SECONDS)
                url = urllib.parse.urlsplit(request.full_url)
                if url.path.endswith("/git/trees/" + TREE):
                    payload = document
                else:
                    self.assertEqual(urllib.parse.parse_qs(url.query), {"ref": [HEAD]})
                    path = urllib.parse.unquote(url.path.split("/contents/", 1)[1])
                    raw = files[path]
                    payload = {"path": path, "type": "file", "encoding": "base64", "size": len(raw),
                               "sha": self.module.git_blob_sha1(raw), "content": base64.b64encode(raw).decode()}
                response = Response(json.dumps(payload).encode())
                responses.append(response)
                return response
            return open_mock

        with mock_source_process(self.module.SOURCE_HTTP), mock.patch.object(self.module.SOURCE_HTTP, "open_source_url", side_effect=transport(tree)):
            bindings = self.module.fetch_profile_bindings("fixture-token", REPOSITORY, HEAD, head_tree=TREE)
            for profile, token in bindings.items():
                binding=self.module.BINDING.operational_binding(token)
                self.assertEqual(binding["chain_digest"], self.binding_view(profile)["chain_digest"])
                self.assertEqual(len(binding["ordered_files"]), 4)
                self.assertEqual(binding["schema_version"], "4")
                self.assertEqual(binding["storage_writer_epoch"], "4")
                self.assertEqual(binding["digest_algorithm"], "ordered-path-git-blob-sha256.v1")
            for mutation in ("truncated", "unlisted", "symlink", "short_lock"):
                candidate_tree = copy.deepcopy(tree)
                original_lock = files[lock_path]
                if mutation == "truncated":
                    candidate_tree["truncated"] = True
                elif mutation == "unlisted":
                    candidate_tree["tree"].append({"path": "migrations/postgresql/0003_unlisted_up.sql", "type": "blob", "mode": "100644"})
                elif mutation == "symlink":
                    candidate_tree["tree"][0]["mode"] = "120000"
                else:
                    candidate_lock = copy.deepcopy(lock)
                    candidate_lock["profiles"]["postgresql"]["ordered_files"].pop()
                    files[lock_path] = json.dumps(candidate_lock).encode()
                with self.subTest(mutation=mutation), \
                        mock.patch.object(self.module.SOURCE_HTTP, "open_source_url", side_effect=transport(candidate_tree)), \
                        self.assertRaises(self.module.VerificationError):
                    self.module.fetch_profile_bindings("fixture-token", REPOSITORY, HEAD, head_tree=TREE)
                files[lock_path] = original_lock
        self.assertTrue(all(response.closed for response in responses))
        self.assertTrue(all(response.read_sizes in ([self.module.MAX_SOURCE_FILE_JSON_BYTES + 1],
                                                   [self.module.MAX_SOURCE_TREE_JSON_BYTES + 1]) for response in responses))

    def test_workflow_identity_is_exact_and_numeric(self) -> None:
        workflow = {
            "id": 42,
            "name": self.module.WORKFLOW_NAME,
            "path": self.module.WORKFLOW_PATH,
            "state": "active",
        }
        self.module.validate_workflow(workflow)
        for key, value in (
            ("id", 0),
            ("name", "wrong"),
            ("path", ".github/workflows/wrong.yml"),
            ("state", "disabled_manually"),
        ):
            invalid = dict(workflow)
            invalid[key] = value
            with self.subTest(key=key):
                with self.assertRaises(self.module.VerificationError):
                    self.module.validate_workflow(invalid)

    def test_run_identity_rejects_wrong_workflow_attempt_tree_and_partial_run(self) -> None:
        workflow_id = 42
        exact = self.workflow_run(workflow_id, current=False)
        self.module.validate_run(
            exact,
            repository=REPOSITORY,
            head_sha=HEAD,
            head_tree=TREE,
            run_attempt=RUN_ATTEMPT,
            workflow_id=workflow_id,
            current=False,
        )
        mutations = [
            ("workflow_id", 43),
            ("run_attempt", 3),
            ("status", "in_progress"),
            ("conclusion", "failure"),
        ]
        for key, value in mutations:
            invalid = copy.deepcopy(exact)
            invalid[key] = value
            with self.subTest(key=key):
                with self.assertRaises(self.module.VerificationError):
                    self.module.validate_run(
                        invalid,
                        repository=REPOSITORY,
                        head_sha=HEAD,
                        head_tree=TREE,
                        run_attempt=RUN_ATTEMPT,
                        workflow_id=workflow_id,
                        current=False,
                    )
        wrong_tree = copy.deepcopy(exact)
        wrong_tree["head_commit"]["tree_id"] = "e" * 40
        with self.assertRaises(self.module.VerificationError):
            self.module.validate_run(
                wrong_tree,
                repository=REPOSITORY,
                head_sha=HEAD,
                head_tree=TREE,
                run_attempt=RUN_ATTEMPT,
                workflow_id=workflow_id,
                current=False,
            )

    def test_closed_world_job_sets_accept_completed_and_current_modes(self) -> None:
        completed = self.module.validate_job_set(self.completed_jobs(), current=False)
        self.assertEqual(set(completed), self.module.EXPECTED_JOB_NAMES)
        current = self.module.validate_job_set(self.current_jobs(), current=True)
        self.assertEqual(current[self.module.FINAL_JOB]["status"], "in_progress")

    def test_jobs_reject_duplicate_injected_zero_runner_empty_steps_and_failure(self) -> None:
        cases: list[list[dict[str, Any]]] = []
        duplicate = self.completed_jobs()
        duplicate[-1]["name"] = duplicate[0]["name"]
        cases.append(duplicate)
        injected = self.completed_jobs() + [self.job("injected", 5)]
        cases.append(injected)
        zero_runner = self.completed_jobs()
        zero_runner[1]["runner_id"] = 0
        cases.append(zero_runner)
        empty_steps = self.completed_jobs()
        empty_steps[1]["steps"] = []
        cases.append(empty_steps)
        failed = self.completed_jobs()
        failed[1]["conclusion"] = "failure"
        cases.append(failed)
        duplicate_step = self.completed_jobs()
        duplicate_step[1]["steps"] = [self.step(1), self.step(1)]
        cases.append(duplicate_step)
        for jobs in cases:
            with self.subTest():
                with self.assertRaises(self.module.VerificationError):
                    self.module.validate_job_set(jobs, current=False)

    def test_git_blob_identity_is_canonical(self) -> None:
        self.assertEqual(
            self.module.git_blob_sha1(b"test content\n"),
            "d670460b4b4aece5915caf5c68d12f560a9fe3e4",
        )


if __name__ == "__main__":
    unittest.main()
