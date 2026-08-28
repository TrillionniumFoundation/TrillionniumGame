#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    raise RuntimeError(message)


def load(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def main() -> int:
    required = [
        "README.md", "CURRENT_PLAN.md", "PROJECT_ID", "PROJECT_BOUNDARY.md",
        "PROJECT_BOUNDARY.json", "LICENSE", "NOTICE", "AGENTS.md",
        "CONTRIBUTING.md", "docs/adr/ADR-0001-FULL-RUST-REIMPLEMENTATION.md",
        "docs/development/FEATURE_PARITY_MATRIX.md",
        "docs/development/EXECUTION_BACKLOG.json",
        "docs/development/UPSTREAM_BASELINE.json",
        "docs/development/THIRD_PARTY_POLICY.md",
        "docs/status/PRODUCT_GATES.json",
    ]
    missing = [p for p in required if not (ROOT / p).is_file()]
    if missing:
        fail("missing required files: " + ", ".join(missing))
    if (ROOT / "PROJECT_ID").read_text().strip() != "trillionnium-game":
        fail("PROJECT_ID mismatch")

    boundary = load("PROJECT_BOUNDARY.json")
    if boundary.get("project_id") != "trillionnium-game":
        fail("boundary project_id mismatch")
    if boundary.get("scope", {}).get("nakama_oss_full_reimplementation") is not True:
        fail("full Nakama OSS scope is not locked")
    if boundary.get("language_policy", {}).get("go_server_allowed") is not False:
        fail("Go server must be forbidden")

    baseline = load("docs/development/UPSTREAM_BASELINE.json")
    nakama = baseline["nakama"]
    common = baseline["nakama_common"]
    if nakama["tag"] != "v3.40.0" or nakama["commit"] != "d4d92f93f78bbbe62c7fc50a3f85c772ec121a09":
        fail("Nakama baseline mismatch")
    if common["tag"] != "v1.47.0" or common["commit"] != "449b77ecc8789aa466c36b67f6e498033dfcd9c5":
        fail("nakama-common baseline mismatch")

    plan = (ROOT / "CURRENT_PLAN.md").read_text(encoding="utf-8")
    workstreams = re.findall(r"^### W(\d+)\b", plan, re.MULTILINE)
    if workstreams != [str(i) for i in range(17)]:
        fail(f"expected W0-W16 in order, got {workstreams}")
    for marker in ("36–48", "28–36 FTE", "Definition of Done", "Nakama OSS `v3.40.0`"):
        if marker not in plan:
            fail(f"plan missing marker: {marker}")

    parity = (ROOT / "docs/development/FEATURE_PARITY_MATRIX.md").read_text(encoding="utf-8")
    rows = [line for line in parity.splitlines() if line.startswith("| TG-PAR-")]
    if len(rows) != 74:
        fail(f"expected 74 parity rollups, found {len(rows)}")

    backlog = load("docs/development/EXECUTION_BACKLOG.json")
    if backlog["target"]["initial_task_count"] != 119:
        fail("initial task count mismatch")
    if [w["id"] for w in backlog["workstreams"]] != [f"W{i}" for i in range(17)]:
        fail("backlog workstream order mismatch")
    if sum(w["task_count"] for w in backlog["workstreams"]) != 119:
        fail("backlog task totals do not equal 119")

    gates = load("docs/status/PRODUCT_GATES.json")
    if gates.get("release_claim") != "planning-only":
        fail("initial release claim must be planning-only")
    if any(g.get("status") != "open" for g in gates.get("gates", [])):
        fail("all initial product gates must be open")
    if len(gates.get("gates", [])) < 10:
        fail("product gate set is incomplete")

    notice = (ROOT / "NOTICE").read_text(encoding="utf-8").lower()
    for marker in ("nakama oss v3.40.0", "apache license", "trademark"):
        if marker not in notice:
            fail(f"NOTICE missing marker: {marker}")

    print("plan validation passed")
    print("workstreams=17 tasks=119 parity_rollups=74 gates=open upstream=v3.40.0")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"plan validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
