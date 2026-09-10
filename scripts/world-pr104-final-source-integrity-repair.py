#!/usr/bin/env python3
"""Repair final deterministic World source-integrity failures.

The repairs are deliberately narrow and fail closed:

* align every detailed-documentation subprocess caller with the checker's
  option-based ``--root`` interface;
* make the semantic-generator assertion tolerate ordinary Markdown wrapping
  inside a bounded match window;
* require the canonical prose lifecycle denominators for the legacy platform
  and Web4 rather than accepting a duplicate fallback elsewhere; and
* publish and validate one unambiguous line for each Nakama, CEX and public-
  online authority decision.
"""
from __future__ import annotations

import argparse
from pathlib import Path


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    source = path.read_text(encoding="utf-8", errors="strict")
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, observed {count}")
    path.write_text(source.replace(old, new), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)

    ci_integrity = root / "scripts/check-trnm-world-ci-integrity.py"
    replace_once(
        ci_integrity,
        '("scripts/check-trnm-world-detailed-documentation.py", [str(root)]),',
        '("scripts/check-trnm-world-detailed-documentation.py", ["--root", str(root)]),',
        "CI-integrity detailed-documentation invocation",
    )

    aggregate = root / "scripts/check-trnm-world-documentation.py"
    aggregate_source = aggregate.read_text(encoding="utf-8", errors="strict")
    required = '("scripts/check-trnm-world-detailed-documentation.py", ["--root", str(ROOT)]),'
    if aggregate_source.count(required) != 1:
        raise RuntimeError("aggregate documentation checker is not option-root aligned")

    current_conformance = root / "scripts/check-trnm-world-current-conformance.py"
    source = current_conformance.read_text(encoding="utf-8", errors="strict")
    old_semantic = (
        'require(decomposition, r"semantic.*(?:build\\.rs|lib\\.rs\\.in).*(?:retired|removed)", '
        '"semantic generator retired")'
    )
    new_semantic = (
        'require(\n'
        '            decomposition,\n'
        '            r"semantic[\\s\\S]{0,240}(?:build\\.rs|lib\\.rs\\.in)[\\s\\S]{0,240}(?:retired|removed)",\n'
        '            "semantic generator retired",\n'
        '        )'
    )
    if source.count(old_semantic) != 1:
        raise RuntimeError("current-conformance semantic-generator anchor drift")
    source = source.replace(old_semantic, new_semantic)

    old_denominators = (
        '        require(web4_readme, r"release denominator.*none|world_release_denominator=none", '
        '"Web4 denominator none")'
    )
    new_denominators = (
        '        require(\n'
        '            platform_readme,\n'
        '            r"^Release denominator:\\s*\\*\\*none\\*\\*\\s*$",\n'
        '            "platform release denominator none",\n'
        '        )\n'
        '        require(\n'
        '            web4_readme,\n'
        '            r"^World game-product release denominator:\\s*\\*\\*none\\*\\*\\s*$",\n'
        '            "Web4 denominator none",\n'
        '        )'
    )
    if source.count(old_denominators) != 1:
        raise RuntimeError("current-conformance denominator anchor drift")
    source = source.replace(old_denominators, new_denominators)

    old_authority = (
        '        require(project_boundary, r"Nakama.*canonical online", "Nakama canonical online authority")\n'
        '        require(project_boundary, r"CEX.*wallet/ledger.*custody", "CEX custody authority")\n'
        '        require(project_boundary, r"public online.*NO-GO|public online.*remain.*disabled", '
        '"public online disabled")'
    )
    new_authority = (
        '        require(\n'
        '            project_boundary,\n'
        '            r"^Trillionnium Nakama owns canonical online authority\\.$",\n'
        '            "Nakama canonical online authority",\n'
        '        )\n'
        '        require(\n'
        '            project_boundary,\n'
        '            r"^CEX owns wallet/ledger settlement and custody\\.$",\n'
        '            "CEX custody authority",\n'
        '        )\n'
        '        require(project_boundary, r"^Public online remains NO-GO\\b", "public online disabled")'
    )
    if source.count(old_authority) != 1:
        raise RuntimeError("current-conformance authority anchor drift")
    current_conformance.write_text(source.replace(old_authority, new_authority), encoding="utf-8")

    boundary = root / "PROJECT_BOUNDARY.md"
    boundary_source = boundary.read_text(encoding="utf-8", errors="strict")
    boundary_anchor = (
        "Those responsibilities belong to Trillionnium Nakama, Trillionnium Chain, CEX and "
        "Trillionnium Integration as defined by the accepted ADRs.\n"
    )
    canonical = (
        boundary_anchor
        + "\nTrillionnium Nakama owns canonical online authority.\n"
        + "CEX owns wallet/ledger settlement and custody.\n"
        + "Public online remains NO-GO until every applicable release denominator is independently closed.\n"
    )
    if boundary_source.count(boundary_anchor) != 1:
        raise RuntimeError("project-boundary authority insertion anchor drift")
    boundary.write_text(boundary_source.replace(boundary_anchor, canonical), encoding="utf-8")

    print(
        "WORLD_FINAL_SOURCE_INTEGRITY_REPAIR=PASS "
        "detailed_documentation_cli=option-root "
        "semantic_generator_boundary=bounded-multiline "
        "lifecycle_denominators=canonical-prose "
        "authority_decisions=anchored-lines"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
