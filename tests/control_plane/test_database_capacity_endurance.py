from __future__ import annotations
import hashlib,importlib.util,json,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
CHECKER=ROOT/"scripts/check-database-capacity-endurance.py"
FINALIZER=ROOT/"scripts/finalize-database-endurance-ledger.py"
def load(path,name):
  spec=importlib.util.spec_from_file_location(name,path)
  if spec is None or spec.loader is None: raise RuntimeError("module unavailable")
  module=importlib.util.module_from_spec(spec); sys.modules[name]=module; spec.loader.exec_module(module); return module
class DatabaseCapacityEnduranceTests(unittest.TestCase):
  @classmethod
  def setUpClass(cls): cls.checker=load(CHECKER,"capacity_checker"); cls.finalizer=load(FINALIZER,"endurance_finalizer")
  def make(self,root,index,previous,duration=21600,failed=0,commit="a"*40):
    value={"schema":"trillionnium.database-endurance-segment.v1","segment_index":index,"previous_segment_sha256":previous,"profile":"postgresql","candidate_commit":commit,"candidate_tree":"b"*40,"workload_sha256":"c"*64,"observed_duration_seconds":duration,"transactions":100,"failed_transactions":failed,"latency_average_ms":1.0,"transactions_per_second":10.0}
    path=root/f"{index:02d}.json"; path.write_text(json.dumps(value,sort_keys=True)+"\n"); return path
  def test_24h_contiguous_chain_passes(self):
    with tempfile.TemporaryDirectory() as td:
      root=Path(td); paths=[]; previous="GENESIS"
      for index in range(4):
        path=self.make(root,index,previous); paths.append(path); previous=hashlib.sha256(path.read_bytes()).hexdigest()
      report=self.finalizer.validate(paths,"24h"); self.assertEqual(report["observed_duration_seconds"],86400)
  def test_gap_digest_failure_and_short_duration_rejected(self):
    with tempfile.TemporaryDirectory() as td:
      root=Path(td); first=self.make(root,0,"GENESIS")
      bad=self.make(root,2,"0"*64)
      with self.assertRaisesRegex(self.finalizer.ValidationError,"segment gap"): self.finalizer.validate([first,bad],"24h")
      with self.assertRaisesRegex(self.finalizer.ValidationError,"below"): self.finalizer.validate([first],"24h")
  def test_failed_transaction_and_candidate_change_rejected(self):
    with tempfile.TemporaryDirectory() as td:
      root=Path(td); first=self.make(root,0,"GENESIS"); digest=hashlib.sha256(first.read_bytes()).hexdigest()
      failed=self.make(root,1,digest,failed=1)
      with self.assertRaisesRegex(self.finalizer.ValidationError,"failed transactions"): self.finalizer.validate([first,failed],"24h")
  def test_source_contract_cli_passes(self):
    result=subprocess.run([sys.executable,str(CHECKER)],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    self.assertEqual(result.returncode,0,result.stderr); self.assertIn("capacity/endurance source contract: OK",result.stdout)
if __name__=="__main__": unittest.main()
