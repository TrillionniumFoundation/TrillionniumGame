#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
export PYTHONDONTWRITEBYTECODE=1
python3 -m unittest discover -s tests/denominator -p 'test_review_lock.py' -v
python3 -m compileall -q tools/denominator scripts/review-denominator-bundle.py tests/denominator
