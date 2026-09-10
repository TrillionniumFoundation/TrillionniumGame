#!/usr/bin/env python3
"""Repair deterministic World source blockers discovered by exact qualification.

This script is intentionally bound to the current World V6 source shape. It:

1. preserves WebSocket close semantics while avoiding Rust 1.98.1's
   `result_large_err` warning from a discarded closure result;
2. gives the authority-cutover workflow unique non-protected context names so
   it cannot accidentally satisfy the three protected admission contexts;
3. reconciles the closed-world CI inventory with all fourteen tracked workflow
   files and forty-one unique static job contexts;
4. updates the aggregate documentation gate for the detailed checker's
   option-based `--root` interface; and
5. updates the inventory regression test without weakening any fail-closed
   checks.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    source = path.read_text(encoding="utf-8", errors="strict")
    if source.count(old) != 1:
        raise RuntimeError(f"{label}: expected one anchor, observed {source.count(old)}")
    path.write_text(source.replace(old, new), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)

    online = root / "trillionnium/crates/trnm-game-server/src/bin/trnm-online-e2e.rs"
    replace_once(
        online,
        "    let _ = tokio::task::block_in_place(|| state_stream.close(None));",
        "    tokio::task::block_in_place(|| {\n        let _ = state_stream.close(None);\n    });",
        "WebSocket close Clippy repair",
    )

    authority = root / ".github/workflows/trnm-world-authority-cutover.yml"
    replace_once(
        authority,
        "    name: trnm-world-p0-boundaries",
        "    name: trnm-world-authority-cutover/source-qualification",
        "authority source context",
    )
    replace_once(
        authority,
        "    name: trnm-world-status-evidence",
        "    name: trnm-world-authority-cutover/status-evidence",
        "authority status context",
    )

    checker = root / "scripts/check-trnm-world-ci-integrity.py"
    checker_source = checker.read_text(encoding="utf-8", errors="strict")
    inventory = '''WORKFLOW_JOBS = {
    "trnm-world-authority-cutover.yml": {
        "authority-source-qualification": "trnm-world-authority-cutover/source-qualification",
        "authority-status-evidence": "trnm-world-authority-cutover/status-evidence",
    },
    "trnm-world-cex-sequence-50-qualification.yml": {
        "static-lock-contract": "trnm-world-v5/cex-lock-contract",
        "live-upstream-qualification": "trnm-world-v5/cex-sequence-50-live",
    },
    "trnm-world-gap-closure-v4.yml": {
        name: "trnm-world-v4/" + name for name in (
            "docs-governance", "transition-contract", "settlement-postgres",
            "game-workspace-release", "supply-chain")
    },
    "trnm-world-module-documentation.yml": {
        "module-documentation": "trnm-world/module-documentation",
    },
    "trnm-world-postgres-cutover.yml": {
        "postgres-cutover": "trnm-world-postgres-cutover",
    },
    "trnm-world-repository-contract-v2.yml": {
        "repository-contract": "trnm-world-v5/repository-contract",
    },
    "trnm-world-repository-contract-v3.yml": {
        "repository-contract": "trnm-world-v5/repository-contract-v3",
    },
    "trnm-world-rts-intake-contract.yml": {
        "intake-contract": "trnm-world-v4/rts-intake-contract",
    },
    "trnm-world-v4-final-gates.yml": {
        **{name: "trnm-world-v4-supplemental/" + name for name in (
            "docs-governance", "transition-contract", "settlement-postgres",
            "game-workspace-release", "supply-chain")},
        "qualified-source-exact-head": "trnm-world-v4/qualified-source-exact-head",
        "closure-contract": "trnm-world-v5-supplemental/closure-contract",
        "prospective-merge": "trnm-world-v4/prospective-merge",
    },
    "trnm-world-v5-closure-contract.yml": {
        "closure-contract": "trnm-world-v5/closure-contract",
    },
    "trnm-world-v6-admission-v2.yml": {
        "truth": "trnm-world-v6/truth-head-and-merge",
        "source": "trnm-world-v6/source-head-and-merge",
        "postgres": "trnm-world-v6/postgres-head-and-merge",
        "package": "trnm-world-v6/package-head-and-merge",
        "required": "trnm-world-v6/required-v2",
    },
    "trnm-world-v6-admission.yml": {
        "truth-head": "trnm-world-v6/docs-governance-exact-head",
        "truth-merge": "trnm-world-v6/docs-governance-prospective-merge",
        "source-head": "trnm-world-v6/source-exact-head",
        "source-merge": "trnm-world-v6/source-prospective-merge",
        "postgres-head": "trnm-world-v6/postgres-exact-head",
        "postgres-merge": "trnm-world-v6/postgres-prospective-merge",
        "package-head": "trnm-world-v6/supply-chain-package-exact-head",
        "package-merge": "trnm-world-v6/supply-chain-package-prospective-merge",
        "required": "trnm-world-v6/required",
    },
    "trnm-world-v6-external-evidence-contract.yml": {
        "external-evidence-contract": "trnm-world-v6/external-evidence-contract",
    },
    "world-pr-native-admission-v1.yml": {
        "game": "trnm-game-ci",
        "boundaries": "trnm-world-p0-boundaries",
        "status": "trnm-world-status-evidence",
    },
}'''
    pattern = re.compile(
        r"WORKFLOW_JOBS = \{\n.*?\n\}\n\n\ndef workflow_inventory",
        re.DOTALL,
    )
    checker_source, count = pattern.subn(
        inventory + "\n\n\ndef workflow_inventory",
        checker_source,
    )
    if count != 1:
        raise RuntimeError(f"CI inventory mapping anchor drift: {count}")
    old_message = 'fail("active workflow inventory differs from the reviewed ten files")'
    new_message = 'fail("active workflow inventory differs from the reviewed closed-world mapping")'
    if checker_source.count(old_message) != 1:
        raise RuntimeError("CI inventory failure-message anchor drift")
    checker.write_text(checker_source.replace(old_message, new_message), encoding="utf-8")

    tests = root / "scripts/test-trnm-world-ci-integrity.py"
    test_source = tests.read_text(encoding="utf-8", errors="strict")
    constants_anchor = 'POSTGRES = "trnm-world-postgres-cutover.yml"\n'
    constants = (
        constants_anchor
        + 'V6 = "trnm-world-v6-admission.yml"\n'
        + 'V6_V2 = "trnm-world-v6-admission-v2.yml"\n'
        + 'EXTERNAL = "trnm-world-v6-external-evidence-contract.yml"\n'
        + 'NATIVE = "world-pr-native-admission-v1.yml"\n'
    )
    if test_source.count(constants_anchor) != 1:
        raise RuntimeError("CI test constants anchor drift")
    test_source = test_source.replace(constants_anchor, constants)

    replacement = '''    def test_reviewed_fourteen_workflows_have_forty_one_unique_contexts(self):
        contexts = checker.workflow_inventory(self.root)
        self.assertEqual(len(contexts), 41)
        for job in ("docs-governance", "transition-contract", "settlement-postgres", "game-workspace-release", "supply-chain"):
            self.assertEqual(contexts["trnm-world-v4/" + job], GAP)
        self.assertEqual(contexts["trnm-world-v5/closure-contract"], "trnm-world-v5-closure-contract.yml")
        self.assertEqual(contexts["trnm-world-authority-cutover/source-qualification"], AUTHORITY)
        self.assertEqual(contexts["trnm-world-authority-cutover/status-evidence"], AUTHORITY)
        self.assertEqual(contexts["trnm-world-p0-boundaries"], NATIVE)
        self.assertEqual(contexts["trnm-world-status-evidence"], NATIVE)
        self.assertEqual(contexts["trnm-game-ci"], NATIVE)
        self.assertEqual(contexts["trnm-world-postgres-cutover"], POSTGRES)
        self.assertEqual(contexts["trnm-world-v6/required"], V6)
        self.assertEqual(contexts["trnm-world-v6/required-v2"], V6_V2)
        self.assertEqual(contexts["trnm-world-v6/external-evidence-contract"], EXTERNAL)
'''
    test_pattern = re.compile(
        r"    def test_reviewed_ten_workflows_have_twenty_three_unique_contexts\(self\):\n"
        r".*?(?=\n    def test_ephemeral_commit_tree_is_allowed_but_source_commit_is_not)",
        re.DOTALL,
    )
    test_source, count = test_pattern.subn(replacement.rstrip(), test_source)
    if count != 1:
        raise RuntimeError(f"CI inventory test anchor drift: {count}")
    tests.write_text(test_source, encoding="utf-8")

    documentation = root / "scripts/check-trnm-world-documentation.py"
    replace_once(
        documentation,
        '("scripts/check-trnm-world-detailed-documentation.py", [str(ROOT)]),',
        '("scripts/check-trnm-world-detailed-documentation.py", ["--root", str(ROOT)]),',
        "detailed documentation aggregate CLI",
    )

    print("WORLD_SOURCE_INTEGRITY_REPAIR=PASS workflows=14 contexts=41 documentation_cli=option-root")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
