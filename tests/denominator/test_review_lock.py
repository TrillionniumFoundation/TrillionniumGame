from __future__ import annotations

import copy
import hashlib
import json
import unittest
from datetime import date, timedelta

from tools.denominator.review_lock import (
    ReviewError,
    aggregate_reviewed_locks,
    canonical_bytes,
    review_candidate,
    sha256_bytes,
)


def policy() -> dict:
    return {
        "required_denominators": ["DEN-API"],
        "classifications": ["mandatory", "optional-profile", "versioned-exclusion"],
        "owner_roles": ["protocol", "security", "governance"],
        "minimum_reviewers": 2,
        "minimum_exclusion_reviewers": 2,
        "evidence_roots": ["docs/evidence"],
        "adr_roots": ["docs/adr"],
    }


def candidate(manual: list[dict] | None = None) -> bytes:
    value = {
        "denominator": "DEN-API",
        "leaves": [
            {"id": "TG-D1-A", "signature_hash": "sha256:" + "a" * 64, "symbol": "A"},
            {"id": "TG-D1-B", "signature_hash": "sha256:" + "b" * 64, "symbol": "B"},
        ],
        "manual_contracts": manual or [],
    }
    return canonical_bytes(value) + b"\n"


def review(candidate_bytes: bytes, manual: list[dict] | None = None) -> dict:
    decisions = []
    for leaf_id, digest in (("TG-D1-A", "a"), ("TG-D1-B", "b")):
        decisions.append(
            {
                "leaf_id": leaf_id,
                "signature_hash": "sha256:" + digest * 64,
                "classification": "mandatory",
                "owner_role": "protocol",
                "task_id": "TG-W2-001",
                "test_id": "TG-DIFF-001",
                "gate_id": "GATE-PROTOCOL",
                "evidence_path": f"docs/evidence/{leaf_id}.json",
                "reviewer_ids": ["reviewer-a"],
            }
        )
    return {
        "schema": "trillionnium.denominator-review.v1",
        "denominator": "DEN-API",
        "candidate_sha256": sha256_bytes(candidate_bytes),
        "candidate_head": "1" * 40,
        "author_identity": "author",
        "self_approval": False,
        "reviewers": [
            {"identity": "reviewer-a", "role": "protocol"},
            {"identity": "reviewer-b", "role": "security"},
        ],
        "leaf_decisions": decisions,
        "manual_contracts": manual or [],
        "remote_evidence": None,
    }


BACKLOG = {"tasks": [{"task_id": "TG-W2-001"}]}
GATES = {"gates": [{"gate_id": "GATE-PROTOCOL"}]}


