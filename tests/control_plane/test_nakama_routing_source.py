"""Routing fixture and unaccepted source-contract regressions."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("routing_source_checker", ROOT / "scripts/check-trnm-server.py")
assert SPEC and SPEC.loader
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)
CONTRACT = "contracts/http/nakama-routing-v1.json"


class NakamaRoutingSourceTests(unittest.TestCase):
    def setUp(self):
        self.contract = json.loads((ROOT / CONTRACT).read_text())
        self.status = json.loads((ROOT / "docs/status/TRNM_SERVER_STATUS.json").read_text())
        self.sources = {Path(path): (ROOT / path).read_text() for path in (
            "crates/trnm-server/src/runtime/app.rs",
            "crates/trnm-server/src/runtime/cors_transport_tests.rs",
        )}

    def check(self, contract=None, status=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / CONTRACT
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps(contract or self.contract))
            with patch.object(CHECKER, "ROOT", root):
                CHECKER.validate_client_routing_source(self.sources, status or self.status)

    def test_current_source_contract_passes(self):
        self.check()

    def test_missing_or_duplicate_reference_cases_fail(self):
        for change in (lambda rows: rows.pop(), lambda rows: rows.__setitem__(0, rows[1])):
            contract = copy.deepcopy(self.contract)
            change(contract["fixtures"])
            with self.assertRaises(SystemExit):
                self.check(contract)

    def test_method_status_and_error_cannot_drift(self):
        for field, value in [("status", 405), ("code", 5), ("message", "custom error")]:
            contract = copy.deepcopy(self.contract)
            contract["fixtures"][0][field] = value
            with self.assertRaises(SystemExit):
                self.check(contract)

    def test_reference_and_native_presence_cannot_grant_acceptance(self):
        for key in self.contract["claims"]:
            contract = copy.deepcopy(self.contract)
            contract["claims"][key] = True
            with self.assertRaises(SystemExit):
                self.check(contract)
            status = copy.deepcopy(self.status)
            status["client_routing_source_candidate"][key] = True
            with self.assertRaises(SystemExit):
                self.check(status=status)

    def test_missing_native_regression_or_residual_is_rejected(self):
        for key in ("native_tests", "limitations"):
            contract = copy.deepcopy(self.contract)
            contract[key] = []
            with self.assertRaises(SystemExit):
                self.check(contract)


if __name__ == "__main__":
    unittest.main()
