from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WRAPPER_PATH = ROOT / "scripts/verify-branch-inventory-event.py"
REPOSITORY = "TrillionniumFoundation/TrillionniumGame"
HEAD_SHA = "a" * 40


def load_module():
    spec = importlib.util.spec_from_file_location(
        "branch_inventory_event_test_module", WRAPPER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BranchInventoryEventContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    @staticmethod
    def make_run(
        event: str,
        *,
        branch: str,
        pull_requests: list[object] | None = None,
    ) -> dict[str, object]:
        return {
            "id": 123,
            "repository": {"full_name": REPOSITORY},
            "head_sha": HEAD_SHA,
            "run_attempt": 2,
            "event": event,
            "name": "branch-inventory",
            "path": ".github/workflows/branch-inventory.yml",
            "status": "completed",
            "conclusion": "success",
            "head_branch": branch,
            "pull_requests": [] if pull_requests is None else pull_requests,
        }

    def validate(self, run: dict[str, object]) -> None:
        self.module.validate_event_scoped_run(
            run,
            repository=REPOSITORY,
            head_sha=HEAD_SHA,
            run_id="123",
            run_attempt="2",
        )

    def test_pull_request_retains_original_contract(self) -> None:
        self.validate(
            self.make_run(
                "pull_request",
                branch="feature/plan-v32-identity-core-20260909",
                pull_requests=[{"number": 163}],
            )
        )

    def test_protected_main_push_is_accepted(self) -> None:
        self.validate(self.make_run("push", branch="main"))

    def test_main_workflow_dispatch_is_accepted(self) -> None:
        self.validate(self.make_run("workflow_dispatch", branch="main"))

    def test_non_main_push_or_dispatch_is_rejected(self) -> None:
        for event in ("push", "workflow_dispatch"):
            with self.subTest(event=event):
                with self.assertRaisesRegex(
                    self.module.BASE.VerificationError,
                    "must target main",
                ):
                    self.validate(self.make_run(event, branch="feature/not-main"))

    def test_push_attached_to_pull_request_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            self.module.BASE.VerificationError,
            "must not be attached",
        ):
            self.validate(
                self.make_run(
                    "push",
                    branch="main",
                    pull_requests=[{"number": 163}],
                )
            )

    def test_unregistered_event_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            self.module.BASE.VerificationError,
            "workflow event must be",
        ):
            self.validate(self.make_run("schedule", branch="main"))


if __name__ == "__main__":
    unittest.main()
