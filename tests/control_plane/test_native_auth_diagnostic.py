"""Synthetic validator/source negatives; these tests grant no native execution."""
import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
import tempfile
import os
import sys
import time
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('native_auth_diagnostic',ROOT/'scripts/native-auth-diagnostic.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class NativeAuthDiagnosticTests(unittest.TestCase):
    def identity(self):
        return dict(repository='TrillionniumFoundation/TrillionniumGame',commit='a'*40,
            tree='b'*40,profile='postgresql',run_id='1',run_attempt='1',job='fixture')

    def synthetic(self):
        # These are synthetic data-shape fixtures, not captured signatures or
        # executed product observations. Actual HMAC behavior has Rust negatives.
        records=[];snapshot={'users':[],'user_device':[]};source_claims=None
        for i,case in enumerate(M.fixture()['cases']):
            before=copy.deepcopy(snapshot);body={};clock=100+i
            if case['created'] is True:
                kind=case['id'].split('-')[0];uid='11111111-1111-4111-8111-111111111111' if kind=='device' else '22222222-2222-4222-8222-222222222222'
                user=dict(id=uid,username='trnm_reference_'+kind,custom_id=None if kind=='device' else 'trnm-reference-custom',create_time='2026-10-04T00:00:00+00:00',update_time='2026-10-04T00:00:00+00:00',disable_time='1970-01-01T00:00:00+00:00',metadata={},wallet={},edge_count=0)
                snapshot['users'].append(user)
                if kind=='device':snapshot['user_device'].append({'id':'trnm-reference-device','user_id':uid})
            if case['code'] is not None:body={'code':case['code'],'message':case['message']}
            elif case['created'] is not None:
                kind='device' if case['id'].startswith('device') else 'custom'
                user=next(u for u in snapshot['users'] if u['username']=='trnm_reference_'+kind)
                claims=dict(uid=user['id'],usn=user['username'],tid=f'00000000-0000-4000-8000-{i:012d}',iat=clock)
                if case['id']=='refresh-valid':claims=copy.deepcopy(source_claims)
                if case['id']=='custom-existing':source_claims=copy.deepcopy(claims)
                for field,ttl in (('token',3600),('refresh_token',86400)):
                    body[field]={'sha256':'sha256:'+'0'*64,'length':288,'header':{'alg':'HS256','typ':'JWT'},'claims':dict(claims,exp=clock+ttl),'signature_verified':True,'signature_verifier':'openssl-hmac-sha256-known-fixture-key'}
                if case['created']:body['created']=True
            raw=json.dumps(body,separators=(',',':')).encode()
            records.append(dict(id=case['id'],status=case['status'],body=body,before=before,after=copy.deepcopy(snapshot),started_at_epoch=clock,completed_at_epoch=clock,raw_tokens_retained=False,body_base64=None if 'token' in body else base64.b64encode(raw).decode(),body_length=len(raw),body_sha256='sha256:'+hashlib.sha256(raw).hexdigest()))
        for row in records:
            header=f"HTTP/1.1 {row['status']} Fixture\r\nContent-Length: {row['body_length']}\r\n\r\n".encode()
            row.update(headers_base64=base64.b64encode(header).decode(),headers=[['Content-Length',str(row['body_length'])]],request_length=100,response_length=len(header)+row['body_length'],request_sha256='sha256:'+'0'*64,response_sha256='sha256:'+hashlib.sha256(header+(base64.b64decode(row['body_base64']) if row['body_base64'] else b'')).hexdigest())
        return dict(schema='trillionnium.native-auth15-diagnostic.v1',source_commit='a'*40,profile='postgresql',database='trnm_native_auth',fixture_sha256='sha256:'+M.FIXTURE_SHA,execution='actual-server-tcp-pool-native-accounts-test-admission',records=records,completed_case_count=15,attempted_request_count=15,incomplete_attempt_may_have_effect=False,capture_complete=True,failure=None,actual_authenticated_drain_joined=True,same_candidate_migration4_to5=True,prior4_publisher_retained=True,public_gate_closed=True,raw_tokens_retained=False,keys_retained=False,normalization_applied=False,**M.CLAIMS)

    def test_synthetic_schema_shape_is_not_live_evidence(self):
        M.validate_result(self.synthetic(),'postgresql','a'*40)

    def test_rejects_custody_execution_and_claim_changes(self):
        for key,value in [(k,True) for k in (*M.CLAIMS,'normalization_applied','keys_retained','raw_tokens_retained')]+[(k,False) for k in ('capture_complete','actual_authenticated_drain_joined','same_candidate_migration4_to5','prior4_publisher_retained','public_gate_closed')]+[('completed_case_count',14),('attempted_request_count',16),('incomplete_attempt_may_have_effect',True),('profile','cockroachdb'),('source_commit','b'*40),('fixture_sha256','sha256:'+'0'*64)]:
            item=self.synthetic();item[key]=value
            with self.subTest(key=key),self.assertRaises(AssertionError):M.validate_result(item,'postgresql','a'*40)

    def test_rejects_token_identity_signature_and_refresh_custody_lies(self):
        for field,value in [('signature_verified',False),('signature_verifier','decoded-only')]:
            item=self.synthetic();item['records'][3]['body']['token'][field]=value
            with self.subTest(field=field),self.assertRaises(AssertionError):M.validate_result(item,'postgresql','a'*40)
        for key,value in [('uid','99999999-9999-4999-8999-999999999999'),('usn','trnm_reference_device'),('iat',1),('exp',1)]:
            item=self.synthetic();item['records'][12]['body']['token']['claims'][key]=value
            with self.subTest(key=key),self.assertRaises(AssertionError):M.validate_result(item,'postgresql','a'*40)
        item=self.synthetic();item['records'][4]['body']['token']['claims']['tid']=item['records'][3]['body']['token']['claims']['tid'];item['records'][4]['body']['refresh_token']['claims']['tid']=item['records'][3]['body']['token']['claims']['tid']
        with self.assertRaises(AssertionError):M.validate_result(item,'postgresql','a'*40)

    def test_rejects_database_effect_and_case_order_changes(self):
        item=self.synthetic();item['records'][0],item['records'][1]=item['records'][1],item['records'][0]
        with self.assertRaises(AssertionError):M.validate_result(item,'postgresql','a'*40)
        item=self.synthetic();item['records'][3]['after']['user_device'][0]['user_id']='wrong'
        with self.assertRaises((AssertionError,ValueError)):M.validate_result(item,'postgresql','a'*40)
        item=self.synthetic();item['records'][14]['after']['users'][0]['wallet']={'amount':1}
        with self.assertRaises(AssertionError):M.validate_result(item,'postgresql','a'*40)

    def test_literal_comparator_never_normalizes_identity_time_signature_or_order(self):
        left={'uid':'a','iat':1,'exp':2,'signature_verified':False,'rows':[{'id':'a'},{'id':'b'}]}
        for key,value in [('uid','b'),('iat',2),('exp',3),('signature_verified',True),('rows',[{'id':'b'},{'id':'a'}])]:
            right=copy.deepcopy(left);right[key]=value
            self.assertTrue(M.literal_differences(left,right),key)
        self.assertTrue(M.literal_differences(False,0))
        self.assertEqual(M.literal_differences(left,copy.deepcopy(left)),[])
        self.assertEqual(left['rows'],[{'id':'a'},{'id':'b'}])

    def test_retained_reader_rejects_duplicate_and_noninteger_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'record.json'
            for text in ('{"a":1,"a":2}','{"a":1.5}','{"a":NaN}'):
                path.write_text(text)
                with self.assertRaises(ValueError):M.read(path)

    def test_unknown_secret_surfaces_are_rejected(self):
        for level in ('result','record','token'):
            item=self.synthetic()
            target=item if level=='result' else item['records'][3] if level=='record' else item['records'][3]['body']['token']
            target['unrecognized_secret']='never retain this field'
            with self.subTest(level=level),self.assertRaises(AssertionError):M.validate_result(item,'postgresql','a'*40)
        item=self.synthetic();item['records'][0]['headers_base64']=base64.b64encode(b'HTTP/1.1 404 Fixture\r\nAuthorization: hidden\r\n\r\n').decode()
        with self.assertRaises(AssertionError):M.validate_result(item,'postgresql','a'*40)

    def test_response_commitment_detects_raw_body_and_header_tampering(self):
        for change in ('digest','header'):
            item=self.synthetic();row=item['records'][0]
            if change == 'digest': row['response_sha256']='sha256:'+'f'*64
            else:
                raw=base64.b64decode(row['headers_base64']).replace(b'Fixture',b'Changed')
                row['headers_base64']=base64.b64encode(raw).decode()
            with self.subTest(change=change),self.assertRaises(AssertionError):
                M.validate_result(item,'postgresql','a'*40)

    def test_timeout_kills_and_reaps_owned_descendant_without_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pidfile=root/'pid'
            child="import os,time;open("+repr(str(pidfile))+",'w').write(str(os.getpid()));time.sleep(30)"
            parent="import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',"+repr(child)+"]);time.sleep(30)"
            started=time.monotonic()
            with self.assertRaises(TimeoutError):
                M.run_bounded([sys.executable,'-c',parent],dict(os.environ),root/'log',0.4)
            self.assertLess(time.monotonic()-started,3)
            pid=int(pidfile.read_text())
            with self.assertRaises(ProcessLookupError):os.kill(pid,0)

    def test_log_limit_does_not_limit_compiler_output_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);artifact=root/'artifact'
            command="open("+repr(str(artifact))+",'wb').write(b'x'*4096);print('ok')"
            with mock.patch.object(M,'LOG_MAX_BYTES',128):
                M.run_bounded([sys.executable,'-c',command],dict(os.environ),root/'log',2)
                with self.assertRaises(RuntimeError):
                    M.run_bounded([sys.executable,'-c',"print('x'*4096)"],dict(os.environ),root/'overflow',2)
            self.assertEqual(artifact.stat().st_size,4096)

    def test_failure_after_success_log_cannot_be_sealed_or_compared(self):
        success_line='test result: ok. 1 passed; 0 failed; 0 ignored;'
        real_cleanup=M.cleanup_owned
        def incomplete_cleanup(process):
            real_cleanup(process)
            raise RuntimeError('simulated cleanup acknowledgement failure after actual cleanup')
        for mode in ('timeout','nonzero','overflow','cleanup','missing','success'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                M.write(root,'container-custody.json',{'id':'owned'})
                M.write(root,'native-auth-result.json',self.synthetic())
                tail={'timeout':'import time;time.sleep(30)','nonzero':'raise SystemExit(7)',
                    'overflow':"print('x'*4096)",'cleanup':'','success':'','missing':''}[mode]
                command='print('+repr(success_line)+',flush=True);'+tail
                if mode == 'missing': (root/'native-auth-test.log').write_text(success_line)
                else:
                    with mock.patch.object(M,'LOG_MAX_BYTES',128),mock.patch.object(M,'cleanup_owned',side_effect=incomplete_cleanup if mode=='cleanup' else real_cleanup):
                        if mode=='success': M.run_bounded([sys.executable,'-c',command],dict(os.environ),root/'native-auth-test.log',0.2,self.identity())
                        else:
                            with self.assertRaises((TimeoutError,RuntimeError)):
                                M.run_bounded([sys.executable,'-c',command],dict(os.environ),root/'native-auth-test.log',0.2,self.identity())
                    outcome=M.read(root/'native-auth-test.log.executor.json')
                    self.assertEqual(outcome['success'],mode=='success')
                    if mode=='timeout':self.assertTrue(outcome['timed_out'])
                    if mode=='nonzero':self.assertEqual(outcome['exit_status'],7)
                    if mode=='overflow':self.assertTrue(outcome['log_overflow'])
                    if mode=='cleanup':self.assertFalse(outcome['descendant_cleanup_complete'])
                def packet(output,profile,commit,tree):
                    M.validate_executor(output,self.identity(),[sys.executable,'-c',command])
                    M.validate_result(M.read(output/'native-auth-result.json'),profile,commit)
                def run(*args):
                    if args[0]=='docker':return ''
                    return 'a'*40 if args[-1]=='HEAD' else 'b'*40
                with mock.patch.object(M.RAW,'run',side_effect=run),mock.patch.object(M,'validate_packet',side_effect=packet),mock.patch.dict(os.environ,CANDIDATE_SHA='a'*40):
                    self.assertEqual(M.seal(root,'postgresql'),mode=='success')
                    self.assertEqual(M.read(root/'lifecycle.json')['capture_complete'],mode=='success')
                    if mode=='success':M.validate_sealed(root,'postgresql','a'*40,'b'*40)
                    else:
                        with self.assertRaises(AssertionError):M.validate_sealed(root,'postgresql','a'*40,'b'*40)

    def test_executor_rejects_copied_identity_and_replaced_observations(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            M.write(root,'native-auth-result.json',self.synthetic())
            command=[sys.executable,'-c',"print('test result: ok. 1 passed; 0 failed; 0 ignored;')"]
            M.run_bounded(command,dict(os.environ),root/'native-auth-test.log',2,self.identity())
            M.validate_executor(root,self.identity(),command)
            # A successful unrelated command never qualifies as the actual Cargo invocation.
            with self.assertRaises(AssertionError):M.validate_executor(root,self.identity())
            for field,value in [('profile','cockroachdb'),('run_id','2'),('run_attempt','2'),
                ('job','other'),('commit','c'*40),('tree','d'*40),('repository','other/repo')]:
                other=self.identity();other[field]=value
                with self.subTest(field=field),self.assertRaises(AssertionError):
                    M.validate_executor(root,other,command)
            log=root/'native-auth-test.log';original=log.read_bytes()
            log.write_bytes(original+b'replaced')
            with self.assertRaises(AssertionError):M.validate_executor(root,self.identity(),command)
            log.write_bytes(original)
            result=root/'native-auth-result.json';result.write_bytes(result.read_bytes()+b' ')
            with self.assertRaises(AssertionError):M.validate_executor(root,self.identity(),command)

    def test_seal_rejects_tamper_missing_lifecycle_and_candidate_substitution(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            M.write(root,'container-custody.json',{'id':'owned'})
            lifecycle={'container_id':'owned','container_removed':True,'capture_complete':True,**M.CLAIMS}
            M.write(root,'lifecycle.json',lifecycle)
            def reseal():
                (root/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in sorted(root.iterdir()) if p.name!='SHA256SUMS'))
            reseal()
            with mock.patch.object(M.RAW,'run',return_value='b'*40),mock.patch.object(M,'validate_packet') as packet:
                M.validate_sealed(root,'postgresql','a'*40,'b'*40)
                packet.assert_called_once_with(root,'postgresql','a'*40,'b'*40)
                with self.assertRaises(AssertionError):M.validate_sealed(root,'postgresql','c'*40,'d'*40)
                for key in ('container_removed','capture_complete'):
                    bad=dict(lifecycle);bad[key]=False
                    (root/'lifecycle.json').write_text(json.dumps(bad));reseal()
                    with self.assertRaises(AssertionError):M.validate_sealed(root,'postgresql','a'*40,'b'*40)
                (root/'lifecycle.json').write_text(json.dumps(lifecycle));reseal()
                (root/'container-custody.json').write_text('{"id":"tampered"}')
                with self.assertRaises(AssertionError):M.validate_sealed(root,'postgresql','a'*40,'b'*40)
                (root/'lifecycle.json').unlink();reseal()
                with self.assertRaises((OSError,AssertionError,M.B.SOURCE.SelectionError)):M.validate_sealed(root,'postgresql','a'*40,'b'*40)

    def test_actual_server_engine_and_fixture_source_contract(self):
        source=(ROOT/'crates/trnm-persistence-pg/src/schema_parts/account_native_auth.rs').read_text()
        for marker in ('migrate_authoritative_schema_admitted','open_verified_repository_admitted','serve_admitted','CREATE ROLE {role} NOLOGIN','"/-/drain"','capture_cases(','TRNM_NATIVE_AUTH_DISPOSABLE'):
            self.assertIn(marker,source)
        self.assertNotIn('impl Repository',source)
        self.assertNotIn('batch_execute(action.sql)',source)
        self.assertNotIn('set_var(',source)
        wire=(ROOT/'crates/trnm-persistence-pg/src/schema_parts/account_native_auth_wire.rs').read_text()
        for marker in ('Signer::new(MessageDigest::sha256()', 'openssl::memcmp::eq','token_view(token, key)?','TcpStream::connect_timeout','response deadline','credential echo','unrecognized token output'):
            self.assertIn(marker,wire)
        self.assertIn('native_auth_fixture_signature_check_is_not_claim_decoding',wire)
        self.assertEqual(hashlib.sha256((ROOT/'oracle/immutable/auth-reference-cases.json').read_bytes()).hexdigest(),M.FIXTURE_SHA)
        gate=(ROOT/'crates/trnm-persistence-pg/src/schema_parts/account_catalog.rs').read_text()
        self.assertIn('const ACCOUNT_CATALOG_CAPTURE_READY: bool = false;',gate)
