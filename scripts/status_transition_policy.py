#!/usr/bin/env python3
"""Fail-closed task, gap and approved roadmap-scope transition policy.

The current status checker validates one repository snapshot. This module compares
an explicit previous repository root with a current root so promotions cannot jump
proof stages, terminal superseded rows cannot reactivate, and fail-closed evidence
regressions remain possible.

A roadmap is specification scope rather than a mutable status table. A scope
replacement is therefore rejected unless its exact previous membership and exact
current immutable-scope digest match a reviewed replacement declared below. Such
a replacement transfers no acceptance or verified-state credit. This module does
not authenticate Git history or review by itself; callers must supply exact roots.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

TASK_STATES = (
    "planned",
    "ready",
    "in-progress",
    "source-candidate",
    "locally-verified",
    "remote-verified",
    "independently-reviewed",
    "accepted",
    "blocked",
    "rejected",
    "superseded",
)
GAP_STATES = (
    "open",
    "ready",
    "in-progress",
    "source-candidate",
    "locally-verified",
    "remote-verified",
    "independently-reviewed",
    "closed",
    "blocked-external-admin",
    "rejected",
    "superseded",
)
TASK_PROGRESS = TASK_STATES[:8]
GAP_PROGRESS = GAP_STATES[:8]
ROADMAP_MUTABLE_ROOT_FIELDS = frozenset(
    {"status", "updated_at", "items", "acceptance_target"}
)
ROADMAP_MUTABLE_ITEM_FIELDS = frozenset(
    {"status", "evidence_ids", "acceptance_target"}
)
SCOPE_RESET_ALLOWED_STATES = frozenset(
    {"planned", "ready", "in-progress", "source-candidate", "blocked", "rejected"}
)
APPROVED_ROADMAP_SCOPE_REPLACEMENTS = (
    {
        "previous_plan_version": 3,
        "previous_milestone_id": "M0-CONTROL-DATA-VERTICAL-SLICE",
        "previous_item_count": 25,
        "previous_item_ids_sha256": (
            "45594490a0fb0d31b54e73496dda4501eb41fad9d2679bb56eacad424f4aef8a"
        ),
        "current_plan_version": 3,
        "current_milestone_id": "M0-SINGULAR-ADMISSION-AND-EVIDENCE-CLOSURE",
        "current_item_count": 12,
        "current_item_ids_sha256": (
            "5a88e8a992c213d4a061a6e54eaff53f097287c988e0d57be0091e5b3fa57987"
        ),
        "current_scope_sha256": (
            "dc7af646e78d3beb976b78e2a7a8787b8f4a5139d65c996b296dfef0e9060678"
        ),
        "credit_policy": "reset-no-verified-or-accepted-state-transfer",
    },
)


class TransitionError(RuntimeError):
    """Raised when a state document or transition violates the policy."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise TransitionError(message)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _graph(progress: tuple[str, ...], blocked: str) -> dict[str, frozenset[str]]:
    """Build one-step promotions, arbitrary fail-closed regressions and terminals."""
    result: dict[str, frozenset[str]] = {}
    for index, state in enumerate(progress):
        targets = {state, blocked, "rejected", "superseded", *progress[:index]}
        if index + 1 < len(progress):
            targets.add(progress[index + 1])
        result[state] = frozenset(targets)
    result[blocked] = frozenset({blocked, "rejected", "superseded", *progress[:-1]})
    result["rejected"] = frozenset({"rejected", "superseded", progress[0], progress[1]})
    result["superseded"] = frozenset({"superseded"})
    return result


def allowed_transitions(kind: str) -> dict[str, frozenset[str]]:
    if kind == "task":
        return _graph(TASK_PROGRESS, "blocked")
    if kind == "gap":
        return _graph(GAP_PROGRESS, "blocked-external-admin")
    raise TransitionError(f"unknown transition kind: {kind}")


