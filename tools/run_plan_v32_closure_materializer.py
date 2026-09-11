#!/usr/bin/env python3
"""Run the closure materializer with layout-neutral schema quarantine guards."""
from __future__ import annotations

import argparse
import importlib.util
import re
from pathlib import Path
from types import ModuleType


def load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("plan_v32_materializer", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("materializer loader unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def repair_schema_authority_guards(module: ModuleType, root: Path) -> None:
    pattern = re.compile(
        r'''(?mx)
        ^(?P<indent>[ \t]*)
        require\(
          ["']database/schema/v2["']
          \s+not\s+in\s+(?P<subject>[A-Za-z_][A-Za-z0-9_]*)\s*,\s*
          ["']non-authoritative\ schema\ referenced["']
        \)\s*$
        '''
    )
    for relative in module.SCHEMA_AUTHORITY_NEGATIVE_CHECKERS:
        path = root / relative
        text = path.read_text(encoding="utf-8")
        matches = list(pattern.finditer(text))
        if len(matches) != 1:
            raise RuntimeError(
                f"{relative}: expected one schema quarantine guard, found {len(matches)}"
            )
        match = matches[0]
        indent = match.group("indent")
        subject = match.group("subject")
        replacement = "\n".join(
            (
                f'{indent}authority_path = ROOT / "docs/development/SCHEMA_AUTHORITY.json"',
                f'{indent}authority = __import__("json").loads(authority_path.read_text(encoding="utf-8"))',
                f'{indent}quarantines = authority.get("non_authoritative")',
                f'{indent}require(isinstance(quarantines, list) and quarantines, "schema quarantine registry missing")',
                f'{indent}for quarantine in quarantines:',
                f'{indent}    require(isinstance(quarantine, dict), "schema quarantine row invalid")',
                f'{indent}    quarantine_path = quarantine.get("path")',
                f'{indent}    require(isinstance(quarantine_path, str) and quarantine_path, "schema quarantine path invalid")',
                f'{indent}    require(quarantine_path not in {subject}, "non-authoritative schema referenced")',
            )
        )
        text, count = pattern.subn(replacement, text, count=1)
        if count != 1 or "database/schema/v2" in text:
            raise RuntimeError(f"{relative}: schema quarantine replacement failed")
        path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--controller", type=Path, required=True)
    parser.add_argument("--materializer", type=Path, required=True)
    args = parser.parse_args()

    module = load_module(args.materializer.resolve())
    module.repair_schema_authority_guards = lambda root: repair_schema_authority_guards(
        module, root
    )
    module.materialize(args.root.resolve(), args.controller.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
