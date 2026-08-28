#!/usr/bin/env python3
"""Validate the audited TrillionniumGame v2 planning control plane offline."""
from __future__ import annotations

import gzip
import hashlib
import json
import re
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
NAKAMA_COMMIT = "d4d92f93f78bbbe62c7fc50a3f85c772ec121a09"
NAKAMA_TREE = "f3c9cfc2726d5543da1564629170f35b98e3797d"
COMMON_COMMIT = "449b77ecc8789aa466c36b67f6e498033dfcd9c5"
COMMON_TREE = "c6a7b9796b9c2a6b5118c74e5f213963a5001f14"
EXPECTED_DENOMINATORS = {"DEN-SOURCE","DEN-API","DEN-RTAPI","DEN-CONSOLE","DEN-RUNTIME","DEN-CONFIG","DEN-CLI","DEN-DB","DEN-DATA","DEN-METRICS","DEN-OPS","DEN-PROVIDERS","DEN-IAP","DEN-SDK"}

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
        "README.md","CURRENT_PLAN.md","PROJECT_ID","PROJECT_BOUNDARY.md","PROJECT_BOUNDARY.json","LICENSE","NOTICE","AGENTS.md","CONTRIBUTING.md",
        "docs/adr/ADR-0001-FULL-RUST-REIMPLEMENTATION.md","docs/adr/ADR-ROADMAP.md",
        "docs/development/PLAN_AUDIT_2026-08-28.md","docs/development/AUDIT_CORRECTIONS_2026-08-28.md","docs/development/PROGRAM_EXECUTION_MODEL.md","docs/development/CRITICAL_PATH_AND_STAGE_GATES.md",
        "docs/development/PARITY_DENOMINATOR_SPEC.md","docs/development/PARITY_DENOMINATORS.json","docs/development/FEATURE_PARITY_MATRIX.md",
        "docs/development/COMPATIBILITY_PROFILES.md","docs/development/COMPATIBILITY_PROFILES.json","docs/development/ORACLE_AND_DIFFERENTIAL_SPEC.md",
        "docs/development/MIGRATION_AUTHORITY_MATRIX.md","docs/development/DATA_MIGRATION_STATE_MACHINE.md","docs/development/CAPACITY_AND_SLO_SPEC.md","docs/development/TECHNICAL_SPIKES.md","docs/development/EVIDENCE_MODEL.md",
        "docs/development/EXECUTION_BACKLOG.json","docs/development/backlog/EXECUTION_BACKLOG.v2.json.gz","docs/development/backlog/README.md","docs/development/UPSTREAM_BASELINE.json","docs/development/THIRD_PARTY_POLICY.md",
        "docs/development/REPOSITORY_TRANSITION_STATUS.md","docs/development/REPOSITORY_TRANSITION_RUNBOOK.md",
        "docs/evidence/schemas/trillionnium-evidence-v1.schema.json","docs/status/PRODUCT_GATES.json","docs/status/RISK_REGISTER.json","docs/status/SERVICE_LEVEL_OBJECTIVES.json",
        "scripts/read-backlog.py","scripts/rename-existing-repository.sh","scripts/publish-repository.sh"
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
    require(boundary.get("scope", {}).get("nakama_oss_full_reimplementation"), True, "full scope")
    require(boundary.get("scope", {}).get("parity_source_of_truth"), "generated-leaf-denominators", "parity source")
    for field in ("go_server_allowed","go_sidecar_allowed","compiled_go_plugin_loader_allowed"):
        require(boundary.get("language_policy", {}).get(field), False, field)
    require(boundary.get("claims", {}).get("current_level"), "C0-planning", "current claim")

