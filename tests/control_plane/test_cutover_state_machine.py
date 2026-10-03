from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MACHINE = ROOT / "scripts/cutover-state-machine.py"
DERIVE = ROOT / "scripts/derive-cutover-blocker-packet.py"
CHECKER = ROOT / "scripts/check-cutover-state-machine.py"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("module unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class CutoverStateMachineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.machine = load(MACHINE, "cutover_machine_tests")
        cls.derive = load(DERIVE, "cutover_derive_tests")
        cls.checker = load(CHECKER, "cutover_checker_tests")

    def binding(self):
        return {
            "repository": "TrillionniumFoundation/TrillionniumGame",
            "source_head": "a" * 40,
            "source_tree": "b" * 40,
            "prospective_merge": "c" * 40,
            "prospective_merge_tree": "d" * 40,
        }

    def request(self, target="shadow"):
        return {
            "schema": "trillionnium.cutover-transition-request.v1",
            "target_state": target,
            "candidate_binding": self.binding(),
            "accepted_gates": ["SG0", "SG1", "SG2", "SG3", "SG4", "SG5", "SG8"],
            "reviewer": {
                "login": "claimed-reviewer",
                "role": "operations",
                "conflict_free_attestation": True,
            },
            "candidate_author": "author",
            "evidence_producers": ["producer"],
            "rollback_packet": {"accepted": True, "sha256": "e" * 64},
            "ordinary_protected_admission": True,
            "request_sha256": "f" * 64,
            "shadow_observation_accepted": True,
            "exclusive_canary_accepted": True,
            "endurance_24h_accepted": True,
            "endurance_72h_accepted": True,
            "endurance_7d_accepted": True,
            "approved_rpo_rto": True,
            "complete_nakama_compatibility": True,
            "global_sg1_accepted": True,
            "retirement_decision": {
                "explicit": True,
                "reviewer_login": "claimed-retirement-reviewer",
                "rollback_window_expired": True,
            },
        }

    def clear_blockers(self):
        return {
            "schema": "trillionnium.cutover-blocker-packet.v1",
            "candidate_binding": self.binding(),
            "open_p0_p1_count": 0,
            "all_required_evidence_accepted": True,
            "independent_review_complete": True,
            "governance_readback_complete": True,
        }

    def current(self, state="planning"):
        return {
            "schema": "trillionnium.cutover-state.v1",
            "state": state,
            "candidate_binding": None,
            "history": [],
            "claims": dict(self.machine.FALSE_CLAIMS),
        }

    def test_open_gap_and_obvious_self_attestation_are_rejected(self):
        blockers = self.clear_blockers()
        blockers["open_p0_p1_count"] = 1
        with self.assertRaisesRegex(self.machine.TransitionError, "open P0/P1"):
            self.machine.transition(self.current(), self.request(), blockers)
        request = self.request()
        request["reviewer"]["login"] = "author"
        with self.assertRaisesRegex(
            self.machine.TransitionError, "candidate author"
        ):
            self.machine.transition(self.current(), request, self.clear_blockers())

    def test_skipped_transition_and_missing_rollback_are_rejected(self):
        with self.assertRaisesRegex(
            self.machine.TransitionError, "illegal or skipped"
        ):
            self.machine.transition(
                self.current(), self.request("production"), self.clear_blockers()
            )
        request = self.request()
        request["rollback_packet"]["accepted"] = False
        with self.assertRaisesRegex(self.machine.TransitionError, "rollback"):
            self.machine.transition(self.current(), request, self.clear_blockers())

    def test_forged_clear_inputs_only_create_proposal_and_never_authority(self):
        result = self.machine.transition(
            self.current(), self.request(), self.clear_blockers()
        )
        self.assertEqual(result["state"], "planning")
        self.assertEqual(result["history"], [])
        self.assertEqual(result["pending_proposal"]["to"], "shadow")
        self.assertFalse(result["pending_proposal"]["authority_materialized"])
        self.assertFalse(any(result["claims"].values()))
        self.assertFalse(result["authority"]["materialization_supported"])
        self.assertFalse(result["authority"]["reviewer_identity_verified"])

    def test_aliases_fake_receipts_and_replay_cannot_set_online_or_cutover(self):
        request = self.request()
        request["reviewer"]["login"] = "same-person-new-alias"
        request["authority_receipt"] = {
            "verified": True,
            "signature": "attacker-controlled",
            "nonce": "replayed",
        }
        first = self.machine.transition(self.current(), request, self.clear_blockers())
        second = self.machine.transition(first, request, self.clear_blockers())
        for result in (first, second):
            self.assertEqual(result["state"], "planning")
            self.assertFalse(result["claims"]["public_online"])
            self.assertFalse(result["claims"]["cutover_authorized"])
            self.assertFalse(result["claims"]["nakama_retired"])
            self.assertFalse(result["authority"]["replay_protection_verified"])

    def test_local_materialization_entry_point_always_fails_closed(self):
        with self.assertRaisesRegex(
            self.machine.TransitionError,
            "trusted authority receipt verifier is not implemented",
        ):
            self.machine.materialize_transition(
                self.current(), self.request(), self.clear_blockers()
            )

    def test_real_gap_register_derives_diagnostic_non_authoritative_packet(self):
        gaps = json.loads((ROOT / "docs/status/GAP_REGISTER.json").read_text())
        packet = self.derive.derive(gaps, self.binding())
        self.assertGreater(packet["open_p0_p1_count"], 0)
        self.assertFalse(packet["all_required_evidence_accepted"])
        self.assertTrue(packet["authority"]["diagnostic_only"])
        self.assertFalse(packet["authority"]["may_authorize_transition"])

    def test_behavioral_checker_passes(self):
        machine = self.checker.load_module(MACHINE, "cutover_machine_nested_test")
        derive = self.checker.load_module(DERIVE, "cutover_derive_nested_test")
        self.checker.validate_behavior(machine, derive)
        contract = json.loads(self.checker.CONTRACT.read_text(encoding="utf-8"))
        self.checker.validate_contract(contract)


if __name__ == "__main__":
    unittest.main()
