#!/usr/bin/env python3
"""Generate fail-closed Nakama configuration and CLI denominator candidates."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.upstream.pinned_archive import SourceArchiveError, git_blob_sha1_bytes, verify_source_lock  # noqa: E402

REPOSITORY = "heroiclabs/nakama"
COMMIT = "d4d92f93f78bbbe62c7fc50a3f85c772ec121a09"
TREE = "f3c9cfc2726d5543da1564629170f35b98e3797d"
GENERATOR = "trillionniumgame-config-cli-denominator"
VERSION = "0.1.0"
SOURCE_PATHS = ["server/config.go", "flags/flags.go", "flags/vars.go", "main.go", "migrate/migrate.go"]


class DenominatorError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def stable_id(denominator: str, item: dict[str, Any]) -> str:
    seed = canonical({"denominator": denominator, "class": item["class"], "path": item["path"], "symbol": item["symbol"], "signature": item["signature"], "metadata": item.get("metadata") or {}})
    return f"TG-D5-{hashlib.sha256(seed).hexdigest()[:20].upper()}"


def run_helper(root: Path, paths: Sequence[str]) -> list[dict[str, Any]]:
    completed = subprocess.run(
        ["go", "run", str(ROOT / "tools/denominator/go_config_surface.go"), "--root", str(root), *paths],
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise DenominatorError(f"Go config extractor failed: {completed.stderr.strip()}")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise DenominatorError(f"Go config extractor emitted invalid JSON: {exc}") from exc
    if value.get("schema") != "trillionnium.go-config-cli-surface.v1" or not isinstance(value.get("items"), list):
        raise DenominatorError("Go config extractor schema mismatch")
    return value["items"]


def derive_generated_flags(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items:
        if item["class"] != "config_field":
            continue
        metadata = item.get("metadata") or {}
        yaml_name = str(metadata.get("yaml") or "").split(",", 1)[0]
        usage = str(metadata.get("usage") or "")
        if not yaml_name or yaml_name == "-":
            continue
        result.append(
            {
                "class": "cli_generated_flag_candidate",
                "symbol": f"{item['symbol']}->{yaml_name}",
                "signature": item["signature"],
                "path": item["path"],
                "start_line": item["start_line"],
                "end_line": item["end_line"],
                "metadata": {"yaml": yaml_name, "usage": usage, "source_field": item["symbol"]},
            }
        )
    return result


def source(root: Path, item: dict[str, Any]) -> dict[str, Any]:
    path = str(item["path"])
    data = (root / path).read_bytes()
    return {
        "repository": REPOSITORY,
        "commit": COMMIT,
        "path": path,
        "blob": git_blob_sha1_bytes(data),
        "sha256": sha256(data),
        "start_line": item.get("start_line"),
        "end_line": item.get("end_line"),
    }


def make_leaf(root: Path, denominator: str, item: dict[str, Any]) -> dict[str, Any]:
    identifier = stable_id(denominator, item)
    contract = {
        "class": item["class"],
        "symbol": item["symbol"],
        "signature": item["signature"],
        "metadata": item.get("metadata") or {},
    }
    owner = "platform-config" if denominator == "DEN-CONFIG" else "platform-cli"
    task = "TG-W1-002" if denominator == "DEN-CONFIG" else "TG-W1-003"
    return {
        "id": identifier,
        "layer": "D5",
        "denominator": denominator,
        "class": item["class"],
        "symbol": item["symbol"],
        "signature_hash": sha256(canonical(contract)),
        "source": source(root, item),
        "compatibility_profile": "C2",
        "stability_tier": "behavior-contract",
        "classification": "unclassified",
        "mandatory": None,
        "owner_role": owner,
        "workstream": "W1",
        "task_ids": ["TG-W0-002", task],
        "test_ids": [f"TG-DIFF-{identifier}"],
        "status": "planned",
        "evidence_refs": [],
        "waiver": None,
        "contract": contract,
    }


def manifest(denominator: str, leaves: list[dict[str, Any]], source_lock: dict[str, Any]) -> dict[str, Any]:
    leaves.sort(key=lambda leaf: leaf["id"])
    if len({leaf["id"] for leaf in leaves}) != len(leaves):
        raise DenominatorError(f"duplicate stable IDs in {denominator}")
    counts: dict[str, int] = {}
    for leaf in leaves:
        counts[leaf["class"]] = counts.get(leaf["class"], 0) + 1
    value: dict[str, Any] = {
        "schema": "trillionnium.config-cli-denominator-candidate.v1",
        "project_id": "trillionnium-game",
        "generator": {"name": GENERATOR, "version": VERSION},
        "denominator": denominator,
        "layer": "D5",
        "status": "candidate-unclassified",
        "leaf_count": len(leaves),
        "unclassified_count": len(leaves),
        "unreviewed_count": len(leaves),
        "counts_by_class": counts,
        "source_lock": source_lock,
        "leaves": leaves,
        "claims": {
            "sg1_complete": False,
            "behavior_compatible": False,
            "migration_compatible": False,
            "operationally_replaceable": False,
            "production_ready": False,
        },
    }
    value["content_sha256"] = sha256(canonical(value))
    return value


def reconciliation(config_manifest: dict[str, Any], cli_manifest: dict[str, Any]) -> dict[str, Any]:
    fields = [leaf for leaf in config_manifest["leaves"] if leaf["class"] == "config_field"]
    defaults = [leaf for leaf in config_manifest["leaves"] if leaf["class"] in {"config_default", "config_default_assignment"}]
    validations = [leaf for leaf in config_manifest["leaves"] if leaf["class"] == "config_validation"]
    generated_flags = [leaf for leaf in cli_manifest["leaves"] if leaf["class"] == "cli_generated_flag_candidate"]
    default_names = {str(leaf["contract"].get("metadata", {}).get("target") or leaf["symbol"]).split("@", 1)[0].rsplit(".", 1)[-1].lower() for leaf in defaults}
    fields_without_observed_default = sorted(
        leaf["symbol"] for leaf in fields if leaf["symbol"].rsplit(".", 1)[-1].lower() not in default_names
    )
    return {
        "schema": "trillionnium.config-cli-reconciliation.v1",
        "status": "candidate-unreviewed",
        "config_field_count": len(fields),
        "default_candidate_count": len(defaults),
        "validation_candidate_count": len(validations),
        "generated_flag_candidate_count": len(generated_flags),
        "fields_without_observed_default_candidate": fields_without_observed_default,
        "default_completeness_proven": False,
        "cli_precedence_proven": False,
        "exit_code_compatibility_proven": False,
        "compatibility_credit": False,
    }


def generate(root: Path, output: Path) -> dict[str, Any]:
    try:
        lock = verify_source_lock(root, repository=REPOSITORY, revision=COMMIT, tree=TREE)
    except SourceArchiveError as exc:
        raise DenominatorError(str(exc)) from exc
    for path in SOURCE_PATHS:
        if not (root / path).is_file():
            raise DenominatorError(f"required exact source is missing: {path}")
    items = run_helper(root, SOURCE_PATHS)
    items.extend(derive_generated_flags(items))
    config_classes = {"config_type", "config_field", "config_embedded_field", "config_interface", "config_interface_method", "config_validation", "config_default", "config_default_assignment", "config_precedence_event"}
    cli_classes = {"cli_generated_flag_candidate", "cli_case_candidate", "cli_flagset", "cli_exit_path", "cli_parse_event"}
    unknown = sorted({item["class"] for item in items} - config_classes - cli_classes)
    if unknown:
        raise DenominatorError(f"unclassified extractor item classes: {unknown}")
    config_leaves = [make_leaf(root, "DEN-CONFIG", item) for item in items if item["class"] in config_classes]
    cli_leaves = [make_leaf(root, "DEN-CLI", item) for item in items if item["class"] in cli_classes]
    config_value = manifest("DEN-CONFIG", config_leaves, lock)
    cli_value = manifest("DEN-CLI", cli_leaves, lock)
    reconcile = reconciliation(config_value, cli_value)
    reconcile["config_manifest_sha256"] = config_value["content_sha256"]
    reconcile["cli_manifest_sha256"] = cli_value["content_sha256"]
    output.mkdir(parents=True, exist_ok=True)
    for name, value in (
        ("config-denominator.candidate.json", config_value),
        ("cli-denominator.candidate.json", cli_value),
        ("config-cli-reconciliation.candidate.json", reconcile),
    ):
        (output / name).write_bytes(canonical(value) + b"\n")
    sums = [f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}" for path in sorted(output.glob("*.json"))]
    (output / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    return {
        "config_leaf_count": len(config_leaves),
        "cli_leaf_count": len(cli_leaves),
        "sg1_complete": False,
        "compatibility_credit": False,
    }


def require_sg1(output: Path) -> None:
    failures = []
    for name in ("config-denominator.candidate.json", "cli-denominator.candidate.json"):
        value = json.loads((output / name).read_text(encoding="utf-8"))
        if value.get("status") != "reviewed-locked":
            failures.append(f"{name} is not reviewed-locked")
        if value.get("unclassified_count") != 0 or value.get("unreviewed_count") != 0:
            failures.append(f"{name} has open classification/review work")
        if value.get("claims", {}).get("sg1_complete") is not True:
            failures.append(f"{name} SG1 claim is false")
    if failures:
        raise DenominatorError("SG1 remains open: " + "; ".join(failures))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--nakama-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--require-sg1", action="store_true")
    args = parser.parse_args()
    try:
        result = generate(args.nakama_dir.resolve(), args.output_dir.resolve())
        if args.require_sg1:
            require_sg1(args.output_dir.resolve())
        print(json.dumps(result, sort_keys=True))
    except (DenominatorError, OSError, json.JSONDecodeError) as exc:
        print(f"config/CLI denominator generation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
