from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check-evidence-index.py"


def load_module():
    spec = importlib.util.spec_from_file_location("check_evidence_index", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EvidenceIndexContractTests(unittest.TestCase):
    def test_index_is_structurally_valid_and_credit_is_explicit(self) -> None:
        result = load_module().validate()
        self.assertEqual(result["schema"], "trillionnium.evidence-index-validation.v1")
        self.assertGreaterEqual(result["evidence_count"], 0)
        self.assertEqual(
            result["evidence_count"],
            result["credited"] + result["diagnostic_only"],
        )

    def test_current_relay_evidence_cannot_gain_credit_implicitly(self) -> None:
        module = load_module()
        index = module.load_object(module.INDEX_PATH)
        for row in module.rows(index):
            if row.get("evidence_id") == "TG-EV-RELAY-FOUNDATION-DATABASE-20260828":
                self.assertFalse(module.credit_enabled(row))
                break
        else:
            self.fail("expected relay foundation database evidence to be indexed")


if __name__ == "__main__":
    unittest.main()
