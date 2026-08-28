#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root"
export PYTHONDONTWRITEBYTECODE=1
python3 -m unittest discover -s tests/oracle -p 'test_hook_inventory.py' -v
python3 -m compileall -q tools/oracle scripts/oracle/generate-hook-inventory.py tests/oracle/test_hook_inventory.py
python3 - <<'PY'
import json
policy = json.load(open('config/oracle-hook-inventory-policy.json'))
assert policy['upstream']['commit'] == 'd4d92f93f78bbbe62c7fc50a3f85c772ec121a09'
assert policy['upstream']['tree'] == 'f3c9cfc2726d5543da1564629170f35b98e3797d'
assert not any(policy['claims'].values())
PY
