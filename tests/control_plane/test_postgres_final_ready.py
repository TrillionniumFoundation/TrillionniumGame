from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "wait-postgres-final-ready.py"
SPEC = importlib.util.spec_from_file_location("wait_postgres_final_ready", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class StableReadinessTests(unittest.TestCase):
    def test_transient_ready_rejecting_then_final_ready_requires_full_window(self) -> None:
        sequence = iter(
            [
                MODULE.ProbeResult(True, True, "temporary-ready"),
                MODULE.ProbeResult(True, False, "handoff-rejecting"),
                MODULE.ProbeResult(True, True, "final-1"),
                MODULE.ProbeResult(True, True, "final-2"),
                MODULE.ProbeResult(True, True, "final-3"),
            ]
        )
        sleeps: list[float] = []
        result = MODULE.wait_for_stable_readiness(
            lambda: next(sequence),
            attempts=5,
            consecutive_successes=3,
            interval_seconds=0.25,
            sleeper=sleeps.append,
        )
        self.assertEqual(result["attempts_used"], 5)
        self.assertEqual(result["stable_window_started_at_attempt"], 3)
        self.assertEqual(result["consecutive_successes"], 3)
        self.assertEqual(sleeps, [0.25, 0.25, 0.25, 0.25])

    def test_container_exit_resets_an_existing_success_streak(self) -> None:
        sequence = iter(
            [
                MODULE.ProbeResult(True, True, "first"),
                MODULE.ProbeResult(False, False, "container-restart"),
                MODULE.ProbeResult(True, True, "second"),
                MODULE.ProbeResult(True, True, "third"),
            ]
        )
        result = MODULE.wait_for_stable_readiness(
            lambda: next(sequence),
            attempts=4,
            consecutive_successes=2,
            interval_seconds=0,
            sleeper=lambda _: None,
        )
        self.assertEqual(result["attempts_used"], 4)
        self.assertEqual(result["stable_window_started_at_attempt"], 3)

    def test_exhaustion_fails_closed(self) -> None:
        with self.assertRaisesRegex(MODULE.ReadinessError, "not established"):
            MODULE.wait_for_stable_readiness(
                lambda: MODULE.ProbeResult(True, False, "rejecting"),
                attempts=3,
                consecutive_successes=2,
                interval_seconds=0,
                sleeper=lambda _: None,
            )

    def test_invalid_policy_is_rejected(self) -> None:
        for attempts, consecutive, interval in ((0, 1, 0), (2, 0, 0), (2, 3, 0), (2, 1, -1)):
            with self.subTest(attempts=attempts, consecutive=consecutive, interval=interval):
                with self.assertRaises(ValueError):
                    MODULE.wait_for_stable_readiness(
                        lambda: MODULE.ProbeResult(True, True, "ok"),
                        attempts=attempts,
                        consecutive_successes=consecutive,
                        interval_seconds=interval,
                        sleeper=lambda _: None,
                    )

    def test_docker_probe_uses_tcp_and_does_not_embed_password_bytes(self) -> None:
        calls: list[list[str]] = []

        def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            if command[1] == "inspect":
                return subprocess.CompletedProcess(command, 0, "true\n", "")
            return subprocess.CompletedProcess(command, 0, "1\n", "")

        result = MODULE.docker_postgres_probe(
            "trnm-postgres-test",
            "trnm",
            "trnm",
            runner=runner,
        )
        self.assertTrue(result.container_running)
        self.assertTrue(result.sql_ok)
        query = calls[1]
        shell = query[-1]
        self.assertIn("-h 127.0.0.1", shell)
        self.assertIn('$POSTGRES_PASSWORD', shell)
        self.assertNotIn("trnm_live_password", " ".join(query))
        self.assertIn("TRNM_READY_USER=trnm", query)
        self.assertIn("TRNM_READY_DATABASE=trnm", query)

    def test_docker_probe_rejects_noncanonical_query_output(self) -> None:
        def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            if command[1] == "inspect":
                return subprocess.CompletedProcess(command, 0, "true\n", "")
            return subprocess.CompletedProcess(command, 0, "1\n2\n", "")

        result = MODULE.docker_postgres_probe(
            "trnm-postgres-test",
            "trnm",
            "trnm",
            runner=runner,
        )
        self.assertTrue(result.container_running)
        self.assertFalse(result.sql_ok)


if __name__ == "__main__":
    unittest.main()
