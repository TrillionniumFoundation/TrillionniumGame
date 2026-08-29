from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check-repository-governance.py"


def load_module():
    spec = importlib.util.spec_from_file_location("check_repository_governance", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RepositoryGovernanceContractTests(unittest.TestCase):
    def test_desired_state_is_fail_closed(self) -> None:
        module = load_module()
        contract = module.load_object(module.CONTRACT_PATH)
        module.validate_contract(contract)
        self.assertFalse(contract["actions"]["empty_run_collection_is_success"])
        self.assertFalse(contract["actions"]["skipped_cancelled_or_older_head_is_success"])
        self.assertFalse(contract["main_rules"]["direct_push_allowed"])
        self.assertEqual(contract["main_rules"]["bypass_roles"], [])
        self.assertFalse(contract["independent_review"]["implementer_may_self_approve"])

    def test_incomplete_observation_is_rejected(self) -> None:
        module = load_module()
        contract = module.load_object(module.CONTRACT_PATH)
        observation = {
            "schema": "trillionnium.github-governance-observation.v1",
            "repository": "TrillionniumFoundation/TrillionniumGame",
            "candidate_commit": "a" * 40,
            "recorded_at": "2026-08-29T00:00:00Z",
            "observer_identity": "test",
            "facts": {"actions_enabled": False},
            "accepted": False,
            "response_sha256": {"actions": "0" * 64},
        }
        with self.assertRaises(module.ValidationError):
            module.validate_observation(contract, observation)


if __name__ == "__main__":
    unittest.main()
