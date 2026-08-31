from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import tarfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
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

    @staticmethod
    def binding(profile: str) -> dict[str, str]:
        migration = f"migrations/{profile}/0001_foundation_up.sql"
        return {
            "migration": migration,
            "migration_blob_sha1": "c" * 40,
            "image": f"example.invalid/{profile}@sha256:{'d' * 64}",
        }

    @classmethod
    def archive(
        cls,
        profile: str,
        *,
        identity_overrides: dict[str, str] | None = None,
        result_overrides: dict[str, str] | None = None,
        manifest_self_reference: bool = False,
    ) -> bytes:
        binding = cls.binding(profile)
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
            "migration": binding["migration"],
            "migration_blob_sha1": binding["migration_blob_sha1"],
        }
        identity.update(identity_overrides or {})
        result = {
            "status": "passed",
            "profile": profile,
            "commit": HEAD,
            "tree": TREE,
        }
        result.update(result_overrides or {})
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
        }
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
    def step(number: int, *, status: str = "completed", conclusion: Any = "success") -> dict[str, Any]:
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
    def run(cls, workflow_id: int, *, current: bool) -> dict[str, Any]:
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
            self.assertFalse(record["production_ready"])
            self.assertFalse(record["compatibility_credit"])

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
                    identity_overrides={"migration_blob_sha1": "e" * 40},
                ),
                binding,
            ),
            (
                self.archive(
                    profile,
                    identity_overrides={
                        "migration": "migrations/cockroachdb/0001_foundation_up.sql"
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
        exact = self.run(workflow_id, current=False)
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
