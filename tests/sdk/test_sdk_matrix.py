from __future__ import annotations
import json,tempfile,unittest
from pathlib import Path
from tools.sdk.matrix import SDKMatrixError,generate,load_registry

def lock(root:Path,repo:str,commit:str,tree:str):
 root.mkdir(parents=True,exist_ok=True);(root/'.trillionnium-source-lock.json').write_text(json.dumps({'repository':repo,'revision':commit,'tree':tree}))
def fixture(base:Path):
 nak=base/'nakama';common=base/'common';sdks=base/'sdks'
 lock(nak,'heroiclabs/nakama','d4d92f93f78bbbe62c7fc50a3f85c772ec121a09','f3c9cfc2726d5543da1564629170f35b98e3797d');(nak/'apigrpc').mkdir();(nak/'apigrpc/apigrpc.proto').write_text('service N { '+''.join(f'rpc Op{i} (R) returns (R);' for i in range(60))+' }')
 lock(common,'heroiclabs/nakama-common','449b77ecc8789aa466c36b67f6e498033dfcd9c5','c6a7b9796b9c2a6b5118c74e5f213963a5001f14');(common/'rtapi').mkdir();(common/'rtapi/realtime.proto').write_text(''.join(f'message Event{i} {{ string id = 1; }}' for i in range(30)))
 profiles=[]
 for i in range(10):
  profile={'id':f'sdk{i}','repository':f'heroiclabs/nakama-sdk{i}','branch':'main','commit':f'{i+1:040x}'[-40:],'tree':f'{i+101:040x}'[-40:],'language':'x','platform':'x'};profiles.append(profile);root=sdks/profile['id'];lock(root,profile['repository'],profile['commit'],profile['tree']);(root/'client.ts').write_text('async function Op1Async(){} class Event2 {}')
 registry={'schema':'trillionnium.sdk-source-snapshots.v1','project_id':'trillionnium-game','status':'candidate-default-branch-snapshots','profiles':profiles,'claims':{'release_line_selected':False,'operation_coverage_verified':False,'transport_profiles_verified':False,'support_windows_verified':False,'sg1_complete':False,'compatibility_credit':False,'production_ready':False}}
 return registry,sdks,nak,common
class Tests(unittest.TestCase):
 def test_matrix_is_finite_deterministic_and_fail_closed(self):
  with tempfile.TemporaryDirectory() as t:
   r,s,n,c=fixture(Path(t));a=generate(r,s,n,c);b=generate(r,s,n,c);self.assertEqual(a,b);self.assertEqual(a['leaf_count'],10*(60+30));self.assertEqual(a['unclassified_count'],a['leaf_count']);self.assertFalse(a['sg1_eligible']);self.assertFalse(a['operation_coverage_verified']);self.assertTrue(any(x['contract']['candidate_presence']=='candidate-present' for x in a['leaves']));self.assertTrue(any(x['contract']['candidate_presence']=='candidate-missing' for x in a['leaves']))
 def test_registry_rejects_duplicates_and_positive_claims(self):
  with tempfile.TemporaryDirectory() as t:
   r,*_=fixture(Path(t));p=Path(t)/'r.json';r['profiles'][1]['id']=r['profiles'][0]['id'];p.write_text(json.dumps(r));
   with self.assertRaises(SDKMatrixError):load_registry(p)
   r,*_=fixture(Path(t)/'b');r['claims']['sg1_complete']=True;p.write_text(json.dumps(r));
   with self.assertRaises(SDKMatrixError):load_registry(p)
 def test_source_lock_tamper_is_rejected(self):
  with tempfile.TemporaryDirectory() as t:
   r,s,n,c=fixture(Path(t));(s/'sdk0/.trillionnium-source-lock.json').write_text('{}')
   with self.assertRaises(Exception):generate(r,s,n,c)
if __name__=='__main__':unittest.main()
