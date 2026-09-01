from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_script(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


WORKFLOW_POLICY = load_script(
    "trnm_check_workflow_action_policy",
    "scripts/check-workflow-action-policy.py",
)
REQUIRED_RUNS = load_script(
    "trnm_check_required_workflow_runs",
    "scripts/check-required-workflow-runs.py",
)


class WorkflowStructureTests(unittest.TestCase):
    def test_duplicate_root_mapping_key_is_rejected(self) -> None:
        source = """name: duplicate
on:
  pull_request:
env:
  FIRST: one
env:
  SECOND: two
jobs:
  check:
    runs-on: ubuntu-24.04
    steps: []
"""
        failures = WORKFLOW_POLICY.workflow_structure_failures(source)
        self.assertTrue(any("duplicate mapping key 'env'" in item for item in failures))

    def test_separate_sequence_items_and_block_scalar_are_not_duplicates(self) -> None:
        source = """name: valid
on:
  pull_request:
permissions:
  contents: read
jobs:
  check:
    runs-on: ubuntu-24.04
    steps:
      - name: first
        env:
          VALUE: one
        run: |
          printf 'env: not-a-key\\n'
      - name: second
        env:
          VALUE: two
        run: echo ok
"""
        self.assertEqual(WORKFLOW_POLICY.workflow_structure_failures(source), [])

    def test_duplicate_key_inside_one_step_is_rejected(self) -> None:
        source = """name: invalid-step
on:
  pull_request:
jobs:
  check:
    runs-on: ubuntu-24.04
    steps:
      - name: duplicate
        env:
          A: one
        env:
          B: two
        run: echo no
"""
        failures = WORKFLOW_POLICY.workflow_structure_failures(source)
        self.assertTrue(any("duplicate mapping key 'env'" in item for item in failures))

    def test_every_prospective_checkout_enters_recreated_workspace(self) -> None:
        source = (ROOT / ".github/workflows/prospective-merge-gate.yml").read_text(
            encoding="utf-8"
        )
        step_header = "      - name: Fetch the exact GitHub prospective merge object\n"
        blocks = source.split(step_header)[1:]
        self.assertEqual(
            len(blocks),
            6,
            "every prospective job must use the one reviewed checkout step",
        )
        for index, remainder in enumerate(blocks, 1):
            block = remainder.split("\n      - name:", 1)[0]
            checkout = block.find("git -C \"$GITHUB_WORKSPACE\" checkout --detach")
            enter = block.find('cd "$GITHUB_WORKSPACE"')
            relative_use = block.find("python3 scripts/")
            self.assertGreaterEqual(checkout, 0, f"checkout {index} is missing")
            self.assertGreater(
                enter,
                checkout,
                f"checkout {index} does not enter the recreated workspace",
            )
            if relative_use >= 0:
                self.assertLess(
                    enter,
                    relative_use,
                    f"checkout {index} uses a relative path before entering workspace",
                )


def raw_run(
    *,
    run_id: int,
    workflow_id: int,
    name: str,
    status: str,
    conclusion: str | None,
    head_sha: str,
    event: str = "pull_request",
) -> dict[str, object]:
    return {
        "id": run_id,
        "workflow_id": workflow_id,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "head_sha": head_sha,
        "event": event,
        "html_url": f"https://example.test/runs/{run_id}",
    }


class RequiredWorkflowRunTests(unittest.TestCase):
    SHA = "a" * 40

    def test_latest_exact_head_run_wins_and_current_workflow_is_excluded(self) -> None:
        values = [
            raw_run(
                run_id=1,
                workflow_id=10,
                name="database",
                status="completed",
                conclusion="failure",
                head_sha=self.SHA,
            ),
            raw_run(
                run_id=2,
                workflow_id=10,
                name="database",
                status="completed",
                conclusion="success",
                head_sha=self.SHA,
            ),
            raw_run(
                run_id=3,
                workflow_id=11,
                name="current",
                status="in_progress",
                conclusion=None,
                head_sha=self.SHA,
            ),
            raw_run(
                run_id=4,
                workflow_id=12,
                name="other-head",
                status="completed",
                conclusion="success",
                head_sha="b" * 40,
            ),
        ]
        selected = REQUIRED_RUNS.latest_runs(
            values,
            head_sha=self.SHA,
            event="pull_request",
            excluded_workflow_id=11,
        )
        self.assertEqual([run.id for run in selected], [2])

    def test_non_success_terminal_conclusion_fails(self) -> None:
        runs = [
            REQUIRED_RUNS.Run.from_json(
                raw_run(
                    run_id=7,
                    workflow_id=20,
                    name="security",
                    status="completed",
                    conclusion="cancelled",
                    head_sha=self.SHA,
                )
            )
        ]
        pending, failed, failures = REQUIRED_RUNS.classify(runs, 1)
        self.assertEqual(pending, [])
        self.assertEqual([run.id for run in failed], [7])
        self.assertTrue(any("cancelled" in item for item in failures))

    def test_pending_run_is_not_success(self) -> None:
        runs = [
            REQUIRED_RUNS.Run.from_json(
                raw_run(
                    run_id=8,
                    workflow_id=21,
                    name="realtime",
                    status="queued",
                    conclusion=None,
                    head_sha=self.SHA,
                )
            )
        ]
        pending, failed, failures = REQUIRED_RUNS.classify(runs, 1)
        self.assertEqual([run.id for run in pending], [8])
        self.assertEqual(failed, [])
        self.assertEqual(failures, [])

    def test_empty_collection_fails_minimum(self) -> None:
        pending, failed, failures = REQUIRED_RUNS.classify([], 1)
        self.assertEqual(pending, [])
        self.assertEqual(failed, [])
        self.assertTrue(any("too small" in item for item in failures))


if __name__ == "__main__":
    unittest.main()
