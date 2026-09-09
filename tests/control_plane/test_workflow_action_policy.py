from __future__ import annotations

import contextlib
import importlib.util
import io
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check-workflow-action-policy.py"


class WorkflowActionPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location(
            "check_workflow_action_policy", SCRIPT
        )
        assert spec is not None and spec.loader is not None
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_repository_local_actions_and_reusable_workflows_are_allowed(self) -> None:
        self.assertTrue(self.module.allowed_use("./.github/actions/local"))
        self.assertTrue(self.module.allowed_use("./.github/workflows/local.yml"))

    def test_every_external_action_form_is_rejected(self) -> None:
        for value in (
            "actions/upload-artifact@043fb460e6257d1ca154e89a5e86196c74e480f8",
            "actions/upload-artifact@v7",
            "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683",
            "third-party/example@043fb460e6257d1ca154e89a5e86196c74e480f8",
            "owner/repository/.github/workflows/reusable.yml@main",
        ):
            with self.subTest(value=value):
                self.assertFalse(self.module.allowed_use(value))

    def test_exact_prospective_merge_profile_is_accepted(self) -> None:
        source = """name: prospective-merge-gate
on:
  pull_request:
env:
  PR_NUMBER: ${{ github.event.pull_request.number }}
  SOURCE_BASE_SHA: ${{ github.event.pull_request.base.sha }}
  SOURCE_HEAD_SHA: ${{ github.event.pull_request.head.sha }}
  PROSPECTIVE_MERGE_SHA: ${{ github.sha }}
jobs:
  check:
    runs-on: ubuntu-24.04
    steps:
      - name: fetch
        run: |
          rm -rf "$GITHUB_WORKSPACE"
          git init "$GITHUB_WORKSPACE"
          git -C "$GITHUB_WORKSPACE" fetch origin "+refs/pull/${PR_NUMBER}/merge:refs/remotes/origin/prospective-merge"
          git -C "$GITHUB_WORKSPACE" checkout --detach refs/remotes/origin/prospective-merge
          cd "$GITHUB_WORKSPACE"
          test "$(git -C "$GITHUB_WORKSPACE" rev-parse HEAD)" = "$PROSPECTIVE_MERGE_SHA"
          python3 scripts/check-prospective-merge-identity.py
"""
        self.assertEqual(
            self.module.prospective_merge_fetch_failures(
                ".github/workflows/prospective-merge-gate.yml", source
            ),
            [],
        )

    def test_prospective_merge_profile_rejects_wrong_path_ref_and_sha(self) -> None:
        valid = """name: prospective-merge-gate
on:
  pull_request:
env:
  PR_NUMBER: ${{ github.event.pull_request.number }}
  SOURCE_BASE_SHA: ${{ github.event.pull_request.base.sha }}
  SOURCE_HEAD_SHA: ${{ github.event.pull_request.head.sha }}
  PROSPECTIVE_MERGE_SHA: ${{ github.sha }}
jobs:
  check:
    runs-on: ubuntu-24.04
    steps:
      - name: fetch
        run: |
          rm -rf "$GITHUB_WORKSPACE"
          git init "$GITHUB_WORKSPACE"
          git -C "$GITHUB_WORKSPACE" fetch origin "+refs/pull/${PR_NUMBER}/merge:refs/remotes/origin/prospective-merge"
          git -C "$GITHUB_WORKSPACE" checkout --detach refs/remotes/origin/prospective-merge
          cd "$GITHUB_WORKSPACE"
          test "$(git -C "$GITHUB_WORKSPACE" rev-parse HEAD)" = "$PROSPECTIVE_MERGE_SHA"
          python3 scripts/check-prospective-merge-identity.py
"""
        wrong_path = self.module.prospective_merge_fetch_failures(
            ".github/workflows/not-the-authority.yml", valid
        )
        self.assertTrue(any("allowed only" in failure for failure in wrong_path))

        wrong_ref = valid.replace(
            "+refs/pull/${PR_NUMBER}/merge:refs/remotes/origin/prospective-merge",
            "refs/heads/main",
        )
        self.assertTrue(
            any(
                "exact merge ref" in failure
                for failure in self.module.prospective_merge_fetch_failures(
                    ".github/workflows/prospective-merge-gate.yml", wrong_ref
                )
            )
        )

        wrong_sha = valid.replace(
            '"$PROSPECTIVE_MERGE_SHA"', '"$SOURCE_HEAD_SHA"'
        )
        self.assertTrue(
            any(
                "checked-out merge assertion" in failure
                for failure in self.module.prospective_merge_fetch_failures(
                    ".github/workflows/prospective-merge-gate.yml", wrong_sha
                )
            )
        )

    def test_repository_policy_passes(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = self.module.main()
        self.assertEqual(status, 0, stderr.getvalue())
        self.assertIn("workflow action policy: OK", stdout.getvalue())
        self.assertIn("no external actions", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
