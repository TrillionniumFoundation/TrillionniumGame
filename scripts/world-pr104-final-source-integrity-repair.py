#!/usr/bin/env python3
"""Repair final deterministic World source-integrity failures.

The repairs are deliberately narrow:

* align every detailed-documentation subprocess caller with the checker's
  option-based ``--root`` interface; and
* make the current-conformance semantic-generator assertion tolerate ordinary
  Markdown line wrapping while retaining a small bounded match window.
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
    replace_once(
        current_conformance,
        'require(decomposition, r"semantic.*(?:build\\.rs|lib\\.rs\\.in).*(?:retired|removed)", "semantic generator retired")',
        'require(\n'
        '            decomposition,\n'
        '            r"semantic[\\s\\S]{0,240}(?:build\\.rs|lib\\.rs\\.in)[\\s\\S]{0,240}(?:retired|removed)",\n'
        '            "semantic generator retired",\n'
        '        )',
        "current-conformance wrapped semantic-generator assertion",
    )

    print(
        "WORLD_FINAL_SOURCE_INTEGRITY_REPAIR=PASS "
        "detailed_documentation_cli=option-root "
        "semantic_generator_boundary=bounded-multiline"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
