#!/usr/bin/env python3
"""Validate mutable execution, gap, evidence, gate and roadmap state."""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

TASK_STATES = {
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
}
GAP_STATES = {
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
}
GATE_STATES = {"open", "blocked", "passed"}
RISK_STATES = {"open", "mitigating", "accepted", "retired", "triggered"}
DIVERGENCE_STATES = {
    "open",
    "explained",
    "fixed-source",
    "verified-fixed",
    "accepted-extension",
    "rejected",
}
ACCEPTED_EVIDENCE_STATUS = "accepted"


class ValidationError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise ValidationError(message)


def load_json(path: str) -> dict[str, Any]:
    try:
        value = json.loads((ROOT / path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"{path}: invalid JSON: {exc}")
    if not isinstance(value, dict):
        fail(f"{path}: top-level value must be an object")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def unique_rows(rows: list[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = row.get(key)
        require(isinstance(value, str) and value, f"{label}: missing {key}")
        require(value not in values, f"{label}: duplicate {key} {value}")
        values[value] = row
    return values


def require_dag(graph: dict[str, list[str]], label: str) -> None:
    nodes = set(graph)
    for node, dependencies in graph.items():
        unknown = set(dependencies) - nodes
        require(not unknown, f"{label}: {node} has unknown dependencies {sorted(unknown)}")
    indegree = {node: 0 for node in nodes}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for node, dependencies in graph.items():
        for dependency in dependencies:
            indegree[node] += 1
            outgoing[dependency].append(node)
    queue = deque(sorted(node for node, degree in indegree.items() if degree == 0))
    visited = 0
    while queue:
        node = queue.popleft()
        visited += 1
        for follower in outgoing[node]:
            indegree[follower] -= 1
            if indegree[follower] == 0:
                queue.append(follower)
    require(visited == len(nodes), f"{label}: dependency cycle detected")


def evidence_is_accepted(row: dict[str, Any]) -> bool:
    review = row.get("independent_review")
    return (
        row.get("status") == ACCEPTED_EVIDENCE_STATUS
        and row.get("schema_valid") is True
        and row.get("target_identity_verified_by_current_repo") is True
        and isinstance(review, dict)
        and review.get("decision") == "accepted"
        and bool(review.get("reviewer_identity"))
    )


def validate_evidence() -> tuple[dict[str, dict[str, Any]], set[str]]:
    index = load_json("docs/evidence/index.json")
    require(index.get("schema") == "trillionnium.evidence-index.v1", "evidence index schema")
    entries = unique_rows(index.get("entries", []), "evidence_id", "evidence entries")
    accepted = {evidence_id for evidence_id, row in entries.items() if evidence_is_accepted(row)}
    require(index.get("accepted_entry_count") == len(accepted), "accepted evidence count mismatch")
    for evidence_id, row in entries.items():
        require(isinstance(row.get("claim_ids"), list), f"{evidence_id}: claim_ids")
        require(isinstance(row.get("gate_ids"), list), f"{evidence_id}: gate_ids")
        require(isinstance(row.get("task_ids"), list), f"{evidence_id}: task_ids")
        require(isinstance(row.get("parity_ids"), list), f"{evidence_id}: parity_ids")
        if row.get("compatibility_credit") is True:
            require(evidence_id in accepted, f"{evidence_id}: credit requires accepted evidence")
    return entries, accepted


def validate_gaps(
    evidence: dict[str, dict[str, Any]], accepted_evidence: set[str]
) -> dict[str, dict[str, Any]]:
    register = load_json("docs/status/GAP_REGISTER.json")
    require(register.get("schema") == "trillionnium.gap-register.v1", "gap register schema")
    gaps = unique_rows(register.get("gaps", []), "id", "gaps")
    require(len(gaps) >= 10, "gap register unexpectedly small")
    for gap_id, row in gaps.items():
        require(re.fullmatch(r"GAP-P[0-2]-[A-Z0-9-]+", gap_id) is not None, f"{gap_id}: invalid ID")
        require(row.get("severity") in {"P0", "P1", "P2"}, f"{gap_id}: invalid severity")
        require(row.get("status") in GAP_STATES, f"{gap_id}: invalid status")
        require(bool(row.get("owner_role")), f"{gap_id}: owner_role")
        require(bool(row.get("close_criteria")), f"{gap_id}: close_criteria")
        require(isinstance(row.get("blocking_claims"), list), f"{gap_id}: blocking_claims")
        evidence_ids = row.get("evidence_ids", [])
        require(isinstance(evidence_ids, list), f"{gap_id}: evidence_ids")
        unknown_evidence = set(evidence_ids) - set(evidence)
        require(not unknown_evidence, f"{gap_id}: unknown evidence {sorted(unknown_evidence)}")
        if row.get("status") == "closed":
            require(bool(evidence_ids), f"{gap_id}: closed gap requires evidence")
            require(set(evidence_ids) <= accepted_evidence, f"{gap_id}: evidence not accepted")
            if row.get("severity") in {"P0", "P1"}:
                for evidence_id in evidence_ids:
                    review = evidence[evidence_id].get("independent_review")
                    require(
                        isinstance(review, dict) and review.get("decision") == "accepted",
                        f"{gap_id}: independent review required",
                    )
    return gaps


def validate_execution(gaps: dict[str, dict[str, Any]]) -> None:
    state = load_json("docs/status/EXECUTION_STATUS.json")
    require(state.get("schema") == "trillionnium.execution-status.v1", "execution status schema")
    require(state.get("default_task_state") in TASK_STATES, "default task state")
    require(state.get("fail_closed") is True, "execution status must fail closed")
    workstreams = unique_rows(state.get("workstreams", []), "id", "workstreams")
    require(list(workstreams) == [f"W{i}" for i in range(17)], "workstream order/coverage")
    stages = unique_rows(state.get("stage_gates", []), "id", "stage gates")
    require(list(stages) == [f"SG{i}" for i in range(10)], "stage gate order/coverage")
    for label, rows in (("workstream", workstreams), ("stage gate", stages)):
        for row_id, row in rows.items():
            require(row.get("status") in TASK_STATES, f"{label} {row_id}: invalid status")
            unknown = set(row.get("blocking_gaps", [])) - set(gaps)
            require(not unknown, f"{label} {row_id}: unknown gaps {sorted(unknown)}")
    seen_tasks: set[str] = set()
    for row in state.get("task_overrides", []):
        task_id = row.get("id")
        require(
            isinstance(task_id, str) and re.fullmatch(r"TG-W\d+-\d{3}", task_id),
            "task override ID",
        )
        require(task_id not in seen_tasks, f"duplicate task override {task_id}")
        seen_tasks.add(task_id)
        require(row.get("status") in TASK_STATES, f"{task_id}: invalid status")
        unknown = set(row.get("blocking_gaps", [])) - set(gaps)
        require(not unknown, f"{task_id}: unknown gaps {sorted(unknown)}")


def validate_roadmap(gaps: dict[str, dict[str, Any]]) -> None:
    roadmap = load_json("docs/roadmap/NEXT_MILESTONE.json")
    require(roadmap.get("schema") == "trillionnium.next-milestone.v1", "roadmap schema")
    items = unique_rows(roadmap.get("items", []), "id", "roadmap items")
    graph: dict[str, list[str]] = {}
    for item_id, row in items.items():
        require(re.fullmatch(r"TG-V3-\d{3}", item_id) is not None, f"{item_id}: invalid ID")
        require(row.get("status") in TASK_STATES, f"{item_id}: invalid status")
        require(row.get("priority") in {"P0", "P1", "P2"}, f"{item_id}: invalid priority")
        require(bool(row.get("deliverables")), f"{item_id}: deliverables")
        require(bool(row.get("acceptance")), f"{item_id}: acceptance")
        require(bool(row.get("required_evidence")), f"{item_id}: required evidence")
        unknown_gaps = set(row.get("gap_ids", [])) - set(gaps)
        require(not unknown_gaps, f"{item_id}: unknown gaps {sorted(unknown_gaps)}")
        graph[item_id] = row.get("depends_on", [])
    require_dag(graph, "roadmap")


def validate_gates(gaps: dict[str, dict[str, Any]]) -> None:
    document = load_json("docs/status/PRODUCT_GATES.json")
    require(document.get("schema") == "trillionnium.product-gates.v3", "product gate schema")
    gates = unique_rows(document.get("gates", []), "id", "product gates")
    require(len(gates) == 15, "expected 15 product gates")
    graph: dict[str, list[str]] = {}
    counts = {state: 0 for state in GATE_STATES}
    for gate_id, row in gates.items():
        require(row.get("status") in GATE_STATES, f"{gate_id}: invalid status")
        counts[row["status"]] += 1
        unknown_gaps = set(row.get("blocking_gap_ids", [])) - set(gaps)
        require(not unknown_gaps, f"{gate_id}: unknown gaps {sorted(unknown_gaps)}")
        require(bool(row.get("pass_criteria")), f"{gate_id}: pass criteria")
        require(bool(row.get("evidence_types")), f"{gate_id}: evidence types")
        graph[gate_id] = row.get("depends_on", [])
    require_dag(graph, "product gates")
    summary = document.get("summary", {})
    for state, count in counts.items():
        require(summary.get(state) == count, f"product gate summary {state}")


def validate_risks(gaps: dict[str, dict[str, Any]]) -> None:
    document = load_json("docs/status/RISK_REGISTER.json")
    require(document.get("schema") == "trillionnium.risk-register.v2", "risk register schema")
    risks = unique_rows(document.get("risks", []), "id", "risks")
    require(len(risks) >= 25, "risk register unexpectedly small")
    triggered = 0
    retired = 0
    for risk_id, row in risks.items():
        require(row.get("severity") in {"P0", "P1", "P2"}, f"{risk_id}: severity")
        require(row.get("status") in RISK_STATES, f"{risk_id}: status")
        require(bool(row.get("owner")), f"{risk_id}: owner")
        unknown = set(row.get("gap_ids", [])) - set(gaps)
        require(not unknown, f"{risk_id}: unknown gaps {sorted(unknown)}")
        triggered += row.get("status") == "triggered"
        retired += row.get("status") == "retired"
    summary = document.get("summary", {})
    require(summary.get("total") == len(risks), "risk total summary")
    require(summary.get("triggered") == triggered, "risk triggered summary")
    require(summary.get("retired") == retired, "risk retired summary")
    require(
        summary.get("open_or_mitigating")
        == sum(row.get("status") in {"open", "mitigating", "accepted"} for row in risks.values()),
        "risk open/mitigating summary",
    )


def parity_ids() -> set[str]:
    rows = (ROOT / "docs/development/FEATURE_PARITY_MATRIX.md").read_text(encoding="utf-8").splitlines()
    return {line.split("|")[1].strip() for line in rows if line.startswith("| TG-PAR-")}


def validate_divergences(gaps: dict[str, dict[str, Any]]) -> None:
    document = load_json("docs/development/COMPATIBILITY_DIVERGENCES.json")
    require(document.get("schema") == "trillionnium.compatibility-divergences.v1", "divergence schema")
    divergences = unique_rows(document.get("divergences", []), "id", "divergences")
    known_parity = parity_ids()
    open_p0 = 0
    open_p1 = 0
    for divergence_id, row in divergences.items():
        require(row.get("severity") in {"P0", "P1", "P2", "informational"}, f"{divergence_id}: severity")
        require(row.get("status") in DIVERGENCE_STATES, f"{divergence_id}: status")
        gap_id = row.get("gap_id")
        require(gap_id is None or gap_id in gaps, f"{divergence_id}: unknown gap {gap_id}")
        unknown_parity = set(row.get("parity_ids", [])) - known_parity
        require(not unknown_parity, f"{divergence_id}: unknown parity {sorted(unknown_parity)}")
        if row.get("status") == "open":
            open_p0 += row.get("severity") == "P0"
            open_p1 += row.get("severity") == "P1"
    require(document.get("open_p0_count") == open_p0, "open P0 divergence count")
    require(document.get("open_p1_count") == open_p1, "open P1 divergence count")


def validate_inventory(
    gaps: dict[str, dict[str, Any]], evidence: dict[str, dict[str, Any]], accepted: set[str]
) -> None:
    document = load_json("docs/status/IMPLEMENTATION_INVENTORY.json")
    require(document.get("schema") == "trillionnium.implementation-inventory.v2", "inventory schema")
    components = unique_rows(document.get("components", []), "id", "components")
    required_components = {
        "COMP-CONTRACTS",
        "COMP-STORAGE",
        "COMP-PERSISTENCE-CORE",
        "COMP-PERSISTENCE-PG",
        "COMP-TRNM-SERVER",
        "COMP-TOKEN-JWT",
        "COMP-MIGRATIONS",
        "COMP-SCHEMA-DESIGN-V2",
        "COMP-CI-CONTROL",
    }
    require(required_components <= set(components), "implementation inventory missing required components")
    for component_id, row in components.items():
        require(bool(row.get("path")), f"{component_id}: path")
        require(bool(row.get("kind")), f"{component_id}: kind")
        require(bool(row.get("status")), f"{component_id}: status")
        require(isinstance(row.get("implemented"), list), f"{component_id}: implemented")
        require(isinstance(row.get("missing"), list), f"{component_id}: missing")
        unknown_gaps = set(row.get("blocking_gaps", [])) - set(gaps)
        require(not unknown_gaps, f"{component_id}: unknown gaps {sorted(unknown_gaps)}")
        evidence_ids = set(row.get("evidence_ids", []))
        unknown_evidence = evidence_ids - set(evidence)
        require(not unknown_evidence, f"{component_id}: unknown evidence {sorted(unknown_evidence)}")
        if row.get("claim_credit") is True:
            require(bool(evidence_ids), f"{component_id}: claim credit requires evidence")
            require(evidence_ids <= accepted, f"{component_id}: claim credit evidence not accepted")
    server = components["COMP-TRNM-SERVER"]
    require(server.get("status") == "source-candidate", "server inventory stage")
    require(server.get("claim_credit") is False, "server inventory must not claim credit")
    require(
        (ROOT / "crates/trnm-persistence-pg/src/bin/trnm-server.rs").is_file(),
        "server inventory source missing",
    )
    design = components["COMP-SCHEMA-DESIGN-V2"]
    require(design.get("status") == "must-not-be-consumed", "alternate schema inventory status")
    require(design.get("claim_credit") is False, "alternate schema cannot receive credit")


def validate_server_status(gaps: dict[str, dict[str, Any]]) -> None:
    document = load_json("docs/status/TRNM_SERVER_STATUS.json")
    require(document.get("schema") == "trillionnium.trnm-server-status.v1", "server status schema")
    require(
        document.get("stage") == "http-database-vertical-source-candidate",
        "server status stage",
    )
    for path in document.get("source_paths", []):
        require((ROOT / path).exists(), f"server status missing source path {path}")
    unknown_gaps = set(document.get("gap_ids", [])) - set(gaps)
    require(not unknown_gaps, f"server status unknown gaps {sorted(unknown_gaps)}")
    claims = document.get("claims", {})
    require(claims.get("source_candidate") is True, "server source candidate claim")
    require(
        claims.get("bounded_retry_source_candidate") is True,
        "server bounded retry source claim",
    )
    forbidden_positive = {
        "remote_verified",
        "live_database_verified",
        "http_wire_compatible",
        "grpc_implemented",
        "websocket_implemented",
        "session_integrated",
        "outbox_delivery_verified",
        "sg4_complete",
        "production_ready",
        "public_online",
        "nakama_replaced",
    }
    require(
        not any(claims.get(field) for field in forbidden_positive),
        "server status overclaims execution or compatibility",
    )
    require(bool(document.get("not_implemented_or_verified")), "server residual gaps missing")


def validate_current_state(gaps: dict[str, dict[str, Any]]) -> None:
    state = load_json("docs/status/CURRENT_STATE.json")
    require(state.get("schema") == "trillionnium.current-state.v1", "current state schema")
    require(state.get("fail_closed") is True, "current state fail_closed")
    claims = state.get("claims", {})
    production_claims = [
        "wire_compatible",
        "behavior_compatible",
        "data_migration_compatible",
        "operationally_replaceable",
        "supported_full_replacement",
        "production_ready",
        "public_online",
        "nakama_retired",
    ]
    require(not any(claims.get(field) for field in production_claims), "current state overclaims")
    known_gap_ids = set(gaps)
    governance = state.get("repository_governance", {})
    unknown = set(governance.get("blocking_gaps", [])) - known_gap_ids
    require(not unknown, f"current state unknown governance gaps {sorted(unknown)}")
    runtime = state.get("runtime_topology", {})
    require(runtime.get("rust_server_binary_present") is True, "current state server source presence")
    require(runtime.get("rust_server_remote_verified") is False, "current state server remote claim")


def main() -> int:
    try:
        evidence, accepted = validate_evidence()
        gaps = validate_gaps(evidence, accepted)
        validate_execution(gaps)
        validate_roadmap(gaps)
        validate_gates(gaps)
        validate_risks(gaps)
        validate_divergences(gaps)
        validate_inventory(gaps, evidence, accepted)
        validate_server_status(gaps)
        validate_current_state(gaps)
    except ValidationError as exc:
        print(f"status validation failed: {exc}", file=sys.stderr)
        return 1
    print("TrillionniumGame v3 execution/status contract: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
