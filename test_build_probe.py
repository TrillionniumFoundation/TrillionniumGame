import os, sys, tempfile, unittest
from pathlib import Path
import build_probe as p

class ProbeTests(unittest.TestCase):
    def run_program(self, text, budget=2, limit=1024):
        with tempfile.TemporaryDirectory() as d:
            file=Path(d)/'log'
            row=p.command([sys.executable,'-c',text],Path(d),file,{'PATH':os.environ['PATH']},budget,limit)
            return row,file.read_bytes()
    def test_stdout_and_stderr_are_retained(self):
        row,data=self.run_program('import sys;print("ok");print("err",file=sys.stderr)')
        self.assertEqual(row['returncode'],0);self.assertIn(b'ok',data);self.assertIn(b'err',data)
    def test_failure_not_success(self):
        row,_=self.run_program('raise SystemExit(7)');self.assertEqual(row['returncode'],7)
    def test_timeout_is_terminal(self):
        row,_=self.run_program('import time;time.sleep(10)',budget=.08)
        self.assertEqual(row['failure'],'command-timeout');self.assertNotEqual(row['returncode'],0)
    def test_output_budget_is_hard(self):
        row,data=self.run_program('import os;os.write(1,b"x"*10000)',limit=32)
        self.assertEqual(row['failure'],'log-byte-budget');self.assertEqual(len(data),32)
    def test_exact_budget_can_pass(self):
        row,data=self.run_program('import os;os.write(1,b"x"*32)',limit=32)
        self.assertIsNone(row['failure']);self.assertEqual(len(data),32)
    def test_existing_log_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'log';path.write_bytes(b'user')
            with self.assertRaises(FileExistsError):p.command([sys.executable,'-c',''],Path(d),path,os.environ)
            self.assertEqual(path.read_bytes(),b'user')
    def test_positive_and_zero_binary_summaries(self):
        row=p.test_summary('test result: ok. 1 passed; 0 failed; 0 ignored;\ntest result: ok. 0 passed; 0 failed; 0 ignored;')
        self.assertEqual(row['reported_passed'],1);self.assertFalse(row['live_database_credit'])
    def test_zero_or_absent_test_discovery_rejected(self):
        for text in ('no tests', 'test result: ok. 0 passed; 0 failed; 0 ignored;'):
            with self.assertRaises(p.ProbeError):p.test_summary(text)
    def test_ignored_or_failed_summaries_rejected(self):
        for text in ('test result: ok. 1 passed; 1 failed; 0 ignored;', 'test result: ok. 1 passed; 0 failed; 1 ignored;'):
            with self.assertRaises(p.ProbeError):p.test_summary(text)
    def test_registered_source_plan(self):
        source=Path(__file__).resolve().parents[1]/'source'
        rows=p.scopes(source);self.assertEqual(len(rows),11)
        self.assertEqual(len({r[0] for r in rows}),11)

if __name__=='__main__':unittest.main()
