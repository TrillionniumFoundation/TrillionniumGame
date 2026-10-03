from __future__ import annotations

import importlib.util
import contextlib
import io
import json
from unittest import mock
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


class AbsoluteCollectionDeadlineTests(unittest.TestCase):
    def run_gate(self, late=None):
        # Actual 52+3 definition inventory; API, Git source custody, and clock
        # are pure protocols here. These mocks grant no external qualification.
        with contextlib.chdir(ROOT):
            manifest=GATE.load_composed_manifest(Path('docs/governance/REQUIRED_WORKFLOWS_V1.json'))
        self.assertEqual(len(manifest.workflows),55)
        head='c'*40
        current=GATE.Run(9000,1,manifest.aggregate.workflow_id,manifest.aggregate.name,
                         manifest.aggregate.path,'in_progress',None,head,manifest.event,'https://example.test/9000')
        raw=[{'id':10000+i,'run_attempt':1,'workflow_id':q.workflow_id,'name':q.name,'path':q.path,
              'status':'completed','conclusion':'success','head_sha':head,'event':manifest.event,
              'html_url':f'https://example.test/{10000+i}'} for i,q in enumerate(manifest.workflows)]
        by_run={10000+i:q for i,q in enumerate(manifest.workflows)}
        class Clock:
            now=0.0
            def monotonic(self):return self.now
            def sleep(self,seconds):self.now+=seconds
        clock=Clock();calls={'current':0,'runs':0,'jobs':0,'runs_started_after_deadline':0}
        class Api:
            def __init__(self,token):pass
            def current_run(self,*args):
                calls['current']+=1
                if late=='initial_api':clock.now=2.0
                return current
            def workflow(self,repo,wid):
                q=manifest.aggregate if wid==manifest.aggregate.workflow_id else next(q for q in manifest.workflows if q.workflow_id==wid)
                return {'id':wid,'name':q.name,'path':q.path,'state':'active'}
            def runs(self,*args):
                calls['runs']+=1
                if clock.now >= 1.0:calls['runs_started_after_deadline']+=1
                if late=='final_api' and calls['runs']==2:clock.now=2.0
                return raw
            def jobs_attempt(self,repo,rid,attempt):
                calls['jobs']+=1;q=by_run[rid]
                if late=='third_verification' and calls['jobs']==165:clock.now=2.0
                return [job([step('pure protocol fixture')]) for _ in range(q.minimum_successful_execution_jobs)]
        def fields(*args):
            if late=='source_custody':clock.now=2.0
            return {'pure_protocol_only':True}
        real_dumps=json.dumps
        def serialize(*args,**kwargs):
            result=real_dumps(*args,**kwargs)
            if late=='serialization':clock.now=2.0
            return result
        class Output(io.StringIO):
            def write(self,value):
                n=super().write(value)
                if (late=='header_delivery' and value.startswith('required workflow gate: OK')) or (late=='receipt_delivery' and value.startswith('[')):
                    clock.now=2.0
                return n
        output=Output();errors=io.StringIO();core=GATE._hardened
        real_job_failures=core.job_failures
        def validate_jobs(*args,**kwargs):
            result=real_job_failures(*args,**kwargs)
            if late=='last_job_validation' and calls['jobs']==55:clock.now=2.0
            return result
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(GATE,'load_composed_manifest',return_value=manifest))
            stack.enter_context(mock.patch.object(core,'verify_files',return_value=[]))
            stack.enter_context(mock.patch.object(core,'source_selection_fields',side_effect=fields))
            stack.enter_context(mock.patch.object(core,'GitHubApi',Api))
            stack.enter_context(mock.patch.object(core,'job_failures',side_effect=validate_jobs))
            stack.enter_context(mock.patch.object(core.time,'monotonic',side_effect=clock.monotonic))
            stack.enter_context(mock.patch.object(core.time,'sleep',side_effect=clock.sleep))
            stack.enter_context(mock.patch.object(core.json,'dumps',side_effect=serialize))
            stack.enter_context(contextlib.redirect_stdout(output));stack.enter_context(contextlib.redirect_stderr(errors))
            code=GATE.main(['--repository',manifest.repository,'--head-sha',head,'--current-run-id','9000',
                            '--manifest',str(ROOT/'docs/governance/REQUIRED_WORKFLOWS_V1.json'),
                            '--timeout-seconds','1','--poll-seconds','0.01','--stable-polls','3'])
        return code,output.getvalue(),errors.getvalue(),calls,clock.now

    def test_actual55_protocol_completes_three_stable_polls_before_deadline(self):
        code,out,err,calls,elapsed=self.run_gate()
        self.assertEqual(code,0);self.assertEqual(err,'');self.assertLess(elapsed,1)
        self.assertEqual(calls['jobs'],165);self.assertEqual(calls['runs'],6)
        self.assertIn('OK (55/55',out)
        self.assertEqual(len(json.loads(out.splitlines()[-1])),55)

    def test_expired_source_custody_cannot_start_api_or_renew_deadline(self):
        code,out,err,calls,elapsed=self.run_gate('source_custody')
        self.assertEqual(code,1);self.assertEqual(calls['current'],0);self.assertEqual(out,'')
        self.assertIn('absolute collection deadline',err);self.assertEqual(elapsed,2)

    def test_initial_and_final_api_and_third_stable_verification_cannot_return_late_success(self):
        for site in ('initial_api','final_api','third_verification'):
            with self.subTest(site=site):
                code,out,err,_,elapsed=self.run_gate(site)
                self.assertEqual(code,1);self.assertNotIn('gate: OK',out)
                self.assertIn('absolute collection deadline',err);self.assertEqual(elapsed,2)

    def test_expired_last_job_validation_cannot_start_final_request(self):
        code,out,err,calls,elapsed=self.run_gate('last_job_validation')
        self.assertEqual(code,1);self.assertEqual(out,'');self.assertEqual(elapsed,2)
        self.assertEqual(calls['jobs'],55);self.assertEqual(calls['runs'],1)
        self.assertEqual(calls['runs_started_after_deadline'],0)
        self.assertIn('absolute collection deadline',err)

    def test_late_serialization_is_rejected_before_success_output(self):
        code,out,err,_,_=self.run_gate('serialization')
        self.assertEqual(code,1);self.assertEqual(out,'');self.assertIn('absolute collection deadline',err)

    def test_late_header_or_receipt_delivery_keeps_failure_exit(self):
        for site in ('header_delivery','receipt_delivery'):
            with self.subTest(site=site):
                code,out,err,_,_=self.run_gate(site)
                self.assertEqual(code,1);self.assertIn('gate: OK',out)
                self.assertIn('absolute collection deadline',err)
                self.assertEqual('[' in out,site=='receipt_delivery')


if __name__ == "__main__":
    unittest.main()
