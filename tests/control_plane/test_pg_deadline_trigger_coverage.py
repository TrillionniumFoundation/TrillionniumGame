"""Trigger-source regressions, not PostgreSQL execution or gap acceptance."""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "trnm_pg_deadline_coverage_contract", ROOT / "scripts/workflow_trigger_contract.py"
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load the required workflow trigger contract")
TRIGGER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = TRIGGER
SPEC.loader.exec_module(TRIGGER)

PATHS = (
    "crates/trnm-persistence-pg/src/pool_parts/**",
    "crates/trnm-persistence-pg/src/bin/trnm_server/app.rs",
    "tests/control_plane/test_pg_cancellation_lifecycle.py",
)
FIXTURE = """name: pg-operation-deadline
on:
  pull_request:
  push:
    branches: [main]
    paths:
      - 'crates/trnm-persistence-pg/src/pool_parts/**'
      - 'crates/trnm-persistence-pg/src/bin/trnm_server/app.rs'
      - 'tests/control_plane/test_pg_cancellation_lifecycle.py'
  workflow_dispatch:
permissions:
  contents: read
jobs: {}
"""


class WorkflowCoveragePureTests(unittest.TestCase):
    def check(self, text: str) -> None:
        TRIGGER.validate_required_pr_and_main_paths(text, PATHS)

    def reject(self, before: str, after: str) -> None:
        self.check(FIXTURE)
        self.assertIn(before, FIXTURE)
        with self.assertRaises(TRIGGER.TriggerContractError):
            self.check(FIXTURE.replace(before, after, 1))

    def test_unfiltered_pr_and_explicit_main_paths_pass(self):
        self.check(FIXTURE)

    def test_crlf_comments_and_paired_quotes_pass(self):
        text = FIXTURE.replace("[main]", '["main"] # integrated branch')
        text = text.replace("    paths:\n", "    paths: # source paths\n      # preserved comment\n")
        text = text.replace("'" + PATHS[0] + "'", '"' + PATHS[0] + '"')
        self.check(text.replace("\n", "\r\n"))

    def test_pr_types_cover_required_lifecycle(self):
        self.check(FIXTURE.replace("  pull_request:\n", "  pull_request:\n    types: [opened, synchronize, reopened, edited]\n"))

    def test_additional_positive_main_paths_pass(self):
        self.check(FIXTURE.replace("  workflow_dispatch:", "      - 'scripts/**'\n  workflow_dispatch:"))

    def test_each_pr_selector_rejects_even_when_relevant(self):
        for key in ("paths", "paths-ignore", "branches", "branches-ignore"):
            with self.subTest(key=key):
                self.reject("  pull_request:\n", f"  pull_request:\n    {key}: ['**']\n")

    def test_missing_pr_rejects(self):
        self.reject("  pull_request:\n", "")

    def test_pr_target_does_not_substitute(self):
        self.reject("  pull_request:\n", "  pull_request_target:\n")

    def test_restricted_pr_activity_rejects(self):
        self.reject("  pull_request:\n", "  pull_request:\n    types: [opened]\n")

    def test_each_required_main_path_is_enforced(self):
        for path in PATHS:
            with self.subTest(path=path):
                self.reject(f"      - '{path}'\n", "")

    def test_missing_push_rejects(self):
        start = FIXTURE.index("  push:\n")
        end = FIXTURE.index("  workflow_dispatch:\n")
        self.reject(FIXTURE[start:end], "")

    def test_wrong_or_broadened_main_branch_rejects(self):
        for value in ("[other]", "[main, other]", "['*']", "[main, '!main']", "['main\"]"):
            with self.subTest(value=value):
                self.reject("[main]", value)

    def test_ignored_main_paths_cannot_substitute(self):
        self.reject("    paths:\n", "    paths-ignore:\n")

    def test_negative_main_filter_rejects(self):
        self.reject("  workflow_dispatch:", "      - '!crates/**'\n  workflow_dispatch:")

    def test_duplicate_push_event_rejects(self):
        self.reject("  workflow_dispatch:\n", "  push:\n  workflow_dispatch:\n")

    def test_duplicate_push_selector_rejects(self):
        self.reject("    paths:\n", "    branches: [main]\n    paths:\n")

    def test_duplicate_on_rejects(self):
        self.reject("permissions:\n", "on:\n  pull_request:\npermissions:\n")

    def test_duplicate_main_path_rejects(self):
        line = f"      - '{PATHS[0]}'\n"
        self.reject(line, line + line)

    def test_path_in_another_event_does_not_count(self):
        line = f"      - '{PATHS[0]}'\n"
        text = FIXTURE.replace(line, "", 1).replace("  workflow_dispatch:\n", "  workflow_dispatch:\n    paths:\n" + line)
        with self.assertRaises(TRIGGER.TriggerContractError):
            self.check(text)

    def test_path_in_comment_does_not_count(self):
        self.reject(f"      - '{PATHS[0]}'\n", f"      # - '{PATHS[0]}'\n")

    def test_push_text_in_job_does_not_count(self):
        start = FIXTURE.index("  push:\n")
        end = FIXTURE.index("  workflow_dispatch:\n")
        trigger = FIXTURE[start:end]
        text = FIXTURE.replace(trigger, "", 1).replace("jobs: {}", "jobs:\n  example:\n    run: |\n" + "".join("      " + line for line in trigger.splitlines(keepends=True)))
        with self.assertRaises(TRIGGER.TriggerContractError):
            self.check(text)

    def test_unsupported_push_forms_fail_closed(self):
        for old, new in (("  push:\n", "  push: *alias\n"),
                         ("    paths:\n", "    paths: ['**']\n"),
                         ("    branches: [main]\n", "    branches: [main]\n      - other\n")):
            with self.subTest(new=new):
                self.reject(old, new)

    def test_tab_and_malformed_pattern_reject(self):
        for value in ("\t    - '" + PATHS[0] + "'\n", "      - '../outside'\n",
                      "      - '/absolute'\n", "      - '" + PATHS[0] + '"\n'):
            with self.subTest(value=value):
                self.reject(f"      - '{PATHS[0]}'\n", value)

    def test_inline_events_do_not_prove_main_policy(self):
        with self.assertRaises(TRIGGER.TriggerContractError):
            self.check("on: [pull_request, push]\njobs: {}\n")

    def test_input_and_requirement_bounds_reject(self):
        for text in (None, "x" * (TRIGGER.MAX_BYTES + 1)):
            with self.subTest(kind=type(text).__name__), self.assertRaises(TRIGGER.TriggerContractError):
                self.check(text)
        for paths in ((), PATHS + (PATHS[0],), ("../outside",), ("/outside",), (None,)):
            with self.subTest(paths=paths), self.assertRaises(TRIGGER.TriggerContractError):
                TRIGGER.validate_required_pr_and_main_paths(FIXTURE, paths)


