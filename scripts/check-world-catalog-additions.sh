#!/usr/bin/env bash
set -euo pipefail
world_dir=${1:-world}
cd "$world_dir"
python3 scripts/check-trnm-world-component-catalog.py
python3 scripts/test-trnm-world-component-catalog.py
python3 scripts/check-trnm-world-catalog-additions.py
