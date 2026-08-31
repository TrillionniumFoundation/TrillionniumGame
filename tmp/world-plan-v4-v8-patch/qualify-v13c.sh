#!/usr/bin/env bash
set -euo pipefail

world="${1:?world checkout required}"
control="${2:?control checkout required}"
export_root="${3:-$PWD/export}"
inner=/tmp/trnm-world-qualify-v13d-inner.sh
cp "$control/tmp/world-plan-v4-v8-patch/qualify-v13.sh" "$inner"

python3 - "$inner" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = 'compat="$control/tmp/world-plan-v4-v8-patch/apply-partition-compat.py"\n'
new = 'compat="$control/tmp/world-plan-v4-v8-patch/apply-partition-compat-v3.py"\n'
if text.count(old) != 1:
    raise SystemExit(f"v13 compatibility hook drifted: {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
PY

bash "$inner" "$world" "$control" "$export_root"

# Intent-to-add makes the unified patch include every new ownership part while
# leaving the source candidate uncommitted and fully auditable.
git -C "$world" add -N .
git -C "$world" diff --binary > "$export_root/world-v13-source.patch"
test "$(grep -c '^diff --git ' "$export_root/world-v13-source.patch")" -ge 60
find "$export_root" -type f -print0 | sort -z | xargs -0 sha256sum > "$export_root/SHA256SUMS"
printf 'world_v13_complete_patch_sha256=%s\n' \
  "$(sha256sum "$export_root/world-v13-source.patch" | awk '{print $1}')"
printf 'TRNM_WORLD_V13D_COMPLETE_EXPORT=PASS\n'
