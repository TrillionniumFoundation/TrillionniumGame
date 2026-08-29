from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/build-automation-evidence-candidate.py"
HEAD = "a" * 40
TREE = "b" * 40


class AutomationEvidenceCandidateTests(unittest.TestCase):
    def environment(self) -> dict[str, str]:
        return {
            **os.environ,
            "GITHUB_SHA": HEAD,
            "GITHUB_WORKFLOW": "test-workflow",
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_JOB": "test-job",
            "RUNNER_OS": "Linux",
            "RUNNER_ARCH": "X64",
        }

    def fixture(self, root: Path, assertion: bool = True) -> tuple[Path, Path, Path, Path]:
        candidate = root / "candidate.json"
        result = root / "result.json"
        artifacts = root / "artifacts"
        output = root / "evidence.json"
        artifacts.mkdir()
        candidate.write_text(
            json.dumps(
                {
                    "repository": "TrillionniumFoundation/TrillionniumGame",
                    "commit": HEAD,
                    "tree": TREE,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        result.write_text(
            json.dumps(
                {
                    "assertions": {
                        "required_scenario": assertion,
                        "nested": {"no_duplicate": assertion},
                    },
                    "divergences": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (artifacts / "log.txt").write_text("non-empty log\n", encoding="utf-8")
        (artifacts / "result-copy.json").write_bytes(result.read_bytes())
        return candidate, result, artifacts, output

    def command(self, candidate: Path, result: Path, artifacts: Path, output: Path) -> list[str]:
        return [
            sys.executable,
            str(SCRIPT),
            "--candidate-manifest",
            str(candidate),
            "--result",
            str(result),
            "--artifact-root",
            str(artifacts),
            "--output",
            str(output),
            "--evidence-id",
            "TG-EV-CAND-TEST-AUTOMATION",
            "--type",
            "unit",
            "--gap",
            "GAP-P0-EVIDENCE-001",
            "--claim",
            "C0-source",
            "--command",
            "python3 test",
            "--limitation",
            "synthetic unit fixture only",
            "--review-role",
            "compatibility-qa",
        ]

    def test_successful_automation_stays_unaccepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate, result, artifacts, output = self.fixture(Path(temporary))
            completed = subprocess.run(
                self.command(candidate, result, artifacts, output),
                cwd=ROOT,
                env=self.environment(),
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            value = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(value["candidate"]["commit"], HEAD)
            self.assertEqual(value["result"]["assertions_total"], 2)
            self.assertEqual(value["result"]["assertions_passed"], 2)
            self.assertTrue(value["claims"]["automation_passed"])
            self.assertFalse(value["claims"]["accepted"])
            self.assertFalse(value["claims"]["gap_closed"])
            self.assertFalse(value["claims"]["compatibility_credit"])
            self.assertEqual(value["review_requirement"]["decision"], "pending")

    def test_failed_boolean_assertion_rejects_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate, result, artifacts, output = self.fixture(
                Path(temporary), assertion=False
            )
            completed = subprocess.run(
                self.command(candidate, result, artifacts, output),
                cwd=ROOT,
                env=self.environment(),
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse(output.exists())
            self.assertIn("failed boolean assertion", completed.stderr)

    def test_candidate_sha_must_equal_exact_automation_sha(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate, result, artifacts, output = self.fixture(Path(temporary))
            environment = self.environment()
            environment["GITHUB_SHA"] = "c" * 40
            completed = subprocess.run(
                self.command(candidate, result, artifacts, output),
                cwd=ROOT,
                env=environment,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse(output.exists())
            self.assertIn("does not match exact automation SHA", completed.stderr)


if __name__ == "__main__":
    unittest.main()
