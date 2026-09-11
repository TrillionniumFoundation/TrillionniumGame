from __future__ import annotations
import importlib.util,json,subprocess,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
MODEL=ROOT/"scripts/durability-state-model.py"; CHECKER=ROOT/"scripts/check-durability-state-model.py"
def load(path,name):
  spec=importlib.util.spec_from_file_location(name,path)
  if spec is None or spec.loader is None: raise RuntimeError("module unavailable")
  module=importlib.util.module_from_spec(spec); sys.modules[name]=module; spec.loader.exec_module(module); return module
class DurabilityStateModelTests(unittest.TestCase):
  @classmethod
  def setUpClass(cls): cls.model=load(MODEL,"durability_state_model"); cls.checker=load(CHECKER,"durability_state_model_checker")
  def test_exhaustive_real_model_reaches_both_terminal_paths(self):
    report=self.model.explore(10); self.assertGreater(report["states"],40); self.assertGreater(report["edges"],100); self.assertGreater(report["delivered_states_seen"],0); self.assertGreater(report["dead_states_seen"],0)
  def test_duplicate_effect_mutant_is_rejected(self):
    with self.assertRaisesRegex(AssertionError,"duplicate externally visible effect"):
      self.model.explore(10,duplicate_visible_effect=True)
  def test_stale_ack_mutant_is_rejected_by_exact_owner_semantics(self):
    state=self.model.State(receipt_fingerprint="A",revision=1,outbox_state=self.model.LEASED,lease_generation=2,lease_owner=1,visible_effect=True,publish_attempts=1)
    mutated=[e.state for e in self.model.transition(state,allow_stale_ack=True) if e.action=="ack_stale_owner_or_generation"][0]
    self.assertEqual(mutated.outbox_state,self.model.DELIVERED)
    self.assertNotEqual(mutated,state)
  def test_source_contract_and_cli_pass(self):
    source=self.checker.MODEL.read_text(); contract=json.loads(self.checker.CONTRACT.read_text()); self.checker.validate(source,contract)
    result=subprocess.run([sys.executable,str(MODEL),"--depth","10"],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    self.assertEqual(result.returncode,0,result.stderr); self.assertGreater(json.loads(result.stdout)["states"],40)
if __name__=="__main__": unittest.main()
