#!/usr/bin/env bash
set -euo pipefail

root=$(git rev-parse --show-toplevel)
cd "$root"

run_root=${TRNM_SERVER_SMOKE_ROOT:-run/server-process-smoke}
rm -rf "$run_root"
mkdir -p "$run_root"

cargo build \
  --manifest-path crates/trnm-server/Cargo.toml \
  --locked \
  --bin trnm-server-composition-candidate \
  >"$run_root/build.log" 2>&1

binary=crates/trnm-server/target/debug/trnm-server-composition-candidate
test -x "$binary"

export TRNM_SERVER_BIND=127.0.0.1:17350
export TRNM_SERVER_GRPC_BIND=127.0.0.1:17351
export TRNM_SERVER_DATABASE_URL='postgresql://smoke:smoke-password@127.0.0.1:1/smoke'
export TRNM_SERVER_DATABASE_PROFILE=postgresql
export TRNM_SERVER_DATABASE_TLS_MODE=plaintext-candidate
export TRNM_SERVER_ALLOW_PLAINTEXT_DATABASE=true
export TRNM_SERVER_SCHEMA_SOURCE_COMMIT=0000000000000000000000000000000000000000
export TRNM_SERVER_ADMIN_TOKEN=0123456789abcdef0123456789abcdef

"$binary" check-config >"$run_root/check-config.out" 2>"$run_root/check-config.err"
grep -qx 'trnm-server configuration valid' "$run_root/check-config.out"
test ! -s "$run_root/check-config.err"
! grep -Eq 'ServerConfig|database_url|admin_token|postgresql://|smoke-password' "$run_root/check-config.out"
! grep -Eq 'ServerConfig|database_url|admin_token|postgresql://|smoke-password' "$run_root/check-config.err"

set +e
env -u TRNM_SERVER_DATABASE_URL "$binary" check-config \
  >"$run_root/missing-database.out" 2>"$run_root/missing-database.err"
missing_status=$?
set -e
test "$missing_status" -ne 0
grep -q 'database_url_missing' "$run_root/missing-database.err"
! grep -q 'smoke-password' "$run_root/missing-database.err"

set +e
timeout 10s "$binary" serve >"$run_root/serve.out" 2>"$run_root/serve.err"
serve_status=$?
set -e
test "$serve_status" -ne 0
test "$serve_status" -ne 124
! grep -q 'smoke-password' "$run_root/serve.out"
! grep -q 'smoke-password' "$run_root/serve.err"

sha256sum \
  "$run_root/build.log" \
  "$run_root/check-config.out" \
  "$run_root/check-config.err" \
  "$run_root/missing-database.out" \
  "$run_root/missing-database.err" \
  "$run_root/serve.out" \
  "$run_root/serve.err" \
  > "$run_root/SHA256SUMS"
printf '%s\n' \
  'status=passed' \
  'process_binary_verified=true' \
  'process_configuration_verified=true' \
  'fail_closed_startup_verified=true' \
  'secret_redaction_verified=true' \
  'process_ingress_verified=false' \
  'graceful_shutdown_verified=false' \
  'database_durability_verified=false' \
  'compatibility_credit=false' \
  'production_ready=false' \
  > "$run_root/result.txt"
