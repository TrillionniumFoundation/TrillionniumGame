"""A successful unit job cannot replace a required live execution job.

These deterministic cases exercise the gate's declared minimum successful-job
count. Identity, workflow-definition and assertion evidence are separate gates.
"""
from __future__ import annotations

import unittest

from tests.control_plane.test_workflow_gate_integrity import GATE


def job(name: str, conclusion: str = "success") -> dict:
    return {
        "name": name,
        "status": "completed",
        "conclusion": conclusion,
        "steps": [
            {"name": "Set up job", "status": "completed", "conclusion": "success"},
            {"name": "Execute assertions", "status": "completed", "conclusion": conclusion},
            {"name": "Complete job", "status": "completed", "conclusion": "success"},
        ],
    }


class RequiredJobMinimumTests(unittest.TestCase):
    def test_unit_and_live_both_success_satisfy_two(self):
        self.assertEqual(GATE.job_failures([job("unit"), job("live")], 2), [])

    def test_unit_success_and_live_skipped_do_not_satisfy_two(self):
        failures = GATE.job_failures([job("unit"), job("live", "skipped")], 2)
        self.assertTrue(any("observed=1 required>=2" in value for value in failures))

    def test_missing_live_job_does_not_satisfy_two(self):
        failures = GATE.job_failures([job("unit")], 2)
        self.assertTrue(any("observed=1 required>=2" in value for value in failures))

    def test_masked_live_step_failure_has_no_quorum_credit(self):
        live = job("live")
        live["steps"][1]["conclusion"] = "failure"
        failures = GATE.job_failures([job("unit"), live], 2)
        self.assertTrue(any("not terminal-success" in value for value in failures))
        self.assertTrue(any("observed=1 required>=2" in value for value in failures))

    def test_framework_only_live_job_has_no_quorum_credit(self):
        live = job("live")
        del live["steps"][1]
        failures = GATE.job_failures([job("unit"), live], 2)
        self.assertTrue(any("zero non-framework" in value for value in failures))
        self.assertTrue(any("observed=1 required>=2" in value for value in failures))

    def test_all_skipped_live_steps_have_no_quorum_credit(self):
        live = job("live")
        live["steps"][1]["conclusion"] = "skipped"
        failures = GATE.job_failures([job("unit"), live], 2)
        self.assertTrue(any("no successful non-framework" in value for value in failures))
        self.assertTrue(any("observed=1 required>=2" in value for value in failures))

    def test_quorum_does_not_mask_an_additional_failed_job(self):
        failures = GATE.job_failures([job("unit"), job("live"), job("other", "failure")], 2)
        self.assertTrue(any("other" in value and "not terminal-success" in value for value in failures))

    def test_two_successes_cannot_satisfy_three_required_jobs(self):
        failures = GATE.job_failures([job("unit"), job("live")], 3)
        self.assertTrue(any("observed=2 required>=3" in value for value in failures))


if __name__ == "__main__":
    unittest.main()
