from __future__ import annotations

import copy
import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("token_source_contract", ROOT / "scripts/check-token-core.py")
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

class TokenSourceLockContractTests(unittest.TestCase):
    def lock(self):
        return json.loads((ROOT / "contracts/session/nakama-v340-token-source-lock.json").read_text())

    def test_current_nine_source_lock_and_independent_checker_pass(self):
        value=self.lock()
        self.assertEqual(len(value["sources"]),9)
        self.assertEqual(len(module.LEGACY_SOURCE_BLOBS),3)
        self.assertEqual(len(module.ADDITIONAL_SOURCE_BLOBS),6)
        module.validate_source_lock(value)
        with redirect_stdout(io.StringIO()) as stream:
            module.main()
        result=json.loads(stream.getvalue())
        self.assertEqual(result["status"],"token-policy-static-contract-passed")
        self.assertIs(result["signature_compatible"],False)

    def test_old_three_new_six_missing_extra_duplicate_or_wrong_blob_rejected(self):
        original=self.lock()
        cases=[]
        for index in range(9):
            value=copy.deepcopy(original);del value["sources"][index];cases.append(value)
            value=copy.deepcopy(original);value["sources"][index]["blob"]="0"*40;cases.append(value)
        value=copy.deepcopy(original);value["sources"][8]=copy.deepcopy(value["sources"][0]);cases.append(value)
        value=copy.deepcopy(original);value["sources"].append(copy.deepcopy(value["sources"][0]));cases.append(value)
        value=copy.deepcopy(original);value["sources"][8]["path"]="server/unreviewed.go";cases.append(value)
        for value in cases:
            with self.subTest(sources=value["sources"]),self.assertRaises(SystemExit):module.validate_source_lock(value)

    def test_complete_file_types_identities_scopes_and_false_claims_are_strict(self):
        original=self.lock()
        for field,value in (("commit","0"*40),("tree","0"*40),("repository","other/nakama")):
            lock=copy.deepcopy(original);lock["upstream"][field]=value
            with self.subTest(field=field),self.assertRaises(SystemExit):module.validate_source_lock(lock)
        for field,value in (("bytes",True),("bytes","30888"),("sha256","0"*64),("blob","0"*40),("primary_url","https://invalid.example")):
            lock=copy.deepcopy(original);lock["verified_files"][0][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(SystemExit):module.validate_source_lock(lock)
        lock=copy.deepcopy(original);lock["verified_files"][8]=copy.deepcopy(lock["verified_files"][0])
        with self.assertRaises(SystemExit):module.validate_source_lock(lock)
        for value in (True,0,None):
            lock=copy.deepcopy(original);lock["claims"]["production_ready"]=value
            with self.subTest(claim=value),self.assertRaises(SystemExit):module.validate_source_lock(lock)
        lock=copy.deepcopy(original);lock["sources"][0]["observed_contract"][0]=False
        with self.assertRaises(SystemExit):module.validate_source_lock(lock)

    def test_duplicate_JSON_fields_and_nonfinite_tokens_fail_before_lock_validation(self):
        for raw in ('{"sources":[],"sources":[]}','{"upstream":{"commit":"a","commit":"b"}}','{"claims":{"production_ready":NaN}}'):
            with self.subTest(raw=raw),self.assertRaises(SystemExit):module.decode_source_lock(raw)
        with self.assertRaises(SystemExit):module.decode_source_lock(" "*(1024*1024+1))

    def test_make_default_includes_both_independent_CI_static_gates(self):
        make=(ROOT/"Makefile").read_text()
        self.assertIn("check: plan server-source ci-static-contracts rust security-critical python legacy-go",make)
        self.assertIn("ci-static-contracts:\n\tpython3 scripts/check-foundation-schema.py\n\tpython3 scripts/check-token-core.py",make)

if __name__ == "__main__":unittest.main()
