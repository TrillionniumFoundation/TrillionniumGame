"""Pure source/mock composite regression; no Git, network, Cargo or native work."""
from pathlib import Path
import base64,copy,hashlib,importlib.util,io,json,os,sys,tarfile,tempfile,unittest,urllib.parse
from unittest import mock
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
sys.path.insert(0,str(ROOT/'tests/control_plane'))
from source_http_mock import mock_source_process
import schema_evidence_binding as B
S=B.SOURCE

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
IDENTITY=load('composite_identity','scripts/check-authoritative-schema-identity.py')
ARCHIVE=load('composite_archive','scripts/verify-actions-log-artifact.py')
HEAD='a'*40;TREE='b'*40

def schema(token,mode='fresh'):
    selected=S.current_selection_document(B.selection_token(token))['selection']
    return {'schema':'trillionnium.authoritative-schema-report.v1','profile':B.binding_document(token)['profile'],
            'schema_version':4,'storage_writer_epoch':4,'chain_digest':selected['execution_chain_digest'],
            'digest_algorithm':S.ALGORITHM,'table_count':12,'source_commit':HEAD,'upgrade_source_commit':HEAD,
            'v2_apply_source_commit':HEAD,'v3_apply_source_commit':HEAD,'migration_applied':mode=='fresh',
            'applied_steps':4 if mode=='fresh' else 0,'compatibility_credit':False}

def annex(token):
    proof=B.binding_document(token)
    return {B.SIDECAR:B.canonical(proof),B.HEAD_PROOF:B.canonical(B.head_document(token,commit=HEAD,tree=TREE)),
            **{row['archive_path']:(ROOT/row['path']).read_bytes() for row in proof['full_source_inventory']}}

def outbox_files(token):
    proof=B.binding_document(token);op=B.operational_binding(token);profile=proof['profile']
    identity={'repository':'TrillionniumFoundation/TrillionniumGame','commit':HEAD,'tree':TREE,'profile':profile,
              'image':op['image'],'run_id':'123456','run_attempt':'2','evidence_run_id':'123456-2-'+profile,
              **ARCHIVE.producer_identity(profile),**B.identity_fields(token),
              **{k:op[k] for k in ('migration_lock','schema_version','storage_writer_epoch','chain_digest','digest_algorithm')}}
    env=lambda v:''.join(k+'='+str(w)+'\n' for k,w in v.items()).encode()
    files={**annex(token),'identity.env':env(identity),
           'result.env':env({'status':'passed','profile':profile,'commit':HEAD,'tree':TREE}),
           'schema-identity.json':B.canonical(schema(token)),
           'migration-chain.lock.json':(ROOT/S.LOCK_PATH).read_bytes(),
           'migration-chain-validation.json':B.canonical(proof['source_selection']['source']['complete_validation']),
           **{row['path']:(ROOT/row['path']).read_bytes() for row in op['ordered_files']}}
    for label,count,lost in (('crash-before-publish','0','true'),('crash-after-publish','1','false')):
        files[label+'/result.env']=env({'possible_lost_effect_declared':lost,'spool_effect_count':count,'outbox_row_count':'1','dead_letter_count':'1'})
        files[label+'/reaper.stdout']=b'claimed=0 completed=0 retried=0 dead_lettered=1\n'
    files['crash-after-publish/spool/effect.json']=b'{}\n'
    return files

def archive(files):
    files=dict(files)
    files['files.sha256']=''.join(B.sha(raw)+'  ./'+name+'\n' for name,raw in sorted(files.items())).encode()
    output=io.BytesIO()
    with tarfile.open(fileobj=output,mode='w:gz') as tar:
        for name,raw in sorted(files.items()):
            item=tarfile.TarInfo('./'+name);item.size=len(raw);tar.addfile(item,io.BytesIO(raw))
    return output.getvalue()

