#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

PATH = "docs/governance/REQUIRED_WORKFLOWS_OVERLAY_V1.json"
ROOT = Path(__file__).resolve().parents[1]
MISSING = object()


def staged(stage: int) -> Any:
    raw = subprocess.check_output(["git", "show", f":{stage}:{PATH}"], cwd=ROOT)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise SystemExit(f"stage {stage} overlay root must be an object")
    return value


def keyed(items: Any, label: str) -> dict[int, dict[str, Any]]:
    if not isinstance(items, list):
        raise SystemExit(f"{label} must be a list")
    result: dict[int, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("workflow_id"), int):
            raise SystemExit(f"{label} item lacks integer workflow_id")
        workflow_id = item["workflow_id"]
        if workflow_id in result:
            raise SystemExit(f"duplicate {label} workflow_id {workflow_id}")
        result[workflow_id] = item
    return result


def three_way(ancestor: Any, ours: Any, theirs: Any, label: str) -> Any:
    if ours == theirs:
        return copy.deepcopy(ours)
    if ours == ancestor:
        return copy.deepcopy(theirs)
    if theirs == ancestor:
        return copy.deepcopy(ours)
    if isinstance(ancestor, dict) and isinstance(ours, dict) and isinstance(theirs, dict):
        output: dict[str, Any] = {}
        keys = set(ancestor) | set(ours) | set(theirs)
        for key in sorted(keys):
            av = ancestor.get(key, MISSING)
            ov = ours.get(key, MISSING)
            tv = theirs.get(key, MISSING)
            if MISSING in (av, ov, tv):
                if ov == tv:
                    if ov is not MISSING:
                        output[key] = copy.deepcopy(ov)
                    continue
                if ov == av:
                    if tv is not MISSING:
                        output[key] = copy.deepcopy(tv)
                    continue
                if tv == av:
                    if ov is not MISSING:
                        output[key] = copy.deepcopy(ov)
                    continue
                raise SystemExit(f"unresolved key-presence conflict at {label}.{key}")
            output[key] = three_way(av, ov, tv, f"{label}.{key}")
        return output
    raise SystemExit(f"unresolved concurrent overlay conflict at {label}")


def merge_workflow_list(
    ancestor_items: Any,
    ours_items: Any,
    theirs_items: Any,
    label: str,
) -> list[dict[str, Any]]:
    ancestor = keyed(ancestor_items, label)
    ours = keyed(ours_items, label)
    theirs = keyed(theirs_items, label)
    merged: dict[int, dict[str, Any]] = {}
    for workflow_id in sorted(set(ancestor) | set(ours) | set(theirs)):
        av = ancestor.get(workflow_id, MISSING)
        ov = ours.get(workflow_id, MISSING)
        tv = theirs.get(workflow_id, MISSING)
        if MISSING in (av, ov, tv):
            if ov == tv:
                if ov is not MISSING:
                    merged[workflow_id] = copy.deepcopy(ov)
                continue
            if ov == av:
                if tv is not MISSING:
                    merged[workflow_id] = copy.deepcopy(tv)
                continue
            if tv == av:
                if ov is not MISSING:
                    merged[workflow_id] = copy.deepcopy(ov)
                continue
            raise SystemExit(f"unresolved workflow membership conflict: {label} {workflow_id}")
        value = three_way(av, ov, tv, f"{label}[{workflow_id}]")
        if not isinstance(value, dict):
            raise SystemExit(f"merged workflow {workflow_id} must be an object")
        merged[workflow_id] = value

    order: list[int] = []
    for sequence in (ancestor_items, ours_items, theirs_items):
        for item in sequence:
            workflow_id = item["workflow_id"]
            if workflow_id in merged and workflow_id not in order:
                order.append(workflow_id)
    if set(order) != set(merged):
        raise SystemExit(f"failed to order complete {label} workflow set")
    return [merged[workflow_id] for workflow_id in order]


ancestor = staged(1)
ours = staged(2)
theirs = staged(3)
for value in (ancestor, ours, theirs):
    value.pop("overlay_sha256", None)

result: dict[str, Any] = {}
for key in sorted(set(ancestor) | set(ours) | set(theirs)):
    if key in {"add_workflows", "replace_workflows"}:
        result[key] = merge_workflow_list(
            ancestor.get(key, []), ours.get(key, []), theirs.get(key, []), key
        )
    else:
        av = ancestor.get(key, MISSING)
        ov = ours.get(key, MISSING)
        tv = theirs.get(key, MISSING)
        if MISSING in (av, ov, tv):
            raise SystemExit(f"overlay top-level key drift: {key}")
        result[key] = three_way(av, ov, tv, key)

payload = json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8")
result["overlay_sha256"] = hashlib.sha256(payload).hexdigest()
output = json.dumps(result, sort_keys=True, separators=(",", ":"))
(ROOT / PATH).write_text(output, encoding="utf-8")

replace = keyed(result.get("replace_workflows"), "replace_workflows")
server = replace.get(345394392)
if server is None or server.get("git_blob_sha1") != "36b236f8dc37c33cb557e2685bb3d6fcad72a133":
    raise SystemExit("server vertical-slice workflow binding was not preserved")
if result.get("base_manifest_blob_sha1") != "f8f97a3f7c9192a58796c169ea3477aa27059fc8":
    raise SystemExit("current base workflow manifest binding was not absorbed")
print(f"overlay_sha256={result['overlay_sha256']}")
print("server_workflow_blob=36b236f8dc37c33cb557e2685bb3d6fcad72a133")
print("base_manifest_blob=f8f97a3f7c9192a58796c169ea3477aa27059fc8")