def validate_policy() -> None:
    for kind, states in (("task", TASK_STATES), ("gap", GAP_STATES)):
        graph = allowed_transitions(kind)
        require(set(graph) == set(states), f"{kind}: transition graph state coverage")
        for source, targets in graph.items():
            require(source in targets, f"{kind}: {source} self-transition missing")
            require(
                bool(targets) and set(targets) <= set(states),
                f"{kind}: {source} has invalid targets",
            )
        require(
            graph["superseded"] == frozenset({"superseded"}),
            f"{kind}: superseded must be terminal",
        )

    seen: set[tuple[Any, ...]] = set()
    for replacement in APPROVED_ROADMAP_SCOPE_REPLACEMENTS:
        required = {
            "previous_plan_version",
            "previous_milestone_id",
            "previous_item_count",
            "previous_item_ids_sha256",
            "current_plan_version",
            "current_milestone_id",
            "current_item_count",
            "current_item_ids_sha256",
            "current_scope_sha256",
            "credit_policy",
        }
        require(
            isinstance(replacement, dict) and set(replacement) == required,
            "roadmap replacement declaration fields",
        )
        require(
            replacement["credit_policy"]
            == "reset-no-verified-or-accepted-state-transfer",
            "roadmap replacement credit policy",
        )
        for key in (
            "previous_item_ids_sha256",
            "current_item_ids_sha256",
            "current_scope_sha256",
        ):
            value = replacement[key]
            require(
                isinstance(value, str)
                and len(value) == 64
                and all(character in "0123456789abcdef" for character in value),
                f"roadmap replacement invalid {key}",
            )
        identity = (
            replacement["previous_plan_version"],
            replacement["previous_milestone_id"],
            replacement["previous_item_count"],
            replacement["previous_item_ids_sha256"],
            replacement["current_plan_version"],
            replacement["current_milestone_id"],
            replacement["current_item_count"],
            replacement["current_item_ids_sha256"],
            replacement["current_scope_sha256"],
        )
        require(identity not in seen, "duplicate roadmap replacement declaration")
        seen.add(identity)


def validate_transition(
    kind: str, previous: str, current: str, *, entity: str = "row"
) -> None:
    graph = allowed_transitions(kind)
    require(previous in graph, f"{entity}: unknown previous {kind} state {previous!r}")
    require(current in graph, f"{entity}: unknown current {kind} state {current!r}")
    require(
        current in graph[previous],
        f"{entity}: illegal {kind} transition {previous!r} -> {current!r}",
    )


