#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path


def patch_review_lock() -> None:
    path = Path("tools/denominator/review_lock.py")
    source = path.read_text(encoding="utf-8")
    start = source.index("def _remote(")
    end = source.index("\ndef _leaf_decision(", start)
    replacement = '''def _remote(
    value: Any,
    required: bool,
    policy: Mapping[str, Any],
) -> dict[str, Any] | None:
    if value is None:
        if required:
            raise ReviewError("non-empty exact-head remote evidence is required")
        return None
    if not isinstance(value, dict):
        raise ReviewError("remote_evidence must be an object")
    allowed_kinds = set(
        policy.get(
            "remote_evidence_kinds",
            ["artifact", "immutable-job-log"],
        )
    )
    evidence_kind = value.get("evidence_kind", "artifact")
    if evidence_kind not in allowed_kinds:
        raise ReviewError(
            f"remote_evidence.evidence_kind is not allowed: {evidence_kind!r}"
        )
    head = _git_sha(value.get("head_sha"), "remote_evidence.head_sha")
    pull_request = value.get("pull_request")
    run_id = value.get("workflow_run_id")
    assertions = value.get("assertion_count")
    if not isinstance(pull_request, int) or pull_request <= 0:
        raise ReviewError("remote_evidence.pull_request must be positive")
    if not isinstance(run_id, int) or run_id <= 0:
        raise ReviewError("remote_evidence.workflow_run_id must be positive")
    if value.get("conclusion") != "success":
        raise ReviewError("remote workflow conclusion must be success")
    if not isinstance(assertions, int) or assertions <= 0:
        raise ReviewError("remote_evidence.assertion_count must be positive")
    result: dict[str, Any] = {
        "evidence_kind": evidence_kind,
        "head_sha": head,
        "pull_request": pull_request,
        "workflow_run_id": run_id,
        "assertion_count": assertions,
        "conclusion": "success",
    }
    if evidence_kind == "artifact":
        artifact_id = value.get("artifact_id")
        if not isinstance(artifact_id, int) or artifact_id <= 0:
            raise ReviewError("remote_evidence.artifact_id must be positive")
        result.update(
            artifact_id=artifact_id,
            artifact_sha256=_sha256(
                value.get("artifact_sha256"),
                "remote_evidence.artifact_sha256",
            ),
        )
    else:
        job_id = value.get("job_id")
        if not isinstance(job_id, int) or job_id <= 0:
            raise ReviewError("remote_evidence.job_id must be positive")
        if value.get("log_sealed") is not True:
            raise ReviewError("remote_evidence.log_sealed must be true")
        result.update(
            job_id=job_id,
            job_name=_string(
                value.get("job_name"),
                "remote_evidence.job_name",
            ),
            archive_sha256=_sha256(
                value.get("archive_sha256"),
                "remote_evidence.archive_sha256",
            ),
            log_sealed=True,
            seal_kind="deterministic-archive-digest-in-job-log",
        )
    return result
'''
    source = source[:start] + replacement + source[end:]
    old = '_remote(review.get("remote_evidence"), require_remote_evidence)'
    new = '_remote(review.get("remote_evidence"), require_remote_evidence, policy)'
    if source.count(old) != 1:
        raise SystemExit("remote evidence call anchor drift")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


