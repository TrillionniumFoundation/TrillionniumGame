from __future__ import annotations
import importlib.util,subprocess,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; CHECKER=ROOT/"scripts/check-cockroachdb-node-failover.py"
def load_checker():
  spec=importlib.util.spec_from_file_location("cockroachdb_node_failover_contract",CHECKER)
  if spec is None or spec.loader is None: raise RuntimeError("checker unavailable")
  module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module
class CockroachDbNodeFailoverContractTests(unittest.TestCase):
  @classmethod
  def setUpClass(cls): cls.checker=load_checker(); cls.text=cls.checker.HARNESS.read_text(encoding="utf-8")
  def test_real_contract_passes(self): self.checker.validate_text(self.text)
  def test_single_node_or_suppression_rejected(self):
    with self.assertRaisesRegex(self.checker.ValidationError,"single-node"): self.checker.validate_text(self.text+"\nsingle-node\n")
    with self.assertRaisesRegex(self.checker.ValidationError,"failure suppression"): self.checker.validate_text(self.text+"\nfalse || true\n")
  def test_command_line_checker_passes(self):
    result=subprocess.run([sys.executable,str(CHECKER)],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    self.assertEqual(result.returncode,0,result.stderr); self.assertIn("node failover source contract: OK",result.stdout)
if __name__=="__main__": unittest.main()
