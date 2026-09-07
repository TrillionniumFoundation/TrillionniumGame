#!/usr/bin/env python3
"""Repin only reviewed canonical-server path and workflow-definition identities.

This tool is intentionally narrow. It validates that the canonical-server migration
preserves all 18 gaps and 92 close criteria, changes only GAP-P0-SERVER-001's
obsolete binary paths, and updates only cryptographic identity fields in the
closed required-workflow manifest composition.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()
PLAN_SHA = os.environ.get("PLAN_SHA", "")
if not re.fullmatch(r"[0-9a-f]{40}", PLAN_SHA):
    raise SystemExit("PLAN_SHA must be an exact 40-character lowercase Git SHA")

BASELINE = ROOT / "scripts/control_baselines/gap-register.v1.json"
POLICY = ROOT / "scripts/gap_register_scope_policy.py"
REGISTER = ROOT / "docs/status/GAP_REGISTER.json"
BASE_MANIFEST = ROOT / "docs/governance/REQUIRED_WORKFLOWS_V1.json"
OVERLAY = ROOT / "docs/governance/REQUIRED_WORKFLOWS_OVERLAY_V1.json"
REPORT = ROOT / "run/canonical-server-identity-repin.json"


def reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SystemExit(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=reject_duplicate_pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(
            SystemExit(f"non-finite JSON constant: {value}")
        ),
    )


def canonical(value: Any, *, newline: bool = True) -> bytes:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return raw + (b"\n" if newline else b"")


def git_blob_bytes(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data).hexdigest()


def git_blob_file(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise SystemExit(f"required regular file is absent or linked: {path.relative_to(ROOT)}")
    return git_blob_bytes(path.read_bytes())


def git_show_json(commit: str, path: str) -> Any:
    raw = subprocess.check_output(
        ["git", "-C", str(ROOT), "show", f"{commit}:{path}"],
        text=True,
    )
    return json.loads(
        raw,
        object_pairs_hook=reject_duplicate_pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(
            SystemExit(f"non-finite source JSON constant: {value}")
        ),
    )


def walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def scrub_identity_fields(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if key in {"git_blob_sha1", "base_manifest_blob_sha1", "overlay_sha256"}:
                result[key] = "<identity>"
            else:
                result[key] = scrub_identity_fields(child)
        return result
    if isinstance(value, list):
        return [scrub_identity_fields(child) for child in value]
    return value


def repin_gap_scope() -> dict[str, Any]:
    original = git_show_json(PLAN_SHA, "scripts/control_baselines/gap-register.v1.json")
    transformed = load(BASELINE)
    current = load(REGISTER)
    expected = copy.deepcopy(original)
    rows = {row["id"]: row for row in expected["gaps"]}
    transformed_ids = [row["id"] for row in transformed["gaps"]]
    if transformed_ids != [row["id"] for row in expected["gaps"]]:
        raise SystemExit("gap ID membership or ordering changed")

    old_paths = [
        "crates/trnm-persistence-pg/src/bin/trnm-server.rs",
        "crates/trnm-persistence-pg/src/bin/trnm_server/**",
        "crates/trnm-server/**",
        "docs/ARCHITECTURE.md",
        "docs/DEVELOPMENT.md",
        "docs/TESTING_AND_EVIDENCE.md",
    ]
    new_paths = [
        "crates/trnm-server/src/main.rs",
        "crates/trnm-server/src/runtime/**",
        "crates/trnm-server/**",
        "docs/ARCHITECTURE.md",
        "docs/DEVELOPMENT.md",
        "docs/TESTING_AND_EVIDENCE.md",
    ]
    row = rows.get("GAP-P0-SERVER-001")
    if row is None or row.get("affected_paths") != old_paths:
        raise SystemExit("source baseline server path contract drifted")
    row["affected_paths"] = new_paths
    if transformed != expected:
        raise SystemExit(
            "canonical-server migration changed immutable gap semantics beyond the reviewed path replacement"
        )
    if current != transformed:
        raise SystemExit("current gap register diverges from the transformed immutable baseline")
    if len(transformed["gaps"]) != 18:
        raise SystemExit("gap denominator changed")
    criteria = sum(len(item.get("close_criteria", [])) for item in transformed["gaps"])
    if criteria != 92:
        raise SystemExit(f"close-criteria denominator changed: {criteria}")

    baseline_blob = git_blob_file(BASELINE)
    policy_text = POLICY.read_text(encoding="utf-8")
    updated, count = re.subn(
        r'BASELINE_GIT_BLOB_SHA1 = "[0-9a-f]{40}"',
        f'BASELINE_GIT_BLOB_SHA1 = "{baseline_blob}"',
        policy_text,
        count=1,
    )
    if count != 1:
        raise SystemExit(f"immutable gap baseline pin anchor count={count}")
    POLICY.write_text(updated, encoding="utf-8")
    return {
        "gap_count": 18,
        "close_criteria_count": 92,
        "baseline_git_blob_sha1": baseline_blob,
        "semantic_change": {
            "gap_id": "GAP-P0-SERVER-001",
            "field": "affected_paths",
            "from": old_paths,
            "to": new_paths,
        },
    }


def repin_workflow_manifests() -> dict[str, Any]:
    before_base = load(BASE_MANIFEST)
    before_overlay = load(OVERLAY)
    base = copy.deepcopy(before_base)
    overlay = copy.deepcopy(before_overlay)
    changed_rows: list[dict[str, str]] = []
    actual_by_path: dict[str, str] = {}

    for document_name, document in (("base", base), ("overlay", overlay)):
        for row in walk_dicts(document):
            path_value = row.get("path")
            declared = row.get("git_blob_sha1")
            if not (
                isinstance(path_value, str)
                and path_value.startswith(".github/workflows/")
                and isinstance(declared, str)
                and re.fullmatch(r"[0-9a-f]{40}", declared)
            ):
                continue
            actual = actual_by_path.get(path_value)
            if actual is None:
                actual = git_blob_file(ROOT / path_value)
                actual_by_path[path_value] = actual
            elif actual != git_blob_file(ROOT / path_value):
                raise SystemExit(f"workflow path changed during manifest repin: {path_value}")
            row["git_blob_sha1"] = actual
            if actual != declared:
                changed_rows.append(
                    {
                        "document": document_name,
                        "path": path_value,
                        "from": declared,
                        "to": actual,
                    }
                )

    changed_paths = sorted({row["path"] for row in changed_rows})
    if not changed_paths:
        raise SystemExit("expected transformed workflow definition drift, observed none")
    if len(changed_paths) > 12:
        raise SystemExit(f"unexpectedly broad workflow repin set: {len(changed_paths)}")
    if scrub_identity_fields(before_base) != scrub_identity_fields(base):
        raise SystemExit("base required-workflow semantics changed outside Git blob identities")

    base_bytes = canonical(base)
    BASE_MANIFEST.write_bytes(base_bytes)
    overlay["base_manifest_blob_sha1"] = git_blob_bytes(base_bytes)
    overlay.pop("overlay_sha256", None)
    overlay["overlay_sha256"] = hashlib.sha256(
        canonical(overlay, newline=False)
    ).hexdigest()
    if scrub_identity_fields(before_overlay) != scrub_identity_fields(overlay):
        raise SystemExit("required-workflow overlay semantics changed outside identity fields")
    OVERLAY.write_bytes(canonical(overlay))

    return {
        "changed_definition_count": len(changed_paths),
        "changed_paths": changed_paths,
        "changed_row_count": len(changed_rows),
        "changed_rows": changed_rows,
        "base_manifest_git_blob_sha1": git_blob_file(BASE_MANIFEST),
        "overlay_git_blob_sha1": git_blob_file(OVERLAY),
        "overlay_sha256": overlay["overlay_sha256"],
    }


def main() -> int:
    gap = repin_gap_scope()
    workflows = repin_workflow_manifests()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": "trillionnium.canonical-server-identity-repin.v1",
        "plan_head": PLAN_SHA,
        "gap_scope": gap,
        "required_workflows": workflows,
        "claims": {
            "accepted_evidence": False,
            "gap_closed": False,
            "production_ready": False,
            "public_online": False,
            "nakama_retired": False,
        },
    }
    REPORT.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
