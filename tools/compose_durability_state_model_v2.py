#!/usr/bin/env python3
"""Finalize the durability state model with an observable stale-ack mutant."""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from types import ModuleType


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def load_support() -> ModuleType:
    path = Path(__file__).with_name("compose_durability_state_model.py")
    spec = importlib.util.spec_from_file_location("durability_model_support", path)
    require(spec is not None and spec.loader is not None, "durability model support unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(root: Path) -> None:
    support = load_support()
    support.run(root)
    model = root / "scripts/durability-state-model.py"
    text = support.read(model)
    field_anchor = "    stale_rejections: int = 0\n"
    if "stale_ack_accepted: bool" not in text:
        require(field_anchor in text, "state field anchor missing")
        text = text.replace(field_anchor, field_anchor + "    stale_ack_accepted: bool = False\n", 1)
    old = """                stale = replace(\n                    state,\n                    outbox_state=DELIVERED,\n                    lease_owner=None,\n                    delivered_receipt=True,\n                )\n"""
    new = """                stale = replace(\n                    state,\n                    outbox_state=DELIVERED,\n                    lease_owner=None,\n                    delivered_receipt=True,\n                    stale_ack_accepted=True,\n                )\n"""
    if "stale_ack_accepted=True" not in text:
        require(old in text, "stale-ack mutant anchor missing")
        text = text.replace(old, new, 1)
    invariant_anchor = "    if state.stale_rejections < 0 or state.terminal_conflicts < 0:\n"
    if "stale lease acknowledgement mutated terminal state" not in text:
        require(invariant_anchor in text, "stale-ack invariant anchor missing")
        text = text.replace(
            invariant_anchor,
            "    if state.stale_ack_accepted:\n"
            "        raise AssertionError(\"stale lease acknowledgement mutated terminal state\")\n"
            + invariant_anchor,
            1,
        )
    support.write(model, text)

    test = root / "tests/control_plane/test_durability_state_model.py"
    text = support.read(test)
    old_test = """          def test_stale_ack_mutant_is_rejected_by_exact_owner_semantics(self):\n            state=self.model.State(receipt_fingerprint=\"A\",revision=1,outbox_state=self.model.LEASED,lease_generation=2,lease_owner=1,visible_effect=True,publish_attempts=1)\n            mutated=[e.state for e in self.model.transition(state,allow_stale_ack=True) if e.action==\"ack_stale_owner_or_generation\"][0]\n            self.assertEqual(mutated.outbox_state,self.model.DELIVERED)\n            self.assertNotEqual(mutated,state)\n"""
    new_test = """          def test_stale_ack_mutant_is_rejected_by_exact_owner_semantics(self):\n            with self.assertRaisesRegex(AssertionError,\"stale lease acknowledgement\"):\n              self.model.explore(10,allow_stale_ack=True)\n"""
    if new_test not in text:
        require(old_test in text, "stale-ack test anchor missing")
        text = text.replace(old_test, new_test, 1)
    support.write(test, text)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    run(parser.parse_args().root.resolve())
