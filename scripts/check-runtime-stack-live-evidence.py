#!/usr/bin/env python3
"""Validate trusted live Runtime stack evidence against the current candidate.

Evidence can remain cryptographically and review-valid for the exact historical
candidate it targets without being applicable to the current moving candidate.
This checker therefore separates two questions:

1. Is an evidence item internally trustworthy for its recorded target?
2. Does that target exactly match the current candidate and therefore earn
   current gate credit?

A historical trusted item is retained but never counted for the current head.
A successful current-head live run still fails closed until a matching trusted
item is indexed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CURRENT_STATE_PATH = ROOT / "docs/status/CURRENT_STATE.json"
EVIDENCE_INDEX_PATH = ROOT / "docs/evidence/index.json"
RUN_INVENTORY_PATH = ROOT / "docs/evidence/ACTIONS_RUN_INVENTORY.json"
TARGET_WORKFLOW = "trnm-server-live"
ZERO_SHA = "0" * 40


class EvidenceError(RuntimeError):
    """Raised when trusted live evidence violates the exact-target contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceError(message)


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceError(f"cannot load {path.relative_to(ROOT)}: {error}") from error
    require(isinstance(value, dict), f"{path.relative_to(ROOT)} must contain an object")
    return value


def sha(value: Any, label: str) -> str:
    require(
        isinstance(value, str)
        and len(value) == 40
        and value != ZERO_SHA
        and all(character in "0123456789abcdef" for character in value),
        f"{label} must be a non-zero lowercase commit/tree SHA",
    )
    return value


def expected_candidate(current_state: dict[str, Any]) -> tuple[str, str, str, str]:
    repository = current_state.get("repository", {}).get("full_name")
    require(isinstance(repository, str) and repository.count("/") == 1, "invalid repository")
    exact = current_state.get("github_state", {}).get("current_exact_candidate", {})
    require(isinstance(exact, dict), "current_exact_candidate must be an object")
    policy = exact.get("target_policy")
    require(policy in {"floating", "target-locked"}, "invalid target_policy")
    branch = exact.get("branch")
    require(isinstance(branch, str) and branch, "current candidate branch is missing")
    if policy == "target-locked":
        head = sha(exact.get("actual_head"), "current candidate actual_head")
        tree = sha(exact.get("actual_tree"), "current candidate actual_tree")
        require(exact.get("observed") is True, "target-locked candidate must be observed")
    else:
        head = sha(exact.get("head"), "current candidate head")
        tree = sha(exact.get("tree"), "current candidate tree")
    return repository, branch, head, tree


def item_targets_runtime_live(item: dict[str, Any]) -> bool:
    if item.get("evidence_type") not in {"unit", "manifest"}:
        return False
    limitations = item.get("limitations", {})
    profile = limitations.get("profile") if isinstance(limitations, dict) else None
    workflow = limitations.get("workflow") if isinstance(limitations, dict) else None
    return profile == TARGET_WORKFLOW or workflow == TARGET_WORKFLOW


def artifact_contract_valid(item: dict[str, Any]) -> bool:
    artifacts = item.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return False
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            return False
        digest = artifact.get("digest")
        if not (
            isinstance(digest, str)
            and digest.startswith("sha256:")
            and len(digest) == 71
            and all(character in "0123456789abcdef" for character in digest[7:])
        ):
            return False
        if not isinstance(artifact.get("name"), str) or not artifact["name"]:
            return False
    return True


def item_internally_valid(item: dict[str, Any]) -> bool:
    review = item.get("review")
    producer = item.get("producer_identity")
    source = item.get("source")
    return bool(
        item.get("status") == "accepted"
        and item.get("freshness_status") == "fresh"
        and item.get("generated_by_automation") is True
        and isinstance(review, dict)
        and review.get("decision") == "accepted"
        and review.get("independent_from_producer") is True
        and review.get("reviewer_identity") not in {None, "", "automation-unreviewed"}
        and isinstance(producer, dict)
        and producer.get("kind") == "github-actions"
        and isinstance(source, dict)
        and source.get("type") == "target-native-actions"
        and isinstance(source.get("run_id"), int)
        and source["run_id"] > 0
        and isinstance(source.get("job_id"), int)
        and source["job_id"] > 0
        and artifact_contract_valid(item)
    )


