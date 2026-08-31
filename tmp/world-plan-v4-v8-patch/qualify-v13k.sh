#!/usr/bin/env bash
set -euo pipefail

world="${1:?world checkout required}"
control="${2:?control checkout required}"
export_root="${3:-$PWD/export}"
outer=/tmp/trnm-world-qualify-v13k-outer.sh
cp "$control/tmp/world-plan-v4-v8-patch/qualify-v13c.sh" "$outer"

python3 - "$outer" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = 'cp "$control/tmp/world-plan-v4-v8-patch/qualify-v13.sh" "$inner"\n'
new = '''base="$control/tmp/world-plan-v4-v8-patch/qualify-v13.sh"
patched_base=/tmp/trnm-world-qualify-v13k-base.sh
cp "$base" "$patched_base"
python3 - "$patched_base" <<'INNERPY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old_hash = "bc70a73f55796d17251e395bca04ab44d0da308d"
new_hash = "f56b9e64b1c257ff783bd40a4d0b9fea7b8a9bad"
if text.count(old_hash) != 1:
    raise SystemExit(f"transition patch identity binding drifted: {text.count(old_hash)}")
text = text.replace(old_hash, new_hash, 1)
anchor = 'git -C "$world" apply "$transition_patch"\\n'
addition = '''git -C "$world" apply "$transition_patch"

# Preserve the allocation-free public result API while documenting the
# intentional accepted/rejected size asymmetry under strict Clippy.
transition_layout="$control/tmp/world-plan-v4-v8-patch/apply-transition-result-layout-compat.py"
test "$(git -C "$control" hash-object "$transition_layout")" = 9c24b2e552a938c3239f5702afe49d1a763cd713
python3 "$transition_layout" "$world"
'''
if text.count(anchor) != 1:
    raise SystemExit(f"transition patch application hook drifted: {text.count(anchor)}")
path.write_text(text.replace(anchor, addition, 1), encoding="utf-8")
INNERPY
cp "$patched_base" "$inner"
'''
if text.count(old) != 1:
    raise SystemExit(f"v13c base hook drifted: {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
PY

bash "$outer" "$world" "$control" "$export_root"
