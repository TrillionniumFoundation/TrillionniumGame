#!/usr/bin/env python3
"""Run the v9 identity repin with an exact diff-derived workflow set.

The previous numeric cap (12) was a diagnostic heuristic. Canonical server
migration updates references in many registered workflow definitions, so the
safe invariant is equality with the registered workflow paths that actually
differ from the exact Plan head—not an arbitrary cardinality ceiling.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

SOURCE = Path(__file__).with_name("repin-canonical-server-wave-v9.py")
OLD = '''    if len(changed_paths) > 12:
        raise SystemExit(f"unexpectedly broad workflow repin set: {len(changed_paths)}")
'''
NEW = '''    registered_paths = set(actual_by_path)
    changed_worktree_paths = set(
        subprocess.check_output(
            [
                "git",
                "-C",
                str(ROOT),
                "diff",
                "--name-only",
                PLAN_SHA,
                "--",
                ".github/workflows",
            ],
            text=True,
        ).splitlines()
    )
    expected_changed_paths = sorted(changed_worktree_paths & registered_paths)
    if changed_paths != expected_changed_paths:
        raise SystemExit(
            "workflow repin set does not equal the registered definitions changed "
            f"from the exact Plan head: repin={changed_paths}, expected={expected_changed_paths}"
        )
'''

text = SOURCE.read_text(encoding="utf-8")
if text.count(OLD) != 1:
    raise SystemExit("v9 workflow-repin cardinality anchor drift")
patched = text.replace(OLD, NEW, 1)
target = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "repin-canonical-server-wave-v10-expanded.py"
target.write_text(patched, encoding="utf-8")
os.execv(sys.executable, [sys.executable, str(target), *sys.argv[1:]])
