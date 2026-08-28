#!/usr/bin/env python3
"""Validate the audited TrillionniumGame planning baseline without network access."""
from __future__ import annotations

import gzip
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
NAKAMA_COMMIT = "d4d92f93f78bbbe62c7fc50a3f85c772ec121a09"
NAKAMA_TREE = "f3c9cfc2726d5543da1564629170f35b98e3797d"
COMMON_COMMIT = "449b77ecc8789aa466c36b67f6e498033dfcd9c5"
COMMON_TREE = "c6a7b9796b9c2a6b5118c74e5f213963a5001f14"


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


def require(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        fail(f"{label}: expected {expected!r}, got {actual!r}")


def validate_files() -> None:
    required = [
        "README.md", "CURRENT_PLAN.md", "PROJECT_ID", "PROJECT_BOUNDARY.md", "PROJECT_BOUNDARY.json",
        "LICENSE", "NOTICE", "AGENTS.md", "CONTRIBUTING.md",
        "docs/adr/ADR-0001-FULL-RUST-REIMPLEMENTATION.md", "docs/adr/ADR-ROADMAP.md",
        "docs/development/PLAN_AUDIT_2026-08-28.md", "docs/development/PROGRAM_EXECUTION_MODEL.md",
        "docs/development/CRITICAL_PATH_AND_STAGE_GATES.md", "docs/development/PARITY_DENOMINATOR_SPEC.md",
        "docs/development/PARITY_DENOMINATORS.json", "docs/development/FEATURE_PARITY_MATRIX.md",
        "docs/development/COMPATIBILITY_PROFILES.md", "docs/development/COMPATIBILITY_PROFILES.json",
        "docs/development/ORACLE_AND_DIFFERENTIAL_SPEC.md", "docs/development/MIGRATION_AUTHORITY_MATRIX.md",
        "docs/development/DATA_MIGRATION_STATE_MACHINE.md", "docs/development/CAPACITY_AND_SLO_SPEC.md",
        "docs/development/TECHNICAL_SPIKES.md", "docs/development/EVIDENCE_MODEL.md",
        "docs/development/EXECUTION_BACKLOG.json", "docs/development/backlog/EXECUTION_BACKLOG.v2.json.gz",
        "docs/development/UPSTREAM_BASELINE.json", "docs/development/THIRD_PARTY_POLICY.md",
        "docs/development/REPOSITORY_TRANSITION_STATUS.md",
        "docs/evidence/schemas/trillionnium-evidence-v1.schema.json",
        "docs/status/PRODUCT_GATES.json", "docs/status/RISK_REGISTER.json", "docs/status/SERVICE_LEVEL_OBJECTIVES.json",
        "scripts/read-backlog.py"
    ]
    missing = [path for path in required if not (ROOT / path).is_file()]
    if missing:
        fail("missing required files: " + ", ".join(missing))
    require((ROOT / "PROJECT_ID").read_text(encoding="utf-8").strip(), "trillionnium-game", "PROJECT_ID")


def validate_boundary() -> None:
    boundary = load_json("PROJECT_BOUNDARY.json")
    require(boundary.get("schema"), "trillionnium.project-boundary.v2", "boundary schema")
    require(boundary.get("project_id"), "trillionnium-game", "boundary project_id")
    require(boundary.get("current_repository"), "TrillionniumFoundation/Trillionnium-Nakama", "current repository")
    require(boundary.get("target_repository"), "TrillionniumFoundation/TrillionniumGame", "target repository")
    require(boundary.get("repository_id"), 1323087470, "repository ID")
    require(boundary.get("scope", {}).get("nakama_oss_full_reimplementation"), True, "full Nakama OSS scope")
    policy = boundary.get("language_policy", {})
    for field in ("go_server_allowed", "go_sidecar_allowed", "compiled_go_plugin_loader_allowed"):
        require(policy.get(field), False, f"language policy {field}")
    require(boundary.get("claims", {}).get("current_level"), "C0-planning", "current compatibility claim")


def validate_upstream() -> None:
    baseline = load_json("docs/development/UPSTREAM_BASELINE.json")
    require(baseline.get("schema"), "trillionnium.upstream-baseline.v2", "baseline schema")
    nakama = baseline["nakama"]
    common = baseline["nakama_common"]
    require((nakama["tag"], nakama["commit"], nakama["tree"]), ("v3.40.0", NAKAMA_COMMIT, NAKAMA_TREE), "Nakama identity")
    require((common["tag"], common["commit"], common["tree"]), ("v1.47.0", COMMON_COMMIT, COMMON_TREE), "nakama-common identity")
    if len(nakama.get("source_roots", [])) < 10:
        fail("Nakama source roots are incomplete")
    if len(baseline.get("generated_manifests_required", [])) < 12:
        fail("generated denominator manifest set is incomplete")


def validate_plan() -> None:
    text = (ROOT / "CURRENT_PLAN.md").read_text(encoding="utf-8")
    for marker in ("开发计划 v2", "P50 48", "P80 60", "C0", "C5", "SG0", "SG9", "D0", "D8", "Definition of Ready / Done", NAKAMA_COMMIT):
        if marker not in text:
            fail(f"CURRENT_PLAN.md missing marker: {marker}")
    workstreams = re.findall(r"^### W(\d+)\b", text, re.MULTILINE)
    require(workstreams, [str(index) for index in range(17)], "plan workstream order")


def validate_parity_and_profiles() -> set[str]:
    matrix = (ROOT / "docs/development/FEATURE_PARITY_MATRIX.md").read_text(encoding="utf-8")
    rows = [line for line in matrix.splitlines() if line.startswith("| TG-PAR-")]
    if len(rows) < 74:
        fail(f"feature parity roll-up may grow but not shrink below 74; found {len(rows)}")
    ids = [line.split("|")[1].strip() for line in rows]
    if len(ids) != len(set(ids)):
        fail("duplicate feature parity IDs")

    denominators = load_json("docs/development/PARITY_DENOMINATORS.json")
    if len(denominators.get("denominators", [])) < 12:
        fail("parity denominator registry is incomplete")
    for row in denominators["denominators"]:
        if row.get("unclassified_allowed") is not False:
            fail(f"{row.get('id')}: unclassified denominator items must be forbidden")
        if not row.get("extractor_task") or not row.get("output"):
            fail(f"{row.get('id')}: extractor task and output are required")

    profiles = load_json("docs/development/COMPATIBILITY_PROFILES.json")
    require(profiles.get("current_level"), "C0-planning", "profile current level")
    require([row["id"] for row in profiles.get("claim_levels", [])], [f"C{i}" for i in range(6)], "C0-C5 claim levels")
    if any(row.get("status") != "open" for row in profiles["claim_levels"]):
        fail("all compatibility levels must remain open in the planning baseline")
    return set(ids)


def validate_backlog(parity_ids: set[str], gate_ids: set[str]) -> None:
    index = load_json("docs/development/EXECUTION_BACKLOG.json")
    require(index.get("schema"), "trillionnium.execution-backlog-index.v2", "backlog index schema")
    require(index.get("task_count"), 120, "backlog task count")
    require(len(index.get("workstreams", [])), 17, "backlog workstream count")
    require(sum(row["task_count"] for row in index["workstreams"]), 120, "workstream task total")
    artifact = index["full_backlog_artifact"]
    path = ROOT / artifact["path"]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    require(digest, artifact["sha256"], "detailed backlog SHA-256")
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            backlog = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"detailed backlog is invalid: {exc}")
    require(backlog.get("schema"), "trillionnium.execution-backlog.v2", "detailed backlog schema")
    require(backlog.get("plan_version"), 2, "detailed backlog plan version")
    workstreams = backlog.get("workstreams", [])
    require([row["id"] for row in workstreams], [f"W{i}" for i in range(17)], "detailed workstream order")
    tasks = [task for workstream in workstreams for task in workstream.get("tasks", [])]
    require(len(tasks), 120, "detailed task count")
    task_ids = [task.get("id") for task in tasks]
    if len(task_ids) != len(set(task_ids)):
        fail("duplicate task IDs")
    task_id_set = set(task_ids)
    required = {"id", "title", "priority", "status", "acceptance", "owner_role", "review_roles", "depends_on", "estimate_person_weeks", "risk", "parity_ids", "gate_ids", "required_exit_state"}
    for task in tasks:
        missing = sorted(required - task.keys())
        if missing:
            fail(f"{task.get('id')}: missing fields {missing}")
        if task["status"] != "planned" or task["priority"] not in {"P0", "P1", "P2"}:
            fail(f"{task['id']}: invalid initial status or priority")
        estimate = task["estimate_person_weeks"]
        if not (0 < estimate["min"] <= estimate["max"]):
            fail(f"{task['id']}: invalid estimate")
        unknown_dependencies = sorted(set(task["depends_on"]) - task_id_set)
        if unknown_dependencies:
            fail(f"{task['id']}: unknown dependencies {unknown_dependencies}")
        unknown_gates = sorted(set(task["gate_ids"]) - gate_ids)
        if unknown_gates:
            fail(f"{task['id']}: unknown gates {unknown_gates}")
        unknown_parity = sorted(set(task["parity_ids"]) - parity_ids)
        if unknown_parity:
            fail(f"{task['id']}: unknown parity IDs {unknown_parity}")


