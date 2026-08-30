from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.denominator.review_request import build_review_package

ROOT = Path(__file__).resolve().parents[2]
HEAD = "1" * 40


class ReviewRequestTests(unittest.TestCase):
    def test_all_required_candidates_receive_conservative_review_templates(self):
        policy = json.loads((ROOT / "config/denominator-review-policy.json").read_text())
        routing = json.loads((ROOT / "config/denominator-review-routing.json").read_text())
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            candidates = []
            remotes = {}
            for index, denominator in enumerate(policy["required_denominators"], start=1):
                candidate = {
                    "denominator": denominator,
                    "leaves": [
                        {
                            "id": f"TG-TEST-{index}",
                            "signature_hash": "sha256:" + f"{index:064x}"[-64:],
                            "task_ids": ["TG-W0-002"],
                            "test_ids": [f"TG-DIFF-TEST-{index}"],
                        }
                    ],
                    "manual_contracts": (
                        [{"class": "restricted", "symbol": "x"}]
                        if denominator == "DEN-CONSOLE"
                        else []
                    ),
                }
                path = base / routing["routes"][denominator]["candidate_filename"]
                path.write_text(json.dumps(candidate, sort_keys=True) + "\n")
                candidates.append(path)
                remotes[denominator] = {
                    "evidence_kind": "immutable-job-log",
                    "head_sha": HEAD,
                    "pull_request": 42,
                    "workflow_run_id": 100,
                    "job_id": 200,
                    "job_name": "generate-review-package",
                    "conclusion": "success",
                    "archive_sha256": "sha256:" + "a" * 64,
                    "assertion_count": 2,
                    "log_sealed": True,
                }
            remote_path = base / "remote.json"
            remote_path.write_text(
                json.dumps({"candidate_head": HEAD, "denominators": remotes}) + "\n"
            )
            output = base / "output"
            worklist = build_review_package(
                candidate_paths=candidates,
                head_sha=HEAD,
                remote_index_path=remote_path,
                output_dir=output,
                policy_path=ROOT / "config/denominator-review-policy.json",
                routing_path=ROOT / "config/denominator-review-routing.json",
            )
            self.assertEqual(worklist["candidate_count"], 14)
            self.assertEqual(worklist["total_leaf_count"], 14)
            self.assertEqual(worklist["manual_blocker_count"], 1)
            self.assertFalse(worklist["claims"]["sg1_complete"])
            for row in worklist["denominators"]:
                request = json.loads(Path(row["review_request_path"]).read_text())
                template = request["review_bundle_template"]
                self.assertEqual(template["reviewers"], [])
                self.assertTrue(
                    all(
                        decision["classification"] == "mandatory"
                        and decision["reviewer_ids"] == []
                        and decision["proposal_only"] is True
                        for decision in template["leaf_decisions"]
                    )
                )


if __name__ == "__main__":
    unittest.main()
