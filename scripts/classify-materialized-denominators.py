#!/usr/bin/env python3
"""Classify materialized denominator candidates using the repository policy."""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import inspect
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "manifests/upstream/candidates/raw"
CLASSIFIED = ROOT / "manifests/upstream/candidates/classified"
STATUS = ROOT / "docs/status/DENOMINATOR_MATERIALIZATION.json"
DRAFT = ROOT / "manifests/upstream/denominator-review-packet.draft.json"
CLASSIFIER = ROOT / "scripts/classify-denominator.py"
RULES = ROOT / "config/denominator-classification-rules.json"
EXPECTED = {
    "DEN-SOURCE", "DEN-API", "DEN-RTAPI", "DEN-CONSOLE", "DEN-RUNTIME",
    "DEN-CONFIG", "DEN-CLI", "DEN-DB", "DEN-DATA", "DEN-METRICS",
    "DEN-OPS", "DEN-PROVIDERS", "DEN-IAP", "DEN-SDK",
}
FORBIDDEN_TRUE = {
    "sg1_eligible", "compatibility_credit", "production_ready",
    "public_online", "nakama_retired", "cutover_authorized",
}


class ClassificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ClassificationError(message)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ClassificationError(f"{path.relative_to(ROOT)}: invalid JSON: {error}") from error
    require(isinstance(value, dict), f"{path.relative_to(ROOT)}: top level must be an object")
    return value


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def identifier(value: dict[str, Any]) -> str | None:
    for key in ("denominator", "denominator_id", "id"):
        if value.get(key) in EXPECTED:
            return str(value[key])
    return None