def item_matches_expected(
    item: dict[str, Any],
    *,
    repository: str,
    branch: str,
    head: str,
    tree: str,
) -> bool:
    target = item.get("target")
    return bool(
        isinstance(target, dict)
        and target.get("repository") == repository
        and target.get("branch") == branch
        and target.get("commit") == head
        and target.get("tree") == tree
    )


def run_matches_expected(
    run: dict[str, Any],
    *,
    inventory_repository: str,
    repository: str,
    branch: str,
    head: str,
) -> bool:
    return bool(
        inventory_repository == repository
        and run.get("head_branch") == branch
        and run.get("head_sha") == head
    )


def verify() -> dict[str, Any]:
    current_state = load_json(CURRENT_STATE_PATH)
    evidence_index = load_json(EVIDENCE_INDEX_PATH)
    inventory = load_json(RUN_INVENTORY_PATH)
    repository, branch, head, tree = expected_candidate(current_state)

    items = evidence_index.get("items")
    require(isinstance(items, list), "evidence index items must be an array")
    trusted_current: list[str] = []
    trusted_historical: list[str] = []
    invalid_trusted: list[str] = []
    for item in items:
        require(isinstance(item, dict), "evidence item must be an object")
        if not item_targets_runtime_live(item):
            continue
        evidence_id = item.get("evidence_id")
        require(isinstance(evidence_id, str) and evidence_id, "runtime evidence_id missing")
        if item.get("trusted") is not True:
            continue
        if not item_internally_valid(item):
            invalid_trusted.append(evidence_id)
            continue
        if item_matches_expected(
            item,
            repository=repository,
            branch=branch,
            head=head,
            tree=tree,
        ):
            trusted_current.append(evidence_id)
        else:
            trusted_historical.append(evidence_id)

    require(
        not invalid_trusted,
        "trusted live evidence is internally invalid: " + ", ".join(sorted(invalid_trusted)),
    )

    runs = inventory.get("runs")
    require(isinstance(runs, list), "run inventory must contain runs")
    inventory_repository = inventory.get("repository")
    require(isinstance(inventory_repository, str), "run inventory repository is invalid")
    successful_current_runs: list[int] = []
    successful_historical_runs: list[int] = []
    for run in runs:
        require(isinstance(run, dict), "run inventory row must be an object")
        workflow = str(run.get("workflow", ""))
        path = str(run.get("path", ""))
        if workflow != TARGET_WORKFLOW and not path.endswith("/trnm-server-live.yml"):
            continue
        if run.get("status") != "completed" or run.get("conclusion") != "success":
            continue
        run_id = run.get("run_id")
        require(isinstance(run_id, int) and run_id > 0, "successful live run_id is invalid")
        if run_matches_expected(
            run,
            inventory_repository=inventory_repository,
            repository=repository,
            branch=branch,
            head=head,
        ):
            successful_current_runs.append(run_id)
        else:
            successful_historical_runs.append(run_id)

    # A current-head successful run is not enough by itself. It must have a
    # matching trusted, independently accepted evidence item before it can
    # influence gates. Historical successes do not impose that requirement on
    # the moving candidate and receive no current credit.
    if successful_current_runs:
        require(
            trusted_current,
            "current trnm-server-live success is not represented by trusted exact-head evidence",
        )

    return {
        "schema": "trillionnium.runtime-stack-live-evidence-report.v1",
        "expected_target": {
            "repository": repository,
            "branch": branch,
            "commit": head,
            "tree": tree,
        },
        "trusted_current_evidence_ids": sorted(trusted_current),
        "trusted_historical_evidence_ids": sorted(trusted_historical),
        "trusted_current_count": len(trusted_current),
        "trusted_historical_count": len(trusted_historical),
        "successful_current_run_ids": sorted(successful_current_runs),
        "successful_historical_run_ids": sorted(successful_historical_runs),
        "current_live_success_observed": bool(successful_current_runs),
        "current_live_success_eligible": bool(successful_current_runs and trusted_current),
        "claims": {
            "historical_evidence_retained": bool(trusted_historical),
            "current_exact_head_live_verified": bool(trusted_current),
            "compatibility_credit": False,
            "production_ready": False,
        },
    }


def main() -> int:
    try:
        report = verify()
    except (EvidenceError, OSError, ValueError) as error:
        print(f"Runtime stack live evidence validation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
