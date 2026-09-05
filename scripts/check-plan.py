#!/usr/bin/env python3
"""Validate the TrillionniumGame plan-v3 execution control plane offline."""
from __future__ import annotations

import gzip
import hashlib
import json
import re
import subprocess
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
NAKAMA_COMMIT = "d4d92f93f78bbbe62c7fc50a3f85c772ec121a09"
NAKAMA_TREE = "f3c9cfc2726d5543da1564629170f35b98e3797d"
COMMON_COMMIT = "449b77ecc8789aa466c36b67f6e498033dfcd9c5"
COMMON_TREE = "c6a7b9796b9c2a6b5118c74e5f213963a5001f14"
EXPECTED_DENOMINATORS = {
    "DEN-SOURCE",
    "DEN-API",
    "DEN-RTAPI",
    "DEN-CONSOLE",
    "DEN-RUNTIME",
    "DEN-CONFIG",
    "DEN-CLI",
    "DEN-DB",
    "DEN-DATA",
    "DEN-METRICS",
    "DEN-OPS",
    "DEN-PROVIDERS",
    "DEN-IAP",
    "DEN-SDK",
}
CURRENT_HUMAN_DOCUMENTS = {
    "docs/README.md",
    "docs/ARCHITECTURE.md",
    "docs/DEVELOPMENT.md",
    "docs/COMPATIBILITY.md",
    "docs/TESTING_AND_EVIDENCE.md",
    "docs/SECURITY_AND_PRIVACY.md",
    "docs/OPERATIONS_AND_RELEASE.md",
    "docs/GOVERNANCE.md",
    "docs/ROADMAP.md",
}
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


class ValidationError(RuntimeError):
    """Raised when plan or control-plane authority is inconsistent."""


def fail(message: str) -> None:
    raise ValidationError(message)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        fail(f"{label}: expected {expected!r}, got {actual!r}")


