#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
export PYTHONDONTWRITEBYTECODE=1
python3 -m unittest discover -s tests/denominator -p 'test_database_denominator.py' -v
python3 -m compileall -q tools/denominator scripts/generate-database-denominator.py tests/denominator
python3 scripts/generate-database-denominator.py --help >/dev/null
