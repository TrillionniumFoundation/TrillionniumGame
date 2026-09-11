from __future__ import annotations
import importlib.util,subprocess,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; CHECKER=ROOT/"scripts/check-remote-mac-unix-transport.py"
def load_checker():
  spec=importlib.util.spec_from_file_location("remote_mac_unix_checker",CHECKER)
  if spec is None or spec.loader is None: raise RuntimeError("checker unavailable")
  module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module
class RemoteMacUnixTransportContractTests(unittest.TestCase):
  @classmethod
  def setUpClass(cls): cls.checker=load_checker(); cls.source=cls.checker.SOURCE.read_text(); cls.lib=cls.checker.LIB.read_text()
  def test_real_contract_passes(self): self.checker.validate(self.source,self.lib)
  def test_raw_key_or_timeout_removal_rejected(self):
    with self.assertRaisesRegex(self.checker.ValidationError,"forbidden"): self.checker.validate(self.source+"raw_key",self.lib)
    with self.assertRaisesRegex(self.checker.ValidationError,"set_read_timeout"): self.checker.validate(self.source.replace("set_read_timeout","removed"),self.lib)
  def test_unix_module_and_export_gates_fail_closed(self):
    ungated_module=self.lib.replace("#[cfg(unix)]\nmod remote_unix;","mod remote_unix;",1)
    with self.assertRaisesRegex(self.checker.ValidationError,"module not gated"): self.checker.validate(self.source,ungated_module)
    ungated_export=self.lib.replace("#[cfg(unix)]\npub use remote_unix::UnixSocketRemoteMacTransport;","pub use remote_unix::UnixSocketRemoteMacTransport;",1)
    with self.assertRaisesRegex(self.checker.ValidationError,"export not gated"): self.checker.validate(self.source,ungated_export)
  def test_cli_passes(self):
    result=subprocess.run([sys.executable,str(CHECKER)],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    self.assertEqual(result.returncode,0,result.stderr); self.assertIn("Unix transport boundary: OK",result.stdout)
if __name__=="__main__": unittest.main()
