"""Pure source selection tests; no subprocess, Git, network, native or DB fixtures.
Run only this file. Historical tests use archived genuine lock4/authority4/checker4
bytes plus SHA-pinned original8 SQL; this test fixture is never current authority.
"""
from __future__ import annotations
import copy
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pickle
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('schema_source_selection_under_test', ROOT / 'scripts/schema_source_selection.py')
KERNEL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(KERNEL)


def blob(data):
    return hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


class SourceSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture_path=ROOT/'tests/control_plane/fixtures/schema_source_selection_historical4.json'
        fixture=json.loads(fixture_path.read_bytes())
        if fixture['schema']!='trillionnium.test-only-genuine-historical4-raw-source.v1':
            raise RuntimeError('explicit_test_only_historical_fixture_required')
        cls.historical_raw={}
        for row in fixture['raw_files']:
            raw=base64.b64decode(row['base64'],validate=True)
            if len(raw)!=row['bytes'] or hashlib.sha256(raw).hexdigest()!=row['sha256'] or blob(raw)!=row['git_blob_sha1']:
                raise RuntimeError('archived_historical_fixture_byte_drift')
            cls.historical_raw[row['path']]=raw
        if set(cls.historical_raw)!={KERNEL.LOCK_PATH,KERNEL.AUTHORITY_PATH,KERNEL.VALIDATOR_PATH}:
            raise RuntimeError('historical_fixture_closed_files')
        for relative,sha in {KERNEL.VALIDATOR_PATH:KERNEL.HISTORICAL_VALIDATOR_SHA256,
                             KERNEL.AUTHORITY_PATH:KERNEL.HISTORICAL_AUTHORITY_SHA256}.items():
            if hashlib.sha256(cls.historical_raw[relative]).hexdigest()!=sha:
                raise RuntimeError('historical_fixture_is_not_genuine')
        if len(fixture['original8_sql'])!=8:raise RuntimeError('original8_historical_denominator')
        for row in fixture['original8_sql']:
            raw=(ROOT/row['path']).read_bytes()
            if len(raw)!=row['bytes'] or hashlib.sha256(raw).hexdigest()!=row['sha256'] or blob(raw)!=row['git_blob_sha1']:
                raise RuntimeError('historical_SQL_bytes_drift')
        cls.cases = 0

    @classmethod
    def tearDownClass(cls):
        print('PURE_SOURCE_SELECTION_NEGATIVE_ASSERTIONS=' + str(cls.cases), file=sys.stderr)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pure-source-selection-')
        self.addCleanup(self.temp.cleanup)
        self.fixture = Path(self.temp.name) / 'current'
        self.copy_source(ROOT, self.fixture)

    def copy_source(self, source, target):
        relative_paths = [KERNEL.LOCK_PATH, KERNEL.AUTHORITY_PATH, KERNEL.VALIDATOR_PATH]
        for profile in KERNEL.PROFILES:
            relative_paths.extend(path.relative_to(source).as_posix()
                                  for path in sorted((source / 'migrations' / profile).glob('*.sql')))
        for relative in relative_paths:
            (target / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, target / relative)
        return target

    def historical_fixture(self,target):
        self.copy_source(ROOT,target)
        for profile in KERNEL.PROFILES:
            (target/f'migrations/{profile}/0005_nakama_accounts_up.sql').unlink()
        for relative,raw in self.historical_raw.items():
            (target/relative).write_bytes(raw)
        return target

    def reset_current(self):
        shutil.rmtree(self.fixture)
        self.copy_source(ROOT, self.fixture)

    def select(self, profile='postgresql', root=None):
        return KERNEL.verify_current_source_selection(root or self.fixture, profile=profile)

    def negative(self, call, reason=None):
        type(self).cases += 1
        with self.assertRaises(KERNEL.SelectionError) as caught:
            call()
        if reason:
            self.assertIn(reason, str(caught.exception))

    def edit_json(self, relative, mutate):
        path = self.fixture / relative
        value = json.loads(path.read_bytes())
        mutate(value)
        path.write_bytes(encoded(value))

    def relock(self, profile, revision):
        path = f'migrations/{profile}/{revision:04}_' + {
            1:'foundation',2:'storage_timestamps',3:'storage_jsonb',4:'storage_source_import',5:'nakama_accounts'}[revision] + '_up.sql'
        digest = blob((self.fixture / path).read_bytes())
        self.edit_json(KERNEL.LOCK_PATH, lambda lock: lock['profiles'][profile]['ordered_files'][revision-1].update(git_blob_sha1=digest))

    def test_current_both_complete_frontier5_selected4(self):
        for profile in KERNEL.PROFILES:
            with self.subTest(profile=profile):
                token = self.select(profile)
                doc = KERNEL.current_selection_document(token)
                self.assertEqual(doc['schema'], KERNEL.CURRENT_SCHEMA)
                self.assertEqual(doc['source']['frontier_schema_version'], 5)
                self.assertEqual(doc['selection']['execution_chain_digest'], KERNEL.ORIGINAL4_DIGESTS[profile])
                self.assertEqual(doc['selection']['executed_migration_file_count'], 4)
                self.assertEqual(doc['selection']['execution_schema_version'], 4)
                self.assertEqual(doc['selection']['storage_writer_epoch'], 4)
                self.assertEqual(doc['selection']['table_count'], 12)
                for source_profile in KERNEL.PROFILES:
                    self.assertEqual(doc['source']['profiles'][source_profile]['file_count'], 5)
                    self.assertEqual(len(doc['source']['profiles'][source_profile]['ordered_files']), 5)
                    for entry in doc['source']['profiles'][source_profile]['ordered_files']:
                        data = (self.fixture / entry['path']).read_bytes()
                        self.assertEqual(entry['git_blob_sha1'], blob(data))
                        self.assertEqual(entry['sha256'], hashlib.sha256(data).hexdigest())
                        self.assertEqual(entry['bytes'], len(data))
                self.assertNotEqual(doc['source']['profiles']['postgresql']['chain_digest'],
                                    doc['source']['profiles']['cockroachdb']['chain_digest'])
                self.assertTrue(doc['claims']['source_identity_verified'])
                self.assertTrue(doc['claims']['current_head_eligible'])
                for key in ('exact_git_head_verified','runtime_execution_verified','native_catalog_verified',
                            'account_transfer_admitted','accepted','compatibility_credit','production_ready'):
                    self.assertIs(doc['claims'][key], False)
                KERNEL.verify_current_selection_envelope(token, encoded(doc))

    def test_complete_unselected_fifth_before_policy_and_prefix(self):
        for profile in KERNEL.PROFILES:
            self.reset_current()
            fifth = self.fixture / f'migrations/{profile}/0005_nakama_accounts_up.sql'
            fifth.unlink()
            self.edit_json(KERNEL.AUTHORITY_PATH, lambda auth: auth['authority']['source_execution_selection_policy'].update(default_target='NakamaAccountsV5'))
            self.negative(lambda: self.select('postgresql'), 'full_source_validation_failed')

    def test_rehashed_fifth_semantic_drift_in_both_profiles(self):
        replacements = [(b"DEFAULT 'en'",b"DEFAULT 'fr'"),
                        (b'ON DELETE CASCADE',b'ON DELETE RESTRICT'),
                        (b'preferences JSONB NOT NULL DEFAULT',b'preferences TEXT NOT NULL DEFAULT'),
                        (b'-- trnm:action nakama_system_user',b'-- trnm:action skipped_seed'),
                        (b'COMMIT;',b'-- removed commit')]
        for profile in KERNEL.PROFILES:
            for before, after in replacements:
                with self.subTest(profile=profile, drift=before):
                    self.reset_current()
                    path = self.fixture / f'migrations/{profile}/0005_nakama_accounts_up.sql'
                    self.assertIn(before,path.read_bytes())
                    path.write_bytes(path.read_bytes().replace(before,after))
                    self.relock(profile,5)
                    self.negative(lambda:self.select('postgresql'),'full_source_validation_failed')

    def test_rehashed_old4_for_each_profile_cannot_change_prefix(self):
        for profile in KERNEL.PROFILES:
            for revision in (1,2,3,4):
                self.reset_current()
                file = sorted((self.fixture / 'migrations' / profile).glob('*.sql'))[revision-1]
                file.write_bytes(file.read_bytes()+b'\n-- forged historical byte\n')
                self.relock(profile,revision)
                self.negative(self.select,'full_source_validation_failed')

    def test_lock_unknown_missing_version_path_order_skip(self):
        mutations = [
            lambda lock:lock.update(unknown=True),
            lambda lock:lock.pop('accounts_v5_activation'),
            lambda lock:lock.update(accounts_v5_activation='ready'),
            lambda lock:lock.update(default_runtime_schema_version=True),
            lambda lock:lock.update(default_runtime_schema_version='4'),
            lambda lock:lock.update(default_runtime_schema_version=4.0),
            lambda lock:lock.update(schema_version=True),
            lambda lock:lock.update(schema_version=5.0),
            lambda lock:lock.update(schema_version='5'),
            lambda lock:lock.update(schema_version=4),
            lambda lock:lock['profiles'].pop('cockroachdb'),
            lambda lock:lock['profiles'].update(other={}),
            lambda lock:lock['profiles']['postgresql'].update(unknown=1),
            lambda lock:lock['profiles']['postgresql']['ordered_files'][4].update(unknown=1),
            lambda lock:lock['profiles']['postgresql']['ordered_files'].pop(),
            lambda lock:lock['profiles']['postgresql']['ordered_files'].pop(1),
            lambda lock:lock['profiles']['postgresql']['ordered_files'].reverse(),
            lambda lock:lock['profiles']['postgresql']['ordered_files'][4].update(path='migrations/cockroachdb/0005_nakama_accounts_up.sql'),
            lambda lock:lock['profiles']['postgresql']['ordered_files'][4].update(path='migrations/postgresql/../cockroachdb/0005_nakama_accounts_up.sql'),
            lambda lock:lock['profiles']['postgresql']['ordered_files'][4].update(git_blob_sha1=True),
            lambda lock:lock['rules'].update(ordered_files_are_complete=1),
            lambda lock:lock['rules'].update(unlisted_sql_is_failure=False),
            lambda lock:lock['rules'].update(unknown=True),
            lambda lock:lock.update(claim_boundary='runtime verified'),
        ]
        for mutation in mutations:
            self.reset_current()
            self.edit_json(KERNEL.LOCK_PATH,mutation)
            self.negative(self.select)

    def test_lock_duplicate_nonfinite_top_type_and_budget(self):
        values = [b'null',b'[]',b'"object"',b'{"schema_version":5,"schema_version":4}',
                  b'{"schema_version":NaN}',b'{"schema_version":Infinity}',
                  b'{' + b' '*(KERNEL.MAX_DOCUMENT_BYTES+1) + b'}']
        for raw in values:
            self.reset_current()
            (self.fixture / KERNEL.LOCK_PATH).write_bytes(raw)
            self.negative(self.select)

    def test_inventory_unlisted_nested_symlink_and_validator_replacement(self):
        for change in ('unlisted','nested','sql_symlink','parent_symlink','validator_replacement','oversized_SQL'):
            self.reset_current()
            if change=='unlisted':
                (self.fixture / 'migrations/postgresql/9999_extra_up.sql').write_bytes(b'SELECT 1;')
            elif change=='nested':
                (self.fixture / 'migrations/postgresql/nested').mkdir()
            elif change=='sql_symlink':
                target=self.fixture / 'migrations/postgresql/0005_nakama_accounts_up.sql'
                target.unlink()
                target.symlink_to(ROOT / 'migrations/postgresql/0005_nakama_accounts_up.sql')
            elif change=='parent_symlink':
                target=self.fixture / 'migrations/postgresql'
                shutil.rmtree(target)
                target.symlink_to(ROOT / 'migrations/postgresql',target_is_directory=True)
            elif change=='validator_replacement':
                marker=self.fixture / 'should_never_be_created'
                script=self.fixture / KERNEL.VALIDATOR_PATH
                script.write_text(script.read_text()+f'\nopen({str(marker)!r}, "w").write("executed")\n')
            else:
                (self.fixture / 'migrations/postgresql/0005_nakama_accounts_up.sql').write_bytes(b' '*(KERNEL.MAX_SOURCE_BYTES+1))
            self.negative(self.select)
            self.assertFalse((self.fixture/'should_never_be_created').exists())

    def test_bounded_directory_iteration_stops_at_first_over_budget(self):
        target=self.fixture/'migrations/postgresql'
        for index in range(2005):
            (target/f'non_sql_{index}.txt').write_bytes(b'')
        consumed=[]
        original=os.scandir
        class Stream:
            def __init__(self,fd):self.inner=original(fd)
            def __enter__(self):self.inner.__enter__();return self
            def __exit__(self,*args):return self.inner.__exit__(*args)
            def __iter__(self):return self
            def __next__(self):
                value=next(self.inner);consumed.append(value.name)
                if len(consumed)>17:raise AssertionError('unbounded directory inventory was consumed')
                return value
        with mock.patch.object(os,'listdir',side_effect=AssertionError('listdir forbidden')), mock.patch.object(os,'scandir',Stream):
            self.negative(self.select,'profile_inventory_budget')
        self.assertEqual(len(consumed),17)

    def test_fifo_regular_file_boundary_rejects_without_blocking(self):
        for relative in (KERNEL.VALIDATOR_PATH,'migrations/postgresql/0005_nakama_accounts_up.sql',KERNEL.AUTHORITY_PATH):
            self.reset_current()
            path=self.fixture/relative
            path.unlink()
            os.mkfifo(path)
            self.negative(self.select,'source_not_regular_or_budget')

    def test_source_read_is_capped_even_if_file_grows_after_stat(self):
        original_fdopen=os.fdopen
        read_sizes=[]
        class GrowingSource:
            def __init__(self,fd,mode):self.real=original_fdopen(fd,mode)
            def __enter__(self):return self
            def __exit__(self,*args):self.real.close()
            def read(self,size):
                read_sizes.append(size)
                if size!=KERNEL.MAX_SOURCE_BYTES+1:raise AssertionError('source read was not bounded')
                return b'x'*size
        with mock.patch.object(KERNEL.os,'fdopen',GrowingSource):
            self.negative(self.select,'source_read_budget')
        self.assertEqual(read_sizes,[KERNEL.MAX_SOURCE_BYTES+1])

    def test_policy_types_values_unknown_fields_and_native_claims(self):
        for key in ('full_source_frontier_schema_version',):
            for bad in (True,'5',5.0,4):
                self.reset_current()
                self.edit_json(KERNEL.AUTHORITY_PATH,lambda auth:auth['authority']['source_execution_selection_policy'].update({key:bad}))
                self.negative(self.select,'source_selection_policy')
        for target in ('StorageV4','NakamaAccountsV5'):
            for key in ('selected_schema_version','executed_migration_file_count','storage_writer_epoch','table_count'):
                for bad in (True,'4',4.0,0):
                    self.reset_current()
                    self.edit_json(KERNEL.AUTHORITY_PATH,lambda auth:auth['authority']['source_execution_selection_policy']['targets'][target].update({key:bad}))
                    self.negative(self.select,'source_selection_policy')
        mutations=[
            lambda a:a.update(schema='wrong'),
            lambda a:a.update(project_id='wrong'),
            lambda a:a.update(unknown=True),
            lambda a:a['authority'].update(migration_root='database/schema/v2'),
            lambda a:a['authority'].update(metadata_table='users'),
            lambda a:a['authority'].update(profiles=['postgresql']),
            lambda a:a['migration_lock'].update(validator='another_checker.py'),
            lambda a:a['authority'].update(unknown=True),
            lambda a:a['authority']['source_execution_selection_policy'].update(unknown=True),
            lambda a:a['authority']['source_execution_selection_policy'].update(default_target='NakamaAccountsV5'),
            lambda a:a['authority']['source_execution_selection_policy'].update(full_source_validation_required=False),
            lambda a:a['authority']['source_execution_selection_policy'].update(selected_prefix_must_be_initial_contiguous=False),
            lambda a:a['authority']['source_execution_selection_policy']['targets'].update(other={}),
            lambda a:a['authority']['source_execution_selection_policy']['targets']['StorageV4'].update(unknown=True),
            lambda a:a['authority']['source_execution_selection_policy']['historical_source4'].update(current_head_eligible=True),
            lambda a:a['authority']['source_execution_selection_policy']['claims'].update(accepted=True),
            lambda a:a['adapter_abi']['required_tables'].pop(),
            lambda a:a['adapter_abi'].update(unknown=True),
            lambda a:a['migration_lock'].update(unknown=True),
            lambda a:a['migration_lock'].update(runtime_execution_credit=True),
            lambda a:a['migration_lock'].update(path='another.lock'),
            lambda a:a['claim_boundary'].update(database_durable=True),
            lambda a:a['claim_boundary'].update(unknown=True),
            lambda a:a['authority']['accounts_v5_source_candidate'].update(unknown=True),
            lambda a:a['authority']['accounts_v5_source_candidate'].update(storage_writer_epoch=True),
        ]
        for key in ('schema_version','default_runtime_schema_version','latest_supported_schema_version'):
            mutations.extend(lambda a,key=key,bad=bad:a['authority'].update({key:bad}) for bad in (True,'4',4.0,0))
        for key in ('native_catalog_observations_bound','native_activation_allowed','default_runtime_promotion_allowed',
                    'repository_service_routes_implemented','account_transfer_implemented','accounts_v5_backups_restores_qualified','accepted'):
            mutations.extend(lambda a,key=key,bad=bad:a['authority']['accounts_v5_source_candidate'].update({key:bad}) for bad in (True,0,None))
        for mutation in mutations:
            self.reset_current()
            self.edit_json(KERNEL.AUTHORITY_PATH,mutation)
            self.negative(self.select)

    def test_typed_accounts_target_rejected_before_any_path_io(self):
        class PoisonPath:
            def __fspath__(self):
                raise AssertionError('no path I/O may occur for AccountsV5 or wrong typed target')
        self.negative(lambda:KERNEL.verify_current_source_selection(PoisonPath(),profile='postgresql',target=KERNEL.SchemaTarget.NakamaAccountsV5),
                      'schema5_native_catalog_capture_pending')
        for bad in (True,5,'StorageV4','NakamaAccountsV5',None):
            self.negative(lambda:KERNEL.verify_current_source_selection(PoisonPath(),profile='postgresql',target=bad),'typed_schema_target_required')
        for bad in (True,0,'postgres','POSTGRESQL',None):
            self.negative(lambda:KERNEL.verify_current_source_selection(PoisonPath(),profile=bad),'source_profile_required')

    def test_envelope_all_counts_digest_order_skip_unknown_credit(self):
        token=self.select()
        original=KERNEL.current_selection_document(token)
        def wrong_frontier(doc):
            doc['source']['profiles']['cockroachdb']['ordered_files'].pop()
        mutations=[
            lambda d:d.update(unknown=1),
            lambda d:d.update(schema=KERNEL.HISTORICAL_SCHEMA),
            lambda d:d.update(profile='cockroachdb'),
            lambda d:d['source'].update(unknown=True),
            lambda d:d['selection'].update(unknown=True),
            lambda d:d['selection'].update(target='NakamaAccountsV5'),
            lambda d:d['selection'].update(execution_chain_digest=d['source']['profiles']['postgresql']['chain_digest']),
            lambda d:d['source'].update(frontier_schema_version=4),
            lambda d:d['source'].update(lock_sha256='0'*64),
            lambda d:d['source'].update(mode='historical-source4-only'),
            lambda d:d['source']['profiles']['postgresql']['ordered_files'][4].update(sha256='0'*64),
            lambda d:d['source']['profiles']['postgresql']['ordered_files'][4].update(unknown=True),
            lambda d:d['source']['profiles']['postgresql']['ordered_files'].pop(),
            lambda d:d['selection']['ordered_files'].pop(),
            lambda d:d['selection']['ordered_files'].reverse(),
            lambda d:d['selection']['ordered_files'].__setitem__(3,copy.deepcopy(d['source']['profiles']['postgresql']['ordered_files'][4])),
            wrong_frontier,
        ]
        for key in ('execution_schema_version','storage_writer_epoch','table_count','executed_migration_file_count'):
            for bad in (True,'4',4.0,0,5):
                mutations.append(lambda d,key=key,bad=bad:d['selection'].update({key:bad}))
        for profile in KERNEL.PROFILES:
            for key in ('file_count',):
                for bad in (True,'5',5.0,4):
                    mutations.append(lambda d,profile=profile,key=key,bad=bad:d['source']['profiles'][profile].update({key:bad}))
            for bad in (True,'1',1.0,0):
                mutations.append(lambda d,profile=profile,bad=bad:d['source']['profiles'][profile]['ordered_files'][4].update(bytes=bad))
        for key in original['claims']:
            bad=not original['claims'][key]
            mutations.append(lambda d,key=key,bad=bad:d['claims'].update({key:bad}))
            mutations.append(lambda d,key=key:d['claims'].update({key:0}))
        for mutation in mutations:
            doc=copy.deepcopy(original)
            mutation(doc)
            self.negative(lambda:KERNEL.verify_current_selection_envelope(token,encoded(doc)))
        for raw in (b'null',b'[]',b'{"a":1,"a":2}',b'{"number":NaN}',b' '*(KERNEL.MAX_DOCUMENT_BYTES+1)):
            self.negative(lambda:KERNEL.verify_current_selection_envelope(token,raw))
        self.negative(lambda:KERNEL.verify_current_selection_envelope(token,original))

    def test_every_envelope_integer_bool_is_type_strict(self):
        token=self.select()
        original=KERNEL.current_selection_document(token)
        paths=[]
        def walk(value,path):
            if type(value) in (int,bool):paths.append((path,value))
            elif type(value) is dict:
                for key,item in value.items():walk(item,path+(key,))
            elif type(value) is list:
                for index,item in enumerate(value):walk(item,path+(index,))
        walk(original,())
        self.assertGreater(len(paths),25)
        for path,value in paths:
            alternatives=(int(value), str(value)) if type(value) is bool else (bool(value),float(value),str(value))
            for bad in alternatives:
                with self.subTest(path=path,bad_type=type(bad).__name__):
                    document=copy.deepcopy(original)
                    cursor=document
                    for key in path[:-1]:cursor=cursor[key]
                    cursor[path[-1]]=bad
                    self.negative(lambda:KERNEL.verify_current_selection_envelope(token,encoded(document)),
                                  'type_shape_or_value')

    def test_token_constructor_forgery_subclass_pickle_dict_mutation(self):
        token=self.select()
        token_type=type(token)
        with self.assertRaises(TypeError):token_type()
        with self.assertRaises(TypeError):type('Forged',(token_type,),{})
        with self.assertRaises(TypeError):pickle.dumps(token)
        with self.assertRaises(TypeError):copy.deepcopy(token)
        forged=object.__new__(token_type)
        for candidate in (forged,{},KERNEL.current_selection_document(token),object(),None):
            self.negative(lambda:KERNEL.current_selection_document(candidate),'unissued_or_forged')
        doc=KERNEL.current_selection_document(token)
        doc['claims']['accepted']=True
        doc['selection']['table_count']=14
        self.assertIs(KERNEL.current_selection_document(token)['claims']['accepted'],False)
        self.assertEqual(KERNEL.current_selection_document(token)['selection']['table_count'],12)
        with self.assertRaises(TypeError):KERNEL.ORIGINAL4_DIGESTS['postgresql']='0'*64
        for name in ('_issue','issue','_document','document','_selection_api','VerifiedSelection'):
            self.assertFalse(hasattr(KERNEL,name),name)

    def test_genuine_historical4_separate_legacy_only_envelope(self):
        historical=self.historical_fixture(Path(self.temp.name)/'historical')
        for profile in KERNEL.PROFILES:
            token=KERNEL.verify_historical_source4_selection(historical,profile=profile)
            doc=KERNEL.historical_selection_document(token)
            self.assertEqual(doc['schema'],KERNEL.HISTORICAL_SCHEMA)
            self.assertEqual(doc['source']['mode'],'historical-source4-only')
            self.assertEqual(doc['source']['frontier_schema_version'],4)
            self.assertIs(doc['claims']['current_head_eligible'],False)
            self.assertIs(doc['claims']['exact_git_head_verified'],False)
            self.assertEqual(doc['selection']['execution_chain_digest'],KERNEL.ORIGINAL4_DIGESTS[profile])
            KERNEL.verify_historical_selection_envelope(token,encoded(doc))
            self.negative(lambda:KERNEL.current_selection_document(token),'scope_mismatch')
            self.negative(lambda:KERNEL.verify_current_selection_envelope(token,encoded(doc)))
        current=self.select()
        self.negative(lambda:KERNEL.historical_selection_document(current),'scope_mismatch')
        self.negative(lambda:KERNEL.verify_historical_source4_selection(self.fixture,profile='postgresql'),'genuine_original_validator_required')
        self.negative(lambda:self.select(root=historical),'genuine_original_validator_required')
        # Full current checker must reject fake lock4 even if a caller removes SQL5.
        shutil.copyfile(historical/KERNEL.LOCK_PATH,self.fixture/KERNEL.LOCK_PATH)
        for profile in KERNEL.PROFILES:
            (self.fixture/f'migrations/{profile}/0005_nakama_accounts_up.sql').unlink()
        self.negative(self.select,'full_source_validation_failed')

    def test_legacy_relocked_unselected_original4_and_unknown_fields(self):
        historical=self.historical_fixture(Path(self.temp.name)/'historical')
        self.fixture=historical
        path=historical/'migrations/cockroachdb/0004_storage_source_import_up.sql'
        path.write_bytes(path.read_bytes()+b'\n-- relocked byte drift\n')
        self.relock('cockroachdb',4)
        self.negative(lambda:KERNEL.verify_historical_source4_selection(historical,profile='postgresql'),
                      'both_profiles_frozen_initial4_digest_changed')
        historical2=self.historical_fixture(Path(self.temp.name)/'historical2')
        self.fixture=historical2
        self.edit_json(KERNEL.LOCK_PATH,lambda lock:lock.update(unknown=True))
        self.negative(lambda:KERNEL.verify_historical_source4_selection(historical2,profile='postgresql'),'closed_fields')


if __name__=='__main__':
    unittest.main(verbosity=2)