def validate_upstream() -> None:
    baseline = load_json("docs/development/UPSTREAM_BASELINE.json")
    require(baseline.get("schema"), "trillionnium.upstream-baseline.v2", "baseline schema")
    nakama, common = baseline["nakama"], baseline["nakama_common"]
    require((nakama["tag"],nakama["commit"],nakama["tree"]),("v3.40.0",NAKAMA_COMMIT,NAKAMA_TREE),"Nakama identity")
    require((common["tag"],common["commit"],common["tree"]),("v1.47.0",COMMON_COMMIT,COMMON_TREE),"nakama-common identity")
    expected_nakama = {"apigrpc/apigrpc.proto":"1cc63aae1aaa5dc56ede9c9d0b6f9a95ff91361c","apigrpc/apigrpc.swagger.json":"17dc459faa529b39278fead44fb4abafe786ccd9","console/console.proto":"1f7ccf8e6dae3bc4c6c239ada23b1104002b917e","console/console.swagger.json":"8a51cb1e449a6c9392a162c92edd140e5d1aec04","console/api.swagger.json":"c8cf70d4b76af614f93a0683a3f0eb7a699674bb"}
    actual_nakama = {row["path"]:row.get("blob") for row in nakama.get("protocol_contracts",[])}
    for path, sha in expected_nakama.items(): require(actual_nakama.get(path),sha,f"protocol blob {path}")
    expected_impl = {"server/config.go":("blob","d9cd2b5c1bca3ae13a2560513a8fd99575ec4fe6"),"flags/flags.go":("blob","9c139f4fdb050e6f00a323854e0c88690a8f37ef"),"flags/vars.go":("blob","c5253fb37de1d2ebfb70408c8f78965bf28840a0"),"migrate/migrate.go":("blob","598138cbeb8dd2832f9746aa4cd9826cc0152e96"),"migrate/sql":("tree","1eb2275e187a543b8203b7b809d0d246c4a2bb6e")}
    actual_impl = {row["path"]:row for row in nakama.get("implementation_contracts",[])}
    for path,(kind,sha) in expected_impl.items(): require(actual_impl.get(path,{}).get(kind),sha,f"implementation identity {path}")
    expected_common = {"api/api.proto":"ddd2744739a252c268b2be004ff0e45c498adb35","rtapi/realtime.proto":"b23efef88565e0e09b3f6ee7ed8e08e9d240e27d","runtime/runtime.go":"da7f2f2ad41ef5061d48f2e037678bb8397cc045","runtime/config.go":"5c0cc9b8b3a6d652ca6c40e030a6f90278e1bd7c","index.d.ts":"83a4c5fe0b87b2e4126623c8e9b86fe34d25bb2e"}
    actual_common = {row["path"]:row.get("blob") for row in common.get("protocol_contracts",[])}
    for path, sha in expected_common.items(): require(actual_common.get(path),sha,f"common blob {path}")
    manifests = set(baseline.get("generated_manifests_required",[]))
    if len(manifests) < 14 or {"source","providers","runtime","console"} - manifests: fail("manifest set incomplete")

def validate_plan_and_parity() -> set[str]:
    text = (ROOT / "CURRENT_PLAN.md").read_text(encoding="utf-8")
    for marker in ("开发计划 v2","P50 48","P80 60","C0","C5","SG0","SG9","D0","D8","Definition of Ready / Done",NAKAMA_COMMIT):
        if marker not in text: fail(f"plan missing marker: {marker}")
    require(re.findall(r"^### W(\d+)\b",text,re.MULTILINE),[str(i) for i in range(17)],"workstream order")
    lines = (ROOT / "docs/development/FEATURE_PARITY_MATRIX.md").read_text(encoding="utf-8").splitlines()
    rows = [line for line in lines if line.startswith("| TG-PAR-")]
    if len(rows) < 74: fail("parity roll-up shrank below 74")
    ids = [line.split("|")[1].strip() for line in rows]
    if len(ids) != len(set(ids)): fail("duplicate parity IDs")
    registry = load_json("docs/development/PARITY_DENOMINATORS.json")
    require({row.get("id") for row in registry.get("denominators",[])},EXPECTED_DENOMINATORS,"denominator IDs")
    for row in registry["denominators"]:
        if row.get("unclassified_allowed") is not False or not row.get("extractor_task") or not row.get("output") or not row.get("layer"): fail(f"incomplete denominator {row.get('id')}")
    profiles = load_json("docs/development/COMPATIBILITY_PROFILES.json")
    require(profiles.get("current_level"),"C0-planning","profile level")
    require([row["id"] for row in profiles.get("claim_levels",[])],[f"C{i}" for i in range(6)],"claim levels")
    if any(row.get("status") != "open" for row in profiles["claim_levels"]): fail("claim levels must be open")
    return set(ids)

def validate_gates_risks_slo() -> set[str]:
    gates = load_json("docs/status/PRODUCT_GATES.json")
    require(gates.get("compatibility_level"),"C0-not-earned","gate claim")
    rows = gates.get("gates",[])
    if len(rows) != 15: fail("expected 15 gates")
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)): fail("duplicate gate IDs")
    for row in rows:
        if row.get("status") != "open" or not row.get("owner") or not row.get("pass_criteria") or not row.get("evidence_types"): fail(f"incomplete gate {row.get('id')}")
    require(gates.get("gate_policy",{}).get("p0_p1_unexplained_divergence_allowed"),False,"divergence policy")
    risks = load_json("docs/status/RISK_REGISTER.json").get("risks",[])
    if len(risks) != 25 or len({row["id"] for row in risks}) != 25: fail("expected 25 unique risks")
    slo = load_json("docs/status/SERVICE_LEVEL_OBJECTIVES.json")
    require(slo.get("ratification_task"),"TG-W0-007","SLO ratification task")
    require(slo.get("integrity",{}).get("acknowledged_durable_writes_lost"),0,"lost writes SLO")
    return set(ids)