def patch_policy() -> None:
    path = Path("config/denominator-review-policy.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    value["remote_evidence_kinds"] = ["artifact", "immutable-job-log"]
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def patch_schema() -> None:
    path = Path("contracts/denominator/review-bundle.v1.schema.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    sha = {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}
    common = {
        "head_sha": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
        "pull_request": {"type": "integer", "minimum": 1},
        "workflow_run_id": {"type": "integer", "minimum": 1},
        "assertion_count": {"type": "integer", "minimum": 1},
        "conclusion": {"const": "success"},
    }
    value["properties"]["remote_evidence"] = {
        "oneOf": [
            {"type": "null"},
            {
                "type": "object",
                "required": [
                    "evidence_kind",
                    "head_sha",
                    "pull_request",
                    "workflow_run_id",
                    "assertion_count",
                    "conclusion",
                    "artifact_id",
                    "artifact_sha256",
                ],
                "properties": {
                    "evidence_kind": {"const": "artifact"},
                    **common,
                    "artifact_id": {"type": "integer", "minimum": 1},
                    "artifact_sha256": sha,
                },
                "additionalProperties": True,
            },
            {
                "type": "object",
                "required": [
                    "evidence_kind",
                    "head_sha",
                    "pull_request",
                    "workflow_run_id",
                    "assertion_count",
                    "conclusion",
                    "job_id",
                    "job_name",
                    "archive_sha256",
                    "log_sealed",
                ],
                "properties": {
                    "evidence_kind": {"const": "immutable-job-log"},
                    **common,
                    "job_id": {"type": "integer", "minimum": 1},
                    "job_name": {"type": "string", "minLength": 1},
                    "archive_sha256": sha,
                    "log_sealed": {"const": True},
                },
                "additionalProperties": True,
            },
        ]
    }
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def patch_tests() -> None:
    path = Path("tests/denominator/test_review_lock.py")
    source = path.read_text(encoding="utf-8")
    marker = "    def test_self_approval_is_rejected(self):\n"
    addition = '''    def test_immutable_job_log_exact_head_evidence_is_accepted(self):
        raw = candidate()
        bundle = review(raw)
        bundle["remote_evidence"] = {
            "evidence_kind": "immutable-job-log",
            "head_sha": "1" * 40,
            "pull_request": 123,
            "workflow_run_id": 44,
            "job_id": 66,
            "job_name": "exact-denominator-candidate",
            "conclusion": "success",
            "archive_sha256": "sha256:" + "d" * 64,
            "assertion_count": 9,
            "log_sealed": True,
        }
        result = self.run_review(raw, bundle, require_remote_evidence=True)
        self.assertEqual(result.lock["status"], "reviewed-locked")
        self.assertTrue(result.can_write_reviewed_lock)
        remote = result.lock["review"]["remote_evidence"]
        self.assertEqual(remote["evidence_kind"], "immutable-job-log")
        self.assertEqual(
            remote["seal_kind"],
            "deterministic-archive-digest-in-job-log",
        )

        for field, invalid_value in (("job_id", 0), ("log_sealed", False)):
            invalid = review(raw)
            invalid["remote_evidence"] = dict(bundle["remote_evidence"])
            invalid["remote_evidence"][field] = invalid_value
            with self.assertRaises(ReviewError):
                self.run_review(raw, invalid, require_remote_evidence=True)

'''
    if source.count(marker) != 1:
        raise SystemExit("review-lock test insertion anchor drift")
    path.write_text(source.replace(marker, addition + marker, 1), encoding="utf-8")


def patch_docs() -> None:
    path = Path("docs/development/DENOMINATOR_REVIEW_AND_LOCK.md")
    source = path.read_text(encoding="utf-8")
    heading = "## Exact-head remote evidence carriers"
    if heading in source:
        return
    section = '''

## Exact-head remote evidence carriers

A reviewed lock may bind either of two fail-closed remote carriers:

1. `artifact`: a positive GitHub Actions artifact ID plus an exact SHA-256;
2. `immutable-job-log`: a positive workflow run/job identity whose successful log
   contains the deterministic evidence-archive SHA-256, with `log_sealed=true`.

The job-log carrier exists for repositories that deliberately avoid external upload
Actions. It does not weaken reviewer independence, candidate-head equality,
non-empty assertion counts, or successful terminal execution. Missing or older
heads, zero IDs, non-success conclusions, absent digests and unsealed logs are
rejected. Neither carrier grants SG1 by itself; two independent reviewers and the
global SG1 gate review remain mandatory.
'''
    path.write_text(source.rstrip() + section + "\n", encoding="utf-8")


def main() -> None:
    patch_review_lock()
    patch_policy()
    patch_schema()
    patch_tests()
    patch_docs()


if __name__ == "__main__":
    main()
