"""Diagnostic evidence must never turn raw fixture DDL into Accounts admission."""
import copy
import importlib.util
from pathlib import Path
import unittest
import json
import re
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('accounts_capture', ROOT / 'scripts/accounts-capture-diagnostic.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class AccountsCaptureDiagnostic(unittest.TestCase):
    def result(self):
        metadata = {'rows':[{'schema_version':'4', 'storage_writer_epoch':'4', 'upgrade_source_commit':'a'*40}]}
        return dict(schema='trillionnium.accounts-raw-fixture-case.v1', profile='postgresql', case='baseline',
                    source_commit='a'*40, fixture_execution='raw-sql5-after-real-storage-v4',
                    metadata_retained=True, v4_apply_source_commit=None, metadata_before=metadata,
                    metadata_after=copy.deepcopy(metadata), storage_v4_refusal='authoritative_schema_upgrade_incomplete',
                    accounts_v5_refusal='schema5_native_catalog_capture_pending', drift_rejection=None,
                    native_migration_executed=False, schema5_activation=False, compatibility_credit=False, accepted=False)

    def test_accepts_only_diagnostic_result(self):
        M.validate_result(self.result(), 'postgresql', 'baseline', 'a'*40)

    def test_rejects_credit_and_metadata_mutations(self):
        for field in ('native_migration_executed','schema5_activation','compatibility_credit','accepted','metadata_retained'):
            result = self.result(); result[field] = not result[field]
            with self.subTest(field=field), self.assertRaises(AssertionError):
                M.validate_result(result, 'postgresql', 'baseline', 'a'*40)
        for field, value in (('schema_version','5'), ('upgrade_source_commit','b'*40), ('storage_writer_epoch','5')):
            result = self.result()
            result['metadata_before']['rows'][0][field] = value
            result['metadata_after'] = copy.deepcopy(result['metadata_before'])
            with self.subTest(field=field), self.assertRaises(AssertionError):
                M.validate_result(result, 'postgresql', 'baseline', 'a'*40)

    def test_requires_real_drift_rejection(self):
        for reason in (None, 'database_unavailable'):
            result = self.result(); result.update(case='type', drift_rejection=reason)
            with self.assertRaises(AssertionError): M.validate_result(result, 'postgresql', 'type', 'a'*40)

    def test_gate_and_selection_stay_closed(self):
        gate = (ROOT / 'crates/trnm-persistence-pg/src/schema_parts/account_catalog.rs').read_text()
        self.assertIn('const ACCOUNT_CATALOG_CAPTURE_READY: bool = false;', gate)
        source = (ROOT / 'scripts/schema_source_selection.py').read_text()
        self.assertIn("if target is SchemaTarget.NakamaAccountsV5:\n            raise SelectionError('schema5_native_catalog_capture_pending')", source)
        rust = (ROOT / 'crates/trnm-persistence-pg/src/schema.rs').read_text()
        self.assertIn('#[cfg(test)]\n#[path = "schema_parts/account_capture_diagnostic.rs"]', rust)

    def test_no_arbitrary_dsn_and_owned_container_required(self):
        with patch.dict(M.os.environ, TRNM_RUN_ID='123-postgresql'), patch.object(M, 'run') as command:
            with self.assertRaises(ValueError): M.verify_container('postgresql', 'production', ROOT)
            command.assert_not_called()
        source = (ROOT / 'scripts/accounts-capture-diagnostic.py').read_text()
        self.assertNotIn('TRNM_DATABASE_URL', source)
        self.assertIn("'CREATE DATABASE ' + database", source)
        self.assertNotIn('CREATE ROLE', source)
        self.assertNotIn('CREATE USER', source)

class AccountsPacketCompleteness(unittest.TestCase):
    def packet(self, root):
        # Synthetic unit fixture only. It is never uploaded or called native evidence.
        commit, tree, profile = 'a'*40, 'b'*40, 'postgresql'
        identity = dict(schema='trillionnium.accounts-raw-fixture-identity.v1',
                        profile=profile, commit=commit, tree=tree, cases=list(M.CASES)+['trigger'],
                        execution='raw-sql5-after-real-storage-v4', **M.CLAIMS)
        token = M.BINDING.verify_binding(ROOT, profile=profile)
        M.BINDING.write_annex(token, ROOT, root, commit=commit, tree=tree)
        sources = {}
        for name in M.READERS:
            path = 'crates/trnm-persistence-pg/src/schema_parts/' + name
            data = (ROOT / path).read_bytes(); (root/name).write_bytes(data)
            sources[path] = {'git_blob_sha1':M.BINDING.blob(data), 'sha256':M.BINDING.sha(data)}
        M.write(root, 'reader-source-binding.json', dict(commit=commit, tree=tree, sources=sources,
                raw_observation_files=list(M.RAW_KINDS), binding_status='diagnostic-observations-only', activation=False))
        diagnostic = (root/'account_capture_diagnostic.rs').read_text()
        queries = {name:json.loads(query) for name,query in re.findall(r'\("([a-z]+)", ("[^"\n]*")\)', diagnostic.split('const QUERIES:', 1)[1])}
        raw = {kind:dict(query=query, rows=[{'synthetic':'unit-only'}]) for kind,query in queries.items()}
        for case in identity['cases']:
            result = AccountsCaptureDiagnostic().result(); result['case'] = case
            result['drift_rejection'] = None if case == 'baseline' else 'schema5_account_index_drift'
            M.write(root, case+'-result.json', result)
            M.write(root, case+'-raw.json', raw)
            if case != 'baseline': M.write(root, case+'-altered-raw.json', raw)
            (root/(case+'-test.log')).write_text('synthetic unit fixture, not execution')
        M.write(root, 'summary.json', dict(profile=profile, cases=identity['cases'], raw_capture_passed=True,
                typed_migration_qualified=False, **M.CLAIMS))
        image = json.loads((ROOT/'config/database-test-images.json').read_text())['profiles'][profile]
        M.write(root, 'repo-digests.json', [image['image']])
        (root/'database-version.txt').write_text(image['version_output'])
        (root/'image-id.txt').write_text('sha256:'+'c'*64)
        return identity, profile, commit, tree

    def test_optimization_is_rejected_before_operations(self):
        result = subprocess.run([sys.executable, '-O', str(ROOT/'scripts/accounts-capture-diagnostic.py'), '--help'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('rejects Python optimization', result.stderr)

    def test_required_raw_reader_and_annex_files_cannot_be_omitted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); arguments = self.packet(root)
            M.validate_packet(root, *arguments)
            names = ['baseline-raw.json', 'type-altered-raw.json', 'reader-source-binding.json',
                     'account_native_columns.rs', 'schema-source-selection.json', 'schema-source-head.json',
                     'full-schema-source/migrations/postgresql/0005_nakama_accounts_up.sql']
            for name in names:
                path = root/name; data = path.read_bytes(); path.unlink()
                with self.subTest(name=name), self.assertRaises((AssertionError, RuntimeError, OSError)):
                    M.validate_packet(root, *arguments)
                path.write_bytes(data)

    def test_seal_retains_failed_packet_without_complete_credit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); identity, profile, commit, tree = self.packet(root)
            M.write(root, 'identity.json', identity)
            M.write(root, 'container-custody.json', {'id':'d'*64})
            (root/'type-altered-raw.json').unlink()
            with patch.object(M, 'run', side_effect=['', commit, tree]), patch.dict(M.os.environ, CANDIDATE_SHA=commit):
                self.assertFalse(M.seal(root, profile))
            self.assertFalse(json.loads((root/'lifecycle.json').read_text())['capture_complete'])
            self.assertTrue((root/'SHA256SUMS').is_file())

    def test_query_profile_and_source_binding_corruption_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); arguments = self.packet(root)
            for name in ['baseline-raw.json','reader-source-binding.json','summary.json']:
                original = (root/name).read_bytes(); value = json.loads(original)
                if name == 'baseline-raw.json': value['columns']['query'] = 'SELECT 1'
                elif name == 'reader-source-binding.json': value['commit'] = 'c'*40
                else: value['profile'] = 'cockroachdb'
                M.write(root, name, value)
                with self.subTest(name=name), self.assertRaises(AssertionError): M.validate_packet(root, *arguments)
                (root/name).write_bytes(original)


if __name__ == '__main__': unittest.main()
