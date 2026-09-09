"""Regression tests for explicit task/gap and roadmap-scope transitions."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "scripts/status_transition_policy.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "trnm_status_transition_policy_tests", SOURCE
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("status transition policy unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


POLICY = load_module()


def write_root(
    root: Path,
    *,
    task_override=None,
    workstream="in-progress",
    stage="blocked",
    gap="open",
    roadmap="in-progress",
    milestone="in-progress",
    milestone_id="M0-TEST",
    item_ids=("TG-V3-001",),
    item_title="fixture item",
) -> None:
    (root / "docs/status").mkdir(parents=True, exist_ok=True)
    (root / "docs/roadmap").mkdir(parents=True, exist_ok=True)
    execution = {
        "schema": "trillionnium.execution-status.v1",
        "default_task_state": "planned",
        "task_overrides": []
        if task_override is None
        else [{"id": "TG-W0-001", "status": task_override}],
        "workstreams": [{"id": "W0", "status": workstream}],
        "stage_gates": [{"id": "SG0", "status": stage}],
    }
    register = {
        "schema": "trillionnium.gap-register.v1",
        "gaps": [{"id": "GAP-P0-TEST-001", "status": gap}],
    }
    next_milestone = {
        "schema": "trillionnium.next-milestone.v1",
        "project_id": "trillionnium-game",
        "plan_version": 3,
        "milestone_id": milestone_id,
        "title": "fixture milestone",
        "status": milestone,
        "created_at": "2026-01-01",
        "objective": "fixture objective",
        "exit_conditions": ["fixture condition"],
        "items": [
            {
                "id": identity,
                "priority": "P0",
                "title": item_title,
                "status": roadmap,
                "owner_role": "fixture",
                "depends_on": [],
                "gap_ids": ["GAP-P0-TEST-001"],
                "deliverables": ["fixture"],
                "acceptance": ["fixture"],
                "required_evidence": ["unit"],
            }
            for identity in item_ids
        ],
        "next_item_rule": "fixture rule",
        "claim_boundary": "fixture boundary",
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


def replacement_for(base: Path, head: Path) -> dict[str, object]:
    previous = POLICY.snapshot(base)["roadmap"]
    current = POLICY.snapshot(head)["roadmap"]
    return {
        "previous_plan_version": previous["plan_version"],
        "previous_milestone_id": previous["milestone_id"],
        "previous_item_count": previous["item_count"],
        "previous_item_ids_sha256": previous["item_ids_sha256"],
        "current_plan_version": current["plan_version"],
        "current_milestone_id": current["milestone_id"],
        "current_item_count": current["item_count"],
        "current_item_ids_sha256": current["item_ids_sha256"],
        "current_scope_sha256": current["scope_sha256"],
        "credit_policy": "reset-no-verified-or-accepted-state-transfer",
    }


class TransitionGraphTests(unittest.TestCase):
    def test_policy_has_exact_state_coverage_and_terminal_superseded(self):
        POLICY.validate_policy()
        self.assertEqual(
            set(POLICY.allowed_transitions("task")), set(POLICY.TASK_STATES)
        )
        self.assertEqual(
            set(POLICY.allowed_transitions("gap")), set(POLICY.GAP_STATES)
        )
        self.assertEqual(
            POLICY.allowed_transitions("task")["superseded"], {"superseded"}
        )
        self.assertEqual(
            POLICY.allowed_transitions("gap")["superseded"], {"superseded"}
        )

    def test_adjacent_promotions_are_legal(self):
        for kind, progress in (
            ("task", POLICY.TASK_PROGRESS),
            ("gap", POLICY.GAP_PROGRESS),
        ):
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
        self.assertEqual(report["scope_replacement_count"], 0)
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

    def test_gap_membership_cannot_change_as_a_status_only_transition(self):
        base, head = self.roots()
        data = json.loads((head / "docs/status/GAP_REGISTER.json").read_text())
        data["gaps"] = []
        (head / "docs/status/GAP_REGISTER.json").write_text(json.dumps(data))
        with self.assertRaisesRegex(POLICY.TransitionError, "membership changed"):
            POLICY.compare_roots(base, head)

    def test_unapproved_roadmap_scope_replacement_is_rejected(self):
        base, head = self.roots()
        write_root(
            head,
            milestone_id="M0-NEW",
            item_ids=("TG-V3-101", "TG-V3-102"),
            roadmap="source-candidate",
        )
        with patch.object(POLICY, "APPROVED_ROADMAP_SCOPE_REPLACEMENTS", ()):
            with self.assertRaisesRegex(
                POLICY.TransitionError, "not an exact approved transition"
            ):
                POLICY.compare_roots(base, head)

    def test_exact_roadmap_scope_replacement_resets_credit(self):
        base, head = self.roots()
        write_root(
            head,
            milestone_id="M0-NEW",
            item_ids=("TG-V3-101", "TG-V3-102"),
            roadmap="source-candidate",
        )
        declaration = replacement_for(base, head)
        with patch.object(
            POLICY, "APPROVED_ROADMAP_SCOPE_REPLACEMENTS", (declaration,)
        ):
            report = POLICY.compare_roots(base, head)
        self.assertEqual(report["scope_replacement_count"], 1)
        event = report["scope_replacements"][0]
        self.assertEqual(
            event["credit_policy"],
            "reset-no-verified-or-accepted-state-transfer",
        )
        self.assertFalse(
            report["claim_boundary"]["scope_replacement_transfers_acceptance"]
        )

    def test_scope_replacement_cannot_carry_verified_or_accepted_state(self):
        for state in (
            "locally-verified",
            "remote-verified",
            "independently-reviewed",
            "accepted",
            "superseded",
        ):
            with self.subTest(state=state):
                base, head = self.roots()
                write_root(
                    head,
                    milestone_id="M0-NEW",
                    item_ids=("TG-V3-101",),
                    roadmap=state,
                )
                declaration = replacement_for(base, head)
                with patch.object(
                    POLICY,
                    "APPROVED_ROADMAP_SCOPE_REPLACEMENTS",
                    (declaration,),
                ):
                    with self.assertRaisesRegex(
                        POLICY.TransitionError, "verified or accepted"
                    ):
                        POLICY.compare_roots(base, head)

    def test_scope_replacement_digest_is_exact(self):
        base, head = self.roots()
        write_root(
            head,
            milestone_id="M0-NEW",
            item_ids=("TG-V3-101",),
            roadmap="source-candidate",
        )
        declaration = replacement_for(base, head)
        write_root(
            head,
            milestone_id="M0-NEW",
            item_ids=("TG-V3-101",),
            roadmap="source-candidate",
            item_title="changed immutable obligation",
        )
        with patch.object(
            POLICY, "APPROVED_ROADMAP_SCOPE_REPLACEMENTS", (declaration,)
        ):
            with self.assertRaisesRegex(
                POLICY.TransitionError, "not an exact approved transition"
            ):
                POLICY.compare_roots(base, head)

    def test_cli_self_check_and_root_comparison(self):
        self_check = subprocess.run(
            [sys.executable, str(SOURCE), "--self-check"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(self_check.returncode, 0, self_check.stderr)
        payload = json.loads(self_check.stdout)
        self.assertTrue(payload["valid"])
        self.assertGreaterEqual(payload["approved_roadmap_scope_replacements"], 1)
        base, head = self.roots()
        compared = subprocess.run(
            [
                sys.executable,
                str(SOURCE),
                "--previous-root",
                str(base),
                "--current-root",
                str(head),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(compared.returncode, 0, compared.stderr)
        self.assertEqual(json.loads(compared.stdout)["transition_count"], 0)

    def test_cli_rejects_missing_root_pair_without_traceback(self):
        result = subprocess.run(
            [sys.executable, str(SOURCE), "--previous-root", str(ROOT)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("both previous and current roots", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
