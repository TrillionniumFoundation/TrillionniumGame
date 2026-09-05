from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check-prospective-merge-identity.py"
POLICY = ROOT / "scripts" / "status_transition_policy.py"
SPEC = importlib.util.spec_from_file_location("check_prospective_merge_identity", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class RepositoryFixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Prospective Merge Test")
        self.git("config", "user.email", "prospective-merge@example.invalid")
        self.git(
            "remote",
            "add",
            "origin",
            "https://github.com/TrillionniumFoundation/TrillionniumGame.git",
        )

        (self.root / "root.txt").write_text("root\n", encoding="utf-8")
        self.write_controls()
        policy = self.root / "scripts/status_transition_policy.py"
        policy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(POLICY, policy)
        policy.chmod(0o755)
        self.git("add", ".")
        self.git("commit", "-m", "root")
        self.root_commit = self.rev("HEAD")

        self.git("checkout", "-b", "feature")
        self.write_controls(workstream="ready", gap="ready", roadmap="ready", milestone="ready")
        (self.root / "feature.txt").write_text("feature\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "feature")
        self.head_commit = self.rev("HEAD")

        self.git("checkout", "main")
        (self.root / "base.txt").write_text("base\n", encoding="utf-8")
        self.git("add", "base.txt")
        self.git("commit", "-m", "base")
        self.base_commit = self.rev("HEAD")

        self.git("merge", "--no-ff", "feature", "-m", "prospective merge")
        self.merge_commit = self.rev("HEAD")
        self.merge_tree = self.rev("HEAD^{tree}")

    def write_controls(
        self,
        *,
        task_override: str | None = None,
        workstream: str = "planned",
        stage: str = "planned",
        gap: str = "open",
        roadmap: str = "planned",
        milestone: str = "planned",
    ) -> None:
        (self.root / "docs/status").mkdir(parents=True, exist_ok=True)
        (self.root / "docs/roadmap").mkdir(parents=True, exist_ok=True)
        execution = {
            "schema": "trillionnium.execution-status.v1",
            "default_task_state": "planned",
            "task_overrides": [] if task_override is None else [
                {"id": "TG-W0-001", "status": task_override}
            ],
            "workstreams": [{"id": "W0", "status": workstream}],
            "stage_gates": [{"id": "SG0", "status": stage}],
        }
        register = {
            "schema": "trillionnium.gap-register.v1",
            "gaps": [{"id": "GAP-P0-TEST-001", "status": gap}],
        }
        next_milestone = {
            "schema": "trillionnium.next-milestone.v1",
            "milestone_id": "M0-TEST",
            "status": milestone,
            "items": [{"id": "TG-V3-001", "status": roadmap}],
        }
        (self.root / "docs/status/EXECUTION_STATUS.json").write_text(
            json.dumps(execution), encoding="utf-8"
        )
        (self.root / "docs/status/GAP_REGISTER.json").write_text(
            json.dumps(register), encoding="utf-8"
        )
        (self.root / "docs/roadmap/NEXT_MILESTONE.json").write_text(
            json.dumps(next_milestone), encoding="utf-8"
        )

    def commit_bad_head(self, *, gap: str = "closed", remove_policy: bool = False) -> str:
        self.git("checkout", "-B", "bad-head", self.root_commit)
        if remove_policy:
            self.git("rm", "scripts/status_transition_policy.py")
        else:
            self.write_controls(gap=gap)
            self.git("add", "docs/status/GAP_REGISTER.json")
        self.git("commit", "-m", "bad head")
        result = self.rev("HEAD")
        self.git("checkout", "--detach", self.merge_commit)
        return result

    def git(self, *arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def rev(self, expression: str) -> str:
        return self.git("rev-parse", expression)

    def close(self) -> None:
        self.temporary.cleanup()


class ProspectiveMergeIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = RepositoryFixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def validate(self, **overrides: str):
        arguments = {
            "repository": "TrillionniumFoundation/TrillionniumGame",
            "expected_base": self.fixture.base_commit,
            "expected_head": self.fixture.head_commit,
            "expected_merge": self.fixture.merge_commit,
        }
        arguments.update(overrides)
        return MODULE.validate_identity(self.fixture.root, **arguments)

    def test_exact_base_first_head_second_merge_is_accepted(self) -> None:
        result = self.validate()
        self.assertEqual(result["status"], "verified")
        self.assertEqual(
            result["ordered_parents"],
            [self.fixture.base_commit, self.fixture.head_commit],
        )
        self.assertEqual(result["merge_tree"], self.fixture.merge_tree)
        self.assertTrue(result["base_first_head_second"])
        self.assertFalse(result["compatibility_credit"])
        self.assertFalse(result["production_ready"])

    def test_exact_parent_status_transition_is_verified(self) -> None:
        result = MODULE.validate_status_transitions(
            self.fixture.root,
            expected_base=self.fixture.base_commit,
            expected_head=self.fixture.head_commit,
        )
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["base_commit"], self.fixture.base_commit)
        self.assertEqual(result["head_commit"], self.fixture.head_commit)
        self.assertEqual(result["transition_count"], 4)
        self.assertEqual(len(result["policy_sha256"]), 64)
        self.assertFalse(result["claim_boundary"]["gap_closed"])

    def test_illegal_base_to_head_status_jump_is_rejected(self) -> None:
        bad_head = self.fixture.commit_bad_head(gap="closed")
        with self.assertRaisesRegex(MODULE.IdentityError, "status transition rejected"):
            MODULE.validate_status_transitions(
                self.fixture.root,
                expected_base=self.fixture.base_commit,
                expected_head=bad_head,
            )

    def test_head_without_transition_policy_is_rejected(self) -> None:
        bad_head = self.fixture.commit_bad_head(remove_policy=True)
        with self.assertRaisesRegex(MODULE.IdentityError, "git show"):
            MODULE.validate_status_transitions(
                self.fixture.root,
                expected_base=self.fixture.base_commit,
                expected_head=bad_head,
            )

    def test_merge_policy_must_equal_exact_head_policy(self) -> None:
        policy = self.fixture.root / "scripts/status_transition_policy.py"
        policy.write_text(policy.read_text(encoding="utf-8") + "\n# tracked mutation\n", encoding="utf-8")
        with self.assertRaisesRegex(MODULE.IdentityError, "merge policy differs"):
            MODULE.validate_status_transitions(
                self.fixture.root,
                expected_base=self.fixture.base_commit,
                expected_head=self.fixture.head_commit,
            )

    def test_cli_receipt_contains_transition_report(self) -> None:
        output = self.fixture.root / "run/identity.json"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--repository-root",
                str(self.fixture.root),
                "--repository",
                "TrillionniumFoundation/TrillionniumGame",
                "--expected-base",
                self.fixture.base_commit,
                "--expected-head",
                self.fixture.head_commit,
                "--expected-merge",
                self.fixture.merge_commit,
                "--output",
                str(output),
            ],
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(output.read_text(encoding="utf-8"))
        self.assertTrue(receipt["status_transition"]["status"] == "verified")
        self.assertEqual(receipt["status_transition"]["transition_count"], 4)

    def test_swapped_parent_expectations_are_rejected(self) -> None:
        with self.assertRaisesRegex(MODULE.IdentityError, "first parent"):
            self.validate(
                expected_base=self.fixture.head_commit,
                expected_head=self.fixture.base_commit,
            )

    def test_another_merge_identity_is_rejected(self) -> None:
        with self.assertRaisesRegex(MODULE.IdentityError, "HEAD mismatch"):
            self.validate(expected_merge=self.fixture.root_commit)

    def test_non_merge_head_is_rejected(self) -> None:
        self.fixture.git("checkout", "--detach", self.fixture.head_commit)
        with self.assertRaisesRegex(MODULE.IdentityError, "exactly two parents"):
            MODULE.validate_identity(
                self.fixture.root,
                repository="TrillionniumFoundation/TrillionniumGame",
                expected_base=self.fixture.base_commit,
                expected_head=self.fixture.root_commit,
                expected_merge=self.fixture.head_commit,
            )

    def test_invalid_sha_is_rejected_before_git_use(self) -> None:
        with self.assertRaisesRegex(MODULE.IdentityError, "40 lowercase"):
            self.validate(expected_head="NOT-A-SHA")

    def test_foreign_origin_is_rejected(self) -> None:
        self.fixture.git(
            "remote",
            "set-url",
            "origin",
            "https://github.com/example/foreign.git",
        )
        with self.assertRaisesRegex(MODULE.IdentityError, "origin repository mismatch"):
            self.validate()

    def test_tracked_worktree_mutation_is_rejected(self) -> None:
        (self.fixture.root / "root.txt").write_text("mutated\n", encoding="utf-8")
        with self.assertRaisesRegex(MODULE.IdentityError, "tracked changes"):
            self.validate()

    def test_octopus_merge_is_rejected(self) -> None:
        self.fixture.git("checkout", "main")
        self.fixture.git("checkout", "-b", "feature-two", self.fixture.root_commit)
        (self.fixture.root / "feature-two.txt").write_text("feature-two\n", encoding="utf-8")
        self.fixture.git("add", "feature-two.txt")
        self.fixture.git("commit", "-m", "feature two")
        feature_two = self.fixture.rev("HEAD")
        self.fixture.git("checkout", "main")
        self.fixture.git("reset", "--hard", self.fixture.base_commit)
        self.fixture.git("merge", "--no-ff", "feature", "feature-two", "-m", "octopus")
        octopus = self.fixture.rev("HEAD")
        with self.assertRaisesRegex(MODULE.IdentityError, "exactly two parents"):
            MODULE.validate_identity(
                self.fixture.root,
                repository="TrillionniumFoundation/TrillionniumGame",
                expected_base=self.fixture.base_commit,
                expected_head=feature_two,
                expected_merge=octopus,
            )


if __name__ == "__main__":
    unittest.main()
