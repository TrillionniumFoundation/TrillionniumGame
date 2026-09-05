import copy
import tempfile
import unittest
from pathlib import Path
import queue_cleanup as q


def fixtures():
 p={'number':63,'state':'open','draft':True,'merged':False,
    'head':{'sha':q.HEAD,'ref':q.BRANCH,'repo':{'id':q.REPO_ID}},
    'base':{'sha':q.BASE,'ref':'main','repo':{'id':q.REPO_ID}}}
 r={'id':q.RUN,'head_sha':q.STALE_HEAD,'head_branch':q.BRANCH,'event':'pull_request',
    'workflow_id':q.WORKFLOW,'path':'.github/workflows/prospective-merge-gate.yml',
    'repository':{'id':q.REPO_ID},'head_repository':{'id':q.REPO_ID},'run_attempt':1,
    'pull_requests':[{'number':63}],'status':'queued','conclusion':None}
 j={'total_count':2,'jobs':[{'id':i,'run_id':q.RUN,'run_attempt':1,'head_sha':q.STALE_HEAD,
      'status':'queued','conclusion':None,'runner_id':0,'steps':[]} for i in (11,12)]}
 return p,r,j

class Fake:
 def __init__(self):
  self.pr,self.run,self.jobs=fixtures();self.requests=[];self.after=False;self.move=False
 def request(self,path,method='GET'):
  self.requests.append((path,method))
  if method=='POST':
   assert path==q.RUN_PATH+'/cancel';self.after=True;return {}
  if path=='/pulls/63':
   if self.move and len(self.requests)>4:return {**self.pr,'head':{**self.pr['head'],'sha':'0'*40}}
   return copy.deepcopy(self.pr)
  if path==f'/git/commits/{q.HEAD}':return {'sha':q.HEAD,'parents':[{'sha':q.STALE_HEAD}]}
  if path.endswith('/jobs?per_page=100'):return copy.deepcopy(self.jobs)
  return {**self.run,'status':'completed','conclusion':'cancelled'} if self.after else copy.deepcopy(self.run)

class Tests(unittest.TestCase):
 def test_dry_run_never_writes(self):
  f=Fake();r=q.perform(f);self.assertFalse(r['cancel_requested']);self.assertTrue(all(m=='GET' for _,m in f.requests))
 def test_only_fixed_run_is_cancelled_once(self):
  f=Fake();r=q.perform(f,True);self.assertTrue(r['cancel_confirmed']);self.assertEqual([(p,m) for p,m in f.requests if m=='POST'],[(q.RUN_PATH+'/cancel','POST')])
 def test_current_run_cannot_be_cancelled(self):
  f=Fake();f.run['head_sha']=q.HEAD
  with self.assertRaises(q.CleanupError):q.perform(f,True)
  self.assertTrue(all(m=='GET' for _,m in f.requests))
 def test_wrong_run_or_repository_or_event_rejected(self):
  for k,v in (('id',123),('event','push'),('workflow_id',456),('head_branch','main'),('run_attempt',2),('head_sha','f'*40)):
   with self.subTest(k=k):
    f=Fake();f.run[k]=v
    with self.assertRaises(q.CleanupError):q.perform(f,True)
    self.assertTrue(all(m=='GET' for _,m in f.requests))
 def test_running_completed_or_assigned_jobs_rejected(self):
  for k,v in (('status','in_progress'),('status','completed'),('runner_id',1),('steps',[{'name':'started'}]),('run_attempt',2)):
   with self.subTest(k=k):
    f=Fake();f.jobs['jobs'][0][k]=v
    with self.assertRaises(q.CleanupError):q.perform(f,True)
    self.assertTrue(all(m=='GET' for _,m in f.requests))
 def test_empty_partial_duplicate_collections_rejected(self):
  for jobs in ({'total_count':0,'jobs':[]},{'total_count':3,'jobs':fixtures()[2]['jobs']},{'total_count':2,'jobs':[fixtures()[2]['jobs'][0]]*2}):
   f=Fake();f.jobs=jobs
   with self.assertRaises(q.CleanupError):q.perform(f,True)
   self.assertTrue(all(m=='GET' for _,m in f.requests))
 def test_candidate_movement_prevents_post(self):
  f=Fake();f.move=True
  with self.assertRaises(q.CleanupError):q.perform(f,True)
  self.assertTrue(all(m=='GET' for _,m in f.requests))
 def test_wrong_current_base_or_non_draft_prevents_post(self):
  for kind in ('base','draft','repository'):
   f=Fake()
   if kind=='base':f.pr['base']['sha']='f'*40
   elif kind=='draft':f.pr['draft']=False
   else:f.pr['head']['repo']['id']=1
   with self.assertRaises(q.CleanupError):q.perform(f,True)
   self.assertTrue(all(m=='GET' for _,m in f.requests))
 def test_parent_check_prevents_post(self):
  class Wrong(Fake):
   def request(self,path,method='GET'):
    if '/git/commits/' in path:return {'sha':q.HEAD,'parents':[]}
    return super().request(path,method)
  f=Wrong()
  with self.assertRaises(q.CleanupError):q.perform(f,True)
  self.assertTrue(all(m=='GET' for _,m in f.requests))
 def test_completed_old_run_not_cancelled(self):
  f=Fake();f.run['status']='completed';f.run['conclusion']='success'
  with self.assertRaises(q.CleanupError):q.perform(f,True)
  self.assertTrue(all(m=='GET' for _,m in f.requests))
 def test_no_generic_write_endpoint(self):
  with tempfile.TemporaryDirectory() as d:
   c=q.Client('synthetic-token',Path(d))
   for path,method in ((q.RUN_PATH+'/force-cancel','POST'),('/git/refs/heads/main','POST'),(q.RUN_PATH,'DELETE'),('/actions/runs/1/cancel','POST')):
    with self.assertRaises(q.CleanupError):c.request(path,method)
 def test_queued_after_post_is_not_confirmed(self):
  class Delayed(Fake):
   def request(self,path,method='GET'):
    value=super().request(path,method)
    if path==q.RUN_PATH and method=='GET':return copy.deepcopy(self.run)
    return value
  f=Delayed();s=[];r=q.perform(f,True,s.append)
  self.assertTrue(r['cancel_requested']);self.assertFalse(r['cancel_confirmed']);self.assertEqual(s,[1,1,1])

if __name__=='__main__':unittest.main()
