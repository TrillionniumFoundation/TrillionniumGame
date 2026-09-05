#!/usr/bin/env python3
"""Fail-closed task and gap state-transition policy.

The current status checker validates one repository snapshot. This module compares
an explicit previous repository root with a current root so promotions cannot jump
proof stages, terminal superseded rows cannot reactivate, and fail-closed evidence
regressions remain possible. It does not authenticate Git history or review by
itself; callers must supply the exact base and candidate roots.
"""
from __future__ import annotations

import argparse
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


class TransitionError(RuntimeError):
    """Raised when a state document or transition violates the policy."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise TransitionError(message)


def _graph(progress: tuple[str, ...], blocked: str) -> dict[str, frozenset[str]]:
    """Build one-step promotions, arbitrary fail-closed regressions and terminals."""
    result: dict[str, frozenset[str]] = {}
    for index, state in enumerate(progress):
        targets = {state, blocked, "rejected", "superseded", *progress[:index]}
        if index + 1 < len(progress):
            targets.add(progress[index + 1])
        result[state] = frozenset(targets)
    # Resolving a block can restore any nonterminal proof stage, but never grants
    # accepted/closed directly. Existing acceptance checks still validate proof.
    result[blocked] = frozenset({blocked, "rejected", "superseded", *progress[:-1]})
    # Reopening a rejected item starts at the first two planning stages only.
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
            require(bool(targets) and set(targets) <= set(states),
                    f"{kind}: {source} has invalid targets")
        require(graph["superseded"] == frozenset({"superseded"}),
                f"{kind}: superseded must be terminal")


def validate_transition(kind: str, previous: str, current: str, *, entity: str = "row") -> None:
    graph = allowed_transitions(kind)
    require(previous in graph, f"{entity}: unknown previous {kind} state {previous!r}")
    require(current in graph, f"{entity}: unknown current {kind} state {current!r}")
    require(current in graph[previous],
            f"{entity}: illegal {kind} transition {previous!r} -> {current!r}")


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
        require(isinstance(identity, str) and identity and identity not in result,
                f"{label}: invalid or duplicate {key}")
        result[identity] = row
    return result


def _states(rows: dict[str, dict[str, Any]], *, kind: str, label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    known = set(TASK_STATES if kind == "task" else GAP_STATES)
    for identity, row in rows.items():
        state = row.get("status")
        require(state in known, f"{label} {identity}: invalid state {state!r}")
        result[identity] = state
    return result


def snapshot(root: Path) -> dict[str, Any]:
    execution = _load(root, "docs/status/EXECUTION_STATUS.json")
    gaps = _load(root, "docs/status/GAP_REGISTER.json")
    roadmap = _load(root, "docs/roadmap/NEXT_MILESTONE.json")
    require(execution.get("schema") == "trillionnium.execution-status.v1",
            "execution status schema")
    require(gaps.get("schema") == "trillionnium.gap-register.v1", "gap register schema")
    require(roadmap.get("schema") == "trillionnium.next-milestone.v1", "roadmap schema")
    default = execution.get("default_task_state")
    require(default in TASK_STATES, "default task state")
    overrides = _states(_rows(execution.get("task_overrides", []), key="id",
                              label="task overrides"), kind="task", label="task")
    workstreams = _states(_rows(execution.get("workstreams", []), key="id",
                                label="workstreams"), kind="task", label="workstream")
    stages = _states(_rows(execution.get("stage_gates", []), key="id",
                           label="stage gates"), kind="task", label="stage")
    gap_states = _states(_rows(gaps.get("gaps", []), key="id", label="gaps"),
                         kind="gap", label="gap")
    items = _states(_rows(roadmap.get("items", []), key="id", label="roadmap items"),
                    kind="task", label="roadmap item")
    milestone_id = roadmap.get("milestone_id")
    milestone_state = roadmap.get("status")
    require(isinstance(milestone_id, str) and milestone_id, "milestone ID")
    require(milestone_state in TASK_STATES, "milestone status")
    return {
        "default_task_state": default,
        "task_overrides": overrides,
        "workstreams": workstreams,
        "stage_gates": stages,
        "gaps": gap_states,
        "roadmap_items": items,
        "milestone": {milestone_id: milestone_state},
    }


def _same_members(previous: dict[str, str], current: dict[str, str], label: str) -> None:
    require(set(previous) == set(current), f"{label}: membership changed during status transition")


def _compare_maps(kind: str, previous: dict[str, str], current: dict[str, str],
                  label: str, report: list[dict[str, str]]) -> None:
    _same_members(previous, current, label)
    for identity in sorted(previous):
        before, after = previous[identity], current[identity]
        validate_transition(kind, before, after, entity=f"{label} {identity}")
        if before != after:
            report.append({"kind": kind, "entity": f"{label}:{identity}",
                           "previous": before, "current": after})


def compare_snapshots(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    validate_policy()
    require(previous["default_task_state"] == current["default_task_state"] == "planned",
            "default task state cannot change")
    report: list[dict[str, str]] = []

    # Task overrides are sparse. Absence means the immutable backlog default,
    # therefore addition/removal is compared against the explicit default state.
    previous_overrides = previous["task_overrides"]
    current_overrides = current["task_overrides"]
    for identity in sorted(set(previous_overrides) | set(current_overrides)):
        before = previous_overrides.get(identity, previous["default_task_state"])
        after = current_overrides.get(identity, current["default_task_state"])
        validate_transition("task", before, after, entity=f"task override {identity}")
        if before != after:
            report.append({"kind": "task", "entity": f"task:{identity}",
                           "previous": before, "current": after})

    for key, label in (("workstreams", "workstream"), ("stage_gates", "stage"),
                       ("roadmap_items", "roadmap"), ("milestone", "milestone")):
        _compare_maps("task", previous[key], current[key], label, report)
    _compare_maps("gap", previous["gaps"], current["gaps"], "gap", report)
    return {
        "schema": "trillionnium.status-transition-report.v1",
        "valid": True,
        "transition_count": len(report),
        "transitions": report,
        "claim_boundary": {
            "git_history_authenticated": False,
            "independent_review_established": False,
            "evidence_acceptance_established": False,
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
            require(arguments.previous_root is None and arguments.current_root is None,
                    "self-check cannot be combined with roots")
            validate_policy()
            result = {"schema": "trillionnium.status-transition-policy.v1", "valid": True,
                      "task_states": len(TASK_STATES), "gap_states": len(GAP_STATES)}
        else:
            require(arguments.previous_root is not None and arguments.current_root is not None,
                    "both previous and current roots are required")
            result = compare_roots(arguments.previous_root, arguments.current_root)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (TransitionError, KeyError, TypeError) as error:
        print(f"status transition validation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
