from __future__ import annotations
import contextlib
from copy import deepcopy
import importlib.util
import io
import json
import unittest
import tempfile
from unittest.mock import patch
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]

def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT / 'scripts' / name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class AuthProviderCompositionTest(unittest.TestCase):
    def test_repository_checker(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(load_script('check-auth-provider-composition.py').main(), 0)


    def test_selected_crypto_inventory_rejects_duplicates_scope_changes_and_qualification(self):
        module = load_script('check-auth-provider-composition.py')
        original = json.loads(module.AUTHORITY.read_text())
        mutations = []
        changed = deepcopy(original); changed['path_classifications'].append(deepcopy(changed['path_classifications'][-1])); mutations.append(changed)
        changed = deepcopy(original); changed['summary']['classified_paths'] = True; mutations.append(changed)
        for key, value in [('AccountsV5_gate', True), ('claim_credit', 0),
                           ('entrypoints', []), ('native_HTTP_qualified', False),
                           ('primitive_provider', 'unreviewed')]:
            changed = deepcopy(original); changed['path_classifications'][-1][key] = value; mutations.append(changed)
        for key, value in [('selected_legacy_key_bytes_min', 32),
                           ('hs256_minimum_scope', 'all legacy and durable keys'),
                           ('selected_legacy_implicit_keys_or_TTLs', 0)]:
            changed = deepcopy(original); changed['key_contracts'][key] = value; mutations.append(changed)
        with tempfile.TemporaryDirectory() as directory:
            authority = Path(directory) / 'authority.json'
            for changed in mutations:
                authority.write_text(json.dumps(changed))
                with patch.object(module, 'AUTHORITY', authority), self.assertRaises(SystemExit):
                    module.main()
        with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(module.main(), 0)

class SelectedAuthoritySourceContractTest(unittest.TestCase):
    def setUp(self):
        self.module = load_script('check-trnm-server.py')
        self.sources = {Path(p): (ROOT / p).read_text() for p in self.module.SELECTED_AUTH_APP_FULL_SOURCE_SHA256}
        self.status = json.loads((ROOT / 'docs/status/TRNM_SERVER_STATUS.json').read_text())
        self.contract = json.loads((ROOT / 'contracts/server/rust-server-vertical-slice.v1.json').read_text())
        self.vertical = json.loads((ROOT / 'docs/status/RUST_SERVER_VERTICAL_SLICE_STATUS.json').read_text())

    def validate(self):
        self.module.validate_selected_auth_app_source(self.sources, self.status, self.contract, self.vertical)

    def test_exact_source_config_and_app_inventory_is_registered_without_runtime_credit(self):
        self.validate()
        self.assertEqual(len(self.sources), 15)
        self.assertFalse(self.status['selected_auth_authority_source']['native_HTTP_qualified'])

    def test_each_document_rejects_extra_missing_coerced_or_promoted_source_facts(self):
        for name in ('status', 'contract', 'vertical'):
            document = getattr(self, name)
            original = deepcopy(document['selected_auth_authority_source'])
            for key, value in original.items():
                changed = deepcopy(original)
                changed[key] = 1 if type(value) is bool else (True if type(value) is int else None)
                document['selected_auth_authority_source'] = changed
                with self.subTest(document=name, field=key), self.assertRaises(SystemExit):
                    self.validate()
                changed = deepcopy(original); del changed[key]
                document['selected_auth_authority_source'] = changed
                with self.subTest(document=name, missing=key), self.assertRaises(SystemExit):
                    self.validate()
            changed = deepcopy(original); changed['native_claim'] = False
            document['selected_auth_authority_source'] = changed
            with self.subTest(document=name, extra=True), self.assertRaises(SystemExit): self.validate()
            document['selected_auth_authority_source'] = original
        self.validate()

    def test_whole_source_bindings_reject_runtime_changes_even_with_old_markers_in_comments(self):
        for path, original in list(self.sources.items()):
            self.sources[path] = original + '\n// unchanged marker strings cannot restore exact executable bytes\n'
            with self.subTest(path=path), self.assertRaises(SystemExit): self.validate()
            self.sources[path] = original
        self.validate()

    def test_canonical_environment_rejects_wip_names_unknowns_and_duplicate_lookup_entries(self):
        original = self.contract['configuration']['environment'][:]
        for altered in (original + ['TRNM_SERVER_ACCESS_TOKEN_PUBLIC_KEY'],
                        original + [original[0]], original[:-1],
                        ['TRNM_SERVER_HTTP_BIND'] + original[1:]):
            self.contract['configuration']['environment'] = altered
            with self.assertRaises(SystemExit): self.validate()
        self.contract['configuration']['environment'] = original
        self.validate()

if __name__ == '__main__':
    unittest.main()
