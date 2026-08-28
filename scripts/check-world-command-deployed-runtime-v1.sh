#!/usr/bin/env bash
set -euo pipefail

root=${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)}

fail() {
  printf 'World command deployed runtime gate failed: %s\n' "$*" >&2
  exit 1
}

required=(
  runtime/main.go
  runtime/world_authoritative_match.go
  runtime/world_command_config.go
  runtime/world_command_environment.go
  runtime/world_command_http.go
  runtime/world_command_storage.go
  runtime/world_command_storage_test.go
  runtime/world_command_rpc.go
  runtime/world_command_failpoint.go
  runtime/cmd/trnm-world-transition-fixture/main.go
  runtime/cmd/trnm-response-drop-proxy/main.go
  runtime/cmd/trnm-tls-fixture/main.go
  runtime/Dockerfile.faultlab
  runtime/internal/core/world_command.go
  runtime/internal/worldcommand/coordinator.go
  runtime/internal/worldcommand/store_commit.go
  runtime/internal/worldcommand/atomic_commit_test.go
  runtime/internal/worldcommand/fault_hook_test.go
  deploy/world-command-fault-lab/compose.yaml
  contracts/world-command-deployed-runtime-v1-status.json
  docs/WORLD_COMMAND_DEPLOYED_RUNTIME_V1.md
  contracts/world-command-rpc-v1/ready-response.schema.json
  contracts/world-command-rpc-v1/status-request.schema.json
  contracts/world-command-rpc-v1/status-response.schema.json
  contracts/world-command-rpc-v1/abort-request.schema.json
  contracts/world-command-rpc-v1/abort-response.schema.json
)
for path in "${required[@]}"; do
  [[ -f "$root/$path" ]] || fail "missing $path"
done

python3 - "$root" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
status = json.loads((root / "contracts/world-command-deployed-runtime-v1-status.json").read_text())
if status.get("contract_version") != "trnm_game_world_command_deployed_runtime_delivery_v1":
    raise SystemExit("unexpected deployed-runtime status contract")
if status.get("owner") != "TrillionniumFoundation/TrillionniumGame" or status.get("repository_id") != 1323087470:
    raise SystemExit("canonical repository identity drift")
if status.get("activation") != "target_profile_opt_in_only":
    raise SystemExit("target profile is not explicit opt-in")
authority = status.get("authority", {})
for field in (
    "world_can_authenticate_players",
    "world_can_assign_participant_roles",
    "world_can_set_global_sequence",
    "world_can_set_match_version",
    "world_can_set_participant_sequence",
    "world_can_set_idempotency",
    "world_can_create_canonical_roots",
    "world_can_sign_completion",
    "world_can_settle_value",
    "cutover_authorized",
    "closed_online_promotion",
    "public_online_enabled",
    "public_player_market_enabled",
):
    if authority.get(field) is not False:
        raise SystemExit(f"authority/release flag must remain false: {field}")
required_pending = {
    "deployed_postgresql_nakama_world_https_startup",
    "deployed_kill_after_reservation",
    "deployed_kill_after_verified_world_result",
    "deployed_response_loss_proxy_retry",
    "pg_stat_activity_and_pg_locks_external_wait_proof",
    "twenty_four_hour_active_endurance",
    "rollback_rehearsal_without_history_rewrite",
}
if not required_pending.issubset(set(status.get("pending_evidence", []))):
    raise SystemExit("deployed evidence blockers were removed")
if "Trillionnium Chain" not in set(status.get("scope_exclusions", [])):
    raise SystemExit("explicit Chain exclusion was removed")

schema_dir = root / "contracts/world-command-rpc-v1"
expected = {
    "ready-response.schema.json",
    "status-request.schema.json",
    "status-response.schema.json",
    "abort-request.schema.json",
    "abort-response.schema.json",
}
actual = {path.name for path in schema_dir.glob("*.schema.json")}
if actual != expected:
    raise SystemExit(f"World command RPC schema set mismatch: {sorted(actual)}")

