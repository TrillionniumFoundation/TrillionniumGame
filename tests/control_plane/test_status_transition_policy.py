"""Regression tests for explicit task/gap state-transition policy."""
from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "scripts/status_transition_policy.py"


def load_module():
    spec = importlib.util.spec_from_file_location("trnm_status_transition_policy_tests", SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError("status transition policy unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


POLICY = load_module()


def write_root(root: Path, *, task_override=None, workstream="in-progress",
               stage="blocked", gap="open", roadmap="in-progress",
               milestone="in-progress") -> None:
    (root / "docs/status").mkdir(parents=True, exist_ok=True)
    (root / "docs/roadmap").mkdir(parents=True, exist_ok=True)
    execution = {
        "schema": "trillionnium.execution-status.v1",
        "default_task_state": "planned",
        "task_overrides": [] if task_override is None else [
            {"id": "TG-W0-001", "status": task_override}
        ],
        "workstreams": [{"id": "W0", "status": workstream}],
        "stage_gates": [{"id": "SG0", "status": stage}],
    }
    register = {
        "schema": "trillionnium.gap-register.v1",
        "gaps": [{"id": "GAP-P0-TEST-001", "status": gap}],
    }
    next_milestone = {
        "schema": "trillionnium.next-milestone.v1",
        "milestone_id": "M0-TEST",
        "status": milestone,
        "items": [{"id": "TG-V3-001", "status": roadmap}],
    }
    (root / "docs/status/EXECUTION_STATUS.json").write_text(
        json.dumps(execution), encoding="utf-8"
    )
    (root / "docs/status/GAP_REGISTER.json").write_text(
        json.dumps(register), encoding="utf-8"
    )
    (root / "docs/roadmap/NEXT_MILESTONE.json").write_text(
        json.dumps(next_milestone), encoding="utf-8"
    )


class TransitionGraphTests(unittest.TestCase):
    def test_policy_has_exact_state_coverage_and_terminal_superseded(self):
        POLICY.validate_policy()
        self.assertEqual(set(POLICY.allowed_transitions("task")), set(POLICY.TASK_STATES))
        self.assertEqual(set(POLICY.allowed_transitions("gap")), set(POLICY.GAP_STATES))
        self.assertEqual(POLICY.allowed_transitions("task")["superseded"], {"superseded"})
        self.assertEqual(POLICY.allowed_transitions("gap")["superseded"], {"superseded"})

    def test_adjacent_promotions_are_legal(self):
        for kind, progress in (("task", POLICY.TASK_PROGRESS), ("gap", POLICY.GAP_PROGRESS)):
            for previous, current in zip(progress, progress[1:]):
                with self.subTest(kind=kind, previous=previous, current=current):
                    POLICY.validate_transition(kind, previous, current)

    def test_proof_stage_jumps_are_rejected(self):
        for kind, previous, current in (
            ("task", "planned", "source-candidate"),
            ("task", "ready", "accepted"),
            ("gap", "open", "remote-verified"),
            ("gap", "source-candidate", "closed"),
        ):
            with self.subTest(kind=kind, previous=previous, current=current):
                with self.assertRaises(POLICY.TransitionError):
                    POLICY.validate_transition(kind, previous, current)

    def test_fail_closed_regressions_are_legal(self):
        for kind, previous, current in (
            ("task", "accepted", "source-candidate"),
            ("task", "remote-verified", "planned"),
            ("gap", "closed", "in-progress"),
            ("gap", "independently-reviewed", "open"),
        ):
            with self.subTest(kind=kind, previous=previous, current=current):
                POLICY.validate_transition(kind, previous, current)

    def test_blocked_and_rejected_states_cannot_grant_acceptance(self):
        for kind, previous, current in (
            ("task", "blocked", "accepted"),
            ("task", "rejected", "source-candidate"),
            ("gap", "blocked-external-admin", "closed"),
            ("gap", "rejected", "source-candidate"),
        ):
            with self.subTest(kind=kind, previous=previous, current=current):
                with self.assertRaises(POLICY.TransitionError):
                    POLICY.validate_transition(kind, previous, current)

    def test_superseded_rows_cannot_reactivate(self):
        for kind, target in (("task", "planned"), ("gap", "open")):
            with self.assertRaises(POLICY.TransitionError):
                POLICY.validate_transition(kind, "superseded", target)

    def test_unknown_kind_and_states_fail_closed(self):
        with self.assertRaises(POLICY.TransitionError):
            POLICY.allowed_transitions("product")
        with self.assertRaises(POLICY.TransitionError):
            POLICY.validate_transition("task", "invented", "planned")
        with self.assertRaises(POLICY.TransitionError):
            POLICY.validate_transition("gap", "open", "invented")


class RepositoryTransitionTests(unittest.TestCase):
    def test_current_repository_is_a_valid_self_transition(self):
        report = POLICY.compare_roots(ROOT, ROOT)
        self.assertTrue(report["valid"])
        self.assertEqual(report["transition_count"], 0)
        self.assertFalse(report["claim_boundary"]["gap_closed"])

    def roots(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name) / "base"
        head = Path(temporary.name) / "head"
        write_root(base)
        write_root(head)
        return base, head

    def test_sparse_task_override_is_compared_against_planned_default(self):
        base, head = self.roots()
        write_root(head, task_override="ready")
        report = POLICY.compare_roots(base, head)
        self.assertEqual(report["transition_count"], 1)
        self.assertEqual(report["transitions"][0]["previous"], "planned")
        self.assertEqual(report["transitions"][0]["current"], "ready")

    def test_sparse_override_cannot_jump_directly_to_accepted(self):
        base, head = self.roots()
        write_root(head, task_override="accepted")
        with self.assertRaisesRegex(POLICY.TransitionError, "illegal task transition"):
            POLICY.compare_roots(base, head)

    def test_removing_override_is_a_fail_closed_regression_to_default(self):
        base, head = self.roots()
        write_root(base, task_override="source-candidate")
        report = POLICY.compare_roots(base, head)
        self.assertEqual(report["transition_count"], 1)
        self.assertEqual(report["transitions"][0]["current"], "planned")

    def test_gap_cannot_jump_from_open_to_closed(self):
        base, head = self.roots()
        write_root(head, gap="closed")
        with self.assertRaisesRegex(POLICY.TransitionError, "illegal gap transition"):
            POLICY.compare_roots(base, head)

    def test_gap_can_regress_when_evidence_becomes_invalid(self):
        base, head = self.roots()
        write_root(base, gap="closed")
        write_root(head, gap="source-candidate")
        report = POLICY.compare_roots(base, head)
        self.assertEqual(report["transition_count"], 1)

    def test_scope_membership_cannot_change_as_a_status_only_transition(self):
        base, head = self.roots()
        data = json.loads((head / "docs/status/GAP_REGISTER.json").read_text())
        data["gaps"] = []
        (head / "docs/status/GAP_REGISTER.json").write_text(json.dumps(data))
        with self.assertRaisesRegex(POLICY.TransitionError, "membership changed"):
            POLICY.compare_roots(base, head)

    def test_cli_self_check_and_root_comparison(self):
        self_check = subprocess.run(
            [sys.executable, str(SOURCE), "--self-check"],
            cwd=ROOT, text=True, capture_output=True, timeout=20,
        )
        self.assertEqual(self_check.returncode, 0, self_check.stderr)
        self.assertTrue(json.loads(self_check.stdout)["valid"])
        base, head = self.roots()
        compared = subprocess.run(
            [sys.executable, str(SOURCE), "--previous-root", str(base),
             "--current-root", str(head)],
            cwd=ROOT, text=True, capture_output=True, timeout=20,
        )
        self.assertEqual(compared.returncode, 0, compared.stderr)
        self.assertEqual(json.loads(compared.stdout)["transition_count"], 0)

    def test_cli_rejects_missing_root_pair_without_traceback(self):
        result = subprocess.run(
            [sys.executable, str(SOURCE), "--previous-root", str(ROOT)],
            cwd=ROOT, text=True, capture_output=True, timeout=20,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("both previous and current roots", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
