from __future__ import annotations

import contextlib
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "trnm_required_workflow_trigger_contract", ROOT / "scripts/workflow_trigger_contract.py",
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load the required-workflow trigger contract")
TRIGGER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = TRIGGER
SPEC.loader.exec_module(TRIGGER)


class RequiredWorkflowTriggerPureTests(unittest.TestCase):
    def test_unfiltered_mapping_is_unchanged(self):
        text = "name: test\non:\n  pull_request:\n  push:\n    branches: [main]\njobs: {}\n"
        self.assertEqual(TRIGGER.remove_required_pr_selectors(text), (text, ()))

    def test_scalar_and_flow_forms_are_unfiltered(self):
        for value in ("pull_request", "[push, pull_request, workflow_dispatch]"):
            with self.subTest(value=value):
                TRIGGER.validate_required_pr_trigger(f"name: test\non: {value}\njobs: {{}}\n")

    def test_paths_are_rejected_then_removed_without_changing_push_or_jobs(self):
        text = "name: test\non:\n  pull_request:\n    paths:\n      - 'src/**'\n  push:\n    paths:\n      - 'src/**'\njobs:\n  test:\n    run: echo paths\n"
        with self.assertRaises(TRIGGER.TriggerContractError):
            TRIGGER.validate_required_pr_trigger(text)
        result, removed = TRIGGER.remove_required_pr_selectors(text)
        self.assertEqual(removed, ("paths",))
        self.assertEqual(result, text.replace("    paths:\n      - 'src/**'\n", "", 1))
        TRIGGER.validate_required_pr_trigger(result)

    def test_branch_and_path_ignores_are_not_substitutes_for_unfiltered(self):
        text = "name: test\non:\n  pull_request:\n    branches-ignore: [archive]\n    paths-ignore: ['docs/**']\n    branches: [main]\n    types: [opened, synchronize, reopened, edited]\njobs: {}\n"
        result, removed = TRIGGER.remove_required_pr_selectors(text)
        self.assertEqual(set(removed), {"branches-ignore", "paths-ignore", "branches"})
        self.assertIn("types: [opened, synchronize, reopened, edited]", result)
        TRIGGER.validate_required_pr_trigger(result)

    def test_multiline_types_are_preserved(self):
        text = "on:\n  pull_request:\n    types:\n      - opened\n      - synchronize\n      - reopened\njobs: {}\n"
        self.assertEqual(TRIGGER.remove_required_pr_selectors(text), (text, ()))

    def test_duplicate_on_or_pr_keys_reject(self):
        for text in ("on:\n  pull_request:\non:\n  pull_request:\n", "on:\n  pull_request:\n  pull_request:\n"):
            with self.subTest(text=text), self.assertRaises(TRIGGER.TriggerContractError):
                TRIGGER.remove_required_pr_selectors(text)

    def test_duplicate_selector_is_not_silently_normalized(self):
        text = "on:\n  pull_request:\n    paths: [a]\n    paths: [b]\njobs: {}\n"
        with self.assertRaises(TRIGGER.TriggerContractError):
            TRIGGER.remove_required_pr_selectors(text)

    def test_missing_or_target_event_is_not_pull_request(self):
        for text in ("on: push\n", "on:\n  pull_request_target:\n", "jobs:\n  x:\n    run: pull_request\n"):
            with self.subTest(text=text), self.assertRaises(TRIGGER.TriggerContractError):
                TRIGGER.validate_required_pr_trigger(text)

    def test_restricted_types_remain_a_failure_after_selector_removal(self):
        with self.assertRaises(TRIGGER.TriggerContractError):
            TRIGGER.remove_required_pr_selectors("on:\n  pull_request:\n    paths: [a]\n    types: [opened]\n")

    def test_complex_mapping_and_unknown_key_reject(self):
        for text in ("on:\n  pull_request: {paths: [a]}\n", "on:\n  pull_request:\n    custom: true\n", '"on":\n  pull_request:\n'):
            with self.subTest(text=text), self.assertRaises(TRIGGER.TriggerContractError):
                TRIGGER.remove_required_pr_selectors(text)

    def test_crlf_and_empty_inline_mapping_are_preserved(self):
        text = "on:\r\n  pull_request: {}\r\n  push:\r\n    branches: [main]\r\njobs: {}\r\n"
        self.assertEqual(TRIGGER.remove_required_pr_selectors(text), (text, ()))

    def test_bound_rejects_oversized_source(self):
        with self.assertRaises(TRIGGER.TriggerContractError):
            TRIGGER.validate_required_pr_trigger("x" * (TRIGGER.MAX_BYTES + 1))


class RequiredWorkflowRepositoryIntegrationTests(unittest.TestCase):
    def manifest(self):
        # Import repository code only in native read-only qualification, never
        # in the write-capable, trusted source-materialization helper.
        from tests.control_plane.test_workflow_gate_integrity import GATE
        with contextlib.chdir(ROOT):
            loader = getattr(GATE, "load_composed_manifest", GATE.Manifest.load)
            manifest = loader(Path("docs/governance/REQUIRED_WORKFLOWS_V1.json"))
        return GATE, manifest

    def test_every_current_definition_matches_its_registered_blob(self):
        gate, manifest = self.manifest()
        self.assertEqual(gate.verify_files(ROOT, manifest), [])

    def test_every_required_workflow_runs_for_unfiltered_pr_changes(self):
        _, manifest = self.manifest()
        for requirement in (manifest.aggregate, *manifest.workflows):
            with self.subTest(workflow=requirement.path):
                TRIGGER.validate_required_pr_trigger((ROOT / requirement.path).read_text())



if __name__ == "__main__":
    unittest.main()
