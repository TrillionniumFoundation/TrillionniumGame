#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PKG="$ROOT/runtime/internal/worldcommand"
STATUS="$ROOT/contracts/world-command-runtime-v1-status.json"
EVIDENCE_SCHEMA="$ROOT/contracts/world-command-fault-evidence-v1.schema.json"
DOC="$ROOT/docs/ARCHITECTURE.md"
SUMMARY_TOOL="$ROOT/tools/summarize_world_command_faults.py"

fail() { printf 'world-command-runtime-v1: %s\n' "$*" >&2; exit 1; }

for path in \
  "$PKG/types.go" \
  "$PKG/codec.go" \
  "$PKG/coordinator.go" \
  "$PKG/backend.go" \
  "$PKG/store_base.go" \
  "$PKG/store_prepare.go" \
  "$PKG/store_commit.go" \
  "$PKG/store_ops.go" \
  "$PKG/validation.go" \
  "$PKG/persistence.go" \
  "$PKG/helpers.go" \
  "$PKG/test_helpers_test.go" \
  "$PKG/fault_test.go" \
  "$PKG/idempotency_test.go" \
  "$PKG/concurrency_test.go" \
  "$STATUS" \
  "$EVIDENCE_SCHEMA" \
  "$DOC" \
  "$SUMMARY_TOOL"; do
  [[ -f "$path" ]] || fail "missing ${path#$ROOT/}"
done

grep -q 'Runtime modules receive explicit capabilities' "$DOC" \
  || fail 'current architecture omits Runtime capability boundary'
grep -q 'The same session family, party, ticket, match, scheduler, IAP transaction or durable command never has two writable owners' "$DOC" \
  || fail 'current architecture omits single-writer migration boundary'

python3 - "$STATUS" "$EVIDENCE_SCHEMA" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
schema_path = pathlib.Path(sys.argv[2])
data = json.loads(path.read_text())
schema = json.loads(schema_path.read_text())
if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
    raise SystemExit("fault evidence schema draft drift")
if schema.get("properties", {}).get("repository", {}).get("const") != "TrillionniumFoundation/TrillionniumGame":
    raise SystemExit("fault evidence repository drift")
if data.get("contract_version") != "trnm_game_world_command_runtime_delivery_v1":
    raise SystemExit("status contract drift")
if data.get("owner") != "TrillionniumFoundation/TrillionniumGame":
    raise SystemExit("repository rename is not reflected in owner")
if data.get("activation") != "shadow_only":
    raise SystemExit("source tranche may only be shadow_only")
authority = data.get("authority", {})
for key in (
    "world_can_set_global_sequence",
    "world_can_set_match_version",
    "world_can_set_participant_sequence",
    "world_can_set_idempotency",
    "world_can_sign_completion",
    "world_can_claim_chain_finality",
    "world_can_settle_value",
    "cutover_authorized",
    "closed_online_promotion",
    "public_online_enabled",
    "public_player_market_enabled",
):
    if authority.get(key) is not False:
        raise SystemExit(f"authority/release overclaim: {key}")
checks = {row.get("id"): row for row in data.get("acceptance", [])}
for check_id in (
    "WORLD-P0-004-A1",
    "WORLD-P0-004-A2",
    "WORLD-P0-004-A3",
    "WORLD-P0-004-A4",
    "WORLD-P0-004-A5",
    "WORLD-P0-004-A6",
    "WORLD-P0-004-A7",
    "WORLD-P0-004-A8",
):
    if check_id not in checks:
        raise SystemExit(f"missing acceptance row {check_id}")
for check_id in ("WORLD-P0-004-A6", "WORLD-P0-004-A7", "WORLD-P0-004-A8"):
    if checks[check_id].get("state") != "pending":
        raise SystemExit(f"external evidence row overclaimed: {check_id}")
PY

if grep -R -nE '"(net|net/http|database/sql|crypto/ed25519|os/exec|math/rand)"' \
  "$PKG" --include='*.go'; then
  fail 'coordinator package acquired a forbidden direct capability'
fi

grep -q 'Store.Prepare' "$PKG/coordinator.go" || fail 'prepare boundary missing'
grep -q 'Executor.Execute' "$PKG/coordinator.go" || fail 'external execute boundary missing'
grep -q 'codec.Verify' "$PKG/coordinator.go" || fail 'verify boundary missing'
grep -q 'Store.Commit' "$PKG/coordinator.go" || fail 'commit boundary missing'
grep -q 'context.WithoutCancel' "$PKG/coordinator.go" \
  || fail 'verified-result cleanup is caller-cancellable'
grep -q 'ErrStaleReservation' "$PKG"/store_*.go || fail 'stale fencing missing'
grep -q 'CompareAndSwap' "$PKG"/store_*.go || fail 'CAS persistence missing'
grep -q 'TestResponseLossReusesExactRequestAcrossRestart' "$PKG"/*_test.go \
  || fail 'response-loss restart test missing'
grep -q 'TestConcurrentReservationsProduceOneCommitAndOneStale' "$PKG"/*_test.go \
  || fail 'concurrent stale test missing'
grep -q 'TestPersistenceFailureLeavesStateAndReservationUnchanged' "$PKG"/*_test.go \
  || fail 'persistence rollback test missing'
grep -q 'TestTwoWorkersConvergeOnOneReceipt' "$PKG"/*_test.go \
  || fail 'two-worker convergence test missing'
grep -q 'TestCommittedDuplicateSurvivesAdvancedAuthorityCursor' "$PKG"/*_test.go \
  || fail 'advanced duplicate replay test missing'
grep -q 'REQUIRED_TESTS' "$SUMMARY_TOOL" \
  || fail 'machine evidence required-scenario set missing'
python3 -m py_compile "$SUMMARY_TOOL"

printf '%s\n' 'world-command-runtime-v1 source contract passed'
