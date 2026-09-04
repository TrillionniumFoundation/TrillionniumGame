from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {relative}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


POLICY = load("trnm_workflow_policy", "scripts/check-workflow-action-policy.py")
GATE = load("trnm_required_runs", "scripts/check-required-workflow-runs.py")
SHA = "a" * 40


def raw(run_id=1, workflow_id=10, name="database", path=".github/workflows/database.yml",
        status="completed", conclusion="success", head_sha=SHA, event="pull_request",
        run_attempt=1):
    return {"id": run_id, "run_attempt": run_attempt, "workflow_id": workflow_id,
            "name": name, "path": path, "status": status, "conclusion": conclusion,
            "head_sha": head_sha, "event": event,
            "html_url": f"https://example.test/runs/{run_id}"}


def requirement(workflow_id=10, name="database", path=".github/workflows/database.yml",
                sha="a" * 40):
    return GATE.Requirement(workflow_id, name, path, sha, ("pull_request",), 1)


def manifest(*items, reject=True):
    return GATE.Manifest("owner/repo", "pull_request",
                         requirement(99, "aggregate", ".github/workflows/aggregate.yml", "0" * 40),
                         reject, tuple(items))


class ExistingWorkflowStructureTests(unittest.TestCase):
    def test_duplicate_root_key(self):
        text = "name: x\non:\n  pull_request:\nenv:\n  A: 1\nenv:\n  B: 2\njobs:\n  x:\n    runs-on: ubuntu-24.04\n    steps: []\n"
        self.assertTrue(any("duplicate mapping key 'env'" in x for x in POLICY.workflow_structure_failures(text)))

    def test_sequence_items_and_block_scalar(self):
        text = """name: valid
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
        self.assertEqual(POLICY.workflow_structure_failures(text), [])

    def test_prospective_jobs_enter_workspace(self):
        text = (ROOT / ".github/workflows/prospective-merge-gate.yml").read_text()
        blocks = text.split('          rm -rf "$GITHUB_WORKSPACE"\n')[1:]
        self.assertEqual(len(blocks), 6)
        for block in blocks:
            block = block.split("\n      - name:", 1)[0]
            checkout = block.find('git -C "$GITHUB_WORKSPACE" checkout --detach')
            enter = block.find('cd "$GITHUB_WORKSPACE"')
            self.assertGreaterEqual(checkout, 0)
            self.assertGreater(enter, checkout)


class ClosedSetSelectionTests(unittest.TestCase):
    def test_latest_attempt_wins(self):
        runs = GATE.latest_runs([
            raw(1, status="completed", conclusion="failure", run_attempt=1),
            raw(1, status="completed", conclusion="success", run_attempt=2),
            raw(2, workflow_id=99, name="aggregate", path=".github/workflows/aggregate.yml",
                status="in_progress", conclusion=None),
            raw(3, workflow_id=12, head_sha="b" * 40),
        ], head_sha=SHA, event="pull_request", excluded_workflow_id=99)
        self.assertEqual([(x.id, x.attempt) for x in runs], [(1, 2)])

    def test_missing_cannot_be_replaced(self):
        selected, failures = GATE.select_runs([
            raw(7, 20, "substitute", ".github/workflows/substitute.yml")
        ], manifest(requirement()), SHA)
        self.assertEqual(selected, [])
        self.assertTrue(any("required workflow is missing" in x for x in failures))
        self.assertTrue(any("unlisted exact-head workflow" in x for x in failures))

    def test_name_and_path_drift(self):
        selected, failures = GATE.select_runs([
            raw(8, 10, "renamed", ".github/workflows/replacement.yml")
        ], manifest(requirement()), SHA)
        self.assertEqual(len(selected), 1)
        self.assertTrue(any("name mismatch" in x for x in failures))
        self.assertTrue(any("path mismatch" in x for x in failures))

    def test_terminal_failure_and_empty_collection(self):
        run = GATE.Run.parse(raw(9, conclusion="cancelled"))
        _, failed, failures = GATE.classify([run], 1)
        self.assertEqual([x.id for x in failed], [9])
        self.assertTrue(any("cancelled" in x for x in failures))
        self.assertTrue(GATE.classify([], 1)[2])


class JobEvidenceTests(unittest.TestCase):
    def good(self):
        return {"name": "verify", "status": "completed", "conclusion": "success",
                "steps": [{"name": "Set up job", "status": "completed", "conclusion": "success"},
                          {"name": "Run invariant tests", "status": "completed", "conclusion": "success"},
                          {"name": "Complete job", "status": "completed", "conclusion": "success"}]}

    def test_zero_jobs(self):
        self.assertIn("workflow has zero jobs", GATE.job_failures([]))

    def test_all_skipped(self):
        failures = GATE.job_failures([
            {"name": "conditional", "status": "completed", "conclusion": "skipped", "steps": []}
        ])
        self.assertTrue(any("too few" in x for x in failures))

    def test_zero_meaningful_steps(self):
        failures = GATE.job_failures([
            {"name": "empty", "status": "completed", "conclusion": "success",
             "steps": [{"name": "Set up job", "status": "completed", "conclusion": "success"},
                       {"name": "Complete job", "status": "completed", "conclusion": "success"}]}
        ])
        self.assertTrue(any("no successful non-framework" in x for x in failures))

    def test_failed_job(self):
        self.assertTrue(any("not terminal-success" in x for x in GATE.job_failures([
            {"name": "tests", "status": "completed", "conclusion": "failure", "steps": []}
        ])))

    def test_good_job(self):
        self.assertEqual(GATE.job_failures([self.good()]), [])


class ManifestFileTests(unittest.TestCase):
    def fixture(self, root: Path):
        directory = root / ".github/workflows"
        directory.mkdir(parents=True)
        required = directory / "database.yml"
        aggregate = directory / "aggregate.yml"
        required.write_text("name: database\non: pull_request\njobs: {}\n")
        aggregate.write_text("name: aggregate\non: pull_request\njobs: {}\n")
        value = GATE.Manifest(
            "owner/repo", "pull_request",
            requirement(99, "aggregate", ".github/workflows/aggregate.yml", GATE.blob_sha(aggregate.read_bytes())),
            True,
            (requirement(10, "database", ".github/workflows/database.yml", GATE.blob_sha(required.read_bytes())),),
        )
        return value, required

    def test_exact_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            value, _ = self.fixture(Path(tmp))
            self.assertEqual(GATE.verify_files(Path(tmp), value), [])

    def test_blob_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            value, required = self.fixture(Path(tmp))
            required.write_text("name: changed\n")
            self.assertTrue(any("definition drift" in x for x in GATE.verify_files(Path(tmp), value)))

    def test_unlisted_definition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            value, _ = self.fixture(root)
            (root / ".github/workflows/unlisted.yml").write_text("name: unlisted\n")
            self.assertTrue(any("unlisted" in x for x in GATE.verify_files(root, value)))

    def test_duplicate_manifest_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            value = {
                "schema": GATE.SCHEMA, "repository": "owner/repo", "event": "pull_request",
                "aggregate_workflow": {"workflow_id": 99, "name": "aggregate",
                    "path": ".github/workflows/aggregate.yml", "git_blob_sha1": "c" * 40,
                    "allowed_events": ["pull_request"]},
                "requirements": {"reject_unlisted_exact_head_workflows": True},
                "workflows": [
                    {"workflow_id": 10, "name": "one", "path": ".github/workflows/one.yml",
                     "git_blob_sha1": "a" * 40, "allowed_events": ["pull_request"]},
                    {"workflow_id": 10, "name": "two", "path": ".github/workflows/two.yml",
                     "git_blob_sha1": "b" * 40, "allowed_events": ["pull_request"]},
                ],
            }
            path.write_text(__import__("json").dumps(value))
            with self.assertRaisesRegex(ValueError, "duplicate workflow_id"):
                GATE.Manifest.load(path)


if __name__ == "__main__":
    unittest.main()