def validate_backlog(parity_ids: set[str], gate_ids: set[str]) -> None:
    index = load_json("docs/development/EXECUTION_BACKLOG.json")
    require(index.get("task_count"),120,"task count")
    require(sum(row["task_count"] for row in index.get("workstreams",[])),120,"workstream total")
    artifact = index["full_backlog_artifact"]
    path = ROOT / artifact["path"]
    require(hashlib.sha256(path.read_bytes()).hexdigest(),artifact["sha256"],"backlog SHA")
    with gzip.open(path,"rt",encoding="utf-8") as handle: backlog = json.load(handle)
    require(backlog.get("schema"),"trillionnium.execution-backlog.v2","backlog schema")
    workstreams = backlog.get("workstreams",[])
    require([row["id"] for row in workstreams],[f"W{i}" for i in range(17)],"detailed workstreams")
    tasks = [task for workstream in workstreams for task in workstream.get("tasks",[])]
    require(len(tasks),120,"detailed tasks")
    ids = [task.get("id") for task in tasks]
    if len(ids) != len(set(ids)): fail("duplicate task IDs")
    idset = set(ids); graph = {}; required = {"id","title","priority","status","acceptance","owner_role","review_roles","depends_on","estimate_person_weeks","risk","parity_ids","gate_ids","required_exit_state"}
    for task in tasks:
        missing = required - task.keys()
        if missing: fail(f"{task.get('id')}: missing {sorted(missing)}")
        if task["status"] != "planned" or task["priority"] not in {"P0","P1","P2"}: fail(f"{task['id']}: invalid status/priority")
        estimate = task["estimate_person_weeks"]
        if not 0 < estimate["min"] <= estimate["max"]: fail(f"{task['id']}: invalid estimate")
        if set(task["depends_on"]) - idset: fail(f"{task['id']}: unknown dependency")
        if set(task["gate_ids"]) - gate_ids: fail(f"{task['id']}: unknown gate")
        if set(task["parity_ids"]) - parity_ids: fail(f"{task['id']}: unknown parity ID")
        graph[task["id"]] = task["depends_on"]
    indegree = {node:0 for node in graph}; outgoing = defaultdict(list)
    for node,deps in graph.items():
        for dep in deps: indegree[node] += 1; outgoing[dep].append(node)
    queue = deque(node for node,degree in indegree.items() if degree == 0); visited = 0
    while queue:
        node = queue.popleft(); visited += 1
        for follower in outgoing[node]:
            indegree[follower] -= 1
            if indegree[follower] == 0: queue.append(follower)
    if visited != len(graph): fail("backlog dependency cycle")

def validate_evidence_transition_notice() -> None:
    required = set(load_json("docs/evidence/schemas/trillionnium-evidence-v1.schema.json").get("required",[]))
    for field in ("evidence_id","upstream","candidate","environment","fixtures","commands","result","artifacts","review"):
        if field not in required: fail(f"evidence schema does not require {field}")
    status = (ROOT / "docs/development/REPOSITORY_TRANSITION_STATUS.md").read_text(encoding="utf-8")
    runbook = (ROOT / "docs/development/REPOSITORY_TRANSITION_RUNBOOK.md").read_text(encoding="utf-8")
    for text,label in ((status,"status"),(runbook,"runbook")):
        for marker in ("1323087470","TrillionniumFoundation/Trillionnium-Nakama","TrillionniumFoundation/TrillionniumGame"):
            if marker not in text: fail(f"transition {label} missing {marker}")
    if "archive/trillionnium-nakama-main-2026-08-28-7f0d4be" not in runbook: fail("archive branch missing")
    rename = (ROOT / "scripts/rename-existing-repository.sh").read_text(encoding="utf-8")
    publish = (ROOT / "scripts/publish-repository.sh").read_text(encoding="utf-8")
    for marker in ("1323087470","PATCH","TRNM_REPOSITORY_RENAME_CONFIRM"):
        if marker not in rename: fail(f"rename script missing {marker}")
    if "gh repo create" in publish: fail("publish script must not create replacement repo")
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8").lower()
    for marker in ("nakama oss","v3.40.0","apache license","trademark"):
        if marker not in notice: fail(f"NOTICE missing {marker}")

def main() -> int:
    validate_files(); validate_boundary(); validate_upstream(); validate_plan_and_parity()
    parity_ids = validate_plan_and_parity(); gate_ids = validate_gates_risks_slo()
    validate_backlog(parity_ids,gate_ids); validate_evidence_transition_notice()
    print("plan validation passed")
    print("plan=v2 workstreams=17 tasks=120 parity_rollups>=74 denominators=14 gates=15 risks=25 claim=C0 upstream=v3.40.0")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValidationError,OSError,json.JSONDecodeError,gzip.BadGzipFile) as exc:
        print(f"plan validation failed: {exc}",file=sys.stderr)
        raise SystemExit(1)
