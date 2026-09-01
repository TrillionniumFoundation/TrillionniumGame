from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check-prospective-merge-identity.py"
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
        self.git("add", "root.txt")
        self.git("commit", "-m", "root")
        self.root_commit = self.rev("HEAD")

        self.git("checkout", "-b", "feature")
        (self.root / "feature.txt").write_text("feature\n", encoding="utf-8")
        self.git("add", "feature.txt")
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

    def test_swapped_parent_expectations_are_rejected(self) -> None:
        with self.assertRaisesRegex(MODULE.IdentityError, "first parent"):
            self.validate(
                expected_base=self.fixture.head_commit,
                expected_head=self.fixture.base_commit,
            )

    def test_another_merge_identity_is_rejected(self) -> None:
        with self.assertRaisesRegex(MODULE.IdentityError, "HEAD mismatch"):
            self.validate(expected_merge=self.fixture.base_commit)

    def test_non_merge_head_is_rejected(self) -> None:
        self.fixture.git("checkout", "--detach", self.fixture.head_commit)
        with self.assertRaisesRegex(MODULE.IdentityError, "exactly two parents"):
            self.validate(expected_merge=self.fixture.head_commit)

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