def force_no_credit(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_TRUE:
                value[key] = False
            else:
                force_no_credit(child)
    elif isinstance(value, list):
        for child in value:
            force_no_credit(child)


def load_classifier() -> Any:
    require(CLASSIFIER.is_file(), f"classifier missing: {CLASSIFIER.relative_to(ROOT)}")
    spec = importlib.util.spec_from_file_location("trillionnium_denominator_classifier", CLASSIFIER)
    require(spec is not None and spec.loader is not None, "could not load classifier module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def candidate_function_names() -> list[str]:
    names = ["classify_candidate", "classify_manifest", "apply_classification", "classify"]
    test = ROOT / "tests/control_plane/test_denominator_classifier.py"
    if test.is_file():
        source = test.read_text(encoding="utf-8")
        for name in sorted(set(__import__("re").findall(r"self\.module\.([A-Za-z_][A-Za-z0-9_]*)\(", source))):
            if "classif" in name and name not in names:
                names.insert(0, name)
    return names


def invoke_function(function: Any, candidate: dict[str, Any], rules: Any, denominator: str) -> dict[str, Any] | None:
    signature = inspect.signature(function)
    kwargs: dict[str, Any] = {}
    positional: list[Any] = []
    for parameter in signature.parameters.values():
        name = parameter.name.lower()
        if name in {"candidate", "manifest", "value", "document"}:
            value: Any = copy.deepcopy(candidate)
        elif name in {"rules", "policy", "classification_rules", "rule_set"}:
            value = copy.deepcopy(rules)
        elif name in {"denominator", "denominator_id", "identifier"}:
            value = denominator
        elif parameter.default is not inspect.Parameter.empty:
            continue
        else:
            return None
        if parameter.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}:
            positional.append(value)
        else:
            kwargs[parameter.name] = value
    working = copy.deepcopy(candidate)
    for index, value in enumerate(positional):
        if isinstance(value, dict) and identifier(value) == denominator:
            positional[index] = working
    result = function(*positional, **kwargs)
    values = [result, working]
    if isinstance(result, tuple):
        values = list(result) + values
    for value in values:
        if isinstance(value, dict):
            if identifier(value) == denominator and isinstance(value.get("leaves"), list):
                return value
            nested = value.get("manifest")
            if isinstance(nested, dict) and identifier(nested) == denominator:
                return nested
    return None


def invoke_cli(raw: Path, output: Path, denominator: str) -> dict[str, Any] | None:
    commands = [
        [sys.executable, str(CLASSIFIER), "--input", str(raw), "--output", str(output)],
        [sys.executable, str(CLASSIFIER), "--candidate", str(raw), "--output", str(output)],
        [sys.executable, str(CLASSIFIER), "--manifest", str(raw), "--output", str(output)],
        [sys.executable, str(CLASSIFIER), "--input", str(raw), "--rules", str(RULES), "--output", str(output)],
        [sys.executable, str(CLASSIFIER), "--candidate", str(raw), "--policy", str(RULES), "--output", str(output)],
        [sys.executable, str(CLASSIFIER), str(raw), str(output)],
    ]
    for command in commands:
        output.unlink(missing_ok=True)
        process = subprocess.run(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=120,
        )
        if process.returncode != 0 or not output.is_file():
            continue
        try:
            value = json.loads(output.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and identifier(value) == denominator and isinstance(value.get("leaves"), list):
            return value
    return None


def classify_one(module: Any, rules: Any, raw: Path) -> dict[str, Any]:
    candidate = load(raw)
    denominator = identifier(candidate)
    require(denominator in EXPECTED, f"{raw.relative_to(ROOT)}: unknown denominator")
    leaves = candidate.get("leaves")
    require(isinstance(leaves, list) and leaves, f"{denominator}: candidate has no leaves")
    for name in candidate_function_names():
        function = getattr(module, name, None)
        if not callable(function):
            continue
        try:
            classified = invoke_function(function, candidate, rules, denominator)
        except Exception:
            classified = None
        if classified is not None:
            force_no_credit(classified)
            return classified
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "classified.json"
        classified = invoke_cli(raw, output, denominator)
    require(classified is not None, f"{denominator}: repository classifier could not classify candidate")
    force_no_credit(classified)
    return classified


def counts(path: Path) -> dict[str, int]:
    value = load(path)
    leaves = value.get("leaves")
    leaves = leaves if isinstance(leaves, list) else []
    leaf_count = value.get("leaf_count")
    if not isinstance(leaf_count, int):
        leaf_count = len(leaves)
    classified = sum(
        1 for leaf in leaves
        if isinstance(leaf, dict) and leaf.get("classification") not in {None, "", "unclassified"}
    )
    owner = sum(1 for leaf in leaves if isinstance(leaf, dict) and leaf.get("owner_role"))
    task = sum(
        1 for leaf in leaves
        if isinstance(leaf, dict) and isinstance(leaf.get("task_ids"), list) and leaf["task_ids"]
    )
    test = sum(
        1 for leaf in leaves
        if isinstance(leaf, dict) and isinstance(leaf.get("test_ids"), list) and leaf["test_ids"]
    )
    return {
        "leaf_count": leaf_count,
        "classified_count": classified,
        "unclassified_count": leaf_count - classified,
        "owner_bound_count": owner,
        "task_bound_count": task,
        "test_bound_count": test,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    try:
        require(RULES.is_file(), f"classification rules missing: {RULES.relative_to(ROOT)}")
        rules = json.loads(RULES.read_text(encoding="utf-8"))
        module = load_classifier()
        raw_paths: dict[str, Path] = {}
        for path in sorted(RAW.glob("*.json")):
            value = load(path)
            name = identifier(value)
            if name:
                require(name not in raw_paths, f"duplicate raw candidate: {name}")
                raw_paths[name] = path
        require(set(raw_paths) == EXPECTED, f"raw denominator set mismatch; missing={sorted(EXPECTED - set(raw_paths))}")

        generated: dict[str, dict[str, Any]] = {}
        for denominator, raw in sorted(raw_paths.items()):
            value = classify_one(module, rules, raw)
            require(identifier(value) == denominator, f"{denominator}: classifier changed identifier")
            destination = CLASSIFIED / f"{denominator.lower()}.candidate.json"
            rendered = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
            if arguments.check:
                require(destination.is_file(), f"{denominator}: classified file missing")
                require(destination.read_text(encoding="utf-8") == rendered, f"{denominator}: classified file is stale")
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(rendered, encoding="utf-8")
            generated[denominator] = {"path": destination, **counts(destination)}

        status = load(STATUS)
        draft = load(DRAFT)
        rows = []
        totals = {
            "leaf_count_total": 0,
            "classified_count": 0,
            "unclassified_count": 0,
            "owner_bound_count": 0,
            "task_bound_count": 0,
            "test_bound_count": 0,
        }
        for denominator, row in sorted(generated.items()):
            path = row.pop("path")
            data = path.read_bytes()
            packet_row = {
                "id": denominator,
                "manifest": {
                    "path": path.relative_to(ROOT).as_posix(),
                    "sha256": digest(data),
                    "size_bytes": len(data),
                },
                **row,
                "review_status": "pending-independent-review",
            }
            rows.append(packet_row)
            totals["leaf_count_total"] += row["leaf_count"]
            for key in totals.keys() - {"leaf_count_total"}:
                totals[key] += row[key]
        review_ready = (
            totals["leaf_count_total"] > 0
            and totals["unclassified_count"] == 0
            and totals["classified_count"] == totals["leaf_count_total"]
            and totals["owner_bound_count"] == totals["leaf_count_total"]
            and totals["task_bound_count"] == totals["leaf_count_total"]
            and totals["test_bound_count"] == totals["leaf_count_total"]
        )
        require(review_ready, "classified denominator set is not fully bound and zero-unclassified")
        draft["denominators"] = rows
        draft["missing_denominators"] = []
        draft["aggregate"] = {
            "denominator_count": len(rows),
            **totals,
            "manifest_sha256": digest(canonical(rows)),
        }
        draft["review"] = {
            **(draft.get("review") if isinstance(draft.get("review"), dict) else {}),
            "decision": "pending",
            "independent": False,
            "minimum_reviewers": 2,
        }
        force_no_credit(draft)
        status["materialized_denominator_count"] = len(rows)
        status["materialized_denominators"] = sorted(generated)
        status["missing_denominators"] = []
        status["aggregate"] = draft["aggregate"]
        status["status"] = "review-ready"
        force_no_credit(status)
        if arguments.check:
            require(load(DRAFT) == draft, "draft review packet is stale")
            require(load(STATUS) == status, "materialization status is stale")
        else:
            write(DRAFT, draft)
            write(STATUS, status)
        print(json.dumps({"status":"review-ready", **totals, "claims":{"sg1_eligible":False,"compatibility_credit":False,"production_ready":False}}, sort_keys=True, separators=(",", ":")))
        return 0
    except (ClassificationError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"materialized denominator classification failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
