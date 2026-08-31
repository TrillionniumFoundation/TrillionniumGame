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
