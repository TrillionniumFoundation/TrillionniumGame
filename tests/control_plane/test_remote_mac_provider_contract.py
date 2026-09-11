from __future__ import annotations
import importlib.util,json,subprocess,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; CHECKER=ROOT/"scripts/check-remote-mac-provider.py"
def load_checker():
  spec=importlib.util.spec_from_file_location("remote_mac_provider_contract",CHECKER)
  if spec is None or spec.loader is None: raise RuntimeError("checker unavailable")
  module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module
class RemoteMacProviderContractTests(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.checker=load_checker(); cls.source=cls.checker.SOURCE.read_text(); cls.contract=json.loads(cls.checker.CONTRACT.read_text())
  def test_real_contract_passes(self): self.checker.validate(self.source,self.contract)
  def test_raw_key_and_fallback_rejected(self):
    for marker in ("raw_key","SoftwareHs256Provider"):
      with self.subTest(marker=marker):
        with self.assertRaisesRegex(self.checker.ValidationError,"forbidden"): self.checker.validate(self.source+marker,self.contract)
  def test_debug_redaction_markers_are_mandatory(self):
    for marker in (
      "impl fmt::Debug for RemoteMacRequest",
      "impl fmt::Debug for RemoteMacResponse",
      "<redacted-tag>",
      "request_kind_and_response_debug_never_expose_sensitive_bytes",
    ):
      with self.subTest(marker=marker):
        mutated=self.source.replace(marker,"removed-redaction-marker")
        with self.assertRaisesRegex(self.checker.ValidationError,"missing"): self.checker.validate(mutated,self.contract)
  def test_positive_claim_rejected(self):
    value=json.loads(json.dumps(self.contract)); value["claim_boundary"]["production_ready"]=True
    with self.assertRaisesRegex(self.checker.ValidationError,"positive"): self.checker.validate(self.source,value)
  def test_command_line_checker_passes(self):
    result=subprocess.run([sys.executable,str(CHECKER)],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    self.assertEqual(result.returncode,0,result.stderr); self.assertIn("remote MAC provider boundary: OK",result.stdout)
if __name__=="__main__": unittest.main()
