from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check-required-workflow-runs.py"
SPEC = importlib.util.spec_from_file_location(
    "trnm_required_workflow_runs_hardened", SCRIPT
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {SCRIPT}")
GATE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = GATE
SPEC.loader.exec_module(GATE)


def requirement() -> object:
    return GATE.Requirement(
        workflow_id=20,
        name="worker",
        path=".github/workflows/worker.yml",
        git_blob_sha1="a" * 40,
        allowed_events=("pull_request",),
        minimum_successful_execution_jobs=1,
    )


def step(
    name: str,
    *,
    status: str = "completed",
    conclusion: str = "success",
) -> dict[str, object]:
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
    }


def job(steps: list[dict[str, object]]) -> dict[str, object]:
    return {
        "name": "verify",
        "status": "completed",
        "conclusion": "success",
        "steps": steps,
    }


class WorkflowMetadataTests(unittest.TestCase):
    def test_disabled_workflow_is_rejected(self) -> None:
        failures = GATE.workflow_metadata_failures(
            requirement(),
            {
                "id": 20,
                "name": "worker",
                "path": ".github/workflows/worker.yml",
                "state": "disabled_manually",
            },
        )
        self.assertTrue(any("not active" in item for item in failures))

    def test_metadata_identity_drift_is_rejected(self) -> None:
        failures = GATE.workflow_metadata_failures(
            requirement(),
            {
                "id": 21,
                "name": "replacement",
                "path": ".github/workflows/replacement.yml",
                "state": "active",
            },
        )
        self.assertTrue(any("ID mismatch" in item for item in failures))
        self.assertTrue(any("name mismatch" in item for item in failures))
        self.assertTrue(any("path mismatch" in item for item in failures))

    def test_active_exact_metadata_is_accepted(self) -> None:
        self.assertEqual(
            GATE.workflow_metadata_failures(
                requirement(),
                {
                    "id": 20,
                    "name": "worker",
                    "path": ".github/workflows/worker.yml",
                    "state": "active",
                },
            ),
            [],
        )


class ExactAttemptJobTests(unittest.TestCase):
    def test_masked_failed_step_is_rejected(self) -> None:
        failures = GATE.job_failures(
            [
                job(
                    [
                        step("Set up job"),
                        step(
                            "Required invariant",
                            conclusion="failure",
                        ),
                        step("Fallback echo"),
                        step("Complete job"),
                    ]
                )
            ]
        )
        self.assertTrue(
            any("Required invariant" in item for item in failures)
        )
        self.assertTrue(any("too few successful" in item for item in failures))

    def test_skipped_optional_step_does_not_mask_real_success(self) -> None:
        failures = GATE.job_failures(
            [
                job(
                    [
                        step("Set up job"),
                        step(
                            "Optional upload",
                            conclusion="skipped",
                        ),
                        step("Run invariant tests"),
                        step("Complete job"),
                    ]
                )
            ]
        )
        self.assertEqual(failures, [])

    def test_runner_preparation_is_not_effective_execution(self) -> None:
        failures = GATE.job_failures(
            [
                job(
                    [
                        step("Set up job"),
                        step("Prepare all required actions"),
                        step("Initialize containers"),
                        step("Complete job"),
                    ]
                )
            ]
        )
        self.assertTrue(
            any("zero non-framework" in item for item in failures)
        )

    def test_jobs_endpoint_is_bound_to_exact_attempt(self) -> None:
        class FakeApi(GATE.GitHubApi):
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, dict[str, object]]] = []

            def paged(
                self,
                url: str,
                key: str,
                query: dict[str, object],
            ) -> list[dict[str, object]]:
                self.calls.append((url, key, query))
                return []

        api = FakeApi()
        self.assertEqual(
            api.jobs_attempt("owner/repository", 123, 4),
            [],
        )
        self.assertEqual(len(api.calls), 1)
        url, key, query = api.calls[0]
        self.assertTrue(url.endswith("/actions/runs/123/attempts/4/jobs"))
        self.assertEqual(key, "jobs")
        self.assertEqual(query, {})


class AggregateTupleTests(unittest.TestCase):
    def test_current_aggregate_tuple_must_match_manifest(self) -> None:
        aggregate = GATE.Requirement(
            workflow_id=99,
            name="aggregate",
            path=".github/workflows/aggregate.yml",
            git_blob_sha1="b" * 40,
            allowed_events=("pull_request",),
            minimum_successful_execution_jobs=1,
        )
        manifest = GATE.Manifest(
            repository="owner/repository",
            event="pull_request",
            aggregate=aggregate,
            reject_unlisted=True,
            workflows=(requirement(),),
        )
        current = GATE.Run(
            id=10,
            attempt=1,
            workflow_id=99,
            name="aggregate",
            path=".github/workflows/aggregate.yml",
            status="in_progress",
            conclusion=None,
            head_sha="c" * 40,
            event="pull_request",
            url="https://example.test/run/10",
        )
        self.assertEqual(
            GATE.current_run_failures(current, manifest, "c" * 40),
            [],
        )
        moved = GATE.Run(
            id=current.id,
            attempt=current.attempt,
            workflow_id=current.workflow_id,
            name=current.name,
            path=current.path,
            status=current.status,
            conclusion=current.conclusion,
            head_sha="d" * 40,
            event=current.event,
            url=current.url,
        )
        self.assertTrue(
            any(
                "head SHA" in item
                for item in GATE.current_run_failures(
                    moved, manifest, "c" * 40
                )
            )
        )


if __name__ == "__main__":
    unittest.main()
