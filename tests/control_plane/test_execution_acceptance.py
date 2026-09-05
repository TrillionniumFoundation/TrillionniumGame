"""Current acceptance claims must not outrun retained evidence and dependencies.

Synthetic positive fixtures exercise admission only; they are not real approvals.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("missing test module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


FIXTURES = load("execution_acceptance_fixtures", Path(__file__).with_name("test_evidence_admission.py"))
CHECK = load("execution_acceptance_checker", ROOT / "scripts/check-status-transitions.py")
TARGET = {"repository": "TrillionniumFoundation/TrillionniumGame", "commit": "a" * 40, "tree": "b" * 40}


class AcceptedTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = FIXTURES.RetainedFixture(self.root)
        # Timeless synthetic fixture; the shared admission module separately
        # tests expiry boundaries. Do not make this suite fail next calendar day.
        self.fixture.entry["expires_at"] = None
        self.fixture.manifest["expires_at"] = None
        self.fixture.write()
        self.row = {
            "id": "TG-W0-001", "status": "accepted", "blocking_gaps": [],
            "acceptance_target": copy.deepcopy(TARGET),
            "required_evidence": ["manifest"],
            "evidence_ids": [self.fixture.entry["evidence_id"]],
        }
        self.evidence = {self.fixture.entry["evidence_id"]: self.fixture.entry}
        self.accepted = set(self.evidence)
        self.gaps = {}
        self.dependencies = []
        self.tasks = {self.row["id"]: self.row}
        self.patcher = patch.object(CHECK, "ROOT", self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def check(self):
        return CHECK.validate_accepted_task(self.row, "blocking_gaps", self.dependencies,
                                            self.tasks, self.gaps, self.evidence, self.accepted)

    def test_complete_retained_fixture_is_accepted(self):
        self.assertEqual(self.check(), tuple(TARGET.values()))

    def test_nonaccepted_source_is_not_promoted(self):
        self.row = {"id": "TG-W0-001", "status": "source-candidate"}
        self.assertIsNone(self.check())

    def test_open_blocking_gap_rejects(self):
        self.gaps = {"GAP-P0-TEST": {"status": "source-candidate"}}
        self.row["blocking_gaps"] = ["GAP-P0-TEST"]
        with self.assertRaisesRegex(CHECK.ValidationError, "open blocking gap"):
            self.check()

    def test_unknown_blocking_gap_rejects(self):
        self.row["blocking_gaps"] = ["GAP-P0-UNKNOWN"]
        with self.assertRaisesRegex(CHECK.ValidationError, "unknown blocking gap"):
            self.check()

    def test_unaccepted_dependency_rejects(self):
        self.dependencies = ["TG-W0-002"]
        self.tasks["TG-W0-002"] = {"status": "source-candidate"}
        with self.assertRaisesRegex(CHECK.ValidationError, "not accepted"):
            self.check()

    def test_dependency_target_cannot_be_substituted(self):
        self.dependencies = ["TG-W0-002"]
        self.tasks["TG-W0-002"] = {"status": "accepted", "acceptance_target": {**TARGET, "tree": "c" * 40}}
        with self.assertRaisesRegex(CHECK.ValidationError, "another candidate"):
            self.check()

    def test_declared_closed_gap_and_same_target_dependency_can_pass(self):
        self.gaps = {"GAP-P0-TEST": {"status": "closed"}}
        self.row["blocking_gaps"] = ["GAP-P0-TEST"]
        self.dependencies = ["TG-W0-002"]
        self.tasks["TG-W0-002"] = {"status": "accepted", "acceptance_target": TARGET}
        # Whole-repository validation independently validates each referenced
        # closed gap and each dependency's evidence; this unit tests conjunction.
        self.assertEqual(self.check(), tuple(TARGET.values()))

    def test_missing_or_unknown_evidence_rejects(self):
        for ids in (None, [], ["TG-EV-UNKNOWN"]):
            with self.subTest(ids=ids):
                self.row["evidence_ids"] = ids
                with self.assertRaises(CHECK.ValidationError):
                    self.check()

    def test_unaccepted_index_entry_rejects(self):
        self.accepted = set()
        with self.assertRaisesRegex(CHECK.ValidationError, "accepted indexed evidence"):
            self.check()

    def test_claimed_accepted_id_is_not_a_validation_bypass(self):
        self.fixture.entry["status"] = "diagnostic"
        with self.assertRaisesRegex(CHECK.ValidationError, "invalid retained evidence"):
            self.check()

    def add_unit_evidence(self, *, different_target=False):
        row = copy.deepcopy(self.fixture.entry)
        manifest = copy.deepcopy(self.fixture.manifest)
        row.update(evidence_id="TG-EV-FIXTURE-RETAINED-UNIT", evidence_type="unit",
                   path="docs/evidence/fixture-unit.json")
        manifest.update(evidence_id=row["evidence_id"], evidence_type="unit")
        if different_target:
            row["target"]["tree"] = "c" * 40
            manifest["candidate"]["tree"] = "c" * 40
            row["independent_review"]["reviewed_tree"] = "c" * 40
            manifest["review"]["reviewed_tree"] = "c" * 40
        (self.root / row["path"]).write_text(json.dumps(manifest))
        self.evidence[row["evidence_id"]] = row
        self.accepted.add(row["evidence_id"])
        self.row["evidence_ids"].append(row["evidence_id"])
        self.row["required_evidence"] = ["manifest", "unit"]

    def test_complete_multiple_evidence_types_same_target_pass(self):
        self.add_unit_evidence()
        self.assertEqual(self.check(), tuple(TARGET.values()))

    def test_mixed_target_types_cannot_complete_acceptance(self):
        self.add_unit_evidence(different_target=True)
        with self.assertRaisesRegex(CHECK.ValidationError, "differs from acceptance target"):
            self.check()

    def test_missing_required_type_rejects(self):
        self.row["required_evidence"] = ["manifest", "unit"]
        with self.assertRaisesRegex(CHECK.ValidationError, "types are missing"):
            self.check()

    def test_empty_unknown_and_duplicate_type_lists_reject(self):
        for required in (None, [], ["invented"], ["manifest", "manifest"], "manifest"):
            with self.subTest(required=required):
                self.row["required_evidence"] = required
                with self.assertRaises(CHECK.ValidationError):
                    self.check()

    def test_duplicate_and_malformed_references_reject(self):
        original = copy.deepcopy(self.row)
        for key in ("evidence_ids", "blocking_gaps"):
            for value in ("abc", [None], [True], [" spaced "], ["x", "x"], [["nested"]]):
                with self.subTest(key=key, value=value):
                    self.row = {**original, key: value}
                    with self.assertRaises(CHECK.ValidationError):
                        self.check()

    def test_unmapped_evidence_cannot_pay_for_another_task(self):
        self.row["id"] = "TG-W0-099"
        with self.assertRaisesRegex(CHECK.ValidationError, "not mapped"):
            self.check()

    def test_exact_target_is_required_and_closed_world(self):
        for target in (None, {}, {**TARGET, "extra": True}, {**TARGET, "commit": "A" * 40},
                       {**TARGET, "repository": "other/repository"}, {**TARGET, "tree": "c" * 40}):
            with self.subTest(target=target):
                self.row["acceptance_target"] = target
                with self.assertRaises(CHECK.ValidationError):
                    self.check()

    def test_artifact_tamper_cannot_hide_behind_accepted_flag(self):
        (self.root / self.fixture.artifact["path"]).write_bytes(b"changed retained output\n")
        with self.assertRaisesRegex(CHECK.ValidationError, "invalid retained evidence"):
            self.check()

    def test_missing_manifest_cannot_hide_behind_accepted_flag(self):
        (self.root / self.fixture.entry["path"]).unlink()
        with self.assertRaises((CHECK.ValidationError, OSError)):
            self.check()

    def test_expired_evidence_remains_ineligible(self):
        for obj in (self.fixture.entry, self.fixture.manifest):
            obj["expires_at"] = "2026-09-04T22:00:00Z"
        self.fixture.write()
        with self.assertRaisesRegex(CHECK.ValidationError, "invalid retained evidence"):
            self.check()

    def test_independence_cannot_be_asserted_by_task_metadata(self):
        self.row["independent_review"] = {"decision": "accepted"}
        self.fixture.entry["independent_review"]["independent"] = False
        with self.assertRaisesRegex(CHECK.ValidationError, "invalid retained evidence"):
            self.check()


class RepositoryAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name) / "source"
        # A full tracked-source fixture runs the real CLI and top-level plan.
        # Keep tests/ because documentation link checks can refer to test paths.
        shutil.copytree(ROOT, cls.root, ignore=shutil.ignore_patterns(".git", "__pycache__", "target"))
        cls.execution = (cls.root / "docs/status/EXECUTION_STATUS.json").read_bytes()
        cls.roadmap = (cls.root / "docs/roadmap/NEXT_MILESTONE.json").read_bytes()
        cls.products = (cls.root / "docs/status/PRODUCT_GATES.json").read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        for name, data in (("docs/status/EXECUTION_STATUS.json", self.execution),
                           ("docs/roadmap/NEXT_MILESTONE.json", self.roadmap),
                           ("docs/status/PRODUCT_GATES.json", self.products)):
            (self.root / name).write_bytes(data)

    def mutate(self, name, change):
        path = self.root / name
        data = json.loads(path.read_text())
        change(data)
        path.write_text(json.dumps(data))

    def cli(self, script="check-status-transitions.py"):
        return subprocess.run([sys.executable, "scripts/" + script], cwd=self.root,
                              text=True, capture_output=True, timeout=40)

    def reject(self, script="check-status-transitions.py", diagnostic=None):
        result = self.cli(script)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        if diagnostic:
            self.assertIn(diagnostic, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_unchanged_repository_snapshot_passes(self):
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_forged_roadmap_acceptance_rejects_real_cli(self):
        self.mutate("docs/roadmap/NEXT_MILESTONE.json", lambda d: d["items"][1].update(status="accepted"))
        self.reject(diagnostic="open blocking gap")

    def test_forged_roadmap_acceptance_rejects_top_level_plan(self):
        self.mutate("docs/roadmap/NEXT_MILESTONE.json", lambda d: d["items"][1].update(status="accepted"))
        self.reject("check-plan.py")

    def test_removing_roadmap_gaps_does_not_create_evidence(self):
        self.mutate("docs/roadmap/NEXT_MILESTONE.json", lambda d: d["items"][0].update(
            status="accepted", gap_ids=[], acceptance_target=TARGET))
        self.reject(diagnostic="evidence_ids")

    def test_default_acceptance_cannot_promote_unlisted_tasks(self):
        self.mutate("docs/status/EXECUTION_STATUS.json", lambda d: d.update(default_task_state="accepted"))
        self.reject(diagnostic="default task state")

    def test_unknown_override_is_rejected_even_with_valid_id_pattern(self):
        self.mutate("docs/status/EXECUTION_STATUS.json", lambda d: d["task_overrides"].append(
            {"id": "TG-W99-001", "status": "accepted", "blocking_gaps": []}))
        self.reject(diagnostic="outside approved backlog")

    def test_override_cannot_erase_immutable_dependencies(self):
        self.mutate("docs/status/EXECUTION_STATUS.json", lambda d: d["task_overrides"].append(
            {"id": "TG-W0-002", "status": "planned", "depends_on": []}))
        self.reject(diagnostic="cannot replace scope dependencies")

    def test_known_task_override_needs_accepted_evidence(self):
        self.mutate("docs/status/EXECUTION_STATUS.json", lambda d: d["task_overrides"].append(
            {"id": "TG-W0-001", "status": "accepted", "blocking_gaps": [],
             "acceptance_target": TARGET, "required_evidence": ["manifest"], "evidence_ids": []}))
        self.reject(diagnostic="evidence_ids")

    def test_workstream_cannot_be_accepted_with_open_gaps(self):
        self.mutate("docs/status/EXECUTION_STATUS.json", lambda d: d["workstreams"][0].update(status="accepted"))
        self.reject(diagnostic="open blocking gap")

    def test_workstream_cannot_hide_unaccepted_child_tasks(self):
        self.mutate("docs/status/EXECUTION_STATUS.json", lambda d: d["workstreams"][0].update(
            status="accepted", blocking_gaps=[], acceptance_target=TARGET))
        self.reject(diagnostic="unaccepted tasks")

    def test_stage_cannot_be_accepted_with_open_gaps(self):
        self.mutate("docs/status/EXECUTION_STATUS.json", lambda d: d["stage_gates"][0].update(status="accepted"))
        self.reject(diagnostic="open blocking gap")

    def test_stage_cannot_bypass_derived_product_gate(self):
        self.mutate("docs/status/EXECUTION_STATUS.json", lambda d: d["stage_gates"][0].update(
            status="accepted", blocking_gaps=[], acceptance_target=TARGET))
        self.reject(diagnostic="product gate GATE-REPOSITORY is not passed")

    def test_forged_product_gate_snapshot_is_rejected_by_state_cli(self):
        def change(data):
            data["gates"][0]["status"] = "passed"
            data["summary"]["passed"] += 1
            data["summary"]["blocked"] -= 1
        self.mutate("docs/status/PRODUCT_GATES.json", change)
        self.reject(diagnostic="product gate derivation failed")

    def test_milestone_cannot_hide_unaccepted_items(self):
        self.mutate("docs/roadmap/NEXT_MILESTONE.json", lambda d: d.update(status="accepted"))
        self.reject(diagnostic="unaccepted items")

    def test_backlog_reference_digest_cannot_be_replaced(self):
        self.mutate("docs/status/EXECUTION_STATUS.json", lambda d: d["base_backlog"].update(artifact_sha256="0" * 64))
        self.reject(diagnostic="backlog digest drift")

    def test_malformed_row_is_clean_rejection(self):
        self.mutate("docs/status/EXECUTION_STATUS.json", lambda d: d["workstreams"].__setitem__(0, None))
        self.reject(diagnostic="row must be an object")


if __name__ == "__main__":
    unittest.main()
