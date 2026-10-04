"""Internal typed evidence cannot grant public migration or startup admission."""
import copy
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('accounts_typed', ROOT / 'scripts/accounts-typed-diagnostic.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class AccountsTypedDiagnostic(unittest.TestCase):
    def result(self, case='fresh', profile='postgresql'):
        row = dict(singleton='1',schema_version='4',profile=profile,source_commit='a'*40,
                   applied_at_ms='1',chain_digest=M.chain_identity(profile,4),digest_algorithm='ordered-path-git-blob-sha256.v1',
                   storage_writer_epoch='4',upgrade_source_commit='a'*40,v2_apply_source_commit='a'*40,
                   v3_apply_source_commit='a'*40,v4_apply_source_commit=None)
        before = {'query':M.METADATA_PREFIX + 'NULL::TEXT AS v4_apply_source_commit FROM trnm_schema_metadata ORDER BY singleton LIMIT 2','rows':[row]}
        after = copy.deepcopy(before)
        after['query'] = M.METADATA_PREFIX + 'v4_apply_source_commit::TEXT FROM trnm_schema_metadata ORDER BY singleton LIMIT 2'
        after['rows'][0].update(schema_version='5',chain_digest=M.chain_identity(profile,5),v4_apply_source_commit='a'*40)
        cut = None
        if case.startswith('cut_'):
            n = int(case[4:])
            cut = dict(fault_kind='returned-error-after-native-action-catalog-check',after_action=n,reason='schema5_diagnostic_action_cut',observed_prefix=M.prefix4(profile)+(n if profile == 'cockroachdb' else 0),expected_prefix=M.prefix4(profile)+(n if profile == 'cockroachdb' else 0),
                       old_metadata_retained=True,postgres_transaction_rolled_back=profile == 'postgresql',metadata=copy.deepcopy(before),
                       writer_rejection='legacy_storage_writer_not_fenced',import_rejection='schema_unpublished_import_journal_nonempty',blocked_resume_catalog_unchanged=True)
        return dict(schema='trillionnium.accounts-internal-typed-case.v1',profile=profile,case=case,
                    database='trnm_accounts_typed_'+case,source_commit='a'*40,execution='internal-admitted-typed-4-to-5',
                    publisher_scope='same-candidate-real-storage4-publisher-only',internal_typed_migration_executed=True,
                    storage_before=M.storage_fixture(),storage_after=M.storage_fixture(),populated_storage_retained=True,
                    corruption_rejections=[dict(reason=reason,data_retained=True,metadata_retained=True,catalog_retained=True) for reason in (['schema_storage_projection_digest_invalid','schema_storage_known_witness_invalid','schema_storage_native_projection_invalid'] if case == 'populated' else [])],
                    all_prior_publishers_preserved=True,preserved_prior4_publisher='a'*40,metadata_before=before,metadata_after=after,
                    applied_steps=1,replay_applied_steps=0,replay_metadata_unchanged=True,action_count=5,interruption=cut,
                    barrier_rejection={'writer':'legacy_storage_writer_not_fenced','import':'schema_unpublished_import_journal_nonempty'}.get(case),
                    public_accounts_refusal='schema5_native_catalog_capture_pending',public_accounts_after_migration_refusal='schema5_native_catalog_capture_pending',public_storage_refusal='authoritative_schema_target_downgrade_rejected',
                    missing_writer_rejection='legacy_storage_writer_barrier_required',**M.CLAIMS)

    def test_each_profile_and_all_statement_cuts(self):
        for profile in ('postgresql','cockroachdb'):
            self.assertEqual(M.cases(profile),['fresh','populated','writer','import','cut_1','cut_2','cut_3','cut_4','cut_5'])
            for case in M.cases(profile): M.validate_result(self.result(case,profile),profile,case,'a'*40)

    def test_rejects_qualification_or_fake_publisher(self):
        for field in M.CLAIMS:
            result = self.result(); result[field] = True
            with self.subTest(field=field),self.assertRaises(AssertionError): M.validate_result(result,'postgresql','fresh','a'*40)
        for field in ('source_commit','v2_apply_source_commit','v3_apply_source_commit','v4_apply_source_commit','applied_at_ms'):
            result = self.result(); result['metadata_after']['rows'][0][field] = 'b'*40
            with self.subTest(field=field),self.assertRaises(AssertionError): M.validate_result(result,'postgresql','fresh','a'*40)

    def test_rejects_interruption_and_barrier_lies(self):
        for field,value in [('postgres_transaction_rolled_back',False),('old_metadata_retained',False),
                            ('blocked_resume_catalog_unchanged',False),('writer_rejection',None),
                            ('import_rejection',None),('observed_prefix',31),('after_action',2)]:
            result = self.result('cut_1'); result['interruption'][field] = value
            with self.subTest(field=field),self.assertRaises(AssertionError): M.validate_result(result,'postgresql','cut_1','a'*40)

    def test_public_gate_and_sealed_test_only_constructor(self):
        source = (ROOT/'crates/trnm-persistence-pg/src/schema_parts/migrate.rs').read_text()
        self.assertIn('mod migration_admission {',source)
        self.assertIn('#[cfg(test)]\n        pub(crate) fn diagnostic',source)
        self.assertIn('require_account_catalog_capture(target)?;',source)
        self.assertNotIn('std::env',source)
        self.assertIn('matches!(revision_target.version, 4 | 5)',source)
        self.assertNotIn('revision_target.version >= 4',source)
        gate = (ROOT/'crates/trnm-persistence-pg/src/schema_parts/account_catalog.rs').read_text()
        self.assertIn('const ACCOUNT_CATALOG_CAPTURE_READY: bool = false;',gate)
        selection = (ROOT/'scripts/schema_source_selection.py').read_text()
        self.assertIn("if target is SchemaTarget.NakamaAccountsV5:\n            raise SelectionError('schema5_native_catalog_capture_pending')",selection)
        test = (ROOT/'crates/trnm-persistence-pg/src/schema_parts/account_typed_diagnostic.rs').read_text()
        self.assertIn('migrate_authoritative_schema_admitted',test)
        self.assertNotIn('batch_execute(action.sql)',test)
        self.assertNotIn('batch_execute(fixture.sql)',test)

    def test_revision_preflight_and_live_workflow_contract(self):
        source = (ROOT/'crates/trnm-persistence-pg/src/schema_parts/migrate.rs').read_text()
        self.assertEqual(source.count('preflight_storage_revision('),6)
        self.assertEqual(source.count(', original.version)?;'),6)
        preflight = (ROOT/'crates/trnm-persistence-pg/src/schema_parts/import_preflight.rs').read_text()
        self.assertIn('published_version == 3',preflight)
        self.assertIn('validate_native_storage_import_row(row)?;',preflight)
        workflow = (ROOT/'.github/workflows/trnm-server-live.yml').read_text()
        self.assertIn('tests.control_plane.test_accounts_typed_diagnostic -v',workflow)

    def test_rejects_storage_or_corruption_retention_lies(self):
        for field in ('data_retained','metadata_retained','catalog_retained'):
            result = self.result('populated'); result['corruption_rejections'][0][field] = False
            with self.subTest(field=field), self.assertRaises(AssertionError):
                M.validate_result(result,'postgresql','populated','a'*40)
        result = self.result(); result['storage_after']['rows'].pop()
        with self.assertRaises(AssertionError): M.validate_result(result,'postgresql','fresh','a'*40)

    def packet(self, output):
        profile,commit,tree = 'postgresql','a'*40,'b'*40
        identity = dict(schema='trillionnium.accounts-internal-typed-identity.v1',profile=profile,
                        commit=commit,tree=tree,cases=M.cases(profile),execution='internal-admitted-typed-4-to-5',
                        publisher_scope='same-candidate-real-storage4-publisher-only',**M.CLAIMS)
        token = M.B.verify_binding(ROOT,profile=profile)
        M.B.write_annex(token,ROOT,output,commit=commit,tree=tree)
        sources = {}
        for path in M.SOURCE_PATHS:
            data = (ROOT/path).read_bytes(); (output/Path(path).name).write_bytes(data)
            sources[path] = dict(git_blob_sha1=M.B.blob(data),sha256=M.B.sha(data))
        M.write(output,'executor-source-binding.json',dict(commit=commit,tree=tree,sources=sources,execution='same-internal-typed-executor-cfg-test-admission',activation=False))
        diagnostic = (output/'account_capture_diagnostic.rs').read_text()
        queries = {name:json.loads(query) for name,query in re.findall(r'\("([a-z]+)", ("[^"\n]*")\)',diagnostic.split('const QUERIES:',1)[1])}
        raw = {kind:dict(query=query,rows=[{'synthetic':'unit-only-not-native-evidence'}]) for kind,query in queries.items()}
        for case in identity['cases']:
            M.write(output,case+'-result.json',self.result(case))
            M.write(output,case+'-raw.json',raw)
            if case.startswith('cut_'): M.write(output,case+'-cut-raw.json',raw)
            (output/(case+'-test.log')).write_text('synthetic unit fixture only')
        M.write(output,'summary.json',dict(profile=profile,cases=identity['cases'],internal_typed_diagnostic_passed=True,**M.CLAIMS))
        pinned = json.loads((ROOT/'config/database-test-images.json').read_text())['profiles'][profile]
        M.write(output,'repo-digests.json',[pinned['image']]); (output/'database-version.txt').write_text(pinned['version_output'])
        (output/'image-id.txt').write_text('sha256:'+'c'*64)
        return identity,profile,commit,tree

    def test_packet_complete_and_cuts_cannot_be_omitted(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory); args = self.packet(output)
            M.validate_packet(output,*args)
            (output/'cut_5-cut-raw.json').unlink()
            with self.assertRaises(AssertionError): M.validate_packet(output,*args)

    def test_packet_binds_executor_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory); args = self.packet(output)
            (output/'migrate.rs').write_text('different executor')
            with self.assertRaises(AssertionError): M.validate_packet(output,*args)


if __name__ == '__main__': unittest.main()