class CoverageWiringTests(unittest.TestCase):
    def test_both_source_validators_call_shared_contract(self):
        for relative in ("scripts/check-pg-operation-deadline.py",
                         "tests/control_plane/test_pg_cancellation_lifecycle.py"):
            with self.subTest(path=relative):
                tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
                validate = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "validate")
                calls = [n for n in ast.walk(validate) if isinstance(n, ast.Call)
                         and isinstance(n.func, ast.Attribute)
                         and n.func.attr == "validate_required_pr_and_main_paths"]
                self.assertEqual(len(calls), 1)
                self.assertEqual(ast.literal_eval(calls[0].args[1]), PATHS)

    def test_deadline_checker_imports_from_its_own_script_directory(self):
        path = ROOT / "scripts/check-pg-operation-deadline.py"
        spec = importlib.util.spec_from_file_location("trnm_deadline_import_test", path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        module.TRIGGER.validate_required_pr_and_main_paths(FIXTURE, PATHS)


class RepositoryCoverageTests(unittest.TestCase):
    def test_actual_deadline_workflow_covers_pr_and_main(self):
        text = (ROOT / ".github/workflows/pg-operation-deadline.yml").read_text(encoding="utf-8")
        TRIGGER.validate_required_pr_and_main_paths(text, PATHS)


if __name__ == "__main__":
    unittest.main()
