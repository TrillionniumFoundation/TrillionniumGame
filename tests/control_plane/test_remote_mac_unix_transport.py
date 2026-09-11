from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/check-remote-mac-unix-transport.py"


def load_checker():
    spec = importlib.util.spec_from_file_location("remote_mac_unix_checker", CHECKER)
    if spec is None or spec.loader is None:
        raise RuntimeError("checker unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RemoteMacUnixTransportContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checker = load_checker()
        cls.source = cls.checker.SOURCE.read_text(encoding="utf-8")
        cls.lib = cls.checker.LIB.read_text(encoding="utf-8")
        cls.contract = json.loads(cls.checker.CONTRACT.read_text(encoding="utf-8"))

    def test_real_contract_passes(self):
        self.checker.validate(self.source, self.lib, self.contract)

    def test_raw_key_or_total_deadline_removal_rejected(self):
        with self.assertRaisesRegex(self.checker.ValidationError, "forbidden"):
            self.checker.validate(self.source + "raw_key", self.lib, self.contract)
        for marker in (
            "recv_timeout",
            "remaining_timeout",
            "read_exact_before_deadline",
            "slow_drip_response_cannot_extend_total_deadline",
        ):
            with self.subTest(marker=marker):
                mutated = self.source.replace(marker, "removed-deadline-marker")
                self.assertNotIn(marker, mutated)
                with self.assertRaisesRegex(self.checker.ValidationError, "missing"):
                    self.checker.validate(mutated, self.lib, self.contract)

    def test_per_syscall_timeout_reset_is_rejected(self):
        mutated = self.source.replace(
            "set_read_timeout(Some(remaining_timeout(deadline)?))",
            "set_read_timeout(Some(Duration::from_secs(1)))",
            1,
        )
        with self.assertRaisesRegex(self.checker.ValidationError, "read remaining-time"):
            self.checker.validate(mutated, self.lib, self.contract)

    def test_direct_unbounded_read_bypass_is_rejected(self):
        mutated = self.source.replace(
            "read_exact_before_deadline(&mut stream, &mut response, deadline)?;",
            "stream.read_exact(&mut response).map_err(map_io_error)?;",
            1,
        )
        with self.assertRaisesRegex(
            self.checker.ValidationError, "response body read|direct read_exact"
        ):
            self.checker.validate(mutated, self.lib, self.contract)

    def test_wrapper_substitution_is_rejected(self):
        mutated = self.source.replace(
            "read_exact_before_deadline(&mut stream, &mut response, deadline)?;",
            "read_body_before_deadline(&mut stream, &mut response, deadline)?;",
            1,
        )
        with self.assertRaisesRegex(self.checker.ValidationError, "response body read"):
            self.checker.validate(mutated, self.lib, self.contract)

    def test_dead_comment_and_string_markers_do_not_satisfy_call_graph(self):
        production_call = (
            "read_exact_before_deadline(&mut stream, &mut response, deadline)?;"
        )
        mutated = self.source.replace(
            production_call,
            "stream.read(&mut response).map_err(map_io_error)?;",
            1,
        )
        dead = (
            "// read_exact_before_deadline remaining_timeout\n"
            'const DEAD_DEADLINE_MARKERS: &str = "read_exact_before_deadline remaining_timeout";\n'
        )
        mutated = mutated.replace("#[cfg(test)]", dead + "#[cfg(test)]", 1)
        with self.assertRaisesRegex(self.checker.ValidationError, "response body read"):
            self.checker.validate(mutated, self.lib, self.contract)

    def test_connect_wait_must_derive_from_same_deadline(self):
        mutated = self.source.replace(
            "receiver.recv_timeout(remaining_timeout(deadline)?)",
            "receiver.recv_timeout(Duration::from_secs(1))",
            1,
        )
        with self.assertRaisesRegex(self.checker.ValidationError, "connect remaining-time"):
            self.checker.validate(mutated, self.lib, self.contract)

    def test_unix_module_and_export_gates_fail_closed(self):
        ungated_module = self.lib.replace(
            "#[cfg(unix)]\nmod remote_unix;", "mod remote_unix;", 1
        )
        with self.assertRaisesRegex(self.checker.ValidationError, "module not gated"):
            self.checker.validate(self.source, ungated_module, self.contract)
        ungated_export = self.lib.replace(
            "#[cfg(unix)]\npub use remote_unix::UnixSocketRemoteMacTransport;",
            "pub use remote_unix::UnixSocketRemoteMacTransport;",
            1,
        )
        with self.assertRaisesRegex(self.checker.ValidationError, "export not gated"):
            self.checker.validate(self.source, ungated_export, self.contract)

    def test_timeout_contract_mutation_is_rejected(self):
        changed = json.loads(json.dumps(self.contract))
        changed["timeout_semantics"] = "per syscall"
        with self.assertRaisesRegex(self.checker.ValidationError, "total timeout"):
            self.checker.validate(self.source, self.lib, changed)
        changed = json.loads(json.dumps(self.contract))
        changed["maximum_pending_connects"] = 0
        with self.assertRaisesRegex(self.checker.ValidationError, "connect budget"):
            self.checker.validate(self.source, self.lib, changed)

    def test_cli_passes(self):
        result = subprocess.run(
            [sys.executable, str(CHECKER)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Unix transport boundary: OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