def _load(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise TransitionError(f"{relative}: invalid JSON: {error}") from error
    require(isinstance(value, dict), f"{relative}: object root required")
    return value


def _rows(value: Any, *, key: str, label: str) -> dict[str, dict[str, Any]]:
    require(isinstance(value, list), f"{label}: array required")
    result: dict[str, dict[str, Any]] = {}
    for row in value:
        require(isinstance(row, dict), f"{label}: object row required")
        identity = row.get(key)
        require(
            isinstance(identity, str) and identity and identity not in result,
            f"{label}: invalid or duplicate {key}",
        )
        result[identity] = row
    return result


def _states(
    rows: dict[str, dict[str, Any]], *, kind: str, label: str
) -> dict[str, str]:
    result: dict[str, str] = {}
    known = set(TASK_STATES if kind == "task" else GAP_STATES)
    for identity, row in rows.items():
        state = row.get("status")
        require(state in known, f"{label} {identity}: invalid state {state!r}")
        result[identity] = state
    return result


def _roadmap_scope(
    roadmap: dict[str, Any], items: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    root_scope = {
        key: value
        for key, value in roadmap.items()
        if key not in ROADMAP_MUTABLE_ROOT_FIELDS
    }
    item_scope = [
        {
            key: value
            for key, value in row.items()
            if key not in ROADMAP_MUTABLE_ITEM_FIELDS
        }
        for row in items.values()
    ]
    return {"root": root_scope, "items": item_scope}


def snapshot(root: Path) -> dict[str, Any]:
    execution = _load(root, "docs/status/EXECUTION_STATUS.json")
    gaps = _load(root, "docs/status/GAP_REGISTER.json")
    roadmap = _load(root, "docs/roadmap/NEXT_MILESTONE.json")
    require(
        execution.get("schema") == "trillionnium.execution-status.v1",
        "execution status schema",
    )
    require(gaps.get("schema") == "trillionnium.gap-register.v1", "gap register schema")
    require(
        roadmap.get("schema") == "trillionnium.next-milestone.v1",
        "roadmap schema",
    )
    require(
        type(roadmap.get("plan_version")) is int and roadmap["plan_version"] > 0,
        "roadmap plan version",
    )
    default = execution.get("default_task_state")
    require(default in TASK_STATES, "default task state")
    overrides = _states(
        _rows(execution.get("task_overrides", []), key="id", label="task overrides"),
        kind="task",
        label="task",
    )
    workstreams = _states(
        _rows(execution.get("workstreams", []), key="id", label="workstreams"),
        kind="task",
        label="workstream",
    )
    stages = _states(
        _rows(execution.get("stage_gates", []), key="id", label="stage gates"),
        kind="task",
        label="stage",
    )
    gap_states = _states(
        _rows(gaps.get("gaps", []), key="id", label="gaps"),
        kind="gap",
        label="gap",
    )
    item_rows = _rows(roadmap.get("items", []), key="id", label="roadmap items")
    item_states = _states(item_rows, kind="task", label="roadmap item")
    milestone_id = roadmap.get("milestone_id")
    milestone_state = roadmap.get("status")
    require(isinstance(milestone_id, str) and milestone_id, "milestone ID")
    require(milestone_state in TASK_STATES, "milestone status")
    item_ids = sorted(item_rows)
    return {
        "default_task_state": default,
        "task_overrides": overrides,
        "workstreams": workstreams,
        "stage_gates": stages,
        "gaps": gap_states,
        "roadmap": {
            "plan_version": roadmap["plan_version"],
            "milestone_id": milestone_id,
            "milestone_status": milestone_state,
            "scope_sha256": _sha256(_roadmap_scope(roadmap, item_rows)),
            "item_count": len(item_ids),
            "item_ids_sha256": _sha256(item_ids),
            "items": item_states,
        },
    }


def _same_members(
    previous: dict[str, str], current: dict[str, str], label: str
) -> None:
    require(
        set(previous) == set(current),
        f"{label}: membership changed during status transition",
    )


def _compare_maps(
    kind: str,
    previous: dict[str, str],
    current: dict[str, str],
    label: str,
    report: list[dict[str, Any]],
) -> None:
    _same_members(previous, current, label)
    for identity in sorted(previous):
        before, after = previous[identity], current[identity]
        validate_transition(kind, before, after, entity=f"{label} {identity}")
        if before != after:
            report.append(
                {
                    "kind": kind,
                    "entity": f"{label}:{identity}",
                    "previous": before,
                    "current": after,
                }
            )


def _replacement_identity(
    previous: dict[str, Any], current: dict[str, Any]
) -> tuple[Any, ...]:
    return (
        previous["plan_version"],
        previous["milestone_id"],
        previous["item_count"],
        previous["item_ids_sha256"],
        current["plan_version"],
        current["milestone_id"],
        current["item_count"],
        current["item_ids_sha256"],
        current["scope_sha256"],
    )


def _declared_replacement_identity(replacement: dict[str, Any]) -> tuple[Any, ...]:
    return (
        replacement["previous_plan_version"],
        replacement["previous_milestone_id"],
        replacement["previous_item_count"],
        replacement["previous_item_ids_sha256"],
        replacement["current_plan_version"],
        replacement["current_milestone_id"],
        replacement["current_item_count"],
        replacement["current_item_ids_sha256"],
        replacement["current_scope_sha256"],
    )


def _compare_roadmap(
    previous: dict[str, Any],
    current: dict[str, Any],
    report: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    same_scope = (
        previous["plan_version"] == current["plan_version"]
        and previous["milestone_id"] == current["milestone_id"]
        and previous["scope_sha256"] == current["scope_sha256"]
        and previous["item_ids_sha256"] == current["item_ids_sha256"]
    )
    if same_scope:
        _compare_maps("task", previous["items"], current["items"], "roadmap", report)
        validate_transition(
            "task",
            previous["milestone_status"],
            current["milestone_status"],
            entity=f"milestone {current['milestone_id']}",
        )
        if previous["milestone_status"] != current["milestone_status"]:
            report.append(
                {
                    "kind": "task",
                    "entity": f"milestone:{current['milestone_id']}",
                    "previous": previous["milestone_status"],
                    "current": current["milestone_status"],
                }
            )
        return []

    identity = _replacement_identity(previous, current)
    replacement = next(
        (
            declaration
            for declaration in APPROVED_ROADMAP_SCOPE_REPLACEMENTS
            if _declared_replacement_identity(declaration) == identity
        ),
        None,
    )
    require(
        replacement is not None,
        "roadmap: scope replacement is not an exact approved transition",
    )
    for label, state in (
        ("previous milestone", previous["milestone_status"]),
        ("current milestone", current["milestone_status"]),
    ):
        require(
            state in SCOPE_RESET_ALLOWED_STATES,
            f"roadmap: {label} carries verified or accepted state across replacement",
        )
    for side, rows in (("previous", previous["items"]), ("current", current["items"])):
        invalid = sorted(
            item_id
            for item_id, state in rows.items()
            if state not in SCOPE_RESET_ALLOWED_STATES
        )
        require(
            not invalid,
            f"roadmap: {side} scope carries verified or accepted items {invalid}",
        )
    event = {
        "kind": "roadmap-scope-replacement",
        "entity": current["milestone_id"],
        "previous_milestone_id": previous["milestone_id"],
        "previous_item_count": previous["item_count"],
        "previous_item_ids_sha256": previous["item_ids_sha256"],
        "current_item_count": current["item_count"],
        "current_item_ids_sha256": current["item_ids_sha256"],
        "current_scope_sha256": current["scope_sha256"],
        "credit_policy": replacement["credit_policy"],
    }
    report.append(event)
    return [event]


def compare_snapshots(
    previous: dict[str, Any], current: dict[str, Any]
) -> dict[str, Any]:
    validate_policy()
    require(
        previous["default_task_state"] == current["default_task_state"] == "planned",
        "default task state cannot change",
    )
    report: list[dict[str, Any]] = []

    previous_overrides = previous["task_overrides"]
    current_overrides = current["task_overrides"]
    for identity in sorted(set(previous_overrides) | set(current_overrides)):
        before = previous_overrides.get(identity, previous["default_task_state"])
        after = current_overrides.get(identity, current["default_task_state"])
        validate_transition("task", before, after, entity=f"task override {identity}")
        if before != after:
            report.append(
                {
                    "kind": "task",
                    "entity": f"task:{identity}",
                    "previous": before,
                    "current": after,
                }
            )

    for key, label in (("workstreams", "workstream"), ("stage_gates", "stage")):
        _compare_maps("task", previous[key], current[key], label, report)
    _compare_maps("gap", previous["gaps"], current["gaps"], "gap", report)
    replacements = _compare_roadmap(previous["roadmap"], current["roadmap"], report)
    return {
        "schema": "trillionnium.status-transition-report.v1",
        "valid": True,
        "transition_count": len(report),
        "scope_replacement_count": len(replacements),
        "scope_replacements": replacements,
        "transitions": report,
        "claim_boundary": {
            "git_history_authenticated": False,
            "independent_review_established": False,
            "evidence_acceptance_established": False,
            "scope_replacement_transfers_acceptance": False,
            "gap_closed": False,
        },
    }


def compare_roots(previous_root: Path, current_root: Path) -> dict[str, Any]:
    return compare_snapshots(snapshot(previous_root), snapshot(current_root))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--previous-root", type=Path)
    parser.add_argument("--current-root", type=Path)
    parser.add_argument("--self-check", action="store_true")
    arguments = parser.parse_args()
    try:
        if arguments.self_check:
            require(
                arguments.previous_root is None and arguments.current_root is None,
                "self-check cannot be combined with roots",
            )
            validate_policy()
            result = {
                "schema": "trillionnium.status-transition-policy.v2",
                "valid": True,
                "task_states": len(TASK_STATES),
                "gap_states": len(GAP_STATES),
                "approved_roadmap_scope_replacements": len(
                    APPROVED_ROADMAP_SCOPE_REPLACEMENTS
                ),
            }
        else:
            require(
                arguments.previous_root is not None
                and arguments.current_root is not None,
                "both previous and current roots are required",
            )
            result = compare_roots(arguments.previous_root, arguments.current_root)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (TransitionError, KeyError, TypeError, ValueError) as error:
        print(f"status transition validation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
