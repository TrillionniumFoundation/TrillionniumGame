#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
export PYTHONDONTWRITEBYTECODE=1
python3 -m unittest discover -s tests/sdk -p 'test_*.py' -v
python3 -m compileall -q tools/sdk scripts tests/sdk
bash -n scripts/check-sdk-denominator.sh
