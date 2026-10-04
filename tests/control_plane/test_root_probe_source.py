"""Bounded root header/CORS source profile and fail-closed claims."""
from __future__ import annotations
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("root_probe_checker", ROOT / "scripts/check-trnm-server.py")
assert SPEC and SPEC.loader
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)
CONTRACT = ROOT / "contracts/http/nakama-root-probe-v1.json"


class RootProbeSourceTests(unittest.TestCase):
    def setUp(self):
        self.contract = json.loads(CONTRACT.read_text())
        self.status = json.loads((ROOT / "docs/status/TRNM_SERVER_STATUS.json").read_text())
        self.sources = {Path(path): (ROOT / path).read_text() for path in self.contract["source_sha256"]}

    def check(self, contract=None, status=None):
        read_text = Path.read_text
        def read(path, *args, **kwargs):
            if path == CONTRACT: return json.dumps(contract or self.contract)
            return read_text(path, *args, **kwargs)
        with patch.object(Path, "read_text", read):
            CHECKER.validate_root_probe_source(self.sources, status or self.status)

    def test_current_root_source_passes(self):
        self.check()

    def test_headers_and_authority_boundaries_cannot_expand(self):
        for key in ("content_type", "cache_control", "vary", "database_access", "credentials_enabled"):
            contract = copy.deepcopy(self.contract)
            contract["scope"][key] = True
            with self.assertRaises(SystemExit): self.check(contract)

    def test_fixture_and_source_bytes_are_bound(self):
        for key, value in (("source_sha256", {}), ("reference_source_sha256", {}), ("fixtures_sha256", "0" * 64), ("native_tests", [])):
            contract = copy.deepcopy(self.contract)
            contract[key] = value
            with self.assertRaises(SystemExit): self.check(contract)

    def test_source_success_cannot_promote_claims(self):
        for key in self.contract["claims"]:
            contract = copy.deepcopy(self.contract)
            contract["claims"][key] = True
            with self.assertRaises(SystemExit): self.check(contract)

    def test_component_cannot_receive_implicit_credit(self):
        status = copy.deepcopy(self.status)
        status["root_probe_source_candidate"]["accepted"] = True
        with self.assertRaises(SystemExit): self.check(status=status)


if __name__ == "__main__": unittest.main()
