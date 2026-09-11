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

    def test_exhaustive_real_model_reaches_delivery_reaper_and_ambiguity_paths(self):
        report = self.model.explore(10)
        self.assertGreater(report["states"], 1_000)
        self.assertGreater(report["edges"], 5_000)
        self.assertGreater(report["delivered_states_seen"], 0)
        self.assertGreater(report["dead_states_seen"], 0)
        self.assertGreater(report["dead_with_visible_effect_states_seen"], 0)
        self.assertGreater(report["idempotent_retry_states_seen"], 0)

    def test_duplicate_effect_mutant_is_rejected_without_rejecting_safe_retry(self):
        report = self.model.explore(10)
        self.assertGreater(report["idempotent_retry_states_seen"], 0)
        with self.assertRaisesRegex(
            AssertionError, "duplicate externally visible effect"
        ):
            self.model.explore(10, duplicate_visible_effect=True)

    def test_stale_ack_mutant_is_actually_killed_by_owner_generation_invariant(self):
        with self.assertRaisesRegex(
            AssertionError, "stale delivery acknowledgement"
        ):
            self.model.explore(10, allow_stale_ack=True)

    def test_exact_ack_records_current_owner_and_generation(self):
        leased = self.model.State(
            receipt_fingerprint="A",
            revision=1,
            outbox_state=self.model.LEASED,
            attempt=1,
            lease_generation=3,
            lease_owner=2,
            last_lease_owner=2,
            visible_effect_count=1,
            publish_calls=1,
        )
        delivered = [
            edge.state
            for edge in self.model.transition(leased)
            if edge.action == "ack_exact"
        ][0]
        self.model.assert_invariants(delivered)
        self.assertEqual(delivered.outbox_state, self.model.DELIVERED)
        self.assertEqual(delivered.delivery_ack_owner, 2)
        self.assertEqual(delivered.delivery_ack_generation, 3)

    def test_reaper_requires_an_expired_final_attempt(self):
        first_lease = self.model.State(
            receipt_fingerprint="A",
            revision=1,
            outbox_state=self.model.LEASED,
            attempt=1,
            lease_generation=1,
            lease_owner=1,
            last_lease_owner=1,
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
        self.assertNotIn(
            "reap_expired_at_limit",
            {edge.action for edge in self.model.transition(expired_first)},
        )
        expired_final = self.model.State(
            receipt_fingerprint="A",
            revision=1,
            outbox_state=self.model.LEASED,
            attempt=self.model.MAX_ATTEMPTS,
            lease_generation=self.model.MAX_ATTEMPTS,
            lease_owner=2,
            last_lease_owner=2,
            lease_expired=True,
        )
        self.assertIn(
            "reap_expired_at_limit",
            {edge.action for edge in self.model.transition(expired_final)},
        )

    def test_source_contract_and_cli_pass(self):
        source = self.checker.MODEL.read_text()
        contract = json.loads(self.checker.CONTRACT.read_text())
        self.checker.validate(source, contract)
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
        self.assertGreater(report["states"], 1_000)
        self.assertGreater(report["dead_with_visible_effect_states_seen"], 0)


if __name__ == "__main__":
    unittest.main()
