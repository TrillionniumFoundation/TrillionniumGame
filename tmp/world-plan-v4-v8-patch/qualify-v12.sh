#!/usr/bin/env bash
set -euo pipefail

control="${2:?control checkout required}"
base="$control/tmp/world-plan-v4-v8-patch/qualify-v8.sh"
inner=/tmp/trnm-world-qualify-v12-inner.sh
cp "$base" "$inner"

python3 - "$inner" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
replacements = [
    (
        "database_warning_patch=/tmp/settlement-database-warning.patch\n",
        "database_warning_patch=/tmp/settlement-database-warning.patch\n"
        "clippy_boundaries_patch=/tmp/clippy-scoped-boundaries.patch\n",
    ),
    (
        'cp "$control"/tmp/world-plan-v4-v8-patch/settlement-database-warning.patch "$database_warning_patch"\n',
        'cp "$control"/tmp/world-plan-v4-v8-patch/settlement-database-warning.patch "$database_warning_patch"\n'
        'cp "$control"/tmp/world-plan-v4-v8-patch/clippy-scoped-boundaries.patch "$clippy_boundaries_patch"\n',
    ),
    (
        'test "$(sha256sum "$database_warning_patch" | awk \'{print $1}\')" = 04a0e13df8aec6d96d1693352a64ff095c0e5e0fab8e13afedcfaf2b9503873a\n',
        'test "$(sha256sum "$database_warning_patch" | awk \'{print $1}\')" = 04a0e13df8aec6d96d1693352a64ff095c0e5e0fab8e13afedcfaf2b9503873a\n'
        'test "$(sha256sum "$clippy_boundaries_patch" | awk \'{print $1}\')" = 112b51285cbbf6249aa6b00499a12613fc8d4d9016efea31c5f0a05474663a94\n',
    ),
    (
        'git -C "$world" apply --check "$database_warning_patch"\n',
        'git -C "$world" apply --check "$database_warning_patch"\n'
        'git -C "$world" apply --check "$clippy_boundaries_patch"\n',
    ),
    (
        'git -C "$world" apply "$database_warning_patch"\n',
        'git -C "$world" apply "$database_warning_patch"\n'
        'git -C "$world" apply "$clippy_boundaries_patch"\n',
    ),
    (
        'world-v11-source.patch',
        'world-v12-source.patch',
    ),
    (
        "  'database_warning_patch_sha256=04a0e13df8aec6d96d1693352a64ff095c0e5e0fab8e13afedcfaf2b9503873a' \\\n",
        "  'database_warning_patch_sha256=04a0e13df8aec6d96d1693352a64ff095c0e5e0fab8e13afedcfaf2b9503873a' \\\n"
        "  'clippy_boundaries_patch_sha256=112b51285cbbf6249aa6b00499a12613fc8d4d9016efea31c5f0a05474663a94' \\\n",
    ),
    (
        'TRNM_WORLD_V11_CORE_QUALIFICATION=PASS',
        'TRNM_WORLD_V12_CORE_QUALIFICATION=PASS',
    ),
]
for old, new in replacements:
    if text.count(old) != 1:
        raise SystemExit(f"qualification source shape drifted for {old!r}: {text.count(old)}")
    text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
PY

bash "$inner" "$@"
