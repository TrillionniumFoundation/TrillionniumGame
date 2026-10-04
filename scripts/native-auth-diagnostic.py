#!/usr/bin/env python3
"""Owned-container actual TCP/native auth15 diagnostic. Never normalizes pairs."""
from __future__ import annotations
import argparse
import base64
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import selectors
import signal
import time
import subprocess
import uuid

if not __debug__:
    raise RuntimeError('native auth diagnostic rejects optimized Python')
ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('accounts_capture', ROOT / 'scripts/accounts-capture-diagnostic.py')
RAW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RAW)
B = RAW.BINDING
FIXTURE = ROOT / 'oracle/immutable/auth-reference-cases.json'
FIXTURE_SHA = 'fcde649cdcca60c4c2a09971f9c3b89d160bdc0662edcde14e80c5dedf0eca63'
REFERENCE_COMMIT = 'cf6541c1b6b999efca7895e7028f96b79ab2951c'
REFERENCE_TREE = '3c852c9d7977b7c70fbb3ff137593e125a783b7c'
CLAIMS = dict(paired_exact_oracle=False, production_ready=False, public_accounts_v5_activation=False,
              startup_qualified=False, restore_qualified=False, compatibility_credit=False)
MAX_BYTES = 2 * 1024 * 1024
LOG_MAX_BYTES = MAX_BYTES
COMMAND = ['cargo','test','--locked','-p','trnm-persistence-pg','--lib',
    'schema::account_native_auth::accounts_native_auth_disposable_listener','--','--ignored','--exact','--nocapture']
IDENTITY_FIELDS = ('repository','commit','tree','profile','run_id','run_attempt','job')


def executor_identity(identity):
    return {key:identity[key] for key in IDENTITY_FIELDS}


def byte_commitment(data):
    return {'length':len(data),'sha256':hashlib.sha256(data).hexdigest()}

EXTRA_SOURCES = ('scripts/native-auth-diagnostic.py','contracts/server/native-auth-admission-source.json',
    '.github/workflows/trnm-server-live.yml','scripts/ci-trnm-server-live.sh','scripts/accounts-capture-diagnostic.py',
    'scripts/schema_evidence_binding.py','scripts/schema_source_selection.py','scripts/evidence_admission.py',
    'scripts/oracle/capture-auth-reference.py','oracle/immutable/oracle-lock.json','config/oracle-normalizers.json',
    'config/database-test-images.json','tests/control_plane/test_native_auth_diagnostic.py')


def read(path):
    def pairs(items):
        result = {}
        for key,value in items:
            if key in result: raise ValueError('duplicate retained JSON key')
            result[key] = value
        return result
    def noninteger(_): raise ValueError('noninteger retained JSON number')
    return json.loads(B.SOURCE._regular(path.parent, path.name, MAX_BYTES),object_pairs_hook=pairs,
                      parse_float=noninteger,parse_constant=noninteger)


