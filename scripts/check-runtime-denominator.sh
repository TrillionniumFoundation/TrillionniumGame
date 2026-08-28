#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
export PYTHONDONTWRITEBYTECODE=1
python3 -m unittest discover -s tests/denominator -p 'test_runtime_denominator.py' -v
python3 -m compileall -q scripts/generate-runtime-denominator.py tests/denominator tools/denominator
if [[ -n $(gofmt -l tools/denominator/go_runtime_surface.go) ]]; then
  echo 'Go Runtime AST extractor is not gofmt-clean' >&2
  exit 1
fi
go run tools/denominator/go_runtime_surface.go >/tmp/trnm-runtime-helper.out 2>/tmp/trnm-runtime-helper.err && {
  echo 'Go helper unexpectedly accepted missing arguments' >&2
  exit 1
}
grep -q 'usage: go_runtime_surface' /tmp/trnm-runtime-helper.err