def validate_gates_risks_slo() -> set[str]:
    gates = load_json("docs/status/PRODUCT_GATES.json")
    require(gates.get("schema"), "trillionnium.product-gates.v2", "gate schema")
    require(gates.get("release_claim"), "planning-only", "release claim")
    require(gates.get("compatibility_level"), "C0-not-earned", "compatibility gate claim")
    rows = gates.get("gates", [])
    if len(rows) < 15:
        fail("product gate set is incomplete")
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)):
        fail("duplicate product gate IDs")
    for row in rows:
        if row.get("status") != "open" or not row.get("owner") or not row.get("pass_criteria") or not row.get("evidence_types"):
            fail(f"{row.get('id')}: gate must be open and fully specified")
    policy = gates.get("gate_policy", {})
    for field in ("exact_commit_artifact_environment_evidence_required", "p0_p1_unexplained_divergence_allowed"):
        if field == "p0_p1_unexplained_divergence_allowed":
            require(policy.get(field), False, field)
        else:
            require(policy.get(field), True, field)

    risks = load_json("docs/status/RISK_REGISTER.json")
    risk_rows = risks.get("risks", [])
    if len(risk_rows) < 25 or len({row["id"] for row in risk_rows}) != len(risk_rows):
        fail("risk register must contain at least 25 unique risks")
    for row in risk_rows:
        for field in ("owner", "risk", "trigger", "mitigation", "contingency"):
            if not row.get(field):
                fail(f"{row.get('id')}: missing {field}")

    slo = load_json("docs/status/SERVICE_LEVEL_OBJECTIVES.json")
    require(slo.get("status"), "provisional-until-oracle-baseline", "SLO status")
    require(slo.get("ratification_task"), "TG-W0-007", "SLO ratification task")
    require(slo.get("integrity", {}).get("acknowledged_durable_writes_lost"), 0, "lost acknowledged writes objective")
    return set(ids)


