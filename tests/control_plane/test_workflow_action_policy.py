from __future__ import annotations

import contextlib
import importlib.util
import io
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check-workflow-action-policy.py"
EXACT_USE = "actions/upload-artifact@043fb460e6257d1ca154e89a5e86196c74e480f8"
OUTBOX_WORKFLOW = ".github/workflows/outbox-final-attempt-reaper.yml"


class WorkflowActionPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location(
            "check_workflow_action_policy", SCRIPT
        )
        assert spec is not None and spec.loader is not None
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_local_and_exact_first_party_action_are_allowed(self) -> None:
        self.assertTrue(
            self.module.allowed_use("./.github/actions/local", OUTBOX_WORKFLOW)
        )
        self.assertTrue(self.module.allowed_use(EXACT_USE, OUTBOX_WORKFLOW))

    def test_exact_action_is_bound_to_only_the_evidence_workflow(self) -> None:
        self.assertFalse(
            self.module.allowed_use(EXACT_USE, ".github/workflows/plan-contract.yml")
        )

    def test_mutable_near_miss_and_other_external_actions_are_rejected(self) -> None:
        self.assertFalse(
            self.module.allowed_use("actions/upload-artifact@v7", OUTBOX_WORKFLOW)
        )
        self.assertFalse(
            self.module.allowed_use(EXACT_USE[:-1] + "0", OUTBOX_WORKFLOW)
        )
        self.assertFalse(
            self.module.allowed_use(
                "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683",
                OUTBOX_WORKFLOW,
            )
        )
        self.assertFalse(
            self.module.allowed_use(
                "third-party/example@043fb460e6257d1ca154e89a5e86196c74e480f8",
                OUTBOX_WORKFLOW,
            )
        )

    def test_external_allowlist_is_exact_first_party_immutable_sha(self) -> None:
        for value, workflows in self.module.ALLOWED_EXTERNAL_USES.items():
            owner_repo, reference = value.rsplit("@", 1)
            self.assertTrue(owner_repo.startswith("actions/"))
            self.assertIsNotNone(re.fullmatch(r"[0-9a-f]{40}", reference))
            self.assertEqual(workflows, frozenset({OUTBOX_WORKFLOW}))

    def test_repository_policy_passes(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = self.module.main()
        self.assertEqual(status, 0, stderr.getvalue())
        self.assertIn("workflow action policy: OK", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
