#!/usr/bin/env python3
"""Keep exact source/merge/live evidence on the first-party artifact path."""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github/actions/upload-evidence/action.yml"
ACTION_JS = ROOT / ".github/actions/upload-evidence/index.js"
WORKFLOWS = {
    "candidate": ROOT / ".github/workflows/candidate-identity-manifest.yml",
    "merge_gate": ROOT / ".github/workflows/trillionnium-game-merge-gate.yml",
    "prospective": ROOT / ".github/workflows/prospective-merge-gate.yml",
    "server_live": ROOT / ".github/workflows/trnm-server-live.yml",
    "response_loss": ROOT / ".github/workflows/pg-server-response-loss.yml",
}


class EvidenceRetentionWorkflowTests(unittest.TestCase):
    def test_local_action_uses_job_scoped_credentials_and_fixed_uploader(self) -> None:
        action = ACTION.read_text(encoding="utf-8")
        script = ACTION_JS.read_text(encoding="utf-8")
        self.assertIn("using: node24", action)
        self.assertIn("main: index.js", action)
        for name in ("artifact_id", "sha256", "size_bytes"):
            self.assertIn(f"  {name}:\n", action)
        for name in ("ACTIONS_RUNTIME_TOKEN", "ACTIONS_RESULTS_URL"):
            self.assertIn(name, script)
        self.assertIn("scripts/upload-actions-artifact.py", script)
        self.assertIn("delete env.GITHUB_TOKEN", script)
        self.assertIn("delete env.GH_TOKEN", script)
        self.assertIn("shell: false", script)
        self.assertNotIn("exec(", script)

    def test_required_workflows_retain_nonempty_exact_packets(self) -> None:
        for label, path in WORKFLOWS.items():
            with self.subTest(workflow=label):
                source = path.read_text(encoding="utf-8")
                self.assertIn("uses: ./.github/actions/upload-evidence", source)
                self.assertIn("outputs.artifact_id", source)
                self.assertIn("outputs.sha256", source)
                self.assertIn("outputs.size_bytes", source)
                self.assertNotIn("no artifact credit", source.lower())

    def test_retained_packets_remain_fail_closed(self) -> None:
        candidate = WORKFLOWS["candidate"].read_text(encoding="utf-8")
        merge_gate = WORKFLOWS["merge_gate"].read_text(encoding="utf-8")
        prospective = WORKFLOWS["prospective"].read_text(encoding="utf-8")
        server = WORKFLOWS["server_live"].read_text(encoding="utf-8")
        response = WORKFLOWS["response_loss"].read_text(encoding="utf-8")
        self.assertIn(".claim_boundary.independently_reviewed == false", candidate)
        self.assertIn("gap closure remain false", candidate)
        for source in (merge_gate, prospective):
            self.assertIn("accepted_evidence:false", source)
            self.assertIn("gap_closed:false", source)
            self.assertIn("production_ready:false", source)
        self.assertIn("summary['production_ready'] is False", server)
        self.assertIn(".claims.production_ready == false", response)
        self.assertIn(".claims.sg4_complete == false", response)

    def test_candidate_receipt_printf_continues_to_arguments(self) -> None:
        source = WORKFLOWS["candidate"].read_text(encoding="utf-8")
        receipt_lines = [
            line
            for line in source.splitlines()
            if "printf " in line and "artifact_id=%s" in line
        ]
        self.assertEqual(len(receipt_lines), 1)
        self.assertTrue(receipt_lines[0].endswith("\\"))

    def test_source_collection_binds_runs_jobs_attempts_and_definition_blobs(self) -> None:
        source = WORKFLOWS["merge_gate"].read_text(encoding="utf-8")
        self.assertIn("required-workflow-retained-collection.v1", source)
        self.assertIn("definition_blob_sha1", source)
        self.assertIn(".run_attempt > 0", source)
        self.assertIn(".jobs > 0", source)
        self.assertIn("--stable-polls 3", source)

    def test_prospective_and_live_packets_are_profile_separated(self) -> None:
        prospective = WORKFLOWS["prospective"].read_text(encoding="utf-8")
        server = WORKFLOWS["server_live"].read_text(encoding="utf-8")
        self.assertIn("prospective-merge-${{ matrix.profile }}", prospective)
        self.assertIn("prospective-merge-retained-gate.v1", prospective)
        self.assertIn("trnm-server-live-${{ matrix.profile }}", server)
        self.assertIn("matrix:\n        profile: [postgresql, cockroachdb]", server)


if __name__ == "__main__":
    unittest.main()
