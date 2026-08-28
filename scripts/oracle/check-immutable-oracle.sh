#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root"
python3 scripts/oracle/check-immutable-oracle.py
python3 -m unittest discover -s tests/oracle -p 'test_*.py' -v
python3 -m compileall -q scripts/oracle tests/oracle
bash -n scripts/oracle/run-immutable-smoke.sh scripts/oracle/check-immutable-oracle.sh