def walk(value, location):
    if isinstance(value, dict):
        if value.get("type") == "object" and value.get("additionalProperties") is not False:
            raise SystemExit(f"{location}: object does not reject unknown fields")
        ref = value.get("$ref")
        if ref is not None and (not isinstance(ref, str) or not ref.startswith("#/")):
            raise SystemExit(f"{location}: non-local schema reference")
        for key, child in value.items():
            walk(child, f"{location}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            walk(child, f"{location}/{index}")

for name in sorted(expected):
    schema = json.loads((schema_dir / name).read_text())
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise SystemExit(f"{name}: wrong schema dialect")
    expected_id = f"https://github.com/TrillionniumFoundation/TrillionniumGame/contracts/world-command-rpc-v1/{name}"
    if schema.get("$id") != expected_id:
        raise SystemExit(f"{name}: schema ID drift")
    walk(schema, name)

ready = json.loads((schema_dir / "ready-response.schema.json").read_text())
for field in ("external_execution_under_lock", "cutover_authorized", "public_online_enabled", "public_player_market_enabled"):
    if ready["properties"][field].get("const") is not False:
        raise SystemExit(f"ready response can overclaim {field}")
abort = json.loads((schema_dir / "abort-response.schema.json").read_text())
if abort["properties"]["status"].get("const") != "retired":
    raise SystemExit("abort response can manufacture a committed result")
PY

main="$root/runtime/main.go"
wrapper="$root/runtime/world_authoritative_match.go"
config="$root/runtime/world_command_config.go"
environment="$root/runtime/world_command_environment.go"
http="$root/runtime/world_command_http.go"
storage="$root/runtime/world_command_storage.go"
coordinator="$root/runtime/internal/worldcommand/coordinator.go"
commit="$root/runtime/internal/worldcommand/store_commit.go"
core="$root/runtime/internal/core/world_command.go"
rpc="$root/runtime/world_command_rpc.go"
failpoint="$root/runtime/world_command_failpoint.go"
world_fixture="$root/runtime/cmd/trnm-world-transition-fixture/main.go"
proxy="$root/runtime/cmd/trnm-response-drop-proxy/main.go"
tls_fixture="$root/runtime/cmd/trnm-tls-fixture/main.go"
compose="$root/deploy/world-command-fault-lab/compose.yaml"

for token in rpcWorldCommandReady rpcWorldCommandStatus rpcWorldCommandAbort worldAuthoritativeMatch; do
  grep -q "$token" "$main" || fail "main.go does not register $token"
done
for token in PreflightCommand WorldBinding; do
  grep -q "func (e \*Engine) $token" "$core" || fail "core lacks non-mutating $token"
done

grep -q 'profile == worldProfileLegacy' "$wrapper" || fail 'legacy profile is not explicit'
grep -q 'worldProfileTarget' "$config" || fail 'target profile is missing'
if grep -Ri -nE 'fallback.*legacy|legacy.*fallback' "$wrapper" "$config"; then
  fail 'target source contains an automatic legacy fallback'
fi

for token in TRNM_WORLD_INITIAL_STATE_HASH TRNM_WORLD_CHALLENGE_SNAPSHOT_HASH; do
  grep -q "$token" "$config" || fail "separate commitment is missing: $token"
  grep -q "$token" "$compose" || fail "fault-lab compose omits $token"
done
grep -q 'envWorldChallengeHash' "$environment" || fail 'challenge snapshot is not process-env allowlisted'

grep -q 'tls.VersionTLS13' "$http" || fail 'World HTTPS executor does not require TLS 1.3'
grep -q 'CheckRedirect' "$http" || fail 'World HTTPS executor does not reject redirects'
grep -q 'io.LimitReader' "$http" || fail 'World HTTPS response is unbounded'
grep -q 'context.WithoutCancel' "$coordinator" || fail 'verified cleanup is caller-cancellation sensitive'

prepare_line=$(grep -n 'c.Store.Prepare' "$coordinator" | head -1 | cut -d: -f1)
execute_line=$(grep -n 'c.Executor.Execute' "$coordinator" | head -1 | cut -d: -f1)
verify_line=$(grep -n 'c.Store.codec.Verify' "$coordinator" | head -1 | cut -d: -f1)
commit_line=$(grep -n 'c.Store.CommitWith' "$coordinator" | head -1 | cut -d: -f1)
[[ -n "$prepare_line" && -n "$execute_line" && -n "$verify_line" && -n "$commit_line" ]] || fail 'coordinator sequence is incomplete'
(( prepare_line < execute_line && execute_line < verify_line && verify_line < commit_line )) || fail 'Prepare -> Execute -> Verify -> Commit ordering drifted'

grep -q 'type CommitPersister func' "$commit" || fail 'atomic sidecar persister is missing'
grep -q 'StorageWrite(ctx, \[\]\*runtime.StorageWrite{' "$storage" || fail 'atomic storage batch is missing'
grep -q 'matchStorageCollection' "$storage" || fail 'atomic batch omits core snapshot'
grep -q 'worldCommandStorageCollection' "$storage" || fail 'atomic batch omits World snapshot'
grep -q 'errAtomicWorldCommitAmbiguous' "$storage" || fail 'ambiguous storage acknowledgement is not fail closed'

grep -q 'TRNM_WORLD_COMMAND_FAULT_LAB' "$config" || fail 'fault-lab gate is missing'
grep -q 'after_reservation' "$config" || fail 'reservation failpoint is missing'
grep -q 'after_verify' "$config" || fail 'verified-result failpoint is missing'
grep -q 'os.Exit' "$failpoint" || fail 'process failpoint does not terminate the process'
if grep -R -n 'os.Exit' "$root/runtime" --include='*.go' | grep -v 'world_command_failpoint.go'; then
  fail 'process exit capability escaped the isolated fault-lab file'
fi

for token in trnm_world_command_ready_v1 trnm_world_command_status_v1 trnm_world_command_abort_v1; do
  grep -q "$token" "$rpc" || fail "operator RPC missing: $token"
done
for token in 'cutover_authorized": false' 'public_online_enabled": false' 'public_player_market_enabled": false'; do
  grep -q "$token" "$rpc" || fail "operator RPC can overclaim: $token"
done

for token in 'ListenAndServeTLS' 'cacheHits' 'storeCached' 'directory.Sync'; do
  grep -q "$token" "$world_fixture" || fail "World HTTPS fixture lacks $token"
done
for token in drop_next delay_next Hijack TLSClientConfig; do
  grep -q "$token" "$proxy" || fail "response-drop proxy lacks $token"
done
for token in 'IsCA:                  true' 'ExtKeyUsageServerAuth' 'writeAtomic'; do
  grep -q "$token" "$tls_fixture" || fail "TLS fixture lacks $token"
done
for service in tls-init world-fixture response-drop-proxy nakama; do
  grep -q "^  $service:" "$compose" || fail "fault-lab compose omits service $service"
done
grep -q 'https://response-drop-proxy:7444/v1/transition' "$compose" || fail 'Nakama is not routed through the response-drop proxy'

new_files=(
  "$wrapper" "$config" "$environment" "$http" "$storage" "$rpc" "$failpoint"
  "$core" "$coordinator" "$commit" "$world_fixture" "$proxy" "$tls_fixture" "$compose"
)
if grep -R -nE 'Trillionnium-Chain|trillionnium-chain|chain/finality|chain/inclusion' "${new_files[@]}"; then
  fail 'Trillionnium Chain entered the excluded deployed-runtime tranche'
fi

printf '%s\n' 'World command deployed runtime boundary: passed'
