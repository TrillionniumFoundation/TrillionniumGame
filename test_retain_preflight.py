"""Synthetic retention supplements; no fabricated remote acceptance."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import retain_preflight as r


def tests_log(n=411):
    return ('\n'.join(f'test_{i} (suite.Case.test_{i}) ... ok' for i in range(n))
            + f'\n\nRan {n} tests in 1.234s\n\nOK\n').encode()

class Tests(unittest.TestCase):
    def test_exact_test_log(self):
        self.assertEqual(len(r.validate_tests(tests_log())), 411)

    def test_missing_extra_duplicate_and_failed_test(self):
        valid=tests_log()
        for bad in (valid.replace(b'test_0 (suite.Case.test_0) ... ok\n',b''),
                    valid.replace(b'test_1 (suite.Case.test_1)',b'test_0 (suite.Case.test_0)'),
                    valid.replace(b'... ok',b'... FAIL',1),
                    valid.replace(b'... ok',b'... skipped',1),
                    valid+valid, valid+b'FAILED\n', b'Ran 411 tests in 1.234s\nOK\n'):
            with self.subTest(size=len(bad)), self.assertRaises(r.RetentionError):r.validate_tests(bad)

    def test_empty_and_oversized_logs(self):
        for bad in (b'',b'x'*(8*1024*1024+1)):
            with self.assertRaises(r.RetentionError):r.validate_tests(bad)

    def test_json_duplicate_and_nonfinite(self):
        for bad in (b'{"a":1,"a":2}',b'{"a":NaN}'):
            with self.assertRaises(r.RetentionError):r.load_json(bad)

    def test_noncanonical_paths(self):
        for bad in ('../x','/x','x/../y','x//y','.git/index','x\\y'):
            with self.assertRaises(r.RetentionError):r.canonical(bad)

    def packet(self, root, edit=None):
        obs={'source_head':r.HEAD,'source_tree':r.TREE,'claims':{'accepted':False}}
        files={'observation.json':json.dumps(obs).encode(),'data':b'original'}
        index=[{'path':n,'sha256':hashlib.sha256(v).hexdigest(),'size_bytes':len(v)} for n,v in files.items()]
        files['file-index.json']=json.dumps(index).encode()
        if edit:edit(files)
        p=Path(root)/'in.zip'
        with zipfile.ZipFile(p,'w') as z:
            for n,v in files.items():z.writestr(n,v)
        return p

    def test_original_index_preserves_all_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(r.verified_packet(self.packet(d))['data'],b'original')

    def test_tampered_or_unindexed_input_rejected(self):
        for edit in (lambda f:f.update(data=b'changed'),lambda f:f.update(extra=b'unindexed')):
            with tempfile.TemporaryDirectory() as d, self.assertRaises(r.RetentionError):
                r.verified_packet(self.packet(d,edit))

    def test_index_boolean_size_rejected(self):
        def edit(files):
            rows=json.loads(files['file-index.json']); rows[1]['size_bytes']=True
            files['file-index.json']=json.dumps(rows).encode()
        with tempfile.TemporaryDirectory() as d, self.assertRaises(r.RetentionError):
            r.verified_packet(self.packet(d,edit))

    def data(self):
        commit=('tree '+r.TREE+'\n\nfixture\n').encode()
        head=hashlib.sha1(b'commit '+str(len(commit)).encode()+b'\0'+commit).hexdigest()
        producer={'repository':r.REPO,'workflow_commit':'a'*40,'run_id':1,'run_attempt':1,'job':'test_job'}
        return commit,head,producer

    def test_supplement_reindexes_and_preserves_incomplete_observation(self):
        commit,head,producer=self.data()
        with patch.object(r,'HEAD',head),tempfile.TemporaryDirectory() as d:
            p=self.packet(d); out=Path(d)/'out.zip'
            result=r.augment(p,out,tests_log(),b'controls ok',commit,producer)
            files=r.verified_packet(out)
            self.assertFalse(result['accepted'])
            self.assertEqual(result['producer_tests'],411)
            self.assertEqual(files['source-commit.raw'],commit)
            self.assertEqual(files['data'],b'original')
            self.assertFalse(json.loads(files['observation.json'])['claims']['accepted'])
            self.assertEqual(len(json.loads(files['producer/preflight-retention.json'])['tests']['identities']),411)

    def test_wrong_raw_commit_has_no_output(self):
        _,_,producer=self.data()
        with tempfile.TemporaryDirectory() as d:
            p=self.packet(d);out=Path(d)/'out.zip'
            with self.assertRaises(r.RetentionError):r.augment(p,out,tests_log(),b'ok',b'wrong',producer)
            self.assertFalse(out.exists())

    def test_wrong_producer_has_no_output(self):
        commit,head,producer=self.data()
        for key,value in (('repository','other/repo'),('workflow_commit','wrong'),('run_id',True),('run_attempt',0),('job','')):
            with patch.object(r,'HEAD',head),tempfile.TemporaryDirectory() as d:
                p=self.packet(d);out=Path(d)/'out.zip'
                with self.assertRaises(r.RetentionError):r.augment(p,out,tests_log(),b'ok',commit,{**producer,key:value})
                self.assertFalse(out.exists())

    def test_existing_output_preserved(self):
        commit,_,producer=self.data()
        with tempfile.TemporaryDirectory() as d:
            p=self.packet(d);out=Path(d)/'out.zip';out.write_bytes(b'user file')
            with self.assertRaises(r.RetentionError):r.augment(p,out,tests_log(),b'ok',commit,producer)
            self.assertEqual(out.read_bytes(),b'user file')

    def test_extra_supplement_collision_rejected(self):
        commit,head,producer=self.data()
        with patch.object(r,'HEAD',head),tempfile.TemporaryDirectory() as d:
            p=self.packet(d);out=Path(d)/'out.zip'
            r.augment(p,out,tests_log(),b'ok',commit,producer)
            with self.assertRaises(r.RetentionError):
                r.augment(out,Path(d)/'twice.zip',tests_log(),b'ok',commit,producer)

if __name__=='__main__':unittest.main()