def validate_evidence_and_transition() -> None:
    schema = load_json("docs/evidence/schemas/trillionnium-evidence-v1.schema.json")
    required = set(schema.get("required", []))
    for field in ("evidence_id", "upstream", "candidate", "environment", "commands", "result", "review"):
        if field not in required:
            fail(f"evidence schema does not require {field}")
    transition = (ROOT / "docs/development/REPOSITORY_TRANSITION_STATUS.md").read_text(encoding="utf-8")
    for marker in ("1323087470", "TrillionniumFoundation/Trillionnium-Nakama", "TrillionniumFoundation/TrillionniumGame", "pending", "Do not delete"):
        if marker not in transition:
            fail(f"repository transition status missing marker: {marker}")


def validate_notice() -> None:
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8").lower()
    for marker in ("nakama oss", "v3.40.0", "apache license", "trademark"):
        if marker not in notice:
            fail(f"NOTICE missing marker: {marker}")


def main() -> int:
    validate_files()
    validate_boundary()
    validate_upstream()
    validate_plan()
    parity_ids = validate_parity_and_profiles()
    gate_ids = validate_gates_risks_slo()
    validate_backlog(parity_ids, gate_ids)
    validate_evidence_and_transition()
    validate_notice()
    print("plan validation passed")
    print("plan=v2 workstreams=17 tasks=120 parity_rollups>=74 denominators=12 gates=15 risks=25 claim=C0 upstream=v3.40.0")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValidationError as exc:
        print(f"plan validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
