from __future__ import annotations
import importlib.util,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
MACHINE=ROOT/"scripts/cutover-state-machine.py"; DERIVE=ROOT/"scripts/derive-cutover-blocker-packet.py"
def load(path,name):
  spec=importlib.util.spec_from_file_location(name,path)
  if spec is None or spec.loader is None: raise RuntimeError("module unavailable")
  module=importlib.util.module_from_spec(spec); sys.modules[name]=module; spec.loader.exec_module(module); return module
class CutoverStateMachineTests(unittest.TestCase):
  @classmethod
  def setUpClass(cls): cls.machine=load(MACHINE,"cutover_machine_tests"); cls.derive=load(DERIVE,"cutover_derive_tests")
  def binding(self): return {"repository":"TrillionniumFoundation/TrillionniumGame","source_head":"a"*40,"source_tree":"b"*40,"prospective_merge":"c"*40,"prospective_merge_tree":"d"*40}
  def request(self,target="shadow"):
    return {"schema":"trillionnium.cutover-transition-request.v1","target_state":target,"candidate_binding":self.binding(),"accepted_gates":["SG0","SG1","SG2","SG3","SG4"],"reviewer":{"login":"reviewer","conflict_free_attestation":True},"candidate_author":"author","evidence_producers":["producer"],"rollback_packet":{"accepted":True,"sha256":"e"*64},"ordinary_protected_admission":True,"request_sha256":"f"*64}
  def clear_blockers(self): return {"schema":"trillionnium.cutover-blocker-packet.v1","candidate_binding":self.binding(),"open_p0_p1_count":0,"all_required_evidence_accepted":True,"independent_review_complete":True,"governance_readback_complete":True}
  def test_open_gap_and_self_approval_rejected(self):
    current={"schema":"trillionnium.cutover-state.v1","state":"planning","candidate_binding":None,"history":[]}
    blockers=self.clear_blockers(); blockers["open_p0_p1_count"]=1
    with self.assertRaisesRegex(self.machine.TransitionError,"open P0/P1"): self.machine.transition(current,self.request(),blockers)
    request=self.request(); request["reviewer"]["login"]="author"
    with self.assertRaisesRegex(self.machine.TransitionError,"self|candidate author"): self.machine.transition(current,request,self.clear_blockers())
  def test_skipped_transition_and_missing_rollback_rejected(self):
    current={"schema":"trillionnium.cutover-state.v1","state":"planning","candidate_binding":None,"history":[]}
    with self.assertRaisesRegex(self.machine.TransitionError,"illegal or skipped"): self.machine.transition(current,self.request("production"),self.clear_blockers())
    request=self.request(); request["rollback_packet"]["accepted"]=False
    with self.assertRaisesRegex(self.machine.TransitionError,"rollback"): self.machine.transition(current,request,self.clear_blockers())
  def test_valid_planning_to_shadow_only_sets_shadow(self):
    current={"schema":"trillionnium.cutover-state.v1","state":"planning","candidate_binding":None,"history":[]}
    result=self.machine.transition(current,self.request(),self.clear_blockers())
    self.assertEqual(result["state"],"shadow"); self.assertFalse(result["claims"]["public_online"])
  def test_real_gap_register_derives_blocked_packet(self):
    import json
    gaps=json.loads((ROOT/"docs/status/GAP_REGISTER.json").read_text())
    packet=self.derive.derive(gaps,self.binding())
    self.assertGreater(packet["open_p0_p1_count"],0); self.assertFalse(packet["all_required_evidence_accepted"])
if __name__=="__main__": unittest.main()
