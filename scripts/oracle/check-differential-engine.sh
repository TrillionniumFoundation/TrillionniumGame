#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root"
export PYTHONDONTWRITEBYTECODE=1
python3 -m unittest discover -s tests/oracle -p 'test_differential.py' -v
python3 -m compileall -q tools/oracle scripts/oracle tests/oracle
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
python3 scripts/oracle/emit-synthetic-differential-corpus.py \
  --output-dir "$work/equivalent"
python3 scripts/oracle/compare-differential.py \
  --input-dir "$work/equivalent" \
  --normalizers config/oracle-normalizers.json \
  --output "$work/equivalent-evidence.json"
python3 scripts/oracle/emit-synthetic-differential-corpus.py \
  --output-dir "$work/divergent" \
  --database-divergence
if python3 scripts/oracle/compare-differential.py \
    --input-dir "$work/divergent" \
    --normalizers config/oracle-normalizers.json \
    --output "$work/divergent-evidence.json"; then
  echo 'P0 database-effect divergence was not rejected' >&2
  exit 1
fi
python3 - "$work/equivalent-evidence.json" "$work/divergent-evidence.json" <<'PY'
import json, sys
clean=json.load(open(sys.argv[1],encoding='utf-8'))
bad=json.load(open(sys.argv[2],encoding='utf-8'))
assert clean['divergence_counts']=={'P0':0,'P1':0,'P2':0,'P3':0}
assert bad['divergence_counts']['P0']>0
assert not any(clean['claims'].values())
assert not any(bad['claims'].values())
PY