def write(output, name, value):
    data = (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()
    assert len(data) < MAX_BYTES
    with (output / name).open('xb') as stream:
        stream.write(data)


def fixture():
    raw = FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == FIXTURE_SHA
    value = json.loads(raw)
    assert value['corpus_id'] == 'immutable-auth-reference-15-v1' and len(value['cases']) == 15
    return value


def cleanup_owned(process):
    try: os.killpg(process.pid,signal.SIGKILL)
    except ProcessLookupError: pass
    process.wait(timeout=5)
    deadline = time.monotonic()+5
    while True:
        try: pid,_ = os.waitpid(-process.pid,os.WNOHANG)
        except ChildProcessError: return
        if pid == 0:
            if time.monotonic() >= deadline:
                raise RuntimeError('owned descendants not reaped; incomplete execution')
            time.sleep(0.01)


def run_bounded(command, env, log_path, timeout, identity=None):
    """Persist the executor outcome even when successful-looking output precedes failure."""
    outcome = dict(schema='trillionnium.native-auth15-executor.v1', started=False,
        exit_status=None, log_collection_complete=False, descendant_cleanup_complete=False,
        timed_out=False, log_overflow=False, failure=None, success=False,
        identity=executor_identity(identity) if identity is not None else None,
        command=list(command), log=None, native_result=None)
    collected = hashlib.sha256()
    collected_length = 0
    process = None
    selector = None
    libc = ctypes.CDLL(None, use_errno=True)
    prior = ctypes.c_int()
    subreaper = False
    try:
        if libc.prctl(37,ctypes.byref(prior),0,0,0) != 0 or libc.prctl(36,1,0,0,0) != 0:
            raise RuntimeError('owned descendant reaping unavailable')
        subreaper = True
        process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        outcome['started'] = True
        deadline = time.monotonic()+timeout
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        retained = 0
        with log_path.open('xb') as log:
            while selector.get_map():
                remaining = deadline-time.monotonic()
                if remaining <= 0: raise TimeoutError('native execution deadline; effects may exist; no replay')
                for key,_ in selector.select(min(remaining,0.05)):
                    chunk = os.read(key.fileobj.fileno(),65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    retained += len(chunk)
                    if retained > LOG_MAX_BYTES:
                        outcome['log_overflow'] = True
                        raise RuntimeError('native log limit; effects may exist; no replay')
                    log.write(chunk)
                    collected.update(chunk)
                    collected_length += len(chunk)
            outcome['log_collection_complete'] = True
            code = process.wait(timeout=max(0.001,deadline-time.monotonic()))
            outcome['exit_status'] = code
            if code: raise RuntimeError('native command failed; effects may exist; no replay')
    except (TimeoutError, subprocess.TimeoutExpired):
        outcome['timed_out'] = True
        outcome['failure'] = 'executor-timeout'
        raise
    except BaseException:
        outcome['failure'] = 'executor-failure'
        raise
    finally:
        try:
            if process is not None:
                cleanup_owned(process)
                outcome['descendant_cleanup_complete'] = True
                outcome['exit_status'] = process.returncode
        except BaseException:
            outcome['failure'] = 'executor-cleanup-incomplete'
            raise
        finally:
            if selector is not None: selector.close()
            if process is not None: process.stdout.close()
            if subreaper and libc.prctl(36,prior.value,0,0,0) != 0:
                outcome['failure'] = 'executor-subreaper-restore-failed'
            outcome['log'] = {'length':collected_length,'sha256':collected.hexdigest()}
            try:
                result_bytes = B.SOURCE._regular(log_path.parent,'native-auth-result.json',MAX_BYTES)
                outcome['native_result'] = byte_commitment(result_bytes)
            except (OSError,B.SOURCE.SelectionError):
                pass
            outcome['success'] = (outcome['started'] and outcome['exit_status'] == 0
                and outcome['log_collection_complete'] and outcome['descendant_cleanup_complete']
                and not outcome['timed_out'] and not outcome['log_overflow'] and outcome['failure'] is None)
            write(log_path.parent,log_path.name+'.executor.json',outcome)
    assert outcome['success'], 'executor outcome incomplete; no execution credit'


def validate_executor(output, identity, expected_command=None):
    outcome = read(output/'native-auth-test.log.executor.json')
    assert set(outcome) == {'schema','started','exit_status','log_collection_complete',
        'descendant_cleanup_complete','timed_out','log_overflow','failure','success',
        'identity','command','log','native_result'}
    assert outcome['identity'] == executor_identity(identity)
    assert outcome['command'] == (COMMAND if expected_command is None else expected_command)
    assert outcome['log'] == byte_commitment(B.SOURCE._regular(output,'native-auth-test.log',MAX_BYTES))
    assert outcome['native_result'] == byte_commitment(B.SOURCE._regular(output,'native-auth-result.json',MAX_BYTES))
    assert outcome['schema'] == 'trillionnium.native-auth15-executor.v1'
    assert type(outcome['exit_status']) is int and outcome['exit_status'] == 0
    assert all(outcome[k] is True for k in ('started','log_collection_complete','descendant_cleanup_complete','success'))
    assert outcome['timed_out'] is False and outcome['log_overflow'] is False and outcome['failure'] is None


def capture(profile, container, parent, output):
    output.mkdir(parents=True, exist_ok=False)
    inspected = RAW.verify_container(profile, container, parent)
    commit, tree = RAW.run('git','rev-parse','HEAD'), RAW.run('git','rev-parse','HEAD^{tree}')
    assert commit == os.environ['CANDIDATE_SHA'] and re.fullmatch('[0-9a-f]{40}',commit)
    fixture()
    write(output,'container-custody.json',{'id':inspected['Id'],'name':container,'image':inspected['Image'],'pinned_image':inspected['Config']['Image']})
    for name in ('database-version.txt','image-id.txt','repo-digests.json'):
        (output/name).write_bytes((parent/name).read_bytes())
    contract = read(ROOT/'contracts/server/native-auth-admission-source.json')
    bindings = {}
    for path, expected in contract['source_sha256'].items():
        data = B.SOURCE._regular(ROOT,path,MAX_BYTES)
        assert hashlib.sha256(data).hexdigest() == expected
        blob = B.blob(data)
        assert RAW.run('git','rev-parse','HEAD:'+path) == blob
        target = output/'source'/path
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(data)
        bindings[path] = {'sha256':expected,'git_blob_sha1':blob}
    for path in EXTRA_SOURCES:
        data = B.SOURCE._regular(ROOT,path,MAX_BYTES)
        target = output/'source'/path; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(data)
        assert RAW.run('git','rev-parse','HEAD:'+path) == B.blob(data)
        bindings[path] = {'sha256':B.sha(data),'git_blob_sha1':B.blob(data)}
    write(output,'source-binding.json',{'commit':commit,'tree':tree,'sources':bindings})
    write(output,'identity.json',{'schema':'trillionnium.native-auth15-identity.v1','repository':os.environ['CANDIDATE_REPOSITORY'],
        'commit':commit,'tree':tree,'profile':profile,'database':'trnm_native_auth','fixture_sha256':FIXTURE_SHA,
        'reference_origin':{'commit':REFERENCE_COMMIT,'tree':REFERENCE_TREE,'database_profile':'postgresql','signature_verified':False},
        'run_id':os.environ['GITHUB_RUN_ID'],'run_attempt':os.environ['GITHUB_RUN_ATTEMPT'],'job':os.environ['GITHUB_JOB'],
        'normalization_applied':False,**CLAIMS})
    RAW.sql(profile,container,'trnm','CREATE DATABASE trnm_native_auth')
    RAW.sql(profile,container,'trnm_native_auth',"CREATE TABLE fixture_custody (marker TEXT NOT NULL); INSERT INTO fixture_custody VALUES ('"+commit+':'+profile+":native-auth15')")
    env = dict(os.environ,TRNM_NATIVE_AUTH_DISPOSABLE='hosted-container-fixture',TRNM_NATIVE_AUTH_PROFILE=profile,
               TRNM_NATIVE_AUTH_OUTPUT=str(output.resolve()),TRNM_SCHEMA_SOURCE_COMMIT=commit)
    run_bounded(COMMAND,env,output/'native-auth-test.log',300,read(output/'identity.json'))
    validate_result(read(output/'native-auth-result.json'),profile,commit)


def validate_result(result, profile, commit):
    assert set(result) == {'schema','source_commit','profile','database','fixture_sha256','execution','records',
        'completed_case_count','attempted_request_count','incomplete_attempt_may_have_effect','capture_complete','failure','actual_authenticated_drain_joined',
        'same_candidate_migration4_to5','prior4_publisher_retained','public_gate_closed',
        'raw_tokens_retained','keys_retained','normalization_applied',*CLAIMS}
    assert result['schema'] == 'trillionnium.native-auth15-diagnostic.v1'
    assert result['source_commit'] == commit and result['profile'] == profile and result['database'] == 'trnm_native_auth'
    assert result['fixture_sha256'] == 'sha256:'+FIXTURE_SHA
    assert result['execution'] == 'actual-server-tcp-pool-native-accounts-test-admission'
    for key in ('capture_complete','actual_authenticated_drain_joined','same_candidate_migration4_to5','prior4_publisher_retained','public_gate_closed'):
        assert result[key] is True, key
    for key in ('raw_tokens_retained','keys_retained','normalization_applied',*CLAIMS):
        assert result[key] is False, key
    assert result['failure'] is None
    cases, records = fixture()['cases'], result['records']
    assert result['completed_case_count'] == result['attempted_request_count'] == len(records) == 15
    assert result['incomplete_attempt_may_have_effect'] is False
    assert records[0]['before'] == {'users':[],'user_device':[]}
    for index,(case,record) in enumerate(zip(cases,records)):
        assert set(record) == {'id','status','body','before','after','started_at_epoch','completed_at_epoch',
            'raw_tokens_retained','body_base64','body_length','body_sha256','headers_base64','headers',
            'request_length','request_sha256','response_length','response_sha256'}
        assert record['id'] == case['id'] and record['status'] == case['status']
        assert record['raw_tokens_retained'] is False
        headers = base64.b64decode(record['headers_base64'],validate=True)
        assert len(headers) <= 32768 and headers.endswith(b'\r\n\r\n')
        assert re.search(rb'[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}',headers) is None
        lines = headers.split(b'\r\n'); assert lines[0].startswith(b'HTTP/1.1 ')
        assert int(lines[0].split(b' ')[1]) == record['status']
        parsed=[]; lengths=[]
        for line in lines[1:]:
            if not line: continue
            name,colon,value=line.partition(b':'); assert colon
            assert name.lower() not in (b'authorization',b'proxy-authenticate',b'set-cookie',b'transfer-encoding')
            parsed.append([name.decode('ascii'),value.strip().decode('ascii')])
            if name.lower()==b'content-length': lengths.append(int(value.strip()))
        assert parsed == record['headers'] and lengths == [record['body_length']]
        for field in ('request_length','response_length','body_length'):
            assert type(record[field]) is int and 0 <= record[field] <= 1024*1024
        assert record['response_length'] == len(headers)+record['body_length']
        for field in ('request_sha256','response_sha256','body_sha256'):
            assert re.fullmatch('sha256:[0-9a-f]{64}',record[field])
        assert type(record['started_at_epoch']) is int and type(record['completed_at_epoch']) is int
        assert 0 <= record['completed_at_epoch']-record['started_at_epoch'] <= 11
        before,after,body = record['before'],record['after'],record['body']
        if index: assert records[index-1]['after'] == before
        for snapshot in (before,after):
            assert set(snapshot) == {'users','user_device'} and len(snapshot['users']) <= 2 and len(snapshot['user_device']) <= 1
            for user in snapshot['users']:
                assert set(user) == {'id','username','custom_id','create_time','update_time','disable_time','metadata','wallet','edge_count'}
                assert str(uuid.UUID(user['id'])) == user['id']
                for key in ('create_time','update_time','disable_time'):
                    assert type(user[key]) is str and re.fullmatch(r'[0-9TZ:+. -]{19,40}',user[key])
                assert user['metadata'] == user['wallet'] == {} and type(user['edge_count']) is int and user['edge_count'] == 0
            for device in snapshot['user_device']:
                assert set(device) == {'id','user_id'} and device['id'] == 'trnm-reference-device'
                assert any(user['id'] == device['user_id'] for user in snapshot['users'])
        if case['code'] is not None:
            assert set(body) <= {'code','message','details'} and body.get('details',[]) == []
            assert body['code'] == case['code'] and body['message'] == case['message']
        elif case['created'] is not None:
            assert body.get('created',False) is case['created']
            assert set(body) <= {'created','token','refresh_token'}
            for field,ttl in (('token',3600),('refresh_token',86400)):
                token = body[field]; claims = token['claims']
                assert set(token) == {'sha256','length','header','claims','signature_verified','signature_verifier'}
                assert type(token['length']) is int and 0 < token['length'] <= 16384
                assert re.fullmatch('sha256:[0-9a-f]{64}',token['sha256'])
                assert token['signature_verified'] is True and token['signature_verifier'] == 'openssl-hmac-sha256-known-fixture-key'
                assert token['header'] == {'alg':'HS256','typ':'JWT'}
                assert set(claims) <= {'uid','tid','iat','exp','usn','vrs'} and claims.get('vrs',{}) == {}
                assert str(uuid.UUID(claims['uid'])) == claims['uid'] and str(uuid.UUID(claims['tid'])) == claims['tid']
                assert type(claims['iat']) is int and type(claims['exp']) is int
                kind = 'device' if case['id'].startswith('device-') else 'custom'
                assert claims['usn'] == 'trnm_reference_'+kind
                assert record['started_at_epoch']+ttl <= claims['exp'] <= record['completed_at_epoch']+ttl
                if case['id'] != 'refresh-valid': assert record['started_at_epoch'] <= claims['iat'] <= record['completed_at_epoch']
                assert len([u for u in after['users'] if u['id'] == claims['uid'] and u['username'] == claims['usn']]) == 1
            assert all(body['token']['claims'][key] == body['refresh_token']['claims'][key] for key in ('uid','tid','iat','usn'))
            assert record['body_base64'] is None
        else:
            assert body == {}
        if case['created'] is True:
            added = [u for u in after['users'] if u not in before['users']]
            assert len(added) == 1 and len(after['users']) == len(before['users'])+1
            assert all(u in after['users'] for u in before['users'])
            kind = case['id'].split('-')[0]
            assert added[0]['username'] == 'trnm_reference_'+kind
            if kind == 'device':
                assert added[0]['custom_id'] is None and before['user_device'] == []
                assert after['user_device'] == [{'id':'trnm-reference-device','user_id':added[0]['id']}]
            else:
                assert added[0]['custom_id'] == 'trnm-reference-custom' and before['user_device'] == after['user_device']
        else:
            assert before == after
        if 'token' not in body and 'refresh_token' not in body:
            raw = base64.b64decode(record['body_base64'],validate=True)
            assert hashlib.sha256(raw).hexdigest() == record['body_sha256'].removeprefix('sha256:') and len(raw) == record['body_length']
            assert json.loads(raw) == body
            assert 'sha256:'+hashlib.sha256(headers+raw).hexdigest() == record['response_sha256']
    issued = [records[i]['body']['token']['claims']['tid'] for i in (3,4,8,9)]
    assert len(set(issued)) == 4
    old,new = records[9]['body']['token']['claims'],records[12]['body']['token']['claims']
    assert all(old[k] == new[k] for k in ('uid','tid','iat','usn'))


def validate_packet(output, profile, commit, tree, run_identity=None):
    identity = read(output/'identity.json')
    assert identity['commit'] == commit and identity['tree'] == tree and identity['profile'] == profile
    if run_identity is None:
        run_identity = {key:os.environ[env] for key,env in
            (('run_id','GITHUB_RUN_ID'),('run_attempt','GITHUB_RUN_ATTEMPT'),('job','GITHUB_JOB'))}
    assert {key:identity[key] for key in ('run_id','run_attempt','job')} == run_identity
    validate_executor(output,identity)
    assert identity['repository'] == 'TrillionniumFoundation/TrillionniumGame'
    assert identity['reference_origin'] == {'commit':REFERENCE_COMMIT,'tree':REFERENCE_TREE,'database_profile':'postgresql','signature_verified':False}
    assert identity['normalization_applied'] is False and all(identity[k] is False for k in CLAIMS)
    registry = read(output/'source-binding.json')
    assert registry['commit'] == commit and registry['tree'] == tree
    expected = set(read(ROOT/'contracts/server/native-auth-admission-source.json')['source_sha256']) | set(EXTRA_SOURCES)
    assert set(registry['sources']) == expected
    for path, row in registry['sources'].items():
        data = B.SOURCE._regular(output,'source/'+path,MAX_BYTES)
        assert data == B.SOURCE._regular(ROOT,path,MAX_BYTES)
        assert row == {'sha256':B.sha(data),'git_blob_sha1':B.blob(data)}
        assert RAW.run('git','rev-parse',commit+':'+path) == B.blob(data)
    pinned = read(ROOT/'config/database-test-images.json')['profiles'][profile]
    assert pinned['image'] in read(output/'repo-digests.json')
    custody = read(output/'container-custody.json')
    image = (output/'image-id.txt').read_text().strip()
    assert re.fullmatch('sha256:[0-9a-f]{64}',image) and custody['image'] == image
    assert custody['pinned_image'] == pinned['image']
    assert custody['name'] == 'trnm-server-live-'+profile+'-'+identity['run_id']+'-'+profile
    assert (output/'database-version.txt').read_text().strip() == pinned['version_output'].strip()
    validate_result(read(output/'native-auth-result.json'),profile,commit)
    log = B.SOURCE._regular(output,'native-auth-test.log',MAX_BYTES).decode()
    assert re.search(r'test result: ok\. 1 passed; 0 failed; 0 ignored;',log)


def seal(output, profile):
    custody = read(output/'container-custody.json')
    assert RAW.run('docker','ps','-aq','--no-trunc','--filter','id='+custody['id']) == ''
    complete = True
    try:
        commit,tree = RAW.run('git','rev-parse','HEAD'),RAW.run('git','rev-parse','HEAD^{tree}')
        assert commit == os.environ['CANDIDATE_SHA']
        validate_packet(output,profile,commit,tree)
    except (OSError,ValueError,KeyError,AssertionError,RuntimeError,B.SOURCE.SelectionError):
        complete = False
    write(output,'lifecycle.json',{'container_id':custody['id'],'container_removed':True,'capture_complete':complete,**CLAIMS})
    paths = sorted(p for p in output.rglob('*') if p.is_file() and p.name != 'SHA256SUMS')
    (output/'SHA256SUMS').write_text(''.join(B.sha(B.SOURCE._regular(output,str(p.relative_to(output)),MAX_BYTES))+'  '+str(p.relative_to(output))+'\n' for p in paths))
    return complete


def validate_sealed(output, profile, commit, tree, run_identity=None):
    assert re.fullmatch('[0-9a-f]{40}',commit) and re.fullmatch('[0-9a-f]{40}',tree)
    assert RAW.run('git','rev-parse',commit+'^{tree}') == tree
    sums = B.SOURCE._regular(output,'SHA256SUMS',MAX_BYTES).decode()
    entries = {}
    for line in sums.splitlines():
        digest,separator,path = line.partition('  ')
        assert separator and re.fullmatch('[0-9a-f]{64}',digest)
        assert path not in entries and path != 'SHA256SUMS'
        entries[path] = digest
        assert B.sha(B.SOURCE._regular(output,path,MAX_BYTES)) == digest
    actual = {str(p.relative_to(output)) for p in output.rglob('*') if p.is_file() or p.is_symlink()}
    assert set(entries) == actual-{'SHA256SUMS'}
    lifecycle = read(output/'lifecycle.json')
    assert set(lifecycle) == {'container_id','container_removed','capture_complete',*CLAIMS}
    assert lifecycle['container_id'] == read(output/'container-custody.json')['id']
    assert lifecycle['container_removed'] is True and lifecycle['capture_complete'] is True
    assert all(lifecycle[k] is False for k in CLAIMS)
    if run_identity is None: validate_packet(output,profile,commit,tree)
    else: validate_packet(output,profile,commit,tree,run_identity)


def literal_differences(left, right, path='$'):
    """Do not rewrite, erase, map, sort arrays or normalize any observed value."""
    if type(left) is not type(right): return [path]
    if isinstance(left,dict):
        result = []
        for key in sorted(set(left)|set(right)):
            child = path+'['+json.dumps(key)+']'
            result.extend([child] if key not in left or key not in right else literal_differences(left[key],right[key],child))
        return result
    if isinstance(left,list):
        result = [path+'.length'] if len(left) != len(right) else []
        for i,(a,b) in enumerate(zip(left,right)): result.extend(literal_differences(a,b,path+'['+str(i)+']'))
        return result
    return [] if left == right else [path]


def compare(reference, native, output, profile, commit, tree, run_identity):
    # Historical reference remains read-only and signature-unverified. Only use
    # its original collector/retained-reader validation, never relabel provenance.
    spec = importlib.util.spec_from_file_location('auth_reference',ROOT/'scripts/oracle/capture-auth-reference.py')
    mod = importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    mod.verify_capture(reference/'auth-reference',reference/'runtime-facts.json')
    manifest = read(reference/'auth-reference/manifest.json')
    assert manifest['candidate_commit'] == REFERENCE_COMMIT and manifest['candidate_tree'] == REFERENCE_TREE
    identity = read(native/'identity.json')
    validate_sealed(native,profile,commit,tree,run_identity)
    result = read(native/'native-auth-result.json');pairs=[]
    fields = ('status','headers_base64','body_base64','body_length','body_sha256','body','before','after')
    for candidate,case in zip(result['records'],fixture()['cases']):
        oracle = read(reference/'auth-reference'/(case['id']+'.json'))
        differences = {field:literal_differences(oracle[field],candidate[field]) for field in fields}
        pairs.append({'id':case['id'],'literal_fields':{k:not v for k,v in differences.items()},
            'raw_body_bytes_equal':None if oracle['body_base64'] is None else oracle['body_base64']==candidate['body_base64'],
            'raw_body_comparison':'unavailable-reference-token-body' if oracle['body_base64'] is None else 'literal-retained-bytes',
            'unresolved':{k:v for k,v in differences.items() if v}})
    packet = {'schema':'trillionnium.native-auth15-literal-comparison.v1','reference_commit':REFERENCE_COMMIT,
        'reference_tree':REFERENCE_TREE,'reference_database_profile':'postgresql','candidate_identity':identity,
        'reference_signature_verified':False,'candidate_known_key_signature_verified':True,
        'comparison_scope':'retained observations; signature-verifier metadata is not a protocol field; digest equality is not replayable token-body proof',
        'token_response_commitments_reconstructible':False,
        'case_count':15,'pairs':pairs,'normalization_applied':False,'normalizer_registry_approved':False,
        'all_fifteen_exact_pairs':False,'all_retained_fields_equal':all(not p['unresolved'] for p in pairs),**CLAIMS}
    output.parent.mkdir(parents=True,exist_ok=True)
    write(output.parent,output.name,packet)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=('capture','seal','compare'))
    p.add_argument('--profile',choices=('postgresql','cockroachdb'));p.add_argument('--container')
    p.add_argument('--parent',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--reference',type=Path);p.add_argument('--native',type=Path)
    p.add_argument('--candidate-commit');p.add_argument('--candidate-tree')
    p.add_argument('--run-id');p.add_argument('--run-attempt');p.add_argument('--job');a=p.parse_args()
    if a.mode=='capture': capture(a.profile,a.container,a.parent,a.output)
    elif a.mode=='seal':
        if not seal(a.output,a.profile): raise SystemExit('incomplete native auth packet; no execution credit')
    else:
        assert a.profile and a.candidate_commit and a.candidate_tree and a.run_id and a.run_attempt and a.job, "external candidate identity required"
        compare(a.reference,a.native,a.output,a.profile,a.candidate_commit,a.candidate_tree,
            {'run_id':a.run_id,'run_attempt':a.run_attempt,'job':a.job})
if __name__=='__main__': main()
