from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping

SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ISSUE_RE = re.compile(r"^https://github\.com/[^/]+/[^/]+/issues/[1-9][0-9]*$")


class ReviewError(ValueError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def load_json(path: Any) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReviewError(f"{path}: root must be an object")
    return value


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReviewError(f"{field} must be a non-empty string")
    return value


def _sha256(value: Any, field: str) -> str:
    value = _string(value, field)
    if not SHA256_RE.fullmatch(value):
        raise ReviewError(f"{field} must be sha256:<64 lowercase hex>")
    return value


def _git_sha(value: Any, field: str) -> str:
    value = _string(value, field)
    if not GIT_SHA_RE.fullmatch(value) or value == "0" * 40:
        raise ReviewError(f"{field} must be a non-zero 40-character Git SHA")
    return value


def _path(value: Any, field: str, roots: Iterable[str]) -> str:
    value = _string(value, field)
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not any(
        value == root or value.startswith(root + "/") for root in roots
    ):
        raise ReviewError(f"{field} is outside an approved root")
    return value


def _candidate(candidate: Mapping[str, Any]) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    denominator = _string(candidate.get("denominator"), "candidate.denominator")
    leaves = candidate.get("leaves")
    manual = candidate.get("manual_contracts", [])
    if not isinstance(leaves, list) or not leaves:
        raise ReviewError("candidate.leaves must be a non-empty list")
    if not isinstance(manual, list):
        raise ReviewError("candidate.manual_contracts must be a list")
    normalized: list[dict[str, Any]] = []
    ids: set[str] = set()
    for raw in leaves:
        if not isinstance(raw, dict):
            raise ReviewError("candidate leaf must be an object")
        leaf = dict(raw)
        leaf_id = _string(leaf.get("id"), "candidate leaf.id")
        if leaf_id in ids:
            raise ReviewError(f"duplicate candidate leaf ID: {leaf_id}")
        ids.add(leaf_id)
        _sha256(leaf.get("signature_hash"), f"candidate leaf {leaf_id}.signature_hash")
        normalized.append(leaf)
    return denominator, sorted(normalized, key=lambda item: item["id"]), list(manual)


def _task_ids(backlog: Mapping[str, Any]) -> set[str]:
    tasks: set[str] = set()
    stack: list[Any] = [backlog]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            task_id = value.get("task_id") or value.get("id")
            if isinstance(task_id, str) and task_id.startswith("TG-"):
                tasks.add(task_id)
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return tasks


def _gate_ids(gates: Mapping[str, Any]) -> set[str]:
    result: set[str] = set()
    stack: list[Any] = [gates]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            gate_id = value.get("gate_id") or value.get("id")
            if isinstance(gate_id, str) and gate_id.startswith("GATE-"):
                result.add(gate_id)
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return result


def _reviewers(review: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    if review.get("self_approval") is not False:
        raise ReviewError("self_approval must be false")
    author = _string(review.get("author_identity"), "review.author_identity")
    values = review.get("reviewers")
    minimum = int(policy.get("minimum_reviewers", 2))
    if not isinstance(values, list) or len(values) < minimum:
        raise ReviewError(f"at least {minimum} independent reviewers are required")
    result: dict[str, dict[str, Any]] = {}
    for raw in values:
        if not isinstance(raw, dict):
            raise ReviewError("reviewer must be an object")
        identity = _string(raw.get("identity"), "reviewer.identity")
        role = _string(raw.get("role"), f"reviewer {identity}.role")
        if identity == author:
            raise ReviewError("author cannot approve their own denominator")
        if identity in result:
            raise ReviewError(f"duplicate reviewer identity: {identity}")
        if role not in set(policy.get("owner_roles", [])):
            raise ReviewError(f"unknown reviewer role: {role}")
        result[identity] = {"identity": identity, "role": role}
    return result


def _remote(value: Any, required: bool) -> dict[str, Any] | None:
    if value is None:
        if required:
            raise ReviewError("non-empty exact-head remote evidence is required")
        return None
    if not isinstance(value, dict):
        raise ReviewError("remote_evidence must be an object")
    head = _git_sha(value.get("head_sha"), "remote_evidence.head_sha")
    run_id = value.get("workflow_run_id")
    artifact_id = value.get("artifact_id")
    assertions = value.get("assertion_count")
    if not isinstance(run_id, int) or run_id <= 0:
        raise ReviewError("remote_evidence.workflow_run_id must be positive")
    if value.get("conclusion") != "success":
        raise ReviewError("remote workflow conclusion must be success")
    if not isinstance(artifact_id, int) or artifact_id <= 0:
        raise ReviewError("remote_evidence.artifact_id must be positive")
    if not isinstance(assertions, int) or assertions <= 0:
        raise ReviewError("remote_evidence.assertion_count must be positive")
    return {
        "head_sha": head,
        "pull_request": int(value.get("pull_request", 0)),
        "workflow_run_id": run_id,
        "artifact_id": artifact_id,
        "artifact_sha256": _sha256(value.get("artifact_sha256"), "remote_evidence.artifact_sha256"),
        "assertion_count": assertions,
        "conclusion": "success",
    }


def _leaf_decision(
    leaf: Mapping[str, Any],
    decision: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
    reviewers: Mapping[str, Mapping[str, Any]],
    task_ids: set[str],
    gate_ids: set[str],
) -> dict[str, Any]:
    leaf_id = str(leaf["id"])
    if decision.get("signature_hash") != leaf.get("signature_hash"):
        raise ReviewError(f"leaf {leaf_id} signature hash changed during review")
    classification = _string(decision.get("classification"), f"leaf {leaf_id}.classification")
    if classification not in set(policy.get("classifications", [])):
        raise ReviewError(f"leaf {leaf_id} has unsupported classification")
    owner = _string(decision.get("owner_role"), f"leaf {leaf_id}.owner_role")
    if owner not in set(policy.get("owner_roles", [])):
        raise ReviewError(f"leaf {leaf_id} has unknown owner role")
    task_id = _string(decision.get("task_id"), f"leaf {leaf_id}.task_id")
    if task_id not in task_ids:
        raise ReviewError(f"leaf {leaf_id} references unknown task {task_id}")
    test_id = _string(decision.get("test_id"), f"leaf {leaf_id}.test_id")
    gate_id = _string(decision.get("gate_id"), f"leaf {leaf_id}.gate_id")
    if gate_id not in gate_ids:
        raise ReviewError(f"leaf {leaf_id} references unknown gate {gate_id}")
    evidence = _path(
        decision.get("evidence_path"),
        f"leaf {leaf_id}.evidence_path",
        policy.get("evidence_roots", ["docs/evidence"]),
    )
    reviewer_ids = decision.get("reviewer_ids")
    if not isinstance(reviewer_ids, list) or not reviewer_ids:
        raise ReviewError(f"leaf {leaf_id} has no reviewer")
    if any(identity not in reviewers for identity in reviewer_ids):
        raise ReviewError(f"leaf {leaf_id} references unknown reviewer")
    result = {
        "classification": classification,
        "owner_role": owner,
        "task_id": task_id,
        "test_id": test_id,
        "gate_id": gate_id,
        "evidence_path": evidence,
        "reviewer_ids": sorted(set(reviewer_ids)),
    }
    if classification == "optional-profile":
        result["profile"] = _string(decision.get("profile"), f"leaf {leaf_id}.profile")
    elif classification == "versioned-exclusion":
        minimum = int(policy.get("minimum_exclusion_reviewers", 2))
        if len(set(reviewer_ids)) < minimum:
            raise ReviewError(f"leaf {leaf_id} exclusion requires {minimum} reviewers")
        result["adr_ref"] = _path(
            decision.get("adr_ref"),
            f"leaf {leaf_id}.adr_ref",
            policy.get("adr_roots", ["docs/adr"]),
        )
        expiry = _string(decision.get("expiry"), f"leaf {leaf_id}.expiry")
        try:
            expiry_date = date.fromisoformat(expiry)
        except ValueError as exc:
            raise ReviewError(f"leaf {leaf_id}.expiry is not ISO date") from exc
        if expiry_date <= date.today():
            raise ReviewError(f"leaf {leaf_id} exclusion expiry must be in the future")
        result["expiry"] = expiry
    return result


def _manual_reviews(
    candidate_manual: list[dict[str, Any]],
    review: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
    reviewers: Mapping[str, Mapping[str, Any]],
    leaf_ids: set[str],
    gate_ids: set[str],
) -> tuple[list[dict[str, Any]], int]:
    decisions = review.get("manual_contracts")
    if not isinstance(decisions, list):
        raise ReviewError("review.manual_contracts must be a list")
    expected = {sha256_bytes(canonical_bytes(item)): item for item in candidate_manual}
    by_identity: dict[str, dict[str, Any]] = {}
    for raw in decisions:
        if not isinstance(raw, dict):
            raise ReviewError("manual contract decision must be an object")
        identity = _string(raw.get("identity"), "manual contract identity")
        if identity in by_identity:
            raise ReviewError(f"duplicate manual contract decision: {identity}")
        by_identity[identity] = raw
    if set(by_identity) != set(expected):
        raise ReviewError("manual contract decisions must cover candidate manual contracts exactly")
    normalized: list[dict[str, Any]] = []
    blockers = 0
    for identity in sorted(expected):
        raw = by_identity[identity]
        reviewer_ids = raw.get("reviewer_ids")
        if not isinstance(reviewer_ids, list) or len(set(reviewer_ids)) < 2:
            raise ReviewError(f"manual contract {identity} requires two reviewers")
        if any(item not in reviewers for item in reviewer_ids):
            raise ReviewError(f"manual contract {identity} references unknown reviewer")
        disposition = raw.get("disposition")
        result = {
            "identity": identity,
            "disposition": disposition,
            "reviewer_ids": sorted(set(reviewer_ids)),
        }
        if disposition == "resolved-to-leaves":
            targets = raw.get("leaf_ids")
            if not isinstance(targets, list) or not targets or any(item not in leaf_ids for item in targets):
                raise ReviewError(f"manual contract {identity} has invalid target leaves")
            result["leaf_ids"] = sorted(set(targets))
        elif disposition == "owned-blocker":
            blockers += 1
            issue_url = _string(raw.get("issue_url"), f"manual contract {identity}.issue_url")
            if not ISSUE_RE.fullmatch(issue_url):
                raise ReviewError(f"manual contract {identity} issue URL is not canonical")
            owner = _string(raw.get("owner_role"), f"manual contract {identity}.owner_role")
            if owner not in set(policy.get("owner_roles", [])):
                raise ReviewError(f"manual contract {identity} has unknown owner role")
            gates = raw.get("gate_ids")
            if not isinstance(gates, list) or not gates or any(item not in gate_ids for item in gates):
                raise ReviewError(f"manual contract {identity} has invalid gate IDs")
            result.update(issue_url=issue_url, owner_role=owner, gate_ids=sorted(set(gates)))
        else:
            raise ReviewError(f"manual contract {identity} has unsupported disposition")
        normalized.append(result)
    return normalized, blockers


@dataclass(frozen=True)
class ReviewResult:
    lock: dict[str, Any]
    can_write_reviewed_lock: bool


def review_candidate(
    *,
    candidate_bytes: bytes,
    review: Mapping[str, Any],
    policy: Mapping[str, Any],
    backlog: Mapping[str, Any],
    gates: Mapping[str, Any],
    require_remote_evidence: bool = False,
    previous_lock: Mapping[str, Any] | None = None,
) -> ReviewResult:
    try:
        candidate = json.loads(candidate_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewError(f"candidate is not UTF-8 JSON: {exc}") from exc
    if not isinstance(candidate, dict):
        raise ReviewError("candidate root must be an object")
    denominator, leaves, manual = _candidate(candidate)
    if denominator not in set(policy.get("required_denominators", [])):
        raise ReviewError(f"unregistered denominator: {denominator}")
    if review.get("schema") != "trillionnium.denominator-review.v1":
        raise ReviewError("unsupported review schema")
    if review.get("denominator") != denominator:
        raise ReviewError("review denominator does not match candidate")
    digest = sha256_bytes(candidate_bytes)
    if review.get("candidate_sha256") != digest:
        raise ReviewError("review candidate_sha256 does not match exact bytes")
    head = _git_sha(review.get("candidate_head"), "review.candidate_head")
    reviewers = _reviewers(review, policy)
    remote = _remote(review.get("remote_evidence"), require_remote_evidence)
    if remote is not None and remote["head_sha"] != head:
        raise ReviewError("remote evidence head does not match candidate head")

    decisions = review.get("leaf_decisions")
    if not isinstance(decisions, list):
        raise ReviewError("review.leaf_decisions must be a list")
    by_id: dict[str, dict[str, Any]] = {}
    for raw in decisions:
        if not isinstance(raw, dict):
            raise ReviewError("leaf decision must be an object")
        leaf_id = _string(raw.get("leaf_id"), "leaf decision.leaf_id")
        if leaf_id in by_id:
            raise ReviewError(f"duplicate leaf decision: {leaf_id}")
        by_id[leaf_id] = raw
    leaf_ids = {str(item["id"]) for item in leaves}
    if set(by_id) != leaf_ids:
        raise ReviewError(
            f"leaf decisions must cover candidate exactly; missing={sorted(leaf_ids-set(by_id))}, extra={sorted(set(by_id)-leaf_ids)}"
        )

    task_ids = _task_ids(backlog)
    gate_ids = _gate_ids(gates)
    reviewed_leaves = []
    for leaf in leaves:
        leaf_id = str(leaf["id"])
        reviewed_leaves.append(
            {**leaf, "review": _leaf_decision(
                leaf,
                by_id[leaf_id],
                policy=policy,
                reviewers=reviewers,
                task_ids=task_ids,
                gate_ids=gate_ids,
            )}
        )
    manual_reviews, blockers = _manual_reviews(
        manual,
        review,
        policy=policy,
        reviewers=reviewers,
        leaf_ids=leaf_ids,
        gate_ids=gate_ids,
    )

    if previous_lock is not None:
        previous_ids = {
            str(item.get("id"))
            for item in previous_lock.get("leaves", [])
            if isinstance(item, dict) and item.get("id")
        }
        removed = sorted(previous_ids - leaf_ids)
        if removed:
            decrease = review.get("denominator_decrease")
            if not isinstance(decrease, dict) or sorted(decrease.get("removed_leaf_ids", [])) != removed:
                raise ReviewError(f"denominator decrease lacks exact removal evidence: {removed}")
            _path(decrease.get("adr_ref"), "denominator_decrease.adr_ref", policy.get("adr_roots", ["docs/adr"]))
            _sha256(decrease.get("upstream_delta_sha256"), "denominator_decrease.upstream_delta_sha256")

    ready = blockers == 0
    locked = ready and require_remote_evidence and remote is not None
    status = "reviewed-locked" if locked else ("reviewed-ready" if ready else "reviewed-blocked")
    result = {
        "schema": "trillionnium.denominator-reviewed-lock.v1",
        "project_id": "trillionnium-game",
        "denominator": denominator,
        "status": status,
        "candidate": {
            "sha256": digest,
            "head_sha": head,
            "leaf_count": len(leaves),
            "manual_contract_count": len(manual),
        },
        "leaf_count": len(reviewed_leaves),
        "unclassified_count": 0,
        "unreviewed_count": 0,
        "manual_blocker_count": blockers,
        "leaves": reviewed_leaves,
        "manual_contract_reviews": manual_reviews,
        "review": {
            "author_identity": review["author_identity"],
            "reviewers": sorted(reviewers.values(), key=lambda item: item["identity"]),
            "self_approval": False,
            "review_bundle_sha256": sha256_bytes(canonical_bytes(review)),
            "remote_evidence": remote,
        },
        "claims": {
            "denominator_review_complete": True,
            "denominator_lock_complete": locked,
            "sg1_complete": False,
            "compatibility_credit": False,
            "production_ready": False,
        },
    }
    result["content_sha256"] = sha256_bytes(canonical_bytes(result))
    return ReviewResult(result, locked)


def aggregate_reviewed_locks(
    locks: Iterable[Mapping[str, Any]], policy: Mapping[str, Any]
) -> dict[str, Any]:
    required = set(policy.get("required_denominators", []))
    by_name: dict[str, Mapping[str, Any]] = {}
    for lock in locks:
        name = _string(lock.get("denominator"), "lock.denominator")
        if name in by_name:
            raise ReviewError(f"duplicate denominator lock: {name}")
        by_name[name] = lock
    if set(by_name) != required:
        raise ReviewError(
            f"denominator set mismatch; missing={sorted(required-set(by_name))}, extra={sorted(set(by_name)-required)}"
        )
    blockers: list[str] = []
    total = 0
    for name in sorted(required):
        lock = by_name[name]
        if lock.get("status") != "reviewed-locked":
            blockers.append(f"{name}:status={lock.get('status')}")
        if lock.get("unclassified_count") != 0 or lock.get("unreviewed_count") != 0:
            blockers.append(f"{name}:review-incomplete")
        if lock.get("manual_blocker_count") != 0:
            blockers.append(f"{name}:manual-blockers")
        if lock.get("claims", {}).get("denominator_lock_complete") is not True:
            blockers.append(f"{name}:lock-claim-false")
        total += int(lock.get("leaf_count", 0))
    result = {
        "schema": "trillionnium.sg1-denominator-aggregate.v1",
        "project_id": "trillionnium-game",
        "required_denominator_count": len(required),
        "reviewed_lock_count": len(by_name),
        "total_leaf_count": total,
        "blockers": blockers,
        "status": "sg1-independent-gate-review-required" if not blockers else "blocked",
        "claims": {
            "all_denominators_reviewed_locked": not blockers,
            "sg1_complete": False,
            "compatibility_credit": False,
            "production_ready": False,
        },
        "locks": [
            {
                "denominator": name,
                "content_sha256": by_name[name].get("content_sha256"),
                "leaf_count": by_name[name].get("leaf_count"),
            }
            for name in sorted(required)
        ],
    }
    result["content_sha256"] = sha256_bytes(canonical_bytes(result))
    return result