class SchemaEvidenceBindingTests(unittest.TestCase):
    def token(self,profile='postgresql'):
        return B.verify_binding(ROOT,profile=profile)
    def verify_archive(self,token,files):
        return ARCHIVE.validate_archive(archive(files),repository='TrillionniumFoundation/TrillionniumGame',
               head_sha=HEAD,head_tree=TREE,run_id='123456',run_attempt='2',profile=B.binding_document(token)['profile'],binding=token)
    def test_complete_source5_both_profiles_then_original4_identity(self):
        for profile in B.PROFILES:
            token=self.token(profile);p=B.binding_document(token)
            self.assertEqual(p['full_source_inventory_count'],18)
            for q in B.PROFILES:self.assertEqual(p['source_selection']['source']['profiles'][q]['file_count'],5)
            self.assertEqual(p['source_selection']['selection']['execution_chain_digest'],S.ORIGINAL4_DIGESTS[profile])
            for mode in ('fresh','verify'):
                IDENTITY.validate_identity(schema(token,mode),profile=profile,selection=B.selection_token(token),mode=mode,
                                           source_commit=HEAD if mode=='fresh' else None)
    def test_identity_forgeries_counts_unknown_and_publisher_drift(self):
        token=self.token();value=schema(token)
        for field,bad in [('schema_version',5),('schema_version',True),('table_count',14),('table_count',True),
                          ('applied_steps',5),('applied_steps',True),('storage_writer_epoch',5),('upgrade_source_commit','c'*40),('surprise',1)]:
            with self.subTest(field=field,bad=bad),self.assertRaises(IDENTITY.ValidationError):
                IDENTITY.validate_identity(value|{field:bad},profile='postgresql',selection=B.selection_token(token),mode='fresh',source_commit=HEAD)
        for forged in (None,{},S.current_selection_document(B.selection_token(token)),B.binding_document(token)):
            with self.assertRaises(IDENTITY.ValidationError):IDENTITY.validate_identity(value,profile='postgresql',selection=forged)
    def test_exact_enum_member_accounts_and_pseudo_member_before_path_io(self):
        class ForbiddenPath:
            def __fspath__(self):raise AssertionError('unexpected path IO/conversion')
        for target in (S.SchemaTarget.NakamaAccountsV5,object.__new__(S.SchemaTarget),'StorageV4',4,True):
            with self.subTest(target=type(target)),self.assertRaises(S.SelectionError):B.verify_binding(ForbiddenPath(),profile='postgresql',target=target)
    def test_real_2005_directory_is_streamed_without_listdir_or_sql_reads(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'migrations/postgresql').mkdir(parents=True)
            (root/S.LOCK_PATH).write_bytes((ROOT/S.LOCK_PATH).read_bytes())
            for n in range(2005):(root/'migrations/postgresql'/f'extra-{n}.txt').write_bytes(b'')
            observed=[];reads=[];real_scan=os.scandir;real_read=S._regular
            class Scan:
                def __init__(self,fd):self.scan=real_scan(fd)
                def __enter__(self):self.scan.__enter__();return self
                def __exit__(self,*a):return self.scan.__exit__(*a)
                def __iter__(self):return self
                def __next__(self):
                    v=next(self.scan);observed.append(v.name)
                    if len(observed)>17:raise AssertionError('stream consumed past first over-budget entry')
                    return v
            def read(*a):reads.append(a[1]);return real_read(*a)
            with mock.patch.object(os,'listdir',side_effect=AssertionError('listdir collection forbidden')),mock.patch.object(os,'scandir',Scan),mock.patch.object(S,'_regular',read):
                with self.assertRaisesRegex(S.SelectionError,'profile_inventory_budget'):S._snapshot(root)
            self.assertEqual(len(observed),17);self.assertFalse(any(p.endswith('.sql') for p in reads))
    def test_all10_annex_body_and_sidecar_closed_types(self):
        token=self.token();files=annex(token);B.validate_annex_files(token,files,commit=HEAD,tree=TREE)
        for name in files:
            altered=dict(files);del altered[name]
            with self.subTest(missing=name),self.assertRaises((B.BindingError,S.SelectionError)):
                B.validate_annex_files(token,altered,commit=HEAD,tree=TREE)
        for path,value in [(('full_source_inventory_count',),True),(('source_selection','source','frontier_schema_version'),4),
                           (('source_selection','selection','executed_migration_file_count'),True),(('claims','accepted'),True)]:
            proof=B.binding_document(token);current=proof
            for key in path[:-1]:current=current[key]
            current[path[-1]]=value;altered=files|{B.SIDECAR:B.canonical(proof)}
            with self.assertRaises(B.BindingError):B.validate_annex_files(token,altered,commit=HEAD,tree=TREE)
        proof=B.binding_document(token);proof['unknown']=False
        with self.assertRaises(B.BindingError):B.validate_annex_files(token,files|{B.SIDECAR:B.canonical(proof)},commit=HEAD,tree=TREE)
        with self.assertRaises(B.BindingError):B.validate_annex_files(token,files|{B.ANNEX+'/users.ndjson':b'{}'},commit=HEAD,tree=TREE)
    def test_current_composite_archives_both_profiles_reject_shortened_rehashed_sources(self):
        for profile in B.PROFILES:
            token=self.token(profile);files=outbox_files(token);result=self.verify_archive(token,files)
            self.assertEqual(result['schema_version'],4);self.assertEqual(result['source_frontier_schema_version'],5)
            names=[B.SIDECAR,B.HEAD_PROOF]+[r['archive_path'] for r in B.binding_document(token)['full_source_inventory']]
            for name in names:
                altered=dict(files);del altered[name]
                with self.subTest(profile=profile,missing=name),self.assertRaises((ARCHIVE.VerificationError,B.BindingError,S.SelectionError)):
                    self.verify_archive(token,altered)
            body=B.binding_document(token);body['source_selection']['source']['complete_validation']['profiles'][profile]['file_count']=True
            with self.assertRaises(ARCHIVE.VerificationError):self.verify_archive(token,files|{B.SIDECAR:B.canonical(body)})
            fake=(ROOT/S.LOCK_PATH).read_bytes().replace(b'"schema_version": 5',b'"schema_version": 4')
            with self.assertRaises(ARCHIVE.VerificationError):self.verify_archive(token,files|{'migration-chain.lock.json':fake})
    def test_issued_binding_cannot_be_constructed_or_substituted(self):
        token=self.token()
        for fake in (B.binding_document(token),B.operational_binding(token),object.__new__(type(token))):
            with self.assertRaises(B.BindingError):B.binding_document(fake)
        with self.assertRaises(TypeError):type(token)()
        with self.assertRaises(TypeError):copy.deepcopy(token)
    def test_annex_directory_rejects_unknown_and_replaced_fifth_body(self):
        token=self.token()
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);B.write_annex(token,ROOT,root,commit=HEAD,tree=TREE)
            B.validate_annex_directory(token,root,commit=HEAD,tree=TREE)
            fifth=root/B.ANNEX/'migrations/cockroachdb/0005_nakama_accounts_up.sql';fifth.write_bytes(fifth.read_bytes()+b'\n')
            with self.assertRaises(B.BindingError):B.validate_annex_directory(token,root,commit=HEAD,tree=TREE)
    def test_composite_two_profile_source_receipts_are_closed_and_cannot_rehash_shortened_annex(self):
        tokens=tuple(self.token(profile) for profile in B.PROFILES)
        for prefix,name,version in [('source-selection','external-workflow-collection.json','trillionnium.required-workflow-retained-collection.v2'),
                                    ('source-selection','final-gate-receipt.json','trillionnium.merge-gate-retained-receipt.v2'),
                                    ('final-source-selection','final-gate-receipt.json','trillionnium.prospective-merge-retained-gate.v2')]:
            with tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary)
                for profile,token in zip(B.PROFILES,tokens):
                    B.write_annex(token,ROOT,root/prefix/profile,commit=HEAD,tree=TREE)
                receipt={'schema':version,'repository':'TrillionniumFoundation/TrillionniumGame','run_id':'123','run_attempt':'2',
                         'schema_source_selection':{profile:B.binding_document(token) for profile,token in zip(B.PROFILES,tokens)},
                         'claims':{'accepted_evidence':False,'gap_closed':False,'production_ready':False}}
                if name=='external-workflow-collection.json':
                    receipt.update(head=HEAD,workflows=[{'schema_source_selection':{profile:B.identity_fields(token) for profile,token in zip(B.PROFILES,tokens)}} for _ in range(55)])
                elif prefix=='source-selection':receipt.update(head=HEAD,event='pull_request',lanes={})
                else:receipt.update(base_commit='c'*40,source_head='d'*40,prospective_merge=HEAD,lanes={})
                path=root/name;path.write_bytes(B.canonical(receipt))
                args={'prefix':prefix,'receipt_filename':name,'commit':HEAD,'tree':TREE}
                self.assertEqual(B.validate_composite_directory(tokens,root,**args)['execution_schema_version'],4)
                for forged in ((tokens[0],tokens[0]),tuple(B.binding_document(token) for token in tokens),tokens[:1]):
                    with self.assertRaises(B.BindingError):B.validate_composite_directory(forged,root,**args)
                for change in (receipt|{'surprise':True},receipt|{'claims':{'accepted_evidence':0,'gap_closed':False,'production_ready':False}},receipt|{'schema_source_selection':{'postgresql':B.binding_document(tokens[0])}}):
                    path.write_bytes(B.canonical(change))
                    with self.assertRaises(B.BindingError):B.validate_composite_directory(tokens,root,**args)
                path.write_bytes(B.canonical(receipt))
                fifth=root/prefix/'cockroachdb'/B.ANNEX/'migrations/cockroachdb/0005_nakama_accounts_up.sql'
                original=fifth.read_bytes();fifth.write_bytes(original+b'-- rehashed cannot authorize different frontier\n')
                with self.assertRaises(B.BindingError):B.validate_composite_directory(tokens,root,**args)
                fifth.write_bytes(original)
                (root/prefix/'surprise').mkdir()
                with self.assertRaises(B.BindingError):B.validate_composite_directory(tokens,root,**args)

    def test_full55_pretty_composite_budget_preserves_both_profile_closed_bindings(self):
        tokens=tuple(self.token(profile) for profile in B.PROFILES)
        fields={profile:B.identity_fields(token) for profile,token in zip(B.PROFILES,tokens)}
        receipt={'schema':'trillionnium.required-workflow-retained-collection.v2',
                 'repository':'TrillionniumFoundation/TrillionniumGame','head':HEAD,
                 'run_id':'37131332288','run_attempt':'1',
                 'schema_source_selection':{profile:B.binding_document(token) for profile,token in zip(B.PROFILES,tokens)},
                 'workflows':[{'workflow_id':1000+i,'path':f'.github/workflows/source-fixture-{i}.yml',
                               'run_id':37130000000+i,'run_attempt':1,'jobs':2,'catalog_name_observed':f'fixture {i}',
                               'run_name':f'fixture {i}','definition_blob_sha1':'e'*40,
                               'catalog_path_alias_verified':False,'schema_source_selection':fields} for i in range(55)],
                 'claims':{'accepted_evidence':False,'gap_closed':False,'production_ready':False}}
        raw=(json.dumps(receipt,indent=2,ensure_ascii=False)+'\n').encode()
        self.assertGreater(len(raw),S.MAX_DOCUMENT_BYTES)
        self.assertLess(len(raw),B.MAX_COMPOSITE_RECEIPT_BYTES)
        with self.assertRaises(S.SelectionError):B.strict(raw,'standalone_source')
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            for profile,token in zip(B.PROFILES,tokens):
                B.write_annex(token,ROOT,root/'source-selection'/profile,commit=HEAD,tree=TREE)
            path=root/'external-workflow-collection.json';path.write_bytes(raw)
            args={'prefix':'source-selection','receipt_filename':path.name,'commit':HEAD,'tree':TREE}
            result=B.validate_composite_directory(tokens,root,**args)
            self.assertEqual(result['execution_schema_version'],4)
            self.assertEqual(result['authoritative_source_files_per_profile'],18)
            self.assertFalse(result['accepted']);self.assertFalse(result['runtime_execution_verified'])
            mutations=[receipt|{'schema':'unknown'},receipt|{'head':'c'*40},
                       receipt|{'schema_source_selection':{'postgresql':B.binding_document(tokens[0])}},
                       receipt|{'schema_source_selection':receipt['schema_source_selection']|{'other':{}}},
                       receipt|{'workflows':receipt['workflows'][:-1]},receipt|{'workflows':receipt['workflows']+[receipt['workflows'][0]]}]
            wrong=copy.deepcopy(receipt);wrong['workflows'][-1]['schema_source_selection']['cockroachdb']['execution_schema_version']=5
            mutations.append(wrong)
            for changed in mutations:
                with self.subTest(mutation=changed.keys()):
                    path.write_bytes(B.canonical(changed))
                    with self.assertRaises(B.BindingError):B.validate_composite_directory(tokens,root,**args)
            path.write_bytes(b'{}'+b' '*(B.MAX_COMPOSITE_RECEIPT_BYTES-1))
            with self.assertRaises((B.BindingError,S.SelectionError)):B.validate_composite_directory(tokens,root,**args)

    def test_composite_json_exact_cap_duplicate_nonfinite_and_standalone_limit(self):
        self.assertEqual(B.MAX_COMPOSITE_RECEIPT_BYTES,512*1024)
        self.assertEqual(S.MAX_DOCUMENT_BYTES,128*1024)
        self.assertEqual(len(B.strict_composite_receipt(b'['*64+b'0'+b']'*64)),1)
        payload=b'{"nested":{"list":[true,null,1]}}'
        exact=payload+b' '*(B.MAX_COMPOSITE_RECEIPT_BYTES-len(payload))
        self.assertEqual(B.strict_composite_receipt(exact),{'nested':{'list':[True,None,1]}})
        quoted=json.dumps({'escaped':'[{}]'+chr(34)+chr(92)}).encode()
        self.assertEqual(B.strict_composite_receipt(quoted),{'escaped':'[{}]'+chr(34)+chr(92)})
        for raw in (exact+b' ',bytearray(payload),b'{"nested":{"a":1,"a":2}}',
                    b'{"a":NaN}',b'{"a":Infinity}',b'\xff',b'['*65+b'0'+b']'*65):
            with self.subTest(raw_type=type(raw),size=len(raw)),self.assertRaises(B.BindingError):
                B.strict_composite_receipt(raw)
        with self.assertRaises(S.SelectionError):B.strict(b'{}'+b' '*S.MAX_DOCUMENT_BYTES)

    def test_remote_binding_fetch_full_immutable_tree_without_live_requests(self):
        token=self.token();paths=[r['path'] for r in B.binding_document(token)['full_source_inventory']]
        data={p:(ROOT/p).read_bytes() for p in paths}
        tree={'sha':TREE,'truncated':False,'tree':[{'path':p,'type':'blob','mode':'100644','sha':B.blob(v),'size':len(v)} for p,v in data.items()]}
        responses=[]
        class Response(io.BytesIO):
            def __init__(self,body):super().__init__(body);self.headers={'Content-Length':str(len(body))};self.read_sizes=[]
            def read(self,size=-1):self.read_sizes.append(size);return super().read(size)
        def transport(document):
            def open_mock(request,timeout):
                self.assertEqual(timeout,ARCHIVE.SOURCE_HTTP_TIMEOUT_SECONDS)
                url=urllib.parse.urlsplit(request.full_url)
                if url.path.endswith('/git/trees/'+TREE):payload=document
                else:
                    self.assertEqual(urllib.parse.parse_qs(url.query),{'ref':[HEAD]})
                    path=urllib.parse.unquote(url.path.split('/contents/',1)[1]);raw=data[path]
                    payload={'path':path,'type':'file','encoding':'base64','size':len(raw),'sha':B.blob(raw),'content':base64.b64encode(raw).decode()}
                response=Response(json.dumps(payload).encode());responses.append(response);return response
            return open_mock
        with mock_source_process(ARCHIVE.SOURCE_HTTP), mock.patch.object(ARCHIVE.SOURCE_HTTP,'open_source_url',side_effect=transport(tree)):
            issued=ARCHIVE.fetch_profile_bindings('mock-token','TrillionniumFoundation/TrillionniumGame',HEAD,head_tree=TREE)
            self.assertEqual(set(issued),set(B.PROFILES))
            for profile,t in issued.items():self.assertEqual(B.operational_binding(t)['chain_digest'],S.ORIGINAL4_DIGESTS[profile])
            for altered in (tree|{'truncated':True},tree|{'tree':tree['tree'][:-1]},tree|{'tree':tree['tree']+[tree['tree'][0]]}):
                with mock.patch.object(ARCHIVE.SOURCE_HTTP,'open_source_url',side_effect=transport(altered)),self.assertRaises(ARCHIVE.VerificationError):
                    ARCHIVE.fetch_profile_bindings('mock-token','TrillionniumFoundation/TrillionniumGame',HEAD,head_tree=TREE)
        self.assertEqual(len(responses),22)
        self.assertTrue(all(response.closed for response in responses))
        self.assertTrue(all(response.read_sizes in ([ARCHIVE.MAX_SOURCE_FILE_JSON_BYTES+1],[ARCHIVE.MAX_SOURCE_TREE_JSON_BYTES+1]) for response in responses))
if __name__=='__main__':unittest.main()