def load_json(path: str) -> dict[str, Any]:
    try:
        value = json.loads((ROOT / path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"{path}: invalid JSON: {exc}")
    if not isinstance(value, dict):
        fail(f"{path}: top-level value must be an object")
    return value


def validate_files() -> None:
    required = [
        "README.md",
        "CURRENT_PLAN.md",
        "PROJECT_ID",
        "PROJECT_BOUNDARY.md",
        "PROJECT_BOUNDARY.json",
        "LICENSE",
        "NOTICE",
        "AGENTS.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        ".github/CODEOWNERS",
        ".github/workflows/trillionnium-game-merge-gate.yml",
        "docs/DOCUMENTATION_AUTHORITY.json",
        *sorted(CURRENT_HUMAN_DOCUMENTS),
        "docs/development/PARITY_DENOMINATORS.json",
        "docs/development/FEATURE_PARITY_MATRIX.md",
        "docs/development/COMPATIBILITY_PROFILES.json",
        "docs/development/COMPATIBILITY_DIVERGENCES.json",
        "docs/development/DENOMINATOR_CLASSIFICATION_RULES.json",
        "docs/development/SCHEMA_AUTHORITY.json",
        "docs/development/RUST_PACKAGE_AUTHORITY.json",
        "docs/development/EXECUTION_BACKLOG.json",
        "docs/development/backlog/EXECUTION_BACKLOG.v2.json.gz",
        "docs/development/UPSTREAM_BASELINE.json",
        "docs/evidence/index.json",
        "docs/evidence/schemas/trillionnium-evidence-v1.schema.json",
        "docs/status/CURRENT_STATE.json",
        "docs/status/EXECUTION_STATUS.json",
        "docs/status/GAP_REGISTER.json",
        "docs/status/IMPLEMENTATION_INVENTORY.json",
        "docs/status/PRODUCT_GATES.json",
        "docs/status/RISK_REGISTER.json",
        "docs/status/SERVICE_LEVEL_OBJECTIVES.json",
        "docs/roadmap/NEXT_MILESTONE.json",
        "docs/review/INDEPENDENT_REVIEW_MATRIX.json",
        "docs/governance/GITHUB_ADMIN_ACCEPTANCE.json",
        "docs/governance/MAIN_RULESET_DESIRED.json",
        "docs/governance/REQUIRED_CHECKS.json",
        "docs/governance/RULESET_DESIRED_ACTIVE_REQUEST.json",
        "database/schema/v2/STATUS.json",
        "database/schema/v2/README.md",
        "scripts/check-documentation-authority.py",
        "scripts/check-status-transitions.py",
        "scripts/derive-gates.py",
        "scripts/check-schema-authority.py",
        "scripts/check-rust-package-inventory.py",
        "scripts/read-backlog.py",
    ]
    missing = [path for path in required if not (ROOT / path).is_file()]
    require(not missing, "missing required files: " + ", ".join(missing))
    equal(
        (ROOT / "PROJECT_ID").read_text(encoding="utf-8").strip(),
        "trillionnium-game",
        "PROJECT_ID",
    )


def validate_documentation_authority() -> None:
    authority = load_json("docs/DOCUMENTATION_AUTHORITY.json")
    equal(
        authority.get("schema"),
        "trillionnium.documentation-authority.v1",
        "documentation authority schema",
    )
    equal(authority.get("project_id"), "trillionnium-game", "documentation project_id")
    equal(authority.get("plan_version"), 3, "documentation plan version")
    equal(authority.get("revision"), "2026-09-01", "documentation revision")
    equal(
        set(authority.get("current_human_documents", [])),
        CURRENT_HUMAN_DOCUMENTS,
        "current human documentation",
    )
    policy = authority.get("policy", {})
    equal(policy.get("single_current_human_document_per_topic"), True, "single current doc policy")
    equal(policy.get("historical_markdown_allowed_in_active_tree"), False, "historical Markdown policy")
    equal(policy.get("git_history_is_the_human_document_archive"), True, "history archive policy")
    equal(policy.get("broken_repository_document_references_allowed"), False, "broken reference policy")
    claims = authority.get("claims", {})
    equal(claims.get("documentation_consolidated"), True, "documentation consolidated")
    equal(claims.get("historical_human_docs_removed_from_active_tree"), True, "historical docs removed")
    equal(claims.get("machine_evidence_deleted"), False, "machine evidence retention")
    for field in ("compatibility_credit", "production_ready", "public_online", "nakama_retired"):
        equal(claims.get(field), False, f"documentation claim {field}")


def validate_boundary() -> None:
    boundary = load_json("PROJECT_BOUNDARY.json")
    equal(boundary.get("schema"), "trillionnium.project-boundary.v2", "boundary schema")
    equal(boundary.get("project_id"), "trillionnium-game", "boundary project_id")
    equal(
        boundary.get("current_repository"),
        "TrillionniumFoundation/TrillionniumGame",
        "current repository",
    )
    equal(
        boundary.get("target_repository"),
        "TrillionniumFoundation/TrillionniumGame",
        "target repository",
    )
    equal(boundary.get("repository_id"), 1323087470, "repository ID")
    equal(
        boundary.get("scope", {}).get("nakama_oss_full_reimplementation"),
        True,
        "full scope",
    )
    equal(
        boundary.get("scope", {}).get("parity_source_of_truth"),
        "generated-leaf-denominators",
        "parity source",
    )
    for field in (
        "go_server_allowed",
        "go_sidecar_allowed",
        "compiled_go_plugin_loader_allowed",
    ):
        equal(boundary.get("language_policy", {}).get(field), False, field)
    require(
        boundary.get("claims", {}).get("current_level") in {"C0-planning", "C0"},
        "current claim level",
    )


def validate_upstream() -> None:
    baseline = load_json("docs/development/UPSTREAM_BASELINE.json")
    equal(baseline.get("schema"), "trillionnium.upstream-baseline.v2", "baseline schema")
    nakama = baseline["nakama"]
    common = baseline["nakama_common"]
    equal(
        (nakama["tag"], nakama["commit"], nakama["tree"]),
        ("v3.40.0", NAKAMA_COMMIT, NAKAMA_TREE),
        "Nakama identity",
    )
    equal(
        (common["tag"], common["commit"], common["tree"]),
        ("v1.47.0", COMMON_COMMIT, COMMON_TREE),
        "nakama-common identity",
    )
    expected_nakama = {
        "apigrpc/apigrpc.proto": "1cc63aae1aaa5dc56ede9c9d0b6f9a95ff91361c",
        "apigrpc/apigrpc.swagger.json": "17dc459faa529b39278fead44fb4abafe786ccd9",
        "console/console.proto": "1f7ccf8e6dae3bc4c6c239ada23b1104002b917e",
        "console/console.swagger.json": "8a51cb1e449a6c9392a162c92edd140e5d1aec04",
        "console/api.swagger.json": "c8cf70d4b76af614f93a0683a3f0eb7a699674bb",
    }
    actual_nakama = {
        row["path"]: row.get("blob") for row in nakama.get("protocol_contracts", [])
    }
    for path, sha in expected_nakama.items():
        equal(actual_nakama.get(path), sha, f"protocol blob {path}")
    expected_common = {
        "api/api.proto": "ddd2744739a252c268b2be004ff0e45c498adb35",
        "rtapi/realtime.proto": "b23efef88565e0e09b3f6ee7ed8e08e9d240e27d",
        "runtime/runtime.go": "da7f2f2ad41ef5061d48f2e037678bb8397cc045",
        "runtime/config.go": "5c0cc9b8b3a6d652ca6c40e030a6f90278e1bd7c",
        "index.d.ts": "83a4c5fe0b87b2e4126623c8e9b86fe34d25bb2e",
    }
    actual_common = {
        row["path"]: row.get("blob") for row in common.get("protocol_contracts", [])
    }
    for path, sha in expected_common.items():
        equal(actual_common.get(path), sha, f"common blob {path}")


def validate_plan_and_parity() -> tuple[set[str], set[str]]:
    text = (ROOT / "CURRENT_PLAN.md").read_text(encoding="utf-8")
    for marker in (
        "开发计划 v3.1",
        "P50 48",
        "P80 60",
        "C0",
        "C5",
        "SG0",
        "SG9",
        "D0",
        "D8",
        "Definition of Ready",
        "Definition of Done",
        "Gap closure definition",
        "docs/DOCUMENTATION_AUTHORITY.json",
        "历史信息只保留在 Git 历史",
        NAKAMA_COMMIT,
    ):
        require(marker in text, f"plan missing marker: {marker}")
    equal(
        re.findall(r"^### W(\d+)\b", text, re.MULTILINE),
        [str(index) for index in range(17)],
        "workstream order",
    )
    equal(
        re.findall(r"^- SG(\d+)：", text, re.MULTILINE),
        [str(index) for index in range(10)],
        "stage gate order",
    )

    lines = (
        ROOT / "docs/development/FEATURE_PARITY_MATRIX.md"
    ).read_text(encoding="utf-8").splitlines()
    rows = [line for line in lines if line.startswith("| TG-PAR-")]
    require(len(rows) >= 74, "parity roll-up shrank below 74")
    parity = [line.split("|")[1].strip() for line in rows]
    require(len(parity) == len(set(parity)), "duplicate parity IDs")

    registry = load_json("docs/development/PARITY_DENOMINATORS.json")
    denominator_rows = registry.get("denominators", [])
    equal(
        {row.get("id") for row in denominator_rows},
        EXPECTED_DENOMINATORS,
        "denominator IDs",
    )
    for row in denominator_rows:
        require(
            row.get("unclassified_allowed") is False,
            f"{row.get('id')}: unclassified policy",
        )
        require(bool(row.get("extractor_task")), f"{row.get('id')}: extractor")
        require(bool(row.get("output")), f"{row.get('id')}: output")
        require(bool(row.get("layer")), f"{row.get('id')}: layer")
    equal(
        registry.get("coverage_policy", {}).get("markdown_rollup_is_denominator"),
        False,
        "Markdown roll-up policy",
    )

    profiles = load_json("docs/development/COMPATIBILITY_PROFILES.json")
    equal(
        [row["id"] for row in profiles.get("claim_levels", [])],
        [f"C{i}" for i in range(6)],
        "claim levels",
    )
    require(isinstance(profiles.get("current_level"), str), "compatibility current level")
    gates = load_json("docs/status/PRODUCT_GATES.json")
    gate_ids = [row.get("id") for row in gates.get("gates", [])]
    require(
        len(gate_ids) == 15 and len(gate_ids) == len(set(gate_ids)),
        "product gate IDs",
    )
    return set(parity), set(gate_ids)


def validate_backlog(parity_ids: set[str], gate_ids: set[str]) -> None:
    index = load_json("docs/development/EXECUTION_BACKLOG.json")
    equal(index.get("task_count"), 120, "task count")
    equal(
        sum(row["task_count"] for row in index.get("workstreams", [])),
        120,
        "workstream total",
    )
    artifact = index["full_backlog_artifact"]
    path = ROOT / artifact["path"]
    equal(hashlib.sha256(path.read_bytes()).hexdigest(), artifact["sha256"], "backlog SHA")
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        backlog = json.load(handle)
    equal(backlog.get("schema"), "trillionnium.execution-backlog.v2", "backlog schema")
    workstreams = backlog.get("workstreams", [])
    equal(
        [row["id"] for row in workstreams],
        [f"W{i}" for i in range(17)],
        "detailed workstreams",
    )
    tasks = [task for workstream in workstreams for task in workstream.get("tasks", [])]
    equal(len(tasks), 120, "detailed tasks")
    ids = [task.get("id") for task in tasks]
    require(len(ids) == len(set(ids)), "duplicate task IDs")
    idset = set(ids)
    graph: dict[str, list[str]] = {}
    required = {
        "id",
        "title",
        "priority",
        "status",
        "acceptance",
        "owner_role",
        "review_roles",
        "depends_on",
        "estimate_person_weeks",
        "risk",
        "parity_ids",
        "gate_ids",
        "required_exit_state",
    }
    for task in tasks:
        task_id = task["id"]
        missing = required - task.keys()
        require(not missing, f"{task_id}: missing {sorted(missing)}")
        require(task["status"] in TASK_STATES, f"{task_id}: invalid status")
        require(task["priority"] in {"P0", "P1", "P2"}, f"{task_id}: invalid priority")
        estimate = task["estimate_person_weeks"]
        require(0 < estimate["min"] <= estimate["max"], f"{task_id}: invalid estimate")
        require(not (set(task["depends_on"]) - idset), f"{task_id}: unknown dependency")
        require(not (set(task["gate_ids"]) - gate_ids), f"{task_id}: unknown gate")
        require(not (set(task["parity_ids"]) - parity_ids), f"{task_id}: unknown parity ID")
        graph[task_id] = task["depends_on"]

    indegree = {node: 0 for node in graph}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for node, dependencies in graph.items():
        for dependency in dependencies:
            indegree[node] += 1
            outgoing[dependency].append(node)
    queue = deque(node for node, degree in indegree.items() if degree == 0)
    visited = 0
    while queue:
        node = queue.popleft()
        visited += 1
        for follower in outgoing[node]:
            indegree[follower] -= 1
            if indegree[follower] == 0:
                queue.append(follower)
    require(visited == len(graph), "backlog dependency cycle")


def validate_evidence_schema() -> None:
    schema = load_json("docs/evidence/schemas/trillionnium-evidence-v1.schema.json")
    required = set(schema.get("required", []))
    for field in (
        "evidence_id",
        "upstream",
        "candidate",
        "environment",
        "fixtures",
        "commands",
        "result",
        "artifacts",
        "review",
    ):
        require(field in required, f"evidence schema does not require {field}")


def validate_identity_and_claims() -> None:
    module = (ROOT / "runtime/go.mod").read_text(encoding="utf-8")
    require(
        module.startswith(
            "module github.com/TrillionniumFoundation/TrillionniumGame/runtime\n"
        ),
        "runtime Go module is not canonical",
    )
    current = load_json("docs/status/CURRENT_STATE.json")
    require(current.get("fail_closed") is True, "current state fail_closed")
    claims = current.get("claims", {})
    for field in (
        "wire_compatible",
        "behavior_compatible",
        "data_migration_compatible",
        "operationally_replaceable",
        "supported_full_replacement",
        "production_ready",
        "public_online",
        "nakama_retired",
    ):
        equal(claims.get(field), False, f"current claim {field}")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for marker in (
        "complete Nakama compatibility = false",
        "production-ready = false",
        "public-online = false",
        "drop-in replacement = false",
        "Nakama retired = false",
        "docs/DOCUMENTATION_AUTHORITY.json",
    ):
        require(marker in readme, f"README missing claim/documentation marker: {marker}")


def run_child(path: str) -> None:
    result = subprocess.run(
        [sys.executable, path],
        cwd=ROOT,
        check=False,
        text=True,
    )
    require(result.returncode == 0, f"{path} failed with exit {result.returncode}")


def main() -> int:
    try:
        validate_files()
        validate_documentation_authority()
        validate_boundary()
        validate_upstream()
        parity_ids, gate_ids = validate_plan_and_parity()
        validate_backlog(parity_ids, gate_ids)
        validate_evidence_schema()
        validate_identity_and_claims()
        run_child("scripts/check-documentation-authority.py")
        run_child("scripts/check-status-transitions.py")
        run_child("scripts/derive-gates.py")
        run_child("scripts/check-schema-authority.py")
        run_child("scripts/check-rust-package-inventory.py")
    except ValidationError as exc:
        print(f"plan validation failed: {exc}", file=sys.stderr)
        return 1
    print("TrillionniumGame plan v3.1 control plane: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
