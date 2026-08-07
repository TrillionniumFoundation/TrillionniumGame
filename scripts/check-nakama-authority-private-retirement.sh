#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
env_file=""
retiring_key_id=""
evidence_file=""
writers_fenced=false
compose_files=()

usage() {
  cat >&2 <<'EOF'
usage: check-nakama-authority-private-retirement.sh \
  --env-file FILE --retiring-key-id KEY_ID --evidence-file FILE \
  --writers-fenced [--compose-file FILE ...]

The explicit --writers-fenced assertion is required. The script also verifies
that this canonical Compose stack's Nakama service is stopped before it opens
one repeatable-read, read-only PostgreSQL transaction.
EOF
}

while (( $# > 0 )); do
  case "$1" in
    --env-file)
      (( $# >= 2 )) || { usage; exit 64; }
      env_file=$2
      shift 2
      ;;
    --retiring-key-id)
      (( $# >= 2 )) || { usage; exit 64; }
      retiring_key_id=$2
      shift 2
      ;;
    --evidence-file)
      (( $# >= 2 )) || { usage; exit 64; }
      evidence_file=$2
      shift 2
      ;;
    --compose-file)
      (( $# >= 2 )) || { usage; exit 64; }
      compose_files+=("$2")
      shift 2
      ;;
    --writers-fenced)
      writers_fenced=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      exit 64
      ;;
  esac
done

if [[ -z "$env_file" || -z "$retiring_key_id" || -z "$evidence_file" || "$writers_fenced" != true ]]; then
  usage
  exit 64
fi
if [[ ! "$retiring_key_id" =~ ^[A-Za-z0-9._:-]{1,128}$ ]]; then
  echo "ERROR: retiring authority key id is not canonical" >&2
  exit 64
fi
if [[ ! -f "$env_file" || -L "$env_file" ]]; then
  echo "ERROR: Compose env input must be a regular non-symlink file" >&2
  exit 64
fi
bash "$root/scripts/lint-nakama-compose-env.sh" "$env_file" >/dev/null

if (( ${#compose_files[@]} == 0 )); then
  compose_files=("$root/compose.yaml")
fi
for compose_file in "${compose_files[@]}"; do
  if [[ ! -f "$compose_file" || -L "$compose_file" ]]; then
    echo "ERROR: Compose model must be a regular non-symlink file: $compose_file" >&2
    exit 64
  fi
done

evidence_parent=$(dirname "$evidence_file")
if [[ ! -d "$evidence_parent" || -L "$evidence_parent" ]]; then
  echo "ERROR: evidence parent must be an existing non-symlink directory" >&2
  exit 64
fi

docker_cmd=()
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  docker_cmd=(docker)
elif command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
  docker_cmd=(sudo -n docker)
else
  echo "ERROR: Docker access is required to inspect the fenced canonical stack" >&2
  exit 1
fi

compose=("${docker_cmd[@]}" compose --env-file "$env_file")
for compose_file in "${compose_files[@]}"; do
  compose+=(-f "$compose_file")
done
"${compose[@]}" config --quiet

if ! nakama_ids_output=$("${compose[@]}" ps --all -q nakama); then
	echo "ERROR: failed to enumerate canonical Nakama service containers" >&2
	exit 66
fi
mapfile -t nakama_ids <<<"$nakama_ids_output"
for nakama_id in "${nakama_ids[@]}"; do
	[[ -n "$nakama_id" ]] || continue
	nakama_running=$("${docker_cmd[@]}" inspect -f '{{.State.Running}}' "$nakama_id")
	if [[ "$nakama_running" == true ]]; then
		echo "ERROR: Nakama is still running; fence every writer before the private-key retirement scan" >&2
		exit 66
	fi
done
if ! postgres_ids_output=$("${compose[@]}" ps --all -q postgres); then
	echo "ERROR: failed to enumerate canonical PostgreSQL service containers" >&2
	exit 66
fi
mapfile -t postgres_ids <<<"$postgres_ids_output"
postgres_running_count=0
for postgres_id in "${postgres_ids[@]}"; do
	[[ -n "$postgres_id" ]] || continue
	postgres_running=$("${docker_cmd[@]}" inspect -f '{{.State.Running}}' "$postgres_id")
	if [[ "$postgres_running" == true ]]; then
		((postgres_running_count += 1))
	fi
done
if (( postgres_running_count != 1 )); then
	echo "ERROR: exactly one canonical PostgreSQL service must be running for the retirement scan" >&2
	exit 66
fi

scratch=$(mktemp -d)
raw="$scratch/rotation-preflight.tsv"
evidence_tmp=$(mktemp "$evidence_parent/.nakama-private-retirement.XXXXXX")
chmod 600 "$evidence_tmp"
cleanup() {
  status=$?
  trap - EXIT INT TERM
  rm -rf "$scratch"
  if [[ -f "$evidence_tmp" ]]; then
    rm -f "$evidence_tmp"
  fi
  exit "$status"
}
trap cleanup EXIT INT TERM

"${compose[@]}" exec -T postgres \
  psql -X -qAt -F $'\t' -v ON_ERROR_STOP=1 -U postgres -d trillionnium_nakama \
  -v "retiring_key_id=$retiring_key_id" >"$raw" <<'SQL'
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;

SELECT 'meta', COALESCE(to_regclass('public.storage')::text, ''),
       COALESCE((
         SELECT data_type
         FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'storage' AND column_name = 'value'
       ), '');

WITH controls AS (
  SELECT user_id, key, version, value
  FROM storage
  WHERE collection = 'trnm_research_control_v2'
), blockers AS (
  SELECT
    CASE
      WHEN user_id IS DISTINCT FROM '00000000-0000-0000-0000-000000000000'::uuid THEN 'non_system_owner'
      WHEN jsonb_typeof(value) IS DISTINCT FROM 'object' THEN 'non_object_value'
      WHEN value->>'schema' IS NULL OR value->>'schema' NOT IN (
        'trnm.nakama.stored-research-control-command.v2',
        'trnm.nakama.stored-research-control-command.v3'
      ) THEN 'unsupported_schema'
      WHEN value->>'status' IS NULL OR value->>'status' NOT IN ('pending', 'applied') THEN 'invalid_status'
      WHEN value->>'command_id' IS NULL OR key IS DISTINCT FROM value->>'command_id' THEN 'storage_identity_mismatch'
      WHEN value->>'schema' = 'trnm.nakama.stored-research-control-command.v2' AND
           value->>'status' = 'pending' THEN 'legacy_v2_pending'
      WHEN value->>'schema' = 'trnm.nakama.stored-research-control-command.v3' AND (
           value->>'expected_response_authority_key_id' IS NULL OR
           value->>'expected_response_authority_key_id' !~ '^[A-Za-z0-9._:-]{1,128}$'
      ) THEN 'invalid_expected_authority'
      WHEN value->>'status' = 'pending' AND (
        value ? 'response_body_base64' OR value ? 'response_sha256' OR
        value ? 'response_authority_key_id' OR value ? 'response_signature_base64' OR
        value ? 'applied_at_unix'
      ) THEN 'pending_carries_response'
      WHEN value->>'status' = 'applied' AND (
        COALESCE(value->>'response_authority_key_id', '') = '' OR
        COALESCE(value->>'response_body_base64', '') = '' OR
        COALESCE(value->>'response_sha256', '') = '' OR
        COALESCE(value->>'response_signature_base64', '') = '' OR
        NOT (value ? 'applied_at_unix')
      ) THEN 'invalid_applied_response'
      WHEN value->>'schema' = 'trnm.nakama.stored-research-control-command.v3' AND
           value->>'status' = 'applied' AND
           value->>'response_authority_key_id' IS DISTINCT FROM
             value->>'expected_response_authority_key_id' THEN 'applied_authority_mismatch'
      WHEN value->>'schema' = 'trnm.nakama.stored-research-control-command.v3' AND
           value->>'status' = 'pending' AND
           value->>'expected_response_authority_key_id' = :'retiring_key_id' THEN 'pending_retiring_authority'
      ELSE NULL
    END AS reason,
    user_id::text AS owner_id,
    key,
    version,
    encode(convert_to(value::text, 'UTF8'), 'hex') AS value_hex
  FROM controls
)
SELECT 'blocker', reason, owner_id, key, version, value_hex
FROM blockers
WHERE reason IS NOT NULL
ORDER BY reason COLLATE "C", owner_id COLLATE "C", key COLLATE "C";

COMMIT;
SQL

if ! nakama_after_scan_ids_output=$("${compose[@]}" ps --all -q nakama); then
	echo "ERROR: failed to re-enumerate canonical Nakama service containers" >&2
	exit 66
fi
mapfile -t nakama_after_scan_ids <<<"$nakama_after_scan_ids_output"
for nakama_after_scan_id in "${nakama_after_scan_ids[@]}"; do
	[[ -n "$nakama_after_scan_id" ]] || continue
	nakama_after_scan_running=$("${docker_cmd[@]}" inspect -f '{{.State.Running}}' "$nakama_after_scan_id")
	if [[ "$nakama_after_scan_running" == true ]]; then
		echo "ERROR: Nakama restarted during the retirement scan; discard the result and re-fence every writer" >&2
		exit 66
	fi
done

python3 - "$raw" "$evidence_tmp" "$retiring_key_id" <<'PY'
import hashlib
import json
import pathlib
import sys

raw_path, output_path, retiring_key_id = sys.argv[1:]
raw = pathlib.Path(raw_path).read_bytes()
lines = raw.decode("utf-8").splitlines()
if not lines:
    raise SystemExit("rotation preflight returned no storage metadata")
meta = lines[0].split("\t")
if meta != ["meta", "storage", "jsonb"]:
    raise SystemExit(f"unsupported Nakama storage schema: {meta!r}")
blockers = []
for line in lines[1:]:
    fields = line.split("\t")
    if len(fields) != 6 or fields[0] != "blocker":
        raise SystemExit("rotation preflight returned a malformed blocker row")
    value_bytes = bytes.fromhex(fields[5])
    json.loads(value_bytes)
    blockers.append({
        "reason": fields[1],
        "owner_id": fields[2],
        "key": fields[3],
        "version": fields[4],
        "value_hex": fields[5],
        "value_sha256": hashlib.sha256(value_bytes).hexdigest(),
    })
evidence = {
    "schema": "trnm.nakama.authority-private-retirement-preflight.v1",
    "retiring_authority_key_id": retiring_key_id,
    "external_writers_fenced_operator_assertion": True,
    "canonical_nakama_service_stopped_observed": True,
    "writer_fence_scope": "external-operator-assertion-plus-before-and-after-compose-observation",
    "transaction": "repeatable-read-read-only",
    "storage_table": "public.storage",
    "storage_value_type": "jsonb",
    "safe_to_destroy_private_key": not blockers,
    "blocker_count": len(blockers),
    "blockers": blockers,
    "query_output_sha256": hashlib.sha256(raw).hexdigest(),
}
pathlib.Path(output_path).write_text(
    json.dumps(evidence, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
PY

mv -f "$evidence_tmp" "$evidence_file"
safe=$(python3 - "$evidence_file" <<'PY'
import json
import sys
print("true" if json.load(open(sys.argv[1], encoding="utf-8"))["safe_to_destroy_private_key"] else "false")
PY
)
if [[ "$safe" != true ]]; then
  blocker_count=$(python3 - "$evidence_file" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["blocker_count"])
PY
  )
  echo "ERROR: retiring authority still has $blocker_count blocker(s); private-key destruction is forbidden (evidence: $evidence_file)" >&2
  exit 3
fi

echo "Nakama authority private-key retirement preflight: safe=true key_id=$retiring_key_id evidence=$evidence_file"
