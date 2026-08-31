#!/usr/bin/env bash
set -euo pipefail

world="${1:?world checkout required}"
control="${2:?control checkout required}"
export_root="${3:-$PWD/export}"
inner=/tmp/trnm-world-qualify-v13j-inner.sh
cp "$control/tmp/world-plan-v4-v8-patch/qualify-v13c.sh" "$inner"

python3 - "$inner" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = 'cp "$control/tmp/world-plan-v4-v8-patch/qualify-v13.sh" "$inner"\n'
new = '''base="$control/tmp/world-plan-v4-v8-patch/qualify-v13.sh"
patched_base=/tmp/trnm-world-qualify-v13j-base.sh
cp "$base" "$patched_base"
python3 - "$patched_base" <<'INNERPY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = "bc70a73f55796d17251e395bca04ab44d0da308d"
new = "f56b9e64b1c257ff783bd40a4d0b9fea7b8a9bad"
if text.count(old) != 1:
    raise SystemExit(f"transition patch identity binding drifted: {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
INNERPY
cp "$patched_base" "$inner"
'''
if text.count(old) != 1:
    raise SystemExit(f"v13c base hook drifted: {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
PY

bash "$inner" "$world" "$control" "$export_root"
