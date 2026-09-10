from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check-required-workflow-runs.py"
SPEC = importlib.util.spec_from_file_location(
    "trnm_required_workflow_status_normalization", SCRIPT
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {SCRIPT}")
GATE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = GATE
SPEC.loader.exec_module(GATE)


def parent(
    *,
    status: str = "completed",
    conclusion: str | None = "success",
) -> object:
    return GATE.Run(
        id=123,
        attempt=1,
        workflow_id=20,
        name="worker",
        path=".github/workflows/worker.yml",
        status=status,
        conclusion=conclusion,
        head_sha="a" * 40,
        event="pull_request",
        url="https://example.test/run/123",
    )


def step(
    name: str,
    *,
    status: str = "completed",
    conclusion: str | None = "success",
) -> dict[str, object]:
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
    }


def stale_job(
    *,
    status: str = "in_progress",
    conclusion: str | None = "success",
    execution_steps: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "name": "exact-api-rtapi-denominator",
        "status": status,
        "conclusion": conclusion,
        "steps": [
            step("Set up job"),
            *(
                execution_steps
                if execution_steps is not None
                else [step("Run exact denominator validation")]
            ),
            step("Complete job"),
        ],
    }


class GitHubJobStatusNormalizationTests(unittest.TestCase):
    def test_terminal_parent_and_complete_success_steps_normalize_stale_status(
        self,
    ) -> None:
        jobs, anomalies = GATE.normalize_github_job_statuses(
            [stale_job()], parent()
        )
        self.assertEqual(jobs[0]["status"], "completed")
        self.assertEqual(jobs[0]["trnm_observed_status"], "in_progress")
        self.assertIs(jobs[0]["trnm_status_normalized"], True)
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(GATE.job_failures(jobs), [])

    def test_nonterminal_parent_never_authorizes_normalization(self) -> None:
        jobs, anomalies = GATE.normalize_github_job_statuses(
            [stale_job()], parent(status="in_progress", conclusion=None)
        )
        self.assertEqual(jobs[0]["status"], "in_progress")
        self.assertEqual(anomalies, ())
        self.assertTrue(GATE.job_failures(jobs))

    def test_failed_parent_never_authorizes_normalization(self) -> None:
        jobs, anomalies = GATE.normalize_github_job_statuses(
            [stale_job()], parent(conclusion="failure")
        )
        self.assertEqual(jobs[0]["status"], "in_progress")
        self.assertEqual(anomalies, ())
        self.assertTrue(GATE.job_failures(jobs))

    def test_pending_execution_step_keeps_job_fail_closed(self) -> None:
        jobs, anomalies = GATE.normalize_github_job_statuses(
            [
                stale_job(
                    execution_steps=[
                        step(
                            "Run exact denominator validation",
                            status="in_progress",
                            conclusion=None,
                        )
                    ]
                )
            ],
            parent(),
        )
        self.assertEqual(jobs[0]["status"], "in_progress")
        self.assertEqual(anomalies, ())
        failures = GATE.job_failures(jobs)
        self.assertTrue(any("not terminal-success" in item for item in failures))

    def test_failed_execution_step_keeps_job_fail_closed(self) -> None:
        jobs, anomalies = GATE.normalize_github_job_statuses(
            [
                stale_job(
                    execution_steps=[
                        step(
                            "Run exact denominator validation",
                            conclusion="failure",
                        )
                    ]
                )
            ],
            parent(),
        )
        self.assertEqual(jobs[0]["status"], "in_progress")
        self.assertEqual(anomalies, ())
        self.assertTrue(GATE.job_failures(jobs))

    def test_success_conclusion_without_effective_execution_is_not_enough(
        self,
    ) -> None:
        jobs, anomalies = GATE.normalize_github_job_statuses(
            [stale_job(execution_steps=[])], parent()
        )
        self.assertEqual(jobs[0]["status"], "in_progress")
        self.assertEqual(anomalies, ())
        self.assertTrue(GATE.job_failures(jobs))

    def test_all_skipped_execution_steps_are_not_enough(self) -> None:
        jobs, anomalies = GATE.normalize_github_job_statuses(
            [
                stale_job(
                    execution_steps=[
                        step("Optional evidence", conclusion="skipped")
                    ]
                )
            ],
            parent(),
        )
        self.assertEqual(jobs[0]["status"], "in_progress")
        self.assertEqual(anomalies, ())
        self.assertTrue(GATE.job_failures(jobs))

    def test_successful_completed_job_is_left_unchanged(self) -> None:
        original = stale_job(status="completed")
        jobs, anomalies = GATE.normalize_github_job_statuses(
            [original], parent()
        )
        self.assertIs(jobs[0], original)
        self.assertEqual(anomalies, ())
        self.assertEqual(GATE.job_failures(jobs), [])

    def test_hardened_main_uses_the_normalizing_api_subclass(self) -> None:
        self.assertIs(GATE._hardened.GitHubApi, GATE.GitHubApi)


if __name__ == "__main__":
    unittest.main()
