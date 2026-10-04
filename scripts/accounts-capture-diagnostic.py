#!/usr/bin/env python3
"""Owned-container SQL5 fixture diagnostics. Never admits AccountsV5 startup."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

# Operational assertions must never disappear in an optimized interpreter.
if not __debug__:
    raise RuntimeError('accounts diagnostic rejects Python optimization')
sys.path.insert(0, str(Path(__file__).resolve().parent))
import schema_evidence_binding as BINDING

ROOT = Path(__file__).resolve().parents[1]
READERS = ('account_catalog.rs', 'account_native_columns.rs', 'account_native_attributes.rs',
           'account_native_objects.rs', 'account_native_triggers.rs', 'account_relation_semantics.rs',
           'account_capture_diagnostic.rs')
RAW_KINDS = ('columns', 'attributes', 'relations', 'constraints', 'indexes', 'triggers')
CASES = ('baseline', 'type', 'default', 'check', 'key', 'predicate', 'foreign_key')
CLAIMS = {'native_migration_executed': False, 'schema5_activation': False,
          'compatibility_credit': False, 'accepted': False, 'backup_restore_qualified': False}


def run(*args, **kw):
    return subprocess.check_output(args, text=True, timeout=kw.pop('timeout', 60), **kw).strip()


def write(root, name, value):
    (root / name).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def verify_container(profile, container, parent):
    expected_name = 'trnm-server-live-' + profile + '-' + re.sub('[^A-Za-z0-9_.-]', '-', os.environ['TRNM_RUN_ID'])
    if container != expected_name:
        raise ValueError('owned live-run container required')
    inspected = json.loads(run('docker', 'inspect', container))[0]
    expected_id = (parent / 'container-id.txt').read_text().strip()
    expected_image = (parent / 'image-id.txt').read_text().strip()
    pinned = json.loads((ROOT / 'config/database-test-images.json').read_text())['profiles'][profile]['image']
    if (inspected['Id'] != expected_id or inspected['Image'] != expected_image
            or inspected['Config']['Image'] != pinned or not inspected['State']['Running']):
        raise ValueError('disposable container/image custody mismatch')
    # Test entry has only these fixed loopback routes; do not accept a DSN.
    if profile == 'postgresql':
        if inspected['NetworkSettings']['Ports']['5432/tcp'] != [{'HostIp': '127.0.0.1', 'HostPort': '55435'}]:
            raise ValueError('fixture requires fixed loopback PostgreSQL port')
    elif inspected['HostConfig']['NetworkMode'] != 'host':
        raise ValueError('fixture requires owned Cockroach loopback host container')
    return inspected


def sql(profile, container, database, statement):
    if profile == 'postgresql':
        return run('docker', 'exec', container, 'psql', '-X', '-U', 'trnm', '-d', database,
                   '-v', 'ON_ERROR_STOP=1', '-c', statement)
    return run('docker', 'exec', container, '/cockroach/cockroach', 'sql', '--insecure',
               '--host=127.0.0.1:26257', '--database=' + database, '--execute=' + statement)


def capture(profile, container, parent, output):
    output.mkdir(parents=True, exist_ok=False)
    inspected = verify_container(profile, container, parent)
    commit = run('git', 'rev-parse', 'HEAD')
    tree = run('git', 'rev-parse', 'HEAD^{tree}')
    if commit != os.environ['CANDIDATE_SHA'] or not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('exact candidate required')
    write(output, 'container-custody.json', {'id': inspected['Id'], 'name': container,
          'image': inspected['Image'], 'pinned_image': inspected['Config']['Image']})
    for name in ('database-version.txt', 'image-id.txt', 'repo-digests.json'):
        (output / name).write_bytes((parent / name).read_bytes())
    # Existing helper retains all ten SQL files plus actual Git custody. Its
    # StorageV4 token covers ONLY the ordinary prefix; SQL5 is fixture material.
    run('python3', 'scripts/capture-schema-source-selection.py', '--root', str(output),
        '--profile', profile, '--commit', commit, '--tree', tree)
    bindings = {}
    for name in READERS:
        path = 'crates/trnm-persistence-pg/src/schema_parts/' + name
        data = (ROOT / path).read_bytes()
        blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        assert run('git', 'rev-parse', 'HEAD:' + path) == blob, 'reader must be exact source HEAD'
        (output / name).write_bytes(data)
        bindings[path] = {'git_blob_sha1':blob, 'sha256':hashlib.sha256(data).hexdigest()}
    write(output, 'reader-source-binding.json', {'commit':commit, 'tree':tree, 'sources':bindings,
          'raw_observation_files': list(RAW_KINDS),
          'binding_status':'diagnostic-observations-only', 'activation':False})
    cases = CASES + (('trigger',) if profile == 'postgresql' else ())
    write(output, 'identity.json', {'schema':'trillionnium.accounts-raw-fixture-identity.v1',
          'repository':os.environ['CANDIDATE_REPOSITORY'], 'commit':commit, 'tree':tree,
          'profile':profile, 'workflow_repository':os.environ['GITHUB_REPOSITORY'],
          'workflow_ref':os.environ['GITHUB_WORKFLOW_REF'], 'workflow_sha':os.environ['GITHUB_WORKFLOW_SHA'],
          'run_id':os.environ['GITHUB_RUN_ID'], 'run_attempt':os.environ['GITHUB_RUN_ATTEMPT'],
          'job':os.environ['GITHUB_JOB'], 'cases':cases,
          'execution':'raw-sql5-after-real-storage-v4', **CLAIMS})
    for case in cases:
        database = 'trnm_accounts_capture_' + case
        # No IF NOT EXISTS: a reused or nonempty fixture must fail closed.
        sql(profile, container, 'trnm', 'CREATE DATABASE ' + database)
        sql(profile, container, database, "CREATE TABLE fixture_custody (marker TEXT NOT NULL); INSERT INTO fixture_custody VALUES ('" + commit + ':' + profile + ':' + case + "')")
        env = dict(os.environ, TRNM_ACCOUNTS_CAPTURE_DISPOSABLE='hosted-container-fixture',
                   TRNM_ACCOUNTS_CAPTURE_PROFILE=profile, TRNM_ACCOUNTS_CAPTURE_CASE=case,
                   TRNM_ACCOUNTS_CAPTURE_OUTPUT=str(output.resolve()), TRNM_SCHEMA_SOURCE_COMMIT=commit)
        with (output / (case + '-test.log')).open('w') as log:
            subprocess.run(['cargo', 'test', '--locked', '-p', 'trnm-persistence-pg', '--lib',
                'schema::account_capture_diagnostic::accounts_capture_disposable_fixture',
                '--', '--ignored', '--exact', '--nocapture'], env=env, stdout=log,
                stderr=subprocess.STDOUT, timeout=900, check=True)
        result = json.loads((output / (case + '-result.json')).read_text())
        validate_result(result, profile, case, commit)
    write(output, 'summary.json', {'profile':profile, 'cases':list(cases), 'raw_capture_passed':True,
          'typed_migration_qualified':False, **CLAIMS})


def validate_result(result, profile, case, commit):
    assert result['profile'] == profile and result['case'] == case and result['source_commit'] == commit
    assert result['schema'] == 'trillionnium.accounts-raw-fixture-case.v1'
    assert result['fixture_execution'] == 'raw-sql5-after-real-storage-v4'
    assert result['metadata_retained'] is True and result['v4_apply_source_commit'] is None
    assert result['metadata_before'] == result['metadata_after']
    rows = result['metadata_before']['rows']
    assert len(rows) == 1 and rows[0]['schema_version'] == '4'
    assert rows[0]['upgrade_source_commit'] == commit and rows[0]['storage_writer_epoch'] == '4'
    assert result['storage_v4_refusal'] == 'authoritative_schema_upgrade_incomplete'
    assert result['accounts_v5_refusal'] == 'schema5_native_catalog_capture_pending'
    for key in ('native_migration_executed', 'schema5_activation', 'compatibility_credit', 'accepted'):
        assert result[key] is False
    assert (result['drift_rejection'] is None) == (case == 'baseline')
    if case != 'baseline':
        reason = result['drift_rejection']
        assert reason.startswith('schema5_') or reason == 'authoritative_schema_partial_action_drift'


def read_json(output, name):
    return BINDING.strict(BINDING.SOURCE._regular(output, name, BINDING.SOURCE.MAX_DOCUMENT_BYTES), name)


def validate_packet(output, identity, profile, commit, tree):
    assert identity['schema'] == 'trillionnium.accounts-raw-fixture-identity.v1'
    assert identity['profile'] == profile and identity['commit'] == commit and identity['tree'] == tree
    assert identity['execution'] == 'raw-sql5-after-real-storage-v4'
    assert all(identity[key] is value for key, value in CLAIMS.items())
    summary = read_json(output, 'summary.json')
    assert summary['raw_capture_passed'] is True and summary['profile'] == profile
    assert summary['typed_migration_qualified'] is False
    assert all(summary[key] is value for key, value in CLAIMS.items())
    expected = list(CASES) + (['trigger'] if profile == 'postgresql' else [])
    assert identity['cases'] == expected == summary['cases']
    token = BINDING.verify_binding(ROOT, profile=profile)
    BINDING.validate_annex_directory(token, output, commit=commit, tree=tree)
    registry = read_json(output, 'reader-source-binding.json')
    assert registry['commit'] == commit and registry['tree'] == tree
    assert registry['binding_status'] == 'diagnostic-observations-only' and registry['activation'] is False
    assert registry['raw_observation_files'] == list(RAW_KINDS)
    paths = {'crates/trnm-persistence-pg/src/schema_parts/' + name for name in READERS}
    assert set(registry['sources']) == paths
    for path in paths:
        data = BINDING.SOURCE._regular(output, Path(path).name, BINDING.MAX_FILE_BYTES)
        assert data == BINDING.SOURCE._regular(ROOT, path, BINDING.MAX_FILE_BYTES)
        assert registry['sources'][path] == {'git_blob_sha1':BINDING.blob(data), 'sha256':BINDING.sha(data)}
    diagnostic = BINDING.SOURCE._regular(output, 'account_capture_diagnostic.rs', BINDING.MAX_FILE_BYTES).decode()
    queries = {name:json.loads(query) for name,query in re.findall(r'\("([a-z]+)", ("[^"\n]*")\)', diagnostic.split('const QUERIES:', 1)[1])}
    assert set(queries) == set(RAW_KINDS)
    wanted = {case + '-raw.json' for case in expected}
    wanted |= {case + '-altered-raw.json' for case in expected if case != 'baseline'}
    assert {path.name for path in output.iterdir() if path.name.endswith('-raw.json')} == wanted
    for name in wanted:
        raw = read_json(output, name)
        assert set(raw) == set(RAW_KINDS)
        for kind, value in raw.items():
            assert set(value) == {'query','rows'} and value['query'] == queries[kind]
            assert type(value['rows']) is list and len(value['rows']) <= 128
            assert all(type(row) is dict and row and all(v is None or type(v) is str for v in row.values()) for row in value['rows'])
            if kind != 'triggers' or profile == 'postgresql':
                assert value['rows'], 'required raw catalog category is empty'
    images = read_json(output, 'repo-digests.json')
    pinned = json.loads((ROOT / 'config/database-test-images.json').read_text())['profiles'][profile]
    assert pinned['image'] in images
    assert BINDING.SOURCE._regular(output, 'database-version.txt', 16384).decode().strip() == pinned['version_output'].strip()
    assert re.fullmatch(r'sha256:[0-9a-f]{64}', BINDING.SOURCE._regular(output, 'image-id.txt', 256).decode().strip())
    for case in expected:
        validate_result(read_json(output, case + '-result.json'), profile, case, commit)
        assert BINDING.SOURCE._regular(output, case + '-test.log', BINDING.MAX_FILE_BYTES)


def seal(output, profile):

    identity = read_json(output, 'identity.json')
    custody = read_json(output, 'container-custody.json')
    # Called after the parent shell EXIT trap; missing Docker is not disposal.
    remaining = run('docker', 'ps', '-aq', '--no-trunc', '--filter', 'id=' + custody['id'])
    assert remaining == '', 'owned fixture container still present'
    complete = True
    try:
        commit = run('git', 'rev-parse', 'HEAD')
        tree = run('git', 'rev-parse', 'HEAD^{tree}')
        assert commit == os.environ['CANDIDATE_SHA']
        validate_packet(output, identity, profile, commit, tree)
    except (OSError, ValueError, KeyError, AssertionError, RuntimeError):
        complete = False
    write(output, 'lifecycle.json', {'container_id':custody['id'], 'container_removed':True,
          'disposal':'parent-live-harness-exit-trap', 'schema5_activation':False,
          'capture_complete':complete})
    files = sorted(p for p in output.rglob('*') if p.is_file() and p.name != 'SHA256SUMS')
    (output / 'SHA256SUMS').write_text(''.join(hashlib.sha256(BINDING.SOURCE._regular(output, str(p.relative_to(output)), BINDING.MAX_FILE_BYTES)).hexdigest() + '  ' + str(p.relative_to(output)) + '\n' for p in files))
    return complete


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=('capture','seal'))
    p.add_argument('--profile', choices=('postgresql','cockroachdb'), required=True)
    p.add_argument('--container'); p.add_argument('--parent', type=Path)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if a.mode == 'capture': capture(a.profile, a.container, a.parent, a.output)
    elif not seal(a.output, a.profile): raise SystemExit("incomplete diagnostic packet retained; no success credit")

if __name__ == '__main__': main()
