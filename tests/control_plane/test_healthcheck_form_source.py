"""The healthcheck form source candidate never relaxes its bounded profile."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("healthcheck_form_checker", ROOT / "scripts/check-trnm-server.py")
assert SPEC and SPEC.loader
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)
CONTRACT = ROOT / "contracts/http/nakama-healthcheck-form-v1.json"


class HealthcheckFormSourceTests(unittest.TestCase):
    def setUp(self):
        self.contract = json.loads(CONTRACT.read_text())
        self.status = json.loads((ROOT / "docs/status/TRNM_SERVER_STATUS.json").read_text())
        self.sources = {Path(path): (ROOT / path).read_text() for path in self.contract["source_sha256"]}

    def check(self, contract=None, status=None):
        read_text = Path.read_text
        def read(path, *args, **kwargs):
            if path == CONTRACT:
                return json.dumps(contract or self.contract)
            return read_text(path, *args, **kwargs)
        with patch.object(Path, "read_text", read):
            CHECKER.validate_healthcheck_form_source(self.sources, status or self.status)

    def test_current_source_passes(self):
        self.check()

    def test_scope_order_and_limits_cannot_relax(self):
        for key in ("media_type_exact_match", "body_error_before_query_error", "override_after_form_parse", "ingress_limits_unchanged", "transport_drain_denial_unchanged"):
            contract = copy.deepcopy(self.contract)
            contract["scope"][key] = False
            with self.assertRaises(SystemExit): self.check(contract)

    def test_case_denominator_and_unicode_mapping_cannot_shrink(self):
        for key, value in (("cases", 65535), ("bytes", 0), ("sha256", "invalid")):
            contract = copy.deepcopy(self.contract)
            contract["go_quote_corpus"][key] = value
            with self.assertRaises(SystemExit): self.check(contract)
        contract = copy.deepcopy(self.contract)
        contract["nonascii_method_mappings"] = []
        with self.assertRaises(SystemExit): self.check(contract)

    def test_sources_fixtures_and_native_tests_are_bound(self):
        for key, value in (("source_sha256", {}), ("fixtures_sha256", "0" * 64), ("native_tests", [])):
            contract = copy.deepcopy(self.contract)
            contract[key] = value
            with self.assertRaises(SystemExit): self.check(contract)

    def test_native_success_cannot_grant_acceptance(self):
        for key in self.contract["claims"]:
            contract = copy.deepcopy(self.contract)
            contract["claims"][key] = True
            with self.assertRaises(SystemExit): self.check(contract)


if __name__ == "__main__":
    unittest.main()
