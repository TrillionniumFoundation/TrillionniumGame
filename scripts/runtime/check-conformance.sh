#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root"
export PYTHONDONTWRITEBYTECODE=1
python3 -m unittest discover -s tests/runtime -p 'test_conformance.py' -v
python3 -m compileall -q tools/runtime scripts/runtime/compare-engine-corpus.py tests/runtime
python3 - <<'PY'
import json
policy=json.load(open('config/runtime-engine-conformance-policy.json'))
corpus=json.load(open('corpus/runtime/runtime-engine-corpus.v1.json'))
assert len(corpus['cases']) >= 20
assert not any(policy['claims'].values())
assert not any(corpus['claims'].values())
PY
