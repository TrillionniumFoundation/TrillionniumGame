#!/usr/bin/env python3
"""Owned-container internal typed AccountsV5 diagnostic; no public admission."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess

if not __debug__:
    raise RuntimeError('accounts typed diagnostic rejects Python optimization')
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('accounts_capture', ROOT / 'scripts/accounts-capture-diagnostic.py')
RAW = importlib.util.module_from_spec(spec)
spec.loader.exec_module(RAW)
B = RAW.BINDING
run, write, sql, read_json = RAW.run, RAW.write, RAW.sql, RAW.read_json
CLAIMS = dict(process_crash_qualified=False, power_loss_qualified=False, public_migration_qualified=False, schema5_activation=False, startup_qualified=False,
              backup_restore_qualified=False, compatibility_credit=False, accepted=False,
              prior_release_producer_qualified=False)
SOURCE_PATHS = tuple('crates/trnm-persistence-pg/src/schema_parts/' + p for p in
    RAW.READERS + ('account_typed_diagnostic.rs', 'migrate.rs', 'metadata.rs', 'catalog.rs',
                   'import_catalog.rs', 'import_preflight.rs', 'jsonb_backfill.rs')) + (
    'crates/trnm-persistence-pg/src/schema.rs', 'crates/trnm-persistence-pg/schema_build.rs',
    'scripts/accounts-typed-diagnostic.py',
    'scripts/accounts-capture-diagnostic.py')


def cases(profile):
    # Derive every current SQL5 action cut from the byte-locked source; never
    # omit a newly added cut silently or accept another target's case count.
    data = (ROOT / 'migrations' / profile / '0005_nakama_accounts_up.sql').read_text()
    count = len(re.findall(r'^-- trnm:action ', data, re.M))
    assert count == 5, 're-review typed diagnostic matrix on action changes'
    return ['fresh', 'populated', 'writer', 'import'] + ['cut_' + str(i) for i in range(1, count + 1)]


def chain_identity(profile, version):
    locked = json.loads((ROOT / 'migrations/MIGRATION_CHAIN.lock.json').read_text())['profiles'][profile]['ordered_files'][:version]
    payload = b''.join(i.to_bytes(8,'big') + entry['path'].encode() + b'\0' + bytes.fromhex(entry['git_blob_sha1']) for i,entry in enumerate(locked))
    return hashlib.sha256(payload).hexdigest()


def prefix4(profile):
    return sum(len(re.findall(r'^-- trnm:action ', path.read_text(), re.M)) for path in (ROOT / 'migrations' / profile).glob('000[234]_*.sql'))


def capture(profile, container, parent, output):
    output.mkdir(parents=True, exist_ok=False)
    inspected = RAW.verify_container(profile, container, parent)
    commit, tree = run('git', 'rev-parse', 'HEAD'), run('git', 'rev-parse', 'HEAD^{tree}')
    assert commit == os.environ['CANDIDATE_SHA'] and re.fullmatch('[0-9a-f]{40}', commit)
    write(output, 'container-custody.json', {'id':inspected['Id'], 'name':container,
          'image':inspected['Image'], 'pinned_image':inspected['Config']['Image']})
    for name in ('database-version.txt', 'image-id.txt', 'repo-digests.json'):
        (output / name).write_bytes((parent / name).read_bytes())
    # Ordinary source token authorizes ONLY StorageV4. The separate executor
    # receipt records test-only execution and cannot grant a target5 token.
    run('python3', 'scripts/capture-schema-source-selection.py', '--root', str(output),
        '--profile', profile, '--commit', commit, '--tree', tree)
    bindings = {}
    for path in SOURCE_PATHS:
        data = (ROOT / path).read_bytes()
        assert run('git', 'rev-parse', 'HEAD:' + path) == B.blob(data)
        (output / Path(path).name).write_bytes(data)
        bindings[path] = {'git_blob_sha1':B.blob(data), 'sha256':B.sha(data)}
    write(output, 'executor-source-binding.json', {'commit':commit,'tree':tree,'sources':bindings,
          'execution':'same-internal-typed-executor-cfg-test-admission','activation':False})
    expected = cases(profile)
    write(output, 'identity.json', {'schema':'trillionnium.accounts-internal-typed-identity.v1',
          'repository':os.environ['CANDIDATE_REPOSITORY'],'commit':commit,'tree':tree,'profile':profile,
          'workflow_repository':os.environ['GITHUB_REPOSITORY'],'workflow_ref':os.environ['GITHUB_WORKFLOW_REF'],
          'workflow_sha':os.environ['GITHUB_WORKFLOW_SHA'],'run_id':os.environ['GITHUB_RUN_ID'],
          'run_attempt':os.environ['GITHUB_RUN_ATTEMPT'],'job':os.environ['GITHUB_JOB'],'cases':expected,
          'execution':'internal-admitted-typed-4-to-5','publisher_scope':'same-candidate-real-storage4-publisher-only',**CLAIMS})
    for case in expected:
        database = 'trnm_accounts_typed_' + case
        sql(profile, container, 'trnm', 'CREATE DATABASE ' + database)
        sql(profile, container, database, "CREATE TABLE fixture_custody (marker TEXT NOT NULL); INSERT INTO fixture_custody VALUES ('" + commit + ':' + profile + ':' + case + "')")
        env = dict(os.environ, TRNM_ACCOUNTS_TYPED_DISPOSABLE='hosted-container-fixture',
                   TRNM_ACCOUNTS_TYPED_PROFILE=profile, TRNM_ACCOUNTS_TYPED_CASE=case,
                   TRNM_ACCOUNTS_TYPED_OUTPUT=str(output.resolve()), TRNM_SCHEMA_SOURCE_COMMIT=commit)
        with (output / (case + '-test.log')).open('w') as log:
            subprocess.run(['cargo','test','--locked','-p','trnm-persistence-pg','--lib',
                'schema::account_typed_diagnostic::accounts_typed_disposable_migration',
                '--','--ignored','--exact','--nocapture'],env=env,stdout=log,stderr=subprocess.STDOUT,timeout=900,check=True)
        validate_result(read_json(output, case + '-result.json'),profile,case,commit)
    write(output,'summary.json',{'profile':profile,'cases':expected,'internal_typed_diagnostic_passed':True,**CLAIMS})


METADATA_PREFIX = 'SELECT singleton::TEXT,schema_version::TEXT,profile::TEXT,source_commit::TEXT,applied_at_ms::TEXT,chain_digest::TEXT,digest_algorithm::TEXT,storage_writer_epoch::TEXT,upgrade_source_commit::TEXT,v2_apply_source_commit::TEXT,v3_apply_source_commit::TEXT,'


def metadata(value):
    assert value['query'] in {METADATA_PREFIX + publisher + ' FROM trnm_schema_metadata ORDER BY singleton LIMIT 2' for publisher in ('v4_apply_source_commit::TEXT','NULL::TEXT AS v4_apply_source_commit')}
    assert len(value['rows']) == 1
    row = value['rows'][0]
    assert set(row) == {'singleton','schema_version','profile','source_commit','applied_at_ms','chain_digest','digest_algorithm','storage_writer_epoch','upgrade_source_commit','v2_apply_source_commit','v3_apply_source_commit','v4_apply_source_commit'}
    assert all(v is None or type(v) is str for v in row.values())
    return row


STORAGE_QUERY = re.search(r'const STORAGE_QUERY: &str = "([^"\n]*)";', (ROOT / 'crates/trnm-persistence-pg/src/schema_parts/account_typed_diagnostic.rs').read_text()).group(1)


def storage_fixture():
    common = dict(collection='',object_key='',value_jsonb='null',value_projection_digest=hashlib.sha256(b'null').hexdigest(),create_time=None,update_time=None)
    return {'query':STORAGE_QUERY,'rows':[
        dict(common,user_id='1'*32,public_version=hashlib.md5(b'null').hexdigest(),value_origin='write-request-bytes',value_bytes=b'null'.hex(),version_digest=hashlib.sha256(b'null').hexdigest(),source_manifest_digest=None,read_permission='32767',write_permission='32767',updated_at_ms='0'),
        dict(common,user_id='2'*32,public_version='',value_origin='nakama-export-unknown-request',value_bytes=None,version_digest=None,source_manifest_digest='3'*64,read_permission='3',write_permission='2',updated_at_ms='1')]}


def validate_result(result, profile, case, commit):
    assert result['schema'] == 'trillionnium.accounts-internal-typed-case.v1'
    assert result['profile'] == profile and result['case'] == case and result['source_commit'] == commit
    assert result['database'] == 'trnm_accounts_typed_' + case
    assert result['execution'] == 'internal-admitted-typed-4-to-5'
    assert result['publisher_scope'] == 'same-candidate-real-storage4-publisher-only'
    assert all(result[k] is v for k,v in CLAIMS.items())
    assert result['internal_typed_migration_executed'] is True and result['all_prior_publishers_preserved'] is True
    assert result['populated_storage_retained'] is True
    assert result['storage_before'] == result['storage_after']
    assert result['storage_before'] == storage_fixture()
    expected_corruptions = ['schema_storage_projection_digest_invalid','schema_storage_known_witness_invalid','schema_storage_native_projection_invalid'] if case == 'populated' else []
    assert result['corruption_rejections'] == [dict(reason=reason,data_retained=True,metadata_retained=True,catalog_retained=True) for reason in expected_corruptions]
    before, after = metadata(result['metadata_before']), metadata(result['metadata_after'])
    assert before['profile'] == profile == after['profile']
    assert before['chain_digest'] == chain_identity(profile,4) and after['chain_digest'] == chain_identity(profile,5)
    assert before['v4_apply_source_commit'] is None
    assert before['schema_version'] == '4' and after['schema_version'] == '5'
    assert before['upgrade_source_commit'] == commit == after['upgrade_source_commit']
    assert result['preserved_prior4_publisher'] == commit == after['v4_apply_source_commit']
    assert before['storage_writer_epoch'] == '4' == after['storage_writer_epoch']
    for field in ('source_commit','v2_apply_source_commit','v3_apply_source_commit','applied_at_ms'):
        assert before[field] == after[field]
        if field != 'applied_at_ms': assert before[field] == commit
    assert result['applied_steps'] == 1 and result['replay_applied_steps'] == 0 and result['replay_metadata_unchanged'] is True
    assert result['missing_writer_rejection'] == 'legacy_storage_writer_barrier_required'
    assert result['public_accounts_refusal'] == 'schema5_native_catalog_capture_pending'
    assert result['public_accounts_after_migration_refusal'] == result['public_accounts_refusal']
    assert result['public_storage_refusal'] == 'authoritative_schema_target_downgrade_rejected'
    assert result['action_count'] == 5
    expected_error = {'writer':'legacy_storage_writer_not_fenced','import':'schema_unpublished_import_journal_nonempty'}.get(case)
    assert result['barrier_rejection'] == expected_error
    cut = result['interruption']
    if case.startswith('cut_'):
        assert cut['fault_kind'] == 'returned-error-after-native-action-catalog-check'
        assert cut['after_action'] == int(case[4:]) and cut['reason'] == 'schema5_diagnostic_action_cut'
        expected_prefix = prefix4(profile) + (int(case[4:]) if profile == 'cockroachdb' else 0)
        assert cut['observed_prefix'] == cut['expected_prefix'] == expected_prefix
        assert cut['writer_rejection'] == 'legacy_storage_writer_not_fenced'
        assert cut['import_rejection'] == 'schema_unpublished_import_journal_nonempty'
        assert cut['blocked_resume_catalog_unchanged'] is True
        assert cut['old_metadata_retained'] is True
        assert cut['postgres_transaction_rolled_back'] is (profile == 'postgresql')
        old = metadata(cut['metadata'])
        assert all(old[k] == v for k,v in before.items())
        assert old.get('v4_apply_source_commit') is None
    else:
        assert cut is None


def validate_packet(output, identity, profile, commit, tree):
    assert identity['schema'] == 'trillionnium.accounts-internal-typed-identity.v1'
    assert identity['profile'] == profile and identity['commit'] == commit and identity['tree'] == tree
    assert identity['execution'] == 'internal-admitted-typed-4-to-5'
    assert identity['publisher_scope'] == 'same-candidate-real-storage4-publisher-only'
    assert all(identity[k] is v for k,v in CLAIMS.items())
    expected = cases(profile)
    summary = read_json(output,'summary.json')
    assert summary['profile'] == profile and summary['internal_typed_diagnostic_passed'] is True
    assert all(summary[k] is v for k,v in CLAIMS.items())
    assert expected == identity['cases'] == summary['cases']
    B.validate_annex_directory(B.verify_binding(ROOT,profile=profile),output,commit=commit,tree=tree)
    registry = read_json(output,'executor-source-binding.json')
    assert registry['commit'] == commit and registry['tree'] == tree and registry['activation'] is False
    assert registry['execution'] == 'same-internal-typed-executor-cfg-test-admission'
    assert set(registry['sources']) == set(SOURCE_PATHS)
    for path in SOURCE_PATHS:
        data = B.SOURCE._regular(output,Path(path).name,B.MAX_FILE_BYTES)
        assert data == B.SOURCE._regular(ROOT,path,B.MAX_FILE_BYTES)
        assert registry['sources'][path] == {'git_blob_sha1':B.blob(data),'sha256':B.sha(data)}
    diagnostic = B.SOURCE._regular(output,'account_capture_diagnostic.rs',B.MAX_FILE_BYTES).decode()
    queries = {name:json.loads(query) for name,query in re.findall(r'\("([a-z]+)", ("[^"\n]*")\)',diagnostic.split('const QUERIES:',1)[1])}
    wanted = {c + '-raw.json' for c in expected} | {c + '-cut-raw.json' for c in expected if c.startswith('cut_')}
    assert {p.name for p in output.iterdir() if p.name.endswith('-raw.json')} == wanted
    for name in wanted:
        raw = read_json(output,name)
        assert set(raw) == set(RAW.RAW_KINDS)
        for kind,value in raw.items():
            assert set(value) == {'query','rows'} and value['query'] == queries[kind]
            assert type(value['rows']) is list and len(value['rows']) <= 128
            assert all(type(row) is dict and row and all(v is None or type(v) is str for v in row.values()) for row in value['rows'])
            # PG transaction cuts roll back all accounts relations; early CR
            # cuts likewise have no native rows yet. Full post-resume must not.
            if '-cut-' not in name and (kind != 'triggers' or profile == 'postgresql'):
                assert value['rows']
    pinned = json.loads((ROOT / 'config/database-test-images.json').read_text())['profiles'][profile]
    assert pinned['image'] in read_json(output,'repo-digests.json')
    assert B.SOURCE._regular(output,'database-version.txt',16384).decode().strip() == pinned['version_output'].strip()
    assert re.fullmatch(r'sha256:[0-9a-f]{64}',B.SOURCE._regular(output,'image-id.txt',256).decode().strip())
    for case in expected:
        validate_result(read_json(output,case + '-result.json'),profile,case,commit)
        assert B.SOURCE._regular(output,case + '-test.log',B.MAX_FILE_BYTES)


def seal(output, profile):
    identity,custody = read_json(output,'identity.json'),read_json(output,'container-custody.json')
    assert run('docker','ps','-aq','--no-trunc','--filter','id=' + custody['id']) == ''
    complete = True
    try:
        commit,tree = run('git','rev-parse','HEAD'),run('git','rev-parse','HEAD^{tree}')
        assert commit == os.environ['CANDIDATE_SHA']
        validate_packet(output,identity,profile,commit,tree)
    except (OSError,ValueError,KeyError,AssertionError,RuntimeError):
        complete = False
    write(output,'lifecycle.json',{'container_id':custody['id'],'container_removed':True,
          'disposal':'parent-live-harness-exit-trap','schema5_activation':False,'capture_complete':complete})
    files = sorted(p for p in output.rglob('*') if p.is_file() and p.name != 'SHA256SUMS')
    (output / 'SHA256SUMS').write_text(''.join(B.sha(B.SOURCE._regular(output,str(p.relative_to(output)),B.MAX_FILE_BYTES)) + '  ' + str(p.relative_to(output)) + '\n' for p in files))
    return complete


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=('capture','seal'))
    p.add_argument('--profile',choices=('postgresql','cockroachdb'),required=True)
    p.add_argument('--container');p.add_argument('--parent',type=Path);p.add_argument('--output',type=Path,required=True)
    a = p.parse_args()
    if a.mode == 'capture': capture(a.profile,a.container,a.parent,a.output)
    elif not seal(a.output,a.profile): raise SystemExit('incomplete typed diagnostic packet; no success credit')

if __name__ == '__main__': main()
