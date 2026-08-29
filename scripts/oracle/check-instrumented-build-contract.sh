#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root"
export PYTHONDONTWRITEBYTECODE=1
python3 -m unittest discover -s tests/oracle -p 'test_instrumented_build_*.py' -v
python3 -m compileall -q tools/oracle scripts/oracle tests/oracle
bash -n scripts/oracle/check-instrumented-build-contract.sh