class ReviewLockTests(unittest.TestCase):
    def run_review(self, raw: bytes, bundle: dict, **kwargs):
        return review_candidate(
            candidate_bytes=raw,
            review=bundle,
            policy=policy(),
            backlog=BACKLOG,
            gates=GATES,
            **kwargs,
        )

    def test_review_ready_without_remote_does_not_lock(self):
        raw = candidate()
        result = self.run_review(raw, review(raw))
        self.assertEqual(result.lock["status"], "reviewed-ready")
        self.assertFalse(result.can_write_reviewed_lock)
        self.assertFalse(result.lock["claims"]["sg1_complete"])

    def test_remote_exact_head_evidence_is_required_for_lock(self):
        raw = candidate()
        bundle = review(raw)
        bundle["remote_evidence"] = {
            "head_sha": "1" * 40,
            "pull_request": 123,
            "workflow_run_id": 44,
            "conclusion": "success",
            "artifact_id": 55,
            "artifact_sha256": "sha256:" + "c" * 64,
            "assertion_count": 9,
        }
        result = self.run_review(raw, bundle, require_remote_evidence=True)
        self.assertEqual(result.lock["status"], "reviewed-locked")
        self.assertTrue(result.can_write_reviewed_lock)

    def test_self_approval_is_rejected(self):
        raw = candidate()
        bundle = review(raw)
        bundle["reviewers"][0]["identity"] = "author"
        with self.assertRaises(ReviewError):
            self.run_review(raw, bundle)

    def test_exact_leaf_coverage_rejects_removal(self):
        raw = candidate()
        bundle = review(raw)
        bundle["leaf_decisions"].pop()
        with self.assertRaises(ReviewError):
            self.run_review(raw, bundle)

    def test_signature_and_candidate_digest_tamper_are_rejected(self):
        raw = candidate()
        bundle = review(raw)
        bundle["leaf_decisions"][0]["signature_hash"] = "sha256:" + "f" * 64
        with self.assertRaises(ReviewError):
            self.run_review(raw, bundle)
        bundle = review(raw)
        bundle["candidate_sha256"] = "sha256:" + "0" * 64
        with self.assertRaises(ReviewError):
            self.run_review(raw, bundle)

    def test_exclusion_requires_two_reviewers_adr_and_future_expiry(self):
        raw = candidate()
        bundle = review(raw)
        decision = bundle["leaf_decisions"][0]
        decision.update(
            classification="versioned-exclusion",
            reviewer_ids=["reviewer-a", "reviewer-b"],
            adr_ref="docs/adr/ADR-99.md",
            expiry=(date.today() + timedelta(days=30)).isoformat(),
        )
        self.assertEqual(
            self.run_review(raw, bundle).lock["leaves"][0]["review"]["classification"],
            "versioned-exclusion",
        )
        decision["reviewer_ids"] = ["reviewer-a"]
        with self.assertRaises(ReviewError):
            self.run_review(raw, bundle)

    def test_manual_blocker_keeps_denominator_blocked(self):
        manual = [{"class": "unsupported", "symbol": "X"}]
        raw = candidate(manual)
        identity = sha256_bytes(canonical_bytes(manual[0]))
        bundle = review(raw, [{
            "identity": identity,
            "disposition": "owned-blocker",
            "owner_role": "governance",
            "issue_url": "https://github.com/org/repo/issues/7",
            "gate_ids": ["GATE-PROTOCOL"],
            "reviewer_ids": ["reviewer-a", "reviewer-b"],
        }])
        result = self.run_review(raw, bundle)
        self.assertEqual(result.lock["status"], "reviewed-blocked")
        self.assertEqual(result.lock["manual_blocker_count"], 1)

    def test_denominator_decrease_requires_adr_and_upstream_delta(self):
        raw = candidate()
        previous = {"leaves": [{"id": "TG-D1-A"}, {"id": "TG-D1-B"}, {"id": "TG-D1-C"}]}
        bundle = review(raw)
        with self.assertRaises(ReviewError):
            self.run_review(raw, bundle, previous_lock=previous)
        bundle["denominator_decrease"] = {
            "removed_leaf_ids": ["TG-D1-C"],
            "adr_ref": "docs/adr/ADR-100.md",
            "upstream_delta_sha256": "sha256:" + "d" * 64,
        }
        self.assertEqual(
            self.run_review(raw, bundle, previous_lock=previous).lock["leaf_count"], 2
        )

    def test_aggregate_requires_exact_reviewed_lock_set_and_never_closes_sg1(self):
        raw = candidate()
        bundle = review(raw)
        bundle["remote_evidence"] = {
            "head_sha": "1" * 40,
            "pull_request": 123,
            "workflow_run_id": 44,
            "conclusion": "success",
            "artifact_id": 55,
            "artifact_sha256": "sha256:" + "c" * 64,
            "assertion_count": 9,
        }
        lock = self.run_review(raw, bundle, require_remote_evidence=True).lock
        aggregate = aggregate_reviewed_locks([lock], policy())
        self.assertEqual(aggregate["status"], "sg1-independent-gate-review-required")
        self.assertTrue(aggregate["claims"]["all_denominators_reviewed_locked"])
        self.assertFalse(aggregate["claims"]["sg1_complete"])

    def test_output_is_deterministic(self):
        raw = candidate()
        bundle = review(raw)
        first = self.run_review(raw, copy.deepcopy(bundle)).lock
        second = self.run_review(raw, copy.deepcopy(bundle)).lock
        self.assertEqual(canonical_bytes(first), canonical_bytes(second))


if __name__ == "__main__":
    unittest.main()
