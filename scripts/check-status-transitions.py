#!/usr/bin/env python3
"""Validate mutable execution, gap, evidence, gate and roadmap state."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import importlib.util
import re
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

_SPEC = importlib.util.spec_from_file_location(
    "trnm_evidence_admission_" + Path(__file__).stem.replace("-", "_"),
    Path(__file__).with_name("evidence_admission.py"),
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("cannot load shared evidence admission contract")
EVIDENCE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = EVIDENCE
_SPEC.loader.exec_module(EVIDENCE)

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
BACKLOG_PATH = "docs/development/backlog/EXECUTION_BACKLOG.v2.json.gz"
BACKLOG_SHA256 = "6a3b94c1c76a44b31966e2d5919aa3c5ebc87822fc6169377b174a4a3a50c114"
MAX_BACKLOG_BYTES = 2 * 1024 * 1024
PRODUCT_GATE_SCOPE_KEYS = (
    "id",
    "owner",
    "depends_on",
    "stage_gates",
    "blocking_gap_ids",
    "pass_criteria",
    "evidence_types",
    "freshness_days",
    "blocked_claims",
)
PRODUCT_GATE_SCOPE_SHA256 = "6393058c799a5efd24309aaedc5fa84f25ae972fab3d3cced61ce0c080eba7eb"
ROADMAP_SCOPE_SHA256 = "dc7af646e78d3beb976b78e2a7a8787b8f4a5139d65c996b296dfef0e9060678"
ROADMAP_MUTABLE_ROOT_FIELDS = {"status", "updated_at", "items", "acceptance_target"}
ROADMAP_MUTABLE_ITEM_FIELDS = {"status", "evidence_ids", "acceptance_target"}


class ValidationError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise ValidationError(message)


def load_json(path: str) -> dict[str, Any]:
    try:
        return EVIDENCE.load_object(ROOT / path)
    except (OSError, ValueError, RecursionError) as error:
        raise ValidationError(f"invalid control JSON: {error}") from error


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def unique_rows(rows: list[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    require(isinstance(rows, list), f"{label}: rows must be an array")
    values: dict[str, dict[str, Any]] = {}
    for row in rows:
        require(isinstance(row, dict), f"{label}: row must be an object")
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
    return EVIDENCE.entry_eligible(row, root=ROOT)


def validate_evidence() -> tuple[dict[str, dict[str, Any]], set[str]]:
    index = load_json("docs/evidence/index.json")
    require(index.get("schema") == "trillionnium.evidence-index.v1", "evidence index schema")
    try:
        entries = {row["evidence_id"]: row for row in EVIDENCE.index_rows(index)}
    except EVIDENCE.AdmissionError as error:
        raise ValidationError(str(error)) from error
    accepted = {evidence_id for evidence_id, row in entries.items() if evidence_is_accepted(row)}
    require(index.get("accepted_entry_count") == len(accepted), "accepted evidence count mismatch")
    for evidence_id, row in entries.items():
        require(isinstance(row.get("claim_ids"), list), f"{evidence_id}: claim_ids")
        require(isinstance(row.get("gate_ids"), list), f"{evidence_id}: gate_ids")
        require(isinstance(row.get("task_ids"), list), f"{evidence_id}: task_ids")
        require(isinstance(row.get("parity_ids"), list), f"{evidence_id}: parity_ids")
        if row.get("status") == "accepted" or row.get("compatibility_credit") is True:
            require(evidence_id in accepted, f"{evidence_id}: credit requires accepted evidence")
    return entries, accepted


def validate_gaps(
    evidence: dict[str, dict[str, Any]], accepted_evidence: set[str]
) -> dict[str, dict[str, Any]]:
    register = load_json("docs/status/GAP_REGISTER.json")
    try:
        EVIDENCE.validate_gap_scope(register)
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        raise ValidationError(str(error)) from error
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
            try:
                EVIDENCE.validate_gap_evidence(row, evidence, root=ROOT)
            except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
                raise ValidationError(f"{gap_id}: {error}") from error
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
    require(state.get("default_task_state") == "planned", "default task state must remain planned")
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


def load_roadmap_acceptance_scope() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Bind the active milestone requirements while permitting status/evidence updates."""
    roadmap = load_json("docs/roadmap/NEXT_MILESTONE.json")
    require(roadmap.get("schema") == "trillionnium.next-milestone.v1",
            "roadmap acceptance schema")
    require(roadmap.get("project_id") == "trillionnium-game",
            "roadmap acceptance project")
    require(roadmap.get("plan_version") == 3, "roadmap acceptance plan version")
    items = unique_rows(roadmap.get("items", []), "id", "roadmap acceptance items")
    root_scope = {
        key: value for key, value in roadmap.items()
        if key not in ROADMAP_MUTABLE_ROOT_FIELDS
    }
    item_scope = {
        item_id: {
            key: value for key, value in row.items()
            if key not in ROADMAP_MUTABLE_ITEM_FIELDS
        }
        for item_id, row in items.items()
    }
    projection = {"root": root_scope, "items": list(item_scope.values())}
    encoded = json.dumps(projection, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    require(hashlib.sha256(encoded).hexdigest() == ROADMAP_SCOPE_SHA256,
            "immutable roadmap acceptance scope digest drift")
    require(len(items) == 12, "roadmap acceptance item count changed")
    graph: dict[str, list[str]] = {}
    for item_id, row in item_scope.items():
        require(re.fullmatch(r"TG-V3-\d{3}", item_id) is not None,
                f"{item_id}: invalid roadmap acceptance ID")
        graph[item_id] = reference_list(row.get("depends_on", []),
                                        f"{item_id}: immutable depends_on")
        reference_list(row.get("gap_ids"), f"{item_id}: immutable gap_ids",
                       nonempty=True)
        required = reference_list(row.get("required_evidence"),
                                  f"{item_id}: immutable required_evidence", nonempty=True)
        require(set(required) <= EVIDENCE.EVIDENCE_TYPES,
                f"{item_id}: immutable roadmap evidence type")
    require_dag(graph, "roadmap acceptance scope")
    return roadmap, item_scope


def validate_roadmap(gaps: dict[str, dict[str, Any]]) -> None:
    roadmap, _ = load_roadmap_acceptance_scope()
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



def reference_list(value: Any, label: str, *, nonempty: bool = False) -> list[str]:
    require(isinstance(value, list), f"{label}: array required")
    require(not nonempty or bool(value), f"{label}: nonempty array required")
    require(all(EVIDENCE.canonical_text(item) for item in value), f"{label}: invalid reference")
    require(len(value) == len(set(value)), f"{label}: duplicate reference")
    return value


def acceptance_target(row: dict[str, Any]) -> tuple[str, str, str]:
    target = row.get("acceptance_target")
    require(isinstance(target, dict) and set(target) == {"repository", "commit", "tree"},
            f"{row.get('id', 'milestone')}: exact acceptance_target required")
    try:
        return EVIDENCE.target_identity({"target": target})
    except ValueError as error:
        raise ValidationError(f"invalid acceptance target: {error}") from error


def require_closed_gaps(row: dict[str, Any], key: str,
                        gaps: dict[str, dict[str, Any]]) -> None:
    ids = reference_list(row.get(key, []), f"{row.get('id')}: {key}")
    require(set(ids) <= set(gaps), f"{row.get('id')}: unknown blocking gap")
    require(all(gaps[item].get("status") == "closed" for item in ids),
            f"{row.get('id')}: accepted state has an open blocking gap")


def validate_accepted_task(
    row: dict[str, Any], gap_key: str, dependencies: list[str],
    task_rows: dict[str, dict[str, Any]], gaps: dict[str, dict[str, Any]],
    evidence: dict[str, dict[str, Any]], accepted: set[str],
) -> tuple[str, str, str] | None:
    if row.get("status") != "accepted":
        return None
    task_id = row["id"]
    require_closed_gaps(row, gap_key, gaps)
    target = acceptance_target(row)
    for dependency in dependencies:
        require(task_rows[dependency].get("status") == "accepted",
                f"{task_id}: dependency {dependency} is not accepted")
        require(acceptance_target(task_rows[dependency]) == target,
                f"{task_id}: dependency evidence belongs to another candidate")
    required = reference_list(row.get("required_evidence"),
                              f"{task_id}: required_evidence", nonempty=True)
    require(set(required) <= EVIDENCE.EVIDENCE_TYPES, f"{task_id}: unsupported evidence type")
    ids = reference_list(row.get("evidence_ids"), f"{task_id}: evidence_ids", nonempty=True)
    require(set(ids) <= set(evidence) and set(ids) <= accepted,
            f"{task_id}: accepted task requires accepted indexed evidence")
    present = set()
    for evidence_id in ids:
        entry = evidence[evidence_id]
        # Reuse retained-byte, schema, review and expiry validation. A caller's
        # accepted-ID set or a copied status string is not an admission token.
        try:
            EVIDENCE.validate_entry(entry, root=ROOT)
            require(EVIDENCE.target_identity(entry) == target,
                    f"{task_id}: evidence target differs from acceptance target")
        except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
            raise ValidationError(f"{task_id}: invalid retained evidence: {error}") from error
        require(task_id in entry.get("task_ids", []), f"{task_id}: evidence is not mapped to this task")
        present.add(entry["evidence_type"])
    require(set(required) <= present, f"{task_id}: required evidence types are missing")
    return target


def load_acceptance_scope() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Read the unchanged approved task/gate membership, not a mutable roll-up."""
    state = load_json("docs/status/EXECUTION_STATUS.json")
    index = load_json("docs/development/EXECUTION_BACKLOG.json")
    authority = state.get("base_backlog", {})
    artifact = index.get("full_backlog_artifact", {})
    require(authority.get("artifact") == BACKLOG_PATH and artifact.get("path") == BACKLOG_PATH,
            "acceptance backlog path differs from approved scope")
    require(authority.get("artifact_sha256") == BACKLOG_SHA256
            and artifact.get("sha256") == BACKLOG_SHA256, "acceptance backlog digest drift")
    # Fixed, digest-bound local bytes; neither URLs nor alternate scope paths are
    # followed. Bound decompression separately from the compressed input.
    with (ROOT / BACKLOG_PATH).open("rb") as handle:
        compressed = handle.read(MAX_BACKLOG_BYTES + 1)
    require(len(compressed) <= MAX_BACKLOG_BYTES, "compressed acceptance backlog too large")
    require(hashlib.sha256(compressed).hexdigest() == BACKLOG_SHA256,
            "acceptance backlog bytes changed")
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as handle:
        raw = handle.read(MAX_BACKLOG_BYTES + 1)
    require(len(raw) <= MAX_BACKLOG_BYTES, "expanded acceptance backlog too large")
    backlog = json.loads(raw)
    require(backlog.get("schema") == "trillionnium.execution-backlog.v2", "acceptance backlog schema")
    streams = unique_rows(backlog.get("workstreams"), "id", "scope workstreams")
    stages = unique_rows(backlog.get("stage_gates"), "id", "scope stages")
    tasks = unique_rows([task for stream in streams.values() for task in stream["tasks"]],
                        "id", "scope tasks")
    require(len(tasks) == 120 and len(streams) == 17 and len(stages) == 10,
            "acceptance scope membership changed")
    require_dag({key: reference_list(row.get("depends_on"), key) for key, row in tasks.items()},
                "acceptance task scope")
    return tasks, streams, stages


def load_product_gate_scope() -> dict[str, dict[str, Any]]:
    """Bind immutable gate requirements separately from mutable gate status."""
    document = load_json("docs/status/PRODUCT_GATES.json")
    require(document.get("schema") == "trillionnium.product-gates.v3",
            "acceptance product gate schema")
    gates = unique_rows(document.get("gates", []), "id", "acceptance product gates")
    require(len(gates) == 15, "acceptance product gate count changed")
    projection: list[dict[str, Any]] = []
    for gate_id, row in gates.items():
        require(set(PRODUCT_GATE_SCOPE_KEYS) <= set(row),
                f"{gate_id}: incomplete immutable gate scope")
        for key in ("depends_on", "stage_gates", "blocking_gap_ids", "pass_criteria",
                    "evidence_types", "blocked_claims"):
            reference_list(row.get(key), f"{gate_id}: {key}", nonempty=key not in {"depends_on"})
        freshness = row.get("freshness_days")
        require(freshness is None or (isinstance(freshness, int)
                and not isinstance(freshness, bool) and freshness > 0),
                f"{gate_id}: freshness_days")
        projection.append({key: row[key] for key in PRODUCT_GATE_SCOPE_KEYS})
    encoded = json.dumps(projection, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    require(hashlib.sha256(encoded).hexdigest() == PRODUCT_GATE_SCOPE_SHA256,
            "immutable product gate scope digest drift")
    require_dag({gate_id: row["depends_on"] for gate_id, row in gates.items()},
                "acceptance product gate scope")
    return gates


def canonical_task_requirements(
    task_id: str, scope: dict[str, Any], gate_scope: dict[str, dict[str, Any]],
) -> tuple[list[str], list[str], list[str]]:
    """Derive exact task requirements from its digest-bound backlog gate scope."""
    gate_ids = reference_list(scope.get("gate_ids"), f"{task_id}: immutable gate_ids",
                              nonempty=True)
    require(set(gate_ids) <= set(gate_scope), f"{task_id}: unknown immutable gate")
    gaps: set[str] = set()
    evidence_types: set[str] = set()
    for gate_id in gate_ids:
        gate = gate_scope[gate_id]
        gaps.update(gate["blocking_gap_ids"])
        evidence_types.update(gate["evidence_types"])
    require(bool(gaps), f"{task_id}: immutable task has no gate blockers")
    require(bool(evidence_types) and evidence_types <= EVIDENCE.EVIDENCE_TYPES,
            f"{task_id}: immutable task has unsupported gate evidence")
    return gate_ids, sorted(gaps), sorted(evidence_types)


def validate_backlog_task_acceptance(
    row: dict[str, Any], scope: dict[str, Any], dependencies: list[str],
    task_rows: dict[str, dict[str, Any]], gaps: dict[str, dict[str, Any]],
    evidence: dict[str, dict[str, Any]], accepted: set[str],
    gate_scope: dict[str, dict[str, Any]], products: dict[str, Any],
) -> tuple[str, str, str] | None:
    """Prevent a mutable task override from shrinking its immutable gate contract."""
    if row.get("status") != "accepted":
        return None
    task_id = row["id"]
    gate_ids, required_gaps, required_types = canonical_task_requirements(
        task_id, scope, gate_scope
    )
    declared_gates = reference_list(row.get("gate_ids"), f"{task_id}: gate_ids",
                                    nonempty=True)
    declared_gaps = reference_list(row.get("blocking_gaps"),
                                   f"{task_id}: blocking_gaps", nonempty=True)
    declared_types = reference_list(row.get("required_evidence"),
                                    f"{task_id}: required_evidence", nonempty=True)
    require(set(declared_gates) == set(gate_ids),
            f"{task_id}: mutable override differs from immutable gate_ids")
    require(set(declared_gaps) == set(required_gaps),
            f"{task_id}: mutable override differs from canonical blocking gaps")
    require(set(declared_types) == set(required_types),
            f"{task_id}: mutable override differs from canonical evidence types")
    target = validate_accepted_task(
        row, "blocking_gaps", dependencies, task_rows, gaps, evidence, accepted
    )
    require(target is not None, f"{task_id}: accepted target missing")
    ids = reference_list(row.get("evidence_ids"), f"{task_id}: evidence_ids",
                         nonempty=True)
    for gate_id in gate_ids:
        product = products[gate_id]
        require(product["status"] == "passed",
                f"{task_id}: required product gate {gate_id} is not passed")
        gate_evidence_ids = product["accepted_evidence_ids"]
        require(bool(gate_evidence_ids),
                f"{task_id}: required product gate {gate_id} has no accepted evidence")
        require(all(EVIDENCE.target_identity(evidence[item]) == target
                    for item in gate_evidence_ids),
                f"{task_id}: product gate {gate_id} targets another candidate")
        task_gate_entries = [
            evidence[item] for item in ids
            if gate_id in evidence[item].get("gate_ids", [])
            and task_id in evidence[item].get("task_ids", [])
        ]
        present = {entry["evidence_type"] for entry in task_gate_entries}
        require(set(gate_scope[gate_id]["evidence_types"]) <= present,
                f"{task_id}: task evidence does not cover product gate {gate_id}")
    return target


def validate_roadmap_task_acceptance(
    row: dict[str, Any], scope: dict[str, Any], task_rows: dict[str, dict[str, Any]],
    gaps: dict[str, dict[str, Any]], evidence: dict[str, dict[str, Any]],
    accepted: set[str],
) -> tuple[str, str, str] | None:
    """Prevent a status update from shrinking the current milestone contract."""
    if row.get("status") != "accepted":
        return None
    task_id = row["id"]
    dependencies = reference_list(scope.get("depends_on", []),
                                  f"{task_id}: immutable depends_on")
    declared_dependencies = reference_list(row.get("depends_on", []),
                                           f"{task_id}: depends_on")
    canonical_gaps = reference_list(scope.get("gap_ids"),
                                    f"{task_id}: immutable gap_ids", nonempty=True)
    declared_gaps = reference_list(row.get("gap_ids"), f"{task_id}: gap_ids",
                                   nonempty=True)
    canonical_types = reference_list(scope.get("required_evidence"),
                                     f"{task_id}: immutable required_evidence", nonempty=True)
    declared_types = reference_list(row.get("required_evidence"),
                                    f"{task_id}: required_evidence", nonempty=True)
    require(declared_dependencies == dependencies,
            f"{task_id}: mutable roadmap item differs from immutable dependencies")
    require(set(declared_gaps) == set(canonical_gaps),
            f"{task_id}: mutable roadmap item differs from canonical gaps")
    require(set(declared_types) == set(canonical_types),
            f"{task_id}: mutable roadmap item differs from canonical evidence types")
    return validate_accepted_task(
        row, "gap_ids", dependencies, task_rows, gaps, evidence, accepted
    )


def derived_product_gates() -> dict[str, Any]:
    """Use the existing derivation, including its retained evidence admission."""
    spec = importlib.util.spec_from_file_location(
        "trnm_status_derived_gates", Path(__file__).with_name("derive-gates.py")
    )
    if spec is None or spec.loader is None:
        raise ValidationError("product gate derivation unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = ROOT
    try:
        result = module.derive()
        module.check_snapshot(result)
        return result["gates"]
    except module.DerivationError as error:
        raise ValidationError(f"product gate derivation failed: {error}") from error


def validate_execution_acceptance(
    gaps: dict[str, dict[str, Any]], evidence: dict[str, dict[str, Any]], accepted: set[str],
) -> None:
    """Validate present acceptance claims, not past Git transition history."""
    scope_tasks, scope_streams, scope_stages = load_acceptance_scope()
    gate_scope = load_product_gate_scope()
    products = derived_product_gates()
    roadmap, roadmap_scope = load_roadmap_acceptance_scope()
    state = load_json("docs/status/EXECUTION_STATUS.json")
    overrides = unique_rows(state.get("task_overrides", []), "id", "task overrides")
    require(set(overrides) <= set(scope_tasks), "task override is outside approved backlog")
    tasks = {key: {"id": key, "status": "planned"} for key in scope_tasks}
    tasks.update(overrides)
    for task_id, row in overrides.items():
        dependencies = scope_tasks[task_id]["depends_on"]
        require("depends_on" not in row or row["depends_on"] == dependencies,
                f"{task_id}: mutable override cannot replace scope dependencies")
        validate_backlog_task_acceptance(
            row, scope_tasks[task_id], dependencies, tasks, gaps, evidence, accepted,
            gate_scope, products,
        )

    streams = unique_rows(state.get("workstreams"), "id", "workstreams")
    for stream_id, row in streams.items():
        if row.get("status") != "accepted":
            continue
        require_closed_gaps(row, "blocking_gaps", gaps)
        target = acceptance_target(row)
        members = [tasks[task["id"]] for task in scope_streams[stream_id]["tasks"]]
        require(all(task.get("status") == "accepted" for task in members),
                f"{stream_id}: workstream has unaccepted tasks")
        require(all(acceptance_target(task) == target for task in members),
                f"{stream_id}: workstream mixes candidate identities")
        for dependency in scope_streams[stream_id].get("depends_on_workstreams", []):
            require(streams[dependency].get("status") == "accepted"
                    and acceptance_target(streams[dependency]) == target,
                    f"{stream_id}: workstream dependency is not accepted for this candidate")

    stages = unique_rows(state.get("stage_gates"), "id", "stage gates")
    for stage_id, row in stages.items():
        if row.get("status") != "accepted":
            continue
        require_closed_gaps(row, "blocking_gaps", gaps)
        target = acceptance_target(row)
        for gate_id in scope_stages[stage_id]["required_gates"]:
            gate = products[gate_id]
            require(gate["status"] == "passed", f"{stage_id}: product gate {gate_id} is not passed")
            ids = gate["accepted_evidence_ids"]
            require(bool(ids) and all(EVIDENCE.target_identity(evidence[item]) == target for item in ids),
                    f"{stage_id}: product evidence targets another candidate")

    items = unique_rows(roadmap.get("items"), "id", "roadmap items")
    require(set(items) == set(roadmap_scope), "roadmap acceptance scope membership changed")
    for item_id, row in items.items():
        validate_roadmap_task_acceptance(
            row, roadmap_scope[item_id], items, gaps, evidence, accepted
        )
    require(roadmap.get("status") in TASK_STATES, "milestone status")
    if roadmap.get("status") == "accepted":
        require(bool(items) and all(row.get("status") == "accepted" for row in items.values()),
                "accepted milestone contains unaccepted items")
        target = acceptance_target(roadmap)
        require(all(acceptance_target(row) == target for row in items.values()),
                "accepted milestone mixes candidate identities")


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
    require(
        server.get("status") == "http-websocket-source-candidate",
        "server inventory stage",
    )
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
        document.get("stage")
        in {
            "http-websocket-database-vertical-source-candidate",
            "http-websocket-session-database-vertical-source-candidate",
        },
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
        "websocket_wire_compatible",
        "websocket_protobuf_implemented",
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
        validate_execution_acceptance(gaps, evidence, accepted)
    except (ValidationError, OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        print(f"status validation failed: {exc}", file=sys.stderr)
        return 1
    print("TrillionniumGame v3 execution/status contract: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
