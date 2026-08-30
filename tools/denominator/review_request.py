from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Iterable


class ReviewRequestError(RuntimeError):
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


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReviewRequestError(f"{path}: {error}") from error


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReviewRequestError(f"{label} must be a non-empty string")
    return value


def _choice(leaf: dict[str, Any], plural: str, singular: str, fallback: str) -> str:
    values = leaf.get(plural)
    if isinstance(values, list):
        for value in values:
            if isinstance(value, str) and value:
                return value
    value = leaf.get(singular)
    if isinstance(value, str) and value:
        return value
    return fallback


def build_review_package(
    *,
    candidate_paths: Iterable[Path],
    head_sha: str,
    remote_index_path: Path,
    output_dir: Path,
    policy_path: Path,
    routing_path: Path,
) -> dict[str, Any]:
    if len(head_sha) != 40 or any(character not in "0123456789abcdef" for character in head_sha):
        raise ReviewRequestError("head_sha must be an exact lowercase Git SHA")
    policy = _load(policy_path)
    routing = _load(routing_path)
    remote_index = _load(remote_index_path)
    required = policy.get("required_denominators")
    routes = routing.get("routes")
    remotes = remote_index.get("denominators")
    if not isinstance(required, list) or not required:
        raise ReviewRequestError("review policy required_denominators is empty")
    if not isinstance(routes, dict) or not isinstance(remotes, dict):
        raise ReviewRequestError("routing or remote evidence index is invalid")
    if remote_index.get("candidate_head") != head_sha:
        raise ReviewRequestError("remote evidence index head does not match candidate head")

    candidates: dict[str, tuple[Path, bytes, dict[str, Any]]] = {}
    for path in candidate_paths:
        raw = path.read_bytes()
        value = json.loads(raw)
        denominator = _string(value.get("denominator"), f"{path}: denominator")
        if denominator in candidates:
            raise ReviewRequestError(f"duplicate candidate for {denominator}")
        candidates[denominator] = (path, raw, value)
    if set(candidates) != set(required):
        raise ReviewRequestError(
            f"candidate set mismatch: missing={sorted(set(required)-set(candidates))} "
            f"extra={sorted(set(candidates)-set(required))}"
        )

    candidate_dir = output_dir / "candidates"
    request_dir = output_dir / "review-requests"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    request_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    total_leaves = 0
    total_manual = 0

    for denominator in required:
        source_path, raw, candidate = candidates[denominator]
        route = routes.get(denominator)
        remote = remotes.get(denominator)
        if not isinstance(route, dict) or not isinstance(remote, dict):
            raise ReviewRequestError(f"missing route or remote evidence for {denominator}")
        filename = _string(route.get("candidate_filename"), f"{denominator}: candidate_filename")
        owner_role = _string(route.get("owner_role"), f"{denominator}: owner_role")
        reviewer_roles = route.get("reviewer_roles")
        if not isinstance(reviewer_roles, list) or len(set(reviewer_roles)) < 2:
            raise ReviewRequestError(f"{denominator}: two distinct reviewer roles are required")
        if owner_role not in set(policy.get("owner_roles", [])):
            raise ReviewRequestError(f"{denominator}: owner role is not allowed")
        if remote.get("head_sha") != head_sha or remote.get("conclusion") != "success":
            raise ReviewRequestError(f"{denominator}: remote evidence is not exact-head success")
        if remote.get("evidence_kind") != "immutable-job-log" or remote.get("log_sealed") is not True:
            raise ReviewRequestError(f"{denominator}: sealed immutable job-log evidence is required")

        leaves = candidate.get("leaves")
        manuals = candidate.get("manual_contracts", [])
        if not isinstance(leaves, list) or not leaves:
            raise ReviewRequestError(f"{denominator}: candidate leaves are empty")
        if not isinstance(manuals, list):
            raise ReviewRequestError(f"{denominator}: manual_contracts must be an array")
        leaf_ids: set[str] = set()
        decisions: list[dict[str, Any]] = []
        default_task = _string(routing.get("default_task_id"), "default_task_id")
        default_gate = _string(routing.get("default_gate_id"), "default_gate_id")
        for leaf in sorted(leaves, key=lambda item: item.get("id", "")):
            if not isinstance(leaf, dict):
                raise ReviewRequestError(f"{denominator}: leaf must be an object")
            leaf_id = _string(leaf.get("id"), f"{denominator}: leaf id")
            signature = _string(leaf.get("signature_hash"), f"{leaf_id}: signature_hash")
            if leaf_id in leaf_ids or not signature.startswith("sha256:") or len(signature) != 71:
                raise ReviewRequestError(f"{denominator}: duplicate leaf or invalid signature {leaf_id}")
            leaf_ids.add(leaf_id)
            task_id = _choice(leaf, "task_ids", "task_id", default_task)
            test_id = _choice(
                leaf,
                "test_ids",
                "test_id",
                "TG-DIFF-" + hashlib.sha256(leaf_id.encode("utf-8")).hexdigest()[:18].upper(),
            )
            decisions.append(
                {
                    "leaf_id": leaf_id,
                    "signature_hash": signature,
                    "classification": "mandatory",
                    "owner_role": owner_role,
                    "task_id": task_id,
                    "test_id": test_id,
                    "gate_id": default_gate,
                    "evidence_path": f"manifests/evidence/denominators/{denominator.lower()}/{leaf_id}.json",
                    "reviewer_ids": [],
                    "proposal_only": True,
                }
            )

        manual_rows: list[dict[str, Any]] = []
        for item in manuals:
            if not isinstance(item, dict):
                raise ReviewRequestError(f"{denominator}: manual contract must be an object")
            manual_rows.append(
                {
                    "identity": sha256_bytes(canonical_bytes(item)),
                    "disposition": "owned-blocker",
                    "owner_role": owner_role,
                    "issue_url": _string(route.get("issue_url"), f"{denominator}: issue_url"),
                    "gate_ids": [default_gate],
                    "reviewer_ids": [],
                    "proposal_only": True,
                    "source_contract": item,
                }
            )

        candidate_sha = sha256_bytes(raw)
        target = candidate_dir / filename
        target.write_bytes(raw)
        request = {
            "schema": "trillionnium.denominator-review-request.v1",
            "project_id": "trillionnium-game",
            "denominator": denominator,
            "status": "awaiting-independent-review",
            "candidate_path": target.as_posix(),
            "candidate_sha256": candidate_sha,
            "candidate_head": head_sha,
            "author_identity": "trillionnium-gap-closure-bot",
            "self_approval": False,
            "required_reviewer_count": policy.get("minimum_reviewers"),
            "required_reviewer_roles": reviewer_roles,
            "proposal_policy": "Every extracted leaf is conservatively proposed mandatory; only independent review may change classification.",
            "review_bundle_template": {
                "schema": "trillionnium.denominator-review.v1",
                "denominator": denominator,
                "candidate_sha256": candidate_sha,
                "candidate_head": head_sha,
                "author_identity": "trillionnium-gap-closure-bot",
                "self_approval": False,
                "reviewers": [],
                "leaf_decisions": decisions,
                "manual_contracts": manual_rows,
                "remote_evidence": remote,
            },
            "claims": {
                "classification_review_completed": False,
                "independent_review_completed": False,
                "reviewed_lock_written": False,
                "sg1_complete": False,
                "compatibility_credit": False,
            },
        }
        request_path = request_dir / f"{denominator.lower()}.review-request.json"
        request_path.write_text(json.dumps(request, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        total_leaves += len(decisions)
        total_manual += len(manual_rows)
        rows.append(
            {
                "denominator": denominator,
                "candidate_path": target.as_posix(),
                "candidate_sha256": candidate_sha,
                "review_request_path": request_path.as_posix(),
                "leaf_count": len(decisions),
                "manual_blocker_count": len(manual_rows),
                "issue_url": route["issue_url"],
                "remote_evidence": remote,
            }
        )

    worklist = {
        "schema": "trillionnium.denominator-review-worklist.v1",
        "project_id": "trillionnium-game",
        "candidate_head": head_sha,
        "status": "awaiting-independent-review",
        "required_denominator_count": len(required),
        "candidate_count": len(rows),
        "total_leaf_count": total_leaves,
        "manual_blocker_count": total_manual,
        "denominators": rows,
        "claims": {
            "all_candidates_materialized": True,
            "all_leaves_have_conservative_proposals": True,
            "independent_review_completed": False,
            "all_denominators_reviewed_locked": False,
            "sg1_complete": False,
            "compatibility_credit": False,
        },
    }
    (output_dir / "denominator-review-worklist.json").write_text(
        json.dumps(worklist, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return worklist
