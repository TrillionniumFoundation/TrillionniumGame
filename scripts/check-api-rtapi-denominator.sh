#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
python3 -m unittest discover -s tests -p 'test_api_rtapi_denominator.py' -v
python3 -m compileall -q scripts tests
