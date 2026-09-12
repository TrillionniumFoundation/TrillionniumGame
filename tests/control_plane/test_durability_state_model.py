from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "scripts/durability-state-model.py"
CHECKER = ROOT / "scripts/check-durability-state-model.py"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("module unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class DurabilityStateModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = load(MODEL, "durability_state_model")
        cls.checker = load(CHECKER, "durability_state_model_checker")

    def test_exhaustive_model_reaches_delivery_reaper_ambiguity_and_takeover(self):
        report = self.model.explore(10)
        self.assertGreater(report["states"], 1_000)
        self.assertGreater(report["edges"], 5_000)
        self.assertGreater(report["delivered_states_seen"], 0)
        self.assertGreater(report["dead_states_seen"], 0)
        self.assertGreater(report["dead_with_visible_effect_states_seen"], 0)
        self.assertGreater(report["idempotent_retry_states_seen"], 0)
        self.assertGreater(report["stale_authority_rejection_states_seen"], 0)
        self.assertGreater(report["reclaimed_after_takeover_states_seen"], 0)

    def test_duplicate_effect_and_stale_token_mutants_are_killed(self):
        mutants = (
            ({"duplicate_visible_effect": True}, "duplicate externally visible effect"),
            ({"allow_stale_ack": True}, "stale delivery acknowledgement"),
            (
                {"allow_stale_authority_publish": True},
                "stale authority accepted mutation",
            ),
            (
                {"allow_stale_authority_ack": True},
                "stale authority accepted mutation",
            ),
        )
        for options, expected in mutants:
            with self.subTest(options=options):
                with self.assertRaisesRegex(AssertionError, expected):
                    self.model.explore(10, **options)

    def test_stale_command_uses_same_transition_and_preserves_state(self):
        initial = self.model.State()
        stale = self.model.commit_command(
            initial,
            caller_authority_generation=0,
            fingerprint="A",
        )
        self.assertEqual(stale.stale_rejections, 1)
        self.assertEqual(stale.revision, 0)
        self.assertIsNone(stale.receipt_fingerprint)
        self.assertIsNone(stale.command_authority_generation)
        self.assertEqual(stale.outbox_state, self.model.ABSENT)

        current = self.model.commit_command(
            initial,
            caller_authority_generation=1,
            fingerprint="A",
        )
        self.assertEqual(current.revision, 1)
        self.assertEqual(current.command_authority_generation, 1)
        self.assertEqual(current.outbox_state, self.model.READY)

    def test_takeover_fences_old_publish_and_ack_until_fresh_reclaim(self):
        committed = self.model.commit_command(
            self.model.State(),
            caller_authority_generation=1,
            fingerprint="A",
        )
        leased = next(self.model.claim_edges(committed)).state
        old_token = self.model.current_lease_token(leased)
        self.assertEqual(old_token.authority_generation, 1)

        taken_over = self.model.replace(leased, authority_generation=2)
        stale_publish = self.model.publish_with_token(taken_over, old_token)
        self.assertEqual(stale_publish.visible_effect_count, 0)
        self.assertEqual(stale_publish.publish_calls, 0)
        self.assertEqual(stale_publish.stale_rejections, 1)

        published = self.model.publish_with_token(leased, old_token)
        taken_over_after_publish = self.model.replace(published, authority_generation=2)
        stale_ack = self.model.acknowledge_with_token(
            taken_over_after_publish, old_token
        )
        self.assertEqual(stale_ack.outbox_state, self.model.LEASED)
        self.assertFalse(stale_ack.delivered_receipt)
        self.assertEqual(stale_ack.stale_rejections, 1)

        expired = self.model.replace(taken_over, lease_expired=True)
        reclaimed = next(self.model.claim_edges(expired)).state
        fresh_token = self.model.current_lease_token(reclaimed)
        self.assertEqual(fresh_token.authority_generation, 2)
        delivered = self.model.acknowledge_with_token(
            self.model.publish_with_token(reclaimed, fresh_token), fresh_token
        )
        self.model.assert_invariants(delivered)
        self.assertEqual(delivered.outbox_state, self.model.DELIVERED)
        self.assertEqual(delivered.delivery_ack_authority_generation, 2)

    def test_exact_ack_records_owner_lease_and_authority_generations(self):
        leased = self.model.State(
            authority_generation=4,
            revision=1,
            command_authority_generation=2,
            receipt_fingerprint="A",
            outbox_state=self.model.LEASED,
            attempt=1,
            lease_generation=3,
            lease_owner=2,
            lease_authority_generation=4,
            last_lease_owner=2,
            last_lease_authority_generation=4,
            visible_effect_count=1,
            published_authority_generation=4,
            publish_calls=1,
        )
        token = self.model.current_lease_token(leased)
        delivered = self.model.acknowledge_with_token(leased, token)
        self.model.assert_invariants(delivered)
        self.assertEqual(delivered.delivery_ack_owner, 2)
        self.assertEqual(delivered.delivery_ack_generation, 3)
        self.assertEqual(delivered.delivery_ack_authority_generation, 4)

    def test_reaper_requires_an_expired_final_attempt(self):
        first_lease = self.model.State(
            revision=1,
            command_authority_generation=1,
            receipt_fingerprint="A",
            outbox_state=self.model.LEASED,
            attempt=1,
            lease_generation=1,
            lease_owner=1,
            lease_authority_generation=1,
            last_lease_owner=1,
            last_lease_authority_generation=1,
        )
        self.assertNotIn(
            "reap_expired_at_limit",
            {edge.action for edge in self.model.transition(first_lease)},
        )
        expired_first = self.model.replace(first_lease, lease_expired=True)
        self.assertIn(
            "claim:2",
            {edge.action for edge in self.model.transition(expired_first)},
        )
        expired_final = self.model.replace(
            first_lease,
            attempt=self.model.MAX_ATTEMPTS,
            lease_generation=self.model.MAX_ATTEMPTS,
            lease_expired=True,
        )
        self.assertIn(
            "reap_expired_at_limit",
            {edge.action for edge in self.model.transition(expired_final)},
        )

    def test_behavioral_source_contract_and_cli_pass(self):
        model = self.checker.load_model()
        self.checker.validate_behavior(model)
        contract = json.loads(self.checker.CONTRACT.read_text(encoding="utf-8"))
        self.checker.validate_contract(contract)
        result = subprocess.run(
            [sys.executable, str(MODEL), "--depth", "10"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertGreater(report["stale_authority_rejection_states_seen"], 0)


if __name__ == "__main__":
    unittest.main()
