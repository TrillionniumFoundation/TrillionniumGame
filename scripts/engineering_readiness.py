#!/usr/bin/env python3
"""Read-only engineering inventory; consistency success is never acceptance.

This is a narrow source-shape/documentation regression, not a Rust parser,
compatibility oracle, security verifier or replacement for evidence_admission.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 8 * 1024 * 1024
DIMENSIONS = (
    "scope-and-dependencies", "public-api-and-examples", "state-and-data",
    "errors-and-side-effects", "concurrency-and-recovery", "security-and-budgets",
    "tests-and-compatibility", "operations-and-change",
)
DEPTHS = {"overview", "partial-design", "detailed-bounded-design"}


class ValidationError(ValueError):
    """An inventory input or a source/document contract is inconsistent."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def text(root: Path, path: str) -> str:
    candidate = Path(path)
    require(not candidate.is_absolute() and ".." not in candidate.parts,
            "inventory path must stay repository-relative")
    try:
        with (root / candidate).open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
        require(len(raw) <= MAX_BYTES, f"input exceeds byte budget: {path}")
        return raw.decode("utf-8")
    except (OSError, UnicodeError) as error:
        raise ValidationError(f"unreadable UTF-8 inventory input: {path}") from error


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def invalid_constant(value: str) -> None:
    raise ValidationError("non-finite JSON value")


def load(root: Path, path: str) -> dict[str, Any]:
    try:
        value = json.loads(text(root, path), object_pairs_hook=unique_object,
                           parse_constant=invalid_constant)
    except (ValueError, RecursionError) as error:
        raise ValidationError(f"invalid inventory JSON: {path}") from error
    require(isinstance(value, dict), f"expected JSON object: {path}")
    return value


def rows(value: Any, label: str) -> list[dict[str, Any]]:
    require(isinstance(value, list) and bool(value), f"nonempty {label} required")
    require(all(isinstance(row, dict) for row in value), f"invalid {label} row")
    identifiers = [row.get("id") for row in value]
    require(all(isinstance(item, str) and item.strip() == item and item
                for item in identifiers), f"invalid {label} identity")
    require(len(identifiers) == len(set(identifiers)), f"duplicate {label} identity")
    return value


def between(source: str, start: str, end: str) -> str:
    require(source.count(start) == 1 and source.count(end) == 1,
            "expected one recognized source/document block")
    begin = source.index(start) + len(start)
    finish = source.index(end)
    require(begin < finish, "reversed source/document block")
    return source[begin:finish]


def doc_block(document: str, name: str) -> str:
    return between(document, f"<!-- trnm-server-{name}:start -->",
                   f"<!-- trnm-server-{name}:end -->")


def source_interface(config: str, app: str) -> dict[str, Any]:
    # These owned regions deliberately exclude unit fixtures and unrelated
    # routing helpers. A source layout change requires reviewing this extractor.
    command_region = between(config, "let command = match arguments {",
                             "\n        let bind =")
    commands = re.findall(
        r'^\s*\[_, value\] if value == "([a-z-]+)" => Command::[A-Za-z]+,\s*$',
        command_region, re.MULTILINE,
    )
    route_region = between(
        app,
        "let response = match (request.method.as_str(), request.target.as_str()) {",
        "\n        if response.status",
    )
    routes = re.findall(r'\("(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)", "(/[^"\s]*)"\)',
                        route_region)
    require(commands and len(commands) == len(set(commands)), "missing or duplicate CLI arms")
    require(routes and len(routes) == len(set(routes)), "missing or duplicate route arms")
    production_config = config.split("\n#[cfg(test)]", 1)[0]
    environment = sorted(set(re.findall(r'"(TRNM_SERVER_[A-Z0-9_]+)"', production_config)))
    require(bool(environment), "missing configuration names")
    return {"commands": sorted(commands), "routes": sorted(routes),
            "environment_names": environment}


def check_documented_interface(interface: dict[str, Any], document: str) -> None:
    commands = re.findall(r'^`([a-z-]+)`\s*$', doc_block(document, "cli"), re.MULTILINE)
    routes = re.findall(r'^\| `(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)` \| `(/[^`]+)` \|',
                        doc_block(document, "routes"), re.MULTILINE)
    environment = re.findall(r'^\| `(TRNM_SERVER_[A-Z0-9_]+)` \|',
                             doc_block(document, "config"), re.MULTILINE)
    require(sorted(commands) == interface["commands"], "documented CLI differs from source")
    require(sorted(routes) == interface["routes"], "documented routes differ from source")
    require(sorted(environment) == interface["environment_names"],
            "documented configuration names differ from source")


