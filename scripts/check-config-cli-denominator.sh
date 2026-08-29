#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
export PYTHONDONTWRITEBYTECODE=1
python3 -m unittest discover -s tests/denominator -p 'test_config_cli_denominator.py' -v
python3 -m compileall -q scripts/generate-config-cli-denominator.py tests/denominator
if [[ -n $(gofmt -l tools/denominator/go_config_surface.go) ]]; then
  echo 'Go config/CLI AST extractor is not gofmt-clean' >&2
  exit 1
fi
go run tools/denominator/go_config_surface.go >/tmp/trnm-config-helper.out 2>/tmp/trnm-config-helper.err && {
  echo 'Go config helper unexpectedly accepted missing arguments' >&2
  exit 1
}
grep -q 'usage: go_config_surface' /tmp/trnm-config-helper.err
