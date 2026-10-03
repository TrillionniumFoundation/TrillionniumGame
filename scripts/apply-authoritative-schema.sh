#!/usr/bin/env bash
# The Rust engine is the sole executor of the locked authoritative SQL chain.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
mode=${1:-}
case "$mode" in migrate|verify) ;; *) echo 'usage: apply-authoritative-schema.sh migrate|verify' >&2; exit 64 ;; esac
test "$#" -eq 1
: "${TRNM_DATABASE_URL:?TRNM_DATABASE_URL is required}"
: "${TRNM_DATABASE_PROFILE:?TRNM_DATABASE_PROFILE is required}"
export TRNM_DATABASE_URL TRNM_DATABASE_PROFILE
if [[ "$mode" == migrate ]]; then
  export TRNM_SCHEMA_SOURCE_COMMIT=${TRNM_SCHEMA_SOURCE_COMMIT:-$(git rev-parse HEAD)}
fi
cargo build --locked -p trnm-persistence-pg --bin trnm-schema >&2
exec "${CARGO_TARGET_DIR:-target}/debug/trnm-schema" "$mode" --candidate-plaintext