def inspect(root: Path) -> dict[str, Any]:
    registry = load(root, "docs/status/MODULE_DOCUMENTATION.json")
    modules = rows(registry.get("modules"), "modules")
    discovered = {path.parent.name for path in (root / "crates").glob("*/Cargo.toml")}
    require({row["id"] for row in modules} == discovered,
            "module registry differs from actual Cargo package directories")
    depth = load(root, "docs/status/DOCUMENTATION_DEPTH.json")
    require(depth.get("schema") == "trillionnium.documentation-depth.v1", "unexpected depth schema")
    require(depth.get("project_id") == "trillionnium-game", "unexpected depth project")
    require(depth.get("assessment_kind") == "engineering-review-proposal", "unexpected assessment kind")
    require(depth.get("claim_credit") is False, "depth assessment cannot grant credit")
    require(depth.get("required_design_dimensions") == list(DIMENSIONS), "design dimensions changed")
    assessments = rows(depth.get("modules"), "depth assessments")
    require({row["id"] for row in assessments} == discovered,
            "depth assessment does not cover every package exactly once")
    for row in assessments:
        require(row.get("depth") in DEPTHS, "invalid documentation depth")
        gaps = row.get("remaining_design_work")
        require(isinstance(gaps, list) and bool(gaps)
                and all(isinstance(item, str) and item.strip() for item in gaps),
                "remaining design work must be explicit")
    for row in modules:
        require(row.get("documentation") == f"crates/{row['id']}/README.md",
                "noncanonical module README")
        require(bool(text(root, row["documentation"]).strip()), "empty module README")
    component_registry = load(root, "docs/status/COMPONENT_DOCUMENTATION.json")
    components = rows(component_registry.get("components"), "components")
    component_depth = rows(depth.get("components"), "component assessments")
    require({row["id"] for row in components} == {row["id"] for row in component_depth},
            "component depth assessment coverage differs")
    for row in component_depth:
        require(isinstance(row.get("remaining_design_work"), list)
                and bool(row["remaining_design_work"])
                and all(isinstance(item, str) and item.strip() for item in row["remaining_design_work"]),
                "component remaining design work must be explicit")
    interface = source_interface(
        text(root, "crates/trnm-server/src/runtime/config.rs"),
        text(root, "crates/trnm-server/src/runtime/app.rs"),
    )
    check_documented_interface(interface, text(root, "docs/DEVELOPMENT.md"))
    gaps = rows(load(root, "docs/status/GAP_REGISTER.json").get("gaps"), "gaps")
    for row in gaps:
        require(isinstance(row.get("status"), str) and bool(row["status"]), "gap status missing")
        require(isinstance(row.get("close_criteria"), list) and bool(row["close_criteria"]),
                "gap close criteria missing")
        require(isinstance(row.get("required_evidence_types"), list)
                and bool(row["required_evidence_types"]), "gap evidence requirements missing")
    detail = load(root, "docs/roadmap/ENGINEERING_EXIT_CONTRACTS.json")
    require(detail.get("schema") == "trillionnium.engineering-exit-detail.v1",
            "unexpected engineering exit-detail schema")
    require(detail.get("claim_credit") is False, "exit detail cannot grant credit")
    gap_details = rows(detail.get("gap_details"), "gap details")
    require({row["id"] for row in gap_details} == {row["id"] for row in gaps},
            "engineering detail must cover every registered gap exactly once")
    for row in gap_details:
        require(type(row.get("priority_band")) is int and row["priority_band"] in (0, 1, 2),
                "invalid advisory priority band")
        for field in ("implementation_steps", "required_checks", "external_obligations"):
            require(isinstance(row.get(field), list) and bool(row[field])
                    and all(isinstance(item, str) and item.strip() for item in row[field]),
                    f"gap detail requires nonempty {field}")
    domains = detail.get("domain_details")
    require(isinstance(domains, list) and len(domains) == 11
            and all(isinstance(row, dict) and type(row.get("issue")) is int for row in domains)
            and {row["issue"] for row in domains} == set(range(137, 148)),
            "full-surface engineering detail must retain all eleven work packages")
    return {
        "schema": "trillionnium.engineering-readiness-report.v1",
        "scope": "read-only source and documentation inventory; not evidence admission",
        "module_count": len(modules),
        "documented_module_count": len(modules),
        "documentation_depth_counts": dict(sorted(Counter(row["depth"] for row in assessments).items())),
        "design_acceptance": "not-evaluated; requires independent review",
        "component_count": len(components),
        "server_interface": interface,
        "gap_count": len(gaps),
        "gap_detail_count": len(gap_details),
        "full_surface_work_package_count": len(domains),
        "recorded_gap_status_counts": dict(sorted(Counter(row["status"] for row in gaps).items())),
        "gaps": [{key: row.get(key) for key in
                  ("id", "status", "owner_role", "external_dependency", "close_criteria",
                   "required_evidence_types", "evidence_ids")} for row in gaps],
        "closure_validated": False,
        "claim_credit": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        report = inspect(args.root)
    except ValidationError as error:
        print(f"engineering-readiness: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
