from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "trnm_pg_tls_readiness", ROOT / "scripts/wait-postgresql-tls-ready.py",
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load the TLS readiness implementation")
READY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = READY
SPEC.loader.exec_module(READY)


class Harness:
    def __init__(self, results):
        self.results = list(results)
        self.now = 0.0
        self.calls = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def run(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if not self.results:
            raise AssertionError("unexpected subprocess call")
        value = self.results.pop(0)
        if isinstance(value, BaseException):
            if isinstance(value, subprocess.TimeoutExpired):
                self.now += kwargs["timeout"]
            raise value
        code, output, duration = value
        self.now += duration
        return SimpleNamespace(returncode=code, stdout=output, stderr="secret-diagnostic")

    def wait(self, timeout=10):
        return READY.wait_ready(
            "trnm-pg-old", "trillionnium_tls", timeout_seconds=timeout,
            run=self.run, clock=self.clock, sleep=self.sleep,
        )


class PostgresTlsReadinessTests(unittest.TestCase):
    def test_real_sql_tls_success_is_bounded_no_credit_receipt(self):
        harness = Harness([(0, "true\n", 0), (0, "ready\n", 0.1)])
        result = harness.wait()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["transport"], "tcp-verify-full")
        self.assertFalse(result["tls_rotation_credit"])
        self.assertFalse(result["production_ready"])
        self.assertNotIn("secret", json.dumps(result))
        self.assertEqual(result["attempts"], 1)

    def test_initialization_transition_retries_sql_not_just_pg_isready(self):
        harness = Harness([
            (0, "true", 0), (1, "", 0),
            (0, "true", 0), (0, "ready", 0),
        ])
        self.assertEqual(harness.wait()["attempts"], 2)
        self.assertEqual(len(harness.calls), 4)

    def test_queries_use_tcp_verify_full_and_actual_session_tls(self):
        harness = Harness([(0, "true", 0), (0, "ready", 0)])
        harness.wait()
        command, options = harness.calls[1]
        self.assertIn("--host=127.0.0.1", command)
        self.assertIn("PGSSLMODE=verify-full", command)
        self.assertIn("PGSSLROOTCERT=/var/lib/postgresql/root.crt", command)
        self.assertIn("PGCONNECT_TIMEOUT=2", command)
        self.assertIn("PGOPTIONS=-c statement_timeout=2000", command)
        self.assertIn("PGPASSWORD", command)
        self.assertFalse(any(value.startswith("PGPASSWORD=") for value in command))
        self.assertIn("pg_stat_ssl", command[-1])
        self.assertIn("pg_backend_pid()", command[-1])
        self.assertIn("pg_is_in_recovery()", command[-1])
        self.assertNotIn("pg_isready", command)
        self.assertLessEqual(options["timeout"], 3)
        self.assertNotIn("shell", options)

    def test_stopped_container_is_immediate_failure(self):
        harness = Harness([(0, "false", 0)])
        with self.assertRaisesRegex(READY.ReadinessError, "unavailable or stopped"):
            harness.wait()
        self.assertEqual(len(harness.calls), 1)

    def test_missing_container_is_immediate_failure(self):
        with self.assertRaisesRegex(READY.ReadinessError, "unavailable or stopped"):
            Harness([(1, "", 0)]).wait()

    def test_unknown_container_state_is_rejected(self):
        with self.assertRaises(READY.ReadinessError):
            Harness([(0, "unknown", 0)]).wait()

    def test_no_tls_or_empty_or_extra_output_never_earns_readiness(self):
        for output in ("not-ready", "", "ready\nnot-ready"):
            with self.subTest(output=output):
                harness = Harness([(0, "true", 0), (0, output, 0)])
                with self.assertRaisesRegex(READY.ReadinessError, "deadline exceeded"):
                    harness.wait(timeout=1)

    def test_failed_sql_with_ready_output_is_not_success(self):
        with self.assertRaisesRegex(READY.ReadinessError, "deadline exceeded"):
            Harness([(0, "true", 0), (1, "ready", 0)]).wait(timeout=1)

    def test_command_timeout_can_retry_within_total_budget(self):
        harness = Harness([
            (0, "true", 0), subprocess.TimeoutExpired("redacted", 3),
            (0, "true", 0), (0, "ready", 0),
        ])
        self.assertEqual(harness.wait()["attempts"], 2)

    def test_inspect_timeout_is_bounded_by_remaining_budget(self):
        harness = Harness([subprocess.TimeoutExpired("redacted", 3)])
        with self.assertRaisesRegex(READY.ReadinessError, "deadline exceeded"):
            harness.wait(timeout=0.5)
        self.assertEqual(harness.calls[0][1]["timeout"], 0.5)
        self.assertEqual(harness.now, 0.5)

    def test_remaining_budget_covers_inspection_and_query(self):
        harness = Harness([(0, "true", 0.75), (0, "ready", 0.1)])
        harness.wait(timeout=1)
        self.assertAlmostEqual(harness.calls[1][1]["timeout"], 0.25)

    def test_late_success_is_rejected(self):
        harness = Harness([(0, "true", 0), (0, "ready", 1)])
        with self.assertRaisesRegex(READY.ReadinessError, "after deadline"):
            harness.wait(timeout=1)

    def test_invalid_input_executes_nothing(self):
        for name in ("", "-bad", "../pg", "pg;echo", "x" * 129):
            with self.subTest(name=name):
                with self.assertRaises(READY.ReadinessError):
                    READY.wait_ready(name, "db", run=lambda *a, **k: self.fail("executed"))
        for timeout in (0, -1, 301, float("inf"), float("nan")):
            with self.subTest(timeout=timeout):
                with self.assertRaises(READY.ReadinessError):
                    READY.wait_ready("pg", "db", timeout_seconds=timeout,
                                     run=lambda *a, **k: self.fail("executed"))

    def test_subprocess_error_does_not_leak_arguments_or_diagnostics(self):
        harness = Harness([OSError("PASSWORD=do-not-print")])
        with self.assertRaises(READY.ReadinessError) as raised:
            harness.wait()
        self.assertNotIn("do-not-print", str(raised.exception))
        self.assertNotIn("secret-diagnostic", str(raised.exception))

    def test_cli_missing_password_fails_closed(self):
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stderr(output):
            self.assertEqual(READY.main(["--container", "pg", "--database", "db"]), 1)
        self.assertIn("ephemeral test PGPASSWORD is required", output.getvalue())

    def test_cli_emits_json_only_on_success(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"PGPASSWORD": "do-not-print"}), \
                patch.object(READY, "wait_ready", return_value={"status": "ready"}), \
                contextlib.redirect_stdout(output):
            self.assertEqual(READY.main(["--container", "pg", "--database", "db"]), 0)
        self.assertEqual(json.loads(output.getvalue()), {"status": "ready"})
        self.assertNotIn("do-not-print", output.getvalue())


if __name__ == "__main__":
    unittest.main()
