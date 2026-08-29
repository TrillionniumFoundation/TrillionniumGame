#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
export PYTHONDONTWRITEBYTECODE=1
python3 -m unittest discover -s tests/upstream -p 'test_*.py' -v
python3 -m compileall -q tools/upstream scripts/fetch-pinned-upstream.py tests/upstream
bash -n scripts/check-pinned-upstream-fetch.sh
